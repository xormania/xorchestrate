import json
import unittest
from pathlib import Path
from unittest.mock import patch

from fixtures.delivery import DeliveryFixture
from support import fixture_work

from devtools.api import API
from devtools.github_http import GitHubError
from devtools.workspaces import git


class DeliveryTests(DeliveryFixture, unittest.TestCase):


    def test_auditor_shares_job_context_but_has_an_immutable_source_view(self):
        audit = self.auditor()
        self.assertEqual(self.job, audit.control.snapshot()["job"])
        self.assertEqual("Feature", self.api.call("work_context", {})["job"]["objective"])
        result = audit.call("work_inspect", {"section": "review"})
        self.assertEqual(self.head, result["pr"]["revision"])
        view = Path(audit.workspaces.ensure()["root"])
        self.assertNotEqual(view, self.workspace)
        (self.workspace / "src/Example.php").write_text("Unsubmitted changes\n")
        self.assertEqual("Delivered implementation\n", (view / "src/Example.php").read_text())
        self.assertNotIn("work_save", {row["name"] for row in audit.tools()})
        self.assertIn("review_record", {row["name"] for row in audit.tools()})

    def test_specialized_auditors_load_and_review_alongside_default_auditor(self):
        from devtools.role_packages import default_role

        specializations = {
            "security_auditor": ("agentscient-security-auditor", "attacker prerequisites"),
            "adversarial_review": ("agentscient-adversarial-review", "counterexamples"),
        }
        config_path = self.state / "team.json"
        config = json.loads(config_path.read_text())
        for role in specializations:
            config["members"].append({**config["members"][-1], "name": role})
        config_path.write_text(json.dumps(config))
        general = self.auditor()
        self.assertEqual("audit", default_role("audit"))
        members = {general.member}
        for role, (skill, focus) in specializations.items():
            with self.subTest(role=role):
                reviewer = self.auditor(role)
                self.assertNotIn(reviewer.member, members)
                members.add(reviewer.member)
                onboarding = reviewer.call("onboard", {})
                self.assertEqual(role, onboarding["package"]["role"])
                self.assertEqual("audit", onboarding["package"]["behavior"])
                self.assertIn(focus, onboarding["role_instructions"])
                self.assertIn(skill, {row["name"] for row in onboarding["skills"] if row["loaded"]})
                self.assertIn(role, {row["name"] for row in self.xo.call("onboard", {})["assignable_roles"]})

                inspected = reviewer.call("work_inspect", {"section": "review"})
                self.assertEqual(self.head, inspected["pr"]["revision"])
                view = Path(reviewer.workspaces.ensure()["root"])
                (self.workspace / "src/Example.php").write_text("Unsubmitted changes\n")
                self.assertEqual("Delivered implementation\n", (view / "src/Example.php").read_text())
                with patch.object(reviewer.workspaces, "save") as save:
                    with self.assertRaisesRegex(ValueError, "not available"):
                        reviewer.call("work_save", {"note": "Cannot edit producer source"})
                    save.assert_not_called()
                recorded = reviewer.call("review_record", {"conclusion": "comment", "summary": role + " evidence for the submitted revision."})
                self.assertEqual("published", recorded["state"])
        self.assertEqual(len(specializations), len(self.provider.reviews))

    def test_review_retry_reconciles_remote_success_and_routes_findings_and_response(self):
        audit = self.auditor()
        audit.call("work_inspect", {"section": "review"})
        self.provider.uncertain = True
        args = {"conclusion": "changes_requested", "summary": "Cover the empty input case."}
        with self.assertRaises(ConnectionError):
            audit.call("review_record", args)
        review = audit.call("review_record", args)
        self.assertEqual(1, len(self.provider.reviews))
        self.assertEqual(review, audit.call("review_record", args))
        self.assertTrue(
            any('"conclusion":"changes_requested"' in row["body"] for row in self.store.inbox("coder")["messages"])
        )
        response = self.api.call(
            "review_respond", {"text": "The attached test covers this behavior.", "review": review["reference"]}
        )
        self.assertEqual("response", response["conclusion"])
        self.assertTrue(
            any('"conclusion":"response"' in row["body"] for row in self.store.inbox(audit.member)["messages"])
        )

    def test_changed_revision_rejects_review_and_acceptance(self):
        audit = self.auditor()
        audit.call("work_inspect", {"section": "review"})
        self.xo.call("monitor", {"work": "Feature"})
        self.provider.pr["head"]["sha"] = "b" * 40
        with self.assertRaisesRegex(ValueError, "head changed"):
            audit.call("review_record", {"conclusion": "ready", "summary": "Reviewed"})
        with self.assertRaisesRegex(ValueError, "changed after inspection"):
            self.xo.call("steer", {"action": "accept", "work": "Feature", "direction": "Meets the objective"})
        self.assertEqual([], self.provider.merges)

    def test_review_rechecks_live_scope_before_publication(self):
        audit = self.auditor()
        audit.call("work_inspect", {"section": "review"})

        def pause_during_read(endpoint):
            self.xo.call("steer", {"work": "Feature", "role": "audit", "action": "pause"})
            return []

        with patch.object(self.provider, "_all", side_effect=pause_during_read):
            with self.assertRaisesRegex(ValueError, "binding|paused"):
                audit.call("review_record", {"conclusion": "ready", "summary": "Reviewed"})
        self.assertEqual([], self.provider.reviews)

    def test_explicit_review_rejection_can_retry_after_credentials_are_fixed(self):
        audit = self.auditor()
        audit.call("work_inspect", {"section": "review"})
        args = {"conclusion": "ready", "summary": "Reviewed"}
        with patch.object(self.provider, "pr_review", side_effect=GitHubError(401, "Rejected")):
            with self.assertRaises(GitHubError):
                audit.call("review_record", args)
        self.assertEqual("published", audit.call("review_record", args)["state"])
        self.assertEqual(1, len(self.provider.reviews))

    def test_acceptance_checks_ci_dirty_work_and_configured_target_before_merge(self):
        self.xo.call("monitor", {"work": "Feature"})
        args = {"action": "accept", "work": "Feature", "direction": "Meets the objective"}
        self.provider.ci = "pending"
        with self.assertRaisesRegex(ValueError, "CI"):
            self.xo.call("steer", args)
        self.provider.ci = "passed"
        (self.workspace / "src/Example.php").write_text("Dirty\n")
        with self.assertRaisesRegex(ValueError, "unsubmitted"):
            self.xo.call("steer", args)
        git(self.workspace, "restore", "src/Example.php")
        self.provider.pr["base"]["ref"] = "master"
        with self.assertRaisesRegex(ValueError, "target"):
            self.xo.call("steer", args)
        self.assertEqual([], self.provider.merges)

    def test_acceptance_retains_history_reuses_capacity_and_next_job_uses_merged_target(self):
        audit = self.auditor()
        self.store.event("coder", "native_usage", work_order=self.order, job=self.job, observation={"inputTokens": 123})
        self.xo.call("monitor", {"work": "Feature"})
        result = self.xo.call(
            "steer",
            {"action": "accept", "work": "Feature", "direction": "The behavior and evidence meet the objective"},
        )
        self.assertEqual("done", result["outcome"])
        self.assertIsNone(self.api.control.snapshot())
        self.assertIsNone(audit.control.snapshot())
        self.assertEqual("done", self.xo.call("monitor", {})["jobs"][0]["status"])
        self.assertTrue((self.workspace / "src/Example.php").exists())
        self.assertEqual(
            "done",
            self.xo.call("steer", {"action": "accept", "work": "Feature", "direction": "Retry after disconnect"})[
                "outcome"
            ],
        )
        self.assertEqual(1, len(self.provider.merges))
        fixture_work(self.xo, [{"name": "Follow-up", "role": "coder", "objective": "Continue", "done_when": "Done", "constraints": "Product only"}])
        new_root = Path(self.api.workspaces.ensure()["root"])
        self.assertNotEqual(self.workspace, new_root)
        self.assertEqual("Delivered implementation\n", (new_root / "src/Example.php").read_text())
        self.assertEqual(self.head, git(new_root, "rev-parse", "HEAD").stdout.decode().strip())
        self.assertIsNone(self.xo.call("monitor", {})["collaborators"][0]["latest_usage"])

    def test_xo_concludes_finished_work_and_co_stays_available_for_next_order(self):
        co = API(self.state, "co")
        with self.assertRaisesRegex(ValueError, "unfinished"):
            self.xo.call("conclude", {"assessment": "Not yet"})
        self.xo.call(
            "steer", {"action": "cancel", "work": "Feature", "direction": "This approach is no longer required"}
        )
        self.assertTrue((self.workspace / "src/Example.php").exists())
        self.xo.call(
            "conclude", {"assessment": "The investigation is complete; the implementation was intentionally cancelled"}
        )
        self.assertEqual(self.order, co.control.snapshot()["work_order"])
        self.assertEqual("done", co.call("work_context", {})["work"]["status"])
        self.assertIn("The investigation is complete", json.dumps(co.call("work_context", {"section": "messages"})))
        self.assertIsNone(self.xo.control.snapshot())
        co.call(
            "direct",
            {"title": "Next effort", "objective": "New goal", "done_when": "Outcome", "constraints": "Keep history"},
        )
        self.assertGreater(co.control.snapshot()["work_order"], self.order)
        with self.store.connect() as db:
            self.assertEqual(
                "done", db.execute("SELECT status FROM work_orders WHERE id=?", (self.order,)).fetchone()[0]
            )
            self.assertEqual("cancelled", db.execute("SELECT status FROM jobs WHERE id=?", (self.job,)).fetchone()[0])


if __name__ == "__main__":
    unittest.main()
