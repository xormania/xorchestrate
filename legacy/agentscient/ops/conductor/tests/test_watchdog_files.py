"""Independent hostile-file boundary oracle: one job cannot silence later jobs.

Synthetic files only. No vendor or real dispatch-store access; subprocess
deadline is an outer guard so a FIFO regression cannot hang the suite.
The implementation producer neither authored nor approved this oracle.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

from ops.testkit import World, run

TOOL = Path(__file__).resolve().parents[1] / "tools" / "watchdog.py"
SIBLINGS = ("instrument.py", "laneconf.py", "dispatch_observation.py")
BAD = "20260904T100000Z-tests-grok-bad001"
GOOD = "20260904T200000Z-tests-grok-good01"


class HeldFileIsolation(unittest.TestCase):
    def setUp(self):
        self.world = World(prefix="watchdog-files-")
        self.addCleanup(self.world.close)
        self.root = self.world.root
        self.store = self.world.xdg_state / "agentscient" / "dispatch"
        self.store.mkdir(parents=True)
        self.staged = self.world.stage(TOOL, siblings=SIBLINGS)
        self.conf = self.staged.parent.parent / "lane.conf"
        self.world.plant_text(
            self.conf,
            "LANE_NAME='agentscient'\nTRUNK_BRANCH='dev'\n",
        )
        self.bad = self.plant(BAD)
        good = self.plant(GOOD)
        self.world.plant_text(good / "raw.out", "errors=2\n")

    def plant(self, ident):
        job = self.store / ident
        job.mkdir()
        self.world.plant_text(
            job / "record.json",
            json.dumps({
                "id": ident, "job": "author-tests", "status": "closed",
                "harness": {"name": "grok"}, "caps": {"timeout": 600},
                "at": {"launched": "2026-09-04T10:00:00Z",
                       "closed": "2026-09-04T10:05:00Z"},
                "result": {"envelope": {"status": "ok", "findings": []},
                           "changed_paths": []},
            }),
        )
        self.world.plant_text(job / "state.json", "{}")
        return job

    def observe(self):
        env = self.world.env({
            "DISPATCH_JOBS": str(self.store),
            "LANE_CONF": str(self.conf),
        })
        result = run(
            [sys.executable, str(self.staged), "--store", str(self.store),
             "--once", "--no-forge", "--report-existing",
             "--heartbeat-every", "1"],
            cwd=self.world.cwd, env=env, timeout=4,
        )
        self.assertEqual(result.exit, 0, result.stderr)
        self.assertIn("deviation errors " + GOOD, result.stdout)
        self.assertIn("heartbeat:", result.stdout)
        self.assertIn("watchdog error:", result.stdout)
        return result.stdout

    def test_directory_is_unavailable_without_abandoning_later_jobs(self):
        (self.bad / "raw.out").mkdir()
        self.observe()

    def test_fifo_cannot_block_a_tick(self):
        os.mkfifo(self.bad / "raw.out")
        self.observe()

    def test_symlink_outside_job_is_not_read_as_held_output(self):
        outside = self.root / "outside.txt"
        self.world.plant_text(outside, "scope-cap: SYNTHETIC-PRIVATE-MARKER\n")
        (self.bad / "raw.out").symlink_to(outside)
        output = self.observe()
        self.assertNotIn("deviation refused " + BAD, output)
        self.assertNotIn("SYNTHETIC-PRIVATE-MARKER", output)

    def test_record_fifo_cannot_block_startup_or_later_jobs(self):
        record = self.bad / "record.json"
        record.unlink()
        os.mkfifo(record)
        self.observe()


if __name__ == "__main__":
    unittest.main()
