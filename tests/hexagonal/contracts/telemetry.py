"""Independent corpus and disclosure guarantees for optional telemetry capture."""

import json
from hashlib import sha256


def _record(fixture, event_type, payload, context=None, **identity):
    result = fixture.capture(event_type, payload, context, **identity)
    assert result["status"] == "recorded", "TELEMETRY_SCENARIO_REQUIRES_A_DURABLY_CAPTURED_EVENT"
    return result


def composed_semantic_capture_contract(fixture):
    work = fixture.create_work("Retain the complete chosen work")
    fixture.record_result(work, {"answer": "rich result body", "tokens": {"input": 0}})
    assert "telemetry_events" in fixture.actions(), "TELEMETRY_COMPOSED_QUERY_MUST_BE_EXPOSED"
    rows = fixture.events(work_id=work)
    assert rows, "TELEMETRY_COMPOSED_SEMANTIC_CAPTURE_MUST_RETAIN_EVENTS"
    assert any(row["context"].get("work_id") == work for row in rows)
    assert any(fixture.contains(row["payload"], "rich result body") for row in rows), \
        "TELEMETRY_RICH_WORK_CONTENT_MUST_BE_RETRIEVABLE"


def rich_payload_policy_contract(fixture):
    source = {"content": "permitted full content", "authorization": "Bearer nested-credential",
        "nested": {"api_key": "nested-key"}, "diagnostic": "failure: configured-secret",
        "serialized": '{"password":"serialized-credential","input_tokens":0}',
        "tokens": {"input": 0, "output": None}, "empty": "", "unknown_extension": {"value": 19}}
    result = fixture.capture("fixture.rich", source, event_id="rich-payload")
    assert result["status"] == "recorded"
    row = fixture.events(event_type="fixture.rich")[0]
    body = json.dumps(row["payload"], sort_keys=True)
    for secret in ("nested-credential", "nested-key", "configured-secret", "serialized-credential"):
        assert secret not in body, "TELEMETRY_CREDENTIALS_MUST_NOT_SURVIVE_RETAINED_PAYLOAD"
    assert row["payload"]["content"] == source["content"]
    assert row["payload"]["tokens"] == {"input": 0, "output": None}
    assert row["payload"]["empty"] == ""
    assert "missing" not in row["payload"]
    assert row["payload"]["unknown_extension"] == {"value": 19}
    retained = fixture.payload("rich-payload")
    assert "permitted full content" in retained["content"]
    for secret in ("nested-credential", "nested-key", "configured-secret", "serialized-credential"):
        assert secret not in retained["content"], "TELEMETRY_PAYLOAD_READ_USES_PROTECTED_CONTENT"
    assert source["authorization"] == "Bearer nested-credential", "TELEMETRY_CAPTURE_MUST_NOT_MUTATE_INPUT"


def stable_receipt_contract(fixture):
    payload = {"observation": "same immutable observation", "usage": {"input_tokens": 0}}
    first = fixture.capture("fixture.receipt", payload, event_id="stable-observation")
    assert first["status"] == "recorded"
    fixture.reopen()
    duplicate = fixture.capture("fixture.receipt", payload, event_id="stable-observation")
    assert duplicate["status"] == "duplicate"
    assert duplicate["receipt"] == first["receipt"], "TELEMETRY_REPLAY_MUST_RETURN_ORIGINAL_DURABLE_RECEIPT"
    conflict = fixture.capture("fixture.receipt", {"observation": "changed"}, event_id="stable-observation")
    assert conflict["status"] == "rejected", "TELEMETRY_IMMUTABLE_ID_CONFLICT_MUST_BE_REJECTED"
    next_attempt = fixture.capture("fixture.receipt", payload, event_id="distinct-attempt")
    assert next_attempt["status"] == "recorded"
    assert next_attempt["receipt"]["sequence"] > first["receipt"]["sequence"]
    assert len(fixture.events(event_type="fixture.receipt")) == 2


def bounded_payload_contract(fixture):
    payload = {"text": "full content: λ" * 19, "empty": "", "zero": 0}
    _record(fixture, "fixture.range", payload, event_id="range-payload")
    chunks, offset = [], 0
    while True:
        part = fixture.payload("range-payload", offset=offset, limit=13)
        assert part["offset"] == offset
        chunk = fixture.payload_bytes(part)
        assert len(chunk) <= 13
        chunks.append(chunk)
        if part["next_offset"] is None:
            break
        assert part["next_offset"] > offset, "TELEMETRY_PAYLOAD_RANGE_MUST_PROGRESS"
        offset = part["next_offset"]
    reconstructed = b"".join(chunks)
    assert json.loads(reconstructed) == payload, "TELEMETRY_BOUNDED_PAYLOAD_MUST_REMAIN_COMPLETE"
    assert len(reconstructed) == part["size_bytes"]
    assert sha256(reconstructed).hexdigest() == part["digest"]


def disclosure_contract(fixture, boundary):
    public, private = fixture.create_work("public work"), fixture.create_work("private work")
    _record(fixture, "fixture.public", {"content": "allowed content"}, {"work_id": public}, event_id="public-event")
    _record(fixture, "fixture.private", {"content": "private canary"}, {"work_id": private}, event_id="private-event")
    _record(fixture, "fixture.mixed", fixture.mixed_snapshot(public, private),
        {"work_id": public}, event_id="mixed-event")
    fixture.restrict_work(private)
    if boundary == "events":
        result = fixture.event_page(actor="observer")
    else:
        result = fixture.payload("mixed-event", actor="observer")
    serialized = json.dumps(result, sort_keys=True)
    assert "private canary" not in serialized, "TELEMETRY_NESTED_PROTECTED_WORK_MUST_NOT_DISCLOSE"
    if boundary == "events":
        assert "allowed content" in serialized
        assert "private-event" not in serialized
    assert fixture.payload_denied("private-event", actor="observer"), \
        "TELEMETRY_PAYLOAD_READ_MUST_AUTHORIZE_OWNING_EVENT"


def opaque_payload_contract(fixture):
    public, private = fixture.create_work("public work"), fixture.create_work("private work")
    source = {"history": {"work": {"id": private, "goal": "opaque caller value"}}}
    _record(fixture, "fixture.opaque", source, {"work_id": public}, event_id="opaque-event")
    fixture.restrict_work(private)
    rows = fixture.events(event_type="fixture.opaque", actor="observer")
    assert rows[0]["payload"] == source, "TELEMETRY_MUST_PRESERVE_OPAQUE_CALLER_PAYLOAD_ONTOLOGY"


def raw_usage_contract(fixture):
    _record(fixture, "fixture.usage", {"usage": {"input_tokens": 0, "output_tokens": None},
        "tokens": {"cache_read": 12}}, event_id="raw-usage")
    result = fixture.usage(event_type="fixture.usage")
    sample = next(item for item in result["samples"] if item["event_id"] == "raw-usage")
    quantities = {tuple(item["path"]): item["value"] for item in sample["measurements"]}
    assert quantities[("usage",)] == {"input_tokens": 0, "output_tokens": None}
    assert quantities[("tokens",)] == {"cache_read": 12}
    assert result["cost"] is None
    assert result["cost_status"] == "unknown", "TELEMETRY_UNKNOWN_COST_MUST_NOT_BECOME_ZERO"


def changing_authorization_contract(fixture, boundary):
    work = fixture.create_work("authorization is current")
    _record(fixture, "fixture.change", {"content": "must not cross a stale decision"},
        {"work_id": work}, event_id="changing-policy")
    fixture.change_during_read(work)
    failure = fixture.read_failure(boundary, "changing-policy")
    assert failure == "conflict", "TELEMETRY_CHANGED_AUTHORIZATION_CONTEXT_MUST_REJECT"


def optional_capture_noninterference_contract(fixture, mode, outcome):
    result = fixture.noninterference(mode, outcome)
    assert result["observed"] == result["control"], "TELEMETRY_OPTIONAL_CAPTURE_MUST_PRESERVE_ACTION_OUTCOME"
    assert result["calls"] == 1, "TELEMETRY_CAPTURE_FAILURE_MUST_NOT_REPEAT_EXTERNAL_EFFECT"
    if mode != "disabled":
        assert result["capture_calls"] > 0, "TELEMETRY_FAILURE_CONTROL_MUST_REACH_CAPTURE_BOUNDARY"


def concurrent_context_contract(fixture):
    rows, identities = fixture.concurrent_calls()
    found = {}
    for row in rows:
        payload = row["payload"]
        if row["event_type"] != "application.completed" or payload.get("action") != "invoke":
            continue
        label = payload["arguments"]["arguments"]["nonce"]
        found[label] = row
        expected = identities[label]
        assert row["context"]["work_id"] == expected["work_id"], "TELEMETRY_CONCURRENT_WORK_CONTEXT_MUST_NOT_CROSS_BIND"
        assert row["context"]["actor"] == expected["actor"]
        assert payload["result"]["invocation"]["result"]["released"] == label
    assert set(found) == {"A", "B"}
    assert found["A"]["context"]["attempt_id"] != found["B"]["context"]["attempt_id"]


def composed_native_capture_contract(fixture):
    session, rows = fixture.native_episode()
    native = [row for row in rows if row["event_type"].startswith("native.")]
    assert native, "TELEMETRY_NATIVE_ADAPTER_OBSERVATION_MUST_REACH_COMMON_COLLECTOR"
    for row in native:
        assert row["context"]["session_id"] == session["id"]
        assert row["context"]["native_id"] == session["backend"]["native_id"]
        assert row["context"]["incarnation"] == session["backend"]["incarnation"]
    assert any(fixture.contains(row["payload"], "native rich result") for row in rows), \
        "TELEMETRY_NATIVE_SEMANTIC_CONTENT_MUST_BE_RETAINED"


def configuration_state_contract(fixture):
    disabled, invalid, empty = fixture.configuration_states()
    assert disabled["status"] == "disabled"
    assert invalid["status"] in {"unavailable", "invalid"}
    assert empty["status"] not in {"disabled", "unavailable", "invalid"}
    assert empty["visible_event_count"] == 0


def fixed_cutoff_pagination_contract(fixture):
    for index in range(3):
        _record(fixture, "fixture.page", {"index": index}, event_id=f"page-{index}")
    page = fixture.event_page(event_type="fixture.page", limit=1)
    cutoff = page["cutoff"]
    _record(fixture, "fixture.page", {"index": "late"}, event_id="page-late")
    rows = list(page["events"])
    after = page["next_after"]
    for _ in range(4):
        page = fixture.event_page(event_type="fixture.page", limit=1, after=after, cutoff=cutoff)
        assert page["cutoff"] == cutoff, "TELEMETRY_PAGING_MUST_KEEP_ORIGINAL_SNAPSHOT_CUTOFF"
        rows.extend(page["events"])
        if not page["events"]:
            break
        assert page["next_after"] > after
        after = page["next_after"]
    assert [row["payload"]["index"] for row in rows] == [0, 1, 2], \
        "TELEMETRY_PAGED_SNAPSHOT_MUST_EXCLUDE_CONCURRENT_APPENDS"


def denied_scan_continuation_contract(fixture):
    fixture.prepare_denied_prefix()
    after, found = 0, []
    for _ in range(32):
        page = fixture.event_page(event_type="fixture.scan", limit=1, after=after)
        found.extend(page["events"])
        if found:
            break
        assert page["next_after"] > after, "TELEMETRY_DENIED_PREFIX_MUST_NOT_STALL_CONTINUATION"
        after = page["next_after"]
    assert [row["event_id"] for row in found] == ["allowed-after-denied-prefix"]


def absent_scope_contract(fixture, kind, lifecycle):
    scope = "missing-source" if lifecycle == "missing" else fixture.existing_scope(kind)
    _record(fixture, "fixture.absent", {"secret": "protected missing source canary"},
        {kind + "_id": scope}, event_id="missing-scope")
    if lifecycle == "deleted":
        fixture.delete_scope(kind, scope)
    assert not fixture.events(event_type="fixture.absent"), \
        "TELEMETRY_MISSING_SCOPE_MUST_NOT_BECOME_UNSCOPED_PUBLIC_CONTENT"
    assert fixture.payload_denied("missing-scope", actor="observer")


def precreation_request_disclosure_contract(fixture):
    work = fixture.create_work("private precreation request canary")
    requested = fixture.events(event_type="application.requested")
    original = next(row for row in requested if row["payload"].get("arguments", {}).get("goal") == "private precreation request canary")
    fixture.restrict_work(work)
    visible = fixture.events(event_type="application.requested", actor="observer")
    assert not any(fixture.contains(row["payload"], "private precreation request canary") for row in visible), \
        "TELEMETRY_PRECREATION_REQUEST_MUST_INHERIT_CURRENT_CREATED_WORK_AUTHORIZATION"
    assert fixture.payload_denied(original["event_id"], actor="observer"), \
        "TELEMETRY_PRECREATION_PAYLOAD_MUST_AUTHORIZE_CREATED_ENTITY"


def unresolved_request_disclosure_contract(fixture):
    fixture.reject_early_request("private rejected request canary")
    owner = fixture.events(event_type="application.requested")
    assert any(fixture.contains(row["payload"], "private rejected request canary") for row in owner)
    visible = fixture.events(actor="observer")
    assert not any(fixture.contains(row["payload"], "private rejected request canary") for row in visible), \
        "TELEMETRY_UNRESOLVED_PRIVATE_REQUEST_MUST_NOT_DEFAULT_TO_GLOBAL_DISCLOSURE"


def source_manifest_disclosure_contract(fixture):
    work = fixture.create_work("protected source membership")
    _record(fixture, "fixture.private_source", {"content": "private source data"},
        {"work_id": work}, event_id="private-source-event-canary")
    fixture.restrict_work(work)
    assert not fixture.events(event_type="fixture.private_source", actor="observer")
    status = fixture.status(actor="observer")
    assert "private-source-event-canary" not in json.dumps(status), \
        "TELEMETRY_SOURCE_MANIFEST_MUST_NOT_DISCLOSE_PROTECTED_EVENT_ID"
    source = status["sources"]["fixture"]
    assert source["configured"] is True
    assert source.get("observed", False) is False
    assert source.get("coverage") != "observed", "TELEMETRY_SOURCE_OBSERVATION_COVERAGE_MUST_BE_AUTHORIZED"


def bounded_event_page_contract(fixture, explicit_budget):
    payload = {"text": "large permitted content " * 50000}
    _record(fixture, "fixture.large", payload, event_id="large-retained-event")
    budget = 4096 if explicit_budget else 1048576
    arguments = {"max_bytes": budget} if explicit_budget else {}
    page = fixture.event_page(event_type="fixture.large", limit=1, **arguments)
    response_bytes = len(json.dumps(page, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    assert response_bytes <= budget, "TELEMETRY_EVENT_PAGE_MUST_BOUND_BYTES_AS_WELL_AS_ROW_COUNT"
    row = page["events"][0]
    assert row["event_id"] == "large-retained-event"
    assert row["payload_available"] is True
    assert row["payload"]["read_action"] == "telemetry_payload"
    chunks, offset = [], 0
    for _ in range(64):
        part = fixture.payload(row["event_id"], offset=offset, limit=32768)
        chunks.append(fixture.payload_bytes(part))
        if part["next_offset"] is None:
            break
        assert part["next_offset"] > offset
        offset = part["next_offset"]
    else:
        raise AssertionError("TELEMETRY_LARGE_PAYLOAD_MUST_HAVE_FINITE_CONTINUATION")
    retained = b"".join(chunks)
    assert json.loads(retained) == payload
    assert sha256(retained).hexdigest() == part["digest"]


def opaque_artifact_marker_contract(fixture):
    source = {"_telemetry_payload": {"caller_extension": "opaque literal value"}}
    _record(fixture, "fixture.marker", source, event_id="opaque-marker")
    rows = fixture.events(event_type="fixture.marker")
    assert rows[0]["payload"] == source, "TELEMETRY_OPAQUE_PAYLOAD_MARKER_MUST_REMAIN_LITERAL"
    assert json.loads(fixture.payload("opaque-marker")["content"]) == source


def forged_artifact_reference_contract(fixture):
    public, private = fixture.create_work("public artifact source"), fixture.create_work("private artifact source")
    protected = {"content": "protected forged reference canary" + "x" * 2048}
    _record(fixture, "fixture.protected_blob", protected, {"work_id": private}, event_id="protected-blob")
    ref = fixture.artifact_reference("protected-blob")
    source = {"_telemetry_payload": ref}
    _record(fixture, "fixture.forged_marker", source, {"work_id": public}, event_id="forged-marker")
    fixture.restrict_work(private)
    rows = fixture.events(event_type="fixture.forged_marker", actor="observer")
    assert "protected forged reference canary" not in json.dumps(rows), \
        "TELEMETRY_OPAQUE_MARKER_MUST_NOT_DEREFERENCE_ANOTHER_EVENTS_PROTECTED_ARTIFACT"
    assert rows[0]["payload"] == source
    assert json.loads(fixture.payload("forged-marker", actor="observer")["content"]) == source

