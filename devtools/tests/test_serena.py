import copy
import json
import unittest
from unittest.mock import patch

from fixtures.workspaces import WorkspaceFixture

from devtools import role_packages
from devtools.api import API
from devtools.profiles import permitted, prepare
from devtools.serena_provider import Serena, enabled


class SerenaTests(unittest.TestCase):
    setUp = WorkspaceFixture.setUp
    prepared = WorkspaceFixture.prepared

    def configure(self):
        config = json.loads((self.state / "team.json").read_text())
        config["project"]["serena"] = {"image": "agentscient-serena:fixture"}
        (self.state / "team.json").write_text(json.dumps(config))
        return Serena(self.state, "coder")

    def test_provider_mounts_only_role_paths_and_never_host_credentials_or_network(self):
        provider = self.configure()
        scope, _ = provider.scope()
        root = self.prepared()
        args = provider.command(scope, self.api.workspaces.ensure())
        self.assertIn("--network=none", args)
        self.assertIn("--read-only", args)
        self.assertIn("--cap-drop=ALL", args)
        mounts = [args[i + 1] for i, value in enumerate(args[:-1]) if value == "--mount"]
        self.assertIn(f"type=bind,src={root},dst=/workspace,readonly", mounts)
        self.assertIn(f"type=bind,src={root}/src/Example.php,dst=/workspace/src/Example.php", mounts)
        self.assertNotIn(f"type=bind,src={root}/src,dst=/workspace/src", mounts)
        self.assertFalse(any("docker.sock" in x or "dst=/workspace/devtools" in x or "dst=/root" in x for x in mounts))
        self.assertFalse(enabled(self.store, API(self.state, "xo").control.snapshot()))

    def test_readonly_role_has_no_writable_source_mounts(self):
        provider = self.configure()
        scope = self.api.control.snapshot()
        self.api.control.bind("coder", "audit", scope["work_order"], scope["job"], "xo")
        scope, _ = provider.scope()
        args = provider.command(scope, self.api.workspaces.ensure())
        writable = [
            args[i + 1]
            for i, value in enumerate(args[:-1])
            if value == "--mount" and not args[i + 1].endswith(",readonly")
        ]
        self.assertEqual(1, len(writable))
        self.assertTrue(writable[0].endswith("dst=/workspace/.serena"))

    def test_symlink_mount_cannot_expand_role_scope(self):
        provider = self.configure()
        root = self.prepared()
        (root / "templates").symlink_to(root / "devtools", target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "escapes"):
            provider.command(provider.scope()[0], self.api.workspaces.ensure())

    def test_pause_and_binding_change_revoke_provider_before_execution(self):
        provider = self.configure()
        provider.signature = provider.scope()[1]
        API(self.state, "xo").call("steer", {"action": "pause", "work": "Feature"})
        with self.assertRaisesRegex(ValueError, "paused"):
            provider.call({"code": "1"})
        API(self.state, "xo").call("steer", {"action": "resume", "work": "Feature"})
        scope = self.api.control.snapshot()
        self.api.control.bind("coder", "audit", scope["work_order"], scope["job"], "xo")
        with self.assertRaisesRegex(ValueError, "Reconnect"):
            provider.call({"code": "1"})

    def test_role_package_write_revocation_reconnects_even_when_binding_is_unchanged(self):
        provider = self.configure()
        before = self.api.control.snapshot()
        provider.signature = provider.scope()[1]
        changed = copy.deepcopy(role_packages.catalog())
        changed["roles"]["coder"]["workspace"]["paths"] = ["tests/"]
        with patch("devtools.role_packages._catalog", return_value=changed):
            self.assertFalse(self.api.workspaces.permitted_path("src/Example.php", "coder"))
            self.assertEqual(before, self.api.control.snapshot())
            with self.assertRaisesRegex(ValueError, "Reconnect"):
                provider.scope()

    def test_native_profiles_keep_serena_separate_and_reject_other_provider_tools(self):
        self.configure()
        private, _, server = prepare(self.state, "coder", "claude")
        servers = json.loads((private / "mcp.json").read_text())["mcpServers"]
        self.assertEqual({"agentscient_dev", "serena"}, set(servers))
        self.assertEqual("mcp", server["args"][1])
        self.assertEqual("serena", servers["serena"]["args"][1])
        for harness, tool in (
            ("codex", "serena_repl"),
            ("claude", "mcp__serena__serena_repl"),
            ("grok", "serena__serena_repl"),
        ):
            self.assertTrue(permitted(harness, tool, {"code": "s.info()"}))
            self.assertFalse(permitted(harness, tool, {"code": "1", "session": "arbitrary"}))
        self.assertFalse(permitted("claude", "mcp__serena__execute_shell_command", {"command": "echo bad"}))

    def test_repl_is_frozen_before_releasing_workspace_lock(self):
        provider = self.configure()
        sentinel = object()
        events = []

        def start(*args):
            provider.process = sentinel
            provider.workspace_root = args[1]["root"]
            events.append("start")

        def freeze(paused):
            events.append("pause" if paused else "unpause")
            with self.assertRaisesRegex(ValueError, "Another operation"):
                self.api.workspaces.inspect()
            provider.paused = paused

        with (
            patch.object(provider, "start", start),
            patch.object(provider, "freeze", freeze),
            patch.object(provider, "request", return_value={"content": [{"type": "text", "text": "42"}]}),
        ):
            provider.call({"code": "value = 42; value"})
            provider.call({"code": "value"})
        provider.process = None
        self.assertEqual(["start", "pause", "unpause", "pause"], events)
        self.assertEqual("prepared", self.api.workspaces.inspect()["workspace"])

    def test_released_external_binding_closes_idle_provider(self):
        provider = self.configure()
        signature = provider.scope()[1]
        sentinel = object()
        provider.process = sentinel
        self.api.control.unbind("coder")

        def close():
            provider.process = None

        with patch.object(provider, "close", side_effect=close) as stopped:
            provider.retire_when_released(sentinel, signature)
            stopped.assert_called_once()


if __name__ == "__main__":
    unittest.main()
