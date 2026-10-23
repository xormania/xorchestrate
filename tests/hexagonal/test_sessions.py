import copy
import json
import sys
import unittest
from pathlib import Path

from xorchestrate.adapters.sessions import ACPSessionDriver, CodexSessionDriver, JSONRPCSessionDriver
from xorchestrate.model import OrchestrationError

FIXTURE = Path(__file__).parent / "fixtures" / "session_server.py"


class SessionTests(unittest.TestCase):
    def options(self, mode="jsonrpc", **extra):
        return {"command": [sys.executable, str(FIXTURE), mode], "timeout": 10, **extra}

    def driver(self, cls=JSONRPCSessionDriver, mode="jsonrpc", **extra):
        driver = cls(self.options(mode, **extra))
        self.addCleanup(driver.close)
        return driver

    def assert_code(self, code, function, *args):
        with self.assertRaises(OrchestrationError) as raised:
            function(*args)
        self.assertEqual(code, raised.exception.code)
        return raised.exception

    def test_generic_session_executes_messages_polls_and_stops_real_process(self):
        driver = self.driver(env={"SESSION_TEST": "configured"})
        specification = {"goal": "a useful task", "context": {"actor": "developer"}, "command": ["this-is-data"]}
        session = driver.start(specification)
        process = driver._get(session).process.process
        specification["context"]["actor"] = "mutated"
        result = driver.send(session, {"text": "$(not-a-shell) `unchanged`"})
        self.assertEqual("configured", result["environment"])
        self.assertEqual("developer", result["start"]["context"]["actor"])
        self.assertEqual(["this-is-data"], result["start"]["command"])
        self.assertEqual("$(not-a-shell) `unchanged`", result["message"]["text"])
        self.assertEqual([{"kind": "message", "message": result["message"]}], driver.poll(session))
        self.assertEqual([], driver.poll(session))
        self.assertTrue(driver.stop(session)["stopped"])
        self.assertIsNotNone(process.poll())
        self.assert_code("session_unavailable", driver.send, session, {"text": "again"})

    def test_generic_protocol_method_selection_is_configuration(self):
        driver = self.driver(methods={"start": "custom/start"}, initialize={"method": "initialize", "params": {}})
        session = driver.start({})
        self.assertTrue(driver.send(session, {"text": "hello"})["accepted"])

    def test_sessions_are_isolated_and_saved_handles_do_not_own_other_processes(self):
        driver = self.driver()
        first, second = driver.start({}), driver.start({})
        self.assertNotEqual(first["native_id"], second["native_id"])
        forged = {**first, "native_id": second["native_id"]}
        self.assert_code("session_unavailable", driver.stop, forged)
        other_driver = self.driver()
        self.assert_code("session_unavailable", other_driver.stop, json.loads(json.dumps(first)))
        driver.stop(first)
        self.assertTrue(driver.send(second, {"text": "still alive"})["accepted"])

    def test_timeout_does_not_replay_and_reaps_the_owned_process(self):
        driver = self.driver()
        session = driver.start({})
        driver._get(session).process.timeout = 0.1
        process = driver._get(session).process.process
        error = self.assert_code("provider_timeout", driver.send, session, {"text": "hang"})
        self.assertEqual("unknown", error.details["outcome"])
        self.assertIsNotNone(process.poll())
        self.assert_code("provider_unavailable", driver.send, session, {"text": "again"})

    def test_session_limit_is_configurable_and_released_when_stopped(self):
        driver = self.driver(max_sessions=1)
        session = driver.start({})
        self.assert_code("session_capacity", driver.start, {})
        driver.stop(session)
        driver.start({})

    def test_invalid_protocol_start_cleans_up_and_releases_capacity(self):
        driver = self.driver(mode="bad_id", max_sessions=1)
        self.assert_code("provider_protocol", driver.start, {})
        self.assertEqual({}, driver._sessions)
        self.assertEqual(0, driver._starting)
        self.assert_code("provider_protocol", driver.start, {})

    def test_output_limit_cleans_up_failed_start(self):
        driver = self.driver(mode="flood", max_frame_bytes=1024)
        self.assert_code("provider_protocol", driver.start, {})
        self.assertEqual({}, driver._sessions)

    def test_codex_native_policy_and_tool_configuration_stay_explicit(self):
        driver = self.driver(CodexSessionDriver, "codex", thread={"sandbox": "workspace-write", "approvalPolicy": "on-request"})
        driver.set_tools([{"name": "echo", "description": "Test callback", "inputSchema": {"type": "object"}}])
        contexts = []

        def callback(method, params, context):
            contexts.append((method, copy.deepcopy(context)))
            context["actor"] = "mutated"
            return {"echo": params["arguments"]["value"]}

        driver.set_request_handler(callback)
        session = driver.start({"parameters": {"model": "configured-by-caller", "sandbox": "danger-full-access"}, "context": {"actor": "coder"}})
        result = driver.send(session, {"text": "tool"})
        self.assertEqual("workspace-write", result["start"]["sandbox"])
        self.assertEqual("on-request", result["start"]["approvalPolicy"])
        self.assertEqual("configured-by-caller", result["start"]["model"])
        self.assertEqual("echo", result["start"]["dynamicTools"][0]["name"])
        tool = result["tool_response"]["result"]
        self.assertTrue(tool["success"])
        self.assertEqual({"echo": "from-native"}, json.loads(tool["contentItems"][0]["text"]))
        driver.send(session, {"text": "tool"})
        self.assertEqual([("item/tool/call", {"actor": "coder"})] * 2, contexts)
        self.assert_code("conflict", driver.set_tools, [])
        self.assertEqual(2, len(driver.poll(session)))

    def test_codex_missing_handler_and_identity_mismatch_never_invoke_a_tool(self):
        driver = self.driver(CodexSessionDriver, "codex")
        session = driver.start({})
        result = driver.send(session, {"text": "tool"})
        self.assertFalse(result["tool_response"]["result"]["success"])
        error = json.loads(result["tool_response"]["result"]["contentItems"][0]["text"])["error"]
        self.assertEqual("unsupported_method", error["code"])
        second = self.driver(CodexSessionDriver, "codex")
        calls = []
        second.set_request_handler(lambda *args: calls.append(args) or {})
        result = second.send(second.start({}), {"text": "wrong_tool"})
        self.assertFalse(result["tool_response"]["result"]["success"])
        self.assertEqual([], calls)

    def test_codex_identity_mismatch_notifications_close_connection(self):
        driver = self.driver(CodexSessionDriver, "codex")
        session = driver.start({})
        process = driver._get(session).process.process
        driver.send(session, {"text": "wrong_identity"})
        self.assert_code("session_identity", driver.poll, session)
        self.assertIsNotNone(process.poll())

    def test_codex_can_explicitly_resume_a_native_thread_after_driver_restart(self):
        driver = self.driver(CodexSessionDriver, "codex")
        session = driver.start({})
        driver.close()
        replacement = self.driver(CodexSessionDriver, "codex")
        self.assert_code("session_unavailable", replacement.send, session, {"text": "old handle"})
        resumed = replacement.start({"resume": session["native_id"]})
        self.assertEqual(session["native_id"], resumed["native_id"])
        self.assertNotEqual(session["instance"], resumed["instance"])
        self.assertTrue(replacement.send(resumed, {"text": "resumed"})["accepted"])
        mismatch = self.driver(CodexSessionDriver, "codex_mismatch")
        self.assert_code("session_identity", mismatch.start, {"resume": session["native_id"]})

    def test_acp_session_authentication_prompt_and_explicit_resume(self):
        driver = self.driver(ACPSessionDriver, "acp", session={"cwd": "/configured", "mcpServers": []}, authentication={"methodId": "operator-login"}, resume_supported=True)
        session = driver.start({"parameters": {"cwd": "/requested"}})
        result = driver.send(session, {"text": "ACP task"})
        self.assertEqual("end_turn", result["stopReason"])
        self.assertEqual("/configured", result["start"]["cwd"])
        self.assertEqual({"methodId": "operator-login"}, result["authentication"])
        self.assertEqual("echo:ACP task", driver.poll(session)[0]["params"]["update"]["content"]["text"])
        resumed = driver.start({"resume": session["native_id"]})
        self.assertEqual(session["native_id"], resumed["native_id"])
        unsupported = self.driver(ACPSessionDriver, "acp")
        self.assert_code("unsupported_operation", unsupported.start, {"resume": session["native_id"]})


if __name__ == "__main__":
    unittest.main()
