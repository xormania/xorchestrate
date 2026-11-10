"""Independent expectations for journal diagnostics and effect recovery."""

import json

PRIVATE_MARKERS = ("PRIVATE_ERROR_MESSAGE", "PRIVATE_ERROR_CODE", "PRIVATE_ERROR_DETAILS", "PRIVATE_ERROR_TYPE")
ERROR_VARIANTS = ("json", "non_json", "malformed", "bad_outcome")


def assert_private_absent(value):
    try:
        encoded = json.dumps(value, allow_nan=False)
    except (TypeError, ValueError):
        raise AssertionError("EFFECT_ERROR_JSON_ENVELOPE") from None
    assert not any(marker in encoded for marker in PRIVATE_MARKERS), "EFFECT_ERROR_PRIVATE_ATTRIBUTES"


def assert_error_retention(subject, variant):
    subject.fail(variant)
    first = subject.invoke()
    assert_private_absent(first)
    assert first["error"]["details"]["outcome"] == "unknown", "EFFECT_ERROR_OUTCOME_RETAINED"
    operation_id = first["error"]["details"]["operation_id"]
    operation = subject.call("get_operation", {"operation_id": operation_id})["operation"]
    assert operation["status"] == "unknown", "EFFECT_ERROR_DURABLE_OUTCOME"
    assert_private_absent(operation)
    assert_private_absent(subject.raw(operation_id))
    replay = subject.invoke()
    assert_private_absent(replay)
    assert replay["error"]["code"] == "operation_unresolved"
    assert subject.effect_calls == 1, "EFFECT_ERROR_NO_AUTOMATIC_REPEAT"
    return operation_id


def assert_completed_metadata(subject, variant):
    subject.fail(variant, completed=True)
    first = subject.invoke()
    assert_private_absent(first)
    assert first["error"]["code"] == "result_unapplied", "EFFECT_ERROR_KNOWN_COMPLETION"
    assert first["error"]["details"]["outcome"] == "completed"
    operation_id = first["error"]["details"]["operation_id"]
    operation = subject.call("get_operation", {"operation_id": operation_id})["operation"]
    assert operation["status"] == "completed_unapplied", "EFFECT_ERROR_COMPLETION_DURABLE"
    assert "external_result" not in operation, "EFFECT_ERROR_UNTRUSTED_COMPLETION_METADATA"
    assert_private_absent(subject.raw(operation_id))
    replay = subject.invoke()
    assert_private_absent(replay)
    assert replay["error"]["details"]["outcome"] == "completed"
    resolved = subject.call("resolve_operation", {"operation_id": operation_id,
        "expected_version": operation["version"], "outcome": "completed",
        "result": {"verified": True}, "reason": "Inspected the external result"})
    assert resolved["operation"]["status"] == "completed"
    assert subject.invoke()["invocation"]["result"] == {"verified": True}
    assert subject.effect_calls == 1, "EFFECT_ERROR_COMPLETION_NOT_REPEATED"


def assert_typed_completion(subject, field):
    payload = {"delivery": {"target": "branch", "head": "checked"}, "status": "published"}
    subject.complete(payload, field)
    first = subject.invoke()
    assert first["error"]["details"]["outcome"] == "completed"
    assert first["error"]["details"].get("external_result") == payload, "EFFECT_ERROR_TYPED_RESULT_COMPATIBILITY"
    operation_id = first["error"]["details"]["operation_id"]
    operation = subject.call("get_operation", {"operation_id": operation_id})["operation"]
    assert operation["external_result"] == payload, "EFFECT_ERROR_TYPED_RESULT_COMPATIBILITY"
    subject.call("resolve_operation", {"operation_id": operation_id,
        "expected_version": operation["version"], "outcome": "completed", "reason": "Apply retained completion"})
    assert subject.invoke()["invocation"]["result"] == payload
    assert subject.effect_calls == 1


def assert_typed_failure(subject):
    subject.reject()
    first = subject.invoke()
    assert first["error"]["message"] == "Remote branch changed; inspect before retrying.", "EFFECT_ERROR_TYPED_DIAGNOSTICS"
    assert first["error"]["details"]["outcome"] == "failed"
    operation_id = first["error"]["details"]["operation_id"]
    operation = subject.call("get_operation", {"operation_id": operation_id})["operation"]
    assert operation["error"] == {"message": "Remote branch changed; inspect before retrying.",
        "code": "remote_changed", "details": {"outcome": "failed", "retry_safe": False}}
    assert subject.invoke()["error"]["details"]["error"] == operation["error"]


def assert_legacy_projection(subject):
    operation_id = subject.seed_legacy()
    raw = subject.raw(operation_id)
    assert any(marker in json.dumps(raw) for marker in PRIVATE_MARKERS), "control: seeded legacy bytes exist"
    current = subject.call("get_operation", {"operation_id": operation_id})["operation"]
    assert_private_absent(current)
    assert current["legacy_error_retained"] is True, "EFFECT_ERROR_PROJECTION_IS_NOT_PURGE"
    assert_private_absent(subject.invoke())
    assert_private_absent(subject.legacy_history(operation_id))
    assert subject.raw(operation_id) == raw, "EFFECT_ERROR_LEGACY_BYTES_PRESERVED"
    resolved = subject.call("resolve_operation", {"operation_id": operation_id,
        "expected_version": current["version"], "outcome": "failed", "reason": "External rejection verified"})
    assert_private_absent(resolved)
    assert resolved["operation"]["status"] == "failed"
    # Seed again: completing an existing row must also project its old error.
    operation_id = subject.seed_legacy(key="legacy-complete")
    current = subject.call("get_operation", {"operation_id": operation_id})["operation"]
    resolved = subject.call("resolve_operation", {"operation_id": operation_id,
        "expected_version": current["version"], "outcome": "completed", "result": {"verified": True},
        "reason": "Completion independently verified"})
    assert_private_absent(resolved)
    assert "PRIVATE_ERROR_DETAILS" in json.dumps(subject.raw(operation_id)), "EFFECT_ERROR_NO_IMPLICIT_PURGE"


def assert_provider_payload_opaque(subject):
    payload = {"error": {"message": "provider text", "code": "provider_code", "details": {"custom": True}},
        "operation": {"error": {"message": "provider nested text"}}, "application_error": "provider application field"}
    subject.succeed(payload)
    result = subject.invoke()
    assert result["invocation"]["result"] == payload, "EFFECT_ERROR_PROVIDER_RESULT_OPAQUE"
    assert subject.invoke() == result
    operation = subject.call("get_operation", {"operation_id": result["operation_id"]})["operation"]
    assert operation["external_result"] == operation["result"]["invocation"]["result"] == payload
