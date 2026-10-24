"""Assertion-specific mutation accounting; a broken environment is not detection."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path


def junit_cases(path):
    """Read raw pytest/unittest cases, not a candidate-authored total."""
    root = ET.parse(Path(path)).getroot()
    cases = {}
    for item in root.iter("testcase"):
        name = item.get("classname", "") + "::" + item.get("name", "")
        if name in cases:
            raise ValueError("Duplicate assertion identity: " + name)
        outcome = "passed"
        diagnostic = ""
        for tag, status in (("error", "error"), ("skipped", "skipped"), ("failure", "failed")):
            node = item.find(tag)
            if node is not None:
                outcome = status
                diagnostic = (node.get("message", "") + "\n" + (node.text or "")).strip()
                break
        cases[name] = {"status": outcome, "diagnostic": diagnostic}
    return cases


def classify(control, mutant, target, diagnostic, unaffected=(), *, mutation_applied=True,
             control_exit=0, mutant_exit=1):
    """Classify one isolated fault using exact assertion identity and diagnostic.

    Both maps come from raw case records. The caller retains the mutation patch,
    measured candidate/checker and full command records beside this decision.
    """
    detail = {"target": target, "expected_diagnostic": diagnostic,
              "unaffected": list(unaffected), "mutation_applied": mutation_applied}
    needed = {target, *unaffected}
    if not needed <= control.keys() or not needed <= mutant.keys():
        return {**detail, "classification": "not_exercised"}
    if control_exit != 0 or any(case["status"] != "passed" for case in control.values()):
        return {**detail, "classification": "invalid_control"}
    if not mutation_applied:
        return {**detail, "classification": "setup_error"}
    if any(case["status"] == "error" for case in mutant.values()):
        return {**detail, "classification": "setup_error"}
    if any(case["status"] == "skipped" for case in mutant.values()):
        return {**detail, "classification": "skipped"}
    if any(mutant[name]["status"] != "passed" for name in unaffected):
        return {**detail, "classification": "unrelated_failure"}
    failures = {name for name, case in mutant.items() if case["status"] != "passed"}
    if mutant_exit == 0 and not failures:
        return {**detail, "classification": "escaped_fault"}
    target_case = mutant[target]
    if (mutant_exit == 1 and failures == {target} and target_case["status"] == "failed"
            and diagnostic and diagnostic in target_case.get("diagnostic", "")):
        return {**detail, "classification": "detected"}
    return {**detail, "classification": "unrelated_failure"}
