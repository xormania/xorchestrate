"""Named disturbances, stage gates and bounded waits for end-to-end worlds, implemented once.

Every kill acts only on the world's own processes: those placed under its private directory, identified
(pid and start time) before any signal and signalled through the test kit's fenced ``send_signal``, so a
reused pid or an unrelated process is never hit. Each disturbance returns a ``Receipt`` naming the
identities it found (``before``) and those still alive afterwards (``effect``).

Gates hold a peer at a named stage through the shared process-side ``controlled_gates.wait_gate``; a gate
still held when the world ends is released by the world's cleanup scope. Waits use the shared classified
wait budget; while one runs, ``remaining`` gives the seconds left so the world's blocking calls can use
them as their timeout.
"""
import os
import signal
import stat
import time
import weakref
from dataclasses import dataclass
from pathlib import Path

from scripts.testkit.adapters import ownership
from scripts.testkit.adapters.clock import SystemClock
from scripts.testkit.core import ResourceScope
from scripts.testkit.waits import WaitBudget, WaitTimeout
from tests.hexagonal.contracts.e2e_world import Receipt, ServerLoss, TotalLoss, WaitExpired
from tests.hexagonal.support.host_processes import processes_under
from tests.hexagonal.support.native import wait_for

POLL_SECONDS = .25
"""Pause between observations of an await: an observation may start a CLI process, so not faster."""
SIGNAL_GRACE = 10
"""Seconds a killed process may take to disappear before the disturbance counts as incomplete."""

_scopes = {}
"""The cleanup scope of each live world, by its directory; ``reap`` closes it."""
_waits = weakref.WeakKeyDictionary()
"""The deadlines of the awaits in progress, innermost last, by world."""


# Identities and fenced signals ------------------------------------------------------------------------

def _identities(directory):
    """Identities of the live processes under ``directory``, taken before anything is signalled."""
    table = ownership.processes()
    return [table[pid][0] for pid, _ in processes_under([directory]) if pid in table]


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


def _receipt(kind, target, before, effect):
    receipt = Receipt(kind=kind, target=target, before=list(before), effect=list(effect),
                      outcome="applied" if before else "no_victim")
    if effect:
        raise AssertionError(f"the disturbance left processes alive: {receipt}")
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
    directory = world.directory
    before = _identities(directory)
    effect = _kill(before)
    if not value.keep_sockets:
        _remove_sockets(directory)
    return _receipt("total_loss", str(directory), before, effect)


def _server_loss(value, world):
    directory = world.directory
    candidates = _identities(directory)
    if not candidates:
        return _receipt("server_loss", str(directory), [], [])
    pid = world.status().get("pid")
    before = [identity for identity in candidates if identity.pid == pid]
    return _receipt("server_loss", str(directory), before, _kill(before))


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


def _scope(world):
    return _scopes.setdefault(str(world.directory), ResourceScope())


def hold(world, stage):
    """Arm ``stage`` before its trigger; every peer that reaches it waits until the gate is released."""
    gates = Path(world.gates)
    gates.mkdir(parents=True, exist_ok=True)
    gate = Gate(stage, {f"{stage}_gate": str(gates / f"{stage}.gate"),
                        f"{stage}_entered": str(gates / f"{stage}.entered")})
    (gates / f"{stage}.armed").touch()
    _scope(world).defer(f"release the {stage} gate", gate.release)
    return gate


# Waiting ------------------------------------------------------------------------------------------------

def _scale():
    try:
        return float(os.environ.get("XO_TEST_TIMEOUT_SCALE", "1"))
    except ValueError as error:
        raise ValueError("XO_TEST_TIMEOUT_SCALE must be a positive finite number") from error


def await_(world, label, predicate, timeout):
    """The shared classified wait; only its own expiry becomes ``WaitExpired`` naming ``label``.

    A predicate's own failure propagates unchanged. While it runs, ``remaining(world)`` gives the
    seconds left of this wait.
    """
    clock = world.clock or SystemClock()
    budget = WaitBudget(timeout, "harness_allowance", scale=_scale())
    stack = _waits.setdefault(world, [])
    stack.append((clock, clock.monotonic() + budget.effective_timeout))
    try:
        return wait_for(predicate, timeout, message=label, clock=clock, sleep=world.sleep, poll_interval=POLL_SECONDS)
    except WaitTimeout as expired:
        raise WaitExpired(label) from expired
    finally:
        stack.pop()


def remaining(world):
    """The seconds left of the innermost wait in progress, or None outside any wait."""
    stack = _waits.get(world)
    if not stack:
        return None
    clock, deadline = stack[-1]
    return max(deadline - clock.monotonic(), 0.001)


# Teardown -----------------------------------------------------------------------------------------------

def reap(directory):
    """Teardown: release the world's held gates, then stop every process left under its directory."""
    scope = _scopes.pop(str(directory), None)
    if scope is not None:
        scope.close()
    _kill(_identities(directory))


def settle(seconds):
    """A fixed pause kept from the original scenarios until an observed boundary replaces it."""
    time.sleep(seconds)
