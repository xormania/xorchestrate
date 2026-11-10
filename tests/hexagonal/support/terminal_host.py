"""Deterministic peer programs, a memory terminal host and contract subjects.

The peer programs are short Python sources run with this interpreter, so real
adapters run real processes. The memory host recognises the same commands by
their marker line and simulates them, so one contract states one behaviour for
every adapter and the fake.
"""
from __future__ import annotations

import itertools
import os
import re
import sys
import time
from dataclasses import dataclass, field

from xorchestrate.model import OrchestrationError
from xorchestrate.ports import AttachInstruction, HostedTerminal, TerminalClosure, TerminalCommand

SIGINT, SIGKILL = 2, 9
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

SOURCES = {
    "lines": "import sys\nfor i in range(1, int(sys.argv[1]) + 1):\n    print(f'line-{i}', flush=True)\n",
    "exits": "import sys\nsys.exit(int(sys.argv[1]))\n",
    "echoes": ("print('ready', flush=True)\n"
               "while True:\n    line = input()\n    if line == 'quit':\n        break\n"
               "    print('got:' + line, flush=True)\n"),
    "waits": ("import signal, time\nsignal.signal(signal.SIGINT, signal.SIG_DFL)\n"
              "print('ready', flush=True)\ntime.sleep(120)\n"),
    "stubborn": ("import signal, time\nsignal.signal(signal.SIGINT, signal.SIG_IGN)\n"
                 "signal.signal(signal.SIGTERM, signal.SIG_IGN)\nprint('ready', flush=True)\ntime.sleep(120)\n"),
    "environment": ("import os\nfor key, value in sorted(os.environ.items()):\n    print(f'env:{key}={value}')\n"
                    "print('env-done', flush=True)\n"),
    "size": ("import os\ntry:\n    columns, rows = os.get_terminal_size(1)\n    print(f'size:{rows}x{columns}', flush=True)\n"
             "except OSError:\n    print('size:none', flush=True)\n"),
}


def _source(kind):
    return f"# xo-peer:{kind}\n" + SOURCES[kind]


@dataclass(frozen=True)
class Programs:
    cwd: str
    interpreter: str = sys.executable

    def command(self, kind, *arguments, environment=None, rows=None, columns=None):
        return TerminalCommand((self.interpreter, "-c", _source(kind), *map(str, arguments)), self.cwd,
                               tuple(sorted((environment or {}).items())), rows, columns)

    def lines(self, count):
        return self.command("lines", count)

    def exits(self, code):
        return self.command("exits", code)

    def echoes(self):
        return self.command("echoes")

    def waits(self):
        return self.command("waits")

    def stubborn(self):
        return self.command("stubborn")

    def environment(self, variables):
        return self.command("environment", environment=variables)

    def size(self, rows, columns):
        return self.command("size", rows=rows, columns=columns)


def wait(condition, timeout=10, interval=0.02):
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() >= deadline:
            raise AssertionError("condition not reached before the timeout")
        time.sleep(interval)


@dataclass
class Subject:
    """What the contract drives: a factory for controllers of named hosts.

    ``crasher(host_name, terminal_name, command)`` opens a terminal from a
    controller that then dies without closing, and returns the saved receipt.
    """

    factory: object
    programs: Programs
    logs: str
    crasher: object
    tracer: object = None
    forger: object = None
    unsafe: object = None
    starter: object = None
    alterer: object = None
    attacher: object = None
    additions: frozenset = frozenset()
    wait: object = staticmethod(wait)
    counter: object = field(default_factory=itertools.count)

    def traces(self, host_name):
        """What is still running or bound for ``host_name``: host processes and endpoints, empty when gone."""
        return list(self.tracer(host_name))

    def host(self, name):
        from tests.hexagonal.contracts.terminal_host import Guarded

        return Guarded(self.factory(name))

    def unrelated(self):
        return self.starter()

    def forge(self, receipt, process):
        return self.forger(receipt, process)

    def make_unsafe(self, host_name):
        self.unsafe(host_name)

    def altered(self, receipt):
        return self.alterer(receipt)

    def run_attach(self, argv, text):
        return self.attacher(argv, text)

    def log_path(self, name):
        directory = os.path.join(self.logs, f"{next(self.counter)}")
        os.makedirs(directory, mode=0o700)
        return os.path.join(directory, f"{name}.log")

    @staticmethod
    def read_log(path):
        with open(path, encoding="utf-8") as source:
            return source.read()

    def crash(self, host_name, terminal_name, command):
        return self.crasher(host_name, terminal_name, command)


# -- memory host -------------------------------------------------------------------

@dataclass
class _Running:
    kind: str
    arguments: tuple
    command: TerminalCommand
    incarnation: int
    output: list = field(default_factory=list)
    log: str | None = None
    pending: str = ""
    status: int | None = None
    signal: int | None = None
    alive: bool = True

    def exit(self, status=None, signal=None):
        self.alive, self.status, self.signal = False, status, signal


class MemoryMachine:
    """Terminals that exist independently of controllers, like a multiplexer's server."""

    def __init__(self):
        self.hosts = {}
        self.unsafe = set()
        self.incarnations = itertools.count(1)

    def start(self, host, name, command, log=None):
        if len(command.argv) < 3 or not command.argv[2].startswith("# xo-peer:"):
            raise OrchestrationError("The memory host runs only peer programs.", "invalid_arguments")
        kind = command.argv[2].split("\n", 1)[0].removeprefix("# xo-peer:")
        item = _Running(kind, command.argv[3:], command, next(self.incarnations))
        self.hosts.setdefault(host, {})[name] = item
        if kind == "lines":
            item.output += [f"line-{i}" for i in range(1, int(item.arguments[0]) + 1)]
            item.exit(0)
        elif kind == "exits":
            item.exit(int(item.arguments[0]))
        elif kind == "environment":
            item.output += [f"env:{key}={value}" for key, value in command.environment] + ["env-done"]
            item.exit(0)
        elif kind == "size":
            rows, columns = command.rows or 24, command.columns or 80
            item.output.append(f"size:{rows}x{columns}")
            item.exit(0)
        else:
            item.output.append("ready")
        item.log = log
        self.flush(item)
        return item

    @staticmethod
    def flush(item):
        if item.log:
            with open(item.log, "w", encoding="utf-8") as sink:
                sink.write("".join(line + "\n" for line in item.output))


class MemoryTerminalHost:
    """An in-memory controller with the port's ownership and identity rules."""

    output_cap = 2000

    def __init__(self, machine, name, *, survives_controller=True, interactive=True):
        self.machine, self.name = machine, name
        self.survives_controller, self.interactive = survives_controller, interactive
        self.owned = {}  # name -> incarnation this controller opened or adopted
        self.closed = False

    def readiness(self):
        return None

    # -- helpers
    def _check_name(self, name):
        if not isinstance(name, str) or not NAME.match(name):
            raise OrchestrationError("Invalid terminal name.", "invalid_arguments", {"name": name})

    def _item(self, name):
        self._check_name(name)
        if name not in self.owned:
            raise OrchestrationError("Unknown terminal.", "terminal_unknown", {"name": name})
        item = self.machine.hosts.get(self.name, {}).get(name)
        if item is None or item.incarnation != self.owned[name]:
            raise OrchestrationError("Terminal identity changed.", "terminal_identity_mismatch", {"name": name})
        return item

    def _observe(self, name, item):
        return HostedTerminal(name, item.alive, item.status, item.signal,
                              {"host": self.name, "terminal": name, "incarnation": item.incarnation})

    # -- port
    def _interactive(self):
        if not self.interactive:
            raise OrchestrationError("This host is not interactive.", "terminal_operation_unsupported")

    def open_terminal(self, name, command, log=None):
        self._check_name(name)
        existing = self.machine.hosts.get(self.name, {}).get(name)
        if existing is not None and existing.alive:
            details = {"name": name}
            if self.interactive:
                details["attach"] = list(("memory-attach", self.name, name))
            raise OrchestrationError("A running terminal has this name.", "terminal_exists", details)
        if existing is not None:  # exited: replaced, whoever opened it
            del self.machine.hosts[self.name][name]
        self.owned.pop(name, None)
        item = self.machine.start(self.name, name, command, log)
        if item.kind == "size" and not self.interactive:
            item.output[:] = ["size:none"]  # a detached process has no terminal to size
            self.machine.flush(item)
        self.owned[name] = item.incarnation
        return self._observe(name, item)

    def terminals(self):
        return [self._observe(name, self._item(name)) for name in sorted(self.owned)]

    def send_input(self, name, text):
        self._interactive()
        item = self._item(name)
        if not item.alive:
            return
        *lines, item.pending = (item.pending + text).split("\n")
        for line in lines:
            if item.kind == "echoes":
                if line == "quit":
                    item.exit(0)
                    return
                item.output.append("got:" + line)
        self.machine.flush(item)

    def interrupt(self, name):
        item = self._item(name)
        if item.alive and item.kind != "stubborn":
            item.exit(signal=SIGINT)

    def recent_output(self, name, lines=None):
        item = self._item(name)
        count = min(lines or self.output_cap, self.output_cap)
        return "\n".join(item.output[-count:]) + "\n"

    def attach_instruction(self, name=None):
        self._interactive()
        if self.name in self.machine.unsafe:
            raise OrchestrationError("The host location is unsafe.", "terminal_host_unavailable",
                                     {"reason": "the location is not private to this user"})
        if name is None:
            return AttachInstruction(("memory-attach", self.name), f"Attach to host {self.name} in memory.")
        self._item(name)
        return AttachInstruction(("memory-attach", self.name, name), f"Attach to {name} in memory.")

    def close_terminal(self, name, grace=None):
        item = self._item(name)
        if item.alive:
            item.exit(signal=SIGKILL if item.kind == "stubborn" else 15)
        final = self._observe(name, item)
        del self.machine.hosts[self.name][name]
        del self.owned[name]
        return TerminalClosure(final, True)

    def adopt(self, receipt):
        if not isinstance(receipt, dict) or receipt.get("host") != self.name:
            raise OrchestrationError("Receipt is not from this host.", "terminal_identity_mismatch")
        if not self.survives_controller:
            raise OrchestrationError("This host cannot adopt terminals.", "terminal_not_recoverable")
        name = receipt.get("terminal")
        self._check_name(name)
        if name in self.owned:
            raise OrchestrationError("Terminal already controlled.", "terminal_exists", {"name": name})
        item = self.machine.hosts.get(self.name, {}).get(name)
        if item is None:
            raise OrchestrationError("Terminal vanished.", "terminal_not_recoverable", {"name": name})
        if item.incarnation != receipt.get("incarnation"):
            raise OrchestrationError("Terminal identity changed.", "terminal_identity_mismatch", {"name": name})
        self.owned[name] = item.incarnation
        return self._observe(name, item)

    def shutdown(self):
        for item in self.machine.hosts.pop(self.name, {}).values():
            if item.alive:
                item.exit(signal=SIGKILL)
        self.owned.clear()

    def close(self):
        if not self.survives_controller:
            hosted = self.machine.hosts.get(self.name, {})
            for name in list(self.owned):
                item = hosted.pop(name, None)
                if item and item.alive:
                    item.exit(signal=SIGKILL)
        self.owned.clear()
        self.closed = True


def memory_subject(directory, *, survives_controller=True, interactive=True):
    machine = MemoryMachine()

    def factory(name):
        return MemoryTerminalHost(machine, name, survives_controller=survives_controller, interactive=interactive)

    def crasher(host_name, terminal_name, command):
        doomed = factory(host_name)
        receipt = doomed.open_terminal(terminal_name, command).receipt
        if not survives_controller:
            doomed.close()  # an owned host takes its terminals down with the controller
        return receipt

    logs = os.path.join(str(directory), "logs")
    os.makedirs(logs, mode=0o700, exist_ok=True)
    class Unrelated:
        def __init__(self):
            self.running = True

        def alive(self):
            return self.running

        def stop(self):
            self.running = False

    return Subject(factory, Programs(str(directory)), logs, crasher,
                   tracer=lambda name: [f"terminal {item}" for item in machine.hosts.get(name, {})],
                   forger=lambda receipt, process: {**receipt, "incarnation": -1},
                   unsafe=machine.unsafe.add, starter=Unrelated,
                   alterer=lambda receipt: [{**receipt, "incarnation": receipt["incarnation"] + 1000}],
                   attacher=lambda argv, text: argv[0] == "memory-attach" and any(
                       text in item.output for item in machine.hosts.get(argv[1], {}).values()))


# -- real adapters ----------------------------------------------------------------

CONTROLLER = """
import importlib, json, sys, time
from xorchestrate.ports import TerminalCommand
spec = json.loads(sys.argv[1])
module, _, factory = spec["factory"].partition(":")
host = getattr(importlib.import_module(module), factory)(spec["options"])
command = spec["command"]
command["argv"] = tuple(command["argv"])
command["environment"] = tuple(tuple(pair) for pair in command["environment"])
terminal = host.open_terminal(spec["terminal"], TerminalCommand(**command))
print(json.dumps(terminal.receipt), flush=True)
time.sleep(120)
"""


def run_in_pty(argv, text, timeout=10):
    """Run ``argv`` on a fresh pseudo-terminal as an operator would; true once ``text`` appears. Then hang up."""
    import pty
    import select
    import signal
    import subprocess

    primary, secondary = pty.openpty()
    environment = {key: value for key, value in os.environ.items() if key not in ("TMUX", "TMUX_PANE")}
    environment["TERM"] = "xterm-256color"
    client = subprocess.Popen(list(argv), stdin=secondary, stdout=secondary, stderr=secondary, env=environment,
                              start_new_session=True, close_fds=True)
    os.close(secondary)
    seen, deadline = b"", time.monotonic() + timeout
    try:
        while text.encode() not in seen and time.monotonic() < deadline and client.poll() is None:
            ready, _, _ = select.select([primary], [], [], 0.1)
            if ready:
                try:
                    seen += os.read(primary, 65536)
                except OSError:
                    break
        return text.encode() in seen
    finally:
        if client.poll() is None:
            client.send_signal(signal.SIGHUP)
        try:
            client.wait(timeout=5)
        except subprocess.TimeoutExpired:
            client.kill()
            client.wait(timeout=5)
        os.close(primary)


def adapter_subject(factory, options_for, directory, *, root, placement_path, additions=()):
    """``factory`` is ``module:callable``; ``options_for(host_name)`` gives one host's options.

    ``crash`` runs a separate controller process that opens the terminal,
    reports its receipt and is then killed with SIGKILL, never closed.
    """
    import importlib
    import json
    import signal
    import subprocess

    module, _, name = factory.partition(":")
    build = getattr(importlib.import_module(module), name)

    def crasher(host_name, terminal_name, command):
        spec = {"factory": factory, "options": options_for(host_name), "terminal": terminal_name,
                "command": {"argv": list(command.argv), "cwd": command.cwd,
                            "environment": [list(pair) for pair in command.environment],
                            "rows": command.rows, "columns": command.columns}}
        controller = subprocess.Popen([sys.executable, "-c", CONTROLLER, json.dumps(spec)], cwd=root,
                                      stdout=subprocess.PIPE, text=True)
        try:
            line = controller.stdout.readline()
        finally:
            controller.send_signal(signal.SIGKILL)
            controller.wait(timeout=10)
            controller.stdout.close()
        assert line, "the controller process did not report a receipt"
        return json.loads(line)

    logs = os.path.join(str(directory), "logs")
    os.makedirs(logs, mode=0o700, exist_ok=True)
    def tracer(host_name):
        from tests.hexagonal.support.host_processes import processes_under

        path = placement_path(host_name)
        traces = [f"process {pid}: {command}" for pid, command in processes_under([path])]
        if os.path.exists(path) and not os.path.isdir(path):
            traces.append(f"endpoint {path}")
        return traces

    class Unrelated:
        """A session leader of this user that no host launched: what a forged receipt would point at."""

        def __init__(self):
            self.process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                            start_new_session=True)

        def alive(self):
            return self.process.poll() is None

        def stop(self):
            if self.process.poll() is None:
                self.process.kill()
            self.process.wait(timeout=10)

    def forger(receipt, process):
        from xorchestrate.adapters.terminal_support import identity

        # Receipt layout knowledge stays here: both adapters keep the launched process under "process".
        return {**receipt, "process": identity(process.process.pid).as_json()}

    def alterer(receipt):
        """Each identity record in the receipt with one field changed: another process, start or boot."""
        for key, value in receipt.items():
            if isinstance(value, dict):
                for field_name, current in value.items():
                    changed = current + 1 if isinstance(current, int) else f"{current}-other"
                    yield {**receipt, key: {**value, field_name: changed}}

    def attacher(argv, text):
        return run_in_pty(argv, text)

    def unsafe(host_name):
        # Deliberately loosened: the check is that the host notices and refuses.
        os.chmod(os.path.dirname(placement_path(host_name)), 0o755)  # noqa: S103

    return Subject(lambda host_name: build(options_for(host_name)), Programs(str(directory)), logs, crasher,
                   tracer=tracer, forger=forger, unsafe=unsafe, starter=Unrelated, alterer=alterer,
                   attacher=attacher, additions=frozenset(additions))
