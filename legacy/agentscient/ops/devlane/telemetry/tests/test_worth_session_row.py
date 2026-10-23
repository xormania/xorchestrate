"""Item 48/R6: waste Claude session rows carry session-budget fields.

turns = assistant usage rows; avg_context = mean of
input+cache_creation+cache_read; refusals = tool-result deny markers.
An unreadable transcript prints unrecorded for those three, never 0.
"""

from __future__ import annotations

import json
import re
import sys
import unittest

import test_worth as worth_contract

from ops.testkit import World, plant_text, run

NOW = "2026-08-22T12:00:00.000Z"
SINCE = "2026-08-22T09:00:00.000Z"
UNTIL = "2026-08-22T12:00:00.000Z"
WINDOW = ("--since", SINCE, "--until", UNTIL)

DENY_MARKER = (
    "PreToolUse:Bash hook returned a deny decision: "
    "[matrix:hand-write] hand-write: expected code changed through "
    "the ladder; found Write on AGENTS.md; needed freeze"
)
COMMAND_ERROR = "Bash failed with exit code 1: command not found: nosuch"
WIRE_REFUSAL = (
    "[engine:claim-unmeasured] claim-unmeasured: expected every paragraph "
    "that claims to carry its reading; found a commit message with none; "
    "needed the reading beside the claim"
)
QUOTED_REFUSAL = (
    "FAIL: test_refuses_without_identity\n"
    "AssertionError: 'identity: expected WF_AGENT; found unset; "
    "needed set it' != ''"
)


def _plain_field(plain, name, value):
    return re.search(
        rf"\b{re.escape(name)}\s*[=:]\s*{re.escape(str(value))}\b",
        plain,
    )


class WasteSessionBudgetRow(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.w = worth_contract.WorthContractTests(
            "test_unusable_arguments_exit_two_and_explain_the_reason"
        )
        self.w.setUp()
        self.addCleanup(self.w.doCleanups)
        self.world = World()
        self.addCleanup(self.world.close)
        self._write_jsonl = self.w._write_jsonl
        self._slug = self.w._slug

    def _assistant(self, session_id, mid, timestamp, usage, content=None, cwd=None):
        return {
            "type": "assistant",
            "timestamp": timestamp,
            "cwd": str(cwd or self.w.repo),
            "sessionId": session_id,
            "message": {
                "id": mid,
                "model": "claude-fable-5",
                "usage": usage,
                "content": content or [],
            },
        }

    def _assistant_without_usage(self, session_id, mid, timestamp):
        return {
            "type": "assistant",
            "timestamp": timestamp,
            "cwd": str(self.w.repo),
            "sessionId": session_id,
            "message": {
                "id": mid,
                "model": "claude-fable-5",
                "content": [{"type": "text", "text": "no usage on this row"}],
            },
        }

    def _tool_result(self, session_id, timestamp, text, is_error):
        return {
            "type": "user",
            "timestamp": timestamp,
            "cwd": str(self.w.repo),
            "sessionId": session_id,
            "message": {
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "is_error": is_error,
                    "content": text,
                }],
            },
        }

    def _plant_transcript(self, session_id, rows):
        path = self.w.claude / self._slug() / f"{session_id}.jsonl"
        planted = self._write_jsonl(path, rows)
        self.assertEqual(
            [json.loads(line) for line in planted.read_text(encoding="utf-8").splitlines()],
            rows,
            "transcript plant did not land intact",
        )
        return planted

    def _budget_rows(self, session_id="budget-sess"):
        # Turn contexts: (10+100+200)=310 and (20+0+80)=100; mean 205.
        # Output 9999/1 must not enter avg_context. The no-usage assistant
        # is not a turn. The command-error tool_result is not a refusal.
        return session_id, [
            self._assistant(
                session_id,
                "m1",
                "2026-08-22T10:00:00.000Z",
                {
                    "input_tokens": 10,
                    "cache_creation_input_tokens": 100,
                    "cache_read_input_tokens": 200,
                    "output_tokens": 9999,
                },
            ),
            self._tool_result(
                session_id,
                "2026-08-22T10:00:05.000Z",
                COMMAND_ERROR,
                True,
            ),
            self._assistant_without_usage(
                session_id,
                "m-no-usage",
                "2026-08-22T10:00:10.000Z",
            ),
            self._tool_result(
                session_id,
                "2026-08-22T10:00:15.000Z",
                DENY_MARKER,
                True,
            ),
            self._assistant(
                session_id,
                "m2",
                "2026-08-22T10:01:00.000Z",
                {
                    "input_tokens": 20,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 80,
                    "output_tokens": 1,
                },
            ),
        ]

    def _run_waste(self, *extra, repo=None, cwd=None):
        cmd = [
            sys.executable,
            str(worth_contract.WORTH),
            "waste",
            *WINDOW,
            *extra,
            "--now",
            NOW,
            "--repo",
            str(repo or self.w.repo),
            "--claude-dir",
            str(self.w.claude),
            "--codex-dir",
            str(self.w.codex),
            "--grok-dir",
            str(self.w.grok),
        ]
        return run(
            cmd,
            cwd=cwd or self.w.root,
            env=self.world.env(extra={"TZ": "Etc/GMT+11"}),
            timeout=30,
        )

    def _waste(self, *extra, repo=None, cwd=None):
        plain = self._run_waste(*extra, repo=repo, cwd=cwd)
        self.assertEqual(plain.exit, 0, plain.stderr)
        data_proc = self._run_waste(*extra, "--format", "json", repo=repo, cwd=cwd)
        self.assertEqual(data_proc.exit, 0, data_proc.stderr)
        data = json.loads(data_proc.stdout)
        return plain.stdout, data

    def _row(self, data, session_id):
        matches = [
            row for row in data["sessions"]
            if isinstance(row, dict)
            and session_id in {row.get("id"), row.get("session")}
        ]
        self.assertEqual(
            len(matches),
            1,
            f"expected one waste row for {session_id}: {data['sessions']}",
        )
        return matches[0]

    def test_turns_count_assistant_usage_rows(self):
        """Scenario: a waste Claude session row carries turns, avg_context, and refusals"""
        session_id, rows = self._budget_rows()
        path = self._plant_transcript(session_id, rows)
        self.assertEqual(path.read_text(encoding="utf-8").count('"type": "assistant"'), 3)
        plain, data = self._waste()
        row = self._row(data, session_id)
        self.assertIn("turns", row)
        self.assertEqual(row["turns"], 2)
        self.assertIsNotNone(_plain_field(plain, "turns", 2), plain)

    def test_avg_context_is_mean_of_input_and_caches_not_output(self):
        session_id, rows = self._budget_rows("avg-sess")
        self._plant_transcript(session_id, rows)
        plain, data = self._waste()
        row = self._row(data, session_id)
        self.assertIn("avg_context", row)
        self.assertEqual(row["avg_context"], 205)
        self.assertNotEqual(row["avg_context"], 5205)
        self.assertNotEqual(row["avg_context"], 15)
        self.assertIsNotNone(
            _plain_field(plain, "avg_context", 205)
            or _plain_field(plain, "avg-context", 205),
            plain,
        )

    def test_refusals_count_tool_result_deny_markers_not_command_errors(self):
        session_id, rows = self._budget_rows("deny-sess")
        path = self._plant_transcript(session_id, rows)
        text = path.read_text(encoding="utf-8")
        self.assertIn(DENY_MARKER, text)
        self.assertIn(COMMAND_ERROR, text)
        self.assertIn('"is_error": true', text)
        plain, data = self._waste()
        row = self._row(data, session_id)
        self.assertIn("refusals", row)
        self.assertEqual(row["refusals"], 1)
        self.assertIsNotNone(_plain_field(plain, "refusals", 1), plain)

    def test_honest_zero_refusals_print_zero_not_unrecorded(self):
        session_id = "clean-sess"
        self._plant_transcript(session_id, [
            self._assistant(
                session_id,
                "m1",
                "2026-08-22T10:00:00.000Z",
                {
                    "input_tokens": 10,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "output_tokens": 40,
                },
            ),
        ])
        plain, data = self._waste()
        row = self._row(data, session_id)
        self.assertIn("refusals", row)
        self.assertEqual(row["refusals"], 0)
        self.assertNotEqual(row["refusals"], "unrecorded")
        self.assertIsNotNone(_plain_field(plain, "refusals", 0), plain)

    def test_relative_repo_path_is_resolved_before_the_cwd_filter(self):
        """Scenario: waste --repo . from the checkout ranks its Claude sessions

        origin: T-56 (AAR-2026-09-15 section Token use): waste --repo .
        printed claude sessions=0 for a window in which three sessions ran,
        and the absolute path found four. The cwd filter compared the
        literal argument with the transcript's absolute cwd.
        """
        session_id, rows = self._budget_rows("relative-sess")
        self._plant_transcript(session_id, rows)
        plain, data = self._waste(repo=".", cwd=self.w.repo)
        row = self._row(data, session_id)
        self.assertEqual(row["turns"], 2)
        self.assertIn(session_id, plain)

    def test_refusals_count_wire_line_errors_without_the_deny_prefix(self):
        """Scenario: an is_error tool result carrying the refusal wire is a refusal

        origin: T-56 (AAR-2026-09-15 section Token use): the report read
        refusals=0 for session 44f9de58 where 30 tool results carried
        is_error and the refusal wire with no "hook returned a deny
        decision" prefix. The same wire in a tool result that is not an
        error is the clean neighbour and stays uncounted, and so is an
        errored result that only quotes a wire line inside an assertion
        message (review of 75f6e9cf by Claude Opus 5, P3: the shape a
        failed lane suite produces).
        """
        session_id = "wire-sess"
        self._plant_transcript(session_id, [
            self._assistant(
                session_id,
                "m1",
                "2026-08-22T10:00:00.000Z",
                {
                    "input_tokens": 10,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "output_tokens": 40,
                },
            ),
            self._tool_result(session_id, "2026-08-22T10:00:05.000Z", WIRE_REFUSAL, True),
            self._tool_result(session_id, "2026-08-22T10:00:10.000Z", WIRE_REFUSAL, False),
            self._tool_result(session_id, "2026-08-22T10:00:15.000Z", COMMAND_ERROR, True),
            self._tool_result(session_id, "2026-08-22T10:00:20.000Z", QUOTED_REFUSAL, True),
        ])
        plain, data = self._waste()
        row = self._row(data, session_id)
        self.assertEqual(row["refusals"], 1)
        self.assertIsNotNone(_plain_field(plain, "refusals", 1), plain)

    def test_a_cwd_inside_the_repo_counts_and_a_sibling_prefix_does_not(self):
        """Scenario: a session run from a directory inside the checkout is the checkout's

        origin: review of 75f6e9cf (Claude Opus 5): exact equality on cwd
        dropped rows whose cwd was ops/conductor/tools or ops under this
        repo (30 usage rows over the store, measured 2026-09-15). A sibling
        directory that shares the prefix without the separator is another
        repository and stays out.
        """
        usage = {
            "input_tokens": 10,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "output_tokens": 40,
        }
        inside = self.w.repo / "ops" / "conductor" / "tools"
        sibling = self.w.root / (self.w.repo.name + "-other")
        self._plant_transcript("inside-sess", [
            self._assistant("inside-sess", "m1", "2026-08-22T10:00:00.000Z", usage, cwd=inside),
        ])
        self._plant_transcript("sibling-sess", [
            self._assistant("sibling-sess", "m1", "2026-08-22T10:00:00.000Z", usage, cwd=sibling),
        ])
        plain, data = self._waste()
        row = self._row(data, "inside-sess")
        self.assertEqual(row["turns"], 1)
        self.assertEqual(
            [r for r in data["sessions"] if isinstance(r, dict) and r.get("session") == "sibling-sess"],
            [],
        )
        self.assertNotIn("sibling-sess", plain)

    def test_unreadable_transcript_prints_unrecorded_never_zero(self):
        """Scenario: an unreadable transcript prints unrecorded for turns, avg_context, and refusals"""
        path = self.w.claude / self._slug() / "unreadable.jsonl"
        raw = "{broken\n"
        plant_text(path, raw)
        self.assertEqual(path.read_text(encoding="utf-8"), raw)
        self.assertNotIn("usage", raw)
        plain, data = self._waste("--top", "20")
        row = self._row(data, "unreadable")
        for key in ("turns", "avg_context", "refusals"):
            with self.subTest(key=key):
                self.assertIn(key, row)
                self.assertEqual(row[key], "unrecorded")
                self.assertNotEqual(row[key], 0)
        combined = plain + json.dumps(data)
        self.assertIn("unrecorded", combined.lower())
        self.assertIsNotNone(_plain_field(plain, "turns", "unrecorded"), plain)
        self.assertIsNone(_plain_field(plain, "turns", 0), plain)

    def test_windowing_scopes_turns_avg_context_and_refusals(self):
        session_id = "straddle-sess"
        self._plant_transcript(session_id, [
            self._assistant(
                session_id,
                "outside-turn",
                "2026-08-21T11:00:00.000Z",
                {
                    "input_tokens": 8000,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "output_tokens": 5,
                },
            ),
            self._tool_result(
                session_id,
                "2026-08-21T11:00:05.000Z",
                DENY_MARKER,
                True,
            ),
            self._assistant(
                session_id,
                "inside-turn",
                "2026-08-22T10:00:00.000Z",
                {
                    "input_tokens": 100,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "output_tokens": 40,
                },
            ),
            self._tool_result(
                session_id,
                "2026-08-22T10:00:05.000Z",
                DENY_MARKER,
                True,
            ),
        ])
        plain, data = self._waste()
        row = self._row(data, session_id)
        self.assertEqual(row.get("turns"), 1)
        self.assertEqual(row.get("avg_context"), 100)
        self.assertEqual(row.get("refusals"), 1)
        self.assertNotIn("8000", json.dumps(row))
        self.assertIsNotNone(_plain_field(plain, "turns", 1), plain)


if __name__ == "__main__":
    unittest.main()
