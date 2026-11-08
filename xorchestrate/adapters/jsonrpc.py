"""Bounded subprocess transport shared by out-of-process adapters.

The JSON-lines framing follows the same public protocol as the legacy
``devtools/rpc.py``; this implementation has no dependency on that runtime.
Commands come exclusively from operator configuration, never remote metadata.
"""

from __future__ import annotations

import base64
import json
import math
import os
import queue
import re
import signal
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from copy import deepcopy
from typing import Any

from ..model import OrchestrationError, unique_keys

_CREDENTIAL_NAME = re.compile(r"(?:api.?key|authorization|password|credential|private.?key|access.?token|refresh.?token|(?:^|_)token$|secret)", re.I)
_INCOMPLETE_CREDENTIAL = re.compile(
    r'''(?ix)(["']?(?:authorization|password|passwd|secret|api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|private[_-]?key)["']?\s*[:=]\s*)(["'])([^"']*)\Z''')


class TelemetryDispatch:
    """One ordered optional worker; admission is never a durable receipt.

    Count and byte pressure are bounded independently. Health is read directly,
    so a broken collector is not needed to report its own failures. Transcript
    readers may request bounded acknowledgment and retry a stable event ID.
    """

    def __init__(self, observer, options=None):
        options = options or {}
        self.observer = observer
        self.capacity = positive_integer(options, "max_telemetry_pending", 64)
        self.maximum = positive_integer(options, "max_telemetry_bytes", 8 * 1024 * 1024)
        self.drain_timeout = positive_number(options, "telemetry_drain_timeout", 0.05)
        self._condition = threading.Condition()
        self._queue = queue.Queue(maxsize=self.capacity + 1)
        self._pending = 0
        self._bytes = 0
        self._closed = False
        self._receipts = {}
        self._counts = {"admitted": 0, "completed": 0, "failed": 0, "rejected": 0,
                        "source_recorded": 0, "source_failed": 0}
        self._thread = threading.Thread(target=self._work, daemon=True)
        self._thread.start()

    def _submit(self, event, retain_ack=False):
        identifier = event.get("event_id") if retain_ack else None
        with self._condition:
            if identifier in self._receipts:
                return self._receipts[identifier]
            try:
                size = len(encode_json(event))
            except BaseException:
                self._counts["rejected"] += 1
                return None
            if self._closed or self._pending >= self.capacity or self._bytes + size > self.maximum:
                self._counts["rejected"] += 1
                return None
            if retain_ack and len(self._receipts) >= self.capacity:
                self._counts["rejected"] += 1
                return None
            future = Future()
            if identifier is not None:
                self._receipts[identifier] = future
            self._pending += 1
            self._bytes += size
            self._counts["admitted"] += 1
            self._queue.put_nowait((event, future, size))
            return future

    def __call__(self, event):
        if threading.current_thread() is self._thread:
            return self._nested(event)
        future = self._submit(event)
        return {"status": "deferred" if future is not None else "unavailable", "receipt": None,
                "reason": "queued" if future is not None else "dispatch_pressure_or_closed"}

    def capture(self, event):
        if threading.current_thread() is self._thread:
            return self._nested(event)
        future = self._submit(event, retain_ack=True)
        if future is None:
            return {"status": "unavailable", "reason": "dispatch_pressure_or_closed"}
        try:
            return future.result(timeout=self.drain_timeout)
        except FutureTimeout:
            return {"status": "deferred", "reason": "awaiting_capture_ack"}

    def release(self, event_id):
        with self._condition:
            self._receipts.pop(event_id, None)

    def _nested(self, event):
        try:
            result = self.observer(event)
            if result is None:
                result = {"status": "recorded", "receipt": None, "boundary": "observer_return"}
        except BaseException:
            result = {"status": "unavailable", "reason": "observer_failed"}
        with self._condition:
            success = isinstance(result, dict) and result.get("status") in {"recorded", "duplicate"}
            self._counts["source_recorded" if success else "source_failed"] += 1
        return result

    def schedule(self, callback):
        """Run bounded source ingestion on the optional worker, off protocol IO."""
        with self._condition:
            if self._closed or self._pending >= self.capacity:
                self._counts["rejected"] += 1
                return False
            self._pending += 1
            self._counts["admitted"] += 1
            self._queue.put_nowait((callback, Future(), 0))
            return True

    def _work(self):
        while True:
            item = self._queue.get()
            if item is None:
                return
            event, future, size = item
            try:
                result = event() if callable(event) else self.observer(event)
                if result is None:
                    result = {"status": "recorded", "receipt": None, "boundary": "observer_return"}
            except BaseException:
                result = {"status": "unavailable", "reason": "observer_failed"}
            with self._condition:
                success = isinstance(result, dict) and result.get("status") in {"recorded", "duplicate"}
                self._counts["completed" if success else "failed"] += 1
                identifier = event.get("event_id") if isinstance(event, dict) else None
                if not success and self._receipts.get(identifier) is future:
                    self._receipts.pop(identifier, None)
                future.set_result(result)
                self._pending -= 1
                self._bytes -= size
                self._condition.notify_all()

    def drain(self, timeout=None):
        if threading.current_thread() is self._thread:
            return self.health()
        duration = self.drain_timeout if timeout is None else positive_number({"timeout": timeout}, "timeout", self.drain_timeout)
        deadline = time.monotonic() + duration
        with self._condition:
            while self._pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(remaining)
            return self.health()

    def health(self):
        with self._condition:
            return {**self._counts, "pending": self._pending, "pending_bytes": self._bytes,
                    "capacity": self.capacity, "max_bytes": self.maximum, "closed": self._closed,
                    "coverage": "gap" if self._counts["rejected"] or self._counts["failed"] or self._counts["source_failed"] else "pending" if self._pending else "observed" if self._counts["admitted"] else "unobserved"}

    def close(self):
        with self._condition:
            if not self._closed:
                self._closed = True
                self._queue.put_nowait(None)
        return self.drain()


def telemetry_dispatch(observer, options=None):
    return observer if observer is None or isinstance(observer, TelemetryDispatch) else TelemetryDispatch(observer, options)


def capture_origin(value=None) -> dict:
    """Detach origin data; transport attempts never use mutable current selection."""
    try:
        context = deepcopy(value) if isinstance(value, dict) else {}
        if context.get("attempt_id"):
            context["parent_attempt_id"] = context["attempt_id"]
        context["attempt_id"] = uuid.uuid4().hex
        return context
    except BaseException:
        return {"attempt_id": uuid.uuid4().hex}


def telemetry_secrets(options: dict) -> tuple[str, ...]:
    """Inspect only credentials explicitly made available to this source process."""
    try:
        environment = command_options(options)["env"]
        return tuple(value for key, value in environment.items() if value and _CREDENTIAL_NAME.search(key))
    except BaseException:
        return ()


def observe_telemetry(observer, event_type, payload, context, *, secrets=(), completeness="complete", reason=None, event_id=None, acknowledge=False):
    """Optional capture is independent of protocol and required evidence callbacks.

    Protection is shared with the pure telemetry contract. A failed protector has
    no unchecked fallback; a failed optional observer cannot replace the effect.
    """
    if observer is None:
        return False
    try:
        from ..telemetry import protect
        event = {"event_type": event_type, "payload": payload, "context": context,
                 "basis": "observed", "completeness": completeness}
        if reason is not None:
            event["reason"] = reason
        if event_id is not None:
            event["event_id"] = event_id
        protected = protect(deepcopy(event), secrets=secrets)
        result = observer.capture(protected) if acknowledge and isinstance(observer, TelemetryDispatch) else observer(protected)
        return result is None or isinstance(result, dict) and result.get("status") in {"recorded", "duplicate"}
    except BaseException:
        return False


def observe_wire(observer, event_type, data, context, *, secrets=(), complete=True,
                 total_bytes=None, reason=None, range_start=0, event_id=None, acknowledge=False, **fields):
    """Retain protected bytes and explicitly distinguish source/retained ranges.

    The reversible decode keeps safe binary diagnostics intact. Credential text
    is filtered before base64 encoding, including when the surrounding frame is
    malformed. A protector failure yields a safe suppression envelope.
    """
    if observer is None:
        return
    try:
        from ..telemetry import protect
        text = data.decode("utf-8", "surrogateescape")
        if not complete:
            # A retained prefix can stop inside a configured credential. Full
            # value replacement alone cannot protect that incomplete suffix.
            for secret in secrets:
                if not isinstance(secret, str) or not secret:
                    continue
                variants = (secret, json.dumps(secret, ensure_ascii=True)[1:-1])
                for variant in variants:
                    for size in range(min(len(text), len(variant) - 1), 0, -1):
                        if text.endswith(variant[:size]):
                            text = text[:-size] + "[REDACTED]"
                            break
        try:
            json.loads(text)
        except (ValueError, RecursionError):
            text = _INCOMPLETE_CREDENTIAL.sub(lambda match: match[1] + match[2] + "[REDACTED]", text)
        protected = protect(text, secrets=secrets).encode("utf-8", "surrogateescape")
        payload = {**fields, "content": base64.b64encode(protected).decode("ascii"), "encoding": "base64",
                   "captured_bytes": len(protected), "range_start": range_start, "range_end": range_start + len(data),
                   "total_bytes": total_bytes, "protection": "unchanged" if data == protected else "redacted",
                   "representation": "protected_bytes", "source_bytes": len(data)}
        return observe_telemetry(observer, event_type, payload, context, secrets=secrets,
                                 completeness="complete" if complete else "partial", reason=reason, event_id=event_id, acknowledge=acknowledge)
    except BaseException:
        observe_telemetry(observer, event_type, {"representation": "suppressed", "captured_bytes": 0}, context,
                          secrets=secrets, completeness="suppressed", reason="credential_filter_unavailable")


class _StderrCapture:
    """Drain independently even after the bounded retained prefix is full."""

    def __init__(self, process, maximum):
        self.process, self.maximum = process, maximum
        self.data = bytearray()
        self.total = 0
        self.eof = False
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        try:
            while True:
                block = self.process.stderr.read(65536)
                if not block:
                    self.eof = True
                    return
                self.total += len(block)
                self.data.extend(block[:max(0, self.maximum - len(self.data))])
        except (OSError, ValueError):
            pass

    def emit(self, observer, context, secrets):
        self.thread.join(timeout=1)
        complete = self.eof and self.total == len(self.data)
        observe_wire(observer, "process.stderr", bytes(self.data), context, secrets=secrets,
                     complete=complete, total_bytes=self.total if self.eof else None,
                     reason=None if complete else "retention_limit" if self.total > len(self.data) else "interrupted",
                     channel="stderr")


def positive_number(options: dict, name: str, default: float) -> float:
    value = options.get(name, default)
    try:
        valid = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and value > 0
    except OverflowError:
        valid = False
    if not valid:
        raise OrchestrationError(f"{name} must be a finite positive number.", "invalid_configuration")
    return float(value)


def positive_integer(options: dict, name: str, default: int) -> int:
    value = options.get(name, default)
    if type(value) is not int or value <= 0:
        raise OrchestrationError(f"{name} must be a positive integer.", "invalid_configuration")
    return value


def command_options(options: dict, key: str = "command") -> dict:
    command = options.get(key)
    if not isinstance(command, list) or not command or any(not isinstance(arg, str) or "\0" in arg for arg in command) or not command[0]:
        raise OrchestrationError(f"{key} must be a nonempty argv list.", "invalid_configuration")
    cwd = options.get("cwd")
    if cwd is not None and (not isinstance(cwd, str) or not cwd or "\0" in cwd):
        raise OrchestrationError("cwd must be a directory path.", "invalid_configuration")
    environment = options.get("env", {})
    if not isinstance(environment, dict) or any(
        not isinstance(k, str) or not k or "=" in k or "\0" in k or not isinstance(v, str) or "\0" in v
        for k, v in environment.items()
    ):
        raise OrchestrationError("env must map environment variable names to strings.", "invalid_configuration")
    inherit = options.get("inherit_env", False)
    if not isinstance(inherit, bool):
        raise OrchestrationError("inherit_env must be boolean.", "invalid_configuration")
    pass_env = options.get("pass_env", [])
    if not isinstance(pass_env, list) or any(not isinstance(name, str) or not name or "=" in name or "\0" in name for name in pass_env):
        raise OrchestrationError("pass_env must list environment variable names.", "invalid_configuration")
    # The explicit default prevents unrelated host credentials reaching a tool.
    env = dict(os.environ) if inherit else {}
    env.update({name: os.environ[name] for name in pass_env if name in os.environ})
    env.update(environment)
    return {"args": list(command), "cwd": cwd, "env": env, "shell": False, "start_new_session": os.name == "posix"}


def encode_json(value: Any, *, sort_keys: bool = False) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"), sort_keys=sort_keys).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise OrchestrationError("Value is not valid JSON.", "invalid_request", {"outcome": "not_started"}) from exc


def decode_json(data: bytes) -> Any:
    def invalid_constant(_):
        raise ValueError("Non-finite JSON number")

    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=unique_keys("Duplicate JSON property"), parse_constant=invalid_constant)
        # The parser can also produce infinity from a syntactically valid
        # exponent such as 1e999; parse_constant alone cannot reject that case.
        json.dumps(value, allow_nan=False)
        return value
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise OrchestrationError("Subprocess returned invalid JSON.", "provider_protocol") from exc


def _signal_group(process: subprocess.Popen, sig: int) -> None:
    try:
        if os.name == "posix":
            os.killpg(process.pid, sig)
        elif process.poll() is None:
            process.kill() if sig == signal.SIGKILL else process.terminate()
    except ProcessLookupError:
        pass


def close_process(process: subprocess.Popen, grace: float = 0.2) -> None:
    """Close stdin, then terminate and kill the owned process group if needed."""
    if process.stdin is not None:
        try:
            process.stdin.close()
        except (OSError, ValueError):
            pass
    try:
        process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        _signal_group(process, signal.SIGTERM)
        try:
            process.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            _signal_group(process, signal.SIGKILL)
            process.wait(timeout=max(grace, 1))
    # A child can survive its parent and retain our pipe descriptors.
    _signal_group(process, signal.SIGKILL)
    if process.stdout is not None:
        try:
            process.stdout.close()
        except (OSError, ValueError):
            pass


def run_json_process(options: dict, payload: dict, command_key: str = "command", *,
                     telemetry_observer=None, telemetry_context=None) -> dict:
    """Execute one JSON stdin/stdout exchange with bounded time and memory."""
    timeout = positive_number(options, "timeout", 30)
    shutdown_timeout = positive_number(options, "shutdown_timeout", 0.2)
    maximum = positive_integer(options, "max_output_bytes", 4 * 1024 * 1024)
    origin = capture_origin(telemetry_context if telemetry_context is not None else payload.get("context"))
    secrets = telemetry_secrets(options) if telemetry_observer is not None else ()
    request = encode_json(payload) + b"\n"
    input_maximum = positive_integer(options, "max_input_bytes", 4 * 1024 * 1024)
    stderr_maximum = positive_integer(options, "max_stderr_bytes", maximum) if telemetry_observer is not None else maximum
    owns_dispatch = telemetry_observer is not None and not isinstance(telemetry_observer, TelemetryDispatch)
    telemetry_observer = telemetry_dispatch(telemetry_observer, options)
    observe_wire(telemetry_observer, "process.request", request[:input_maximum + 1], origin, secrets=secrets,
                 complete=len(request) <= input_maximum + 1, total_bytes=len(request), phase="serialized")
    if len(request) > input_maximum:
        if owns_dispatch:
            telemetry_observer.close()
        raise OrchestrationError("Subprocess input exceeds its configured bound.", "invalid_request", {"outcome": "not_started"})
    try:
        process = subprocess.Popen(**command_options(options, command_key), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE if telemetry_observer is not None else subprocess.DEVNULL, bufsize=0)
    except (OSError, ValueError) as exc:
        observe_telemetry(telemetry_observer, "process.error", {"code": "provider_unavailable", "outcome": "not_started"}, origin, secrets=secrets)
        if owns_dispatch:
            telemetry_observer.close()
        raise OrchestrationError("Could not start configured subprocess.", "provider_unavailable", {"outcome": "not_started"}) from exc
    stderr = _StderrCapture(process, stderr_maximum) if telemetry_observer is not None else None
    output = bytearray()
    failure: list[Exception] = []
    finished = threading.Event()
    input_finished = threading.Event()
    output_eof = threading.Event()
    response_observed = False

    def observe_output():
        nonlocal response_observed
        if not response_observed:
            response_observed = True
            complete = output_eof.is_set() and len(output) <= maximum
            observe_wire(telemetry_observer, "process.response", bytes(output), origin, secrets=secrets,
                         complete=complete, total_bytes=len(output) if output_eof.is_set() else None,
                         reason=None if complete else "output_limit" if len(output) > maximum else "interrupted", channel="stdout")

    def read_output():
        try:
            while True:
                block = process.stdout.read(min(65536, maximum + 1 - len(output)))
                if not block:
                    output_eof.set()
                    break
                output.extend(block)
                if len(output) > maximum:
                    raise OrchestrationError("Subprocess output exceeds its configured bound.", "provider_protocol", {"outcome": "unknown"})
        except (OSError, OrchestrationError) as exc:
            failure.append(exc)
        finally:
            finished.set()

    def write_input():
        try:
            view = memoryview(request)
            while view:
                written = process.stdin.write(view)
                view = view[written:]
            process.stdin.close()
        except (OSError, ValueError) as exc:
            failure.append(exc)
        finally:
            input_finished.set()

    reader = threading.Thread(target=read_output, daemon=True)
    writer = threading.Thread(target=write_input, daemon=True)
    deadline = time.monotonic() + timeout
    reader.start()
    writer.start()
    try:
        if not finished.wait(max(0, deadline - time.monotonic())):
            raise OrchestrationError("Subprocess exceeded its execution deadline.", "provider_timeout", {"outcome": "unknown"})
        # A peer can close stdout before it has accepted the request. Do not
        # accept its result until the writer has finished, including any late
        # broken pipe. Preserve an already-observed output failure immediately.
        if not failure and not input_finished.wait(max(0, deadline - time.monotonic())):
            raise OrchestrationError("Subprocess did not accept input before the deadline.", "provider_timeout", {"outcome": "unknown"})
        if failure:
            if isinstance(failure[0], OrchestrationError):
                raise failure[0]
            raise OrchestrationError("Subprocess communication failed.", "provider_unavailable", {"outcome": "unknown"}) from failure[0]
        try:
            status = process.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired as exc:
            raise OrchestrationError("Subprocess exceeded its execution deadline.", "provider_timeout", {"outcome": "unknown"}) from exc
        if status:
            raise OrchestrationError("Subprocess exited unsuccessfully.", "capability_failed", {"exit_code": status, "outcome": "unknown"})
        observe_output()
        try:
            result = decode_json(bytes(output))
        except OrchestrationError as exc:
            exc.details["outcome"] = "unknown"
            raise
        if not isinstance(result, dict):
            raise OrchestrationError("Subprocess result must be a JSON object.", "provider_protocol", {"outcome": "unknown"})
        return result
    except BaseException as error:
        observe_telemetry(telemetry_observer, "process.error", {"code": getattr(error, "code", "provider_unavailable"),
                          "outcome": getattr(error, "details", {}).get("outcome", "unknown")}, origin, secrets=secrets)
        raise
    finally:
        close_process(process, shutdown_timeout)
        reader.join(timeout=1)
        writer.join(timeout=1)
        observe_output()
        if stderr is not None:
            stderr.emit(telemetry_observer, origin, secrets)
            try:
                process.stderr.close()
            except (OSError, ValueError):
                pass
        observe_telemetry(telemetry_observer, "process.exit", {"exit_code": process.poll()}, origin, secrets=secrets)
        if isinstance(telemetry_observer, TelemetryDispatch):
            telemetry_observer.close() if owns_dispatch else telemetry_observer.drain()


class JsonLineProcess:
    """Bounded, full-duplex JSON-RPC client.

    A reader continuously correlates responses and dispatches callbacks, even
    after a submission has returned. Callback workers and output buffering are
    bounded independently. ``request_async`` returns after writing the request;
    its Future represents the eventual response, not successful native receipt.
    Notification observers must be short and nonblocking. They see packets in
    wire order, while ``poll_notifications`` retains the same bounded packets.
    """

    def __init__(self, options: dict, request_handler: Callable | None = None, *,
                 jsonrpc: bool = True, notification_handler: Callable | None = None,
                 telemetry_observer=None, telemetry_context=None, application_callback_methods=()):
        self.timeout = positive_number(options, "timeout", 30)
        self.shutdown_timeout = positive_number(options, "shutdown_timeout", 0.2)
        self.callback_timeout = positive_number(options, "callback_timeout", self.timeout)
        self.max_frame = positive_integer(options, "max_frame_bytes", 1024 * 1024)
        self.max_output = positive_integer(options, "max_output_bytes", 4 * 1024 * 1024)
        stderr_maximum = positive_integer(options, "max_stderr_bytes", self.max_output) if telemetry_observer is not None else self.max_output
        self.telemetry_context = capture_origin(telemetry_context)
        self.telemetry_secrets = telemetry_secrets(options) if telemetry_observer is not None else ()
        self.max_pending = positive_integer(options, "max_pending_messages", 1024)
        self.max_callback_history = positive_integer(options, "max_callback_history", 4096)
        worker_count = positive_integer(options, "callback_workers", 8)
        self.handler = request_handler
        if (isinstance(application_callback_methods, str) or
                any(not isinstance(method, str) or not method for method in application_callback_methods)):
            raise OrchestrationError("Application callback methods must be explicit trusted method names.", "invalid_configuration")
        # Selected by the owning adapter, never inferred from a provider's JSON
        # result shape. Generic callbacks retain their ordinary wire capture.
        self.application_callback_methods = frozenset(application_callback_methods)
        self.notification_handler = notification_handler
        self.jsonrpc = jsonrpc
        self._state = threading.RLock()
        self._write_lock = threading.Lock()
        self._write_slots = threading.BoundedSemaphore(self.max_pending)
        self._pending: dict[int, dict] = {}
        self._callbacks: dict[tuple, dict] = {}
        self._callback_queue: queue.Queue = queue.Queue(maxsize=self.max_pending)
        self._callback_bytes = 0
        self._notifications: list[dict] = []
        self._notification_bytes = 0
        self._failure: BaseException | None = None
        self._next_id = 0
        self._closed = False
        self._close_complete = threading.Event()
        self._close_owner = None
        self._owns_dispatch = telemetry_observer is not None and not isinstance(telemetry_observer, TelemetryDispatch)
        self.telemetry_observer = telemetry_dispatch(telemetry_observer, options)
        try:
            self.process = subprocess.Popen(**command_options(options), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=subprocess.PIPE if telemetry_observer is not None else subprocess.DEVNULL, bufsize=0)
        except (OSError, ValueError) as exc:
            observe_telemetry(self.telemetry_observer, "process.error", {"code": "provider_unavailable", "outcome": "not_started"},
                              self.telemetry_context, secrets=self.telemetry_secrets)
            if self._owns_dispatch:
                self.telemetry_observer.close()
            raise OrchestrationError("Could not start configured subprocess.", "provider_unavailable", {"outcome": "not_started"}) from exc
        self._stderr = _StderrCapture(self.process, stderr_maximum) if telemetry_observer is not None else None
        self._workers = [threading.Thread(target=self._callback_worker, daemon=True) for _ in range(worker_count)]
        self._reader = threading.Thread(target=self._read, daemon=True)
        for worker in self._workers:
            worker.start()
        self._reader.start()

    @staticmethod
    def _unavailable(outcome="unknown"):
        return OrchestrationError("Subprocess connection is closed.", "provider_unavailable", {"outcome": outcome})

    def _fail(self, error):
        if isinstance(error, OrchestrationError):
            error.details["outcome"] = "unknown"
        elif isinstance(error, Exception):
            error = OrchestrationError("Subprocess exchange failed unexpectedly.", "provider_unavailable", {"outcome": "unknown"})
        with self._state:
            if self._failure is None:
                self._failure = error
                origins = [(identifier, item["context"]) for identifier, item in self._pending.items()]
                if not origins:
                    origins = [(None, self.telemetry_context)]
            else:
                origins = []
        for identifier, origin in origins:
            observe_telemetry(self.telemetry_observer, "process.error", {"code": getattr(error, "code", "provider_unavailable"),
                              "outcome": "unknown", "request_id": identifier}, origin, secrets=self.telemetry_secrets)
        self.close()

    def _read(self):
        try:
            while not self._closed:
                line = self.process.stdout.readline(self.max_frame + 1)
                if not line:
                    if not self._closed:
                        raise OrchestrationError("Subprocess closed its output stream.", "provider_unavailable", {"outcome": "unknown"})
                    return
                origin = self.telemetry_context
                try:
                    packet = decode_json(line)
                    if isinstance(packet, dict) and "method" not in packet:
                        with self._state:
                            pending = self._pending.get(packet.get("id")) if type(packet.get("id")) is int else None
                            if pending is not None:
                                origin = pending["context"]
                finally:
                    complete = len(line) <= self.max_frame and line.endswith(b"\n")
                    observe_wire(self.telemetry_observer, "process.response", line, origin, secrets=self.telemetry_secrets,
                                 complete=complete, total_bytes=len(line) if complete else None,
                                 reason=None if complete else "frame_limit_or_interrupted", channel="stdout")
                if len(line) > self.max_frame or not line.endswith(b"\n"):
                    raise OrchestrationError("Subprocess exceeded the JSON-line frame bound or omitted its delimiter.", "provider_protocol")
                if not isinstance(packet, dict) or (self.jsonrpc and packet.get("jsonrpc") != "2.0"):
                    raise OrchestrationError("Invalid JSON-RPC envelope.", "provider_protocol")
                with self._state:
                    for pending in self._pending.values():
                        pending["received"] += len(line)
                        if pending["received"] > self.max_output:
                            raise OrchestrationError("Subprocess response exceeded its output bound.", "provider_protocol")
                self._dispatch(packet)
        except BaseException as exc:
            if not self._closed:
                self._fail(exc)

    def _dispatch(self, packet):
        if "method" in packet:
            if not isinstance(packet["method"], str) or not packet["method"] or "result" in packet or "error" in packet:
                raise OrchestrationError("Invalid JSON-RPC request or notification.", "provider_protocol")
            params = packet.get("params", {})
            if not isinstance(params, (dict, list)):
                raise OrchestrationError("Invalid JSON-RPC parameters.", "provider_protocol")
            if "id" not in packet:
                size = len(encode_json(packet))
                with self._state:
                    if len(self._notifications) >= self.max_pending or self._notification_bytes + size > self.max_output:
                        raise OrchestrationError("Subprocess notification buffer exceeded its bound.", "provider_protocol")
                    self._notifications.append(packet)
                    self._notification_bytes += size
                if self.notification_handler is not None:
                    self.notification_handler(packet["method"], deepcopy(params))
                return
            identifier = packet["id"]
            if type(identifier) not in (int, str):
                raise OrchestrationError("Invalid JSON-RPC request identifier.", "provider_protocol")
            # A retransmitted native request cannot execute its callback twice.
            # Never evict identity history and silently permit a conflicting reuse.
            key = (type(identifier), identifier)
            fingerprint = encode_json(packet, sort_keys=True)
            with self._state:
                previous = self._callbacks.get(key)
                if previous:
                    if previous["fingerprint"] != fingerprint:
                        raise OrchestrationError("Conflicting JSON-RPC callback identifier reuse.", "provider_protocol")
                    answer = previous.get("answer")
                else:
                    if len(self._callbacks) >= self.max_callback_history or self._callback_bytes + len(fingerprint) > self.max_output:
                        raise OrchestrationError("Subprocess callback history exceeded its bound.", "provider_protocol")
                    self._callbacks[key] = {"fingerprint": fingerprint}
                    self._callback_bytes += len(fingerprint)
                    try:
                        self._callback_queue.put_nowait((key, packet))
                    except queue.Full as exc:
                        raise OrchestrationError("Subprocess callback queue exceeded its bound.", "provider_protocol") from exc
                    return
            if answer is not None:
                self._write(answer, time.monotonic() + self.timeout,
                            callback_method=packet["method"] if packet["method"] in self.application_callback_methods else None)
            return
        identifier = packet.get("id")
        if type(identifier) is not int or ("result" in packet) == ("error" in packet):
            raise OrchestrationError("Unexpected or malformed JSON-RPC response.", "provider_protocol")
        error = packet.get("error")
        if "error" in packet and (not isinstance(error, dict) or type(error.get("code")) is not int or not isinstance(error.get("message"), str)):
            raise OrchestrationError("Malformed JSON-RPC error.", "provider_protocol")
        with self._state:
            pending = self._pending.pop(identifier, None)
        if pending is None:
            raise OrchestrationError("Unexpected or duplicate JSON-RPC response.", "provider_protocol")
        if pending["timer"]:
            pending["timer"].cancel()
        if "error" in packet:
            pending["future"].set_exception(OrchestrationError("Subprocess returned a JSON-RPC error.", "provider_error", {
                "outcome": "unknown", "remote_error": error}))
        else:
            pending["future"].set_result(packet["result"])

    def _callback_worker(self):
        while not self._closed:
            try:
                key, packet = self._callback_queue.get(timeout=0.05)
            except queue.Empty:
                continue
            finished = threading.Event()

            def expire_callback(finished=finished):
                if not finished.is_set() and not self._closed:
                    self._fail(OrchestrationError("Subprocess callback exceeded its deadline.", "provider_timeout", {"outcome": "unknown"}))

            timer = threading.Timer(self.callback_timeout, expire_callback)
            timer.daemon = True
            timer.start()
            try:
                if self._closed:
                    return
                try:
                    if packet["method"] == "ping":
                        result = {}
                    elif self.handler is not None:
                        result = self.handler(packet["method"], packet.get("params", {}))
                    else:
                        raise OrchestrationError("Client does not support this request.", "unsupported_method")
                    answer = self._envelope(id=packet["id"], result=result)
                except OrchestrationError as exc:
                    answer = self._envelope(id=packet["id"], error={"code": -32601 if exc.code == "unsupported_method" else -32603, "message": str(exc)})
                size = len(encode_json(answer))
                with self._state:
                    if self._callback_bytes + size > self.max_output:
                        raise OrchestrationError("Subprocess callback history exceeded its byte bound.", "provider_protocol")
                    self._callback_bytes += size
                    self._callbacks[key]["answer"] = answer
                self._write(answer, time.monotonic() + self.timeout,
                            callback_method=packet["method"] if packet["method"] in self.application_callback_methods else None)
            except BaseException as exc:
                if not self._closed:
                    self._fail(exc)
            finally:
                finished.set()
                timer.cancel()
                self._callback_queue.task_done()

    def _write(self, message: dict, deadline: float, context=None, *, callback_method=None):
        with self._state:
            if self._closed:
                raise self._unavailable("not_started")
        data = encode_json(message) + b"\n"
        origin = self.telemetry_context if context is None else context
        if callback_method is not None:
            # The application's semantic/native observations retain typed
            # results. A second opaque encoding cannot inherit the source
            # session's grant to every resource embedded in that result.
            observe_telemetry(self.telemetry_observer, "process.request", {
                "representation": "suppressed", "captured_bytes": 0, "source_bytes": len(data),
                "total_bytes": len(data), "range_start": 0, "range_end": 0, "phase": "serialized",
                "direction": "callback_response", "request_id": message.get("id"),
                "callback_method": callback_method, "provenance": "application-capable-callback-v1",
            }, origin, secrets=self.telemetry_secrets, completeness="suppressed",
                reason="callback_response_requires_typed_observation")
        else:
            observe_wire(self.telemetry_observer, "process.request", data[:self.max_frame + 1],
                         origin, secrets=self.telemetry_secrets, complete=len(data) <= self.max_frame + 1,
                         total_bytes=len(data), phase="serialized")
        if len(data) > self.max_frame:
            raise OrchestrationError("Request exceeds the configured frame bound.", "invalid_request", {"outcome": "not_started"})
        if not self._write_slots.acquire(timeout=max(0, deadline - time.monotonic())):
            raise OrchestrationError("Subprocess write capacity deadline exceeded.", "provider_timeout", {"outcome": "not_started"})
        done = threading.Event()
        failures = []

        def send():
            try:
                with self._write_lock:
                    if self._closed:
                        raise OSError("Connection closed")
                    remaining = memoryview(data)
                    while remaining:
                        written = self.process.stdin.write(remaining)
                        if not written:
                            raise OSError("Incomplete subprocess input")
                        remaining = remaining[written:]
            except (OSError, ValueError) as exc:
                failures.append(exc)
            finally:
                self._write_slots.release()
                done.set()

        threading.Thread(target=send, daemon=True).start()
        if not done.wait(max(0, deadline - time.monotonic())):
            raise OrchestrationError("Subprocess did not accept input before the deadline.", "provider_timeout", {"outcome": "unknown"})
        if failures:
            raise OrchestrationError("Subprocess input stream is unavailable.", "provider_unavailable", {"outcome": "unknown"}) from failures[0]

    def _envelope(self, **fields):
        return {"jsonrpc": "2.0", **fields} if self.jsonrpc else fields

    def _expire(self, identifier):
        with self._state:
            if identifier not in self._pending or self._closed:
                return
        self._fail(OrchestrationError("Subprocess request exceeded its deadline.", "provider_timeout", {"outcome": "unknown"}))

    def request_async(self, method: str, params: dict | list | None = None, timeout: float | None = None, *, telemetry_context=None) -> Future:
        duration = self.timeout if timeout is None else positive_number({"timeout": timeout}, "timeout", self.timeout)
        deadline = time.monotonic() + duration
        origin = capture_origin(self.telemetry_context if telemetry_context is None else telemetry_context)
        with self._state:
            if self._closed:
                raise self._unavailable("not_started")
            if len(self._pending) >= self.max_pending:
                raise OrchestrationError("Too many pending subprocess requests.", "provider_capacity", {"outcome": "not_started"})
            self._next_id += 1
            identifier = self._next_id
            packet = self._envelope(id=identifier, method=method, params={} if params is None else params)
            # Validate before publishing a pending request or writing any bytes.
            serialized = encode_json(packet) + b"\n"
            if len(serialized) > self.max_frame:
                observe_wire(self.telemetry_observer, "process.request", serialized[:self.max_frame + 1], origin,
                             secrets=self.telemetry_secrets, complete=False, total_bytes=len(serialized),
                             reason="input_limit", phase="serialized", outcome="not_started")
                observe_telemetry(self.telemetry_observer, "process.error", {"code": "invalid_request", "outcome": "not_started",
                                  "request_id": identifier}, origin, secrets=self.telemetry_secrets)
                raise OrchestrationError("Request exceeds the configured frame bound.", "invalid_request", {"outcome": "not_started"})
            future = Future()
            # Cancellation cannot discard an already submitted effect or lose a
            # subsequent response. Consumers retain the uncertain operation.
            future.set_running_or_notify_cancel()
            self._pending[identifier] = {"future": future, "received": 0, "timer": None, "context": origin}
        try:
            self._write(packet, deadline, origin)
            with self._state:
                pending = self._pending.get(identifier)
                if pending:
                    timer = threading.Timer(max(0, deadline - time.monotonic()), self._expire, args=(identifier,))
                    timer.daemon = True
                    pending["timer"] = timer
                    timer.start()
            return future
        except BaseException as exc:
            if isinstance(exc, OrchestrationError) and exc.details.get("outcome") == "not_started":
                with self._state:
                    self._pending.pop(identifier, None)
                if not future.done():
                    future.set_exception(exc)
            else:
                self._fail(exc)
            raise

    def request(self, method: str, params: dict | list | None = None, timeout: float | None = None, *, telemetry_context=None) -> Any:
        try:
            return self.request_async(method, params, timeout, telemetry_context=telemetry_context).result()
        except BaseException:
            if self._closed:
                self.close()
            raise

    def notify(self, method: str, params: dict | None = None) -> None:
        try:
            self._write(self._envelope(method=method, **({"params": params} if params is not None else {})), time.monotonic() + self.timeout)
        except OrchestrationError as exc:
            if exc.details.get("outcome") != "not_started":
                self._fail(exc)
            raise

    def poll_notifications(self) -> list[dict]:
        with self._state:
            failure = self._failure
            result, self._notifications = self._notifications, []
            self._notification_bytes = 0
        if failure:
            self.close()
            raise failure
        return result

    def telemetry_health(self):
        observer = self.telemetry_observer
        return observer.health() if isinstance(observer, TelemetryDispatch) else {"status": "disabled"}

    def drain_telemetry(self, timeout=None):
        observer = self.telemetry_observer
        return observer.drain(timeout) if isinstance(observer, TelemetryDispatch) else {"status": "disabled"}

    def close(self) -> None:
        with self._state:
            if not self._closed:
                self._closed = True
                self._close_owner = threading.current_thread()
                pending, self._pending = list(self._pending.values()), {}
                for item in pending:
                    if item["timer"]:
                        item["timer"].cancel()
                owner = True
            else:
                owner = False
        if owner:
            try:
                close_process(self.process, self.shutdown_timeout)
                if threading.current_thread() is not self._reader:
                    self._reader.join(timeout=1)
                if self._stderr is not None:
                    self._stderr.emit(self.telemetry_observer, self.telemetry_context, self.telemetry_secrets)
                    try:
                        self.process.stderr.close()
                    except (OSError, ValueError):
                        pass
                observe_telemetry(self.telemetry_observer, "process.exit", {"exit_code": self.process.poll()},
                                  self.telemetry_context, secrets=self.telemetry_secrets)
                if isinstance(self.telemetry_observer, TelemetryDispatch):
                    self.telemetry_observer.close() if self._owns_dispatch else self.telemetry_observer.drain()
            finally:
                self._close_complete.set()
                for item in pending:
                    if not item["future"].done():
                        item["future"].set_exception(self._failure or self._unavailable())
        elif threading.current_thread() not in (self._reader, self._close_owner):
            self._close_complete.wait(timeout=2 * self.shutdown_timeout + 2)
            self._reader.join(timeout=1)
