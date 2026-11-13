"""Named disturbances for end-to-end worlds, implemented once.

Slice 1 moves the recovery scenarios' existing failure mechanics here unchanged: a total loss kills every process of
the project at once and removes its sockets, keeping the durable home; a server loss kills only the project server.
Each returns a ``Receipt`` naming what it did. Receipts with verified identities, gates and sentinels follow in the
next slice; they extend this module rather than adding another.
"""
import os
import signal
import stat
import time

from scripts.testkit.waits import WaitTimeout
from tests.hexagonal.contracts.e2e_world import Receipt, ServerLoss, TotalLoss, WaitExpired
from tests.hexagonal.support.host_processes import processes_under
from tests.hexagonal.support.native import wait_for


def _kill_all(directory):
    for pid, _ in processes_under([directory]):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def reap(directory):
    """Teardown: nothing of the project outlives its world."""
    _kill_all(directory)


def settle(seconds):
    """A fixed pause kept from the original scenarios until the next slice replaces it with an observed boundary."""
    time.sleep(seconds)


def _remove_sockets(directory):
    removed = []
    for root, _, files in os.walk(directory):
        for name in files:
            path = os.path.join(root, name)
            if stat.S_ISSOCK(os.lstat(path).st_mode):
                os.unlink(path)
                removed.append(path)
    return removed


def _total_loss(value, world):
    directory = world.cli.directory
    before = [pid for pid, _ in processes_under([directory])]
    _kill_all(directory)
    deadline = time.monotonic() + 10
    while processes_under([directory]) and time.monotonic() < deadline:
        time.sleep(0.05)
    left = processes_under([directory])
    assert left == [], "the simulated failure left processes"
    removed = [] if value.keep_sockets else _remove_sockets(directory)
    return Receipt(kind="total_loss", target=str(directory), before=tuple(before),
                   effect={"left": len(left), "sockets_removed": len(removed)},
                   outcome="applied" if before else "no_victim")


def _server_loss(value, world):
    pid = world.status()["pid"]
    os.kill(pid, signal.SIGKILL)
    return Receipt(kind="server_loss", target="server", before=(pid,), effect={"signal": "SIGKILL"},
                   outcome="applied")


def apply(value, world):
    """Apply one named disturbance to ``world`` and return its receipt."""
    if isinstance(value, TotalLoss):
        return _total_loss(value, world)
    if isinstance(value, ServerLoss):
        return _server_loss(value, world)
    raise TypeError(f"unknown disturbance: {value!r}")


def await_(world, label, predicate, timeout):
    """The shared classified wait; only its own expiry becomes ``WaitExpired`` naming ``label``.

    A predicate's own failure propagates unchanged.
    """
    try:
        return wait_for(predicate, timeout, message=label, clock=world.clock, sleep=world.sleep)
    except WaitTimeout as expired:
        raise WaitExpired(label) from expired


def remaining(world):
    """The seconds left of the wait in progress; the next slice ties this to the active wait budget."""
    return None
