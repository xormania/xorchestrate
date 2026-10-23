"""Reports commit only under the binding that authorized the request."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from support import initialize

from devtools.api import API
from devtools.assignments import snapshot
from devtools.commands import Commands
from devtools.store import SCHEMA_VERSION, Store


class ReportBindingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        workspace = root / "workspace"
        workspace.mkdir()
        state = root / "state"
        initialize(state, workspace, ["owner:external", "xo:external", "coder:external"])
        self.operator = API(state, "owner", operator=True)
        self.store = self.operator.store
        self.order = self.operator.work.work_order_create(
            title="Report custody", objective="Retain current work", done_when="No stale messages"
        )["work_order"]["id"]
        self.jobs = [self.operator.work.job_create(
            title=title, objective=title, done_when="Evidence retained", work_order=self.order
        )["job"]["id"] for title in ("First", "Second")]
        self.operator.control.bind("owner", "co", self.order)
        self.operator.control.bind("xo", "xo", self.order, reports_to="owner")
        self.rebind(self.jobs[0])
        self.coder = API(state, "coder")

    def rebind(self, job):
        self.operator.control.bind("coder", "coder", self.order, job=job, reports_to="xo")

    def assert_no_publication(self):
        with self.store.connect() as db:
            self.assertEqual(0, db.execute("SELECT count(*) FROM work_updates").fetchone()[0])
            self.assertEqual(0, db.execute("SELECT count(*) FROM messages").fetchone()[0])
            self.assertEqual(0, db.execute("SELECT count(*) FROM work_links WHERE artifact IS NOT NULL").fetchone()[0])

    def test_rebind_after_request_capture_rejects_result_and_guidance(self):
        for tool in ("work_report", "guidance_request"):
            with self.subTest(tool=tool):
                self.rebind(self.jobs[0])
                with self.assertRaisesRegex(ValueError, "changed during this report"):
                    self.coder.call(tool, {"text": "Stale first-job report"},
                                    on_context=lambda: self.rebind(self.jobs[1]))
                self.assert_no_publication()

    def test_rebind_while_saving_evidence_does_not_publish_stale_artifact(self):
        original = self.coder.control.artifacts.save

        def save_then_rebind(**kwargs):
            retained = original(**kwargs)
            self.rebind(self.jobs[1])
            return retained

        with patch.object(self.coder.control.artifacts, "save", side_effect=save_then_rebind):
            with self.assertRaisesRegex(ValueError, "changed during this report"):
                self.coder.call("work_report", {"text": "First-job result", "kind": "result",
                                               "evidence": {"name": "proof.txt", "content": "First-job proof"}})
        self.assert_no_publication()
        with self.store.connect() as db:
            # The private bytes may be retained, but no work context gains access.
            self.assertEqual(1, db.execute("SELECT count(*) FROM artifacts").fetchone()[0])

    def test_revision_only_result_cannot_accept_rebound_work(self):
        coordinator = API(self.store.directory, "xo")
        job = coordinator.work.job_create(title="Acceptance", objective="Deliver current work",
                                          done_when="Current result retained", work_order=self.order)["job"]["id"]
        self.operator.control.unbind("coder")
        self.rebind(job)
        report = self.coder.call("work_report", {"text": "Old first-job result", "kind": "result"})
        reference = int(report["reference"].split(":")[1])
        with self.store.connect() as db:
            original_revision = db.execute("SELECT binding_revision FROM work_updates WHERE id=?", (reference,)).fetchone()[0]
            # Historical reports predate full binding snapshots. Their numeric
            # revision can repeat after a slot is unbound and appointed again.
            db.execute("UPDATE work_updates SET binding=NULL WHERE id=?", (reference,))
        self.operator.control.unbind("coder")
        self.rebind(job)
        current_revision = self.coder.control.snapshot()["revision"]
        self.assertGreater(current_revision, original_revision)
        with self.store.connect() as db:
            # Independently model historical numeric collisions; a missing full
            # snapshot must remain inadmissible even if its number matches.
            db.execute("UPDATE work_updates SET binding_revision=? WHERE id=?", (current_revision, reference))
        arguments = {"action": "accept", "work": "Acceptance", "direction": "Accept the current checked result"}
        with self.assertRaisesRegex(ValueError, "current retained result"):
            coordinator.call("steer", arguments)
        with self.store.connect() as db:
            self.assertEqual("open", db.execute("SELECT status FROM jobs WHERE id=?", (job,)).fetchone()[0])
            self.assertEqual(0, db.execute("SELECT count(*) FROM deliveries").fetchone()[0])
            self.assertIsNotNone(db.execute("SELECT 1 FROM work_updates WHERE id=?", (reference,)).fetchone())
        self.coder.call("work_report", {"text": "Fresh first-job result", "kind": "result"})
        self.assertEqual("done", coordinator.call("steer", arguments)["outcome"])

    def test_same_clock_reappointment_cannot_reuse_a_full_result_snapshot(self):
        coordinator = API(self.store.directory, "xo")
        clock = "2026-10-02T08:00:00.000+00:00"
        for touched in (False, True):
            with self.subTest(touched=touched):
                title = "Fixed clock touched" if touched else "Fixed clock"
                job = coordinator.work.job_create(title=title, objective="Deliver current work",
                                                  done_when="Current result retained", work_order=self.order)["job"]["id"]
                with patch("devtools.control.stamp", return_value=clock), patch("devtools.commands.stamp", return_value=clock):
                    self.operator.control.unbind("coder")
                    self.rebind(job)
                    if touched:
                        with self.store.connect() as db:
                            db.execute("BEGIN IMMEDIATE")
                            Commands._touch(db, "coder")
                    before = snapshot(self.coder.control.snapshot())
                    self.coder.call("work_report", {"text": "Old result", "kind": "result"})
                    self.operator.control.unbind("coder")
                    restarted = API(self.store.directory, "owner", operator=True)
                    restarted.control.bind("coder", "coder", self.order, job=job, reports_to="xo")
                after = snapshot(self.coder.control.snapshot())
                self.assertEqual(before["updated"], after["updated"])
                arguments = {"action": "accept", "work": title, "direction": "Accept current result"}
                with self.assertRaisesRegex(ValueError, "current retained result"):
                    coordinator.call("steer", arguments)
                self.assertGreater(after["revision"], before["revision"])
                self.coder.call("work_report", {"text": "Fresh result", "kind": "result"})
                self.assertEqual("done", coordinator.call("steer", arguments)["outcome"])

    def test_v14_upgrade_preserves_active_proof_and_retires_unbound_history(self):
        coordinator = API(self.store.directory, "xo")
        for active in (True, False):
            with self.subTest(active=active), patch("devtools.control.stamp", return_value="2026-10-02T08:00:00.000+00:00"):
                title = "Upgrade active" if active else "Upgrade unbound"
                job = coordinator.work.job_create(title=title, objective="Preserve proof identity",
                                                  done_when="Current result retained", work_order=self.order)["job"]["id"]
                self.operator.control.unbind("coder")
                self.rebind(job)
                before = self.coder.control.snapshot()
                self.coder.call("work_report", {"text": "Pre-upgrade result", "kind": "result"})
                if not active:
                    self.operator.control.unbind("coder")
                with self.store.connect() as db:
                    db.execute("ALTER TABLE members DROP COLUMN binding_revision")
                    db.execute("PRAGMA user_version=14")
                upgraded = Store(self.store.directory)
                with upgraded.connect() as db:
                    self.assertEqual(SCHEMA_VERSION, db.execute("PRAGMA user_version").fetchone()[0])
                    self.assertEqual(before["revision"], db.execute(
                        "SELECT binding_revision FROM members WHERE name='coder'").fetchone()[0])
                arguments = {"action": "accept", "work": title, "direction": "Accept current result"}
                if active:
                    self.assertEqual(before, self.coder.control.snapshot())
                else:
                    self.rebind(job)
                    self.assertGreater(self.coder.control.snapshot()["revision"], before["revision"])
                    with self.assertRaisesRegex(ValueError, "current retained result"):
                        coordinator.call("steer", arguments)
                    self.coder.call("work_report", {"text": "Post-upgrade result", "kind": "result"})
                self.assertEqual("done", coordinator.call("steer", arguments)["outcome"])

    def test_same_binding_retry_reuses_report_but_stale_retry_is_rejected(self):
        args = {"text": "Current progress"}
        first = self.coder.call("work_report", args)
        second = self.coder.call("work_report", args)
        self.assertEqual(first["reference"], second["reference"])
        self.assertTrue(second["reused"])
        with self.assertRaisesRegex(ValueError, "changed during this report"):
            self.coder.call("work_report", args, on_context=lambda: self.rebind(self.jobs[1]))
        with self.store.connect() as db:
            self.assertEqual(1, db.execute("SELECT count(*) FROM work_updates").fetchone()[0])
            self.assertEqual(1, db.execute("SELECT count(*) FROM messages").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
