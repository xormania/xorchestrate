"""Calibrate five shared-state guarantees against isolated source faults.

Uses the existing snapshot runner and trusted assertion observer. Each fault
runs one narrow witness and an unaffected positive control; a separate control
proves that an unrelated exception with the expected marker earns no credit.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .__main__ import calibrate
from .capture import candidate_identity, now, verify_candidate, write_json

TEST_PATH = "tests/hexagonal/test_state_definition.py"
TEST_MODULE = "tests.hexagonal.test_state_definition"
POSITIVE = "test_state_calibration_positive_control"


def case(name, witness, diagnostic, path, old, new):
    return {
        "name": name,
        "mutation": {"path": path, "old": old, "new": new},
        "selectors": [f"{TEST_PATH}::{witness}", f"{TEST_PATH}::{POSITIVE}"],
        "target": f"{TEST_MODULE}::{witness}",
        "diagnostic": diagnostic,
        "unaffected": [f"{TEST_MODULE}::{POSITIVE}"],
    }


FAULTS = (
    case("guard-bypass", "test_state_calibration_guard_refusal", "STATE_CALIBRATION_GUARD_REFUSAL",
         "xorchestrate/state.py",
         "permitted = True if rule.guard is None else rule.guard(deepcopy(supplied))",
         "permitted = True  # planted guard bypass"),
    case("expected-version-bypass", "test_state_calibration_stale_version", "STATE_CALIBRATION_STALE_VERSION",
         "xorchestrate/state.py", "        current = tx.get(kind, key)",
         "        current = tx.get(kind, key)\n"
         "        if current is not None and expected_version is not None:\n"
         "            expected_version = current.version  # planted loss of caller CAS"),
    case("history-omission", "test_state_calibration_required_history", "STATE_CALIBRATION_REQUIRED_HISTORY",
         "xorchestrate/state.py", "            tx.emit(history)",
         "            pass  # planted loss of required transition history"),
    case("target-validation-bypass", "test_state_calibration_declared_target", "STATE_CALIBRATION_DECLARED_TARGET",
         "xorchestrate/state.py", "        if proposed.get(definition.state_field) != decision.target:",
         "        if False:  # planted target validation bypass"),
    case("application-engine-bypass", "test_state_calibration_application_consumes_engine",
         "STATE_CALIBRATION_APPLICATION_CONSUMES_ENGINE", "xorchestrate/application.py",
         "if kind in DURABLE_LIFECYCLES else tx.put(kind, key, payload)",
         "if False else tx.put(kind, key, payload)"),
)

UNRELATED_ERROR = case(
    "unrelated-error-control", "test_state_calibration_guard_refusal", "STATE_CALIBRATION_GUARD_REFUSAL",
    TEST_PATH, "def test_state_calibration_guard_refusal(tmp_path):",
    "def test_state_calibration_guard_refusal(tmp_path):\n"
    '    raise RuntimeError("STATE_CALIBRATION_GUARD_REFUSAL")',
)


def _run(root, output, definition):
    try:
        result = calibrate(root, output / definition["name"], definition["mutation"], definition["selectors"],
                           definition["target"], definition["diagnostic"], definition["unaffected"])
    except Exception as error:
        result = {"classification": "setup_error", "error": str(error)}
    return {"name": definition["name"], **result}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, default=Path.cwd())
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    root, output = args.candidate.resolve(), args.out.resolve()
    if output == root or root in output.parents:
        parser.error("--out must be outside the candidate tree")
    output.mkdir(parents=True, exist_ok=False)
    candidate = candidate_identity(root)
    results = [_run(root, output, definition) for definition in FAULTS]
    unrelated = _run(root, output, UNRELATED_ERROR)
    runs = [*results, unrelated]
    integrity = verify_candidate(root, candidate)
    same_candidate = all(row.get("candidate_sha256") == candidate["content_sha256"] for row in runs)
    detected = sum(row["classification"] == "detected" for row in results)
    misses = sum(row["classification"] == "escaped_fault" for row in results)
    errors = sum(row["classification"] not in {"detected", "escaped_fault"} for row in results)
    unrelated_rejected = unrelated["classification"] == "unrelated_failure"
    errors += int(not unrelated_rejected) + int(not integrity["passed"] or not same_candidate)
    positive_controls = sum(row.get("commands", {}).get("control", {}).get("exit_status") == 0 for row in runs)
    summary = {
        "candidate_sha256": candidate["content_sha256"],
        "cases": len(FAULTS), "faults": len(FAULTS), "detected": detected,
        "positive_controls": {"passed": positive_controls, "total": len(runs)},
        "unrelated_error_control": unrelated["classification"],
        "errors": errors, "misses": misses,
        "passed": detected == len(FAULTS) and errors == 0 and misses == 0,
        "results": [{"name": row["name"], "classification": row["classification"]} for row in runs],
    }
    report = {**summary, "candidate": candidate, "results": results, "unrelated_error_control": unrelated,
              "candidate_integrity": integrity, "same_candidate": same_candidate, "finished_at": now()}
    write_json(output / "suite.json", report)
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
