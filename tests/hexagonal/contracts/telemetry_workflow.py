"""Independent public-episode requirements without infrastructure imports."""


def public_episode_contract(subject):
    work = subject.create_work()
    first = subject.invoke_mcp(work)
    assert first["invocation"]["result"]["arguments"]["value"] == subject.tool_value
    assert subject.replay_cli(work) == first
    assert subject.effect_names() == ["project.inspect"]
    events = subject.events(work)
    completed = [row for row in events if row["event_type"] == "application.completed"
                 and subject.payload(row).get("action") == "invoke"]
    assert completed, "The real MCP invocation must retain a semantic result"
    assert any(subject.payload(row)["result"]["operation_id"] == first["operation_id"] for row in completed)
    processes = [row for row in events if row["event_type"] in {"process.request", "process.response"}
                 and row["context"].get("operation_id") == first["operation_id"]]
    assert {row["event_type"] for row in processes} == {"process.request", "process.response"}, \
        "The actual configured subprocess request and response must reach the composed corpus"
    subject.require_actions("telemetry_snapshot", "telemetry_verify", "telemetry_restore")
    snapshot = subject.call("telemetry_snapshot", {"destination": "episode"})
    assert snapshot["status"] == "complete"
    verified = subject.call("telemetry_verify", {"destination": "episode"})
    assert verified["bundle_id"] == snapshot["bundle_id"]
    restored = subject.call("telemetry_restore", {"bundle": "episode", "destination": "restored"})
    assert restored["status"] == "complete"
    assert restored["bundle_id"] == snapshot["bundle_id"]
    original = subject.bundle_rows("episode")
    recovered = subject.bundle_rows("restored")
    assert recovered == original
    assert {row["event_id"] for row in completed} <= {row["event_id"] for row in original}
    assert subject.bundle_artifacts_verified("restored")
    assert not subject.has_bundle_sidecars("restored")


def denied_bundle_contract(subject, operation):
    work = subject.create_work()
    subject.require_actions("telemetry_snapshot", "telemetry_verify", "telemetry_restore")
    subject.call("telemetry_snapshot", {"destination": "private"})
    subject.deny_work(work)
    arguments = ({"bundle": "private", "destination": "denied-restored"}
                 if operation == "telemetry_restore" else
                 {"destination": "denied-snapshot" if operation == "telemetry_snapshot" else "private"})
    failure = subject.failed_call(operation, arguments, actor="observer")
    assert failure == "denied", "Current whole-closure authority must reject the public bundle operation"
    if operation != "telemetry_verify":
        name = "denied-restored" if operation == "telemetry_restore" else "denied-snapshot"
        assert not subject.bundle_database_exists(name)
    assert subject.call("telemetry_verify", {"destination": "private"})["status"] == "complete"


def changed_authority_contract(subject, operation):
    work = subject.create_work()
    subject.require_actions("telemetry_snapshot", "telemetry_verify", "telemetry_restore")
    subject.call("telemetry_snapshot", {"destination": "private"})
    arguments = ({"bundle": "private", "destination": "changed-restored"}
                 if operation == "telemetry_restore" else
                 {"destination": "changed-snapshot" if operation == "telemetry_snapshot" else "private"})
    with subject.revoke_after_authorized_artifact_read(work) as state:
        failure = subject.failed_call(operation, arguments, actor="observer")
    assert state["revoked"], "The independent race fixture must actually revoke after initial authorization"
    assert failure == "denied", "A stale initial authorization must not release the bundle or its metadata"
    if operation != "telemetry_verify":
        name = "changed-restored" if operation == "telemetry_restore" else "changed-snapshot"
        assert not subject.bundle_database_exists(name)


def normalized_provider_usage_contract(subject):
    work, assessment, request_count = subject.assess_actual_provider()
    assert assessment["assessment"]["conclusion"]["outcome"] == "ready"
    assert request_count == 1
    usage = subject.call("telemetry_usage", {"work_id": work, "event_type": "evaluator.usage"})
    assert usage.get("quantities", {}).get("input_tokens") == 0, \
        "Actual provider-reported zero must reach the public normalized quantity"
    assert usage["quantities"]["output_tokens"] == 2
    assert usage["quantities"]["cache_read_tokens"] is None
    assert usage["cost_status"] == "unknown"
    assert usage["source_event_ids"]


def comparison_authority_contract(subject):
    baseline = subject.create_work()
    candidate = subject.create_work()
    subject.report_trial(baseline, "baseline-trial", "passed")
    subject.report_trial(candidate, "candidate-trial", "failed")
    args = {"baseline_work_id": baseline, "candidate_work_id": candidate, "event_types": ["trial.result"]}
    subject.require_actions("telemetry_compare")
    comparison = subject.call("telemetry_compare", args)
    assert comparison["status"] == "compared"
    assert comparison["baseline"]["passed"] == 1
    assert comparison["candidate"]["failed"] == 1
    assert comparison["population_basis"]["outcome_basis"] == "reported"
    subject.deny_work(baseline)
    restricted = subject.call("telemetry_compare", args, actor="observer")
    assert restricted["status"] == "inconclusive"
    assert restricted["baseline"]["observed_outcomes"] == 0
    assert "baseline-trial" not in restricted["baseline"]["source_event_ids"]


def comparison_changed_authority_contract(subject):
    baseline = subject.create_work()
    candidate = subject.create_work()
    subject.report_trial(baseline, "baseline-private-trial", "passed")
    subject.report_trial(candidate, "candidate-trial", "failed")
    arguments = {"baseline_work_id": baseline, "candidate_work_id": candidate,
                 "event_types": ["trial.result"]}
    subject.require_actions("telemetry_compare")
    with subject.revoke_baseline_after_candidate_query(baseline, candidate) as state:
        failure = subject.failed_call("telemetry_compare", arguments, actor="observer")
    assert state["revoked"], "The fixture must revoke baseline authority after both cohort queries"
    assert subject.failed_call("telemetry_events", {"work_id": baseline}, actor="observer") == "denied"
    assert failure == "denied", "Comparison must reauthorize the complete cohort union before returning private data"


def mixed_bundle_changed_authority_contract(subject, operation):
    baseline = subject.create_work()
    subject.report_trial(baseline, "baseline-private-trial", "passed")
    candidate = subject.create_work()
    subject.report_trial(candidate, "candidate-trial", "failed")
    subject.call("telemetry_snapshot", {"destination": "before-revocation"})
    destination = {"telemetry_snapshot": "mixed-snapshot", "telemetry_verify": "before-revocation",
                   "telemetry_restore": "mixed-restored"}[operation]
    arguments = ({"bundle": "before-revocation", "destination": destination}
                 if operation == "telemetry_restore" else {"destination": destination})
    with subject.revoke_earlier_work_during_final_bundle_pass(baseline, candidate, operation, destination) as state:
        failure = subject.failed_call(operation, arguments, actor="observer")
    assert state["verified"], "The fixture must complete actual final-destination artifact verification"
    assert state["baseline_authorized"], "Earlier-work authority must initially pass in the final closure walk"
    assert state["revoked"], "Later-work authorization must revoke the already authorized earlier work"
    assert subject.failed_call("telemetry_events", {"work_id": baseline}, actor="observer") == "denied"
    assert failure == "denied", "Public bundle completion must require current authority for its entire mixed closure"
    if operation != "telemetry_verify":
        assert not subject.bundle_database_exists(destination)


def mixed_read_changed_authority_contract(subject, operation):
    baseline = subject.create_work()
    subject.report_usage(baseline, "baseline-private-usage", 997)
    candidate = subject.create_work()
    subject.report_usage(candidate, "candidate-usage", 2)
    arguments = subject.mixed_read_arguments(operation, baseline, candidate)
    with subject.revoke_earlier_work_during_public_read(baseline, candidate, operation) as state:
        failure = subject.failed_call(operation, arguments, actor="observer")
    assert state["baseline_authorized"], "The earlier private work must actually pass authorization before revocation"
    assert state["revoked"], "A later work authorization must actually revoke the earlier work"
    assert subject.failed_call("telemetry_events", {"work_id": baseline}, actor="observer") == "denied"
    assert failure in {"denied", "conflict"}, "A mixed read must not return earlier private membership after authority changes"


def blocked_export_contract(subject):
    result, endpoint_reached, completed_before_release = subject.work_while_export_blocked()
    assert endpoint_reached, "The configured operational transport must actually be exercised"
    assert completed_before_release, "Blocked optional export must not hold the observed action"
    assert result["work"]["goal"] == "complete despite blocked optional transport"
    assert subject.events(result["work"]["id"])
