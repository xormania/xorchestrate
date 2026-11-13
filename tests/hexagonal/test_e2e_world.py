"""The end-to-end world's own machinery: fenced disturbances with receipts, stage gates, bounded waits, isolation.

A world runs the real project server in a private short directory on a real terminal
host (the detached-process host, and the multiplexer where it is installed). Its
disturbances kill real processes, so every test proves the kill stays inside the world:
an unrelated sentinel started by the test itself, outside the world, always survives.

The world and the contract are imported when a test asks for them, so a missing module
fails that test instead of breaking collection.
"""

import importlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from tests.hexagonal.support.host_processes import track
from tests.hexagonal.support.temporal_waits import ControlledClock

ROOT = Path(__file__).resolve().parents[2]
HOSTS = ("detached-process", "multiplexer")


def support():
    return importlib.import_module("tests.hexagonal.support.e2e_world")


def contract():
    return importlib.import_module("tests.hexagonal.contracts.e2e_world")


def ownership():
    return importlib.import_module("scripts.testkit.adapters.ownership")


def World(host, **kwargs):
    """``support.world(host)``; it skips the test itself (never substitutes a host) when the host cannot run here."""
    return support().world(host, **kwargs)


@pytest.fixture
def sentinel():
    """An unrelated process this test starts itself, outside any world; it must outlive every disturbance."""
    outside = Path(track(tempfile.mkdtemp(prefix="xo-sentinel-")))
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], cwd=outside,
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    identity = next(item for pid, (item, _, _) in ownership().processes().items() if pid == process.pid)
    try:
        yield identity
    finally:
        process.kill()
        process.wait(timeout=10)
        os.rmdir(outside)


def alive(identity):
    return ownership().alive(identity)


def sockets_under(directory):
    found = []
    for root, _, files in os.walk(directory):
        for name in files:
            path = os.path.join(root, name)
            try:
                if stat.S_ISSOCK(os.lstat(path).st_mode):
                    found.append(path)
            except OSError:
                continue
    return sorted(found)


def running(world):
    world.save()
    world.up()
    assert world.processes(), "the world's server is not running"
    return world


def sleeper(world):
    """An extra owned process inside the world."""
    return world.spawn([sys.executable, "-c", "import time; time.sleep(120)"])


def assert_applied(receipt, kind):
    assert receipt.kind == kind and receipt.outcome == "applied", receipt
    assert receipt.before and all(hasattr(item, "pid") and hasattr(item, "started") for item in receipt.before), receipt
    assert receipt.effect == [], receipt
    assert not any(alive(item) for item in receipt.before), "a victim the receipt names is still alive"


# Disturbances ------------------------------------------------------------------------------------

@pytest.mark.parametrize("host", HOSTS)
def test_total_loss_kills_every_process_of_the_world_and_records_them_first(host, sentinel):
    with World(host) as world:
        running(world)
        extra = sleeper(world)
        before = {pid for pid, _ in world.processes()}
        receipt = world.disturb(contract().TotalLoss())
        assert_applied(receipt, "total_loss")
        assert receipt.target == str(world.directory), receipt
        assert extra in receipt.before and before <= {item.pid for item in receipt.before}, receipt
        assert world.processes() == [], "the world still has processes"
        assert alive(sentinel), "an unrelated process outside the world was killed"


@pytest.mark.parametrize("keep", [False, True], ids=["sockets-removed", "sockets-kept"])
def test_total_loss_removes_or_keeps_the_worlds_socket_files(keep, sentinel):
    with World("detached-process") as world:
        running(world)
        present = sockets_under(world.directory)
        assert present, "the running world has no socket files"
        receipt = world.disturb(contract().TotalLoss(keep_sockets=keep))
        assert_applied(receipt, "total_loss")
        assert (sockets_under(world.directory) == present) if keep else (sockets_under(world.directory) == [])
        assert alive(sentinel)


@pytest.mark.parametrize("host", HOSTS)
def test_server_loss_kills_only_the_server(host, sentinel):
    with World(host) as world:
        running(world)
        extra = sleeper(world)
        receipt = world.disturb(contract().ServerLoss())
        assert_applied(receipt, "server_loss")
        assert len(receipt.before) == 1 and extra not in receipt.before, receipt
        assert alive(extra), "server-only loss killed another process of the world"
        assert alive(sentinel)


@pytest.mark.parametrize("kind", ["TotalLoss", "ServerLoss"])
def test_a_world_with_nothing_to_kill_reports_no_victim(kind, sentinel):
    with World("detached-process") as world:
        receipt = world.disturb(getattr(contract(), kind)())
        assert receipt.outcome == "no_victim" and list(receipt.before) == [] and receipt.effect == [], receipt
        assert alive(sentinel)


def test_two_worlds_side_by_side_never_touch_each_others_processes(sentinel):
    with World("detached-process") as first, World("detached-process") as second:
        running(first)
        running(second)
        theirs = [identity for identity in ownership().processes().values()
                  if identity[0].pid in {pid for pid, _ in second.processes()}]
        second_identities = [identity for identity, _, _ in theirs]
        receipt = first.disturb(contract().TotalLoss())
        assert_applied(receipt, "total_loss")
        assert not set(receipt.before) & set(second_identities), "a receipt names the other world's processes"
        assert second_identities and all(alive(item) for item in second_identities), \
            "a disturbance in one world killed a process of another"
        assert alive(sentinel)


# Gates -------------------------------------------------------------------------------------------

def gate_peer(world, gate, stage, done):
    """A peer inside the world that holds at ``stage`` through the shared process-side gate, then marks ``done``."""
    program = ("import json, sys; sys.path.insert(0, sys.argv[1]);"
               "from tests.hexagonal.fixtures.controlled_gates import wait_gate;"
               "wait_gate(json.loads(sys.argv[2]), sys.argv[3]); open(sys.argv[4], 'w').write('done')")
    return world.spawn([sys.executable, "-c", program, str(ROOT), json.dumps(gate.arguments), stage, str(done)])


def test_a_gate_holds_its_peer_until_released(sentinel):
    with World("detached-process") as world:
        gate = world.hold("hang")
        done = world.directory / "peer-done"
        assert not gate.entered(), "a gate reports entry before any peer reached it"
        gate_peer(world, gate, "hang", done)
        world.await_("peer reaches the gate", lambda: gate.entered())
        time.sleep(0.3)
        assert not done.exists(), "the peer passed a held gate"
        gate.release()
        world.await_("peer continues after release", lambda: done.exists())
        assert alive(sentinel)


def test_a_gate_still_held_at_exit_is_released_in_cleanup():
    with World("detached-process") as world:
        gate = world.hold("hang")
        arguments = dict(gate.arguments)
    assert Path(arguments["hang_gate"]).exists(), "a held gate was left closed when the world ended"


# Waiting -----------------------------------------------------------------------------------------

def test_await_fails_at_its_deadline_naming_its_label_on_the_injected_clock():
    clock = ControlledClock()
    with World("detached-process", clock=clock, sleep=clock.sleep) as world:
        started = time.monotonic()
        with pytest.raises(contract().WaitExpired, match="the impossible condition") as expired:
            world.await_("the impossible condition", lambda: False, timeout=30)
        assert isinstance(expired.value, AssertionError)
        assert time.monotonic() - started < 5, "the wait used the wall clock instead of the injected one"
        assert clock.elapsed >= 30, clock.elapsed


def test_the_remaining_budget_shrinks_on_the_injected_clock_during_an_await():
    clock = ControlledClock()
    seen = []

    def predicate():
        seen.append(world.remaining())
        return len(seen) >= 3

    with World("detached-process", clock=clock, sleep=clock.sleep) as world:
        assert world.await_("budget", predicate, timeout=30)
    assert seen[0] <= 30 and all(later < earlier for earlier, later in zip(seen, seen[1:])), seen
    assert all(value > 0 for value in seen), seen


def test_a_blocking_call_inside_an_await_gets_the_remaining_budget_as_its_timeout(monkeypatch):
    project_server = importlib.import_module("tests.hexagonal.support.project_server")
    real = project_server.subprocess.run
    timeouts = []

    def recording(*args, **kwargs):
        timeouts.append(kwargs.get("timeout"))
        return real(*args, **kwargs)

    clock = ControlledClock()
    with World("detached-process", clock=clock, sleep=clock.sleep) as world:
        running(world)
        monkeypatch.setattr(project_server.subprocess, "run", recording)
        budgets = []

        def predicate():
            budgets.append(world.remaining())
            world.status()
            clock.advance(elapsed=7)
            return len(budgets) >= 2

        world.await_("status twice", predicate, timeout=30)
        monkeypatch.setattr(project_server.subprocess, "run", real)
    assert len(timeouts) >= 2 and timeouts[:2] == budgets[:2], (timeouts, budgets)
    assert budgets[1] < budgets[0] <= 30, budgets


def test_a_predicates_own_error_propagates_unchanged():
    clock = ControlledClock()

    class Broken(Exception):
        pass

    def predicate():
        raise Broken("the observation itself failed")

    with World("detached-process", clock=clock, sleep=clock.sleep) as world:
        with pytest.raises(Broken, match="the observation itself failed"):
            world.await_("broken observation", predicate, timeout=30)


def test_await_returns_the_first_truthy_observation():
    clock = ControlledClock()
    with World("detached-process", clock=clock, sleep=clock.sleep) as world:
        assert world.await_("value", lambda: {"ready": True}, timeout=5) == {"ready": True}


# Cleanup -----------------------------------------------------------------------------------------

def test_leaving_a_world_stops_everything_it_started_and_nothing_else(sentinel):
    with World("detached-process") as world:
        running(world)
        extra = sleeper(world)
        directory = world.directory
    assert not alive(extra), "a process the world started outlived it"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and support_processes_under(directory):
        time.sleep(0.1)
    assert support_processes_under(directory) == [], "the world left processes behind"
    assert alive(sentinel)


def support_processes_under(directory):
    return importlib.import_module("tests.hexagonal.support.host_processes").processes_under([directory])


def test_a_disturbance_never_signals_outside_the_world(monkeypatch, sentinel):
    signalled = []
    real = ownership().send_signal

    def recording(identity, number):
        signalled.append(identity)
        return real(identity, number)

    with World("detached-process") as world:
        running(world)
        with monkeypatch.context() as during:
            during.setattr(ownership(), "send_signal", recording)
            during.setattr(os, "kill", lambda pid, number: pytest.fail(f"raw os.kill({pid}, {number}) is unfenced"))
            during.setattr(os, "killpg", lambda pgid, number: pytest.fail(f"os.killpg({pgid}, {number}) is unfenced"))
            receipt = world.disturb(contract().TotalLoss())
        assert_applied(receipt, "total_loss")
        assert signalled and set(signalled) <= set(receipt.before), "a signal went to a process the receipt omits"
        assert sentinel not in signalled


# The recovery decision as an observed boundary ----------------------------------------------------

def recovery_decided(world):
    """The server's startup recovery decision is recorded and nothing is running for it (manual policy)."""
    recovery = world.status().get("recovery") or {}
    detected = recovery.get("detected")
    return detected if detected is not None and not recovery.get("running") else None


def test_after_a_total_loss_a_manual_world_awaits_the_recovery_decision_without_a_fixed_pause(sentinel):
    clock = ControlledClock()
    with World("detached-process", clock=clock, sleep=clock.sleep) as world:
        world.save(policy="manual")
        world.up()
        world.disturb(contract().TotalLoss())
        world.up()
        detected = world.await_("recovery decided", lambda: recovery_decided(world), timeout=30)
        assert detected["unclean_stop"] is True and detected["selected"] == [], detected
        assert sum(clock.sleeps) < 1, f"the boundary was reached by waiting, not observed: {clock.sleeps}"
        assert alive(sentinel)
