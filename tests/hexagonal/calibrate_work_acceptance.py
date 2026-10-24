"""Isolated assertion-specific fault calibration for B1/B2 acceptance.

Run directly; this is a checker, not an imported-runtime test or a source edit.
Every case retains an exact input snapshot, fault patch, pytest XML and raw log.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
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
    xml_path, log_path = output / "results.xml", output / "output.txt"
    command = [sys.executable, "-m", "pytest", "-p", "_xorchestrate_calibration_observer", *selectors, "-q", "--tb=short", "--junitxml=" + str(xml_path)]
    started = datetime.now(timezone.utc).isoformat()
    environment = dict(os.environ)
    checker = output / "checker"
    checker.mkdir(parents=True, exist_ok=True)
    observer = checker / "_xorchestrate_calibration_observer.py"
    observer.write_bytes((ROOT / "scripts/verification/pytest_calibration.py").read_bytes())
    environment["PYTHONPATH"] = str(checker) + os.pathsep + str(root)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    output.mkdir(parents=True, exist_ok=True)
    timed_out = False
    try:
        result = subprocess.run(command, cwd=root, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=45)
        code, content = result.returncode, result.stdout
    except subprocess.TimeoutExpired as error:
        code, content, timed_out = 124, error.stdout or b"", True
    log_path.write_bytes(content)
    record = {"command": command, "cwd": str(root), "started": started,
        "finished": datetime.now(timezone.utc).isoformat(), "exit_code": code, "timed_out": timed_out,
        "environment": {key: environment[key] for key in ("PYTHONPATH", "PYTHONDONTWRITEBYTECODE")},
        "xml": str(xml_path), "log": str(log_path),
        "assertion_observer_sha256": hashlib.sha256(observer.read_bytes()).hexdigest()}
    (output / "command.json").write_text(json.dumps(record, indent=2) + "\n")
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    output = arguments.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    baseline = output / "snapshot"
    baseline.mkdir()
    shutil.copytree(ROOT / "xorchestrate", baseline / "xorchestrate", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (baseline / "tests/hexagonal").mkdir(parents=True)
    shutil.copyfile(ROOT / TEST, baseline / TEST)
    shutil.copyfile(Path(__file__), baseline / "tests/hexagonal/calibrate_work_acceptance.py")
    shutil.copyfile(ROOT / "pyproject.toml", baseline / "pyproject.toml")
    (baseline / "scripts/verification").mkdir(parents=True)
    shutil.copyfile(ROOT / "scripts/verification/calibration.py", baseline / "scripts/verification/calibration.py")
    shutil.copyfile(ROOT / "scripts/verification/pytest_calibration.py", baseline / "scripts/verification/pytest_calibration.py")
    inputs = {str(path.relative_to(baseline)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(baseline.rglob("*")) if path.is_file()}
    (output / "inputs.json").write_text(json.dumps(inputs, indent=2) + "\n")
    with tarfile.open(output / "measured-snapshot.tar.gz", "w:gz") as archive:
        archive.add(baseline, arcname="candidate")
    source = (baseline / "xorchestrate/acceptance.py").read_text()
    results = []
    for case in CASES:
        case_dir = output / case["id"]
        case_dir.mkdir()
        mutant = case_dir / "candidate"
        shutil.copytree(baseline, mutant)
        target = mutant / "xorchestrate/acceptance.py"
        if source.count(case["before"]) != 1:
            results.append({"id": case["id"], "classification": "setup_error", "reason": "mutation_anchor_not_unique"})
            continue
        changed = source.replace(case["before"], case["after"], 1)
        target.write_text(changed)
        (case_dir / "mutation.patch").write_text("".join(difflib.unified_diff(source.splitlines(keepends=True), changed.splitlines(keepends=True),
            fromfile="a/xorchestrate/acceptance.py", tofile="b/xorchestrate/acceptance.py")))
        selectors = [TEST + "::" + case["test"], TEST + "::" + CONTROL]
        if case.get("nodes"):
            # All parameters are run. A fault must fail only its candidate-
            # mismatch instances, while all other binding assertions remain green.
            selectors = [TEST + "::" + case["test"] + "[" + store + "-" + node + "]"
                for store in ("memory", "sqlite") for node in case["nodes"]] + [TEST + "::" + CONTROL]
        passing = execute(baseline, case_dir / "control", selectors)
        baseline_class = classify(passing["exit_code"], Path(passing["xml"]), case["test"], case["assertion"])
        if baseline_class["classification"] != "escaped_fault":
            results.append({"id": case["id"], "classification": baseline_class["classification"],
                "reason": "baseline_not_clean_pass", "control": passing})
            continue
        mutated = execute(mutant, case_dir / "fault", selectors)
        outcome = classify(mutated["exit_code"], Path(mutated["xml"]), case["test"], case["assertion"])
        results.append({"id": case["id"], "assertion": case["assertion"], "control": passing, "fault": mutated, **outcome})
    (output / "calibration.json").write_text(json.dumps({"snapshot": str(output / "measured-snapshot.tar.gz"),
        "checker": inputs["tests/hexagonal/calibrate_work_acceptance.py"], "results": results}, indent=2) + "\n")
    print(json.dumps({"results": [{"id": row["id"], "classification": row["classification"]} for row in results]}, indent=2))
    return 0 if len(results) == len(CASES) and all(row["classification"] == "detected" for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
