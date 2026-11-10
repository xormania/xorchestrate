"""Qualified PR reuse preserves current validation through metadata event bursts."""

from copy import deepcopy
from itertools import permutations

import pytest

from scripts.ci import reuse as subject
from scripts.ci.reuse import compatible_context, run_title, unstarted_metadata
from tests.hexagonal.contracts.ci_reuse import (
    IDENTITY_CHANGES,
    MATERIAL_STATES,
    RUNTIME_CHANGES,
    assert_execute,
    assert_metadata_burst,
    assert_reused,
    replace_path,
)
from tests.hexagonal.support.ci_reuse import (
    TEST_LANE,
    TMUX_LANE,
    CurrentPRObservations,
    ReuseEvidence,
    candidate_context,
    current_pull_request,
    metadata_title,
)
from tests.hexagonal.support.ci_reuse_api import ProducerAPI


@pytest.fixture
def evidence():
    return ReuseEvidence()


def test_same_candidate_reuse_retains_original_producer_and_detached_evidence(evidence):
    run = evidence.add_run()
    before = deepcopy(evidence.bundles)
    result = evidence.select([run])
    assert_reused(result, run["id"])
    assert result["original_context"]["producer"]["run_id"] == 101
    assert result["current_context"]["producer"]["run_id"] == 301
    assert result["references"] == before[101]["references"]
    result["lanes"][TEST_LANE]["runtime"]["python"] = "changed by consumer"
    result["references"][0]["artifact_id"] = -1
    assert evidence.bundles == before, "CI_REUSE_EVIDENCE_IMMUTABLE"


def test_observation_action_run_and_attempt_do_not_change_candidate_identity(evidence):
    original = candidate_context(101, action="synchronize")
    original["producer"]["run_attempt"] = 2
    assert compatible_context(evidence.current, original), "CI_REUSE_OBSERVATIONS_ARE_NOT_INPUTS"


@pytest.mark.parametrize("path", IDENTITY_CHANGES)
def test_changed_identity_or_validation_input_executes(evidence, path):
    original = candidate_context(101)
    old = original[path[0]][path[1]]
    changed = replace_path(original, path, old + 1 if isinstance(old, int) else "f" * len(old))
    run = evidence.add_run(context=changed)
    assert_execute(evidence.select([run]))


@pytest.mark.parametrize("field", ("source", "base"))
def test_changed_source_or_tested_base_executes_even_with_internally_consistent_parents(evidence, field):
    original = candidate_context(101)
    if field == "source":
        original["source_sha"] = original["producer"]["check_sha"] = "f" * 40
        original["checkout"]["parents"][1] = "f" * 40
    else:
        original["tested_base_sha"] = original["checkout"]["parents"][0] = "f" * 40
    run = evidence.add_run(context=original)
    assert_execute(evidence.select([run]))


@pytest.mark.parametrize("status,conclusion", MATERIAL_STATES)
def test_newer_material_attempt_cannot_be_hidden_by_older_green(evidence, status, conclusion):
    green = evidence.add_run(101, 1)
    newer = evidence.add_run(201, 2, status=status, conclusion=conclusion)
    # Completion timestamp is not attempt order: older slow completion is irrelevant.
    green["updated_at"] = "2099-01-01T00:00:00Z"
    newer["updated_at"] = "2000-01-01T00:00:00Z"
    result = evidence.select([green, newer])
    assert_execute(result)
    assert result["reason"] == "newer_material_attempt", "CI_REUSE_NEWEST_MATERIAL_ATTEMPT"
    assert evidence.inspected == [201], "CI_REUSE_NO_SEARCH_PAST_FAILURE"


@pytest.mark.parametrize("conclusion", ("failure", "success"))
def test_latest_attempt_start_wins_when_an_older_run_is_rerun(evidence, conclusion):
    newer_run = evidence.add_run(201, 2, started_at="2026-11-10T12:00:02Z")
    older_rerun = evidence.add_run(101, 1, attempt=2, conclusion=conclusion,
        started_at="2026-11-10T12:00:03Z")
    # Updated/completion time must not turn attempt order back into run order.
    newer_run["updated_at"] = "2026-11-10T12:00:05Z"
    older_rerun["updated_at"] = "2026-11-10T12:00:04Z"
    result = evidence.select([older_rerun, newer_run])
    if conclusion == "failure":
        assert_execute(result)
        assert result["reason"] == "newer_material_attempt", "CI_REUSE_RERUN_FAILURE_REMAINS_VISIBLE"
    else:
        assert_reused(result, 101)
        assert result["producer"]["run_attempt"] == 2
    assert evidence.inspected == [101], "CI_REUSE_ATTEMPT_START_ORDER"


@pytest.mark.parametrize("timestamp", (None, "", "invalid", "2026-11-10T12:00:00"))
def test_unknown_latest_attempt_order_executes_instead_of_guessing(evidence, timestamp):
    run = evidence.add_run()
    run["attempt_started_at"] = timestamp
    assert_execute(evidence.select([run]))


def test_attempt_start_offsets_are_ordered_by_time_instead_of_text(evidence):
    green = evidence.add_run(201, 2, started_at="2026-11-10T11:30:00Z")
    failure = evidence.add_run(101, 1, conclusion="failure", started_at="2026-11-10T07:00:00-05:00")
    result = evidence.select([green, failure])
    assert_execute(result)
    assert result["reason"] == "newer_material_attempt", "CI_REUSE_ATTEMPT_TIME_OFFSETS"


@pytest.mark.parametrize("change", ("failed", "attempt", "id"))
def test_native_metadata_supersedes_cached_history_during_inspection(evidence, change):
    green = evidence.add_run(101, 1)
    cached = evidence.add_run(201, 2)
    observed = deepcopy(cached)
    observed[{"failed": "conclusion", "attempt": "run_attempt", "id": "id"}[change]] = {
        "failed": "failure", "attempt": 2, "id": 202}[change]
    evidence.bundles[201]["run"] = observed
    assert_execute(evidence.select([green, cached]))


@pytest.mark.parametrize("action", ("edited", "ready_for_review"))
def test_metadata_replacement_burst_preserves_eligible_behavior_run(evidence, action):
    assert_metadata_burst(evidence, action)


@pytest.mark.parametrize("order", tuple(permutations(range(3))))
def test_delayed_old_source_event_cannot_hide_current_candidate_after_finite_burst(evidence, order):
    current_green = evidence.add_run(101, 1)
    metadata = evidence.add_run(201, 2, conclusion="cancelled", action="edited", jobs=[])
    old = candidate_context(202)
    old["source_sha"] = old["producer"]["check_sha"] = old["checkout"]["parents"][1] = "f" * 40
    delayed_old_source = evidence.add_run(202, 3, conclusion="failure", context=old)
    runs = [current_green, metadata, delayed_old_source]
    result = evidence.select([runs[index] for index in order])
    assert_reused(result, 101)


@pytest.mark.parametrize("change", ("started_job", "unknown_jobs", "user_title", "source_action", "other_base"))
def test_cancelled_metadata_is_neutral_only_with_positive_unstarted_proof(evidence, change):
    green = evidence.add_run(101, 1)
    cancelled = evidence.add_run(201, 2, conclusion="cancelled", action="edited", jobs=[])
    bundle = evidence.bundles[201]
    if change == "started_job":
        bundle["jobs"] = [{"name": "Application tests", "status": "completed", "conclusion": "cancelled"}]
    elif change == "unknown_jobs":
        bundle.pop("jobs")
    elif change == "user_title":
        cancelled["display_title"] = "CI: arbitrary PR title"
    elif change == "source_action":
        cancelled["display_title"] = metadata_title(evidence.current, "synchronize")
    else:
        cancelled["display_title"] = cancelled["display_title"].rsplit(":", 1)[0] + ":" + "f" * 40
    assert_execute(evidence.select([green, cancelled]))
    assert evidence.inspected == [201], "CI_REUSE_CANCELLED_BEHAVIOR_IS_MATERIAL"


def test_metadata_title_is_workflow_generated_and_contains_no_pr_prose(evidence):
    expected = metadata_title(evidence.current, "edited")
    assert run_title(evidence.current, "edited") == expected
    run = evidence.add_run(201, 2, conclusion="cancelled", action="edited", jobs=[])
    assert unstarted_metadata(run, [], evidence.current)
    assert not unstarted_metadata({**run, "conclusion": "failure"}, [], evidence.current)


def test_unknown_newer_candidate_falls_back_without_searching_past_it(evidence):
    green = evidence.add_run(101, 1)
    newer = evidence.add_run(201, 2, conclusion="failure")
    evidence.bundles[201].pop("context")
    result = evidence.select([green, newer])
    assert_execute(result)
    assert result["reason"] == "unknown_newer_candidate"
    assert evidence.inspected == [201]


def test_proved_different_candidate_does_not_veto_matching_prior_execution(evidence):
    green = evidence.add_run(101, 1)
    other = candidate_context(201)
    other["tested_base_sha"] = other["checkout"]["parents"][0] = "f" * 40
    newer = evidence.add_run(201, 2, conclusion="failure", context=other)
    result = evidence.select([green, newer])
    assert_reused(result, 101)
    assert result["history"][0]["classification"] == "different_inputs"


@pytest.mark.parametrize("path", RUNTIME_CHANGES)
def test_changed_relevant_runtime_input_requires_execution(evidence, path):
    run = evidence.add_run()
    lane = TMUX_LANE if path == ("tmux",) else TEST_LANE
    evidence.runtime[lane] = replace_path(evidence.runtime[lane], path, "different runtime")
    result = evidence.select([run])
    assert_execute(result)
    assert result["reason"] == "runtime_changed_or_unknown"


@pytest.mark.parametrize("path", RUNTIME_CHANGES)
def test_equal_but_unknown_relevant_runtime_inputs_do_not_qualify(evidence, path):
    lane = TMUX_LANE if path == ("tmux",) else TEST_LANE
    evidence.runtime[lane] = replace_path(evidence.runtime[lane], path, None)
    run = evidence.add_run()
    assert_execute(evidence.select([run]))


@pytest.mark.parametrize("change", ("missing_lane", "extra_lane", "unknown_manifest"))
def test_incomplete_or_extra_lane_inventory_is_a_reuse_miss(evidence, change):
    run = evidence.add_run()
    lanes = evidence.bundles[101]["lanes"]
    if change == "missing_lane":
        lanes.pop(TEST_LANE)
    elif change == "extra_lane":
        lanes["unrelated"] = deepcopy(lanes[TEST_LANE])
    else:
        lanes[TEST_LANE] = None
    assert_execute(evidence.select([run]))


@pytest.mark.parametrize("change", ("unqualified", "failed_result", "recursive_flag", "recursive_decision", "recursive_lane"))
def test_successful_run_requires_qualified_original_execution(evidence, change):
    run = evidence.add_run()
    bundle = evidence.bundles[101]
    if change == "unqualified":
        bundle["qualified"] = False
    elif change == "failed_result":
        bundle["result"]["status"] = "failed"
    elif change == "recursive_flag":
        bundle["result"]["reuse"] = {"run_id": 100}
    elif change == "recursive_decision":
        bundle["result"]["decision"] = "reuse"
    else:
        bundle["result"]["evidence_references"][0]["decision"] = "reused"
    assert_execute(evidence.select([run]))


@pytest.mark.parametrize("failure", (OSError("no artifact permission"), ValueError("invalid artifact"), None))
def test_missing_unavailable_or_malformed_evidence_executes(evidence, failure):
    run = evidence.add_run()
    evidence.bundles[101] = failure
    assert_execute(evidence.select([run]))


def test_disable_switch_executes_without_fetching_prior_evidence(evidence):
    run = evidence.add_run()
    result = evidence.select([run], enabled=False)
    assert_execute(result)
    assert result["reason"] == "reuse_disabled"
    assert evidence.inspected == []


@pytest.mark.parametrize("event", ("push", "workflow_dispatch"))
def test_master_or_manual_execution_never_reuses_pr_evidence(evidence, event):
    run = evidence.add_run()
    evidence.current["event"]["name"] = event
    assert_execute(evidence.select([run]))


@pytest.mark.parametrize("field,value", (("path", "different.yml"), ("head_sha", "f" * 40), ("event", "workflow_dispatch")))
def test_unrelated_workflow_source_or_event_is_not_a_material_veto(evidence, field, value):
    green = evidence.add_run(101, 1)
    other = evidence.add_run(201, 2, conclusion="failure")
    other[field] = value
    assert_reused(evidence.select([green, other]), 101)


@pytest.mark.parametrize("draft", (True, False))
def test_current_observation_retains_readiness_state_without_mutating_candidate(evidence, monkeypatch, draft):
    observed = current_pull_request(evidence.current)
    observed["draft"] = draft
    reader = CurrentPRObservations(evidence.current, observed)
    monkeypatch.setattr(subject, "_get", reader)
    before = deepcopy(evidence.current)
    result = subject.observe_current(evidence.current, "opaque-test-token")
    assert result["status"] == "current", "CI_REUSE_CURRENT_PR_OBSERVATION"
    assert result["candidate"]["draft"] is draft
    assert evidence.current == before
    assert reader.requests[0] == (evidence.current["repository"]["full_name"], "/pulls/17")
    assert "opaque-test-token" not in str(result)


@pytest.mark.parametrize("path,replacement", (
    (("head", "sha"), "f" * 40), (("base", "sha"), "f" * 40),
    (("merge_commit_sha",), "f" * 40), (("head", "ref"), "another-source"),
    (("base", "ref"), "another-target"), (("head", "repo", "id"), 987),
    (("number",), 18), (("state",), "closed"),
))
def test_late_candidate_changes_are_observed_as_superseded(evidence, monkeypatch, path, replacement):
    observed = replace_path(current_pull_request(evidence.current), path, replacement)
    monkeypatch.setattr(subject, "_get", CurrentPRObservations(evidence.current, observed))
    result = subject.observe_current(evidence.current, "opaque-test-token")
    assert result["status"] == "superseded", "CI_REUSE_QUEUED_EVENT_CANNOT_ASSERT_CURRENT"


@pytest.mark.parametrize("path,replacement", (
    (("head", "sha"), None), (("base", "sha"), None), (("merge_commit_sha",), None),
    (("head", "ref"), ""), (("base", "ref"), None), (("head", "repo"), {}),
    (("number",), True), (("state",), None), (("draft",), None),
))
def test_incomplete_current_observation_cannot_claim_current_or_known_supersession(evidence, monkeypatch, path, replacement):
    observed = replace_path(current_pull_request(evidence.current), path, replacement)
    monkeypatch.setattr(subject, "_get", CurrentPRObservations(evidence.current, observed))
    with pytest.raises(ValueError):
        subject.observe_current(evidence.current, "opaque-test-token")


def test_failed_current_observation_remains_unavailable_without_retrying(evidence, monkeypatch):
    requests = []

    def unavailable(*args):
        requests.append(True)
        raise OSError("API unavailable")

    monkeypatch.setattr(subject, "_get", unavailable)
    with pytest.raises(OSError, match="API unavailable"):
        subject.observe_current(evidence.current, "opaque-test-token")
    assert requests == [True]


def test_requalification_preserves_the_same_original_execution(evidence, monkeypatch, tmp_path):
    plan, bundle = evidence.prepared()
    monkeypatch.setattr(subject, "prepare", lambda *args, **kwargs: (deepcopy(plan), bundle))
    qualified, retained = subject.qualify(plan, evidence.current, {}, {}, tmp_path, None, token="test-token", env={})
    assert qualified == plan
    assert retained is bundle


@pytest.mark.parametrize("field", ("producer", "references", "original_context", "result_artifact"))
def test_changed_original_evidence_after_skip_planning_fails_qualification(evidence, monkeypatch, tmp_path, field):
    plan, bundle = evidence.prepared()
    changed = deepcopy(plan)
    changed[field] = {"changed": True}
    monkeypatch.setattr(subject, "prepare", lambda *args, **kwargs: (changed, bundle))
    with pytest.raises(ValueError, match="Original producer changed"):
        subject.qualify(plan, evidence.current, {}, {}, tmp_path, None, token="test-token", env={})


@pytest.mark.parametrize("change", ("decision", "bundle"))
def test_evidence_unavailable_after_skipping_is_a_qualification_failure(evidence, monkeypatch, tmp_path, change):
    plan, bundle = evidence.prepared()
    latest = deepcopy(plan)
    if change == "decision":
        latest["decision"] = "execute"
    else:
        bundle = None
    monkeypatch.setattr(subject, "prepare", lambda *args, **kwargs: (latest, bundle))
    with pytest.raises(ValueError, match="no longer qualifies"):
        subject.qualify(plan, evidence.current, {}, {}, tmp_path, None, token="test-token", env={})


@pytest.mark.parametrize("change", ("schema", "schema_float", "schema_bool", "decision", "candidate"))
def test_requalification_requires_a_bound_supported_plan(evidence, monkeypatch, tmp_path, change):
    plan, bundle = evidence.prepared()
    invalid = deepcopy(plan)
    if change.startswith("schema"):
        invalid["schema_version"] = {"schema": 999, "schema_float": 1.0, "schema_bool": True}[change]
    elif change == "decision":
        invalid["decision"] = "execute"
    else:
        invalid["current_context"]["tested_base_sha"] = "f" * 40
    monkeypatch.setattr(subject, "prepare", lambda *args, **kwargs: (deepcopy(plan), bundle))
    with pytest.raises(ValueError, match="no bound reuse decision"):
        subject.qualify(invalid, evidence.current, {}, {}, tmp_path, None, token="test-token", env={})


@pytest.fixture
def producer_api(tmp_path, monkeypatch):
    peer = ProducerAPI(tmp_path / "api")
    monkeypatch.setattr(subject.candidate, "urlopen", peer.urlopen)
    return peer


def test_full_producer_api_fixture_authenticates_original_receipts(producer_api, tmp_path):
    bundle = producer_api.inspect(101, tmp_path / "observed")
    assert bundle["qualified"] is True, "CI_REUSE_FULL_API_HEALTHY_CONTROL"
    assert {row["run_id"] for row in bundle["references"]} == {101}
    assert bundle["result"]["status"] == "passed"


@pytest.mark.parametrize("latest", (201, 202))
def test_native_summary_flattens_to_original_executed_ids_once(producer_api, tmp_path, latest):
    producer_api.summary(201)
    if latest == 202:
        producer_api.summary(202)
    bundle = producer_api.inspect(latest, tmp_path / "observed")
    assert bundle.get("qualified") is True, "CI_REUSE_ONEHOP_HEALTHY_SUMMARY"
    assert bundle["context"]["producer"]["run_id"] == latest
    assert bundle["original_context"]["producer"]["run_id"] == 101, "CI_REUSE_ONEHOP_ORIGINAL_IDENTITY"
    assert {row["run_id"] for row in bundle["references"]} == {101}
    assert {row["decision"] for row in bundle["references"]} == {"executed"}
    assert bundle["summary_ref"]["run_id"] == latest
    assert {row["run_id"] for row in bundle["summary_ref"]["static_references"]} == {latest}
    assert bundle["result"] == producer_api.results[101]
    if latest == 202:
        assert not any("/runs/201" in path for path in producer_api.requests), "CI_REUSE_NO_GROWING_SUMMARY_CHAIN"


def test_repeated_metadata_plan_uses_latest_summary_and_original_full_evidence(producer_api, tmp_path):
    producer_api.summary(201)
    producer_api.summary(202)
    runs = [producer_api.metadata[identifier]["run"] for identifier in (101, 201, 202)]
    plan = subject.select_run(runs, producer_api.current,
        lambda run: producer_api.inspect(run["id"], tmp_path / f"observed-{run['id']}"), runtime=producer_api.runtime)
    assert_reused(plan, 202)
    assert plan["original_context"]["producer"]["run_id"] == 101, "CI_REUSE_FLATTENED_PLAN_ORIGINAL"
    assert {row["run_id"] for row in plan["references"]} == {101}
    assert plan["summary_ref"]["run_id"] == 202


def test_newer_failed_summary_still_vetoes_original_green_evidence(producer_api, tmp_path):
    producer_api.summary(201)
    producer_api.summary(202)
    producer_api.metadata[202]["run"]["conclusion"] = "failure"
    runs = [producer_api.metadata[identifier]["run"] for identifier in (101, 201, 202)]
    plan = subject.select_run(runs, producer_api.current,
        lambda run: producer_api.inspect(run["id"], tmp_path / f"observed-{run['id']}"), runtime=producer_api.runtime)
    assert_execute(plan)
    assert plan["reason"] == "newer_material_attempt", "CI_REUSE_FAILED_SUMMARY_REMAINS_VISIBLE"


@pytest.mark.parametrize("fault", ("original_context", "result_artifact", "heavy_reference", "missing_heavy", "static_reference"))
def test_authenticated_summary_cannot_substitute_original_or_static_proof(producer_api, tmp_path, fault):
    producer_api.summary()
    summary = producer_api.results[201]
    if fault == "original_context":
        summary["reuse"]["original_context"]["checkout"]["tree"] = "f" * 40
    elif fault == "result_artifact":
        summary["reuse"]["result_artifact"]["digest"] = "sha256:" + "0" * 64
    elif fault == "heavy_reference":
        next(row for row in summary["evidence_references"] if row["decision"] == "reused")["artifact_id"] = 99999
    elif fault == "missing_heavy":
        summary["evidence_references"].pop()
    else:
        summary["evidence_references"][0]["run_id"] = 101
    producer_api.rewrite_result(201)
    with pytest.raises(ValueError):
        producer_api.inspect(201, tmp_path / "observed")


def test_summary_reference_cycle_is_rejected(producer_api, tmp_path):
    context = producer_api.summary()
    summary = producer_api.results[201]
    summary["reuse"]["original_context"] = deepcopy(context)
    artifact = next(row for row in producer_api.metadata[201]["artifacts"] if row["name"] == "ci-result-evidence--attempt-1")
    summary["reuse"]["result_artifact"] = {key: artifact[key] for key in ("id", "name", "digest")}
    producer_api.rewrite_result(201)
    with pytest.raises(ValueError):
        producer_api.inspect(201, tmp_path / "observed")


def test_second_summary_hop_is_rejected_before_fetching_a_third_producer(producer_api, tmp_path):
    producer_api.summary(201)
    producer_api.summary(202, original_id=201)
    with pytest.raises(ValueError):
        producer_api.inspect(202, tmp_path / "observed")
    assert not any("/runs/101" in path for path in producer_api.requests), "CI_REUSE_ONEHOP_FETCH_BOUND"


@pytest.mark.parametrize("fault", ("summary_aggregate", "original_aggregate", "summary_static"))
def test_onehop_requires_successful_native_aggregates_and_current_static_receipts(producer_api, tmp_path, fault):
    producer_api.summary()
    if fault.endswith("aggregate"):
        identifier = 201 if fault == "summary_aggregate" else 101
        next(row for row in producer_api.metadata[identifier]["jobs"] if row["name"] == "CI result")["conclusion"] = "failure"
    else:
        from tests.hexagonal.support.ci_receipts import write_json

        path = producer_api.root / "201/receipts/static-checks-python-3.12--attempt-1/static/candidate.json"
        write_json(path, {"commit": "f" * 40, "tree": "d" * 40, "status": ""})
        producer_api.rewrite_lane(201, "static-checks-python-3.12")
    with pytest.raises(ValueError):
        producer_api.inspect(201, tmp_path / "observed")


@pytest.mark.parametrize("producer", ("full", "summary"))
def test_onehop_result_schema_is_a_supported_integer(producer_api, tmp_path, producer):
    if producer == "full":
        producer_api.results[101]["schema_version"] = 2.0
        producer_api.rewrite_result(101)
    producer_api.summary()
    if producer == "summary":
        producer_api.results[201]["schema_version"] = 2.0
        producer_api.rewrite_result(201)
        assert producer_api.inspect(201, tmp_path / "observed").get("qualified") is not True, "CI_REUSE_TYPED_RESULT_SCHEMA"
    else:
        with pytest.raises(ValueError):
            producer_api.inspect(201, tmp_path / "observed")


@pytest.mark.parametrize("prior", ("failure", "unknown"))
@pytest.mark.parametrize("reverse", (False, True))
def test_same_second_attempt_cohort_cannot_hide_failed_or_unknown_peer(evidence, prior, reverse):
    started = "2026-11-10T12:00:03Z"
    rerun = evidence.add_run(101, 1, attempt=2, conclusion="failure" if prior == "failure" else "success", started_at=started)
    green = evidence.add_run(201, 2, started_at=started)
    if prior == "unknown":
        evidence.bundles[101].pop("context")
    runs = [green, rerun] if reverse else [rerun, green]
    result = evidence.select(runs)
    assert result["decision"] == "execute", "CI_REUSE_TIED_ATTEMPT_CANNOT_HIDE_MATERIAL_OR_UNKNOWN_PEER"


def test_unambiguous_later_green_can_reuse_after_an_older_failed_attempt(evidence):
    older = evidence.add_run(101, 1, attempt=2, conclusion="failure", started_at="2026-11-10T12:00:02Z")
    later = evidence.add_run(201, 2, started_at="2026-11-10T12:00:03Z")
    assert_reused(evidence.select([older, later]), 201)


def test_same_second_already_proven_unstarted_metadata_does_not_veto_green(evidence):
    started = "2026-11-10T12:00:03Z"
    green = evidence.add_run(201, 2, started_at=started)
    neutral = evidence.add_run(202, 3, conclusion="cancelled", action="edited", jobs=[], started_at=started)
    result = evidence.select([green, neutral])
    assert_reused(result, 201)
    assert result["history"][0]["classification"] == "superseded_unstarted_metadata"


def test_zero_job_retry_is_not_positive_proof_of_never_executed_metadata(evidence):
    retry = evidence.add_run(201, 2, attempt=2, conclusion="cancelled", action="edited", jobs=[])
    assert not unstarted_metadata(retry, [], evidence.current), "CI_REUSE_LATEST_ZERO_JOBS_DO_NOT_ERASE_ATTEMPT_HISTORY"


@pytest.mark.parametrize("retry", (False, True))
def test_metadata_cancellation_observes_prior_behavior_across_actual_api_attempts(producer_api, tmp_path, retry):
    producer_api.summary(201)
    metadata = producer_api.metadata[201]
    metadata["run"]["conclusion"] = "cancelled"
    if retry:
        metadata["run"]["run_attempt"] = 2
        metadata["jobs"].append({"id": 20150, "name": "Application tests shard 1/2", "run_attempt": 1,
            "status": "completed", "conclusion": "failure"})
    else:
        metadata["jobs"] = []
        metadata["artifacts"] = []
    runs = [producer_api.metadata[101]["run"], metadata["run"]]
    plan = subject.select_run(runs, producer_api.current,
        lambda run: producer_api.inspect(run["id"], tmp_path / f"observed-{run['id']}"), runtime=producer_api.runtime)
    if retry:
        assert plan["decision"] == "execute", "CI_REUSE_CANCELLED_RETRY_CANNOT_ERASE_PRIOR_FAILED_BEHAVIOR"
    else:
        assert_reused(plan, 101)


def test_cached_neutral_attempt_is_rechecked_if_a_retry_appears_during_api_inspection(producer_api, tmp_path):
    producer_api.summary(201)
    metadata = producer_api.metadata[201]
    cached = {**deepcopy(metadata["run"]), "conclusion": "cancelled"}
    metadata["run"].update(run_attempt=2, conclusion="failure")
    metadata["jobs"] = [{"id": 20150, "name": "Application tests shard 1/2", "run_attempt": 2,
        "status": "completed", "conclusion": "failure"}]
    metadata["artifacts"] = []
    # List and latest-job reads still describe attempt one; the authoritative
    # run read observes the retry and must prevent the old neutral shortcut.
    result = subject.select_run([producer_api.metadata[101]["run"], cached], producer_api.current,
        lambda run: subject.inspect_producer(producer_api.current, cached, producer_api.workflow, "fixture-token",
            tmp_path / "observed", None, producer_api.catalog) if run["id"] == 201
            else producer_api.inspect(101, tmp_path / "original"),
        runtime=producer_api.runtime)
    assert result["decision"] == "execute", "CI_REUSE_NEUTRAL_LIST_ATTEMPT_CANNOT_HIDE_NEW_RETRY"


@pytest.mark.parametrize("native_copy", (False, True))
def test_native_aggregate_copy_retains_original_artifact_and_fresh_observation(producer_api, tmp_path, native_copy):
    if native_copy:
        producer_api.native_rerun()
    try:
        bundle = producer_api.inspect(101, tmp_path / "observed")
    except ValueError as error:
        raise AssertionError("CI_REUSE_NATIVE_AGGREGATE_COPY_MUST_KEEP_ORIGINAL_EVIDENCE") from error
    assert bundle.get("qualified") is True, "CI_REUSE_NATIVE_AGGREGATE_COPY_MUST_KEEP_ORIGINAL_EVIDENCE"
    assert bundle["result_artifact"]["name"] == "ci-result-evidence--attempt-1"
    assert {row["run_attempt"] for row in bundle["references"]} == {1}
    assert {row["observation_attempt"] for row in bundle["references"]} == {2 if native_copy else 1}
    assert {row["carried_forward"] for row in bundle["references"]} == {native_copy}
    assert {row["observation_attempt"] for row in bundle["result"]["evidence_references"]} == {1}, "CI_REUSE_STORED_OBSERVATIONS_STAY_HISTORICAL"
    if native_copy:
        assert all(row["observation_job_id"] == row["job_id"] + 1000000 for row in bundle["references"])
        original = next(row for row in producer_api.metadata[101]["jobs"] if row["name"] == "CI result" and row["run_attempt"] == 1)
        assert bundle["result_producer"] == {"job_id": original["id"], "run_attempt": 1,
            "observation_job_id": original["id"] + 1000000, "observation_attempt": 2, "carried_forward": True}


def test_native_summary_aggregate_copy_keeps_its_own_observation_and_full_original(producer_api, tmp_path):
    producer_api.summary(201)
    producer_api.native_rerun(201)
    try:
        bundle = producer_api.inspect(201, tmp_path / "observed")
    except ValueError as error:
        raise AssertionError("CI_REUSE_NATIVE_SUMMARY_COPY_MUST_KEEP_ORIGINAL_EVIDENCE") from error
    assert bundle.get("qualified") is True, "CI_REUSE_NATIVE_SUMMARY_COPY_MUST_KEEP_ORIGINAL_EVIDENCE"
    assert bundle["original_context"]["producer"]["run_id"] == 101
    own = bundle["summary_ref"]["result_producer"]
    assert own == {"job_id": 20199, "run_attempt": 1, "observation_job_id": 1020199,
        "observation_attempt": 2, "carried_forward": True}
    assert bundle["result_producer"]["observation_attempt"] == 1
    assert {row["observation_attempt"] for row in bundle["summary_ref"]["static_references"]} == {2}


@pytest.mark.parametrize("publish", (False, True))
def test_actual_aggregate_retry_requires_its_own_qualified_artifact(producer_api, tmp_path, publish):
    producer_api.native_rerun(actual_aggregate=True, publish_aggregate=publish)
    if not publish:
        with pytest.raises(ValueError):
            producer_api.inspect(101, tmp_path / "observed")
    else:
        bundle = producer_api.inspect(101, tmp_path / "observed")
        assert bundle.get("qualified") is True
        assert bundle["result_artifact"]["name"] == "ci-result-evidence--attempt-2", "CI_REUSE_NEW_AGGREGATE_EXECUTION_NEEDS_NEW_ARTIFACT"


@pytest.mark.parametrize("field,value", (("observation_job_id", 99999999), ("observation_attempt", 2)))
def test_native_copies_cannot_launder_forged_historical_observation_claims(producer_api, tmp_path, field, value):
    producer_api.results[101]["evidence_references"][0][field] = value
    producer_api.rewrite_result(101)
    producer_api.native_rerun()
    with pytest.raises(ValueError):
        producer_api.inspect(101, tmp_path / "observed")


@pytest.mark.parametrize("advanced", (None, "source", "target"))
def test_canonical_branch_refs_bound_current_claim_when_pr_payload_is_unchanged(evidence, monkeypatch, advanced):
    reader = CurrentPRObservations(evidence.current)
    if advanced:
        key = reader.source_key if advanced == "source" else reader.target_key
        reader.refs[key]["object"]["sha"] = "f" * 40
    monkeypatch.setattr(subject, "_get", reader)
    result = subject.observe_current(evidence.current, "fixture-token")
    assert result["status"] == ("superseded" if advanced else "current"), "CI_REUSE_STALE_PR_PAYLOAD_CANNOT_HIDE_CANONICAL_ADVANCEMENT"
    if advanced:
        assert result["candidate"]["head"] == evidence.current["source_sha"]
        assert result["candidate"]["base"] == evidence.current["tested_base_sha"]
        assert result["canonical_refs"][advanced]["sha"] == "f" * 40, "CI_REUSE_CANONICAL_OBSERVATION_DOES_NOT_REWRITE_TESTED_IDENTITY"


@pytest.mark.parametrize("side", ("source", "target"))
@pytest.mark.parametrize("fault", ("missing", "wrong_ref", "not_commit", "invalid_sha"))
def test_unknown_or_malformed_canonical_branch_cannot_claim_current(evidence, monkeypatch, side, fault):
    reader = CurrentPRObservations(evidence.current)
    key = reader.source_key if side == "source" else reader.target_key
    if fault == "missing":
        reader.refs[key] = None
    elif fault == "wrong_ref":
        reader.refs[key]["ref"] = "refs/heads/unrelated"
    elif fault == "not_commit":
        reader.refs[key]["object"]["type"] = "tag"
    else:
        reader.refs[key]["object"]["sha"] = "unknown"
    monkeypatch.setattr(subject, "_get", reader)
    try:
        result = subject.observe_current(evidence.current, "fixture-token")
    except (ValueError, OSError):
        return
    assert result["status"] in {"unknown", "incomplete"}, "CI_REUSE_MISSING_CANONICAL_REF_IS_UNKNOWN"


def test_fork_canonical_source_uses_source_repository_and_quotes_branch_names(evidence, monkeypatch):
    from urllib.parse import quote

    context = deepcopy(evidence.current)
    context["source_repository"] = {"id": 9876, "full_name": "contributor/xorchestrate"}
    context["pr"].update(source_ref="feature/branch#42", target_ref="release/main")
    reader = CurrentPRObservations(context)
    monkeypatch.setattr(subject, "_get", reader)
    result = subject.observe_current(context, "fixture-token")
    assert result["status"] == "current"
    expected = {(context["source_repository"]["full_name"], "/git/ref/heads/" + quote(context["pr"]["source_ref"], safe="/")),
        (context["repository"]["full_name"], "/git/ref/heads/" + quote(context["pr"]["target_ref"], safe="/"))}
    assert expected <= set(reader.requests), "CI_REUSE_CANONICAL_REFS_USE_CORRECT_REPOSITORY_AND_REF"
    assert result["canonical_refs"]["source"]["repository"] == context["source_repository"]


def test_current_observation_http_peer_uses_canonical_ref_endpoints(producer_api):
    result = subject.observe_current(producer_api.current, "fixture-token")
    assert result["status"] == "current"
    assert result["canonical_refs"]["source"]["sha"] == producer_api.current["source_sha"]
    assert result["canonical_refs"]["target"]["sha"] == producer_api.current["tested_base_sha"]
    assert {"/git/ref/heads/feature", "/git/ref/heads/master"} <= set(producer_api.requests)
