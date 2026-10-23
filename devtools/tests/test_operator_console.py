"""Operator console privileges do not leak into managed role profiles."""

import copy
import json
import os
import sys
import tomllib
import unittest
from itertools import product
from unittest.mock import patch

from fixtures.native_boundaries import NativeBoundaryFixture
from support import runtime_policy

from devtools import role_packages, runtime
from devtools.api import API
from devtools.console import command
from devtools.profiles import hook, prepare


class OperatorConsoleTests(NativeBoundaryFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.team_file = self.directory / "team.json"
        self.team = json.loads(self.team_file.read_text())
        self.team["co"] = "owner"
        self.team_file.write_text(json.dumps(self.team))
        self.codex_binary = self.root / "codex-fixture"
        self.codex_version("codex-cli 0.122.0")

    def codex_version(self, output, exit_code=0):
        self.codex_binary.write_text(
            f"#!{sys.executable}\nimport sys\n"
            "assert sys.argv[1:] == ['--version']\n"
            f"print({output!r})\nraise SystemExit({exit_code})\n"
        )
        self.codex_binary.chmod(0o700)

    def config(self, harness):
        return {"binary": str(self.codex_binary) if harness == "codex" else "fixture",
                "workspace": str(self.workspace), "harness": harness,
                "model": "initial-model", "reasoning": "high", "operator_console": True}

    def test_unsupported_codex_is_rejected_before_operator_profile_is_written(self):
        for output, code in (("codex-cli 0.121.0", 0), ("codex-cli 0.122.0-alpha.1", 0),
                             ("unknown-client 9.9.9", 0), ("codex-cli 0.122.0", 1)):
            with self.subTest(output=output, exit_code=code):
                self.codex_version(output, code)
                with self.assertRaisesRegex(ValueError, "Codex CLI 0.122.0 or newer"):
                    prepare(self.directory, "owner", "codex", config=self.config("codex"))
                self.assertFalse((self.directory / "profiles").exists())
        # Workers keep their existing protocol checks; the CO version gate
        # must not prevent creation of a managed profile on the same binary.
        _, _, server = prepare(self.directory, "worker", "codex", config={"binary": str(self.codex_binary)})
        self.assertNotIn("default_tools_approval_mode", server)

    def test_claude_owner_keeps_native_controls_and_observes_without_tool_denial(self):
        config = self.config("claude")
        native_environment = {"CLAUDE_CODE_SIMPLE": "1", "CLAUDE_CODE_SAFE_MODE": "1",
                              "CLAUDE_CONFIG_DIR": str(self.home / "custom-claude")}
        with patch.dict(os.environ, native_environment):
            private, environment, server = prepare(self.directory, "owner", "claude", config=config)
        argv, _ = command(self.directory, "owner", config, private, server)
        for flag in ("--disable-slash-commands", "--setting-sources", "--strict-mcp-config",
                     "--tools", "--disallowedTools", "--allowedTools"):
            self.assertNotIn(flag, argv)
        self.assertEqual("initial-model", argv[argv.index("--model") + 1])
        self.assertEqual("high", argv[argv.index("--effort") + 1])
        self.assertEqual(str(self.home), environment["HOME"])
        self.assertEqual(native_environment, {name: environment.get(name) for name in native_environment})
        self.assertEqual({"SessionStart", "PostToolUse", "Stop"},
                         set(json.loads((private / "settings.json").read_text())["hooks"]))
        self.assertNotIn("--operator", server["args"])
        self.assertEqual("owner", server["args"][server["args"].index("--member") + 1])
        api = API(self.directory, "owner")
        exposed = {row["name"] for row in api._tools(api.control.snapshot())}
        self.assertIn("work_report", exposed)
        self.assertNotIn("orchestrate", exposed)
        with self.assertRaisesRegex(ValueError, "not available"):
            api.call("orchestrate", {"work": []})

    def test_codex_owner_keeps_native_home_keyring_and_configuration(self):
        original = self.home / "codex"
        original.mkdir()
        settings = ('cli_auth_credentials_store = "keyring"\n'
                    'features = { multi_agent = true, shell_tool = true, plugins = true }\n'
                    'sandbox_mode = "workspace-write"\n'
                    'approval_policy = "never"\n'
                    'developer_instructions = "Owner instructions"\n'
                    'mcp_servers.personal.command = "personal-server"\n'
                    'mcp_servers.agentscient_dev.tools.direct.approval_mode = "prompt"\n')
        (original / "config.toml").write_text(settings)
        config = self.config("codex") | {"provider": "chosen-provider"}
        private, environment, server = prepare(self.directory, "owner", "codex", config=config)
        argv, _ = command(self.directory, "owner", config, private, server)
        self.assertEqual(str(original), environment["CODEX_HOME"])
        self.assertEqual(str(self.home), environment["HOME"])
        self.assertEqual(settings, (original / "config.toml").read_text())
        self.assertFalse((private / "config.toml").exists())
        overrides = [argv[index + 1] for index, arg in enumerate(argv) if arg == "-c"]
        options = tomllib.loads("\n".join(overrides))
        self.assertTrue(any(row.startswith("mcp_servers.agentscient_dev=") for row in overrides))
        self.assertFalse(any(row.startswith("mcp_servers=") for row in overrides))
        self.assertEqual(server, options["mcp_servers"]["agentscient_dev"])
        self.assertEqual("approve", server["default_tools_approval_mode"])
        # Leave explicit native per-tool policies available to Codex's config merge.
        self.assertNotIn("tools", options["mcp_servers"]["agentscient_dev"])
        self.assertEqual({"agentscient_dev"}, set(options["mcp_servers"]))
        self.assertEqual("initial-model", options["model"])
        self.assertEqual("high", options["model_reasoning_effort"])
        self.assertEqual("chosen-provider", options["model_provider"])
        for key in ("features", "sandbox_mode", "approval_policy", "developer_instructions"):
            self.assertNotIn(key, options)
        self.assertEqual({"SessionStart", "PostToolUse", "Stop"}, set(options["hooks"]))
        self.assertNotIn("--operator", server["args"])

    def test_codex_mcp_approval_default_is_only_for_operator_console(self):
        # Managed Codex uses its existing dynamic-tool transport, not CO's TUI
        # approval default. Nor should a Codex-only field reach Claude or Grok.
        for harness in ("codex", "claude", "grok"):
            with self.subTest(harness=harness):
                worker_private, _, worker_server = prepare(self.directory, "worker", harness)
                self.assertNotIn("default_tools_approval_mode", worker_server)
                _, _, owner_server = prepare(self.directory, "owner", harness, config=self.config(harness))
                if harness == "codex":
                    self.assertEqual("approve", owner_server["default_tools_approval_mode"])
                    self.assertEqual({}, tomllib.loads((worker_private / "config.toml").read_text())["mcp_servers"])
                else:
                    self.assertNotIn("default_tools_approval_mode", owner_server)

    def test_operator_mode_requires_appointed_external_co_before_creating_profiles(self):
        scenarios = (("unappointed", "owner"), ("other-member", "worker"),
                     ("appointed-worker", "worker"), ("nonexternal", "owner"),
                     ("stored-managed", "owner"))
        for scenario, member in scenarios:
            with self.subTest(scenario=scenario):
                team = copy.deepcopy(self.team)
                if scenario == "unappointed":
                    del team["co"]
                elif scenario == "appointed-worker":
                    team["co"] = "worker"
                elif scenario == "nonexternal":
                    next(row for row in team["members"] if row["name"] == "owner")["harness"] = "claude"
                elif scenario == "stored-managed":
                    with self.store.connect() as db:
                        db.execute("UPDATE members SET harness='claude' WHERE name='owner'")
                self.team_file.write_text(json.dumps(team))
                with self.assertRaises(ValueError):
                    prepare(self.directory, member, "claude", config=self.config("claude"))
                self.assertFalse((self.directory / "profiles").exists())

    def test_grok_owner_keeps_native_settings_and_worker_drops_operator_marker(self):
        original = self.home / "grok"
        original.mkdir()
        settings = 'subagents = { enabled = true }\nworkflows = { enabled = true }\n'
        (original / "config.toml").write_text(settings)
        native = {"GROK_SUBAGENTS": "1", "GROK_WORKFLOWS": "1", "GROK_GOAL": "1",
                  "GROK_CONFIG_DIR": str(original), "AGENTSCIENT_CO_GENERATION": "inherited"}
        config = self.config("grok")
        with patch.dict(os.environ, native):
            private, environment, server = prepare(self.directory, "owner", "grok", config=config)
            _, worker_environment, _ = prepare(self.directory, "worker", "grok")
        argv, _ = command(self.directory, "owner", config, private, server)
        self.assertEqual(str(self.home), environment["HOME"])
        self.assertEqual(str(original), environment["GROK_HOME"])
        for key in ("GROK_SUBAGENTS", "GROK_WORKFLOWS", "GROK_GOAL", "GROK_CONFIG_DIR"):
            self.assertEqual(native[key], environment[key])
        self.assertEqual(private.name, environment["AGENTSCIENT_CO_GENERATION"])
        self.assertNotIn("AGENTSCIENT_CO_GENERATION", worker_environment)
        self.assertEqual(settings, (original / "config.toml").read_text())
        self.assertFalse((private / "config.toml").exists())
        self.assertNotIn("PreToolUse", json.loads((private / "observer-hooks.json").read_text()))
        for flag in ("--tools", "--no-subagents", "--no-memory", "--disable-web-search"):
            self.assertNotIn(flag, argv)
        self.assertIn("--no-leader", argv)
        self.assertEqual("initial-model", argv[argv.index("--model") + 1])
        self.assertEqual("high", argv[argv.index("--effort") + 1])

    def test_initial_brief_is_native_user_input_with_operator_controls_preserved(self):
        brief = "--not-a-flag\nGreeting — keep $HOME, `literal`, and \"quotes\" intact.\n"
        for harness in ("codex", "claude", "grok"):
            with self.subTest(harness=harness):
                config = self.config(harness)
                private, _, server = prepare(self.directory, "owner", harness, config=config)
                before, _ = command(self.directory, "owner", config, private, server)
                argv, _ = command(self.directory, "owner", {**config, "initial_prompt": brief}, private, server)
                self.assertEqual("--", argv[-2])
                self.assertTrue(argv[-1].endswith(brief))
                self.assertEqual(1, sum(value.count(brief) for value in argv))
                for flag in ("--single", "--prompt-file", "--print", "--disable-slash-commands", "--tools"):
                    self.assertNotIn(flag, argv)
                self.assertNotIn("initial_prompt", config)
                if harness == "grok":
                    self.assertEqual(brief, argv[-1])
                    self.assertEqual(before[before.index("--rules") + 1], argv[argv.index("--rules") + 1])
                elif harness == "claude":
                    self.assertEqual(before[before.index("--append-system-prompt") + 1],
                                     argv[argv.index("--append-system-prompt") + 1])
                self.assertEqual("initial-model", argv[argv.index("--model") + 1] if harness != "codex"
                                 else tomllib.loads("\n".join(argv[i + 1] for i, item in enumerate(argv)
                                                              if item == "-c"))["model"])

    def test_co_behavior_alias_and_initial_unbound_owner_can_use_operator_console(self):
        packages = copy.deepcopy(role_packages.catalog())
        packages["roles"]["liaison"] = packages["roles"]["co"]
        packages["defaults"]["co"] = "liaison"
        with patch.object(role_packages, "_catalog", return_value=packages):
            for bound, harness in product((True, False), ("claude", "codex", "grok")):
                with self.subTest(bound=bound, harness=harness):
                    with self.store.connect() as db:
                        if bound:
                            db.execute("UPDATE member_bindings SET role='liaison' WHERE member='owner'")
                        else:
                            db.execute("DELETE FROM member_bindings WHERE member='owner'")
                    config = self.config(harness) | {"initial_prompt": "Carry this operator brief to XO."}
                    private, _, server = prepare(self.directory, "owner", harness, config=config)
                    argv, _ = command(self.directory, "owner", config, private, server)
                    self.assertNotIn("--disable-slash-commands", argv)
                    self.assertTrue(argv[-1].endswith(config["initial_prompt"]))
                    if harness == "claude":
                        hooks = json.loads((private / "settings.json").read_text())["hooks"]
                    else:
                        hooks = json.loads((private / "observer-hooks.json").read_text())
                    self.assertNotIn("PreToolUse", hooks)
                    if harness == "codex":
                        self.assertEqual("approve", server["default_tools_approval_mode"])

    def test_runtime_profiles_cannot_enable_internal_operator_mode(self):
        for key, value in (("operator_console", True), ("initial_prompt", "Run this brief.")):
            policy = runtime_policy()
            policy["profiles"]["claude"][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "Unknown runtime profile field"):
                runtime.validate(policy, runtime.vendor_defaults(policy))

    def test_native_session_change_updates_console_metadata_only(self):
        generation = self.store.begin_session("owner", os.getpid())
        self.store.session("owner", generation=generation, native_id="original",
                           detail={"transport": "interactive/pty"})
        payload = {"hook_event_name": "SessionStart", "session_id": "resumed", "model": "user-choice"}
        self.assertEqual({}, hook(self.directory, "owner", "claude", payload, generation=generation))
        self.assertEqual("resumed", self.store.member("owner")["native_id"])
        self.store.session("worker", generation=self.generation, native_id="worker-session",
                           detail={"transport": "interactive/pty"})
        hook(self.directory, "worker", "claude", payload, generation=self.generation)
        self.assertEqual("worker-session", self.store.member("worker")["native_id"])
