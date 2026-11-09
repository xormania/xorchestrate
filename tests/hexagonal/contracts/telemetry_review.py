"""Independent disclosure, retained-usage and bounded-read regression contracts."""

import json


def invocation_disclosure_contract(subject, state, selector, boundary):
    event_id, canary, address, work, outcome = subject.invocation(state)
    assert outcome == {"completed": "completed", "rejected": "denied", "missing-provider": "not_found",
        "spoofed-operation": "invalid_arguments"}[state]
    subject.deny_invocation(selector, address)
    assert subject.work_readable(work), "The source restriction must remain distinct from work visibility"
    if boundary == "events":
        rows = subject.events(event_type="application.requested", work_id=work, actor="observer")
        assert canary not in json.dumps(rows), "TELEMETRY_REQUEST_ARGUMENTS_MUST_RESPECT_CURRENT_PROVIDER_CAPABILITY_AUTHORITY"
        assert all(row["event_id"] != event_id for row in rows)
    else:
        assert subject.payload_denied(event_id, actor="observer"), \
            "TELEMETRY_REQUEST_PAYLOAD_MUST_RESPECT_CURRENT_PROVIDER_CAPABILITY_AUTHORITY"


def operation_disclosure_contract(subject, boundary):
    event_id, canary, _address, work, outcome = subject.invocation("completed")
    assert outcome == "completed"
    subject.deny_operation_reads()
    assert subject.work_readable(work), "The operation restriction must remain distinct from work visibility"
    assert subject.operation_denied(event_id), "The fixture must deny direct operation reads"
    if boundary == "events":
        rows = subject.events(event_type="application.requested", work_id=work, actor="observer")
        assert canary not in json.dumps(rows), "TELEMETRY_REQUEST_ARGUMENTS_MUST_RESPECT_CURRENT_OPERATION_READS"
        assert all(row["event_id"] != event_id for row in rows)
    else:
        assert subject.payload_denied(event_id, actor="observer"), \
            "TELEMETRY_REQUEST_PAYLOAD_MUST_RESPECT_CURRENT_OPERATION_READS"


def artifact_usage_contract(subject):
    subject.capture_artifact_usage()
    result = subject.usage(event_type="fixture.retained_usage")
    raw = next((row for row in result["raw_observations"] if row["event_id"] == "retained-usage"), None)
    assert raw is not None and "retained-usage" in result["source_event_ids"], \
        "TELEMETRY_USAGE_MUST_INCLUDE_AUTHORIZED_ARTIFACT_BACKED_OBSERVATIONS"
    assert raw["payload"]["usage"] == {"input_tokens": 0, "output_tokens": None}
    assert raw["payload"]["retained_content"] == "u" * 2048
    assert result["cost"] is None and result["cost_status"] == "unknown"


def byte_limited_status_contract(subject, field):
    latest = subject.populate_byte_limited_status()
    page = subject.event_page(limit=1000)
    assert page["next_after"] < page["cutoff"] and len(page["events"]) < 21, \
        "The independent fixture must actually stop its event page at the byte bound"
    status = subject.status()
    if field == "count":
        assert status["count_complete"] is True
        assert status["event_count"] == status["visible_event_count"] == 21, \
            "TELEMETRY_STATUS_MUST_NOT_LABEL_A_BYTE_TRUNCATED_PREFIX_AS_COMPLETE_COUNT"
    elif field == "latest":
        assert status["last_capture"] == latest["receipt"]["captured_at"], \
            "TELEMETRY_STATUS_LATEST_CAPTURE_MUST_NOT_BE_THE_FIRST_BYTE_PAGE_TAIL"
        assert status["last_capture_complete"] is True
    else:
        assert status["sources"]["late"]["observed"] is True, \
            "TELEMETRY_STATUS_SOURCE_COVERAGE_MUST_INCLUDE_LATER_AUTHORIZED_PAGES"
        assert status["sources"]["late"]["last_event_id"] == "status-latest"
        assert status["source_coverage_complete"] is True


def sparse_filter_contract(subject, resource):
    batches, wanted = subject.sparse_filtered_pages(resource)
    cursor, found = 0, []
    for batch in batches:
        assert 0 < batch["raw_rows"] <= 10000, \
            "TELEMETRY_RESOURCE_FILTER_MUST_NOT_HIDE_UNBOUNDED_REPOSITORY_SCANS"
        page = batch["page"]
        found.extend(page["events"])
        assert page["next_after"] > cursor, "TELEMETRY_SPARSE_FILTER_MUST_ADVANCE_ITS_RAW_CONTINUATION"
        cursor = page["next_after"]
    assert [row["event_id"] for row in found] == ["sparse-20001"]
    assert found[0]["context"][resource] == wanted


def bounded_usage_contract(subject, explicit_budget):
    source = subject.capture_large_usage()
    budget = 65536 if explicit_budget else 262144
    result = subject.bounded_usage(budget if explicit_budget else None)
    if "error" in result:
        assert result["error"] == "invalid_query", "TELEMETRY_USAGE_OVERFLOW_MUST_FAIL_WITH_EXPLICIT_QUERY_ERROR"
    else:
        assert len(json.dumps(result).encode("utf-8")) <= budget, \
            "TELEMETRY_USAGE_MUST_ENFORCE_BYTE_BUDGET_AFTER_PAYLOAD_HYDRATION"
        assert "large-valid-usage" in result["source_event_ids"]
        assert result["raw_observations_omitted"] is True and "raw_observations" not in result
        assert {"event_id": "large-valid-usage", "read_action": "telemetry_payload"} in result["source_references"], \
            "TELEMETRY_USAGE_MUST_NOT_SILENTLY_DROP_A_MEASUREMENT_THAT_EXCEEDS_ITS_VIEW_BUDGET"
    assert subject.complete_payload("large-valid-usage") == source, \
        "TELEMETRY_USAGE_BYTE_BOUND_MUST_PRESERVE_COMPLETE_AUTHORIZED_RAW_MEASUREMENTS"
