"""Operator command behavior against retained capability-gap state."""

import contextlib
import importlib.util
import io
import json
import shlex
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from devtools.capability_gaps import record_report, review_gap, show_gap
from devtools.store import SCHEMA_VERSION, State, Store, canonical, stamp
from devtools.work import Work

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "capability-gaps.py"
SPEC = importlib.util.spec_from_file_location("capability_gap_cli", SCRIPT)
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)


class CapabilityGapCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="capability-gap-cli-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.directory = self.root / "private state"
        self.store = Store(self.directory)
        self.store.initialize([{"name": "worker", "harness": "external", "workspace": str(self.workspace)}])
        self.work_order = Work(self.store, "worker").work_order_create(
            title="Fixture", objective="Observe limitations", done_when="Evidence retained",
        )["work_order"]["id"]
        self.encounters = 0

    def report(self, capability="Discover configured checks"):
        self.encounters += 1
        gap = {"capability": capability, "category": "discovery", "occurrence_key": str(self.encounters)}
        scope = {"revision": 1, "work_order": self.work_order, "job": None}
        with self.store.connect() as db:
            update = db.execute(
                "INSERT INTO work_updates(actor,kind,text,work_order,binding_revision,request_key,created,capability_gap) "
                "VALUES ('worker','finding',?,?,1,?,?,?)",
                ("Could not discover the check names", self.work_order, str(self.encounters), stamp(), canonical(gap)),
            ).lastrowid
            record_report(self.store, db, "worker", scope, update, gap)
            return db.execute("SELECT gap FROM capability_gap_occurrences WHERE update_id=?", (update,)).fetchone()[0]

    def run_cli(self, *args, state=None, structured=True):
        stdout, stderr = io.StringIO(), io.StringIO()
        options = ["--state", str(state or self.directory)]
        if structured:
            options.append("--json")
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main([*options, *args])
        output = stdout.getvalue()
        return code, json.loads(output) if structured else output, stderr.getvalue()

    def test_portable_script_and_active_status_filter(self):
        active = self.report()
        resolved = self.report("Inspect provider readiness")
        review_gap(self.store, resolved, 1, "resolved", "discovery", "Tool located", evidence="Observed tool response")
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--state", str(self.directory), "--json", "list"],
            cwd=self.root, text=True, capture_output=True, check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([active], [row["id"] for row in json.loads(result.stdout)["gaps"]])
        code, output, _ = self.run_cli("list", "--status", "all", "--limit", "1")
        self.assertEqual(0, code)
        self.assertEqual(active, output["next_after"])
        code, output, _ = self.run_cli("list", "--status", "all", "--after", str(output["next_after"]))
        self.assertEqual([resolved], [row["id"] for row in output["gaps"]])
        self.assertIsNone(output["next_after"])

    def test_show_has_independent_occurrence_and_review_cursors(self):
        gap = self.report()
        self.report()
        review_gap(self.store, gap, 2, "reviewing", "discovery", "Investigating")
        review_gap(self.store, gap, 3, "deferred", "discovery", "Follow up after current work")
        code, first, _ = self.run_cli("show", str(gap), "--limit", "1")
        self.assertEqual(0, code)
        self.assertIsNotNone(first["next_after"])
        self.assertIsNotNone(first["next_review_after"])
        code, second, _ = self.run_cli("show", str(gap), "--limit", "1", "--after", str(first["next_after"]),
                                     "--review-after", str(first["next_review_after"]))
        self.assertEqual(0, code)
        self.assertNotEqual(first["occurrences"][0]["id"], second["occurrences"][0]["id"])
        self.assertNotEqual(first["reviews"][0]["id"], second["reviews"][0]["id"])
        self.assertIsNone(second["next_after"])
        self.assertIsNone(second["next_review_after"])

    def test_show_resumes_both_streams_without_replaying_exhausted_history(self):
        for occurrence_count, review_count in ((1, 3), (3, 1), (3, 0), (1, 0)):
            with self.subTest(occurrences=occurrence_count, reviews=review_count):
                gap = self.report(f"History {occurrence_count}/{review_count}")
                for _ in range(occurrence_count - 1):
                    self.report(f"History {occurrence_count}/{review_count}")
                for revision in range(occurrence_count, occurrence_count + review_count):
                    review_gap(self.store, gap, revision, "reviewing", "discovery", "Investigating")
                expected = show_gap(State(self.directory), gap)
                occurrences, reviews = [], []
                cursor = {"after": 0, "review_after": 0}
                for _ in range(4):
                    code, page, _ = self.run_cli(
                        "show", str(gap), "--limit", "1", "--after", str(cursor["after"]),
                        "--review-after", str(cursor["review_after"]),
                    )
                    self.assertEqual(0, code)
                    occurrences.extend(row["id"] for row in page["occurrences"])
                    reviews.extend(row["id"] for row in page["reviews"])
                    # The combined cursor carries the consumed position of both
                    # streams, even if only one has further pages.
                    cursor = page["next_page"]
                    if cursor is None:
                        break
                else:
                    self.fail("History pagination did not terminate")
                self.assertEqual([row["id"] for row in expected["occurrences"]], occurrences)
                self.assertEqual([row["id"] for row in expected["reviews"]], reviews)

    def test_show_preserves_empty_stream_positions_and_terminates_empty_pages(self):
        gap = self.report()
        for revision in (1, 2, 3):
            review_gap(self.store, gap, revision, "reviewing", "discovery", "Investigating")
        # An occurrence position beyond the existing history must not rewind
        # while the independent review stream continues.
        position = show_gap(State(self.directory), gap)["occurrences"][-1]["id"] + 10
        reviews = []
        cursor = {"after": position, "review_after": 0}
        for _ in range(3):
            page = show_gap(State(self.directory), gap, limit=1, **cursor)
            self.assertEqual([], page["occurrences"])
            reviews.extend(row["id"] for row in page["reviews"])
            cursor = page["next_page"]
            if cursor is not None:
                self.assertEqual(position, cursor["after"])
        self.assertIsNone(cursor)
        self.assertEqual(3, len(set(reviews)))
        empty = show_gap(State(self.directory), gap, after=position, review_after=reviews[-1], limit=1)
        self.assertEqual([], empty["occurrences"])
        self.assertEqual([], empty["reviews"])
        self.assertIsNone(empty["next_page"])

    def test_human_show_continuation_carries_both_positions(self):
        gap = self.report()
        review_gap(self.store, gap, 1, "reviewing", "discovery", "Investigating")
        review_gap(self.store, gap, 2, "deferred", "discovery", "Follow up")
        occurrence = show_gap(State(self.directory), gap)["occurrences"][0]["id"]
        review = show_gap(State(self.directory), gap)["reviews"][0]["id"]
        code, output, _ = self.run_cli("show", str(gap), "--limit", "1", structured=False)
        self.assertEqual(0, code)
        continuation = next(line for line in output.splitlines() if line.startswith("Continue"))
        options = shlex.split(continuation.split("with ", 1)[1].removesuffix("."))
        code, resumed, _ = self.run_cli("show", str(gap), "--limit", "1", *options)
        self.assertEqual(0, code)
        self.assertEqual([], resumed["occurrences"])
        self.assertEqual(1, len(resumed["reviews"]))
        self.assertIsNone(resumed["next_page"])
        self.assertIn(f"Continue with --after {occurrence} --review-after {review}.", output)
        self.assertNotIn("Continue reviews with", output)

    def test_review_persists_owner_evidence_and_rejects_stale_revision(self):
        gap = self.report()
        arguments = ["review", str(gap), "--expected-revision", "1", "--status", "reviewing",
                     "--classification", "discovery", "--note", "Investigating", "--owner", "xor"]
        code, result, _ = self.run_cli(*arguments)
        self.assertEqual(0, code)
        self.assertEqual("xor", result["gap"]["owner"])
        self.assertEqual(2, result["gap"]["revision"])
        code, result, _ = self.run_cli(*arguments)
        self.assertEqual(2, code)
        self.assertIn("changed", result["error"])
        code, result, _ = self.run_cli("review", str(gap), "--expected-revision", "2", "--status", "resolved",
                                     "--classification", "discovery", "--note", "Tool found",
                                     "--evidence", "Observed check-list response")
        self.assertEqual(0, code)
        self.assertEqual("xor", result["gap"]["owner"])
        self.assertEqual("Observed check-list response", result["review"]["evidence"])
        self.assertEqual(2, len(show_gap(State(self.directory), gap)["reviews"]))

    def test_invalid_reviews_never_construct_store_or_upgrade_state(self):
        with self.store.connect() as db:
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION - 1}")
        base = ["review", "1", "--expected-revision", "1", "--status", "resolved", "--classification", "unknown"]
        for extra in (["--note", "Need evidence"], ["--note", " ", "--evidence", "Supplied"],
                      ["--note", "A" * 3001, "--evidence", "Supplied"],
                      ["--note", "Valid", "--evidence", "Supplied", "--owner", "X" * 161]):
            with self.subTest(extra=extra), patch.object(cli, "Store") as writer:
                code, output, _ = self.run_cli(*base, *extra)
                self.assertEqual(2, code)
                self.assertIn("error", output)
                writer.assert_not_called()
        with State(self.directory).connect() as db:
            self.assertEqual(SCHEMA_VERSION - 1, db.execute("PRAGMA user_version").fetchone()[0])

    def test_inspection_never_initializes_or_migrates_missing_or_old_state(self):
        absent = self.root / "absent"
        code, output, _ = self.run_cli("list", state=absent)
        self.assertEqual(2, code)
        self.assertIn("unavailable", output["error"])
        self.assertFalse(absent.exists())
        legacy = self.root / "legacy"
        legacy.mkdir()
        with sqlite3.connect(legacy / "live.sqlite3") as db:
            db.execute("CREATE TABLE retained (value TEXT)")
            db.execute("INSERT INTO retained VALUES ('keep')")
            db.execute("PRAGMA user_version=1")
        before = (legacy / "live.sqlite3").read_bytes()
        for action in (["list"], ["show", "1"]):
            code, output, _ = self.run_cli(*action, state=legacy)
            self.assertEqual(2, code)
            self.assertIn("upgrade", output["error"])
        self.assertEqual(before, (legacy / "live.sqlite3").read_bytes())

    def test_review_does_not_create_or_migrate_uninitialized_state(self):
        empty = self.root / "empty"
        empty.mkdir()
        arguments = ["review", "1", "--expected-revision", "1", "--status", "reviewing",
                     "--classification", "unknown", "--note", "Investigating"]
        with patch.object(cli, "Store") as writer:
            code, output, _ = self.run_cli(*arguments, state=empty)
            self.assertEqual(2, code)
            self.assertIn("error", output)
            writer.assert_not_called()
        self.assertEqual([], list(empty.iterdir()))
        with sqlite3.connect(empty / "live.sqlite3") as db:
            db.execute("CREATE TABLE unrelated (value TEXT)")
        before = (empty / "live.sqlite3").read_bytes()
        with patch.object(cli, "Store") as writer:
            code, output, _ = self.run_cli(*arguments, state=empty)
            self.assertEqual(2, code)
            writer.assert_not_called()
        self.assertEqual(before, (empty / "live.sqlite3").read_bytes())

    def test_suppressed_capture_is_explicit_in_human_and_json_output(self):
        gap = self.report()
        (self.directory / "team.json").write_text('{"project":{"configuration_file":"missing.json"}}')
        code, output, _ = self.run_cli("list")
        self.assertEqual(2, code)
        self.assertEqual("suppressed", output["capture"])
        code, output, _ = self.run_cli("show", str(gap), structured=False)
        self.assertEqual(2, code)
        self.assertIn("Inspection unavailable", output)
        code, output, _ = self.run_cli("review", str(gap), "--expected-revision", "1", "--status", "reviewing",
                                     "--classification", "unknown", "--note", "Investigating", structured=False)
        self.assertEqual(0, code)
        self.assertIn("Review committed", output)
        self.assertIn("suppressed", output)
        with State(self.directory).connect() as db:
            self.assertEqual("reviewing", db.execute("SELECT status FROM capability_gaps WHERE id=?", (gap,)).fetchone()[0])

    def test_human_output_preserves_controls_as_literal_text(self):
        self.report("Need check discovery\x1b[2J\nsecond line")
        code, output, _ = self.run_cli("list", structured=False)
        self.assertEqual(0, code)
        self.assertNotIn("\x1b", output)
        self.assertIn("\\u001b[2J\\nsecond line", output)


if __name__ == "__main__":
    unittest.main()
