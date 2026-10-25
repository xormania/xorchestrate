"""Recover through managed calls without treating a suggestion as authority."""

import json
import sys
import unittest
from unittest.mock import patch

from fixtures import completion as test_completion
from fixtures.completion import CompletionFixture
from fixtures.development import DevelopmentFixture
from fixtures.workspaces import WorkspaceFixture
from support import Client

from devtools.api import API
from devtools.development import Development
from devtools.diagnostics import Diagnostics
from devtools.failures import Failure
from devtools.operations import Operations, execute
from devtools.role_packages import catalog
from devtools.workspaces import git


class RecoveryTests(unittest.TestCase):
    prepared = WorkspaceFixture.prepared
    configure = CompletionFixture.configure
    assignment = CompletionFixture.assignment

    def setUp(self):
        WorkspaceFixture.setUp(self)
        self.xo = API(self.state, "xo")
        self.job = self.api.control.snapshot()["job"]
        self.order = self.xo.control.snapshot()["work_order"]
        self.configure({})
        self.workspace = DevelopmentFixture.configure(self, {"behavior": [
            sys.executable, "-c",
            "from pathlib import Path; value = Path('src/Example.php').read_text().strip(); "
            "print('Expected fixed; observed ' + value); assert value == 'fixed'",
        ]})
        # Fixture dependencies are committed so the real source-cleanliness
        # checks remain enabled, without requiring a PHP installation.
        git(self.workspace, "add", ".")
        git(self.workspace, "commit", "-m", "Fixture dependencies")
        provider = patch("devtools.advice.Jev.evaluate", side_effect=test_completion.answer)
        self.provider = provider.start()
        self.addCleanup(provider.stop)

    def check(self, arguments=None):
        # Run the normal persisted worker synchronously while retaining its real
        # configured subprocess, evidence, notifications and request binding.
        with patch.object(Operations, "launch", side_effect=lambda ident: execute(self.state, "coder", ident)):
            return self.api.call("work_check", arguments or {"check": "behavior"})

    def fix(self, text="fixed\n"):
        (self.workspace / "src/Example.php").write_text(text)
        self.api.call("work_save", {"note": "Correct the measured behavior"})

    def retained(self, reference, api=None):
        api = api or self.api
        page = api.call("work_context", {"reference": reference})
        content = page["content"]
        while page.get("next_offset") is not None:
            page = api.call("work_context", {"reference": reference, "offset": page["next_offset"]})
            content += page["content"]
        return json.loads(content)

    def check_action(self, result):
        return next(item for item in result["result"]["diagnostic_guidance"]["actions"]
                    if item["tool"] == "work_check")

    def test_failed_measurement_guides_real_worker_recovery_and_retains_the_observation(self):
        first = self.check()
        self.assertEqual("completed", first["state"])
        self.assertFalse(first["result"]["passed"])
        guidance = first["result"]["diagnostic_guidance"]
        action = self.check_action(first)
        self.assertEqual({"check": "behavior"}, action["arguments"])
        self.assertIn("after", action["when"].lower())
        reference = first["result"]["checks"][0]["evidence"]
        reads = [item["arguments"]["reference"] for item in guidance["actions"]
                 if item["tool"] == "work_context" and "reference" in item["arguments"]]
        self.assertIn(first["reference"], reads)
        self.assertIn(reference, reads)
        self.assertIn("Expected fixed; observed original", self.retained(reference)["output"])
        self.assertEqual(guidance, self.retained(first["reference"])["result"]["diagnostic_guidance"])

        unchanged = self.check(action["arguments"])
        self.assertFalse(unchanged["result"]["passed"])
        self.fix()
        recovered = self.check(action["arguments"])
        self.assertTrue(recovered["result"]["passed"])
        self.assertEqual([], recovered["result"]["uncommitted"])
        self.assertNotIn("diagnostic_guidance", recovered["result"])
        self.provider.assert_not_called()
        observations = [row["data"] for row in self.store.events(kind="recovery_action_observed", limit=100)["events"]
                        if row["data"]["guidance_event"] == guidance["reference"] and
                        row["data"]["action"] == "work_check"]
        self.assertEqual(["failed", "succeeded"], [row["outcome"] for row in observations])
        self.assertEqual([False, True], [row["condition_resolved"] for row in observations])
        self.assertEqual(recovered["reference"], observations[-1]["subsequent_operation"])
        measured = observations[-1]["measurements"][0]
        self.assertTrue(measured["passed"])
        self.assertTrue(measured["workspace_clean"])
        self.assertEqual(recovered["result"]["checks"][0]["evidence"], measured["evidence"])

    def test_stale_completion_mcp_retry_stays_blocked_until_current_specialist_measurement(self):
        assignment = self.assignment()
        self.fix()
        old = self.check()
        self.api.call("work_report", {"kind": "result", "text": "Measured behavior", "evidence": {
            "name": "proof.txt", "content": "Expected fixed; observed fixed."}})
        self.fix("fixed\n\n")
        arguments = {"action": "complete", "work": "Feature", "assignment": "Feature implementation",
                     "direction": "Inspect retained measurements"}
        client = Client(self.state, "xo")
        self.addCleanup(client.close)
        for _ in range(2):
            blocked = client.call("tools/call", {"name": "steer", "arguments": arguments})
            self.assertTrue(blocked["isError"])
            metadata = blocked["structuredContent"]
            self.assertEqual(metadata, json.loads(blocked["content"][1]["text"]))
            self.assertEqual("proof_stale", metadata["failure"]["reason"])
            self.assertEqual(["behavior"], metadata["failure"]["details"]["checks"])
            actions = metadata["diagnostic_guidance"]["actions"]
            self.assertIn(old["reference"], [item["arguments"].get("reference") for item in actions])
            # XO is not bound to the specialist's job: it must not receive an
            # action that would measure its own work-order workspace instead.
            self.assertFalse(any(item["tool"] == "work_check" for item in actions))
            with self.store.connect() as db:
                self.assertEqual("active", db.execute("SELECT status FROM role_assignments WHERE id=?", (assignment,)).fetchone()[0])
            self.assertIsNotNone(self.api.control.snapshot())
        self.provider.assert_not_called()
        self.assertFalse(self.store.events(kind="jev_request")["events"])
        current = self.check()
        self.assertTrue(current["result"]["passed"])
        complete = self.xo.call("steer", arguments)
        self.assertEqual("done", complete["outcome"])
        self.assertIsNone(self.api.control.snapshot())
        self.assertEqual(1, self.provider.call_count)
        events = [row["data"] for row in self.store.events(kind="recovery_action_observed", limit=100)["events"]]
        self.assertTrue(any(row["condition_resolved"] is False for row in events))
        self.assertTrue(any(row["condition_resolved"] is True for row in events))

    def test_nco_does_not_receive_own_check_for_specialist_assignment_proof(self):
        self.api.control.bind("docs", "nco", self.order, self.job, "xo")
        self.api.control.bind("coder", "coder", self.order, self.job, "docs")
        assignment = self.assignment()
        nco = API(self.state, "docs")
        self.assertIn("work_check", {tool["name"] for tool in nco.tools()})
        failed = self.check()
        self.api.call("work_report", {"kind": "result", "text": "The measured behavior needs correction"})
        args = {"action": "complete", "work": "Feature", "assignment": "Feature implementation",
                "direction": "Inspect specialist evidence"}
        with self.assertRaises(Failure) as blocked:
            nco.call("steer", args)
        self.assertEqual(assignment, blocked.exception.diagnostic_failure["details"]["assignment"])
        self.assertEqual("check_failed", blocked.exception.diagnostic_failure["reason"])
        self.assertFalse(any(item["tool"] == "work_check"
                             for item in blocked.exception.diagnostic_guidance["actions"]))
        self.assertEqual({"check": "behavior"}, self.check_action(failed)["arguments"])
        self.provider.assert_not_called()
        self.fix()
        self.assertTrue(self.check(self.check_action(failed)["arguments"])["result"]["passed"])
        self.assertEqual("done", nco.call("steer", args)["outcome"])
        self.assertEqual(1, self.provider.call_count)

    def test_check_removed_after_guidance_is_not_executed_or_suggested_again(self):
        failed = self.check()
        action = self.check_action(failed)
        config = json.loads((self.state / "team.json").read_text())
        config["project"]["development"]["checks"] = {"replacement": [sys.executable, "-c", "print('new policy')"]}
        (self.state / "team.json").write_text(json.dumps(config))
        with patch.object(Development, "run") as process:
            rejected = self.check(action["arguments"])
        process.assert_not_called()
        self.assertEqual("failed", rejected["state"])
        self.assertIn("Choose a configured check", rejected["error"])
        fresh = Diagnostics(self.store, "coder").guidance("work_check", failed["result"])
        self.assertFalse(any(item["tool"] == "work_check" for item in fresh["actions"]))
        self.provider.assert_not_called()

    def test_revoked_permission_rejects_delivered_action_before_invocation(self):
        failed = self.check()
        action = self.check_action(failed)
        packages = catalog()
        for group in packages["roles"]["coder"]["mcp"].values():
            if "work_check" in group:
                group.remove("work_check")
        with patch("devtools.role_packages._catalog", return_value=packages), patch.object(self.api, "invoke") as invoke:
            with self.assertRaises(Failure) as denied:
                self.api.call(action["tool"], action["arguments"])
        invoke.assert_not_called()
        self.assertEqual("tool_not_permitted", denied.exception.diagnostic_failure["reason"])
        self.assertFalse(any(item["tool"] == "work_check" for item in denied.exception.diagnostic_guidance["actions"]))
        self.provider.assert_not_called()

    def test_changed_job_rejects_previous_evidence_and_omits_previous_check(self):
        failed = self.check()
        other = self.xo.work.job_create(title="Other", objective="Other", done_when="Other proof",
                                        constraints="Other scope", work_order=self.order)["job"]["id"]
        failure = failed["result"]["failure"]
        failure["details"]["job"] = self.job
        self.api.control.bind("coder", "coder", self.order, other, "xo")
        with self.assertRaisesRegex(ValueError, "not available"):
            self.api.call("work_context", {"reference": failed["reference"]})
        guidance = Diagnostics(self.store, "coder").guidance("work_check", failed["result"], failure=failure)
        self.assertFalse(any(item["tool"] == "work_check" or "reference" in item["arguments"]
                             for item in guidance["actions"]))
        self.provider.assert_not_called()

    def test_renamed_role_retains_real_configured_recovery_without_provider_calls(self):
        packages = catalog()
        packages["roles"]["builder"] = packages["roles"].pop("coder")
        packages["defaults"]["coder"] = "builder"
        with patch("devtools.role_packages._catalog", return_value=packages):
            self.api.control.bind("coder", "builder", self.order, self.job, "xo")
            failed = self.check()
            self.assertEqual({"check": "behavior"}, self.check_action(failed)["arguments"])
            self.fix()
            self.assertTrue(self.check(self.check_action(failed)["arguments"])["result"]["passed"])
        self.provider.assert_not_called()


if __name__ == "__main__":
    unittest.main()
