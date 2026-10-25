import copy
import json
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from xorchestrate.adapters.sessions import ACPSessionDriver, CodexSessionDriver, JSONRPCSessionDriver, _LiveSession
from xorchestrate.model import Capability, OrchestrationError

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
        driver = self.driver(CodexSessionDriver, "codex", thread={"sandbox": "workspace-write", "approvalPolicy": "on-request"}, request_parameters={
            "type": "object", "properties": {"model": {"enum": ["configured-by-caller"]}}, "additionalProperties": False,
        })
        driver.set_tools([{"name": "echo", "description": "Test callback", "inputSchema": {"type": "object"}}])
        contexts = []

        def callback(method, params, context):
            contexts.append((method, copy.deepcopy(context)))
            context["actor"] = "mutated"
            return {"echo": params["arguments"]["value"]}

        driver.set_request_handler(callback)
        session = driver.start({"parameters": {"model": "configured-by-caller"}, "context": {"actor": "coder"}})
        result = driver.send(session, {"text": "tool"})
        self.assertEqual("workspace-write", result["start"]["sandbox"])
        self.assertEqual("on-request", result["start"]["approvalPolicy"])
        self.assertEqual("configured-by-caller", result["start"]["model"])
        self.assertEqual("echo", result["start"]["dynamicTools"][0]["name"])
        tool = result["tool_response"]["result"]
        self.assertTrue(tool["success"])
        self.assertEqual({"echo": "from-native"}, json.loads(tool["contentItems"][0]["text"]))
        driver.send(session, {"text": "tool"})
        self.assertEqual([("item/tool/call", {"actor": "coder"})], contexts)  # duplicate native call is replayed
        self.assert_code("conflict", driver.set_tools, [])
        self.assertEqual(2, len(driver.poll(session)))

    def test_codex_application_tools_translate_descriptors_and_arguments_with_bound_context(self):
        driver = self.driver(CodexSessionDriver, "codex")
        capability = Capability("echo", "Application callback", {
            "type": "object", "properties": {"value": {"type": "string"}},
        })
        calls = []

        def callback(action, arguments, context, key):
            calls.append((action, copy.deepcopy(arguments), copy.deepcopy(context), key))
            context["actor"] = "callback mutation"
            return {"echo": arguments["value"]}

        driver.bind_application_tools([capability], callback)
        session = driver.start({"context": {"actor": "bound-coder", "work_id": "assigned-work"}})
        result = driver.send(session, {"text": "tool"})
        self.assertEqual([{"type": "function", "name": "echo", "description": "Application callback",
                           "inputSchema": capability.input_schema}], result["start"]["dynamicTools"])
        self.assertTrue(result["tool_response"]["result"]["success"])
        self.assertEqual(("echo", {"value": "from-native"}), calls[0][:2])
        self.assertEqual({"actor": "bound-coder", "work_id": "assigned-work", "native_id": session["native_id"],
                          "incarnation": session["incarnation"], "call_id": "call-1", "action": "echo"}, calls[0][2])
        self.assertTrue(calls[0][3])

        translated = driver._request(driver._get(session), "item/tool/call", {
            "tool": "echo", "arguments": json.dumps({"value": "JSON arguments", "actor": "claimed-owner"}),
            "actor": "claimed-owner", "context": {"actor": "claimed-owner"},
            "threadId": session["native_id"], "callId": "native-json-args",
        })
        self.assertTrue(translated["success"])
        self.assertEqual({"echo": "JSON arguments"}, json.loads(translated["contentItems"][0]["text"]))
        self.assertEqual(("echo", {"value": "JSON arguments", "actor": "claimed-owner"},
                          {"actor": "bound-coder", "work_id": "assigned-work", "native_id": session["native_id"],
                           "incarnation": session["incarnation"], "call_id": "native-json-args", "action": "echo"}, calls[1][3]), calls[1])
        self.assertTrue(calls[1][3])

    def test_codex_application_invocation_keys_use_verified_identity_without_delimiter_collisions(self):
        driver = self.driver(CodexSessionDriver, "codex")
        keys = []
        driver.bind_application_tools([Capability("echo", "Callback", {"type": "object"})],
                                      lambda action, arguments, context, key: keys.append(key) or {})
        for native_id, call_id in (("a:b", "c"), ("a", "b:c"), ("other", "c"), ("a:b", "c")):
            live = _LiveSession(context={"actor": "coder"}, native_id=native_id)
            result = driver._request(live, "item/tool/call", {"tool": "echo", "arguments": {}, "callId": call_id, "threadId": native_id})
            self.assertTrue(result["success"])
        self.assertEqual(keys[0], keys[3])
        self.assertEqual(3, len(set(keys[:3])))
        self.assertTrue(all(keys))
        rejected = driver._request(_LiveSession(context={"actor": "coder"}, native_id="owned"), "item/tool/call", {
            "tool": "echo", "arguments": {}, "callId": "c", "threadId": "another-thread",
        })
        self.assertFalse(rejected["success"])
        self.assertEqual("session_identity", json.loads(rejected["contentItems"][0]["text"])["error"]["code"])
        self.assertEqual(4, len(keys))

    def test_codex_application_tools_coexist_with_configured_tools_and_raw_handlers(self):
        driver = self.driver(CodexSessionDriver, "codex", dynamic_tools=[{
            "name": "echo", "description": "Configured native callback", "inputSchema": {"type": "object"},
        }])
        raw_calls, application_calls = [], []

        def raw_handler(method, params, context):
            raw_calls.append((method, params, context))
            return {"handled": "raw"}

        driver.set_request_handler(raw_handler)
        driver.bind_application_tools([Capability("record_result", "Application callback", {"type": "object"})],
                                      lambda *args: application_calls.append(args) or {"handled": "application"})
        session = driver.start({"context": {"actor": "coder"}})
        result = driver.send(session, {"text": "tool"})
        self.assertEqual({"echo", "record_result"}, {tool["name"] for tool in result["start"]["dynamicTools"]})
        self.assertEqual({"handled": "raw"}, json.loads(result["tool_response"]["result"]["contentItems"][0]["text"]))
        live = driver._get(session)
        response = driver._request(live, "item/tool/call", {"tool": "record_result", "arguments": {"result": "observed"}, "threadId": session["native_id"], "callId": "record-1"})
        self.assertEqual({"handled": "application"}, json.loads(response["contentItems"][0]["text"]))
        self.assertEqual({"handled": "raw"}, driver._request(live, "item/commandExecution/requestApproval", {"threadId": session["native_id"]}))
        self.assertEqual(["item/tool/call", "item/commandExecution/requestApproval"], [call[0] for call in raw_calls])
        self.assertEqual(1, len(application_calls))
        self.assertEqual({"actor": "coder", "native_id": session["native_id"], "incarnation": session["incarnation"],
                          "call_id": "record-1", "action": "record_result"}, application_calls[0][2])

    def test_codex_application_tool_names_cannot_replace_configured_tools(self):
        driver = self.driver(CodexSessionDriver, "codex", dynamic_tools=[{
            "name": "echo", "description": "Configured native callback", "inputSchema": {"type": "object"},
        }])
        with patch("xorchestrate.adapters.sessions.JsonLineProcess") as process:
            self.assert_code("invalid_configuration", driver.bind_application_tools,
                             [Capability("echo", "Conflicting application callback", {"type": "object"})], lambda *args: {})
            process.assert_not_called()

    def test_native_request_parameters_cannot_supply_policy_or_executables_by_default(self):
        codex = self.driver(CodexSessionDriver, "codex")
        acp = self.driver(ACPSessionDriver, "acp")
        cases = [
            (codex, {"sandbox": "danger-full-access"}),
            (codex, {"approvalPolicy": "never"}),
            (codex, {"config": {"mcp_servers": {"injected": {"command": "untrusted"}}}}),
            (codex, {"model": "not-delegated"}),
            (acp, {"mcpServers": [{"name": "injected", "command": "untrusted", "args": [], "env": []}]}),
            (acp, {"cwd": "/not-delegated"}),
        ]
        for driver, parameters in cases:
            with self.subTest(parameters=parameters), patch("xorchestrate.adapters.sessions.JsonLineProcess") as process:
                error = self.assert_code("invalid_arguments", driver.start, {"parameters": parameters})
                self.assertEqual("not_started", error.details["outcome"])
                process.assert_not_called()

    def test_native_parameter_delegation_is_bounded_and_configuration_wins(self):
        driver = self.driver(CodexSessionDriver, "codex", thread={"model": "operator-selected"}, request_parameters={
            "type": "object", "properties": {"model": {"enum": ["delegated-model"]}}, "additionalProperties": False,
        })
        with patch("xorchestrate.adapters.sessions.JsonLineProcess") as process:
            self.assert_code("invalid_arguments", driver.start, {"parameters": {"model": "outside-delegation"}})
            self.assert_code("invalid_arguments", driver.start, {"parameters": {"model": "delegated-model", "sandbox": "danger-full-access"}})
            process.assert_not_called()
        session = driver.start({"parameters": {"model": "delegated-model"}})
        self.assertEqual("operator-selected", driver.send(session, {"text": "hello"})["start"]["model"])

    def test_native_identity_and_tool_binding_remain_owned_under_broad_delegation(self):
        codex = self.driver(CodexSessionDriver, "codex", request_parameters={"type": "object"})
        acp = self.driver(ACPSessionDriver, "acp", request_parameters={"type": "object"})
        for driver, parameters in ((codex, {"threadId": "other"}), (codex, {"dynamicTools": []}), (acp, {"sessionId": "other"})):
            with self.subTest(parameters=parameters), patch("xorchestrate.adapters.sessions.JsonLineProcess") as process:
                error = self.assert_code("invalid_request", driver.start, {"parameters": parameters})
                self.assertEqual("not_started", error.details["outcome"])
                process.assert_not_called()

    def test_native_resume_identity_cannot_come_from_a_start_specification(self):
        for cls, mode in ((CodexSessionDriver, "codex"), (ACPSessionDriver, "acp")):
            driver = self.driver(cls, mode, request_parameters={"type": "object"})
            for value in ("another-native-id", None):
                with self.subTest(protocol=mode, resume=value), patch("xorchestrate.adapters.sessions.JsonLineProcess") as process:
                    error = self.assert_code("invalid_request", driver.start, {"resume": value})
                    self.assertEqual("not_started", error.details["outcome"])
                    process.assert_not_called()

    def test_resume_source_requires_a_matching_protocol_and_native_identity(self):
        for cls, mode in ((CodexSessionDriver, "codex"), (ACPSessionDriver, "acp")):
            driver = self.driver(cls, mode, **({"resume_supported": True} if mode == "acp" else {}))
            for source in ({"protocol": "other", "native_id": "thread"}, {"protocol": mode},
                           {"protocol": mode, "native_id": ""}, {"protocol": mode, "native_id": 1}):
                with self.subTest(protocol=mode, source=source), patch("xorchestrate.adapters.sessions.JsonLineProcess") as process:
                    error = self.assert_code("session_identity", driver.resume, source, {})
                    self.assertEqual("not_started", error.details["outcome"])
                    process.assert_not_called()

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
        self.assert_code("session_identity", driver.send, session, {"text": "wrong_identity"})
        self.assert_code("session_identity", driver.poll, session)
        self.assertIsNotNone(process.poll())

    def test_codex_can_explicitly_resume_a_native_thread_after_driver_restart(self):
        driver = self.driver(CodexSessionDriver, "codex")
        session = driver.start({})
        driver.close()
        replacement = self.driver(CodexSessionDriver, "codex")
        self.assert_code("session_unavailable", replacement.send, session, {"text": "old handle"})
        resumed = replacement.resume(session, {})
        self.assertEqual(session["native_id"], resumed["native_id"])
        self.assertNotEqual(session["instance"], resumed["instance"])
        self.assertTrue(replacement.send(resumed, {"text": "resumed"})["accepted"])
        mismatch = self.driver(CodexSessionDriver, "codex_mismatch", allow_configuration_change_on_resume=True)
        self.assert_code("session_identity", mismatch.resume, session, {})

    def test_acp_session_authentication_prompt_and_explicit_resume(self):
        driver = self.driver(ACPSessionDriver, "acp", session={"cwd": "/configured", "mcpServers": []}, authentication={"methodId": "operator-login"}, resume_supported=True, request_parameters={
            "type": "object", "properties": {"cwd": {"enum": ["/requested"]}}, "additionalProperties": False,
        })
        session = driver.start({"parameters": {"cwd": "/requested"}})
        result = driver.send(session, {"text": "ACP task"})
        self.assertTrue(result["submitted"])
        self.assertEqual("pending", result["completion"])
        self.assertNotIn("stopReason", result)
        events = []
        deadline = time.monotonic() + 3
        while not any(event.get("kind") == "native_completion" for event in events) and time.monotonic() < deadline:
            events.extend(driver.poll(session))
            time.sleep(.01)
        completion = next(event["result"] for event in events if event.get("kind") == "native_completion")
        self.assertEqual("end_turn", completion["stopReason"])
        self.assertEqual("/configured", completion["start"]["cwd"])
        self.assertEqual({"methodId": "operator-login"}, completion["authentication"])
        self.assertEqual("echo:ACP task", events[0]["params"]["update"]["content"]["text"])
        resumed = driver.resume(session, {})
        self.assertEqual(session["native_id"], resumed["native_id"])
        unsupported = self.driver(ACPSessionDriver, "acp")
        with patch("xorchestrate.adapters.sessions.JsonLineProcess") as process:
            self.assert_code("unsupported_operation", unsupported.resume, session, {})
            process.assert_not_called()


if __name__ == "__main__":
    unittest.main()
