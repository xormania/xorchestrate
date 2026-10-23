"""LD-14 regression: Codex read-role containment, second producer.

Contract: ops/devlane/dispatch/CONTRACT.md paragraph "Measured Codex
read-role containment". Every Codex job of role read (plan,
check-tests, security-audit, adversarial-review, adjudicate, sweep)
keeps its role, but the Codex process uses the workspace-write sandbox
label so it can write scratch under the job out/, the only writable
root; TMPDIR is a real, non-symlinked out/tmp. Before the attempt the
launcher strips Unix write bits from every snapshot file. A Codex
write-role job (implement) is unchanged: workspace-write with that
same single root; snapshot mode stripping is not required of it.

These tests do not read launch.py. They launch through
launch_support._TempLaunch. The migration's red proof bound that seam
to a pre-LD-14 stand-in which emitted `--sandbox read-only`, named no
writable_roots, left TMPDIR unset, and left snapshot write bits intact.
Each assertion was red against that stand-in and is green against the
real launcher.
"""

from __future__ import annotations

import json
import os
import re
import stat
import threading
import time
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
    if idx + 1 >= len(items):
        return None
    return items[idx + 1]


def _writable_roots_blob(argv) -> str:
    items = _argv_strs(argv)
    chunks: list[str] = []
    skip: set[int] = set()
    for idx, item in enumerate(items):
        if idx in skip:
            continue
        if item in ("-c", "--config") and idx + 1 < len(items):
            nxt = items[idx + 1]
            if "sandbox_workspace_write.writable_roots" in nxt:
                chunks.append(nxt)
                skip.add(idx + 1)
                continue
        if "sandbox_workspace_write.writable_roots" in item:
            chunks.append(item)
            if idx + 1 < len(items) and not items[idx + 1].startswith("-"):
                chunks.append(items[idx + 1])
                skip.add(idx + 1)
    return " ".join(chunks)


def _writable_root_key_count(argv) -> int:
    return sum(
        1
        for item in _argv_strs(argv)
        if "sandbox_workspace_write.writable_roots" in item
    )


def _writable_roots(argv) -> list[str]:
    blob = _writable_roots_blob(argv)
    roots: list[str] = []
    for match in re.finditer(r"\[[^\]]*\]", blob):
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(value, list):
            roots.extend(str(item) for item in value)
    if not roots:
        for match in re.finditer(
            r"sandbox_workspace_write\.writable_roots\s*=\s*(\S+)",
            blob,
        ):
            token = match.group(1).strip().strip(",")
            if token.startswith("["):
                continue
            roots.append(token.strip("\"'"))
    if not roots:
        roots.extend(re.findall(r"/[^\s,\[\]\"']+", blob))
    unique: list[str] = []
    seen: set[str] = set()
    for root in roots:
        key = os.path.realpath(root)
        if key in seen:
            continue
        seen.add(key)
        unique.append(root)
    return unique


def _real(path) -> str:
    return os.path.realpath(str(path))


class _KitLaunch(ls._TempLaunch):
    """The shared launch fixture with kit-owned snapshot inspection."""

    world: World

    def _plant_dev_at_mid(self) -> None:
        self._git("branch", "dev", self.mid_sha)
        self.assertEqual(
            self._git("rev-parse", "dev").stdout.strip(),
            self.mid_sha,
        )
        self.assertNotEqual(self.mid_sha, self.ref)

    def _writable_regular_files(self, root: Path) -> list[str]:
        write_bits = stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH
        writable = []
        for path in root.rglob("*"):
            if ".git" in path.parts:
                continue
            try:
                mode = path.lstat().st_mode
            except OSError:
                continue
            if stat.S_ISREG(mode) and mode & write_bits:
                writable.append(str(path.relative_to(root)))
        return writable


class _ReadRoleMixin:
    JOB = ""
    STAGE = ""

    def setUp(self):
        super().setUp()
        self.assertTrue(self.JOB, "read-role subclass must name JOB")
        self.assertTrue(self.STAGE, "read-role subclass must name STAGE")
        catalogue = json.loads(ls.JOBS_PATH.read_text(encoding="utf-8"))
        spec = catalogue[self.JOB]
        self.assertEqual(
            spec.get("role"),
            "read",
            f"{self.JOB} must stay role read in jobs.json",
        )
        self._plant_dev_at_mid()

    def _job_argv(self):
        extra = []
        scope = "ld14-codex-read-containment"
        if self.JOB == "adjudicate":
            report = self.home / "in" / "report.md"
            self.plant_new_file(report, "# report\n", must_contain="report")
            extra = ["--input", str(report)]
            scope = None
        return self.argv_for(
            job=self.JOB,
            harness="codex",
            stage=self.STAGE,
            scope=scope,
            extra=extra,
        )

    def _launch_one(self):
        os.environ["TASK_LAUNCH_JOB"] = self.JOB
        rec, witness, *_ = self.launch_ok(self._job_argv())
        self.assertEqual(rec["job"], self.JOB)
        self.assertEqual(rec["role"], "read")
        self.assertEqual(rec["harness"]["name"], "codex")
        argv = _argv_strs(witness["argv"])
        self.assertTrue(argv, f"{self.JOB}: child argv is empty")
        return rec, witness, argv

    def test_child_argv_sandbox_is_workspace_write(self):
        _rec, _witness, argv = self._launch_one()
        self.assertEqual(
            argv.count("--sandbox"),
            1,
            f"{self.JOB}: expected one --sandbox, got {argv!r}",
        )
        self.assertEqual(
            _sandbox_value(argv),
            "workspace-write",
            f"{self.JOB}: Codex read-role argv must carry "
            f"--sandbox workspace-write; under read-only, writable_roots "
            f"are ignored and TMPDIR is blocked; "
            f"got {_sandbox_value(argv)!r} argv={argv!r}",
        )

    def test_child_argv_has_exactly_one_writable_root_the_job_out(self):
        rec, _witness, argv = self._launch_one()
        out_dir = self.the_job_dir() / "out"
        self.assertTrue(
            out_dir.is_dir(),
            f"{self.JOB}: job out/ must exist so writable_roots can name it: "
            f"{out_dir}",
        )
        self.assertEqual(
            _writable_root_key_count(argv),
            1,
            f"{self.JOB}: argv must carry exactly one "
            f"sandbox_workspace_write.writable_roots; argv={argv!r}",
        )
        roots = _writable_roots(argv)
        self.assertEqual(
            len(roots),
            1,
            f"{self.JOB}: writable_roots must name exactly one path, "
            f"the job out/; listed={roots!r} argv={argv!r}",
        )
        out_real = _real(out_dir)
        self.assertEqual(
            _real(roots[0]),
            out_real,
            f"{self.JOB}: the single writable root must be the job out/ "
            f"{out_real}; listed={roots!r}",
        )
        snapshot = _real(self.snapshot_of(rec))
        self.assertNotEqual(
            _real(roots[0]),
            snapshot,
            f"{self.JOB}: writable_roots must not name the snapshot; "
            f"argv={argv!r}",
        )

    def test_record_harness_sandbox_is_workspace_write(self):
        rec, _witness, _argv = self._launch_one()
        self.assertEqual(
            rec["harness"]["sandbox"],
            "workspace-write",
            f"{self.JOB}: record harness.sandbox must say workspace-write; "
            f"got {rec['harness'].get('sandbox')!r}",
        )

    def test_child_tmpdir_is_real_nonsymlinked_job_out_tmp(self):
        rec, witness, _argv = self._launch_one()
        env = witness.get("env") or {}
        tmpdir = env.get("TMPDIR")
        self.assertTrue(
            tmpdir,
            f"{self.JOB}: child TMPDIR must be set; env={env!r}",
        )
        started = self.read_start_witness()
        self.assertEqual(
            started.get("tmpdir"),
            tmpdir,
            f"{self.JOB}: start-witness TMPDIR must match the child env",
        )
        tmp_path = Path(tmpdir)
        self.assertFalse(
            tmp_path.is_symlink() or os.path.islink(tmpdir),
            f"{self.JOB}: TMPDIR must not be a symlink; got {tmpdir!r}",
        )
        self.assertTrue(
            tmp_path.is_dir(),
            f"{self.JOB}: TMPDIR must be a real directory; got {tmpdir!r}",
        )
        out_tmp = self.the_job_dir() / "out" / "tmp"
        self.assertTrue(
            out_tmp.is_dir() and not out_tmp.is_symlink(),
            f"{self.JOB}: out/tmp must be a real directory under the job; "
            f"exists={out_tmp.exists()} is_symlink="
            f"{out_tmp.is_symlink() if out_tmp.exists() else None}",
        )
        self.assertEqual(
            _real(tmpdir),
            _real(out_tmp),
            f"{self.JOB}: TMPDIR must be the job out/tmp; "
            f"tmpdir={tmpdir!r} out/tmp={out_tmp}",
        )
        snapshot = _real(self.snapshot_of(rec))
        self.assertFalse(
            _real(tmpdir) == snapshot
            or _real(tmpdir).startswith(snapshot + os.sep),
            f"{self.JOB}: TMPDIR must not land in the snapshot {snapshot}; "
            f"got {tmpdir!r}",
        )

    def test_snapshot_regular_files_have_no_write_bit_after_launch(self):
        os.environ["TASK_LAUNCH_SLEEP"] = "1"
        outcome = {}

        def launch():
            try:
                outcome["value"] = self._launch_one()
            except BaseException as exc:
                outcome["error"] = exc

        worker = threading.Thread(target=launch)
        worker.start()
        deadline = time.monotonic() + 5
        while not self.start_witness.is_file() and worker.is_alive():
            self.assertLess(time.monotonic(), deadline, "codex start witness timed out")
            time.sleep(0.01)
        try:
            self.assertTrue(
                self.start_witness.is_file(),
                "codex must start before snapshot modes are inspected",
            )
            started = self.read_start_witness()
            snapshot_path = Path(started["cwd"])
            writable = self._writable_regular_files(snapshot_path)
        finally:
            worker.join(timeout=5)
        self.assertFalse(worker.is_alive(), "codex launch thread did not finish")
        if "error" in outcome:
            raise outcome["error"]
        rec, witness, _argv = outcome["value"]
        snapshot = _real(self.snapshot_of(rec))
        self.assertEqual(
            _real(witness.get("cwd") or ""),
            snapshot,
            f"{self.JOB}: mode probe must run with cwd at the snapshot; "
            f"witness={witness!r}",
        )
        self.assertEqual(
            writable,
            [],
            f"{self.JOB}: every regular snapshot file must have no write "
            f"bit at exec; still writable: {writable!r}",
        )


class PlanCodexReadContainment(_ReadRoleMixin, _KitLaunch):
    JOB = "plan"
    STAGE = "plan"


class CheckTestsCodexReadContainment(_ReadRoleMixin, _KitLaunch):
    JOB = "check-tests"
    STAGE = "check-tests"


class SecurityAuditCodexReadContainment(_ReadRoleMixin, _KitLaunch):
    JOB = "security-audit"
    STAGE = "review"


class AdversarialReviewCodexReadContainment(_ReadRoleMixin, _KitLaunch):
    JOB = "adversarial-review"
    STAGE = "review"


class AdjudicateCodexReadContainment(_ReadRoleMixin, _KitLaunch):
    JOB = "adjudicate"
    STAGE = "adjudicate"


class SweepCodexReadContainment(_ReadRoleMixin, _KitLaunch):
    JOB = "sweep"
    STAGE = "plan"


class CodexImplementWriteRoleUnchanged(_KitLaunch):
    """Implement stays workspace-write with the same single out/ root."""

    JOB = "implement"
    STAGE = "code"

    def setUp(self):
        super().setUp()
        catalogue = json.loads(ls.JOBS_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            catalogue[self.JOB].get("role"),
            "write",
            "implement must stay role write in jobs.json",
        )
        self._plant_dev_at_mid()

    def _launch_one(self):
        os.environ["TASK_LAUNCH_JOB"] = self.JOB
        os.environ["TASK_LAUNCH_VERDICT"] = "null"
        rec, witness, *_ = self.launch_ok(self.argv_for(
            job=self.JOB,
            harness="codex",
            stage=self.STAGE,
            scope="python3 -m unittest",
        ))
        self.assertEqual(rec["job"], self.JOB)
        self.assertEqual(rec["role"], "write")
        self.assertEqual(rec["harness"]["name"], "codex")
        argv = _argv_strs(witness["argv"])
        self.assertTrue(argv, "implement: child argv is empty")
        return rec, witness, argv

    def test_child_argv_sandbox_is_workspace_write(self):
        _rec, _witness, argv = self._launch_one()
        self.assertEqual(
            _sandbox_value(argv),
            "workspace-write",
            "implement must keep --sandbox workspace-write; "
            f"got {_sandbox_value(argv)!r} argv={argv!r}",
        )

    def test_child_argv_has_exactly_one_writable_root_the_job_out(self):
        rec, _witness, argv = self._launch_one()
        out_dir = self.the_job_dir() / "out"
        self.assertTrue(out_dir.is_dir(), f"implement out/ missing: {out_dir}")
        self.assertEqual(
            _writable_root_key_count(argv),
            1,
            f"implement argv must carry exactly one "
            f"sandbox_workspace_write.writable_roots; argv={argv!r}",
        )
        roots = _writable_roots(argv)
        self.assertEqual(
            len(roots),
            1,
            f"implement writable_roots must name exactly one path; "
            f"listed={roots!r} argv={argv!r}",
        )
        self.assertEqual(
            _real(roots[0]),
            _real(out_dir),
            f"implement writable root must be the job out/; listed={roots!r}",
        )
        self.assertNotEqual(
            _real(roots[0]),
            _real(self.snapshot_of(rec)),
            "implement writable_roots must not name the snapshot",
        )

    def test_record_harness_sandbox_is_workspace_write(self):
        rec, _witness, _argv = self._launch_one()
        self.assertEqual(
            rec["harness"]["sandbox"],
            "workspace-write",
            "implement record harness.sandbox must stay workspace-write; "
            f"got {rec['harness'].get('sandbox')!r}",
        )
