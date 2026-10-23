"""Durable gap identity, review concurrency, migration and safe inspection."""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from support import initialize

from devtools.capability_gaps import existing_report, list_gaps, record_report, review_gap, show_gap, validate_gap
from devtools.store import SCHEMA_VERSION, State, Store, canonical, digest, stamp
from devtools.telemetry import clean_for_state


class CapabilityGapStorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        workspace = self.root / "workspace"
        workspace.mkdir()
        self.directory = self.root / "state"
        initialize(self.directory, workspace, ["worker:external"])
        self.store = Store(self.directory)
        self.view = State(self.directory)
        self.order = self.order_for("org/project")
        self.scope = {"work_order": self.order, "job": None, "revision": 1}

    def order_for(self, repository):
        with self.store.connect() as db:
            return db.execute("INSERT INTO work_orders(title,objective,done_when,created_by,created,updated,request_payload,repository) "
                              "VALUES ('Task','Build','Pass','worker',?,?,?,?)",
                              (stamp(), stamp(), '{}', repository)).lastrowid

    def report(self, key="first", capability="run checks", *, scope=None, text="No maintained check exists"):
        scope = scope or self.scope
        gap = validate_gap({"capability": capability, "category": "missing_tool", "occurrence_key": key})
        fingerprint = digest([scope, text, gap])
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = existing_report(db, "worker", scope, gap, fingerprint)
            if old:
                return old["id"]
            update = db.execute("INSERT INTO work_updates(actor,kind,text,work_order,job,binding_revision,request_key,created,capability_gap) "
                                "VALUES ('worker','blocker',?,?,?,?,?,?,?)",
                                (text, scope["work_order"], scope["job"], scope["revision"], fingerprint, stamp(),
                                 canonical(clean_for_state(self.directory, gap)))).lastrowid
            record_report(self.store, db, "worker", scope, update, gap)
            return update

    def test_retries_are_idempotent_and_changed_payload_is_rejected(self):
        update = self.report()
        self.assertEqual(update, self.report())
        with self.assertRaisesRegex(ValueError, "different report"):
            self.report(text="Different report for same key")
        gap = list_gaps(self.view)["gaps"][0]
        self.assertEqual((1, 1), (gap["occurrence_count"], gap["revision"]))
        with self.store.connect() as db:
            self.assertEqual(1, db.execute("SELECT count(*) FROM work_updates").fetchone()[0])
            self.assertEqual(1, db.execute("SELECT count(*) FROM events WHERE kind='capability_gap_reported'").fetchone()[0])

    def test_grouping_normalizes_only_case_and_surrounding_spaces(self):
        self.report()
        self.report("second", " RUN CHECKS ")
        self.report("third", "run  checks")
        gaps = list_gaps(self.view)["gaps"]
        self.assertEqual([2, 1], [row["occurrence_count"] for row in gaps])
        self.assertEqual(["unknown", "unknown"], [row["classification"] for row in gaps])

    def test_project_groups_and_unconfigured_work_orders_are_isolated(self):
        self.report()
        for repository in ("org/project", "org/different", "", ""):
            scope = {**self.scope, "work_order": self.order_for(repository)}
            self.report(scope=scope)
        gaps = list_gaps(self.view)["gaps"]
        self.assertEqual(4, len(gaps))
        self.assertEqual([2, 1, 1, 1], [row["occurrence_count"] for row in gaps])

    def test_configured_repository_groups_reports_before_workspace_initialization(self):
        team_path = self.directory / "team.json"
        team = json.loads(team_path.read_text())
        team["project"] = {"repository": " ORG/Project "}
        team_path.write_text(canonical(team))
        scope = {**self.scope, "work_order": self.order_for("")}
        self.report(scope=scope)
        gap = list_gaps(self.view)["gaps"][0]
        self.assertEqual(" ORG/Project ", gap["repository"])
        review_gap(self.store, gap["id"], 1, "reviewing", "discovery", "Investigating", owner="xor")
        before = show_gap(self.view, gap["id"])["reviews"]
        with self.store.connect() as db:
            db.execute("UPDATE work_orders SET repository='org/project' WHERE id=?", (scope["work_order"],))
        self.report("after-workspace", " RUN CHECKS ", scope=scope)
        detail = show_gap(self.view, gap["id"])
        self.assertEqual(1, len(list_gaps(self.view)["gaps"]))
        self.assertEqual((2, 3, "reviewing", "xor"),
                         (detail["gap"]["occurrence_count"], detail["gap"]["revision"],
                          detail["gap"]["status"], detail["gap"]["owner"]))
        self.assertEqual(before, detail["reviews"])

    def test_late_repository_promotes_fallback_without_losing_history_or_id(self):
        scope = {**self.scope, "work_order": self.order_for("")}
        first_update = self.report(scope=scope)
        gap = list_gaps(self.view)["gaps"][0]
        review_gap(self.store, gap["id"], 1, "resolved", "discovery", "Found", evidence="Verified", owner="xor")
        before = show_gap(self.view, gap["id"])["reviews"]
        with self.store.connect() as db:
            db.execute("UPDATE work_orders SET repository='org/project' WHERE id=?", (scope["work_order"],))
        self.report("after-repository", " RUN CHECKS ", scope=scope)
        self.assertEqual(first_update, self.report(scope=scope))
        self.report("other-order", scope={**scope, "work_order": self.order})
        detail = show_gap(self.view, gap["id"])
        self.assertEqual(1, len(list_gaps(self.view)["gaps"]))
        self.assertEqual((gap["id"], 3, 4, "open", "xor", "org/project"),
                         (detail["gap"]["id"], detail["gap"]["occurrence_count"], detail["gap"]["revision"],
                          detail["gap"]["status"], detail["gap"]["owner"], detail["gap"]["repository"]))
        self.assertEqual(before, detail["reviews"])
        self.assertEqual(3, len(detail["occurrences"]))
        with self.assertRaisesRegex(ValueError, "changed"):
            review_gap(self.store, gap["id"], 2, "resolved", "discovery", "Stale", evidence="Old")

    def test_late_repository_collision_preserves_independent_reviewed_gap_ids(self):
        self.report()
        canonical_gap = list_gaps(self.view)["gaps"][0]
        review_gap(self.store, canonical_gap["id"], 1, "reviewing", "permission", "Keep boundary", owner="canonical")
        before = show_gap(self.view, canonical_gap["id"])
        scope = {**self.scope, "work_order": self.order_for("")}
        self.report(scope=scope)
        fallback = list_gaps(self.view)["gaps"][1]
        review_gap(self.store, fallback["id"], 1, "resolved", "discovery", "Found", evidence="Verified", owner="fallback")
        fallback_reviews = show_gap(self.view, fallback["id"])["reviews"]
        with self.store.connect() as db:
            db.execute("UPDATE work_orders SET repository='org/project' WHERE id=?", (scope["work_order"],))
        self.report("later", scope=scope)
        self.report("later-again", scope={**scope, "revision": 2})
        self.assertEqual(before, show_gap(self.view, canonical_gap["id"]))
        detail = show_gap(self.view, fallback["id"])
        self.assertEqual(2, len(list_gaps(self.view)["gaps"]))
        self.assertEqual((3, 4, "open", "fallback"),
                         (detail["gap"]["occurrence_count"], detail["gap"]["revision"],
                          detail["gap"]["status"], detail["gap"]["owner"]))
        self.assertEqual(fallback_reviews, detail["reviews"])

    def test_two_fallback_work_orders_converging_keep_their_original_histories(self):
        scopes = [{**self.scope, "work_order": self.order_for("")} for _ in range(2)]
        for scope in scopes:
            self.report(scope=scope)
        gaps = list_gaps(self.view)["gaps"]
        for gap in gaps:
            review_gap(self.store, gap["id"], 1, "reviewing", "unknown", "Separate investigation")
        reviews = [show_gap(self.view, gap["id"])["reviews"] for gap in gaps]
        with self.store.connect() as db:
            for scope in scopes:
                db.execute("UPDATE work_orders SET repository='org/project' WHERE id=?", (scope["work_order"],))
        for scope in scopes:
            self.report("known", scope=scope)
            self.report("known-again", scope=scope)
        current = list_gaps(self.view)["gaps"]
        self.assertEqual([gap["id"] for gap in gaps], [gap["id"] for gap in current])
        self.assertEqual([3, 3], [gap["occurrence_count"] for gap in current])
        self.assertEqual(reviews, [show_gap(self.view, gap["id"])["reviews"] for gap in current])

    def test_invalid_project_metadata_preserves_suppressed_work_order_reports(self):
        team_path = self.directory / "team.json"
        original = team_path.read_bytes()
        for value in ([], None, "invalid team shape"):
            with self.subTest(value=value):
                scope = {**self.scope, "work_order": self.order_for("")}
                try:
                    team_path.write_text(canonical(value))
                    update = self.report(scope=scope)
                finally:
                    team_path.write_bytes(original)
                with self.store.connect() as db:
                    ident = db.execute("SELECT gap FROM capability_gap_occurrences WHERE update_id=?", (update,)).fetchone()[0]
                gap = show_gap(self.view, ident)["gap"]
                self.assertEqual(1, gap["occurrence_count"])
                self.assertEqual("suppressed", gap["capture"]["capture"])

    def test_new_binding_can_report_same_key_again(self):
        self.report()
        self.report(scope={**self.scope, "revision": 2})
        self.assertEqual(2, list_gaps(self.view)["gaps"][0]["occurrence_count"])

    def test_resolution_requires_evidence_and_new_occurrence_reopens(self):
        self.report()
        gap = list_gaps(self.view)["gaps"][0]
        with self.assertRaisesRegex(ValueError, "requires evidence"):
            review_gap(self.store, gap["id"], 1, "resolved", "missing_tool", "Implemented")
        resolved = review_gap(self.store, gap["id"], 1, "resolved", "missing_tool", "Implemented",
                              evidence="Maintainer verified PR 123")
        self.assertEqual("resolved", resolved["gap"]["status"])
        self.assertEqual([], list_gaps(self.view)["gaps"])
        self.report()  # Retried delivery after resolution is still the same encounter.
        unchanged = show_gap(self.view, gap["id"])["gap"]
        self.assertEqual(("resolved", 2, 1), (unchanged["status"], unchanged["revision"], unchanged["occurrence_count"]))
        self.report("another encounter")
        detail = show_gap(self.view, gap["id"])
        self.assertEqual(("open", 3, 2), (detail["gap"]["status"], detail["gap"]["revision"], detail["gap"]["occurrence_count"]))
        self.assertEqual("resolved", detail["reviews"][0]["status"])
        self.assertEqual("operator", detail["reviews"][0]["actor"])
        with self.store.connect() as db:
            events = list(db.execute("SELECT * FROM events WHERE kind='capability_gap_reopened'"))
            self.assertEqual([(self.order, None)], [(row["work_order"], row["job"]) for row in events])

    def test_stale_reviews_and_new_reports_do_not_overwrite_history(self):
        self.report()
        ident = list_gaps(self.view)["gaps"][0]["id"]
        review_gap(self.store, ident, 1, "reviewing", "unknown", "Investigating", owner="maintainer")
        with self.assertRaisesRegex(ValueError, "changed"):
            review_gap(self.store, ident, 1, "deferred", "unknown", "Stale")
        self.report("next")
        with self.assertRaisesRegex(ValueError, "changed"):
            review_gap(self.store, ident, 2, "resolved", "discovery", "Stale", evidence="Old evidence")
        with self.store.work_context("operator", self.order, None):
            result = review_gap(self.store, ident, 3, "deferred", "permission", "Keep the existing boundary")
        self.assertEqual("maintainer", result["gap"]["owner"])
        self.assertEqual(2, len(show_gap(self.view, ident)["reviews"]))
        with self.store.connect() as db:
            event = db.execute("SELECT * FROM events WHERE kind='capability_gap_reviewed' ORDER BY id DESC").fetchone()
            self.assertEqual(("operator", None, None), (event["actor"], event["work_order"], event["job"]))

    def test_projection_pagination_is_bounded_and_independent(self):
        self.report()
        self.report("two")
        self.report("three", "different capability")
        first = list_gaps(self.view, limit=1)
        self.assertIsNotNone(first["next_after"])
        second = list_gaps(self.view, after=first["next_after"], limit=1)
        self.assertIsNone(second["next_after"])
        ident = first["gaps"][0]["id"]
        for revision in (2, 3):
            review_gap(self.store, ident, revision, "reviewing", "unknown", "Review")
        first = show_gap(self.view, ident, limit=1)
        self.assertIsNotNone(first["next_after"])
        self.assertIsNotNone(first["next_review_after"])
        second = show_gap(self.view, ident, after=first["next_after"], review_after=first["next_review_after"], limit=1)
        self.assertIsNone(second["next_after"])
        self.assertIsNone(second["next_review_after"])
        self.assertNotIn("identity", first["gap"])
        self.assertNotIn("occurrence_key", first["occurrences"][0])

    def test_record_report_is_idempotent_and_uses_source_scope_for_events(self):
        update = self.report()
        with self.store.connect() as db:
            record_report(self.store, db, "worker", self.scope, update,
                          {"capability": "run checks", "category": "missing_tool", "occurrence_key": "first"})
            self.assertEqual(1, db.execute("SELECT count(*) FROM capability_gap_occurrences").fetchone()[0])
            db.execute("UPDATE members SET selected_work_order=NULL WHERE name='worker'")
        self.report("next")
        with self.store.connect() as db:
            self.assertEqual({self.order}, {row[0] for row in db.execute("SELECT work_order FROM events WHERE kind='capability_gap_reported'")})

    def test_migration_from_v17_preserves_reports_and_is_idempotent(self):
        update = self.report()
        with self.store.connect() as db:
            report = dict(db.execute("SELECT * FROM work_updates WHERE id=?", (update,)).fetchone())
            del report["capability_gap"]
            for table in ("capability_gap_reviews", "capability_gap_occurrences", "capability_gaps"):
                db.execute("DROP TABLE " + table)
            db.execute("ALTER TABLE work_updates DROP COLUMN capability_gap")
            db.execute("PRAGMA user_version=17")
        Store(self.directory).upgrade()
        with self.store.connect() as db:
            self.assertEqual(SCHEMA_VERSION, db.execute("PRAGMA user_version").fetchone()[0])
            upgraded = dict(db.execute("SELECT * FROM work_updates WHERE id=?", (update,)).fetchone())
            self.assertIsNone(upgraded.pop("capability_gap"))
            self.assertEqual(report, upgraded)
        self.assertEqual([], list_gaps(self.view)["gaps"])

    def test_read_only_missing_or_old_state_does_not_create_or_upgrade(self):
        absent = State(self.root / "absent")
        with self.assertRaisesRegex(ValueError, "initialize or upgrade"):
            list_gaps(absent)
        self.assertFalse(absent.directory.exists())
        old = self.root / "old"
        old.mkdir()
        with sqlite3.connect(old / "live.sqlite3") as db:
            db.execute("CREATE TABLE retained(value TEXT)")
            db.execute("PRAGMA user_version=17")
        for inspect in (lambda: list_gaps(State(old)), lambda: show_gap(State(old), 1)):
            with self.assertRaisesRegex(ValueError, "initialize or upgrade"):
                inspect()
        with sqlite3.connect(old / "live.sqlite3") as db:
            self.assertEqual(17, db.execute("PRAGMA user_version").fetchone()[0])
            self.assertEqual([("retained",)], db.execute("SELECT name FROM sqlite_master").fetchall())

    def test_redaction_before_truncation_and_on_new_metadata_and_reviews(self):
        secret = "fixture-private-secret-value"
        with patch("devtools.credentials.configured_values", return_value=(secret,)):
            self.report(capability="check " + secret, text="x" * 2990 + secret + "tail")
            ident = list_gaps(self.view)["gaps"][0]["id"]
            review_gap(self.store, ident, 1, "resolved", "discovery", "note " + secret,
                       evidence="evidence " + secret, owner="owner " + secret)
            result = show_gap(self.view, ident)
            self.assertNotIn(secret[:10], canonical(result))
            self.assertTrue(result["occurrences"][0]["text_truncated"])
            self.assertEqual("redacted", result["gap"]["capture"]["capture"])
            with self.store.connect() as db:
                self.assertNotIn(secret, canonical(dict(db.execute("SELECT * FROM capability_gaps").fetchone())))
                self.assertNotIn(secret, canonical(dict(db.execute("SELECT * FROM capability_gap_reviews").fetchone())))
                self.assertNotIn(secret, db.execute("SELECT capability_gap FROM work_updates").fetchone()[0])

    def test_suppressed_capture_preserves_uncertainty_without_merging_labels(self):
        with patch("devtools.credentials.configured_values", side_effect=ValueError("filter unavailable")):
            self.report(capability="first capability")
            self.report("next", capability="second capability")
            self.assertEqual("suppressed", list_gaps(self.view)["capture"])
        gaps = list_gaps(self.view)["gaps"]
        self.assertEqual(2, len(gaps))
        self.assertEqual(["[capture suppressed]"] * 2, [row["capability"] for row in gaps])
        self.assertEqual(["suppressed"] * 2, [row["capture"]["capture"] for row in gaps])

    def test_operator_assessment_commits_with_explicit_suppressed_evidence(self):
        self.report()
        ident = list_gaps(self.view)["gaps"][0]["id"]
        with patch("devtools.credentials.configured_values", side_effect=ValueError("filter unavailable")):
            result = review_gap(self.store, ident, 1, "resolved", "discovery", "Secret operator note",
                                evidence="Secret operator evidence")
            self.assertEqual("suppressed", result["capture"])
        detail = show_gap(self.view, ident)
        self.assertEqual("resolved", detail["gap"]["status"])
        self.assertEqual("[capture suppressed]", detail["reviews"][0]["evidence"])
        self.assertEqual("suppressed", detail["reviews"][0]["capture"]["capture"])
        self.assertNotIn("Secret operator", canonical(detail))

    def test_occurrence_retains_report_role_and_binding_revision(self):
        update = self.report()
        with self.store.connect() as db:
            db.execute("UPDATE work_updates SET binding=? WHERE id=?", (canonical({"role": "coder"}), update))
        occurrence = show_gap(self.view, list_gaps(self.view)["gaps"][0]["id"])["occurrences"][0]
        self.assertEqual(("coder", 1), (occurrence["role"], occurrence["binding_revision"]))

    def test_direct_validation_rejects_malformed_and_unbounded_input(self):
        valid = {"capability": " checks ", "category": "unknown", "occurrence_key": " next "}
        self.assertEqual("checks", validate_gap(valid)["capability"])
        for field, value in (("capability", " "), ("occurrence_key", "x" * 81), ("category", "grant"),
                             ("reference", "operation:0"), ("reference", "job:1"), ("extra", True)):
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_gap({**valid, field: value})
        for args in ({"limit": 101}, {"limit": True}, {"after": -1}, {"status": "obsolete"}):
            with self.assertRaises(ValueError):
                list_gaps(self.view, **args)


if __name__ == "__main__":
    unittest.main()
