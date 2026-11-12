"""Fake trials, raw case records and throwaway inventory directories for the fault calibration tests.

The service and loader modules are imported when a test asks for them, so a missing module fails that test
instead of breaking collection.
"""

import importlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
INVENTORIES = ROOT / "scripts/verification/inventories"

MARKER = "PLANTED_FAULT_MARKER"
TARGET = "tests.hexagonal.test_probe::test_target"
OTHER = "tests.hexagonal.test_probe::test_other"
TARGET_SELECTOR = "tests/hexagonal/test_probe.py::test_target"
OTHER_SELECTOR = "tests/hexagonal/test_probe.py::test_other"
CANDIDATE = "c" * 64


def service():
    return importlib.import_module("scripts.verification.fault_calibration")


def loader():
    return importlib.import_module("scripts.verification.inventory_files")


def passed():
    return {"status": "passed", "diagnostic": "", "failure_message": "", "exception_type": "", "attribution": None}


def failed(message, *, own_assertion=True, exception="AssertionError"):
    return {"status": "failed", "diagnostic": message, "failure_message": message,
            "exception_type": message.partition(":")[0],
            "attribution": {"phase": "call", "test_name": TARGET.split("::")[1], "exception_type": exception,
                            "target_assertion": own_assertion}}


def record(*, control=None, mutant=None, control_exit=0, mutant_exit=1, applied=True, candidate=CANDIDATE):
    """A raw trial record as the trial adapter returns it."""
    return {"control_cases": control if control is not None else {TARGET: passed(), OTHER: passed()},
            "mutant_cases": mutant if mutant is not None else {TARGET: failed("AssertionError: " + MARKER),
                                                               OTHER: passed()},
            "control_exit": control_exit, "mutant_exit": mutant_exit, "mutation_applied": applied,
            "candidate_sha256": candidate, "commands": {}, "mutation_sha256": "m" * 64,
            "assertion_observer_sha256": "o" * 64}


OUTCOMES = {
    "detected": record(),
    "escaped_fault": record(mutant={TARGET: passed(), OTHER: passed()}, mutant_exit=0),
    "invalid_control": record(control={TARGET: failed("AssertionError: broken control"), OTHER: passed()}),
    "setup_error": record(applied=False),
    "skipped": record(mutant={TARGET: {**passed(), "status": "skipped"}, OTHER: passed()}, mutant_exit=0),
    "not_exercised": record(control={OTHER: passed()}, mutant={OTHER: passed()}, mutant_exit=0),
    "unrelated_failure": record(mutant={TARGET: failed("TimeoutError: unrelated", own_assertion=False,
                                                       exception="TimeoutError"), OTHER: passed()}),
}


def case(name, *, expect=None, target=TARGET, unaffected=(OTHER,), selectors=(TARGET_SELECTOR, OTHER_SELECTOR)):
    module = service()
    options = {} if expect is None else {"expect": expect}
    return module.FaultCase(name=name, mutation=module.Mutation(path="module.py", old="VALUE = 1", new="VALUE = 2"),
                            selectors=tuple(selectors), target=target, diagnostic=MARKER,
                            unaffected=tuple(unaffected), **options)


class Recorder:
    """A result sink that keeps every event, in order, as JSON would carry it."""

    def __init__(self):
        self.events = []

    def __call__(self, event):
        self.events.append(json.loads(json.dumps(event)))


def scripted(records):
    """A trial that returns, or raises, the scripted outcome for each case name."""
    calls = []

    def trial(fault, case_id):
        calls.append((fault.name, case_id))
        outcome = records[fault.name]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome
    trial.calls = calls
    return trial


# Inventory directories ------------------------------------------------------------------------------

def raw_case(name="first", **changes):
    value = {"name": name, "mutation": {"path": "module.py", "old": "VALUE = 1", "new": "VALUE = 2"},
             "selectors": [TARGET_SELECTOR, OTHER_SELECTOR], "target": TARGET, "diagnostic": MARKER,
             "unaffected": [OTHER]}
    value.update(changes)
    return value


def inventory_directory(root, *, registry=None, inventories=None, extra=None):
    """A registry directory with the real schema: one inventory "probe" unless told otherwise."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(INVENTORIES / "schema.json", root / "schema.json")
    registry = registry if registry is not None else {
        "schema_version": 1, "inventories": {"probe": {"file": "probe.json", "stages": ["ci"]}}}
    inventories = inventories if inventories is not None else {"probe.json": {"schema_version": 1,
                                                                              "cases": [raw_case()]}}
    (root / "registry.json").write_text(json.dumps(registry), encoding="utf-8")
    for name, content in {**inventories, **(extra or {})}.items():
        (root / name).write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
    return root


def module_form(selector):
    path, _, test = selector.partition("::")
    return path.removesuffix(".py").replace("/", ".") + "::" + test
