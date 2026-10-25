"""Stable failure facts survive managed MCP, workers, and scoped result reads."""

import json
import unittest
from unittest.mock import patch

from fixtures.control import ControlFixture
from fixtures.development import DevelopmentFixture

from devtools import role_packages
from devtools.api import API
from devtools.control import REQUEST_BINDING
from devtools.development import Development
from devtools.failures import Failure, failure_info
from devtools.operations import Operations, execute
from devtools.workspaces import Workspaces


class FailureTransportTests(unittest.TestCase):
    setUp = ControlFixture.setUp
    client = ControlFixture.client

    def test_real_mcp_reports_role_denial_and_argument_errors_without_losing_text(self):
        client = self.client("coder")
        for name, arguments, category, reason, diagnostic in (
            ("member_bind", {"member": "coder", "role": "co"}, "not_permitted", "tool_not_permitted", "not available"),
            ("work_report", {}, "invalid_request", "invalid_arguments", "missing required fields"),
        ):
            with self.subTest(name=name):
                result = client.call("tools/call", {"name": name, "arguments": arguments})
                self.assertTrue(result["isError"])
                self.assertIn(diagnostic, result["content"][0]["text"])
                metadata = result["structuredContent"]
                self.assertEqual(metadata, json.loads(result["content"][1]["text"]))
                self.assertEqual(category, metadata["failure"]["category"])
                self.assertEqual(reason, metadata["failure"]["reason"])
                self.assertIn("next_action", metadata["diagnostic_guidance"])
                self.assertNotIn("SECRET SIBLING", json.dumps(metadata))
        self.assertEqual("coder", API(self.state, "coder").control.snapshot()["role"])

    def test_denial_guidance_uses_trusted_binding_and_never_invokes_the_tool(self):
        api = API(self.state, "coder")
        with patch.object(api, "invoke") as invoke:
            with self.assertRaises(Failure) as rejected:
                api.call("member_bind", {"member": "coder", "role": "co", "job": self.other})
        invoke.assert_not_called()
        self.assertEqual("tool_not_permitted", rejected.exception.diagnostic_failure["reason"])
        self.assertIn("next_action", rejected.exception.diagnostic_guidance)
        self.assertIsNone(REQUEST_BINDING.get())
        event = self.store.events(kind="tool_denied")["events"][-1]
        self.assertEqual(rejected.exception.diagnostic_failure, event["data"]["failure"])
        self.assertEqual(self.job, event["job"])

    def test_binding_changed_during_denial_does_not_deliver_old_guidance(self):
        api = API(self.state, "coder")
        original = api._tools

        def rebind(scope):
            api.control.bind("coder", "docs", self.order, self.job, "xo")
            return original(scope)

        with patch.object(api, "_tools", side_effect=rebind):
            with self.assertRaises(Failure) as rejected:
                api.call("member_bind", {})
        self.assertEqual({}, rejected.exception.diagnostic_guidance)
        self.assertIsNone(REQUEST_BINDING.get())

    def test_synchronous_service_failure_retains_original_exception_and_telemetry(self):
        api = API(self.state, "coder")
        failure = Failure("The evaluator returned an invalid assessment", category="evaluation_invalid",
                          reason="provider_response_invalid", details={"field": "answers"})
        with patch.object(api, "invoke", side_effect=failure):
            with self.assertRaises(Failure) as rejected:
                api.call("work_context", {})
        self.assertIs(failure, rejected.exception)
        self.assertEqual(failure_info(failure), rejected.exception.diagnostic_failure)
        event = self.store.events(kind="operation_failed")["events"][-1]["data"]
        self.assertEqual(str(failure), event["error"])
        self.assertEqual(failure_info(failure), event["failure"])

    def test_queued_capability_revocation_is_a_typed_denial_before_effects(self):
        operations = Operations(self.store, "coder")
        with patch.object(operations, "launch"):
            receipt = operations.start("work_check", {})
        ident = int(receipt["reference"].split(":")[1])
        catalog = role_packages.catalog()
        for tools in catalog["roles"]["coder"]["mcp"].values():
            if "work_check" in tools:
                tools.remove("work_check")
        with patch("devtools.role_packages._catalog", return_value=catalog), \
                patch.object(Development, "check") as check:
            execute(self.state, "coder", ident)
        check.assert_not_called()
        result = operations.get(ident)
        self.assertEqual("failed", result["state"])
        self.assertIn("role package does not permit work_check", result["error"])
        self.assertEqual("not_permitted", result["failure"]["category"])
        self.assertEqual("capability_not_permitted", result["failure"]["reason"])
        notice = json.loads(self.store.inbox("xo")["messages"][-1]["body"])
        self.assertEqual(result["failure"], notice["failure"])
        with self.store.connect() as db:
            self.assertEqual(0, db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0])
            self.assertEqual(0, db.execute("SELECT COUNT(*) FROM job_workspaces").fetchone()[0])

    def test_workspace_source_distinguishes_denial_from_changed_binding(self):
        for member, write, reason in (("idle", False, "work_binding_required"),
                                      ("audit", True, "role_cannot_write")):
            with self.subTest(member=member), self.assertRaises(Failure) as rejected:
                Workspaces(self.store, member).scope(write=write)
            self.assertEqual("not_permitted", failure_info(rejected.exception)["category"])
            self.assertEqual(reason, failure_info(rejected.exception)["reason"])
        api = API(self.state, "coder")
        token = REQUEST_BINDING.set(("coder", api.control.snapshot()))
        try:
            api.control.bind("coder", "docs", self.order, self.job, "xo")
            with self.assertRaises(Failure) as rejected:
                api.workspaces.scope()
        finally:
            REQUEST_BINDING.reset(token)
        self.assertEqual("not_ready", failure_info(rejected.exception)["category"])
        self.assertEqual("binding_changed", failure_info(rejected.exception)["reason"])


class BackgroundFailureTransportTests(unittest.TestCase):
    setUp = DevelopmentFixture.setUp
    prepared = DevelopmentFixture.prepared
    configure = DevelopmentFixture.configure

    def test_worker_failure_survives_notices_summary_and_scoped_context(self):
        self.configure()
        failure = Failure("Configured evaluator is unavailable", category="evaluation_unavailable",
                          reason="provider_unavailable", details={"retryable": True})
        with patch.object(Operations, "launch", side_effect=lambda ident: execute(self.state, "coder", ident)), \
                patch.object(Development, "check", side_effect=failure):
            receipt = self.api.call("work_check", {})
            ident = int(receipt["reference"].split(":")[1])
        expected = failure_info(failure)
        self.assertEqual(expected, receipt["failure"])
        retained = self.api.operations.get(ident)
        self.assertEqual("failed", retained["state"])
        self.assertEqual(str(failure), retained["error"])
        self.assertEqual(expected, retained["failure"])
        self.assertEqual(expected, retained["result"]["failure"])
        self.assertEqual(expected, self.api.operations.inspect()["operations"][0]["failure"])
        for member in ("coder", "xo"):
            notice = json.loads(self.store.inbox(member)["messages"][-1]["body"])
            self.assertEqual(expected, notice["failure"])
            self.assertEqual(member == "coder", "diagnostic_guidance" in notice)
        page = self.api.call("work_context", {"reference": receipt["reference"]})
        content = page["content"]
        while page.get("next_offset") is not None:
            page = self.api.call("work_context", {"reference": receipt["reference"], "offset": page["next_offset"]})
            content += page["content"]
        self.assertEqual(expected, json.loads(content)["failure"])
        event = self.store.events(kind="tool_operation_finished")["events"][-1]["data"]
        self.assertEqual(expected, event["failure"])

    def test_completed_check_failure_is_not_reclassified_as_worker_exception(self):
        self.configure()
        config = json.loads((self.state / "team.json").read_text())
        config["project"]["configuration_file"] = "configuration.json"
        (self.state / "team.json").write_text(json.dumps(config))
        (self.state / "configuration.json").write_text(json.dumps({
            "version": 1, "project": {"features": {"guidance": False}},
        }))
        result = {"passed": False, "checks": [{"check": "fixture", "exit_code": 1, "output": "Contract failed"}]}
        with patch.object(Operations, "launch"), patch.object(Development, "check", return_value=result):
            receipt = self.api.call("work_check", {})
            ident = int(receipt["reference"].split(":")[1])
            execute(self.state, "coder", ident)
        retained = self.api.operations.get(ident)
        self.assertEqual("completed", retained["state"])
        self.assertIsNone(retained["error"])
        self.assertFalse(retained["result"]["passed"])
        self.assertEqual("not_ready", retained["failure"]["category"])
        self.assertEqual("check_failed", retained["failure"]["reason"])
        self.assertNotIn("diagnostic_guidance", retained["result"])
        summary = self.api.operations.inspect()["operations"][0]
        self.assertFalse(summary["passed"])
        self.assertEqual(1, summary["checks"]["failed"])


if __name__ == "__main__":
    unittest.main()
