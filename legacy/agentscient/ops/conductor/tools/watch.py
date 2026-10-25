#!/usr/bin/env python3
"""Wait for a pull request's check table to settle."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from typing import NoReturn

# The shared instrument module sits beside this file; its directory goes
# on the path so the import resolves however this file was loaded: as
# the script, by path from another tool, or as a copy a test staged
# with its siblings.
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
try:
    from instrument import Refusal, conf, emit, refuse
except ModuleNotFoundError:
    print(
        "tooling: expected instrument.py beside this tool; found absent; "
        "needed restore ops/conductor/tools/instrument.py beside it",
        file=sys.stderr,
    )
    raise SystemExit(2) from None

PASS_STATES = {"SUCCESS"}
FAIL_STATES = {
    "FAILURE",
    "ERROR",
    "CANCELLED",
    "TIMED_OUT",
    "ACTION_REQUIRED",
    "STARTUP_FAILURE",
}
SKIP_STATES = {"SKIPPED", "NEUTRAL"}
# gh reports a head with no check registered as an error even under --json
# (gh 2.99.0, pkg/cmd/pr/checks/checks.go: the error returns before the
# JSON write). That is the zero-row table the pinned reading waits
# through, not a failure to look (LD-37).
NO_CHECKS_EXIT = 1
NO_CHECKS_TEXT = "no checks reported on the "


class Parser(argparse.ArgumentParser):
    """Turn argparse failures into the tool's refusal shape."""

    def error(self, message: str) -> NoReturn:
        refuse("arguments", "valid watch arguments", message, "use --help for usage")


def positive_number(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        refuse("number", "a positive integer", repr(value), "NUMBER must be at least 1")
    if number < 1:
        refuse("number", "a positive integer", str(number), "NUMBER must be at least 1")
    return number


def classify(state: str) -> str:
    if state in PASS_STATES:
        return "pass"
    if state in FAIL_STATES:
        return "fail"
    if state in SKIP_STATES:
        return "skip"
    return "pending"


def read_checks(number: int, repo: str | None) -> list[tuple[str, str]]:
    command = ["gh", "pr", "checks", str(number), "--json", "name,state"]
    if repo is not None:
        command.extend(["--repo", repo])
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        refuse("gh-missing", "gh on PATH", "gh was not found", "install gh or add it to PATH")
    if result.returncode != 0:
        detail = result.stderr.rstrip("\n")
        if result.returncode == NO_CHECKS_EXIT and detail.startswith(NO_CHECKS_TEXT):
            return []
        refuse(
            "gh-error",
            "gh pr checks to exit 0",
            f"exit {result.returncode}, stderr={detail!r}",
            "make the gh command succeed",
        )
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        refuse("json", "a JSON array of name/state objects", str(error), "valid gh JSON output")
    if not isinstance(data, list) or any(
        not isinstance(row, dict)
        or set(row) != {"name", "state"}
        or not isinstance(row["name"], str)
        or not isinstance(row["state"], str)
        for row in data
    ):
        refuse(
            "json",
            "a JSON array of string name/state objects",
            repr(data),
            "the shape emitted by gh pr checks --json name,state",
        )
    return [(row["name"], classify(row["state"])) for row in data]


def counts(checks: list[tuple[str, str]]) -> dict[str, int]:
    result = {"rows": len(checks), "pass": 0, "fail": 0, "pending": 0, "skip": 0}
    for _, state in checks:
        result[state] += 1
    return result


def configured_check_names() -> tuple[str, ...]:
    """Read CHECK_NAMES through the shared reader; any absence is conf-key."""
    try:
        return tuple(conf("CHECK_NAMES").split())
    except Refusal as error:
        refuse(
            "conf-key",
            "CHECK_NAMES in the lane conf",
            str(error),
            "define CHECK_NAMES in the lane conf",
        )


def watch(args: argparse.Namespace) -> int:
    if args.interval < 5:
        refuse("interval", "at least 5 seconds", str(args.interval), "--interval must be at least 5")
    if args.expect_min < 1:
        refuse(
            "arguments",
            "--expect-min of at least 1",
            str(args.expect_min),
            "--expect-min must preserve the non-empty reading",
        )
    if shutil.which("gh") is None:
        refuse("gh-missing", "gh on PATH", "gh was not found", "install gh or add it to PATH")

    expected = (
        tuple(
            name.strip()
            for name in args.expect_names.split(",")
            if name.strip()
        )
        if args.expect_names is not None
        else configured_check_names()
    )
    scale = _tick_scale()
    started = time.monotonic()
    sleep_adjustment = 0.0
    # Registration is not atomic: a settled-looking table is a candidate,
    # settled only when the next poll reports the identical table.
    candidate = None
    while True:
        checks = read_checks(args.number, args.repo)
        observed = counts(checks)
        missing = [name for name in expected if name not in {n for n, _ in checks}]
        settled_shape = (
            observed["rows"] >= args.expect_min
            and observed["pending"] == 0
            and not missing
        )
        table = tuple(sorted(checks))
        confirmed = settled_shape and candidate == table
        candidate = table if settled_shape else None
        elapsed = time.monotonic() - started + sleep_adjustment
        line = (
            f"poll rows={observed['rows']} pass={observed['pass']} "
            f"fail={observed['fail']} pending={observed['pending']} "
            f"skip={observed['skip']} elapsed={int(elapsed)}s"
        )
        if missing:
            line += f" missing={len(missing)}"
        if settled_shape and not confirmed:
            line += " confirming"
        print(line, file=sys.stderr)
        if confirmed:
            for name, state in checks:
                print(f"{state.upper()}\t{name}")
            print(
                f"settled rows={observed['rows']} pass={observed['pass']} "
                f"fail={observed['fail']} skip={observed['skip']}"
            )
            return 1 if observed["fail"] else 0
        if elapsed >= args.timeout:
            print(
                f"abort rows={observed['rows']} pass={observed['pass']} "
                f"fail={observed['fail']} pending={observed['pending']} "
                f"skip={observed['skip']}",
                file=sys.stderr,
            )
            return 3
        time.sleep(args.interval * scale)
        sleep_adjustment += args.interval * (1 - scale)


def _tick_scale() -> float:
    """Test apparatus: scale requested sleeps, retaining their logical interval.

    Subprocess startup and other work consume real elapsed time once, never
    multiplied by the test scale. Unset means real time throughout.
    """
    raw = os.environ.get("WATCH_TICK_SCALE")
    if raw is None:
        return 1.0
    try:
        scale = float(raw)
    except ValueError:
        scale = 0.0
    if not scale > 0:
        refuse("tick-scale", "a positive number in WATCH_TICK_SCALE", repr(raw),
               "unset it, or set a positive number; it is test apparatus")
    return scale


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = Parser(description=__doc__)
    subparsers = parser.add_subparsers(dest="verb", required=True)
    pr = subparsers.add_parser("pr")
    pr.add_argument("number", type=positive_number, metavar="NUMBER")
    pr.add_argument("--repo")
    pr.add_argument("--interval", type=float, default=30)
    pr.add_argument("--timeout", type=float, default=1800)
    pr.add_argument("--expect-min", type=int, default=1)
    pr.add_argument("--expect-names")
    return parser.parse_args(argv)


def main() -> int:
    try:
        return watch(parse_args())
    except Refusal as refusal:
        return emit(refusal)


if __name__ == "__main__":
    raise SystemExit(main())
