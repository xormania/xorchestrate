"""Prod-lane provider integration: state, real pipes, failures and source receipts."""

import hashlib
import json
import os
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from ops.testkit import World, run

ROOT = Path(__file__).resolve().parents[3]
TOOLS = ROOT / "ops/conductor/tools"
sys.path.insert(0, str(TOOLS))
import dev_mcp  # noqa: E402
import source_work  # noqa: E402
from instrument import Refusal  # noqa: E402


class CodeWork(unittest.TestCase):
    def setUp(self):
        self.world = World(prefix="prod-code-")
        self.addCleanup(self.world.close)
        self.root = self.world.cwd
        self.conf = self.world.plant_text("lane.conf", "LANE_NAME='code-test'\n")
        self.env = self.world.env(extra={"LANE_CONF": str(self.conf), "WF_AGENT": "Codex <test@example.test>",
                                        "CLAUDE_CODE_SESSION_ID": "one", "COORDINATION_CAPABILITY": hashlib.sha256(b"one").hexdigest()})
        self.patch = patch.dict(os.environ, self.env, clear=True)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        for args in (("init", "-q", "-b", "dev"), ("config", "user.name", "Code Fixture"),
                     ("config", "user.email", "code@example.test")):
            self.git(*args)
        self.file = self.root / "src/Service/Greeting.php"
        self.file.parent.mkdir(parents=True)
        self.file.write_text("<?php\nclass Greeting { public function greet() { return 'hello'; } }\n")
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")
        self.bind("one")
        fake = Path(__file__).parent / "fakes/serena_repl.py"
        self.python = self.world.tools.plant("serena-python", script=f"#!{sys.executable}\n" + fake.read_text())
        self.entry = self.make_entry()

    def git(self, *args):
        result = run(["git", *args], cwd=self.root, env=self.env, timeout=20)
        self.assertEqual(result.exit, 0, result.stderr)
        return result.stdout

    def bind(self, name):
        env = {**self.env, "CLAUDE_CODE_SESSION_ID": name, "COORDINATION_CAPABILITY": hashlib.sha256(name.encode()).hexdigest()}
        result = run([sys.executable, "-B", str(TOOLS / "coordination.py"), "bind", "--roles", "worker"],
                     cwd=self.root, env=env, timeout=20)
        self.assertEqual(result.exit, 0, result.stderr)
        return env

    def make_entry(self, **kw):
        entry = dev_mcp.Entry(serena_python=self.python, workspace=self.root, **kw)
        self.addCleanup(entry.code.close)
        return entry

    def invoke(self, name, args=None, *, entry=None):
        return (entry or self.entry).call("capability_invoke", {"capability": name, "arguments": args or {}})

    def cell(self, code, *, entry=None, state=None):
        state = state or self.invoke("code_status", entry=entry)
        return self.invoke("code_repl", {"code": code, "expected_source_fingerprint": state["source_fingerprint"],
                                       "expected_epoch": state["provider_epoch"]}, entry=entry)

    def test_discovery_stays_small_and_unconfigured_provider_does_not_block_messages(self):
        entry = dev_mcp.Entry(workspace=self.root)
        cards = entry.call("capability_search", {"query": "Serena"})["capabilities"]
        self.assertIn("code_repl", [card["name"] for card in cards])
        self.assertFalse(entry.card("code_repl")["available"])
        self.assertFalse(self.invoke("code_status", entry=entry)["configured"])
        self.assertEqual(self.invoke("coord_inbox", entry=entry)["messages"], [])
        self.assertEqual(len(dev_mcp.TOOLS), 3)
        self.assertLess(len(json.dumps(dev_mcp.TOOLS)), 1500)
        self.assertIsNone(self.entry.code.provider)

    def test_cells_persist_without_inheriting_harness_authority(self):
        with patch.dict(os.environ, {"GITHUB_TOKEN": "synthetic-secret", "SSH_AUTH_SOCK": "synthetic-socket"}):
            first = self.cell("answer = 42\nanswer")
            self.assertEqual(first["text"], "42")
            self.assertEqual(self.cell("answer + 1")["text"], "43")
            result = self.cell("import os\n[k for k in ('GITHUB_TOKEN', 'SSH_AUTH_SOCK', 'LANE_CONF', 'COORDINATION_CAPABILITY', 'CLAUDE_CODE_SESSION_ID') if k in os.environ]")
            self.assertEqual(result["text"], "[]")
            telemetry = self.cell("import json\njson.loads(os.environ['APPLICATIONINSIGHTS_CONFIGURATION_CONTENT'])")
            self.assertIn("'disableAppInsights': True", telemetry["text"])
            self.assertIn("'disableStatsbeat': True", telemetry["text"])
            self.assertEqual(self.cell("os.environ['SERENA_USAGE_REPORTING']")["text"], "false")
        self.assertFalse((self.root / ".serena").exists())
        self.assertEqual(self.entry.code.provider.runtime.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.invoke("code_status")["scope"], "prod-lane")

    def test_edit_then_textual_error_is_recorded_and_invalidates_checks(self):
        state = self.invoke("code_status")
        source_work.record_check(self.root, "fixture-check", fingerprint=state["source_fingerprint"],
                                 generation=state["generation"], passed=True, evidence="synthetic existing check")
        self.assertEqual(self.invoke("code_status")["verified_at_current_source"], ["fixture-check"])
        result = self.cell("from pathlib import Path\nPath('src/Service/Greeting.php').write_text('<?php // partial change')\nraise ValueError('partial edit')")
        self.assertEqual(result["cell_outcome"], "error")
        self.assertIn("ValueError", result["text"])
        self.assertEqual(result["changed_paths"], ["src/Service/Greeting.php"])
        self.assertNotEqual(result["source_fingerprint"], state["source_fingerprint"])
        self.assertEqual(self.invoke("code_status")["verified_at_current_source"], [])
        with sqlite3.connect(self.world.xdg_state / "code-test/state.db") as conn:
            row = conn.execute("SELECT participant,outcome,after_fingerprint FROM source_events WHERE id=?", (result["source_event"],)).fetchone()
        self.assertEqual(row, (1, "error", result["source_fingerprint"]))

    def test_stale_source_unknown_arguments_and_wrong_worktree_do_not_execute(self):
        state = self.invoke("code_status")
        self.file.write_text("<?php // peer edit")
        with self.assertRaises(Refusal):
            self.cell("raise AssertionError('must not execute')", state=state)
        with self.assertRaises(Refusal):
            self.invoke("code_repl", {"code": "1", "session": "someone-else"})
        with self.assertRaises(Refusal):
            self.invoke("code_status", entry=self.make_entry(binding={
                "participant": "PT-1", "assignment": None, "worktree": str(self.world.root),
                "capabilities": list(dev_mcp.code_work.CAPABILITIES)}))
        self.assertEqual(self.invoke("code_status")["generation"], 0)

    def test_unreadable_source_after_cell_keeps_event_and_invalidates_checks(self):
        state = self.invoke("code_status")
        source_work.record_check(self.root, "prior-check", fingerprint=state["source_fingerprint"],
                                 generation=state["generation"], passed=True, evidence="synthetic check")
        result = self.cell("from pathlib import Path\nPath('src/Service/Greeting.php').unlink()\nPath('src/Service').rmdir()\nPath('src/Service').symlink_to('../../')")
        self.assertIsNone(result["source_fingerprint"])
        self.assertTrue(result["verification_invalidated"])
        with sqlite3.connect(self.world.xdg_state / "code-test/state.db") as conn:
            row = conn.execute("SELECT outcome,after_fingerprint,finished_at FROM source_events WHERE id=?", (result["source_event"],)).fetchone()
            generation = conn.execute("SELECT generation FROM source_workspaces").fetchone()[0]
        self.assertEqual(row[:2], ("source_unknown", None))
        self.assertIsNotNone(row[2])
        self.assertGreater(generation, state["generation"])

    def test_restart_and_provider_death_never_replay_a_cell(self):
        self.cell("saved = 'temporary'")
        state = self.invoke("code_status")
        restarted = self.invoke("code_restart", {"expected_epoch": state["provider_epoch"]})
        self.assertNotEqual(restarted["provider_epoch"], state["provider_epoch"])
        self.assertTrue(restarted["previous_namespace_lost"])
        self.assertEqual(restarted["participant"], state["participant"])
        with self.assertRaises(Refusal):
            self.cell("saved", state=state)
        self.assertEqual(self.cell("globals().get('saved')")["text"], "None")
        result = self.cell("from pathlib import Path\nPath('partial.txt').write_text('one')\nimport os\nos._exit(7)")
        self.assertEqual(result["cell_outcome"], "provider_lost")
        self.assertIn("partial.txt", result["changed_paths"])
        self.assertEqual((self.root / "partial.txt").read_text(), "one")
        self.assertEqual(self.invoke("code_status")["last_event"]["outcome"], "provider_lost")

    def test_participants_have_separate_namespaces_and_original_binding_is_retained(self):
        self.cell("private_value = 'first'")
        peer_env = self.bind("two")
        with patch.dict(os.environ, peer_env, clear=True):
            peer = self.make_entry()
            self.assertEqual(self.cell("globals().get('private_value')", entry=peer)["text"], "None")
            self.cell("private_value = 'second'", entry=peer)
            with self.assertRaises(Refusal):
                self.cell("private_value", entry=self.entry)
        self.assertEqual(self.cell("private_value")["text"], "first")

    def test_lost_namespace_is_reported_before_requested_cell_runs(self):
        self.cell("del _dev_lane_epoch")
        result = self.cell("from pathlib import Path\nPath('must-not-exist.php').write_text('<?php')")
        self.assertEqual(result["cell_outcome"], "provider_lost")
        self.assertIn("namespace", result["text"])
        self.assertFalse((self.root / "must-not-exist.php").exists())

    def test_bounded_output_keeps_source_receipt_and_scope_changes_visible(self):
        state = self.invoke("code_status")
        response = self.entry.route(1, "tools/call", {"name": "capability_invoke", "arguments": {
            "capability": "code_repl", "arguments": {
                "code": "from pathlib import Path\nPath('ops').mkdir()\nPath('ops/wrong.py').write_text('# unintended')\n'☃' * 20000",
                "expected_epoch": state["provider_epoch"], "expected_source_fingerprint": state["source_fingerprint"]}}})
        self.assertLessEqual(len(json.dumps(response).encode()), 16384)
        payload = response["result"]["structuredContent"]
        self.assertTrue(payload["truncated"])
        self.assertIsInstance(payload["source_event"], int)
        self.assertEqual(payload["outside_prod_lane"], ["ops/wrong.py"])

    def test_source_lock_does_not_hold_the_coordination_database(self):
        self.bind("two")
        with source_work.workspace_lock(self.root):
            result = self.invoke("coord_send", {"to": "PT-2", "purpose": "inform", "body": "Still live", "key": "during-cell"})
            self.assertTrue(result["message"].startswith("M-"))
            with self.assertRaises(Refusal):
                self.cell("1")

    def test_fingerprint_covers_untracked_deletion_mode_and_symlink_target(self):
        before = source_work.snapshot(self.root)["fingerprint"]
        self.file.chmod(0o755)
        mode = source_work.snapshot(self.root)["fingerprint"]
        self.assertNotEqual(before, mode)
        self.file.unlink()
        deleted = source_work.snapshot(self.root)["fingerprint"]
        self.assertNotEqual(mode, deleted)
        self.file.symlink_to("first-target")
        first = source_work.snapshot(self.root)["fingerprint"]
        self.file.unlink()
        self.file.symlink_to("second-target")
        self.assertNotEqual(first, source_work.snapshot(self.root)["fingerprint"])
        (self.root / "new.php").write_text("<?php // new")
        self.assertNotEqual(deleted, source_work.snapshot(self.root)["fingerprint"])


if __name__ == "__main__":
    unittest.main()
