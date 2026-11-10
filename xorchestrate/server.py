"""The long-running server: many MCP clients sharing one application.

``ProjectServer`` accepts connections from a ``ClientTransport``. MCP
connections each get their own MCP session in the shared ``MCPDispatcher``;
control connections ask for ``status`` or ``down``. Closing a connection ends
only that client's session: the application, its drivers and their live
sessions belong to the server.

One project has at most one server, whatever each client's environment and
whichever of its worktrees the client runs in: the instance lock and a
discovery record naming the socket live under the per-user xorchestrate home,
keyed by the project instance's identity. The client
side lives here too: discovering the socket, starting the server when none is
running (once, under a start lock), relaying a harness's stdio, and sending
control requests.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from .adapters.local_socket import (
    MAX_SOCKET_PATH_BYTES,
    InstanceLock,
    check_socket_path,
    lock_is_held,
    open_private_file,
)
from .adapters.unix import UnixClient
from .instance.home import HOME_VARIABLE as HOME_VARIABLE  # re-exported for callers of the server module
from .instance.home import absolute_setting, private_directory, xorchestrate_home
from .mcp import failure
from .model import OrchestrationError

RUNTIME_DIR_VARIABLE = "XORCHESTRATE_RUNTIME_DIR"
SERVER_LOCK_WAIT = 2.0
# Refusals that waiting cannot change: a client gives up at once instead of retrying.
FINAL_REFUSALS = frozenset({"server_full", "configuration_mismatch", "worktree_required"})


class ProjectServer:
    """Serve connections until ``down`` (or ``terminate``) drains and stops it.

    MCP clients are limited by ``max_connections``. Control connections have
    their own small allowance, so ``status`` and ``down`` work on a full server.
    """

    def __init__(self, application, transport, dispatcher, *, max_connections=64, max_control_connections=8,
                 poll_interval=0.2, details=None, peer_policy=None):
        for name, value in (("max_connections", max_connections), ("max_control_connections", max_control_connections)):
            if type(value) is not int or value < 1:
                raise OrchestrationError(f"{name} must be a positive integer.", "invalid_configuration")
        self.application = application
        self.transport = transport
        self.dispatcher = dispatcher
        self.limits = {"mcp": max_connections, "control": max_control_connections}
        self.poll_interval = poll_interval
        self.details = dict(details or {})
        # ``peer_policy(peer)`` may refuse an MCP client with ``(code, message[, details])``,
        # for example a worktree whose tracked configuration is not the running one.
        self.peer_policy = peer_policy
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
        worktrees = {(peer.attributes.get("worktree") or {}).get("root") for peer in clients
                     if isinstance(peer.attributes, dict) and isinstance(peer.attributes.get("worktree"), dict)}
        return {"running": True, "pid": os.getpid(), "started_at": self.started_at, **self.details,
                "connections": len(clients), "max_connections": self.limits["mcp"],
                "actors": sorted({peer.actor for peer in clients}),
                "worktrees": sorted(root for root in worktrees if isinstance(root, str)),
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
        if purpose == "mcp" and self.peer_policy is not None and (refusal := self.peer_policy(peer)) is not None:
            return refusal
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

_absolute_setting = absolute_setting
_private_directory = private_directory


def server_identity(instance_id):
    """A project's server is keyed by its instance, never by a path or a worktree."""
    if not isinstance(instance_id, str) or not instance_id:
        raise OrchestrationError("A project server needs a project instance.", "project_required")
    return "project:" + instance_id


@dataclass(frozen=True)
class ServerFiles:
    """Per-project files under ``<home>/servers``: lock, start lock, log and discovery record."""

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

    @property
    def terminal_record(self):
        """Where the server's terminal host is placed and the terminal's receipt, so later commands can adopt it.

        Like the discovery record, it is written once by the launching client;
        later clients read it instead of recomputing the placement from their
        own environment.
        """
        return self.directory / f"{self.key}.terminal.json"

    def terminal_placement(self, environ=None):
        """Where this project's terminal host keeps its state and endpoint (same location rule as the server socket)."""
        return {"name": self.key, "location": str(self.directory / f"{self.key}.terminals"),
                "socket": str(default_socket_path(self, environ, name=f"{self.key}.terminals.sock"))}

    def read_terminal_record(self):
        try:
            fd = open_private_file(self.terminal_record, os.O_RDONLY)
        except FileNotFoundError:
            return None
        with os.fdopen(fd, "rb") as source:
            data = source.read(65536)
        try:
            record = json.loads(data)
        except ValueError:
            record = None
        valid = isinstance(record, dict) and isinstance(record.get("placement"), dict) and isinstance(
            record.get("receipt"), dict)
        if not valid:
            self.remove_terminal_record()  # A corrupt record is treated as missing.
            return None
        return record

    def write_terminal_record(self, placement, receipt):
        temporary = self.directory / f".{self.key}.{os.getpid()}.terminal.json"
        fd = open_private_file(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
        with os.fdopen(fd, "wb") as target:
            target.write(json.dumps({"placement": placement, "receipt": receipt}).encode())
        os.replace(temporary, self.terminal_record)

    def remove_terminal_record(self):
        self.terminal_record.unlink(missing_ok=True)

    def remove_record(self):
        record = self.read_record()
        if record is not None and record.get("pid") == os.getpid():
            self.record.unlink(missing_ok=True)


def _fits(path):
    return len(os.fsencode(str(path))) <= MAX_SOCKET_PATH_BYTES


def default_socket_path(files, environ=None, *, name=None):
    """Where a server listens when no ``--socket`` is given.

    ``$XORCHESTRATE_RUNTIME_DIR`` (absolute) if set; else the server directory
    itself when the path fits ``sun_path``; else ``$XDG_RUNTIME_DIR/xorchestrate``;
    else ``<tmp>/xorchestrate-<uid>``. Clients never compute this: they read
    the discovery record, so the choice cannot split one project.
    """
    environ = os.environ if environ is None else environ
    name = name or f"{files.key}.sock"
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
        if exc.details.get("reason") in FINAL_REFUSALS:
            raise
        if exc.code == "connection_refused" or exc.code == "handshake_timeout":
            raise ConnectionError(str(exc)) from exc  # retryable: e.g. draining
        raise


class ServerLaunch(Protocol):
    """One launched server process, as its launcher observes it."""

    def exited(self) -> dict | None:
        """``None`` while the server may still be starting or running; once it is known to have
        exited, diagnostics ``{"exit_code": int | None, "exit_signal": int | None}``."""
        ...


ServerLauncher = Callable[[list, Path, "str | None"], ServerLaunch]
"""``launch(command, log, cwd)`` starts the server command with its output appended to ``log``."""


def ensure_server(files, start_command, *, launch, explicit_socket=None, actor="owner", purpose="mcp",
                  attributes=None, timeout=30.0, cwd=None, interval=0.05):
    """Connect to the configuration's server, starting exactly one server through ``launch`` if none runs.

    Concurrent callers serialize on the start lock; only a caller that finds
    no server holding the instance lock launches one. A full server is a
    refusal, never a reason to start another. ``launch`` (a ``ServerLauncher``)
    is called at most once. Returns ``(client, started)``.
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
            exited = process.exited() if process is not None else None
            if exited is not None:
                raise OrchestrationError("The server exited during startup; see its log.", "server_unavailable",
                                         {**exited, "log": str(files.log)})
            if process is None and not lock_is_held(files.lock):
                process = launch(list(start_command), files.log, cwd)
            if time.monotonic() >= deadline:
                record = files.read_record()
                raise OrchestrationError("Timed out waiting for the server to accept connections.", "server_unavailable",
                                         {"log": str(files.log), "running_socket": record and record.get("socket")})
            time.sleep(interval)
    finally:
        start_lock.release()


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
    """The detached server command: this interpreter, the same global options, ``serve --transport unix``.

    ``-P`` keeps the working directory off ``sys.path``, so a directory or
    module named ``xorchestrate`` there can never run in place of the package.
    ``PYTHONPATH`` and the user's site packages stay in effect: they are the
    user's own environment (``-I`` would also drop them and break source
    checkouts and editable development installs).
    """
    command = [sys.executable, "-P", "-m", "xorchestrate", *arguments, "serve", "--transport", "unix"]
    return command + (["--socket", str(check_socket_path(explicit_socket))] if explicit_socket else [])
