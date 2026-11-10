"""Independent controls for the bounded architecture effectiveness comparison."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.architecture import effectiveness as subject

CORPUS = Path(__file__).parents[1] / "fixtures/architecture-effectiveness.json"


def _row(identifier, *, fault=True, baseline_confirmed=False, outcome="no_concern", status="complete",
         confirmed=False, eligible=True):
    return {"id": identifier, "group": "independent-controls", "expected_fault": fault,
            "comparison_eligible": eligible,
            "baseline": {"outcome": "concern" if baseline_confirmed else "no_concern",
                         "confirmed": baseline_confirmed},
            "jev": {"status": status, "outcome": outcome, "confirmed": confirmed,
                    "unsupported_concerns": int(outcome == "concern" and not confirmed)}}


def test_summary_counts_observed_actions_without_converting_unknowns_to_clears():
    rows = [
        _row("already-detected", baseline_confirmed=True, outcome="concern", confirmed=True),
        _row("additional", outcome="concern", confirmed=True),
        _row("false-clear"),
        _row("abstain", outcome="insufficient_evidence"),
        _row("provider-down", status="unavailable"),
        _row("malformed-provider", status="error"),
        _row("missing-source", outcome="concern", confirmed=True, eligible=False),
        _row("false-alarm", fault=False, outcome="concern"),
        _row("healthy", fault=False),
    ]
    result = subject.summarize(rows)
    assert (result["cases"], result["groups"], result["fault_cases"], result["healthy_cases"]) == (9, 1, 7, 2)
    assert result["comparison_eligible_cases"] == 8
    assert result["baseline"] == {"confirmed_findings": 1, "misses": 6, "false_alarms": 0, "unknown": 0}
    assert result["jev"] == {
        "confirmed_findings": 2, "incremental_confirmed_findings": 1, "misses": 1,
        "false_clears": 1, "false_alarms": 1, "unknown": 2, "unavailable": 1,
        "errors": 1, "unsupported_concerns": 1,
    }
    assert result["reviewer_minutes"] is None and result["cost"] is None


@pytest.mark.parametrize("fault", ("duplicate_id", "empty_population", "invalid_outcome", "confirmed_healthy",
                                  "confirmed_clear", "missing_group", "unknown_eligibility"))
def test_summary_rejects_populations_that_cannot_support_the_claim(fault):
    rows = [_row("case")]
    if fault == "duplicate_id":
        rows.append(deepcopy(rows[0]))
    elif fault == "empty_population":
        rows = []
    elif fault == "invalid_outcome":
        rows[0]["jev"]["outcome"] = "passed"
    elif fault == "confirmed_healthy":
        rows[0].update(expected_fault=False)
        rows[0]["jev"].update(outcome="concern", confirmed=True)
    elif fault == "confirmed_clear":
        rows[0]["jev"]["confirmed"] = True
    elif fault == "missing_group":
        del rows[0]["group"]
    else:
        rows[0]["comparison_eligible"] = None
    with pytest.raises(ValueError):
        subject.summarize(rows)


@pytest.mark.parametrize("fault", ("duplicate_case", "unpaired_group", "escaping_source", "empty_fault_witness",
                                  "oracle_checks_string", "oracle_checks_unknown", "oracle_checks_duplicate",
                                  "case_checks_string", "case_checks_empty", "case_checks_unknown", "case_checks_duplicate",
                                  "deterministic_rules_string"))
def test_corpus_requires_paired_controls_and_bounded_independent_witnesses(tmp_path, fault):
    corpus = json.loads(CORPUS.read_text())
    if fault == "duplicate_case":
        corpus["cases"].append(deepcopy(corpus["cases"][0]))
    elif fault == "unpaired_group":
        corpus["cases"] = [case for case in corpus["cases"] if case["id"] != "imports-healthy"]
    elif fault == "escaping_source":
        corpus["cases"][0]["sources"] = {"../outside.py": "VALUE = 1\n"}
    elif fault == "empty_fault_witness":
        corpus["cases"][1]["oracle"]["findings"] = []
    elif fault == "deterministic_rules_string":
        corpus["cases"][1]["oracle"]["findings"][0]["deterministic_rule_ids"] = "dependencies.external_not_this_rule"
    else:
        invalid = {"string": "semantic_boundaries_not_this_check", "unknown": ["not-a-diagnostic"],
                   "duplicate": ["semantic_boundaries", "semantic_boundaries"], "empty": []}
        if fault.startswith("oracle_"):
            corpus["cases"][1]["oracle"]["findings"][0]["checks"] = invalid[fault.removeprefix("oracle_checks_")]
        else:
            corpus["cases"][1]["checks"] = invalid[fault.removeprefix("case_checks_")]
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps(corpus))
    with pytest.raises(ValueError):
        subject.load_corpus(path)


def test_keyless_pilot_preserves_strong_baseline_false_alarm_and_abstention():
    result = subject.evaluate(subject.load_corpus(CORPUS))
    assert result["mode"] == "fixture" and result["measurement_basis"] == "scripted_fixture_only"
    assert result["summary"]["cases"] == 6 and result["summary"]["groups"] == 3
    cases = {case["id"]: case for case in result["cases"]}
    assert cases["imports-fault"]["baseline"]["confirmed"] is True
    assert cases["storage-fault"]["baseline"]["outcome"] == "no_concern"
    assert cases["storage-fault"]["jev"]["confirmed"] is True
    assert cases["transport-healthy-suspicious-name"]["jev"]["confirmed"] is False
    assert cases["transport-fault"]["jev"]["outcome"] == "insufficient_evidence"
    assert result["summary"]["baseline"]["confirmed_findings"] == 1
    assert result["summary"]["baseline"]["misses"] == 2
    assert result["summary"]["jev"]["incremental_confirmed_findings"] == 1
    assert result["summary"]["jev"]["false_alarms"] == 1
    assert result["summary"]["jev"]["unknown"] == 1
    assert result["summary"]["jev"]["false_clears"] == 0
    assert result["summary"]["reviewer_minutes"] is None and result["summary"]["cost"] is None
    assert all(case["comparison_eligible"] for case in result["cases"])
    assert all(case["jev"]["usage"] is None and case["jev"]["latency_basis"] == "fixture_execution"
               for case in result["cases"])


@pytest.mark.parametrize("variant", ("generic_concern", "forged_candidate", "missing_packet", "provider_unavailable"))
def test_live_comparison_requires_actual_grounded_source_evidence(variant):
    corpus = subject.load_corpus(CORPUS)
    observed_packets = []

    def provider(report, root, options):
        source = (root / "app/core.py").read_text()
        case = next(case for case in corpus["cases"] if case["sources"]["app/core.py"] == source)
        value = subject.fixture_diagnostics(report, root, options, case)
        observed_packets.append(deepcopy(value.get("evidence_context")))
        if variant == "provider_unavailable":
            return {"status": "unavailable", "assessments": []}
        if variant == "missing_packet":
            value.pop("evidence_context")
        elif variant == "generic_concern":
            value["assessments"] = [{"outcome": "concern", "confidence": 1.0}]
        else:
            for item in value["assessments"]:
                item["candidate_id"] = "unobserved-candidate"
        return value

    result = subject.evaluate(corpus, mode="live", diagnose_fn=provider)
    assert result["summary"]["jev"]["confirmed_findings"] == 0
    assert result["summary"]["jev"]["incremental_confirmed_findings"] == 0
    assert result["summary"]["jev"]["unavailable"] == (6 if variant == "provider_unavailable" else 0)
    if variant == "missing_packet":
        assert result["summary"]["comparison_eligible_cases"] == 0
    for packet in observed_packets:
        assert "oracle" not in packet and "fixture_outcome" not in packet


def test_operator_command_retains_exact_fixture_measurement_without_live_claim(tmp_path):
    output = tmp_path / "effectiveness.json"
    assert subject.main(["--corpus", str(CORPUS), "--output", str(output)]) == 0
    result = json.loads(output.read_text())
    assert result["measurement_basis"] == "scripted_fixture_only"
    assert len(result["corpus_sha256"]) == 64
    assert result["execution"]["commit"]
    assert result["summary"]["cases"] == 6 and result["summary"]["groups"] == 3


def test_expired_overall_budget_does_not_launch_provider_or_claim_clear(monkeypatch):
    ticks = iter([0.0] + [2.0] * 50)
    monkeypatch.setattr(subject.time, "monotonic", lambda: next(ticks))
    calls = []

    def provider(report, root, options):
        calls.append(options)
        return {"status": "unavailable", "assessments": []}

    result = subject.evaluate(subject.load_corpus(CORPUS), mode="live", diagnose_fn=provider, max_seconds=1)
    assert calls == []
    assert result["summary"]["jev"]["unknown"] == 6
    assert result["summary"]["jev"]["confirmed_findings"] == result["summary"]["jev"]["false_clears"] == 0
    assert all(case["jev"]["status"] == "budget_exhausted" for case in result["cases"])


def test_controlled_provider_identity_and_usage_stay_observed_not_imputed():
    corpus = subject.load_corpus(CORPUS)

    def provider(report, root, options):
        source = (root / "app/core.py").read_text()
        case = next(case for case in corpus["cases"] if case["sources"]["app/core.py"] == source)
        response = subject.fixture_diagnostics(report, root, options, case)
        response.update(provider="controlled-test-provider", model="controlled-model")
        for item in response["assessments"]:
            item["details"]["usage"] = {"input_tokens": 5, "output_tokens": 3}
        return response

    result = subject.evaluate(corpus, mode="live", diagnose_fn=provider)
    assert all(case["jev"]["diagnostics"]["provider"] == "controlled-test-provider" for case in result["cases"])
    assert all(case["jev"]["usage"] == [{"input_tokens": 5, "output_tokens": 3}] for case in result["cases"])
    assert all(case["jev"]["latency_basis"] == "diagnostic_call_wall_clock" and case["jev"]["latency_seconds"] >= 0
               for case in result["cases"])
    assert result["summary"]["cost"] is None and result["summary"]["reviewer_minutes"] is None


def test_optional_live_pilot_is_bounded_and_retains_provider_availability():
    workflow = json.loads((Path(__file__).parents[2] / ".github/workflows/ci.yml").read_text())
    steps = workflow["jobs"]["architecture"]["steps"]
    pilots = [step for step in steps if "-m scripts.architecture.effectiveness" in step.get("run", "")]
    assert len(pilots) == 1, "CI must expose one optional live effectiveness measurement"
    pilot = pilots[0]
    assert pilot.get("continue-on-error") is True
    assert "--mode live" in pilot["run"] and "--max-seconds 120" in pilot["run"]
    assert "effectiveness.json" in pilot["run"]
    assert pilot["env"] == {
        "TYPESAFE_API_KEY": "${{ secrets.TYPESAFE_API_KEY || secrets.JEV_API_KEY }}",
        "JEV_ENDPOINT": "${{ vars.JEV_ENDPOINT }}", "JEV_MODEL": "${{ vars.JEV_MODEL }}",
    }
