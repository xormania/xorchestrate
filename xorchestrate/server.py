"""The long-running server: many MCP clients sharing one application.

``ProjectServer`` accepts connections from a ``ClientTransport``. MCP
connections each get their own MCP session in the shared ``MCPDispatcher``;
control connections ask for ``status`` or ``down``. Closing a connection ends
only that client's session: the application, its drivers and their live
sessions belong to the server.

The client side lives here too: deriving the socket path, starting the server
when none is listening (once, under a start lock), relaying a harness's stdio,
and sending control requests.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .adapters.local_socket import MAX_SOCKET_PATH_BYTES, InstanceLock, lock_is_held
from .adapters.unix import UnixClient
from .mcp import failure
from .model import OrchestrationError

RUNTIME_DIR_VARIABLE = "XORCHESTRATE_RUNTIME_DIR"


class ProjectServer:
    """Serve connections until ``down`` (or ``terminate``) drains and stops it."""

    def __init__(self, application, transport, dispatcher, *, max_connections=64, poll_interval=0.2, details=None):
        if type(max_connections) is not int or max_connections < 1:
            raise OrchestrationError("max_connections must be a positive integer.", "invalid_configuration")
        self.application = application
        self.transport = transport
        self.dispatcher = dispatcher
        self.max_connections = max_connections
        self.poll_interval = poll_interval
        self.details = dict(details or {})
        self.started_at = datetime.now(timezone.utc).isoformat()
        self._lock = threading.Lock()
        self._drain_lock = threading.Lock()
        self._connections = {}
        self._stopping = threading.Event()
        self._terminate = threading.Event()
        self._stop_decided = False

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
            self.dispatcher.begin_drain()
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

    def status(self):
        with self._lock:
            clients = [connection.peer for connection in self._connections if connection.peer.purpose == "mcp"]
        return {"running": True, "pid": os.getpid(), "started_at": self.started_at, **self.details,
                "connections": len(clients), "actors": sorted({peer.actor for peer in clients}),
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
            self.dispatcher.begin_drain()
            self.dispatcher.wait_idle()
            # An admitted call may have started a session while draining.
            live = self.live_sessions()
            if live and not detach:
                self.dispatcher.end_drain()
                return self._refusal(live)
            self._stop_decided = True
            return {"stopping": True, "pid": os.getpid(), "detached_sessions": live}

    # -- connections ---------------------------------------------------------

    def _admit(self, connection):
        with self._lock:
            refuse = (len(self._connections) >= self.max_connections
                      or (connection.peer.purpose == "mcp" and (self.dispatcher.draining or self._stop_decided)))
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
            try:
                request = json.loads(frame)
                command = request.get("command") if isinstance(request, dict) else None
                if command == "status":
                    reply = self.status()
                elif command == "down":
                    reply = self._drain(bool(request.get("detach", False)))
                else:
                    reply = {"error": {"code": "unsupported_operation", "message": "Unknown control command."}}
            except OrchestrationError as exc:
                reply = {"error": failure(exc)}
            except (ValueError, RecursionError):
                reply = {"error": {"code": "invalid_json", "message": "Expected one JSON control request."}}
            connection.write_frame(json.dumps(reply, allow_nan=False).encode())
            if self._stop_decided:
                # Reply first, then let the accept loop close every connection.
                self._stopping.set()
                return


# -- socket location ---------------------------------------------------------

def _private_directory(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = path.lstat()
    if not path.is_dir() or path.is_symlink() or metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
        raise OrchestrationError("The runtime directory must be a private directory owned by this user.",
                                 "invalid_configuration", {"path": str(path)})
    return path


def _fits(path):
    return len(os.fsencode(str(path))) <= MAX_SOCKET_PATH_BYTES


def default_socket_path(config_path=None, cwd=None, environ=None):
    """Derive a per-user socket path from the resolved configuration.

    The name is a digest of the resolved ``--config`` path (or of the working
    directory when no configuration is given, since that is where the default
    state lives), so every client of one configuration reaches one server.
    The directory is ``$XORCHESTRATE_RUNTIME_DIR``, else
    ``$XDG_RUNTIME_DIR/xorchestrate``, else ``<tmp>/xorchestrate-<uid>``; a
    path that would not fit ``sun_path`` falls back to the short temporary one.
    """
    environ = os.environ if environ is None else environ
    if config_path:
        identity = "config:" + str(Path(config_path).expanduser().resolve())
    else:
        identity = "cwd:" + str(Path(cwd or Path.cwd()).resolve())
    name = hashlib.sha256(identity.encode()).hexdigest()[:16] + ".sock"
    fallback = Path(tempfile.gettempdir()) / f"xorchestrate-{os.getuid()}"
    if environ.get(RUNTIME_DIR_VARIABLE):
        directory = Path(environ[RUNTIME_DIR_VARIABLE]).expanduser().absolute()
        if not _fits(directory / name):
            raise OrchestrationError(f"{RUNTIME_DIR_VARIABLE} is too long for a Unix socket path.", "invalid_configuration")
    elif environ.get("XDG_RUNTIME_DIR") and _fits(Path(environ["XDG_RUNTIME_DIR"]) / "xorchestrate" / name):
        directory = Path(environ["XDG_RUNTIME_DIR"]) / "xorchestrate"
    else:
        directory = fallback
    if not _fits(directory / name):
        raise OrchestrationError("No runtime directory is short enough for a Unix socket path; pass --socket.",
                                 "invalid_configuration")
    return _private_directory(directory) / name


def companion_paths(socket_path):
    """Files kept next to the socket: the server's instance lock, the start lock and its log."""
    socket_path = Path(socket_path)
    return {"instance_lock": Path(f"{socket_path}.lock"), "start_lock": Path(f"{socket_path}.start.lock"),
            "log": Path(f"{socket_path}.log")}


# -- client side -------------------------------------------------------------

def open_client(socket_path, *, actor="owner", purpose="mcp", attributes=None, timeout=5.0):
    return UnixClient(socket_path, actor=actor, purpose=purpose, attributes=attributes, timeout=timeout)


def ensure_server(socket_path, start_command, *, actor="owner", purpose="mcp", attributes=None,
                  timeout=30.0, cwd=None, interval=0.05):
    """Connect to the server, starting exactly one detached server if none is running.

    Concurrent callers serialize on the start lock; only a caller that finds
    no server holding the instance lock launches one. Returns ``(client, started)``.
    """
    socket_path = Path(socket_path)
    deadline = time.monotonic() + timeout
    try:
        return open_client(socket_path, actor=actor, purpose=purpose, attributes=attributes), False
    except (OSError, OrchestrationError):
        pass
    paths = companion_paths(socket_path)
    socket_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    start_lock = InstanceLock(paths["start_lock"])
    if not start_lock.acquire(wait=max(0.0, deadline - time.monotonic())):
        raise OrchestrationError("Timed out waiting for another client to start the server.", "server_unavailable")
    process = None
    try:
        while True:
            try:
                return open_client(socket_path, actor=actor, purpose=purpose, attributes=attributes), process is not None
            except (OSError, OrchestrationError):
                pass
            if process is not None and process.poll() is not None:
                raise OrchestrationError("The server exited during startup; see its log.", "server_unavailable",
                                         {"exit_code": process.returncode, "log": str(paths["log"])})
            if process is None and not lock_is_held(paths["instance_lock"]):
                process = _spawn(start_command, paths["log"], cwd)
            if time.monotonic() >= deadline:
                raise OrchestrationError("Timed out waiting for the server to accept connections.", "server_unavailable",
                                         {"log": str(paths["log"])})
            time.sleep(interval)
    finally:
        start_lock.release()


def _spawn(command, log_path, cwd):
    descriptor = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    try:
        # A new session detaches the server from the harness's terminal and signals.
        return subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=descriptor, stderr=descriptor,
                                cwd=cwd, start_new_session=True, close_fds=True)
    finally:
        os.close(descriptor)


def control(socket_path, command, *, actor="owner", timeout=None, **parameters):
    client = open_client(socket_path, actor=actor, purpose="control")
    try:
        reply = client.request({"command": command, **parameters}, timeout)
    finally:
        client.close()
    if not isinstance(reply, dict):
        raise OrchestrationError("The server returned an invalid control reply.", "io_error")
    return reply


def wait_stopped(socket_path, timeout, interval=0.05):
    """Wait until the server has released its instance lock (its process has finished)."""
    lock = companion_paths(socket_path)["instance_lock"]
    deadline = time.monotonic() + timeout
    while lock_is_held(lock):
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


def server_command(arguments, socket_path):
    """The detached server command: this interpreter, the same global options, ``serve --transport unix``."""
    return [sys.executable, "-m", "xorchestrate", *arguments, "serve", "--transport", "unix", "--socket", str(socket_path)]
