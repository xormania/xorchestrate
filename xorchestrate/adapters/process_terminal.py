"""A terminal host of detached processes: no multiplexer, not interactive.

Each terminal is one command started in a new session with its output appended
to a private log. By default it outlives the controller and a later controller
adopts it by process identity (pid, start time, boot, owner); with the
``owned`` option an orderly close stops and verifies everything. If the
controller is killed outright, each terminal's leader is killed through the
Linux parent-death signal, but a member that detached from its session can
survive.

Options: ``name`` (the host name, required), ``location`` (private directory
for logs of terminals opened without one, required), ``socket`` (accepted
with the operator's placement and unused: this host has no endpoint), ``owned`` (default
false), ``output_lines`` (cap for ``recent_output``, default 2000), ``grace``
(seconds before forcing a close, default 10).
"""

from __future__ import annotations

import ctypes
import json
import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..model import OrchestrationError
from ..ports import HostedTerminal, InventoryItem, TerminalClosure
from . import terminal_support as support
from .local_socket import open_private_file
from .process import positive_integer, positive_number

KIND = "detached-process"
TAIL_BYTES = 4 * 1024 * 1024


@dataclass
class _Terminal:
    name: str
    leader: support.Identity
    log: Path
    process: subprocess.Popen | None = None
    exit_status: int | None = None
    exit_signal: int | None = None


def _die_with_parent():
    library = ctypes.CDLL(None, use_errno=True)
    library.prctl(1, signal.SIGKILL, 0, 0, 0)  # PR_SET_PDEATHSIG


class DetachedProcessHost:
    interactive = False

    def __init__(self, options):
        unknown = set(options) - {"name", "location", "socket", "owned", "output_lines", "grace"}
        if unknown:
            raise OrchestrationError("Unknown terminal host options.", "invalid_configuration",
                                     {"options": sorted(unknown)})
        self.name = support.check_name(options.get("name"), "terminal host name")
        if not isinstance(options.get("location"), str) or not os.path.isabs(options["location"]):
            raise OrchestrationError("location must be an absolute path.", "invalid_configuration")
        self.location = Path(options["location"])
        if type(options.get("owned", False)) is not bool:
            raise OrchestrationError("owned must be a boolean.", "invalid_configuration")
        self.owned = options.get("owned", False)
        self.survives_controller = not self.owned
        self.output_lines = positive_integer(options, "output_lines", 2000)
        self.grace = positive_number(options, "grace", 10.0)
        self._terminals: dict[str, _Terminal] = {}

    # -- capabilities -------------------------------------------------------------

    def readiness(self):
        if not Path("/proc/self/stat").exists():
            return "process identities are unavailable (no /proc)"
        return support.location_problem(self.location)

    # -- terminals ----------------------------------------------------------------

    def open_terminal(self, name, command, log=None):
        support.check_name(name)
        argv, cwd, environment = support.check_command(command)
        held = self._terminals.get(name)
        if held is not None:
            if self._running(held):
                raise OrchestrationError("A terminal with this name is running.", "terminal_exists", {"name": name})
            self.close_terminal(name)  # Exited: its output is in its log; the name is free again.
        recorded = self._recorded(name)
        if recorded is not None and support.alive(recorded[0]):
            raise OrchestrationError("A terminal with this name is running; adopt it from its receipt.",
                                     "terminal_exists", {"name": name})
        problem = self.readiness()
        if problem is not None:
            raise OrchestrationError(f"The terminal host is unavailable: {problem}.", "terminal_host_unavailable")
        path = support.log_path(log) if log is not None else support.private_directory(self.location) / f"{name}.log"
        descriptor = open_private_file(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=descriptor, stderr=descriptor, cwd=cwd,
                                       env=environment, start_new_session=True, close_fds=True,
                                       preexec_fn=_die_with_parent if self.owned else None)
        except OSError as error:
            raise OrchestrationError("The terminal command could not be started.", "terminal_host_unavailable",
                                     {"name": name, "error": type(error).__name__}) from None
        finally:
            os.close(descriptor)
        leader = support.identity(process.pid)
        if leader is None:  # Exited before it could be observed; its status is still ours to collect.
            process.wait()
            leader = support.Identity(process.pid, -1, support.boot_id())
        terminal = _Terminal(name, leader, path, process)
        self._terminals[name] = terminal
        self._record(name, leader, path)
        return self._observe(terminal)

    def terminals(self):
        return [self._observe(self._terminals[name]) for name in sorted(self._terminals)]

    def send_input(self, name, text):
        self._known(name)
        raise OrchestrationError("Detached-process terminals do not accept input.", "terminal_operation_unsupported")

    def interrupt(self, name):
        terminal = self._known(name)
        if self._running(terminal):
            support.send_signal(terminal.leader, signal.SIGINT)

    def recent_output(self, name, lines=None):
        terminal = self._known(name)
        lines = support.check_lines(lines, self.output_lines)
        try:
            descriptor = open_private_file(terminal.log, os.O_RDONLY)
        except FileNotFoundError:
            return ""
        with os.fdopen(descriptor, "rb") as stream:
            size = stream.seek(0, os.SEEK_END)
            stream.seek(max(0, size - TAIL_BYTES))
            data = stream.read(TAIL_BYTES)
        return support.last_lines(data.decode("utf-8", "replace"), lines)

    def attach_instruction(self, name=None):
        if name is not None:
            self._known(name)
        raise OrchestrationError("Detached-process terminals cannot be attached to.", "terminal_operation_unsupported")

    def attach_instruction_for(self, receipt):
        raise OrchestrationError("Detached-process terminals cannot be attached to.", "terminal_operation_unsupported")

    def close_terminal(self, name, grace=None):
        terminal = self._known(name)
        grace = support.check_grace(grace, self.grace)
        verified = support.stop_session(terminal.leader, grace) if terminal.leader.started >= 0 else True
        observation = self._observe(terminal)
        del self._terminals[name]
        if (self._recorded(name) or (None,))[0] == terminal.leader:
            self._forget_record(name, terminal.log)
        return TerminalClosure(observation, verified and not support.alive(terminal.leader))

    def adopt(self, receipt):
        if self.owned:
            raise OrchestrationError("Owned terminals belong to the controller that opened them.",
                                     "terminal_not_recoverable")
        if not isinstance(receipt, dict) or receipt.get("kind") != KIND:
            raise OrchestrationError("Not a receipt of this terminal host.", "terminal_identity_mismatch")
        if receipt.get("host") != self.name:
            raise OrchestrationError("The receipt belongs to another terminal host.", "terminal_identity_mismatch")
        name = support.check_name(receipt.get("name"))
        leader = support.Identity.from_json(receipt.get("process"))
        if leader is None:
            raise OrchestrationError("The receipt is malformed.", "terminal_identity_mismatch")
        if leader.boot_id != support.boot_id():
            raise OrchestrationError("The terminal did not survive a restart of the machine.", "terminal_not_recoverable",
                                     {"name": name})
        if name in self._terminals:
            raise OrchestrationError("A terminal with this name is already open here.", "terminal_exists",
                                     {"name": name})
        # Only what this host itself launched under the name can be adopted: the receipt must match the host's
        # private record, so a stale, corrupt or foreign receipt can never point it at another process.
        recorded = self._recorded(name)
        current = support.identity(leader.pid)
        if recorded is not None and recorded[0] != leader:
            raise OrchestrationError("The receipt does not match what this host launched under the name.",
                                     "terminal_identity_mismatch", {"name": name})
        if current is None:
            raise OrchestrationError("The terminal is gone.", "terminal_not_recoverable", {"name": name})
        if recorded is None:
            raise OrchestrationError("This host has no record of launching the receipt's process.",
                                     "terminal_identity_mismatch", {"name": name})
        if current != leader or support._stat(leader.pid)[1] != leader.pid:
            raise OrchestrationError("The receipt's process identity was reused.", "terminal_identity_mismatch",
                                     {"name": name})
        terminal = _Terminal(name, leader, recorded[1])
        self._terminals[name] = terminal
        return self._observe(terminal)

    def inventory(self):
        items = []
        for name in self._record_names():
            recorded = self._recorded(name)
            if recorded is None:
                continue
            held = self._terminals.get(name)
            items.append(InventoryItem(name, support.alive(recorded[0]),
                                       held is not None and held.leader == recorded[0]))
        return items

    def sweep(self):
        swept = []
        for name in self._record_names():
            recorded = self._recorded(name)
            if recorded is None or support.alive(recorded[0]):
                continue
            held = self._terminals.get(name)
            if held is not None and held.leader == recorded[0]:
                continue
            self._forget_record(name, recorded[1])
            swept.append(name)
        if not self._record_names():
            self._remove_empty_location()  # Nothing left: the host ends, like the multiplexer's server.
        return swept

    def _remove_empty_location(self):
        for directory in (self.location / "terminals", self.location):
            try:
                directory.rmdir()  # Only when empty: a caller's own files are never removed.
            except OSError:
                pass

    def _record_names(self):
        records = self.location / "terminals"
        if not records.is_dir():
            return []
        return sorted(path.stem for path in records.glob("*.json") if support.NAME.fullmatch(path.stem))

    def _forget_record(self, name, log):
        """Remove the host's record of ``name`` and the log the host itself chose for it (a caller's log is kept)."""
        (self.location / "terminals" / f"{name}.json").unlink(missing_ok=True)
        if Path(log) == self.location / f"{name}.log":
            Path(log).unlink(missing_ok=True)

    def shutdown(self):
        for name in list(self._terminals):
            self.close_terminal(name)
        # Terminals other controllers opened here: stop each recorded process, fenced by its identity.
        for name in self._record_names():
            recorded = self._recorded(name)
            if recorded is None:
                continue
            if recorded[0].boot_id == support.boot_id():
                support.stop_session(recorded[0], self.grace)
            self._forget_record(name, recorded[1])
        self._remove_empty_location()

    def close(self):
        """Release this controller. Owned: stop only this controller's own terminals, never other controllers'."""
        if self.owned:
            for name in list(self._terminals):
                self.close_terminal(name)
        else:
            for terminal in self._terminals.values():
                if terminal.process is not None:
                    terminal.process.returncode = 0  # Do not reap or warn: the process is meant to survive us.
            self._terminals.clear()

    # -- internals ----------------------------------------------------------------

    def _record(self, name, leader, log):
        """Note which process currently holds ``name`` here, so a stale receipt is told from a vanished one."""
        directory = support.private_directory(self.location / "terminals")
        temporary = directory / f".{name}.{os.getpid()}.tmp"
        descriptor = open_private_file(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump({"process": leader.as_json(), "log": str(log)}, stream)
        temporary.replace(directory / f"{name}.json")

    def _recorded(self, name):
        """``(process identity, log path)`` this host launched under ``name``, or ``None``; a corrupt record is removed."""
        path = self.location / "terminals" / f"{name}.json"
        try:
            descriptor = open_private_file(path, os.O_RDONLY)
        except FileNotFoundError:
            return None
        except OrchestrationError:
            return None  # Not a private file of ours: never trusted, never removed.
        with os.fdopen(descriptor, encoding="utf-8") as stream:
            try:
                value = json.loads(stream.read(4096))
                leader = support.Identity.from_json(value.get("process"))
                log = support.log_path(value.get("log"))
            except (ValueError, AttributeError, OrchestrationError):
                leader = None
        if leader is None:
            path.unlink(missing_ok=True)
            return None
        return leader, log

    def _known(self, name):
        support.check_name(name)
        terminal = self._terminals.get(name)
        if terminal is None:
            raise OrchestrationError("No terminal with this name is open here.", "terminal_unknown", {"name": name})
        return terminal

    def _running(self, terminal):
        if terminal.process is not None and terminal.exit_status is None and terminal.exit_signal is None:
            code = terminal.process.poll()
            if code is not None:
                terminal.exit_status, terminal.exit_signal = (code, None) if code >= 0 else (None, -code)
                return False
        return terminal.leader.started >= 0 and support.alive(terminal.leader)

    def _observe(self, terminal):
        running = self._running(terminal)
        receipt = {"kind": KIND, "host": self.name, "name": terminal.name, "process": terminal.leader.as_json(),
                   "log": str(terminal.log)}
        return HostedTerminal(terminal.name, running, None if running else terminal.exit_status,
                              None if running else terminal.exit_signal, receipt)

