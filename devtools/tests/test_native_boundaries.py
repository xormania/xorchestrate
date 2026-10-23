import asyncio
import json
import os
import tomllib
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from fixtures.native_boundaries import DELEGATION, NativeBoundaryFixture

from devtools import role_packages
from devtools.api import API
from devtools.console import command
from devtools.native import claude
from devtools.profiles import permitted, prepare, tool_catalogue


class NativeBoundaryTests(NativeBoundaryFixture, unittest.TestCase):

    def test_managed_worker_cannot_use_operator_command_builder(self):
        for harness in ("codex", "claude", "grok"):
            with self.subTest(harness=harness):
                config = {"binary": "fixture", "harness": harness, "operator_console": True}
                private, _, server = prepare(self.directory, "worker", harness)
                with self.assertRaisesRegex(ValueError, "appointed external CO"):
                    command(self.directory, "worker", config, private, server)


    def test_known_unknown_and_wrapped_native_delegation_is_not_permitted(self):
        for harness in ("codex", "claude", "grok"):
            for tool in DELEGATION:
                with self.subTest(harness=harness, tool=tool):
                    self.assertFalse(permitted(harness, tool, {}))
                    self.assertFalse(permitted(harness, "use_tool", {"tool_name": tool, "tool_input": {}}))
        for tool_input in (None, [], {"tool_name": "Task", "tool_input": {}}):
            self.assertFalse(permitted("grok", "use_tool", {"tool_name": "use_tool", "tool_input": tool_input}))
        for harness in ("unknown", None, {}, []):
            self.assertFalse(permitted(harness, "mcp__agentscient_dev__work_context", {}))
        for harness, tool in (("codex", "update_plan"), ("codex", "request_user_input"),
                              ("claude", "EndConversation"), ("claude", "StructuredOutput"),
                              ("grok", "search_tool")):
            self.assertTrue(permitted(harness, tool, {}))

    def test_codex_denied_delegation_does_not_reach_either_provider_or_change_work(self):
        native = self.native("codex")
        before = self.effects()
        for tool in (*DELEGATION, "use_tool", "mcp__agentscient_dev__spawn_agent"):
            with self.subTest(tool=tool), self.assertRaisesRegex(ValueError, "MCP tools"):
                asyncio.run(native.request("item/tool/call", {"threadId": native.session, "tool": tool,
                    "arguments": {"tool_name": "spawn_agent", "tool_input": {}}}))
        with self.assertRaisesRegex(ValueError, "No native client capability"):
            asyncio.run(native.request("agent/spawn", {"threadId": native.session}))
        self.assertEqual(before, self.effects())
        native.mcp.request.assert_not_awaited()
        native.serena.request.assert_not_awaited()

    def test_codex_scoped_mcp_proxy_still_reaches_provider(self):
        native = self.native("codex")
        native.mcp.request.return_value = {"content": [{"type": "text", "text": "current brief"}]}
        result = asyncio.run(native.request("item/tool/call", {"threadId": native.session,
                               "tool": "work_context", "arguments": {}}))
        self.assertTrue(result["success"])
        native.mcp.request.assert_awaited_once_with("tools/call", {"name": "work_context", "arguments": {}})
        native.serena.request.assert_not_awaited()


    def test_grok_direct_and_wrapped_delegation_denies_without_effects(self):
        native = self.native("grok")
        before = self.effects()
        for tool in DELEGATION:
            for name, arguments in ((tool, {}), ("use_tool", {"tool_name": tool, "tool_input": {}})):
                with self.subTest(tool=name, arguments=arguments):
                    self.assertEqual({"outcome": {"outcome": "selected", "optionId": "reject"}},
                                     self.permission(native, name, arguments))
        self.assertEqual({"outcome": {"outcome": "cancelled"}}, self.permission(native, "Task", options=[
            {"kind": "allow_once", "optionId": "allow"}]))
        self.assertEqual(before, self.effects())
        native.mcp.request.assert_not_awaited()
        native.serena.request.assert_not_awaited()

    def test_grok_mcp_wrappers_and_discovery_helper_remain_allowed(self):
        native = self.native("grok")
        for tool, arguments in (("search_tool", {}), ("agentscient_dev__work_context", {}),
                                ("use_tool", {"tool_name": "agentscient_dev__work_context", "tool_input": {}})):
            with self.subTest(tool=tool):
                self.assertEqual({"outcome": {"outcome": "selected", "optionId": "allow"}},
                                 self.permission(native, tool, arguments))
        self.assertEqual({"outcome": {"outcome": "selected", "optionId": "reject"}},
                         self.permission(native, "search_tool", namespace="unknown"))

    def test_private_profiles_disable_native_delegation_despite_operator_settings(self):
        codex_home, grok_home = self.home / "codex", self.home / "grok"
        codex_home.mkdir()
        grok_home.mkdir()
        codex_config = 'model = "selected-model"\nfeatures = { multi_agent = true }\n'
        grok_config = 'subagents = { enabled = true }\nworkflows = { enabled = true }\ngoal = { enabled = true }\n'
        (codex_home / "config.toml").write_text(codex_config)
        (grok_home / "config.toml").write_text(grok_config)
        private, _, _ = prepare(self.directory, "worker", "codex")
        config = tomllib.loads((private / "config.toml").read_text())
        self.assertFalse(config["features"]["multi_agent"])
        self.assertEqual("selected-model", config["model"])
        with patch.dict(os.environ, {
            "GROK_SUBAGENTS": "1", "GROK_WORKFLOWS": "1", "GROK_GOAL": "1",
        }):
            native = self.native("grok")
        config = tomllib.loads((native.private / "config.toml").read_text())
        for feature in ("subagents", "workflows", "goal"):
            self.assertEqual({"enabled": False}, config.get(feature))
            self.assertEqual("0", native.env["GROK_" + feature.upper()])
        self.assertEqual(codex_config, (codex_home / "config.toml").read_text())
        self.assertEqual(grok_config, (grok_home / "config.toml").read_text())

    def test_grok_launch_disables_subagents_before_creating_acp_session(self):
        native = self.native("grok")
        rpc = Mock(request=AsyncMock(side_effect=[{"authMethods": []}, {"sessionId": "bound-session"}]))
        native.spawn = AsyncMock(return_value=rpc)
        asyncio.run(native.start())
        argv = native.spawn.await_args.args[0]
        self.assertIn("--no-subagents", argv)
        self.assertEqual(["agent", "stdio"], argv[-2:])
        self.assertEqual("bound-session", native.session)

    def test_managed_claude_profiles_keep_role_ceiling_and_live_tool_authority(self):
        config = {"binary": "fixture", "workspace": str(self.workspace), "harness": "claude"}
        api = API(self.directory, "worker")
        process = Mock(pid=12345, returncode=0)
        process.poll.return_value = 0
        with patch("devtools.native.subprocess.Popen", return_value=process) as spawn, \
                patch("devtools.processes.register"), patch("devtools.native.os.killpg"):
            claude(self.directory, "worker", config, self.generation)
        argv = spawn.call_args.args[0]
        allowed = set(argv[argv.index("--allowedTools") + 1].split(","))
        role = api.commands.role(api.control.snapshot())
        expected = {"mcp__agentscient_dev__" + name
                    for group in role_packages.role(role)["mcp"].values() for name in group}
        self.assertEqual(expected, allowed)
        self.assertIn("mcp__agentscient_dev__work_context", allowed)
        self.assertEqual("", argv[argv.index("--tools") + 1])
        self.assertEqual({"Agent", "Task"}, set(argv[argv.index("--disallowedTools") + 1].split(",")))
        self.assertIn("--disable-slash-commands", argv)
        self.assertIn("--strict-mcp-config", argv)
        self.assertEqual("", argv[argv.index("--setting-sources") + 1])
        settings = json.loads(Path(argv[argv.index("--settings") + 1]).read_text())
        self.assertIn("PreToolUse", settings["hooks"])
        self.assertFalse(permitted("claude", "mcp__agentscient_dev__orchestrate", {"work": []},
                                   catalogue=tool_catalogue(self.directory, "worker")))
