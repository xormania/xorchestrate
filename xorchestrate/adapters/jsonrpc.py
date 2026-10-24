"""Bounded subprocess transport shared by out-of-process adapters.

The JSON-lines framing follows the same public protocol as the legacy
``devtools/rpc.py``; this implementation has no dependency on that runtime.
Commands come exclusively from operator configuration, never remote metadata.
"""

from __future__ import annotations

import json
import math
import os
import queue
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from copy import deepcopy
from typing import Any

from ..model import OrchestrationError


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
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON property")
            result[key] = value
        return result

    def invalid_constant(_):
        raise ValueError("Non-finite JSON number")

    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=unique_pairs, parse_constant=invalid_constant)
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


def run_json_process(options: dict, payload: dict, command_key: str = "command") -> dict:
    """Execute one JSON stdin/stdout exchange with bounded time and memory."""
    timeout = positive_number(options, "timeout", 30)
    shutdown_timeout = positive_number(options, "shutdown_timeout", 0.2)
    maximum = positive_integer(options, "max_output_bytes", 4 * 1024 * 1024)
    request = encode_json(payload) + b"\n"
    if len(request) > positive_integer(options, "max_input_bytes", 4 * 1024 * 1024):
        raise OrchestrationError("Subprocess input exceeds its configured bound.", "invalid_request", {"outcome": "not_started"})
    try:
        process = subprocess.Popen(**command_options(options, command_key), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
    except (OSError, ValueError) as exc:
        raise OrchestrationError("Could not start configured subprocess.", "provider_unavailable", {"outcome": "not_started"}) from exc
    output = bytearray()
    failure: list[Exception] = []
    finished = threading.Event()
    input_finished = threading.Event()

    def read_output():
        try:
            while True:
                block = process.stdout.read(min(65536, maximum + 1 - len(output)))
                if not block:
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
        try:
            result = decode_json(bytes(output))
        except OrchestrationError as exc:
            exc.details["outcome"] = "unknown"
            raise
        if not isinstance(result, dict):
            raise OrchestrationError("Subprocess result must be a JSON object.", "provider_protocol", {"outcome": "unknown"})
        return result
    finally:
        close_process(process, shutdown_timeout)
        reader.join(timeout=1)
        writer.join(timeout=1)


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
                 jsonrpc: bool = True, notification_handler: Callable | None = None):
        self.timeout = positive_number(options, "timeout", 30)
        self.shutdown_timeout = positive_number(options, "shutdown_timeout", 0.2)
        self.callback_timeout = positive_number(options, "callback_timeout", self.timeout)
        self.max_frame = positive_integer(options, "max_frame_bytes", 1024 * 1024)
        self.max_output = positive_integer(options, "max_output_bytes", 4 * 1024 * 1024)
        self.max_pending = positive_integer(options, "max_pending_messages", 1024)
        self.max_callback_history = positive_integer(options, "max_callback_history", 4096)
        worker_count = positive_integer(options, "callback_workers", 8)
        self.handler = request_handler
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
        try:
            self.process = subprocess.Popen(**command_options(options), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=subprocess.DEVNULL, bufsize=0)
        except (OSError, ValueError) as exc:
            raise OrchestrationError("Could not start configured subprocess.", "provider_unavailable", {"outcome": "not_started"}) from exc
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
        self.close()

    def _read(self):
        try:
            while not self._closed:
                line = self.process.stdout.readline(self.max_frame + 1)
                if not line:
                    if not self._closed:
                        raise OrchestrationError("Subprocess closed its output stream.", "provider_unavailable", {"outcome": "unknown"})
                    return
                if len(line) > self.max_frame or not line.endswith(b"\n"):
                    raise OrchestrationError("Subprocess exceeded the JSON-line frame bound or omitted its delimiter.", "provider_protocol")
                packet = decode_json(line)
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
                self._write(answer, time.monotonic() + self.timeout)
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
                self._write(answer, time.monotonic() + self.timeout)
            except BaseException as exc:
                if not self._closed:
                    self._fail(exc)
            finally:
                finished.set()
                timer.cancel()
                self._callback_queue.task_done()

    def _write(self, message: dict, deadline: float):
        with self._state:
            if self._closed:
                raise self._unavailable("not_started")
        data = encode_json(message) + b"\n"
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

    def request_async(self, method: str, params: dict | list | None = None, timeout: float | None = None) -> Future:
        duration = self.timeout if timeout is None else positive_number({"timeout": timeout}, "timeout", self.timeout)
        deadline = time.monotonic() + duration
        with self._state:
            if self._closed:
                raise self._unavailable("not_started")
            if len(self._pending) >= self.max_pending:
                raise OrchestrationError("Too many pending subprocess requests.", "provider_capacity", {"outcome": "not_started"})
            self._next_id += 1
            identifier = self._next_id
            packet = self._envelope(id=identifier, method=method, params={} if params is None else params)
            # Validate before publishing a pending request or writing any bytes.
            if len(encode_json(packet)) + 1 > self.max_frame:
                raise OrchestrationError("Request exceeds the configured frame bound.", "invalid_request", {"outcome": "not_started"})
            future = Future()
            # Cancellation cannot discard an already submitted effect or lose a
            # subsequent response. Consumers retain the uncertain operation.
            future.set_running_or_notify_cancel()
            self._pending[identifier] = {"future": future, "received": 0, "timer": None}
        try:
            self._write(packet, deadline)
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

    def request(self, method: str, params: dict | list | None = None, timeout: float | None = None) -> Any:
        try:
            return self.request_async(method, params, timeout).result()
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
            finally:
                self._close_complete.set()
                for item in pending:
                    if not item["future"].done():
                        item["future"].set_exception(self._failure or self._unavailable())
        elif threading.current_thread() not in (self._reader, self._close_owner):
            self._close_complete.wait(timeout=2 * self.shutdown_timeout + 2)
            self._reader.join(timeout=1)
