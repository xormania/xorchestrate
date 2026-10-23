"""MCP-only profile canaries; real helper processes, no native-harness claim."""

import json
import os
import sys
import tomllib
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ops.conductor.tools.session_adapters import envelope, grok
from ops.devlane.hooks import enforcement, profiles
from ops.testkit import World, run

ROOT = Path(__file__).resolve().parents[4]


class McpOnlyProfiles(unittest.TestCase):
    def setUp(self):
        self.world = World(prefix="mcp-only-")
        self.addCleanup(self.world.close)
        self.env = self.world.env()
        self.environment = patch.dict(os.environ, self.env, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def profile(self, harness, *, mcp_only=True, serena_python=None, project_config=None):
        return profiles.generate(harness, self.world.root / (harness + str(mcp_only)),
                                 source=ROOT, cwd=self.world.cwd, mode="interactive",
                                 executable=sys.executable, agent="Codex <test@example.test>",
                                 native_settings={"model": "operator-choice", "mcp_servers": {
                                     "unrelated": {"command": "/usr/bin/false"}}}, mcp_only=mcp_only,
                                 serena_python=serena_python, project_config=project_config)

    def test_project_configuration_is_copied_pinned_and_uses_the_same_entry(self):
        config = self.world.plant_text("project.json", json.dumps({
            "version": 1, "provider": "symfony", "php": "/fixture/php", "environment": "test"}))
        for harness in profiles.MEMBER_HARNESSES:
            binding = self.profile(harness, project_config=config)
            profiles.validate(binding)
            retained = Path(binding["project_config"])
            self.assertNotEqual(retained, config)
            self.assertIn(str(retained), binding["config_pins"])
            args = binding["native_settings"]["mcp_servers"]["coordination"]["args"]
            self.assertEqual(args[args.index("--project-config") + 1], str(retained))
            retained.write_text('{}')
            with self.assertRaises(ValueError):
                profiles.validate(binding)

    def test_private_serena_selection_keeps_venv_path_and_one_mcp_entry(self):
        python = self.world.root / "provider/bin/python"
        python.parent.mkdir(parents=True)
        python.symlink_to(sys.executable)
        for harness in profiles.MEMBER_HARNESSES:
            with self.subTest(harness=harness):
                binding = self.profile(harness, serena_python=python)
                profiles.validate(binding)
                self.assertEqual(binding["serena_python"], str(python))
                servers = binding["native_settings"]["mcp_servers"]
                self.assertEqual(set(servers), {"coordination"})
                args = servers["coordination"]["args"]
                self.assertEqual(args[args.index("--serena-python") + 1], str(python))
                if harness == "codex":
                    self.assertEqual(servers["coordination"]["tool_timeout_sec"], 240)
        with self.assertRaises(ValueError):
            self.profile("claude", mcp_only=False, serena_python=python)

    def hook(self, binding, tool, arguments=None, *, event="PreToolUse"):
        payload = {"session_id": "native-" + binding["harness"], "cwd": str(self.world.cwd),
                   "tool_name": tool, "tool_input": arguments or {}}
        result = run(["/bin/sh", binding["helper"], event], cwd=self.world.cwd, env=self.env,
                     stdin=json.dumps(payload), timeout=30)
        self.assertEqual(result.exit, 0, result.stderr)
        return json.loads(result.stdout)

    def outcome(self, response):
        return response.get("hookSpecificOutput", {}).get("permissionDecision")

    def test_profiles_select_one_entry_and_preserve_model(self):
        for harness in profiles.MEMBER_HARNESSES:
            with self.subTest(harness=harness):
                binding = self.profile(harness)
                profiles.validate(binding)
                self.assertEqual(binding["servers"], ["coordination"])
                self.assertEqual(binding["native_settings"]["model"], "operator-choice")
                servers = binding["native_settings"]["mcp_servers"]
                self.assertEqual(set(servers), {"coordination"})
                self.assertTrue(servers["coordination"]["args"][1].endswith("/dev_mcp.py"))
                command, _ = profiles.native_launch(binding, [sys.executable], self.env)
                if harness == "claude":
                    self.assertEqual(command[command.index("--tools") + 1], "")
                    self.assertIn("--strict-mcp-config", command)
                elif harness == "codex":
                    config = tomllib.loads((Path(binding["home"]) / "config.toml").read_text())
                    for key in ("shell_tool", "unified_exec", "apply_patch_freeform", "js_repl", "code_mode"):
                        self.assertFalse(config["features"][key])
                    self.assertIn("features.shell_tool=false", command)
                    self.assertIn("features.hooks=true", command)
                    self.assertTrue(any(arg.startswith("mcp_servers.coordination=") and "dev_mcp.py" in arg for arg in command))

    def test_real_helpers_deny_native_project_tools_and_allow_broker(self):
        seeded = run([sys.executable, "-B", str(ROOT / "ops/conductor/tools/coordination.py"), "bind", "--roles", "worker"],
                     cwd=self.world.cwd, env={**self.env, "LANE_CONF": str(ROOT / "ops/lane.conf"),
                     "WF_AGENT": "Codex <test@example.test>", "CLAUDE_CODE_SESSION_ID": "seed",
                     "COORDINATION_CAPABILITY": "a" * 64}, timeout=30)
        self.assertEqual(seeded.exit, 0, seeded.stderr)
        for harness in profiles.MEMBER_HARNESSES:
            binding = self.profile(harness)
            for tool in ("Bash", "exec_command", "Read", "read_file", "Edit", "apply_patch", "spawn_agent", "UnknownFutureTool"):
                with self.subTest(harness=harness, tool=tool):
                    self.assertEqual(self.outcome(self.hook(binding, tool)), "deny")
            with self.subTest(harness=harness, tool="broker"):
                if harness == "grok":
                    tool, arguments = "use_tool", {"tool_name": "coordination__capability_search", "tool_input": {}}
                else:
                    tool, arguments = "mcp__coordination__capability_search", {}
                response = self.hook(binding, tool, arguments)
                self.assertEqual(self.outcome(response), "allow", response)
                # The entry launched by this exact profile can read the real
                # participant established by the helper, without a shell tool.
                server = binding["native_settings"]["mcp_servers"]["coordination"]
                request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                    "name": "capability_invoke", "arguments": {"capability": "coord_inbox", "arguments": {}}}}
                reply = run([server["command"], *server["args"]], cwd=self.world.cwd,
                            env={**self.env, **server["env"]}, stdin=json.dumps(request) + "\n", timeout=30)
                self.assertEqual(reply.exit, 0, reply.stderr)
                self.assertFalse(json.loads(reply.stdout)["result"]["isError"], reply.stdout)

    def test_wrappers_cannot_name_another_server_or_native_tool(self):
        for target in ("shell", "coordination__exec_command", "serena__execute_code", "coordination__coord_send"):
            with self.subTest(target=target):
                result = enforcement.mcp_only_decision("grok", {"tool_name": "use_tool", "tool_input": {
                    "tool_name": target, "tool_input": {}}})
                self.assertEqual(result["outcome"], "deny")
        for harness in profiles.MEMBER_HARNESSES:
            result = enforcement.mcp_only_decision(harness, {
                "tool_name": "mcp__other__capability_search", "tool_input": {}})
            self.assertEqual(result["outcome"], "deny")
            for name, args in (("capability_search", {}), ("capability_describe", {"capability": "coord_ack"}),
                               ("capability_invoke", {"capability": "coord_ack", "arguments": {"message": "M-1"}})):
                self.assertEqual(enforcement.mcp_only_decision(harness, {
                    "toolName": "mcp__coordination__" + name, "toolInput": args})["outcome"], "allow")

    def test_malformed_calls_and_broken_profile_fail_closed(self):
        binding = self.profile("codex")
        self.assertEqual(self.outcome(self.hook(binding, "mcp__coordination__capability_invoke", {"capability": "coord_ack"})), "deny")
        config = Path(binding["home"]) / "config.toml"
        config.write_text(config.read_text() + "# changed\n")
        self.assertEqual(self.outcome(self.hook(binding, "mcp__coordination__capability_search")), "deny")
        self.assertNotEqual(self.outcome(self.hook(binding, "Read", event="PostToolUse")), "deny")

    def test_standard_profile_keeps_advisory_behavior(self):
        binding = self.profile("claude", mcp_only=False)
        self.assertNotEqual(self.outcome(self.hook(binding, "UnknownFutureTool")), "deny")
        settings = Path(binding["helper"]).parent / "settings.json"
        settings.write_text("{}")
        self.assertNotEqual(self.outcome(self.hook(binding, "Read")), "deny")

    def test_native_arguments_cannot_override_selection(self):
        for harness, tokens in (("claude", ["--tools", "Read"]), ("codex", ["--enable", "shell_tool"]),
                                ("codex", ["-c", 'mcp_servers.evil.command="sh"']), ("grok", ["--allow", "*"])):
            with self.subTest(harness=harness), self.assertRaises(ValueError):
                profiles.interactive_args(harness, tokens)

    def test_grok_protocol_retains_entry_permissions_and_message_identity(self):
        binding = self.profile("grok")
        adapter = grok.Adapter()
        self.assertEqual(adapter.channel({}, binding), ["--no-auto-update", "agent", "stdio"])
        self.assertEqual([row["name"] for row in adapter.servers], ["coordination"])
        rpc = Mock()
        for tool in ("capability_search", "capability_describe", "capability_invoke"):
            params = {"toolCall": {"_meta": {"x.ai/tool": {"name": "use_tool", "namespace": "grok_build"}},
                                  "rawInput": {"tool_name": "coordination__" + tool, "tool_input": {}}},
                      "options": [{"kind": "allow_once", "optionId": "once"},
                                  {"kind": "allow_always", "optionId": "always"}]}
            with patch("builtins.print"):
                adapter.answer(rpc, {"id": 1}, "session/request_permission", params, None)
            rpc.answer.assert_called_with({"id": 1}, result={"outcome": {"outcome": "selected", "optionId": "once"}})
        text = envelope({"message": "M-7", "purpose": "request", "sender": "PT-2", "body": "Review this edit"}, mcp_only=True)
        self.assertIn("[coordination M-7]", text)
        self.assertIn("capability_invoke", text)
        self.assertIn("reply_to=M-7", text)

    def test_prepare_does_not_copy_cli_onboarding_or_other_servers(self):
        auth = self.world.home / ".codex/auth.json"
        auth.parent.mkdir()
        auth.write_text('{"OPENAI_API_KEY":"fixture-not-a-credential"}')
        binding = profiles.prepare_interactive("codex", self.world.root / "prepared", source=ROOT,
                    cwd=self.world.cwd, agent="Codex <test@example.test>", executable=sys.executable, mcp_only=True)
        profiles.validate(binding)
        self.assertFalse(binding["member_skills"])
        self.assertFalse((Path(binding["home"]) / "skills").exists())

    def test_dispatch_and_unverified_harness_are_not_silently_enabled(self):
        for harness, mode in (("codex", "worker"), ("agy", "interactive")):
            with self.subTest(harness=harness), self.assertRaisesRegex(ValueError, "persistent member"):
                profiles.generate(harness, self.world.root / "unsupported", source=ROOT, cwd=self.world.cwd,
                                  mode=mode, session="issued", mcp_only=True)


if __name__ == "__main__":
    unittest.main()
