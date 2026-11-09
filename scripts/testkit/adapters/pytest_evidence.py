"""Qualify pytest command results using the existing behavioral inventory."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from ..catalog import select_families
from .evidence import canonical, digest
from .pytest_plugin import load_catalog, shard_selection


def _count(actual, expected, label):
    if type(actual) is not int or actual != expected:
        raise ValueError("Pytest inventory has inconsistent " + label)


def bind_pytest_report(*, root, directory, families=(), shard=None, exit_status):
    """Retain a checked inventory without changing native process outcomes.

    The test-kit runs the catalog's test roots, optionally selecting families and
    one balanced shard through the existing selection implementation.
    A successful subprocess must supply complete outcomes for that selection and
    at least one passed case. Failed runs may retain partial execution. This checks
    the reporting contract; it cannot establish the quality of a test's oracle.
    """
    root, directory = Path(root).resolve(), Path(directory).resolve()
    path = directory / "inventory.json"
    if path.is_symlink() or not path.is_file():
        raise ValueError("Pytest inventory is missing or is not a regular run artifact")
    raw = path.read_bytes()
    report = json.loads(raw)
    if not isinstance(report, dict) or type(report.get("schema_version")) is not int or report["schema_version"] != 1:
        raise ValueError("Pytest inventory requires schema_version 1")
    catalog, assignments = load_catalog(root)
    selected_modules, expected_shard = shard_selection(root, select_families(assignments, families), shard)
    reported_shard = report.get("shard")
    if expected_shard is None:
        if reported_shard is not None:
            raise ValueError("Pytest inventory reports an unrequested shard")
    elif (not isinstance(reported_shard, dict)
          or canonical({key: reported_shard.get(key) for key in expected_shard}) != canonical(expected_shard)):
        raise ValueError("Pytest inventory contradicts the requested shard")
    if report.get("catalog_id") != catalog["id"] or report.get("catalog_sha256") != digest(canonical(catalog)):
        raise ValueError("Pytest inventory belongs to another catalog")
    if type(exit_status) is not int or type(report.get("exit_status")) is not int or report["exit_status"] != exit_status:
        raise ValueError("Pytest inventory contradicts the native exit status")
    expected_status = "passed" if exit_status == 0 else "failed"
    if report.get("status") != expected_status:
        raise ValueError("Pytest inventory does not describe this execution outcome")
    cases = report.get("cases")
    if not isinstance(cases, list):
        raise ValueError("Pytest inventory requires case records")
    seen, observed_modules, selected, counts = set(), set(), [], Counter()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("Pytest inventory case must be an object")
        name, module = case.get("id"), case.get("path")
        if (not isinstance(name, str) or not isinstance(module, str)
                or module not in assignments or not name.startswith(module + "::") or name in seen):
            raise ValueError("Pytest inventory has a duplicate or unowned case identity")
        if any(case.get(key) != value for key, value in assignments[module].items()):
            raise ValueError("Pytest inventory case contradicts catalog ownership")
        is_selected = case.get("selected")
        if type(is_selected) is not bool or is_selected != (module in selected_modules):
            raise ValueError("Pytest inventory contradicts the requested family selection")
        outcome = case.get("outcome")
        allowed = {"passed", "failed", "skipped", "not_run"} if is_selected else {"deselected"}
        if not isinstance(outcome, str) or outcome not in allowed:
            raise ValueError("Pytest inventory has an invalid case outcome")
        seen.add(name)
        observed_modules.add(module)
        if is_selected:
            selected.append(case)
            counts[outcome] += 1
    _count(report.get("collected"), len(cases), "collected count")
    _count(report.get("selected"), len(selected), "selected count")
    _count(report.get("declared_modules"), len(assignments), "declared module count")
    collected = report.get("collected_modules")
    if (not isinstance(collected, list) or any(not isinstance(name, str) for name in collected)
            or len(collected) != len(set(collected)) or not set(collected) <= assignments.keys()
            or not observed_modules <= set(collected)):
        raise ValueError("Pytest inventory has inconsistent collected modules")
    reported_families = report.get("families")
    if not isinstance(reported_families, dict) or reported_families.keys() != catalog["families"].keys():
        raise ValueError("Pytest inventory has inconsistent family membership")
    for name, family in catalog["families"].items():
        members = [case for case in cases if case["family"] == name]
        actual = reported_families[name]
        if not isinstance(actual, dict) or actual.get("guarantee") != family["guarantee"] or actual.get("modules") != family["modules"]:
            raise ValueError("Pytest inventory has inconsistent family ownership")
        _count(actual.get("collected"), len(members), "family collected count")
        _count(actual.get("selected"), sum(case["selected"] for case in members), "family selected count")
        outcomes = actual.get("outcomes")
        expected = dict(Counter(case["outcome"] for case in members))
        if (not isinstance(outcomes, dict) or any(type(value) is not int for value in outcomes.values())
                or outcomes != expected):
            raise ValueError("Pytest inventory has inconsistent family outcomes")
    if exit_status == 0:
        if not set(selected_modules) <= observed_modules:
            raise ValueError("Pytest inventory omitted selected test modules")
        if counts["failed"] or counts["not_run"]:
            raise ValueError("Successful pytest command has failed or unexecuted selected cases")
        if not counts["passed"]:
            raise ValueError("Successful pytest command executed no passing cases")
    return {"path": str(path), "sha256": digest(raw), "schema_version": 1,
            "catalog_id": catalog["id"], "catalog_sha256": report["catalog_sha256"],
            "status": expected_status, "selected": len(selected), "outcomes": dict(counts),
            "shard": expected_shard}
