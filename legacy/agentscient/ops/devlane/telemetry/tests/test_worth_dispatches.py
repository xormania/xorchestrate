"""Dispatches are a first-class input to the meter.

Authored from ops/process/worth.md rules 9 and 10 (as specified by the
Unit A plan): the meter reads the dispatch store, windows on
at.launched, takes spend from session.spend only, and never prints 0
for an unreadable figure. report prints a dispatches block; waste
ranks dispatch rows with sessions; JSON is the same truth.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import test_worth as worth_contract

from ops.testkit import World, run

MARKER = "DISPATCH-LEAK-MARKER"
WINDOW_SINCE = "2026-08-22T09:00:00.000Z"
WINDOW_UNTIL = "2026-08-22T12:00:00.000Z"
WINDOW_NOW = "2026-08-22T13:00:00.000Z"

CLAUDE_ID = "20260822T100000Z-plan-claude-aa0001"
CODEX_ID = "20260822T100100Z-check-tests-codex-bb0002"
GROK_ID = "20260822T100200Z-review-grok-cc0003"
MISSING_ID = "20260822T100300Z-plan-grok-dd0004"
BEFORE_ID = "20260821T100000Z-plan-claude-ee0005"
UNRESOLVED_ID = "20260822T100400Z-plan-codex-ff0006"
MALFORMED_ID = "20260822T100500Z-plan-claude-aa0007"
TRACKED_ID = "20260822T100600Z-plan-grok-aa0008"
FOREIGN_ID = "20260822T100700Z-plan-claude-aa0009"
AT_SINCE_ID = "20260822T090000Z-plan-claude-aa0010"
AT_UNTIL_ID = "20260822T120000Z-plan-codex-aa0011"

CLAUDE_SPEND = {
    "input": 100,
    "cached": 2010,
    "output": 10,
    "total": 2120,
    "source": "stream",
    "messages": 2,
    "cost_usd": 5.16,
}
CODEX_SPEND = {
    "input": 400,
    "cached": 0,
    "output": 90,
    "total": 490,
    "source": "stream",
    "messages": 5,
}
GROK_SPEND = {
    "input": 70,
    "cached": 15,
    "output": 15,
    "total": 100,
    "source": "stream",
    "cost_usd_ticks": 9,
}
FOREIGN_SPEND = {
    "input": 700,
    "cached": 700,
    "output": 700,
    "total": 777001,
    "source": "stream",
    "messages": 2,
    "cost_usd": 88.88,
}
AT_SINCE_SPEND = {
    "input": 40,
    "cached": 10,
    "output": 10,
    "total": 43210,
    "source": "stream",
    "messages": 1,
    "cost_usd": 1.25,
}
AT_UNTIL_SPEND = {
    "input": 30,
    "cached": 30,
    "output": 30,
    "total": 654321,
    "source": "stream",
    "messages": 1,
    "cost_usd": 12.34,
}


def window_args():
    return ("--since", WINDOW_SINCE, "--until", WINDOW_UNTIL)


class DispatchMeterTests(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.world = World(prefix="worth-dispatches-")
        self.addCleanup(self.world.close)
        self.w = worth_contract.WorthContractTests(
            "test_default_window_boundaries_and_independent_edge_overrides"
        )
        self.root = self.world.root
        self.repo = self.world.cwd / "repo"
        self.repo.mkdir()
        self.claude = self.root / "claude"
        self.codex = self.root / "codex"
        self.grok = self.root / "grok"
        ops_root = Path(__file__).resolve().parents[3]
        conductor_tools = ops_root / "conductor" / "tools"
        self.world.stage(
            conductor_tools / "laneconf.py",
            siblings=("instrument.py",),
        )
        self.subject = self.world.stage(worth_contract.WORTH)
        self.lane_conf = self.world.stage(ops_root / "lane.conf", name="lane.conf")
        for name, value in (
            ("root", self.root),
            ("repo", self.repo),
            ("claude", self.claude),
            ("codex", self.codex),
            ("grok", self.grok),
            ("_git_env", self._git_env),
            ("_git", self._git),
            ("_write_jsonl", self._write_jsonl),
        ):
            setattr(self.w, name, value)
        for name in (
            "_plant_claude",
            "_assert_key_number",
            "_harness_record",
            "_line_for_harness",
            "_signals_of_kind",
            "_assert_plain_json_parity",
        ):
            setattr(self, name, getattr(self.w, name))
        self._git("init", "-q", "-b", "dev")
        self._git("config", "user.name", "Fixture Owner")
        self._git("config", "user.email", "fixture@example.invalid")
        self.world.plant_text(self.repo / "README.md", "throwaway worth fixture\n")
        self._git("add", "-A")
        self._git(
            "commit", "-q", "-m", "initial fixture",
            when="2026-08-20T10:00:00.000Z",
        )
        self.initial = self._git("rev-parse", "HEAD").stdout.strip()
        self.w.initial = self.initial
        self.home = self.world.home
        self.dispatch = self.root / "dispatch"
        self.dispatch.mkdir()

    def _git_env(self, when=None):
        extra = {"TZ": "UTC"}
        if when is not None:
            fixed = worth_contract.git_date(when)
            extra.update({
                "GIT_AUTHOR_DATE": fixed,
                "GIT_COMMITTER_DATE": fixed,
            })
        return self.world.env(extra)

    def _git(self, *args, when=None, check=True):
        result = run(
            ["git", *args],
            cwd=self.repo,
            env=self._git_env(when),
            timeout=60,
        )
        if check:
            self.assertEqual(
                result.exit,
                0,
                f"git {' '.join(args)} failed\nstdout:\n{result.stdout}"
                f"\nstderr:\n{result.stderr}",
            )
        return result

    def _write_jsonl(self, path, entries):
        self.world.plant_text(
            path,
            "".join(
                json.dumps(entry, sort_keys=True) + "\n"
                for entry in entries
            ),
        )
        planted = worth_contract.json_lines(path)
        self.assertEqual(len(planted), len(entries))
        self.assertEqual(planted, entries)
        return path

    def _json_worth(self, verb, *args, now=WINDOW_NOW, repo=None, **kwargs):
        proc = self._run_worth(
            verb,
            *args,
            "--format",
            "json",
            now=now,
            repo=repo,
            **kwargs,
        )
        self.assertEqual(proc.exit, 0, proc.stderr)
        try:
            value = json.loads(proc.stdout)
        except ValueError as exc:
            self.fail(f"invalid JSON: {exc}\nstdout:\n{proc.stdout}")
        self.assertIsInstance(value, dict)
        return value

    def _worth_env(self, extra=None):
        env = self.world.env({
            "LC_ALL": "C",
            "TZ": "Etc/GMT+11",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "LANE_CONF": str(self.lane_conf),
        })
        for key in ("DISPATCH_JOBS", "XDG_STATE_HOME"):
            env.pop(key, None)
        if extra:
            env.update(extra)
        return env

    def _run_worth(
        self,
        verb,
        *args,
        now=WINDOW_NOW,
        repo=None,
        dispatch_dir="jobs-env",
        extra_env=None,
    ):
        repo = str(repo or self.repo)
        cmd = [
            sys.executable,
            str(self.subject),
            verb,
            *args,
            "--now",
            now,
            "--repo",
            repo,
            "--claude-dir",
            str(self.claude),
            "--codex-dir",
            str(self.codex),
            "--grok-dir",
            str(self.grok),
        ]
        env = extra_env or {}
        if dispatch_dir == "jobs-env":
            merged = {"DISPATCH_JOBS": str(self.dispatch), **env}
            env = merged
        elif dispatch_dir is not None:
            cmd.extend(["--dispatch-dir", str(dispatch_dir)])
        result = run(
            cmd,
            cwd=self.root,
            env=self._worth_env(env),
            timeout=60,
        )
        return result

    def _record_payload(
        self,
        job_id,
        *,
        job,
        harness,
        stage,
        launched,
        spend,
        status="closed",
        model_ran="stream-ran-model",
        unit="work",
        branch="work",
        snapshot_root=None,
        ref_sha=None,
    ):
        closed = "2026-08-22T10:20:00Z" if status == "closed" else None
        harness_block = harness if isinstance(harness, dict) else {
            "name": harness,
            "version": "1.0.0",
            "isolation": {"observed": {"unresolved": "not run"}},
            "sandbox": "plan",
            "containment": "policy",
            "argv": [harness],
        }
        return {
            "id": job_id,
            "lane": "dev",
            "stage": stage,
            "unit": unit,
            "lineage": {"branch": branch, "base_sha": "a" * 40},
            "follows": [],
            "job": job,
            "role": "read",
            "dispatched_by": "Launch Test <noreply@example.invalid>",
            "at": {"launched": launched, "closed": closed},
            "snapshot": {
                "mode": "whole",
                "ref_name": "HEAD",
                "ref_sha": ref_sha or "b" * 40,
                "behind_tip": 0,
                "root": snapshot_root or str(
                    self.dispatch / job_id / "snapshot"
                ),
            },
            "harness": harness_block,
            "model": {
                "requested": "alias-requested",
                "effort_requested": None,
                "ran": model_ran,
                "read_from": "stream",
            },
            "session": {
                "id": "00000000-0000-4000-8000-000000000001",
                "stream": None,
                "stream_sha256_at_close": None,
                "spend": spend,
            },
            "brief": {
                "template": {"path": "jobs.json", "sha256": "c" * 64},
                "scope": "pin",
                "inputs": [],
                "sha256": "d" * 64,
                "bytes": 3,
            },
            "caps": {"source": "wires.py"},
            "overrides": [],
            "attempts": [],
            "result": {
                "head": "b" * 40,
                "changed_paths": [],
                "residual_paths": [],
                "envelope": {},
            } if status == "closed" else None,
            "status": status,
        }

    def _write_job_dir(
        self,
        job_id,
        *,
        record=None,
        raw_out=None,
        root=None,
        exit_text="0\n",
    ):
        root = Path(root or self.dispatch)
        job_dir = root / job_id
        job_dir.mkdir(parents=True)
        prompt = self.world.plant_text(
            job_dir / "prompt.txt", f"{MARKER} brief for {job_id}\n"
        )
        self.assertIn(MARKER, prompt.read_text(encoding="utf-8"))
        self.world.plant_text(
            job_dir / "state.json",
            json.dumps({
                "pid": 1,
                "pgid": 1,
                "session": {"id": "00000000-0000-4000-8000-000000000001"},
                "stream": None,
                "attempt": 1,
            }) + "\n",
        )
        self.world.plant_text(job_dir / "exit", exit_text)
        raw = raw_out if raw_out is not None else '{"job":"plan"}\n'
        self.world.plant_text(job_dir / "raw.out", raw)
        if record is not None:
            payload = dict(record)
            path = self.world.plant_text(
                job_dir / "record.json",
                json.dumps(payload, indent=2) + "\n",
            )
            landed = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(landed["id"], job_id)
        return job_dir

    def _plant_dispatch(self, job_id, **kwargs):
        raw_out = kwargs.pop("raw_out", None)
        root = kwargs.pop("root", None)
        tracked = kwargs.pop("tracked", False)
        write_job_record = kwargs.pop("write_job_record", True)
        record = self._record_payload(job_id, **kwargs)
        job_dir = self._write_job_dir(
            job_id,
            record=record if write_job_record else None,
            raw_out=raw_out,
            root=root,
        )
        if tracked:
            dest = (
                self.repo / ".dev" / "records" / "dispatches"
                / f"{job_id}.json"
            )
            payload = dict(record)
            self.world.plant_text(
                dest,
                json.dumps(payload, indent=2) + "\n",
            )
            self.assertEqual(
                json.loads(dest.read_text(encoding="utf-8"))["id"],
                job_id,
            )
        return job_dir, record

    def _init_other_repo(self, path, subject="foreign repository fixture"):
        path.mkdir(parents=True)
        env = self._git_env("2026-08-20T11:00:00.000Z")
        for args in (
            ("git", "init", "-q", "-b", "dev"),
            ("git", "config", "user.name", "Foreign Owner"),
            ("git", "config", "user.email", "foreign@example.invalid"),
        ):
            proc = run(args, cwd=path, env=env, timeout=60)
            self.assertEqual(proc.exit, 0, proc.stderr)
        readme = self.world.plant_text(
            path / "README.md", "foreign repository fixture\n"
        )
        self.assertEqual(
            readme.read_text(encoding="utf-8"),
            "foreign repository fixture\n",
        )
        for args in (
            ("git", "add", "-A"),
            ("git", "commit", "-q", "-m", subject),
        ):
            proc = run(args, cwd=path, env=env, timeout=60)
            self.assertEqual(proc.exit, 0, proc.stderr)
        sha = run(
            ("git", "rev-parse", "HEAD"),
            cwd=path,
            env=env,
            timeout=60,
        ).stdout.strip()
        home_sha = self.w._git("rev-parse", "HEAD").stdout.strip()
        self.assertTrue(sha)
        self.assertNotEqual(
            sha,
            home_sha,
            "the foreign repository must not share this report's HEAD",
        )
        return sha

    def _report(self, **kwargs):
        kwargs.setdefault("now", WINDOW_NOW)
        return self._json_worth("report", *window_args(), **kwargs)

    def _waste(self, *extra, **kwargs):
        kwargs.setdefault("now", WINDOW_NOW)
        return self._json_worth(
            "waste", *window_args(), *extra, **kwargs,
        )

    def _plain(self, verb, *extra, **kwargs):
        proc = self._run_worth(verb, *window_args(), *extra, **kwargs)
        self.assertEqual(proc.exit, 0, proc.stderr)
        return proc.stdout

    def _dispatch_block(self, data):
        self.assertIn(
            "dispatches",
            data,
            f"report JSON lacks a dispatches block: {sorted(data)}",
        )
        block = data["dispatches"]
        self.assertIsInstance(block, dict)
        for key in ("claude", "codex", "grok", "rows"):
            self.assertIn(key, block, f"dispatches block lacks {key}: {block}")
        self.assertIsInstance(block["rows"], list)
        return block

    def _row_by_id(self, rows, job_id):
        matches = [
            row for row in rows
            if isinstance(row, dict) and row.get("id") == job_id
        ]
        self.assertEqual(
            len(matches),
            1,
            f"expected one row for {job_id}, found {matches} in {rows}",
        )
        return matches[0]

    def _field_at(self, value, *path):
        current = value
        walked = []
        for key in path:
            location = ".".join(walked) if walked else "row"
            self.assertIsInstance(
                current,
                dict,
                f"expected a dict at {location}; found "
                f"{type(current).__name__}: {current}",
            )
            self.assertIn(
                key,
                current,
                f"missing {'.'.join(walked + [key])}; "
                f"keys={sorted(current)}",
            )
            walked.append(key)
            current = current[key]
        return current

    def _ranked(self, data):
        sessions = data["sessions"]
        self.assertIsInstance(sessions, list)
        return sessions

    def _dispatch_rows(self, data):
        rows = [
            row for row in self._ranked(data)
            if isinstance(row, dict) and row.get("kind") == "dispatch"
        ]
        self.assertGreater(
            len(rows),
            0,
            f"waste ranked no dispatch rows: {data['sessions']}",
        )
        return rows

    def _assert_no_leak(self, *blobs):
        for blob in blobs:
            self.assertNotIn(MARKER, blob)

    def test_report_counts_a_claude_dispatch_with_usd(self):
        """Scenario: a dispatched Claude run's USD appears in the report"""
        self._plant_dispatch(
            CLAUDE_ID,
            job="plan",
            harness="claude",
            stage="plan",
            launched="2026-08-22T10:00:00Z",
            spend=CLAUDE_SPEND,
        )
        plain = self._plain("report")
        data = self._report()
        block = self._dispatch_block(data)
        row = self._row_by_id(block["rows"], CLAUDE_ID)
        self.assertEqual(row["job"], "plan")
        self.assertEqual(row.get("harness"), "claude")
        self._assert_key_number(row, ("input", "in"), 100)
        self._assert_key_number(row, ("cached",), 2010)
        self._assert_key_number(row, ("output", "out"), 10)
        self._assert_key_number(row, ("total",), 2120)
        self._assert_key_number(row, ("cost_usd", "cost"), 5.16)
        self.assertIn(CLAUDE_ID, plain)
        self.assertRegex(plain, r"\bcost_usd\s*[=:]\s*5\.16\b")
        self.assertIn("job=plan", plain)
        claude = block["claude"]
        self._assert_key_number(claude, ("dispatches",), 1)
        self.assertRegex(
            plain,
            r"\bclaude\b[^\n]*dispatches\s*=\s*1\b",
        )
        self._assert_no_leak(plain, json.dumps(data))

    def test_report_prints_codex_tokens_and_unrecorded_cost(self):
        """Scenario: a Codex dispatch prints tokens and cost=unrecorded"""
        self._plant_dispatch(
            CODEX_ID,
            job="check-tests",
            harness="codex",
            stage="check-tests",
            launched="2026-08-22T10:01:00Z",
            spend=CODEX_SPEND,
        )
        plain = self._plain("report")
        data = self._report()
        row = self._row_by_id(self._dispatch_block(data)["rows"], CODEX_ID)
        self.assertEqual(row["job"], "check-tests")
        self._assert_key_number(row, ("total",), 490)
        rendered = json.dumps(row).lower()
        self.assertIn("unrecorded", rendered)
        self.assertRegex(plain, r"\bcost\s*[=:]\s*unrecorded\b")
        self.assertNotRegex(
            plain,
            r"\bcost(?:_usd)?\s*[=:]\s*0\b",
        )
        self._assert_no_leak(plain, json.dumps(data))

    def test_report_prints_grok_tokens_and_ticks(self):
        """Scenario: a Grok dispatch prints tokens and ticks"""
        self._plant_dispatch(
            GROK_ID,
            job="adversarial-review",
            harness="grok",
            stage="review",
            launched="2026-08-22T10:02:00Z",
            spend=GROK_SPEND,
        )
        plain = self._plain("report")
        data = self._report()
        row = self._row_by_id(self._dispatch_block(data)["rows"], GROK_ID)
        self.assertEqual(row["job"], "adversarial-review")
        self._assert_key_number(row, ("total",), 100)
        self._assert_key_number(
            row, ("cost_usd_ticks", "costUsdTicks"), 9,
        )
        self.assertRegex(
            plain,
            r"\bcost_usd_ticks\s*[=:]\s*9\b",
        )
        self._assert_no_leak(plain, json.dumps(data))

    def test_a_job_directory_without_a_record_is_counted_unrecorded(self):
        """Scenario: a job directory without a record is counted and unrecorded"""
        self._write_job_dir(MISSING_ID)
        self.assertFalse(
            (self.dispatch / MISSING_ID / "record.json").exists(),
            "plant: this job has no record.json",
        )
        plain = self._plain("report")
        data = self._report()
        block = self._dispatch_block(data)
        row = self._row_by_id(block["rows"], MISSING_ID)
        rendered = json.dumps(row).lower()
        self.assertIn("unrecorded", rendered)
        self.assertNotIn(" 0", f" {json.dumps(row)} ")
        self.assertIn(MISSING_ID, plain)
        self.assertIn("unrecorded", plain.lower())
        self.assertNotRegex(
            self._line_for_harness(plain, "grok"),
            r"\b(?:in|input|cached|out|output|total|cost(?:_usd_ticks)?)"
            r"\s*[=:]\s*0\b",
        )
        grok = block["grok"]
        grok_text = json.dumps(grok).lower()
        self.assertTrue(
            "0/1" in grok_text or "counted" in grok_text,
            f"missing record must hold a denominator place: {grok}",
        )
        self.assertIn("counted=0/1", self._line_for_harness(plain, "grok"))

    def test_a_dispatch_launched_before_the_window_is_not_charged(self):
        """Scenario: a dispatch launched before the window is not charged to it"""
        self._plant_dispatch(
            CLAUDE_ID,
            job="plan",
            harness="claude",
            stage="plan",
            launched="2026-08-22T10:00:00Z",
            spend=CLAUDE_SPEND,
        )
        self._plant_dispatch(
            BEFORE_ID,
            job="plan",
            harness="claude",
            stage="plan",
            launched="2026-08-21T10:00:00Z",
            spend={
                "input": 9,
                "cached": 9,
                "output": 9,
                "total": 888_888,
                "cost_usd": 99.99,
            },
        )
        plain = self._plain("report")
        data = self._report()
        rows = self._dispatch_block(data)["rows"]
        ids = [row.get("id") for row in rows]
        self.assertIn(CLAUDE_ID, ids)
        self.assertNotIn(BEFORE_ID, ids)
        self.assertNotIn(BEFORE_ID, plain)
        self.assertNotIn("888888", plain)
        self.assertNotIn("888888", json.dumps(data))
        self.assertNotIn("99.99", plain)

    def test_window_includes_launched_at_since_and_excludes_until(self):
        self._plant_dispatch(
            AT_SINCE_ID,
            job="plan",
            harness="claude",
            stage="plan",
            launched=WINDOW_SINCE,
            spend=AT_SINCE_SPEND,
        )
        self._plant_dispatch(
            CLAUDE_ID,
            job="plan",
            harness="claude",
            stage="plan",
            launched="2026-08-22T10:00:00Z",
            spend=CLAUDE_SPEND,
        )
        self._plant_dispatch(
            AT_UNTIL_ID,
            job="check-tests",
            harness="codex",
            stage="check-tests",
            launched=WINDOW_UNTIL,
            spend=AT_UNTIL_SPEND,
        )
        since_record = json.loads(
            (self.dispatch / AT_SINCE_ID / "record.json").read_text(
                encoding="utf-8",
            )
        )
        until_record = json.loads(
            (self.dispatch / AT_UNTIL_ID / "record.json").read_text(
                encoding="utf-8",
            )
        )
        self.assertEqual(since_record["at"]["launched"], WINDOW_SINCE)
        self.assertEqual(until_record["at"]["launched"], WINDOW_UNTIL)

        plain = self._plain("report")
        data = self._report()
        ids = [row.get("id") for row in self._dispatch_block(data)["rows"]]
        self.assertIn(AT_SINCE_ID, ids)
        self.assertIn(CLAUDE_ID, ids)
        self.assertNotIn(
            AT_UNTIL_ID,
            ids,
            "a dispatch launched at until is inside [since, until)",
        )
        combined = plain + json.dumps(data)
        self.assertIn("43210", combined)
        self.assertNotIn(AT_UNTIL_ID, combined)
        self.assertNotIn("654321", combined)
        self.assertNotIn("12.34", combined)

    def test_a_dispatch_for_another_repository_is_not_charged(self):
        home_sha = self.w._git("rev-parse", "HEAD").stdout.strip()
        foreign = self.root / "foreign-repo"
        foreign_sha = self._init_other_repo(foreign)
        self._plant_dispatch(
            CLAUDE_ID,
            job="plan",
            harness="claude",
            stage="plan",
            launched="2026-08-22T10:00:00Z",
            spend=CLAUDE_SPEND,
            snapshot_root=str(self.repo),
            ref_sha=home_sha,
        )
        self._plant_dispatch(
            FOREIGN_ID,
            job="plan",
            harness="claude",
            stage="plan",
            launched="2026-08-22T10:07:00Z",
            spend=FOREIGN_SPEND,
            snapshot_root=str(foreign),
            ref_sha=foreign_sha,
        )
        home_record = json.loads(
            (self.dispatch / CLAUDE_ID / "record.json").read_text(
                encoding="utf-8",
            )
        )
        foreign_record = json.loads(
            (self.dispatch / FOREIGN_ID / "record.json").read_text(
                encoding="utf-8",
            )
        )
        self.assertEqual(home_record["snapshot"]["root"], str(self.repo))
        self.assertEqual(home_record["snapshot"]["ref_sha"], home_sha)
        self.assertEqual(foreign_record["snapshot"]["root"], str(foreign))
        self.assertEqual(foreign_record["snapshot"]["ref_sha"], foreign_sha)
        self.assertEqual(
            (self.dispatch / CLAUDE_ID).parent,
            (self.dispatch / FOREIGN_ID).parent,
            "the foreign dispatch must sit in the same jobs root",
        )

        plain = self._plain("report")
        data = self._report()
        ids = [row.get("id") for row in self._dispatch_block(data)["rows"]]
        self.assertIn(CLAUDE_ID, ids)
        self.assertNotIn(
            FOREIGN_ID,
            ids,
            "a dispatch for another repository was charged to this repo",
        )
        combined = plain + json.dumps(data)
        self.assertNotIn(FOREIGN_ID, combined)
        self.assertNotIn("777001", combined)
        self.assertNotIn("88.88", combined)

        foreign_plain = self._plain("report", repo=foreign)
        foreign_data = self._report(repo=foreign)
        foreign_ids = [
            row.get("id")
            for row in self._dispatch_block(foreign_data)["rows"]
        ]
        self.assertIn(FOREIGN_ID, foreign_ids)
        self.assertNotIn(CLAUDE_ID, foreign_ids)
        self.assertIn("777001", foreign_plain + json.dumps(foreign_data))

    def test_a_dispatched_stream_is_not_also_counted_as_a_session(self):
        """Scenario: a dispatched stream is not also counted as a session"""
        snapshot = self.root / "snap-cwd"
        snapshot.mkdir()
        self._plant_dispatch(
            CLAUDE_ID,
            job="plan",
            harness="claude",
            stage="plan",
            launched="2026-08-22T10:00:00Z",
            spend=CLAUDE_SPEND,
        )
        self._plant_claude(
            "snapshot-stream",
            [{
                "timestamp": "2026-08-22T10:00:00.000Z",
                "input": 777_001,
                "cache_read": 777_002,
                "output": 777_003,
            }],
            repo=snapshot,
        )
        self._plant_claude(
            "repo-session",
            [{
                "timestamp": "2026-08-22T10:00:00.000Z",
                "output": 40,
            }],
        )
        plain = self._plain("report")
        data = self._report()
        claude = self._harness_record(data, "claude")
        self._assert_key_number(claude, ("sessions",), 1)
        self._assert_key_number(claude, ("output", "out"), 40)
        combined = plain + json.dumps(data)
        self.assertNotIn("777001", combined)
        self.assertNotIn("777003", combined)
        waste = self._waste("--top", "20")
        kinds = {
            row.get("id"): row.get("kind")
            for row in self._ranked(waste)
            if isinstance(row, dict)
        }
        self.assertEqual(kinds.get(CLAUDE_ID), "dispatch")
        self.assertNotEqual(kinds.get("snapshot-stream"), "session")
        self.assertNotIn("snapshot-stream", kinds)
        self.assertEqual(kinds.get("repo-session"), "session")

    def test_spend_comes_from_session_spend_never_from_the_stream(self):
        decoy = '{"output_tokens": 999999, "total_tokens": 999999}\n'
        self._plant_dispatch(
            GROK_ID,
            job="plan",
            harness="grok",
            stage="plan",
            launched="2026-08-22T10:02:00Z",
            spend=GROK_SPEND,
            raw_out=decoy,
        )
        self.assertIn("999999", (self.dispatch / GROK_ID / "raw.out").read_text())
        plain = self._plain("report")
        data = self._report()
        row = self._row_by_id(self._dispatch_block(data)["rows"], GROK_ID)
        self._assert_key_number(row, ("total",), 100)
        combined = plain + json.dumps(data)
        self.assertNotIn("999999", combined)
        self._assert_no_leak(combined)

    def test_unresolved_spend_is_unrecorded_never_zero(self):
        self._plant_dispatch(
            UNRESOLVED_ID,
            job="plan",
            harness="codex",
            stage="plan",
            launched="2026-08-22T10:04:00Z",
            spend={"unresolved": "no stream was found"},
        )
        plain = self._plain("report")
        data = self._report()
        row = self._row_by_id(
            self._dispatch_block(data)["rows"], UNRESOLVED_ID,
        )
        rendered = json.dumps(row).lower()
        self.assertIn("unrecorded", rendered)
        self.assertIn("no stream", rendered)
        self.assertNotRegex(
            json.dumps(row),
            r'"total"\s*:\s*0\b',
        )
        self.assertIn("tokens=unrecorded", plain)
        self.assertIn("no stream", plain.lower())
        line = self._line_for_harness(plain, "codex")
        self.assertIn("counted=0/1", line)

    def test_malformed_launched_stamp_is_a_gap_in_the_denominator(self):
        self._plant_dispatch(
            MALFORMED_ID,
            job="plan",
            harness="claude",
            stage="plan",
            launched="not-a-stamp",
            spend=CLAUDE_SPEND,
        )
        self._plant_dispatch(
            CLAUDE_ID,
            job="plan",
            harness="claude",
            stage="plan",
            launched="2026-08-22T10:00:00Z",
            spend=CLAUDE_SPEND,
        )
        planted = json.loads(
            (self.dispatch / MALFORMED_ID / "record.json").read_text(
                encoding="utf-8",
            )
        )
        self.assertEqual(planted["at"]["launched"], "not-a-stamp")
        self.assertEqual(planted["session"]["spend"]["total"], 2120)
        self.assertEqual(planted["session"]["spend"]["cost_usd"], 5.16)
        proc = self._run_worth("report", *window_args())
        self.assertEqual(proc.exit, 0, proc.stderr)
        data = self._report()
        block = self._dispatch_block(data)
        ids = [row.get("id") for row in block["rows"]]
        self.assertIn(CLAUDE_ID, ids)
        self.assertIn(MALFORMED_ID, ids)
        claude = block["claude"]
        self.assertIn("counted=1/2", json.dumps(claude))
        self.assertIn(
            "counted=1/2",
            self._line_for_harness(proc.stdout, "claude"),
        )
        gap = self._row_by_id(block["rows"], MALFORMED_ID)
        rendered = json.dumps(gap).lower()
        self.assertIn("unrecorded", rendered)
        gap_text = json.dumps(gap)
        self.assertNotIn("2120", gap_text)
        self.assertNotIn("5.16", gap_text)
        self.assertNotRegex(gap_text, r'"total"\s*:\s*2120\b')
        self.assertNotRegex(gap_text, r'"cost_usd"\s*:\s*5\.16\b')

    def test_tracked_record_fallback_when_the_job_has_no_record_json(self):
        job_dir, _record = self._plant_dispatch(
            TRACKED_ID,
            job="plan",
            harness="grok",
            stage="plan",
            launched="2026-08-22T10:06:00Z",
            spend=GROK_SPEND,
            write_job_record=False,
            tracked=True,
        )
        self.assertFalse((job_dir / "record.json").exists())
        self.assertTrue(
            (self.repo / ".dev" / "records" / "dispatches"
             / f"{TRACKED_ID}.json").is_file(),
        )
        data = self._report()
        row = self._row_by_id(self._dispatch_block(data)["rows"], TRACKED_ID)
        self._assert_key_number(row, ("total",), 100)
        self._assert_key_number(row, ("cost_usd_ticks", "costUsdTicks"), 9)

    def test_each_row_is_keyed_by_record_id_and_job(self):
        self._plant_dispatch(
            CLAUDE_ID,
            job="plan",
            harness="claude",
            stage="plan",
            launched="2026-08-22T10:00:00Z",
            spend=CLAUDE_SPEND,
            model_ran="claude-fable-5",
            unit="WO-7",
            branch="lane/token-meter",
        )
        planted = json.loads(
            (self.dispatch / CLAUDE_ID / "record.json").read_text(
                encoding="utf-8",
            )
        )
        self.assertEqual(planted["unit"], "WO-7")
        self.assertEqual(planted["lineage"]["branch"], "lane/token-meter")
        self.assertEqual(planted["model"]["ran"], "claude-fable-5")
        data = self._report()
        row = self._row_by_id(self._dispatch_block(data)["rows"], CLAUDE_ID)
        for key in (
            "id", "job", "stage", "harness", "unit", "status",
        ):
            self.assertIn(key, row)
        self.assertEqual(row["id"], CLAUDE_ID)
        self.assertEqual(row["job"], "plan")
        self.assertEqual(row["stage"], "plan")
        self.assertEqual(row["harness"], "claude")
        self.assertEqual(row["unit"], "WO-7")
        self.assertEqual(row["status"], "closed")
        self.assertEqual(
            self._field_at(row, "lineage", "branch"),
            "lane/token-meter",
        )
        self.assertEqual(
            self._field_at(row, "model", "ran"),
            "claude-fable-5",
        )

    def test_waste_ranks_dispatches_with_sessions_by_window_total(self):
        self._plant_claude("session-leader", [{
            "timestamp": "2026-08-22T10:00:00.000Z",
            "output": 3000,
        }])
        self._plant_claude("session-tail", [{
            "timestamp": "2026-08-22T10:00:00.000Z",
            "output": 50,
        }])
        self._plant_dispatch(
            CLAUDE_ID,
            job="plan",
            harness="claude",
            stage="plan",
            launched="2026-08-22T10:00:00Z",
            spend=CLAUDE_SPEND,
        )
        data = self._waste("--top", "10")
        rows = self._ranked(data)
        ids = []
        for row in rows:
            if row.get("kind") == "dispatch":
                ids.append(row.get("id"))
            else:
                found = None
                for path, value in worth_contract.walk_leaves(row):
                    if (
                        path
                        and worth_contract.canonical_key(path[-1])
                        in {"session", "sessionid", "id"}
                        and isinstance(value, str)
                    ):
                        found = value
                        break
                ids.append(found)
        self.assertEqual(
            ids[:3],
            ["session-leader", CLAUDE_ID, "session-tail"],
            f"waste did not rank dispatches with sessions: {ids}",
        )
        dispatch = self._row_by_id(
            [row for row in rows if row.get("kind") == "dispatch"],
            CLAUDE_ID,
        )
        self.assertEqual(dispatch["kind"], "dispatch")
        self.assertEqual(dispatch.get("job"), "plan")
        plain = self._plain("waste", "--top", "10")
        self.assertIn(f"record={CLAUDE_ID}", plain)
        self.assertIn("job=plan", plain)
        self.assertRegex(plain, r"\bdispatches\s*=\s*1\b")
        leader_at = plain.find("session-leader")
        dispatch_at = plain.find(CLAUDE_ID)
        tail_at = plain.find("session-tail")
        self.assertTrue(leader_at >= 0 and dispatch_at >= 0 and tail_at >= 0)
        self.assertLess(leader_at, dispatch_at)
        self.assertLess(dispatch_at, tail_at)

    def test_waste_cache_churn_on_a_dispatch_names_cached_not_cached_read(self):
        self._plant_dispatch(
            CLAUDE_ID,
            job="plan",
            harness="claude",
            stage="plan",
            launched="2026-08-22T10:00:00Z",
            spend=CLAUDE_SPEND,
        )
        data = self._waste("--top", "10")
        plain = self._plain("waste", "--top", "10")
        signals = self._signals_of_kind(data, "cache-churn")
        self.assertEqual(len(signals), 1, data["signals"])
        rendered = json.dumps(signals[0])
        self.assertIn(CLAUDE_ID, rendered)
        self.assertIn("2010", rendered)
        self.assertIn("10", rendered)
        self.assertIn("cached=", plain)
        self.assertNotIn("cached_read=", plain)
        self.assertIn("cache-churn", plain)

    def test_waste_does_not_emit_heavy_turn_for_a_dispatch_row(self):
        self._plant_dispatch(
            CLAUDE_ID,
            job="plan",
            harness="claude",
            stage="plan",
            launched="2026-08-22T10:00:00Z",
            spend=CLAUDE_SPEND,
        )
        data = self._waste("--top", "10")
        heavy = self._signals_of_kind(data, "heavy-turn")
        self.assertEqual(heavy, [], data["signals"])
        row = self._row_by_id(self._dispatch_rows(data), CLAUDE_ID)
        heavy_field = row.get("heavy")
        self.assertIsInstance(heavy_field, dict, row)
        self.assertEqual(
            heavy_field.get("declared_absent"),
            "record carries no per-turn usage",
        )
        plain = self._plain("waste", "--top", "10")
        self.assertNotIn("heavy-turn", plain)

    def test_json_and_plain_carry_the_same_dispatch_figures(self):
        self._plant_dispatch(
            CLAUDE_ID,
            job="plan",
            harness="claude",
            stage="plan",
            launched="2026-08-22T10:00:00Z",
            spend=CLAUDE_SPEND,
        )
        self._plant_dispatch(
            CODEX_ID,
            job="check-tests",
            harness="codex",
            stage="check-tests",
            launched="2026-08-22T10:01:00Z",
            spend=CODEX_SPEND,
        )
        self._plant_dispatch(
            GROK_ID,
            job="adversarial-review",
            harness="grok",
            stage="review",
            launched="2026-08-22T10:02:00Z",
            spend=GROK_SPEND,
        )
        for verb, extra in (
            ("report", ()),
            ("waste", ("--top", "10")),
        ):
            with self.subTest(verb=verb):
                plain = self._plain(verb, *extra)
                data = self._json_worth(
                    verb, *window_args(), *extra, now=WINDOW_NOW,
                )
                if verb == "report":
                    self.assertIn("dispatches", data)
                else:
                    kinds = {row.get("kind") for row in data["sessions"]}
                    self.assertIn("dispatch", kinds)
                self._assert_plain_json_parity(plain, data)
                for job_id in (CLAUDE_ID, CODEX_ID, GROK_ID):
                    self.assertIn(job_id, plain)
                    self.assertIn(job_id, json.dumps(data))

    def test_dispatch_dir_flag_overrides_the_jobs_root(self):
        other = self.root / "other-jobs"
        other.mkdir()
        self._plant_dispatch(
            CLAUDE_ID,
            job="plan",
            harness="claude",
            stage="plan",
            launched="2026-08-22T10:00:00Z",
            spend=CLAUDE_SPEND,
            root=other,
        )
        self._plant_dispatch(
            GROK_ID,
            job="plan",
            harness="grok",
            stage="plan",
            launched="2026-08-22T10:02:00Z",
            spend=GROK_SPEND,
            root=self.dispatch,
        )
        proc = self._run_worth(
            "report",
            *window_args(),
            "--format",
            "json",
            dispatch_dir=other,
            extra_env={"DISPATCH_JOBS": str(self.dispatch)},
        )
        self.assertEqual(proc.exit, 0, proc.stderr)
        data = json.loads(proc.stdout)
        ids = [
            row.get("id")
            for row in self._dispatch_block(data)["rows"]
        ]
        self.assertIn(CLAUDE_ID, ids)
        self.assertNotIn(GROK_ID, ids)

    def test_jobs_root_falls_back_from_dispatch_jobs_to_xdg_to_home(self):
        xdg = self.root / "xdg-state"
        xdg_jobs = xdg / "agentscient" / "dispatch"
        home_jobs = self.home / ".local" / "state" / "agentscient" / "dispatch"
        cases = (
            (
                "DISPATCH_JOBS",
                {"DISPATCH_JOBS": str(self.dispatch)},
                self.dispatch,
            ),
            (
                "XDG_STATE_HOME",
                {"XDG_STATE_HOME": str(xdg)},
                xdg_jobs,
            ),
            (
                "HOME",
                {},
                home_jobs,
            ),
        )
        for label, extra_env, root in cases:
            with self.subTest(root=label):
                job_id = CLAUDE_ID
                if root.exists():
                    for child in root.iterdir():
                        if child.is_dir():
                            for path in child.rglob("*"):
                                if path.is_file():
                                    path.unlink()
                self._plant_dispatch(
                    job_id,
                    job="plan",
                    harness="claude",
                    stage="plan",
                    launched="2026-08-22T10:00:00Z",
                    spend=CLAUDE_SPEND,
                    root=root,
                )
                proc = self._run_worth(
                    "report",
                    *window_args(),
                    "--format",
                    "json",
                    dispatch_dir=None,
                    extra_env=extra_env,
                )
                self.assertEqual(proc.exit, 0, proc.stderr)
                data = json.loads(proc.stdout)
                ids = [
                    row.get("id")
                    for row in self._dispatch_block(data)["rows"]
                ]
                self.assertEqual(ids, [job_id], f"{label} root was not read")

    def test_lane_name_from_conf_selects_the_jobs_root(self):
        conf = self.root / "lane.conf"
        text = (
            "LANE_NAME='lanex'\n"
            "TRUNK_BRANCH='trunkx'\n"
            "RELEASE_BRANCH='relx'\n"
            "REPO_SLUG='org/projx'\n"
            "CHECK_NAMES='checkx'\n"
            "WF_AGENT_DEFAULT='Conf Agent <conf@example.invalid>'\n"
            "LANE_AUTHOR='Lane Owner <lane-owner@example.invalid>'\n"
            "MODEL_GROK='model-grok'\n"
            "MODEL_CODEX='model-codex'\n"
            "MODEL_CLAUDE_READ='model-claude-read'\n"
            "MODEL_CLAUDE_PLAN='model-claude-plan'\n"
            "EFFORT_GROK='high'\n"
            "EFFORT_CODEX='high'\n"
            "EFFORT_CLAUDE='high'\n"
            "WALL_DEFAULT='900'\n"
            "SCOPE_CAP='1024'\n"
        )
        self.world.plant_text(conf, text)
        self.assertEqual(conf.read_text(encoding="utf-8"), text)
        lanex = self.home / ".local" / "state" / "lanex" / "dispatch"
        decoy = self.home / ".local" / "state" / "agentscient" / "dispatch"
        self._plant_dispatch(
            CLAUDE_ID,
            job="plan",
            harness="claude",
            stage="plan",
            launched="2026-08-22T10:00:00Z",
            spend=CLAUDE_SPEND,
            root=lanex,
        )
        self._plant_dispatch(
            GROK_ID,
            job="plan",
            harness="grok",
            stage="plan",
            launched="2026-08-22T10:02:00Z",
            spend=GROK_SPEND,
            root=decoy,
        )
        self.assertTrue((lanex / CLAUDE_ID).is_dir())
        self.assertTrue((decoy / GROK_ID).is_dir())
        proc = self._run_worth(
            "report",
            *window_args(),
            "--format",
            "json",
            dispatch_dir=None,
            extra_env={"LANE_CONF": str(conf)},
        )
        self.assertEqual(proc.exit, 0, proc.stderr)
        data = json.loads(proc.stdout)
        ids = [
            row.get("id")
            for row in self._dispatch_block(data)["rows"]
        ]
        self.assertIn(CLAUDE_ID, ids)
        self.assertNotIn(GROK_ID, ids)


if __name__ == "__main__":
    unittest.main()
