import os
import time
import unittest
from unittest.mock import patch

from fixtures.github import HEAD, MERGE, GitHubFixture
from support import require_process_identity, until

from devtools.github import GitHub
from devtools.github_http import GitHubError, GitHubHTTP
from devtools.store import SCHEMA_VERSION


class GitHubTests(GitHubFixture, unittest.TestCase):


    def test_mcp_pr_lifecycle_uses_work_context_and_inspected_revision(self):
        client = self.client()
        order, job = self.work(client)
        created = client.invoke("pr_open", summary="Adds agent tasks")
        self.assertEqual((order, job), (created["work_order"], created["job"]))
        self.assertFalse(created["reused"])
        self.assertTrue(client.invoke("pr_open")["reused"])
        self.assertIn("Work order: " + str(order), self.provider.pr["body"])
        self.assertEqual(self.provider.pr["html_url"], client.invoke("job_get")["job"]["pull_request"])
        observed = client.invoke("pr_get")
        self.assertEqual("failed", observed["ci"]["state"])
        self.assertEqual(MERGE, observed["ci"]["tested_revision"])
        self.assertIn("PR is a draft", observed["observed_blockers"])
        diff = client.invoke("pr_diff", path="devtools/example.py", revision=HEAD)
        self.assertIn("+new", diff["patch"])
        client.invoke("pr_update", draft=False, title="Useful GitHub tasks")
        self.assertFalse(self.provider.pr["draft"])
        client.invoke(
            "pr_review",
            revision=HEAD,
            decision="request_changes",
            body="Please address the failure",
            findings=[{"path": "devtools/example.py", "line": 1, "body": "Check this branch"}],
        )
        self.assertEqual(HEAD, self.provider.reviews[0]["commit_id"])
        client.invoke("pr_comment", body="Evidence is attached to the work order")
        self.assertEqual("Evidence is attached to the work order", client.invoke("pr_comments")["comments"][0]["body"])
        self.assertEqual(HEAD, client.invoke("pr_comments", kind="review")["comments"][0]["revision"])
        with self.assertRaisesRegex(ValueError, "head changed"):
            client.invoke("pr_merge", revision="c" * 40)
        with self.assertRaisesRegex(ValueError, "not ready to merge"):
            client.invoke("pr_merge", revision=HEAD)
        self.assertFalse(any(method == "PUT" for method, *_ in self.provider.requests))
        self.provider.review_decision = None
        self.provider.merge_state = "CLEAN"
        self.provider.checks[MERGE] = [self.provider.check("success")]
        self.provider.run["conclusion"] = "success"
        merged = client.invoke("pr_merge", revision=HEAD)
        self.assertEqual("merge", merged["method"])
        self.assertTrue(merged["merged"])
        self.assertEqual("open", client.invoke("work_order_get")["work_order"]["status"])
        self.assertTrue(client.invoke("pr_merge", revision=HEAD)["already_merged"])

    def test_review_fix_requires_current_checks_and_github_merge_eligibility(self):
        client = self.client()
        self.work(client)
        client.invoke("pr_open", draft=False)
        self.provider.review_decision = None
        self.provider.checks[MERGE] = [self.provider.check("success")]
        self.provider.run["conclusion"] = "success"
        revised, test_merge = "c" * 40, "d" * 40
        self.provider.pr["head"]["sha"] = revised
        self.provider.pr["merge_commit_sha"] = test_merge
        with self.assertRaisesRegex(ValueError, "head changed"):
            client.invoke("pr_merge", revision=HEAD)
        with self.assertRaisesRegex(ValueError, "Observed CI: unknown"):
            client.invoke("pr_merge", revision=revised)

        # Old successes do not cover the fix. Other new checks can pass before
        # required lane even appears; GitHub still reports the merge as blocked.
        self.provider.checks[test_merge] = [self.provider.check("success")]
        self.assertEqual("passed", client.invoke("ci_status")["state"])
        with self.assertRaisesRegex(ValueError, "GitHub merge state: BLOCKED"):
            client.invoke("pr_merge", revision=revised)
        lane = dict(self.provider.check(None), id=202, name="lane", status="in_progress")
        self.provider.checks[test_merge].append(lane)
        with self.assertRaisesRegex(ValueError, "Observed CI: pending"):
            client.invoke("pr_merge", revision=revised)
        lane.update(status="completed", conclusion="failure")
        with self.assertRaisesRegex(ValueError, "Observed CI: failed"):
            client.invoke("pr_merge", revision=revised)
        lane["conclusion"] = "success"
        with self.assertRaisesRegex(ValueError, "GitHub merge state: BLOCKED"):
            client.invoke("pr_merge", revision=revised)
        self.assertFalse(any(method == "PUT" for method, *_ in self.provider.requests))
        self.provider.merge_state = "CLEAN"
        self.assertTrue(client.invoke("pr_merge", revision=revised)["merged"])
        writes = [body for method, _, body, _ in self.provider.requests if method == "PUT"]
        self.assertEqual([{"sha": revised, "merge_method": "merge"}], writes)

    def test_review_threads_and_closed_input_boundary(self):
        client = self.client()
        self.work(client)
        client.invoke("pr_open")
        thread = client.invoke("pr_threads")["threads"][0]["id"]
        client.invoke("pr_threads", action="reply", thread=thread, body="Fixed in the next revision")
        self.assertTrue(client.invoke("pr_threads", action="resolve", thread=thread)["thread"]["thread"]["isResolved"])
        self.assertFalse(client.invoke("pr_threads", action="reopen", thread=thread)["thread"]["thread"]["isResolved"])
        with self.assertRaisesRegex(ValueError, "does not belong"):
            client.invoke("pr_threads", action="resolve", thread="WRONG_THREAD")
        for args in ({"endpoint": "/user"}, {"token": "secret"}, {"command": "gh api /user"}):
            with self.assertRaises(ValueError):
                client.invoke("pr_get", **args)
        with self.assertRaises(ValueError):
            client.invoke(
                "pr_review",
                revision=HEAD,
                decision="approve",
                body="Okay",
                findings=[{"path": "x", "line": "bad", "body": "y"}],
            )
        self.assertEqual(3, len(client.call("tools/list")["tools"]))

    def test_ci_diagnosis_control_and_redirect_credential_boundary(self):
        client = self.client()
        self.work(client)
        client.invoke("pr_open")
        diagnosis = client.invoke("ci_failures", run=10)
        failed = diagnosis["failures"][0]
        self.assertEqual("Run tests", failed["steps"][0]["name"])
        self.assertIn("assertion failed", failed["log_excerpt"])
        self.assertNotIn("fixture-credential", failed["log_excerpt"])
        self.assertIsNone(self.provider.assert_download_auth)
        self.assertEqual(12, failed["annotations"][0]["start_line"])
        self.provider.logs_missing = True
        unavailable = client.invoke("ci_failures", run=10)["failures"][0]
        self.assertIn("expired", unavailable["log_error"])
        self.assertEqual("Run tests", unavailable["steps"][0]["name"])
        self.assertEqual("CI", client.invoke("ci_workflows")["workflows"][0]["name"])
        self.assertTrue(client.invoke("ci_run", workflow="ci.yml", inputs={"suite": "storage"})["requested"])
        rerun = client.invoke("ci_rerun", run=10)
        self.assertEqual(HEAD, rerun["revision"])
        self.assertEqual("pending", client.invoke("ci_status", run=10)["state"])
        self.assertTrue(client.invoke("ci_cancel", run=10)["requested"])
        self.assertFalse(client.invoke("ci_cancel", run=10)["requested"])

    def test_ci_evidence_is_managed_and_retains_source(self):
        client = self.client()
        order, job = self.work(client)
        client.invoke("pr_open")
        self.assertEqual(90, client.invoke("ci_artifacts", run=10)["artifacts"][0]["id"])
        saved = client.invoke("ci_artifact_save", artifact=90)
        self.assertEqual((order, job), (saved["work_order"], saved["job"]))
        self.assertEqual(2, saved["entry_count"])
        self.assertEqual(HEAD, saved["source"]["revision"])
        self.assertEqual(10, saved["source"]["run"])
        self.assertTrue(client.invoke("ci_artifact_save", artifact=90)["reused"])
        self.provider.run["run_attempt"] = 2
        report = client.invoke("ci_artifact_save", artifact=90, entry="test-results.xml")
        self.assertEqual(2, report["source"]["observed_attempt"])
        self.assertIn("does not identify", report["source"]["note"])
        retained = client.invoke("artifact_read", artifact=report["artifact"]["id"])
        self.assertIn("tests='12'", retained["content"])
        self.assertEqual(90, retained["sources"][0]["source_artifact"])
        client.invoke("ci_artifact_save", artifact=90, entry="../evidence.txt")
        self.assertFalse((self.root / "evidence.txt").exists())
        self.provider.artifact["digest"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(ValueError, "digest"):
            client.invoke("ci_artifact_save", artifact=90)

    def test_watch_survives_restart_and_retains_original_work(self):
        require_process_identity()
        client = self.client()
        order, job = self.work(client)
        client.invoke("pr_open")
        self.provider.run.update(status="in_progress", conclusion=None)
        watch = client.invoke("ci_watch", run=10)["watch"]
        until(lambda: self.store.inbox("alpha")["messages"])
        client.close()
        from devtools import watcher

        observer = watcher.status(self.store)
        self.assertTrue(observer["running"])
        self.assertNotEqual(client.process.pid, observer["pid"])
        service = GitHub(self.store, "alpha", http=self.http)
        with self.store.connect() as db:
            db.execute("UPDATE members SET selected_work_order=NULL,selected_job=NULL WHERE name='alpha'")
            # Simulate a process killed while it owned a polling lease; a restart recovers it immediately.
            db.execute(
                "UPDATE github_watches SET next_poll=?,poll_pid=? WHERE id=?",
                (time.time() + 600, client.process.pid, watch),
            )
        self.provider.run.update(status="completed", conclusion="success")
        messages = until(lambda: rows if len(rows := self.store.inbox("alpha")["messages"]) >= 2 else None)
        self.assertEqual((order, job), (messages[-1]["work_order"], messages[-1]["job"]))
        self.assertIn("passed", messages[-1]["body"])
        restarted = self.client()
        self.assertEqual(observer["pid"], watcher.status(self.store)["pid"])
        service.tick()
        self.assertEqual(2, len(self.store.inbox("alpha")["messages"]))
        restarted.invoke("job_select", job=job)
        next_watch = restarted.invoke("ci_watch")["watch"]
        self.provider.pr["head"]["sha"] = "c" * 40
        service.tick()
        self.assertIn("superseded", self.store.inbox("alpha")["messages"][-1]["body"])
        beta = self.client("beta")
        self.assertFalse(beta.invoke("ci_watch_stop", watch=next_watch)["stopped"])

    def test_pagination_does_not_make_incomplete_ci_green(self):
        self.provider.exists = True
        self.provider.checks[MERGE] = [dict(self.provider.check("success"), id=i) for i in range(1, 101)]
        self.provider.checks[MERGE].append(dict(self.provider.check("failure"), id=999))
        client = self.client()
        status = client.invoke("ci_status", pr=7)
        self.assertEqual("failed", status["state"])
        self.assertEqual(102, status["check_count"])
        self.assertEqual(20, len(status["checks"]))
        self.assertEqual(2, status["next_page"])

    def test_transport_reports_errors_without_exposing_credentials(self):
        with self.assertRaises(GitHubError) as error:
            self.http.request("GET", "/missing")
        self.assertEqual(404, error.exception.status)
        self.assertNotIn("fixture-credential", str(error.exception))
        with patch("devtools.github_http.subprocess.run", side_effect=FileNotFoundError):
            with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(GitHubError, "authentication"):
                GitHubHTTP().token()

    def test_comment_previews_and_full_text_keep_pr_scope(self):
        client = self.client()
        self.work(client)
        client.invoke("pr_open")
        body = "a" * 7000 + "final finding"
        comment = client.invoke("pr_comment", body=body)["comment"]
        preview = client.invoke("pr_comments")["comments"][0]
        self.assertEqual(1500, len(preview["body"]))
        self.assertEqual(body, client.invoke("pr_comments", comment=comment)["comments"][0]["body"])
        self.provider.comments[0]["issue_url"] = self.provider.url + "/repos/example/project/issues/999"
        with self.assertRaisesRegex(ValueError, "does not belong"):
            client.invoke("pr_comments", comment=comment)

    def test_existing_work_and_evidence_survive_version_one_upgrade(self):
        client = self.client()
        order, job = self.work(client)
        saved = client.invoke("artifact_save", name="earlier-report.txt", content="Prior evidence")
        client.close()
        with self.store.connect() as db:
            db.execute("DROP TABLE github_watches")
            db.execute("DROP TABLE artifact_sources")
            db.execute("PRAGMA user_version=1")
        restarted = self.client()
        self.assertEqual(job, restarted.invoke("job_get")["job"]["id"])
        report = restarted.invoke("artifact_read", artifact=saved["artifact"]["id"])
        self.assertEqual("Prior evidence", report["content"])
        self.assertEqual(order, report["work"][0]["work_order"])
        self.assertEqual([], report["sources"])
        with self.store.connect() as db:
            self.assertEqual(SCHEMA_VERSION, db.execute("PRAGMA user_version").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
