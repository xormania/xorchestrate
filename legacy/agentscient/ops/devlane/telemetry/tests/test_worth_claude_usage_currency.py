"""PR 66 item 3: retained Claude usage currencies must be finite numbers.

Contract: ops/conductor/pr66-fixes.md item 3 and ops/process/worth.md
(malformed retained usage is an explicit gap). Reuses the worth
fixture helpers. Bool is not a currency. Invalid retained usage is
the malformed-usage gap, not a measured row or partial totals.
Zero, valid positives, missing fields, and last-write dedup stay.
"""

from __future__ import annotations

import json
import math
import re
import sys
import unittest
from pathlib import Path

from ops.testkit import World, run

WORTH = Path(__file__).resolve().parents[1] / "worth.py"
NOW = "2026-08-22T12:00:00.000Z"
INSIDE = "2026-08-22T10:00:00.000Z"
VALID_OUT = 41
VALID_IN = 7
NEGATIVE_OUT = -17
PARTIAL_IN = 100
TIMEOUT = 60


def canonical_key(value):
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def walk_leaves(value, path=()):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from walk_leaves(child, path + (str(key),))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk_leaves(child, path + (str(index),))
    else:
        yield path, value


class ClaudeUsageCurrency(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.world = World(prefix="worth-currency-")
        self.addCleanup(self.world.close)
        self.world.require("git")
        # Kit gap: worth.py locates conductor siblings from Path(__file__).
        self.worth_py = WORTH
        self.repo = self.world.root / "repo"
        self.repo.mkdir()
        self.claude = self.world.root / "claude"
        self.codex = self.world.root / "codex"
        self.grok = self.world.root / "grok"
        self.world.git("init", "-q", "-b", "dev", cwd=self.repo)
        self.world.plant_text(self.repo / "README.md", "throwaway worth fixture\n")
        extra = {
            "GIT_AUTHOR_DATE": "2026-08-20T10:00:00+00:00",
            "GIT_COMMITTER_DATE": "2026-08-20T10:00:00+00:00",
        }
        env = self.world.env(extra=extra)
        run(["git", "add", "-A"], cwd=self.repo, env=env, timeout=TIMEOUT,
            expect=0)
        run(["git", "commit", "-q", "-m", "initial fixture"], cwd=self.repo,
            env=env, timeout=TIMEOUT, expect=0)

    def _write_jsonl(self, path, entries):
        content = "".join(
            json.dumps(entry, sort_keys=True) + "\n" for entry in entries
        )
        planted_path = self.world.plant_text(path, content)
        planted = [
            json.loads(raw)
            for raw in planted_path.read_text(encoding="utf-8").splitlines()
            if raw.strip()
        ]
        self.assertEqual(len(planted), len(entries))
        self.assertEqual(planted, entries)
        return planted_path

    def _slug(self, repo=None):
        repo = str(repo or self.repo)
        return "-" + "-".join(repo.strip("/").split("/"))

    def _assert_key_number(self, value, aliases, expected):
        wanted = {canonical_key(alias) for alias in aliases}
        matches = [
            leaf for path, leaf in walk_leaves(value)
            if path
            and canonical_key(path[-1]) in wanted
            and isinstance(leaf, (int, float))
            and not isinstance(leaf, bool)
        ]
        self.assertIn(
            expected,
            matches,
            f"expected {aliases}={expected}; observed {matches} in {value}",
        )

    def _harness_record(self, data, harness):
        container = data["cost"]
        wanted = canonical_key(harness)

        def find(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if canonical_key(key) == wanted:
                        return child
                if canonical_key(value.get("harness", "")) == wanted:
                    return value
                for child in value.values():
                    found = find(child)
                    if found is not None:
                        return found
            elif isinstance(value, list):
                for child in value:
                    found = find(child)
                    if found is not None:
                        return found
            return None

        record = find(container)
        self.assertIsNotNone(record, f"no cost record for {harness}: {container}")
        return record

    def _run_worth(self, verb, *args):
        cmd = [
            sys.executable,
            str(self.worth_py),
            verb,
            *args,
            "--now",
            NOW,
            "--repo",
            str(self.repo),
            "--claude-dir",
            str(self.claude),
            "--codex-dir",
            str(self.codex),
            "--grok-dir",
            str(self.grok),
        ]
        self.assertEqual(cmd.count("--now"), 1)
        return run(
            cmd,
            cwd=self.world.cwd,
            env=self.world.env(extra={"TZ": "Etc/GMT+11"}),
            timeout=TIMEOUT,
        )

    def _json_worth(self, verb, *args):
        proc = self._run_worth(verb, *args, "--format", "json")
        self.assertEqual(proc.exit, 0, proc.stderr)
        try:
            value = json.loads(proc.stdout)
        except ValueError as exc:
            self.fail(f"invalid JSON: {exc}\nstdout:\n{proc.stdout}")
        self.assertIsInstance(value, dict)
        return value

    def _assistant(self, session_id, mid, usage, timestamp=INSIDE):
        return {
            "type": "assistant",
            "timestamp": timestamp,
            "cwd": str(self.repo),
            "sessionId": session_id,
            "message": {
                "id": mid,
                "model": "claude-fable-5",
                "usage": usage,
                "content": [],
            },
        }

    def _valid_usage(self, output=VALID_OUT, inp=VALID_IN):
        return {
            "input_tokens": inp,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "output_tokens": output,
        }

    def _plant(self, session_id, rows):
        path = self.claude / self._slug() / f"{session_id}.jsonl"
        planted = self._write_jsonl(path, rows)
        self.assertEqual(
            [
                json.loads(line)
                for line in planted.read_text(encoding="utf-8").splitlines()
            ],
            rows,
            "transcript plant did not land intact",
        )
        return planted

    def _plant_raw(self, session_id, text):
        path = self.claude / self._slug() / f"{session_id}.jsonl"
        planted = self.world.plant_text(path, text)
        self.assertEqual(planted.read_text(encoding="utf-8"), text)
        return planted

    def _plant_valid_control(self, session_id="valid-control"):
        path = self._plant(
            session_id,
            [
                self._assistant(
                    session_id,
                    f"{session_id}-m1",
                    self._valid_usage(),
                )
            ],
        )
        planted = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(planted["message"]["usage"]["output_tokens"], VALID_OUT)
        self.assertEqual(planted["message"]["usage"]["input_tokens"], VALID_IN)
        return session_id

    def _report(self):
        plain = self._run_worth("report")
        self.assertEqual(plain.exit, 0, plain.stderr)
        data = self._json_worth("report")
        return plain.stdout, data

    def _waste(self, *extra):
        plain = self._run_worth("waste", "--top", "20", *extra)
        self.assertEqual(plain.exit, 0, plain.stderr)
        data = self._json_worth("waste", "--top", "20", *extra)
        return plain.stdout, data

    def _claude_cost(self, data):
        return self._harness_record(data, "claude")

    def _gap_rows(self, data, session_id):
        sessions = data["sessions"]
        self.assertIsInstance(sessions, list)
        return [
            row
            for row in sessions
            if isinstance(row, dict)
            and row.get("kind") == "gap"
            and session_id in {row.get("id"), row.get("session"), row.get("session_id")}
        ]

    def _measured_rows(self, data, session_id):
        sessions = data["sessions"]
        self.assertIsInstance(sessions, list)
        return [
            row
            for row in sessions
            if isinstance(row, dict)
            and row.get("kind") != "gap"
            and session_id in {row.get("id"), row.get("session"), row.get("session_id")}
        ]

    def _assert_valid_control_measured(self, report, waste, session_id="valid-control"):
        cost = self._claude_cost(report)
        self._assert_key_number(cost, ("output", "out"), VALID_OUT)
        self._assert_key_number(cost, ("input", "in"), VALID_IN)
        rendered = json.dumps(cost)
        self.assertIn(str(VALID_OUT), rendered)
        measured = self._measured_rows(waste, session_id)
        self.assertEqual(len(measured), 1, waste["sessions"])
        self.assertNotEqual(measured[0].get("kind"), "gap")

    # Every key of a waste row or signal that carries a measurement. The
    # rest of the row (id, session, harness, kind, gap_kind, repo, window)
    # and the stamp (head, branch, since, until, now) name things; a
    # planted number inside one of those is LD-21, not a leaked total.
    _MEASURED_KEYS = (
        "total", "out", "cached", "input", "output", "messages", "runs",
        "turns", "avg_context", "refusals", "tokens", "cost_usd",
        "cost_usd_ticks", "cost",
    )

    def _measured_fields(self, waste):
        """The waste report's measured values, keyed by row and field.

        LD-21: the serialized whole also carries the fixture's temporary
        repo path and the head sha, so a planted number could match a
        directory name (``worth-contract-m041w_6s`` carried 41 in CI run
        34644878635; ``claude-1006`` carries 100). Only these values are
        the report's answer.
        """
        fields = {}
        rows = waste["sessions"]
        self.assertIsInstance(rows, list)
        for row in rows:
            self.assertIsInstance(row, dict)
            ident = row.get("id", row.get("session", row.get("record")))
            for key in self._MEASURED_KEYS:
                if key in row:
                    fields[f"{ident}.{key}"] = row[key]
        signals = waste.get("signals", [])
        self.assertIsInstance(signals, list)
        for n, signal in enumerate(signals):
            for key in ("cached", "out"):
                if key in signal:
                    fields[f"signal[{n}].{key}"] = signal[key]
        return fields

    def _assert_invalid_is_gap_not_partial(self, report, waste, session_id, forbidden):
        cost = self._claude_cost(report)
        rendered = json.dumps(cost).lower()
        measured = self._measured_fields(waste)
        for token in forbidden:
            self.assertNotIn(str(token).lower(), rendered)
            for name, value in measured.items():
                self.assertNotIn(
                    str(token).lower(),
                    json.dumps(value).lower(),
                    f"{name}={value!r} carries the planted {token}",
                )
        gaps = self._gap_rows(waste, session_id)
        self.assertEqual(len(gaps), 1, waste["sessions"])
        gap = gaps[0]
        for key in ("turns", "avg_context", "refusals", "tokens"):
            if key in gap:
                self.assertEqual(gap[key], "unrecorded")
        measured = self._measured_rows(waste, session_id)
        self.assertEqual(measured, [], waste["sessions"])

    def test_negative_output_is_a_gap_and_does_not_shift_valid_totals(self):
        valid = self._plant_valid_control()
        path = self._plant(
            "neg-sess",
            [
                self._assistant(
                    "neg-sess",
                    "neg-m1",
                    self._valid_usage(output=NEGATIVE_OUT, inp=PARTIAL_IN),
                )
            ],
        )
        text = path.read_text(encoding="utf-8")
        self.assertIn(str(NEGATIVE_OUT), text)
        self.assertIn(str(PARTIAL_IN), text)
        _plain, report = self._report()
        _waste_plain, waste = self._waste()
        self._assert_valid_control_measured(report, waste, valid)
        self._assert_invalid_is_gap_not_partial(
            report,
            waste,
            "neg-sess",
            forbidden=(NEGATIVE_OUT, PARTIAL_IN),
        )
        cost = self._claude_cost(report)
        self._assert_key_number(cost, ("output", "out"), VALID_OUT)
        self.assertNotEqual(cost.get("out"), VALID_OUT + NEGATIVE_OUT)

    def test_nonfinite_output_is_a_gap_not_a_partial_total(self):
        valid = self._plant_valid_control()
        for session_id, value, token in (
            ("nan-sess", float("nan"), "NaN"),
            ("inf-sess", float("inf"), "Infinity"),
            ("ninf-sess", float("-inf"), "-Infinity"),
        ):
            with self.subTest(session_id=session_id):
                row = self._assistant(
                    session_id,
                    f"{session_id}-m1",
                    self._valid_usage(output=value, inp=PARTIAL_IN),
                )
                raw = json.dumps(row, allow_nan=True) + "\n"
                path = self._plant_raw(session_id, raw)
                self.assertIn(token, path.read_text(encoding="utf-8"))
                self.assertTrue(math.isnan(value) or math.isinf(value))
        _plain, report = self._report()
        _waste_plain, waste = self._waste()
        self._assert_valid_control_measured(report, waste, valid)
        cost = self._claude_cost(report)
        self._assert_key_number(cost, ("output", "out"), VALID_OUT)
        rendered = json.dumps(cost).lower()
        self.assertNotIn("nan", rendered)
        self.assertNotIn("inf", rendered)
        self.assertNotIn(str(PARTIAL_IN), json.dumps(cost))
        for session_id in ("nan-sess", "inf-sess", "ninf-sess"):
            with self.subTest(gap=session_id):
                self._assert_invalid_is_gap_not_partial(
                    report,
                    waste,
                    session_id,
                    forbidden=(PARTIAL_IN,),
                )

    def test_bool_output_is_not_a_currency_and_gaps(self):
        valid = self._plant_valid_control()
        path = self._plant(
            "bool-sess",
            [
                self._assistant(
                    "bool-sess",
                    "bool-m1",
                    self._valid_usage(output=True, inp=False),
                )
            ],
        )
        planted = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        usage = planted["message"]["usage"]
        self.assertIs(usage["output_tokens"], True)
        self.assertIs(usage["input_tokens"], False)
        self.assertEqual(type(usage["output_tokens"]), bool)
        self.assertEqual(type(usage["input_tokens"]), bool)
        _plain, report = self._report()
        _waste_plain, waste = self._waste()
        self._assert_valid_control_measured(report, waste, valid)
        self._assert_invalid_is_gap_not_partial(
            report,
            waste,
            "bool-sess",
            forbidden=(),
        )
        cost = self._claude_cost(report)
        self._assert_key_number(cost, ("output", "out"), VALID_OUT)
        self._assert_key_number(cost, ("input", "in"), VALID_IN)
        self.assertNotEqual(cost.get("out"), VALID_OUT + 1)

    def test_zero_output_stays_a_measured_row(self):
        path = self._plant(
            "zero-sess",
            [
                self._assistant(
                    "zero-sess",
                    "zero-m1",
                    self._valid_usage(output=0, inp=0),
                )
            ],
        )
        self.assertIn('"output_tokens": 0', path.read_text(encoding="utf-8"))
        _plain, report = self._report()
        _waste_plain, waste = self._waste()
        cost = self._claude_cost(report)
        self._assert_key_number(cost, ("output", "out"), 0)
        self._assert_key_number(cost, ("sessions",), 1)
        measured = self._measured_rows(waste, "zero-sess")
        self.assertEqual(len(measured), 1, waste["sessions"])
        self.assertEqual(self._gap_rows(waste, "zero-sess"), [])

    def test_missing_usage_fields_stay_unrecorded_not_zero(self):
        valid = self._plant_valid_control()
        path = self._plant(
            "missing-sess",
            [self._assistant("missing-sess", "missing-m1", {})],
        )
        self.assertIn('"usage": {}', path.read_text(encoding="utf-8"))
        _plain, report = self._report()
        cost = self._claude_cost(report)
        self._assert_key_number(cost, ("output", "out"), VALID_OUT)
        self._assert_key_number(cost, ("input", "in"), VALID_IN)
        _waste_plain, waste = self._waste()
        self._assert_valid_control_measured(report, waste, valid)
        self.assertEqual(self._measured_rows(waste, "missing-sess"), [])

    def test_last_write_invalid_gaps_and_does_not_keep_the_first_total(self):
        rows = [
            self._assistant("dedup-bad", "same-mid", self._valid_usage()),
            self._assistant(
                "dedup-bad",
                "same-mid",
                self._valid_usage(output=NEGATIVE_OUT, inp=PARTIAL_IN),
            ),
        ]
        path = self._plant("dedup-bad", rows)
        planted = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(planted[0]["message"]["id"], planted[1]["message"]["id"])
        self.assertEqual(planted[1]["message"]["usage"]["output_tokens"], NEGATIVE_OUT)
        _plain, report = self._report()
        _waste_plain, waste = self._waste()
        cost = self._claude_cost(report)
        rendered = json.dumps(cost)
        self.assertNotIn(str(VALID_OUT), rendered)
        self.assertNotIn(str(NEGATIVE_OUT), rendered)
        self.assertNotIn(str(PARTIAL_IN), rendered)
        self._assert_invalid_is_gap_not_partial(
            report,
            waste,
            "dedup-bad",
            forbidden=(NEGATIVE_OUT, PARTIAL_IN, VALID_OUT),
        )

    def test_last_write_valid_keeps_the_later_total(self):
        rows = [
            self._assistant(
                "dedup-good",
                "same-mid",
                self._valid_usage(output=NEGATIVE_OUT, inp=PARTIAL_IN),
            ),
            self._assistant("dedup-good", "same-mid", self._valid_usage()),
        ]
        path = self._plant("dedup-good", rows)
        planted = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(planted[0]["message"]["id"], planted[1]["message"]["id"])
        self.assertEqual(planted[1]["message"]["usage"]["output_tokens"], VALID_OUT)
        _plain, report = self._report()
        _waste_plain, waste = self._waste()
        cost = self._claude_cost(report)
        self._assert_key_number(cost, ("output", "out"), VALID_OUT)
        self._assert_key_number(cost, ("input", "in"), VALID_IN)
        self.assertNotIn(str(NEGATIVE_OUT), json.dumps(cost))
        measured = self._measured_rows(waste, "dedup-good")
        self.assertEqual(len(measured), 1, waste["sessions"])
        self.assertEqual(self._gap_rows(waste, "dedup-good"), [])


if __name__ == "__main__":
    unittest.main()
