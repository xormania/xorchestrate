"""LD-14 (2026-09-11): every Codex read-role job gets the containment
check-tests already had, so security audits can execute suites.

Measured before the fix: three of three Daybreak security audits on
2026-09-11 (records 20260911T020630Z-review-codex-bc441c,
20260911T024148Z-review-codex-2abcc1, 20260911T030332Z-review-codex-5711a3)
reported "the read-only sandbox exposes no writable temporary directory",
so no unittest suite and no temp-backed reproduction ran; their verdicts
were static reading. Codex 0.153.2's read-only sandbox ignores
writable_roots (test_launch_skeptic_write.py, measured), so a writable
TMPDIR needs the workspace-write label with the snapshot protected by
permission bits, exactly as check-tests is launched.

Contract (LD-14): a Codex job of role `read` is launched with
`--sandbox workspace-write`, `sandbox_workspace_write.writable_roots`
naming the job `out/` only, `TMPDIR` set to a real directory `out/tmp`,
and every file of the snapshot stripped of its write bits before exec.
"""

from __future__ import annotations

import json
import os
import re
import stat
import unittest
from pathlib import Path

import launch_support as ls

from ops.testkit import World


def _argv_strs(argv) -> list[str]:
    return [str(item) for item in argv]


def _sandbox_value(argv) -> str | None:
    items = _argv_strs(argv)
    if "--sandbox" not in items:
        return None
    idx = items.index("--sandbox")
    return items[idx + 1] if idx + 1 < len(items) else None


def _writable_roots(argv) -> list[str]:
    blob = " ".join(a for a in _argv_strs(argv) if "writable_roots" in a)
    roots: list[str] = []
    for match in re.finditer(r"\[[^\]]*\]", blob):
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(value, list):
            roots.extend(str(item) for item in value)
    return roots


class _CodexReadRoleJob(ls._TempLaunch):
    """One Codex read-role job per case: the fixture holds one record."""

    JOB = ""
    world: World

    def _launch(self):
        # A review's base is the merge-base with dev; the fixture has no dev,
        # so plant one at the middle commit and ref..base is not empty.
        heads = self._git("for-each-ref", "--format=%(refname:short)", "refs/heads/").stdout.split()
        if "dev" not in heads:
            self._git("branch", "dev", self.mid_sha)
        rec, witness, *_ = self.launch_ok(job=self.JOB, harness="codex", stage="review")
        self.assertEqual(rec["role"], "read", self.JOB)
        self.assertEqual(rec["harness"]["name"], "codex")
        return rec, witness, _argv_strs(witness["argv"])

    def test_uses_workspace_write_with_out_as_the_only_root(self):
        rec, _witness, argv = self._launch()
        self.assertEqual(
            _sandbox_value(argv), "workspace-write",
            f"{self.JOB}: read-only ignores writable_roots and blocks TMPDIR; "
            f"got {_sandbox_value(argv)!r} argv={argv!r}",
        )
        out_real = os.path.realpath(self.the_job_dir() / "out")
        roots = [os.path.realpath(r) for r in _writable_roots(argv)]
        self.assertEqual(roots, [out_real], f"{self.JOB}: roots={roots!r}")
        self.assertEqual(rec["harness"]["sandbox"], "workspace-write", self.JOB)

    def test_gets_a_real_tmpdir_under_out(self):
        _rec, witness, _argv = self._launch()
        tmpdir = witness["env"].get("TMPDIR")
        self.assertTrue(tmpdir, f"{self.JOB}: TMPDIR unset on the child")
        expected = self.the_job_dir() / "out" / "tmp"
        self.assertEqual(os.path.realpath(tmpdir), os.path.realpath(expected))
        self.assertTrue(expected.is_dir() and not expected.is_symlink(), self.JOB)

    def test_snapshot_files_lose_their_write_bits(self):
        rec, _witness, _argv = self._launch()
        snapshot = Path(self.snapshot_of(rec))
        writable = []
        for root, _dirs, files in os.walk(snapshot):
            for name in files:
                path = Path(root) / name
                if path.is_symlink():
                    continue
                if path.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
                    writable.append(str(path.relative_to(snapshot)))
        self.assertEqual(writable, [], f"{self.JOB}: writable snapshot files")


class SecurityAuditRunsUnderOsContainment(_CodexReadRoleJob):
    JOB = "security-audit"


class AdversarialReviewRunsUnderOsContainment(_CodexReadRoleJob):
    JOB = "adversarial-review"


def load_tests(loader, tests, pattern):
    """Only the concrete job classes run; the base holds no job."""
    suite = unittest.TestSuite()
    for cls in (SecurityAuditRunsUnderOsContainment, AdversarialReviewRunsUnderOsContainment):
        suite.addTests(loader.loadTestsFromTestCase(cls))
    return suite
