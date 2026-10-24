"""IV03: native evidence never lends authority to a contradictory callback.

These raw-evidence controls retain the reviewer's one-field witness through
normalization, application effects, provider joins and the native-call audit.
"""
from copy import deepcopy

import pytest

import scripts.verification.checker as checker
from scripts.verification.normalize import normalize
from tests.hexagonal.test_raw_evidence_calibration import raw_exchange


def assert_callback_rejected(inputs, diagnostic="callback_identity_mismatch"):
    result = checker.verify(*inputs)
    assert result["status"] == "incomplete", "IV03_CALLBACK_IDENTITY_MUST_BE_BOUND"
    assert any(case.get("assertion") == diagnostic for case in result["cases"]), result


def callback(inputs, phase, action):
    return next(record for record in inputs[0]["raw_records"]["observations"]
                if record["observation"].get("phase") == phase
                and record["observation"].get("action") == action)


@pytest.mark.parametrize("action", ["send_message", "record_result", "invoke"])
@pytest.mark.parametrize("phase", ["requested", "completed"])
@pytest.mark.parametrize("field", ["native_id", "incarnation"])
@pytest.mark.parametrize("value", ["contradictory", None, True, 1, [], "missing"])
def test_callback_identity_is_independent_on_every_join(action, phase, field, value):
    inputs = raw_exchange("busy")
    assert checker.verify(*inputs)["status"] == "passed"
    observed = callback(inputs, phase, action)["observation"]
    if value == "missing":
        observed.pop(field)
    else:
        observed[field] = value
    assert_callback_rejected(inputs)


@pytest.mark.parametrize("phase", ["requested", "completed"])
@pytest.mark.parametrize("value", ["grok-app", "unrecognized-app", None, "missing"])
def test_callback_application_session_cannot_be_replaced_or_omitted(phase, value):
    inputs = raw_exchange()
    assert checker.verify(*inputs)["status"] == "passed"
    record = callback(inputs, phase, "send_message")
    if value == "missing":
        record.pop("session_id")
        diagnostic = "malformed_raw_evidence"
    else:
        record["session_id"] = value
        diagnostic = "callback_identity_mismatch"
    assert_callback_rejected(inputs, diagnostic)


@pytest.mark.parametrize("side", ["native", "requested", "completed"])
@pytest.mark.parametrize("value", ["other-app", None, False])
def test_explicit_nested_session_identity_cannot_be_reconciled_away(side, value):
    inputs = raw_exchange()
    assert checker.verify(*inputs)["status"] == "passed"
    record = next(r for r in inputs[0]["raw_records"]["observations"] if r["id"] == side + "call-0")
    record["observation"]["session_id"] = value
    assert_callback_rejected(inputs, "native_call_missing_or_misbound" if side == "native"
                             else "callback_identity_mismatch")


@pytest.mark.parametrize("field", ["native_id", "incarnation", "actor_id", "missing-session"])
@pytest.mark.parametrize("value", ["contradictory", None, True])
def test_callback_must_agree_with_independent_durable_session(field, value):
    inputs = raw_exchange()
    assert checker.verify(*inputs)["status"] == "passed"
    rows = inputs[0]["raw_records"]["sessions"]
    rows[0]["backend"] = deepcopy(rows[0]["backend"])
    if field == "missing-session":
        rows.pop(0)
    elif field == "actor_id":
        rows[0][field] = value
    else:
        rows[0]["backend"][field] = value
    assert_callback_rejected(inputs, "callback_session_binding")


@pytest.mark.parametrize("phase", ["requested", "completed"])
@pytest.mark.parametrize("value", ["other-actor", None, False])
def test_explicit_callback_actor_is_not_replaced_by_session_actor(phase, value):
    inputs = raw_exchange()
    assert checker.verify(*inputs)["status"] == "passed"
    callback(inputs, phase, "send_message")["created_by"] = value
    assert_callback_rejected(inputs, "callback_actor_binding")


@pytest.mark.parametrize("phase", ["requested", "completed"])
@pytest.mark.parametrize("value", [None, False, 1, []])
def test_callback_call_identity_requires_a_nonempty_string(phase, value):
    inputs = raw_exchange()
    assert checker.verify(*inputs)["status"] == "passed"
    callback(inputs, phase, "send_message")["observation"]["call_id"] = value
    assert_callback_rejected(inputs, "callback_call_binding")


def test_completed_callback_cannot_substitute_for_its_missing_request():
    inputs = raw_exchange()
    assert checker.verify(*inputs)["status"] == "passed"
    rows = inputs[0]["raw_records"]["observations"]
    rows.remove(callback(inputs, "requested", "send_message"))
    assert_callback_rejected(inputs, "callback_request_missing_or_misbound")


def test_permitted_explicit_work_scope_and_unrelated_history_stay_valid():
    inputs = raw_exchange()
    raw = inputs[0]["raw_records"]
    for record in raw["observations"]:
        record["observation"]["arguments"]["work_id"] = "explicit-work"
    for record in raw["messages"] + raw["evidence"]:
        record["work_id"] = "explicit-work"
    # Irrelevant historical sessions need not be qualified for this exchange.
    raw["sessions"].append({"id": "unrelated-history", "work_id": "other-work"})
    assert checker.verify(*inputs)["status"] == "passed"
    events = normalize(raw, inputs[0]["sessions"], [], inputs[0]["mode"])
    assert all(event["callback_actor_basis"] == "session" for event in events if event["event"] == "tool_called")


def test_valid_duplicate_native_observation_does_not_change_callback_authority():
    inputs = raw_exchange()
    original = inputs[0]["raw_records"]["observations"][0]
    inputs[0]["raw_records"]["observations"].append({**deepcopy(original), "id": "duplicate-native-observation"})
    assert checker.verify(*inputs)["status"] == "passed"
    callback(inputs, "completed", "send_message")["observation"]["incarnation"] = "retired"
    assert_callback_rejected(inputs)


def test_sustained_fault_calibrates_callback_identity_rejection(monkeypatch):
    valid = raw_exchange()
    invalid = deepcopy(valid)
    callback(invalid, "completed", "send_message")["observation"]["native_id"] = "unrelated-native"
    assert checker.verify(*valid)["status"] == "passed"
    assert_callback_rejected(invalid)
    match = checker.match_callback
    activated = []

    def lends_native_identity(events, event, sessions):
        activated.append(True)
        # Deliberately restore the reviewed defect by lending the valid peer's
        # qualified identity to the callback while preserving its wrong raw data.
        return match(events, {**event, **sessions[event["peer"]]}, sessions)

    monkeypatch.setattr(checker, "match_callback", lends_native_identity)
    assert checker.verify(*valid)["status"] == "passed"
    assert checker.verify(*invalid)["status"] == "passed", "IV03_FAULT_MUST_ACTIVATE"
    with pytest.raises(AssertionError, match="^IV03_CALLBACK_IDENTITY_MUST_BE_BOUND"):
        assert_callback_rejected(invalid)
    assert activated


def test_unrelated_checker_failure_is_not_callback_detection(monkeypatch):
    inputs = raw_exchange()
    assert checker.verify(*inputs)["status"] == "passed"

    def unrelated(*args):
        raise RuntimeError("unrelated checker setup failure")

    monkeypatch.setattr(checker, "match_callback", unrelated)
    with pytest.raises(RuntimeError, match="unrelated checker setup failure"):
        assert_callback_rejected(inputs)
