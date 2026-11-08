"""Independent correctness oracles for finite public-operation measurements."""

import hashlib
import json
import math
from copy import deepcopy

OPERATIONS = {"telemetry_events", "telemetry_payload", "telemetry_snapshot", "telemetry_verify", "telemetry_restore"}
PASSES = {"first-pass", "repeat-pass"}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def completed_measurements_contract(subject, report):
    samples = report.get("measurements", [])
    assert {row["operation"] for row in samples} == OPERATIONS, \
        "PERFORMANCE_REPORT_MUST_MEASURE_ACTUAL_PUBLIC_READ_OPERATIONS"
    assert {(row["pass"], row["operation"]) for row in samples} == {(phase, operation) for phase in PASSES for operation in OPERATIONS}
    assert len(samples) == 10
    workload = report["workload"]
    assert workload["work_count"] == 2 and workload["payload_bytes"] == 2048
    assert workload["expected_completed_events"] == 4 and workload["expected_corpus_events"] == 8
    calls = subject.completed_calls()
    created = [row for row in calls if row["action"] == "create_work"]
    results = [row for row in calls if row["action"] == "record_result"]
    assert len(created) == len(results) == 2
    for index, row in enumerate(created):
        assert row["arguments"]["goal"] == row["result"]["work"]["goal"] == f"calibration-0-{index}"
        assert results[index]["arguments"] == {"work_id": row["result"]["work"]["id"],
            "result": {"trial": 0, "index": index, "text": "x" * 2048}}
    facts = subject.independent_facts(report)
    assert len(facts["source_rows"]) == 8
    assert report["live_corpus_bytes_after_close"] == facts["live_bytes"]
    event_ids = [row["event_id"] for row in facts["source_rows"]
                 if row["observation"]["event_type"] == "application.completed"]
    assert len(event_ids) == 4
    expected_payload = facts["payload"]
    matching = next(row for row in results if row["arguments"] == expected_payload["arguments"])
    assert expected_payload == {"action": "record_result", "arguments": matching["arguments"], "result": matching["result"]}
    payload_bytes = canonical(expected_payload)
    digest = hashlib.sha256(payload_bytes).hexdigest()
    validation = report["validation"]
    assert validation["expected_payload"] == expected_payload
    assert validation["expected_payload_sha256"] == digest
    assert validation["expected_payload_size_bytes"] == len(payload_bytes)
    assert validation["completed_event_ids"] == event_ids
    for row in samples:
        actual = [call for call in calls if call["action"] == row["operation"] and call.get("pass") == row["pass"]]
        assert row["completed_operations"] == len(actual) > 0, "PERFORMANCE_OPERATION_POPULATION_MUST_COUNT_ACTUAL_SUCCESSFUL_API_CALLS"
        returned = sum(len(canonical(call["result"])) for call in actual)
        assert row["returned_bytes"] == returned
        assert row["byte_basis"] == "canonical-json-public-results"
        assert math.isfinite(row["wall_seconds"]) and row["wall_seconds"] > 0
        assert math.isfinite(row["cpu_seconds"]) and row["cpu_seconds"] >= 0
        assert row["python_tracemalloc_peak_bytes"] >= 0
        assert math.isclose(row["operations_per_second"], len(actual) / row["wall_seconds"], rel_tol=1e-9)
        assert math.isclose(row["returned_bytes_per_second"], returned / row["wall_seconds"], rel_tol=1e-9)
        result = row["result"]
        if row["operation"] == "telemetry_events":
            assert result["event_ids"] == event_ids and result["event_count"] == 4, \
                "PERFORMANCE_QUERY_MUST_COMPLETE_THE_DECLARED_NONEMPTY_EVENT_POPULATION"
            assert result["cutoff"] == 8
        elif row["operation"] == "telemetry_payload":
            assert result == {"event_id": validation["payload_event_id"], "sha256": digest, "size_bytes": len(payload_bytes)}, \
                "PERFORMANCE_PAYLOAD_MUST_MATCH_INDEPENDENT_ORIGINAL_ARGUMENTS_AND_RESULT"
        else:
            assert result["status"] == "complete" and result["event_count"] == 8
            bundle = facts["bundles"][result["manifest_reference"]["path"]]
            manifest = bundle["manifest"]
            assert result["bundle_id"] == manifest["bundle_id"]
            assert result["manifest_reference"]["sha256"] == hashlib.sha256(bundle["manifest_bytes"]).hexdigest()
            assert result["manifest_reference"]["size_bytes"] == len(bundle["manifest_bytes"])
            assert manifest["source"]["event_count"] == 8
            assert manifest["database"]["sha256"] == hashlib.sha256(bundle["database_bytes"]).hexdigest()
            assert bundle["rows"] == facts["source_rows"], "PERFORMANCE_BUNDLE_MUST_RETAIN_THE_COMPLETE_DECLARED_SOURCE"
            assert bundle["artifact_ids"] == facts["artifact_ids"]
            assert bundle["artifacts_valid"] and not bundle["sidecars"]
    for phase in PASSES:
        receipts = {row["operation"]: row["result"] for row in samples if row["pass"] == phase and row["operation"] != "telemetry_events"}
        assert receipts["telemetry_snapshot"]["bundle_id"] == receipts["telemetry_verify"]["bundle_id"] == receipts["telemetry_restore"]["bundle_id"]
        assert receipts["telemetry_snapshot"]["manifest_reference"] == receipts["telemetry_verify"]["manifest_reference"]
        source = facts["bundles"][receipts["telemetry_snapshot"]["manifest_reference"]["path"]]
        restored = facts["bundles"][receipts["telemetry_restore"]["manifest_reference"]["path"]]
        assert restored["manifest_bytes"] == source["manifest_bytes"]
        assert restored["database_bytes"] == source["database_bytes"]


def invalid_work_contract(subject, fault):
    accepted = subject.run_invalid(fault)
    assert subject.fault_injected, "PERFORMANCE_NEGATIVE_CONTROL_MUST_REACH_ITS_ACTUAL_PUBLIC_BOUNDARY"
    assert not accepted, "PERFORMANCE_INVALID_OR_INCOMPLETE_WORK_MUST_NOT_BE_REPORTED_AS_VALID_MEASUREMENT"


def report_mutation_contract(subject, report, mutation):
    altered = deepcopy(report)
    operation = "telemetry_events" if mutation == "query" else "telemetry_payload"
    row = next(item for item in altered["measurements"] if item["operation"] == operation)
    if mutation == "query":
        row["result"]["event_ids"] = []
    elif mutation == "digest":
        row["result"]["sha256"] = "0" * 64
    else:
        row["completed_operations"] += 1
    try:
        completed_measurements_contract(subject, altered)
    except AssertionError:
        return
    raise AssertionError("PERFORMANCE_INDEPENDENT_ORACLE_MUST_REJECT_FALSE_SUCCESS_REPORTS")
