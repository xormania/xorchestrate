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


def test_a_gate_still_held_at_exit_is_released_in_cleanup(tmp_path):
    gates = tmp_path / "gates"
    with World("detached-process", gates=gates) as world:
        gate = world.hold("hang")
        arguments = dict(gate.arguments)
        assert Path(arguments["hang_gate"]).parent == gates and not Path(arguments["hang_gate"]).exists()
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


# Gaps found by planted faults -------------------------------------------------------------------

def test_a_victim_that_survives_the_grace_fails_the_disturbance_naming_its_receipt(monkeypatch, sentinel):
    monkeypatch.setenv("XO_TEST_TIMEOUT_SCALE", "0.05")
    with World("detached-process") as world:
        running(world)
        with monkeypatch.context() as during:
            during.setattr(ownership(), "send_signal", lambda identity, number: None)  # the signal never lands
            with pytest.raises(AssertionError, match="total_loss"):
                world.disturb(contract().TotalLoss())
        assert world.processes(), "nothing should have died without a signal"
        assert alive(sentinel)


def test_the_remaining_budget_never_reaches_zero_at_the_deadline():
    clock = ControlledClock()
    seen = []

    def predicate():
        clock.advance(elapsed=31)
        seen.append(world.remaining())
        return True

    with World("detached-process", clock=clock, sleep=clock.sleep) as world:
        world.await_("past the deadline", predicate, timeout=30)
    assert seen and seen[0] > 0, seen


def test_there_is_no_remaining_budget_outside_an_await():
    clock = ControlledClock()
    with World("detached-process", clock=clock, sleep=clock.sleep) as world:
        assert world.remaining() is None
        world.await_("instant", lambda: True, timeout=5)
        assert world.remaining() is None, "a finished wait left its budget behind"


# Pre-draft review findings ----------------------------------------------------------------------

def outside_process(argv_suffix_path):
    """A same-user process outside the world (cwd elsewhere) whose arguments merely mention ``argv_suffix_path``."""
    outside = Path(track(tempfile.mkdtemp(prefix="xo-outside-")))
    process = subprocess.Popen([sys.executable, "-c", "import time, sys; time.sleep(120)", argv_suffix_path],
                               cwd=outside, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
    return process, outside


def end(process, outside):
    process.kill()
    process.wait(timeout=10)
    os.rmdir(outside)


@pytest.mark.parametrize("kind", ["TotalLoss", "ServerLoss", "teardown"])
def test_a_process_outside_the_world_that_only_names_its_directory_survives(kind, sentinel):
    with World("detached-process") as world:
        running(world)
        mentions = [outside_process(str(world.directory) + "/home/x.log"),
                    outside_process(str(world.directory) + "x")]
        try:
            identities = [next(item for pid, (item, _, _) in ownership().processes().items() if pid == process.pid)
                          for process, _ in mentions]
            if kind != "teardown":
                world.disturb(getattr(contract(), kind)())
                assert all(alive(item) for item in identities), "a process outside the world was killed"
        except BaseException:
            for pair in mentions:
                end(*pair)
            raise
    try:
        assert all(alive(item) for item in identities), "leaving the world killed a process outside it"
        assert alive(sentinel)
    finally:
        for pair in mentions:
            end(*pair)


def test_a_server_loss_after_the_server_is_gone_reports_no_victim(sentinel):
    with World("detached-process") as world:
        running(world)
        extra = sleeper(world)
        assert_applied(world.disturb(contract().ServerLoss()), "server_loss")
        receipt = world.disturb(contract().ServerLoss())
        assert receipt.outcome == "no_victim" and list(receipt.before) == [], receipt
        assert alive(extra) and alive(sentinel)


def test_a_blocking_call_cut_off_at_the_deadline_is_an_expired_wait(monkeypatch):
    project_server = importlib.import_module("tests.hexagonal.support.project_server")
    clock = ControlledClock()
    with World("detached-process", clock=clock, sleep=clock.sleep) as world:
        running(world)

        def cut_off(*args, **kwargs):
            raise subprocess.TimeoutExpired(args[0] if args else "command", kwargs.get("timeout"))

        with monkeypatch.context() as during:
            during.setattr(project_server.subprocess, "run", cut_off)
            with pytest.raises(contract().WaitExpired, match="status at the deadline"):
                world.await_("status at the deadline", lambda: world.status(), timeout=5)


def test_an_inner_await_never_gets_more_time_than_the_outer_one_has_left():
    clock = ControlledClock()
    seen = []
    with World("detached-process", clock=clock, sleep=clock.sleep) as world:
        def inner():
            seen.append(world.remaining())
            return True

        world.await_("outer", lambda: world.await_("inner", inner, timeout=60), timeout=2)
    assert seen and all(value <= 2 for value in seen), seen


def test_a_stage_can_be_held_again_after_release(sentinel):
    with World("detached-process") as world:
        first = world.hold("hang")
        done = world.directory / "first-done"
        gate_peer(world, first, "hang", done)
        world.await_("first peer held", first.entered)
        first.release()
        world.await_("first peer through", done.exists)
        second = world.hold("hang")
        assert not second.entered(), "a re-armed gate reports the earlier peer's entry"
        again = world.directory / "second-done"
        gate_peer(world, second, "hang", again)
        world.await_("second peer held", second.entered)
        time.sleep(0.3)
        assert not again.exists(), "a re-armed gate let its peer through"
        second.release()
        world.await_("second peer through", again.exists)
        assert alive(sentinel)


def test_total_loss_also_kills_a_world_process_that_appears_after_identification(monkeypatch, sentinel):
    real = ownership().send_signal
    late = []
    with World("detached-process") as world:
        running(world)

        def spawning_first(identity, number):
            if not late:
                late.append(sleeper(world))
            return real(identity, number)

        with monkeypatch.context() as during:
            during.setattr(ownership(), "send_signal", spawning_first)
            try:
                receipt = world.disturb(contract().TotalLoss())
            except AssertionError:
                receipt = None  # raising about it is also acceptable
        assert late, "the hook never ran"
        if receipt is not None:
            assert late[0] in receipt.before, "a world process born during the disturbance is missing from it"
            assert not alive(late[0]), "a world process born during the disturbance survived it"
        assert alive(sentinel)


def test_a_surviving_victim_fails_the_disturbance_before_its_sockets_are_removed(monkeypatch, sentinel):
    monkeypatch.setenv("XO_TEST_TIMEOUT_SCALE", "0.05")
    real = ownership().send_signal
    with World("detached-process") as world:
        running(world)
        spared = sleeper(world)
        sockets = sockets_under(world.directory)
        with monkeypatch.context() as during:
            during.setattr(ownership(), "send_signal",
                           lambda identity, number: None if identity == spared else real(identity, number))
            with pytest.raises(AssertionError, match=str(spared.pid)):
                world.disturb(contract().TotalLoss())
        assert sockets and sockets_under(world.directory) == sockets, "sockets were removed before the failure"
        assert alive(sentinel)


def test_leaving_a_world_reports_a_process_it_could_not_stop(monkeypatch, sentinel):
    monkeypatch.setenv("XO_TEST_TIMEOUT_SCALE", "0.05")
    real = ownership().send_signal
    spared = []
    try:
        with pytest.raises(AssertionError, match="left"):
            with World("detached-process") as world:
                spared.append(sleeper(world))
                monkeypatch.setattr(ownership(), "send_signal",
                                    lambda identity, number: None if identity in spared else real(identity, number))
    finally:
        monkeypatch.setattr(ownership(), "send_signal", real)
        for identity in spared:
            real(identity, 9)
    assert alive(sentinel)


# The selector: the world's own home in the environment, or what the world spawned --------------------

def started(argv, *, cwd, env):
    process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
    identity = ownership().processes()[process.pid][0]
    return process, identity


def stop(process):
    if process.poll() is None:
        process.kill()
    process.wait(timeout=10)


def without_home():
    return {name: value for name, value in os.environ.items() if name != "XORCHESTRATE_HOME"}


@pytest.mark.parametrize("home", ["none", "another"])
@pytest.mark.parametrize("kind", ["TotalLoss", "teardown"])
def test_a_process_inside_the_worlds_tree_without_its_home_survives(kind, home, sentinel):
    other = Path(track(tempfile.mkdtemp(prefix="xo-other-home-")))
    env = without_home() if home == "none" else {**without_home(), "XORCHESTRATE_HOME": str(other)}
    with World("detached-process") as world:
        running(world)
        inside = world.directory / "inside"
        inside.mkdir()
        process, identity = started([sys.executable, "-c", "import time; time.sleep(120)"], cwd=inside, env=env)
        try:
            if kind == "TotalLoss":
                receipt = world.disturb(contract().TotalLoss())
                assert identity not in receipt.before and alive(identity), "a process without the world's home was taken"
        except BaseException:
            stop(process)
            raise
    try:
        assert alive(identity), "leaving the world killed a process without its home"
        assert alive(sentinel)
    finally:
        stop(process)
        os.rmdir(other)


def test_a_process_carrying_the_worlds_home_is_taken_wherever_it_runs(sentinel):
    outside = Path(track(tempfile.mkdtemp(prefix="xo-outside-")))
    with World("detached-process") as world:
        running(world)
        env = {**without_home(), "XORCHESTRATE_HOME": str(world.cli.home)}
        process, identity = started([sys.executable, "-c", "import time; time.sleep(120)"], cwd=outside, env=env)
        try:
            receipt = world.disturb(contract().TotalLoss())
            assert identity in receipt.before and not alive(identity), "a process of the world's home was missed"
        finally:
            stop(process)
    os.rmdir(outside)
    assert alive(sentinel)


def test_a_world_whose_home_it_did_not_create_refuses_to_disturb(monkeypatch, sentinel):
    foreign = Path(track(tempfile.mkdtemp(prefix="xo-foreign-home-")))
    signalled = []
    with World("detached-process") as world:
        running(world)
        monkeypatch.setattr(world.cli, "home", foreign)
        with monkeypatch.context() as during:
            during.setattr(ownership(), "send_signal", lambda identity, number: signalled.append(identity))
            with pytest.raises((AssertionError, ValueError, RuntimeError)):
                world.disturb(contract().TotalLoss())
        assert signalled == [], "a world with a home it did not create signalled something"
        monkeypatch.undo()
    os.rmdir(foreign)
    assert alive(sentinel)


NOT_DUMPABLE = ("import ctypes, time; ctypes.CDLL(None).prctl(4, 0, 0, 0, 0);"  # PR_SET_DUMPABLE 0
                "open(__import__('sys').argv[1], 'w').write('ready'); time.sleep(120)")


def test_a_process_whose_environment_cannot_be_read_is_not_a_victim(tmp_path, sentinel):
    outside = Path(track(tempfile.mkdtemp(prefix="xo-outside-")))
    ready = tmp_path / "ready"
    with World("detached-process") as world:
        running(world)
        env = {**without_home(), "XORCHESTRATE_HOME": str(world.cli.home)}
        process, identity = started([sys.executable, "-c", NOT_DUMPABLE, str(ready)], cwd=outside, env=env)
        try:
            deadline = time.monotonic() + 10
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            try:
                Path(f"/proc/{process.pid}/environ").read_bytes()
                pytest.skip("this kernel lets the same user read a non-dumpable process's environment")
            except PermissionError:
                pass
            receipt = world.disturb(contract().TotalLoss())
            assert identity not in receipt.before and alive(identity), "an unreadable environment was taken"
        finally:
            stop(process)
    os.rmdir(outside)
    assert alive(sentinel)


# The selector: a listening Unix socket bound inside the world's home -----------------------------------

def terminal_host_servers(world):
    """Observation only: the multiplexer server processes placed on a socket under the world's home."""
    home = str(world.cli.home)
    return [pid for pid, command in importlib.import_module("tests.hexagonal.support.host_processes")
            .arguments_containing(".terminals.sock") if home in command and "tmux" in command]


def test_the_multiplexer_server_of_the_world_is_taken_on_total_loss(sentinel):
    with World("multiplexer") as world:
        running(world)
        servers = terminal_host_servers(world)
        assert servers, "the multiplexer world has no terminal host server to judge"
        table = ownership().processes()
        identities = [table[pid][0] for pid in servers if pid in table]
        receipt = world.disturb(contract().TotalLoss())
        assert set(identities) <= set(receipt.before), "the world's multiplexer server was not taken"
        assert not any(alive(item) for item in identities)
        assert alive(sentinel)


CONNECTED_CLIENT = ("import os, socket, time; s = socket.socket(socket.AF_UNIX); s.connect(os.environ['TARGET']);"
                    "open(os.environ['READY'], 'w').write('ready'); time.sleep(120)")
FILE_HOLDER = ("import os, time; f = open(os.environ['TARGET']); open(os.environ['READY'], 'w').write('ready');"
               "time.sleep(120)")
LISTENER = ("import os, socket, time; s = socket.socket(socket.AF_UNIX); s.bind(os.environ['TARGET']); s.listen();"
            "open(os.environ['READY'], 'w').write('ready'); time.sleep(120)")


def ready_process(program, target, tmp_path, name):
    """A process (no world home in its environment, cwd outside the world) acting on ``target``, once ready."""
    outside = Path(track(tempfile.mkdtemp(prefix="xo-outside-")))
    ready = tmp_path / f"{name}.ready"
    env = {**without_home(), "TARGET": str(target), "READY": str(ready)}
    process, identity = started([sys.executable, "-c", program], cwd=outside, env=env)
    deadline = time.monotonic() + 10
    while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert ready.exists(), f"the {name} process did not get ready"
    return process, identity, outside


def server_socket(world):
    found = [path for path in Path(world.cli.home).rglob("*.sock")
             if ".terminals" not in path.name and stat.S_ISSOCK(path.lstat().st_mode)]
    assert found, "the running world has no server socket in its home"
    return found[0]


@pytest.mark.parametrize("kind", ["TotalLoss", "ServerLoss", "teardown"])
@pytest.mark.parametrize("holder", ["connected-client", "open-file"])
def test_a_process_that_only_uses_something_inside_the_home_survives(holder, kind, tmp_path, sentinel):
    with World("detached-process") as world:
        running(world)
        if holder == "connected-client":
            process, identity, outside = ready_process(CONNECTED_CLIENT, server_socket(world), tmp_path, holder)
        else:
            held = Path(world.cli.home) / "held.txt"
            held.write_text("held\n")
            process, identity, outside = ready_process(FILE_HOLDER, held, tmp_path, holder)
        try:
            if kind != "teardown":
                receipt = world.disturb(getattr(contract(), kind)())
                assert identity not in receipt.before and alive(identity), f"a {holder} was taken"
        except BaseException:
            stop(process)
            raise
    try:
        assert alive(identity), f"leaving the world killed a {holder}"
        assert alive(sentinel)
    finally:
        stop(process)
        os.rmdir(outside)


def test_a_process_listening_on_a_socket_bound_inside_the_home_is_taken(tmp_path, sentinel):
    with World("detached-process") as world:
        running(world)
        process, identity, outside = ready_process(LISTENER, Path(world.cli.home) / "listener.sock", tmp_path,
                                                   "listener")
        try:
            receipt = world.disturb(contract().TotalLoss())
            assert identity in receipt.before and not alive(identity), "a listener bound inside the home was missed"
        finally:
            stop(process)
            os.rmdir(outside)
    assert alive(sentinel)


def test_a_listener_bound_beside_the_home_on_a_shared_prefix_survives(tmp_path, sentinel):
    with World("detached-process") as world:
        running(world)
        beside = Path(str(world.cli.home) + "x.sock")
        process, identity, outside = ready_process(LISTENER, beside, tmp_path, "beside")
        try:
            receipt = world.disturb(contract().TotalLoss())
            assert identity not in receipt.before and alive(identity), "a listener outside the home was taken"
        finally:
            stop(process)
            os.rmdir(outside)
            beside.unlink(missing_ok=True)
    assert alive(sentinel)


# Which signal selected each victim ----------------------------------------------------------------------

def selection(receipt):
    return dict(getattr(receipt, "selected", {}) or {})


def test_a_total_loss_records_which_signal_selected_each_victim(tmp_path, sentinel):
    with World("detached-process") as world:
        running(world)
        spawned = sleeper(world)
        listener, listening, outside = ready_process(LISTENER, Path(world.cli.home) / "listener.sock", tmp_path,
                                                     "listener")
        carrier, carrying = started([sys.executable, "-c", "import time; time.sleep(120)"], cwd=outside,
                                    env={**without_home(), "XORCHESTRATE_HOME": str(world.cli.home)})
        try:
            receipt = world.disturb(contract().TotalLoss())
            chosen = selection(receipt)
            assert set(chosen) == set(receipt.before), (chosen, receipt.before)
            assert set(chosen.values()) <= contract().SELECTION_SIGNALS, chosen
            assert (chosen.get(spawned), chosen.get(listening), chosen.get(carrying)) == ("spawned", "socket", "home"), \
                chosen
        finally:
            stop(listener)
            stop(carrier)
            os.rmdir(outside)
    assert alive(sentinel)


def test_the_multiplexer_server_is_selected_by_its_socket(sentinel):
    with World("multiplexer") as world:
        running(world)
        table = ownership().processes()
        servers = [table[pid][0] for pid in terminal_host_servers(world) if pid in table]
        assert servers
        chosen = selection(world.disturb(contract().TotalLoss()))
        assert all(chosen.get(item) == "socket" for item in servers), chosen
        assert alive(sentinel)


def test_a_server_loss_records_its_victims_selection_and_no_victim_records_none(sentinel):
    with World("detached-process") as world:
        running(world)
        receipt = world.disturb(contract().ServerLoss())
        chosen = selection(receipt)
        assert set(chosen) == set(receipt.before) and set(chosen.values()) <= {"home", "socket"}, chosen
        empty = world.disturb(contract().ServerLoss())
        assert empty.outcome == "no_victim" and selection(empty) == {}, empty
        assert alive(sentinel)


# Gaps found by planted faults in the selector ---------------------------------------------------------

@pytest.mark.parametrize("variant", ["sibling", "nested", "embedded"])
def test_a_home_that_only_resembles_the_worlds_is_not_a_victim(variant, sentinel):
    outside = Path(track(tempfile.mkdtemp(prefix="xo-outside-")))
    with World("detached-process") as world:
        running(world)
        home = str(world.cli.home)
        lookalike = {"sibling": home + "x", "nested": home + "/nested", "embedded": "/prefix" + home}[variant]
        process, identity = started([sys.executable, "-c", "import time; time.sleep(120)"], cwd=outside,
                                    env={**without_home(), "XORCHESTRATE_HOME": lookalike})
        try:
            receipt = world.disturb(contract().TotalLoss())
            assert identity not in receipt.before and alive(identity), f"a {variant} look-alike home was taken"
        finally:
            stop(process)
    os.rmdir(outside)
    assert alive(sentinel)


BOUND_ONLY = ("import os, socket, time; s = socket.socket(socket.AF_UNIX); s.bind(os.environ['TARGET']);"
              "open(os.environ['READY'], 'w').write('ready'); time.sleep(120)")


def test_a_socket_bound_inside_the_home_but_not_listening_does_not_select(tmp_path, sentinel):
    with World("detached-process") as world:
        running(world)
        process, identity, outside = ready_process(BOUND_ONLY, Path(world.cli.home) / "bound.sock", tmp_path, "bound")
        try:
            receipt = world.disturb(contract().TotalLoss())
            assert identity not in receipt.before and alive(identity), "a bound, non-listening socket selected"
        finally:
            stop(process)
            os.rmdir(outside)
    assert alive(sentinel)


def test_a_server_loss_without_a_server_reports_no_victim_while_other_world_processes_run(sentinel):
    outside = Path(track(tempfile.mkdtemp(prefix="xo-outside-")))
    with World("detached-process") as world:
        running(world)
        process, carrier = started([sys.executable, "-c", "import time; time.sleep(120)"], cwd=outside,
                                   env={**without_home(), "XORCHESTRATE_HOME": str(world.cli.home)})
        try:
            assert_applied(world.disturb(contract().ServerLoss()), "server_loss")
            receipt = world.disturb(contract().ServerLoss())
            assert receipt.outcome == "no_victim" and alive(carrier), receipt
        finally:
            stop(process)
    os.rmdir(outside)
    assert alive(sentinel)


def test_a_timeout_the_wait_never_handed_out_propagates_unchanged():
    clock = ControlledClock()

    def predicate():
        raise subprocess.TimeoutExpired("an unrelated command", 99)

    with World("detached-process", clock=clock, sleep=clock.sleep) as world:
        with pytest.raises(subprocess.TimeoutExpired) as raised:
            world.await_("unrelated timeout", predicate, timeout=30)
    assert not isinstance(raised.value, contract().WaitExpired)


# The selector: descendants of selected processes --------------------------------------------------------

FORKING_PARENT = ("import subprocess, sys, time;"
                  "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'], cwd=sys.argv[1]);"
                  "open(sys.argv[2], 'w').write(str(child.pid)); time.sleep(120)")
DOUBLE_FORK = ("import os, sys, time\n"
               "if os.fork() == 0:\n"
               "    os.setsid()\n"
               "    if os.fork() == 0:\n"
               "        os.chdir(sys.argv[1]); open(sys.argv[2], 'w').write(str(os.getpid())); time.sleep(120)\n"
               "    os._exit(0)\n"
               "time.sleep(120)\n")


def pid_from(path, timeout=10):
    deadline = time.monotonic() + timeout
    while not (path.exists() and path.read_text().strip()) and time.monotonic() < deadline:
        time.sleep(0.05)
    return int(path.read_text())


def test_a_child_of_a_selected_process_is_taken_as_its_descendant(tmp_path, sentinel):
    outside = Path(track(tempfile.mkdtemp(prefix="xo-outside-")))
    with World("detached-process") as world:
        running(world)
        parent = world.spawn([sys.executable, "-c", FORKING_PARENT, str(outside), str(tmp_path / "child.pid")])
        child_pid = pid_from(tmp_path / "child.pid")
        child = ownership().processes()[child_pid][0]
        try:
            receipt = world.disturb(contract().TotalLoss())
            assert child in receipt.before and not alive(child), "a child of a world process was missed"
            assert selection(receipt).get(child) == "descendant", selection(receipt)
            assert dict(getattr(receipt, "ancestors", {}) or {}).get(child) == parent, receipt
        finally:
            ownership().send_signal(child, 9)
    os.rmdir(outside)
    assert alive(sentinel), "a child of the test runner that the world did not spawn was taken"


def test_a_re_parented_process_left_in_the_world_is_reported_as_leaked_and_left_alone(tmp_path, sentinel):
    with World("detached-process") as world:
        running(world)
        hideout = world.directory / "hideout"
        hideout.mkdir()
        world.spawn([sys.executable, "-c", DOUBLE_FORK, str(hideout), str(tmp_path / "orphan.pid")])
        orphan_pid = pid_from(tmp_path / "orphan.pid")
        orphan = ownership().processes()[orphan_pid][0]
        try:
            receipt = world.disturb(contract().TotalLoss())
            leaked = list(getattr(receipt, "leaked", []) or [])
            assert orphan not in receipt.before, "an unselected re-parented process was killed"
            assert orphan in leaked and alive(orphan), receipt
        finally:
            ownership().send_signal(orphan, 9)
    assert alive(sentinel)
