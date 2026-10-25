"""Shared fast, local integration and explicit rehearsal check plans."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

from ops.testkit import Check, execute_checks, suite_checks

ROOT = Path(__file__).resolve().parents[4]
FAMILIES = (
    "ops/devlane/dispatch/tests", "ops/devlane/harness/tests",
    "ops/devlane/task/tests", "ops/devlane/telemetry/tests",
    "ops/devlane/hooks/tests", "ops/devlane/fixtures/tests",
    "ops/devlane/workflow/tests", "ops/testkit/tests",
    "ops/conductor/tests", ".claude/skills/pr-overview/tests",
)
# Independent modules prevent a growing alphabetical remainder from becoming
# one eight-minute process. The shared kit proves complete, disjoint discovery.
SHARDS = {directory: None for directory in FAMILIES}
# Native run 35952536576 measured these at 196s and 251s respectively.
PARTITIONS = {
    "ops/conductor/tests": {"test_sessions_trial.py": 4, "test_coordination.py": 4},
}
APPLICATION_CASE = "test_symfony_app_app.SymfonyAppMakeProve.test_full_proof_after_composer_install"


PROFILES = ("ci", "local", "rehearsal")
INVENTORY = "ops/devlane/workflow/checks/test-profiles.json"


def inventory(root):
    """Every module is explicitly classified; additions cannot silently vanish."""
    data = json.loads((root / INVENTORY).read_text())
    if set(data) != set(PROFILES):
        raise ValueError("checks: expected ci/local/rehearsal inventory; found invalid profiles; needed classify every test")
    listed = [path for paths in data.values() for path in paths]
    discovered = {str(path.relative_to(root)) for directory in FAMILIES
                  for path in (root / directory).glob("test_*.py")}
    if len(listed) != len(set(listed)) or set(listed) != discovered:
        raise ValueError(f"checks: expected every test classified once; found unclassified={sorted(discovered - set(listed))}, absent={sorted(set(listed) - discovered)} or duplicates; needed update {INVENTORY}")
    return data


def check_cost(check, root):
    """Large modules start first; scheduling never determines coverage."""
    if not check.required or not (root / check.required[0]).is_dir():
        return 1
    folder = root / check.required[0]
    size = sum(path.stat().st_size for path in folder.glob(check.argv[-1]))
    cost = size / 1000
    if "--partition" in check.argv:
        cost /= int(check.argv[check.argv.index("--partition") + 1].split("/")[1])
    return max(0.01, cost)


def python_check(name, path, *args, timeout=60, required=()):
    return Check(name, (sys.executable, path, *args), timeout, (path, *required))


LINT = Check("ruff", ("ruff", "check", "."), 60, ("ruff.toml",))
GUARDS = (
    LINT,
    python_check("term-wall", "ops/devlane/workflow/checks/term_wall.py", "--root", "."),
    python_check("testkit-ratchet", "ops/devlane/workflow/checks/testkit_ratchet.py",
                 required=("ops/devlane/workflow/checks/testkit-ratchet.json",)),
    python_check("conf-closure", "ops/conductor/tools/laneconf.py", "closure",
                 required=("ops/lane.conf",)),
    python_check("conf-literals", "ops/conductor/tools/laneconf.py", "literals",
                 required=("ops/lane.conf",)),
    python_check("probes", "ops/conductor/tools/probes.py", "run", timeout=600,
                 required=("ops/conductor/handoff/probes",)),
)


def plan(root, selected=(), *, profile="rehearsal"):
    # Portable subjects carry their own lint contract. They explicitly select
    # their copied suites, and never claim the full repository was checked.
    checks = [replace(LINT, required=())] if selected else list(GUARDS)
    for directory in selected or FAMILIES:
        patterns = ("test_*.py",) if selected else SHARDS.get(directory, ("test_*.py",))
        discovered = suite_checks(root, directory, patterns,
                                  partitions=None if selected else PARTITIONS.get(directory))
        for check in discovered:
            if not selected and check.name == "ops/devlane/fixtures/tests:test_symfony_app_app.py":
                check = replace(check, argv=(*check.argv[:-2], "--exclude-case", APPLICATION_CASE, *check.argv[-2:]))
            checks.append(check)
    if profile not in PROFILES:
        raise ValueError("checks: expected a declared test profile")
    if selected or profile == "rehearsal":
        return checks
    data = inventory(root)
    accepted = set(data["ci"] + (data["local"] if profile == "local" else []))
    return [check for check in checks if check in GUARDS or
            check.required[0] + "/" + check.argv[-1] in accepted]


def run_plan(checks, *, root, env, scope="FULL", cached=None, record=None, workers=None):
    # Validate every required input before starting even the cheap phase.
    execute_checks(checks, cwd=root, env=env, workers=workers)
    cached = cached or {}
    print(f"{scope}: {len(checks)} required checks", flush=True)
    completed = set()
    failed = []
    guard_names = {check.name for check in GUARDS}
    phases = ([check for check in checks if check.name in guard_names],
              [check for check in checks if check.name not in guard_names])
    for phase in phases:
        if not phase:
            continue
        pending = []
        for check in phase:
            if check.name in cached:
                origin = cached[check.name]
                print(f"REUSED {check.name} from {origin['url']}", flush=True)
                completed.add(check.name)
                if record:
                    record(check.name, None, origin)
            else:
                pending.append(check)
        if not pending:
            continue
        # Large modules start first. Durations in CI evidence expose any
        # remaining imbalance without making coverage depend on a timing file.
        pending.sort(key=lambda c: check_cost(c, root), reverse=True)
        for name, result in execute_checks(pending, cwd=root, env=env, workers=workers):
            print(f"== {name}", flush=True)
            print(result.stdout, end="", flush=True)
            print(result.stderr, end="", flush=True)
            print(f"{'PASS' if result.exit == 0 else 'FAIL'} {name} ({result.duration:.3f}s)", flush=True)
            completed.add(name)
            if result.exit != 0:
                failed.append(name)
            if record:
                record(name, result, None)
        if failed:
            break
    missing = {check.name for check in checks} - completed
    good = not failed and not missing
    print(f"{scope}: {'PASS' if good else 'FAIL'} ({len(completed)}/{len(checks)} completed)", flush=True)
    return 0 if good else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", action="append", default=[], metavar="DIR",
                        help="lint plus explicitly selected suites; PARTIAL, never full CI")
    parser.add_argument("--profile", choices=PROFILES, default="local",
                        help="ci: fast checks; local: fast plus integration; rehearsal: all dev tests")
    args = parser.parse_args(argv)
    env = dict(os.environ)
    try:
        return run_plan(plan(ROOT, args.suite, profile=args.profile), root=ROOT, env=env,
                        scope="PARTIAL" if args.suite else args.profile.upper(),
                        workers=1 if env.get("SUITES_SERIAL") == "1" else None)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
