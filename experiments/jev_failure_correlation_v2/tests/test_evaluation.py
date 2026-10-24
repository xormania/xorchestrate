import copy
import json

import pytest

from experiments.jev_failure_correlation_v2.economics import accounting, economic_report
from experiments.jev_failure_correlation_v2.evaluation import (
    audit_summary, confidence_summary, evaluate, evaluate_run, learning_transfer, score_row, transitions,
)


def case(cid="x", action="reject"):
    return {"id": cid, "group": "incident-1", "split": "holdout",
            "packet": {"consequences": {"class": "duplicate_effect"}},
            "initial_key": {"judgment": "unresolved", "action": "investigate", "violations": []},
            "outcome_key": {"judgment": "contradicts", "action": action,
                            "violations": ["effect_uncertainty"]}}


def decision(policy="P0", action="investigate", **extra):
    return {"case_id": "x", "policy": policy, "status": "ok", "acquired_evidence": False,
            "action": action, "answers": {"judgment": "unresolved", "action": action, "violations": []},
            "request_ids": [], "probe_ids": [], **extra}


def test_initial_uncertainty_can_be_epistemically_correct_with_hidden_fault():
    scored = score_row(case(), decision())
    assert scored["semantic_all_correct"] is True
    assert scored["outcome_action_correct"] is None
    assert scored["key_layer"] == "initial_key"
    # Reveal the independently observed fact only after an exercised relevant probe.
    post = decision("P2", "reject", acquired_evidence=True,
                    answers={"judgment": "contradicts", "action": "reject", "violations": ["effect_uncertainty"]})
    assert score_row(case(), post)["semantic_all_correct"] is True
    assert score_row(case(), post)["outcome_action_correct"] is True


def test_provider_failure_cannot_be_a_correct_clear_or_a_wrong_model_answer():
    row = decision("P1", "accept", status="provider_failure")
    result = score_row(case(action="accept"), row)
    assert result["semantic_all_correct"] is None
    assert result["outcome_action_correct"] is None
    assert result["false_clear"] is False


def test_same_action_label_correction_has_zero_operational_benefit():
    old = decision("P0", "reject")
    new = decision("P3", "reject", acquired_evidence=True,
                   answers={"judgment": "contradicts", "action": "reject", "violations": ["effect_uncertainty"]})
    report = economic_report([case()], [old, new])
    result = report["policies"]["P3"]["scenario_results"]["consequential"]
    assert result["gross_loss_reduction_usd"] == 0
    assert result["action_changes"] == 0
    assert result["net_value_usd"] is None  # Review/probe/development costs are not measured.


def test_escalation_damage_is_in_outcome_loss_not_an_extra_penalty():
    old, new = decision("P0", "reject"), decision("P4", "accept")
    report = economic_report([case()], [old, new])
    result = report["policies"]["P4"]["scenario_results"]["consequential"]
    assert result["gross_loss_reduction_usd"] == -500
    assert result["higher_loss_actions"] == 1
    assert result["maximum_total_incremental_cost_for_break_even_usd"] == -500


def test_accounting_includes_failed_retries_but_not_cache_references():
    rows = [
        {"attempt_id": "a", "request_id": "r", "status": "timeout", "cost_usd": .4},
        {"attempt_id": "b", "request_id": "r", "status": "ok", "cost_usd": .6,
         "list_price_estimate_usd": .01, "usage": {"input_tokens": 250}},
        {"attempt_id": "cache", "request_id": "r", "status": "cache_reuse", "cache_hit": True},
        {"attempt_id": "native", "request_id": "native", "status": "ok", "cost_usd": None},
    ]
    result = accounting(rows)
    assert result["known_charges_usd"] == 1
    assert result["actual_total_usd"] is None
    assert result["unknown_charge_attempts"] == 1
    assert result["attempts_incurred"] == 3
    assert result["cache_reuses"] == 1
    assert result["known_list_price_estimate_usd"] == .01
    with pytest.raises(ValueError, match="Duplicate attempt"):
        accounting(rows + [rows[0]])


def test_unknown_probe_duration_and_missing_artifact_are_visible():
    row = decision("P0", "reject", probe_ids=["missing", "observed"])
    report = economic_report([case()], [row], probes=[{"probe_id": "observed", "elapsed_seconds": None}])
    value = report["policies"]["P0"]
    assert value["probe_durations_unknown"] == 2
    assert value["missing_probe_records"] == ["missing"]
    assert value["probe_seconds_measured"] == 0


def test_grounded_price_scenario_preserves_known_jev_list_price():
    rows = [decision("P0", "accept"), decision("P4", "reject", request_ids=["jev", "native"])]
    attempts = [{"request_id": "jev", "status": "ok", "actual_cost_usd": None,
                 "list_price_estimate_usd": .000042},
                {"request_id": "native", "status": "ok", "actual_cost_usd": None}]
    result = economic_report([case()], rows, attempts)["policies"]["P4"]["scenario_results"]["consequential"]
    grounded = result["list_price_grounded_sensitivity"][0]
    assert grounded["remaining_unpriced_calls"] == 1
    assert grounded["actual_or_jev_usage_list_price_basis_usd"] == .000042
    assert grounded["net_before_unmeasured_review_and_other_costs_usd"] == pytest.approx(500 - .000042 - .001)
    assert result["net_value_usd"] is None


def test_missing_required_arm_and_unknown_outcomes_never_report_passed():
    result = evaluate([case()], [decision()])
    assert result["policies"]["always"]["status"] == "incomplete"
    assert result["policies"]["always"]["by_split"]["holdout"]["missing_cases"] == ["x"]
    econ = result["economics"]["incremental_comparisons"]["P3->P4"]["all"]["scenario_results"]["low_stakes"]
    assert econ["gross_loss_reduction_usd"] is None
    assert econ["call_price_sensitivity"] == []


def test_transitions_keep_unresolved_and_missing_separate_from_errors():
    before = [{"case_id": "a", "outcome_action_correct": False},
              {"case_id": "b", "outcome_action_correct": True},
              {"case_id": "c", "outcome_action_correct": None}]
    after = [{"case_id": "a", "outcome_action_correct": True},
             {"case_id": "b", "outcome_action_correct": False},
             {"case_id": "c", "outcome_action_correct": True},
             {"case_id": "d", "outcome_action_correct": True}]
    result = transitions(before, after)
    assert result["counts"]["wrong_to_right"] == 1
    assert result["counts"]["right_to_wrong"] == 1
    assert result["counts"]["unresolved"] == 1
    assert result["missing_before"] == ["d"]


def test_audit_estimate_uses_inclusion_probabilities_and_independent_evidence():
    scored = [{"case_id": x, "policy": "P4"} for x in ("a", "b", "c", "d")]
    rows = [{"case_id": "a", "policy": "P4", "selection_type": "random", "selected": True,
             "inclusion_probability": .5, "independent_miss": True, "outcome_evidence": "a.json"},
            {"case_id": "b", "policy": "P4", "selection_type": "targeted", "selected": True,
             "inclusion_probability": 1, "independent_miss": True, "outcome_evidence": "b.json"}]
    result = audit_summary(rows, scored)
    assert result["random"]["P4"]["estimated_miss_rate_ht"] == .5
    rows[0].pop("outcome_evidence")
    result = audit_summary(rows, scored)
    assert result["random"]["P4"]["estimated_miss_rate_ht"] is None


def test_confidence_reports_confident_error_and_undefined_auc():
    result = confidence_summary([{"confidence": 1.0, "correct": False},
                                 {"confidence": .5, "correct": True}])
    assert result["error_ranking_auc"] == 0
    assert result["coverage_error_curve"][-1]["error_rate"] == 1
    assert confidence_summary([{"confidence": .2, "correct": True}])["error_ranking_auc"] is None


def test_native_confidence_is_scored_against_raw_answer_before_composition():
    row = decision("P1", native_confidence={"judgment": 1.0},
                   assessment={"answers": {"judgment": "supports"}})
    report = evaluate([case()], [row])
    confidence = report["confidence"]["P1"]["judgment"]["holdout"]
    assert report["rows"][0]["questions"]["judgment"]["correct"] is True
    assert confidence["reliability"][-1]["error_rate"] == 1
    assert confidence["raw_vs_composed_answer_changes"] == 1


def test_grouped_variants_and_multi_mechanism_exact_match():
    cases = [case(), {**case("y"), "group": "incident-1"}]
    bad = decision(answers={"judgment": "contradicts", "violations": ["effect_uncertainty", "other"]},
                   acquired_evidence=True)
    bad["acquired_evidence"] = False
    result = evaluate(cases, [bad])
    assert result["n_independent_groups"] == 1
    assert result["rows"][0]["questions"]["violations"]["correct"] is False


def test_outcome_key_requires_applicable_decisive_probe_evidence():
    c = {**case(), "probe_id": "target"}
    row = decision("P2", "reject", acquired_evidence=True, probe_ids=["run-one"])
    with pytest.raises(ValueError, match="Post-observation key"):
        evaluate([c], [row])
    probe = {"record_id": "run-one", "case_id": "x", "probe_id": "unrelated", "status": "executed",
             "exercised": True, "decisive": True, "raw_reference": "trace.json"}
    with pytest.raises(ValueError, match="Post-observation key"):
        evaluate([c], [row], probes=[probe])
    probe["probe_id"] = "target"
    assert evaluate([c], [row], probes=[probe])["rows"][0]["key_layer"] == "outcome_key"
    probe["decisive"] = False
    with pytest.raises(ValueError, match="Post-observation key"):
        evaluate([c], [row], probes=[probe])


def test_replay_requires_frozen_case_evidence_and_rejects_duplicate_decisions(tmp_path):
    with pytest.raises(ValueError, match="Missing frozen cases"):
        evaluate_run(tmp_path)
    (tmp_path / "cases.json").write_text(json.dumps([case()]))
    (tmp_path / "decisions.jsonl").write_text(json.dumps(decision()) + "\n")
    assert evaluate_run(tmp_path)["n_cases"] == 1
    with pytest.raises(ValueError, match="duplicate final"):
        evaluate([case()], [decision(), copy.deepcopy(decision())])


def test_shared_native_invocation_is_one_unknown_charge_unit():
    rows = [{"attempt_id": str(i), "request_id": str(i), "invocation_id": "agent-one",
             "billing_scope": "invocation", "status": "ok", "actual_cost_usd": None} for i in range(4)]
    result = accounting(rows)
    assert result["attempts_incurred"] == 4
    assert result["unknown_charge_attempts"] == 1
    assert result["recorded_distinct_invocations"] == 1
    assert result["charge_units"] == 1


def test_declared_paired_population_is_not_expanded_to_entire_corpus():
    cases = [case(), case("outside-paired")]
    result = evaluate(cases, [decision("always")], policy_populations={"always": ["x"]})
    assert result["policies"]["always"]["status"] == "measured"
    assert result["policies"]["always"]["by_split"]["holdout"]["expected_cases"] == 1
    assert result["policies"]["P4"]["status"] == "incomplete"


def test_reusable_guard_transfer_is_scoped_and_unknown_without_measurement():
    c = case()
    c["packet"]["facts"] = {"checks": [{"observed": False}]}
    c["initial_key"]["sufficiency"] = "missing"
    row = decision(answers={"judgment": "unresolved", "sufficiency": "missing"})
    result = learning_transfer([c], [row])
    assert result["status"] == "observed_reuse_check"
    assert result["by_split"]["holdout"]["scoring"]["correct"] == 1
    assert result["artifact_sha256"]
    assert result["learning_intervention_uplift"] is None
    assert result["monetized_reuse_value_usd"] is None
    assert learning_transfer([c], [])["status"] == "unverified_holdout_reuse"
