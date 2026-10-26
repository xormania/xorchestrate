"""Sustained F08–F10 faults, from independent raw observations to verdict.

B5/H8/A2: corrupt native arguments while leaving executed request/effect intact;
H7/B5: add a later unknown attempt while preserving the successful history;
H2/B5: finish work after submission but before the retained-context reply.
The faulty subjects remain faulty: these tests pass only on the named rejection.
Each test checks an unmodified passing control before activating its fault.
"""
from copy import deepcopy

import pytest

from scripts.verification.checker import verify
from scripts.verification.normalize import normalize
from tests.hexagonal.support.raw_evidence import raw_exchange


def check(inputs):
    return verify(*inputs)


def assert_rejected(inputs, diagnostic):
    result = check(inputs)
    assert result["status"] == "incomplete"
    assert any(case.get("assertion") == diagnostic for case in result["cases"]), result


@pytest.mark.parametrize("side", ["native", "requested", "completed"])
def test_argument_disagreement_survives_normalization_and_is_rejected(side):
    inputs = raw_exchange()
    assert check(inputs)["status"] == "passed"
    run = inputs[0]
    observation = next(o for o in run["raw_records"]["observations"] if o["id"] == side + "call-0")
    observation["observation"]["arguments"]["destination"]["actor"] = "denied-canary"
    events = normalize(run["raw_records"], run["sessions"], [], run["mode"])
    native = next(e for e in events if e["event"] == "native_tool")
    application = next(e for e in events if e["event"] == ("queued" if side == "completed" else "tool_called"))
    assert native["arguments"]["destination"] != application["arguments"]["destination"]
    assert_rejected(inputs, "native_call_missing_or_misbound")


def test_consistent_wrong_callback_cannot_substitute_for_the_persisted_effect():
    inputs = raw_exchange()
    assert check(inputs)["status"] == "passed"
    for observation in inputs[0]["raw_records"]["observations"]:
        if observation["observation"]["call_id"] == "call-0":
            observation["observation"]["arguments"]["body"]["verification"]["nonce"] = "wrong"
    assert_rejected(inputs, "application_effect_arguments_mismatch")


@pytest.mark.parametrize("status", ["unknown", "running", "failed", "queued"])
@pytest.mark.parametrize("session", ["grok-app", "unrecognized-session"])
def test_later_unsuccessful_attempt_cannot_be_masked_by_old_success(status, session):
    inputs = raw_exchange()
    assert check(inputs)["status"] == "passed"
    attempts = inputs[0]["raw_records"]["attempts"]
    attempts.append({**deepcopy(attempts[0]), "id": "later", "sequence": 2, "status": status, "session_id": session})
    assert_rejected(inputs, "latest_delivery_attempt_not_submitted")


@pytest.mark.parametrize("sequence", [None, True, "2", 0, -1, 1])
def test_ambiguous_attempt_order_is_invalid_evidence(sequence):
    inputs = raw_exchange()
    assert check(inputs)["status"] == "passed"
    attempts = inputs[0]["raw_records"]["attempts"]
    attempts.append({**deepcopy(attempts[0]), "id": "later", "sequence": sequence})
    assert_rejected(inputs, "delivery_attempt_sequence_invalid")


def test_latest_success_is_selected_without_erasing_unsuccessful_history():
    inputs = raw_exchange()
    assert check(inputs)["status"] == "passed"
    attempts = inputs[0]["raw_records"]["attempts"]
    attempts.append({**deepcopy(attempts[0]), "id": "later", "sequence": 2})
    assert check(inputs)["cases"][0]["evidence"]["attempt_ids"][0] == "later"
    attempts[0]["status"] = "unknown"
    assert check(inputs)["status"] == "passed"


def test_unfinished_work_requires_reply_before_completion():
    inputs = raw_exchange("work_exchange")
    assert check(inputs)["status"] == "passed"
    inputs[0]["provider_events"][1]["at"] = "2026-10-24T00:00:07+00:00"
    assert_rejected(inputs, "reply_after_work_completion")


def test_busy_tool_requires_submission_during_hold_but_can_reply_after_release():
    inputs = raw_exchange("busy")
    result = check(inputs)
    assert result["status"] == "passed", result
    assert result["cases"][0]["evidence"]["timing"]["reply_before_hold_finished"] is False
    inputs[0]["provider_events"][1]["at"] = "2026-10-24T00:00:01+00:00"
    assert_rejected(inputs, "submission_outside_tool_hold")


@pytest.mark.parametrize("recipient", [None, "different-recipient"])
def test_later_attempt_with_invalid_recipient_cannot_disappear(recipient):
    inputs = raw_exchange()
    assert check(inputs)["status"] == "passed"
    attempts = inputs[0]["raw_records"]["attempts"]
    attempts.append({**deepcopy(attempts[0]), "id": "later", "sequence": 2, "recipient": recipient, "status": "unknown"})
    assert_rejected(inputs, "delivery_attempt_recipient_mismatch")


@pytest.mark.parametrize("side", ["native", "requested", "completed", "persisted"])
def test_record_result_disagreement_is_not_hidden(side):
    inputs = raw_exchange()
    assert check(inputs)["status"] == "passed"
    raw = inputs[0]["raw_records"]
    if side == "persisted":
        raw["evidence"][0]["result"]["verification"]["memory"] = "wrong"
    else:
        record = next(o for o in raw["observations"] if o["id"].startswith(side)
                      and o["observation"]["action"] == "record_result")
        record["observation"]["arguments"]["result"]["verification"]["memory"] = "wrong"
    assert_rejected(inputs, "sender_receipt_missing" if side in {"completed", "persisted"} else "native_call_missing_or_misbound")


def test_injected_work_id_cannot_redirect_executed_callback():
    inputs = raw_exchange()
    assert check(inputs)["status"] == "passed"
    for record in inputs[0]["raw_records"]["observations"]:
        if record["observation"].get("phase") == "requested":
            record["observation"]["arguments"]["work_id"] = "other-work"
    assert_rejected(inputs, "callback_work_binding")


def test_explicit_null_native_argument_cannot_be_erased():
    inputs = raw_exchange()
    assert check(inputs)["status"] == "passed"
    inputs[0]["raw_records"]["observations"][0]["observation"]["arguments"]["material"] = None
    assert_rejected(inputs, "native_call_missing_or_misbound")


def test_equal_work_reply_and_completion_timestamps_do_not_prove_before():
    inputs = raw_exchange("work_exchange")
    assert check(inputs)["status"] == "passed"
    inputs[0]["provider_events"][1]["at"] = inputs[0]["raw_records"]["messages"][1]["created_at"]
    assert_rejected(inputs, "reply_after_work_completion")


@pytest.mark.parametrize("kind", ["messages", "evidence", "observations", "attempts", "sessions"])
def test_duplicate_raw_record_identity_cannot_overwrite_counterevidence(kind):
    inputs = raw_exchange()
    assert check(inputs)["status"] == "passed"
    rows = inputs[0]["raw_records"][kind]
    rows.insert(0, {**deepcopy(rows[0]), "counterevidence": True})
    assert_rejected(inputs, "duplicate_or_missing_raw_record_identity")


def test_conflicting_native_work_observation_is_not_hidden_by_good_one():
    inputs = raw_exchange()
    assert check(inputs)["status"] == "passed"
    observations = inputs[0]["raw_records"]["observations"]
    observations.append({**deepcopy(observations[0]), "id": "conflicting-native", "work_id": "other"})
    assert_rejected(inputs, "native_call_missing_or_misbound")


def test_consistent_callback_work_redirect_cannot_override_retained_session():
    inputs = raw_exchange()
    assert check(inputs)["status"] == "passed"
    raw = inputs[0]["raw_records"]
    for record in raw["observations"]:
        record["work_id"] = "other"
        if "work_id" in record["observation"]["arguments"]:
            record["observation"]["arguments"]["work_id"] = "other"
    for record in raw["messages"] + raw["evidence"]:
        record["work_id"] = "other"
    assert_rejected(inputs, "callback_work_binding")


def test_persisted_receipt_actor_cannot_disagree_with_native_callback():
    inputs = raw_exchange()
    assert check(inputs)["status"] == "passed"
    saved = inputs[0]["raw_records"]["evidence"][0]
    saved.update(created_by="other", source={"type": "caller", "actor": "other"})
    assert_rejected(inputs, "persisted_evidence_actor_mismatch")


@pytest.mark.parametrize("before,after", [(0, False), (1, True), (0, 0.0)])
def test_native_argument_comparison_preserves_json_types(before, after):
    inputs = raw_exchange()
    raw = inputs[0]["raw_records"]
    for record in raw["observations"]:
        if record["observation"]["call_id"] == "call-0":
            record["observation"]["arguments"]["body"]["material"] = before
    raw["messages"][0]["body"]["material"] = before
    assert check(inputs)["status"] == "passed"
    raw["observations"][0]["observation"]["arguments"]["body"]["material"] = after
    assert_rejected(inputs, "native_call_missing_or_misbound")


def test_observed_timestamp_order_uses_timezone_offsets():
    inputs = raw_exchange("work_exchange")
    assert check(inputs)["status"] == "passed"
    inputs[0]["provider_events"][0]["at"] = "2026-10-23T23:59:59-12:00"
    assert_rejected(inputs, "submission_outside_unfinished_work")


def test_explicit_native_work_scope_is_preserved_without_forcing_session_default():
    inputs = raw_exchange()
    raw = inputs[0]["raw_records"]
    for record in raw["observations"]:
        record["observation"]["arguments"]["work_id"] = "explicit-work"
    for record in raw["messages"] + raw["evidence"]:
        record["work_id"] = "explicit-work"
    assert check(inputs)["status"] == "passed"


@pytest.mark.parametrize("fault", ["missing-record-id", "nonrecord", "naive-time", "invalid-time"])
def test_malformed_raw_evidence_has_no_pass(fault):
    inputs = raw_exchange()
    assert check(inputs)["status"] == "passed"
    raw = inputs[0]["raw_records"]
    if fault == "missing-record-id":
        del raw["messages"][0]["id"]
    elif fault == "nonrecord":
        raw["messages"][0] = None
    else:
        raw["messages"][0]["created_at"] = "2026-10-24T00:00:03" if fault == "naive-time" else "invalid"
    result = check(inputs)
    assert result["status"] == "incomplete"
    assert all(case["status"] == "invalid_evidence" for case in result["cases"])
