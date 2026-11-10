"""The long-running server: many MCP clients sharing one application.

``ProjectServer`` accepts connections from a ``ClientTransport``. MCP
connections each get their own MCP session in the shared ``MCPDispatcher``;
control connections ask for ``status`` or ``down``. Closing a connection ends
only that client's session: the application, its drivers and their live
sessions belong to the server.

One configuration has at most one server, whatever each client's environment:
the instance lock and a discovery record naming the socket live under the
per-user xorchestrate home, keyed by the configuration's identity. The client
side lives here too: discovering the socket, starting the server when none is
running (once, under a start lock), relaying a harness's stdio, and sending
control requests.
"""
from __future__ import annotations

import hashlib
import json
import os
import pwd
import socket
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .adapters.local_socket import (
    MAX_SOCKET_PATH_BYTES,
    InstanceLock,
    check_socket_path,
    lock_is_held,
    open_private_file,
)
from .adapters.unix import UnixClient
from .mcp import failure
from .model import OrchestrationError

HOME_VARIABLE = "XORCHESTRATE_HOME"
RUNTIME_DIR_VARIABLE = "XORCHESTRATE_RUNTIME_DIR"
SERVER_LOCK_WAIT = 2.0


class ProjectServer:
    """Serve connections until ``down`` (or ``terminate``) drains and stops it.

    MCP clients are limited by ``max_connections``. Control connections have
    their own small allowance, so ``status`` and ``down`` work on a full server.
    """

    def __init__(self, application, transport, dispatcher, *, max_connections=64, max_control_connections=8,
                 poll_interval=0.2, details=None):
        for name, value in (("max_connections", max_connections), ("max_control_connections", max_control_connections)):
            if type(value) is not int or value < 1:
                raise OrchestrationError(f"{name} must be a positive integer.", "invalid_configuration")
        self.application = application
        self.transport = transport
        self.dispatcher = dispatcher
        self.limits = {"mcp": max_connections, "control": max_control_connections}
        self.poll_interval = poll_interval
        self.details = dict(details or {})
        self.started_at = datetime.now(timezone.utc).isoformat()
        self._lock = threading.Lock()
        self._drain_lock = threading.Lock()
        self._connections = {}
        self._reserved = {"mcp": 0, "control": 0}
        self._stopping = threading.Event()
        self._terminate = threading.Event()
        self._stop_decided = False
        set_admission = getattr(transport, "set_admission", None)
        self._handshake_admission = callable(set_admission)
        if self._handshake_admission:
            set_admission(self)

    # -- lifecycle -----------------------------------------------------------

    def serve_forever(self):
        try:
            while not self._stopping.is_set():
                if self._terminate.is_set():
                    self._drain(detach=True)
                    self._stopping.set()
                    break
                connection = self.transport.accept(self.poll_interval)
                if connection is not None:
                    self._admit(connection)
        finally:
            self.transport.close()
            # Already idle after a drain; on an interrupt, admit nothing further.
            self.dispatcher.stop_admission()
            with self._lock:
                connections = list(self._connections.items())
            for connection, _thread in connections:
                connection.close()
            for _connection, thread in connections:
                thread.join(timeout=5)

    def terminate(self):
        """Signal-safe stop request: drain admitted calls, then stop even with live sessions."""
        self._terminate.set()

    def down(self, detach=False):
        """Drain and stop. Refused (and the server keeps serving) while sessions are live, unless ``detach``."""
        reply = self._drain(detach)
        if self._stop_decided:
            self._stopping.set()
        return reply

    @property
    def stopped(self):
        return self._stopping.is_set()

    def live_sessions(self):
        live = getattr(self.application, "live_sessions", None)
        return live() if callable(live) else []

    def _count(self, purpose):
        return sum(1 for connection in self._connections if connection.peer.purpose == purpose)

    def status(self):
        with self._lock:
            clients = [connection.peer for connection in self._connections if connection.peer.purpose == "mcp"]
        return {"running": True, "pid": os.getpid(), "started_at": self.started_at, **self.details,
                "connections": len(clients), "max_connections": self.limits["mcp"],
                "actors": sorted({peer.actor for peer in clients}),
                "in_flight": self.dispatcher.in_flight, "draining": self.dispatcher.draining or self._stop_decided,
                "live_sessions": self.live_sessions()}

    def _refusal(self, live):
        return {"stopping": False, "error": {"code": "sessions_live", "message":
                "Live sessions would end; stop or detach them, or use --detach.", "details": {"sessions": live}}}

    def _drain(self, detach):
        with self._drain_lock:
            if self._stop_decided:
                return {"stopping": True, "pid": os.getpid()}
            if not detach and (live := self.live_sessions()):
                return self._refusal(live)
            # Effect calls are refused from here; observation/control tools stay
            # available so clients can stop or detach their sessions.
            self.dispatcher.begin_drain()
            self.dispatcher.wait_idle()
            # An admitted call may have started a session while draining.
            live = self.live_sessions()
            if live and not detach:
                self.dispatcher.end_drain()
                return self._refusal(live)
            self._stop_decided = True
            self.dispatcher.stop_admission()
            self.dispatcher.wait_idle()
            return {"stopping": True, "pid": os.getpid(), "detached_sessions": live}

    # -- admission (called by the transport during each handshake) -----------

    def admit(self, peer):
        """Reserve a slot for a verified peer, or return ``(code, message)`` to refuse it."""
        purpose = peer.purpose if peer.purpose in self.limits else "mcp"
        with self._lock:
            if purpose == "mcp" and (self._stop_decided or self.dispatcher.draining):
                return "draining", "The server is shutting down and admits no new MCP clients."
            if self._count(purpose) + self._reserved[purpose] >= self.limits[purpose]:
                return "server_full", f"The server is at its {purpose} connection limit."
            self._reserved[purpose] += 1
        return None

    def cancel(self, peer):
        purpose = peer.purpose if peer.purpose in self.limits else "mcp"
        with self._lock:
            self._reserved[purpose] = max(0, self._reserved[purpose] - 1)

    # -- connections ---------------------------------------------------------

    def _admit(self, connection):
        if not self._handshake_admission and self.admit(connection.peer) is not None:
            connection.close()
            return
        purpose = connection.peer.purpose if connection.peer.purpose in self.limits else "mcp"
        with self._lock:
            self._reserved[purpose] = max(0, self._reserved[purpose] - 1)
            refuse = self._stopping.is_set()
            if not refuse:
                thread = threading.Thread(target=self._handle, args=(connection,), daemon=True,
                                          name=f"client-{connection.peer.purpose}")
                self._connections[connection] = thread
        if refuse:
            connection.close()
        else:
            thread.start()

    def _handle(self, connection):
        try:
            if connection.peer.purpose == "control":
                self._control(connection)
            else:
                self.dispatcher.serve_connection(connection)
        except Exception:  # noqa: S110 - a failed client never stops the server
            pass
        finally:
            connection.close()
            with self._lock:
                self._connections.pop(connection, None)

    def _control(self, connection):
        while (frame := connection.read_frame()) is not None:
            drained, reply = False, None
            try:
                try:
                    request = json.loads(frame)
                    command = request.get("command") if isinstance(request, dict) else None
                    if command == "status":
                        reply = self.status()
                    elif command == "down":
                        detach = request.get("detach", False)
                        if type(detach) is not bool:
                            reply = {"error": {"code": "invalid_arguments", "message": "detach must be a boolean."}}
                        else:
                            drained = True
                            reply = self._drain(detach)
                    else:
                        reply = {"error": {"code": "unsupported_operation", "message": "Unknown control command."}}
                except OrchestrationError as exc:
                    reply = {"error": failure(exc)}
                except (ValueError, RecursionError):
                    reply = {"error": {"code": "invalid_json", "message": "Expected one JSON control request."}}
                try:
                    connection.write_frame(json.dumps(reply, allow_nan=False).encode())
                except Exception:
                    if not self._stops_after(drained, reply):
                        raise
            finally:
                if self._stops_after(drained, reply):
                    # The decision stands however this exchange ended: reply
                    # delivered, client gone, or an unexpected error after the
                    # decision. The accept loop now stops.
                    self._stopping.set()
            if self._stops_after(drained, reply):
                return

    def _stops_after(self, drained, reply):
        """Whether this ``down`` exchange decided the stop (``reply`` is ``None`` if it raised)."""
        return drained and self._stop_decided and (reply is None or reply.get("stopping") is True)


# -- locations ---------------------------------------------------------------

def _absolute_setting(environ, name):
    value = environ.get(name)
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise OrchestrationError(f"{name} must be an absolute path.", "invalid_configuration", {name: value})
    return path


def _private_directory(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = path.lstat()
    if not path.is_dir() or path.is_symlink() or metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
        raise OrchestrationError("Server directories must be private directories owned by this user.",
                                 "invalid_configuration", {"path": str(path)})
    return path


def xorchestrate_home(environ=None):
    """``$XORCHESTRATE_HOME`` if set (absolute), else ``.xorchestrate`` in the account's home.

    The account home comes from the user database, not ``$HOME``, so clients
    with different shells or sudo settings agree.
    """
    environ = os.environ if environ is None else environ
    configured = _absolute_setting(environ, HOME_VARIABLE)
    return configured if configured is not None else Path(pwd.getpwuid(os.getuid()).pw_dir) / ".xorchestrate"


def server_identity(config_path=None, cwd=None):
    """The resolved configuration path, or the working directory when there is none."""
    if config_path:
        return "config:" + str(Path(config_path).expanduser().resolve())
    return "cwd:" + str(Path(cwd or Path.cwd()).resolve())


@dataclass(frozen=True)
class ServerFiles:
    """Per-configuration files under ``<home>/servers``: lock, start lock, log and discovery record."""

    identity: str
    key: str
    directory: Path

    @classmethod
    def locate(cls, identity, environ=None):
        directory = xorchestrate_home(environ) / "servers"
        return cls(identity, hashlib.sha256(identity.encode()).hexdigest()[:16], directory)

    def ensure(self):
        _private_directory(self.directory.parent)
        _private_directory(self.directory)
        return self

    @property
    def lock(self):
        return self.directory / f"{self.key}.lock"

    @property
    def start_lock(self):
        return self.directory / f"{self.key}.start.lock"

    @property
    def log(self):
        return self.directory / f"{self.key}.log"

    @property
    def record(self):
        return self.directory / f"{self.key}.json"

    def read_record(self):
        try:
            fd = open_private_file(self.record, os.O_RDONLY)
        except FileNotFoundError:
            return None
        with os.fdopen(fd, "rb") as source:
            data = source.read(65536)
        try:
            record = json.loads(data)
        except ValueError:
            return None
        return record if isinstance(record, dict) and isinstance(record.get("socket"), str) else None

    def write_record(self, socket_path, config):
        record = {"schema": 1, "identity": self.identity, "socket": str(socket_path), "pid": os.getpid(),
                  "config": config}
        temporary = self.directory / f".{self.key}.{os.getpid()}.json"
        fd = open_private_file(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
        with os.fdopen(fd, "wb") as target:
            target.write(json.dumps(record).encode())
        os.replace(temporary, self.record)

    def remove_record(self):
        record = self.read_record()
        if record is not None and record.get("pid") == os.getpid():
            self.record.unlink(missing_ok=True)


def _fits(path):
    return len(os.fsencode(str(path))) <= MAX_SOCKET_PATH_BYTES


def default_socket_path(files, environ=None):
    """Where a server listens when no ``--socket`` is given.

    ``$XORCHESTRATE_RUNTIME_DIR`` (absolute) if set; else the server directory
    itself when the path fits ``sun_path``; else ``$XDG_RUNTIME_DIR/xorchestrate``;
    else ``<tmp>/xorchestrate-<uid>``. Clients never compute this: they read
    the discovery record, so the choice cannot split one configuration.
    """
    environ = os.environ if environ is None else environ
    name = f"{files.key}.sock"
    configured = _absolute_setting(environ, RUNTIME_DIR_VARIABLE)
    if configured is not None:
        if not _fits(configured / name):
            raise OrchestrationError(f"{RUNTIME_DIR_VARIABLE} is too long for a Unix socket path.", "invalid_configuration")
        return _private_directory(configured) / name
    candidates = [files.directory]
    if environ.get("XDG_RUNTIME_DIR") and Path(environ["XDG_RUNTIME_DIR"]).is_absolute():
        candidates.append(Path(environ["XDG_RUNTIME_DIR"]) / "xorchestrate")
    candidates.append(Path(tempfile.gettempdir()) / f"xorchestrate-{os.getuid()}")
    for directory in candidates:
        if _fits(directory / name):
            return _private_directory(directory) / name
    raise OrchestrationError("No runtime directory is short enough for a Unix socket path; pass --socket.",
                             "invalid_configuration")


# -- client side -------------------------------------------------------------

def open_client(socket_path, *, actor="owner", purpose="mcp", attributes=None, timeout=5.0):
    return UnixClient(socket_path, actor=actor, purpose=purpose, attributes=attributes, timeout=timeout)


def discover(files, explicit=None):
    """The socket to reach: the explicit one, else the running server's recorded socket."""
    if explicit is not None:
        return Path(explicit)
    record = files.read_record()
    return None if record is None else Path(record["socket"])


def _try_connect(files, explicit, **options):
    target = discover(files, explicit)
    if target is None:
        raise FileNotFoundError("no server record")
    try:
        return open_client(target, **options)
    except OrchestrationError as exc:
        if exc.details.get("reason") == "server_full":
            raise
        if exc.code == "connection_refused" or exc.code == "handshake_timeout":
            raise ConnectionError(str(exc)) from exc  # retryable: e.g. draining
        raise


def ensure_server(files, start_command, *, explicit_socket=None, actor="owner", purpose="mcp", attributes=None,
                  timeout=30.0, cwd=None, interval=0.05):
    """Connect to the configuration's server, starting exactly one detached server if none runs.

    Concurrent callers serialize on the start lock; only a caller that finds
    no server holding the instance lock launches one. A full server is a
    refusal, never a reason to start another. Returns ``(client, started)``.
    """
    options = {"actor": actor, "purpose": purpose, "attributes": attributes}
    deadline = time.monotonic() + timeout
    try:
        return _try_connect(files, explicit_socket, **options), False
    except OSError:
        pass
    files.ensure()
    start_lock = InstanceLock(files.start_lock)
    if not start_lock.acquire(wait=max(0.0, deadline - time.monotonic())):
        raise OrchestrationError("Timed out waiting for another client to start the server.", "server_unavailable")
    process = None
    try:
        while True:
            try:
                return _try_connect(files, explicit_socket, **options), process is not None
            except OSError:
                pass
            if process is not None and process.poll() is not None:
                raise OrchestrationError("The server exited during startup; see its log.", "server_unavailable",
                                         {"exit_code": process.returncode, "log": str(files.log)})
            if process is None and not lock_is_held(files.lock):
                process = _spawn(start_command, files.log, cwd)
            if time.monotonic() >= deadline:
                record = files.read_record()
                raise OrchestrationError("Timed out waiting for the server to accept connections.", "server_unavailable",
                                         {"log": str(files.log), "running_socket": record and record.get("socket")})
            time.sleep(interval)
    finally:
        start_lock.release()


def _spawn(command, log_path, cwd):
    descriptor = open_private_file(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
    try:
        # A new session detaches the server from the harness's terminal and signals.
        return subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=descriptor, stderr=descriptor,
                                cwd=cwd, start_new_session=True, close_fds=True)
    finally:
        os.close(descriptor)


def wait_stopped(files, timeout, interval=0.05):
    """Wait until the server has released its instance lock (its process has finished)."""
    deadline = time.monotonic() + timeout
    while lock_is_held(files.lock):
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)
    return True


def relay(client, stdin_fd, stdout):
    """Copy bytes between a harness's stdio and a connected server until both directions finish.

    Returns ``True`` when the harness finished its input first (a normal end),
    ``False`` when the server closed the connection on its own.
    """
    finished = threading.Event()

    def upstream():
        try:
            while chunk := os.read(stdin_fd, 65536):
                client.socket.sendall(chunk)
        except OSError:
            pass
        finally:
            finished.set()
            try:
                client.socket.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    threading.Thread(target=upstream, daemon=True, name="connect-upstream").start()
    try:
        while chunk := client.reader.read1(65536):
            stdout.write(chunk)
            stdout.flush()
    except (OSError, ValueError):
        pass
    return finished.is_set()


def server_command(arguments, explicit_socket=None):
    """The detached server command: this interpreter, the same global options, ``serve --transport unix``."""
    command = [sys.executable, "-m", "xorchestrate", *arguments, "serve", "--transport", "unix"]
    return command + (["--socket", str(check_socket_path(explicit_socket))] if explicit_socket else [])
