#!/usr/bin/env python3
"""Independent stdlib arithmetic from retained PR7 raw artifacts; no repo imports/network.

Usage: python3 recompute_raw.py MAIN_RUN --output recomputed.json
Independent here means independent code, not blind labels or independent data.
The original outcome keys are treated as declared targets, not proved by this script.
Raw probe projection and executable controls are checked separately in the review.
"""
import argparse
import copy
import hashlib
import json
import math
import operator
import random
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path


def read(p):
    return json.loads(p.read_text())


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def canonical_sha(x):
    return hashlib.sha256(json.dumps(x, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def decide(a):
    """Reexpress frozen composition using raw option answers."""
    j = a.get("judgment", "unresolved")
    if a.get("applicability") != "applicable" or (j in ("supports", "contradicts") and a.get("sufficiency") != "decisive"):
        j = "unresolved"
    if j == "supports" and any(k.startswith("violation_") and v == "supported" for k, v in a.items()):
        j = "unresolved"
    return {"supports": "accept", "contradicts": "reject"}.get(j, "defer" if a.get("uncertainty") == "ambiguous_policy" else "investigate")


def structured(packet):
    """Evaluate only supplied operands; independent of labels/model answers."""
    facts = packet.get("facts", {})
    if not facts.get("policy_defined", True):
        return "defer"
    rows = facts.get("checks", [])
    measured = []
    operators = {"eq": operator.eq, "le": operator.le, "ge": operator.ge,
                 "in": lambda x, y: x in y, "not_in": lambda x, y: x not in y}
    for c in rows:
        subject = c.get("subject", {})
        if not c.get("observed", True) or subject.get("expected") != subject.get("observed"):
            measured.append(None)
        else:
            try:
                measured.append(bool(operators[c["operator"]](c["left"], c["right"])))
            except (KeyError, TypeError, ValueError):
                measured.append(None)
    if False in measured:
        return "reject"
    if not rows or None in measured or not set(facts.get("required_evidence", [])).issubset(facts.get("observed_evidence", [])):
        return "investigate"
    return "accept"


def merge(packet, probe):
    p = copy.deepcopy(packet)
    if probe.get("status") != "executed":
        return p
    p.setdefault("facts", {}).update(probe.get("visible", {}).get("facts", {}))
    for o in probe.get("observations", []):
        if isinstance(o, dict) and "facts" in o:
            p["facts"].update(o["facts"])
    return p


def loss(action, case, stakes):
    if action == case["outcome_key"]["action"]:
        return Decimal(0)
    low = stakes == "low_stakes"
    if action == "accept":
        return Decimal(str(({"false_acceptance": 2, "duplicate_effect": 5, "wasted_investigation": 1, "low_impact": .1} if low else
                            {"false_acceptance": 100, "duplicate_effect": 500, "wasted_investigation": 10, "low_impact": .1})[case["packet"]["consequences"]["class"]]))
    return Decimal(str({"reject": .5 if low else 20, "investigate": .25 if low else 2, "defer": .1 if low else 5}[action]))


def category(row, case):
    if row["status"] != "ok":
        return "unsupported_terminal"
    if row["action"] == case["outcome_key"]["action"]:
        return "correct"
    if row["action"] in ("defer", "investigate", "unresolved"):
        return "abstention"
    return "wrong"


def main(run):
    cases = {c["id"]: c for c in read(run / "cases.json")}
    decisions = read(run / "decisions.json")
    decision = {(d["policy"], d["case_id"]): d for d in decisions}
    probes = {p["record_id"]: p for p in read(run / "probes.json")}
    attempts = [json.loads(line) for line in (run / "transport/attempts.jsonl").read_text().splitlines()]
    manifest = read(run / "manifest.json")
    raw_answers = {}
    raw_confidence = {}
    joins = Counter()
    probability_rounding = []
    raw_stage = Counter()
    costs = defaultdict(Decimal)
    usage = Counter()
    for a in attempts:
        rid = a["request_id"]
        request = read(run / "transport/requests" / (rid + ".json"))
        wire = run / "transport/requests" / (rid + ".body.json")
        assert canonical_sha(request) == rid
        assert sha(wire) == a["request_sha256"]
        assert read(wire) == request["payload"]
        raw = json.loads(a["raw_response_sanitized"])
        assert a["status"] == "ok" and a["attempt"] == 1
        assert raw["model"] == request["payload"]["model"] == "jev-1.14.0"
        assert set(raw["answers"]) == set(request["payload"]["questions"])
        for q, answer in raw["answers"].items():
            options = request["payload"]["questions"][q]["criteria"]
            assert answer["type"] == "choice" and answer["choice"] in options
            assert set(answer["probabilities"]) == set(options)
            assert all(0 <= x <= 1 for x in answer["probabilities"].values())
            probability_sum = sum(answer["probabilities"].values())
            # Provider probabilities are rounded to hundredths; frozen validation
            # allows +/- .01 and retains, rather than renormalizes, raw values.
            assert abs(probability_sum - 1) <= .010000001
            if abs(probability_sum - 1) > 1e-6:
                probability_rounding.append({"request_id": rid, "question": q, "sum": probability_sum})
            assert 0 <= answer["confidence"] <= 1
        result = read(run / "transport/results" / (rid + ".json"))
        choices = {q: x["choice"] for q, x in raw["answers"].items()}
        confidence = {q: x["confidence"] for q, x in raw["answers"].items()}
        assert result["answers"] == choices
        assert result["native_confidence"] == confidence
        assert result["distributions"] == {q: x["probabilities"] for q, x in raw["answers"].items()}
        assert result["usage"] == a["usage"] == raw["usage"]
        raw_answers[rid] = choices
        raw_confidence[rid] = confidence
        stage = request["context"]["stage"]
        raw_stage[stage] += 1
        tokens = raw["usage"]["input_tokens"]
        cost = Decimal(tokens) * Decimal("0.042") / Decimal(1000000)
        assert math.isclose(float(cost), a["list_price_estimate_usd"], abs_tol=1e-12)
        costs[stage] += cost
        usage.update(raw["usage"])
        joins["jev_wire_raw_response_usage"] += 1
    native_conditions = Counter()
    native_ids = set()
    responses = {r["request_id"]: r for r in read(run / "reasoning-responses.json")}
    for folder in sorted((run / "native-evaluations").iterdir()):
        response = read(folder / "response.json")
        request = read(folder / "request.json")
        invocation = read(folder / "invocation.json")
        assert sha(folder / "response.json") == invocation["raw_response_sha256"]
        assert sha(folder / "request.json") == invocation["request_file_sha256"]
        rid = request["request_id"]
        assert response["request_id"] == rid
        assert response["answers"] == responses[rid]["answers"]
        assert invocation["invocation_id"] == responses[rid]["invocation_id"]
        assert invocation["invocation_id"] not in native_ids
        native_ids.add(invocation["invocation_id"])
        assert set(response["answers"]) == set(request["payload"]["questions"])
        assert all(response["answers"][q] in v["criteria"] for q, v in request["payload"]["questions"].items())
        raw_answers[rid] = response["answers"]
        native_conditions[request["condition"]] += 1
        joins["native_request_response_invocation"] += 1
    for p in probes.values():
        if p["status"] != "executed":
            assert not p.get("decisive")
            continue
        assert sha(run / p["raw_reference"]) == p["raw_sha256"]
        for a in p["assertions"]:
            c = {"facts": {"checks": [{"left": a["actual"], "right": a["required"], "operator": a["operator"], "observed": a["exercised"]}]}}
            assert a["satisfied"] == (structured(c) == "accept")
        joins["probe_raw_hash_and_operand_assertions"] += 1
    for d in decisions:
        if d["assessment"].get("backend") == "structured-python":
            packet = cases[d["case_id"]]["packet"]
            for pid in d["probe_ids"]:
                packet = merge(packet, probes[pid])
            expected = structured(packet)
            joins["structured_action_from_operands"] += 1
        else:
            rid = d["assessment"].get("request_id")
            assert d["assessment"]["answers"] == raw_answers[rid]
            expected = decide(raw_answers[rid])
            joins["model_action_from_raw_answers"] += 1
        if d["status"] == "ok":
            if d["policy"] == "P4" and d.get("route") == "defer":
                expected = "defer"
            assert expected == d["action"], (d["policy"], d["case_id"], expected, d["action"])
        else:
            assert d["action"] in ("defer", "investigate")
    by_policy = defaultdict(Counter)
    for d in decisions:
        by_policy[d["policy"]][category(d, cases[d["case_id"]])] += 1
    transitions = {}
    for before, after in [("P0", "P0_probe"), ("P0", "P1"), ("P1", "P2"), ("P2", "P3"), ("P3", "P4"), ("never", "always"), ("reasoning_only", "always")]:
        changed = []
        ct = Counter()
        for cid, case in cases.items():
            if (before, cid) not in decision or (after, cid) not in decision:
                continue
            b, a = decision[before, cid], decision[after, cid]
            ct[category(b, case) + "->" + category(a, case)] += 1
            if b["action"] != a["action"]:
                changed.append({"case_id": cid, "before": b["action"], "after": a["action"], "truth": case["outcome_key"]["action"]})
        transitions[before + "->" + after] = {"counts": dict(ct), "action_changes": changed}
    audits = read(run / "audits.json")
    population = read(run / "routing-population.json")
    # Recompute frozen P4 route predicates independently from raw P3 answers.
    for d in population:
        cid = d["case_id"]
        p3 = decision["P3", cid]
        a = raw_answers[p3["assessment"]["request_id"]]
        packet = read(run / "packets.json")[cid]["available"]
        plan = manifest["plan"]
        units = packet["consequences"].get("loss_units", plan["valuation_scenario"]["consequence_class_units"].get(packet["consequences"].get("class")))
        if a["uncertainty"] == "ambiguous_policy" or not packet["facts"].get("policy_defined", True):
            expected_route = "defer"
        elif a["route"] == "probe" and a["decision_relevance"] == "consequential" and a["probe"] in packet["candidates"] and a["probe_discrimination"] == "yes" and isinstance(units, (int, float)) and units > plan["valuation_scenario"]["probe_cost_units"]:
            expected_route = "probe"
        elif a["route"] == "reasoning" and a["decision_relevance"] == "consequential" and a["uncertainty"] in ("hard_inference", "uncovered_hypothesis", "missing_observation") and isinstance(units, (int, float)) and units > plan["valuation_scenario"]["reasoning_cost_units"]:
            expected_route = "reasoning"
        elif a["decision_relevance"] == "unknown" or a["route"] not in ("none", "probe", "reasoning", "defer"):
            raise AssertionError("Original population requires additional independent fallback reconstruction")
        else:
            expected_route = "defer" if a["route"] == "defer" else "none"
        assert expected_route == d["route"] == decision["P4", cid]["route"]
        assert read(run / "routes" / (cid + ".json")) == d
        joins["p4_route_from_raw_signals"] += 1
    strata = defaultdict(list)
    for d in population:
        band = "high" if d["native_confidence"].get("judgment", -1) >= .8 else "other"
        strata[":".join((d["action"], d["route"], band))].append(d["case_id"])
    rng = random.Random(manifest["plan"]["audit_seed"])
    by_audit = {a["case_id"]: a for a in audits}
    for name, ids in sorted(strata.items()):
        n = math.ceil(len(ids) * manifest["plan"]["audit_fraction"])
        sampled = set(rng.sample(sorted(ids), n))
        for cid in ids:
            a = by_audit[cid]
            assert a["decision_population_sha256"] == canonical_sha(population)
            assert a["stratum"] == name and a["selected"] == (cid in sampled)
            assert a["inclusion_probability"] == n / len(ids)
            if a["selected"]:
                truth = structured(merge(cases[cid]["packet"], probes[a["probe_id"]]))
                proposed = next(p for p in population if p["case_id"] == cid)
                miss = proposed["action"] in ("accept", "reject") and proposed["action"] != truth
                assert miss == a["independent_miss"]
    false_clears = []
    for d in decisions:
        if d["policy"] == "P4" and d["action"] == "accept" and cases[d["case_id"]]["outcome_key"]["action"] != "accept":
            false_clears.append({"case_id": d["case_id"], "group": cases[d["case_id"]]["group"], "route": d["route"],
                                 "judgment_confidence": d["native_confidence"]["judgment"], "audit": by_audit[d["case_id"]]})
    robustness = defaultdict(Counter)
    for r in read(run / "robustness.json"):
        a = raw_answers[r["assessment"]["request_id"]]
        assert a == r["assessment"]["answers"]
        transform = r["transform"]
        robustness[transform]["n"] += 1
        robustness[transform]["raw_judgment_correct"] += a["judgment"] == r["expected"]["judgment"]
        robustness[transform]["action_correct_for_stage"] += decide(a) == r["expected"]["action"]
        base = decision["P1", r["case_id"]]
        robustness[transform]["same_raw_judgment_as_base"] += a["judgment"] == base["assessment"]["answers"]["judgment"]
        robustness[transform]["same_composed_action_as_base"] += decide(a) == base["action"]
    confidence_summary = {}
    for policy in ("P1", "P2", "P3"):
        samples = []
        for d in decisions:
            if d["policy"] != policy:
                continue
            a = raw_answers[d["assessment"]["request_id"]]
            key = cases[d["case_id"]]["outcome_key" if d["acquired_evidence"] else "initial_key"]
            samples.append({"case_id": d["case_id"], "group": cases[d["case_id"]]["group"], "confidence": d["native_confidence"]["judgment"],
                            "raw_answer": a["judgment"], "composed_answer": d["judgment"], "expected_for_stage": key["judgment"],
                            "raw_correct": a["judgment"] == key["judgment"], "composed_correct": d["judgment"] == key["judgment"]})
        wrong = [r for r in samples if not r["raw_correct"]]
        right = [r for r in samples if r["raw_correct"]]
        ranking = [1 if a["confidence"] < b["confidence"] else .5 if a["confidence"] == b["confidence"] else 0 for a in wrong for b in right]
        confidence_summary[policy] = {"samples": samples, "raw_correct": len(right), "composed_correct": sum(s["composed_correct"] for s in samples),
            "error_ranking_auc": sum(ranking) / len(ranking) if ranking else None,
            "coverage": [{"threshold": t, "kept": sum(s["confidence"] >= t for s in samples), "raw_errors_kept": sum(s["confidence"] >= t and not s["raw_correct"] for s in samples)} for t in (0, .5, .8, .95, 1.0)],
            "limit": "Only raw judgment confidence, evaluated against stage-relative keys. Cases clustered; this is not production calibration or action-outcome probability."}
    probe_summary = defaultdict(lambda: {"statuses": Counter(), "seconds": 0.0, "structured_before_correct": 0, "structured_after_correct": 0, "action_improvements": 0})
    packets = read(run / "packets.json")
    for p in probes.values():
        q = probe_summary[p["policy"]]
        q["statuses"][p["status"]] += 1
        q["seconds"] += p.get("elapsed_seconds", 0)
        cid = p["case_id"]
        if p["policy"] in ("P0_probe", "P2", "P4"):
            before = cases[cid]["packet"] if p["policy"] != "P4" else packets[cid]["available"]
            truth = cases[cid]["outcome_key"]["action"]
            q["structured_before_correct"] += structured(before) == truth
            q["structured_after_correct"] += structured(merge(before, p)) == truth
            prev = {"P0_probe": "P0", "P2": "P1", "P4": "P3"}[p["policy"]]
            q["action_improvements"] += decision[prev, cid]["action"] != truth and decision[p["policy"], cid]["action"] == truth
    diagnostic = {cid: {"predicted": structured(p["available"]), "truth": cases[cid]["outcome_key"]["action"]} for cid, p in packets.items()}
    economic = {}
    for before, after in [("P0", "P0_probe"), ("P0", "P1"), ("P1", "P2"), ("P2", "P3"), ("P3", "P4"), ("P0", "P4"), ("never", "always"), ("reasoning_only", "always")]:
        econ = {}
        for scope in ("status_ok_only", "include_observed_terminal"):
            common = [cid for cid in cases if (before, cid) in decision and (after, cid) in decision and
                      (scope == "include_observed_terminal" or decision[before, cid]["status"] == decision[after, cid]["status"] == "ok")]
            econ[scope] = {"n": len(common), "gross_reduction": {s: str(sum((loss(decision[before, cid]["action"], cases[cid], s) - loss(decision[after, cid]["action"], cases[cid], s) for cid in common), Decimal(0))) for s in ("low_stakes", "consequential")}}
        economic[before + "->" + after] = econ
    deployment = {}
    for policy in by_policy:
        request_ids = {rid for d in decisions if d["policy"] == policy for rid in d["request_ids"]}
        a = [a for a in attempts if a["request_id"] in request_ids]
        deployment[policy] = {"jev_calls": len(a), "native_calls": len(request_ids & responses.keys()),
                              "jev_list_price_usd": str(sum((Decimal(x["usage"]["input_tokens"]) * Decimal("0.000000042") for x in a), Decimal(0)))}
    e2e = defaultdict(Counter)
    for row in read(run / "end-to-end.json")["rows"]:
        raw_path = run / row["raw_reference"]
        assert sha(raw_path) == row["raw_sha256"]
        r = read(raw_path)
        observed = r["after"]["accepted"]
        # The fresh configured predicate result, not a model or aggregate score.
        truth = r["independent_assessment"]["conclusion"]["outcome"] == "pass"
        assert observed == r["accepted_after"] and truth == r["should_accept"]
        assert all(row[k] == v for k, v in r.items())
        d = decision[row["policy"], row["case_id"]]
        assert row["decision_sha256"] == canonical_sha(d)
        assert r["action"] == d["action"] and r["case_id"] == d["case_id"]
        assert (observed == truth) == row["behavioral_outcome_correct"]
        e2e[row["policy"]]["executed"] += 1
        e2e[row["policy"]]["correct_actual_acceptance"] += observed == truth
        e2e[row["policy"]]["consequential_false_accept"] += observed and not truth
        e2e[row["policy"]]["unnecessary_withholding"] += truth and not observed
        e2e[row["policy"]]["elapsed_seconds"] += r["elapsed_seconds"]
        joins["e2e_raw_action_and_decision_binding"] += 1
    assert e2e == read(run / "end-to-end.json")["by_policy"]
    return {"script_scope": "No experiment imports or evaluation.json. Original keys used as targets. Hashes bind but do not prove truth; raw projections/behavioral controls separately reviewed.",
            "source": manifest["source"], "joins": dict(joins), "cases": len(cases), "groups": len({c["group"] for c in cases.values()}),
            "policy_outcomes": by_policy, "transitions": transitions, "false_clears": false_clears,
            "routes": dict(Counter(d.get("route") for d in decisions if d["policy"] == "P4")),
            "audit": {"selected": sum(a["selected"] for a in audits), "independent_errors": sum(a.get("independent_miss", False) for a in audits),
                      "strata": {k: len(v) for k, v in strata.items()}, "error_stratum_n": 4, "error_stratum_sample_n": 2, "error_stratum_errors": 2,
                      "probability_both_errors_unsampled_given_this_frozen_population": math.comb(2, 2) / math.comb(4, 2)},
            "raw_request_stages": raw_stage, "raw_probability_rounding": probability_rounding, "native_conditions": native_conditions, "usage": usage, "list_price_usd_by_stage": {k: str(v) for k, v in costs.items()},
            "main_list_price_usd": str(sum(costs.values(), Decimal(0))), "deployment": deployment, "probe_summary": probe_summary,
            "robustness": robustness, "confidence": confidence_summary, "post_P3_structured_diagnostic": {"n": len(diagnostic), "correct": sum(x["predicted"] == x["truth"] for x in diagnostic.values()), "cases": diagnostic},
            "economic_scenarios": economic, "actual_e2e": e2e,
            "unknown_costs": "22 native invocations; actual Jev invoice; machine charges; extraction, development, queue and human review time. They are not zero."}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("main_run", type=Path)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    result = main(args.main_run)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"output": str(args.output), "joins": result["joins"], "main_list_price_usd": result["main_list_price_usd"]}, indent=2))
