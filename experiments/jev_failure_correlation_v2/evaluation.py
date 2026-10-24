"""Offline, key-separated scoring of retained v2 records; no provider access."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from .economics import economic_report


SUCCESS = {"ok"}
ABSTENTIONS = {"unresolved", "insufficient", "unknown", "unsupported", "defer", "investigate"}
REQUIRED_POLICIES = ("P0", "P1", "P2", "P3", "P4", "never", "always", "random", "risk", "reasoning_only")


def wilson(successes: int, total: int, z: float = 1.959963984540054) -> list[float] | None:
    if total == 0:
        return None
    if not 0 <= successes <= total:
        raise ValueError("Invalid binomial counts")
    p = successes / total
    d = 1 + z * z / total
    center = (p + z * z / (2 * total)) / d
    radius = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / d
    return [max(0.0, center - radius), min(1.0, center + radius)]


def _match(actual: Any, expected: Any, question: str) -> bool | None:
    if actual is None:
        return None
    if question == "violations":
        if not isinstance(actual, list) or not isinstance(expected, list):
            return False
        return sorted(actual) == sorted(expected)
    return actual in expected if isinstance(expected, list) else actual == expected


def _answer(row: dict, question: str) -> Any:
    return row.get("answers", {}).get(question, row.get(question))


def _count(values: list[bool | None]) -> dict:
    correct = sum(x is True for x in values)
    wrong = sum(x is False for x in values)
    missing = sum(x is None for x in values)
    return {"total": len(values), "correct": correct, "incorrect": wrong, "unverified": missing,
            "accuracy_answered": correct / (correct + wrong) if correct + wrong else None,
            "coverage": (correct + wrong) / len(values) if values else None,
            "wilson_95_answered": wilson(correct, correct + wrong),
            "interval_limit": "Descriptive case interval; correlated variants are not independent incidents."}


def score_row(case: dict, row: dict) -> dict:
    available = row.get("status") in SUCCESS
    key_name = "outcome_key" if row.get("acquired_evidence") else "initial_key"
    key = case.get(key_name, {})
    questions = {}
    for question, expected in key.items():
        if question in {"rationale", "evidence", "evidence_refs", "author", "reviewer", "notes"}:
            continue
        actual = _answer(row, question) if available else None
        questions[question] = {"actual": actual, "expected": expected,
                               "correct": _match(actual, expected, question)}
    action = row.get("action") if available else None
    expected_action = case.get("outcome_key", {}).get("action")
    outcome_correct = _match(action, expected_action, "action") if expected_action is not None else None
    # An unresolved action is explicitly reported, not silently counted wrong or right.
    if action in {"unresolved", "investigate", "defer"} and not outcome_correct:
        outcome_correct = None
    supported = [q["correct"] for q in questions.values()]
    semantic = None if not supported or any(x is None for x in supported) else all(supported)
    truth = expected_action if isinstance(expected_action, list) else [expected_action]
    return {
        "case_id": case["id"], "group": case.get("group", case["id"]),
        "split": case.get("split", "unknown"), "policy": row["policy"],
        "status": row.get("status", "unknown"), "key_layer": key_name,
        "questions": questions, "semantic_all_correct": semantic,
        "action": action, "outcome_action_correct": outcome_correct,
        "false_clear": available and action == "accept" and "accept" not in truth and expected_action is not None,
        "unnecessary_rejection": available and action == "reject" and "accept" in truth,
        "abstention": action in {"unresolved", "investigate", "defer"},
        "label_disputed": bool(case.get("label_disputed", False)),
        "route": row.get("route"),
    }


def transitions(before: list[dict], after: list[dict], field: str = "outcome_action_correct") -> dict:
    left = {r["case_id"]: r for r in before}
    right = {r["case_id"]: r for r in after}
    counts = {k: 0 for k in ("wrong_to_right", "right_to_wrong", "unchanged_wrong", "unchanged_right", "unresolved")}
    details = []
    for cid in sorted(left.keys() & right.keys()):
        a, b = left[cid].get(field), right[cid].get(field)
        label = ("unresolved" if a is None or b is None else
                 "unchanged_right" if a and b else "unchanged_wrong" if not a and not b else
                 "wrong_to_right" if b else "right_to_wrong")
        counts[label] += 1
        details.append({"case_id": cid, "transition": label,
                        "action_changed": left[cid].get("action") != right[cid].get("action"),
                        "before_key_layer": left[cid].get("key_layer"),
                        "after_key_layer": right[cid].get("key_layer"),
                        "epistemic_target_changed": left[cid].get("key_layer") != right[cid].get("key_layer")})
    return {"field": field, "paired_cases": len(details), "counts": counts, "cases": details,
            "missing_before": sorted(right.keys() - left.keys()), "missing_after": sorted(left.keys() - right.keys()),
            "interpretation": "Semantic transitions with changed key layers compare stage-relative correctness, not correction on one unchanged target. Outcome-action transitions use a common outcome key."}


def confidence_summary(samples: list[dict]) -> dict:
    """Native confidence is ranked separately from top answer probability."""
    bins = []
    for lower, upper in ((0.0, 0.5), (0.5, 0.8), (0.8, 0.95), (0.95, 1.0000001)):
        rows = [r for r in samples if lower <= r["confidence"] < upper]
        correct = sum(r["correct"] for r in rows)
        bins.append({"lower": lower, "upper": min(1.0, upper), "n": len(rows),
                     "correct": correct, "error_rate": 1 - correct / len(rows) if rows else None,
                     "mean_native_confidence": sum(r["confidence"] for r in rows) / len(rows) if rows else None,
                     "wilson_95_accuracy": wilson(correct, len(rows))})
    wrong = [r for r in samples if not r["correct"]]
    right = [r for r in samples if r["correct"]]
    comparisons = len(wrong) * len(right)
    # A useful error rank gives errors LOWER native confidence than correct answers.
    rank = sum(1 if a["confidence"] < b["confidence"] else 0.5 if a["confidence"] == b["confidence"] else 0
               for a in wrong for b in right)
    curves = []
    for threshold in (0.0, 0.5, 0.8, 0.95, 1.0):
        kept = [r for r in samples if r["confidence"] >= threshold]
        curves.append({"threshold": threshold, "kept": len(kept),
                       "coverage": len(kept) / len(samples) if samples else None,
                       "errors": sum(not r["correct"] for r in kept),
                       "error_rate": sum(not r["correct"] for r in kept) / len(kept) if kept else None})
    return {"n": len(samples), "reliability": bins,
            "error_ranking_auc": rank / comparisons if comparisons else None,
            "error_correct_pairs": comparisons, "coverage_error_curve": curves,
            "raw_vs_composed_answer_changes": sum(r.get("raw_answer") != r.get("composed_answer") for r in samples),
            "interpretation": "Native raw-answer correctness before deterministic composition; descriptive frozen thresholds. Native confidence is not an outcome probability or dollars."}


def audit_summary(audits: list[dict], scored: list[dict]) -> dict:
    """Horvitz-Thompson estimated misses only for completed random audits.

    A sampled decision without independent adjudication leaves the rate unknown.
    Targeted/adaptive streams and escalation randomization never enter this rate.
    """
    streams: dict[str, list[dict]] = defaultdict(list)
    for row in audits:
        streams[row.get("selection_type", row.get("stream", "unknown"))].append(row)
    result = {"streams": {k: len(v) for k, v in streams.items()}, "random": {}}
    random_rows = streams.get("random", []) + streams.get("random_audit", []) + streams.get("independent_random_audit", [])
    by_policy: dict[str, list[dict]] = defaultdict(list)
    for row in random_rows:
        by_policy[row.get("policy", "P4")].append(row)
    for policy, rows in by_policy.items():
        population = {r["case_id"] for r in scored if r["policy"] == policy}
        weighted = 0.0
        pending = []
        selected_count = 0
        probs = []
        for row in rows:
            p = row.get("inclusion_probability")
            if not isinstance(p, (float, int)) or not 0 < p <= 1:
                raise ValueError("Audit inclusion probability must be positive and <=1")
            probs.append(p)
            if not row.get("selected", True):
                continue
            selected_count += 1
            # Caller must attach independent finding; model agreement is insufficient.
            finding = row.get("independent_miss", row.get("miss"))
            has_evidence = bool(row.get("outcome_evidence") or row.get("independent_evidence_ref"))
            if not isinstance(finding, bool) or not has_evidence:
                pending.append(row.get("case_id"))
            else:
                weighted += int(finding) / p
        result["random"][policy] = {
            "population_n": len(population), "selected_n": selected_count,
            "pending_independent_adjudication": pending,
            "inclusion_probabilities": sorted(set(probs)),
            "estimated_total_misses_ht": weighted if not pending else None,
            "estimated_miss_rate_ht": weighted / len(population) if population and not pending else None,
            "interval": None, "interval_reason": "Small stratified pilot; no unsupported iid interval on the weighted rate.",
        }
    if not random_rows:
        result["status"] = "unverified_no_random_audit_records"
    return result


def probe_summary(probes: list[dict], decisions: list[dict], cases: list[dict]) -> dict:
    by_case = {c["id"]: c for c in cases}
    counts = Counter()
    purpose_counts: dict[str, Counter] = defaultdict(Counter)
    details = []
    for row in probes:
        counts["records"] += 1
        status = row.get("status", "unknown")
        counts[status] += 1
        performed = status == "executed" and row.get("exercised") is True
        information = performed and row.get("distinguishing") is True
        counts["executed_and_exercised"] += performed
        counts["distinguishing_executions"] += information
        cid = row.get("case_id")
        relevant = [r for r in decisions if r.get("case_id") == cid and r.get("status") == "ok"]
        policy = row.get("policy")
        predecessor = {"P0_probe": "P0", "P2": "P1", "P4": "P3", "risk": "P3"}.get(policy)
        before = next((r for r in relevant if r["policy"] == predecessor), None)
        after = next((r for r in relevant if r["policy"] == policy), None)
        record_id = row.get("record_id", row.get("id", row.get("probe_id")))
        linked = bool(after and record_id in after.get("probe_ids", []))
        action_useful = None
        if before and after and linked and cid in by_case:
            target = by_case[cid].get("outcome_key", {}).get("action")
            old = _match(before.get("action"), target, "action")
            new = _match(after.get("action"), target, "action")
            action_useful = bool(information and new and not old and before.get("action") != after.get("action"))
            counts["action_improvements_after_probe"] += action_useful
        purpose = row.get("purpose", "unspecified")
        purpose_counts[purpose]["records"] += 1
        purpose_counts[purpose]["executed_and_exercised"] += performed
        purpose_counts[purpose]["distinguishing_executions"] += information
        purpose_counts[purpose]["action_improvements"] += action_useful is True
        details.append({"case_id": cid, "probe_id": row.get("probe_id", row.get("id")), "status": status,
                        "record_id": record_id, "policy": policy, "purpose": purpose, "decision_link_verified": linked,
                        "information_gain_observed": information, "action_improvement": action_useful,
                        "elapsed_seconds": row.get("elapsed_seconds"), "raw_reference": row.get("raw_reference")})
    known_seconds = [p.get("elapsed_seconds") for p in probes if isinstance(p.get("elapsed_seconds"), (int, float))]
    measured_seconds = sum(known_seconds)
    return {"counts": dict(counts), "records": details,
            "by_purpose": {k: dict(v) for k, v in purpose_counts.items()},
            "measured_total_seconds": measured_seconds,
            "unknown_duration_records": len(probes) - len(known_seconds),
            "distinguishing_executions_per_measured_second": counts["distinguishing_executions"] / measured_seconds if measured_seconds and len(known_seconds) == len(probes) else None,
            "interpretation": "Executed distinguishing information and action improvement are separate. End-to-end product improvement needs separately retained causal execution."}


def routing_value(scored: list[dict]) -> dict:
    """Conditional benefit/damage of paired fallback, with unobserved cells visible."""
    by = {(r["policy"], r["case_id"]): r for r in scored}
    records = []
    groups: dict[str, Counter] = defaultdict(Counter)
    for (policy, cid), routed in by.items():
        if policy != "P4":
            continue
        initial, alternative = by.get(("P3", cid)), by.get(("always", cid))
        route = routed.get("route", "unknown")
        old = initial.get("outcome_action_correct") if initial else None
        new = alternative.get("outcome_action_correct") if alternative else None
        observed = bool(initial and alternative and initial["status"] == "ok" and alternative["status"] == "ok")
        helpful = observed and old is not True and new is True
        harmful = observed and old is True and new is False
        groups[route]["population"] += 1
        groups[route]["paired_observed"] += observed
        groups[route]["could_correct_or_resolve"] += helpful
        groups[route]["could_damage"] += harmful
        records.append({"case_id": cid, "route": route, "paired_observed": observed,
                        "could_correct_or_resolve": helpful if observed else None,
                        "could_damage": harmful if observed else None,
                        "missed_reasoning_opportunity": helpful and route not in {"reasoning", "escalate"} if observed else None})
    return {"by_proposed_route": {k: dict(v) for k, v in groups.items()}, "records": records,
            "interpretation": "Hypothetical paired fallback on compatible evidence. Missing fallback remains unknown; probe/state-changing trajectories need controlled causal runs."}


def learning_transfer(cases: list[dict], decisions: list[dict]) -> dict:
    """Predeclared reuse check for an evidence guard, not a learned-model claim.

    Reuse is operationalized as applying the same unchanged guard to distinct
    incident groups and checking its two evidence judgments against frozen keys.
    No model outputs select these cases or fit the guard.
    """
    artifact = Path(__file__).with_name("policies.py")
    by_case = {r["case_id"]: r for r in decisions if r["policy"] == "P0"}
    splits = {}
    for split in sorted({c.get("split", "unknown") for c in cases}):
        selected = []
        for case in cases:
            if case.get("split", "unknown") != split:
                continue
            checks = case.get("packet", {}).get("facts", {}).get("checks", [])
            missing_or_foreign = any(not check.get("observed", True) or
                                    check.get("subject", {}).get("expected") != check.get("subject", {}).get("observed")
                                    for check in checks)
            if missing_or_foreign:
                selected.append(case)
        records = []
        for case in selected:
            row = by_case.get(case["id"])
            results = {}
            for q in ("sufficiency", "judgment"):
                actual = _answer(row, q) if row and row.get("status") == "ok" else None
                results[q] = _match(actual, case["initial_key"].get(q), q)
            records.append({"case_id": case["id"], "group": case.get("group", case["id"]),
                            "sufficiency_correct": results["sufficiency"], "judgment_correct": results["judgment"],
                            "correct": None if any(v is None for v in results.values()) else all(results.values())})
        splits[split] = {"n_cases": len(selected), "n_groups": len({c.get("group", c["id"]) for c in selected}),
                         "scoring": _count([r["correct"] for r in records]), "records": records}
    holdout = splits.get("holdout", {}).get("scoring", {})
    return {
        "status": "observed_reuse_check" if holdout.get("total", 0) and not holdout.get("unverified") else "unverified_holdout_reuse",
        "artifact": "experiments/jev_failure_correlation_v2/policies.py:check_fact and baseline",
        "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest() if artifact.exists() else None,
        "predeclared_task": "Same evidence-applicability/sufficiency guard on missing or foreign-boundary observations across distinct holdout groups.",
        "motivation": "Frozen v1 evidence: confidence or a success summary cannot substitute for applicable boundary observation.",
        "by_split": splits,
        "learning_intervention_uplift": None, "monetized_reuse_value_usd": None,
        "limits": "Tests reuse of a fixed deterministic artifact. No prospective before/after learning intervention, training effect, production recurrence, or causal learning ROI is established.",
    }


def evaluate(cases: list[dict], decisions: list[dict], attempts: list[dict] | None = None,
             audits: list[dict] | None = None, probes: list[dict] | None = None,
             *, scenarios: dict | None = None, policy_populations: dict | None = None) -> dict:
    by_case = {c["id"]: c for c in cases}
    if len(by_case) != len(cases):
        raise ValueError("Duplicate case identity")
    seen = set()
    scored = []
    confidence: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    policy_rows: dict[str, list[dict]] = defaultdict(list)
    for row in decisions:
        key = (row["policy"], row["case_id"])
        if key in seen:
            raise ValueError(f"Ambiguous duplicate final policy/case decision: {key}")
        seen.add(key)
        if row["case_id"] not in by_case:
            raise ValueError("Decision refers to absent case")
        if row.get("acquired_evidence"):
            case = by_case[row["case_id"]]
            linked = set(row.get("probe_ids", []))
            witnesses = [p for p in (probes or [])
                         if p.get("record_id", p.get("id", p.get("probe_id"))) in linked
                         and p.get("case_id") == case["id"]
                         and p.get("probe_id") == case.get("probe_id")
                         and p.get("status") == "executed" and p.get("exercised") is True
                         and p.get("decisive") is True and p.get("raw_reference")]
            if not witnesses:
                raise ValueError("Post-observation key requires a linked, applicable, decisive exercised probe with raw evidence")
        result = score_row(by_case[row["case_id"]], row)
        scored.append(result)
        policy_rows[row["policy"]].append(result)
        raw_answers = row.get("assessment", {}).get("answers", row.get("answers", {}))
        for question, q in result["questions"].items():
            c = row.get("native_confidence", {}).get(question)
            raw_correct = _match(raw_answers.get(question), q["expected"], question) if row.get("status") == "ok" else None
            if raw_correct is not None and isinstance(c, (int, float)) and 0 <= c <= 1:
                sample = {"case_id": row["case_id"], "confidence": c, "correct": raw_correct,
                          "raw_answer": raw_answers.get(question), "composed_answer": q["actual"],
                          "distribution": row.get("distributions", {}).get(question)}
                confidence[(row["policy"], question, result["split"])].append(sample)
    report: dict = {"version": "v2-evaluation-1", "n_cases": len(cases),
                    "n_independent_groups": len({c.get("group", c["id"]) for c in cases}),
                    "rows": scored, "policies": {}, "comparisons": {}, "confidence": {},
                    "limits": ["Controlled pilot, not a random sample of production failures.",
                               "Related variants share a group; intervals over rows are descriptive.",
                               "No rare-error production guarantee or causal workflow benefit follows from label agreement."]}
    for policy in sorted(set(REQUIRED_POLICIES) | set(policy_rows)):
        rows = policy_rows.get(policy, [])
        splits = {}
        for split in sorted({c.get("split", "unknown") for c in cases}):
            subset = [r for r in rows if r["split"] == split]
            population = (policy_populations or {}).get(policy, by_case.keys())
            expected_ids = {c["id"] for c in cases if c.get("split", "unknown") == split and c["id"] in population}
            present = {r["case_id"] for r in subset}
            answered = [r for r in subset if r["status"] == "ok"]
            clears = [r for r in answered if r["action"] == "accept"]
            groups = defaultdict(list)
            for r in subset:
                groups[r["group"]].append(r)
            questions = sorted({q for r in subset for q in r["questions"]})
            splits[split] = {
                "expected_cases": len(expected_ids), "present_rows": len(subset),
                "missing_cases": sorted(expected_ids - present), "independent_groups": len(groups),
                "statuses": dict(Counter(r["status"] for r in subset)),
                "semantic": _count([r["semantic_all_correct"] for r in subset]),
                "outcome_action": _count([r["outcome_action_correct"] for r in subset]),
                "questions": {q: _count([r["questions"].get(q, {}).get("correct") for r in subset]) for q in questions},
                "false_clears": sum(r["false_clear"] for r in subset), "clears": len(clears),
                "false_clear_fraction_of_clears": sum(r["false_clear"] for r in subset) / len(clears) if clears else None,
                "unnecessary_rejections": sum(r["unnecessary_rejection"] for r in subset),
                "abstentions": sum(r["abstention"] for r in subset),
                "disputed_labels": [r["case_id"] for r in subset if r["label_disputed"]],
                "by_group": {g: _count([r["outcome_action_correct"] for r in rs]) for g, rs in groups.items()},
            }
        complete_population = not any(s["missing_cases"] for s in splits.values())
        report["policies"][policy] = {"status": "measured" if rows and complete_population and all(r["status"] == "ok" for r in rows) else "incomplete",
                                       "by_split": splits}
    for a, b in (("P0", "P1"), ("P0", "P0_probe"), ("P0_probe", "P2"), ("P1", "P2"), ("P2", "P3"), ("P3", "P4"),
                 ("never", "always"), ("random", "P4"), ("risk", "P4"), ("reasoning_only", "always")):
        report["comparisons"][f"{a}->{b}"] = {
            "action": transitions(policy_rows.get(a, []), policy_rows.get(b, [])),
            "semantic": transitions(policy_rows.get(a, []), policy_rows.get(b, []), "semantic_all_correct"),
        }
    for (policy, question, split), samples in confidence.items():
        report["confidence"].setdefault(policy, {}).setdefault(question, {})[split] = confidence_summary(samples)
    report["audits"] = audit_summary(audits or [], scored)
    report["probes"] = probe_summary(probes or [], decisions, cases)
    report["routing_value"] = routing_value(scored)
    report["economics"] = economic_report(cases, decisions, attempts or [], scenarios, probes or [])
    report["learning_transfer"] = learning_transfer(cases, decisions)
    report["economics"]["learning"]["artifact_reuse_check"] = report["learning_transfer"]["status"]
    return report


def _load_records(directory: Path, stem: str) -> list[dict]:
    path = directory / f"{stem}.jsonl"
    if path.exists():
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    path = directory / f"{stem}.json"
    if path.exists():
        content = json.loads(path.read_text())
        if isinstance(content, list):
            return content
        if stem in content and isinstance(content[stem], list):
            return content[stem]
        raise ValueError(f"Expected list of {stem} records in {path}")
    return []


def evaluate_run(path: str | Path) -> dict:
    root = Path(path)
    cases = _load_records(root, "cases")
    if not cases:
        raise ValueError("Missing frozen cases; replay must not silently use a changed corpus")
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    return evaluate(cases, _load_records(root, "decisions"), _load_records(root, "attempts"),
                    _load_records(root, "audits"), _load_records(root, "probes"),
                    policy_populations=manifest.get("policy_populations"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = json.dumps(evaluate_run(args.run), indent=2, sort_keys=True, allow_nan=False) + "\n"
    if args.output:
        args.output.write_text(result)
    else:
        print(result, end="")


if __name__ == "__main__":
    main()
