"""Deterministic composition, provisional economic routing and audit sampling."""
from __future__ import annotations

import copy
import math
import operator
import random

from .contracts import INVARIANTS, compose, digest

VERSION = "policies-2.0.0"
OPERATORS = {"eq": operator.eq, "le": operator.le, "ge": operator.ge,
             "in": lambda a, b: a in b, "not_in": lambda a, b: a not in b}
DEFAULT_PLAN = {
    "version": VERSION,
    "jev_model": "jev-1.14.0",
    "reasoning": {"provider": "native_codex", "requested_model": "inherited_session_model", "effort": "inherited_session_effort",
                  "observed_model": None, "observed_effort": None, "cost_usd": None},
    "maximum_cases": 24, "maximum_requests": 200, "maximum_questions_per_request": 24,
    "maximum_attempts_per_request": 2, "maximum_request_bytes": 48000,
    "maximum_response_bytes": 131072, "maximum_native_response_bytes": 32000,
    "maximum_elapsed_seconds": 3600, "timeout_seconds": 20, "concurrency": 1,
    "spending_cap_usd": 25.0,
    "cost_basis": "Provider usage priced using retained rate; no inference of actual invoice from estimated tokens.",
    "candidate_generation": "Fixed human/implementation-agent supplied probes, no model generation calls; development labor unknown.",
    "audit_seed": 20261024, "audit_fraction": 0.5, "random_escalation_seed": 4017,
    "paired_subset": "First case by ID per incident group plus second case per holdout group; distinct reasoning-only/post-P3 packets.",
    "routing": {"version": "provisional-uncalibrated-1", "consequence_threshold_units": 10,
                "learning_only_escalation": False, "unknown_signal_fallback": "risk",
                "maximum_additional_probes": 1, "maximum_reasoning_calls": 1},
    "valuation_scenario": {"id": "provisional-unit-loss", "currency": "assumed_loss_units",
                           "false_clear": 100, "false_alarm": 10, "unresolved": 1,
                           "consequence_class_units": {"false_acceptance": 100, "duplicate_effect": 500,
                                                       "wasted_investigation": 10, "low_impact": 0.1},
                           "reasoning_cost_units": 1, "probe_cost_units": 0.1,
                           "basis": "Analyst assumptions, not owner dollar valuations; no calibration fitted."},
    "robustness": {"invariance": ["reverse_options", "irrelevant_context", "equivalent_wording"],
                   "sensitivity": ["remove_decisive", "wrong_subject"],
                   "sampling": "All 30 corpus-prespecified transformations: two contexts in each split, five transforms each; never new independent units.",
                   "adaptive_attack_budget": 0},
    "measurement_budget": "Finite collection including paired comparisons and random audits; audit learning accounted separately.",
    "implementation_budget": "Labor and native development subscription costs unknown, separate from measurement.",
    "routine_operation_budget": "Counterfactual per-policy resource consumption; not a deployment authorization.",
}


def check_fact(check):
    if not check.get("observed", True):
        return None
    subject = check.get("subject", {})
    if subject.get("expected") != subject.get("observed"):
        return None
    op = OPERATORS.get(check.get("operator"))
    if op is None or "left" not in check or "right" not in check:
        return None
    try:
        return bool(op(check["left"], check["right"]))
    except (TypeError, ValueError, KeyError):
        return None


def baseline(packet):
    """A structured comparator over visible operands, never corpus labels.

    Facts carry event operands and declared requirements. Python compares IDs,
    revisions, arguments and sequence order. Missing or unrelated operands do
    not become negative observations. This is deliberately a strong comparator.
    """
    facts = packet.get("facts", {})
    checks = facts.get("checks", [])
    results = [(c, check_fact(c)) for c in checks]
    wrong = any(c.get("subject", {}).get("expected") != c.get("subject", {}).get("observed") for c in checks)
    known = [(c, r) for c, r in results if r is not None]
    violations = sorted({c["invariant"] for c, r in known if not r and "invariant" in c})
    policy_defined = facts.get("policy_defined", True)
    missing = not checks or any(r is None for _, r in results)
    required = set(facts.get("required_evidence", []))
    observed = set(facts.get("observed_evidence", []))
    missing = missing or not required.issubset(observed)
    applicable = "wrong_subject" if wrong and not known else "applicable" if checks else "unknown"
    if not policy_defined:
        judgment, uncertainty = "unresolved", "ambiguous_policy"
    elif violations:
        judgment, uncertainty = "contradicts", "none"
    elif missing:
        judgment, uncertainty = "unresolved", "missing_observation"
    else:
        judgment, uncertainty = "supports", "none"
    answers = {"applicability": applicable, "sufficiency": "missing" if missing and not violations else "decisive",
               "judgment": judgment, "uncertainty": uncertainty,
               "probe": "none", "probe_discrimination": "no", "probe_observation": "none",
               "decision_relevance": "none", "route": "none"}
    for name in INVARIANTS:
        related = [(c, r) for c, r in results if c.get("invariant") == name]
        answers["violation_" + name] = "supported" if name in violations else (
            "insufficient" if any(r is None for _, r in related) else "unsupported")
    if uncertainty == "ambiguous_policy":
        answers.update(route="defer", probe_observation="policy")
    elif missing:
        # Candidate descriptions are declared operations, not hidden labels.
        candidates = packet.get("candidates", {})
        relevant = {c.get("invariant") for c, r in results if r is None}
        # Explicit protocol-field dispatch is part of the structured comparator.
        # It reads no case IDs, labels, split names or subject injection settings.
        if answers["probe"] == "none":
            invariant = next((c.get("invariant") for c, r in results if r is None), None)
            direct = {"argument_integrity": "p_arguments", "assertion_execution": "p_assertion",
                      "authority_binding": "p_authority", "concurrent_activity": "p_busy"}
            pid = direct.get(invariant)
            if invariant == "current_evidence":
                pid = "p_replay" if any(type(c.get("right")) is int for c, r in results if r is None) else "p_context"
            if invariant == "effect_uncertainty":
                pid = "p_attempts" if any(c.get("right") == "submitted" for c, r in results if r is None) else "p_effect"
            if pid in candidates:
                answers.update(probe=pid, probe_discrimination="yes", route="probe", decision_relevance="consequential", probe_observation="execution")
        if answers["probe"] == "none":
            answers.update(probe="insufficient", probe_discrimination="unknown", route="defer", decision_relevance="unknown")
    relationship = packet.get("relationship")
    if relationship:
        left, right = relationship.get("left", {}), relationship.get("right", {})
        a, b = set(left.get("invariants", [])), set(right.get("invariants", []))
        answers["shared_invariant"] = "yes" if a & b else "no" if a and b else "insufficient"
        behavior_a, behavior_b = left.get("behavior"), right.get("behavior")
        answers["similar_behavior"] = "yes" if behavior_a and behavior_a == behavior_b else "no" if behavior_a and behavior_b else "insufficient"
        impl_a, impl_b = left.get("implementation_evidence"), right.get("implementation_evidence")
        # Only explicit common cause identity is evidence, never file-name resemblance.
        cause_a = impl_a.get("cause_identity") if isinstance(impl_a, dict) else None
        cause_b = impl_b.get("cause_identity") if isinstance(impl_b, dict) else None
        answers["common_implementation"] = "yes" if cause_a and cause_a == cause_b else "no" if cause_a and cause_b else "insufficient"
        if all("reported_revision" in side and "current_revision" in side for side in (left, right)):
            if any(side["reported_revision"] is None for side in (left, right)):
                answers.update(shared_invariant="insufficient", similar_behavior="insufficient")
            else:
                obsolete = [side["reported_revision"] != side["current_revision"] for side in (left, right)]
                answers["shared_invariant"] = "yes" if all(obsolete) else "no"
                answers["similar_behavior"] = "yes" if obsolete[0] == obsolete[1] else "no"
    result = {"status": "ok", "answers": answers, "native_confidence": {}, "distributions": {}, "backend": "structured-python"}
    result.update(compose(result))
    result["answers"].update({k: result[k] for k in ("violations", "relationship")})
    return result


def merge_observation(packet, probe):
    result = copy.deepcopy(packet)
    if probe.get("status") != "executed":
        return result
    visible = probe.get("visible", {})
    if visible.get("facts"):
        result["facts"].update(copy.deepcopy(visible["facts"]))
    if visible.get("relationship"):
        result["relationship"] = copy.deepcopy(visible["relationship"])
    for observation in probe.get("observations", []):
        if isinstance(observation, dict) and "facts" in observation:
            result["facts"].update(copy.deepcopy(observation["facts"]))
    result.setdefault("observations", []).extend(copy.deepcopy(probe.get("observations", [])))
    return result


def risk_route(packet, plan=DEFAULT_PLAN):
    facts = packet.get("facts", {})
    if not facts.get("policy_defined", True):
        return "defer"
    consequence = packet.get("consequences", {})
    units = consequence.get("loss_units", consequence.get("false_clear", plan["valuation_scenario"]["consequence_class_units"].get(consequence.get("class"))))
    if not isinstance(units, (int, float)):
        return "defer"
    initial = baseline(packet)
    if initial["answers"].get("route") == "probe" and units > plan["valuation_scenario"]["probe_cost_units"]:
        return "probe"
    return "reasoning" if units >= plan["routing"]["consequence_threshold_units"] else "none"


def route(packet, assessment, plan=DEFAULT_PLAN):
    a = assessment.get("answers", {})
    signal = a.get("route", "unknown")
    uncertainty = a.get("uncertainty", "unknown")
    relevance = a.get("decision_relevance", "unknown")
    scenario = plan["valuation_scenario"]
    consequence = packet.get("consequences", {})
    upper_benefit = consequence.get("loss_units", scenario["consequence_class_units"].get(consequence.get("class")))
    if uncertainty == "ambiguous_policy" or not packet.get("facts", {}).get("policy_defined", True):
        selected, reason = "defer", "undefined_owner_policy"
    elif assessment.get("status") != "ok":
        selected, reason = "defer", "assessment_unavailable"
    elif (signal == "probe" and relevance == "consequential" and a.get("probe") in packet.get("candidates", {})
          and a.get("probe_discrimination") == "yes" and isinstance(upper_benefit, (int, float))
          and upper_benefit > scenario["probe_cost_units"]):
        selected, reason = "probe", "observable_discriminating_candidate"
    elif (signal == "reasoning" and relevance == "consequential" and uncertainty in {"hard_inference", "uncovered_hypothesis", "missing_observation"}
          and isinstance(upper_benefit, (int, float)) and upper_benefit > scenario["reasoning_cost_units"]):
        selected, reason = "reasoning", "uncalibrated_jev_consequential_signal"
    elif relevance == "unknown" or signal not in {"none", "probe", "reasoning", "defer"}:
        selected, reason = risk_route(packet, plan), "bounded_risk_fallback"
    else:
        selected, reason = ("defer" if signal == "defer" else "none"), "no_supported_incremental_action_value"
    return {"route": selected, "reason_codes": [reason], "signals": {k: a.get(k) for k in ("route", "uncertainty", "decision_relevance", "probe_discrimination", "probe")},
            "economic_estimate": {"expected_benefit": None, "possible_benefit_range": [0, upper_benefit] if upper_benefit is not None else None,
                                  "expected_cost": scenario.get(selected + "_cost_units", 0) if selected in {"probe", "reasoning", "none", "defer"} else None,
                                  "units": "analyst assumed loss units; possible maximum is not expected benefit", "calibrated": False,
                                  "method": plan["routing"]["version"], "scenario": plan["valuation_scenario"], "purpose": "operational"}}


def random_escalation(case_ids, selected_count, seed):
    """An exact call-count comparator, explicitly not a monetary-cost match."""
    if not 0 <= selected_count <= len(case_ids) or len(set(case_ids)) != len(case_ids):
        raise ValueError("Invalid paired comparison population")
    return set(random.Random(seed).sample(sorted(case_ids), selected_count))


def audit_sample(decisions, *, seed, fraction, frozen_decision_hash):
    """Stratified SRS after decisions are frozen, with positive inclusion everywhere."""
    if not 0 < fraction <= 1 or frozen_decision_hash != digest(decisions):
        raise ValueError("Audits require a frozen decision population and positive inclusion")
    by_case = {row["case_id"]: row for row in decisions}
    if len(by_case) != len(decisions):
        raise ValueError("Audit population needs one initial decision per case")
    strata = {}
    for row in decisions:
        confidence = row.get("native_confidence", {}).get("judgment")
        band = "high" if isinstance(confidence, (float, int)) and confidence >= 0.8 else "other"
        stratum = ":".join((row.get("action", "defer"), row.get("route", "defer"), band))
        strata.setdefault(stratum, []).append(row["case_id"])
    rng = random.Random(seed)
    rows = []
    for stratum, population in sorted(strata.items()):
        population = sorted(population)
        count = math.ceil(len(population) * fraction)
        selected = set(rng.sample(population, count))
        for cid in population:
            rows.append({"case_id": cid, "stream": "independent_random_audit", "stratum": stratum,
                         "population_size": len(population), "sample_size": count,
                         "inclusion_probability": count / len(population), "selected": cid in selected,
                         "seed": seed, "selection_order": "after_initial_routes_frozen",
                         "decision_population_sha256": frozen_decision_hash,
                         "status": "pending" if cid in selected else "not_selected"})
    return rows
