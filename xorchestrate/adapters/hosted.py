"""Hosted sessions: a native conversation in a bridge host that Xorchestrate opens in its terminal host.

Each session is one bridge host (``python -m xorchestrate.bridge``) running in a
terminal of the project's terminal host, attached to through the existing
bridge driver. Its private directory keeps the generated host configuration,
the host's state file and the terminal's log, so the conversation can be
reconnected while the terminal runs and resumed in a fresh terminal after it
is gone (a reboot, or after ``stop``).

Configuration: ``{"adapter": "hosted", "options": {"session": {"adapter": ...,
"options": {...}}, "application_tools": bool}}``. Options: ``cwd`` (resolved by
composition; the inner session's relative ``cwd`` is resolved against it),
``directory`` (private per-session directories; resolved by composition),
``attach_timeout`` (seconds, default 30), ``timeout`` (bridge request
timeout, default 30), ``max_sessions`` (default: the wrapped selection's
``max_sessions``, else 64).

Composition binds the terminal host with ``bind_terminal_host``; without one,
sessions are refused with ``terminal_host_unavailable``.

- ``stop`` asks the bridge host to stop, then always closes the terminal, even
  when the bridge host does not answer or this runtime holds no connection to
  it (so a failed stop can be retried). The private directory and state
  remain, so a later resume reopens the terminal with the same state.
- ``max_sessions`` bounds the running hosted terminals of this entry, the
  detached ones a previous runtime left included, plus starts in flight. A new
  terminal past the limit is refused with ``session_capacity`` before anything
  is created; reconnecting to a running terminal is not a new session.
- ``detach`` and ``close`` only detach: the terminal keeps running.
"""

from __future__ import annotations

import json
import os
import re
import socket
import stat
import sys
import threading
import time
import uuid
from copy import deepcopy
from pathlib import Path

from ..model import OrchestrationError
from ..ports import TerminalCommand
from . import terminal_support as support
from .bridge import BridgeSessionDriver
from .local_socket import open_private_file
from .process import positive_integer, positive_number, resolve_repo

NATIVE = "native"
MAX_SOCKET_PATH_BYTES = 107
ENDPOINT_RELEASE_WAIT = 5.0
"""How long a just-killed bridge host's endpoint may stay bound before a reopen refuses."""


def _remove_stale_endpoint(endpoint, release_wait=ENDPOINT_RELEASE_WAIT):
    """Remove ``endpoint`` once it is a socket of this user that nothing listens on (its host is gone).

    Called only after the session's terminal is verified gone. A bridge host
    that was just killed can still hold its listening socket for a moment
    while the kernel tears the process down, so a socket that still answers
    is given up to ``release_wait`` seconds to be released before refusing.
    """
    deadline = time.monotonic() + release_wait
    while True:
        try:
            metadata = os.lstat(endpoint)
        except FileNotFoundError:
            return
        if not stat.S_ISSOCK(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise OrchestrationError("The hosted session's endpoint path is occupied by something else.",
                                     "terminal_host_unavailable", {"outcome": "not_started"})
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.settimeout(1)
        try:
            probe.connect(str(endpoint))
        except (ConnectionRefusedError, FileNotFoundError):
            Path(endpoint).unlink(missing_ok=True)
            return
        except OSError:
            pass
        finally:
            probe.close()
        if time.monotonic() >= deadline:
            raise OrchestrationError("Something still listens on the hosted session's endpoint.",
                                     "terminal_host_unavailable", {"outcome": "not_started"})
        time.sleep(0.05)


def _terminal_name(entry, key):
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", entry).strip(".-_") or "session"
    return f"{base[:40]}-{key}"


class HostedSessionDriver:
    """Starts each session's bridge host in a hosted terminal and attaches to it through the bridge driver."""

    def __init__(self, options):
        options = deepcopy(options)
        unknown = set(options) - {"session", "application_tools", "cwd", "directory", "attach_timeout", "timeout",
                                  "max_sessions"}
        if unknown:
            raise OrchestrationError("Unknown hosted session options.", "invalid_configuration",
                                     {"options": sorted(unknown)})
        inner = options.get("session")
        if (not isinstance(inner, dict) or not isinstance(inner.get("adapter"), str) or not inner["adapter"]
                or set(inner) - {"adapter", "options"} or not isinstance(inner.get("options", {}), dict)):
            raise OrchestrationError("hosted needs a session selection {\"adapter\", \"options\"}.",
                                     "invalid_configuration")
        if inner["adapter"] == "hosted":
            raise OrchestrationError("A hosted session cannot host another hosted session.", "invalid_configuration")
        if type(options.get("application_tools", False)) is not bool:
            raise OrchestrationError("application_tools must be a boolean.", "invalid_configuration")
        for name in ("cwd", "directory"):
            if not isinstance(options.get(name), str) or not os.path.isabs(options[name]):
                raise OrchestrationError(f"hosted {name} must be an absolute path.", "invalid_configuration")
        inner = {"adapter": inner["adapter"], "options": dict(inner.get("options", {}))}
        if not isinstance(inner["options"].get("cwd", "."), str):
            raise OrchestrationError("The hosted session's cwd must be a path.", "invalid_configuration")
        self.cwd = Path(options["cwd"])
        self.inner = inner
        self.application_tools = options.get("application_tools", False)
        self.directory = Path(options["directory"])
        self.attach_timeout = positive_number(options, "attach_timeout", 30.0)
        self.bridge_options = {"timeout": positive_number(options, "timeout", 30.0)}
        self.max_sessions = positive_integer(
            options, "max_sessions", positive_integer(inner["options"], "max_sessions", 64))
        self.host = None
        self.endpoints = None
        self.entry = "session"
        self.config_version = None
        self._capabilities, self._callback, self._replay, self._observer = (), None, None, None
        self._bridges = {}
        self._terminals = {}  # handle -> terminal name, for the sessions this runtime holds
        self._starting = set()  # terminal names reserved by opens in flight
        self._lock = threading.RLock()
        self._closed = False

    # -- composition bindings -------------------------------------------------------

    def bind_terminal_host(self, host, *, endpoints, entry, config_version=None):
        """Bind the project's terminal host, the short private directory for endpoints, and this entry's name."""
        self.host, self.endpoints, self.entry, self.config_version = host, Path(endpoints), entry, config_version

    def bind_application_tools(self, capabilities, callback):
        self._capabilities, self._callback = tuple(capabilities), callback

    def bind_application_replay(self, callback):
        self._replay = callback

    def bind_observer(self, observer):
        self._observer = observer

    # -- lifecycle --------------------------------------------------------------------

    def start(self, specification):
        return self._launch(specification)

    def attach(self, specification):
        return self._launch(specification)

    def resume(self, source_backend, specification):
        return self._recover("resume", source_backend, specification)

    def reconnect(self, source_backend, specification):
        return self._recover("reconnect", source_backend, specification)

    def send(self, session, message):
        bridge, inner = self._bridge(session)
        return bridge.send(inner, message)

    def deliver(self, session, message):
        bridge, inner = self._bridge(session)
        return bridge.deliver(inner, message)

    def poll(self, session):
        bridge, inner = self._bridge(session)
        return bridge.poll(inner)

    def inspect(self, session):
        bridge, inner = self._bridge(session)
        return bridge.inspect(inner)

    def detach(self, session):
        """Detach the application; the hosted conversation and its terminal keep running."""
        bridge, inner = self._bridge(session)
        try:
            return bridge.detach(inner)
        finally:
            self._forget(session)

    def stop(self, session):
        """End the conversation: stop the bridge host, then always close its terminal.

        The terminal is closed even when the bridge host fails or does not
        answer, and when this runtime holds no connection to it (a retry after
        such a failure, or a session another runtime started): the terminal is
        then adopted from the session's receipt. A bridge failure with the
        terminal closed is a successful stop that reports the bridge host did
        not acknowledge. A failed close raises; stop can be called again. The
        session's directory and state stay for a later resume.
        """
        hosted = session.get("hosted") if isinstance(session, dict) else None
        if not isinstance(hosted, dict) or not isinstance(hosted.get("terminal"), dict) \
                or not isinstance(hosted["terminal"].get("name"), str):
            raise OrchestrationError("Backend receipt is not a hosted session.", "session_unavailable")
        host = self._require_host()
        inner = {name: value for name, value in session.items() if name != "hosted"}
        with self._lock:
            bridge = self._bridges.get(inner.get("handle"))
        result, acknowledged, failure = {}, None, None
        if bridge is not None:
            try:
                stopped = bridge.stop(inner)
                result = stopped if isinstance(stopped, dict) else {"result": stopped}
                acknowledged = True
            except (OrchestrationError, OSError) as error:
                acknowledged = False
                failure = error.code if isinstance(error, OrchestrationError) else "bridge_unavailable"
            finally:
                self._forget(session)
        name = hosted["terminal"]["name"]
        try:
            host.adopt(hosted["terminal"])
        except OrchestrationError as error:
            if error.code not in {"terminal_exists", "terminal_not_recoverable"}:
                raise
        closure = None
        if any(item.name == name for item in host.terminals()):
            closure = host.close_terminal(name)
        verified = True if closure is None else closure.cleanup_verified
        report = {**result, "terminal": {"closed": closure is not None, "cleanup_verified": verified}}
        if acknowledged is not True:
            report["bridge"] = {"acknowledged": False, "reason": failure or "not_connected"}
        return report

    def close(self):
        with self._lock:
            self._closed = True
            bridges = list(self._bridges.values())
            self._bridges.clear()
            self._terminals.clear()
        for bridge in bridges:
            bridge.close()  # Detaches; hosted terminals keep running.

    # -- internals --------------------------------------------------------------------

    def _require_host(self):
        if self.host is None:
            raise OrchestrationError("No terminal host is bound for hosted sessions.", "terminal_host_unavailable",
                                     {"outcome": "not_started"})
        return self.host

    def _paths(self, key):
        directory = self.directory / key
        endpoint = self.endpoints / f"{self.host.name}.{key}.sock"
        if len(os.fsencode(str(endpoint))) > MAX_SOCKET_PATH_BYTES:
            raise OrchestrationError("The hosted session endpoint path is too long for a Unix socket.",
                                     "terminal_host_unavailable", {"outcome": "not_started"})
        return directory, endpoint

    def _write_private(self, path, value):
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        descriptor = open_private_file(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream)
        temporary.replace(path)

    def _resolved_inner(self):
        """The wrapped selection for the bridge host: ``${repo}`` resolved for this call's worktree, cwd absolute."""
        inner = deepcopy(self.inner)
        options = inner["options"]
        for key in ("command", "discovery_command"):
            if isinstance(options.get(key), list):
                options[key] = [resolve_repo(item) if isinstance(item, str) else item for item in options[key]]
        if isinstance(options.get("env"), dict):
            options["env"] = {name: resolve_repo(value) if isinstance(value, str) else value
                              for name, value in options["env"].items()}
        options["cwd"] = str((self.cwd / resolve_repo(options.get("cwd", "."))).resolve())
        return inner

    def _prepare(self, key):
        directory, endpoint = self._paths(key)
        support.private_directory(self.directory)
        support.private_directory(directory)
        support.private_directory(self.endpoints)
        config = {"sessions": {NATIVE: self._resolved_inner()}}
        if self.config_version is not None:
            config["version"] = self.config_version
        self._write_private(directory / "config.json", config)
        return directory, endpoint

    def _open_terminal(self, key, native_specification):
        host = self._require_host()
        directory, endpoint = self._prepare(key)
        name = _terminal_name(self.entry, key)
        # Only paths and names on the command line (readable by other local users); the native specification,
        # which can carry a work goal, goes in a private file the bridge host reads.
        self._write_private(directory / "specification.json", native_specification)
        argv = [sys.executable, "-P", "-m", "xorchestrate.bridge", "--config", str(directory / "config.json"),
                "--driver", NATIVE, "--endpoint", str(endpoint), "--state", str(directory / "state.json"),
                "--specification-file", str(directory / "specification.json")]
        if self.application_tools:
            argv.append("--application-tools")
        terminal = host.open_terminal(name, TerminalCommand(tuple(argv), str(directory), tuple(os.environ.items())),
                                      log=str(directory / "terminal.log"))
        return terminal, directory, endpoint

    def _new_bridge(self, endpoint):
        bridge = BridgeSessionDriver({"endpoint": str(endpoint), **self.bridge_options})
        if self._callback is not None:
            bridge.bind_application_tools(self._capabilities, self._callback)
        if self._replay is not None:
            bridge.bind_application_replay(self._replay)
        if self._observer is not None:
            bridge.bind_observer(self._observer)
        return bridge

    def _attach_while_running(self, terminal, directory, endpoint, call):
        """Retry ``call(bridge, remaining)`` within one attachment allowance.

        The bridge carries that remainder through connect, attach and activate;
        terminal control and any failure cleanup retain their own port policy.
        """
        host = self.host
        deadline = time.monotonic() + self.attach_timeout
        last = None
        while True:
            remaining = deadline - time.monotonic()
            if remaining > 0:
                bridge = self._new_bridge(endpoint)
                try:
                    return bridge, call(bridge, remaining)
                except OrchestrationError as error:
                    bridge.close()
                    last = error
                    if error.code != "bridge_unavailable":
                        raise
            current = next((item for item in host.terminals() if item.name == terminal.name), None)
            if current is None or not current.running or time.monotonic() >= deadline:
                # A safe summary and where the private log is: what the terminal printed stays in that log
                # (it is not provider error data, so it never enters the public response or preserved outcome).
                details = {"outcome": "not_started", "log": str(directory / "terminal.log"),
                           "terminal_running": bool(current and current.running)}
                if current is not None:
                    host.close_terminal(terminal.name)
                reason = "exited" if current is None or not current.running else "did not accept a connection in time"
                raise OrchestrationError(f"The hosted session's bridge host {reason}.", "session_start_failed",
                                         details) from last
            time.sleep(min(0.1, max(0, deadline - time.monotonic())))

    def _entry_terminals(self, host):
        """Names of this entry's running hosted terminals at the host, whoever holds them."""
        pattern = re.compile(re.escape(_terminal_name(self.entry, "")) + r"[0-9a-f]{12}")
        return {item.name for item in host.inventory() if item.running and pattern.fullmatch(item.name)}

    def _reserve(self, key):
        """Reserve a new terminal for ``key`` within ``max_sessions``, before anything is created."""
        host = self._require_host()
        name = _terminal_name(self.entry, key)
        with self._lock:
            if self._closed:
                raise OrchestrationError("The hosted driver is closed.", "session_unavailable",
                                         {"outcome": "not_started"})
            occupied = self._entry_terminals(host) | set(self._terminals.values()) | self._starting
            if len(occupied - {name}) >= self.max_sessions:
                raise OrchestrationError("Configured concurrent hosted session limit reached.", "session_capacity",
                                         {"outcome": "not_started", "max_sessions": self.max_sessions})
            self._starting.add(name)
        return name

    def _release(self, name):
        with self._lock:
            self._starting.discard(name)

    def _launch(self, specification):
        if self._closed:
            raise OrchestrationError("The hosted driver is closed.", "session_unavailable", {"outcome": "not_started"})
        specification = deepcopy(specification)
        context = specification.pop("context", {})
        key = uuid.uuid4().hex[:12]
        reserved = self._reserve(key)
        try:
            terminal, directory, endpoint = self._open_terminal(key, specification)
            bridge, receipt = self._attach_while_running(terminal, directory, endpoint,
                                                         lambda bridge, remaining: bridge.attach({"context": context}, timeout=remaining))
            return self._hosted(bridge, receipt, terminal, key, directory, endpoint, specification, context)
        finally:
            self._release(reserved)

    def _recover(self, method, source_backend, specification):
        hosted = source_backend.get("hosted") if isinstance(source_backend, dict) else None
        if not isinstance(hosted, dict) or not isinstance(hosted.get("key"), str):
            raise OrchestrationError("The source is not a hosted session receipt.", "session_identity",
                                     {"outcome": "not_started"})
        host = self._require_host()
        key = hosted["key"]
        if not re.fullmatch(r"[0-9a-f]{12}", key):
            raise OrchestrationError("The hosted session key is malformed.", "session_identity",
                                     {"outcome": "not_started"})
        directory, endpoint = self._paths(key)
        if not (directory / "state.json").exists():
            raise OrchestrationError("The hosted session's saved state is gone.", "session_unavailable",
                                     {"outcome": "not_started"})
        inner_source = {name: value for name, value in source_backend.items() if name != "hosted"}
        context = deepcopy(specification).get("context", {})
        native_specification = deepcopy(hosted.get("native_specification", {}))
        try:
            terminal = host.adopt(hosted["terminal"])
        except OrchestrationError as error:
            if error.code == "terminal_exists":
                terminal = next(item for item in host.terminals() if item.name == hosted["terminal"]["name"])
            elif error.code in {"terminal_not_recoverable", "terminal_identity_mismatch"}:
                terminal = None
            else:
                raise
        if terminal is not None and not terminal.running:
            host.close_terminal(terminal.name)
            terminal = None
        reserved = None
        try:
            if terminal is None:
                # Gone (a reboot, a container restart, or stopped): a fresh terminal resumes the conversation from
                # the saved state, a new session for capacity. The bridge host never replaces an existing endpoint,
                # so remove our own stale one first.
                reserved = self._reserve(key)
                _remove_stale_endpoint(endpoint)
                terminal, directory, endpoint = self._open_terminal(key, native_specification)
            bridge, receipt = self._attach_while_running(
                terminal, directory, endpoint,
                lambda bridge, remaining: getattr(bridge, method)(inner_source, {"context": context}, timeout=remaining))
            return self._hosted(bridge, receipt, terminal, key, directory, endpoint, native_specification, context)
        finally:
            if reserved is not None:
                self._release(reserved)

    def _hosted(self, bridge, receipt, terminal, key, directory, endpoint, native_specification, context):
        receipt = deepcopy(receipt)
        with self._lock:
            self._bridges[receipt["handle"]] = bridge
            self._terminals[receipt["handle"]] = terminal.name
        self._diagnose(receipt, terminal, directory, context)
        return {**receipt, "hosted": {"key": key, "terminal": terminal.receipt, "endpoint": str(endpoint),
                                      "native_specification": native_specification}}

    def _diagnose(self, receipt, terminal, directory, context):
        """Report, as an observation rather than in the receipt, whether the session survives and how to look."""
        if self._observer is None:
            return
        host = self.host
        attach = None
        if host.interactive:
            try:
                attach = host.attach_instruction(terminal.name).text
            except OrchestrationError:
                attach = None
        observation = {"kind": "hosted_terminal", "native_id": receipt["native_id"],
                       "incarnation": receipt["incarnation"], "terminal": terminal.name,
                       "survives_controller": host.survives_controller, "interactive": host.interactive,
                       "attach": attach, "log": str(directory / "terminal.log")}
        try:
            self._observer(deepcopy(context), observation)
        except OrchestrationError:
            pass  # Diagnostics never fail a started session.

    def _bridge(self, session):
        if not isinstance(session, dict) or not isinstance(session.get("hosted"), dict):
            raise OrchestrationError("Backend receipt is not a hosted session.", "session_unavailable")
        inner = {name: value for name, value in session.items() if name != "hosted"}
        with self._lock:
            bridge = self._bridges.get(inner.get("handle"))
        if bridge is None:
            raise OrchestrationError("Backend receipt does not identify a hosted connection.", "session_unavailable")
        return bridge, inner

    def _forget(self, session):
        with self._lock:
            bridge = self._bridges.pop(session.get("handle"), None)
            self._terminals.pop(session.get("handle"), None)
        if bridge is not None:
            bridge.close()
