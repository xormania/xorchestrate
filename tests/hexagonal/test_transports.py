import json
import os
import selectors
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from xorchestrate.mcp import MCPServer
from xorchestrate.model import Capability

ROOT = Path(__file__).resolve().parents[2]
SESSION_FIXTURE = Path(__file__).parent / "fixtures" / "session_server.py"


class InteractiveProcess:
    """An actual CLI peer with bounded waits, even if a regression writes no line."""

    def __init__(self, test, command, cwd, env):
        self.test = test
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, cwd=cwd, env=env, bufsize=0)
        self.pending = b""
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        test.addCleanup(self.close)

    def write(self, value):
        self.process.stdin.write(value if isinstance(value, bytes) else json.dumps(value).encode() + b"\n")
        self.process.stdin.flush()

    def read(self):
        deadline = time.monotonic() + 10
        while b"\n" not in self.pending:
            remaining = deadline - time.monotonic()
            self.test.assertGreater(remaining, 0, "CLI did not emit a complete response within ten seconds")
            ready = self.selector.select(remaining)
            self.test.assertTrue(ready, "CLI did not respond within ten seconds")
            chunk = os.read(self.process.stdout.fileno(), 65536)
            self.test.assertTrue(chunk, "CLI closed stdout before returning a response")
            self.pending += chunk
        line, self.pending = self.pending.split(b"\n", 1)
        return json.loads(line)

    def request(self, value):
        self.write(value)
        return self.read()

    def finish(self):
        self.process.stdin.close()
        self.process.stdin = None
        stdout, stderr = self.process.communicate(timeout=10)
        self.test.assertEqual(self.process.returncode, 0, stderr.decode())
        self.test.assertEqual(self.pending + stdout, b"", "Unexpected non-protocol stdout")
        self.test.assertEqual(stderr, b"", "Unexpected stderr from successful CLI process")

    def close(self):
        self.selector.close()
        if self.process.poll() is None:
            self.process.kill()
        self.process.communicate(timeout=10)


class TransportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.config = self.directory / "config.json"
        self.options = {"store": {"adapter": "sqlite", "options": {"path": "state.sqlite3"}}}
        self.save_config()
        self.environment = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT), str(self.directory), os.environ.get("PYTHONPATH", "")])}

    def save_config(self):
        self.config.write_text(json.dumps(self.options), encoding="utf-8")

    def command(self, *arguments, actor="owner", configured=True, max_frame=None):
        command = [sys.executable, "-m", "xorchestrate"]
        if configured:
            command += ["--config", str(self.config)]
        command += ["--actor", actor]
        if max_frame is not None:
            command += ["--max-frame-bytes", str(max_frame)]
        return command + list(arguments)

    def cli(self, *arguments, input=None, actor="owner", configured=True):
        return subprocess.run(self.command(*arguments, actor=actor, configured=configured),
                              input=input, capture_output=True, cwd=self.directory, env=self.environment, timeout=15)

    def call(self, action, arguments, actor="owner"):
        result = self.cli("call", action, input=json.dumps(arguments).encode(), actor=actor)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stderr, b"")
        return json.loads(result.stdout)

    def interactive(self, command, actor="owner", max_frame=None):
        return InteractiveProcess(self, self.command(command, actor=actor, max_frame=max_frame), self.directory, self.environment)

    @staticmethod
    def rpc(identifier, method, params=None):
        return {"jsonrpc": "2.0", "id": identifier, "method": method, "params": params or {}}

    def initialize(self, process):
        result = process.request(self.rpc(1, "initialize", {"protocolVersion": "2025-06-18", "clientInfo": {"name": "fixture", "version": "1"}, "capabilities": {}}))
        self.assertEqual(result["result"]["serverInfo"]["name"], "xorchestrate")
        process.write({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def test_describe_and_default_cli_setup_need_no_evaluator(self):
        result = self.cli("describe", configured=False)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(result.stderr, b"")
        operations = {item["name"]: item for item in json.loads(result.stdout)["operations"]}
        self.assertIn("create_work", operations)
        self.assertIn("invoke", operations)
        self.assertIn("goal", operations["create_work"]["input_schema"]["required"])
        result = self.cli("call", "create_work", "--json", '{"goal":"A complex feature"}', configured=False)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(json.loads(result.stdout)["work"]["goal"], "A complex feature")

    def test_same_persisted_work_is_created_and_read_through_both_transports(self):
        goal = "Add a feature using project evidence from configured tooling"
        work = self.call("create_work", {"goal": goal, "inputs": {"source": "tooling"}}, actor="cli-operator")["work"]
        process = self.interactive("serve", actor="mcp-operator")
        self.initialize(process)
        reply = process.request(self.rpc(2, "tools/call", {"name": "get_work", "arguments": {"work_id": work["id"]}}))
        received = reply["result"]["structuredContent"]["work"]
        self.assertEqual(received["goal"], goal)
        self.assertEqual(received["created_by"], "cli-operator")
        self.assertEqual(json.loads(reply["result"]["content"][0]["text"])["work"], received)
        second = process.request(self.rpc(3, "tools/call", {"name": "create_work", "arguments": {"goal": goal}}))["result"]["structuredContent"]["work"]
        read = self.call("get_work", {"work_id": second["id"]})["work"]
        self.assertEqual(read["created_by"], "mcp-operator")
        self.assertEqual(read["goal"], goal)
        process.finish()

    def test_fixed_process_actor_cannot_be_replaced_by_tool_arguments(self):
        self.options["guardrail"] = {"adapter": "rules", "options": {"default": "deny", "rules": [
            {"effect": "allow", "actions": ["create_work"], "actors": ["worker"]}]}}
        self.save_config()
        process = self.interactive("serve", actor="worker")
        self.initialize(process)
        forged = process.request(self.rpc(2, "tools/call", {"name": "create_work", "arguments": {"goal": "work", "actor": "owner"}}))
        self.assertTrue(forged["result"]["isError"])
        self.assertEqual(forged["result"]["structuredContent"]["error"]["code"], "invalid_arguments")
        request = self.rpc(3, "tools/call", {"name": "create_work", "arguments": {"goal": "work"}, "actor": "owner"})
        request["actor"] = "owner"
        created = process.request(request)["result"]["structuredContent"]["work"]
        self.assertEqual(created["created_by"], "worker")
        process.finish()
        denied = self.cli("call", "create_work", "--json", '{"goal":"work"}', actor="outsider")
        self.assertEqual(denied.returncode, 2)
        self.assertEqual(json.loads(denied.stderr)["error"]["code"], "denied")

    def test_external_capability_execution_and_replay_work_across_cli_and_mcp(self):
        fixture = Path(__file__).parent / "fixtures" / "capability_server.py"
        definitions = self.directory / "capabilities.json"
        marker = self.directory / "invocations"
        catalog = [{"name": "project.inspect", "description": "Inspect configured project context", "input_schema": {
            "type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"], "additionalProperties": False}}]
        definitions.write_text(json.dumps(catalog), encoding="utf-8")
        self.options["providers"] = {"tools": {"adapter": "cli", "options": {
            "command": [sys.executable, str(fixture), "cli", "--definitions", str(definitions), "--marker", str(marker)],
            "capabilities": catalog, "timeout": 10}}}
        self.save_config()
        work = self.call("create_work", {"goal": "Use observed project evidence", "context": {"project": "supplied-by-tooling"}})["work"]
        arguments = {"capability": "tools/project.inspect", "arguments": {"value": "observations"},
                     "work_id": work["id"], "idempotency_key": "inspection-1"}
        first = self.call("invoke", arguments)
        self.assertEqual(first["invocation"]["result"]["context"], {
            "actor": "owner", "work_id": work["id"], "operation_id": first["operation_id"],
            "capability": {**catalog[0], "annotations": {}},
        })
        process = self.interactive("serve")
        self.initialize(process)
        replay = process.request(self.rpc(2, "tools/call", {"name": "invoke", "arguments": arguments}))
        self.assertEqual(replay["result"]["structuredContent"], first)
        self.assertEqual(marker.read_text().splitlines(), ["project.inspect"])
        second = process.request(self.rpc(3, "tools/call", {"name": "invoke", "arguments": {**arguments, "idempotency_key": "inspection-2"}}))
        self.assertFalse(second["result"]["isError"])
        self.assertEqual(marker.read_text().splitlines(), ["project.inspect", "project.inspect"])
        process.finish()
        state = self.call("get_work", {"work_id": work["id"]})
        self.assertEqual(len(state["evidence"]), 2)
        self.assertTrue(all(item["source"]["type"] == "capability" for item in state["evidence"]))

    def test_run_recovers_after_bad_frames_and_keeps_clean_json_stdout(self):
        process = self.interactive("run", actor="runner", max_frame=256)
        for frame, expected in ((b"{bad json}\n", "invalid_json"), (b"\xff\n", "invalid_json"),
                                (b"x" * 600 + b"\n", "frame_too_large"),
                                (b'{"action":"list_work","action":"create_work"}\n', "invalid_json")):
            response = process.request(frame)
            self.assertEqual(response["error"]["code"], expected)
        result = process.request({"action": "create_work", "arguments": {"goal": "After invalid frames"}, "actor": "forged"})
        self.assertEqual(result["result"]["work"]["created_by"], "runner")
        work_id = result["result"]["work"]["id"]
        read = process.request({"action": "get_work", "arguments": {"work_id": work_id}})
        self.assertEqual(read["result"]["work"]["goal"], "After invalid frames")
        process.finish()

    def test_mcp_serve_recovers_from_bad_frames_and_requires_initialization(self):
        process = self.interactive("serve", max_frame=256)
        self.assertEqual(process.request(self.rpc(1, "tools/list"))["error"]["code"], -32002)
        for frame in (b"no json\n", b"\xff\n", b"x" * 600 + b"\n"):
            self.assertEqual(process.request(frame)["error"]["code"], -32700)
        self.initialize(process)
        listed = process.request(self.rpc(2, "tools/list"))
        self.assertIn("create_work", {tool["name"] for tool in listed["result"]["tools"]})
        self.assertEqual(process.request(self.rpc(3, "missing"))["error"]["code"], -32601)
        self.assertEqual(process.request(self.rpc(4, "tools/call", {"name": "get_work", "arguments": []}))["error"]["code"], -32602)
        process.finish()

    def test_deep_json_cannot_terminate_a_persistent_connection(self):
        for mode in ("run", "serve"):
            process = self.interactive(mode)
            if mode == "serve":
                self.initialize(process)
            invalid = b"[" * 10000 + b"0" + b"]" * 10000 + b"\n"
            self.assertIn("error", process.request(invalid))
            arguments = b'{"goal":"Complex input","context":{"nested":' + b"[" * 500 + b"0" + b"]" * 500 + b"}}"
            if mode == "run":
                packet = b'{"action":"create_work","arguments":' + arguments + b'}\n'
            else:
                packet = b'{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"create_work","arguments":' + arguments + b'}}\n'
            # Whether the implementation can accept this depth or rejects it,
            # the failure belongs to this request, not the persistent process.
            self.assertIsInstance(process.request(packet), dict)
            subsequent = {"action": "list_work", "arguments": {}} if mode == "run" else self.rpc(3, "ping")
            self.assertIn("result", process.request(subsequent))
            process.finish()

    def test_run_retains_live_session_but_new_process_does_not_reuse_stale_handle(self):
        self.options["sessions"] = {"fixture": {"adapter": "jsonrpc", "options": {
            "command": [sys.executable, str(SESSION_FIXTURE), "jsonrpc"], "timeout": 10}}}
        self.save_config()
        process = self.interactive("run", actor="session-owner")
        started = process.request({"action": "start_session", "arguments": {
            "driver": "fixture", "specification": {"goal": "Feature work"}, "idempotency_key": "start-1"}})
        session_id = started["result"]["session"]["id"]
        sent = process.request({"action": "send_session", "arguments": {
            "session_id": session_id, "message": {"text": "Continue work"}, "idempotency_key": "send-1"}})
        self.assertTrue(sent["result"]["output"]["accepted"])
        self.assertEqual(sent["result"]["output"]["start"]["context"]["actor"], "session-owner")
        polled = process.request({"action": "poll_session", "arguments": {"session_id": session_id, "idempotency_key": "poll-1"}})
        self.assertEqual(polled["result"]["output"], [{"kind": "message", "message": {"text": "Continue work"}}])
        process.finish()
        stale = self.cli("call", "send_session", input=json.dumps({"session_id": session_id,
            "message": {"text": "No implicit restart"}, "idempotency_key": "send-after-exit"}).encode(), actor="session-owner")
        self.assertEqual(stale.returncode, 2)
        error = json.loads(stale.stderr)["error"]
        # Runtime shutdown now durably marks the connection disconnected, so
        # application lifecycle validation refuses before starting an effect.
        self.assertEqual(error["code"], "invalid_state")
        self.assertEqual(stale.stdout, b"")

    def test_cli_input_file_and_failures_do_not_print_traceback_or_argument_secrets(self):
        argument_file = self.directory / "arguments.json"
        argument_file.write_text('{"goal":"Read from file"}', encoding="utf-8")
        result = self.cli("call", "create_work", "--input", str(argument_file))
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual(json.loads(result.stdout)["work"]["goal"], "Read from file")
        result = self.cli("call", "create_work", "--json", '{"goal":"work","secret":"DO-NOT-ECHO-THIS"}')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(json.loads(result.stderr)["error"]["code"], "invalid_arguments")
        self.assertNotIn(b"DO-NOT-ECHO-THIS", result.stderr)
        self.assertNotIn(b"Traceback", result.stderr)
        (self.directory / "broken_adapter.py").write_text(
            'class Policy:\n    def __init__(self, options): pass\n    def evaluate(self, action, context): raise RuntimeError("private-token-diagnostic")\n', encoding="utf-8")
        self.options["guardrail"] = {"adapter": "broken_adapter:Policy"}
        self.save_config()
        result = self.cli("call", "create_work", "--json", '{"goal":"work"}')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stderr)["error"]["code"], "internal_error")
        self.assertNotIn(b"private-token-diagnostic", result.stderr)
        self.assertNotIn(b"Traceback", result.stderr)


class MCPProtocolTests(unittest.TestCase):
    def test_server_validates_envelopes_without_executing_notifications(self):
        class Application:
            def __init__(self):
                self.calls = []

            def call(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                return {}

            def describe(self):
                return [Capability("action", "description")]

        app = Application()
        server = MCPServer(app, "fixed-principal")
        for request in ([], {"jsonrpc": "1.0", "id": 1, "method": "ping"},
                        {"jsonrpc": "2.0", "id": True, "method": "ping"}):
            self.assertEqual(server.handle(request)["error"]["code"], -32600)
        self.assertEqual(server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})["error"]["code"], -32602)
        self.assertIsNone(server.handle({"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "action"}}))
        self.assertEqual(app.calls, [])
        server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
        server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "action", "actor": "forged"}})
        self.assertEqual(app.calls, [(("action", {}), {"actor": "fixed-principal"})])

    def test_server_sanitizes_unexpected_adapter_errors(self):
        class Application:
            def call(self, *args, **kwargs):
                raise RuntimeError("private-token-diagnostic")

        server = MCPServer(Application())
        server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
        response = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "action"}})
        self.assertEqual(response["error"]["code"], -32603)
        self.assertNotIn("private-token-diagnostic", json.dumps(response))


if __name__ == "__main__":
    unittest.main()
