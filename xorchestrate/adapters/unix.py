"""``unix`` client transport: an owner-only Unix socket shared by many clients.

Every accepted peer passes the injectable credential check (``SO_PEERCRED``
same-user by default) and then sends one hello frame naming its purpose and
asserted actor. The server answers ``welcome`` or ``refused`` with a reason;
afterwards the stream carries newline-delimited protocol frames unchanged.

Hello: ``{"xorchestrate": "hello", "version": 1, "purpose": "mcp" | "control",
"actor": NAME, "attributes": {...}}``. ``attributes`` is client-asserted context
that a later server may bind to the connection (for example its worktree).

Hellos are read on their own threads with a total deadline, so a slow or idle
peer never delays the listener, other clients or the server's control loop.
"""
from __future__ import annotations

import io
import json
import os
import queue
import socket
import struct
import threading
import time

from ..model import OrchestrationError
from ..ports import PeerIdentity
from .local_socket import OwnerSocketListener, check_socket_path, same_user
from .process import decode_json
from .stdio import read_line_frame

HANDSHAKE_VERSION = 1
PURPOSES = ("mcp", "control")


def _encode(value):
    return json.dumps(value, allow_nan=False, separators=(",", ":")).encode()


def _validate_hello(value):
    if (not isinstance(value, dict) or value.get("xorchestrate") != "hello" or value.get("version") != HANDSHAKE_VERSION
            or value.get("purpose") not in PURPOSES or not isinstance(value.get("actor"), str) or not value["actor"]
            or not isinstance(value.get("attributes", {}), dict)):
        raise OrchestrationError("Expected an xorchestrate hello frame.", "invalid_handshake")
    return value


class _SocketStream(io.RawIOBase):
    """Raw reads from a socket that first return bytes already received with the hello."""

    def __init__(self, connection, prefix):
        self._socket = connection
        self._prefix = bytes(prefix)

    def readable(self):
        return True

    def readinto(self, buffer):
        if self._prefix:
            count = min(len(buffer), len(self._prefix))
            buffer[:count] = self._prefix[:count]
            self._prefix = self._prefix[count:]
            return count
        return self._socket.recv_into(buffer)


def read_hello(connection, maximum, timeout):
    """Read one line within a total deadline; return ``(frame, remaining bytes)``."""
    deadline = time.monotonic() + timeout
    data = bytearray()
    while b"\n" not in data:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("hello deadline")
        connection.settimeout(remaining)
        chunk = connection.recv(65536)
        if not chunk:
            raise OrchestrationError("Client closed before its hello frame.", "invalid_handshake")
        data.extend(chunk)
        if len(data.split(b"\n", 1)[0]) > maximum:
            raise OrchestrationError("Hello frame exceeds the configured limit.", "frame_too_large")
    line, rest = bytes(data).split(b"\n", 1)
    return line.rstrip(b"\r"), rest


class UnixConnection:
    """A verified ``ClientConnection`` over one accepted stream socket.

    Any write failure, including a send timeout after a partial frame, closes
    the connection: a later reply can never be appended to a truncated one.
    """

    def __init__(self, connection, reader, peer, max_frame_bytes):
        self.socket = connection
        self.reader = reader
        self.peer = peer
        self.max_frame_bytes = max_frame_bytes
        self._write_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._closed = False

    def read_frame(self):
        if self._closed:
            return None
        try:
            return read_line_frame(self.reader, self.max_frame_bytes)
        except OrchestrationError:
            raise  # an oversized frame; the stream remains usable
        except (OSError, ValueError):
            return None  # closed locally or by the peer

    def write_frame(self, frame):
        with self._write_lock:
            if self._closed:
                raise OrchestrationError("Client connection is closed.", "io_error")
            try:
                self.socket.sendall(frame + b"\n")
            except BaseException:
                self.close()
                raise

    def close(self):
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
        try:
            self.socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        for resource in (self.reader, self.socket):
            try:
                resource.close()
            except (OSError, ValueError):
                pass


class UnixClientTransport:
    """Owner-only listening socket yielding one ``UnixConnection`` per verified client.

    ``admission``, when set (see ``set_admission``), decides each verified hello:
    ``admit(peer)`` returns ``None`` to welcome the peer or ``(code, message)`` to
    refuse it; ``cancel(peer)`` undoes an admission whose welcome could not be
    delivered.
    """

    def __init__(self, path, *, max_frame_bytes=1_048_576, check=same_user, replace_stale=False,
                 handshake_timeout=5.0, write_timeout=30.0, backlog=64, max_handshakes=32):
        self.max_frame_bytes = max_frame_bytes
        self.handshake_timeout = handshake_timeout
        self.write_timeout = write_timeout
        self.listener = OwnerSocketListener(path, backlog, check=check, replace_stale=replace_stale)
        self.path = self.listener.path
        self.admission = None
        self._ready = queue.Queue()
        self._handshakes = threading.BoundedSemaphore(max_handshakes)
        self._closed = False
        self._thread = threading.Thread(target=self._listen, daemon=True, name="unix-transport-accept")
        self._thread.start()

    def set_admission(self, admission):
        self.admission = admission

    def _listen(self):
        while not self._closed:
            try:
                connection, credentials = self.listener.accept()
            except OSError:
                return
            if not self._handshakes.acquire(blocking=False):
                connection.close()  # too many unfinished hellos
                continue
            threading.Thread(target=self._handshake_thread, args=(connection, credentials), daemon=True,
                             name="unix-transport-hello").start()

    def _handshake_thread(self, connection, credentials):
        try:
            accepted = self._handshake(connection, credentials)
            if accepted is not None:
                if self._closed:
                    self._cancel(accepted.peer)
                    accepted.close()
                else:
                    self._ready.put(accepted)
        finally:
            self._handshakes.release()

    def accept(self, timeout=None):
        if self._closed:
            return None
        try:
            return self._ready.get(timeout=timeout)
        except queue.Empty:
            return None

    def _refuse(self, connection, code, message):
        try:
            connection.settimeout(1.0)
            connection.sendall(_encode({"xorchestrate": "refused", "error": {"code": code, "message": message}}) + b"\n")
        except OSError:
            pass
        connection.close()

    def _cancel(self, peer):
        cancel = getattr(self.admission, "cancel", None)
        if callable(cancel):
            cancel(peer)

    def _handshake(self, connection, credentials):
        try:
            frame, rest = read_hello(connection, self.max_frame_bytes, self.handshake_timeout)
            hello = _validate_hello(decode_json(frame))
        except TimeoutError:
            self._refuse(connection, "handshake_timeout", "No hello frame arrived before the deadline.")
            return None
        except OrchestrationError as exc:
            self._refuse(connection, exc.code if exc.code != "provider_protocol" else "invalid_handshake",
                         "The connection handshake was refused.")
            return None
        except OSError:
            connection.close()
            return None
        peer = PeerIdentity(uid=None if credentials is None else credentials.uid,
                            pid=None if credentials is None else credentials.pid,
                            actor=hello["actor"], purpose=hello["purpose"],
                            attributes=json.loads(json.dumps(hello.get("attributes", {}))))
        refusal = self.admission.admit(peer) if self.admission is not None else None
        if refusal is not None:
            self._refuse(connection, *refusal)
            return None
        try:
            connection.settimeout(None)
            seconds = int(self.write_timeout)
            microseconds = int((self.write_timeout - seconds) * 1_000_000)
            # A client that stops reading cannot hold a shared worker forever.
            connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDTIMEO, struct.pack("ll", seconds, microseconds))
            connection.sendall(_encode({"xorchestrate": "welcome", "version": HANDSHAKE_VERSION, "pid": os.getpid()}) + b"\n")
        except OSError:
            self._cancel(peer)
            connection.close()
            return None
        reader = io.BufferedReader(_SocketStream(connection, rest))
        return UnixConnection(connection, reader, peer, self.max_frame_bytes)

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.listener.shutdown()
        self._thread.join(timeout=5)
        self.listener.close()
        while True:
            try:
                pending = self._ready.get_nowait()
            except queue.Empty:
                break
            self._cancel(pending.peer)
            pending.close()


class UnixClient:
    """Client side of the hello exchange, used by ``connect`` and the control commands.

    Raises ``OSError`` when nothing listens at the path, and ``OrchestrationError``
    coded ``connection_refused`` (with the server's ``reason``) or
    ``handshake_timeout`` when a listener does not welcome this client.
    """

    def __init__(self, path, *, actor="owner", purpose="mcp", attributes=None, timeout=5.0, max_frame_bytes=1_048_576):
        self.path = check_socket_path(path)
        self.max_frame_bytes = max_frame_bytes
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self.socket.settimeout(timeout)
            self.socket.connect(str(self.path))
            hello = {"xorchestrate": "hello", "version": HANDSHAKE_VERSION, "purpose": purpose, "actor": actor,
                     "attributes": attributes or {}}
            self.reader = self.socket.makefile("rb")
            try:
                self.socket.sendall(_encode(hello) + b"\n")
                frame = read_line_frame(self.reader, self.max_frame_bytes)
                reply = None if frame is None else decode_json(frame)
            except TimeoutError as exc:
                raise OrchestrationError("The server did not answer the connection handshake in time.",
                                         "handshake_timeout") from exc
            except OSError as exc:
                # A listener that refuses the peer closes it, possibly with a reset.
                raise OrchestrationError("The server refused the connection.", "connection_refused",
                                         {"reason": "closed"}) from exc
            if not isinstance(reply, dict) or reply.get("xorchestrate") != "welcome":
                error = reply.get("error", {}) if isinstance(reply, dict) else {}
                reason = error.get("code", "closed") if isinstance(error, dict) else "closed"
                raise OrchestrationError(_REFUSALS.get(reason, "The server refused the connection."),
                                         "connection_refused", {"reason": reason})
            self.welcome = reply
            self.socket.settimeout(None)
        except BaseException:
            self.socket.close()
            raise

    def request(self, value, timeout=None):
        """Send one control frame and read its single reply."""
        self.socket.settimeout(timeout)
        self.socket.sendall(_encode(value) + b"\n")
        frame = read_line_frame(self.reader, self.max_frame_bytes)
        if frame is None:
            raise OrchestrationError("The server closed the control connection.", "io_error")
        return decode_json(frame)

    def close(self):
        try:
            self.socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        for resource in (getattr(self, "reader", None), self.socket):
            if resource is not None:
                try:
                    resource.close()
                except OSError:
                    pass


_REFUSALS = {
    "server_full": "The server is at its connection limit.",
    "draining": "The server is shutting down and admits no new MCP clients.",
    "handshake_timeout": "The server gave up waiting for this client's hello.",
}
