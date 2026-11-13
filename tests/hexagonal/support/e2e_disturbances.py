"""Named disturbances, stage gates and bounded waits for end-to-end worlds, implemented once.

Kills act only on the world's own processes. A victim is a process of the current user whose initial
environment sets ``XORCHESTRATE_HOME`` to exactly this world's home (a fresh temporary path the world
created), or a process the world spawned itself. Nothing is chosen by working directory, path prefix or
command line. Only that one environment key is read, compared and discarded; an unreadable environment
means "not a victim". Each victim's identity (pid and start time) is captured at selection, and the kill
goes through the test kit's fenced ``send_signal``, so a reused pid is never signalled.

Each disturbance returns a ``Receipt`` naming the identities it found (``before``) and those still alive
afterwards (``effect``); a survivor is a failure. Gates hold a peer at a named stage through the shared
process-side ``controlled_gates.wait_gate`` and are released by the world's cleanup scope. Waits use the
shared classified wait budget; while one runs, ``remaining`` gives the seconds left so the world's blocking
calls can use them as their timeout.
"""
import os
import signal
import stat
import subprocess
import time
import weakref
from dataclasses import dataclass
from pathlib import Path

from scripts.testkit.adapters import ownership
from scripts.testkit.adapters.clock import SystemClock
from scripts.testkit.waits import WaitBudget, WaitTimeout
from tests.hexagonal.contracts.e2e_world import Receipt, ServerLoss, TotalLoss, WaitExpired
from tests.hexagonal.support.native import wait_for

HOME_KEY = b"XORCHESTRATE_HOME="
POLL_SECONDS = .25
"""Pause between observations of an await: an observation may start a CLI process, so not faster."""
SIGNAL_GRACE = 10
"""Seconds a killed process may take to disappear before the disturbance counts as incomplete."""

_waits = weakref.WeakKeyDictionary()
"""The deadlines of the awaits in progress, innermost last, by world."""
_handed = weakref.WeakKeyDictionary()
"""The remaining budgets handed to the world's blocking calls during its current awaits."""


# Victims -------------------------------------------------------------------------------------------------

def _has_home(pid, expected):
    """Whether process ``pid``'s initial environment sets the home key to exactly ``expected``.

    Reads only that key; the environment block is never kept, logged or returned.
    """
    try:
        with open(f"/proc/{pid}/environ", "rb") as source:
            block = source.read()
    except OSError:
        return False
    wanted = HOME_KEY + expected
    return any(entry == wanted for entry in block.split(b"\0"))


def _fresh_home(world_home, directory):
    """The world's home as bytes, if it is a directory inside the world's own directory; else refuse."""
    home, root = os.path.realpath(world_home), os.path.realpath(directory)
    if not home.startswith(root + os.sep) or not os.path.isdir(home):
        raise AssertionError("refusing to disturb: the world's home is not a fresh directory of its own")
    return os.fsencode(str(world_home))


def _victims(home, spawned=()):
    """Identities of this user's processes carrying ``home`` (bytes), plus the still-running spawned ones."""
    uid, table = os.getuid(), ownership.processes()
    found = {}
    for pid, (identity, _, state) in table.items():
        if state == "Z" or pid == os.getpid():
            continue
        try:
            if os.stat(f"/proc/{pid}").st_uid != uid:
                continue
        except OSError:
            continue
        if _has_home(pid, home):
            found[identity] = None
    for process in spawned:
        if process.poll() is None and process.pid in table:
            found[table[process.pid][0]] = None
    return list(found)


def _kill(identities):
    """SIGKILL each identity through the fenced signal, then return those still alive after the grace."""
    for identity in identities:
        ownership.send_signal(identity, signal.SIGKILL)
    try:
        wait_for(lambda: not any(ownership.alive(identity) for identity in identities), SIGNAL_GRACE,
                 message="killed processes did not disappear")
    except WaitTimeout:
        pass
    return [identity for identity in identities if ownership.alive(identity)]


def _kill_until_quiet(home, spawned=()):
    """Kill every victim, again for any that appeared meanwhile; return (all victims, survivors)."""
    victims, survivors = [], []
    for _ in range(3):
        fresh = [identity for identity in _victims(home, spawned) if identity not in victims]
        if not fresh:
            break
        victims += fresh
        survivors = _kill(victims)
        if survivors:
            break
    return victims, survivors


def _receipt(kind, target, before, effect):
    receipt = Receipt(kind=kind, target=target, before=list(before), effect=list(effect),
                      outcome="applied" if before else "no_victim")
    if effect:
        raise AssertionError(f"the disturbance left {len(effect)} of its victims alive: {receipt}")
    return receipt


# Disturbances -------------------------------------------------------------------------------------------

def _remove_sockets(directory):
    for root, _, files in os.walk(directory):
        for name in files:
            path = os.path.join(root, name)
            try:
                if stat.S_ISSOCK(os.lstat(path).st_mode):
                    os.unlink(path)
            except FileNotFoundError:
                continue


def _total_loss(value, world):
    home = _fresh_home(world.cli.home, world.directory)
    before, effect = _kill_until_quiet(home, world.spawned)
    receipt = _receipt("total_loss", str(world.directory), before, effect)
    if not value.keep_sockets:
        _remove_sockets(world.directory)
    return receipt


def _server_loss(value, world):
    home = _fresh_home(world.cli.home, world.directory)
    candidates = _victims(home)
    status = world.cli.json("status", expect=0) if candidates and _server_up(world) else {}
    pid = status.get("pid")
    before = [identity for identity in candidates if identity.pid == pid]
    return _receipt("server_loss", str(world.directory), before, _kill(before))


def _server_up(world):
    """Whether the project server answers; a project without one is simply not disturbed."""
    return world.cli.run("status").returncode == 0


def apply(value, world):
    """Apply one named disturbance to ``world`` and return its receipt."""
    if isinstance(value, TotalLoss):
        return _total_loss(value, world)
    if isinstance(value, ServerLoss):
        return _server_loss(value, world)
    raise TypeError(f"unknown disturbance: {value!r}")


# Gates --------------------------------------------------------------------------------------------------

@dataclass
class Gate:
    """One stage held for peers through ``controlled_gates.wait_gate``."""

    stage: str
    arguments: dict

    def entered(self):
        return Path(self.arguments[f"{self.stage}_entered"]).exists()

    def release(self):
        Path(self.arguments[f"{self.stage}_gate"]).touch()


def hold(world, stage):
    """Arm ``stage`` before its trigger; every peer that reaches it waits until the gate is released."""
    gates = Path(world.gates)
    gates.mkdir(parents=True, exist_ok=True)
    gate = Gate(stage, {f"{stage}_gate": str(gates / f"{stage}.gate"),
                        f"{stage}_entered": str(gates / f"{stage}.entered")})
    for leftover in (gate.arguments[f"{stage}_gate"], gate.arguments[f"{stage}_entered"]):
        Path(leftover).unlink(missing_ok=True)  # a re-armed stage holds again
    (gates / f"{stage}.armed").touch()
    world.scope.defer(f"release the {stage} gate", gate.release)
    return gate


# Waiting ------------------------------------------------------------------------------------------------

def _scale():
    try:
        return float(os.environ.get("XO_TEST_TIMEOUT_SCALE", "1"))
    except ValueError as error:
        raise ValueError("XO_TEST_TIMEOUT_SCALE must be a positive finite number") from error


def await_(world, label, predicate, timeout):
    """The shared classified wait; only its own expiry becomes ``WaitExpired`` naming ``label``.

    A predicate's own failure propagates unchanged, except a blocking call cut off at this wait's deadline,
    which is the wait's own expiry seen through a child process. While it runs, ``remaining(world)`` gives
    the seconds left.
    """
    clock = world.clock or SystemClock()
    budget = WaitBudget(timeout, "harness_allowance", scale=_scale())
    deadline = clock.monotonic() + budget.effective_timeout
    stack = _waits.setdefault(world, [])
    stack.append((clock, deadline))
    try:
        return wait_for(predicate, timeout, message=label, clock=clock, sleep=world.sleep, poll_interval=POLL_SECONDS)
    except WaitTimeout as expired:
        raise WaitExpired(label) from expired
    except subprocess.TimeoutExpired as expired:
        if expired.timeout is not None and expired.timeout in _handed.get(world, ()):
            raise WaitExpired(label) from expired  # a blocking call given this wait's remaining budget ran out
        raise
    finally:
        stack.pop()
        if not stack:
            _handed.pop(world, None)


def remaining(world):
    """The seconds left before the earliest deadline among the waits in progress, or None outside any wait."""
    stack = _waits.get(world)
    if not stack:
        return None
    clock = stack[-1][0]
    left = max(min(deadline for _, deadline in stack) - clock.monotonic(), 0.001)
    _handed.setdefault(world, set()).add(left)
    return left


# Teardown -----------------------------------------------------------------------------------------------

def reap(world):
    """Teardown: stop every process carrying the world's home and every process it spawned; report survivors."""
    _, survivors = _kill_until_quiet(os.fsencode(str(world.cli.home)), world.spawned)
    if survivors:
        raise AssertionError(f"the world's teardown left {len(survivors)} of its processes alive")


def settle(seconds):
    """A fixed pause kept from the original scenarios until an observed boundary replaces it."""
    time.sleep(seconds)
