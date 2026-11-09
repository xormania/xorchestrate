"""Read recorded JUnit durations and publish the per-module sharding weights."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ElementTree  # noqa: S405 - parses JUnit written by local or CI pytest runs
from pathlib import Path

from ..sharding import TIMINGS, module_durations, timings_document
from .pytest_plugin import load_catalog


def junit_cases(path):
    """Yield ``(classname, seconds)`` for each recorded case; pytest includes setup and teardown."""
    tree = ElementTree.parse(path)  # noqa: S314 - trusted pytest output selected by the operator
    for case in tree.iter("testcase"):
        yield case.get("classname", ""), float(case.get("time", "0") or 0)


def regenerate(root, reports, output=None, *, allow_partial=False):
    """Aggregate JUnit reports (one full run or every CI shard) into the timings file.

    Reports that leave cataloged modules untimed are refused unless explicitly
    allowed, so a partial run cannot silently replace the committed weights.
    """
    if not reports:
        raise ValueError("Select at least one JUnit report")
    _, assignments = load_catalog(root)
    durations = module_durations([case for report in reports for case in junit_cases(report)], assignments)
    untimed = sorted(set(assignments) - set(durations))
    if untimed and not allow_partial:
        raise ValueError(f"JUnit reports leave {len(untimed)} cataloged modules untimed "
                         f"(pass every shard's report or --allow-partial): {untimed}")
    document = timings_document(durations)
    target = Path(output) if output is not None else Path(root) / TIMINGS
    target.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    return {"status": "passed", "output": str(target), "modules": len(document["modules"]),
            "untimed_modules": untimed, "default_seconds": document["default_seconds"],
            "total_seconds": round(sum(document["modules"].values()), 2)}
