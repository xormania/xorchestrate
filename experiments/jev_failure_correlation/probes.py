"""Bounded, credential-free probes; candidate behavior is not experiment success.

p4 replays real pytest results into both unmodified candidate classifiers.
p7 exercises the active application via retained public test helpers.
Each selector gets the same two unique-probe budget. Other choices stay unrun.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
BUDGET = {"unique_probes_per_selector": 2, "supported": ["p4", "p7"],
          "p4_pytest_processes": 9, "subprocess_timeout_seconds": 30,
          "p7_application_scenarios": 4, "retries": 0,
          "selection_rule": "Run each supported selected option once; keep all selecting case IDs. Do not replace unsupported selections."}


def write(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def execution(root, out, variant):
    root.mkdir(parents=True)
    marker = "TARGET_REQUIREMENT"
    control = "test_measurement_replay_is_bound_to_request_and_does_not_reexecute"
    (root / "module.py").write_text(variant["module"])
    (root / "dependency.py").write_text(variant.get("dependency", ""))
    tests = f"def {control}():\n    assert True\n"
    if not variant.get("omit_target"):
        tests = ('from module import compute\ndef test_target():\n'
                 f'    assert compute() == 1, "{marker}"\n' + tests)
    (root / "test_probe.py").write_text(tests)
    out.mkdir(parents=True)
    xml = out / "results.xml"
    command = [sys.executable, "-m", "pytest", "-q", "--tb=short", "-p", "no:cacheprovider", "test_probe.py", "--junitxml=" + str(xml)]
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    started = datetime.now(timezone.utc).isoformat()
    begin = time.monotonic()
    try:
        result = subprocess.run(command, cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        code, stdout, stderr, status = result.returncode, result.stdout, result.stderr, "completed"
    except subprocess.TimeoutExpired as exc:
        code, stdout, stderr, status = 124, exc.stdout or b"", exc.stderr or b"", "timeout"
    (out / "stdout.log").write_bytes(stdout)
    (out / "stderr.log").write_bytes(stderr)
    record = {"command": command, "cwd": str(root), "started": started, "elapsed_seconds": time.monotonic()-begin,
              "exit_code": code, "status": status, "source_sha256": {p.name: sha(p) for p in root.glob("*.py")},
              "xml_sha256": sha(xml) if xml.exists() else None}
    write(out / "command.json", record)
    return record


def intended_credit(target, cases, control_exit, exit_code):
    """Attribute this fixed returned-value fault to its executed outer assertion.

    The XML failure starts with the actual exception message. A marker elsewhere
    in its traceback cannot meet these checks; the dependency-frame exclusion
    also rejects a helper raising the same exception before the outer comparison.
    """
    if control_exit != 0 or exit_code != 1 or not target or target["status"] != "failed":
        return False
    diagnostic = target.get("diagnostic", "")
    unaffected = cases.get("test_probe::test_measurement_replay_is_bound_to_request_and_does_not_reexecute")
    return bool(unaffected and unaffected["status"] == "passed"
                and diagnostic.startswith("AssertionError: TARGET_REQUIREMENT\nassert 2 == 1\n")
                and "test_probe.py:3: in test_target" in diagnostic
                and not re.search(r"(?:module|dependency)\.py:\d+: in ", diagnostic))


def calibration(out):
    from scripts.verification.calibration import classify, junit_cases
    spec = importlib.util.spec_from_file_location("probe_work_calibrator", ROOT / "tests/hexagonal/calibrate_work_acceptance.py")
    work = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(work)
    fixture = json.loads((HERE / "probe_fixtures/calibration.json").read_text())
    control = execution(out / "control/input", out / "control/output", {"module": "def compute():\n    return 1\n"})
    control_cases = junit_cases(out / "control/output/results.xml")
    records = []
    for variant in fixture["variants"]:
        path = out / variant["id"]
        result = execution(path / "input", path / "output", variant)
        xml = path / "output/results.xml"
        cases = junit_cases(xml) if xml.exists() else {}
        native = classify(control_cases, cases, "test_probe::test_target", fixture["marker"],
                          ["test_probe::" + work.CONTROL], control_exit=control["exit_code"], mutant_exit=result["exit_code"])
        other = work.classify(result["exit_code"], xml, "test_target", fixture["marker"])
        target = cases.get("test_probe::test_target")
        observed = {"variant": variant["id"], "expected_execution": variant["expected"], "independent": variant["independent"],
                    "control_exit": control["exit_code"], "exit_code": result["exit_code"],
                    "cases": cases, "native_classification": native["classification"],
                    "work_classification": other["classification"], "target_observation": target,
                    "fixture_sha256": sha(HERE / "probe_fixtures/calibration.json")}
        # Credit requires the deliberately wrong returned value, not traceback marker presence.
        observed["intended_detection_credit"] = variant["expected"] == "intended_assertion" and intended_credit(target, cases, control["exit_code"], result["exit_code"])
        observed["known_defect_reproduced"] = (variant["expected"] in {"unrelated_failure", "unrelated_assertion"}
                                                and native["classification"] == "detected" and other["classification"] == "detected")
        records.append(observed)
    write(out / "observations.json", {"probe": "p4", "control": control, "records": records,
           "conclusion": "Distinguishes target assertion from helper exceptions using independently fixed fixture behavior.",
           "new_product_defect": False})
    return records


def dependencies(out):
    from tests.hexagonal.test_work_acceptance import make_app, setup_work, measure, accept
    from xorchestrate.adapters.acceptance import RequiredChecksAcceptance
    from xorchestrate.adapters.storage import MemoryStore, SQLiteStore
    from xorchestrate.model import Assessment
    class Evaluator:
        def __init__(self): self.contexts = []
        def assess(self, question, context):
            self.contexts.append(deepcopy(context))
            return Assessment("reject" if context["context"].get("check_result") == "failed" else "pass", .99, "Configured synthetic context predicate")
        def close(self): pass
    records = []
    for backend in ("memory", "sqlite"):
        for material in (False, True):
            store = MemoryStore() if backend == "memory" else SQLiteStore(str(out / f"{backend}-{material}.sqlite3"))
            evaluator = Evaluator()
            app = make_app(store=store, evaluator=evaluator, policy=RequiredChecksAcceptance({"require_assessment": True}))
            try:
                work, _ = setup_work(app, {"A": "Current measurements plus configured review"})
                measure(app, work)
                old = app.call("assess", {"work_id": work["id"], "question": "Evaluate current context"})["assessment"]
                initial = accept(app, work, old)
                changes = {"context": {"check_result": "failed"}} if material else {"title": "A revised display label"}
                app.call("update_work", {"work_id": work["id"], "expected_version": work["version"], "changes": changes})
                persisted = app.call("get_acceptance", {"work_id": work["id"]})
                replay = accept(app, work, old)
                fresh = app.call("assess", {"work_id": work["id"], "question": "Evaluate current context"})["assessment"]
                current = accept(app, work, fresh)
                records.append({"backend": backend, "change": changes, "initial_accepted": initial["accepted"],
                    "persisted_accepted_after_change": persisted["accepted"], "old_assessment_reaccepted": replay["accepted"],
                    "fresh_assessment_accepted": current["accepted"], "evaluator_contexts": evaluator.contexts,
                    "old_assessment": old, "fresh_assessment": fresh, "replay_decision": replay, "fresh_decision": current,
                    "known_defect_reproduced": material and replay["accepted"] and not current["accepted"],
                    "valid_control_passed": not material and initial["accepted"] and replay["accepted"] and current["accepted"]})
            finally: store.close()
    write(out / "observations.json", {"probe": "p7", "records": records, "new_product_defect": False})
    return records


def run(predictions, selector, out):
    out.mkdir(parents=True, exist_ok=False)
    write(out / "budget.json", BUDGET)
    sources = ["scripts/verification/calibration.py", "tests/hexagonal/calibrate_work_acceptance.py",
               "tests/hexagonal/test_work_acceptance.py", "xorchestrate/acceptance.py",
               "experiments/jev_failure_correlation/probes.py",
               "experiments/jev_failure_correlation/probe_fixtures/calibration.json"]
    write(out / "source-identity.json", {"git_head": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "files": {p: sha(ROOT / p) for p in sources}, "python": sys.version})
    write(out / "selections.json", {"selector": selector, "predictions": predictions})
    selected = {}
    for case_id, entry in predictions.get("cases", {}).items():
        selected.setdefault(entry.get("probe", "insufficient"), []).append(case_id)
    results = []
    for probe, case_ids in sorted(selected.items()):
        record = {"selector": selector, "probe": probe, "case_ids": case_ids, "status": "unexecuted", "intended_detection_credit": False}
        if probe in BUDGET["supported"]:
            target = out / probe
            target.mkdir()
            try:
                observations = (calibration if probe == "p4" else dependencies)(target)
                record.update(status="executed", observations=len(observations), new_product_defect=False,
                              known_defect_reproductions=sum(bool(r.get("known_defect_reproduced")) for r in observations),
                              distinguished_explanations=any(bool(r.get("known_defect_reproduced")) for r in observations))
            except Exception as exc:
                record.update(status="probe_error", error_type=type(exc).__name__, error=str(exc))
        else:
            record["reason"] = "no_probe_selected" if probe in {"none", "insufficient"} else "outside_prespecified_executable_subset"
        results.append(record)
    report = {"selector": selector, "budget": BUDGET, "results": results,
              "note": "One execution per distinct selected option, not one per selecting case. No unexecuted option receives utility credit."}
    write(out / "summary.json", report)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--predictions", type=Path)
    parser.add_argument("--selector", choices=["baseline", "jev", "independent-generation"], required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    predictions = json.loads(args.predictions.read_text()) if args.predictions else {"cases": {"generation": {"probe": "p4"}}}
    if not args.predictions and args.selector != "independent-generation": parser.error("--predictions required for a measured selector")
    report = run(predictions, args.selector, args.out.resolve())
    print(json.dumps(report, indent=2))
    return 1 if any(r["status"] == "probe_error" for r in report["results"]) else 0


if __name__ == "__main__": raise SystemExit(main())
