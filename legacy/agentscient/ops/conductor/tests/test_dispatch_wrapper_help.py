"""Triage 2026-09-11: `dispatch.sh --help` answers with its usage, exit 0,
instead of refusing `--help` as an unknown flag (it did, twice today)."""

from __future__ import annotations

import unittest
from pathlib import Path

from ops.testkit import World, run

ROOT = Path(__file__).resolve().parents[3]
WRAPPER = ROOT / "ops" / "conductor" / "tools" / "dispatch.sh"


class DispatchWrapperAnswersHelp(unittest.TestCase):
    def setUp(self):
        self.world = World()
        self.addCleanup(self.world.close)
        self.world.require("bash")

    def _run(self, flag):
        return run(
            ["bash", str(WRAPPER), flag],
            cwd=ROOT,
            env=self.world.env(),
            timeout=30,
        )

    def test_help_prints_usage_and_exits_0(self):
        for flag in ("--help", "-h"):
            with self.subTest(flag=flag):
                proc = self._run(flag)
                self.assertEqual(proc.exit, 0, proc.stderr)
                self.assertIn("usage: dispatch.sh", proc.stdout)
                self.assertIn("--scope-file", proc.stdout)
                self.assertEqual(proc.stderr.strip(), "")

    def test_an_unknown_flag_still_refuses(self):
        proc = self._run("--status")
        self.assertNotEqual(proc.exit, 0)
        self.assertIn("unknown flag", proc.stderr)


if __name__ == "__main__":
    unittest.main()
