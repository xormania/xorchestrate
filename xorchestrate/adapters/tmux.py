"""A terminal host on a private tmux server: interactive, attachable, recoverable.

One host is one tmux server on its own socket, started with ``-f /dev/null``
so no user configuration is loaded. Each terminal is one tmux session holding
one pane. By default the server is a daemon that outlives the controller, and
a restarted controller adopts terminals from their receipts; with ``owned``
the server runs as this controller's child (``-D``) and everything stops when
the controller closes or dies.

Identity: the server is fenced by process identity (pid, start time, boot,
owner); a pane by its pane and session ids on that server (tmux never reuses
them within one server's lifetime) and the identity of the process it started.

Options: ``name`` (host name, required), ``socket`` (absolute socket path in
a private directory, at most 107 bytes, required), ``location`` (accepted
with the operator's placement and unused: the server keeps no files), ``binary`` (absolute path;
default: ``tmux`` on ``PATH``), ``owned`` (default false), ``output_lines``
(default 2000, also the history limit), ``grace`` (default 10 seconds),
``rows``/``columns`` (default 48/160), ``terminal_type`` (default
``tmux-256color``), ``control_timeout`` (default 5 seconds).
"""

from __future__ import annotations

import ctypes
import json
import os
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from ..model import OrchestrationError
from ..ports import AttachInstruction, HostedTerminal, TerminalClosure
from . import terminal_support as support
from .local_socket import open_private_file
from .process import positive_integer, positive_number

KIND = "tmux"
EXEC_SHIM = str(Path(__file__).with_name("terminal_exec.py"))
MAX_SOCKET_PATH_BYTES = 107
NESTING_MARKERS = ("TMUX", "TMUX_PANE")
CONTROL_ENVIRONMENT = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR")
INPUT_BYTES = 4096
PANE_FORMAT = ("#{pane_id}|#{session_id}|#{pane_pid}|#{pane_dead}|#{pane_dead_status}|#{pane_dead_signal}"
               "|#{@terminal_host}|#{@terminal_name}|#{@terminal_process}")


@dataclass
class _Terminal:
    name: str
    pane: str
    session: str
    leader: support.Identity | None


@dataclass(frozen=True)
class _Pane:
    pane: str
    session: str
    pid: int
    dead: bool
    status: int | None
    signal: int | None
    host: str
    name: str
    process: str


def _process_label(leader):
    return "" if leader is None else f"{leader.pid}:{leader.started}:{leader.boot_id}"


def _signal_number(value):
    if value.isdigit():
        return int(value)
    number = getattr(signal, "SIG" + value.upper().removeprefix("SIG"), None)
    return int(number) if number is not None else None


def _die_with_parent():
    ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGKILL, 0, 0, 0)  # PR_SET_PDEATHSIG


class TmuxTerminalHost:
    interactive = True

    def __init__(self, options):
        allowed = {"name", "socket", "location", "binary", "owned", "output_lines", "grace", "rows", "columns", "terminal_type",
                   "control_timeout"}
        if set(options) - allowed:
            raise OrchestrationError("Unknown terminal host options.", "invalid_configuration",
                                     {"options": sorted(set(options) - allowed)})
        self.name = support.check_name(options.get("name"), "terminal host name")
        if not isinstance(options.get("socket"), str) or not os.path.isabs(options["socket"]):
            raise OrchestrationError("socket must be an absolute path.", "invalid_configuration")
        self.socket = Path(options["socket"])
        binary = options.get("binary")
        if binary is not None and (not isinstance(binary, str) or not os.path.isabs(binary)):
            raise OrchestrationError("binary must be an absolute path.", "invalid_configuration")
        self.binary = binary or shutil.which(KIND)
        if type(options.get("owned", False)) is not bool:
            raise OrchestrationError("owned must be a boolean.", "invalid_configuration")
        self.owned = options.get("owned", False)
        self.survives_controller = not self.owned
        self.output_lines = positive_integer(options, "output_lines", 2000)
        self.grace = positive_number(options, "grace", 10.0)
        self.rows = positive_integer(options, "rows", 48)
        self.columns = positive_integer(options, "columns", 160)
        self.control_timeout = positive_number(options, "control_timeout", 5.0)
        terminal_type = options.get("terminal_type", "tmux-256color")
        if not isinstance(terminal_type, str) or not support.NAME.fullmatch(terminal_type):
            raise OrchestrationError("terminal_type must be a terminal name.", "invalid_configuration")
        self.terminal_type = terminal_type
        self._server: support.Identity | None = None
        self._child: subprocess.Popen | None = None
        self._terminals: dict[str, _Terminal] = {}
        self._environment = {key: os.environ[key] for key in CONTROL_ENVIRONMENT if key in os.environ}

    # -- capabilities -------------------------------------------------------------

    def readiness(self):
        try:
            if not self.binary or not os.access(self.binary, os.X_OK):
                return "the terminal multiplexer is not installed"
            if not Path("/proc/self/stat").exists():
                return "process identities are unavailable (no /proc)"
            if len(os.fsencode(str(self.socket))) > MAX_SOCKET_PATH_BYTES:
                return "the socket path is too long for a Unix socket"
            return support.location_problem(self.socket.parent)
        except Exception as error:  # readiness never raises
            return type(error).__name__

    # -- terminals ----------------------------------------------------------------

    def open_terminal(self, name, command, log=None):
        support.check_name(name)
        argv, cwd, environment = support.check_command(command, remove=NESTING_MARKERS)
        held = self._terminals.get(name)
        if held is not None:
            if self._observe(held).running:
                raise OrchestrationError("A terminal with this name is running.", "terminal_exists", {"name": name})
            self.close_terminal(name)  # Exited: its output was shown or logged; the name is free again.
        log_path = support.log_path(log) if log is not None else None
        environment.setdefault("TERM", self.terminal_type)
        # Values never go on a command line (readable by other users): the shim reads them from a private
        # file, removes it and execs the command with exactly this environment.
        environment_file = self._environment_file(environment)
        wrapped = [sys.executable, "-I", EXEC_SHIM, str(environment_file), "--", *argv]
        sequence = ["new-session", "-d", "-P", "-F", "#{pane_id}|#{session_id}|#{pane_pid}",
                    "-x", str(command.columns or self.columns), "-y", str(command.rows or self.rows),
                    "-c", cwd, "--", *wrapped]
        if log_path is not None:
            os.close(open_private_file(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND))
            sequence += [";", "pipe-pane", "-O", f"exec cat >> {shlex.quote(str(log_path))}"]
        # The server is kept alive while empty only until its first terminal exists; afterwards it ends with
        # its last terminal, so a closed host leaves nothing running.
        sequence += [";", "set-option", "@terminal_host", self.name, ";", "set-option", "@terminal_name", name,
                     ";", "set-option", "-g", "exit-empty", "on"]
        for attempt in range(2):
            self._ensure_server()
            server = self._server
            try:
                self._release_name(name)
                result = self._control(*sequence)
                break
            except OrchestrationError as error:
                if error.code == "terminal_exists":
                    raise
                if attempt == 0 and not support.alive(server) and not self._terminals:
                    # The server found at the socket was ending with its last terminal; start a new one.
                    self._await_exit(server)
                    continue
                environment_file.unlink(missing_ok=True)
                if not self._panes_if_running():
                    self.shutdown()
                raise
        try:
            pane, session, pid = result.stdout.strip().splitlines()[0].split("|")
        except (IndexError, ValueError):
            raise OrchestrationError("The terminal host did not report the new terminal.",
                                     "terminal_host_unavailable") from None
        terminal = _Terminal(name, pane, session, support.identity(int(pid)) if pid.isdigit() else None)
        # Label the session with the full identity of what it started, so adoption can compare a receipt with
        # what this host launched even after the process has exited.
        self._control("set-option", "-t", session, "@terminal_process", _process_label(terminal.leader))
        self._terminals[name] = terminal
        return self._observe(terminal)

    def terminals(self):
        panes = self._panes_if_running()
        return [self._observe(self._terminals[name], panes) for name in sorted(self._terminals)]

    def send_input(self, name, text):
        terminal, pane = self._verified(name)
        if not isinstance(text, str) or "\0" in text:
            raise OrchestrationError("text must be a string without NUL.", "invalid_arguments")
        if pane.dead:
            raise OrchestrationError("The terminal has exited.", "terminal_not_recoverable", {"name": name})
        # As hex bytes: tmux parses no part of them (a literal argument ending in ";" would be a separator).
        data = text.encode("utf-8")
        for start in range(0, len(data), INPUT_BYTES):
            self._control("send-keys", "-t", terminal.pane, "-H", *(f"{byte:02x}" for byte in data[start:start + INPUT_BYTES]))

    def interrupt(self, name):
        terminal, pane = self._verified(name)
        if not pane.dead:
            self._control("send-keys", "-t", terminal.pane, "C-c")

    def recent_output(self, name, lines=None):
        terminal, _ = self._verified(name)
        lines = support.check_lines(lines, self.output_lines)
        result = self._control("capture-pane", "-p", "-J", "-t", terminal.pane, "-S", f"-{lines}")
        return support.last_lines(result.stdout, lines)

    def attach_instruction(self, name=None):
        problem = support.location_problem(self.socket.parent)
        if problem is not None:
            raise OrchestrationError(f"The terminal host location is unsafe: {problem}.", "terminal_host_unavailable",
                                     {"reason": problem})
        if name is None:
            self._verified_server()
            target = []
        else:
            terminal, _ = self._verified(name)
            target = ["-t", terminal.session]
        argv = (*self._attach_prefix(), "-N", "-S", str(self.socket), "attach-session", *target)
        return AttachInstruction(argv, f"Run {shlex.join(argv)} to attach; detach again with the prefix key, then d.")

    def close_terminal(self, name, grace=None):
        terminal = self._known(name)
        grace = support.check_grace(grace, self.grace)
        verified = True
        if terminal.leader is not None:
            verified = support.stop_session(terminal.leader, grace)
        observation = self._observe(terminal)
        deadline = time.monotonic() + self.control_timeout
        while observation.running and verified and time.monotonic() < deadline:
            time.sleep(0.05)  # Stopped; give the server time to collect the exit status.
            observation = self._observe(terminal)
        pane = self._pane(terminal, self._panes_if_running())
        if pane is not None:
            # Fenced before the call: killing the last session ends the server, as intended.
            self._control("kill-session", "-t", terminal.session, check=False, server=self._verified_server(),
                          after=False)
            remaining = self._panes_if_running()
            verified = verified and self._pane(terminal, remaining) is None
            if not remaining:
                self._end_empty_server()
        del self._terminals[name]
        if terminal.leader is not None:
            verified = verified and not support.session_members(terminal.leader)
        return TerminalClosure(observation, verified)

    def adopt(self, receipt):
        if self.owned:
            raise OrchestrationError("Owned terminals belong to the controller that opened them.",
                                     "terminal_not_recoverable")
        if not isinstance(receipt, dict) or receipt.get("kind") != KIND:
            raise OrchestrationError("Not a receipt of this terminal host.", "terminal_identity_mismatch")
        if receipt.get("host") != self.name or receipt.get("socket") != str(self.socket):
            raise OrchestrationError("The receipt belongs to another terminal host.", "terminal_identity_mismatch")
        name = support.check_name(receipt.get("name"))
        server = support.Identity.from_json(receipt.get("server"))
        leader = receipt.get("process")
        leader = None if leader is None else support.Identity.from_json(leader)
        pane_id, session_id = receipt.get("pane"), receipt.get("session")
        if server is None or not isinstance(pane_id, str) or not isinstance(session_id, str):
            raise OrchestrationError("The receipt is malformed.", "terminal_identity_mismatch")
        if server.boot_id != support.boot_id() or not support.alive(server):
            raise OrchestrationError("The terminal host is gone.", "terminal_not_recoverable", {"name": name})
        if self._server is not None and self._server != server:
            raise OrchestrationError("The receipt names another server.", "terminal_identity_mismatch")
        if self._server is None:
            if self._query_server() != server:
                raise OrchestrationError("The receipt's server identity was reused.", "terminal_identity_mismatch")
            self._server = server
        if name in self._terminals:
            raise OrchestrationError("A terminal with this name is already open here.", "terminal_exists",
                                     {"name": name})
        terminal = _Terminal(name, pane_id, session_id, leader)
        panes = self._panes()
        pane = self._pane(terminal, panes)
        if pane is None:
            if any((other.host, other.name) == (self.name, name) for other in panes):
                raise OrchestrationError("The name now belongs to another terminal.", "terminal_identity_mismatch",
                                         {"name": name})
            raise OrchestrationError("The terminal is gone.", "terminal_not_recoverable", {"name": name})
        if (pane.host, pane.name, pane.process) != (self.name, name, _process_label(leader)) or (
                leader is not None and (pane.pid != leader.pid or (not pane.dead and not support.alive(leader)))):
            raise OrchestrationError("The terminal does not match its receipt.", "terminal_identity_mismatch")
        self._terminals[name] = terminal
        return self._observe(terminal)

    def shutdown(self):
        for name in list(self._terminals):
            self.close_terminal(name)
        server = self._server or (self._query_server() if self.socket.exists() else None)
        if server is not None and support.alive(server):
            self._control("kill-server", check=False, server=False)
            deadline = time.monotonic() + self.grace
            while support.alive(server) and time.monotonic() < deadline:
                time.sleep(0.02)
            if support.alive(server):
                support.send_signal(server, signal.SIGKILL)
        if self._child is not None:
            try:
                self._child.wait(timeout=self.control_timeout)
            except subprocess.TimeoutExpired:
                pass
            self._child = None
        self._server = None
        try:
            if self.socket.is_socket():
                self.socket.unlink()
        except OSError:
            pass

    def close(self):
        if self.owned:
            self.shutdown()
        self._terminals.clear()

    # -- server -------------------------------------------------------------------

    def _ensure_server(self):
        problem = self.readiness()
        if problem is not None:
            raise OrchestrationError(f"The terminal host is unavailable: {problem}.", "terminal_host_unavailable")
        if self._server is not None and support.alive(self._server):
            return
        # The server ends with its last terminal; a later open starts a new one.
        self._server = None
        support.private_directory(self.socket.parent)
        if self.socket.exists():
            # A server left by an earlier controller: reuse it only if it is ours.
            server = self._query_server()
            if server is not None:
                self._server = server
                return
            if self.socket.is_socket():
                self.socket.unlink()  # A stale socket of a dead server.
        options = ["set-option", "-g", "exit-empty", "off", ";", "set-option", "-wg", "remain-on-exit", "on", ";",
                   "set-option", "-wg", "remain-on-exit-format", "", ";",
                   "set-option", "-g", "history-limit", str(self.output_lines), ";",
                   "set-option", "-g", "default-terminal", self.terminal_type]
        if self.owned:
            self._child = subprocess.Popen([self.binary, "-D", "-S", str(self.socket), "-f", os.devnull],
                                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL, env=self._environment,
                                           preexec_fn=_die_with_parent, cwd="/")
            deadline = time.monotonic() + self.control_timeout
            while not self.socket.exists():
                if self._child.poll() is not None or time.monotonic() >= deadline:
                    raise OrchestrationError("The terminal host did not start.", "terminal_host_unavailable")
                time.sleep(0.02)
            server = support.identity(self._child.pid)
            self._server = server
            self._control(*options)
        else:
            self._control("start-server", ";", *options, start=True)
            server = self._query_server()
            self._server = server
        if self._server is None:
            raise OrchestrationError("The terminal host did not start.", "terminal_host_unavailable")
        os.chmod(self.socket, 0o600)

    def _attach_prefix(self):
        """The multiplexer command, run with its nesting markers cleared so attaching works from inside a session."""
        return (shutil.which("env") or "/usr/bin/env", *(part for marker in NESTING_MARKERS for part in ("-u", marker)),
                self.binary)

    def _release_name(self, name):
        """Free ``name`` on the server: replace an exited terminal; refuse a running one, saying how to look at it."""
        for pane in self._panes():
            if (pane.host, pane.name) != (self.name, name):
                continue
            if not pane.dead:
                argv = (*self._attach_prefix(), "-N", "-S", str(self.socket), "attach-session", "-t", pane.session)
                raise OrchestrationError("A terminal with this name is running; adopt it from its receipt.",
                                         "terminal_exists", {"name": name, "attach": list(argv)})
            # Fenced by the server identity checked in this call; ending the last session is expected here.
            self._control("kill-session", "-t", pane.session, check=False, server=self._verified_server(),
                          after=False)

    def _end_empty_server(self):
        """The server ends with its last session; wait for it, then remove the socket it leaves behind."""
        server = self._server
        if server is not None:
            self._await_exit(server)
            if support.alive(server):
                self._server = server  # Still serving (another controller opened a terminal): keep it.
                return
        try:
            if self.socket.is_socket():
                self.socket.unlink()
        except OSError:
            pass

    def _environment_file(self, environment):
        directory = support.private_directory(self.socket.parent)
        path = directory / f".environment-{secrets.token_hex(8)}"
        descriptor = open_private_file(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(environment, stream)
        return path

    def _await_exit(self, server):
        deadline = time.monotonic() + self.control_timeout
        while support.alive(server) and time.monotonic() < deadline:
            time.sleep(0.02)
        self._server = None

    def _query_server(self):
        result = self._control("display-message", "-p", "#{pid}", check=False, server=False)
        value = result.stdout.strip()
        return support.identity(int(value)) if result.returncode == 0 and value.isdigit() else None

    def _verified_server(self):
        if self._server is None or not support.alive(self._server):
            raise OrchestrationError("The terminal host is gone.", "terminal_not_recoverable")
        return self._server

    def _control(self, *arguments, check=True, start=False, server=None, after=True):
        if server is None:
            server = False if start else self._verified_server()
        command = [self.binary, *([] if start else ["-N"]), "-S", str(self.socket), "-f", os.devnull, *arguments]
        try:
            result = subprocess.run(command, env=self._environment, capture_output=True, text=True,
                                    timeout=self.control_timeout, cwd="/", stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise OrchestrationError("The terminal host did not answer.", "terminal_host_unavailable",
                                     {"error": type(error).__name__}) from None
        if server is not False and after and not support.alive(server):
            raise OrchestrationError("The terminal host changed while it was being controlled.",
                                     "terminal_identity_mismatch")
        if check and result.returncode:
            raise OrchestrationError("The terminal host refused a control request.", "terminal_host_unavailable")
        return result

    # -- panes --------------------------------------------------------------------

    def _panes(self):
        # A server without sessions answers list-panes with an error; its pane list is empty.
        result = self._control("list-panes", "-a", "-F", PANE_FORMAT, check=False)
        panes = []
        for line in result.stdout.splitlines():
            fields = line.split("|")
            if len(fields) != 9 or not fields[2].isdigit():
                continue
            dead = fields[3] == "1"
            panes.append(_Pane(fields[0], fields[1], int(fields[2]), dead,
                               int(fields[4]) if dead and fields[4].isdigit() else None,
                               _signal_number(fields[5]) if dead and fields[5] else None, fields[6], fields[7],
                               fields[8]))
        return panes

    def _panes_if_running(self):
        if self._server is None or not support.alive(self._server):
            return []
        try:
            return self._panes()
        except OrchestrationError:
            return []

    @staticmethod
    def _pane(terminal, panes):
        return next((pane for pane in panes if (pane.pane, pane.session) == (terminal.pane, terminal.session)), None)

    def _known(self, name):
        support.check_name(name)
        terminal = self._terminals.get(name)
        if terminal is None:
            raise OrchestrationError("No terminal with this name is open here.", "terminal_unknown", {"name": name})
        return terminal

    def _verified(self, name):
        terminal = self._known(name)
        self._verified_server()
        pane = self._pane(terminal, self._panes())
        if pane is None:
            raise OrchestrationError("The terminal is gone.", "terminal_not_recoverable", {"name": name})
        if (pane.host, pane.name) != (self.name, name) or (
                terminal.leader is not None and pane.pid != terminal.leader.pid):
            raise OrchestrationError("The terminal no longer matches.", "terminal_identity_mismatch", {"name": name})
        return terminal, pane

    def _observe(self, terminal, panes=None):
        panes = self._panes_if_running() if panes is None else panes
        pane = self._pane(terminal, panes)
        status = exit_signal = None
        if pane is not None and pane.dead and pane.status is None and pane.signal is None and self._server:
            # tmux 3.4 can miss SIGCHLD for a dead pane; ask our fenced server to reap again.
            support.send_signal(self._server, signal.SIGCHLD)
            time.sleep(0.02)
            pane = self._pane(terminal, self._panes_if_running()) or pane
        if pane is not None:
            running = not (pane.dead and (pane.status is not None or pane.signal is not None))
            if pane.dead and not running:
                status, exit_signal = pane.status, pane.signal
        else:
            running = terminal.leader is not None and support.alive(terminal.leader)
        receipt = {"kind": KIND, "host": self.name, "socket": str(self.socket), "name": terminal.name,
                   "server": self._server.as_json() if self._server else None, "pane": terminal.pane,
                   "session": terminal.session, "process": terminal.leader.as_json() if terminal.leader else None}
        return HostedTerminal(terminal.name, running, status, exit_signal, receipt)

