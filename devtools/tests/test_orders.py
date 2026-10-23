import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from fixtures.delivery import DeliveryFixture
from support import Client, adopt_plan, fixture_work, held_check, until

from devtools.api import API
from devtools.github import GitHub, short_pr
from devtools.retirement import Retirement, lease
from devtools.store import Store, canonical, stamp
from devtools.workspaces import git


class OrderTests(unittest.TestCase):
    setUp_base = DeliveryFixture.setUp_base
    prepared = DeliveryFixture.prepared
    auditor = DeliveryFixture.auditor

    def setUp(self):
        DeliveryFixture.setUp(self)
        self.co = API(self.state, "co")
        self.integration = self.xo.workspaces.ensure()
        self.initial = git(self.remote, "rev-parse", "dev").stdout.decode().strip()
        self.cleanup = Retirement(self.store, "co")
        test = self

        class Provider:
            pr = None
            ci = "passed"
            lose_merge_reply = False

            def __init__(self, store, member):
                self.real = GitHub(store, member)

            def _context(self, **kwargs):
                return self.real._context(**kwargs)

            def _pr(self, context):
                return Provider.pr

            def pr_open(self, context, base, **kwargs):
                test.assertIsNone(context["job"])
                test.assertEqual("dev", base)
                if Provider.pr is None:
                    Provider.pr = {
                        "number": 2,
                        "html_url": "https://github.com/example/project/pull/2",
                        "title": "Integrated result",
                        "state": "open",
                        "merged": False,
                        "head": {
                            "ref": context["work"]["branch"],
                            "sha": git(test.remote, "rev-parse", context["work"]["branch"]).stdout.decode().strip(),
                        },
                        "base": {"ref": base, "sha": test.initial},
                    }
                self.real._bind(context, Provider.pr)
                return {"pr": short_pr(Provider.pr)}

            def ci_watch(self, context, **kwargs):
                return {"state": "watching", "revision": Provider.pr["head"]["sha"]}

            def pr_get(self, context):
                return {"pr": short_pr(Provider.pr), "ci": {"state": Provider.ci}}

            def pr_diff(self, context, **kwargs):
                return {"files": [{"filename": "src/Example.php"}]}

            def pr_merge(self, context, revision):
                GitHub._revision(Provider.pr, revision)
                if not Provider.pr["merged"]:
                    git(test.remote, "update-ref", "refs/heads/dev", revision)
                    git(test.remote, "update-ref", "-d", "refs/heads/" + test.integration["branch"])
                    Provider.pr.update(merged=True, state="closed", merge_commit_sha=revision)
                if Provider.lose_merge_reply:
                    Provider.lose_merge_reply = False
                    raise ConnectionError("Merge succeeded but reply was lost")
                return {"merged": True, "commit": revision}

        self.order_provider = Provider
        mock = patch("devtools.orders.GitHub", Provider)
        mock.start()
        self.addCleanup(mock.stop)
        # GitHub wire behavior is exercised by test_codex_reviews; this fixture
        # isolates work-order integration, inspected-head checks and cleanup.
        reviews = patch("devtools.orders.CodexReviews")
        self.reviews = reviews.start().return_value
        self.reviews.request.return_value = {"state": "requested"}
        self.reviews.status.return_value = {"state": "ready"}
        self.addCleanup(reviews.stop)

    def accept(self):
        self.xo.call("monitor", {"work": "Feature"})
        return self.xo.call("steer", {"action": "accept", "work": "Feature", "direction": "Verified contribution"})

    def retry_cleanup(self):
        with self.store.connect() as db:
            db.execute("UPDATE managed_worktrees SET retry_after=0")
        self.cleanup.tick()

    def test_job_integrates_before_final_pr_and_cleanup_keeps_evidence(self):
        adopt_plan(self.xo, [{"name": "Feature", "objective": "Feature", "done_when": "Works", "constraints": "Product only"}])
        self.xo.call("steer", {"action": "resume", "work": "Feature"})
        result = self.api.call("work_report", {"kind": "result", "text": "Checked implementation is submitted"})
        self.xo.call("steer", {"action": "complete", "work": "Feature", "assignment": "Feature", "direction": "Implementation checked"})
        self.assertIsNone(self.api.control.snapshot())
        self.assertEqual(result["reference"], self.xo.call("monitor", {})["assignments"][-1]["result"])
        self.assertEqual(self.checkout.parent / "wt-ag", self.workspace.parent)
        self.assertEqual(self.integration["branch"], self.managed["target"])
        self.assertEqual(self.initial, self.integration["base_revision"])
        with self.assertRaisesRegex(ValueError, "unfinished"):
            self.xo.call("work_submit", {"summary": "Too soon"})
        self.accept()
        self.assertEqual(self.initial, git(self.remote, "rev-parse", "dev").stdout.decode().strip())
        self.assertEqual(self.head, git(self.remote, "rev-parse", self.integration["branch"]).stdout.decode().strip())
        self.cleanup.tick()
        self.assertFalse(self.workspace.exists())
        self.assertTrue(Path(self.integration["root"]).exists())
        self.assertNotEqual(
            0,
            git(self.checkout, "show-ref", "--verify", "refs/heads/" + self.managed["branch"], check=False).returncode,
        )
        result = self.xo.call("work_submit", {"summary": "The complete outcome"})
        self.assertEqual("submitted", result["state"])
        with self.assertRaisesRegex(ValueError, "Monitor"):
            self.xo.call("conclude", {"assessment": "Ready"})
        self.xo.call("monitor", {"section": "review"})
        self.order_provider.ci = "pending"
        with self.assertRaisesRegex(ValueError, "CI"):
            self.xo.call("conclude", {"assessment": "Ready"})
        self.order_provider.ci = "passed"
        self.reviews.require_ready.side_effect = ValueError("Both Codex reviews must complete")
        with self.assertRaisesRegex(ValueError, "Codex"):
            self.xo.call("conclude", {"assessment": "Ready"})
        self.assertEqual(self.initial, git(self.remote, "rev-parse", "dev").stdout.decode().strip())
        self.reviews.require_ready.side_effect = None
        self.xo.call("conclude", {"assessment": "Combined outcome verified"})
        self.reviews.request.assert_called_once()
        self.assertEqual(self.head, self.reviews.require_ready.call_args.args[1])
        self.retry_cleanup()
        self.assertEqual(self.head, git(self.remote, "rev-parse", "dev").stdout.decode().strip())
        self.assertFalse(Path(self.integration["root"]).exists())
        self.assertTrue(self.checkout.exists())
        with self.store.connect() as db:
            self.assertGreater(db.execute("SELECT count(*) FROM artifacts").fetchone()[0], 0)
            self.assertEqual(1, db.execute("SELECT count(*) FROM deliveries").fetchone()[0])
            self.assertEqual(1, db.execute("SELECT count(*) FROM order_deliveries").fetchone()[0])
            self.assertEqual({"removed"}, {r[0] for r in db.execute("SELECT state FROM managed_worktrees")})

    def test_lost_merge_response_reconciles_after_remote_branch_deletion(self):
        self.accept()
        self.xo.call("work_submit", {"summary": "Integrated result"})
        self.xo.call("monitor", {"section": "review"})
        self.order_provider.lose_merge_reply = True
        with self.assertRaises(ConnectionError):
            self.xo.call("conclude", {"assessment": "Ready"})
        self.reviews.require_ready.side_effect = ValueError("Both Codex reviews must complete")
        with self.assertRaisesRegex(ValueError, "Codex"):
            self.xo.call("conclude", {"assessment": "Ready"})
        with self.store.connect() as db:
            self.assertEqual(0, db.execute("SELECT count(*) FROM order_deliveries").fetchone()[0])
        self.reviews.require_ready.side_effect = None
        self.assertEqual("done", self.xo.call("conclude", {"assessment": "Ready"})["outcome"])

    def test_final_cleanup_preserves_running_co_interface_and_completed_context(self):
        self.accept()
        self.xo.call("work_submit", {"summary": "Integrated result"})
        self.xo.call("monitor", {"section": "review"})
        self.store.session("co", state="active", desired="running", pid=123)
        before = self.store.member("co")
        self.xo.call("conclude", {"assessment": "Combined outcome verified"})
        with self.store.connect() as db:
            sessions = json.loads(db.execute("SELECT sessions FROM managed_worktrees WHERE job IS NULL").fetchone()[0])
        self.assertNotIn("co", sessions)
        self.cleanup.tick()
        self.assertFalse(Path(self.integration["root"]).exists())
        self.assertEqual(before, self.store.member("co"))
        self.assertEqual("done", self.co.call("work_context", {})["work"]["status"])
        self.assertIn("Combined outcome verified", json.dumps(self.co.call("work_context", {"section": "messages"})))
        self.assertIsNone(self.xo.control.snapshot())

    def test_planner_can_inspect_integrated_work_without_acceptance_authority(self):
        self.accept()
        self.xo.call("work_submit", {"summary": "Integrated result"})
        self.xo.call("orchestrate", {"work": [{"name": "Follow-up planning", "role": "planner",
                                              "objective": "Inspect the integrated result",
                                              "done_when": "A follow-up plan is reported", "constraints": "Read only"}]})
        with self.store.connect() as db:
            member = db.execute("SELECT member FROM member_bindings WHERE role='planner'").fetchone()[0]
        planner = API(self.state, member)
        result = planner.call("work_inspect", {"section": "review"})
        self.assertEqual(self.head, result["pr"]["revision"])
        with self.assertRaisesRegex(ValueError, "not available"):
            planner.call("conclude", {"assessment": "Accept it"})
        with self.assertRaises(ValueError):
            planner.commands.conclude("Accept it")
        with self.store.connect() as db:
            self.assertEqual(0, db.execute("SELECT count(*) FROM order_deliveries").fetchone()[0])

    def test_review_fix_updates_same_job_pr_before_both_merge_stages(self):
        branch = self.managed["branch"]
        self.assertTrue(branch.startswith(self.integration["branch"] + "-job-"))
        self.assertEqual(1, branch.count("/"))
        self.xo.call("monitor", {"work": "Feature"})
        (self.workspace / "src/Example.php").write_text("Implementation with review fix\n")

        def reuse_pr(context, base, **kwargs):
            self.assertEqual(self.job, context["job"])
            self.assertEqual(self.integration["branch"], base)
            revision = git(self.remote, "rev-parse", branch).stdout.decode().strip()
            self.provider.pr["head"]["sha"] = revision
            self.provider.ci = "pending"
            return {"pr": short_pr(self.provider.pr), "reused": True}

        with (
            patch("devtools.github.GitHub", return_value=self.provider),
            patch.object(self.provider, "pr_open", side_effect=reuse_pr, create=True) as opened,
            patch.object(self.provider, "ci_watch", return_value={"state": "watching"}, create=True),
        ):
            submitted = self.api.call("work_submit", {"summary": "Address review finding"})
            self.api.call("work_submit", {"summary": "Confirm submitted fix"})
        revised = submitted["checkpoint"].split(":")[1]
        self.assertNotEqual(self.head, revised)
        self.assertEqual(self.provider.pr["html_url"], submitted["pr"])
        self.assertEqual(2, opened.call_count)
        self.assertEqual(branch, self.api.workspaces.ensure()["branch"])
        self.assertEqual(0, git(self.workspace, "merge-base", "--is-ancestor", self.head, revised).returncode)
        self.assertEqual(self.initial, git(self.remote, "rev-parse", self.integration["branch"]).stdout.decode().strip())
        self.assertEqual(self.initial, git(self.remote, "rev-parse", "dev").stdout.decode().strip())

        args = {"action": "accept", "work": "Feature", "direction": "Reviewed fix"}
        with self.assertRaisesRegex(ValueError, "changed after inspection"):
            self.xo.call("steer", args)
        self.xo.call("monitor", {"work": "Feature"})
        with self.assertRaisesRegex(ValueError, "CI has not passed"):
            self.xo.call("steer", args)
        self.assertEqual([], self.provider.merges)
        self.provider.ci = "passed"
        self.xo.call("steer", args)
        self.assertEqual(revised, git(self.remote, "rev-parse", self.integration["branch"]).stdout.decode().strip())
        self.assertEqual(self.initial, git(self.remote, "rev-parse", "dev").stdout.decode().strip())

        self.xo.call("work_submit", {"summary": "Integrated reviewed contribution"})
        self.xo.call("monitor", {"section": "review"})
        self.xo.call("conclude", {"assessment": "Combined result verified"})
        self.cleanup.tick()
        self.assertEqual(revised, git(self.remote, "rev-parse", "dev").stdout.decode().strip())
        self.assertFalse(self.workspace.exists())
        self.assertFalse(Path(self.integration["root"]).exists())

    def test_final_delivery_rejects_changed_target_revision_and_dirty_integration(self):
        self.accept()
        self.xo.call("work_submit", {"summary": "Integrated result"})
        self.xo.call("monitor", {"section": "review"})
        self.order_provider.pr["base"]["ref"] = "master"
        with self.assertRaisesRegex(ValueError, "target"):
            self.xo.call("conclude", {"assessment": "Ready"})
        self.order_provider.pr["base"]["ref"] = "dev"
        self.order_provider.pr["head"]["sha"] = "a" * 40
        with self.assertRaisesRegex(ValueError, "Monitor"):
            self.xo.call("conclude", {"assessment": "Ready"})
        self.order_provider.pr["head"]["sha"] = self.head
        (Path(self.integration["root"]) / "unexpected.txt").write_text("preserve")
        with self.assertRaisesRegex(ValueError, "unexpected"):
            self.xo.call("conclude", {"assessment": "Ready"})
        git(self.integration["root"], "add", "unexpected.txt")
        git(self.integration["root"], "commit", "-m", "Unexpected local commit")
        with self.assertRaisesRegex(ValueError, "unexpected local commits"):
            self.xo.call("conclude", {"assessment": "Ready"})
        self.assertEqual(self.initial, git(self.remote, "rev-parse", "dev").stdout.decode().strip())

    def test_cleanup_waits_for_processes_providers_and_lease_and_preserves_untracked(self):
        self.store.session("coder", pid=123, generation=None)
        self.accept()
        self.cleanup.tick()
        self.assertTrue(self.workspace.exists())
        self.store.session("coder", pid=None)
        with lease(self.store, self.workspace):
            self.retry_cleanup()
            self.assertTrue(self.workspace.exists())
        with self.store.connect() as db:
            db.execute("UPDATE managed_worktrees SET provider=1 WHERE root=?", (str(self.workspace),))
        with patch(
            "devtools.retirement.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, b"container-id\n", b""),
        ):
            self.retry_cleanup()
        self.assertTrue(self.workspace.exists())
        with self.store.connect() as db:
            db.execute("UPDATE managed_worktrees SET provider=0 WHERE root=?", (str(self.workspace),))
        (self.workspace / "unexpected.txt").write_text("preserve")
        self.retry_cleanup()
        self.assertTrue((self.workspace / "unexpected.txt").exists())
        (self.workspace / "unexpected.txt").unlink()
        self.retry_cleanup()
        self.assertFalse(self.workspace.exists())

    def test_cleanup_keeps_cancelled_work_and_unexpected_commits(self):
        self.accept()
        (self.workspace / "src/Example.php").write_text("More work\n")
        git(self.workspace, "commit", "-am", "Unexpected later commit")
        self.cleanup.tick()
        self.assertTrue(self.workspace.exists())
        self.assertIn("commits", self.xo.call("monitor", {})["cleanup"][0]["detail"])
        with self.store.connect() as db:
            db.execute("UPDATE deliveries SET merge_commit=NULL,outcome='cancelled'")
        self.retry_cleanup()
        self.assertTrue(self.workspace.exists())

    def test_all_refreshed_audit_snapshots_are_retired(self):
        audit = self.auditor()
        audit.call("work_inspect", {"section": "review"})
        first = Path(audit.workspaces.ensure()["root"])
        (self.workspace / "src/Example.php").write_text("Revised implementation\n")
        self.api.call("work_save", {"note": "Revise"})
        git(self.workspace, "push", "origin", self.managed["branch"])
        self.provider.pr["head"]["sha"] = git(self.workspace, "rev-parse", "HEAD").stdout.decode().strip()
        audit.call("work_inspect", {"section": "review"})
        second = Path(audit.workspaces.ensure()["root"])
        self.assertNotEqual(first, second)
        self.accept()
        self.cleanup.tick()
        self.assertFalse(first.exists())
        self.assertFalse(second.exists())

    def test_configured_workspace_parent_is_preserved(self):
        config = json.loads((self.state / "team.json").read_text())
        config["project"]["workspaces"] = str(self.root / "custom")
        (self.state / "team.json").write_text(json.dumps(config))
        self.assertEqual(self.workspace, Path(self.api.workspaces.ensure()["root"]))
        self.assertEqual(self.root / "custom", self.api.workspaces.parent())

    def test_order_checks_survive_disconnect_and_migration_preserves_job_operations(self):
        # Exercise a real v7 NOT NULL operation table with retained job history.
        with self.store.connect() as db:
            schema = db.execute("SELECT sql FROM sqlite_master WHERE name='operations'").fetchone()[0]
            db.execute("ALTER TABLE operations RENAME TO previous_operations")
            db.execute(schema.replace("job INTEGER REFERENCES", "job INTEGER NOT NULL REFERENCES"))
            db.execute("DROP TABLE previous_operations")
            db.execute(
                "INSERT INTO operations(id,member,work_order,job,binding,action,arguments,state,result,created,updated) "
                "VALUES (77,'coder',?,?,?,'work_check','{}','completed','{}',?,?)",
                (self.order, self.job, canonical(self.api.control.snapshot()), stamp(), stamp()),
            )
            db.execute("PRAGMA user_version=7")
        Store(self.state)
        with self.store.connect() as db:
            self.assertEqual(self.job, db.execute("SELECT job FROM operations WHERE id=77").fetchone()[0])
        for name in ("composer.json", "composer.lock"):
            (self.workspace / name).write_text("{}")
        self.api.call("work_save", {"note": "Add fixture dependencies"})
        self.provider.pr["head"]["sha"] = git(self.workspace, "rev-parse", "HEAD").stdout.decode().strip()
        git(self.workspace, "push", "origin", self.managed["branch"])
        self.accept()
        self.xo.call("work_refresh", {})
        root = Path(self.integration["root"])
        (root / "vendor").mkdir()
        (root / "vendor/autoload.php").write_text("<?php")
        import hashlib

        (root / "vendor/.agentscient-dependencies").write_text(hashlib.sha256(b"{}{}").hexdigest())
        config = json.loads((self.state / "team.json").read_text())
        argv, started, release = held_check(self.root)
        config["project"]["development"] = {"checks": {"combined": argv}}
        (self.state / "team.json").write_text(json.dumps(config))
        client = Client(self.state, "xo")
        try:
            receipt = client.tool("work_check", {})
            self.assertIn(receipt["state"], {"queued", "running"})
            until(started.exists)
            client.close()
            self.assertEqual("running", self.xo.operations.inspect()["operations"][0]["state"])
        finally:
            release.touch()
            client.close()
        result = until(
            lambda: next((r for r in self.xo.operations.inspect()["operations"] if r["state"] == "completed"), None)
        )
        result = json.loads(self.xo.call("work_context", {"reference": result["reference"]})["content"])
        self.assertTrue(result["result"]["passed"])
        with self.store.connect() as db:
            operation = db.execute("SELECT job,work_order FROM operations WHERE id>77").fetchone()
            self.assertIsNone(operation["job"])
            self.assertEqual(self.order, operation["work_order"])

    def test_squash_merge_cleanup_and_followup_start_from_integrated_commit(self):
        root = self.integration["root"]
        git(root, "merge", "--squash", self.head)
        git(root, "commit", "-m", "Squashed contribution")
        squashed = git(root, "rev-parse", "HEAD").stdout.decode().strip()

        def merge(context, revision):
            git(root, "push", "origin", self.integration["branch"])
            self.provider.pr.update(merged=True, state="closed", merge_commit_sha=squashed)
            return {"merged": True, "commit": squashed}

        with patch.object(self.provider, "pr_merge", side_effect=merge):
            self.accept()
        self.cleanup.tick()
        self.assertFalse(self.workspace.exists())
        fixture_work(self.xo, [{"name": "Next", "role": "coder", "objective": "Continue", "done_when": "Done", "constraints": "Product only"}])
        new_root = self.api.workspaces.ensure()["root"]
        self.assertEqual(squashed, git(new_root, "rev-parse", "HEAD").stdout.decode().strip())
        self.assertNotEqual(self.head, squashed)


if __name__ == "__main__":
    unittest.main()
