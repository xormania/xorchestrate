"""Isolated assertion-specific fault calibration for B1/B2 acceptance.

Run directly; this is a checker, not an imported-runtime test or a source edit.
Every case retains an exact input snapshot, fault patch, pytest XML and raw log.
"""
from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
TEST = "tests/hexagonal/test_work_acceptance.py"
CONTROL = "test_measurement_replay_is_bound_to_request_and_does_not_reexecute"
CASES = [
    {
        "id": "B1-cancelled-contribution-erases-owner-outcome",
        "test": "test_full_owner_objective_survives_narrowing_and_cancellation",
        "assertion": "OWNER_OUTCOME_B_MUST_REMAIN_REQUIRED",
        "before": '            self._event(tx, "contribution.updated", actor, work_id=args["work_id"], contribution_id=current.key)',
        "after": '''            selected = set(record.data["outcomes"])
            tx.put("requirements", basis.key, {**basis.data,
                "outcomes": {key: value for key, value in basis.data["outcomes"].items() if key in selected},
                "checks": {key: value for key, value in basis.data["checks"].items() if set(value["outcomes"]) <= selected}}, basis.version)
            self._event(tx, "contribution.updated", actor, work_id=args["work_id"], contribution_id=current.key)''',
    },
    {
        "id": "B2-wrong-observed-candidate",
        "test": "test_insufficient_or_misbound_measurement_cannot_pass",
        "nodes": ["candidate"],
        "assertion": "BOUND_REQUIRED_MEASUREMENT_MUST_PASS",
        "before": '"candidate": candidate["candidate"], "bindings": check.get("bindings", {})',
        "after": '"candidate": observed.get("candidate") if observed else None, "bindings": check.get("bindings", {})',
    },
    {
        "id": "B2-caller-origin-accepted-as-measurement",
        "test": "test_observation_must_match_retained_adapter_operation",
        "assertion": "ADAPTER_ORIGIN_REQUIRED",
        "before": 'or operation.get("external_result_origin") != "adapter"',
        "after": 'or False',
    },
    {
        "id": "B2-uncertain-measurement-hidden-by-later-pass",
        "test": "test_incomplete_observed_measurement_cannot_be_hidden_by_later_passing_transport",
        "assertion": "UNRESOLVED_MEASUREMENT_CANNOT_BE_MASKED",
        "before": 'or (_observation(item.get("raw_response")) or {}).get("status") in {"pending", "running", "unknown", "uncertain"}',
        "after": 'or False',
    },
    {
        "id": "B2-stale-assessment-configuration",
        "test": "test_assessment_threshold_change_invalidates_only_decisions_consuming_assessment",
        "assertion": "ASSESSMENT_CONFIGURATION_MUST_REMAIN_CURRENT",
        "before": 'if assessment.data.get("assessment_configuration_identity") != snapshot["assessment_configuration_identity"]:',
        "after": 'if False:',
    },
]


def classify(exit_code, xml_path, target, assertion):
    """A nonzero exit alone never receives detection credit."""
    from scripts.verification.calibration import assertion_attribution, assertion_detected, exception_type

    if not xml_path.exists():
        return {"classification": "setup_error", "reason": "missing_test_results"}
    try:
        cases = list(ET.parse(xml_path).getroot().iter("testcase"))
    except ET.ParseError:
        return {"classification": "setup_error", "reason": "invalid_test_results"}
    identities = [(case.get("classname"), case.get("name")) for case in cases]
    if len(identities) != len(set(identities)):
        return {"classification": "setup_error", "reason": "duplicate_test_identity"}
    errors = [case for case in cases if case.find("error") is not None or
              (case.find("failure") is not None and
               exception_type(case.find("failure").get("message", "")) in {"ImportError", "ModuleNotFoundError"})]
    skips = [case for case in cases if case.find("skipped") is not None]
    failures = [case for case in cases if case.find("failure") is not None]
    controls = [case for case in cases if (case.get("name") == CONTROL or case.get("name", "").startswith(CONTROL + "["))]
    targets = [case for case in cases if (case.get("name") == target or case.get("name", "").startswith(target + "["))]
    if errors:
        state = "setup_error"
    elif skips:
        state = "skipped"
    elif not targets or not controls:
        state = "missing_coverage"
    elif not failures and exit_code == 0:
        state = "escaped_fault"
    elif (exit_code == 1 and len(failures) == len(targets)
          and all(case in targets and assertion_detected(case.find("failure").get("message", ""), assertion, assertion_attribution(case)) for case in failures)
          and all(case.find("failure") is None for case in controls)):
        state = "detected"
    else:
        state = "unrelated_failure"
    return {"classification": state, "cases": len(cases), "targets": len(targets), "controls": len(controls),
        "failures": len(failures), "errors": len(errors), "skips": len(skips)}


def execute(root, output, selectors):
    """Compatibility receipt for the shared observed-pytest execution service."""
    from scripts.verification.__main__ import observed_pytest

    record = observed_pytest(root, output, selectors, timeout=45)
    code = record["exit_status"]
    return {**record, "exit_code": code if code is not None else (124 if record["status"] == "timeout" else 3),
            "timed_out": record["status"] == "timeout", "log": record["stdout"],
            "started": record["started_at"], "finished": record["finished_at"]}


def main(argv=None):
    from scripts.verification.__main__ import calibration_workspace
    from scripts.verification.capture import candidate_identity, verify_candidate, write_json

    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args(argv)
    root, output = arguments.candidate.resolve(), arguments.output.resolve()
    if output == root or root in output.parents:
        parser.error("--output must be outside the candidate tree")
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    candidate = candidate_identity(root)
    results = []
    for case in CASES:
        case_dir = output / case["id"]
        mutation = {"path": "xorchestrate/acceptance.py", "old": case["before"], "new": case["after"]}
        try:
            identity, baseline, mutant = calibration_workspace(root, case_dir, mutation)
            selectors = [TEST + "::" + case["test"], TEST + "::" + CONTROL]
            if case.get("nodes"):
                selectors = [TEST + "::" + case["test"] + "[" + store + "-" + node + "]"
                    for store in ("memory", "sqlite") for node in case["nodes"]] + [TEST + "::" + CONTROL]
            passing = execute(baseline, case_dir / "control-run", selectors)
            baseline_class = classify(passing["exit_code"], Path(passing["xml"]), case["test"], case["assertion"])
            if baseline_class["classification"] != "escaped_fault":
                outcome = {**baseline_class, "reason": "baseline_not_clean_pass", "control": passing}
            else:
                mutated = execute(mutant, case_dir / "mutant-run", selectors)
                outcome = {**classify(mutated["exit_code"], Path(mutated["xml"]), case["test"], case["assertion"]),
                           "control": passing, "fault": mutated}
            results.append({"id": case["id"], "assertion": case["assertion"],
                            "candidate_sha256": identity["content_sha256"], **outcome})
        except Exception as error:
            results.append({"id": case["id"], "classification": "setup_error", "error": str(error)})
    integrity = verify_candidate(root, candidate)
    same_candidate = all(row.get("candidate_sha256") == candidate["content_sha256"] for row in results)
    passed = (integrity["passed"] and same_candidate and len(results) == len(CASES)
              and all(row["classification"] == "detected" for row in results))
    write_json(output / "calibration.json", {"candidate": candidate, "candidate_integrity": integrity,
        "same_candidate": same_candidate, "passed": passed, "results": results})
    print(json.dumps({"passed": passed, "results": [
        {"id": row["id"], "classification": row["classification"]} for row in results]}, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
