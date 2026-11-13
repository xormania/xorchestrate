"""Telemetry uses ordinary provider addressing without granting data authority."""

import json

OPERATIONS = {"status", "events", "payload", "usage", "compare", "snapshot", "verify", "restore", "report"}


def provider_discovery_contract(subject):
    descriptors = subject.descriptors()
    assert {row["name"] for row in descriptors} == {
        f"{subject.provider_name}/{name}" for name in OPERATIONS
    }, "TELEMETRY_PROVIDER_MUST_USE_ORDINARY_DISCOVERY"
    assert all(row["provider"] == subject.provider_name for row in descriptors)
    assert all(row["input_schema"]["additionalProperties"] is False for row in descriptors)


def shared_capture_contract(subject):
    work = subject.create_work("Shared service rich content")
    subject.record_result(work, {"answer": "shared retained answer"})
    before = subject.collector_count()
    arguments = {"work_id": work, "limit": 1000}
    ordinary = subject.query("events", arguments)
    legacy = subject.app.call("telemetry_events", arguments)
    assert ordinary == legacy, "TELEMETRY_PROVIDER_AND_LEGACY_ACTION_MUST_SHARE_SOURCE"
    assert "shared retained answer" in json.dumps(ordinary)
    assert subject.collector_count() == before, "TELEMETRY_READS_MUST_NOT_OBSERVE_THEIR_OWN_PAYLOADS"
    retained = subject.retained()
    assert not retained["operation"] and not retained["invocation"], \
        "TELEMETRY_QUERIES_MUST_NOT_CREATE_EFFECT_RECORDS"


def report_provenance_contract(subject):
    work = subject.create_work("Reported observation scope")
    payload = {"content": "reported private payload canary", "basis": "observed",
               "actor": "forged-runtime", "operation_id": "forged-operation"}
    arguments = {"name": "caller measurement", "payload": payload, "work_id": work}
    first = subject.invoke("report", arguments, actor="reporter", key="report-once")
    assert first["result"]["status"] == "recorded"
    rows = subject.events(event_type="telemetry.reported", actor="reporter")
    assert len(rows) == 1, "TELEMETRY_REPORT_MUST_APPEND_ONCE"
    row = rows[0]
    assert row["basis"] == "reported", "TELEMETRY_CALLER_REPORT_MUST_NOT_BECOME_RUNTIME_OBSERVATION"
    assert row["context"]["actor"] == "reporter"
    assert row["context"]["work_id"] == work
    assert row["context"].get("operation_id") != "forged-operation"
    assert row["payload"] == {"name": "caller measurement", "payload": payload}
    repeated = subject.invoke("report", arguments, actor="reporter", key="report-once")
    assert repeated == {"operation_id": first["operation_id"], "status": "completed", "result_retained": False}
    assert len(subject.events(event_type="telemetry.reported", actor="reporter")) == 1
    retained = subject.retained()
    assert "reported private payload canary" not in json.dumps(retained), \
        "TELEMETRY_REPORT_PAYLOAD_MUST_STAY_OUTSIDE_ORCHESTRATION_JOURNAL"
    assert not retained["evidence"], "TELEMETRY_REPORT_MUST_NOT_CREATE_ACCEPTANCE_EVIDENCE"


def report_resource_authority_contract(subject):
    work = subject.create_work("Private report target")
    subject.deny(["telemetry_report"], work_id=work)
    before = subject.collector_count()
    code = subject.refusal("report", {"name": "blocked", "payload": {"value": 1}, "work_id": work}, key="denied-report")
    assert code in {"denied", "operation_failed"}, "TELEMETRY_REPORT_MUST_AUTHORIZE_ITS_DECLARED_SCOPE"
    assert subject.collector_count() == before
    assert not subject.events(event_type="telemetry.reported")


def report_session_scope_contract(subject):
    actual = subject.create_work("Actual session work")
    claimed = subject.create_work("Conflicting claimed work")
    session = subject.session_for(actual)
    result = subject.invoke("report", {"name": "session report", "payload": {"value": 1},
        "work_id": claimed, "session_id": session}, key="session-report")
    assert result["result"]["status"] == "recorded"
    row, = subject.events(event_type="telemetry.reported")
    assert row["context"]["session_id"] == session
    assert row["context"]["work_id"] == actual, "TELEMETRY_REPORT_SCOPE_MUST_USE_RESOLVED_SESSION_WORK"


def protected_bundle_contract(subject):
    work = subject.create_work("Private bundle source")
    subject.capture("fixture.bundle", {"content": "protected bundle payload"}, {"work_id": work}, event_id="bundle-source")
    source = str(subject.bundle_path("source"))
    first = subject.invoke("snapshot", {"destination": source}, key="snapshot-once")
    receipt = first["result"]
    manifest = subject.read_manifest("source")
    assert receipt["status"] == manifest["status"] == "complete"
    assert receipt["event_count"] == manifest["source"]["event_count"]
    assert subject.query("verify", {"destination": source}) == receipt
    subject.restrict_work(work)
    for name, arguments in (
        ("snapshot", {"destination": str(subject.bundle_path("denied-snapshot"))}),
        ("verify", {"destination": source}),
        ("restore", {"bundle": source, "destination": str(subject.bundle_path("denied-restore"))}),
    ):
        code = subject.refusal(name, arguments, key="denied-" + name if name != "verify" else None)
        assert code is not None, "TELEMETRY_PROVIDER_BUNDLE_MUST_AUTHORIZE_COMPLETE_CLOSURE"
    assert not subject.bundle_exists("denied-snapshot")
    assert not subject.bundle_exists("denied-restore")
    # A repeated effect returns only the non-sensitive completion fact, even
    # after its caller loses authority to the saved source closure.
    subject.deny(["telemetry*"], actor="owner", work_id=work)
    repeated = subject.invoke("snapshot", {"destination": source}, key="snapshot-once")
    assert repeated == {"operation_id": first["operation_id"], "status": "completed", "result_retained": False}
    assert "manifest_reference" not in json.dumps(repeated)


def restored_bundle_contract(subject):
    subject.capture("fixture.portable", {"content": "portable bytes"}, event_id="portable-source")
    source = str(subject.bundle_path("source"))
    destination = str(subject.bundle_path("restored"))
    subject.invoke("snapshot", {"destination": source}, key="snapshot")
    first = subject.invoke("restore", {"bundle": source, "destination": destination}, key="restore")
    assert first["result"]["status"] == "complete"
    assert subject.read_manifest("source") == subject.read_manifest("restored")
    repeated = subject.invoke("restore", {"bundle": source, "destination": destination}, key="restore")
    assert repeated == {"operation_id": first["operation_id"], "status": "completed", "result_retained": False}


def fresh_query_contract(subject):
    subject.capture("fixture.fresh", {"content": "first"}, event_id="fresh-one")
    first = subject.invoke("events", {"event_type": "fixture.fresh"}, key="read-key")
    subject.capture("fixture.fresh", {"content": "second"}, event_id="fresh-two")
    second = subject.invoke("events", {"event_type": "fixture.fresh"}, key="read-key")
    assert len(first["result"]["events"]) == 1
    assert len(second["result"]["events"]) == 2, "TELEMETRY_QUERY_KEY_MUST_NOT_REPLAY_STALE_CONTENT"
