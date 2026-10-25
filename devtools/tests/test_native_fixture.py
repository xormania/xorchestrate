"""Exercise the protocol fixture's tool contract even when tmux is unavailable."""

import asyncio
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import claude_fixture
from fixtures.control import ControlFixture
from native_fixture import Fixture
from support import fixture_environment, until

from devtools.api import API
from devtools.profiles import prepare
from devtools.rpc import RPC
from devtools.telemetry import Trace, Transcript


class NativeFixtureTests(unittest.IsolatedAsyncioTestCase):
    setUp = ControlFixture.setUp
    client = ControlFixture.client

    async def test_current_xo_catalog_starts_the_native_fixture(self):
        fixture = Fixture()
        tools = API(self.state, "xo").tools()
        self.assertIn("conclude", {tool["name"] for tool in tools})
        with patch.object(fixture, "write") as output, patch.object(fixture, "tool", new=AsyncMock(return_value=API(self.state, "xo").call("onboard", {}))):
            await fixture.handle({"id": 1, "method": "thread/start", "params": {
                "sandbox": "read-only", "dynamicTools": tools,
            }})
        packet = output.call_args.args[0]
        self.assertEqual(1, packet["id"])
        self.assertEqual(fixture.session, packet["result"]["thread"]["id"])
        self.assertTrue(fixture.command)

    async def test_native_fixture_rejects_an_unapproved_tool(self):
        fixture = Fixture()
        tools = [*API(self.state, "xo").tools(), {"name": "exec_command"}]
        with patch.object(fixture, "write") as output, patch.object(fixture, "tool", new=AsyncMock(return_value=API(self.state, "xo").call("onboard", {}))):
            await fixture.handle({"id": 2, "method": "thread/start", "params": {
                "sandbox": "read-only", "dynamicTools": tools,
            }})
        packet = output.call_args.args[0]
        self.assertEqual(2, packet["id"])
        self.assertNotIn("result", packet)
        self.assertEqual(-32603, packet["error"]["code"])

    async def test_native_fixture_rejects_a_missing_declared_mcp_tool(self):
        fixture = Fixture()
        api = API(self.state, "xo")
        tools = [tool for tool in api.tools() if tool["name"] != "plan_adopt"]
        with patch.object(fixture, "write") as output, patch.object(fixture, "tool", new=AsyncMock(return_value=api.call("onboard", {}))):
            await fixture.handle({"id": 3, "method": "thread/start", "params": {
                "sandbox": "read-only", "dynamicTools": tools,
            }})
        self.assertNotIn("result", output.call_args.args[0])

    async def test_subprocess_fixture_checks_mcp_surface_during_startup(self):
        api = API(self.state, "xo")
        requests = []

        async def proxy(method, params):
            requests.append((method, params["tool"]))
            self.assertEqual("item/tool/call", method)
            value = api.call(params["tool"], params["arguments"])
            return {"success": True, "contentItems": [{"text": json.dumps(value)}]}

        async def ignore(method, params):
            return None

        process = await asyncio.create_subprocess_exec(
            sys.executable, str(Path(__file__).with_name("native_fixture.py")), "app-server",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        rpc = RPC(process, proxy, ignore, jsonrpc=False)
        try:
            result = await rpc.request("thread/start", {"sandbox": "read-only", "dynamicTools": api.tools()}, timeout=10)
            self.assertTrue(result["thread"]["id"].startswith("fixture-native-"))
            self.assertEqual([("item/tool/call", "onboard")], requests)
        finally:
            await rpc.close()

    async def test_claude_fixture_registers_transcript_in_its_isolated_config_directory(self):
        environment = fixture_environment(self.state)
        self.assertNotEqual(Path(environment["HOME"]) / ".claude", Path(environment["CLAUDE_CONFIG_DIR"]))
        generation = self.store.begin_session("coder", os.getpid())
        self.store.session("coder", generation=generation, state="idle")
        connections = []

        def capture_connection(*args, **kwargs):
            connection = RPC(*args, **kwargs)
            connections.append(connection)
            return connection

        with patch.dict(os.environ, environment, clear=True):
            private, _, _ = prepare(self.state, "coder", "claude")
            debug = private / "fixture-debug.log"
            argv = ["claude-fixture", "--session-id", "fixture-transcript",
                    "--settings", str(private / "settings.json"),
                    "--mcp-config", str(private / "mcp.json"), "--debug-file", str(debug)]
            with patch.object(claude_fixture.sys, "argv", argv), \
                    patch.object(claude_fixture, "RPC", side_effect=capture_connection):
                task = asyncio.create_task(claude_fixture.main())
                try:
                    await asyncio.to_thread(until, debug.exists)
                    message = self.store.send("xo", "coder", "Observe fixture transcript usage",
                                              key="fixture-usage", work_order=self.order, job=self.job)
                    await asyncio.to_thread(until, lambda: any(
                        row["data"].get("event") == "Stop"
                        for row in self.store.events(actor="coder", kind="native_hook")["events"]))
                    self.assertFalse(self.store.events(actor="coder", kind="transcript_rejected")["events"])
                    registrations = self.store.events(actor="coder", kind="native_transcript")["events"]
                    self.assertTrue(registrations)
                    expected = Path(environment["CLAUDE_CONFIG_DIR"]) / "projects/fixture/fixture-transcript.jsonl"
                    self.assertEqual({str(expected)}, {row["data"]["path"] for row in registrations})
                    self.assertFalse((Path(environment["HOME"]) / ".claude").exists())
                    trace = Trace(self.state, "coder")
                    try:
                        Transcript(self.store, "coder", trace).poll()
                    finally:
                        trace.close()
                    usage = self.store.events(actor="coder", kind="native_usage")["events"]
                    self.assertEqual([{"input_tokens": 23}], [row["data"]["observation"] for row in usage])
                    self.assertEqual(f"reply-{message['id']}", usage[0]["data"]["message"])
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    for connection in connections:
                        await connection.close()


if __name__ == "__main__":
    unittest.main()
