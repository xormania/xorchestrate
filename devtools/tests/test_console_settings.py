"""Native Grok CO registration preserves operator state and launch boundaries."""

import json
import shlex
import subprocess
import sys
import tempfile
import tomllib
import unittest
import uuid
from pathlib import Path

from grok_mcp_fixture import write_config
from support import fixture_environment

from devtools.api import SERVER
from devtools.console_settings import GENERATION_ENV, grok_settings


class ConsoleSettingsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.home = self.root / "native-home"
        self.home.mkdir()
        self.private = self.root / "private" / uuid.uuid4().hex
        self.private.mkdir(parents=True)
        self.server_name = SERVER + "_" + self.private.name
        self.output = self.root / "observed"
        self.original = [sys.executable, "-c", "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('ran')",
                         str(self.output)]
        self.server = {"command": self.original[0], "args": self.original[1:]}
        self.hooks = {"SessionStart": [{"hooks": [{"type": "command", "timeout": 10,
                        "command": shlex.join(self.original)}]}]}
        (self.private / "observer-hooks.json").write_text(json.dumps(self.hooks))
        self.binary = Path(__file__).with_name("grok_mcp_fixture.py")
        self.binary.chmod(0o755)
        self.env = fixture_environment(self.root, {"HOME": str(self.root), "GROK_HOME": str(self.home),
                                                  GENERATION_ENV: self.private.name})
        self.config = self.home / "config.toml"

    def register(self, private=None, env=None):
        return grok_settings(str(self.binary), private or self.private, self.server, self.workspace, env or self.env)

    def settings(self):
        return tomllib.loads(self.config.read_text())

    def hook_files(self):
        return list((self.home / "hooks").glob("agentscient-co-*.json"))

    def test_native_mcp_registration_preserves_user_settings_and_concurrent_edits(self):
        initial = {"model": "owner-model", "subagents": {"enabled": True},
                   "mcp_servers": {"personal": {"command": "personal-server", "args": ["--read"]}}}
        write_config(self.config, initial)
        with self.register():
            configured = self.settings()
            self.assertEqual(initial["mcp_servers"]["personal"], configured["mcp_servers"]["personal"])
            self.assertTrue(configured["mcp_servers"][self.server_name]["enabled"])
            self.assertEqual("owner-model", configured["model"])
            self.assertFalse((self.workspace / ".grok").exists())
            configured["model"] = "operator-changed-model"
            configured["mcp_servers"]["another"] = {"command": "another-server"}
            write_config(self.config, configured)
        expected = {**initial, "model": "operator-changed-model",
                    "mcp_servers": {**initial["mcp_servers"], "another": {"command": "another-server"}}}
        self.assertEqual(expected, self.settings())
        self.assertEqual([], self.hook_files())

    def test_cleanup_after_native_exception_removes_own_registration_and_hooks(self):
        with self.assertRaisesRegex(RuntimeError, "native exited"):
            with self.register():
                self.assertIn(self.server_name, self.settings()["mcp_servers"])
                raise RuntimeError("native exited")
        self.assertNotIn(self.server_name, self.settings().get("mcp_servers", {}))
        self.assertEqual([], self.hook_files())

    def test_operator_changes_to_registered_server_and_hook_survive_cleanup(self):
        with self.register():
            configured = self.settings()
            configured["mcp_servers"][self.server_name]["args"] = ["operator-choice"]
            write_config(self.config, configured)
            hook, = self.hook_files()
            hook.write_text('{"hooks": {"SessionStart": []}}\n')
        self.assertEqual(["operator-choice"], self.settings()["mcp_servers"][self.server_name]["args"])
        self.assertEqual('{"hooks": {"SessionStart": []}}\n', hook.read_text())

    def test_existing_server_or_hook_collision_is_never_overwritten(self):
        original = {"mcp_servers": {self.server_name: {"command": "existing-server"}}}
        write_config(self.config, original)
        with self.assertRaises((ValueError, FileExistsError)):
            with self.register():
                self.fail("Existing server must not be overwritten")
        self.assertEqual(original, self.settings())
        write_config(self.config, {})
        hook = self.home / "hooks" / ("agentscient-co-" + self.private.name + ".json")
        hook.parent.mkdir(exist_ok=True)
        hook.write_text("existing hook")
        with self.assertRaises((ValueError, FileExistsError)):
            with self.register():
                self.fail("Existing hook must not be overwritten")
        self.assertEqual("existing hook", hook.read_text())
        self.assertNotIn(self.server_name, self.settings().get("mcp_servers", {}))

    def test_failed_native_registration_never_enters_tui_and_leaves_no_observer(self):
        with self.assertRaises((ValueError, RuntimeError, subprocess.CalledProcessError)):
            with self.register(env=self.env | {"AGENTSCIENT_GROK_MCP_FAIL": "add"}):
                self.fail("A failed native MCP registration must block TUI startup")
        self.assertEqual([], self.hook_files())
        self.assertFalse(self.config.exists())

    def test_failed_add_after_native_write_cleans_up_its_partial_registration(self):
        with self.assertRaisesRegex(ValueError, "add failed"):
            with self.register(env=self.env | {"AGENTSCIENT_GROK_MCP_FAIL": "add-after-write"}):
                self.fail("A failed native MCP registration must block TUI startup")
        self.assertNotIn(self.server_name, self.settings().get("mcp_servers", {}))
        self.assertEqual([], self.hook_files())

    def test_unexpected_registration_blocks_launch_without_removing_foreign_replacement(self):
        with self.assertRaisesRegex(ValueError, "did not register"):
            with self.register(env=self.env | {"AGENTSCIENT_GROK_MCP_FAIL": "add-foreign-replacement"}):
                self.fail("An unexpected MCP registration must block TUI startup")
        self.assertEqual({"command": "operator-replacement", "args": ["--keep"], "enabled": True},
                         self.settings()["mcp_servers"][self.server_name])
        self.assertEqual([], self.hook_files())

    def test_failed_native_remove_is_reported_while_observer_cleanup_still_runs(self):
        with self.assertRaisesRegex(ValueError, "remove failed"):
            with self.register(env=self.env | {"AGENTSCIENT_GROK_MCP_FAIL": "remove"}):
                self.assertIn(self.server_name, self.settings()["mcp_servers"])
        self.assertIn(self.server_name, self.settings()["mcp_servers"])
        self.assertEqual([], self.hook_files())

    def test_mcp_and_hooks_run_only_in_matching_operator_environment(self):
        with self.register():
            server = self.settings()["mcp_servers"][self.server_name]
            hook, = self.hook_files()
            payload = json.loads(hook.read_text())
            self.assertNotIn("PreToolUse", payload["hooks"])
            self.assertNotIn(GENERATION_ENV, server.get("env", {}))
            commands = ([server["command"], *server["args"]],
                        shlex.split(payload["hooks"]["SessionStart"][0]["hooks"][0]["command"]))
            for kind, command in zip(("MCP", "observer"), commands, strict=True):
                for marker in (None, uuid.uuid4().hex, self.private.name):
                    with self.subTest(kind=kind, generation=marker):
                        self.output.unlink(missing_ok=True)
                        environment = dict(self.env)
                        environment.pop(GENERATION_ENV, None)
                        if marker is not None:
                            environment[GENERATION_ENV] = marker
                        subprocess.run(command, env=environment, capture_output=True, check=True, timeout=5)
                        self.assertEqual(marker == self.private.name, self.output.exists())

    def test_concurrent_sessions_and_repeated_launches_leave_no_uuid_registrations(self):
        other = self.private.parent / uuid.uuid4().hex
        other.mkdir()
        (other / "observer-hooks.json").write_text(json.dumps(self.hooks))
        other_name = SERVER + "_" + other.name
        for _ in range(2):
            with self.register():
                with self.register(private=other, env=self.env | {GENERATION_ENV: other.name}):
                    self.assertEqual({self.server_name, other_name}, set(self.settings()["mcp_servers"]))
                    self.assertEqual(2, len(self.hook_files()))
                self.assertEqual({self.server_name}, set(self.settings()["mcp_servers"]))
                self.assertEqual(1, len(self.hook_files()))
            self.assertEqual({}, self.settings().get("mcp_servers", {}))
            self.assertEqual([], self.hook_files())

    def test_generated_permission_hook_is_rejected_before_native_configuration_changes(self):
        (self.private / "observer-hooks.json").write_text(json.dumps({"PreToolUse": []}))
        with self.assertRaisesRegex(ValueError, "observe"):
            with self.register():
                self.fail("Permission hook must not be registered")
        self.assertFalse(self.config.exists())
        self.assertEqual([], self.hook_files())


if __name__ == "__main__":
    unittest.main()
