"""Scoped freshness: a push depends on its own remote ref, a PR on its base.

Movement on other refs is another lineage's business. Detached HEAD
with no explicit push target keeps the old breadth: every ref. The
fetch itself is unchanged: it always runs and refreshes every
remote-tracking ref.
"""

import sys
import unittest

import support

from ops.testkit import World, run
from ops.testkit.world import FIXED_DATE

SCRIPT = support.CLAUDE_DIR / "context-precheck.py"
TIMEOUT = 120


class PrecheckScope(unittest.TestCase):
    """Clone on ``records`` off the seed; only the command's refs matter."""

    maxDiff = None

    def setUp(self):
        self.world = World(prefix="precheck-scope-")
        self.addCleanup(self.world.close)
        self.world.require("git")
        self.hook = self._subject()
        self.env = self.world.env()
        pair = self.world.root / "pair"
        pair.mkdir()
        self.fixture = support.StaleClone(pair, self.env).build()
        self.records_new_sha = None
        self.world.git(
            "checkout", "-q", "-b", "records", cwd=self.fixture.clone,
        )
        branch = self.world.git(
            "rev-parse", "--abbrev-ref", "HEAD", cwd=self.fixture.clone,
        ).stdout.strip()
        self.assertEqual(
            branch, "records",
            f"INVALID: clone is on {branch!r}, not records",
        )
        head = self._rev("HEAD")
        self.assertEqual(
            head, self.fixture.old_sha,
            "INVALID: records was not created off the seed",
        )
        self.assertEqual(self._rev("origin/main"), self.fixture.old_sha)

    def _subject(self):
        self.assertTrue(
            SCRIPT.is_file(),
            f"INVALID: context-precheck.py missing: {SCRIPT}",
        )
        staged = self.world.stage(SCRIPT, siblings=("command_shape.py",))
        self.world.plant_bytes(staged.parent.parent / "guidance.py", (SCRIPT.parent.parent / "guidance.py").read_bytes())
        return staged

    def _rev(self, name: str) -> str:
        return self.world.git(
            "rev-parse", name, cwd=self.fixture.clone,
        ).stdout.strip()

    def _has_ref(self, name: str) -> bool:
        proc = self.world.git(
            "rev-parse", "--verify", "--quiet", name,
            cwd=self.fixture.clone, check=False,
        )
        return proc.exit == 0

    def _run(self, command: str):
        payload = support.bash_payload(command)
        return run(
            [sys.executable, str(self.hook)],
            cwd=self.fixture.clone,
            env=self.env,
            stdin=payload,
            timeout=TIMEOUT,
        )

    def _drop_fetch_head(self):
        fetch_head = self.fixture.clone / ".git" / "FETCH_HEAD"
        if fetch_head.exists():
            fetch_head.unlink()
        self.assertFalse(
            fetch_head.exists(),
            "INVALID: FETCH_HEAD still present",
        )

    def _prove_only_origin_main_moved(self):
        self.assertEqual(self._rev("origin/main"), self.fixture.old_sha)
        self.assertNotEqual(self.fixture.old_sha, self.fixture.new_sha)
        self.assertFalse(
            self._has_ref("origin/records"),
            "INVALID: origin/records exists; this case is only origin/main",
        )
        remote = self.world.git(
            "rev-parse", "HEAD", cwd=self.fixture.bare,
        ).stdout.strip()
        self.assertEqual(
            remote, self.fixture.new_sha,
            "INVALID: bare HEAD is not the moved main",
        )

    def _plant_moved_origin_records(self):
        """origin/records exists in the clone and is behind the remote."""
        seed = self.fixture.seed
        bare = self.fixture.bare
        clone = self.fixture.clone
        self.world.git(
            "branch", "records", self.fixture.old_sha, cwd=seed,
        )
        self.world.git("push", "-q", str(bare), "records", cwd=seed)
        bare_old = self.world.git(
            "rev-parse", "refs/heads/records", cwd=bare,
        ).stdout.strip()
        self.assertEqual(
            bare_old, self.fixture.old_sha,
            "INVALID: remote records is not the seed",
        )
        self.world.git(
            "fetch", "-q", "origin",
            "refs/heads/records:refs/remotes/origin/records",
            cwd=clone,
        )
        self._drop_fetch_head()
        self.assertEqual(
            self._rev("origin/records"), self.fixture.old_sha,
            "INVALID: clone origin/records is not the seed",
        )
        self.assertEqual(
            self._rev("origin/main"), self.fixture.old_sha,
            "INVALID: fetching records refreshed origin/main",
        )

        self.world.git("checkout", "-q", "records", cwd=seed)
        self.world.plant_text(
            seed / "records.txt", "records-one\n",
            recognisable="records-one",
        )
        self.world.git("add", "records.txt", cwd=seed)
        self.world.git(
            "commit", "-q", "-m", "R", "--date", FIXED_DATE, cwd=seed,
        )
        self.records_new_sha = self.world.git(
            "rev-parse", "HEAD", cwd=seed,
        ).stdout.strip()
        self.assertNotEqual(
            self.records_new_sha, self.fixture.old_sha,
            "INVALID: records HEAD did not move",
        )
        self.assertNotEqual(
            self.records_new_sha, self.fixture.new_sha,
            "INVALID: records SHA collided with main",
        )
        self.world.git("push", "-q", str(bare), "records", cwd=seed)
        bare_new = self.world.git(
            "rev-parse", "refs/heads/records", cwd=bare,
        ).stdout.strip()
        self.assertEqual(
            bare_new, self.records_new_sha,
            "INVALID: bare records still at the seed",
        )
        self.assertEqual(
            self._rev("origin/records"), self.fixture.old_sha,
            "INVALID: clone origin/records moved before the hook ran",
        )
        self.assertEqual(
            self._rev("origin/main"), self.fixture.old_sha,
            "INVALID: clone origin/main moved before the hook ran",
        )

    def test_push_records_allows_when_only_origin_main_moved(self):
        """git push origin records: origin/main moved is another lineage."""
        self._prove_only_origin_main_moved()
        proc = self._run("git push origin records")
        decision, reason = support.diagnostic(proc.stdout)
        self.assertNotEqual(
            decision, "warn",
            f"push of records denied for movement on origin/main: "
            f"{decision!r} {reason!r}\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}",
        )
        self.assertEqual(
            self._rev("origin/main"), self.fixture.new_sha,
            "fetch did not land; origin/main still stale",
        )

    def test_push_records_denies_naming_origin_records_only(self):
        """git push origin records while origin/records moved names that ref.

        origin/main also moved; that is another lineage and must not be
        named in the deny.
        """
        self._plant_moved_origin_records()
        proc = self._run("git push origin records")
        decision, reason = support.diagnostic(proc.stdout)
        self.assertEqual(
            decision, "warn",
            f"wanted deny for moved origin/records, got {decision!r}\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}",
        )
        self.assertIn(
            "origin/records", reason,
            f"deny did not name origin/records: {reason!r}",
        )
        self.assertNotIn(
            "origin/main", reason,
            f"deny named origin/main; that is another lineage: {reason!r}",
        )
        self.assertEqual(
            self._rev("origin/records"), self.records_new_sha,
            "fetch did not land; origin/records still stale",
        )
        self.assertEqual(
            self._rev("origin/main"), self.fixture.new_sha,
            "fetch did not land; origin/main still stale",
        )

    def test_pr_create_base_main_denies_when_origin_main_moved(self):
        """gh pr create --base main depends on origin/main."""
        self._prove_only_origin_main_moved()
        proc = self._run("gh pr create --base main --fill")
        decision, reason = support.diagnostic(proc.stdout)
        self.assertEqual(
            decision, "warn",
            f"PR against main wanted deny, got {decision!r}\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}",
        )
        self.assertIn(
            "origin/main", reason,
            f"deny did not name origin/main: {reason!r}",
        )
        self.assertEqual(
            self._rev("origin/main"), self.fixture.new_sha,
            "fetch did not land; origin/main still stale",
        )

    def test_pr_create_without_base_denies_when_default_branch_moved(self):
        """gh pr create --fill with no --base depends on origin/HEAD."""
        self._prove_only_origin_main_moved()
        self.assertTrue(
            self._has_ref("origin/HEAD"),
            "INVALID: clone has no origin/HEAD",
        )
        self.assertEqual(
            self._rev("origin/HEAD"), self.fixture.old_sha,
            "INVALID: origin/HEAD is not the stale default",
        )
        proc = self._run("gh pr create --fill")
        decision, reason = support.diagnostic(proc.stdout)
        self.assertEqual(
            decision, "warn",
            f"PR with no base wanted deny, got {decision!r}\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}",
        )
        self.assertTrue(
            "origin/HEAD" in reason or "origin/main" in reason,
            f"deny named neither origin/HEAD nor origin/main: {reason!r}",
        )
        self.assertEqual(
            self._rev("origin/main"), self.fixture.new_sha,
            "fetch did not land; origin/main still stale",
        )

    def test_detached_head_push_records_denies_when_origin_main_moved(self):
        """Detached HEAD keeps the old breadth even for an explicit dest."""
        self._prove_only_origin_main_moved()
        self.world.git(
            "checkout", "--detach", "-q", cwd=self.fixture.clone,
        )
        abbrev = self.world.git(
            "rev-parse", "--abbrev-ref", "HEAD", cwd=self.fixture.clone,
        ).stdout.strip()
        self.assertEqual(
            abbrev, "HEAD",
            f"INVALID: HEAD is {abbrev!r}, not detached",
        )
        proc = self._run("git push origin records")
        decision, reason = support.diagnostic(proc.stdout)
        self.assertEqual(
            decision, "warn",
            f"detached HEAD wanted old breadth deny, got {decision!r}\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}",
        )
        self.assertIn(
            "origin/main", reason,
            f"old breadth deny did not name origin/main: {reason!r}",
        )
        self.assertEqual(
            self._rev("origin/main"), self.fixture.new_sha,
            "fetch did not land; origin/main still stale",
        )

    def test_push_head_refspec_to_records_allows_when_only_origin_main_moved(
            self):
        """git push origin HEAD:refs/heads/records reads the destination."""
        self._prove_only_origin_main_moved()
        branch = self.world.git(
            "rev-parse", "--abbrev-ref", "HEAD", cwd=self.fixture.clone,
        ).stdout.strip()
        self.assertEqual(
            branch, "records",
            f"INVALID: clone is on {branch!r}, not records",
        )
        proc = self._run("git push origin HEAD:refs/heads/records")
        decision, reason = support.diagnostic(proc.stdout)
        self.assertNotEqual(
            decision, "warn",
            f"HEAD:refs/heads/records denied for origin/main: "
            f"{decision!r} {reason!r}\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}",
        )
        self.assertEqual(
            self._rev("origin/main"), self.fixture.new_sha,
            "fetch did not land; origin/main still stale",
        )
