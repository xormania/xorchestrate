"""Nested blockers receive elapsed remainder; independent watchdogs bound old defects."""

import importlib
import json
import os
from types import SimpleNamespace

import pytest

from scripts.acceptance.scenario import AcceptanceFailure
from scripts.testkit.adapters import command
from scripts.testkit.model import Cancelled
from scripts.verification import lifecycle
from scripts.verification.checker import VerificationFailure
from tests.hexagonal.support.deadline_boundaries import (
    CostedHost,
    PhaseChild,
    PhaseGroup,
    QuietTracker,
    guarded_call,
    partial_host,
    responding_client,
    saturated_client,
)
from tests.hexagonal.support.temporal_waits import ControlledClock


def test_command_cancellation_spends_signal_cost_inside_grace(tmp_path, monkeypatch):
    clock = ControlledClock()
    child, group = PhaseChild(clock), PhaseGroup(clock, signal_cost=.4)
    reasons = iter([None, "operator-stop"])
    monkeypatch.setattr(command.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(command.time, "sleep", clock.sleep)
    monkeypatch.setattr(command.subprocess, "Popen", lambda *args, **kwargs: child)
    monkeypatch.setattr(command, "_OwnedGroup", lambda child: group)
    with pytest.raises(Cancelled) as caught:
        command.SubprocessRunner(cancellation=lambda: next(reasons), shutdown_grace=1).run(
            ("controlled-command",), cwd=tmp_path, env={}, timeout=10)
    assert child.timeouts == [("drain", pytest.approx(.6))], "CANCELLATION_GRACE_INCLUDES_SIGNAL_COST"
    assert caught.value.reason == "operator-stop"
    assert caught.value.shutdown["verified"] is True
    assert caught.value.raw_stdout == b"retained"


def test_forced_drain_does_not_renew_the_cleanup_allowance(monkeypatch):
    clock = ControlledClock()
    child = PhaseChild(clock, drain_expires=True)
    monkeypatch.setattr(command.time, "monotonic", clock.monotonic)
    assert command._finish(child) == (b"partial", b"")
    assert child.timeouts == [("drain", command.CLEANUP_TIMEOUT), ("reap", 0)], "CLEANUP_REAP_USES_REMAINDER"


def test_acceptance_request_deadline_bounds_a_saturated_pipe():
    with saturated_client() as (client, release):
        completed, outcome = guarded_call(lambda: client.write({"method": "blocked"}), release)
    assert completed, "ACCEPTANCE_REQUEST_WRITE_MUST_OBSERVE_DEADLINE"
    assert isinstance(outcome, AcceptanceFailure) and outcome.code == "request_deadline"


def test_lifecycle_host_deadline_bounds_an_incomplete_readiness_frame(tmp_path, monkeypatch):
    deferred = []
    resources = SimpleNamespace(defer=lambda name, close: deferred.append((name, close)))
    with partial_host() as (process, release):
        monkeypatch.setattr(lifecycle.subprocess, "Popen", lambda *args, **kwargs: process)
        completed, outcome = guarded_call(
            lambda: lifecycle._host(tmp_path / "config", "peer", "endpoint", tmp_path, .05, resources), release)
    for name, close in deferred:
        if name != "external_host":
            close()
    assert completed, "HOST_READINESS_PARTIAL_FRAME_MUST_OBSERVE_DEADLINE"
    assert isinstance(outcome, VerificationFailure) and outcome.assertion == "external_host_start_failed"


def test_nested_world_polling_inherits_the_earlier_outer_deadline(tmp_path, monkeypatch):
    from tests.hexagonal.contracts.e2e_world import WaitExpired
    from tests.hexagonal.support.e2e_world import World

    monkeypatch.setenv("XO_TEST_TIMEOUT_SCALE", "1")
    clock = ControlledClock()
    world = World(SimpleNamespace(), "detached-process", gates=tmp_path, clock=clock, sleep=clock.sleep)
    polls = []

    def inner():
        polls.append(clock.elapsed)
        return False

    with pytest.raises(WaitExpired):
        world.await_("outer", lambda: world.await_("inner", inner, timeout=1), timeout=.05)
    assert clock.elapsed == pytest.approx(.05), "NESTED_WAIT_MUST_NOT_RESTART_THE_PARENT_BUDGET"
    assert polls and all(started < .05 for started in polls)
    assert world.remaining() is None


def test_world_refuses_a_new_cli_call_after_its_wait_budget_is_spent(tmp_path, monkeypatch):
    from tests.hexagonal.contracts.e2e_world import WaitExpired
    from tests.hexagonal.support.e2e_world import World

    monkeypatch.setenv("XO_TEST_TIMEOUT_SCALE", "1")
    clock = ControlledClock()
    launches = []
    cli = SimpleNamespace(json=lambda *args, **kwargs: launches.append((args, kwargs)) or True)
    world = World(cli, "detached-process", gates=tmp_path, clock=clock, sleep=clock.sleep)

    def spent():
        clock.advance(elapsed=.1)
        return world._json("status")

    expired = False
    try:
        world.await_("spent", spent, timeout=.05)
    except WaitExpired:
        expired = True
    assert not launches, "EXHAUSTED_WAIT_MUST_NOT_LAUNCH_ANOTHER_COMMAND"
    assert expired


def test_acceptance_pipe_exchange_keeps_request_and_buffered_reply_boundaries():
    responses = b'{"id":1,"result":{"first":true}}\n{"id":2,"result":{"second":true}}\n'
    with responding_client(responses) as (client, reader):
        assert client.call("first", {"one": 1}) == {"first": True}
        assert client.call("second", {"two": 2}) == {"second": True}
        requests = [json.loads(row) for row in os.read(reader, 65536).splitlines()]
    assert requests == [{"jsonrpc": "2.0", "id": 1, "method": "first", "params": {"one": 1}},
                        {"jsonrpc": "2.0", "id": 2, "method": "second", "params": {"two": 2}}]


def test_lifecycle_complete_readiness_frame_is_a_healthy_control(tmp_path, monkeypatch):
    deferred = []
    resources = SimpleNamespace(defer=lambda name, close: deferred.append((name, close)))
    with partial_host(b'{"ready":true}\n') as (process, release):
        monkeypatch.setattr(lifecycle.subprocess, "Popen", lambda *args, **kwargs: process)
        completed, outcome = guarded_call(
            lambda: lifecycle._host(tmp_path / "config", "peer", "endpoint", tmp_path, .5, resources), release)
    for name, close in deferred:
        if name != "external_host":
            close()
    assert completed and outcome[0] is process
    assert json.loads((tmp_path / "host-launch.json").read_text())["ready"] == {"ready": True}


def test_composed_suite_clamps_observation_sleep_after_work_consumes_budget(monkeypatch):
    from scripts.verification import multi_harness

    clock = ControlledClock()
    suite = multi_harness.ComposedSuite.__new__(multi_harness.ComposedSuite)
    suite.timeout = .03
    suite.call = lambda *args, **kwargs: clock.advance(elapsed=.025, wall=500)
    suite.raw_run = lambda: {}
    saved = []
    suite.save = lambda: saved.append(clock.elapsed)
    monkeypatch.setattr(multi_harness.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(multi_harness.time, "sleep", clock.sleep)
    with pytest.raises(VerificationFailure) as caught:
        suite.wait(lambda run: False, "missing-observation")
    assert clock.sleeps == pytest.approx([.005]), "COMPOSED_WAIT_SLEEP_USES_POST_OBSERVATION_REMAINDER"
    assert saved == pytest.approx([.03])
    assert caught.value.assertion == "missing-observation"


def test_composed_suite_still_accepts_a_late_truthy_cooperative_observation(monkeypatch):
    from scripts.verification import multi_harness

    clock = ControlledClock()
    suite = multi_harness.ComposedSuite.__new__(multi_harness.ComposedSuite)
    suite.timeout = .03
    suite.call = lambda *args, **kwargs: clock.advance(elapsed=.04)
    suite.raw_run = lambda: {"observed": True}
    monkeypatch.setattr(multi_harness.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(multi_harness.time, "sleep", clock.sleep)
    result = suite.wait(lambda run: run, "missing-observation")
    assert result == {"observed": True} and clock.sleeps == []


def test_composed_suite_retries_do_not_continue_beyond_the_observation_deadline(monkeypatch):
    from scripts.verification import multi_harness
    from xorchestrate.model import Conflict

    clock = ControlledClock()
    suite = multi_harness.ComposedSuite.__new__(multi_harness.ComposedSuite)
    suite.timeout = .025
    attempts = []

    def conflict(*args, **kwargs):
        attempts.append(clock.elapsed)
        raise Conflict("safe retry", retry_safe=True)

    suite.application = SimpleNamespace(call=conflict)
    suite.raw_run = lambda: {}
    suite.save = lambda: None
    monkeypatch.setattr(multi_harness.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(multi_harness.time, "sleep", clock.sleep)
    outcome = None
    try:
        suite.wait(lambda run: False, "missing-observation")
    except Exception as error:
        outcome = error
    assert attempts == pytest.approx([0, .01, .02]), "COMPOSED_RETRIES_SHARE_THE_OBSERVATION_BUDGET"
    assert isinstance(outcome, VerificationFailure) and outcome.assertion == "missing-observation"


@pytest.mark.parametrize("module,function,error,message", [
    ("managed_workflow", "await_condition", TimeoutError, "Expected managed workflow transition was not observed"),
    ("terminal_host", "wait", AssertionError, "condition not reached before the timeout"),
])
def test_shared_support_wait_clamps_after_predicate_work(monkeypatch, module, function, error, message):
    subject = importlib.import_module("tests.hexagonal.support." + module)
    clock = ControlledClock()
    polls = []
    monkeypatch.setenv("XO_TEST_TIMEOUT_SCALE", "1")
    monkeypatch.setattr(subject.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(subject.time, "sleep", clock.sleep)

    def predicate():
        polls.append(clock.elapsed)
        clock.advance(elapsed=.025, wall=-500)
        return False

    with pytest.raises(error, match=message):
        getattr(subject, function)(predicate, timeout=.03)
    assert clock.sleeps == pytest.approx([.005]), "SHARED_SUPPORT_WAIT_CLAMPS_POST_PREDICATE_REMAINDER"
    assert polls == [0] and clock.elapsed == pytest.approx(.03)


@pytest.mark.parametrize("module,function", [("managed_workflow", "await_condition"), ("terminal_host", "wait")])
@pytest.mark.parametrize("timeout_error", [False, True], ids=["runtime-error", "independent-wait-timeout"])
def test_shared_support_wait_preserves_predicate_error_identity(module, function, timeout_error):
    from scripts.testkit.waits import WaitBudget, WaitTimeout

    subject = importlib.import_module("tests.hexagonal.support." + module)
    failure = (WaitTimeout("independent predicate timeout", budget=WaitBudget(1, "product_guarantee"),
                           elapsed=1, attempts=1, retries=0) if timeout_error
               else RuntimeError("predicate failed independently"))

    def predicate():
        raise failure

    with pytest.raises(Exception) as caught:
        getattr(subject, function)(predicate)
    assert caught.value is failure, "SHARED_SUPPORT_PRESERVES_INDEPENDENT_PREDICATE_ERROR"


@pytest.mark.parametrize("module,function,returns_value", [
    ("managed_workflow", "await_condition", True), ("terminal_host", "wait", False),
])
def test_shared_support_wait_retains_its_success_return_contract(module, function, returns_value):
    subject = importlib.import_module("tests.hexagonal.support." + module)
    value = {"observed": True}
    assert getattr(subject, function)(lambda: value) == (value if returns_value else None)


@pytest.mark.parametrize("module,function,error", [
    ("managed_workflow", "await_condition", TimeoutError), ("terminal_host", "wait", AssertionError),
])
def test_shared_support_harness_allowance_uses_the_opted_in_scale(monkeypatch, module, function, error):
    subject = importlib.import_module("tests.hexagonal.support." + module)
    clock = ControlledClock()
    monkeypatch.setenv("XO_TEST_TIMEOUT_SCALE", "2")
    monkeypatch.setattr(subject.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(subject.time, "sleep", clock.sleep)
    with pytest.raises(error):
        getattr(subject, function)(lambda: False, timeout=.03)
    assert clock.elapsed == pytest.approx(.06), "SHARED_HARNESS_ALLOWANCE_APPLIES_CONFIGURED_SCALE"


@pytest.mark.parametrize("terminal", [False, True], ids=["socket", "terminal"])
@pytest.mark.parametrize("timeout,scale,expected", [(.005, 1, .005), (.02, 2, .04)])
def test_terminal_receivers_never_manufacture_a_minimum_timeout(monkeypatch, terminal, timeout, scale, expected):
    from tests.hexagonal.support import managed_terminals

    clock = ControlledClock()
    supplied = []
    monkeypatch.setenv("XO_TEST_TIMEOUT_SCALE", str(scale))
    monkeypatch.setattr(managed_terminals.time, "monotonic", clock.monotonic)
    if terminal:
        def select(read, write, errors, timeout):
            supplied.append(timeout)
            return read, [], []
        monkeypatch.setattr(managed_terminals.select, "select", select)
        monkeypatch.setattr(managed_terminals.os, "read", lambda fd, limit: b"ready")
        result = managed_terminals.receive_terminal_until(42, b"ready", timeout=timeout)
    else:
        connection = SimpleNamespace(settimeout=supplied.append, recv=lambda size: b"ready")
        result = managed_terminals.receive_until(connection, b"ready", timeout=timeout)
    assert result == b"ready"
    assert supplied == pytest.approx([expected]), "TERMINAL_RECEIVE_USES_ACTUAL_REMAINDER"


@pytest.mark.parametrize("terminal", [False, True], ids=["socket", "terminal"])
def test_terminal_receiver_eof_preserves_missing_marker_assertion(monkeypatch, terminal):
    from tests.hexagonal.support import managed_terminals

    if terminal:
        monkeypatch.setattr(managed_terminals.select, "select", lambda read, *args: (read, [], []))
        monkeypatch.setattr(managed_terminals.os, "read", lambda fd, limit: b"")
        def call():
            return managed_terminals.receive_terminal_until(42, b"missing")
    else:
        connection = SimpleNamespace(settimeout=lambda seconds: None, recv=lambda size: b"")
        def call():
            return managed_terminals.receive_until(connection, b"missing")
    with pytest.raises(AssertionError) as caught:
        call()
    assert caught.value.args == (b"",)


def test_socket_receiver_preserves_its_underlying_read_error():
    from tests.hexagonal.support.managed_terminals import receive_until

    error = OSError("connection lost")
    def receive(size):
        raise error
    connection = SimpleNamespace(settimeout=lambda seconds: None, recv=receive)
    with pytest.raises(OSError) as caught:
        receive_until(connection, b"missing")
    assert caught.value is error


@pytest.mark.parametrize("cost,expected", [(.03, .02), (.06, 0)])
def test_supervisor_cleanup_does_not_add_a_terminal_grace_floor(tmp_path, monkeypatch, cost, expected):
    from scripts.testkit.adapters import supervised_run

    clock = ControlledClock()
    host = CostedHost(clock, observation_cost=cost)
    monkeypatch.setattr(supervised_run.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(supervised_run.time, "sleep", clock.sleep)
    monkeypatch.setattr(supervised_run, "OwnedProcesses", QuietTracker)
    report = supervised_run.supervise(("controlled-worker",), tmp_path, host=host, cleanup_budget=.05, grace=1)
    assert host.graces == pytest.approx([expected]), "SUPERVISOR_CLEANUP_PASSES_ZERO_WITHOUT_NEW_GRACE"
    if cost > .05:
        assert report["cleanup"]["verified"] is False and "cleanup_deadline" in report["cleanup"]["errors"]


def test_supervisor_records_unbudgeted_host_shutdown_overrun(tmp_path, monkeypatch):
    from scripts.testkit.adapters import supervised_run

    clock = ControlledClock()
    host = CostedHost(clock, observation_cost=0, shutdown_cost=.1)
    monkeypatch.setattr(supervised_run.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(supervised_run.time, "sleep", clock.sleep)
    monkeypatch.setattr(supervised_run, "OwnedProcesses", QuietTracker)
    report = supervised_run.supervise(("controlled-worker",), tmp_path, host=host, cleanup_budget=.05, grace=1)
    assert report["cleanup"]["verified"] is False
    assert "cleanup_deadline" in report["cleanup"]["errors"]
    assert report["cleanup"]["elapsed_seconds"] == pytest.approx(.1)
    assert report["cleanup"]["overrun_seconds"] == pytest.approx(.05)
    assert report["cleanup"]["budget_scope"] == "cooperative_cleanup"
    assert report["duration_seconds"] == .1, "unbudgeted host.shutdown remains a cooperative boundary"


def test_managed_worker_clamps_its_term_phase_sleep_after_observation(tmp_path, monkeypatch):
    import signal

    from scripts.testkit.adapters import managed_process
    from scripts.testkit.adapters.ownership import Identity

    clock = ControlledClock()
    identity = Identity(1234, "controlled")
    signals = []

    def observe():
        if signals and signals[-1] == signal.SIGKILL:
            return set()
        if signals:
            clock.advance(elapsed=.025)
        return {identity}

    tracker = SimpleNamespace(observe=observe, reap=lambda: None)
    monkeypatch.setattr(managed_process, "OwnedProcesses", lambda: tracker)
    monkeypatch.setattr(managed_process, "send_signal", lambda item, number: signals.append(number))
    monkeypatch.setattr(managed_process.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(managed_process.time, "sleep", clock.sleep)
    host = SimpleNamespace(terminals=lambda: [])
    worker = managed_process.ManagedWorker(tmp_path, host=host, root=tmp_path, interpreter="unused", grace=.03)
    receipt = worker.cleanup_work()
    assert clock.sleeps[0] == pytest.approx(.005), "MANAGED_WORKER_PHASE_SLEEP_USES_POST_OBSERVATION_REMAINDER"
    assert signals == [signal.SIGTERM, signal.SIGKILL] and receipt["verified"] is True


def test_composed_suite_preserves_an_independent_timeout_after_cooperative_overrun(monkeypatch):
    from scripts.verification import multi_harness

    clock = ControlledClock()
    error = TimeoutError("independent adapter timeout")
    suite = multi_harness.ComposedSuite.__new__(multi_harness.ComposedSuite)
    suite.timeout = .01

    def call(*args, **kwargs):
        clock.advance(elapsed=.02)
        raise error

    suite.call = call
    monkeypatch.setattr(multi_harness.time, "monotonic", clock.monotonic)
    with pytest.raises(TimeoutError) as caught:
        suite.wait(lambda run: True, "outer-observation")
    assert caught.value is error
