"""Framework-independent execution ownership and bounded project observations."""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from ops.testkit import World, run

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "ops/conductor/tools"))
import dev_mcp  # noqa: E402
import project_work  # noqa: E402
import source_work  # noqa: E402
from instrument import Refusal  # noqa: E402
from project_providers.symfony import Provider  # noqa: E402


class FixtureProvider:
    identity = "non-framework-fixture/1"
    capabilities = {"project_status": ("status", {}, (), False),
                    "fixture_write": ("write", {}, (), True), "fixture_fail": ("fail", {}, (), True)}

    def unavailable(self, root, name):
        return None

    def status(self, root):
        return {"integration": self.identity}

    def invoke(self, root, runtime, name, arguments):
        (root / "example.txt").write_text("changed")
        if name == "fixture_fail":
            raise RuntimeError("partial write")
        return {"outcome": "success"}

    def receipts(self, root, name, result):
        return {}


class ProjectOperations(unittest.TestCase):
    def setUp(self):
        self.world = World(prefix="project-work-")
        self.addCleanup(self.world.close)
        self.root = self.world.cwd
        self.world.plant_text(self.root / "seed.txt", "fixture\n")
        config = self.world.plant_text("lane.conf", "LANE_NAME='project-fixture'\n")
        self.env = self.world.env(extra={"LANE_CONF": str(config), "WF_AGENT": "Codex <test@example.test>",
                                        "CLAUDE_CODE_SESSION_ID": "one", "COORDINATION_CAPABILITY": "a" * 64})
        env_patch = patch.dict(os.environ, self.env, clear=True)
        env_patch.start()
        self.addCleanup(env_patch.stop)
        for args in (["git", "init", "-qb", "dev"], ["git", "config", "user.name", "Fixture"],
                     ["git", "config", "user.email", "fixture@example.test"], ["git", "add", "."],
                     ["git", "commit", "-qm", "fixture"],
                     [sys.executable, "-B", str(ROOT / "ops/conductor/tools/coordination.py"), "bind", "--roles", "worker"]):
            result = run(args, cwd=self.root, env=self.env, timeout=20)
            self.assertEqual(result.exit, 0, f"{args}: {result.stderr}{result.stdout}")

    def test_non_framework_provider_shares_source_identity_and_accounts_partial_failure(self):
        work = project_work.ProjectWork(workspace=self.root, provider=FixtureProvider())
        before = work.call(None, "project_status", {})
        source_work.record_check(self.root, "fixture_check", fingerprint=before["source_fingerprint"],
                                 generation=before["generation"], passed=True, evidence="fixture")
        result = work.call(None, "fixture_fail", {"expected_source_fingerprint": before["source_fingerprint"]})
        self.assertEqual(result["outcome"], "failed")
        self.assertEqual(result["changed_paths"], ["example.txt"])
        self.assertTrue(result["verification_invalidated"])
        status = dev_mcp.Entry(workspace=self.root).call("capability_invoke", {"capability": "code_status", "arguments": {}})
        self.assertEqual(status["source_fingerprint"], result["source_fingerprint"])
        self.assertEqual(status["verified_at_current_source"], [])
        self.assertEqual(status["last_event"]["id"], result["source_event"])

    def test_stale_source_and_wrong_worktree_refuse_before_provider_writes(self):
        work = project_work.ProjectWork(workspace=self.root, provider=FixtureProvider())
        with self.assertRaises(Refusal):
            work.call(None, "fixture_write", {"expected_source_fingerprint": "a" * 64})
        with self.assertRaises(Refusal):
            work.call({"participant": "PT-1", "assignment": None, "worktree": str(self.world.root),
                       "capabilities": ["fixture_write"]}, "fixture_write", {})
        self.assertFalse((self.root / "example.txt").exists())
        self.assertEqual(source_work.status(self.root)["generation"], 0)

    def test_missing_php_or_vendor_does_not_block_core_coordination(self):
        entry = dev_mcp.Entry(workspace=self.root)
        self.assertFalse(entry.card("mate_services")["available"])
        self.assertTrue(entry.card("coord_inbox")["available"])
        self.assertEqual(entry.call("capability_invoke", {"capability": "coord_inbox", "arguments": {}})["messages"], [])
        provider = Provider({"php": sys.executable, "environment": "test"})
        self.assertIn("vendor/autoload.php", provider.unavailable(self.root, "mate_services"))
        with self.assertRaises(ValueError):
            Provider({"php": "php", "environment": "test"})

    def test_closed_schemas_reject_paths_shell_payloads_and_unknown_tools(self):
        entry = dev_mcp.Entry(workspace=self.root)
        for name, args in (("mate_resource", {"token": "../../secret"}),
                           ("mate_skill", {"name": "../../secret"}),
                           ("mate_describe", {"tool": "arbitrary-execution"}),
                           ("mate_services", {"command": "anything"})):
            with self.subTest(name=name), self.assertRaises(Refusal):
                dev_mcp.arguments_for(entry.catalogue[name], args)

    def test_process_bounds_and_environment_exclude_workflow_credentials(self):
        provider = Provider({"php": sys.executable, "environment": "test"})
        runtime = self.world.root / "runtime"
        with patch.dict(os.environ, {"GITHUB_TOKEN": "synthetic-secret", "SSH_AUTH_SOCK": "synthetic-agent"}):
            result = provider._run(self.root, runtime, ["-c", "import json,os; print(json.dumps(sorted(k for k in os.environ if k in ['GITHUB_TOKEN','SSH_AUTH_SOCK','LANE_CONF','COORDINATION_CAPABILITY'])))"])
        self.assertEqual(json.loads(result["stdout"]), [])
        timed = provider._run(self.root, runtime, ["-c", "import time; time.sleep(60)"], timeout=0.1)
        self.assertEqual(timed["outcome"], "failed")
        self.assertIn("timed out", timed["diagnostic"])
        large = provider._run(self.root, runtime, ["-c", "print('x' * 1100000)"])
        self.assertEqual(large["outcome"], "failed")
        self.assertIn("one MiB", large["diagnostic"])

    def test_large_observations_page_without_reexecuting_or_accepting_a_path(self):
        provider = Provider({})
        item = provider._result({"data": "snowman ☃ " * 2000})
        self.assertTrue(item["truncated"])
        page = provider.invoke(self.root, self.world.root, "project_output", {"result": item["result"]})
        self.assertLessEqual(len(json.dumps(page["untrusted_text"]).encode()), 6000)
        self.assertGreater(page["next_offset"], 0)
        with self.assertRaises(Refusal):
            provider.invoke(self.root, self.world.root, "project_output", {"result": "missing"})


if __name__ == "__main__":
    unittest.main()
