"""Triage 2026-09-11: `findings.py export --write [PATH]` regenerates the
export file; plain `export` still prints and writes nothing."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from ops.testkit import World, run

ROOT = Path(__file__).resolve().parents[3]
TOOL = ROOT / "ops" / "conductor" / "tools" / "findings.py"


class ExportWrite(unittest.TestCase):
    def setUp(self):
        self.world = World()
        self.addCleanup(self.world.close)
        conf = self.world.plant_text("lane.conf", "LANE_NAME='agentscient'\n")
        self.env = self.world.env(extra={"WF_AGENT": "Tester <t@x.y>", "LANE_CONF": str(conf)})
        self.tool = self.world.stage(TOOL, "repo/ops/conductor/tools/findings.py",
                                     siblings=("instrument.py", "laneconf.py"))

    def initialize(self):
        result = run([sys.executable, str(self.tool), "import"], cwd=ROOT, env=self.env, timeout=30)
        self.assertEqual(result.exit, 0, result.stderr)

    def _run(self, *argv):
        return run(
            [sys.executable, str(self.tool), "export", *argv],
            cwd=ROOT,
            env=self.env,
            timeout=60,
        )

    def test_write_to_an_explicit_path(self):
        out = self.world.path("out/findings.jsonl")
        absent = self._run("--write", str(out))
        self.assertEqual(absent.refusal().cls, "store")
        self.assertFalse(out.parent.exists())
        self.initialize()
        proc = self._run("--write", str(out))
        self.assertEqual(proc.exit, 0, proc.stderr)
        self.assertTrue(out.is_file())
        self.assertIn(str(out), proc.stdout)
        self.assertIn("wrote 0 row(s)", proc.stdout)

    def test_plain_export_prints_and_writes_nothing(self):
        absent = self._run()
        self.assertEqual(absent.refusal().cls, "store")
        self.assertFalse((self.world.xdg_state / "agentscient").exists())
        self.initialize()
        proc = self._run()
        self.assertEqual(proc.exit, 0, proc.stderr)
        # Only the explicit import initialized the source. The view writes no export.
        self.assertEqual([p.name for p in self.world.root.rglob("*.jsonl")], [])

    def test_write_with_two_paths_refuses_arguments(self):
        proc = self._run("--write", "a", "b")
        self.assertEqual(proc.exit, 2)
        self.assertIn("arguments:", proc.stderr)
        self.assertEqual(proc.stdout, "")


if __name__ == "__main__":
    unittest.main()
