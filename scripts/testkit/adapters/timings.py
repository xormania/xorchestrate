"""Read recorded JUnit durations and publish the per-module sharding weights."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ElementTree  # noqa: S405 - parses JUnit written by local or CI pytest runs
from pathlib import Path

from ..sharding import module_durations, timings_document

TIMINGS = "contracts/test-timings.json"


def declared_modules(root):
    """Every module path the catalog declares, independent of discovery."""
    catalog = json.loads((Path(root) / "contracts/testing.json").read_text())
    return sorted(path for family in catalog["families"].values() for path in family["modules"])


def junit_cases(path):
    """Yield ``(classname, seconds)`` for each recorded case; pytest includes setup and teardown."""
    tree = ElementTree.parse(path)  # noqa: S314 - trusted pytest output selected by the operator
    for case in tree.iter("testcase"):
        yield case.get("classname", ""), float(case.get("time", "0") or 0)


def regenerate(root, reports, output=None):
    """Aggregate one or more JUnit reports (for example every CI shard) into the timings file."""
    if not reports:
        raise ValueError("Select at least one JUnit report")
    modules = declared_modules(root)
    cases = [case for report in reports for case in junit_cases(report)]
    durations = module_durations(cases, modules)
    document = timings_document(durations)
    target = Path(output) if output is not None else Path(root) / TIMINGS
    target.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    return {"status": "passed", "output": str(target), "modules": len(document["modules"]),
            "untimed_modules": sorted(set(modules) - set(durations)),
            "default_seconds": document["default_seconds"],
            "total_seconds": round(sum(document["modules"].values()), 2)}
