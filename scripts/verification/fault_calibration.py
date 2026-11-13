"""One calibration service for any fault inventory, with one verdict policy.

The service owns no infrastructure. A trial plants one case's fault in an
isolated copy and returns the raw control and mutant case records; the
service judges them with ``calibration.classify`` and reports through the
test kit's result sink. Where inventories come from, and when they run, is
decided at the edge: this module never imports a loader, a command line or a
composition root.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from scripts.testkit.ports import ResultSink

from .calibration import classify

EXPECTATIONS = ("detected", "unrelated_failure")
"""What a case must classify as to pass: a fault the target catches, or a negative control."""


@dataclass(frozen=True)
class Mutation:
    """Replace exactly one occurrence of ``old`` with ``new`` in ``path``, relative to the candidate."""
    path: str
    old: str
    new: str


@dataclass(frozen=True)
class FaultCase:
    name: str
    mutation: Mutation
    selectors: tuple[str, ...]
    target: str
    diagnostic: str
    unaffected: tuple[str, ...] = ()
    expect: str = "detected"


@dataclass(frozen=True)
class Inventory:
    name: str
    cases: tuple[FaultCase, ...]


Trial = Callable[[FaultCase, str], Mapping]
"""Run one case's control and mutant in isolation and return the raw record.

The record holds ``control_cases`` and ``mutant_cases`` (raw case maps from
``calibration.junit_cases``), ``control_exit``, ``mutant_exit``,
``mutation_applied`` and ``candidate_sha256``. The trial keeps its own
evidence; it may raise, and a typed ``code`` on the error is the only detail
the report keeps.
"""


def _judge(case: FaultCase, raw: Mapping) -> str:
    return classify(raw["control_cases"], raw["mutant_cases"], case.target, case.diagnostic, case.unaffected,
                    mutation_applied=raw["mutation_applied"], control_exit=raw["control_exit"],
                    mutant_exit=raw["mutant_exit"])["classification"]


TYPED_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")


def _reason(error: Exception) -> str:
    """A typed code only; free text, a message or a class name never reaches the report."""
    code = getattr(error, "code", None)
    return code if isinstance(code, str) and TYPED_CODE.fullmatch(code) else "trial_failed"


def calibrate_inventory(inventory: Inventory, *, trial: Trial, sink: ResultSink, candidate_sha256: str) -> dict:
    """Run every case once, in order, and pass only when each meets its expectation on one candidate."""
    results = []
    for index, case in enumerate(inventory.cases, 1):
        try:
            raw = trial(case, f"{index:02d}-{case.name}")
        except Exception as error:  # A failed trial is a setup error, never detection.
            row = {"name": case.name, "expect": case.expect, "classification": "setup_error", "passed": False,
                   "candidate_sha256": candidate_sha256, "reason": _reason(error)}
        else:
            classification = _judge(case, raw)
            row = {"name": case.name, "expect": case.expect, "classification": classification,
                   "passed": classification == case.expect, "candidate_sha256": raw["candidate_sha256"]}
        results.append(row)
        sink({"event": "case", **row})
    same_candidate = all(row["candidate_sha256"] == candidate_sha256 for row in results)
    report = {"inventory": inventory.name, "candidate_sha256": candidate_sha256, "results": results,
              "same_candidate": same_candidate,
              "passed": bool(results) and same_candidate and all(row["passed"] for row in results)}
    sink({"event": "report", **report})
    return report
