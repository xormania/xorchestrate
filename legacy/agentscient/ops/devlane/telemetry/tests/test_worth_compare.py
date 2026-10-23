"""worth compare: two windows, the levers as rows.

Authored from ops/process/worth.md section `worth compare` and Behavior
contract item 9 alone; worth.py was not read. Origin: T-62, the owner's
2026-09-15 token-efficiency order, and the reading that `messages` is
not turns.
"""

from __future__ import annotations

import json
import unittest

import test_worth as worth_contract

from ops.testkit import World

NOW = "2026-08-22T12:00:00.000Z"
BASE_WINDOW = "2026-08-22T06:00:00Z..2026-08-22T09:00:00Z"
WINDOW = "2026-08-22T09:00:00Z..2026-08-22T12:00:00Z"
LEVERS = ("context", "refusals", "landed", "dispatch-cached")
FALLING = ("refusals", "landed")
CONTEXT_THRESHOLD = 300_000


class _Compare(unittest.TestCase):
    """The shared planting harness, borrowed as test_worth_session_row does."""

    maxDiff = None

    def setUp(self):
        self.w = worth_contract.WorthContractTests(
            "test_unusable_arguments_exit_two_and_explain_the_reason"
        )
        self.w.setUp()
        self.addCleanup(self.w.doCleanups)
        self.world = World(prefix="worth-compare-")
        self.addCleanup(self.world.close)

    def _assistant(self, session_id, mid, timestamp, context, out=10):
        """One assistant turn whose context is input plus both caches."""
        return {
            "type": "assistant",
            "timestamp": timestamp,
            "cwd": str(self.w.repo),
            "sessionId": session_id,
            "message": {
                "id": mid,
                "model": "claude-fable-5",
                "usage": {
                    "input_tokens": context,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "output_tokens": out,
                },
                "content": [],
            },
        }

    def _plant(self, session_id, rows):
        path = self.w.claude / self.w._slug() / f"{session_id}.jsonl"
        return self.w._write_jsonl(path, rows)

    def _plant_context(self, session_id, stamp, context):
        self._plant(session_id, [
            self._assistant(session_id, f"msg-{session_id}-1", stamp, context),
        ])

    def _plant_dispatch(self, job_id, launched, job="author-tests",
                        harness="grok", cached=1000, total=1200):
        """A launcher record the reader can count, minimal by design:
        omitting snapshot leaves _same_repo answering this repository."""
        root = self.world.cwd / "dispatch"
        path = root / job_id / "record.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.world.plant_text(path, json.dumps({
            "id": job_id, "job": job, "stage": "tests", "unit": "work",
            "status": "closed",
            "harness": {"name": harness, "version": "1.0.0", "argv": [harness]},
            "lineage": {"branch": "work"},
            "model": {"ran": "ran"},
            "at": {"launched": launched, "closed": launched},
            "session": {"spend": {"total": total, "cached": cached,
                                  "input": 100, "out": 10}},
        }, indent=1) + "\n", recognisable=job_id)
        return root

    def _merged_pr(self, when, number=99):
        """One merged PR on the first-parent line inside a window."""
        self.w._git("switch", "-q", "-c", f"pr-{number}")
        self.w._commit_files({f"src/pr{number}.txt": "landed\n"},
                             f"implement pr-{number}", when)
        self.w._git("switch", "-q", "dev")
        return self.w._merge(
            f"pr-{number}",
            f"Merge pull request #{number} from fixtures/pr-{number}",
            when,
        )

    def _run(self, *args, now=NOW):
        return self.w._run_worth("compare", *args, now=now)

    def _refused(self, proc):
        """An exit 2 that is compare's own refusal, not argparse rejecting
        an unknown verb — otherwise every argument row here would pass
        before the verb exists."""
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertNotIn("invalid choice", proc.stderr)
        self.assertTrue(proc.stderr.strip(), "a refusal says why")
        self.assertEqual(proc.stdout, "")

    def _levers(self, *args, now=NOW):
        proc = self._run(*args, "--format", "json", now=now)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        value = json.loads(proc.stdout)
        self.assertIsInstance(value, dict)
        self.assertIn("levers", value)
        return value

    def _row(self, value, name):
        rows = value["levers"]
        if isinstance(rows, dict):
            self.assertIn(name, rows)
            return rows[name]
        found = [r for r in rows if r.get("lever") == name or r.get("name") == name]
        self.assertEqual(len(found), 1, f"{name} not one row in {rows!r}")
        return found[0]


class CompareArguments(_Compare):
    """Item 9: both windows required; a bad range or `--top` refuses, exit 2."""

    def test_both_windows_are_required(self):
        self._refused(self._run("--window", WINDOW))

    def test_a_malformed_range_exits_two(self):
        self._refused(
            self._run("--baseline", "2026-08-22T06:00:00Z", "--window", WINDOW))

    def test_until_at_or_before_since_exits_two(self):
        self._refused(self._run(
            "--baseline", "2026-08-22T09:00:00Z..2026-08-22T06:00:00Z",
            "--window", WINDOW,
        ))

    def test_top_is_refused_naming_compare(self):
        """`--top` ranks nothing here; accepted-and-ignored is a claim
        the verb does not keep."""
        proc = self._run("--baseline", BASE_WINDOW, "--window", WINDOW, "--top", "5")
        self._refused(proc)
        self.assertIn("compare", proc.stderr)


class CompareLeverRows(_Compare):
    """Item 9: every row carries n for both windows; n=0 reads UNREACHABLE."""

    def test_every_lever_row_carries_n_for_both_windows(self):
        self._plant_context("base-sess", "2026-08-22T07:00:00.000Z", 100)
        self._plant_context("win-sess", "2026-08-22T10:00:00.000Z", 100)
        value = self._levers("--baseline", BASE_WINDOW, "--window", WINDOW)
        for name in LEVERS:
            row = self._row(value, name)
            self.assertIn("baseline", row, name)
            self.assertIn("window", row, name)
            self.assertIn("n", row["baseline"], name)
            self.assertIn("n", row["window"], name)

    def test_a_window_with_no_sessions_reads_unreachable(self):
        """An arm that could not look has no result: never met, never 0."""
        self._plant_context("base-sess", "2026-08-22T07:00:00.000Z", 100)
        value = self._levers("--baseline", BASE_WINDOW, "--window", WINDOW)
        row = self._row(value, "context")
        self.assertEqual(row["verdict"], "UNREACHABLE")
        self.assertEqual(row["window"]["n"], 0)
        self.assertEqual(row["window"]["figure"], "unrecorded")

    def test_a_baseline_with_no_sessions_leaves_a_falling_lever_unreachable(self):
        """A fall measured against nothing is not a fall."""
        self._plant_context("win-sess", "2026-08-22T10:00:00.000Z", 100)
        value = self._levers("--baseline", BASE_WINDOW, "--window", WINDOW)
        for name in FALLING:
            row = self._row(value, name)
            self.assertEqual(row["verdict"], "UNREACHABLE", name)
            self.assertEqual(row["baseline"]["n"], 0, name)

    def test_context_reads_met_under_the_threshold(self):
        self._plant_context("base-sess", "2026-08-22T07:00:00.000Z", 500_000)
        self._plant_context("win-sess", "2026-08-22T10:00:00.000Z", 150_000)
        value = self._levers("--baseline", BASE_WINDOW, "--window", WINDOW)
        row = self._row(value, "context")
        self.assertEqual(row["verdict"], "met")
        self.assertEqual(row["window"]["n"], 1)
        self.assertLess(row["window"]["figure"], CONTEXT_THRESHOLD)

    def test_context_reads_short_above_the_threshold(self):
        self._plant_context("base-sess", "2026-08-22T07:00:00.000Z", 150_000)
        self._plant_context("win-sess", "2026-08-22T10:00:00.000Z", 430_000)
        value = self._levers("--baseline", BASE_WINDOW, "--window", WINDOW)
        row = self._row(value, "context")
        self.assertEqual(row["verdict"], "short")
        self.assertGreaterEqual(row["window"]["figure"], CONTEXT_THRESHOLD)

    def test_no_figure_is_called_turns(self):
        """`messages` is not turns for codex or grok, so compare names none."""
        self._plant_context("win-sess", "2026-08-22T10:00:00.000Z", 100)
        proc = self._run("--baseline", BASE_WINDOW, "--window", WINDOW)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("turns", proc.stdout)


class LandedCountsBothHalves(_Compare):
    """LD-54: a rate of zero produced by an empty numerator is not an
    improvement. `landed` carries `dispatches` beside `n` and reads
    UNREACHABLE when either half is zero."""

    def test_landed_carries_the_dispatch_count_beside_the_pr_count(self):
        self._merged_pr("2026-08-22T10:30:00.000Z")
        root = self._plant_dispatch("20260822T103000Z-tests-grok-aaaaaa",
                                    "2026-08-22T10:30:00Z")
        value = self._levers("--baseline", BASE_WINDOW, "--window", WINDOW,
                             "--dispatch-dir", str(root))
        row = self._row(value, "landed")
        self.assertIn("dispatches", row["window"], row["window"])
        self.assertEqual(row["window"]["n"], 1)
        self.assertEqual(row["window"]["dispatches"], 1)
        self.assertEqual(row["window"]["figure"], 1.0)

    def test_landed_with_a_merge_and_no_dispatch_reads_unreachable(self):
        self._merged_pr("2026-08-22T10:30:00.000Z")
        root = self.world.cwd / "empty-dispatch"
        root.mkdir(parents=True, exist_ok=True)
        value = self._levers("--baseline", BASE_WINDOW, "--window", WINDOW,
                             "--dispatch-dir", str(root))
        row = self._row(value, "landed")
        self.assertIn("dispatches", row["window"], row["window"])
        self.assertEqual(row["window"]["n"], 1)
        self.assertEqual(row["window"]["dispatches"], 0)
        self.assertEqual(row["verdict"], "UNREACHABLE")
        self.assertEqual(row["window"]["figure"], "unrecorded")


class CompareOutput(_Compare):
    """Item 9: JSON carries the stamp and both windows; exit 0 all-UNREACHABLE."""

    def test_json_carries_stamp_baseline_window_and_levers(self):
        self._plant_context("win-sess", "2026-08-22T10:00:00.000Z", 100)
        value = self._levers("--baseline", BASE_WINDOW, "--window", WINDOW)
        for key in ("stamp", "baseline", "window", "levers"):
            self.assertIn(key, value)
        stamp = value["stamp"]
        self.assertIn("head", json.dumps(stamp))

    def test_exit_zero_when_every_row_is_unreachable(self):
        """An empty pair of windows is an answer, not a failure."""
        proc = self._run("--baseline", BASE_WINDOW, "--window", WINDOW)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        value = self._levers("--baseline", BASE_WINDOW, "--window", WINDOW)
        rows = value["levers"]
        rows = list(rows.values()) if isinstance(rows, dict) else rows
        self.assertTrue(rows, "levers are declared even when unreachable")
        self.assertTrue(all(r["verdict"] == "UNREACHABLE" for r in rows), rows)


if __name__ == "__main__":
    unittest.main()
