"""F06/H2/H7/H8: per-turn completion never manufactures aggregate idle.

Known-bad raw subjects retain held/uncertain work while individual turns stop.
The checker must reject them; actual aggregate idle remains the passing control.
"""
from copy import deepcopy

import pytest

from scripts.verification.checker import verify
from scripts.verification.normalize import normalize
from tests.hexagonal.support.completion_evidence import completion_input


def assert_pending_rejected(inputs):
    result = verify(*inputs)
    assert result["status"] == "incomplete", "F06_OUTSTANDING_WORK_CANNOT_PASS_COMPLETION"
    assert result["cases"][0]["assertion"] == "native_completion_missing"


@pytest.mark.parametrize("peer", ["codex", "claude", "grok"])
def test_explicit_aggregate_completion_and_later_state_are_adjudicated(peer):
    inputs, add = completion_input(peer)
    add("native_completion", activity="idle")
    assert verify(*inputs)["status"] == "passed"
    add("activity", state="active")
    assert_pending_rejected(inputs)
    add("activity", state="unknown")
    assert_pending_rejected(inputs)
    add("native_completion", activity="idle")
    assert verify(*inputs)["status"] == "passed"


@pytest.mark.parametrize("kind", ["turn_completed", "turn_failed", "native_completion", "native_completion_unknown"])
def test_turn_result_without_aggregate_state_is_unknown_not_idle(kind):
    inputs, add = completion_input()
    add("activity", state="idle")
    assert verify(*inputs)["status"] == "passed"
    add(kind, turn_id="A")
    assert_pending_rejected(inputs)
    run = inputs[0]
    observed = normalize(run["raw_records"], run["sessions"], [], run["mode"])
    assert observed[-1]["state"] == "unknown"
    add("activity", state="idle")
    assert verify(*inputs)["status"] == "passed"


def test_one_claude_stop_with_another_held_callback_cannot_pass():
    inputs, add = completion_input()
    add("activity", state="active")
    assert_pending_rejected(inputs)
    add("turn_completed", turn_id="A")
    add("activity", state="active")
    assert_pending_rejected(inputs)
    add("turn_completed", turn_id="B")
    add("activity", state="idle")
    assert verify(*inputs)["status"] == "passed"


@pytest.mark.parametrize("stale", ["record", "observation", "incarnation"])
def test_retired_state_does_not_override_current_native_idle(stale):
    inputs, add = completion_input()
    add("native_completion", activity="idle")
    last = add("activity", state="active")
    if stale == "record":
        last["stale"] = True
    elif stale == "observation":
        last["observation"]["stale"] = True
    else:
        last["observation"]["incarnation"] = "retired"
    assert verify(*inputs)["status"] == "passed"


def test_sustained_fault_calibrates_the_completion_assertion(monkeypatch):
    """Fault recreates old normalization; oracle must expose its false pass."""
    import scripts.verification.normalize as normalizer

    inputs, add = completion_input()
    add("turn_completed", turn_id="A")
    add("activity", state="active")
    assert_pending_rejected(inputs)
    valid = deepcopy(inputs)
    valid[0]["raw_records"]["observations"][-1]["observation"]["state"] = "idle"
    assert verify(*valid)["status"] == "passed"
    activation = []
    def faulty(raw, sessions, provider_events, mode):
        events = normalize(raw, sessions, provider_events, mode)
        activation.append(True)
        # Deliberately faulty: ignore aggregate state and declare any Stop idle.
        events = [event for event in events if event.get("activity_kind") != "activity"]
        for event in events:
            if event.get("activity_kind") == "turn_completed":
                event["state"] = "idle"
        return events
    monkeypatch.setattr(normalizer, "normalize", faulty)
    assert verify(*inputs)["status"] == "passed", "F06_FAULT_MUST_ACTIVATE"
    with pytest.raises(AssertionError, match="^F06_OUTSTANDING_WORK_CANNOT_PASS_COMPLETION"):
        assert_pending_rejected(inputs)
    assert activation
    assert verify(*valid)["status"] == "passed"


def test_unrelated_normalization_failure_is_not_completion_detection(monkeypatch):
    import scripts.verification.normalize as normalizer

    inputs, _ = completion_input()
    def unrelated(*args):
        raise RuntimeError("unrelated checker setup failure")
    monkeypatch.setattr(normalizer, "normalize", unrelated)
    with pytest.raises(RuntimeError, match="unrelated checker setup failure"):
        assert_pending_rejected(inputs)
