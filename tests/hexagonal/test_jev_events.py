"""Event-specific advisory probes retain evidence without repeating validation."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from scripts.ci import jev_events
from scripts.ci.jev_events import collect_event, collect_failure, relay_event, run_probe
from tests.hexagonal.contracts.jev_events import (
    labels_are_not_provider_evidence,
    no_provider_work,
    unchanged_input,
)
from tests.hexagonal.support.jev_efficiency import http_choices
from tests.hexagonal.support.jev_events import (
    HEAD,
    ROOT,
    GitHubEventFixture,
    credential_review_packet,
    event_case,
    policy,
    provider_factory,
    scripted_response,
)


def test_review_probe_sends_complete_source_without_oracle_and_retains_current_identity(tmp_path):
    packet, _ = event_case("review-failure-still-present")
    before = deepcopy(packet)
    with http_choices() as provider:
        provider["reply"] = scripted_response("yes")
        report = run_probe(packet, policy(), cache_dir=tmp_path, evaluator_factory=provider_factory(provider))
        assert report["status"] == "complete", "CURRENT_EVENT_PACKET_IS_ASSESSED"
        assert report["source"] == packet["source"], "EVENT_ASSESSMENT_BINDS_EXACT_HEAD"
        assert len(provider["requests"]) == 1, "REVIEW_EVENT_USES_ONE_REQUEST"
        sent = provider["requests"][0]
        assert packet["evidence"]["source"]["text"] in json.dumps(sent["state"], ensure_ascii=False).replace("\\n", "\n"), "COMPLETE_CHANGED_UNIT_REACHES_PROVIDER"
        labels_are_not_provider_evidence(sent)
        unchanged_input(before, packet)


@pytest.mark.parametrize("kind", ("assignment", "configured_value"))
def test_review_redaction_preserves_verified_identity_and_reuses_protected_inference(tmp_path, monkeypatch, kind):
    credentials = ("fixture-private-value-71386", "fixture-private-value-95174")
    reports = []
    with http_choices() as provider:
        provider["reply"] = scripted_response("yes")
        factory = provider_factory(provider)
        for credential in credentials:
            if kind == "configured_value":
                monkeypatch.setenv("EVENT_REVIEW_SECRET", credential)
            packet, expected_text = credential_review_packet(kind, credential)
            before = deepcopy(packet)
            report = run_probe(packet, policy(), cache_dir=tmp_path, evaluator_factory=factory)
            assert report["status"] == "complete", "REDACTED_VERIFIED_REVIEW_SOURCE_IS_ASSESSED"
            protected = report["packet"]["evidence"]["source"]
            inference_sha = hashlib.sha256(expected_text.encode()).hexdigest()
            assert protected["text"] == expected_text and protected["sha256"] == inference_sha, \
                "PROTECTED_SOURCE_IDENTITY_MATCHES_INFERENCE_TEXT"
            assert report["source_content_identity"] == {
                "path": protected["path"], "source_sha256": before["evidence"]["source"]["sha256"],
                "inference_sha256": inference_sha}, "ORIGINAL_AND_PROTECTED_SOURCE_IDENTITIES_REMAIN_DISTINCT"
            unchanged_input(before, packet)
            reports.append(report)
        assert len(provider["requests"]) == 1, "IDENTICAL_PROTECTED_EVIDENCE_REUSES_INFERENCE"
        assert reports[0]["request_sha256"] == reports[1]["request_sha256"]
        assert reports[0]["source_content_identity"]["source_sha256"] != reports[1]["source_content_identity"]["source_sha256"]
        retained = json.dumps(reports) + json.dumps(provider["requests"])
        retained += "".join(path.read_text() for path in tmp_path.rglob("*") if path.is_file())
        assert all(credential not in retained for credential in credentials), "REVIEW_SECRETS_ABSENT_FROM_PROVIDER_REPORT_AND_CACHE"


@pytest.mark.parametrize("action", ("opened", "ready_for_review"))
def test_healthy_pr_keeps_focused_advice_when_broad_automatic_advice_is_off(tmp_path, action):
    broad_policy = json.loads((ROOT / "contracts/jev-advice.json").read_text())
    assert broad_policy["automatic"] == {"source": False, "post_test": False}
    github = GitHubEventFixture()
    packet = collect_event("pull_request_target", github.event(action), github.api, policy())
    with http_choices() as provider:
        provider["reply"] = scripted_response("yes")
        report = run_probe(packet, policy(), cache_dir=tmp_path, evaluator_factory=provider_factory(provider))
        assert report["status"] == "complete" and len(provider["requests"]) == 1, \
            "FOCUSED_PR_ADVICE_REMAINS_ENABLED_WITH_BROAD_ADVICE_OFF"
        assert report["source"]["head_sha"] == HEAD and report["advisory"] is True


def test_unchanged_event_reuses_inference_but_selected_evidence_invalidates_it(tmp_path):
    packet, _ = event_case("review-failure-still-present")
    with http_choices() as provider:
        provider["reply"] = scripted_response("yes")
        factory = provider_factory(provider)
        first = run_probe(packet, policy(), cache_dir=tmp_path, evaluator_factory=factory)
        repeated = run_probe(deepcopy(packet), policy(), cache_dir=tmp_path, evaluator_factory=factory)
        assert first["status"] == repeated["status"] == "complete"
        assert len(provider["requests"]) == 1, "UNCHANGED_EVENT_REUSES_INFERENCE"
        assert repeated["execution"]["provider_calls"] == 0
        packet["evidence"]["finding"]["text"] = "The receipt argument may be dropped before storing."
        changed = run_probe(packet, policy(), cache_dir=tmp_path, evaluator_factory=factory)
        assert len(provider["requests"]) == 2, "CHANGED_FINDING_INVALIDATES_EVENT_CACHE"
        assert changed["request_sha256"] != first["request_sha256"]


def test_semantic_cache_reuse_is_rebound_to_a_new_current_head(tmp_path):
    packet, _ = event_case("review-failure-still-present")
    with http_choices() as provider:
        provider["reply"] = scripted_response("yes")
        factory = provider_factory(provider)
        original = run_probe(packet, policy(), cache_dir=tmp_path, evaluator_factory=factory)
        packet["source"]["head_sha"] = "c" * 40
        current = run_probe(packet, policy(), cache_dir=tmp_path, evaluator_factory=factory)
        assert len(provider["requests"]) == 1, "UNCHANGED_SEMANTIC_EVIDENCE_CAN_REUSE_ACROSS_HEADS"
        assert current["source"]["head_sha"] == "c" * 40 and original["source"]["head_sha"] == HEAD, "CACHED_ASSESSMENT_REBINDS_WITHOUT_REWRITING_ORIGINAL"


def test_failure_probe_selects_existing_evidence_and_keeps_original_failure(tmp_path):
    packet, expected = event_case("incorrect-count-assertion")
    before = deepcopy(packet)
    with http_choices() as provider:
        provider["reply"] = scripted_response(lambda question, _: expected[
            "failure_kind" if "test_defect" in question["criteria"] else "causal_excerpt"])
        report = run_probe(packet, policy(), cache_dir=tmp_path, evaluator_factory=provider_factory(provider))
        assert report["status"] == "complete" and report["advisory"] is True
        assert {row["id"]: row["outcome"] for row in report["assessments"]} == expected, "FAILURE_CATEGORY_AND_SUPPLIED_EXCERPT_ARE_SEPARATE_RESULTS"
        assert len(provider["requests"]) == 2, "FAILURE_PROBE_HAS_TWO_BOUNDED_RUBRICS"
        assert list(provider["requests"][1]["questions"]["q0"]["criteria"]) == ["e1", "e2", "none"], "EXCERPT_CHOICES_COME_ONLY_FROM_INPUT"
        assert report["execution"]["fresh_usage"] == {"input_tokens": 34, "output_tokens": 6}
        assert all(row["assessment"]["details"]["probabilities"] for row in report["assessments"])
        assert all(provider["requests"][0]["state"] == request["state"] for request in provider["requests"])
        unchanged_input(before, packet)


def test_shared_deadline_preserves_completed_category_without_starting_another_call(tmp_path, monkeypatch):
    packet, _ = event_case("incorrect-count-assertion")
    clock = {"now": 0.0}
    monkeypatch.setattr(jev_events, "time", SimpleNamespace(monotonic=lambda: clock["now"]))
    with http_choices() as provider:
        answer = scripted_response(lambda question, _: "test_defect" if "test_defect" in question["criteria"] else "e2")
        def reply(payload, attempt):
            result = answer(payload, attempt)
            clock["now"] = 10.0
            return result
        provider["reply"] = reply
        result = run_probe(packet, policy(), cache_dir=tmp_path, evaluator_factory=provider_factory(provider))
        assert result["status"] == "partial" and result["reason"] == "deadline", "SHARED_EVENT_DEADLINE_RETAINS_PARTIAL_RESULT"
        assert len(provider["requests"]) == 1, "SHARED_DEADLINE_PREVENTS_SECOND_REQUEST"
        assert [row["id"] for row in result["assessments"]] == ["failure_kind"]


def test_scope_questions_are_batched_and_keep_each_candidate_outcome_separate(tmp_path):
    packet, _ = event_case("scope-direct-requirement")
    with http_choices() as provider:
        provider["reply"] = scripted_response(lambda question, _: "yes" if "candidates[0]" in question["instructions"] else "no")
        report = run_probe(packet, policy(), cache_dir=tmp_path, evaluator_factory=provider_factory(provider))
        assert report["status"] == "complete"
        assert len(provider["requests"]) == 1 and len(provider["requests"][0]["questions"]) == 2, "RELEVANCE_CANDIDATES_SHARE_ONE_REQUEST"
        assert {row["id"]: row["outcome"] for row in report["assessments"]} == {
            "retain-before-success": "yes", "release-tag-format": "no"}, "RELEVANCE_IS_NOT_MAX_COMPOSED_ACROSS_CANDIDATES"
        labels_are_not_provider_evidence(provider["requests"][0])


def test_uncertain_probability_is_visible_without_promoting_high_native_confidence(tmp_path):
    packet, _ = event_case("review-failure-still-present")
    with http_choices() as provider:
        provider["response"] = {"model": "fixture-choice", "answers": {"q0": {
            "type": "choice", "choice": "yes", "confidence": .99,
            "probabilities": {"yes": .6, "no": .3, "unknown": .1}}},
            "usage": {"input_tokens": 17, "output_tokens": 3}}
        report = run_probe(packet, policy(), cache_dir=tmp_path, evaluator_factory=provider_factory(provider))
        assert report["status"] == "complete"
        assert report["assessments"][0]["outcome"] == "unknown", "UNCERTAIN_OPTION_PROBABILITY_REMAINS_UNKNOWN"
        assert report["assessments"][0]["assessment"]["outcome"] == "yes", "ORIGINAL_PROVIDER_JUDGMENT_IS_RETAINED"


@pytest.mark.parametrize("fault", ("incomplete", "oversized", "wrong_digest", "wrong_digest_with_redaction"))
def test_incomplete_or_unbound_source_never_reaches_inference(tmp_path, fault):
    packet, _ = event_case("review-failure-still-present")
    if fault == "wrong_digest_with_redaction":
        packet, _ = credential_review_packet("assignment", "fixture-invalid-source-71386")
    if fault == "incomplete":
        packet["evidence"]["complete"] = False
    elif fault == "oversized":
        packet["evidence"]["source"]["text"] = "# full unit\n" * 200000
        packet["evidence"]["source"]["sha256"] = hashlib.sha256(packet["evidence"]["source"]["text"].encode()).hexdigest()
    else:
        packet["evidence"]["source"]["sha256"] = "f" * 64
    with http_choices() as provider:
        provider["reply"] = scripted_response("yes")
        result = run_probe(packet, policy(), cache_dir=tmp_path, evaluator_factory=provider_factory(provider))
        assert no_provider_work(result, provider), "INCOMPLETE_EVENT_EVIDENCE_IS_NOT_SENT"


def test_provider_failure_stays_advisory_and_is_not_cached(tmp_path):
    packet, _ = event_case("review-failure-still-present")
    before = deepcopy(packet)
    with http_choices() as provider:
        provider["status"] = 400
        provider["response"] = {"error": {"message": "unavailable"}}
        factory = provider_factory(provider)
        unavailable = run_probe(packet, policy(), cache_dir=tmp_path, evaluator_factory=factory)
        assert unavailable["status"] != "complete", "PROVIDER_FAILURE_IS_EXPLICITLY_UNAVAILABLE"
        assert len(provider["requests"]) == 1, "PERMANENT_PROVIDER_ERROR_IS_NOT_RETRIED"
        provider["status"], provider["response"] = 200, None
        provider["reply"] = scripted_response("yes")
        recovered = run_probe(packet, policy(), cache_dir=tmp_path, evaluator_factory=factory)
        assert recovered["status"] == "complete" and len(provider["requests"]) == 2, "UNAVAILABLE_EVENT_IS_NOT_CACHED"
        unchanged_input(before, packet)


def test_event_collector_rejects_a_superseded_head_before_fetching_source():
    github = GitHubEventFixture()
    event = github.event("opened")
    github.pr["head"]["sha"] = "d" * 40
    result = collect_event("pull_request_target", event, github.api, policy())
    assert result.get("status") != "complete" and result.get("kind") is None, "STALE_EVENT_NEVER_BUILDS_PACKET"
    assert not any("/contents/" in path for path in github.calls), "STALE_EVENT_DOES_NOT_FETCH_SOURCE"


def test_review_collector_reads_the_finding_and_complete_source_at_the_observed_head():
    github = GitHubEventFixture()
    packet = collect_event("pull_request_review", github.event("submitted"), github.api, policy())
    assert packet.get("kind") == "review", "SUBMITTED_REVIEW_BUILDS_REVIEW_PACKET"
    assert packet["source"]["head_sha"] == HEAD
    assert packet["evidence"]["source"]["text"] == github.source, "REVIEW_COLLECTOR_RETAINS_COMPLETE_UNIT"
    assert packet["evidence"]["finding"]["id"] == "41"
    assert not any("/actions/runs" in path for path in github.calls), "REVIEW_EVENT_DOES_NOT_LAUNCH_CI"


@pytest.mark.parametrize("case", (
    "owner", "codex", "configured_actor", "untrusted", "forged_payload", "review_substitution",
    "missing_review_user", "missing_review_url", "wrong_review_id", "empty_policy", "invalid_policy", "historical_review",
))
def test_review_command_authorizes_refetched_identity_before_evidence_or_provider_use(tmp_path, monkeypatch, case):
    github = GitHubEventFixture()
    selected_policy = policy()
    selected_policy["review_authorization"] = {"allowed_actor_ids": [127287135, 199175422]}
    actor = 199175422 if case == "codex" else 987654321 if case in {
        "configured_actor", "untrusted", "forged_payload", "review_substitution"} else 127287135
    github.review["user"]["id"] = actor
    relay = github.event("submitted")
    relay["sender"] = {"id": actor}
    source = github.failure_event()
    source["workflow_run"].update(path=".github/workflows/jev-review-events.yml", conclusion="success", actor={"id": actor})
    if case == "configured_actor":
        selected_policy["review_authorization"]["allowed_actor_ids"].append(actor)
    elif case == "forged_payload":
        relay["sender"] = {"id": 127287135}
        relay["review"]["user"] = {"id": 127287135}
        relay["review"]["author_association"] = "OWNER"
    elif case == "review_substitution":
        github.review["user"]["id"] = 127287135
        relay["sender"] = {"id": 127287135}
        relay["review"]["user"] = {"id": 127287135}
    elif case == "missing_review_user":
        github.review.pop("user")
    elif case == "missing_review_url":
        github.review.pop("pull_request_url")
    elif case == "wrong_review_id":
        github.review["id"] = 99
    elif case == "empty_policy":
        selected_policy["review_authorization"]["allowed_actor_ids"] = []
    elif case == "invalid_policy":
        selected_policy["review_authorization"]["allowed_actor_ids"] = "all"
    elif case == "historical_review":
        github.review["commit_id"] = "d" * 40
    event_path, relay_path, policy_path = (tmp_path / name for name in ("event.json", "relay.json", "policy.json"))
    for path, value in ((event_path, source), (relay_path, relay), (policy_path, selected_policy)):
        path.write_text(json.dumps(value))
    output = tmp_path / "assessment"
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(jev_events, "api_bytes", lambda url, *_: json.dumps(
        github.api(url.removeprefix("https://api.github.com"))).encode())
    with http_choices() as provider:
        provider["reply"] = scripted_response("yes")
        real_probe = jev_events.run_probe
        monkeypatch.setattr(jev_events, "run_probe", lambda packet, configured, **options: real_probe(
            packet, configured, **options, evaluator_factory=provider_factory(provider)))
        assert jev_events.main(["--event", str(event_path), "--event-name", "workflow_run", "--relay", str(relay_path),
                                "--policy", str(policy_path), "--output", str(output)]) == 0
        report = json.loads((output / "assessment.json").read_text())
        allowed = case in {"owner", "codex", "configured_actor", "historical_review"}
        if allowed:
            assert report["status"] == "complete" and len(provider["requests"]) == 1, \
                "AUTHORIZED_REVIEWER_RETAINS_FOCUSED_ADVICE"
            assert "/repos/example/project/pulls/12/reviews/51" in github.calls, "REVIEW_AUTHORIZATION_REFETCHES_AUTHORITATIVE_REVIEW"
        else:
            assert no_provider_work(report, provider), "UNAUTHORIZED_OR_UNBOUND_REVIEW_MAKES_ZERO_PROVIDER_CALLS"
            assert not any("/contents/" in path or path.endswith("/files?per_page=100") or "/comments?" in path
                           for path in github.calls), "REVIEW_AUTHORIZATION_PRECEDES_EVIDENCE_READS"


def test_irrelevant_metadata_edits_do_not_collect_or_assess_code():
    github = GitHubEventFixture()
    event = github.event("edited")
    event["changes"] = {"base": {"ref": {"from": "old-target"}}}
    result = collect_event("pull_request_target", event, github.api, policy())
    assert result.get("kind") is None, "NONSEMANTIC_EDIT_HAS_NO_JEV_PACKET"
    assert not any("/contents/" in path for path in github.calls)


@pytest.mark.parametrize("movement", ("stable", "before", "during"))
def test_event_command_checks_freshness_before_spending_and_before_publication(tmp_path, monkeypatch, movement):
    github = GitHubEventFixture()
    event_path, policy_path = tmp_path / "event.json", tmp_path / "policy.json"
    event_path.write_text(json.dumps(github.event("submitted")))
    policy_path.write_text(json.dumps(policy()))
    output = tmp_path / "assessment"
    monkeypatch.setenv("TRUSTED_SHA", "e" * 40)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    def api_bytes(url, *_):
        if movement == "before" and url.endswith("/pulls/12") and any(path.endswith("/pulls/12") for path in github.calls):
            github.pr["head"]["sha"] = "d" * 40
        return json.dumps(github.api(url.removeprefix("https://api.github.com"))).encode()
    monkeypatch.setattr(jev_events, "api_bytes", api_bytes)
    with http_choices() as provider:
        answer = scripted_response("yes")
        def reply(payload, attempt):
            result = answer(payload, attempt)
            if movement == "during":
                github.pr["head"]["sha"] = "d" * 40
            return result
        provider["reply"] = reply
        real_probe = jev_events.run_probe
        monkeypatch.setattr(jev_events, "run_probe", lambda packet, selected_policy, **options: real_probe(
            packet, selected_policy, **options, evaluator_factory=provider_factory(provider)))
        exit_code = jev_events.main(["--event", str(event_path), "--event-name", "pull_request_review",
                                    "--policy", str(policy_path), "--output", str(output)])
        report = json.loads((output / "assessment.json").read_text())
        assert exit_code == 0, "EVENT_ADVICE_DOES_NOT_REPLACE_REQUIRED_VERDICT"
        assert report["trusted_sha"] == "e" * 40 and report["source"]["head_sha"] == HEAD
        if movement == "stable":
            assert report["status"] == "complete" and len(provider["requests"]) == 1
        else:
            assert report["status"] == "stale" and not report.get("assessments"), "STALE_EVENT_CANNOT_PUBLISH_ADVICE"
            assert len(provider["requests"]) == int(movement == "during"), "PRE_CALL_FRESHNESS_AVOIDS_WASTED_INFERENCE"


def test_failed_workflow_reads_its_own_attempt_and_supplies_real_log_windows(tmp_path, monkeypatch):
    github = GitHubEventFixture()
    secret = "fixture-private-credential-98244"
    monkeypatch.setenv("TYPESAFE_API_KEY", secret)
    log = "Setting up\nRunning test_two_entries\nAssertionError: 3 != 2\nBearer " + secret + "\nFAILED test_two_entries\n"
    log_reads = []
    def logs(path):
        log_reads.append(path)
        return log
    packet = collect_failure(github.failure_event(), github.api, logs)
    assert packet["kind"] == "failure", "FAILED_CI_CREATES_FAILURE_PACKET"
    assert packet["evidence"]["source_run"] == {"id": 61, "attempt": 2}
    assert log_reads == ["/repos/example/project/actions/jobs/71/logs"], "ONLY_SELECTED_JOB_LOG_IS_READ"
    assert any("/runs/61/attempts/2/jobs" in path for path in github.calls), "JOB_SELECTION_BINDS_EXACT_ATTEMPT"
    with http_choices() as provider:
        provider["reply"] = scripted_response(lambda question, _: "test_defect" if "test_defect" in question["criteria"] else "excerpt_0")
        report = run_probe(packet, policy(), cache_dir=tmp_path, evaluator_factory=provider_factory(provider))
        assert report["status"] == "complete", "ACTUAL_FAILURE_WINDOWS_REACH_DIAGNOSIS"
        assert "AssertionError: 3 != 2" in json.dumps(provider["requests"]), "CAUSAL_LOG_TEXT_IS_PRESERVED"
        assert secret not in json.dumps(provider["requests"]) and secret not in json.dumps(report), "EVENT_EVIDENCE_REDACTS_CREDENTIALS"


@pytest.mark.parametrize("ineligible", ("healthy", "superseded", "foreign_repository"))
def test_failure_collector_does_not_fetch_logs_for_ineligible_runs(ineligible):
    github = GitHubEventFixture()
    event = github.failure_event()
    if ineligible == "healthy":
        event["workflow_run"]["conclusion"] = "success"
    elif ineligible == "superseded":
        github.pr["head"]["sha"] = "d" * 40
    else:
        event["workflow_run"]["head_repository"]["full_name"] = "outsider/project"
    logs = []
    def read_logs(path):
        logs.append(path)
        return "AssertionError: deliberately wrong expectation\nFAILED known check\n"
    result = collect_failure(event, github.api, read_logs)
    assert result.get("kind") is None and logs == [], "INELIGIBLE_RUN_MAKES_NO_DIAGNOSTIC_READ"


@pytest.mark.parametrize("fault", (None, "repository", "number", "head", "workflow"))
def test_review_relay_is_bound_to_its_producer_pr(fault):
    github = GitHubEventFixture()
    source = github.failure_event()
    source["workflow_run"].update(path=".github/workflows/jev-review-events.yml", conclusion="success")
    relay = github.event("submitted")
    if fault == "repository":
        relay["repository"]["full_name"] = "outsider/project"
    elif fault == "number":
        relay["pull_request"]["number"] = 19
    elif fault == "head":
        relay["pull_request"]["head"]["sha"] = "d" * 40
    elif fault == "workflow":
        source["workflow_run"]["path"] = ".github/workflows/unrelated.yml"
    if fault is None:
        assert relay_event(source, relay) == relay, "VALID_REVIEW_RELAY_PRESERVES_EVENT"
    else:
        rejected = False
        try:
            relay_event(source, relay)
        except ValueError:
            rejected = True
        assert rejected, "REVIEW_RELAY_CANNOT_CHANGE_CANDIDATE_IDENTITY"


def test_event_workflows_keep_credentials_on_trusted_code_and_relays_as_data():
    workflow = json.loads((ROOT / ".github/workflows/jev-events.yml").read_text())
    relay = json.loads((ROOT / ".github/workflows/jev-review-events.yml").read_text())
    allowed = policy().get("review_authorization", {}).get("allowed_actor_ids", [])
    assert {127287135, 199175422} <= set(allowed), "DEFAULT_REVIEW_POLICY_AUTHORIZES_XOR_AND_CODEX_IDENTITIES"
    assert set(workflow["on"]) == {"pull_request_target", "workflow_run"}, "SECRET_CONSUMER_USES_TRUSTED_EVENTS"
    assert relay["on"]["pull_request_review"]["types"] == ["submitted"]
    steps = workflow["jobs"]["advise"]["steps"]
    checkouts = [step for step in steps if step.get("uses", "").startswith("actions/checkout@")]
    assert checkouts and all(step["with"].get("ref") == "master" for step in checkouts), "EVENT_CONSUMER_EXECUTES_ONLY_TRUSTED_BRANCH"
    secret_steps = [step for step in steps if "secrets.TYPESAFE_API_KEY" in json.dumps(step)]
    assert len(secret_steps) == 1 and "scripts.ci.jev_events" in secret_steps[0]["run"]
    assert secret_steps[0]["continue-on-error"] is True
    assert not any("checkout@" in step.get("uses", "") or "run" in step or "secrets." in json.dumps(step)
                   for job in relay["jobs"].values() for step in job["steps"]), "REVIEW_RELAY_NEITHER_EXECUTES_CODE_NOR_READS_SECRETS"
    assert not any(mode == "write" for mode in workflow["permissions"].values()), "EVENT_ADVICE_HAS_NO_REVIEW_OR_MERGE_WRITE_CAPABILITY"
