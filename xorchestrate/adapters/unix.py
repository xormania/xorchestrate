"""``unix`` client transport: an owner-only Unix socket shared by many clients.

Every accepted peer passes the injectable credential check (``SO_PEERCRED``
same-user by default) and then sends one hello frame naming its purpose and
asserted actor. The server answers ``welcome`` or ``refused``; afterwards the
stream carries newline-delimited protocol frames unchanged.

Hello: ``{"xorchestrate": "hello", "version": 1, "purpose": "mcp" | "control",
"actor": NAME, "attributes": {...}}``. ``attributes`` is client-asserted context
that a later server may bind to the connection (for example its worktree).
"""
from __future__ import annotations

import json
import os
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


class UnixConnection:
    """A verified ``ClientConnection`` over one accepted stream socket."""

    def __init__(self, connection, reader, peer, max_frame_bytes):
        self.socket = connection
        self.reader = reader
        self.peer = peer
        self.max_frame_bytes = max_frame_bytes
        self._write_lock = threading.Lock()
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
            self.socket.sendall(frame + b"\n")

    def close(self):
        with self._write_lock:
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
    """Owner-only listening socket yielding one ``UnixConnection`` per verified client."""

    def __init__(self, path, *, max_frame_bytes=1_048_576, check=same_user, replace_stale=False,
                 handshake_timeout=5.0, write_timeout=30.0, backlog=64):
        self.max_frame_bytes = max_frame_bytes
        self.handshake_timeout = handshake_timeout
        self.write_timeout = write_timeout
        self.listener = OwnerSocketListener(path, backlog, check=check, replace_stale=replace_stale)
        self.path = self.listener.path
        self._closed = False

    def accept(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self._closed:
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            try:
                connection, credentials = self.listener.accept(remaining)
            except OSError:
                return None
            if connection is None:
                return None
            accepted = self._handshake(connection, credentials)
            if accepted is not None:
                return accepted
            if deadline is not None and time.monotonic() >= deadline:
                return None
        return None

    def _handshake(self, connection, credentials):
        reader = None
        try:
            connection.settimeout(self.handshake_timeout)
            reader = connection.makefile("rb")
            frame = read_line_frame(reader, self.max_frame_bytes)
            if frame is None:
                raise OrchestrationError("Client closed before its hello frame.", "invalid_handshake")
            hello = _validate_hello(decode_json(frame))
            connection.settimeout(None)
            seconds = int(self.write_timeout)
            microseconds = int((self.write_timeout - seconds) * 1_000_000)
            # A client that stops reading cannot hold a shared worker forever.
            connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDTIMEO, struct.pack("ll", seconds, microseconds))
            peer = PeerIdentity(uid=None if credentials is None else credentials.uid,
                                pid=None if credentials is None else credentials.pid,
                                actor=hello["actor"], purpose=hello["purpose"],
                                attributes=json.loads(json.dumps(hello.get("attributes", {}))))
            connection.sendall(_encode({"xorchestrate": "welcome", "version": HANDSHAKE_VERSION, "pid": os.getpid()}) + b"\n")
            return UnixConnection(connection, reader, peer, self.max_frame_bytes)
        except Exception as exc:
            code = exc.code if isinstance(exc, OrchestrationError) else "invalid_handshake"
            try:
                connection.settimeout(1.0)
                connection.sendall(_encode({"xorchestrate": "refused", "error": {
                    "code": code, "message": "The connection handshake was refused."}}) + b"\n")
            except OSError:
                pass
            for resource in (reader, connection):
                if resource is not None:
                    try:
                        resource.close()
                    except OSError:
                        pass
            return None

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.listener.shutdown()
        self.listener.close()


class UnixClient:
    """Client side of the hello exchange, used by ``connect`` and the control commands."""

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
            except OSError as exc:
                # A listener that refuses the peer closes it, possibly with a reset.
                raise OrchestrationError("The server refused the connection.", "connection_refused",
                                         {"reason": "closed"}) from exc
            if not isinstance(reply, dict) or reply.get("xorchestrate") != "welcome":
                error = reply.get("error", {}) if isinstance(reply, dict) else {}
                raise OrchestrationError("The server refused the connection.", "connection_refused",
                                         {"reason": error.get("code", "closed")})
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
