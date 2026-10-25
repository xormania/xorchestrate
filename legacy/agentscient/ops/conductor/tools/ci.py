#!/usr/bin/env python3
"""Accumulate and report GitHub Actions run and check history."""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# The shared instrument module sits beside this file; its directory goes
# on the path so the import resolves however this file was loaded: as
# the script, by path from another tool, or as a copy a test staged
# with its siblings.
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
try:
    import instrument
    from instrument import Refusal, emit, identity, refuse, stamp, state_dir
except ModuleNotFoundError:
    print(
        "tooling: expected instrument.py beside this tool; found absent; "
        "needed restore ops/conductor/tools/instrument.py beside it",
        file=sys.stderr,
    )
    raise SystemExit(2) from None

GH_TIMEOUT_S = 30
SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(
    forge TEXT NOT NULL, repo TEXT NOT NULL,
    run_id INTEGER NOT NULL, attempt INTEGER NOT NULL,
    name TEXT NOT NULL, event TEXT, head_sha TEXT NOT NULL,
    branch TEXT, status TEXT, conclusion TEXT,
    created_at TEXT, started_at TEXT, completed_at TEXT,
    duration_s INTEGER, ingested_at TEXT NOT NULL,
    PRIMARY KEY (forge, repo, run_id, attempt));
CREATE TABLE IF NOT EXISTS checks(
    forge TEXT NOT NULL, repo TEXT NOT NULL,
    check_id INTEGER NOT NULL, name TEXT NOT NULL,
    head_sha TEXT NOT NULL, status TEXT, conclusion TEXT,
    started_at TEXT, completed_at TEXT, duration_s INTEGER,
    ingested_at TEXT NOT NULL,
    PRIMARY KEY (forge, repo, check_id));
"""


def store_path() -> Path:
    return state_dir() / "ci.db"


def positive(value: str, option: str) -> int:
    try:
        parsed = int(value)
    except ValueError:
        parsed = 0
    if parsed <= 0:
        refuse("window", f"{option} as a positive integer", repr(value), f"set {option} above zero")
    return parsed


def connect(*, write: bool) -> sqlite3.Connection:
    path = store_path()
    if write:
        connection = instrument.connect_store(path, mode="rwc")
        connection.executescript(SCHEMA)
    elif path.is_file():
        connection = instrument.connect_store(path)
    else:
        refuse(
            "store",
            f"the CI store at {path}",
            "ABSENT",
            "run ingest to create and populate the store",
        )
    connection.row_factory = sqlite3.Row
    return connection


def gh(args: list[str]) -> str:
    executable = shutil.which("gh")
    if executable is None:
        refuse("gh-missing", "gh on PATH", "ABSENT", "install and authenticate gh")
    try:
        result = subprocess.run(
            [executable, *args], capture_output=True, text=True, check=False, timeout=GH_TIMEOUT_S
        )
    except subprocess.TimeoutExpired:
        refuse("gh-error", f"gh completing within {GH_TIMEOUT_S}s", "timeout", "restore GitHub access and retry")
    if result.returncode != 0:
        detail = result.stderr.strip() or f"gh exited {result.returncode}"
        refuse("gh-error", "a successful gh request", detail, "restore GitHub access and retry")
    return result.stdout


def json_object(raw: str, description: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        refuse("json", description, f"invalid JSON at character {error.pos}", "retry a valid gh response")
    if not isinstance(value, dict):
        refuse("json", description, type(value).__name__, "retry a valid gh response")
    return value


def repository(value: str | None) -> str:
    if value:
        return value
    executable = shutil.which("gh")
    if executable is None:
        refuse("gh-missing", "gh on PATH", "ABSENT", "install and authenticate gh")
    try:
        result = subprocess.run(
            [executable, "repo", "view", "--json", "nameWithOwner"],
            capture_output=True,
            text=True,
            check=False,
            timeout=GH_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        refuse(
            "repo",
            "a repository derived by gh repo view",
            "gh timed out",
            "pass --repo OWNER/NAME",
        )
    if result.returncode != 0:
        detail = result.stderr.strip() or f"gh exited {result.returncode}"
        refuse(
            "repo",
            "a repository derived by gh repo view",
            detail,
            "pass --repo OWNER/NAME",
        )
    raw = result.stdout
    payload = json_object(raw, "gh repo JSON containing nameWithOwner")
    repo = payload.get("nameWithOwner")
    if not isinstance(repo, str) or not repo:
        refuse("repo", "a repository from gh repo view", repr(repo), "pass --repo OWNER/NAME")
    return repo


def required(row: dict[str, Any], key: str, kind: type, context: str) -> Any:
    value = row.get(key)
    if not isinstance(value, kind) or (kind is str and not value):
        refuse("json", f"{context}.{key} as {kind.__name__}", repr(value), "retry a valid gh response")
    return value


def api_pages(endpoint: str, collection: str, *, maximum: int | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    page = 1
    while maximum is None or len(rows) < maximum:
        page_size = 100 if maximum is None else min(100, maximum - len(rows))
        separator = "&" if "?" in endpoint else "?"
        raw = gh(["api", f"{endpoint}{separator}per_page={page_size}&page={page}"])
        payload = json_object(raw, f"gh API object containing {collection}")
        values = payload.get(collection)
        if not isinstance(values, list) or any(not isinstance(item, dict) for item in values):
            refuse("json", f"a JSON array at {collection}", repr(values), "retry a valid gh response")
        rows.extend(values)
        if len(values) < page_size:
            break
        page += 1
        if maximum is None and page > 100:
            refuse("json", f"at most 100 pages of {collection}", "more pages", "reduce the repository history")
    return rows if maximum is None else rows[:maximum]


def parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def duration(started: object, completed: object) -> int | None:
    start = parse_time(started)
    end = parse_time(completed)
    return int((end - start).total_seconds()) if start is not None and end is not None else None


def ingest(args: argparse.Namespace) -> None:
    identity()
    limit = positive(args.limit, "--limit")
    repo = repository(args.repo)
    run_rows = api_pages(f"repos/{repo}/actions/runs", "workflow_runs", maximum=limit)
    at = stamp()
    shas: set[str] = set()
    prepared_runs: list[tuple[object, ...]] = []
    for row in run_rows:
        run_id = required(row, "id", int, "workflow run")
        name = required(row, "name", str, "workflow run")
        head_sha = required(row, "head_sha", str, "workflow run")
        attempt = row.get("run_attempt", 1)
        if not isinstance(attempt, int):
            refuse("json", "workflow run.run_attempt as int", repr(attempt), "retry a valid gh response")
        shas.add(head_sha)
        completed_at = row.get("completed_at") or row.get("updated_at")
        prepared_runs.append((
            "github", repo, run_id, attempt, name, row.get("event"), head_sha,
            row.get("head_branch"), row.get("status"), row.get("conclusion"),
            row.get("created_at"), row.get("run_started_at"), completed_at,
            duration(row.get("run_started_at"), completed_at), at,
        ))
    check_rows: list[dict[str, Any]] = []
    for head_sha in sorted(shas):
        check_rows.extend(api_pages(f"repos/{repo}/commits/{head_sha}/check-runs", "check_runs", maximum=None))
    prepared_checks: list[tuple[object, ...]] = []
    for row in check_rows:
        check_id = required(row, "id", int, "check run")
        name = required(row, "name", str, "check run")
        head_sha = required(row, "head_sha", str, "check run")
        prepared_checks.append((
            "github", repo, check_id, name, head_sha, row.get("status"), row.get("conclusion"),
            row.get("started_at"), row.get("completed_at"),
            duration(row.get("started_at"), row.get("completed_at")), at,
        ))
    with connect(write=True) as connection:
        connection.executemany("INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", prepared_runs)
        connection.executemany("INSERT OR REPLACE INTO checks VALUES (?,?,?,?,?,?,?,?,?,?,?)", prepared_checks)
    print(f"ingested runs={len(prepared_runs)} checks={len(prepared_checks)} shas={len(shas)}")


def cutoff(days: str) -> str:
    count = positive(days, "--days")
    return (datetime.now(UTC) - timedelta(days=count)).isoformat().replace("+00:00", "Z")


def percentile(values: list[int], fraction: float) -> int:
    # Nearest-rank percentile: rank is ceil(p * N) in the sorted sample.
    return sorted(values)[max(0, math.ceil(fraction * len(values)) - 1)]


def duration_rows(connection: sqlite3.Connection, since: str) -> list[tuple[str, int, int, int, int]]:
    grouped: dict[str, list[int]] = {}
    for row in connection.execute(
        "SELECT name, duration_s FROM checks WHERE started_at >= ? AND completed_at IS NOT NULL AND duration_s IS NOT NULL",
        (since,),
    ):
        grouped.setdefault(row["name"], []).append(row["duration_s"])
    result = [
        (name, len(values), percentile(values, 0.5), percentile(values, 0.95), max(values))
        for name, values in grouped.items()
    ]
    return sorted(result, key=lambda row: (-row[3], row[0]))


def flake_rows(connection: sqlite3.Connection, since: str) -> list[sqlite3.Row]:
    return list(connection.execute(
        "SELECT name, head_sha, SUM(conclusion = 'success') AS successes, "
        "SUM(conclusion = 'failure') AS failures, MAX(completed_at) AS last_completed "
        "FROM checks WHERE started_at >= ? AND completed_at IS NOT NULL "
        "AND conclusion IN ('success','failure') GROUP BY name, head_sha "
        "HAVING successes > 0 AND failures > 0 ORDER BY last_completed DESC, name, head_sha",
        (since,),
    ))


def failure_rows(connection: sqlite3.Connection, since: str) -> list[tuple[str, int, int, float]]:
    rows = []
    for row in connection.execute(
        "SELECT name, COUNT(*) AS completed, SUM(conclusion = 'failure') AS failures "
        "FROM checks WHERE started_at >= ? AND completed_at IS NOT NULL GROUP BY name",
        (since,),
    ):
        rate = 100.0 * row["failures"] / row["completed"]
        rows.append((row["name"], row["completed"], row["failures"], rate))
    return sorted(rows, key=lambda row: (-row[3], -row[1], row[0]))


def run_report(verb: str, days: str) -> int:
    since = cutoff(days)
    with connect(write=False) as connection:
        if verb == "flakes":
            rows = flake_rows(connection, since)
            for row in rows:
                print(f"{row['name']}\t{row['head_sha']}\tsuccess={row['successes']} failure={row['failures']} last={row['last_completed']}")
            return 1 if rows else 0
        if verb == "durations":
            for name, count, p50, p95, maximum in duration_rows(connection, since):
                print(f"{name}\tcount={count} p50={p50} p95={p95} max={maximum}")
            return 0
        if verb == "failures":
            for name, completed, failures, rate in failure_rows(connection, since):
                print(f"{name}\tcompleted={completed} failures={failures} rate={rate:.2f}%")
            return 0
        run_summary = connection.execute(
            "SELECT COUNT(*) AS total, MIN(created_at) AS first, MAX(created_at) AS last FROM runs WHERE created_at >= ?",
            (since,),
        ).fetchone()
        check_total = connection.execute("SELECT COUNT(*) FROM checks WHERE started_at >= ?", (since,)).fetchone()[0]
        slowest = duration_rows(connection, since)[:3]
        flakes = flake_rows(connection, since)
        failures = failure_rows(connection, since)
        print(f"runs={run_summary['total']} checks={check_total} window={run_summary['first'] or 'none'}..{run_summary['last'] or 'none'}")
        print("slowest=" + (", ".join(f"{row[0]}:{row[3]}" for row in slowest) or "none"))
        print(f"flakes={len(flakes)} top_failure=" + (f"{failures[0][0]}:{failures[0][3]:.2f}%" if failures else "none"))
        return 1 if flakes else 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="verb", required=True)
    ingest_parser = commands.add_parser("ingest")
    ingest_parser.add_argument("--repo")
    ingest_parser.add_argument("--limit", default="200")
    for verb in ("flakes", "durations", "failures", "report"):
        report_parser = commands.add_parser(verb)
        report_parser.add_argument("--days", default="14")
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        if args.verb == "ingest":
            ingest(args)
            return 0
        return run_report(args.verb, args.days)
    except Refusal as error:
        return emit(error)


if __name__ == "__main__":
    raise SystemExit(main())
