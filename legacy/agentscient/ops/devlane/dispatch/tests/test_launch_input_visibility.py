"""Copied --input paths must appear in the rendered worker brief.

Written from CONTRACT.md §Dispatch Verbs (--input visibility) and
§Template values (``inputs``):

  --input PATH copies a file into the job directory's in/ and names it
  to the template as {inputs}; its digest enters the record. Every
  shipped harness-job template must actually include that value in its
  rendered brief. Copying inputs without telling the worker their paths
  does not discharge this boundary. List the copied paths in the
  caller's order, including for implementation, planning and review
  jobs; retain the existing instruction to read the supplied files
  before work. With no inputs the list is empty, never a guessed file
  or directory scan. Independent acceptance launches the shipped jobs
  through synthetic harnesses and checks that the exact copied paths
  reach the worker's prompt and agree with brief.inputs; it also checks
  an empty-input neighbor. No input contents are inlined or promoted to
  extra authority.

  Template values: inputs is the absolute paths of the copied inputs,
  space-separated, in the order given.

launch.py and jobs.json were not read as a source for these assertions.
The catalogue is loaded at runtime only to name every shipped harness
job. Current defect: six job prompts omit the input listing even though
the files are copied.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import launch_support as ls

from ops.testkit import World

STAGES = (
    "plan", "tests", "check-tests", "code", "review", "adjudicate",
)
SYNTHETIC_HARNESS = "grok"


def _item_path(item):
    if isinstance(item, str):
        return item
    if isinstance(item, dict) and "path" in item:
        return item["path"]
    raise TypeError(f"brief.inputs item is not a path: {item!r}")


class ShippedHarnessJobsNameCopiedInputsInTheWorkerPrompt(ls._TempLaunch):
    """Every shipped harness job lists copied --input paths in the brief."""

    def setUp(self):
        super().setUp()
        self.assertIsInstance(self.world, World)
        # Review jobs refuse an empty comparison (base_sha == ref_sha).
        # Existing silent-neighbour tests plant local `dev` at mid so
        # merge-base(work, dev) differs from --ref; reuse that plant.
        self._git("branch", "dev", self.mid_sha)
        self.assertNotEqual(self.mid_sha, self.ref)

    def _shipped_harness_jobs(self):
        self.assertTrue(ls.JOBS_PATH.is_file(), f"missing {ls.JOBS_PATH}")
        catalogue = json.loads(ls.JOBS_PATH.read_text(encoding="utf-8"))
        self.assertIsInstance(catalogue, dict)
        self.assertGreater(len(catalogue), 0, "jobs catalogue must name jobs")
        shipped = {}
        for name, spec in catalogue.items():
            self.assertIsInstance(spec, dict, f"{name} spec is not an object")
            adapter = spec.get("adapter")
            if adapter is not None and adapter != "harness":
                continue
            if spec.get("snapshot") == "fileset":
                continue
            if spec.get("withheld"):
                continue
            shipped[name] = spec
        self.assertGreaterEqual(
            len(shipped), 6,
            "the contracted defect names six omitting jobs; "
            f"catalogue yielded {sorted(shipped)}",
        )
        return shipped

    def _stage_for(self, name, spec):
        if name in STAGES:
            return name
        role = spec.get("role")
        if role == "write":
            if "test" in name:
                return "tests"
            return "code"
        if "review" in name or "audit" in name:
            return "review"
        if "adjudicat" in name:
            return "adjudicate"
        return "plan"

    def _clear_dispatch(self):
        for path in list(self.job_dirs()):
            shutil.rmtree(path, ignore_errors=True)
        for path in list(self.record_files()):
            path.unlink(missing_ok=True)
        self.start_witness.unlink(missing_ok=True)
        self.witness.unlink(missing_ok=True)

    def _argv_for_job(self, name, spec, extra=(), scope="pin-input-visibility"):
        return self.argv_for(
            job=name,
            harness=SYNTHETIC_HARNESS,
            stage=self._stage_for(name, spec),
            scope=scope,
            extra=list(extra),
        )

    def _launch_shipped(self, name, spec, extra=()):
        os.environ["TASK_LAUNCH_JOB"] = name
        self._clear_dispatch()
        argv = self._argv_for_job(name, spec, extra=extra)
        code, out, err = self.dispatch(argv)
        text = self.combined(out, err)
        if code == ls.REFUSAL_EXIT and "scope-cap" in text.lower():
            self.assert_not_started()
            self.assertEqual(self.job_dirs(), [])
            rec, witness, *_ = self.launch_ok(
                self._argv_for_job(name, spec, extra=extra, scope=None),
            )
            return rec, witness
        self.assertNotEqual(
            code, ls.REFUSAL_EXIT,
            f"{name} must not refuse: {text!r}",
        )
        self.assertEqual(code, 0, f"{name} exits 0, got {code}: {text!r}")
        rec = self.read_record()
        self.assertEqual(rec.get("status"), "closed", rec)
        return rec, self.read_witness()

    def _worker_prompt(self, rec, witness):
        job_dir = Path(rec["snapshot"]["root"]).parent
        path = job_dir / "prompt.txt"
        self.assertTrue(path.is_file(), f"prompt.txt missing under {job_dir}")
        handed = path.read_text(encoding="utf-8")
        self.assertTrue(handed, "prompt.txt is empty")
        received = witness.get("prompt_text") or ""
        self.assertTrue(
            received,
            f"{SYNTHETIC_HARNESS} must receive the rendered brief via "
            "--prompt-file (prompt_text empty)",
        )
        return handed, received, job_dir

    def _recorded_input_paths(self, rec):
        items = rec.get("brief", {}).get("inputs")
        self.assertIsInstance(
            items, list,
            f"brief.inputs must be a list, got {items!r}",
        )
        paths = []
        for item in items:
            try:
                path = _item_path(item)
            except TypeError as exc:
                self.fail(str(exc))
            self.assertIsInstance(path, str, item)
            self.assertTrue(path, f"brief.inputs path is empty: {item!r}")
            paths.append(path)
        return paths

    def _plant_pair(self, job):
        src = self.home / "caller-inputs" / job
        first = src / f"{job}-z-given-first.md"
        second = src / f"{job}-a-given-second.md"
        token_first = f"INLINE-FORBIDDEN-{job}-FIRST"
        token_second = f"INLINE-FORBIDDEN-{job}-SECOND"
        body_first = f"# {token_first}\nunique-bytes-1-{job}\n"
        body_second = f"# {token_second}\nunique-bytes-2-{job}\n"
        self.world.plant_text(first, body_first, recognisable=token_first)
        self.world.plant_text(second, body_second, recognisable=token_second)
        return first, second, token_first, token_second, body_first, body_second

    def test_every_shipped_harness_job_hands_copied_input_paths_to_the_worker(
            self):
        shipped = self._shipped_harness_jobs()
        launched = 0
        for name, spec in sorted(shipped.items()):
            with self.subTest(job=name):
                first, second, tok_a, tok_b, body_a, body_b = self._plant_pair(
                    name,
                )
                rec, witness = self._launch_shipped(
                    name, spec,
                    extra=["--input", str(first), "--input", str(second)],
                )
                launched += 1
                self.assertEqual(rec["job"], name)
                handed, received, job_dir = self._worker_prompt(rec, witness)
                recorded = self._recorded_input_paths(rec)
                self.assertEqual(
                    len(recorded), 2,
                    f"{name}: both --input flags must land in brief.inputs, "
                    f"got {recorded!r}",
                )

                job_in = (job_dir / "in").resolve()
                self.assertTrue(job_in.is_dir(), f"{name}: copies land in in/")
                copy_first = job_in / first.name
                copy_second = job_in / second.name
                self.assertTrue(
                    copy_first.is_file(),
                    f"{name}: expected copy {copy_first}",
                )
                self.assertTrue(
                    copy_second.is_file(),
                    f"{name}: expected copy {copy_second}",
                )
                self.assertEqual(copy_first.read_text(encoding="utf-8"), body_a)
                self.assertEqual(copy_second.read_text(encoding="utf-8"), body_b)

                resolved = [Path(p).resolve() for p in recorded]
                self.assertEqual(
                    resolved, [copy_first, copy_second],
                    f"{name}: brief.inputs must be the copies in caller "
                    f"order, not originals or sorted names; got {recorded!r}",
                )
                for raw, orig in zip(recorded, (first, second), strict=True):
                    copy = Path(raw)
                    self.assertTrue(
                        copy.is_absolute(),
                        f"{name}: template inputs are absolute paths, "
                        f"got {raw!r}",
                    )
                    self.assertNotEqual(
                        copy.resolve(), orig.resolve(),
                        f"{name}: brief.inputs names the original path {orig}",
                    )

                listed = " ".join(recorded)
                for blob, label in ((received, "worker prompt"), (handed, "prompt.txt")):
                    self.assertIn(
                        listed, blob,
                        f"{name}: {label} must contain the exact copied "
                        "paths, space-separated, in brief.inputs order; "
                        f"copied={recorded!r} originals="
                        f"{[str(first), str(second)]!r}",
                    )
                    first_at = blob.find(recorded[0])
                    second_at = blob.find(recorded[1])
                    self.assertGreaterEqual(first_at, 0, blob)
                    self.assertGreaterEqual(second_at, 0, blob)
                    self.assertLess(
                        first_at, second_at,
                        f"{name}: {label} must list copies in caller order",
                    )
                    self.assertRegex(
                        blob,
                        r"(?i)read (?:them|the supplied files).{0,40}before work",
                        f"{name}: {label} must retain the instruction "
                        "to read the supplied files before work; "
                        "listing paths without that instruction does "
                        "not discharge the boundary",
                    )
                    self.assertNotIn(
                        str(first.resolve()), blob,
                        f"{name}: {label} must not name the original path "
                        f"{first}",
                    )
                    self.assertNotIn(
                        str(second.resolve()), blob,
                        f"{name}: {label} must not name the original path "
                        f"{second}",
                    )
                    self.assertNotIn(
                        tok_a, blob,
                        f"{name}: {label} inlined contents of {first.name}",
                    )
                    self.assertNotIn(
                        tok_b, blob,
                        f"{name}: {label} inlined contents of {second.name}",
                    )
                    self.assertNotIn(body_a.strip(), blob)
                    self.assertNotIn(body_b.strip(), blob)
        self.assertEqual(
            launched, len(shipped),
            "every shipped harness job must be launched",
        )

    def test_empty_input_neighbor_does_not_invent_paths_or_inline_contents(
            self):
        shipped = self._shipped_harness_jobs()
        decoy = self.home / "caller-inputs" / "not-passed.md"
        decoy_token = "INLINE-FORBIDDEN-EMPTY-DECOY"
        decoy_body = f"# {decoy_token}\nnot-an-input\n"
        self.world.plant_text(decoy, decoy_body, recognisable=decoy_token)
        launched = 0
        for name, spec in sorted(shipped.items()):
            with self.subTest(job=name):
                rec, witness = self._launch_shipped(name, spec)
                launched += 1
                self.assertEqual(rec["job"], name)
                recorded = self._recorded_input_paths(rec)
                self.assertEqual(
                    len(recorded), 0,
                    f"{name}: with no --input the list is empty, never a "
                    f"guessed file or directory scan; got {recorded!r}",
                )
                handed, received, job_dir = self._worker_prompt(rec, witness)
                job_in = job_dir / "in"
                if job_in.exists():
                    found = sorted(
                        p.name for p in job_in.iterdir() if p.is_file()
                    )
                    self.assertEqual(
                        found, [],
                        f"{name}: empty --input must not copy or scan files "
                        f"into in/; found {found}",
                    )
                for blob, label in ((received, "worker prompt"), (handed, "prompt.txt")):
                    self.assertNotIn(
                        str(decoy.resolve()), blob,
                        f"{name}: {label} must not guess unused path {decoy}",
                    )
                    self.assertNotIn(
                        decoy_token, blob,
                        f"{name}: {label} inlined unused input contents",
                    )
                    self.assertNotIn(decoy_body.strip(), blob)
                    self.assertNotIn(
                        str((job_dir / "in" / decoy.name).resolve()), blob,
                        f"{name}: {label} must not invent a copied path for "
                        "an input that was not given",
                    )
                    self.assertNotRegex(
                        blob,
                        r"(?im)^Inputs:\s+\S",
                        f"{name}: {label} must not invent an Inputs: "
                        "list when none were given; with no inputs the "
                        "list is empty, never a guessed file",
                    )
        self.assertEqual(
            launched, len(shipped),
            "empty-input neighbor must run every shipped harness job",
        )
