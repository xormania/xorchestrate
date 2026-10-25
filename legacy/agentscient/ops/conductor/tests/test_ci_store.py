"""CI-store acceptance tests, written from the contracts.

Contracts (only): `ops/conductor/ci-store.md` and the inherited
`ops/conductor/instrument-conventions.md`. The tool
`ops/conductor/tools/ci.py` is the subject and is not read here.
Tests invoke it through the test kit with a data-driven `gh` first on PATH.

The live store (`$XDG_STATE_HOME/agentscient/ci.db` else
`~/.local/state/agentscient/ci.db` at import-time HOME) must stay
untouched — asserted in tearDown.

The implementation lands in parallel. A missing tool file is one red
(`test_the_tool_file_is_present`). Every other case stages a
copy-tool-bytes stand-in so a missing file is not every test's failure:
when the tool exists its bytes are copied and run; when it does not, a
no-op stub (exit 0, no output) is planted so each assertion goes red on
the contract promise rather than Python's missing-file exit 2.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ops.testkit import World, run

REPO = Path(__file__).resolve().parents[3]
TOOL = REPO / "ops" / "conductor" / "tools" / "ci.py"
# The tool imports these beside itself (instrument-conventions.md §The
# shared implementation); a staged copy run from a scratch directory
# needs them staged too, or it refuses `tooling` before the contract.
SIBLINGS = ("instrument.py", "laneconf.py")
# Captured at import, before any test mutates a subprocess HOME.
_IMPORT_XDG = os.environ.get("XDG_STATE_HOME")
LIVE_STORES = []
if _IMPORT_XDG:
    LIVE_STORES.append(Path(_IMPORT_XDG) / "agentscient" / "ci.db")
LIVE_STORES.append(Path.home() / ".local" / "state" / "agentscient" / "ci.db")

AGENT = "CI Store Tester <ci-store@example.invalid>"
MALFORMED_AGENT = "JustAName"
REPO_SLUG = "owner/name"
RUN_CAP = 30

# PIN_GH_ENVELOPES: ingest reads GitHub's two API shapes
# `{"workflow_runs":[…]}` and `{"check_runs":[…]}`.
# PIN_RUN_ID: GitHub workflow-run `id` is store `run_id`.
# PIN_ATTEMPT: GitHub `run_attempt` is store `attempt`.
# PIN_BRANCH: GitHub `head_branch` is store `branch`.
# PIN_CHECK_ID: GitHub check-run `id` is store `check_id`.
# PIN_CHECK_TIMESTAMPS: GitHub check-run `started_at`/`completed_at`
# land in those store columns; duration_s is their difference in
# integer seconds when both are present, else NULL.
# PIN_FORGE: ingest writes forge `github`.
# PIN_SUMMARY: ingest prints `ingested runs=R checks=C shas=S`.
# PIN_REFUSAL_CLASS: the refusals table's left column is the token on
# the one stderr line (repo, gh-missing, gh-error, json, store, window,
# identity).

SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40

T_START = "2026-08-01T12:00:00Z"
T_END_10 = "2026-08-01T12:00:10Z"
T_END_20 = "2026-08-01T12:00:20Z"
T_CREATED = "2026-08-01T11:59:00Z"
INGESTED_AT = "2026-09-01T00:00:00Z"

GH_ERROR_L1 = "GH_ERROR_LINE1: boom"
GH_ERROR_L2 = "GH_ERROR_LINE2: still boom"

# Contract schema, adopted verbatim, used when planting a store for
# report verbs so those reds are the query promise, not ingest.
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

RUN_COLS = (
    "forge", "repo", "run_id", "attempt", "name", "event", "head_sha",
    "branch", "status", "conclusion", "created_at", "started_at",
    "completed_at", "duration_s", "ingested_at",
)
CHECK_COLS = (
    "forge", "repo", "check_id", "name", "head_sha", "status",
    "conclusion", "started_at", "completed_at", "duration_s",
    "ingested_at",
)


def _live_snapshot() -> dict[str, tuple[str, bytes | None]]:
    snap: dict[str, tuple[str, bytes | None]] = {}
    for path in LIVE_STORES:
        key = str(path)
        if key in snap:
            continue
        if path.is_file():
            snap[key] = ("file", path.read_bytes())
        elif path.exists():
            snap[key] = ("other", None)
        else:
            snap[key] = ("absent", None)
    return snap


def utc_ago(days: int, *, seconds: int = 0) -> str:
    t = datetime.now(timezone.utc) - timedelta(days=days, seconds=seconds)
    return t.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def gh_run(
    run_id: int,
    *,
    name: str,
    head_sha: str,
    attempt: int = 1,
    event: str = "push",
    branch: str = "dev",
    status: str = "completed",
    conclusion: str | None = "success",
    created_at: str = T_CREATED,
    started_at: str = T_START,
    completed_at: str | None = T_END_10,
) -> dict:
    return {
        "id": run_id,
        "name": name,
        "head_sha": head_sha,
        "run_attempt": attempt,
        "event": event,
        "head_branch": branch,
        "status": status,
        "conclusion": conclusion,
        "created_at": created_at,
        "run_started_at": started_at,
        "updated_at": completed_at or started_at,
    }


def gh_check(
    check_id: int,
    *,
    name: str,
    head_sha: str,
    status: str = "completed",
    conclusion: str | None = "success",
    started_at: str | None = T_START,
    completed_at: str | None = T_END_10,
) -> dict:
    return {
        "id": check_id,
        "name": name,
        "head_sha": head_sha,
        "status": status,
        "conclusion": conclusion,
        "started_at": started_at,
        "completed_at": completed_at,
    }


class _CIFixture(unittest.TestCase):
    """Scratch HOME + XDG_STATE_HOME, staged tool copy, stub gh, live store frozen."""

    def setUp(self):
        self._live_before = _live_snapshot()
        self.world = World(prefix="ci-store-")
        self.addCleanup(self.world.close)
        self.home = self.world.home
        self.xdg = self.world.xdg_state
        self.scratch = self.world.cwd
        self.bin = self.world.bin
        self.store = self.xdg / "agentscient" / "ci.db"
        self.home_store = (
            self.home / ".local" / "state" / "agentscient" / "ci.db"
        )
        self.assertFalse(
            self.store.exists(),
            "plant: isolated XDG store must start absent",
        )
        self.assertFalse(
            self.home_store.exists(),
            "plant: isolated HOME store must start absent",
        )
        self.gh = self.bin / "gh"
        self.argv_log = self.world.tools.log_path("gh")
        self.staged = self.world.stage(TOOL, siblings=SIBLINGS)
        # The staged copy has no tracked conf beside its reader; the
        # fixture names one, so the lane under XDG_STATE_HOME is the one
        # asserted.
        self.conf = self.world.plant_text(
            self.staged.parent.parent / "lane.conf",
            "LANE_NAME='agentscient'\n",
        )
        self.assertFalse(self.argv_log.exists(), "plant: argv log must start absent")
        self.assertFalse(self.gh.exists(), "plant: gh stub is not planted yet")

    def tearDown(self):
        self.assertEqual(
            _live_snapshot(),
            self._live_before,
            f"live store mutated: {LIVE_STORES}",
        )
        for path in LIVE_STORES:
            if self._live_before.get(str(path), ("absent", None))[0] == "absent":
                self.assertFalse(
                    path.exists(),
                    f"tests created the live store: {path}",
                )

    def plant_gh(
        self,
        *,
        runs: list[dict] | None = None,
        checks_by_sha: dict[str, list[dict]] | None = None,
        repo_slug: str = REPO_SLUG,
        repo_view_exit: int = 0,
        repo_view_stderr: str = "",
        fail_exit: int = 0,
        fail_stderr: str = "",
        raw_stdout: str | None = None,
    ) -> None:
        runs = list(runs or [])
        checks_by_sha = dict(checks_by_sha or {})
        responses = [{
            "match": ["repo", "view"],
            "stdout": json.dumps({"nameWithOwner": repo_slug}) + "\n"
            if repo_view_exit == 0 else "",
            "stderr": repo_view_stderr,
            "exit": repo_view_exit,
        }]
        if fail_exit == 0 and raw_stdout is None:
            responses.extend({
                "match": ["check-runs", sha],
                "stdout": json.dumps({
                    "total_count": len(rows),
                    "check_runs": rows,
                }) + "\n",
                "stderr": "",
                "exit": 0,
            } for sha, rows in checks_by_sha.items())
            responses.append({
                "match": ["actions/runs"],
                "stdout": json.dumps({
                    "total_count": len(runs),
                    "workflow_runs": runs,
                }) + "\n",
                "stderr": "",
                "exit": 0,
            })
        self.world.tools.plant(
            "gh",
            stdout=raw_stdout or "",
            stderr=fail_stderr if fail_exit else "unhandled gh argv\n",
            exit=fail_exit or (0 if raw_stdout is not None else 1),
            responses=responses,
        )

    def env(self, *, path: str | None = None, agent: str | None = None) -> dict[str, str]:
        extra = {"LANE_CONF": str(self.conf)}
        if path is not None:
            extra["PATH"] = path
        if agent is not None:
            extra["WF_AGENT"] = agent
        return self.world.env(extra)

    def path_without_gh(self) -> str:
        empty = self.scratch / "no-gh"
        empty.mkdir(exist_ok=True)
        self.assertFalse((empty / "gh").exists(), "plant: empty PATH dir has a gh")
        return str(empty)

    def invoke(
        self,
        argv: list[str],
        *,
        path: str | None = None,
        agent: str | None = None,
    ):
        env = self.env(path=path, agent=agent)
        if agent is None:
            self.assertNotIn("WF_AGENT", env)
        else:
            self.assertEqual(env.get("WF_AGENT"), agent)
        return run(
            [sys.executable, str(self.staged), *argv],
            cwd=REPO,
            env=env,
            timeout=RUN_CAP,
        )

    def ingest(self, extra: list[str] | None = None, *, agent: str | None = AGENT, **kw):
        argv = ["ingest", "--repo", REPO_SLUG]
        if extra:
            argv.extend(extra)
        return self.invoke(argv, agent=agent, **kw)

    def combined(self, proc) -> str:
        return f"{proc.stdout or ''}{proc.stderr or ''}"

    def assert_refusal(self, proc, class_name: str) -> None:
        err = proc.stderr or ""
        compact = err.lower().replace("_", "-")
        token = class_name.lower().replace("_", "-")
        spaced = token.replace("-", " ")
        self.assertTrue(
            token in compact or spaced in err.lower(),
            f"refusal class {class_name!r} not named in stderr: {err!r}",
        )
        self.assertEqual(proc.exit, 2, err)
        self.assertTrue(err.strip(), "refusal was silent")
        lines = [ln for ln in err.splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1, f"refusal must be one line: {err!r}")
        lower = err.lower()
        self.assertIn("expected", lower)
        self.assertIn("found", lower)
        self.assertIn("needed", lower)
        self.assertEqual(
            (proc.stdout or "").strip(),
            "",
            "refusal text belongs on stderr, not stdout",
        )

    def plant_store(self, runs: list[dict], checks: list[dict]) -> None:
        self.assertFalse(self.store.exists(), "plant: store must start absent")
        self.store.parent.mkdir(parents=True)
        conn = sqlite3.connect(self.store)
        try:
            conn.executescript(SCHEMA)
            if runs:
                cols = list(runs[0].keys())
                marks = ",".join("?" * len(cols))
                conn.executemany(
                    f"INSERT INTO runs ({','.join(cols)}) VALUES ({marks})",
                    [tuple(row[c] for c in cols) for row in runs],
                )
            if checks:
                cols = list(checks[0].keys())
                marks = ",".join("?" * len(cols))
                conn.executemany(
                    f"INSERT INTO checks ({','.join(cols)}) VALUES ({marks})",
                    [tuple(row[c] for c in cols) for row in checks],
                )
            conn.commit()
        finally:
            conn.close()
        self.assertTrue(self.store.is_file(), "INVALID: store plant did not land")
        self.assertGreater(self.store.stat().st_size, 0, "INVALID: store plant empty")
        self.assertEqual(
            self.count("runs"),
            len(runs),
            "INVALID: run plant count mismatch",
        )
        self.assertEqual(
            self.count("checks"),
            len(checks),
            "INVALID: check plant count mismatch",
        )
        probe = self.store.read_bytes()
        empty = SCHEMA.encode("utf-8")
        self.assertNotEqual(probe, empty, "INVALID: plant lost the rows")

    def _row(self, sql: str, args: tuple) -> dict | None:
        if not self.store.is_file():
            return None
        conn = sqlite3.connect(self.store)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(sql, args).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def run_row(self, run_id: int, attempt: int = 1) -> dict | None:
        return self._row(
            "SELECT * FROM runs WHERE run_id=? AND attempt=?",
            (run_id, attempt),
        )

    def check_row(self, check_id: int) -> dict | None:
        return self._row("SELECT * FROM checks WHERE check_id=?", (check_id,))

    def count(self, table: str) -> int:
        if not self.store.is_file():
            return 0
        conn = sqlite3.connect(self.store)
        try:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        finally:
            conn.close()

    def table_columns(self, table: str) -> list[str]:
        conn = sqlite3.connect(self.store)
        try:
            rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
            return [r[1] for r in rows]
        finally:
            conn.close()

    def user_tables(self) -> list[str]:
        if not self.store.is_file():
            return []
        conn = sqlite3.connect(self.store)
        try:
            rows = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name"
            ).fetchall()
            return [r[0] for r in rows]
        finally:
            conn.close()

    def lines_with(self, text: str, name: str) -> list[str]:
        lines = [ln for ln in text.splitlines() if name in ln]
        self.assertTrue(lines, f"no line names {name!r} in {text!r}")
        return lines

    def labeled(self, line: str, key: str):
        match = re.search(
            rf"(?:^|[\s,;]){re.escape(key)}\s*[=:]\s*(-?\d+(?:\.\d+)?)",
            line,
            re.IGNORECASE,
        )
        if not match:
            return None
        raw = match.group(1)
        if re.fullmatch(r"-?\d+", raw):
            return int(raw)
        return float(raw)

    def duration_stats(self, text: str, name: str) -> dict:
        line = self.lines_with(text, name)[0]
        stats = {
            key: self.labeled(line, key)
            for key in ("count", "p50", "p95", "max")
        }
        if all(v is not None for v in stats.values()):
            return stats
        rest = line.replace(name, " ", 1)
        nums = re.findall(r"-?\d+(?:\.\d+)?", rest)
        self.assertGreaterEqual(
            len(nums), 4, f"need count p50 p95 max on {line!r}"
        )
        return {
            "count": int(float(nums[0])),
            "p50": float(nums[1]),
            "p95": float(nums[2]),
            "max": float(nums[3]),
        }

    def failure_stats(self, text: str, name: str) -> dict:
        line = self.lines_with(text, name)[0]
        count = self.labeled(line, "count")
        if count is None:
            count = self.labeled(line, "completed")
        failures = self.labeled(line, "failures")
        if failures is None:
            failures = self.labeled(line, "failed")
        rate = self.labeled(line, "rate")
        if rate is None:
            rate = self.labeled(line, "failure_rate")
        if rate is None:
            match = re.search(r"\b(\d+\.\d{2})\b", line)
            self.assertIsNotNone(
                match, f"failure rate is not two decimals: {line!r}"
            )
            rate = float(match.group(1))
        else:
            self.assertRegex(
                f"{float(rate):.2f}",
                r"^\d+\.\d{2}$",
                f"rate not two decimals: {line!r}",
            )
        if count is None or failures is None:
            rate_m = re.search(r"\b\d+\.\d{2}\b", line)
            rest = line
            if rate_m:
                rest = line[: rate_m.start()] + " " + line[rate_m.end() :]
            rest = rest.replace(name, " ", 1)
            ints = [int(x) for x in re.findall(r"\b\d+\b", rest)]
            self.assertGreaterEqual(
                len(ints), 2, f"need completed count and failures: {line!r}"
            )
            if count is None:
                count = ints[0]
            if failures is None:
                failures = ints[1]
        return {
            "count": int(count),
            "failures": int(failures),
            "rate": float(rate),
        }

    def stored_run(self, **fields) -> dict:
        row = {
            "forge": "github",
            "repo": REPO_SLUG,
            "event": "push",
            "branch": "dev",
            "status": "completed",
            "conclusion": "success",
            "created_at": T_CREATED,
            "started_at": T_START,
            "completed_at": T_END_10,
            "duration_s": 10,
            "ingested_at": INGESTED_AT,
        }
        row.update(fields)
        return row

    def stored_check(self, **fields) -> dict:
        row = {
            "forge": "github",
            "repo": REPO_SLUG,
            "status": "completed",
            "conclusion": "success",
            "started_at": T_START,
            "completed_at": T_END_10,
            "duration_s": 10,
            "ingested_at": INGESTED_AT,
        }
        row.update(fields)
        return row


class ToolPresence(_CIFixture):
    """Missing tool file counts once."""

    def test_the_tool_file_is_present(self):
        self.assertTrue(TOOL.is_file(), f"RED: tool is absent: {TOOL}")


class IngestHappy(_CIFixture):
    """1. Runs and checks land with contract columns; summary; exit 0."""

    def test_ingest_lands_runs_and_checks_with_contract_columns(self):
        self.plant_gh(
            runs=[
                gh_run(101, name="CI", head_sha=SHA_A, attempt=1),
                gh_run(102, name="Docs", head_sha=SHA_B, attempt=1),
            ],
            checks_by_sha={
                SHA_A: [
                    gh_check(201, name="lint", head_sha=SHA_A),
                    gh_check(
                        202,
                        name="open-check",
                        head_sha=SHA_A,
                        status="in_progress",
                        conclusion=None,
                        started_at=T_START,
                        completed_at=None,
                    ),
                ],
                SHA_B: [gh_check(203, name="docs", head_sha=SHA_B)],
            },
        )
        proc = self.ingest()
        out = proc.stdout or ""
        self.assertRegex(
            out,
            r"ingested runs=2 checks=3 shas=2",
            f"missing ingest summary: {out!r} err={proc.stderr!r}",
        )
        self.assertEqual(proc.exit, 0, self.combined(proc))
        self.assertTrue(
            self.store.is_file(),
            f"ingest did not create XDG store {self.store}",
        )
        self.assertFalse(
            self.home_store.exists(),
            "store leaked to HOME while XDG_STATE_HOME was set",
        )
        tables = self.user_tables()
        self.assertIn("runs", tables)
        self.assertIn("checks", tables)
        for col in RUN_COLS:
            self.assertIn(col, self.table_columns("runs"), f"runs missing {col}")
        for col in CHECK_COLS:
            self.assertIn(
                col, self.table_columns("checks"), f"checks missing {col}"
            )
        self.assertEqual(self.count("runs"), 2)
        self.assertEqual(self.count("checks"), 3)
        row = self.run_row(101)
        self.assertIsNotNone(row, "run 101 did not land")
        self.assertEqual(row["forge"], "github")
        self.assertEqual(row["repo"], REPO_SLUG)
        self.assertEqual(row["run_id"], 101)
        self.assertEqual(row["attempt"], 1)
        self.assertEqual(row["name"], "CI")
        self.assertEqual(row["event"], "push")
        self.assertEqual(row["head_sha"], SHA_A)
        self.assertEqual(row["branch"], "dev")
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["conclusion"], "success")
        self.assertTrue(row["ingested_at"], "ingested_at must be stamped")
        check = self.check_row(201)
        self.assertIsNotNone(check, "check 201 did not land")
        self.assertEqual(check["forge"], "github")
        self.assertEqual(check["repo"], REPO_SLUG)
        self.assertEqual(check["name"], "lint")
        self.assertEqual(check["head_sha"], SHA_A)
        self.assertEqual(check["duration_s"], 10)
        open_check = self.check_row(202)
        self.assertIsNotNone(open_check, "in-progress check 202 did not land")
        self.assertIsNone(
            open_check["duration_s"],
            "duration_s must be NULL when a timestamp is missing",
        )


class Idempotence(_CIFixture):
    """2. Re-ingest is free; in-progress gains its conclusion."""

    def test_second_identical_ingest_does_not_change_row_counts(self):
        self.plant_gh(
            runs=[gh_run(101, name="CI", head_sha=SHA_A)],
            checks_by_sha={SHA_A: [gh_check(201, name="lint", head_sha=SHA_A)]},
        )
        first = self.ingest()
        self.assertRegex(first.stdout or "", r"ingested runs=1 checks=1 shas=1")
        self.assertEqual(self.count("runs"), 1)
        self.assertEqual(self.count("checks"), 1)
        second = self.ingest()
        self.assertEqual(second.exit, 0, self.combined(second))
        self.assertEqual(self.count("runs"), 1)
        self.assertEqual(self.count("checks"), 1)

    def test_in_progress_run_gains_conclusion_on_reingest(self):
        pending = gh_run(
            101,
            name="CI",
            head_sha=SHA_A,
            status="in_progress",
            conclusion=None,
            completed_at=None,
        )
        done = gh_run(
            101,
            name="CI",
            head_sha=SHA_A,
            status="completed",
            conclusion="success",
        )
        self.plant_gh(
            runs=[pending],
            checks_by_sha={
                SHA_A: [
                    gh_check(
                        201,
                        name="lint",
                        head_sha=SHA_A,
                        status="in_progress",
                        conclusion=None,
                        completed_at=None,
                    )
                ]
            },
        )
        first = self.ingest()
        self.assertRegex(first.stdout or "", r"ingested runs=1")
        row = self.run_row(101)
        self.assertIsNotNone(row, "in-progress run 101 did not land")
        self.assertEqual(row["status"], "in_progress")
        self.assertTrue(
            row["conclusion"] in (None, ""),
            f"in-progress conclusion should be empty, got {row['conclusion']!r}",
        )
        self.plant_gh(
            runs=[done],
            checks_by_sha={
                SHA_A: [
                    gh_check(
                        201,
                        name="lint",
                        head_sha=SHA_A,
                        conclusion="success",
                    )
                ]
            },
        )
        second = self.ingest()
        self.assertEqual(second.exit, 0, self.combined(second))
        self.assertEqual(self.count("runs"), 1)
        updated = self.run_row(101)
        self.assertIsNotNone(updated)
        self.assertEqual(updated["conclusion"], "success")
        self.assertEqual(updated["status"], "completed")


class Flakes(_CIFixture):
    """3. Same name+sha success+failure is a flake; cancelled/skipped are not."""

    def test_success_and_failure_same_name_sha_lists_the_pair_and_exits_1(self):
        newer = utc_ago(1)
        older = utc_ago(3)
        self.plant_store(
            runs=[
                self.stored_run(
                    run_id=1, attempt=1, name="CI", head_sha=SHA_A,
                    created_at=newer,
                ),
                self.stored_run(
                    run_id=2, attempt=1, name="CI", head_sha=SHA_B,
                    created_at=older,
                ),
            ],
            checks=[
                self.stored_check(
                    check_id=1, name="new-flake", head_sha=SHA_A,
                    conclusion="success", started_at=newer,
                    completed_at=newer, duration_s=10,
                ),
                self.stored_check(
                    check_id=2, name="new-flake", head_sha=SHA_A,
                    conclusion="failure", started_at=newer,
                    completed_at=newer, duration_s=11,
                ),
                self.stored_check(
                    check_id=3, name="old-flake", head_sha=SHA_B,
                    conclusion="success", started_at=older,
                    completed_at=older, duration_s=10,
                ),
                self.stored_check(
                    check_id=4, name="old-flake", head_sha=SHA_B,
                    conclusion="failure", started_at=older,
                    completed_at=older, duration_s=12,
                ),
            ],
        )
        proc = self.invoke(["flakes"], agent=None)
        out = proc.stdout or ""
        self.assertEqual(proc.exit, 1, self.combined(proc))
        self.assertIn("new-flake", out)
        self.assertIn(SHA_A, out)
        self.assertIn("old-flake", out)
        self.assertIn(SHA_B, out)
        self.assertLess(
            out.find("new-flake"),
            out.find("old-flake"),
            f"flakes must list newest first: {out!r}",
        )

    def test_cancelled_and_skipped_join_no_pair(self):
        now = utc_ago(1)
        self.plant_store(
            runs=[self.stored_run(run_id=1, attempt=1, name="CI", head_sha=SHA_A)],
            checks=[
                self.stored_check(
                    check_id=1, name="flake-gate", head_sha=SHA_A,
                    conclusion="success", started_at=now, completed_at=now,
                ),
                self.stored_check(
                    check_id=2, name="flake-gate", head_sha=SHA_A,
                    conclusion="failure", started_at=now, completed_at=now,
                ),
                self.stored_check(
                    check_id=3, name="calm-gate", head_sha=SHA_A,
                    conclusion="success", started_at=now, completed_at=now,
                ),
                self.stored_check(
                    check_id=4, name="calm-gate", head_sha=SHA_A,
                    conclusion="cancelled", started_at=now, completed_at=now,
                ),
                self.stored_check(
                    check_id=5, name="skip-gate", head_sha=SHA_A,
                    conclusion="failure", started_at=now, completed_at=now,
                ),
                self.stored_check(
                    check_id=6, name="skip-gate", head_sha=SHA_A,
                    conclusion="skipped", started_at=now, completed_at=now,
                ),
            ],
        )
        proc = self.invoke(["flakes"], agent=None)
        out = proc.stdout or ""
        self.assertEqual(proc.exit, 1, self.combined(proc))
        self.assertIn("flake-gate", out)
        self.assertNotIn("calm-gate", out)
        self.assertNotIn("skip-gate", out)

    def test_clean_window_exits_0(self):
        self.plant_gh(
            runs=[gh_run(301, name="clean-ci", head_sha=SHA_C)],
            checks_by_sha={
                SHA_C: [
                    gh_check(301, name="clean-gate", head_sha=SHA_C),
                    gh_check(302, name="clean-gate", head_sha=SHA_C),
                ],
            },
        )
        ingested = self.ingest()
        self.assertRegex(
            ingested.stdout or "",
            r"ingested runs=\d+ checks=\d+ shas=\d+",
            f"clean-window fixture must ingest first: {self.combined(ingested)!r}",
        )
        proc = self.invoke(["flakes"], agent=None)
        self.assertEqual(proc.exit, 0, self.combined(proc))
        self.assertNotIn(SHA_C, proc.stdout or "")


class Durations(_CIFixture):
    """4. count/p50/p95/max per name, sorted p95 descending."""

    def test_durations_print_count_p50_p95_max_sorted_p95_desc(self):
        now = utc_ago(1)
        self.plant_store(
            runs=[self.stored_run(run_id=1, attempt=1, name="CI", head_sha=SHA_A)],
            checks=[
                self.stored_check(
                    check_id=1, name="gate-slow", head_sha=SHA_A,
                    started_at=now, completed_at=now, duration_s=100,
                ),
                self.stored_check(
                    check_id=2, name="gate-mid", head_sha=SHA_A,
                    started_at=now, completed_at=now, duration_s=10,
                ),
                self.stored_check(
                    check_id=3, name="gate-mid", head_sha=SHA_A,
                    started_at=now, completed_at=now, duration_s=20,
                ),
                self.stored_check(
                    check_id=4, name="gate-mid", head_sha=SHA_A,
                    started_at=now, completed_at=now, duration_s=30,
                ),
                self.stored_check(
                    check_id=5, name="gate-fast", head_sha=SHA_A,
                    started_at=now, completed_at=now, duration_s=5,
                ),
            ],
        )
        proc = self.invoke(["durations"], agent=None)
        out = proc.stdout or ""
        slow = self.duration_stats(out, "gate-slow")
        mid = self.duration_stats(out, "gate-mid")
        fast = self.duration_stats(out, "gate-fast")
        self.assertEqual(slow["count"], 1)
        self.assertEqual(slow["p50"], 100)
        self.assertEqual(slow["p95"], 100)
        self.assertEqual(slow["max"], 100)
        self.assertEqual(mid["count"], 3)
        self.assertEqual(mid["p50"], 20)
        self.assertEqual(mid["max"], 30)
        self.assertGreaterEqual(mid["p95"], mid["p50"])
        self.assertLessEqual(mid["p95"], mid["max"])
        self.assertEqual(fast["count"], 1)
        self.assertEqual(fast["p50"], 5)
        self.assertEqual(fast["p95"], 5)
        self.assertEqual(fast["max"], 5)
        self.assertLess(out.find("gate-slow"), out.find("gate-mid"), out)
        self.assertLess(out.find("gate-mid"), out.find("gate-fast"), out)
        self.assertEqual(proc.exit, 0, self.combined(proc))


class Failures(_CIFixture):
    """5. Rates to two decimals, sorted by rate then count, descending."""

    def test_failures_print_rates_to_two_decimals_sorted_by_rate_then_count(self):
        now = utc_ago(1)
        rows = []
        cid = 1
        def add(name, conclusion):
            nonlocal cid
            rows.append(
                self.stored_check(
                    check_id=cid, name=name, head_sha=SHA_A,
                    conclusion=conclusion, started_at=now, completed_at=now,
                )
            )
            cid += 1
        add("fail-always", "failure")
        add("fail-always", "failure")
        add("fail-more", "failure")
        add("fail-more", "failure")
        add("fail-more", "success")
        add("fail-more", "success")
        add("fail-half", "failure")
        add("fail-half", "success")
        add("fail-none", "success")
        add("fail-none", "success")
        add("fail-none", "success")
        self.plant_store(
            runs=[self.stored_run(run_id=1, attempt=1, name="CI", head_sha=SHA_A)],
            checks=rows,
        )
        proc = self.invoke(["failures"], agent=None)
        out = proc.stdout or ""
        always = self.failure_stats(out, "fail-always")
        more = self.failure_stats(out, "fail-more")
        half = self.failure_stats(out, "fail-half")
        none = self.failure_stats(out, "fail-none")
        self.assertEqual(always["count"], 2)
        self.assertEqual(always["failures"], 2)
        self.assertEqual(f"{always['rate']:.2f}", "100.00")
        self.assertEqual(more["count"], 4)
        self.assertEqual(more["failures"], 2)
        self.assertEqual(f"{more['rate']:.2f}", "50.00")
        self.assertEqual(half["count"], 2)
        self.assertEqual(half["failures"], 1)
        self.assertEqual(f"{half['rate']:.2f}", "50.00")
        self.assertEqual(none["count"], 3)
        self.assertEqual(none["failures"], 0)
        self.assertEqual(f"{none['rate']:.2f}", "0.00")
        self.assertLess(out.find("fail-always"), out.find("fail-more"), out)
        self.assertLess(out.find("fail-more"), out.find("fail-half"), out)
        self.assertLess(out.find("fail-half"), out.find("fail-none"), out)
        self.assertEqual(proc.exit, 0, self.combined(proc))


class Report(_CIFixture):
    """6. Totals, covered window, flake count; exit 1 iff flakes exist."""

    def test_report_totals_window_flake_count_and_exits_1_when_flakes(self):
        tmin = utc_ago(5)
        tmax = utc_ago(1)
        self.plant_store(
            runs=[
                self.stored_run(
                    run_id=1, attempt=1, name="CI", head_sha=SHA_A,
                    created_at=tmin,
                ),
                self.stored_run(
                    run_id=2, attempt=1, name="CI", head_sha=SHA_B,
                    created_at=tmax,
                ),
            ],
            checks=[
                self.stored_check(
                    check_id=1, name="slow-a", head_sha=SHA_A,
                    started_at=tmax, completed_at=tmax, duration_s=100,
                ),
                self.stored_check(
                    check_id=2, name="slow-b", head_sha=SHA_A,
                    started_at=tmax, completed_at=tmax, duration_s=50,
                ),
                self.stored_check(
                    check_id=3, name="slow-c", head_sha=SHA_A,
                    started_at=tmax, completed_at=tmax, duration_s=20,
                ),
                self.stored_check(
                    check_id=4, name="flake-gate", head_sha=SHA_A,
                    conclusion="success", started_at=tmax, completed_at=tmax,
                ),
                self.stored_check(
                    check_id=5, name="flake-gate", head_sha=SHA_A,
                    conclusion="failure", started_at=tmax, completed_at=tmax,
                ),
            ],
        )
        proc = self.invoke(["report"], agent=None)
        out = proc.stdout or ""
        text = out.lower()
        self.assertTrue(
            re.search(r"runs\s*[=:]\s*2", text) or re.search(r"\b2\s+runs\b", text),
            f"report must name runs total 2: {out!r}",
        )
        self.assertTrue(
            re.search(r"checks\s*[=:]\s*5", text)
            or re.search(r"\b5\s+checks\b", text),
            f"report must name checks total 5: {out!r}",
        )
        self.assertIn(tmin, out)
        self.assertIn(tmax, out)
        self.assertIn("slow-a", out)
        self.assertIn("slow-b", out)
        self.assertIn("slow-c", out)
        self.assertTrue(
            re.search(r"flake[s]?\s*[=:]?\s*1", text)
            or re.search(r"\b1\s+flake", text),
            f"report must name flake count 1: {out!r}",
        )
        self.assertEqual(proc.exit, 1, self.combined(proc))

    def test_report_exits_0_without_flakes_and_names_totals(self):
        now = utc_ago(1)
        self.plant_store(
            runs=[
                self.stored_run(
                    run_id=1, attempt=1, name="CI", head_sha=SHA_A,
                    created_at=now,
                )
            ],
            checks=[
                self.stored_check(
                    check_id=1, name="only-gate", head_sha=SHA_A,
                    conclusion="success", started_at=now, completed_at=now,
                    duration_s=8,
                )
            ],
        )
        proc = self.invoke(["report"], agent=None)
        out = proc.stdout or ""
        text = out.lower()
        self.assertTrue(
            re.search(r"runs\s*[=:]\s*1", text) or re.search(r"\b1\s+runs\b", text),
            f"report must name runs total 1: {out!r}",
        )
        self.assertTrue(
            re.search(r"checks\s*[=:]\s*1", text)
            or re.search(r"\b1\s+checks\b", text),
            f"report must name checks total 1: {out!r}",
        )
        self.assertIn("only-gate", out)
        self.assertEqual(proc.exit, 0, self.combined(proc))
        self.assertNotIn("identity", (proc.stderr or "").lower())


class DaysWindow(_CIFixture):
    """7. --days filters the window; 0 and non-integers refuse class window."""

    def test_days_filters_the_window(self):
        recent = utc_ago(1)
        mid = utc_ago(10)
        old = utc_ago(20)
        self.plant_store(
            runs=[
                self.stored_run(
                    run_id=1, attempt=1, name="CI", head_sha=SHA_A,
                    created_at=recent,
                ),
                self.stored_run(
                    run_id=2, attempt=1, name="CI", head_sha=SHA_B,
                    created_at=mid,
                ),
                self.stored_run(
                    run_id=3, attempt=1, name="CI", head_sha=SHA_C,
                    created_at=old,
                ),
            ],
            checks=[
                self.stored_check(
                    check_id=1, name="gate-new", head_sha=SHA_A,
                    conclusion="success", started_at=recent, completed_at=recent,
                ),
                self.stored_check(
                    check_id=2, name="gate-new", head_sha=SHA_A,
                    conclusion="failure", started_at=recent, completed_at=recent,
                ),
                self.stored_check(
                    check_id=3, name="gate-mid", head_sha=SHA_B,
                    conclusion="success", started_at=mid, completed_at=mid,
                ),
                self.stored_check(
                    check_id=4, name="gate-mid", head_sha=SHA_B,
                    conclusion="failure", started_at=mid, completed_at=mid,
                ),
                self.stored_check(
                    check_id=5, name="gate-old", head_sha=SHA_C,
                    conclusion="success", started_at=old, completed_at=old,
                ),
                self.stored_check(
                    check_id=6, name="gate-old", head_sha=SHA_C,
                    conclusion="failure", started_at=old, completed_at=old,
                ),
            ],
        )
        seven = self.invoke(["flakes", "--days", "7"], agent=None)
        out7 = seven.stdout or ""
        self.assertEqual(seven.exit, 1, self.combined(seven))
        self.assertIn("gate-new", out7)
        self.assertNotIn("gate-mid", out7)
        self.assertNotIn("gate-old", out7)
        default = self.invoke(["flakes"], agent=None)
        outd = default.stdout or ""
        self.assertEqual(default.exit, 1, self.combined(default))
        self.assertIn("gate-new", outd)
        self.assertIn("gate-mid", outd)
        self.assertNotIn("gate-old", outd)
        wide = self.invoke(["flakes", "--days", "30"], agent=None)
        outw = wide.stdout or ""
        self.assertEqual(wide.exit, 1, self.combined(wide))
        self.assertIn("gate-new", outw)
        self.assertIn("gate-mid", outw)
        self.assertIn("gate-old", outw)

    def test_days_0_and_non_integers_refuse_window(self):
        now = utc_ago(1)
        self.plant_store(
            runs=[self.stored_run(run_id=1, attempt=1, name="CI", head_sha=SHA_A)],
            checks=[
                self.stored_check(
                    check_id=1, name="lint", head_sha=SHA_A,
                    started_at=now, completed_at=now,
                )
            ],
        )
        for bad in ("0", "abc", "1.5"):
            with self.subTest(days=bad):
                proc = self.invoke(["flakes", "--days", bad], agent=None)
                self.assertIn(
                    bad,
                    proc.stderr or "",
                    f"--days {bad!r} must appear in found: {proc.stderr!r}",
                )
                self.assert_refusal(proc, "window")


class Refusals(_CIFixture):
    """8. Conventions wire: exit 2, one stderr line, expected/found/needed."""

    def test_ingest_without_identity_refuses(self):
        self.plant_gh(
            runs=[gh_run(101, name="CI", head_sha=SHA_A)],
            checks_by_sha={SHA_A: [gh_check(201, name="lint", head_sha=SHA_A)]},
        )
        proc = self.ingest(agent=None)
        self.assert_refusal(proc, "identity")

    def test_malformed_wf_agent_refuses_identity(self):
        self.plant_gh(
            runs=[gh_run(101, name="CI", head_sha=SHA_A)],
            checks_by_sha={SHA_A: [gh_check(201, name="lint", head_sha=SHA_A)]},
        )
        proc = self.ingest(agent=MALFORMED_AGENT)
        self.assert_refusal(proc, "identity")
        self.assertIn(MALFORMED_AGENT, proc.stderr or "")

    def test_report_verbs_with_no_store_refuse_store(self):
        self.assertFalse(self.store.exists(), "plant: store must stay absent")
        for verb in ("flakes", "durations", "failures", "report"):
            with self.subTest(verb=verb):
                proc = self.invoke([verb], agent=None)
                self.assert_refusal(proc, "store")

    def test_gh_missing_from_path_refuses(self):
        path = self.path_without_gh()
        proc = self.ingest(path=path)
        self.assert_refusal(proc, "gh-missing")

    def test_gh_nonzero_multiline_stderr_is_one_line_gh_error(self):
        self.plant_gh(
            fail_exit=1,
            fail_stderr=GH_ERROR_L1 + "\n" + GH_ERROR_L2 + "\n",
        )
        proc = self.ingest()
        err = proc.stderr or ""
        self.assertIn(GH_ERROR_L1, err, f"gh stderr line 1 missing: {err!r}")
        self.assertIn(GH_ERROR_L2, err, f"gh stderr line 2 missing: {err!r}")
        self.assert_refusal(proc, "gh-error")

    def test_non_json_gh_output_refuses_json(self):
        self.plant_gh(raw_stdout="this is not json\n")
        proc = self.ingest()
        self.assert_refusal(proc, "json")

    def test_underivable_repo_refuses_repo(self):
        self.plant_gh(
            repo_view_exit=1,
            repo_view_stderr="gh: not a github repository\n",
        )
        proc = self.invoke(["ingest"], agent=AGENT)
        self.assert_refusal(proc, "repo")


if __name__ == "__main__":
    unittest.main()
