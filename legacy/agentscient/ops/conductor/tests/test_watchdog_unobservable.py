"""PR 66 item 2: a nonterminal job with no usable worker PID is unobservable.

Contract: ops/conductor/pr66-fixes.md item 2. Reuses the watchdog
fixture; does not read the tool source. Missing PID is reported
explicitly, excluded from running/long, and reconsidered when a live
worker appears. Closed, settling, confirmed-gone, and live-worker
rows stay as they are.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from test_watchdog import (
    CATALOGUE_JOB,
    HARNESS,
    _WatchdogFixture,
    utc_ago,
)

from ops.testkit import World

UNOBS_ID = "20260905T020000Z-code-grok-aa3301"
NULL_ID = "20260905T020100Z-code-grok-aa3302"
KEYLESS_ID = "20260905T020200Z-code-grok-aa3303"
RECOVER_ID = "20260905T020300Z-code-grok-aa3304"
LONG_MISS_ID = "20260905T020400Z-code-grok-aa3305"
LIVE_ID = "20260905T020500Z-code-grok-aa3306"
GONE_ID = "20260905T020600Z-code-grok-aa3307"
SETTLE_ID = "20260905T020700Z-code-grok-aa3308"
CLOSE_ID = "20260905T020800Z-code-grok-aa3309"


class MissingPidUnobservable(_WatchdogFixture):
    """No usable worker PID is unobservable, not running or gone."""

    def setUp(self):
        super().setUp()
        self.world = World(prefix="watchdog-unobs-")
        self.addCleanup(self.world.close)

    def unobservable_lines(self, proc, rec_id: str) -> list[str]:
        return [
            ln
            for ln in self.stdout_lines(proc)
            if rec_id in ln
            and "unobservable" in ln.lower()
            and not ln.startswith("heartbeat:")
        ]

    def running_section(self, beat: str) -> str:
        self.assertIn("running=", beat, beat)
        return beat.split("settling=")[0]

    def plant_missing_state(self, rec_id: str, **kwargs) -> Path:
        job_dir = self.plant_job(rec_id, **kwargs)
        state = job_dir / "state.json"
        self.assertFalse(state.exists(), f"plant: {rec_id} must have no state.json")
        return job_dir

    def test_absent_state_json_is_unobservable_and_not_running(self):
        job_dir = self.plant_missing_state(UNOBS_ID, launched=utc_ago(4))
        self.assertFalse((job_dir / "state.json").exists())
        rec = json.loads((job_dir / "record.json").read_text(encoding="utf-8"))
        self.assertEqual(rec["status"], "launched")
        proc = self.once(extra=["--heartbeat-every", "1"])
        self.assert_armed(proc, store=self.store)
        self.assertEqual(proc.returncode, 0, self.combined(proc))
        lines = self.unobservable_lines(proc, UNOBS_ID)
        self.assertEqual(len(lines), 1, proc.stdout)
        self.assertFalse(
            self.named_lines(proc, f"harness gone {UNOBS_ID}"),
            proc.stdout,
        )
        beats = self.heartbeat_lines(proc)
        self.assertEqual(len(beats), 1, proc.stdout)
        self.assertNotIn(UNOBS_ID, self.running_section(beats[0]))

    def test_null_pid_is_unobservable_not_gone(self):
        raw = json.dumps({"pid": None, "pgid": None}) + "\n"
        job_dir = self.plant_job(NULL_ID, launched=utc_ago(3))
        self.world.plant_bytes(job_dir / "state.json", raw.encode("utf-8"))
        landed = json.loads((job_dir / "state.json").read_text(encoding="utf-8"))
        self.assertIn("pid", landed)
        self.assertIsNone(landed["pid"])
        proc = self.once(extra=["--heartbeat-every", "1"])
        self.assertEqual(proc.returncode, 0, self.combined(proc))
        self.assertEqual(len(self.unobservable_lines(proc, NULL_ID)), 1, proc.stdout)
        self.assertFalse(self.named_lines(proc, f"harness gone {NULL_ID}"), proc.stdout)
        beats = self.heartbeat_lines(proc)
        self.assertEqual(len(beats), 1, proc.stdout)
        self.assertNotIn(NULL_ID, self.running_section(beats[0]))

    def test_state_json_without_pid_is_unobservable(self):
        raw = json.dumps({"note": "no pid key"}) + "\n"
        job_dir = self.plant_job(KEYLESS_ID, launched=utc_ago(3))
        self.world.plant_bytes(job_dir / "state.json", raw.encode("utf-8"))
        landed = json.loads((job_dir / "state.json").read_text(encoding="utf-8"))
        self.assertNotIn("pid", landed)
        proc = self.once(extra=["--heartbeat-every", "1"])
        self.assertEqual(proc.returncode, 0, self.combined(proc))
        self.assertEqual(
            len(self.unobservable_lines(proc, KEYLESS_ID)),
            1,
            proc.stdout,
        )
        beats = self.heartbeat_lines(proc)
        self.assertEqual(len(beats), 1, proc.stdout)
        self.assertNotIn(KEYLESS_ID, self.running_section(beats[0]))

    def test_missing_pid_is_not_a_long_warning(self):
        job_dir = self.plant_missing_state(
            LONG_MISS_ID,
            timeout=900,
            launched=utc_ago(13),
        )
        rec = json.loads((job_dir / "record.json").read_text(encoding="utf-8"))
        self.assertEqual(rec["caps"]["timeout"], 900)
        self.assertFalse((job_dir / "state.json").exists())
        proc = self.once(extra=["--heartbeat-every", "1"])
        self.assertEqual(proc.returncode, 0, self.combined(proc))
        self.assertEqual(
            len(self.unobservable_lines(proc, LONG_MISS_ID)),
            1,
            proc.stdout,
        )
        self.assertFalse(
            self.named_lines(proc, f"long {LONG_MISS_ID}:"),
            proc.stdout,
        )
        beats = self.heartbeat_lines(proc)
        self.assertEqual(len(beats), 1, proc.stdout)
        self.assertNotIn(LONG_MISS_ID, self.running_section(beats[0]))

    def test_later_tick_recovers_when_a_live_worker_pid_appears(self):
        job_dir = self.plant_missing_state(RECOVER_ID, launched=utc_ago(2))
        self.assertFalse((job_dir / "state.json").exists())

        def attach_live_pid() -> None:
            pid = self.spawn_named(HARNESS)
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
            self.assertIn(HARNESS.encode("utf-8"), cmdline)
            payload = json.dumps({"pid": pid, "pgid": pid}) + "\n"
            state = job_dir / "state.json"
            self.world.plant_bytes(state, payload.encode("utf-8"))
            landed = json.loads(state.read_text(encoding="utf-8"))
            self.assertEqual(landed["pid"], pid)

        proc = self.loop(
            ["--tick", "0.15", "--no-forge", "--heartbeat-every", "1"],
            seconds=0.7,
            mutate=attach_live_pid,
            mutate_after=0.22,
        )
        self.assert_armed(proc, store=self.store)
        self.assertTrue(
            self.unobservable_lines(proc, RECOVER_ID),
            proc.stdout,
        )
        self.assertFalse(
            self.named_lines(proc, f"harness gone {RECOVER_ID}"),
            proc.stdout,
        )
        beats = self.heartbeat_lines(proc)
        self.assertGreaterEqual(len(beats), 2, proc.stdout)
        self.assertNotIn(RECOVER_ID, self.running_section(beats[0]))
        self.assertIn(RECOVER_ID, self.running_section(beats[-1]))

    def test_live_worker_is_not_unobservable(self):
        pid = self.spawn_named(HARNESS)
        self.plant_job(LIVE_ID, pid=pid, launched=utc_ago(3))
        proc = self.once(extra=["--heartbeat-every", "1"])
        self.assertEqual(proc.returncode, 0, self.combined(proc))
        self.assertFalse(self.unobservable_lines(proc, LIVE_ID), proc.stdout)
        self.assertFalse(self.named_lines(proc, f"harness gone {LIVE_ID}"), proc.stdout)
        beats = self.heartbeat_lines(proc)
        self.assertEqual(len(beats), 1, proc.stdout)
        self.assertIn(LIVE_ID, self.running_section(beats[0]))

    def test_dead_pid_stays_harness_gone_not_unobservable(self):
        pid = self.dead_pid()
        self.plant_job(GONE_ID, pid=pid, launched=utc_ago(9))
        proc = self.once(extra=["--heartbeat-every", "1"])
        self.assertEqual(proc.returncode, 0, self.combined(proc))
        gone = self.named_lines(proc, f"harness gone {GONE_ID}")
        self.assertEqual(len(gone), 1, proc.stdout)
        self.assertFalse(self.unobservable_lines(proc, GONE_ID), proc.stdout)
        beats = self.heartbeat_lines(proc)
        self.assertEqual(len(beats), 1, proc.stdout)
        self.assertNotIn(GONE_ID, self.running_section(beats[0]))

    def test_settling_without_pid_is_not_unobservable(self):
        job_dir = self.plant_missing_state(
            SETTLE_ID,
            exit_text="0\n",
            launched=utc_ago(5),
        )
        self.assertTrue((job_dir / "exit").is_file())
        rec = json.loads((job_dir / "record.json").read_text(encoding="utf-8"))
        self.assertEqual(rec["status"], "launched")
        proc = self.once(extra=["--heartbeat-every", "1"])
        self.assertEqual(proc.returncode, 0, self.combined(proc))
        self.assertFalse(self.unobservable_lines(proc, SETTLE_ID), proc.stdout)
        beats = self.heartbeat_lines(proc)
        self.assertEqual(len(beats), 1, proc.stdout)
        self.assertNotIn(SETTLE_ID, self.running_section(beats[0]))
        self.assertIn("settling=", beats[0])

    def test_closed_without_pid_stays_closed_not_unobservable(self):
        launched = "2026-09-04T10:00:00Z"
        job_dir = self.plant_missing_state(
            CLOSE_ID,
            status="closed",
            launched=launched,
            closed="2026-09-04T10:12:00Z",
            env_status="ok",
            changed_paths=[],
            job=CATALOGUE_JOB,
        )
        rec = json.loads((job_dir / "record.json").read_text(encoding="utf-8"))
        self.assertEqual(rec["status"], "closed")
        proc = self.once(extra=["--heartbeat-every", "1", "--report-existing"])
        self.assertEqual(proc.returncode, 0, self.combined(proc))
        self.assertFalse(self.unobservable_lines(proc, CLOSE_ID), proc.stdout)
        closed = self.named_lines(proc, f"closed {CLOSE_ID} ")
        self.assertEqual(len(closed), 1, proc.stdout)
        beats = self.heartbeat_lines(proc)
        self.assertEqual(len(beats), 1, proc.stdout)
        self.assertNotIn(CLOSE_ID, self.running_section(beats[0]))


if __name__ == "__main__":
    unittest.main()
