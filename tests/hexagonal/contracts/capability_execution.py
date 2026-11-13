"""Independent execution, privacy and replay guarantees for provider policies."""

import json

PRIVATE_MARKERS = ("PRIVATE_PROVIDER_ARGUMENT", "PRIVATE_PROVIDER_RESULT", "PRIVATE_RECONCILIATION_RESULT")


def private_absent(value):
    return not any(marker in json.dumps(value, sort_keys=True) for marker in PRIVATE_MARKERS)


def query_contract(subject, native=False, key="one"):
    if native:
        subject.make_native()
    else:
        subject.make_work()
    subject.observe_during_call()
    before = subject.retained()
    first = subject.invoke(native=native, key=key)
    second = subject.invoke(native=native, key=key, replay_only=native)
    assert first["result"]["value"] == 1 and second["result"]["value"] == 2, "QUERY_MUST_EXECUTE_FRESH"
    assert subject.provider.calls == 2
    after = subject.retained()
    for kind in ("operation", "invocation", "evidence"):
        assert after[kind] == before[kind], "QUERY_MUST_NOT_CREATE_EFFECT_EVIDENCE"
    assert private_absent(after), "QUERY_MUST_NOT_RETAIN_PAYLOADS"
    assert not after["capture"], "QUERY_OBSERVATION_NONE_MUST_SUPPRESS_NESTED_CAPTURE"


def query_authority_contract(subject, denied, native=False):
    if native:
        subject.make_native()
    subject.invoke(native=native)
    subject.deny(denied)
    failure = subject.failure(lambda: subject.invoke(native=native, replay_only=native))
    assert failure["code"] == "denied", "QUERY_REPEAT_MUST_CHECK_CURRENT_AUTHORIZATION"
    assert subject.provider.calls == 1
    assert private_absent(subject.retained())


def query_currentness_contract(subject, target):
    subject.make_work()
    subject.change_during_call(target)
    failure = subject.failure(subject.invoke)
    assert failure["code"] == "conflict", "QUERY_MUST_RECHECK_AUTHORITY_BEFORE_RELEASE"
    assert subject.provider.calls == 1
    assert private_absent(subject.retained())


def receipt_contract(subject, native=False):
    if native:
        subject.make_native()
    else:
        subject.make_work()
    subject.observe_during_call()
    first = subject.invoke(native=native)
    assert first["result"]["payload"] == "PRIVATE_PROVIDER_RESULT", "CONTROL_FIRST_CALL_MUST_RETURN_PROVIDER_RESULT"
    second = subject.invoke(native=native, replay_only=native)
    assert second == {"operation_id": first["operation_id"], "status": "completed", "result_retained": False}, \
        "RECEIPT_REPLAY_MUST_RETURN_ONLY_COMPLETION"
    assert subject.provider.calls == 1, "RECEIPT_EFFECT_MUST_NOT_REPEAT"
    assert private_absent(subject.retained()), "RECEIPT_MUST_NOT_RETAIN_PAYLOADS"
    conflicting = subject.failure(lambda: subject.invoke(native=native, payload="different"))
    assert conflicting["code"] == "conflict", "RECEIPT_MUST_BIND_EXACT_REQUEST"
    assert subject.provider.calls == 1


def receipt_failure_contract(subject, outcome, native=False):
    if native:
        subject.make_native()
    subject.fail_provider(outcome)
    failure = subject.failure(lambda: subject.invoke(native=native))
    expected = {"unknown": "operation_unknown", "failed": "operation_failed", "completed": "result_unapplied"}
    assert failure["code"] == expected[outcome], "RECEIPT_MUST_PRESERVE_EFFECT_KNOWLEDGE"
    operations = [row for row in subject.retained()["operation"] if row["action"] == "invoke"]
    assert len(operations) == 1 and failure["details"].get("operation_id") == operations[0]["id"], \
        "RECEIPT_FAILURE_MUST_IDENTIFY_ITS_RECONCILABLE_OPERATION"
    assert private_absent(failure), "RECEIPT_FAILURE_MUST_NOT_PUBLISH_RETAINED_PAYLOAD"
    assert private_absent(subject.retained()), "RECEIPT_FAILURE_MUST_NOT_RETAIN_PAYLOAD"
    subject.failure(lambda: subject.invoke(native=native, replay_only=native))
    assert subject.provider.calls == 1, "RECEIPT_UNCERTAIN_EFFECT_MUST_NOT_REPEAT"


def receipt_policy_change_contract(subject, native=False):
    if native:
        subject.make_native()
    original = subject.invoke(native=native)
    subject.reconstruct("query")
    if native:
        failure = subject.failure(lambda: subject.invoke(native=True, replay_only=True))
        assert failure["code"] == "conflict", "NATIVE_RECEIPT_MODE_CHANGE_MUST_NOT_REEXECUTE_EFFECT"
    else:
        repeated = subject.invoke()
        assert repeated == {"operation_id": original["operation_id"], "status": "completed", "result_retained": False}, \
            "RETAINED_EFFECT_MUST_NOT_BECOME_A_FRESH_QUERY"
    assert subject.provider.calls == 1
    assert private_absent(subject.retained())


def retained_receipt_new_native_call_contract(subject):
    subject.make_native()
    original = subject.invoke(native=True)
    subject.reconstruct("journaled", "full")
    subject.native_context["call_id"] = "new-native-retry"
    repeated = subject.invoke(native=True)
    assert repeated == {"operation_id": original["operation_id"], "status": "completed", "result_retained": False}
    assert subject.provider.calls == 1, "RETAINED_RECEIPT_RETRY_MUST_NOT_REPEAT_EFFECT"
    retained = subject.retained()
    assert private_absent(retained), "RETAINED_RECEIPT_RETRY_MUST_NOT_COPY_PRIVATE_PAYLOADS"
    assert all(context["actor"] == subject.actor for context in subject.provider.contexts)
    assert {row["created_by"] for row in retained["native_call"]} == {subject.actor}
    assert {row["call_id"] for row in retained["native_call"]} == {"provider-call", "new-native-retry"}


def driver_observation_policy_contract(subject, observation, boundary):
    subject.make_native()
    if boundary == "codex":
        response = subject.codex_callback()
        assert response["result"]["invocation"]["result"]["payload"] == "PRIVATE_PROVIDER_RESULT"
        assert response["observed"] == [("native_tool_call", 0), ("native_tool_result", 1)], \
            "CONTROL_DRIVER_OBSERVATIONS_MUST_BRACKET_ACTUAL_CALLBACK"
        assert response["health"]["pending"] == 0
        assert subject.provider.contexts[0]["actor"] == subject.actor
    else:
        subject.observe_driver_envelopes()
    retained = subject.retained()
    rows = [row for row in retained["native_observation"]
            if row["observation"]["kind"] in {"native_tool_call", "native_tool_result"}]
    assert {row["observation"]["kind"] for row in rows} == {"native_tool_call", "native_tool_result"}
    assert all(row["created_by"] == subject.actor and row["driver"] == "agent" and not row["stale"] for row in rows)
    assert all(row["session_id"] == subject.native_context["session_id"]
               and row["observation"]["native_id"] == subject.native_context["native_id"]
               and row["observation"]["incarnation"] == subject.native_context["incarnation"] for row in rows)
    durable = json.dumps(rows)
    assert "PRIVATE_PROVIDER_ARGUMENT" in durable and "PRIVATE_PROVIDER_RESULT" in durable, \
        "CONTROL_JOURNALED_MODE_MUST_KEEP_NATIVE_CORRECTNESS_EVIDENCE"
    if observation == "none":
        assert not retained["capture"], "DRIVER_OBSERVATION_NONE_MUST_SUPPRESS_OPTIONAL_CAPTURE"
    else:
        encoded = json.dumps(retained["capture"])
        assert "PRIVATE_PROVIDER_ARGUMENT" in encoded and "PRIVATE_PROVIDER_RESULT" in encoded, \
            "CONTROL_FULL_DRIVER_CAPTURE_MUST_KEEP_REQUEST_AND_RESULT"
        assert {event["event_type"] for event in retained["capture"]} >= {"native.native_tool_call", "native.native_tool_result"}


def receipt_settlement_contract(subject):
    subject.fail_settlement()
    failure = subject.failure(subject.invoke)
    assert failure["code"] == "result_unapplied", "RECEIPT_SETTLEMENT_MUST_RETAIN_COMPLETED_OUTCOME"
    assert failure["details"]["outcome"] == "completed"
    assert private_absent(failure), "RECEIPT_SETTLEMENT_MUST_NOT_RELEASE_RETAINED_PAYLOAD"
    assert private_absent(subject.retained()), "RECEIPT_SETTLEMENT_MUST_NOT_RETAIN_PAYLOAD"
    operation_id = failure["details"]["operation_id"]
    operation = subject.operation(operation_id)
    assert operation["status"] == "completed_unapplied"
    subject.resolve(operation_id)
    assert subject.invoke() == {"operation_id": operation_id, "status": "completed", "result_retained": False}
    assert private_absent(subject.retained()), "RECEIPT_RECONCILIATION_MUST_NOT_RETAIN_PAYLOAD"
    assert subject.provider.calls == 1


def native_receipt_reconciliation_contract(subject, outcome):
    subject.make_native()
    subject.fail_settlement()
    failure = subject.failure(subject.invoke)
    assert failure["code"] == "result_unapplied", "CONTROL_RECEIPT_NEEDS_LOCAL_SETTLEMENT"
    operation_id = failure["details"]["operation_id"]
    subject.prepare_native_resolution(operation_id, outcome)
    resolved = subject.failure(subject.resolve_native)
    assert resolved["code"] is None, "NATIVE_RECEIPT_RECONCILIATION_MUST_SETTLE_SUCCESSFULLY"
    assert resolved["result"]["operation"]["status"] == outcome
    settled = subject.operation(operation_id)
    repeated = subject.resolve_native(replay_only=True)
    assert repeated == {"operation_id": operation_id, "status": outcome, "result_retained": False}, \
        "NATIVE_RECONCILIATION_REPLAY_MUST_PRESERVE_SETTLED_OUTCOME"
    assert subject.operation(operation_id) == settled, "NATIVE_RECONCILIATION_MUST_NOT_EXECUTE_TWICE"
    assert subject.provider.calls == 1
    assert private_absent(subject.retained()), "NATIVE_RECONCILIATION_MUST_NOT_RETAIN_PAYLOADS"
    subject.deny("resolve_operation")
    denied = subject.failure(lambda: subject.resolve_native(replay_only=True))
    assert denied["code"] == "denied", "NATIVE_RECONCILIATION_REPLAY_MUST_REAUTHORIZE"


def default_contract(subject):
    subject.provider.annotations = {"readOnlyHint": True, "mode": "query", "observation": "none"}
    first = subject.invoke()
    second = subject.invoke()
    assert first["invocation"]["result"]["payload"] == "PRIVATE_PROVIDER_RESULT"
    assert first == second and subject.provider.calls == 1, "UNTRUSTED_ANNOTATIONS_MUST_NOT_CHANGE_EXECUTION_POLICY"
    assert not private_absent(subject.retained()), "CONTROL_DEFAULT_JOURNAL_RETAINS_PAYLOAD"
