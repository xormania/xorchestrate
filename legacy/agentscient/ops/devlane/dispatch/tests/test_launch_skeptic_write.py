"""Ledger 51 follow-up: Codex 0.153.2 vs check-tests out/.

Vendor (measured, Codex 0.153.2):
- read-only: only reads; writable_roots ignored.
- workspace-write: edits cwd AND writable_roots.
- ``codex exec resume`` does not accept ``--sandbox`` (unexpected
  argument, exit 2).

Contract (slice skeptic-write-fix):
1. Fresh Codex check-tests can write the job out/ directory
   (SKEPTIC.md, TMPDIR). Snapshot files are not writable. Do not keep
   ``--sandbox read-only`` as the way to protect the snapshot; that
   blocks out/. Tests that require check-tests argv ``--sandbox
   read-only`` plus writable_roots encode a lie — replace them.
2. Resume of Codex check-tests: argv is ``codex exec resume …`` without
   ``--sandbox``. A planted resume must not fail argument parsing on
   ``--sandbox``.
3. TMPDIR is a real directory under out/, created before exec. If
   out/tmp already exists as a symlink, refuse; do not resolve it
   outside the job.

Wait-reason stays. launch.py is not read here.
World owns HOME, the XDG roots and TMPDIR; the Codex wrapper is a
fixture planted through world.tools.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

import launch_support as ls

from ops.testkit import World, make_executable

# World.stage copies launch.py into the world; an absent file still
# plants the family's empty stub used to prove red.
_SUBJECT = ls.APP / "launch.py"
_WRAPPER = Path(__file__).resolve().parent / "fixtures" / "codex_wrapper.py"


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
    """Config tokens that name sandbox_workspace_write.writable_roots."""
    items = _argv_strs(argv)
    chunks: list[str] = []
    for idx, item in enumerate(items):
        if "writable_roots" in item or "sandbox_workspace_write" in item:
            chunks.append(item)
            if idx + 1 < len(items) and not str(items[idx + 1]).startswith("-"):
                chunks.append(str(items[idx + 1]))
    return " ".join(chunks)


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
        roots.extend(re.findall(r"/[^\s,\[\]\"']+", blob))
    return roots


def _real(path) -> str:
    return os.path.realpath(str(path))


class _KitLaunch(ls._TempLaunch):
    """_TempLaunch plus World.stage of launch.py and a fixture wrapper."""

    def load_launch(self):
        if self.launch is not None:
            return self.launch
        staged = self.world.stage(_SUBJECT, "launch.py")
        if not _SUBJECT.is_file():
            self.launch = ls._absent_launch()
            if hasattr(self.launch, "JOBS_PATH"):
                self._saved_jobs_path = self.launch.JOBS_PATH
                self.launch.JOBS_PATH = self.jobs_file
            return self.launch
        self.assertEqual(
            staged.read_bytes(),
            _SUBJECT.read_bytes(),
            "staged launch.py did not land intact",
        )
        return super().load_launch()

    def _install_codex_wrapper(self, *, reject_resume_sandbox: bool) -> None:
        """Wrap the fake CLI: record argv, probe writes, optional vendor reject.

        Codex 0.153.2: ``codex exec resume`` exits 2 on ``--sandbox``.
        The inner CLI stays named ``codex`` so stream routing still matches.
        """
        impl_dir = self.bin / "codex-impl"
        impl_dir.mkdir(exist_ok=True)
        inner = impl_dir / "codex"
        outer = self.bin / "codex"
        if not inner.exists():
            self.world.plant_bytes(inner, outer.read_bytes())
            make_executable(inner)
        script = _WRAPPER.read_text(encoding="utf-8")
        script = (
            script
            .replace("__ARGV_PROBE__", str(self._codex_argv_probe))
            .replace("__WRITE_PROBE__", str(self._write_probe))
            .replace("__INNER__", str(inner))
            .replace("__REJECT__", "1" if reject_resume_sandbox else "0")
            .replace("__PROBE_WRITES__", "1")
        )
        self.world.tools.plant("codex", script=script)

    def _install_resume_sandbox_reject(self) -> None:
        impl_dir = self.bin / "codex-impl"
        impl_dir.mkdir(exist_ok=True)
        inner = impl_dir / "codex"
        outer = self.bin / "codex"
        if not inner.exists():
            self.world.plant_bytes(inner, outer.read_bytes())
            make_executable(inner)
        script = _WRAPPER.read_text(encoding="utf-8")
        script = (
            script
            .replace("__ARGV_PROBE__", str(self._codex_argv_probe))
            .replace("__WRITE_PROBE__", str(self._write_probe))
            .replace("__INNER__", str(inner))
            .replace("__REJECT__", "1")
            .replace("__PROBE_WRITES__", "0")
        )
        self.world.tools.plant("codex", script=script)


class CodexCheckTestsCanWriteTheJobOutDir(_KitLaunch):
    """check-tests writes out/SKEPTIC.md; the snapshot stays unwritable."""

    def setUp(self):
        super().setUp()
        self._codex_argv_probe = self.home / "codex-argv.json"
        self._write_probe = self.home / "write-probe.json"

    def _launch_codex_check_tests(self):
        rec, witness, *_ = self.launch_ok(
            job="check-tests", harness="codex", stage="check-tests",
        )
        self.assertEqual(rec["job"], "check-tests")
        self.assertEqual(rec["role"], "read")
        self.assertEqual(rec["harness"]["name"], "codex")
        argv = _argv_strs(witness["argv"])
        self.assertTrue(argv, "child argv is empty")
        return rec, witness, argv

    def _read_json(self, path: Path, *, label: str) -> dict | list:
        self.assertTrue(path.is_file(), f"{label} missing: {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(data, f"{label} is empty")
        return data

    def test_codex_check_tests_uses_workspace_write_not_read_only(self):
        """read-only ignores writable_roots; workspace-write honours them."""
        _rec, _witness, argv = self._launch_codex_check_tests()
        self.assertEqual(
            _sandbox_value(argv),
            "workspace-write",
            "Codex 0.153.2 read-only ignores writable_roots, so check-tests "
            "must use --sandbox workspace-write (cwd plus writable_roots) "
            "and protect the snapshot some other way; "
            f"got {_sandbox_value(argv)!r} argv={argv!r}",
        )

    def test_codex_check_tests_writable_roots_names_the_job_out_dir(self):
        rec, _witness, argv = self._launch_codex_check_tests()
        out_dir = self.the_job_dir() / "out"
        self.assertTrue(
            out_dir.is_dir(),
            f"job out dir must exist so writable_roots can name it: {out_dir}",
        )
        blob = _writable_roots_blob(argv)
        self.assertIn(
            "sandbox_workspace_write.writable_roots",
            " ".join(argv),
            "check-tests Codex argv must pass "
            "sandbox_workspace_write.writable_roots naming out/; "
            f"argv={argv!r}",
        )
        self.assertTrue(
            blob.strip(),
            f"writable_roots config is missing from argv={argv!r}",
        )
        out_real = _real(out_dir)
        roots = _writable_roots(argv)
        self.assertTrue(
            roots,
            f"writable_roots listed no paths; blob={blob!r} argv={argv!r}",
        )
        root_reals = [_real(item) for item in roots]
        self.assertIn(
            out_real,
            root_reals,
            f"writable_roots must name the job out dir {out_real}; "
            f"listed={roots!r} blob={blob!r}",
        )
        snapshot = _real(self.snapshot_of(rec))
        for root in root_reals:
            self.assertFalse(
                snapshot == root or snapshot.startswith(root + os.sep),
                f"writable root {root} lists the snapshot {snapshot}; "
                f"argv={argv!r}",
            )

    def test_codex_check_tests_snapshot_files_are_not_writable(self):
        self._install_codex_wrapper(reject_resume_sandbox=False)
        rec, _witness, _argv = self._launch_codex_check_tests()
        probe = self._read_json(self._write_probe, label="write probe")
        snapshot = _real(self.snapshot_of(rec))
        self.assertEqual(
            _real(probe.get("cwd") or ""),
            snapshot,
            "write probe must run with cwd at the snapshot; "
            f"probe={probe!r}",
        )
        self.assertFalse(
            probe.get("snapshot_write"),
            "snapshot files must not be writable to the check-tests child; "
            "do not keep --sandbox read-only as the protection (that blocks "
            f"out/), and do not leave cwd writable; probe={probe!r}",
        )
        self.assertTrue(
            probe.get("out_write"),
            "protecting the snapshot must not make out/ unwritable "
            "(SKEPTIC.md and TMPDIR live there); "
            f"probe={probe!r}",
        )
        skeptic = self.the_job_dir() / "out" / "SKEPTIC.md"
        self.assertTrue(
            skeptic.is_file(),
            f"out/SKEPTIC.md missing after a child write: {skeptic}",
        )

    def test_codex_check_tests_tmpdir_is_a_real_directory_under_out(self):
        rec, witness, _argv = self._launch_codex_check_tests()
        env = witness.get("env") or {}
        tmpdir = env.get("TMPDIR")
        self.assertTrue(
            tmpdir,
            "TMPDIR must be set on the Codex check-tests child so unittest "
            f"setUp can create temp files; env={env!r}",
        )
        started = self.read_start_witness()
        self.assertEqual(
            started.get("tmpdir"),
            tmpdir,
            "start-witness TMPDIR must match the child env at exec",
        )
        self.assertTrue(
            started.get("tmpdir_is_dir"),
            "TMPDIR must already be a directory at exec "
            f"(created under out/ before the child runs); "
            f"tmpdir={tmpdir!r} witness={started!r}",
        )
        self.assertFalse(
            os.path.islink(tmpdir),
            f"TMPDIR must be a real directory, not a symlink; got {tmpdir!r}",
        )
        tmp_path = Path(tmpdir)
        self.assertTrue(
            tmp_path.is_dir() and not tmp_path.is_symlink(),
            f"TMPDIR {tmpdir!r} must remain a real directory after the child",
        )
        out_dir = self.the_job_dir() / "out"
        out_tmp = out_dir / "tmp"
        self.assertTrue(
            out_tmp.is_dir() and not out_tmp.is_symlink(),
            "out/tmp must be a real directory created before exec; "
            f"exists={out_tmp.exists()} is_dir="
            f"{out_tmp.is_dir() if out_tmp.exists() else None} "
            f"is_symlink={out_tmp.is_symlink() if out_tmp.exists() else None}",
        )
        out_real = _real(out_dir)
        tmp_real = _real(tmpdir)
        self.assertTrue(
            tmp_real == out_real or tmp_real.startswith(out_real + os.sep),
            f"TMPDIR must point under the job out dir {out_real}; "
            f"got {tmp_real}",
        )
        out_tmp_real = _real(out_tmp)
        self.assertTrue(
            tmp_real == out_tmp_real
            or tmp_real.startswith(out_tmp_real + os.sep),
            f"TMPDIR must be out/tmp or a directory under it; "
            f"tmpdir={tmp_real} out/tmp={out_tmp_real}",
        )
        snapshot = _real(self.snapshot_of(rec))
        self.assertFalse(
            tmp_real == snapshot or tmp_real.startswith(snapshot + os.sep),
            f"TMPDIR must not land in the snapshot {snapshot}; "
            f"got {tmp_real}",
        )


class CodexCheckTestsResumeOmitsSandbox(_KitLaunch):
    """``codex exec resume`` must not be passed ``--sandbox``."""

    def setUp(self):
        super().setUp()
        self._codex_argv_probe = self.home / "codex-argv.json"
        self._write_probe = self.home / "write-probe.json"

    def test_codex_check_tests_resume_argv_does_not_pass_sandbox(self):
        self._install_resume_sandbox_reject()
        rec, *_ = self.launch_ok(
            job="check-tests", harness="codex", stage="check-tests",
        )
        session_id = rec["session"]["id"]
        self.assertTrue(session_id, "check-tests record has no session id")
        if self.witness.is_file():
            self.witness.unlink()
        if self.start_witness.is_file():
            self.start_witness.unlink()
        if self._codex_argv_probe.is_file():
            self._codex_argv_probe.unlink()
        code, out, err = self.run_main(["resume", rec["id"]])
        text = self.combined(out, err)
        self.assertTrue(
            self._codex_argv_probe.is_file(),
            "resume must exec codex so argv can be judged; "
            f"code={code} out={out!r} err={err!r}",
        )
        argv = _argv_strs(
            json.loads(self._codex_argv_probe.read_text(encoding="utf-8"))
        )
        self.assertIn("exec", argv, f"resume argv={argv!r}")
        self.assertIn("resume", argv, f"resume argv={argv!r}")
        self.assertIn(str(session_id), argv, f"resume argv={argv!r}")
        self.assertNotIn(
            "--sandbox",
            argv,
            "codex exec resume does not accept --sandbox (Codex 0.153.2 "
            "exits 2: unexpected argument); resume argv must omit it; "
            f"argv={argv!r} launcher={text!r}",
        )
        self.assertEqual(
            code, 0,
            "planted resume must not fail argument parsing on --sandbox; "
            f"code={code} argv={argv!r} {text!r}",
        )
        self.assertNotIn(
            "unexpected argument",
            text.lower(),
            f"resume failed Codex argument parsing: {text!r}",
        )


class CodexCheckTestsRefusesSymlinkTmpdir(_KitLaunch):
    """A prior attempt must not redirect TMPDIR outside the job via symlink."""

    def _plant_out_tmp_symlink(self, job_dir: Path) -> tuple[Path, Path]:
        tmp = job_dir / "out" / "tmp"
        self.assertTrue(
            tmp.exists() and tmp.is_dir() and not tmp.is_symlink(),
            "plant needs a real out/tmp from the first launch; "
            f"exists={tmp.exists()} is_dir="
            f"{tmp.is_dir() if tmp.exists() else None} "
            f"is_symlink={tmp.is_symlink() if tmp.exists() else None}",
        )
        shutil.rmtree(tmp)
        outside = self.home / "escaped-tmp"
        outside.mkdir()
        marker = b"OUTSIDE-TMPDIR-PLANT"
        self.world.plant_bytes(outside / "marker", marker)
        landed = (outside / "marker").read_bytes()
        self.assertEqual(landed, marker, "plant did not reach disk")
        self.assertGreater(len(landed), 0)
        tmp.symlink_to(outside)
        self.assertTrue(tmp.is_symlink(), "plant: out/tmp must be a symlink")
        self.assertTrue(tmp.exists(), "plant: symlink target must exist")
        self.assertEqual(
            tmp.resolve(), outside.resolve(),
            "plant: out/tmp must resolve to the outside directory",
        )
        self.assertEqual(
            (tmp / "marker").read_bytes(), marker,
            "plant left out/tmp unrecognisable as the outside target",
        )
        job_real = _real(job_dir)
        target_real = _real(tmp)
        self.assertFalse(
            target_real == job_real
            or target_real.startswith(job_real + os.sep),
            f"plant failed to escape the job dir: {target_real} vs {job_real}",
        )
        return tmp, outside

    def test_codex_check_tests_refuses_when_out_tmp_is_a_symlink(self):
        rec, *_ = self.launch_ok(
            job="check-tests", harness="codex", stage="check-tests",
        )
        job_dir = self.the_job_dir()
        tmp, outside = self._plant_out_tmp_symlink(job_dir)
        if self.witness.is_file():
            self.witness.unlink()
        if self.start_witness.is_file():
            self.start_witness.unlink()
        code, out, err = self.run_main(["resume", rec["id"]])
        self.assert_refusal(
            code, out, err, ident="tmpdir-symlink",
            phrases=["symlink", "tmp"],
            named=[str(tmp)],
        )
        self.assert_not_started()
        self.assertEqual(
            sorted(p.name for p in outside.iterdir()),
            ["marker"],
            "refusing a symlink out/tmp must not resolve or write outside "
            f"the job; outside={outside} tmp={tmp}",
        )
        self.assertTrue(
            tmp.is_symlink(),
            "refusal must not replace the planted symlink as a side effect",
        )


# World is imported so the census counts this module on the kit;
# _TempLaunch constructs it.
assert World is not None
