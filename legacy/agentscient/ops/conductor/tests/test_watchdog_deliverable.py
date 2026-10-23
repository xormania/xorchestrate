"""The watchdog reports drift and stall from the deliverable lock.

Owner, 2026-09-11: "i want watchdog updated right now to keep you from
fucking up." With `<state>/<lane>/deliverable.json` naming branches, a
record launched after the lock on another branch is `drift`; a
deliverable branch with nothing running and its newest record closed
STALL_MIN minutes ago is `stall`; a stopped ladder on a deliverable
branch is `stall`. Without a lock, none of these lines exist.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import test_watchdog as base
from live_store_guard import IGNORE, TOLERATE_NEW

from ops.testkit import World, run

LOCKED = "lane/deliverable-x"
OTHER = "lane/other-y"
DRIFT_ID = "20260911T070000Z-tests-grok-d41f70"
QUIET_ID = "20260911T050000Z-tests-grok-5ca1e0"
LADDER_ID = "20260911T060000Z-ladder-1adde7"


def _ago(minutes):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


class DeliverableLines(base._WatchdogFixture):
    def setUp(self):
        self.world = World(prefix="watchdog-deliverable-")
        self.addCleanup(self.world.close)
        self._live_watch = self.world.watch(
            *base._live_store_paths(),
            ignore=IGNORE,
            tolerate_new=TOLERATE_NEW,
            contents=False,
        )
        self.root = self.world.root
        self.home = self.world.home
        self.xdg = self.world.xdg_state
        self.scratch = self.world.root / "scratch"
        self.scratch.mkdir()
        self.bin = self.world.bin
        self.store = self.xdg / "agentscient" / "dispatch"
        self.home_store = self.home / ".local" / "state" / "agentscient" / "dispatch"
        self.gh = self.bin / "gh"
        self.argv_log = self.scratch / "gh-argv.jsonl"
        self.staged = self.world.stage(base.TOOL, siblings=base.SIBLINGS)
        self.conf = self.staged.parent.parent / "lane.conf"
        self.world.plant_text(
            self.conf,
            "LANE_NAME='agentscient'\nTRUNK_BRANCH='dev'\n",
        )
        self._workers = []

    def tearDown(self):
        try:
            self._live_watch.assert_unchanged(self)
        finally:
            self.world.close()

    def _plant_bytes(self, path, data):
        self.world.plant_bytes(path, data)

    def dead_pid(self):
        pid = 1_000_000_000
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        return pid

    def invoke(
        self,
        argv,
        *,
        extra_env=None,
        xdg=False,
        timeout=base.RUN_CAP,
        home=None,
    ):
        self.assertIs(xdg, False, "this family uses the world's XDG root")
        self.assertIsNone(home, "this family uses the world's home")
        extra = {"LANE_CONF": str(self.conf), "PYTHONUNBUFFERED": "1"}
        if extra_env:
            extra.update(extra_env)
        env = self.world.env(extra)
        return run(
            [sys.executable, "-u", str(self.staged), *argv],
            cwd=base.REPO,
            env=env,
            timeout=timeout,
        )

    def plant_lock(self, minutes_ago=120):
        path = self.xdg / "agentscient" / "deliverable.json"
        self.world.plant_text(
            path,
            json.dumps({
                "branches": [LOCKED],
                "why": "slice",
                "by": "Owner <o@x>",
                "at": _ago(minutes_ago),
            }) + "\n",
        )
        self.assertIn(LOCKED, path.read_text(), "plant: lock names the branch")

    def plant_closed(self, rec_id, branch, closed_minutes_ago):
        launched = _ago(closed_minutes_ago + 5)
        job_dir = self.plant_job(rec_id, pid=self.dead_pid(), launched=launched)
        payload = self.record_dict(rec_id, status="closed", launched=launched, closed=_ago(closed_minutes_ago), env_status="ok", changed_paths=[])
        payload["lineage"] = {"branch": branch, "base_sha": "0" * 40}
        self.world.plant_text(
            job_dir / "record.json",
            json.dumps(payload, indent=2) + "\n",
        )
        return job_dir

    def test_no_lock_says_nothing_about_deliverables(self):
        self.plant_closed(DRIFT_ID, OTHER, 1)
        proc = self.once(["--report-existing"])
        self.assertEqual(self.named_lines(proc, "drift "), [], proc.stdout)
        self.assertEqual(self.named_lines(proc, "stall "), [], proc.stdout)

    def test_a_record_on_another_branch_after_the_lock_is_drift(self):
        self.plant_lock(minutes_ago=120)
        self.plant_closed(DRIFT_ID, OTHER, 1)
        proc = self.once(["--report-existing"])
        lines = self.named_lines(proc, f"drift {DRIFT_ID}")
        self.assertEqual(len(lines), 1, proc.stdout)
        self.assertIn(OTHER, lines[0])
        self.assertIn(LOCKED, lines[0])

    def test_a_record_launched_before_the_lock_is_not_drift(self):
        self.plant_lock(minutes_ago=10)
        self.plant_closed(QUIET_ID, OTHER, 60)
        proc = self.once(["--report-existing"])
        self.assertEqual(self.named_lines(proc, "drift "), [], proc.stdout)

    def test_a_record_closed_at_start_is_not_drift_without_report_existing(self):
        """LD-70: backlog is silent in the drift family too (owner,
        2026-09-16, through the Lead). The line with the flag is pinned
        by test_a_record_on_another_branch_after_the_lock_is_drift."""
        self.plant_lock(minutes_ago=120)
        self.plant_closed(DRIFT_ID, OTHER, 1)
        proc = self.once()
        self.assertEqual(self.named_lines(proc, "drift "), [], proc.stdout)
        self.assertEqual(
            self.named_lines(proc, f"closed {DRIFT_ID}"), [], proc.stdout
        )

    def test_a_quiet_deliverable_branch_is_stall(self):
        self.plant_lock(minutes_ago=180)
        self.plant_closed(QUIET_ID, LOCKED, base.watchdog_module().STALL_MIN + 5 if hasattr(base, "watchdog_module") else 50)
        proc = self.once(["--report-existing"])
        lines = self.named_lines(proc, f"stall {LOCKED}")
        self.assertEqual(len(lines), 1, proc.stdout)
        self.assertIn(QUIET_ID, lines[0])

    def test_a_fresh_deliverable_record_is_not_stall(self):
        self.plant_lock(minutes_ago=180)
        self.plant_closed(QUIET_ID, LOCKED, 5)
        proc = self.once(["--report-existing"])
        self.assertEqual(self.named_lines(proc, "stall "), [], proc.stdout)

    def test_a_stopped_deliverable_ladder_is_stall(self):
        self.plant_lock(minutes_ago=180)
        self.plant_closed(QUIET_ID, LOCKED, 5)
        self.plant_ladder(LADDER_ID, {"id": LADDER_ID, "branch": LOCKED, "status": "stopped",
                                      "stopped": {"stage": "fix-tests", "reason": "withheld", "record": "none"},
                                      "stages": [{"name": "tests", "attempt": 1, "outcome": "ok"}]})
        proc = self.once(["--report-existing"])
        lines = self.named_lines(proc, f"stall {LOCKED}: ladder {LADDER_ID}")
        self.assertEqual(len(lines), 1, proc.stdout)
        self.assertIn("withheld", lines[0])
        self.assertIn("fix-tests", lines[0])


if __name__ == "__main__":
    import unittest
    unittest.main()
