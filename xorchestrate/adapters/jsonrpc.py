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


def encode_json(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("utf-8")
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

    reader = threading.Thread(target=read_output, daemon=True)
    writer = threading.Thread(target=write_input, daemon=True)
    deadline = time.monotonic() + timeout
    reader.start()
    writer.start()
    try:
        if not finished.wait(max(0, deadline - time.monotonic())):
            raise OrchestrationError("Subprocess exceeded its execution deadline.", "provider_timeout", {"outcome": "unknown"})
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
    """A serialized JSON-RPC client with bounded asynchronous receive buffering.

    Server requests are answered while a request or poll is in progress. A
    request_handler receives (method, params) and returns a JSON result. It must
    not recursively issue requests on this connection. Unsupported requests are
    rejected. No protocol notification or remote metadata grants authority.
    """

    def __init__(self, options: dict, request_handler: Callable | None = None, *, jsonrpc: bool = True):
        self.timeout = positive_number(options, "timeout", 30)
        self.shutdown_timeout = positive_number(options, "shutdown_timeout", 0.2)
        self.max_frame = positive_integer(options, "max_frame_bytes", 1024 * 1024)
        self.max_output = positive_integer(options, "max_output_bytes", 4 * 1024 * 1024)
        self.max_pending = positive_integer(options, "max_pending_messages", 1024)
        self.handler = request_handler
        self.jsonrpc = jsonrpc
        self._incoming: queue.Queue = queue.Queue(maxsize=self.max_pending)
        self._buffer_lock = threading.Lock()
        self._operation_lock = threading.RLock()
        self._buffered = 0
        self._received_total = 0
        self._notifications: list[dict] = []
        self._notification_bytes = 0
        self._failure: OrchestrationError | None = None
        self._next_id = 0
        self._closed = False
        try:
            self.process = subprocess.Popen(**command_options(options), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        except (OSError, ValueError) as exc:
            raise OrchestrationError("Could not start configured subprocess.", "provider_unavailable", {"outcome": "not_started"}) from exc
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self):
        try:
            while not self._closed:
                line = self.process.stdout.readline(self.max_frame + 1)
                if not line:
                    self._incoming.put_nowait((None, 0))
                    return
                if len(line) > self.max_frame or not line.endswith(b"\n"):
                    raise OrchestrationError("Subprocess exceeded the JSON-line frame bound or omitted its delimiter.", "provider_protocol")
                with self._buffer_lock:
                    self._buffered += len(line)
                    if self._buffered > self.max_output:
                        raise OrchestrationError("Subprocess exceeded the receive buffer bound.", "provider_protocol")
                self._incoming.put_nowait((decode_json(line), len(line)))
        except (OSError, ValueError, queue.Full, OrchestrationError) as exc:
            if not self._closed:
                self._failure = exc if isinstance(exc, OrchestrationError) else OrchestrationError("Subprocess communication failed or exceeded its queue bound.", "provider_protocol")
                _signal_group(self.process, signal.SIGKILL)

    def _write(self, message: dict, deadline: float):
        if self._closed:
            raise OrchestrationError("Subprocess connection is closed.", "provider_unavailable", {"outcome": "not_started"})
        data = encode_json(message) + b"\n"
        if len(data) > self.max_frame:
            raise OrchestrationError("Request exceeds the configured frame bound.", "invalid_request", {"outcome": "not_started"})
        done = threading.Event()
        failures = []

        def send():
            try:
                remaining = memoryview(data)
                while remaining:
                    written = self.process.stdin.write(remaining)
                    remaining = remaining[written:]
            except (OSError, ValueError) as exc:
                failures.append(exc)
            finally:
                done.set()

        writer = threading.Thread(target=send, daemon=True)
        writer.start()
        if not done.wait(max(0, deadline - time.monotonic())):
            self.close()
            raise OrchestrationError("Subprocess did not accept input before the deadline.", "provider_timeout", {"outcome": "unknown"})
        if failures:
            self.close()
            raise OrchestrationError("Subprocess input stream is unavailable.", "provider_unavailable", {"outcome": "unknown"}) from failures[0]

    def _envelope(self, **fields):
        return {"jsonrpc": "2.0", **fields} if self.jsonrpc else fields

    def _receive(self, deadline: float, block: bool = True):
        while True:
            if self._failure:
                raise self._failure
            wait = max(0, deadline - time.monotonic())
            try:
                packet, size = self._incoming.get(timeout=min(wait, 0.05)) if block else self._incoming.get_nowait()
            except queue.Empty:
                if not block:
                    return None
                if wait <= 0:
                    raise OrchestrationError("Subprocess request exceeded its deadline.", "provider_timeout", {"outcome": "unknown"})
                continue
            with self._buffer_lock:
                self._buffered -= size
            self._received_total += size
            if packet is None:
                raise OrchestrationError("Subprocess closed its output stream.", "provider_unavailable", {"outcome": "unknown"})
            if not isinstance(packet, dict) or (self.jsonrpc and packet.get("jsonrpc") != "2.0"):
                raise OrchestrationError("Invalid JSON-RPC envelope.", "provider_protocol")
            return packet

    def _handle_message(self, packet: dict, deadline: float) -> bool:
        if "method" not in packet:
            return False
        if not isinstance(packet["method"], str) or not packet["method"] or "result" in packet or "error" in packet:
            raise OrchestrationError("Invalid JSON-RPC request or notification.", "provider_protocol")
        params = packet.get("params", {})
        if not isinstance(params, (dict, list)):
            raise OrchestrationError("Invalid JSON-RPC parameters.", "provider_protocol")
        if "id" in packet:
            identifier = packet["id"]
            if type(identifier) not in (int, str):
                raise OrchestrationError("Invalid JSON-RPC request identifier.", "provider_protocol")
            try:
                if packet["method"] == "ping":
                    result = {}
                elif self.handler is not None:
                    result = self.handler(packet["method"], params)
                else:
                    raise OrchestrationError("Client does not support this request.", "unsupported_method")
                answer = self._envelope(id=identifier, result=result)
            except OrchestrationError as exc:
                answer = self._envelope(id=identifier, error={"code": -32601 if exc.code == "unsupported_method" else -32603, "message": str(exc)})
            self._write(answer, deadline)
        else:
            size = len(encode_json(packet))
            self._notification_bytes += size
            if len(self._notifications) >= self.max_pending or self._notification_bytes > self.max_output:
                raise OrchestrationError("Subprocess notification buffer exceeded its bound.", "provider_protocol")
            self._notifications.append(packet)
        return True

    def request(self, method: str, params: dict | list | None = None, timeout: float | None = None) -> Any:
        with self._operation_lock:
            deadline = time.monotonic() + (self.timeout if timeout is None else positive_number({"timeout": timeout}, "timeout", self.timeout))
            self._next_id += 1
            identifier = self._next_id
            received_before = self._received_total
            try:
                self._write(self._envelope(id=identifier, method=method, params={} if params is None else params), deadline)
                while True:
                    packet = self._receive(deadline)
                    if self._received_total - received_before > self.max_output:
                        raise OrchestrationError("Subprocess response exceeded its output bound.", "provider_protocol")
                    if self._handle_message(packet, deadline):
                        continue
                    if type(packet.get("id")) is not int or packet["id"] != identifier or ("result" in packet) == ("error" in packet):
                        raise OrchestrationError("Unexpected or malformed JSON-RPC response.", "provider_protocol")
                    if "error" in packet:
                        error = packet["error"]
                        if not isinstance(error, dict) or type(error.get("code")) is not int or not isinstance(error.get("message"), str):
                            raise OrchestrationError("Malformed JSON-RPC error.", "provider_protocol")
                        # A protocol error can follow a partial side effect; an
                        # error response alone does not prove rollback or that
                        # execution never began. Keep the evidence for recovery.
                        raise OrchestrationError("Subprocess returned a JSON-RPC error.", "provider_error", {"outcome": "unknown", "remote_error": error})
                    return packet["result"]
            except OrchestrationError as exc:
                if exc.code != "provider_error":
                    exc.details.setdefault("outcome", "unknown")
                    self.close()
                raise

    def notify(self, method: str, params: dict | None = None) -> None:
        with self._operation_lock:
            self._write(self._envelope(method=method, **({"params": params} if params is not None else {})), time.monotonic() + self.timeout)

    def poll_notifications(self) -> list[dict]:
        try:
            return self._poll_notifications()
        except OrchestrationError:
            self.close()
            raise

    def _poll_notifications(self) -> list[dict]:
        with self._operation_lock:
            deadline = time.monotonic() + self.timeout
            received_before = self._received_total
            while True:
                packet = self._receive(deadline, block=False)
                if packet is None:
                    break
                if self._received_total - received_before > self.max_output or time.monotonic() >= deadline:
                    self.close()
                    raise OrchestrationError("Subprocess polling exceeded its configured bounds.", "provider_protocol")
                if not self._handle_message(packet, deadline):
                    self.close()
                    raise OrchestrationError("Unexpected JSON-RPC response without a pending request.", "provider_protocol")
            result, self._notifications = self._notifications, []
            self._notification_bytes = 0
            return result

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            close_process(self.process, self.shutdown_timeout)
            if threading.current_thread() is not self._reader:
                self._reader.join(timeout=1)
