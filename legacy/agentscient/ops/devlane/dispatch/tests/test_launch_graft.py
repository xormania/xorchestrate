"""Dispatcher custody for Graft — withheld test roles, ready metadata,
disabled byte-stability, resume, brief --check, child query.

Written from ops/devlane/harness/graft/CONTRACT.md dispatcher integration.
Canonical launch through the existing synthetic harness CLIs.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
from pathlib import Path

import launch_support as ls

from ops.testkit import World  # noqa: F401 - _TempLaunch owns the world

FAKE_PY = ls.HARNESS_DIR / "tests" / "fakes" / "graft_bridge.py"
GRAFT_PY = ls.HARNESS_DIR / "graft.py"
PIN_PATH = ls.HARNESS_DIR / "graft" / "pin.json"
WITHHELD_REASON = "implementation context withheld for test independence"
READY_JOBS = (
    ("plan", "plan", "read", "context/graft-cache"),
    ("implement", "code", "write", "out/graft-cache"),
    ("sweep", "review", "read", "context/graft-cache"),
)
WITHHELD_JOBS = ("author-tests", "check-tests")
HARNESSES = ("grok", "claude", "codex")


def load_fake():
    spec = importlib.util.spec_from_file_location("graft_bridge_fake", FAKE_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _GraftLaunch(ls._TempLaunch):
    def setUp(self):
        self._graft_env = {
            k: os.environ.get(k)
            for k in ("GRAFT_INSTALL", "LANE_CONF")
        }
        super().setUp()
        self._write("ops/lane.md", "ops source\n")
        self.ref = self._commit("ops-source")
        self.fake = load_fake()
        self.pin = json.loads(PIN_PATH.read_text(encoding="utf-8"))
        self.install = self.fake.plant_install(
            self.home / "graft-install", pin=self.pin,
        )
        os.environ["GRAFT_INSTALL"] = str(self.install)

    def tearDown(self):
        try:
            super().tearDown()
        finally:
            for key, value in self._graft_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def graft_meta(self, rec):
        brief = rec.get("brief") if isinstance(rec.get("brief"), dict) else {}
        return brief.get("graft")

    def assert_ready_meta(self, rec):
        meta = self.graft_meta(rec)
        self.assertIsInstance(meta, dict)
        self.assertEqual(meta.get("state"), "ready")
        return meta

    def disable(self):
        os.environ["GRAFT_INSTALL"] = ""

    def wipe_jobs(self):
        for path in list(self.job_dirs()):
            shutil.rmtree(path)
        records = self.repo / ".dev" / "records" / "dispatches"
        if records.is_dir():
            for path in records.glob("*.json"):
                path.unlink()
        self.start_witness.unlink(missing_ok=True)
        self.witness.unlink(missing_ok=True)

    def install_querying_cli(self, name="grok"):
        dest = self.bin / name
        body = dest.read_text(encoding="utf-8")
        needle = "raise SystemExit(exit_code)"
        self.assertIn(needle, body)
        snippet = f"""
import contextlib as graft_contextlib
import io as graft_io
import runpy as graft_runpy
graft_ctx = Path(os.getcwd()).parent / "context" / "graft.json"
graft_py = Path({str(GRAFT_PY)!r})
graft_wit = Path(os.getcwd()).parent / "graft-query.witness"
if graft_ctx.is_file() and graft_py.is_file():
    graft_out, graft_err = graft_io.StringIO(), graft_io.StringIO()
    graft_argv = sys.argv
    sys.argv = [str(graft_py), "query", "--context", str(graft_ctx),
                "graft_repo_map", "{{}}"]
    try:
        with graft_contextlib.redirect_stdout(graft_out), graft_contextlib.redirect_stderr(graft_err):
            try:
                graft_runpy.run_path(str(graft_py), run_name="__main__")
            except SystemExit as exc:
                graft_code = 0 if exc.code is None else int(exc.code)
            else:
                graft_code = 0
    finally:
        sys.argv = graft_argv
    graft_wit.write_text(json.dumps({{
        "code": graft_code,
        "out": graft_out.getvalue(),
        "err": graft_err.getvalue(),
    }}) + "\\n", encoding="utf-8")
"""
        return self.world.tools.plant(
            name, script=body.replace(needle, snippet + "\n" + needle, 1),
        )


class DisabledJobsAreByteStable(_GraftLaunch):
    def test_disabled_launch_does_not_add_graft_fields_or_caches(self):
        self.disable()
        rec, witness, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        self.assertIsNone(self.graft_meta(rec))
        job = self.the_job_dir()
        self.assertFalse((job / "context" / "graft.json").exists())
        self.assertFalse((job / "context" / "graft-cache").exists())
        self.assertFalse((job / "out" / "graft-cache").exists())
        prompt = (job / "prompt.txt").read_text(encoding="utf-8")
        self.assertNotIn("graft.py query", prompt)
        self.assertNotIn("../context/graft.json", prompt)
        env = witness.get("env") or {}
        self.assertNotIn("GRAFT_DIR", env)
        home = Path(rec["harness"]["isolation"]["home"])
        self.assertEqual(sorted(p.name for p in home.iterdir()), ["auth.json", "hooks"])

    def test_legacy_record_without_brief_graft_still_resumes_when_now_enabled(self):
        self.disable()
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        self.assertIsNone(self.graft_meta(rec))
        os.environ["GRAFT_INSTALL"] = str(self.install)
        self.start_witness.unlink(missing_ok=True)
        code, out, err = self.run_main(["resume", rec["id"]])
        self.assertEqual(code, 0, err + out)
        rec2 = self.read_record()
        self.assertIsNone(self.graft_meta(rec2))
        self.assertFalse((self.the_job_dir() / "context" / "graft.json").exists())
        self.assertTrue(self.start_witness.is_file())


class TestRolesAreWithheld(_GraftLaunch):
    def test_author_tests_metadata_is_withheld_and_exposes_no_paths(self):
        rec, witness, *_ = self.launch_ok(
            job="author-tests", harness="grok", stage="tests",
        )
        meta = self.graft_meta(rec)
        self.assertIsInstance(meta, dict)
        self.assertEqual(meta.get("state"), "withheld")
        self.assertEqual(meta.get("reason"), WITHHELD_REASON)
        self.assertEqual(set(meta), {"state", "reason"})
        job = self.the_job_dir()
        self.assertFalse((job / "context" / "graft.json").exists())
        self.assertFalse((job / "context" / "graft-cache").exists())
        prompt = (job / "prompt.txt").read_text(encoding="utf-8")
        self.assertIn(WITHHELD_REASON, prompt)
        self.assertNotIn(str(self.install), prompt)
        self.assertNotIn("graft-cache", prompt)
        self.assertNotIn("../context/graft.json", prompt)
        self.assertTrue(witness["argv"])

    def test_check_tests_are_withheld_the_same_way(self):
        rec, *_ = self.launch_ok(
            job="check-tests", harness="grok", stage="check-tests",
        )
        meta = self.graft_meta(rec)
        self.assertIsInstance(meta, dict)
        self.assertEqual(meta.get("state"), "withheld")
        self.assertFalse((self.the_job_dir() / "context" / "graft.json").exists())


class AllowedJobsAreReadyAndDiscoverable(_GraftLaunch):
    def test_read_and_write_jobs_store_ready_metadata_in_the_contracted_caches(self):
        self.assertGreaterEqual(len(READY_JOBS), 3)
        for job, stage, role, cache_rel in READY_JOBS:
            with self.subTest(job=job):
                self.wipe_jobs()
                rec, *_ = self.launch_ok(job=job, harness="grok", stage=stage)
                meta = self.graft_meta(rec)
                self.assertIsInstance(meta, dict, job)
                self.assertEqual(meta.get("state"), "ready", job)
                self.assertEqual(meta.get("descriptor"), "context/graft.json")
                job_dir = self.the_job_dir()
                desc_path = job_dir / "context" / "graft.json"
                self.assertTrue(desc_path.is_file(), job)
                self.assertEqual(meta.get("sha256"), ls.sha256_file(desc_path))
                desc = json.loads(desc_path.read_text(encoding="utf-8"))
                self.assertEqual(desc.get("role"), role, job)
                self.assertEqual(
                    Path(desc["cache"]).resolve(),
                    (job_dir / cache_rel).resolve(),
                    job,
                )
                prompt = (job_dir / "prompt.txt").read_text(encoding="utf-8")
                self.assertIn("../context/graft.json", prompt)
                self.assertIn("python3 ops/devlane/harness/graft.py query", prompt)
                self.assertIn("navigation", prompt.lower())
                digest = hashlib.sha256(
                    (job_dir / "prompt.txt").read_bytes()
                ).hexdigest()
                self.assertEqual(rec["brief"]["sha256"], digest)

    def test_every_synthetic_harness_prompt_names_the_query_invocation(self):
        self.assertGreaterEqual(len(HARNESSES), 3)
        for harness in HARNESSES:
            with self.subTest(harness=harness):
                self.wipe_jobs()
                rec, *_ = self.launch_ok(
                    job="plan", harness=harness, stage="plan",
                )
                prompt = (self.the_job_dir() / "prompt.txt").read_text(encoding="utf-8")
                self.assertIn("python3 ops/devlane/harness/graft.py query", prompt)
                self.assert_ready_meta(rec)


class ChildActuallyQueries(_GraftLaunch):
    def test_ready_worker_invokes_query_through_the_public_cli(self):
        self.install_querying_cli("grok")
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        self.assert_ready_meta(rec)
        witness = self.the_job_dir() / "graft-query.witness"
        self.assertTrue(witness.is_file(), "child must invoke graft query")
        payload = json.loads(witness.read_text(encoding="utf-8"))
        self.assertEqual(payload["code"], 0, payload)
        self.assertTrue((payload.get("out") or "").strip())

    def test_withheld_worker_does_not_invoke_query(self):
        self.install_querying_cli("grok")
        self.launch_ok(job="author-tests", harness="grok", stage="tests")
        self.assertFalse((self.the_job_dir() / "graft-query.witness").exists())


class ResumeAndBriefCheck(_GraftLaunch):
    def test_resume_refuses_a_missing_ready_descriptor(self):
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        self.assert_ready_meta(rec)
        desc = self.the_job_dir() / "context" / "graft.json"
        self.assertTrue(desc.is_file())
        desc.unlink()
        self.assertFalse(desc.exists())
        self.start_witness.unlink(missing_ok=True)
        code, out, err = self.run_main(["resume", rec["id"]])
        self.assertNotEqual(code, 0)
        text = (out or "") + (err or "")
        self.assertIn("graft-context", text.lower())
        self.assert_not_started()

    def test_resume_refuses_a_foreign_descriptor_root(self):
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        self.assert_ready_meta(rec)
        desc_path = self.the_job_dir() / "context" / "graft.json"
        self.assertTrue(desc_path.is_file())
        desc = json.loads(desc_path.read_text(encoding="utf-8"))
        desc["root"] = str(self.home / "other-snapshot")
        self.world.plant_text(desc_path, json.dumps(desc) + "\n")
        self.start_witness.unlink(missing_ok=True)
        code, out, err = self.run_main(["resume", rec["id"]])
        self.assertNotEqual(code, 0)
        self.assertIn("graft-context", ((out or "") + (err or "")).lower())
        self.assert_not_started()

    def test_brief_check_refuses_a_tampered_descriptor_digest(self):
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        meta = self.assert_ready_meta(rec)
        bogus = "0" * 64
        self.assertNotEqual(meta.get("sha256"), bogus)
        for path in (self.the_record_path(), self.the_job_dir() / "record.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            data["brief"]["graft"]["sha256"] = bogus
            self.world.plant_text(path, json.dumps(data, indent=2) + "\n")
        code, out, err = self.run_main(["brief", "--check", rec["id"]])
        self.assertNotEqual(code, 0, err + out)
        text = ((out or "") + (err or "")).lower()
        self.assertTrue("graft" in text or "digest" in text or "sha256" in text)

    def test_intact_ready_context_resumes(self):
        rec, *_ = self.launch_ok(job="plan", harness="grok", stage="plan")
        first = self.assert_ready_meta(rec)
        self.start_witness.unlink(missing_ok=True)
        code, out, err = self.run_main(["resume", rec["id"]])
        self.assertEqual(code, 0, err + out)
        rec2 = self.read_record()
        self.assertEqual(self.assert_ready_meta(rec2).get("sha256"), first.get("sha256"))
        self.assertTrue(self.start_witness.is_file())
