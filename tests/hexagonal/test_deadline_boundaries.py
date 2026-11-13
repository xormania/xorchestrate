"""Nested blockers receive elapsed remainder; independent watchdogs bound old defects."""

from types import SimpleNamespace

import pytest

from scripts.acceptance.scenario import AcceptanceFailure
from scripts.testkit.adapters import command
from scripts.testkit.model import Cancelled
from scripts.verification import lifecycle
from scripts.verification.checker import VerificationFailure
from tests.hexagonal.support.deadline_boundaries import (
    PhaseChild,
    PhaseGroup,
    guarded_call,
    partial_host,
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
