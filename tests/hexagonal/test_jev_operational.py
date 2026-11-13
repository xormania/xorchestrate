"""Independent controls for pinned operational cases and economical live pilots."""

import json
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.architecture import operational as subject
from tests.hexagonal.contracts.jev_operational import comparison_stays_advisory, labels_are_withheld
from tests.hexagonal.support.github_actions import expression
from tests.hexagonal.support.jev_operational import SubjectProvider, declared_disposition, operational_catalog
from tests.hexagonal.support.jev_triage import AtomicProvider


@pytest.fixture
def catalog(tmp_path):
    value, repository = operational_catalog(tmp_path / "repository")
    return value, repository["root"]


def test_pinned_fault_pairs_preserve_static_comparator_without_inventing_confirmation(catalog):
    value, root = catalog
    report = subject.replay(value, root=root)
    comparison_stays_advisory(report)
    assert report["measurement_basis"] == "scripted_fixture_only"
    assert (report["summary"]["cases"], report["summary"]["groups"]) == (4, 2)
    assert report["summary"]["baseline"]["architecture"] == {"pass": 4, "violation": 0, "error": 0}
    assert report["summary"]["baseline"]["historical_codex_findings"] == 2
    assert report["summary"]["known_fault_matches"] == 2
    assert report["summary"]["confirmed_findings"] == 0, "KNOWN_FAULT_MATCH_IS_NOT_CONFIRMATION"
    assert report["summary"]["cost"]["status"] == "unknown" and report["summary"]["reviewer_minutes"] is None
    assert all(row["coverage"]["target_complete"] for row in report["cases"])


def test_heldout_packets_withhold_labels_discovery_and_dispositions(catalog):
    value, root = catalog
    provider = SubjectProvider("concern")
    report = subject.replay(value, root=root, mode="live", split="heldout", provider_fn=provider)
    assert {row["id"] for row in report["cases"]} == {"transport-before", "transport-after"}
    assert len(provider.calls) == 2
    for call in provider.calls:
        labels_are_withheld(call["subjects"])
        labels_are_withheld(call["options"])
    assert report["summary"]["known_fault_matches"] == 1
    assert report["summary"]["confirmed_findings"] == 0
    assert report["summary"]["false_alerts"] == 1


@pytest.mark.parametrize("fault", ("split_crossing", "source_pin", "source_ref", "oracle_requirement"))
def test_case_identity_errors_cannot_reach_provider(catalog, fault):
    value, root = catalog
    value = deepcopy(value)
    if fault == "split_crossing":
        value["cases"][0]["split"] = "heldout"
    elif fault == "source_pin":
        value["cases"][0]["pins"][0]["sha256"] = "f" * 64
    elif fault == "source_ref":
        value["cases"][0]["source_ref"] = "f" * 40
    else:
        value["cases"][0]["oracle"]["findings"][0]["requirements"] = ["unknown-requirement"]
    provider = SubjectProvider()
    with pytest.raises(ValueError):
        subject.replay(value, root=root, mode="live", provider_fn=provider)
    assert provider.calls == []


@pytest.mark.parametrize("fault", ("unknown_subject", "missing_subject", "duplicate_subject", "unknown_outcome"))
def test_unmatched_provider_answers_do_not_clear_or_confirm_operational_cases(catalog, fault):
    value, root = catalog
    provider = SubjectProvider("no_concern")

    def invalid(subjects, options):
        response = provider(subjects, options)
        if fault == "unknown_subject":
            response["assessments"][0]["subject_id"] = "f" * 64
        elif fault == "missing_subject":
            response["assessments"] = []
        elif fault == "duplicate_subject":
            response["assessments"].append(deepcopy(response["assessments"][0]))
        else:
            response["assessments"][0]["outcome"] = "accepted"
        return response

    report = subject.replay(value, root=root, mode="live", provider_fn=invalid)
    assert report["summary"]["misses"] == 0, "UNBOUND_PROVIDER_ANSWER_IS_NOT_A_CLEAR"
    assert report["summary"]["confirmed_findings"] == 0
    assert report["summary"]["errors"] + report["summary"]["unknown"] == 4
    assert report["summary"]["errors"] == 4, "INVALID_POPULATION_IS_AN_EXPLICIT_PROVIDER_ERROR"


def test_cache_replays_keep_historical_usage_out_of_fresh_cost_and_stability(catalog):
    value, root = catalog
    provider = SubjectProvider("no_concern", execution={
        "provider_calls": 0, "cache_status": "hit", "fresh_usage": {"input_tokens": 0, "output_tokens": 0},
        "reused_usage": {"input_tokens": 13, "output_tokens": 5},
    })
    report = subject.replay(value, root=root, mode="live", provider_fn=provider, repeats=2)
    summary = report["summary"]
    assert summary["provider_calls"] == 0 and summary["cache_hits"] == 8
    assert summary["fresh_usage"] == {"input_tokens": 0, "output_tokens": 0}
    assert summary["reused_usage"] == {"input_tokens": 104, "output_tokens": 40}
    assert summary["stability"]["compared_cases"] == 0 and summary["stability"]["cache_replays_excluded"] == 8
    assert summary["cost"]["status"] == "unknown" and summary["reviewer_minutes"] is None


def test_fresh_repeated_outputs_compare_only_matching_source_and_model(catalog):
    value, root = catalog
    calls = {}

    def changing(subjects, options):
        key = subjects[0]["subject_id"]
        calls[key] = calls.get(key, 0) + 1
        outcome = "concern" if calls[key] == 1 else "no_concern"
        return {"status": "complete", "model": "controlled-model", "assessments": [
            {"subject_id": row["subject_id"], "outcome": outcome} for row in subjects],
            "execution": {"provider_calls": 1, "cache_status": "disabled",
                          "fresh_usage": {"input_tokens": 7, "output_tokens": 3}, "reused_usage": None}}

    report = subject.replay(value, root=root, mode="live", provider_fn=changing, repeats=2)
    assert report["summary"]["stability"] == {
        "compared_cases": 4, "changed_cases": 4, "identical_cases": 0, "cache_replays_excluded": 0,
    }
    assert report["summary"]["fresh_usage"] == {"input_tokens": 56, "output_tokens": 24}


def test_missing_target_source_stays_unknown_without_spending_provider_calls(catalog):
    value, root = catalog
    provider = SubjectProvider("concern")
    report = subject.replay(value, root=root, mode="live", provider_fn=provider, options={"max_source_bytes": 1})
    assert provider.calls == [], "OMITTED_SOURCE_MUST_NOT_BE_ASSESSED"
    assert report["summary"]["coverage"] == {"eligible": 0, "ineligible": 4}
    assert report["summary"]["unknown"] == 4 and report["summary"]["confirmed_findings"] == 0


def test_only_bound_live_disposition_counts_declared_confirmation(catalog, tmp_path):
    value, root = catalog
    first = subject.replay(value, root=root, mode="live", provider_fn=SubjectProvider())
    row = next(row for row in first["cases"] if row["id"] == "storage-before")
    ledger, _ = declared_disposition(row, tmp_path)
    report = subject.replay(value, root=root, mode="live", provider_fn=SubjectProvider(), dispositions=ledger)
    assert report["summary"]["known_fault_matches"] == 2
    assert report["summary"]["confirmed_findings"] == 1, "BOUND_DISPOSITION_REQUIRED_FOR_CONFIRMATION"
    assert report["summary"]["observed_review_minutes"] == 2.5
    assert report["summary"]["reviewer_minutes"] is None and report["summary"]["missing_review_times"] == 3
    scripted = subject.replay(value, root=root, dispositions=ledger)
    assert scripted["summary"]["confirmed_findings"] == 0, "SCRIPTED_OUTPUT_CANNOT_BECOME_LIVE_CONFIRMATION"


@pytest.mark.parametrize("fault", ("different_source", "changed_witness", "unmatched_packet"))
def test_disposition_cannot_transfer_confirmation_to_other_inputs(catalog, tmp_path, fault):
    value, root = catalog
    first = subject.replay(value, root=root, mode="live", provider_fn=SubjectProvider())
    row = next(row for row in first["cases"] if row["id"] == "storage-before")
    ledger, path = declared_disposition(row, tmp_path)
    if fault == "different_source":
        ledger["entries"][0]["source_ref"] = "f" * 40
    elif fault == "changed_witness":
        path.write_text(path.read_text() + " ")
    else:
        ledger["entries"][0]["packet_sha256"] = "f" * 64
    if fault == "unmatched_packet":
        report = subject.replay(value, root=root, mode="live", provider_fn=SubjectProvider(), dispositions=ledger)
        assert report["summary"]["confirmed_findings"] == 0
    else:
        with pytest.raises(ValueError):
            subject.replay(value, root=root, mode="live", provider_fn=SubjectProvider(), dispositions=ledger)


@pytest.mark.parametrize("paths,expected", ((None, "full"), ([], "health"), (["README.md"], "health"),
    (["scripts/architecture/atomic.py"], "full"), (["tests/fixtures/jev-operational/catalog.json"], "full"),
    (["xorchestrate/adapters/evaluation.py"], "full"), ([".github/workflows/ci.yml"], "full")))
def test_full_pilot_runs_when_its_inputs_change_and_other_runs_are_health_only(paths, expected):
    assert subject.classify_evaluation_changes(paths)["mode"] == expected, "PILOT_SCOPE_MATCHES_CHANGED_INPUTS"


def test_routine_health_is_one_fresh_control_without_engineering_benefit_claim():
    provider = SubjectProvider("no_concern")
    report = subject.health(mode="live", options={"cache_dir": "/unused/fixture-cache"}, provider_fn=provider)
    assert len(provider.calls) == 1 and len(provider.calls[0]["subjects"]) == 1, "HEALTH_IS_ONE_INTENTIONAL_CONTROL"
    assert "cache_dir" not in provider.calls[0]["options"], "CACHED_ANSWER_IS_NOT_LIVE_HEALTH"
    assert report["measurement_basis"] == "intentional_live_health_control"
    assert report["operational_benefit"] is None and report["expected_control_outcome"] == "no_concern"


def test_retained_report_can_be_adjudicated_without_repeating_provider_work(catalog, tmp_path, monkeypatch):
    value, root = catalog
    report = subject.replay(value, root=root, mode="live", provider_fn=SubjectProvider())
    row = next(row for row in report["cases"] if row["id"] == "storage-before")
    ledger, _ = declared_disposition(row, tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("ADJUDICATION_MUST_NOT_REPEAT_PROVIDER_WORK")

    monkeypatch.setattr(subject, "_live_provider", forbidden)
    updated = subject.adjudicate(report, ledger)
    assert updated["summary"]["confirmed_findings"] == 1
    assert updated["cases"][0]["packet_sha256"] == report["cases"][0]["packet_sha256"]
    assert report["summary"]["confirmed_findings"] == 0, "ADJUDICATION_DOES_NOT_MUTATE_RETAINED_REPORT"


def test_adjudication_rejects_a_changed_retained_question_packet(catalog, tmp_path):
    value, root = catalog
    report = subject.replay(value, root=root, mode="live", provider_fn=SubjectProvider())
    row = next(row for row in report["cases"] if row["id"] == "storage-before")
    ledger, _ = declared_disposition(row, tmp_path)
    row["subjects"][0]["claim"] += " This text was changed after provider execution."
    with pytest.raises(ValueError):
        subject.adjudicate(report, ledger)


def test_live_calibration_is_explicit_default_branch_work_separate_from_source_advice():
    workflow = json.loads((Path(__file__).parents[2] / ".github/workflows/jev.yml").read_text())
    option = workflow["on"]["workflow_dispatch"]["inputs"]["calibration"]
    assert option["type"] == "boolean" and option["default"] is False and option["required"] is True
    job = workflow["jobs"]["advice"]
    for branch, calibration, source_run, expected in (
        ("master", False, "", False), ("master", True, "", True), ("master", False, "101", True),
        ("feature", True, "", False), ("feature", False, "101", False),
    ):
        context = {"github": {"event_name": "workflow_dispatch", "ref_name": branch,
                              "event": {"repository": {"default_branch": "master"}}},
                   "inputs": {"calibration": calibration, "source_run_id": source_run}}
        assert expression("${{ " + job["if"] + " }}", context) is expected, \
            "LIVE_CALIBRATION_REQUIRES_EXPLICIT_DEFAULT_BRANCH_DISPATCH"
    pilots = [step for step in job["steps"] if "--split heldout" in step.get("run", "")]
    assert len(pilots) == 1 and not any("--health" in step.get("run", "") for step in job["steps"])
    pilot = pilots[0]
    assert "steps.gate.outputs.kind == 'calibration'" in pilot["if"], "SOURCE_EVENTS_DO_NOT_RUN_CALIBRATION"
    assert pilot.get("continue-on-error") is True and pilot.get("timeout-minutes", 0) > 0
    assert "--mode live" in pilot["run"] and "--max-seconds 120" in pilot["run"]
    assert pilot["env"]["TYPESAFE_API_KEY"] == "${{ secrets.TYPESAFE_API_KEY || secrets.JEV_API_KEY }}"


def test_live_bridge_forwards_shared_flat_budget_and_retry_controls(monkeypatch):
    observed = []

    def evaluate(subjects, options):
        observed.append(options)
        return [], []

    monkeypatch.setitem(sys.modules, "scripts.architecture.atomic", SimpleNamespace(
        TRANSPORT={"endpoint", "model", "retries"}, evaluate_subjects=evaluate, execution_summary=lambda records: {}))
    monkeypatch.setitem(sys.modules, "scripts.architecture.requests", SimpleNamespace(BUDGET_OPTIONS={
        "max_request_tokens", "max_question_tokens", "bytes_per_token_estimate", "token_headroom"}))
    controls = {"endpoint": "https://fixture.invalid", "model": "fixture", "retries": 2,
                "max_request_tokens": 2400, "max_question_tokens": 1200,
                "bytes_per_token_estimate": 3.0, "token_headroom": 0.25}
    subject._live_provider([], {**controls, "unrelated": True})
    assert observed == [controls], "LIVE_BRIDGE_PRESERVES_SHARED_REQUEST_CONTROLS"


def test_repeated_trials_do_not_multiply_unique_disposition_findings(catalog, tmp_path):
    value, root = catalog
    initial = subject.replay(value, root=root, mode="live", provider_fn=SubjectProvider())
    row = next(row for row in initial["cases"] if row["id"] == "storage-before")
    ledger, _ = declared_disposition(row, tmp_path)
    report = subject.replay(value, root=root, mode="live", provider_fn=SubjectProvider(), repeats=2, dispositions=ledger)
    assert report["summary"]["attempts"] == 8
    assert report["summary"]["confirmed_findings"] == 1, "REPEATED_TRIAL_IS_NOT_A_NEW_FINDING"
    assert report["summary"]["known_fault_matches"] == 2
    assert report["summary"]["observed_review_minutes"] == 2.5


@pytest.mark.parametrize("entrypoint", ("replay", "health"))
def test_live_entrypoints_preserve_configured_support_question_limit(catalog, monkeypatch, entrypoint):
    from scripts.architecture import atomic

    provider = AtomicProvider()
    evaluate = atomic.evaluate_subjects
    observed = []

    def bounded_evaluator(subjects, options):
        observed.append(deepcopy(options))
        return evaluate(subjects, options, evaluator_factory=provider)

    monkeypatch.setattr(atomic, "evaluate_subjects", bounded_evaluator)
    options = {"endpoint": "https://fixture.invalid", "model": "fixture", "max_support_evidence": 2}
    if entrypoint == "health":
        report = subject.health(mode="live", options=options)
        diagnostics = [report["diagnostics"]]
    else:
        value, root = catalog
        report = subject.replay(value, root=root, mode="live", split="heldout", options=options)
        diagnostics = [row["diagnostics"] for row in report["cases"]]

    assert observed and all(item.get("max_support_evidence") == 2 for item in observed), "LIVE_SUPPORT_LIMIT_REACHES_ATOMIC_EVALUATOR"
    assert all(item["status"] == "complete" for item in diagnostics)
    assessments = [assessment for item in diagnostics for assessment in item["assessments"]]
    assert assessments and all(len(item["support_scored_ids"]) == 2 for item in assessments)
    support_questions = [question for call in provider.calls for question in call["questions"].values()
                         if "concretely support this subject's claim" in question]
    assert len(support_questions) == 2 * len(assessments), "CONFIGURED_SUPPORT_QUESTION_COUNT_IS_RETAINED"
