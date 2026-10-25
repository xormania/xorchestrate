"""Check definitions and collection, composed over the existing command runner."""

from __future__ import annotations

import fnmatch
import math
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from .command import Result, Timeout, run
from .world import PlantFailed


@dataclass(frozen=True)
class Check:
    name: str
    argv: tuple[str, ...]
    timeout: float
    required: tuple[str, ...] = ()


def reject(expected, found, needed):
    raise ValueError(f"checks: expected {expected}; found {found}; needed {needed}")


def suite_checks(root, directory, patterns=("test_*.py",), *, timeout=600, partitions=None):
    """Prove a discovery partition, then declare one bounded child per slice."""
    root = Path(root).resolve()
    folder = (root / directory).resolve()
    if not folder.is_relative_to(root) or not folder.is_dir():
        reject("a suite directory inside the root", f"{directory} ABSENT or outside root",
               "restore the required suite")
    files = sorted(p.name for p in folder.glob("test_*.py") if p.is_file())
    if not files:
        reject("test files in every required suite", f"{directory}: no test files",
               "restore the suite's test files")
    if patterns is None:
        patterns = tuple(files)
    wrong = {name: sum(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)
             for name in files}
    wrong = {name: count for name, count in wrong.items() if count != 1}
    if wrong:
        reject("every test file covered exactly once", f"{directory}: {wrong}",
               "disjoint patterns whose union covers the family")
    checks = []
    for pattern in patterns:
        count = (partitions or {}).get(pattern, 1)
        if type(count) is not int or count < 1:
            reject("a positive case partition count", repr(count), "declare at least one partition")
        for index in range(count):
            selection = ("--partition", f"{index}/{count}") if count > 1 else ()
            suffix = f":{index}/{count}" if count > 1 else ""
            checks.append(Check(f"{directory}:{pattern}{suffix}",
                                (sys.executable, "-m", "ops.testkit.suite", *selection, directory, pattern),
                                timeout, (directory,)))
    return tuple(checks)


def _execute(check, cwd, env):
    started = time.monotonic()
    try:
        return run(check.argv, cwd=cwd, env=env, timeout=check.timeout)
    except (Timeout, PlantFailed, OSError) as exc:
        return Result(check.argv, 3, "", f"{exc}\n", time.monotonic() - started, str(cwd))


def execute_checks(checks, *, cwd, env, workers=None):
    """Preflight the whole plan before returning an iterator of named Results.

    Every process goes through run, including timeout cleanup. Results arrive
    when each command finishes; the caller owns presentation and the verdict.
    """
    checks = tuple(checks)
    if not checks:
        reject("a nonempty check plan", "no checks", "declare the required checks")
    names = [c.name for c in checks]
    if any(not name for name in names) or len(set(names)) != len(names):
        reject("unique nonempty check names", repr(names), "name each check once")
    for check in checks:
        if not check.argv or any(not isinstance(arg, str) or not arg for arg in check.argv):
            reject("a nonempty argv", check.name, "declare the executable and arguments")
        if not math.isfinite(check.timeout) or check.timeout <= 0:
            reject("a finite positive timeout", check.name, "bound the check's execution")
        missing = [path for path in check.required if not (Path(cwd) / path).exists()]
        if missing:
            reject("every required input present", f"{check.name}: {missing} ABSENT",
                   "restore the required inputs")
    if workers is None:
        workers = min(8, os.cpu_count() or 1)
    if workers < 1:
        reject("at least one worker", workers, "use a positive worker count")

    def collect():
        with ThreadPoolExecutor(max_workers=min(workers, len(checks))) as pool:
            pending = {pool.submit(_execute, check, cwd, env): check.name for check in checks}
            for future in as_completed(pending):
                yield pending[future], future.result()

    return collect()
