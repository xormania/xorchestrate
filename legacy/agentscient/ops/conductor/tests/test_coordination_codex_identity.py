"""Native Codex sessions remain distinct at the real coordination boundary.

Recovery grant 309: one dev-lane producer; independent test author absent.
The planted failure is the reader ignoring the native Codex session keys.
"""

import hashlib
import sqlite3
import sys
import unittest
from pathlib import Path

from ops.testkit import World, run

ROOT = Path(__file__).resolve().parents[3]
TOOL = ROOT / "ops/conductor/tools/coordination.py"
AGENT = "Recovery test model <noreply@example.test>"


class NativeCodexSessions(unittest.TestCase):
    def setUp(self):
        self.world = World(prefix="coordination-codex-")
        self.addCleanup(self.world.close)
        self.conf = self.world.plant_text("lane.conf", "LANE_NAME='testlane'\n")

    def invoke(self, keys, *args):
        env = self.world.env(extra={"LANE_CONF": str(self.conf), "WF_AGENT": AGENT})
        for name in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID",
                     "CODEX_THREAD_ID", "CODEX_SESSION_ID"):
            env.pop(name, None)
        env.update(keys)
        native = next((value.strip() for value in keys.values() if value.strip()), "absent")
        env["COORDINATION_CAPABILITY"] = hashlib.sha256(native.encode()).hexdigest()
        return run([sys.executable, "-B", str(TOOL), *args],
                   cwd=self.world.cwd, env=env, timeout=20)

    def rows(self):
        with sqlite3.connect(self.world.xdg_state / "testlane/state.db") as conn:
            return conn.execute("SELECT session_id, roles FROM participants ORDER BY id").fetchall()

    def test_same_model_sessions_have_distinct_participants_and_roles(self):
        lead = {"CODEX_THREAD_ID": "codex-lead", "CODEX_SESSION_ID": "codex-lead"}
        worker = {"CODEX_THREAD_ID": "codex-worker", "CODEX_SESSION_ID": "codex-worker"}
        for keys, role in ((lead, "lead"), (worker, "worker")):
            result = self.invoke(keys, "bind", "--roles", role, "--harness", "codex")
            self.assertEqual(result.exit, 0, result.stderr)
        self.assertEqual(self.rows(), [("codex-lead", "lead"), ("codex-worker", "worker")])
        denied = self.invoke(worker, "grant", "PT-2", "--roles", "lead")
        self.assertEqual(denied.exit, 2, denied.stdout + denied.stderr)
        self.assertIn("role: expected", denied.stderr)
        mine = self.invoke(worker, "who")
        self.assertEqual(mine.exit, 0, mine.stderr)
        self.assertIn("PT-2", mine.stdout)
        self.assertNotIn("PT-1", mine.stdout)

    def test_blank_thread_id_uses_native_session_id(self):
        result = self.invoke({"CODEX_THREAD_ID": " ", "CODEX_SESSION_ID": "codex-fallback"},
                             "bind", "--roles", "worker")
        self.assertEqual(result.exit, 0, result.stderr)
        self.assertEqual(self.rows(), [("codex-fallback", "worker")])

    def test_explicit_dispatch_binding_keeps_precedence(self):
        result = self.invoke({"CLAUDE_SESSION_ID": "dispatch-issued-session",
                              "CODEX_THREAD_ID": "native-worker-thread",
                              "CODEX_SESSION_ID": "native-worker-thread"},
                             "bind", "--roles", "worker", "--harness", "codex")
        self.assertEqual(result.exit, 0, result.stderr)
        self.assertEqual(self.rows(), [("dispatch-issued-session", "worker")])


if __name__ == "__main__":
    unittest.main()
