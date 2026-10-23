"""Freshness gate must inspect the repository the command names.

Claim 1 of the git -C finding (not recognised as consequential) is
already pinned by the moved corpus. Claim 2 is a different fact: the
gate fetches and compares in the hook process cwd, not in the repository
`-C` / `--git-dir` / `GIT_DIR` selects. `git -C .` cannot tell those
apart; these cases use two clones.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

import support

from ops.testkit import World, run

SCRIPT = support.CLAUDE_DIR / "context-precheck.py"
TIMEOUT = 120


class SelectedRepoFreshness(unittest.TestCase):
    """cwd and `other` are independent file:// clones."""

    maxDiff = None

    def setUp(self):
        self.world = World(prefix="precheck-selected-")
        self.addCleanup(self.world.close)
        self.world.require("git")
        self.hook = self._subject()
        self.env = self.world.env()
        cwd_root = self.world.root / "cwd-pair"
        other_root = self.world.root / "other-pair"
        cwd_root.mkdir()
        other_root.mkdir()
        self.cwd_fix = support.StaleClone(cwd_root, self.env).build()
        self.other_fix = support.StaleClone(other_root, self.env).build()

    def _subject(self):
        self.assertTrue(
            SCRIPT.is_file(),
            f"INVALID: context-precheck.py missing: {SCRIPT}",
        )
        staged = self.world.stage(SCRIPT, siblings=("command_shape.py",))
        self.world.plant_bytes(staged.parent.parent / "guidance.py", (SCRIPT.parent.parent / "guidance.py").read_bytes())
        return staged

    def _origin(self, fix: support.StaleClone) -> str:
        return self.world.git(
            "rev-parse", "origin/main", cwd=fix.clone,
        ).stdout.strip()

    def _fetch_head(self, fix: support.StaleClone) -> Path:
        return fix.clone / ".git" / "FETCH_HEAD"

    def _prove_stale(self, fix: support.StaleClone, label: str):
        self.assertEqual(
            self._origin(fix), fix.old_sha,
            f"INVALID: {label} origin/main is not the old SHA",
        )
        self.assertNotEqual(
            fix.old_sha, fix.new_sha,
            f"INVALID: {label} remote did not move",
        )
        remote = self.world.git(
            "rev-parse", "HEAD", cwd=fix.bare,
        ).stdout.strip()
        self.assertEqual(
            remote, fix.new_sha,
            f"INVALID: {label} bare HEAD is {remote}, not {fix.new_sha}",
        )

    def _prove_fresh(self, fix: support.StaleClone, label: str):
        fix.refresh()
        self.assertEqual(
            self._origin(fix), fix.new_sha,
            f"INVALID: {label} did not refresh to the remote",
        )

    def _fire(self, command: str, *, cwd: Path):
        payload = support.bash_payload(command)
        return run(
            [sys.executable, str(self.hook)],
            cwd=cwd,
            env=self.env,
            stdin=payload,
            timeout=TIMEOUT,
        )

    def _assert_denied_and_other_refreshed(self, command: str, *, cwd: Path):
        self._prove_stale(self.other_fix, "other")
        other_before = self._origin(self.other_fix)
        self.assertEqual(other_before, self.other_fix.old_sha)
        self.assertFalse(
            self._fetch_head(self.other_fix).exists(),
            "INVALID: other FETCH_HEAD present before the hook",
        )
        proc = self._fire(command, cwd=cwd)
        decision, reason = support.diagnostic(proc.stdout)
        self.assertEqual(
            decision, "warn",
            f"wanted deny for {command!r} in cwd {cwd}, got {decision!r}\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}",
        )
        self.assertIn(
            "origin/main", reason,
            f"deny did not name origin/main: {reason!r}",
        )
        self.assertEqual(
            self._origin(self.other_fix), self.other_fix.new_sha,
            f"{command!r} left other origin/main stale "
            f"({self._origin(self.other_fix)}); the gate inspected cwd",
        )
        self.assertTrue(
            self._fetch_head(self.other_fix).is_file(),
            f"{command!r} did not write other FETCH_HEAD; fetch ran elsewhere",
        )

    def test_minus_c_other_stale_cwd_fresh_denies_and_refreshes_other(self):
        """VERIFY-GIT-C case A: cwd fresh, other stale, `git -C other push`."""
        self._prove_fresh(self.cwd_fix, "cwd")
        self._prove_stale(self.other_fix, "other")
        command = f"git -C {self.other_fix.clone} push origin topic"
        self._assert_denied_and_other_refreshed(
            command, cwd=self.cwd_fix.clone,
        )

    def test_minus_c_other_fresh_cwd_stale_does_not_deny(self):
        """VERIFY-GIT-C case B: deny citing cwd is the wrong repository."""
        self._prove_stale(self.cwd_fix, "cwd")
        self._prove_fresh(self.other_fix, "other")
        cwd_sha = self._origin(self.cwd_fix)
        command = f"git -C {self.other_fix.clone} push origin topic"
        proc = self._fire(command, cwd=self.cwd_fix.clone)
        decision, reason = support.diagnostic(proc.stdout)
        self.assertNotEqual(
            decision, "warn",
            f"-C other (fresh) denied using cwd (stale): {decision!r} "
            f"{reason!r}\nstdout: {proc.stdout}",
        )
        self.assertEqual(
            self._origin(self.cwd_fix), cwd_sha,
            "cwd origin/main moved; a deny of cwd was applied to -C other",
        )
        self.assertEqual(
            self._origin(self.other_fix), self.other_fix.new_sha,
            "INVALID: other origin drifted during the case",
        )

    def test_git_dir_option_other_stale_denies_and_refreshes_other(self):
        self._prove_fresh(self.cwd_fix, "cwd")
        git_dir = self.other_fix.clone / ".git"
        self.assertTrue(git_dir.is_dir(), "INVALID: other .git missing")
        command = f"git --git-dir={git_dir} push origin topic"
        self._assert_denied_and_other_refreshed(
            command, cwd=self.cwd_fix.clone,
        )

    def test_git_dir_separate_arg_other_stale_denies_and_refreshes_other(self):
        self._prove_fresh(self.cwd_fix, "cwd")
        git_dir = self.other_fix.clone / ".git"
        command = f"git --git-dir {git_dir} push origin topic"
        self._assert_denied_and_other_refreshed(
            command, cwd=self.cwd_fix.clone,
        )

    def test_git_dir_assignment_in_command_other_stale_denies_and_refreshes(
            self):
        self._prove_fresh(self.cwd_fix, "cwd")
        git_dir = self.other_fix.clone / ".git"
        command = f"GIT_DIR={git_dir} git push origin topic"
        self._assert_denied_and_other_refreshed(
            command, cwd=self.cwd_fix.clone,
        )

    def test_relative_minus_c_other_stale_denies_and_refreshes_other(self):
        self._prove_fresh(self.cwd_fix, "cwd")
        rel = os.path.relpath(
            str(self.other_fix.clone), start=str(self.cwd_fix.clone),
        )
        self.assertFalse(
            Path(rel).is_absolute(),
            f"INVALID: relative path was absolute: {rel}",
        )
        command = f"git -C {rel} push origin topic"
        self._assert_denied_and_other_refreshed(
            command, cwd=self.cwd_fix.clone,
        )

    def test_minus_c_with_config_other_stale_denies_and_refreshes_other(self):
        self._prove_fresh(self.cwd_fix, "cwd")
        command = (
            f"git -C {self.other_fix.clone} "
            "-c push.default=simple push origin topic"
        )
        self._assert_denied_and_other_refreshed(
            command, cwd=self.cwd_fix.clone,
        )

    def test_minus_c_other_when_cwd_is_not_a_repo(self):
        plain = self.world.root / "plain"
        plain.mkdir()
        self.assertFalse(
            (plain / ".git").exists(),
            "INVALID: plain cwd grew a .git",
        )
        self._prove_stale(self.other_fix, "other")
        command = f"git -C {self.other_fix.clone} push origin topic"
        self._assert_denied_and_other_refreshed(command, cwd=plain)

    def test_minus_c_linked_worktree_of_other_allows_and_refreshes_other(self):
        """Worktree on wt-topic pushing topic: other origin/main is another lineage.

        Repository selection stays: the hook fetches in the OTHER
        repository and leaves cwd's refs untouched.
        """
        self._prove_fresh(self.cwd_fix, "cwd")
        self._prove_stale(self.other_fix, "other")
        cwd_main = self._origin(self.cwd_fix)
        wt = self.world.root / "other-wt"
        self.world.git(
            "worktree", "add", str(wt), "-b", "wt-topic",
            cwd=self.other_fix.clone,
        )
        gitfile = wt / ".git"
        self.assertTrue(
            gitfile.is_file(),
            "INVALID: linked worktree .git is not a file",
        )
        landed = gitfile.read_text(encoding="utf-8")
        self.assertIn(
            "gitdir:", landed,
            f"INVALID: worktree .git contents {landed!r}",
        )
        command = f"git -C {wt} push origin topic"
        proc = self._fire(command, cwd=self.cwd_fix.clone)
        decision, reason = support.diagnostic(proc.stdout)
        self.assertNotEqual(
            decision, "warn",
            f"wt-topic pushing topic denied for other's origin/main: "
            f"{decision!r} {reason!r}\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}",
        )
        self.assertEqual(
            self._origin(self.other_fix), self.other_fix.new_sha,
            f"{command!r} left other origin/main stale "
            f"({self._origin(self.other_fix)}); the gate inspected cwd",
        )
        self.assertTrue(
            self._fetch_head(self.other_fix).is_file(),
            f"{command!r} did not write other FETCH_HEAD; fetch ran elsewhere",
        )
        self.assertEqual(
            self._origin(self.cwd_fix), cwd_main,
            "cwd origin/main moved; the gate fetched in cwd",
        )
