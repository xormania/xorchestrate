"""F06/H2/H7/H8: per-turn completion never manufactures aggregate idle.

Known-bad raw subjects retain held/uncertain work while individual turns stop.
The checker must reject them; actual aggregate idle remains the passing control.
"""
from copy import deepcopy

import pytest

from scripts.verification.capture import canonical, digest
from scripts.verification.checker import verify
from scripts.verification.normalize import normalize


def completion_input(peer="claude"):
    identity = {"session_id": peer + "-app", "native_id": peer + "-native", "incarnation": peer + "-epoch"}
    records = []
    def add(kind, *, at=None, **fields):
        index = len(records) + 1
        source = "claude/Stop" if peer == "claude" else peer
        if kind == "native_completion" and peer != "claude":
            fields.setdefault("turn_id" if peer == "codex" else "prompt_id", "A")
        record = {"id": "observation-" + str(index), "session_id": identity["session_id"],
                  "origin": "adapter", "stale": False, "created_at": at or f"2026-10-24T00:00:{index:02}+00:00",
                  "observation": {**identity, "kind": kind, "source": source, **fields}}
        records.append(record)
        return record
    def turn(phase, name):
        if peer == "claude":
            add("turn_started" if phase == "start" else "turn_completed", turn_id=name, activity="idle" if name == "B" else "active")
        elif peer == "codex":
            add("native_notification", method="turn/started" if phase == "start" else "turn/completed",
                payload={"threadId": identity["native_id"], "turn": {"id": name}})
        else:
            add("native_notification", method="session/update" if phase == "start" else "_x.ai/session/prompt_complete",
                payload={"sessionId": identity["native_id"], "promptId": name,
                         "update": {"sessionUpdate": "agent_message_chunk"} if phase == "start" else {}})
    turn("start", "A")
    turn("start", "B")
    add("application_tool", phase="requested", action="invoke", arguments={}, call_id="A")
    add("application_tool", phase="requested", action="invoke", arguments={}, call_id="B")
    # These tests isolate aggregate activity: both callbacks actually finished.
    # Outstanding callback/effect contradictions have separate IV02 coverage.
    add("application_tool", phase="completed", action="invoke", arguments={}, call_id="A")
    add("application_tool", phase="completed", action="invoke", arguments={}, call_id="B")
    turn("end", "A")
    turn("end", "B")
    criteria = {"required_cases": [{"id": "completion-" + peer, "kind": "completion", "peer": peer}]}
    fixture = {"batches": []}
    bindings = {"criteria_sha256": digest(canonical(criteria)), "fixture_sha256": digest(canonical(fixture))}
    run = {"mode": "protocol-fixture", "raw_records": {"observations": records, "messages": [], "evidence": [], "attempts": []},
           "sessions": {peer: identity}, "bindings": bindings, "executed_cases": ["completion-" + peer]}
    return (run, criteria, bindings, fixture), add


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
