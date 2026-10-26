"""IV03: native evidence never lends authority to a contradictory callback.

These raw-evidence controls retain the reviewer's one-field witness through
normalization, application effects, provider joins and the native-call audit.
"""
from copy import deepcopy

import pytest

import scripts.verification.checker as checker
from scripts.verification.normalize import normalize
from tests.hexagonal.contracts.callback_identity import CALLBACK_JOIN_SCENARIOS, CALLBACK_LIFECYCLE_SCENARIOS
from tests.hexagonal.contracts.identity import assert_identity_scenario
from tests.hexagonal.support.callback_identity import RawCallbackEvidence, callback
from tests.hexagonal.support.raw_evidence import lifecycle_input, raw_exchange


def assert_callback_rejected(inputs, diagnostic="callback_identity_mismatch"):
    result = checker.verify(*inputs)
    assert result["status"] == "incomplete", "IV03_CALLBACK_IDENTITY_MUST_BE_BOUND"
    assert any(case.get("assertion") == diagnostic for case in result["cases"]), result


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
    scenario = next(case for case in CALLBACK_JOIN_SCENARIOS
                    if case.name == "completed-send_message-native_session-wrong")
    with pytest.raises(AssertionError, match="IV03_INDEPENDENT_CLAIMS"):
        assert_identity_scenario(RawCallbackEvidence, scenario, exact_diagnostic=True)
    assert activated


def test_unrelated_checker_failure_is_not_callback_detection(monkeypatch):
    inputs = raw_exchange()
    assert checker.verify(*inputs)["status"] == "passed"

    def unrelated(*args):
        raise RuntimeError("unrelated checker setup failure")

    monkeypatch.setattr(checker, "match_callback", unrelated)
    with pytest.raises(RuntimeError, match="unrelated checker setup failure"):
        assert_callback_rejected(inputs)


def assert_lifecycle_rejected(inputs):
    rejected = False
    try:
        checker.lifecycle_evidence(*inputs)
    except checker.VerificationFailure as error:
        rejected = error.assertion == "lifecycle_retained_observation_binding"
    assert rejected, "IV03_LIFECYCLE_CALLBACK_IDENTITY_MUST_BE_BOUND"


def test_sustained_lifecycle_fault_cannot_erase_nested_identity(monkeypatch):
    inputs, rows = lifecycle_input()
    assert checker.lifecycle_evidence(*inputs)["operation_id"] == "op"
    rows[1]["observation"]["session_id"] = "other-app"
    assert_lifecycle_rejected(inputs)
    match = checker.identity_matches
    activated = []

    def ignores_nested_identity(event, sessions):
        activated.append(True)
        return match({key: value for key, value in event.items() if key != "observation_session_id"}, sessions)

    monkeypatch.setattr(checker, "identity_matches", ignores_nested_identity)
    assert checker.lifecycle_evidence(*inputs)["operation_id"] == "op", "IV03_LIFECYCLE_FAULT_MUST_ACTIVATE"
    with pytest.raises(AssertionError, match="^IV03_LIFECYCLE_CALLBACK_IDENTITY_MUST_BE_BOUND"):
        assert_lifecycle_rejected(inputs)
    scenario = next(case for case in CALLBACK_LIFECYCLE_SCENARIOS
                    if case.name == "callback-nested-session-wrong")
    with pytest.raises(AssertionError, match="IV03_INDEPENDENT_CLAIMS"):
        assert_identity_scenario(RawCallbackEvidence, scenario, exact_diagnostic=True)
    assert activated




@pytest.mark.parametrize("scenario", CALLBACK_JOIN_SCENARIOS, ids=lambda scenario: scenario.name)
def test_callback_identity_contract(scenario):
    assert_identity_scenario(RawCallbackEvidence, scenario, exact_diagnostic=True)


@pytest.mark.parametrize("operation", ("resume", "reconnect"))
@pytest.mark.parametrize("scenario", CALLBACK_LIFECYCLE_SCENARIOS, ids=lambda scenario: scenario.name)
def test_lifecycle_callback_identity_contract(operation, scenario):
    assert_identity_scenario(RawCallbackEvidence, scenario, operation, exact_diagnostic=True)


@pytest.mark.parametrize("operation", ("resume", "reconnect"))
def test_lifecycle_control_returns_the_bound_operation(operation):
    inputs, _ = lifecycle_input(operation)
    assert checker.lifecycle_evidence(*inputs)["operation_id"] == inputs[2]["operation_id"]
