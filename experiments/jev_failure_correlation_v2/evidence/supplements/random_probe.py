"""Prospectively frozen, post-hoc equal-call probe-routing supplement.

The frozen main experiment is read-only. Selection functions accept only visible
packets. Hidden subject selectors enter only after plan freezing, inside the
existing executable probe adapter. No model or network calls are made.
"""
from __future__ import annotations
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from experiments.jev_failure_correlation_v2.economics import SCENARIOS, loss, accounting
from experiments.jev_failure_correlation_v2.policies import baseline, merge_observation, check_fact
from experiments.jev_failure_correlation_v2.probes import execute_probe, view, assertion_rows

VERSION = "posthoc-random-probe-1"
MAIN_SOURCE = "9c6d7612e31bb78eb07868b5ac2a046629c5876c"


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def candidate(packet):
    """Choose a declared boundary operation from visible requirements only.

    Deliberately usable on both decisive and missing observations; spending a
    random slot on a resolved boundary is allowed and charged. No case identifier,
    scenario, outcome key, stored probe selector, or model answer is accepted.
    """
    checks = packet.get("facts", {}).get("checks", [])
    invariants = {c.get("invariant") for c in checks}
    notes = " ".join(str(c.get("note", "")) for c in checks).lower()
    if "argument_integrity" in invariants:
        selected = "p_arguments"
    elif "assertion_execution" in invariants:
        selected = "p_assertion"
    elif "authority_binding" in invariants:
        selected = "p_authority"
    elif "concurrent_activity" in invariants:
        selected = "p_busy"
    elif "current_evidence" in invariants:
        selected = "p_replay" if "requirement revision" in notes or any(type(c.get("right")) is int for c in checks) else "p_context"
    elif "effect_uncertainty" in invariants:
        selected = "p_attempts" if any(c.get("right") == "submitted" for c in checks) else "p_effect"
    else:
        selected = "insufficient"
    return selected if selected in packet.get("candidates", {}) else "insufficient"


def visible_signals(packet, value_map):
    checks = packet.get("facts", {}).get("checks", [])
    unknown = sum(check_fact(c) is None for c in checks)
    consequence = packet.get("consequences", {}).get("class")
    return {"missing_observation_count": unknown,
            "consequence_class": consequence,
            "assumed_consequence_units": value_map.get(consequence, 0),
            "candidate": candidate(packet)}


def verify_main(main, expected=None):
    manifest = read(main / "manifest.json")
    if manifest["source"]["commit"] != MAIN_SOURCE:
        raise ValueError("Unexpected main measured source")
    for name, expected_sha in manifest["source"]["source_hashes"].items():
        if sha(ROOT / name) != expected_sha:
            raise ValueError("Measured source changed: " + name)
    if expected:
        for name, expected_sha in expected.items():
            if sha(main / name) != expected_sha:
                raise ValueError("Main evidence changed after supplement preparation: " + name)
    return manifest


def prepare(main, out):
    main, out = main.resolve(), out.resolve()
    manifest = verify_main(main)
    if out.exists():
        raise FileExistsError("Fresh supplement directory required")
    packets = read(main / "packets.json")
    rows = read(main / "decisions.json")
    ids = sorted(manifest["paired_case_ids"])
    p4 = {r["case_id"]: r for r in rows if r["policy"] == "P4" and r["case_id"] in ids}
    p3 = {r["case_id"]: r for r in rows if r["policy"] == "P3" and r["case_id"] in ids}
    if len(ids) != 11 or len(p4) != len(ids) or len(p3) != len(ids):
        raise ValueError("Expected exact complete paired population of eleven")
    k = sum(row.get("route") in {"probe", "reasoning"} for row in p4.values())
    seed = manifest["plan"]["random_escalation_seed"]
    value_map = manifest["plan"]["valuation_scenario"]["consequence_class_units"]
    available = {cid: packets[cid]["available"] for cid in ids}
    signals = {cid: visible_signals(packet, value_map) for cid, packet in available.items()}
    random_ids = sorted(random.Random(seed).sample(ids, k))
    risk_order = sorted(ids, key=lambda cid: (
        -int(signals[cid]["missing_observation_count"] > 0),
        -signals[cid]["missing_observation_count"],
        -signals[cid]["assumed_consequence_units"], cid))
    simple_ids = sorted(risk_order[:k])
    inputs = ["manifest.json", "packets.json", "decisions.json", "probes.json", "cases.json", "transport/attempts.jsonl"]
    script = Path(__file__).resolve()
    plan = {"version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
            "classification": "POSTHOC design after inspecting main route counts; selections frozen prospectively before new probe execution",
            "main_run": str(main), "main_source_commit": MAIN_SOURCE,
            "preparation_commit": git("rev-parse", "HEAD"),
            "script": str(script.relative_to(ROOT)), "script_sha256": sha(script),
            "plan_document_sha256": sha(script.with_name("PLAN.md")),
            "main_files": {name: sha(main / name) for name in inputs},
            "paired_case_ids": ids, "population_size": len(ids), "investigation_slots": k,
            "random_seed": seed, "random_selection": random_ids,
            "simple_selection": simple_ids, "simple_priority_order": risk_order,
            "simple_rule": "Missing observations first; missing check count descending; frozen consequence units descending; case ID ascending ties.",
            "candidate_rule": "Fixed non-Jev visible invariant/requirement dispatch; see committed script. Hidden scenario/key/probe_id unavailable to selector.",
            "signals": signals, "valuation_scenarios": copy.deepcopy(SCENARIOS),
            "terminal_unresolved_cost": "Retained investigate/defer actions carry frozen scenario handling/delay loss; provider/invalid unknown actions stay unknown, not zero.",
            "visible_packets_sha256": digest(available),
            "p3_decision_sha256": {cid: digest(p3[cid]) for cid in ids},
            "main_p4_decision_sha256": {cid: digest(p4[cid]) for cid in ids},
            "limits": {"new_model_calls": 0, "new_probe_calls": 2 * k, "maximum_seconds": 180,
                       "concurrency": 1, "retries": 0},
            "comparison_basis": "Equal additional investigation call count proxy, not matched dollars or elapsed time. Prefix P3 observations and request identities identical.",
            "confounds": ["Post-hoc design and outcome exposure during implementation; no confirmatory holdout claim.",
                         "Main P4 uses Jev's candidate; random/simple use fixed non-Jev candidate dispatch. Candidate selection and routing jointly vary.",
                         "Every selected supplement result uses the same frozen deterministic interpretation as main P4 successful probes.",
                         "Static action replay with real new probes; no new end-to-end production outcome or monetized learning measured.",
                         "One random seed on eleven clustered cases cannot estimate policy performance precisely."]}
    out.mkdir(parents=True)
    write(out / "visible-packets.json", available)
    write(out / "plan.json", plan)
    (out / "plan.sha256").write_text(sha(out / "plan.json") + "\n")
    return {"out": str(out), "plan_sha256": sha(out / "plan.json"), "k": k,
            "random_selection": random_ids, "simple_selection": simple_ids}


def checked_plan(out, main_override=None):
    if sha(out / "plan.json") != (out / "plan.sha256").read_text().strip():
        raise ValueError("Prepared plan changed")
    plan = read(out / "plan.json")
    if sha(Path(__file__)) != plan["script_sha256"] or sha(Path(__file__).with_name("PLAN.md")) != plan["plan_document_sha256"]:
        raise ValueError("Supplement implementation changed after preparation")
    if digest(read(out / "visible-packets.json")) != plan["visible_packets_sha256"]:
        raise ValueError("Visible packets changed")
    main = Path(main_override).resolve() if main_override else Path(plan["main_run"])
    manifest = verify_main(main, plan["main_files"])
    ids = sorted(manifest["paired_case_ids"])
    original = read(main / "decisions.json")
    k = sum(r.get("route") in {"probe", "reasoning"} for r in original if r["policy"] == "P4" and r["case_id"] in ids)
    if ids != plan["paired_case_ids"] or k != plan["investigation_slots"] or manifest["plan"]["random_escalation_seed"] != plan["random_seed"]:
        raise ValueError("Population or call budget changed")
    packets = {cid: read(main / "packets.json")[cid]["available"] for cid in ids}
    if packets != read(out / "visible-packets.json"):
        raise ValueError("Supplement packets differ from applicable main P3 evidence")
    signals = {cid: visible_signals(packet, manifest["plan"]["valuation_scenario"]["consequence_class_units"]) for cid, packet in packets.items()}
    order = sorted(ids, key=lambda cid: (-int(signals[cid]["missing_observation_count"] > 0), -signals[cid]["missing_observation_count"], -signals[cid]["assumed_consequence_units"], cid))
    if signals != plan["signals"] or sorted(order[:k]) != plan["simple_selection"] or order != plan["simple_priority_order"]:
        raise ValueError("Simple selection differs from visible-fact replay")
    if sorted(random.Random(plan["random_seed"]).sample(ids, k)) != plan["random_selection"]:
        raise ValueError("Random selection differs from frozen seeded replay")
    return plan


def _fresh_row(base, policy, assessment, record):
    row = copy.deepcopy(base)
    row.update(policy=policy, route="probe", stage="posthoc_deterministic_probe",
               probe_ids=base.get("probe_ids", []) + [record["record_id"]],
               status="ok", assessment=assessment, answers=assessment["answers"],
               judgment=assessment["judgment"], action=assessment["action"],
               violations=assessment["violations"], relationship=assessment["relationship"],
               acquired_evidence=True, native_confidence={}, distributions={})
    return row


def execute(out):
    out = out.resolve()
    plan = checked_plan(out)
    # User requested a concrete committed plan before this supplementary execution.
    relative = plan["script"]
    committed = subprocess.check_output(["git", "show", "HEAD:" + relative], cwd=ROOT)
    if hashlib.sha256(committed).hexdigest() != plan["script_sha256"]:
        raise ValueError("Supplement script must be committed unchanged before execution")
    committed_plan = subprocess.check_output(["git", "show", "HEAD:" + str(Path(relative).with_name("PLAN.md"))], cwd=ROOT)
    if hashlib.sha256(committed_plan).hexdigest() != plan["plan_document_sha256"]:
        raise ValueError("Plan document must be committed unchanged before execution")
    execution = out / "execution"
    execution.mkdir(exist_ok=False)
    main = Path(plan["main_run"])
    cases = {c["id"]: c for c in read(main / "cases.json")}
    original = read(main / "decisions.json")
    p3 = {r["case_id"]: r for r in original if r["policy"] == "P3"}
    packets = read(out / "visible-packets.json")
    records, decisions = [], []
    started = time.monotonic()
    write(out / "execution-identity.json", {"commit": git("rev-parse", "HEAD"), "tree": git("rev-parse", "HEAD^{tree}"),
          "plan_sha256": sha(out / "plan.json"), "started_at": datetime.now(timezone.utc).isoformat(),
          "script_sha256": plan["script_sha256"], "main_source": MAIN_SOURCE})
    for policy, selected in (("random_probe", plan["random_selection"]), ("simple_probe", plan["simple_selection"])):
        for cid in plan["paired_case_ids"]:
            base = p3[cid]
            if digest(base) != plan["p3_decision_sha256"][cid]:
                raise ValueError("P3 prefix changed")
            row = copy.deepcopy(base)
            row.update(policy=policy, route="none", stage="posthoc_no_additional_probe",
                       prefix_decision_sha256=digest(base), selected_for_additional_probe=cid in selected)
            if cid in selected:
                if time.monotonic() - started > plan["limits"]["maximum_seconds"]:
                    raise TimeoutError("Supplement bounded wall-time exhausted")
                pid = candidate(packets[cid])
                if pid != plan["signals"][cid]["candidate"]:
                    raise ValueError("Candidate selection changed")
                record = execute_probe(cases[cid], pid, execution / (policy + "-" + cid))
                record.update(policy=policy, record_id=policy + "-" + cid,
                              selection_plan_sha256=sha(out / "plan.json"), purpose="posthoc_routing_comparison")
                if record.get("raw_reference"):
                    record["raw_reference"] = str(Path(record["raw_reference"]).relative_to(out))
                records.append(record)
                if record.get("status") == "executed" and record.get("decisive"):
                    updated = merge_observation(packets[cid], record)
                    assessment = baseline(updated)
                    row = _fresh_row(row, policy, assessment, record)
                    row["updated_packet_sha256"] = digest(updated)
                    write(execution / (policy + "-" + cid) / "visible-updated.json", updated)
                else:
                    row.update(route="probe", status="unresolved", judgment="unresolved", action="defer",
                               probe_ids=base.get("probe_ids", []) + [record["record_id"]])
                    row["answers"] = {**row["answers"], "action": "defer", "judgment": "unresolved"}
            decisions.append(row)
            write(out / "probes.json", records)
            write(out / "decisions.json", decisions)
    report = summarize(out, plan, cases, original, records, decisions)
    write(out / "report.json", report)
    return report


def summarize(out, plan, cases, original, new_probes, new_decisions, main_override=None):
    main = Path(main_override).resolve() if main_override else Path(plan["main_run"])
    ids = set(plan["paired_case_ids"])
    p3 = {r["case_id"]: r for r in original if r["policy"] == "P3" and r["case_id"] in ids}
    main_probes = read(main / "probes.json")
    main_p4 = [r for r in original if r["policy"] == "P4" and r["case_id"] in ids]
    all_rows = [*p3.values(), *main_p4, *new_decisions]
    attempts = [json.loads(line) for line in (main / "transport/attempts.jsonl").read_text().splitlines() if line]
    prefix_ids = {rid for row in p3.values() for rid in row.get("request_ids", [])}
    prefix_attempts = [a for a in attempts if a.get("request_id") in prefix_ids]
    prefix_probe_ids = {pid for row in p3.values() for pid in row.get("probe_ids", [])}
    prefix_probes = [p for p in main_probes if p.get("record_id") in prefix_probe_ids]
    report = {"version": VERSION, "classification": "posthoc supplement; no main records modified",
              "plan_sha256": sha(out / "plan.json"), "source_identity": read(out / "execution-identity.json"),
              "population": len(ids), "dependency_clusters": len({cases[cid]["group"] for cid in ids}),
              "additional_call_budget_each": plan["investigation_slots"],
              "new_model_calls": 0, "new_probe_calls": len(new_probes),
              "additional_actual_model_charge_usd": 0,
              "additional_actual_compute_charge_usd": None,
              "new_probe_serial_seconds": sum(p["elapsed_seconds"] for p in new_probes),
              "common_P3_prefix": {"request_ids": sorted(prefix_ids), "probe_ids": sorted(prefix_probe_ids),
                 "model_accounting": accounting(prefix_attempts),
                 "probe_serial_seconds": sum(p["elapsed_seconds"] for p in prefix_probes),
                 "note": "Already incurred main collection reused under identical inputs. Routine policy totals include this same prefix; incremental comparisons cancel it."},
              "policies": {}, "limitations": plan["confounds"], "terminal_unresolved_cost": plan["terminal_unresolved_cost"]}
    for policy in ("P3", "P4", "random_probe", "simple_probe"):
        rows = [r for r in all_rows if r["policy"] == policy]
        if len(rows) != len(ids):
            raise ValueError("Incomplete policy population")
        refs = {pid for row in rows for pid in row.get("probe_ids", [])} - prefix_probe_ids
        extra = [p for p in [*main_probes, *new_probes] if p.get("record_id") in refs]
        seconds = sum(p["elapsed_seconds"] for p in extra)
        corrections = damage = unchanged_wrong = unchanged_right = unknown = 0
        unresolved_before = unresolved_after = resolved_correct = resolved_wrong = became_unresolved = action_gains = action_damage = 0
        for row in rows:
            truth = cases[row["case_id"]]["outcome_key"]["action"]
            before = p3[row["case_id"]]["action"] == truth
            after = row["action"] == truth
            before_row = p3[row["case_id"]]
            before_decisive = before_row.get("judgment") in {"supports", "contradicts"} and before_row.get("action") in {"accept", "reject"} and before_row.get("status") == "ok"
            after_decisive = row.get("judgment") in {"supports", "contradicts"} and row.get("action") in {"accept", "reject"} and row.get("status") == "ok"
            corrections += before_decisive and after_decisive and not before and after
            damage += before_decisive and after_decisive and before and not after
            unchanged_wrong += before_decisive and after_decisive and not before and not after
            unchanged_right += before_decisive and after_decisive and before and after
            unresolved_before += not before_decisive
            unresolved_after += not after_decisive
            resolved_correct += not before_decisive and after_decisive and after
            resolved_wrong += not before_decisive and after_decisive and not after
            became_unresolved += before_decisive and not after_decisive
            action_gains += not before and after
            action_damage += before and not after
            unknown += row.get("status") not in {"ok"}
        info = {"actions": {a: sum(r["action"] == a for r in rows) for a in ("accept", "reject", "investigate", "defer")},
                "correct_actions": sum(r["action"] == cases[r["case_id"]]["outcome_key"]["action"] for r in rows),
                "corrections_vs_P3": corrections, "damage_vs_P3": damage,
                "unchanged_wrong": unchanged_wrong, "unchanged_right": unchanged_right, "non_ok_status": unknown,
                "unresolved_before": unresolved_before, "unresolved_after": unresolved_after,
                "resolved_uncertainty_correct": resolved_correct, "resolved_uncertainty_incorrect": resolved_wrong,
                "became_unresolved": became_unresolved,
                "action_accuracy_gains_including_resolutions": action_gains,
                "action_accuracy_losses_including_unresolved": action_damage,
                "transition_basis": "Corrections/damage and unchanged right/wrong require decisive actions on both sides. Uncertainty transitions reported separately.",
                "additional_probe_calls": len(extra), "additional_probe_seconds": seconds,
                "additional_probe_unknown_monetary_cost": bool(extra),
                "probe_records": sorted(refs), "decision_sha256": {r["case_id"]: digest(r) for r in rows},
                "scenarios": {}}
        for name, scenario in plan["valuation_scenarios"].items():
            losses = [loss(cases[r["case_id"]], r["action"], scenario) for r in rows]
            before = [loss(cases[cid], p3[cid]["action"], scenario) for cid in plan["paired_case_ids"]]
            known_total = sum(v for v in losses if v is not None)
            total = known_total if all(v is not None for v in losses) else None
            gross = sum(before) - total if total is not None and all(v is not None for v in before) else None
            info["scenarios"][name] = {"outcome_loss_usd_assumed": total,
                "known_loss_subtotal_usd_assumed": known_total,
                "unknown_loss_cases": sum(v is None for v in losses),
                "unknown_P3_loss_cases": sum(v is None for v in before),
                "gross_loss_reduction_vs_P3_usd_assumed": gross,
                "probe_compute_sensitivity": [{"usd_per_second": rate,
                    "incremental_probe_cost_usd_assumed": seconds * rate,
                    "net_value_vs_P3_usd_assumed": gross - seconds * rate if gross is not None else None} for rate in scenario["probe_second_usd"]],
                "break_even_probe_usd_per_second": gross / seconds if seconds and gross is not None else None,
                "unpriced": ["human review", "queue/critical-path delay beyond observed duration", "development effort", "actual compute billing"]}
        report["policies"][policy] = info
    p4_seconds = report["policies"]["P4"]["additional_probe_seconds"]
    report["budget_mismatch"] = {policy: {"call_count_difference_vs_P4": report["policies"][policy]["additional_probe_calls"] - report["policies"]["P4"]["additional_probe_calls"],
        "seconds_difference_vs_P4": report["policies"][policy]["additional_probe_seconds"] - p4_seconds}
        for policy in ("random_probe", "simple_probe")}
    return report


def replay(out, main_override=None):
    plan = checked_plan(out, main_override)
    main = Path(main_override).resolve() if main_override else Path(plan["main_run"])
    records = read(out / "probes.json")
    cases = {c["id"]: c for c in read(main / "cases.json")}
    packets = read(out / "visible-packets.json")
    rows = read(out / "decisions.json")
    if len(rows) != 2 * plan["population_size"] or len(records) != plan["limits"]["new_probe_calls"]:
        raise ValueError("Incomplete supplement")
    p3 = {r["case_id"]: r for r in read(main / "decisions.json") if r["policy"] == "P3"}
    for record in records:
        if record.get("raw_reference") and sha(out / record["raw_reference"]) != record["raw_sha256"]:
            raise ValueError("Raw probe artifact changed")
        if record.get("raw_reference"):
            derived_visible = view(read(out / record["raw_reference"]), record["probe_id"], cases[record["case_id"]]["scenario"])
            if derived_visible != record.get("visible") or assertion_rows(derived_visible["facts"]) != record.get("assertions"):
                raise ValueError("Probe interpretation differs from raw observations")
    for row in rows:
        cid, policy = row["case_id"], row["policy"]
        selected = plan["random_selection"] if policy == "random_probe" else plan["simple_selection"]
        if digest(p3[cid]) != plan["p3_decision_sha256"][cid]:
            raise ValueError("Original P3 decision digest changed")
        if row["request_ids"] != p3[cid]["request_ids"] or row["selected_for_additional_probe"] != (cid in selected):
            raise ValueError("Prefix or frozen selection changed")
        if cid not in selected:
            if any(row[k] != p3[cid][k] for k in ("action", "judgment", "answers", "assessment", "probe_ids")):
                raise ValueError("Nonselected action changed")
        else:
            record = next(r for r in records if r["case_id"] == cid and r["policy"] == policy)
            if record["probe_id"] != candidate(packets[cid]):
                raise ValueError("Selected candidate changed")
            if record.get("status") == "executed" and record.get("decisive"):
                updated = merge_observation(packets[cid], record)
                interpreted = baseline(updated)
                if row["assessment"] != interpreted or any(row[k] != interpreted[k] for k in ("action", "judgment", "violations", "relationship")) or row["updated_packet_sha256"] != digest(updated):
                    raise ValueError("Decision differs from deterministic raw replay")
            elif row["action"] != "defer" or row["judgment"] != "unresolved":
                raise ValueError("Unexercised probe became a confident decision")
    report = summarize(out, plan, {c["id"]: c for c in read(main / "cases.json")},
                       read(main / "decisions.json"), records, read(out / "decisions.json"), main_override=main)
    if report != read(out / "report.json"):
        raise ValueError("Recomputed supplement report differs")
    return {"verified": True, "plan_sha256": sha(out / "plan.json"), "policies": list(report["policies"])}


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare"); p.add_argument("--main", type=Path, required=True); p.add_argument("--out", type=Path, required=True)
    for name in ("execute", "replay"):
        p = sub.add_parser(name); p.add_argument("--out", type=Path, required=True)
        if name == "replay":
            p.add_argument("--main", type=Path, help="Relocated main evidence; original plan path remains metadata and every byte hash is revalidated")
    args = parser.parse_args()
    value = prepare(args.main, args.out) if args.command == "prepare" else execute(args.out) if args.command == "execute" else replay(args.out, args.main)
    print(json.dumps(value, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
