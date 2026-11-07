"""Current authority applies to app-owned callback results, not opaque provider data."""

import json


def _serialized(fixture, values):
    return json.dumps(fixture.expand(values), ensure_ascii=False, sort_keys=True)


def callback_disclosure_contract(fixture, resource, boundary):
    reply = fixture.invoke_provider() if resource == "provider" else fixture.get_private_work()
    expected = reply["invocation"]["result"]["body"] if resource == "provider" else reply["work"]["goal"]
    assert expected == fixture.secret, "CONTROL_ACTUAL_CALLBACK_REPLY_MUST_KEEP_ORIGINAL_RICH_RESULT"
    typed = fixture.owner_typed_results()
    assert typed and fixture.secret in _serialized(fixture, typed), "CONTROL_OWNER_TYPED_CAPTURE_MUST_RETAIN_RICH_RESULT"
    part = fixture.subject.payload(typed[-1]["event_id"], actor="owner")
    owner_payload = json.loads(fixture.subject.payload_bytes(part))
    assert owner_payload["result"] == reply, "CONTROL_OWNER_PUBLIC_PAYLOAD_MUST_RETAIN_EXACT_TYPED_CALLBACK_RESULT"
    if boundary != "events":
        assert any(row.get("payload_storage") for row in typed), "CONTROL_MUST_EXERCISE_ARTIFACT_BACKED_TYPED_PAYLOAD"
    fixture.restrict(provider=resource == "provider")
    visible = fixture.public_events() if boundary == "events" else fixture.readable_payloads(original=boundary == "digest")
    assert fixture.secret not in _serialized(fixture, visible), \
        "CALLBACK_WIRE_DUPLICATE_MUST_NOT_BYPASS_CURRENT_NESTED_RESOURCE_AUTHORIZATION"
    outer = fixture.app.call("get_work", {"work_id": fixture.public}, actor="observer")
    assert outer["work"]["id"] == fixture.public, "CONTROL_OUTER_WORK_MUST_REMAIN_AUTHORIZED"


def opaque_provider_contract(fixture, boundary):
    reply = fixture.invoke_provider(opaque=True)
    assert reply["invocation"]["result"] == fixture.provider_payload, "CONTROL_PROTOCOL_REPLY_MUST_REMAIN_UNCHANGED"
    fixture.restrict()
    visible = fixture.public_events() if boundary == "events" else fixture.readable_payloads()
    text = _serialized(fixture, visible)
    assert fixture.secret in text, "OPAQUE_PROVIDER_CONTENT_MUST_NOT_BECOME_TYPED_RESOURCE_AUTHORITY"
    assert fixture.provider_payload == reply["invocation"]["result"]


def explicit_callback_gap_contract(fixture):
    fixture.get_private_work()
    rows = [row for row in fixture.rows() if row["event_type"] == "process.request"
        and row["payload"].get("direction") == "callback_response"]
    assert len(rows) == 1, "CALLBACK_SUPPRESSION_MUST_BE_A_DURABLE_EXPLICIT_OBSERVATION"
    row = rows[0]
    assert row["completeness"] == "suppressed"
    assert row["reason"] == "callback_response_requires_typed_observation"
    payload = row["payload"]
    assert payload["representation"] == "suppressed"
    assert payload["captured_bytes"] == payload["range_start"] == payload["range_end"] == 0
    assert payload["source_bytes"] == payload["total_bytes"] == fixture.reply_wire_size
    assert not ({"content", "encoding", "digest"} & payload.keys()), "SUPPRESSED_WIRE_MUST_NOT_KEEP_REVERSIBLE_CONTENT"
    assert fixture.secret in _serialized(fixture, fixture.owner_typed_results())


def generic_callback_capture_contract(observed):
    packets, calls, result, expected = observed
    assert calls == [("fixture/callback", {})]
    assert result == {"callback_answered": True}
    assert any(packet.get("id") == "callback" and packet.get("result") == expected for packet in packets), \
        "UNREGISTERED_GENERIC_CALLBACK_MUST_PRESERVE_OPAQUE_WIRE_CAPTURE"

