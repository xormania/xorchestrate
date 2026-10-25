"""Assertion-specific mutation accounting; a broken environment is not detection."""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path


def exception_type(message):
    """Read pytest's exception summary, never a traceback's source lines."""
    return str(message).split("\n", 1)[0].partition(":")[0]


def assertion_detected(message, expected, attribution=None):
    """The executed AssertionError must carry the exact declared marker.

    JUnit's message attribute is the exception summary. Its body also includes
    unevaluated assertion source, so using that body would credit helper errors.
    """
    return (bool(expected) and str(message).split("\n", 1)[0] == "AssertionError: " + expected
            and isinstance(attribution, dict) and attribution.get("phase") == "call"
            and attribution.get("exception_type") == "AssertionError"
            and attribution.get("target_assertion") is True)


def assertion_attribution(item):
    """Read the observer's one structured outcome for this exact testcase."""
    values = [node.get("value", "") for node in item.findall("./properties/property")
              if node.get("name") == "calibration.attribution"]
    if len(values) != 1:
        return None
    try:
        value = json.loads(values[0])
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) and value.get("test_name") == item.get("name") else None


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
        message = ""
        for tag, status in (("error", "error"), ("skipped", "skipped"), ("failure", "failed")):
            node = item.find(tag)
            if node is not None:
                outcome = status
                message = node.get("message", "")
                diagnostic = (message + "\n" + (node.text or "")).strip()
                break
        cases[name] = {"status": outcome, "diagnostic": diagnostic, "failure_message": message,
                       "exception_type": exception_type(message), "attribution": assertion_attribution(item)}
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
    if control_exit != 0 or any(case["status"] != "passed" for case in control.values()):
        return {**detail, "classification": "invalid_control"}
    if not mutation_applied:
        return {**detail, "classification": "setup_error"}
    if any(case["status"] == "error" or case.get("exception_type") in {"ImportError", "ModuleNotFoundError"}
           for case in mutant.values()):
        return {**detail, "classification": "setup_error"}
    if any(case["status"] == "skipped" for case in mutant.values()):
        return {**detail, "classification": "skipped"}
    if not needed <= control.keys() or not needed <= mutant.keys():
        return {**detail, "classification": "not_exercised"}
    if any(mutant[name]["status"] != "passed" for name in unaffected):
        return {**detail, "classification": "unrelated_failure"}
    failures = {name for name, case in mutant.items() if case["status"] != "passed"}
    if mutant_exit == 0 and not failures:
        return {**detail, "classification": "escaped_fault"}
    target_case = mutant[target]
    if (mutant_exit == 1 and failures == {target} and target_case["status"] == "failed"
            and assertion_detected(target_case.get("failure_message", ""), diagnostic, target_case.get("attribution"))):
        return {**detail, "classification": "detected"}
    return {**detail, "classification": "unrelated_failure"}
