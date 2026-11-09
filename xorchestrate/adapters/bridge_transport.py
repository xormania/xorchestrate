"""Local bridge transport: owner-only Unix socket or FIFO streams and bounded peer RPC.

The stream is a local trust boundary; peer credentials are checked by the host.
"""
from __future__ import annotations

import hashlib
import json
import os
import select
import socket
import stat
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from pathlib import Path

from ..model import OrchestrationError
from .process import decode_json, positive_number


def copy_json(value):
    return json.loads(json.dumps(value, allow_nan=False))


def json_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def bridge_error(message, code="bridge_unavailable", outcome="not_started"):
    return OrchestrationError(message, code, {"outcome": outcome})


def positive_option(options, name, default):
    """``process.positive_integer`` with the bridge's ``not_started`` outcome detail."""
    value = options.get(name, default)
    if type(value) is not int or value < 1:
        raise bridge_error(f"{name} must be a positive integer.", "invalid_configuration")
    return value


class _FifoConnection:
    """A filesystem stream option for hosts where local sockets are unavailable."""

    def __init__(self, directory, server=False):
        self.directory = Path(directory)
        self.server = server
        self.closed = False
        flags = os.O_RDWR | os.O_NONBLOCK | os.O_NOFOLLOW
        read_name, write_name = ("to-host", "to-client") if server else ("to-client", "to-host")
        self.read_fd = os.open(self.directory / read_name, flags)
        try:
            self.write_fd = os.open(self.directory / write_name, flags)
        except Exception:
            os.close(self.read_fd)
            raise

    def fileno(self):
        return self.write_fd

    def send(self, data, flags=0):
        if self.closed or not self.directory.exists():
            raise OSError("closed")
        return os.write(self.write_fd, data)

    def recv(self, size):
        while not self.closed and self.directory.exists():
            try:
                if select.select([self.read_fd], [], [], 0.2)[0]:
                    data = os.read(self.read_fd, size)
                    if data:
                        return data
            except BlockingIOError:
                pass
            except (OSError, ValueError):
                break
        return b""

    def shutdown(self, *_):
        self.closed = True

    def close(self):
        self.closed = True
        for fd in (self.read_fd, self.write_fd):
            try:
                os.close(fd)
            except OSError:
                pass
        if not self.server:
            for name in ("to-host", "to-client"):
                try:
                    (self.directory / name).unlink()
                except FileNotFoundError:
                    pass
            try:
                self.directory.rmdir()
            except FileNotFoundError:
                pass


class FifoListener:
    def __init__(self, endpoint):
        self.endpoint = endpoint
        self.closed = False
        self.clients = []
        self.buffer = bytearray()
        endpoint.mkdir(mode=0o700)  # Refuse to replace an existing endpoint.
        os.mkfifo(endpoint / "attach", 0o600)
        self.fd = os.open(endpoint / "attach", os.O_RDWR | os.O_NONBLOCK | os.O_NOFOLLOW)

    def accept(self):
        while not self.closed:
            if b"\n" not in self.buffer:
                if not select.select([self.fd], [], [], 0.2)[0]:
                    continue
                self.buffer.extend(os.read(self.fd, 4096))
            while b"\n" in self.buffer:
                raw, _, rest = self.buffer.partition(b"\n")
                self.buffer = bytearray(rest)
                if len(raw) != 32 or any(byte not in b"0123456789abcdef" for byte in raw):
                    continue
                directory = self.endpoint / raw.decode()
                try:
                    metadata = directory.lstat()
                    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
                        continue
                    valid = all(stat.S_ISFIFO((directory / name).lstat().st_mode) for name in ("to-host", "to-client"))
                    if not valid:
                        continue
                    connection = _FifoConnection(directory, server=True)
                except OSError:
                    continue
                self.clients.append(directory)
                return connection, None
            if len(self.buffer) > 4096:
                self.buffer.clear()
        raise OSError("closed")

    def shutdown(self, *_):
        self.closed = True

    def close(self):
        self.closed = True
        os.close(self.fd)
        for directory in self.clients:
            for name in ("to-host", "to-client"):
                try:
                    (directory / name).unlink()
                except FileNotFoundError:
                    pass
            try:
                directory.rmdir()
            except FileNotFoundError:
                pass
        try:
            (self.endpoint / "attach").unlink()
            self.endpoint.rmdir()
        except FileNotFoundError:
            pass


def fifo_connect(endpoint):
    endpoint = Path(endpoint)
    metadata = endpoint.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
        raise bridge_error("FIFO endpoint must be an owner-only directory.", "bridge_permissions")
    nonce = uuid.uuid4().hex
    directory = endpoint / nonce
    directory.mkdir(mode=0o700)
    try:
        for name in ("to-host", "to-client"):
            os.mkfifo(directory / name, 0o600)
        connection = _FifoConnection(directory)
        fd = os.open(endpoint / "attach", os.O_WRONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        try:
            os.write(fd, (nonce + "\n").encode())
        finally:
            os.close(fd)
        return connection
    except Exception:
        for name in ("to-host", "to-client"):
            try:
                (directory / name).unlink()
            except FileNotFoundError:
                pass
        directory.rmdir()
        raise


class Peer:
    """Bounded bidirectional RPC; callbacks never run on the receive thread."""

    def __init__(self, connection, handler, options=None):
        options = options or {}
        self.socket = connection
        self.handler = handler
        self.timeout = positive_number(options, "timeout", 30)
        self.max_frame = positive_option(options, "max_frame_bytes", 1024 * 1024)
        self.max_pending = positive_option(options, "max_pending", 128)
        workers = positive_option(options, "workers", 8)
        self._capacity = threading.BoundedSemaphore(workers)
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="session-bridge")
        self._pending = {}
        self._seen = set()
        self._lock = threading.RLock()
        self._writer = threading.Lock()
        self.closed = False
        self.reader = threading.Thread(target=self._read, daemon=True, name="session-bridge-reader")
        self.reader.start()

    def _write(self, packet):
        data = (json.dumps(packet, allow_nan=False, separators=(",", ":")) + "\n").encode()
        if len(data) > self.max_frame:
            raise bridge_error("Bridge frame exceeds the configured limit.", "bridge_protocol")
        with self._writer:
            if self.closed:
                raise bridge_error("Bridge connection is closed.")
            try:
                deadline = time.monotonic() + self.timeout
                remaining = memoryview(data)
                while remaining:
                    delay = deadline - time.monotonic()
                    if delay <= 0 or not select.select([], [self.socket], [], delay)[1]:
                        raise OSError("write timeout")
                    try:
                        sent = self.socket.send(remaining, socket.MSG_DONTWAIT)
                    except BlockingIOError:
                        continue
                    if sent <= 0:
                        raise OSError("closed")
                    remaining = remaining[sent:]
            except OSError as exc:
                raise bridge_error("Bridge write outcome is unknown.", outcome="unknown") from exc

    def request(self, method, params):
        key = uuid.uuid4().hex
        future = Future()
        with self._lock:
            if self.closed:
                raise bridge_error("Bridge connection is closed.")
            if len(self._pending) >= self.max_pending:
                raise bridge_error("Bridge pending request limit reached.", "bridge_capacity")
            self._pending[key] = future
        try:
            self._write({"id": key, "method": method, "params": copy_json(params)})
        except Exception:
            with self._lock:
                self._pending.pop(key, None)
            raise
        try:
            return future.result(self.timeout)
        except TimeoutError as exc:
            # Keep this request registered. A late response still has its original
            # identity; another observation/control request may proceed meanwhile.
            raise bridge_error("Bridge response timed out; the effect is unresolved.", "bridge_timeout", "unknown") from exc

    def _read(self):
        buffer = bytearray()
        try:
            while not self.closed:
                data = self.socket.recv(min(65536, self.max_frame))
                if not data:
                    break
                buffer.extend(data)
                while b"\n" in buffer:
                    raw, _, rest = buffer.partition(b"\n")
                    buffer = bytearray(rest)
                    if len(raw) > self.max_frame:
                        raise ValueError("frame limit")
                    packet = decode_json(bytes(raw))
                    if not isinstance(packet, dict) or not isinstance(packet.get("id"), str):
                        raise ValueError("invalid envelope")
                    key = packet["id"]
                    if "method" in packet:
                        if key in self._seen or len(self._seen) >= self.max_pending * 1024:
                            raise ValueError("duplicate request or connection request limit")
                        self._seen.add(key)
                        if not self._capacity.acquire(blocking=False):
                            self._write({"id": key, "error": {"code": "bridge_capacity", "message": "Bridge worker limit reached.", "details": {"outcome": "not_started"}}})
                        else:
                            self._executor.submit(self._dispatch, packet)
                    else:
                        with self._lock:
                            future = self._pending.pop(key, None)
                        if future is None:
                            raise ValueError("unmatched response")
                        if "error" in packet:
                            error = packet["error"]
                            future.set_exception(OrchestrationError(error.get("message", "Bridge request failed."), error.get("code", "bridge_error"), error.get("details", {})))
                        elif "result" in packet:
                            future.set_result(packet["result"])
                        else:
                            raise ValueError("missing response")
                if len(buffer) > self.max_frame:
                    raise ValueError("frame limit")
        except (OSError, ValueError, TypeError, OrchestrationError):
            pass
        finally:
            self.close()

    def _dispatch(self, packet):
        try:
            result = self.handler(packet["method"], packet.get("params", {}))
            response = {"id": packet["id"], "result": result}
        except Exception as exc:
            response = {"id": packet["id"], "error": {"code": getattr(exc, "code", "bridge_error"),
                "message": str(exc) if isinstance(exc, OrchestrationError) else "Bridge request did not return a verified result.",
                "details": getattr(exc, "details", {"outcome": "unknown"})}}
        try:
            self._write(response)
        except Exception:
            self.close()
        finally:
            self._capacity.release()

    def close(self):
        with self._lock:
            if self.closed:
                return
            self.closed = True
            pending, self._pending = self._pending, {}
        try:
            self.socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        # FIFO shutdown wakes its bounded receive loop without closing the file
        # descriptors. Let that reader leave before they can be reused by a new
        # attachment; otherwise an old reader can consume the new peer's frame.
        if threading.current_thread() is not self.reader:
            self.reader.join()
        self.socket.close()
        for future in pending.values():
            if not future.done():
                future.set_exception(bridge_error("Bridge connection ended before the outcome was observed.", outcome="unknown"))
        self._executor.shutdown(wait=False, cancel_futures=True)


