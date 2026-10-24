"""Fresh executions and frozen clustered cases, with two separate reference layers.

No v1 packet is a v2 holdout. Protocol schedules and intended fault families are
known development knowledge, so this pilot tests transfer to fresh executions
and new bounded representations, not discovery of unseen failure mechanisms.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import time

from .probes import (HERE, ROOT, PRODUCT_COMMIT, PRODUCT_TREE, PROBES, assertion_rows,
                     canonical, run_operation, sha, view, write)

VERSION = "2.0.1"
CANDIDATE_INVARIANTS = {
 "p_context": ["current_evidence"], "p_replay": ["current_evidence"],
 "p_assertion": ["assertion_execution"], "p_arguments": ["argument_integrity", "effect_uncertainty"],
 "p_attempts": ["effect_uncertainty"], "p_effect": ["effect_uncertainty"],
 "p_busy": ["concurrent_activity"], "p_authority": ["authority_binding"],
}
CLAIMS = {
 "p_context": "The reused assessment is still applicable to the evaluator-consumed context.",
 "p_assertion": "The calibration checker credited only the intended target assertion failure.",
 "p_arguments": "The proposed complete-delivery claim has matching native/callback arguments and no later unresolved attempt.",
 "p_attempts": "The proposed complete-delivery claim has no later unresolved attempt.",
 "p_replay": "The returned acceptance read accurately represents current owner requirements.",
 "p_effect": "The response and recovery fence preserve an already observed effect across the connection changes.",
 "p_busy": "The aggregate activity correctly represents outstanding held callbacks.",
 "p_authority": "The executed callback retained the authority and work scope under which it was originally observed.",
}
GROUPS = [
    ("p_context", "development", "false_acceptance"),
    ("p_assertion", "development", "low_impact"),
    ("p_arguments", "calibration", "false_acceptance"),
    ("p_attempts", "calibration", "duplicate_effect"),
    ("p_replay", "holdout", "false_acceptance"),
    ("p_effect", "holdout", "duplicate_effect"),
    ("p_busy", "holdout", "wasted_investigation"),
    ("p_authority", "holdout", "false_acceptance"),
]


def stable_id(value, prefix="c"):
    return prefix + hashlib.sha256(value.encode()).hexdigest()[:12]


def key_for(packet):
    """Transparent relational oracle; not an evaluated model's opinion.

    Operands are direct measured observations and declared owner-contract values.
    This key generator is independently checked before final evaluation.
    """
    facts = packet["facts"]
    rows = assertion_rows(facts)
    wrong_subject = any(c["subject"]["expected"] != c["subject"]["observed"] for c in facts["checks"])
    violations = sorted({r["invariant"] for r in rows if r["exercised"] and r["satisfied"] is False})
    decisive = all(r["exercised"] for r in rows)
    judgment = "contradicts" if violations else ("supports" if decisive else "unresolved")
    key = {"applicability": "wrong_subject" if wrong_subject else "applicable",
           "sufficiency": "decisive" if decisive else "missing", "judgment": judgment,
           "violations": violations, "uncertainty": "none" if decisive else "missing_observation",
           "action": {"supports": "accept", "contradicts": "reject", "unresolved": "investigate"}[judgment],
           "relationship": "insufficient"}
    relation = packet.get("relationship")
    if relation:
        if not decisive:
            key["relationship"] = "insufficient"
        elif all(relation[side]["reported_revision"] != relation[side]["current_revision"] for side in ("left", "right")):
            key["relationship"] = "shared_invariant"
        else:
            key["relationship"] = "similar_behavior"
    return key


def hide_observation(packet, wrong_subject=False):
    packet = deepcopy(packet)
    for item in packet["facts"]["checks"]:
        item["left"] = None
        item["observed"] = False
        # right is an applicable requirement, except the context evaluator's fresh result
        # which itself is a withheld observation and must also be hidden.
        if item["note"].startswith("A reused assessment") or item["invariant"] == "assertion_execution":
            item["right"] = None
        if wrong_subject:
            item["subject"]["observed"] = "another-operation"
    packet["observations"] = [{"summary": "The collector reported success.",
        "evidence_status": "No current boundary observation was retained.",
        "run_binding": "another-operation" if wrong_subject else "operation-current",
        "summary_is_measurement": False}]
    packet["facts"]["declared_missing"] = "The actual boundary result must be collected; the success summary supplies no measurement."
    if "relationship" in packet:
        for side in ("left", "right"):
            packet["relationship"][side]["reported_revision"] = None
    return packet


def packet_for(visible, probe_id, consequence):
    packet = deepcopy(visible)
    packet.update({
        "claim": CLAIMS[probe_id],
        "violation_semantics": "An unsatisfied requirement of this supplied local claim. This does not by itself establish that the product accepted the claim; receipt rejections are retained as correct defensive behavior.",
        "requirements": [c["note"] for c in visible["facts"]["checks"]],
        "candidates": {p: {"description": description, "invariants": CANDIDATE_INVARIANTS[p], "cost_units": 1,
                           "units": "one controlled boundary execution; costs differ by elapsed time"}
                       for p, description in PROBES.items()},
        "actions": ["accept", "reject", "investigate", "defer"],
        "consequences": {"class": consequence, "reversibility": "acceptance can be withdrawn; already applied effects cannot be assumed reversible",
                         "exposure": "one controlled operation; production repetition unknown",
                         "urgency": "no measured deadline", "source": "analyst scenario classification, not measured business loss"},
        "candidate_generation": "fixed human-designed candidates; no model generation call",
    })
    return packet


def transformations(cases):
    """Prespecified diagnostic set; these are not new independent incidents."""
    rows = []
    # Two contexts per split: one decisive positive and one decisive negative.
    for split in ("development", "calibration", "holdout"):
        chosen = [c for c in cases if c["split"] == split and c["scenario"] in {"healthy", "fault"}][:2]
        for case in chosen:
            for name in ("reverse_options", "irrelevant_context", "equivalent_wording", "remove_decisive", "wrong_subject"):
                packet = deepcopy(case["packet"])
                if name == "reverse_options":
                    packet["candidates"] = dict(reversed(list(packet["candidates"].items())))
                elif name == "irrelevant_context":
                    packet["observations"].append({"unrelated": "The presentation theme is blue; it does not alter the operation or requirements."})
                elif name == "equivalent_wording":
                    packet["claim"] = "The supplied local claim is supported under all explicitly listed boundary requirements."
                elif name == "wrong_subject":
                    for item in packet["facts"]["checks"]:
                        item["subject"]["observed"] = "another-operation"
                    packet["observations"] = [{**observation, "run_binding": "another-operation"} for observation in packet["observations"]]
                else:
                    packet = hide_observation(packet)
                rows.append({"id": stable_id(case["id"] + name, "t"), "parent_id": case["id"],
                             "group": case["group"], "split": split, "transform": name,
                             "kind": "invariance" if name in {"reverse_options", "irrelevant_context", "equivalent_wording"} else "sensitivity",
                             "packet": packet, "expected": key_for(packet),
                             "expected_behavior": "unchanged" if name in {"reverse_options", "irrelevant_context", "equivalent_wording"} else "unresolved_without_current_observation"})
    return rows


def build_corpus(out):
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    cases = []
    sources = {}
    for probe_id, split, consequence in GROUPS:
        begin = time.monotonic()
        raw = run_operation(probe_id, out / "executions" / probe_id)
        rawpath = out / "raw" / (probe_id + ".json")
        write(rawpath, raw)
        sources[probe_id] = {"path": "raw/" + rawpath.name, "sha256": sha(rawpath),
                             "elapsed_seconds": time.monotonic() - begin, "boundary": raw["boundary"]}
        for variant in ("healthy", "fault", "missing"):
            scenario = "infrastructure" if probe_id == "p_assertion" and variant == "missing" else variant
            outcome = packet_for(view(raw, probe_id, scenario), probe_id, consequence)
            initial = hide_observation(outcome, probe_id == "p_attempts") if variant == "missing" else deepcopy(outcome)
            case = {"id": stable_id(probe_id + "/" + variant), "group": stable_id("durable_receipt" if probe_id in {"p_arguments", "p_attempts"} else probe_id, "g"),
                    "split": split, "packet": initial, "initial_key": key_for(initial),
                    "outcome_key": key_for(outcome), "probe_id": probe_id, "scenario": scenario,
                    "subject": {"commit": PRODUCT_COMMIT, "tree": PRODUCT_TREE},
                    "label_author": "corpus implementation agent; deterministic observation/requirement comparison",
                    "label_disputed": False, "label_review_status": "pending independent premeasurement check",
                    "key_rationale": "Compare retained measured operands to the declared boundary contract; missing/wrong-run observations cannot establish compliance.",
                    "raw_reference": sources[probe_id]["path"], "raw_sha256": sources[probe_id]["sha256"],
                    "transforms": ["explicit field selection", "identity-preserving JSON serialization"] + (["withhold decisive observations"] if variant == "missing" else []),
                    "omissions": "Keys, split/group, scenario selectors, source paths, fault names, unrelated transport configuration and outcomes excluded from initial packet.",
                    "useful_probes": [probe_id] if key_for(initial)["action"] != key_for(outcome)["action"] else [],
                    "independence": "Related variants share scenario family; argument and ordered-attempt cases also share one receipt fixture cluster. No independent production sampling claim.",
                    "outcome_observation": outcome,
                    }
            cases.append(case)
    write(out / "cases.json", {"version": VERSION, "cases": cases})
    write(out / "initial_packets.json", {"version": VERSION, "cases": [{"id": c["id"], "packet": c["packet"]} for c in cases]})
    write(out / "initial_key.json", {"version": VERSION, "cases": {c["id"]: c["initial_key"] for c in cases}})
    write(out / "outcome_key.json", {"version": VERSION, "cases": {c["id"]: c["outcome_key"] for c in cases}})
    transform_rows = transformations(cases)
    write(out / "transformations.json", {"version": VERSION, "cases": transform_rows})
    write(out / "transformation_packets.json", {"version": VERSION, "cases": [
        {k: row[k] for k in ("id", "parent_id", "transform", "kind", "packet")} for row in transform_rows]})
    write(out / "provenance.json", {"version": VERSION, "sources": sources,
          "subject": {"commit": PRODUCT_COMMIT, "tree": PRODUCT_TREE},
          "prior_exposure": "Known historical F02–F09 reproductions informed all schedules. Fresh execution and new packets do not imply new mechanisms.",
          "holdout_material_variants": {"p_replay": "two owner revisions with intermediate semantic replay instead of one revision", "p_effect": "two connection replacements while effect response held instead of one", "p_busy": "three overlapping holds and middle-first completion instead of two and first completion", "p_authority": "two changes of actor/work connection binding before delayed callback instead of one"},
          "development_repeats": "Context, assertion and receipt helper cases reuse known families; none is claimed as fresh incident generalization.",
          "clusters": len({c["group"] for c in cases}), "boundary_scenarios": len(GROUPS), "cases": len(cases), "v1_role": "development/reference only; no v1 packet reused as holdout",
          "vendor_behavior": "not tested; subprocess protocol peers establish adapter behavior only",
          "causal_limit": "Probes execute each boundary; reject/accept decisions and valuation are static policy replay, not production downstream state changes."})
    write(out / "freeze.json", {"version": VERSION, "split_membership_frozen_before_model_evaluation": True,
          "case_count": len(cases), "independent_clusters": len({c["group"] for c in cases}),
          "files": {str(p.relative_to(out)): sha(p) for p in sorted(out.rglob("*.json"))},
          "splits": {c["id"]: {"split": c["split"], "group": c["group"]} for c in cases}})
    return {"path": str(out), "cases": len(cases), "clusters": len({c["group"] for c in cases}), "freeze_sha256": sha(out / "freeze.json")}


def load_corpus(path=None):
    path = Path(path) if path else HERE / "data" / "frozen"
    if path.is_dir():
        path = path / "cases.json"
    payload = json.loads(path.read_text())
    return payload["cases"] if isinstance(payload, dict) else payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_corpus(args.out), indent=2))
