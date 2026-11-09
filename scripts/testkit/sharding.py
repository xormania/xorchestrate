"""Pure, deterministic partitioning of selected test modules across shards.

Shards partition the modules a catalog selection already chose; they never add
or drop a module. Recorded per-module durations only balance the partition. A
module without a recorded duration receives the declared default weight and is
assigned like every other module.
"""

from __future__ import annotations

import math
import re

SHARD_PATTERN = re.compile(r"([1-9][0-9]*)/([1-9][0-9]*)")
TIMINGS = "contracts/test-timings.json"


def parse_shard(value):
    """Return a one-based ``(index, count)`` pair from ``"K/N"``; ``None`` stays unsharded."""
    if value is None:
        return None
    match = SHARD_PATTERN.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise ValueError("Shard must be K/N with positive integers, for example 2/4")
    index, count = int(match.group(1)), int(match.group(2))
    if index > count:
        raise ValueError("Shard index must satisfy 1 <= K <= N")
    return index, count


def suite_shard(suite, value):
    """The one rule for where a shard applies: only the standalone tests suite."""
    shard = parse_shard(value)
    if shard is not None and suite != "tests":
        raise ValueError("--shard applies only to --suite tests")
    return shard


def _seconds(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(name + " must be a finite nonnegative number of seconds")
    return float(value)


def validate_timings(document):
    """Return ``(default_seconds, {module: seconds})`` from a timings document."""
    if not isinstance(document, dict) or set(document) != {"schema_version", "default_seconds", "modules"}:
        raise ValueError("Test timings require schema_version, default_seconds and modules")
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise ValueError("Test timings require schema_version 1")
    default = _seconds(document["default_seconds"], "default_seconds")
    if default <= 0:
        raise ValueError("default_seconds must be positive")
    modules = document["modules"]
    if not isinstance(modules, dict) or any(not isinstance(path, str) or not path for path in modules):
        raise ValueError("Test timings modules must map module paths to seconds")
    return default, {path: _seconds(seconds, "Timing for " + path) for path, seconds in modules.items()}


def timings_document(durations):
    """Build a timings document; the default weight is the median recorded duration."""
    modules = {path: round(_seconds(seconds, "Timing for " + path), 2)
               for path, seconds in sorted(durations.items())}
    values = sorted(modules.values())
    if not values:
        raise ValueError("Test timings need at least one recorded module")
    middle = len(values) // 2
    median = values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2
    return {"schema_version": 1, "default_seconds": max(round(median, 2), 0.01),
            "modules": modules}


def partition(modules, count, *, weights=None, default=1.0):
    """Assign every module to exactly one of ``count`` shards.

    Longest-processing-time first: heaviest modules are placed first on the
    currently lightest shard; ties break by path and then by shard index, so the
    result depends only on the module set, the weights and ``count``.
    """
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValueError("Shard count must be a positive integer")
    weights = weights or {}
    modules = tuple(modules)
    paths = sorted(set(modules))
    if len(paths) != len(modules):
        raise ValueError("Sharded modules must be distinct")
    weight = {path: weights.get(path, default) for path in paths}
    shards = [{"modules": [], "estimated_seconds": 0.0} for _ in range(count)]
    for path in sorted(paths, key=lambda item: (-weight[item], item)):
        target = min(range(count), key=lambda index: (shards[index]["estimated_seconds"], index))
        shards[target]["modules"].append(path)
        shards[target]["estimated_seconds"] += weight[path]
    for shard in shards:
        shard["modules"].sort()
        shard["estimated_seconds"] = round(shard["estimated_seconds"], 3)
    return shards


def shard_plan(selected, shard, *, weights=None, default=1.0):
    """One shard of a catalog selection, refusing to manufacture an empty plan."""
    index, count = shard
    plan = partition(selected, count, weights=weights, default=default)[index - 1]
    if not plan["modules"]:
        raise ValueError(f"Shard {index}/{count} selects no modules; use fewer shards for this selection")
    return plan


def module_durations(cases, modules):
    """Aggregate ``(dotted classname, seconds)`` test cases onto declared module paths."""
    dotted = {path[:-3].replace("/", "."): path for path in modules}
    totals = {}
    for classname, seconds in cases:
        seconds = _seconds(seconds, "Test case duration")
        parts = classname.split(".") if isinstance(classname, str) else []
        owner = next((dotted[".".join(parts[:end])] for end in range(len(parts), 0, -1)
                      if ".".join(parts[:end]) in dotted), None)
        if owner is not None:
            totals[owner] = totals.get(owner, 0.0) + seconds
    return totals
