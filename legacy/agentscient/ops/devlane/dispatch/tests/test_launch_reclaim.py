"""The dispatch store's retention: launch.py's backlog sweep at close.

Authored against the contract in `_reclaim_store`'s docstring, not a reading of
its body: at a close the launcher drops the reproducible bulk (`snapshot/`,
`home/`) of jobs that closed outside the retention window, never touches the
job that just closed, and keeps every piece of evidence.

Origin: T-204, opened 2026-09-18 after the store was measured at 1,556,838
inodes -- 44% of the host, 845 jobs since 2026-09-03, no retention rule -- and
swept by hand for the third time in three days.

Fixture set. Planted faults it must catch, each keeping the job whole:
  fresh       a job inside the retention window
  self        the job that just closed, however old the window makes it
  resumable   an invalid envelope, which `resume` reads both directories for
  orphan      a head this repository cannot resolve
  residue     residual paths with no patch capturing them
  nostamp     a record with no readable closed stamp
  disabled    DISPATCH_RECLAIM off, or a window that is not a positive integer
Clean neighbours it must sweep:
  aged        an ok envelope, a head the repository holds, outside the window
  sealed      the same, with the tree sealed 555 as the harness leaves it
  patched     residual paths WITH the patch that captured them
"""

from __future__ import annotations

import contextlib
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import launch_support as ls

from ops.testkit import World

LAUNCH = Path(__file__).resolve().parents[1] / "launch.py"
CONF = Path(__file__).resolve().parents[3] / "lane.conf"


def stamp(days_ago):
    when = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


class StoreSweep(unittest.TestCase):
    """Rows by id; each plants a store and calls the sweep once."""

    def setUp(self):
        self.world = World()
        self.addCleanup(self.world.close)
        self.root = Path(self.world.root)
        os.environ["LANE_CONF"] = str(CONF)
        self.launch = ls.load_path(self, LAUNCH, "launch_under_test")
        self.store = self.root / "store"
        self.store.mkdir(parents=True, exist_ok=True)
        os.environ["DISPATCH_JOBS"] = str(self.store)
        self.addCleanup(os.environ.pop, "DISPATCH_JOBS", None)
        self.repo, self.head = self._repo()

    # -- apparatus ----------------------------------------------------------

    def _repo(self):
        repo = self.root / "repo"
        repo.mkdir(parents=True, exist_ok=True)
        def run(*a):
            return self.world.git(*a, cwd=repo, timeout=30)

        run("init", "-q", "-b", "main")
        (repo / "f").write_text("x")
        run("add", "-A")
        run("commit", "-qm", "one")
        head = run("rev-parse", "HEAD").stdout.strip()
        self.assertEqual(len(head), 40, "the fixture repository has no commit")
        return repo, head

    def _job(self, jid, *, closed_days=9, status="ok", head=None,
             residual=(), patch=None, seal=False, record=True):
        job = self.store / jid
        for part in ("snapshot", "home", "out", "context"):
            (job / part).mkdir(parents=True, exist_ok=True)
            (job / part / "leaf").write_text(part)
        (job / "snapshot" / "deep").mkdir(exist_ok=True)
        (job / "snapshot" / "deep" / "leaf").write_text("deep")
        (job / "diff.patch").write_text("diff")
        result = {"head": self.head if head is None else head,
                  "residual_paths": list(residual),
                  "envelope": {"status": status}}
        if patch:
            result["residual_patch"] = patch
        rec = {"id": jid, "status": "closed", "result": result,
               "at": ({"closed": stamp(closed_days)} if closed_days is not None
                      else {})}
        if record:
            (job / "record.json").write_text(json.dumps(rec))
        if seal:
            for d in sorted((job / "snapshot").rglob("*"), reverse=True):
                if d.is_dir():
                    d.chmod(0o555)
            (job / "snapshot").chmod(0o555)
            self.addCleanup(self._unseal, job)
        return job

    def _unseal(self, job):
        snap = job / "snapshot"
        if not snap.exists():
            return
        for d in (snap, *snap.rglob("*")):
            if d.is_dir():
                with contextlib.suppress(OSError):
                    d.chmod(0o755)

    def _sweep(self, exclude="none", conf=("on", "3")):
        """Run one sweep with the conf this row declares.

        The seam is `_reclaim_conf`, the launcher's own reader for these two
        keys, so a row states its setting without writing the host's lane.conf.
        """
        real = self.launch._reclaim_conf
        self.launch._reclaim_conf = lambda: conf
        self.addCleanup(setattr, self.launch, "_reclaim_conf", real)
        return self.launch._reclaim_store(self.repo, exclude)

    def _assert_whole(self, job, why):
        self.assertTrue((job / "snapshot" / "leaf").is_file(), f"{why}: snapshot/ went")
        self.assertTrue((job / "home" / "leaf").is_file(), f"{why}: home/ went")

    def _assert_swept(self, job):
        self.assertFalse((job / "snapshot").exists(), "snapshot/ survived")
        self.assertFalse((job / "home").exists(), "home/ survived")
        self.assertTrue((job / "record.json").is_file(), "the record was dropped")
        self.assertTrue((job / "diff.patch").is_file(), "diff.patch was dropped")
        for part in ("out", "context"):
            self.assertTrue((job / part / "leaf").is_file(), f"{part}/ was dropped")

    # -- clean neighbours ---------------------------------------------------

    def test_aged_job_is_swept_and_all_its_evidence_kept(self):
        job = self._job("aged")
        out = self._sweep()
        self.assertTrue(out["swept"], out)
        self.assertEqual(out["reclaimed"], 1, out)
        self._assert_swept(job)

    def test_sealed_tree_is_still_swept(self):
        """The fault met by hand on 2026-09-18: the 555 seal runs through the
        whole tree, and rmtree cannot unlink from a directory it cannot write."""
        job = self._job("sealed", seal=True)
        self.assertFalse(os.access(job / "snapshot" / "deep", os.W_OK),
                         "the fixture did not actually seal the tree")
        self._sweep()
        self.assertFalse((job / "snapshot").exists(), "a sealed snapshot survived")

    def test_residual_with_its_patch_is_swept(self):
        job = self._job("patched", residual=["a.py"], patch="residual.patch")
        self._sweep()
        self._assert_swept(job)

    # -- planted faults -----------------------------------------------------

    def test_a_job_inside_the_window_is_kept_whole(self):
        job = self._job("fresh", closed_days=1)
        out = self._sweep()
        self.assertEqual(out["reclaimed"], 0, out)
        self._assert_whole(job, "fresh")

    def test_the_job_that_just_closed_is_never_touched(self):
        job = self._job("self", closed_days=99)
        out = self._sweep(exclude="self")
        self.assertEqual(out["reclaimed"], 0, out)
        self._assert_whole(job, "the closing job")

    def test_resumable_envelope_is_kept_whole(self):
        job = self._job("resumable", status="invalid")
        self._sweep()
        self._assert_whole(job, "resumable")

    def test_head_absent_from_the_repository_is_kept_whole(self):
        job = self._job("orphan", head="0" * 40)
        self._sweep()
        self._assert_whole(job, "unreachable head")

    def test_residual_without_a_patch_is_kept_whole(self):
        job = self._job("residue", residual=["a.py", "b.py"])
        self._sweep()
        self._assert_whole(job, "uncaptured residue")

    def test_unreadable_closed_stamp_is_kept_whole(self):
        job = self._job("nostamp", closed_days=None)
        self._sweep()
        self._assert_whole(job, "no closed stamp")

    def test_a_job_with_no_record_is_kept_whole(self):
        job = self._job("norecord", record=False)
        self._sweep()
        self._assert_whole(job, "no record")

    def test_conf_off_sweeps_nothing(self):
        job = self._job("aged")
        out = self._sweep(conf=("off", "3"))
        self.assertFalse(out["swept"], out)
        self.assertIn("DISPATCH_RECLAIM", out["reason"])
        self._assert_whole(job, "conf off")

    def test_a_window_that_is_not_a_positive_integer_sweeps_nothing(self):
        for bad in ("0", "-1", "", "three"):
            with self.subTest(days=bad):
                job = self._job(f"aged-{bad or 'blank'}")
                out = self._sweep(conf=("on", bad))
                self.assertFalse(out["swept"], out)
                self.assertIn("positive integer", out["reason"])
                self._assert_whole(job, f"window {bad!r}")

    # -- the sweep separates the population ---------------------------------

    def test_one_sweep_takes_only_what_every_reading_allows(self):
        allowed = [self._job("aged-1"), self._job("aged-2")]
        held = {
            "fresh": self._job("held-fresh", closed_days=1),
            "resumable": self._job("held-resumable", status="invalid"),
            "orphan": self._job("held-orphan", head="0" * 40),
            "residue": self._job("held-residue", residual=["a"]),
        }
        out = self._sweep()
        self.assertEqual(out["reclaimed"], 2, out)
        self.assertEqual(out["kept"], len(held), out)
        for job in allowed:
            self._assert_swept(job)
        for why, job in held.items():
            self._assert_whole(job, why)


if __name__ == "__main__":
    unittest.main()
