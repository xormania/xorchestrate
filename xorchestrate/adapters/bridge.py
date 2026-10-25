"""A local, operator-owned native session host with replaceable client leases.

The Unix socket is a local trust boundary (0600, same-user peer check). It does
not attach to arbitrary PIDs. The host owns its native driver; attached clients
own only a connection. A signed host receipt permits reconnecting the same
conversation after a client restart without accepting caller-created identities.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import queue
import select
import socket
import stat
import struct
import tempfile
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from ..model import OrchestrationError
from .jsonrpc import decode_json, positive_number


def _copy(value):
    return json.loads(json.dumps(value, allow_nan=False))


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _call_binding(native_context):
    return ("native_call", json.dumps([native_context.get("native_id"), native_context.get("incarnation"),
        native_context.get("call_id")], separators=(",", ":")))


def _error(message, code="bridge_unavailable", outcome="not_started"):
    return OrchestrationError(message, code, {"outcome": outcome})


def _positive(options, name, default):
    value = options.get(name, default)
    if type(value) is not int or value < 1:
        raise _error(f"{name} must be a positive integer.", "invalid_configuration")
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


class _FifoListener:
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


def _fifo_connect(endpoint):
    endpoint = Path(endpoint)
    metadata = endpoint.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
        raise _error("FIFO endpoint must be an owner-only directory.", "bridge_permissions")
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


class _Peer:
    """Bounded bidirectional RPC; callbacks never run on the receive thread."""

    def __init__(self, connection, handler, options=None):
        options = options or {}
        self.socket = connection
        self.handler = handler
        self.timeout = positive_number(options, "timeout", 30)
        self.max_frame = _positive(options, "max_frame_bytes", 1024 * 1024)
        self.max_pending = _positive(options, "max_pending", 128)
        workers = _positive(options, "workers", 8)
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
            raise _error("Bridge frame exceeds the configured limit.", "bridge_protocol")
        with self._writer:
            if self.closed:
                raise _error("Bridge connection is closed.")
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
                raise _error("Bridge write outcome is unknown.", outcome="unknown") from exc

    def request(self, method, params):
        key = uuid.uuid4().hex
        future = Future()
        with self._lock:
            if self.closed:
                raise _error("Bridge connection is closed.")
            if len(self._pending) >= self.max_pending:
                raise _error("Bridge pending request limit reached.", "bridge_capacity")
            self._pending[key] = future
        try:
            self._write({"id": key, "method": method, "params": _copy(params)})
        except Exception:
            with self._lock:
                self._pending.pop(key, None)
            raise
        try:
            return future.result(self.timeout)
        except TimeoutError as exc:
            # Keep this request registered. A late response still has its original
            # identity; another observation/control request may proceed meanwhile.
            raise _error("Bridge response timed out; the effect is unresolved.", "bridge_timeout", "unknown") from exc

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
                future.set_exception(_error("Bridge connection ended before the outcome was observed.", outcome="unknown"))
        self._executor.shutdown(wait=False, cancel_futures=True)


class SessionBridgeHost:
    """Keep one configured native conversation alive across client attachments."""

    def __init__(self, driver, endpoint, specification=None, capabilities=(), options=None):
        self.driver = driver
        self.endpoint = Path(endpoint).expanduser().absolute()
        self.options = _copy(options or {})
        state_path = self.options.get("state_path")
        self.state_path = Path(state_path).expanduser().absolute() if state_path else None
        self.transport = self.options.get("transport", "unix")
        if self.transport not in {"unix", "fifo"}:
            raise _error("Bridge transport must be unix or fifo.", "invalid_configuration")
        self.max_events = _positive(self.options, "max_events", 10000)
        self.max_connections = _positive(self.options, "max_connections", 64)
        self.host_id = uuid.uuid4().hex
        self._secret = os.urandom(32)
        self._lock = threading.RLock()
        self._peers = set()
        self._active = None
        self._active_ready = False
        self._lease = None
        self._context = {}
        self._events = []
        self._sequence = 0
        self._effects = {}
        self._bindings = {}
        self._effect_context = threading.local()
        self._closed = False
        self._outgoing = queue.Queue(maxsize=self.max_events)
        self.capabilities = tuple(capabilities)
        self.catalog_identity = _digest([asdict(c) for c in self.capabilities])
        if self.capabilities:
            bind = getattr(driver, "bind_application_tools", None)
            if not callable(bind):
                raise _error("Native driver cannot expose configured application tools.", "invalid_configuration")
            bind(list(self.capabilities), self._callback)
            bind_replay = getattr(driver, "bind_application_replay", None)
            if callable(bind_replay):
                bind_replay(self._replay_callback)
        observer = getattr(driver, "bind_observer", None)
        if callable(observer):
            observer(self._observe)
        saved = self._load_state()
        if saved:
            resume = getattr(driver, "resume", None)
            if not callable(resume):
                raise _error("Configured native driver cannot recover the saved host conversation.", "unsupported_operation")
            self.host_id = saved["host_id"]
            self._secret = bytes.fromhex(saved["secret"])
            self._effects = saved.get("effects", {})
            for effect in self._effects.values():
                if effect["status"] == "pending":
                    effect["status"] = "unknown"
                    effect["recovery_reason"] = "host_restarted_before_effect_outcome"
            self._events = saved.get("events", [])
            self._sequence = saved.get("sequence", 0)
            self._bindings = {(field, value): lease for field, value, lease in saved.get("bindings", [])}
            # Older host snapshots indexed call IDs without native incarnation.
            # Recover the qualified bindings from their retained observations;
            # never reinterpret those calls as calls from the resumed driver.
            for event in self._events:
                if event.get("call_id"):
                    binding = _call_binding({**event, "incarnation": event.get("native_incarnation")})
                    self._bindings.setdefault(binding, event.get("incarnation"))
            self.backend = resume(deepcopy(saved["backend"]), _copy(specification or {}))
            if self.backend.get("native_id") != saved["backend"].get("native_id"):
                driver.close()
                raise _error("Recovered native conversation identity changed.", "session_identity", "unknown")
        else:
            self.backend = driver.start(_copy(specification or {}))
        self.configuration_id = self.backend.get("configuration_id", _digest({"driver": type(driver).__module__ + ":" + type(driver).__name__, "options": getattr(driver, "options", {})}))
        self._socket = None
        try:
            self.endpoint.parent.mkdir(parents=True, exist_ok=True)
            # Never unlink an operator's existing socket or unrelated file.
            if self.transport == "fifo":
                self._socket = _FifoListener(self.endpoint)
            else:
                self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self._socket.bind(str(self.endpoint))
                os.chmod(self.endpoint, 0o600)
                self._socket.listen(16)
            self._save_state()
        except Exception:
            if self._socket is not None:
                self._socket.close()
            driver.close()
            raise
        self.thread = threading.Thread(target=self._serve, daemon=True, name="session-bridge-host")
        self.thread.start()
        self.observer_thread = threading.Thread(target=self._forward_observations, daemon=True, name="session-bridge-observer")
        self.observer_thread.start()

    def _load_state(self):
        if self.state_path is None:
            return None
        try:
            metadata = self.state_path.lstat()
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
            raise _error("Host state must be an owner-only regular file.", "bridge_permissions")
        if metadata.st_size > 64 * 1024 * 1024:
            raise _error("Host state exceeds the configured safety bound.", "bridge_capacity")
        fd = os.open(self.state_path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as source:
            return decode_json(source.read())

    def _save_state(self):
        if self.state_path is None or not hasattr(self, "backend"):
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        state = {"host_id": self.host_id, "secret": self._secret.hex(), "backend": self.backend,
            "effects": self._effects, "events": self._events, "sequence": self._sequence,
            "bindings": [[field, value, lease] for (field, value), lease in self._bindings.items()]}
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.state_path.parent, prefix=".bridge-state-", delete=False) as target:
                temporary = Path(target.name)
                os.fchmod(target.fileno(), 0o600)
                target.write(json.dumps(state, allow_nan=False, separators=(",", ":")).encode())
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, self.state_path)
            directory = os.open(self.state_path.parent, os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _serve(self):
        while not self._closed:
            try:
                connection, _ = self._socket.accept()
            except OSError:
                break
            if self.transport == "unix" and hasattr(socket, "SO_PEERCRED"):
                _, uid, _ = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                if uid != os.getuid():
                    connection.close()
                    continue
            with self._lock:
                self._peers = {item for item in self._peers if not item.closed}
                if len(self._peers) >= self.max_connections:
                    connection.close()
                    continue
            holder = {}
            ready = threading.Event()

            def handle(method, params, holder=holder, ready=ready):
                ready.wait()
                return self._request(holder["peer"], method, params)

            peer = _Peer(connection, handle, self.options)
            holder["peer"] = peer
            with self._lock:
                self._peers = {item for item in self._peers if not item.closed}
                self._peers.add(peer)
            ready.set()

    def _token(self, source=None):
        value = {"host_id": self.host_id, "native_id": self.backend["native_id"], "configuration_id": self.configuration_id}
        if source is not None:
            value = {key: source.get(key) for key in value}
        return hmac.new(self._secret, _digest(value).encode(), hashlib.sha256).hexdigest()

    def _receipt(self):
        return {"host_id": self.host_id, "native_id": self.backend["native_id"], "protocol": "bridge",
            "native_protocol": self.backend.get("protocol"), "native_incarnation": self.backend.get("incarnation"),
            "incarnation": self._lease, "ownership": "external", "connection_state": "connected", "resumable": True,
            "configuration_id": self.configuration_id, "settings": deepcopy(self.backend.get("settings", {"requested": {}, "resolved": {}, "observed": {}})),
            "resume_token": self._token(), "catalog_identity": self.catalog_identity}

    def _check(self, peer, params, *, outcome="not_started"):
        if peer is not self._active or params.get("incarnation") != self._lease:
            raise _error("The bridge connection was detached or replaced.", "stale_connection", outcome)

    def _request(self, peer, method, params):
        if not isinstance(params, dict):
            raise _error("Bridge parameters must be an object.", "bridge_protocol")
        with self._lock:
            if method == "attach":
                if params.get("catalog_identity") != self.catalog_identity:
                    raise _error("Attached application tool catalog differs from the host catalog.", "configuration_changed")
                source = params.get("source")
                if source is not None:
                    if not isinstance(source, dict) or source.get("host_id") != self.host_id or source.get("native_id") != self.backend["native_id"] or not hmac.compare_digest(str(source.get("resume_token", "")), self._token(source)):
                        raise _error("The source receipt does not identify this host conversation.", "session_identity")
                    if source.get("configuration_id") != self.configuration_id:
                        raise _error("The source configuration no longer matches this host.", "configuration_changed")
                context = params.get("context", {})
                if not isinstance(context, dict):
                    raise _error("Attach context must be an object.", "bridge_protocol")
                self._active, self._lease = peer, uuid.uuid4().hex
                self._active_ready = False
                self._context = _copy(context)
                return self._receipt()
            self._check(peer, params)
            lease = self._lease
            if method == "activate":
                self._active_ready = True
                return {"active": True}
            if method == "detach":
                self._active = None
                return {"detached": True, "terminated": False, "native_id": self.backend["native_id"], "incarnation": lease}
            if method == "events":
                after = params.get("after", 0)
                if type(after) is not int or after < 0:
                    raise _error("Observation cursor must be nonnegative.", "bridge_protocol")
                return {"events": deepcopy([event for event in self._events if event["sequence"] > after]), "last_sequence": self._sequence}
            if method == "inspect":
                return {"receipt": self._receipt(), "effects": deepcopy(self._effects)}
            if method not in {"send", "deliver", "poll"}:
                raise _error("Unsupported bridge operation.", "unsupported_operation")
            key = params.get("operation_id")
            if not isinstance(key, str) or not key:
                raise _error("A bridge operation identity is required.", "bridge_protocol")
            identity = _digest({"method": method, "message": params.get("message"), "incarnation": lease})
            existing = self._effects.get(key)
            if existing:
                if existing["identity"] != identity:
                    raise _error("Bridge operation identity was reused for another effect.", "conflict")
                if existing["status"] == "completed":
                    return deepcopy(existing["result"])
                raise _error("The bridge effect remains unresolved.", "operation_unresolved", "unknown")
            if len(self._effects) >= self.max_events:
                raise _error("Host effect retention limit reached; inspect and rotate the host.", "bridge_capacity")
            self._effects[key] = {"identity": identity, "incarnation": lease, "status": "pending"}
            message = _copy(params.get("message", {}))
            if not isinstance(message, dict):
                raise _error("Session message must be an object.", "bridge_protocol")
            if message.get("attempt_id"):
                self._bindings[("attempt_id", message["attempt_id"])] = lease
            self._save_state()
        try:
            self._effect_context.lease = lease
            if method == "poll":
                result = self.driver.poll(deepcopy(self.backend))
            else:
                function = getattr(self.driver, method, None)
                if not callable(function):
                    raise _error("Native driver does not implement this operation.", "unsupported_operation")
                result = function(deepcopy(self.backend), message)
        except Exception as exc:
            with self._lock:
                self._effects[key].update({"status": "unknown" if getattr(exc, "details", {}).get("outcome") not in {"not_started", "failed"} else "failed", "error": getattr(exc, "code", type(exc).__name__)})
                self._save_state()
            raise
        finally:
            self._effect_context.lease = None
        with self._lock:
            if isinstance(result, dict):
                for field in ("turn_id", "prompt_id", "submission_id", "call_id"):
                    if result.get(field):
                        self._bindings[(field, result[field])] = lease
            self._effects[key].update({"status": "completed", "result": deepcopy(result)})
            self._save_state()
            # Retain the result on the host even if a new attachment replaced the
            # issuing connection while the native operation was in progress.
            self._check(peer, params, outcome="unknown")
        return result

    def _replay_callback(self, action, arguments, native_context, idempotency_key=None):
        return self._callback(action, arguments, native_context, idempotency_key, replay_only=True)

    def _callback(self, action, arguments, native_context, idempotency_key=None, *, replay_only=False):
        with self._lock:
            peer, lease = self._active, self._lease
            if peer is None or peer.closed or not self._active_ready:
                raise _error("No application is attached to receive native tool calls.", "application_detached", "unknown")
            call_id = native_context.get("call_id")
            if not isinstance(call_id, str) or not call_id:
                raise _error("Native callback lacks a call identity.", "missing_call_identity", "unknown")
            if (native_context.get("native_id") != self.backend["native_id"]
                    or native_context.get("incarnation") != self.backend.get("incarnation")):
                raise _error("Native callback belongs to another conversation incarnation.", "session_identity", "unknown")
            call_key = _call_binding(native_context)
            # Observation is the first authority binding, not dispatch time. A
            # reconnect may replace actor/work as well as the connection. Never
            # lend that replacement authority to a call already seen elsewhere.
            bound_lease = self._bindings.get(call_key, getattr(self._effect_context, "lease", None) or lease)
            if bound_lease != lease:
                raise _error("Native callback belongs to a detached or replaced application connection.", "stale_connection", "unknown")
            context = {**deepcopy(self._context), "native_id": self.backend["native_id"], "incarnation": lease,
                "native_incarnation": native_context.get("incarnation"), "host_id": self.host_id,
                "call_id": call_id, "action": action}
            if call_id:
                self._bindings.setdefault(call_key, lease)
                self._save_state()
        try:
            result = peer.request("application_tool", {"action": action, "arguments": arguments, "context": context,
                "idempotency_key": idempotency_key, "replay_only": replay_only})
        except OrchestrationError as error:
            # A callback rejected before application execution says nothing
            # about other native effects in the enclosing send/delivery.
            if error.details.get("outcome") in {"not_started", "failed"}:
                raise OrchestrationError(str(error), error.code, {**error.details, "outcome": "unknown",
                    "callback_outcome": error.details["outcome"]}) from error
            raise
        with self._lock:
            if peer is not self._active or lease != self._lease:
                raise _error("Native callback completed after its application connection was replaced.", "stale_connection", "unknown")
        return result

    def _observe(self, native_context, observation):
        with self._lock:
            lease = getattr(self._effect_context, "lease", None)
            identities = {}
            # A native call has its own identity. Reusing a turn/prompt label
            # must not bind a fresh call to an obsolete attachment; the host
            # dispatches that call through the currently attached application.
            fields = ("call_id",) if observation.get("call_id") else ("attempt_id", "turn_id", "prompt_id", "submission_id")
            for field in fields:
                if observation.get(field):
                    identities[field] = observation[field]
                    key = _call_binding(observation) if field == "call_id" else (field, observation[field])
                    lease = lease or self._bindings.get(key)
            payload = observation.get("payload", {})
            if not observation.get("call_id") and isinstance(payload, dict) and payload.get("turnId"):
                identities["turn_id"] = payload["turnId"]
                lease = lease or self._bindings.get(("turn_id", payload["turnId"]))
            message = observation.get("message", {})
            if not observation.get("call_id") and isinstance(message, dict) and message.get("attempt_id"):
                identities["attempt_id"] = message["attempt_id"]
                lease = lease or self._bindings.get(("attempt_id", message["attempt_id"]))
            result = observation.get("result", {})
            if not observation.get("call_id") and isinstance(result, dict) and isinstance(result.get("turn"), dict) and result["turn"].get("id"):
                identities["turn_id"] = result["turn"]["id"]
                lease = lease or self._bindings.get(("turn_id", identities["turn_id"]))
            # Tool observations arrive before their callback; bind them once,
            # including an unbound observation made before any attachment.
            # Completion without a known turn stays
            # unbound rather than becoming evidence for a replacement lease.
            if lease is None and observation.get("kind") in {"native_tool", "native_tool_call", "tool_call", "native_call"}:
                call_key = _call_binding(observation)
                lease = self._bindings.get(call_key, self._lease)
                if observation.get("call_id"):
                    self._bindings.setdefault(call_key, lease)
            conflict = hasattr(self, "backend") and (
                observation.get("native_id") != self.backend.get("native_id")
                or observation.get("incarnation") != self.backend.get("incarnation"))
            # An asynchronous native tool may be the first lease-qualified
            # observation of its enclosing turn. Preserve that relation so its
            # later completion can be forwarded. A fresh call never transfers
            # an already-owned turn, and a stale call cannot bind a new turn.
            if (not conflict and lease and lease == self._lease
                    and observation.get("kind") in {"native_tool", "native_tool_call", "tool_call", "native_call"}
                    and observation.get("call_id")
                    and self._bindings.get(_call_binding(observation)) == lease):
                for field in ("turn_id", "prompt_id"):
                    turn = observation.get(field)
                    if isinstance(turn, str) and turn:
                        self._bindings.setdefault((field, turn), lease)
            if lease:
                for field, value in identities.items():
                    key = _call_binding(observation) if field == "call_id" else (field, value)
                    previous = self._bindings.get(key)
                    if key in self._bindings and previous != lease:
                        conflict = True
                    else:
                        self._bindings[key] = lease
            if conflict:
                lease = None
            self._sequence += 1
            event = {**deepcopy(observation), "native_incarnation": observation.get("incarnation"),
                "incarnation": lease, "host_id": self.host_id, "sequence": self._sequence,
                "bridge_bound": lease is not None, "bridge_identity_conflict": conflict,
                "at": observation.get("at", datetime.now(timezone.utc).isoformat())}
            self._events.append(event)
            recovered = []
            if observation.get("kind") == "native_submission" and lease:
                for older in self._events:
                    if older.get("bridge_bound") or older.get("bridge_identity_conflict") or older.get("native_incarnation") != event.get("native_incarnation"):
                        continue
                    matches = any(older.get(field) == value for field, value in identities.items() if field in {"turn_id", "prompt_id", "submission_id"})
                    if matches:
                        older.update({"incarnation": lease, "bridge_bound": True, "bridge_binding_reason": "matched_native_submission"})
                        recovered.append(deepcopy(older))
            if len(self._events) > self.max_events:
                # A gap is explicit and cannot be mistaken for complete audit.
                self._events.pop(0)
            self._save_state()
            if self._active is not None and self._active_ready and lease == self._lease:
                try:
                    for item in [*recovered, event]:
                        self._outgoing.put_nowait((self._active, lease, deepcopy(self._context), deepcopy(item)))
                except queue.Full:
                    # Events are retained for explicit collection. Queue pressure
                    # does not turn an observation into successful delivery.
                    pass

    def _forward_observations(self):
        while not self._closed:
            try:
                peer, lease, context, event = self._outgoing.get(timeout=0.2)
            except queue.Empty:
                continue
            with self._lock:
                current = peer is self._active and lease == self._lease
            if current:
                try:
                    peer.request("observation", {"context": context, "observation": event})
                except OrchestrationError:
                    pass  # Still retained; poll exposes the original observation.

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self._socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        if threading.current_thread() is not self.thread:
            self.thread.join()
        self._socket.close()
        with self._lock:
            peers = list(self._peers)
            self._active = None
        for peer in peers:
            peer.close()
        self.driver.close()
        try:
            if self.transport == "unix":
                self.endpoint.unlink()
        except FileNotFoundError:
            pass


class BridgeSessionDriver:
    """Attach to a trusted operator bridge; stopping means detaching, never kill."""

    def __init__(self, options):
        self.options = _copy(options)
        endpoint = self.options.get("endpoint")
        if not isinstance(endpoint, str) or not endpoint:
            raise _error("A configured bridge endpoint is required.", "invalid_configuration")
        self.endpoint = str(Path(endpoint).expanduser().absolute())
        self.transport = self.options.get("transport", "unix")
        if self.transport not in {"unix", "fifo"}:
            raise _error("Bridge transport must be unix or fifo.", "invalid_configuration")
        self._instance = uuid.uuid4().hex
        self._sessions = {}
        self._capabilities = ()
        self._callback = None
        self._application_replay = None
        self._observer = None
        self._lock = threading.RLock()
        self._closed = False

    def bind_application_tools(self, capabilities, callback):
        if self._sessions:
            raise _error("Bind application tools before attaching.", "conflict")
        self._capabilities, self._callback = tuple(capabilities), callback

    def bind_application_replay(self, callback):
        if self._sessions:
            raise _error("Bind replay validation before attaching.", "conflict")
        if not callable(callback):
            raise _error("Application replay validator must be callable.", "invalid_configuration")
        self._application_replay = callback

    def bind_observer(self, observer):
        if self._sessions:
            raise _error("Bind observations before attaching.", "conflict")
        self._observer = observer

    def _incoming(self, local, method, params):
        if method == "observation":
            observation = params.get("observation", {})
            receipt = local.get("receipt")
            if receipt is None or observation.get("native_id") != receipt["native_id"] or observation.get("incarnation") != receipt["incarnation"] or observation.get("host_id") != receipt["host_id"]:
                raise _error("Observation does not match this bridge attachment.", "session_identity")
            self._emit_observation(local, observation)
            return {"retained": True}
        if method != "application_tool" or self._callback is None:
            raise _error("No application callback is bound.", "unsupported_operation")
        context = params.get("context", {})
        receipt = local.get("receipt")
        if receipt is None or context.get("native_id") != receipt["native_id"] or context.get("incarnation") != receipt["incarnation"] or context.get("host_id") != receipt["host_id"]:
            raise _error("Callback does not match this bridge attachment.", "session_identity")
        callback = self._application_replay if params.get("replay_only") else self._callback
        if callback is None:
            raise _error("Application replay validation is unavailable.", "stale_native_result", "unknown")
        return callback(params["action"], _copy(params["arguments"]), _copy(context), params.get("idempotency_key"))

    def _emit_observation(self, local, observation):
        with self._lock:
            if observation["sequence"] in local["observed"]:
                return
            if self._observer:
                self._observer(deepcopy(local["context"]), deepcopy(observation))
            local["observed"].add(observation["sequence"])

    def _attach(self, specification, source=None):
        specification = _copy(specification)
        if not isinstance(specification, dict) or set(specification) - {"context"}:
            raise _error("Bridge attachment accepts trusted application context only; configure the host to select native settings.", "invalid_arguments")
        if self._closed:
            raise _error("Bridge driver is closed.")
        if source is not None and (source.get("bridge_endpoint") != self.endpoint or source.get("bridge_transport", "unix") != self.transport):
            raise _error("Resume source belongs to another configured endpoint.", "session_identity")
        connection = None
        try:
            if self.transport == "fifo":
                connection = _fifo_connect(self.endpoint)
            else:
                connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                connection.settimeout(self.options.get("timeout", 30))
                connection.connect(self.endpoint)
                connection.settimeout(None)
        except OSError as exc:
            if connection is not None:
                connection.close()
            raise _error("The configured bridge endpoint is unavailable.") from exc
        local = {"context": _copy(specification.get("context", {})), "cursor": 0, "observed": set()}
        peer = _Peer(connection, lambda method, params: self._incoming(local, method, params), self.options)
        try:
            receipt = peer.request("attach", {"context": local["context"], "source": source,
                "catalog_identity": _digest([asdict(c) for c in self._capabilities])})
            if not isinstance(receipt, dict) or not receipt.get("native_id") or not receipt.get("incarnation") or receipt.get("ownership") != "external":
                raise _error("Host returned an invalid attachment receipt.", "bridge_protocol")
            handle = uuid.uuid4().hex
            receipt.update({"instance": self._instance, "handle": handle, "bridge_endpoint": self.endpoint, "bridge_transport": self.transport})
            local.update({"receipt": deepcopy(receipt), "peer": peer})
            with self._lock:
                self._sessions[handle] = local
            peer.request("activate", {"incarnation": receipt["incarnation"]})
            return receipt
        except Exception:
            peer.close()
            raise

    def start(self, specification):
        return self.attach(specification)

    def attach(self, specification):
        return self._attach(specification)

    def resume(self, source_backend, specification):
        return self._attach(specification, _copy(source_backend))

    def reconnect(self, source_backend, specification):
        return self.resume(source_backend, specification)

    def _get(self, session):
        with self._lock:
            local = self._sessions.get(session.get("handle"))
            if local is None or session != local["receipt"] or session.get("instance") != self._instance:
                raise _error("Backend receipt does not identify this bridge connection.", "session_unavailable")
            return local

    def _call(self, session, method, **params):
        local = self._get(session)
        result = local["peer"].request(method, {"incarnation": session["incarnation"], "operation_id": uuid.uuid4().hex, **params})
        if method in {"send", "deliver"} and isinstance(result, dict) and "incarnation" in result:
            if result.get("native_id") != session["native_id"]:
                raise _error("Native submission changed conversation identity.", "session_identity", "unknown")
            result = {**result, "native_incarnation": result["incarnation"], "incarnation": session["incarnation"], "host_id": session["host_id"]}
        return result

    def send(self, session, message):
        return self._call(session, "send", message=_copy(message))

    def deliver(self, session, message):
        return self._call(session, "deliver", message=_copy(message))

    def poll(self, session):
        local = self._get(session)
        native = self._call(session, "poll")
        observed = self._call(session, "events", after=local["cursor"])
        events = observed["events"]
        if events and events[0]["sequence"] != local["cursor"] + 1:
            raise _error("Bridge observation history has a retention gap.", "evidence_gap", "unknown")
        for observation in events:
            # Historical old-lease events remain inspectable but are never
            # forwarded to the new application as trusted current observations.
            if observation.get("incarnation") == session["incarnation"] and self._observer:
                self._emit_observation(local, observation)
        local["cursor"] = observed["last_sequence"]
        return [*native, *({"kind": "bridge_observation", "observation": event} for event in events)]

    def inspect(self, session):
        return self._call(session, "inspect")

    def detach(self, session):
        local = self._get(session)
        try:
            return self._call(session, "detach")
        finally:
            local["peer"].close()
            with self._lock:
                self._sessions.pop(session["handle"], None)

    def stop(self, session):
        return self.detach(session)

    def close(self):
        with self._lock:
            self._closed = True
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for local in sessions:
            # Socket close cannot terminate or stop the host-owned native process.
            local["peer"].close()
