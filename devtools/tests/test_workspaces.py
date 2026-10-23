import json
import os
import unittest
from unittest.mock import patch

from fixtures.workspaces import WorkspaceFixture
from support import Client

from devtools.api import API
from devtools.workspaces import git


class WorkspaceTests(WorkspaceFixture, unittest.TestCase):


    def test_co_conversation_uses_the_stable_checkout_without_creating_an_order_worktree(self):
        co = API(self.state, "co")
        workspace = co.workspaces.ensure()
        self.assertEqual(str(self.checkout), workspace["root"])
        with self.store.connect() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM order_workspaces").fetchone())
        with self.store.connect() as db:
            db.execute("UPDATE work_orders SET status='done' WHERE id=?", (co.control.snapshot()["work_order"],))
        self.assertEqual(str(self.checkout), co.workspaces.ensure()["root"])
        git(self.checkout, "checkout", "--detach", "HEAD")
        detached = co.workspaces.ensure()
        self.assertEqual(str(self.checkout), detached["root"])
        self.assertIsNone(detached["branch"])

    def test_job_prepares_owned_worktree_and_normal_mcp_checkpoint(self):
        client = Client(self.state, "coder")
        self.addCleanup(client.close)
        context = client.tool("work_inspect", {})
        workspace = self.prepared()
        self.assertNotEqual(workspace, self.checkout)
        self.assertEqual("prepared", context["workspace"])
        (workspace / "src/Example.php").write_text("changed\n")
        saved = client.tool("work_save", {"note": "Implement the feature"})
        self.assertEqual("original\n", (self.checkout / "src/Example.php").read_text())
        self.assertEqual([], self.api.call("work_inspect", {})["changes"])
        self.assertFalse(client.tool("work_save", {"note": "Implement the feature"})["changed"])
        evidence = client.tool("work_context", {"reference": saved["evidence"]})
        self.assertIn("changed", evidence["content"])
        self.assertNotIn(str(self.root), json.dumps(context))

    def test_role_pause_and_protected_changes_are_checked_before_commit(self):
        workspace = self.prepared()
        (workspace / "devtools/protected.py").write_text("unauthorized\n")
        before = git(workspace, "rev-parse", "HEAD").stdout
        with self.assertRaisesRegex(ValueError, "outside this role"):
            self.api.call("work_save", {"note": "Try a protected change"})
        self.assertEqual(before, git(workspace, "rev-parse", "HEAD").stdout)
        self.assertEqual("unauthorized\n", (workspace / "devtools/protected.py").read_text())
        API(self.state, "xo").call("steer", {"action": "pause", "work": "Feature"})
        with self.assertRaisesRegex(ValueError, "paused"):
            self.api.call("work_save", {"note": "Try while paused"})
        self.assertEqual("prepared", self.api.call("work_inspect", {})["workspace"])
        with self.api.workspaces.lock("job-" + str(self.api.control.snapshot()["job"])):
            with self.assertRaisesRegex(ValueError, "Another operation"):
                self.api.call("work_inspect", {})

    def test_recovery_preserves_history_without_rewriting_published_commits(self):
        workspace = self.prepared()
        first = self.api.call("work_save", {"note": "Initial checkpoint"})
        (workspace / "src/Example.php").write_text("second\n")
        second = self.api.call("work_save", {"note": "Second checkpoint"})
        (workspace / "src/Example.php").write_text("third\n")
        result = self.api.call(
            "work_recover", {"checkpoint": first["checkpoint"], "reason": "Restore original behavior"}
        )
        self.assertEqual("original\n", (workspace / "src/Example.php").read_text())
        ancestor = git(
            workspace, "merge-base", "--is-ancestor", second["checkpoint"].split(":")[1], "HEAD", check=False
        )
        self.assertEqual(0, ancestor.returncode)
        self.assertNotEqual(result["preserved"]["checkpoint"], result["restored"]["checkpoint"])
        with self.assertRaisesRegex(ValueError, "this job"):
            self.api.call("work_recover", {"checkpoint": "checkpoint:" + "a" * 40, "reason": "Foreign revision"})

    def test_refresh_fast_forward_and_conflict_preserve_job_work(self):
        workspace = self.prepared()
        (self.checkout / "README.md").write_text("Upstream documentation\n")
        git(self.checkout, "commit", "-am", "Upstream documentation")
        git(self.checkout, "push", "origin", "dev")
        self.assertEqual("refreshed", self.api.call("work_refresh", {})["state"])
        (workspace / "src/Example.php").write_text("local version\n")
        (self.checkout / "src/Example.php").write_text("remote version\n")
        (self.checkout / "README.md").write_text("More upstream documentation\n")
        git(self.checkout, "commit", "-am", "Upstream feature")
        git(self.checkout, "push", "origin", "dev")
        result = self.api.call("work_refresh", {})
        self.assertEqual("needs_resolution", result["state"])
        self.assertEqual(["src/Example.php"], result["conflicts"])
        contents = (workspace / "src/Example.php").read_text()
        self.assertIn("local version", contents)
        self.assertIn("remote version", contents)
        with self.assertRaisesRegex(ValueError, "Resolve conflict"):
            self.api.call("work_save", {"note": "Still unresolved"})
        (workspace / "src/Example.php").write_text("Resolved local and remote behavior\n")
        (workspace / "devtools/protected.py").write_text("unauthorized\n")
        with self.assertRaisesRegex(ValueError, "outside this role"):
            self.api.call("work_save", {"note": "Cannot smuggle edits with upstream changes"})
        git(workspace, "restore", "devtools/protected.py")
        (workspace / "README.md").write_text("Unauthorized replacement of an imported file\n")
        git(workspace, "add", "README.md")
        with self.assertRaisesRegex(ValueError, "outside this role"):
            self.api.call("work_save", {"note": "Staging an imported file cannot bypass role limits"})
        git(workspace, "restore", "--source=origin/dev", "--staged", "--worktree", "README.md")
        saved = self.api.call("work_save", {"note": "Resolve upstream integration"})
        self.assertTrue(saved["changed"])
        self.assertEqual("More upstream documentation\n", (workspace / "README.md").read_text())
        for ancestor in ("origin/dev", result["preserved"]["checkpoint"].split(":")[1]):
            self.assertEqual(0, git(workspace, "merge-base", "--is-ancestor", ancestor, "HEAD").returncode)
        self.assertEqual("refreshed", self.api.call("work_refresh", {})["state"])
        self.assertEqual([], self.api.call("work_inspect", {})["changes"])

    def test_submit_retries_reuse_checkpoint_and_bind_pr_with_git_publication(self):
        workspace = self.prepared()
        (workspace / "src/Example.php").write_text("Delivered\n")
        provider_calls = []

        class Provider:
            def __init__(self, store, member):
                pass

            def _context(self, **kwargs):
                return kwargs

            def pr_open(self, context, **kwargs):
                provider_calls.append(kwargs)
                return {
                    "pr": {
                        "revision": git(workspace, "rev-parse", "HEAD").stdout.decode().strip(),
                        "url": "https://github.com/example/project/pull/1",
                    }
                }

            def ci_watch(self, context):
                return {"state": "watching"}

        with patch("devtools.github.GitHub", Provider):
            first = self.api.call("work_submit", {"summary": "Deliver feature"})
            second = self.api.call("work_submit", {"summary": "Deliver feature"})
        self.assertEqual(first, second)
        branch = self.api.workspaces.ensure()["branch"]
        published = git(self.remote, "rev-parse", "refs/heads/" + branch).stdout.decode().strip()
        self.assertEqual(first["checkpoint"].split(":")[1], published)
        self.assertEqual(self.api.workspaces.ensure()["target"], provider_calls[0]["base"])
        self.assertTrue(provider_calls[0]["base"].startswith("work-order/"))
        self.assertTrue(self.store.inbox("xo")["messages"])

    def test_artifact_source_uses_bound_job_and_rejects_symlink_escape(self):
        workspace = self.prepared()
        (workspace / "src/Example.php").write_text("Job-specific evidence\n")
        evidence = self.api.artifacts.save(name="example.txt", source="src/Example.php")
        read = self.api.artifacts.read(evidence["artifact"]["id"])
        self.assertEqual("Job-specific evidence\n", read["content"])
        os.symlink(self.checkout / "README.md", workspace / "outside")
        with self.assertRaisesRegex(ValueError, "relative"):
            self.api.artifacts.save(name="outside.txt", source="outside")


if __name__ == "__main__":
    unittest.main()
