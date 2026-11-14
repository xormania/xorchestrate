"""Observation allowances consume one elapsed budget independently of wall time."""

import pytest

from tests.hexagonal.support import native
from tests.hexagonal.support.temporal_waits import ControlledClock, conflict_with_retry_flag
from xorchestrate.model import OrchestrationError


@pytest.fixture(autouse=True)
def clear_timeout_scale(monkeypatch):
    monkeypatch.delenv("XO_TEST_TIMEOUT_SCALE", raising=False)


def test_running_deadline_children_keep_the_earlier_expiry_and_same_clock():
    from scripts.testkit.waits import Deadline

    clock = ControlledClock()
    parent = Deadline.after(5, clock=clock)
    clock.advance(elapsed=2, wall=-10_000)
    long_child, short_child = parent.child(10), parent.child(1)
    assert parent.remaining() == 3 and parent.remaining(limit=.5) == .5
    assert long_child.remaining() == 3 and short_child.remaining() == 1
    clock.advance(elapsed=2, wall=20_000)
    assert parent.remaining() == long_child.remaining() == 1
    assert short_child.remaining() == 0, "CHILD_EXPIRY_NEVER_RENEWS_PARENT_TIME"
    assert long_child.clock is short_child.clock is clock


def test_running_zero_deadline_has_no_minimum_new_allowance():
    from scripts.testkit.waits import Deadline

    clock = ControlledClock()
    deadline = Deadline.after(0, clock=clock)
    assert deadline.remaining() == deadline.child(10).remaining() == 0
    clock.advance(elapsed=1)
    assert deadline.remaining(limit=.001) == 0


@pytest.mark.parametrize("duration", [-1, float("nan"), float("inf"), True])
def test_running_deadline_rejects_unusable_durations(duration):
    from scripts.testkit.waits import Deadline

    with pytest.raises(ValueError):
        Deadline.after(duration, clock=ControlledClock())


@pytest.mark.parametrize("classification,expected", [
    ("harness_allowance", 4), ("product_guarantee", 2), ("fault_control", 2),
])
def test_started_wait_budget_keeps_classification_and_parent_remainder(classification, expected):
    from scripts.testkit.waits import Deadline, WaitBudget

    clock = ControlledClock()
    parent = Deadline.after(5, clock=clock)
    clock.advance(elapsed=1)
    started = WaitBudget(2, classification, scale=3).start(clock, parent=parent)
    assert started.remaining() == expected, "STARTED_BUDGET_CANNOT_EXTEND_THE_PARENT"
    clock.advance(elapsed=1, wall=-500)
    assert started.remaining() == expected - 1


def test_shared_parent_deadline_limits_the_native_wait_without_restarting():
    from scripts.testkit.waits import Deadline, WaitTimeout

    clock = ControlledClock()
    parent = Deadline.after(.025, clock=clock)
    polls = []

    def observe():
        polls.append(clock.elapsed)
        return False

    with pytest.raises(WaitTimeout):
        native.wait_for(observe, timeout=1, timeout_scale=3, deadline=parent,
                        clock=clock, sleep=clock.sleep)
    assert clock.sleeps == pytest.approx([.01, .01, .005]), "NATIVE_CHILD_WAIT_USES_PARENT_REMAINDER"
    assert polls == pytest.approx([0, .01, .02])


def test_exhausted_parent_does_not_start_a_predicate():
    from scripts.testkit.waits import Deadline, WaitBudget, WaitTimeout, bounded_wait

    clock = ControlledClock()
    calls = []
    with pytest.raises(WaitTimeout) as caught:
        bounded_wait(lambda: calls.append(True), WaitBudget(1, "harness_allowance"),
                     deadline=Deadline.after(0, clock=clock), clock=clock, sleep=clock.sleep)
    assert calls == clock.sleeps == [], "EXHAUSTED_PARENT_PREVENTS_CHILD_POLLING"
    assert caught.value.details["attempts"] == 0


def test_native_wait_clamps_sleep_to_remaining_budget(monkeypatch):
    clock = ControlledClock()
    polls = []

    def observe():
        polls.append(clock.elapsed)
        clock.advance(wall=-100)
        return False

    with monkeypatch.context() as patch:
        patch.setattr(native.time, "monotonic", clock.monotonic)
        patch.setattr(native.time, "time", clock.time)
        patch.setattr(native.time, "sleep", clock.sleep)
        with pytest.raises(AssertionError, match="missing fixture acknowledgement"):
            native.wait_for(observe, timeout=.025, message="missing fixture acknowledgement")

    assert clock.sleeps == pytest.approx([.01, .01, .005]), "NATIVE_WAIT_CLAMPS_REMAINING_BUDGET"
    assert clock.elapsed == pytest.approx(.025)
    assert polls == pytest.approx([0, .01, .02])
    assert clock.wall == 700, "wall-clock changes remain independent of elapsed time"


def test_native_wait_does_not_sleep_after_observation_consumes_budget(monkeypatch):
    clock = ControlledClock()
    polls = []

    def observe():
        polls.append(clock.elapsed)
        clock.advance(elapsed=.03, wall=600)
        return False

    with monkeypatch.context() as patch:
        patch.setattr(native.time, "monotonic", clock.monotonic)
        patch.setattr(native.time, "time", clock.time)
        patch.setattr(native.time, "sleep", clock.sleep)
        with pytest.raises(AssertionError, match="Expected native observation"):
            native.wait_for(observe, timeout=.025)

    assert clock.sleeps == [], "NATIVE_WAIT_RECHECKS_BUDGET_AFTER_OBSERVATION"
    assert polls == [0]
    assert clock.elapsed == pytest.approx(.03)


@pytest.mark.parametrize("classification,expected_scale", [
    ("harness_allowance", 3), ("product_guarantee", 1), ("fault_control", 1),
])
def test_budget_scaling_is_confined_to_harness_allowances(classification, expected_scale):
    from scripts.testkit.waits import WaitBudget

    budget = WaitBudget(.5, classification, scale=3)
    assert budget.effective_scale == expected_scale, "ONLY_HARNESS_ALLOWANCES_SCALE"
    assert budget.effective_timeout == .5 * expected_scale


def test_injected_wait_reports_one_budget_across_retries_and_wall_steps():
    from scripts.testkit.waits import WaitBudget, WaitTimeout, bounded_wait

    clock = ControlledClock()
    retry = LookupError("observation changed")
    polls = []

    def observe():
        polls.append(clock.elapsed)
        clock.advance(wall=1_000 if len(polls) % 2 else -2_000)
        raise retry

    with pytest.raises(WaitTimeout, match="acknowledgement missing") as raised:
        bounded_wait(observe, WaitBudget(.025, "harness_allowance"), clock=clock, sleep=clock.sleep,
                     retryable=lambda error: error is retry, message="acknowledgement missing")

    assert polls == pytest.approx([0, .01, .02]), "RETRIES_KEEP_ORIGINAL_MONOTONIC_BUDGET"
    assert clock.sleeps == pytest.approx([.01, .01, .005])
    assert raised.value.details == {
        "classification": "harness_allowance", "requested_timeout": .025,
        "effective_timeout": .025, "configured_scale": 1, "scale": 1,
        "elapsed": pytest.approx(.025), "attempts": 3, "retries": 3,
    }


def test_native_wait_returns_late_truthy_observation_without_claiming_preemption():
    clock = ControlledClock()
    expected = {"observed": True}

    def observe():
        clock.advance(elapsed=2, wall=-100)
        return expected

    result = native.wait_for(observe, timeout=1, clock=clock, sleep=clock.sleep)
    assert result is expected
    assert clock.elapsed == 2 and clock.sleeps == [], "OBSERVATION_WAIT_DOES_NOT_PREEMPT_PREDICATES"


@pytest.mark.parametrize("error", [
    conflict_with_retry_flag(False),
    conflict_with_retry_flag(1),
    conflict_with_retry_flag("true"),
    conflict_with_retry_flag(None),
    OrchestrationError("other application error", "operation_unknown", {"retry_safe": True}),
])
def test_native_wait_propagates_every_error_without_explicit_safe_conflict(error):
    clock = ControlledClock()
    attempts = []

    def observe():
        attempts.append(True)
        raise error

    with pytest.raises(type(error)) as raised:
        native.wait_for(observe, clock=clock, sleep=clock.sleep)
    assert raised.value is error
    assert attempts == [True] and clock.sleeps == [], "ONLY_EXPLICIT_SAFE_CONFLICTS_RETRY"


@pytest.mark.parametrize("classification,override,expected_scale", [
    ("harness_allowance", None, 3),
    ("harness_allowance", 2, 2),
    ("product_guarantee", None, 1),
    ("fault_control", None, 1),
])
def test_native_wait_classifies_environment_scale_and_explicit_override(
    monkeypatch, classification, override, expected_scale,
):
    monkeypatch.setenv("XO_TEST_TIMEOUT_SCALE", "3")
    clock = ControlledClock()
    polls = []

    def observe():
        polls.append(clock.elapsed)
        return False

    with pytest.raises(AssertionError) as raised:
        native.wait_for(observe, timeout=.01, classification=classification, timeout_scale=override,
                        clock=clock, sleep=clock.sleep)
    assert clock.elapsed == pytest.approx(.01 * expected_scale), "NATIVE_SCALE_REACHES_SHARED_WAIT"
    assert len(polls) == expected_scale
    details = raised.value.details
    assert details["classification"] == classification
    assert details["requested_timeout"] == .01
    assert details["effective_timeout"] == pytest.approx(.01 * expected_scale)
    assert details["configured_scale"] == (3 if override is None else override)
    assert details["scale"] == expected_scale


@pytest.mark.parametrize("value", ["broken", "", "0", "-1", "nan", "inf"])
def test_invalid_environment_scale_fails_before_any_observation(monkeypatch, value):
    monkeypatch.setenv("XO_TEST_TIMEOUT_SCALE", value)
    clock = ControlledClock()
    calls = []

    with pytest.raises(ValueError):
        native.wait_for(lambda: calls.append(True), clock=clock, sleep=clock.sleep,
                        classification="product_guarantee")
    assert calls == [] and clock.sleeps == [], "INVALID_SCALE_FAILS_BEFORE_OBSERVATION"


def test_explicit_scale_overrides_invalid_environment(monkeypatch):
    monkeypatch.setenv("XO_TEST_TIMEOUT_SCALE", "broken")
    clock = ControlledClock()
    assert native.wait_for(lambda: "ready", timeout_scale=1, clock=clock, sleep=clock.sleep) == "ready"


@pytest.mark.parametrize("field,value", [
    ("timeout", 0), ("timeout", -1), ("timeout", True), ("timeout", float("nan")),
    ("timeout", float("inf")), ("timeout_scale", 0), ("timeout_scale", True),
    ("timeout_scale", float("nan")), ("timeout_scale", float("inf")),
    ("poll_interval", 0), ("poll_interval", -1), ("poll_interval", True),
    ("poll_interval", float("nan")), ("poll_interval", float("inf")),
    ("classification", "unspecified"),
])
def test_invalid_wait_parameters_fail_before_predicate(field, value):
    clock = ControlledClock()
    calls = []
    with pytest.raises(ValueError):
        native.wait_for(lambda: calls.append(True), clock=clock, sleep=clock.sleep, **{field: value})
    assert calls == [] and clock.sleeps == [], "INVALID_BUDGET_FAILS_BEFORE_OBSERVATION"


def test_scaling_overflow_is_rejected_before_predicate():
    clock = ControlledClock()
    calls = []
    with pytest.raises(ValueError):
        native.wait_for(lambda: calls.append(True), timeout=1e308, timeout_scale=2,
                        clock=clock, sleep=clock.sleep)
    assert calls == [] and clock.sleeps == []


@pytest.mark.parametrize("scale", [None, "2.5"])
def test_testkit_environment_retains_selected_scale_for_command_receipts(tmp_path, monkeypatch, scale):
    import sys

    from scripts.testkit.composition import cache_environment

    root = tmp_path / "candidate"
    cache = tmp_path / "bytecode"
    monkeypatch.setattr(sys, "pycache_prefix", str(cache))
    monkeypatch.setenv("UNRELATED_OPERATOR_SECRET", "do-not-forward")
    if scale is not None:
        monkeypatch.setenv("XO_TEST_TIMEOUT_SCALE", scale)
    expected = {"PYTHONPYCACHEPREFIX": str(cache)}
    if scale is not None:
        expected["XO_TEST_TIMEOUT_SCALE"] = scale
    assert cache_environment(root) == expected, "COMMAND_RECEIPTS_RETAIN_SELECTED_TIMEOUT_SCALE"
