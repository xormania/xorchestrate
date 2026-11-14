"""Deterministic peer programs, a memory terminal host and contract subjects.

The peer programs are short Python sources run with this interpreter, so real
adapters run real processes. The memory host recognises the same commands by
their marker line and simulates them, so one contract states one behaviour for
every adapter and the fake.
"""
from __future__ import annotations

import contextlib
import itertools
import os
import re
import sys
import time
from dataclasses import dataclass, field

from xorchestrate.model import OrchestrationError
from xorchestrate.ports import AttachInstruction, HostedTerminal, InventoryItem, TerminalClosure, TerminalCommand

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
    # The child marks a SIGTERM it receives; the leader stops it with SIGKILL, which leaves no mark. So the leader
    # reports whether the host signalled its child before (or instead of) asking the leader first.
    "orderly": ("import os, signal, subprocess, sys, time\n"
                "marker = os.path.abspath(f'orderly-{os.getpid()}.term')\n"
                "child = subprocess.Popen([sys.executable, '-c', 'import signal, sys, time\\n'\n"
                "    f'def mark(*_):\\n    open({marker!r}, \"w\").close(); sys.exit(0)\\n'\n"
                "    'signal.signal(signal.SIGTERM, mark)\\n'\n"
                "    f'open({marker!r} + \".ready\", \"w\").close()\\ntime.sleep(120)\\n'])\n"
                "while not os.path.exists(marker + '.ready'):\n    time.sleep(0.01)\n"
                "def stop(*_):\n"
                "    time.sleep(0.3)\n"
                "    print(f'term:child-signalled={os.path.exists(marker)}', flush=True)\n"
                "    child.kill(); child.wait(); sys.exit(0)\n"
                "signal.signal(signal.SIGTERM, stop)\nprint('ready', flush=True)\ntime.sleep(120)\n"),
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

    def orderly(self):
        return self.command("orderly")

    def environment(self, variables):
        return self.command("environment", environment=variables)

    def size(self, rows, columns):
        return self.command("size", rows=rows, columns=columns)


def wait(condition, timeout=10, interval=0.02):
    from scripts.testkit.adapters.clock import SystemClock
    from scripts.testkit.waits import bounded_wait
    from tests.hexagonal.support.native import observation_budget

    bounded_wait(condition, observation_budget(timeout), clock=SystemClock(), sleep=time.sleep,
                 poll_interval=interval, message="condition not reached before the timeout")


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
    looker: object = None
    killer: object = None
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

    def race(self, host_name, names, command):
        """Open ``names`` concurrently, each from its own controller process (``crash``); ``(receipts, errors)``."""
        import threading

        receipts, errors = {}, []

        def open_one(name):
            try:
                receipts[name] = self.crash(host_name, name, command)
            except Exception as error:  # noqa: BLE001 - reported to the contract
                errors.append((name, error))

        racers = [threading.Thread(target=open_one, args=(name,)) for name in names]
        for racer in racers:
            racer.start()
        for racer in racers:
            racer.join(30)
        return receipts, errors

    def kill(self, host_name, receipt):
        """Kill the terminal's process outright (SIGKILL), as a crash would, and wait until it is gone."""
        self.killer(host_name, receipt)

    def look(self, host_name, receipt):
        """``attach_instruction_for(receipt)`` from a separate controller process: the argv, or the refusal's code."""
        return self.looker(host_name, receipt)

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

    def attach_instruction_for(self, receipt):
        self._interactive()
        if not isinstance(receipt, dict) or receipt.get("host") != self.name:
            raise OrchestrationError("Receipt is not from this host.", "terminal_identity_mismatch")
        name = receipt.get("terminal")
        self._check_name(name)
        item = self.machine.hosts.get(self.name, {}).get(name)
        if item is None:
            raise OrchestrationError("Terminal vanished.", "terminal_not_recoverable", {"name": name})
        if item.incarnation != receipt.get("incarnation"):
            raise OrchestrationError("Terminal identity changed.", "terminal_identity_mismatch", {"name": name})
        return AttachInstruction(("memory-attach", self.name, name), f"Attach to {name} in memory.")

    def close_terminal(self, name, grace=None):
        item = self._item(name)
        if item.alive and item.kind == "orderly":  # its own handler runs first and stops its child
            item.output.append("term:child-signalled=False")
            self.machine.flush(item)
            item.exit(0)
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

    def inventory(self):
        hosted = self.machine.hosts.get(self.name, {})
        return [InventoryItem(name, hosted[name].alive, self.owned.get(name) == hosted[name].incarnation)
                for name in sorted(hosted)]

    def sweep(self):
        hosted = self.machine.hosts.get(self.name, {})
        removed = [name for name, item in sorted(hosted.items()) if not item.alive and name not in self.owned]
        for name in removed:
            del hosted[name]
        return removed

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


def _look_in_memory(host, receipt):
    try:
        return list(host.attach_instruction_for(receipt).argv)
    except OrchestrationError as error:
        return error.code


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
                   looker=lambda name, receipt: _look_in_memory(factory(name), receipt),
                   killer=lambda name, receipt: machine.hosts[name][receipt["terminal"]].exit(signal=SIGKILL),
                   attacher=lambda argv, text: argv[0] == "memory-attach" and any(
                       text in item.output for item in machine.hosts.get(argv[1], {}).values()))


# -- real adapters ----------------------------------------------------------------

LOOKER = """
import importlib, json, sys
spec = json.loads(sys.argv[1])
module, _, factory = spec["factory"].partition(":")
host = getattr(importlib.import_module(module), factory)(spec["options"])
try:
    print(json.dumps(list(host.attach_instruction_for(spec["receipt"]).argv)))
except Exception as error:
    print(json.dumps(getattr(error, "code", type(error).__name__)))
finally:
    host.close()
"""

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

    def looker(host_name, receipt):
        spec = {"factory": factory, "options": options_for(host_name), "receipt": receipt}
        result = subprocess.run([sys.executable, "-c", LOOKER, json.dumps(spec)], cwd=root, capture_output=True,
                                text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout.strip().splitlines()[-1])

    def killer(host_name, receipt):
        from xorchestrate.adapters.terminal_support import alive, identity

        # Receipt layout knowledge stays here: both adapters keep the launched process under "process".
        leader = identity(receipt["process"]["pid"])
        if leader is None or leader.started != receipt["process"]["started"]:
            return
        os.kill(leader.pid, signal.SIGKILL)
        deadline = time.monotonic() + 10
        while alive(leader) and time.monotonic() < deadline:
            time.sleep(0.02)

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
                   tracer=tracer, forger=forger, unsafe=unsafe, starter=Unrelated, alterer=alterer, looker=looker,
                   killer=killer,
                   attacher=attacher, additions=frozenset(additions))


# -- the test kit's hosts: one readiness helper for every test that needs a real host --

MULTIPLEXER = {"adapter": "tmux"}  # selected explicitly, without the default's fallback


def host_unready(selection=MULTIPLEXER):
    """Why the selected host cannot run here (``None`` when it can), from the host's own readiness."""
    import tempfile

    from scripts.testkit.adapters import terminal_selection

    with tempfile.TemporaryDirectory(prefix="xr-") as probe:
        item = terminal_selection.readiness(selection, name="probe", location=os.path.join(probe, "host"),
                                            socket=os.path.join(probe, "host.sock"))
    return None if item["passed"] else item.get("reason", "not ready")


def requires_host(selection=MULTIPLEXER):
    """Skip a test when the selected terminal host is not ready on this machine."""
    import pytest

    reason = host_unready(selection)
    return pytest.mark.skipif(reason is not None, reason=f"terminal host unavailable: {reason}")


@contextlib.contextmanager
def owned_host(selection=MULTIPLEXER, *, name="acceptance"):
    """An owned host as the test kit builds one, shut down and removed afterwards even when the test fails."""
    import shutil
    import tempfile

    from scripts.testkit.adapters import terminal_selection
    from tests.hexagonal.support.host_processes import track

    base = track(tempfile.mkdtemp(prefix="xth-"))
    selected = terminal_selection.build(selection, name=name, location=os.path.join(base, "host"),
                                        socket=os.path.join(base, "host.sock"), owned=True)
    try:
        yield selected
    finally:
        if selected.host is not None:
            with contextlib.suppress(Exception):
                selected.host.shutdown()
        shutil.rmtree(base, ignore_errors=True)
