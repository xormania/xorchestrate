"""The launcher leaves the record beside the job, same bytes as tracked.

Written from the Unit A plan (A1) and CONTRACT.md §The job directory:
record.json is the dispatch record, the same bytes as the tracked copy,
written at launch and again at close, and rewritten by resume and close.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import launch_support as ls

from ops.testkit import World, run  # noqa: F401 - World via _TempLaunch


class RecordCopyBesideTheJob(ls._TempLaunch):
    """A1 — _write_record writes <job>/record.json, byte-identical."""

    def job_record_path(self, rec=None):
        if rec is not None:
            return self.jobs_root / rec["id"] / "record.json"
        return self.the_job_dir() / "record.json"

    def assert_copies_byte_identical(self, rec=None):
        tracked = self.the_record_path()
        beside = self.job_record_path(rec)
        self.assertTrue(
            beside.is_file(),
            f"job-directory record.json missing: {beside}",
        )
        self.assertTrue(
            tracked.is_file(),
            f"tracked record missing: {tracked}",
        )
        self.assertEqual(
            beside.read_bytes(),
            tracked.read_bytes(),
            "job-directory record.json must be the same bytes as the "
            "tracked copy",
        )
        return beside.read_bytes()

    def test_record_json_matches_the_tracked_copy_while_launched(self):
        os.environ["TASK_LAUNCH_SLEEP"] = "0.5"
        seen = {}

        def watch():
            deadline = time.time() + 4
            while not self.start_witness.is_file() and time.time() < deadline:
                time.sleep(0.01)
            files = self.record_files()
            seen["n_tracked"] = len(files)
            if not files:
                return
            tracked = files[0]
            job_id = tracked.stem
            beside = self.jobs_root / job_id / "record.json"
            seen["beside_exists"] = beside.is_file()
            if beside.is_file():
                tracked_bytes = tracked.read_bytes()
                beside_bytes = beside.read_bytes()
                seen["identical"] = tracked_bytes == beside_bytes
                seen["status"] = json.loads(beside_bytes).get("status")
                seen["result"] = json.loads(beside_bytes).get("result")
            exits = list(self.jobs_root.glob("*/exit"))
            seen["exit"] = bool(exits)

        watcher = threading.Thread(target=watch)
        watcher.start()
        self.dispatch(self.argv_for(job="plan", harness="grok", stage="plan"))
        watcher.join(timeout=6)
        self.assertTrue(
            seen.get("beside_exists"),
            "record.json must sit in the job directory at launch: "
            f"{seen!r}",
        )
        self.assertTrue(
            seen.get("identical"),
            "the launched job copy must be byte-identical to the "
            f"tracked record: {seen!r}",
        )
        self.assertEqual(seen.get("status"), "launched")
        self.assertIsNone(seen.get("result"))
        self.assertFalse(
            seen.get("exit"),
            "exit is written last, so it must be absent at child-start",
        )

    def test_record_json_matches_the_tracked_copy_at_close(self):
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        body = self.assert_copies_byte_identical(rec)
        closed = json.loads(body)
        self.assertEqual(closed["id"], rec["id"])
        self.assertEqual(closed["status"], "closed")
        self.assertEqual(closed["job"], "plan")

    def test_resume_rewrites_both_copies_identically(self):
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        tracked = self.the_record_path()
        before = tracked.read_bytes()
        self.assertTrue(before, "plant: the closed tracked record exists")
        os.environ["TASK_LAUNCH_TOKEN"] = "SECOND-ATTEMPT"
        os.environ["TASK_LAUNCH_STDOUT"] = "token"
        self.witness.unlink()
        code, out, err = self.run_main(["resume", rec["id"]])
        self.assertEqual(code, 0, self.combined(out, err))
        after = self.assert_copies_byte_identical()
        self.assertNotEqual(
            after,
            before,
            "resume must rewrite the record, not leave the closed copy",
        )
        rec2 = json.loads(after)
        self.assertEqual(rec2["id"], rec["id"])
        attempts = rec2.get("attempts") or []
        self.assertGreater(
            len(attempts),
            len(rec.get("attempts") or []),
            "the rewritten record must carry the resumed attempt",
        )

    def test_close_rewrites_both_copies_identically(self):
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        job_id = "20260826T000000Z-plan-grok-cl0se6"
        src_snap = Path(rec["snapshot"]["root"])
        self.assertTrue(src_snap.is_dir(), "plant: first snapshot exists")

        job_dir = self.jobs_root / job_id
        job_dir.mkdir()
        snap = job_dir / "snapshot"
        run(
            ["git", "clone", "--no-hardlinks", str(src_snap), str(snap)],
            cwd=self.repo, env=self.env, timeout=60, expect=0,
        )
        pid = self.dead_pid()
        self.world.plant_text(
            job_dir / "state.json",
            json.dumps({
                "pid": pid,
                "pgid": pid,
                "session": {"id": rec["session"]["id"]},
                "stream": None,
                "attempt": 1,
            }) + "\n",
        )
        self.world.plant_text(job_dir / "exit", "0\n")
        self.world.plant_text(job_dir / "prompt.txt", "planted brief\n")
        self.world.plant_text(job_dir / "raw.out", "{}\n")
        self.assertTrue((job_dir / "exit").is_file(), "plant: exit is present")
        self.assertFalse(
            (job_dir / "record.json").exists(),
            "plant: close must be the first writer of job record.json",
        )

        seed = json.loads(json.dumps(rec))
        seed["id"] = job_id
        seed["status"] = "launched"
        seed["result"] = None
        seed["at"]["closed"] = None
        seed["snapshot"]["root"] = str(snap)
        # This is a new synthetic job, not a job entitled to retain the
        # original live job's isolated home. Bind every home carrier.
        home = str(job_dir / "home" / "grok")
        seed["harness"]["isolation"]["home"] = home
        seed["harness"]["isolation"]["store"] = str(Path(home) / "sessions")
        seed["harness"]["isolation"]["env"] = {
            name: home for name in ("HOME", "GROK_HOME")
        }
        tracked = (
            self.repo / ".dev" / "records" / "dispatches" / f"{job_id}.json"
        )
        self.world.plant_text(
            tracked, json.dumps(seed, indent=2) + "\n",
        )
        planted = json.loads(tracked.read_text(encoding="utf-8"))
        self.assertEqual(planted["status"], "launched")
        self.assertIsNone(planted["result"])

        before_tracked = tracked.read_bytes()
        code, out, err = self.run_main(["close", job_id])
        self.assertEqual(code, 0, self.combined(out, err))
        beside = job_dir / "record.json"
        self.assertTrue(
            beside.is_file(),
            f"close must write record.json beside the job: {out}{err}",
        )
        self.assertEqual(
            beside.read_bytes(),
            tracked.read_bytes(),
            "close must rewrite both copies to the same bytes",
        )
        self.assertNotEqual(
            tracked.read_bytes(),
            before_tracked,
            "close must rewrite the launched record, not leave it",
        )
        closed = json.loads(beside.read_text(encoding="utf-8"))
        self.assertEqual(closed["id"], job_id)
        self.assertEqual(closed["status"], "closed")
