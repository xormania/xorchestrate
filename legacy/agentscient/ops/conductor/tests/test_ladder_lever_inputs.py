"""Ladder start-time --input regression tests, written from the contract.

Contract (only): the quoted paragraphs in the 2026-09-10 brief, taken
verbatim from the repaired ops/conductor/ladder-lever.md:

  ops/bin/ladder.sh SCOPE BRANCH [PLAN] [--input PATH]... starts a
  ladder in the worktree that owns BRANCH. Each --input PATH names a
  file handed to every stage beside the scope and plan, so the work
  order a scope sentence cites reaches every worker as a supplied
  input; each is recorded with its digest and re-verified on resume.

  Record: scope, optional plan, start inputs, and conf paths with
  SHA-256 digests. The record key is inputs, a list of
  {"path", "sha256"} in the order given.

  Refusal input: a --input path is absent or unreadable; needed
  "supply a readable input file". Refusal arguments names the form
  SCOPE BRANCH [PLAN] [--input PATH]... or resume LADDER_ID.

  Resume: a recorded input whose bytes changed refuses class record,
  "restore the recorded bytes or repair the digest", before any
  dispatch.

The tool ops/conductor/tools/ladder.py is the subject and is not read
here. Tests reuse _LadderFixture and its stubbed dispatcher, launcher,
landing and findings tools from test_ladder_lever.py.
Worlds, plants and subprocesses go through ops/testkit.
"""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from test_ladder_lever import BRANCH, _LadderFixture

from ops.testkit import World, plant_bytes

START_FORM = "SCOPE BRANCH [PLAN] [--input PATH]... or resume LADDER_ID"
NEEDED_INPUT = "supply a readable input file"
NEEDED_RECORD = "restore the recorded bytes, or resume with --rescope to record the change"
ALPHA_REL = "wo-alpha-aa32.md"
BETA_REL = "wo-beta-aa32.md"
ALPHA_BODY = "start-input-alpha-aa32\n"
BETA_BODY = "start-input-beta-aa32\n"
CHANGED_BODY = "start-input-alpha-changed-aa32\n"


class _InputFixture(_LadderFixture):
    """Helpers for start-time --input, on the existing ladder fixture."""

    def plant_start_inputs(self, *files: tuple[str, str]) -> list[Path]:
        self.assertIsInstance(self.world, World)
        self.assertTrue(files, "plant: no start inputs given")
        paths: list[Path] = []
        for rel, body in files:
            path = self.repo / rel
            data = body.encode("utf-8")
            plant_bytes(path, data)
            self.git("add", "--", rel)
            paths.append(path)
        self.git("commit", "-m", "plant start inputs\n")
        self.assertEqual(self.porcelain(), "")
        for path, (_rel, body) in zip(paths, files, strict=True):
            self.assertEqual(path.read_text(encoding="utf-8"), body)
            self.assertTrue(path.is_file(), path)
        return paths

    def plant_start_input(self, rel: str, body: str) -> Path:
        paths = self.plant_start_inputs((rel, body))
        self.assertEqual(len(paths), 1, paths)
        return paths[0]

    def file_digest(self, path: Path) -> str:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertRegex(digest, r"^[0-9a-f]{64}$", digest)
        return digest

    def resolve_recorded_path(self, raw: str) -> Path:
        path = Path(raw)
        if not path.is_absolute():
            path = self.repo / path
        return path.resolve()

    def plan_argv_index(self, argv: list[str]) -> int:
        plan = self.plan_path.resolve()
        for i, tok in enumerate(argv):
            raw = str(tok)
            candidate = Path(raw)
            if not candidate.is_absolute():
                candidate = self.repo / candidate
            if candidate.resolve() == plan:
                return i
        self.fail(f"plan path missing from argv: {argv!r}")
        return -1

    def assert_input_rows(
        self,
        record: dict,
        expected: list[tuple[Path, str]],
    ) -> None:
        self.assertIn("inputs", record)
        rows = record["inputs"]
        self.assertIsInstance(rows, list, rows)
        self.assertEqual(len(rows), len(expected), rows)
        for row, (path, digest) in zip(rows, expected, strict=True):
            self.assertIsInstance(row, dict, row)
            self.assertIn("path", row, row)
            self.assertIn("sha256", row, row)
            self.assertEqual(row["sha256"], digest, row)
            self.assertEqual(
                self.resolve_recorded_path(str(row["path"])),
                path.resolve(),
                row,
            )

    def assert_start_inputs_after_plan(
        self,
        argv: list[str],
        paths: list[Path],
    ) -> None:
        plan_idx = self.plan_argv_index(argv)
        wanted = [path.resolve() for path in paths]
        self.assertTrue(wanted, "no start inputs to assert")
        got: list[Path] = []
        i = plan_idx + 1
        while i < len(argv):
            if argv[i] == "--input" and i + 1 < len(argv):
                resolved = self.resolve_recorded_path(str(argv[i + 1]))
                if resolved in wanted:
                    got.append(resolved)
                i += 2
                continue
            i += 1
        self.assertEqual(got, wanted, argv)

    def start_with_inputs(self, *paths: Path, extra: list[str] | None = None):
        argv = [str(self.scope_path), BRANCH]
        if extra:
            argv.extend(extra)
        for path in paths:
            argv.extend(["--input", str(path)])
        return self.invoke(argv)

    def assert_leading_class(self, proc, class_name: str) -> str:
        err = proc.stderr or ""
        lines = [ln for ln in err.splitlines() if ln.strip()]
        self.assertTrue(lines, f"refusal was silent: {err!r}")
        head = lines[0].split(":", 1)[0].strip().lower().replace("_", "-")
        token = class_name.lower().replace("_", "-")
        self.assertEqual(head, token, lines[0])
        return lines[0]


class InputFlagAccepted(_InputFixture):
    """--input PATH after the positionals is accepted; --input=PATH too."""

    def test_input_path_after_positionals_is_accepted(self):
        path = self.plant_start_input(ALPHA_REL, ALPHA_BODY)
        self.patch_plant(trip_stages=["tests"])
        proc = self.start_with_inputs(path)
        self.assertEqual(proc.returncode, 3, self.combined(proc))
        self.assertIn("tests", self.dispatch_names())
        self.assertGreaterEqual(len(self.ladder_paths()), 1)

    def test_input_equals_path_is_accepted(self):
        path = self.plant_start_input(ALPHA_REL, ALPHA_BODY)
        self.patch_plant(trip_stages=["tests"])
        proc = self.invoke(
            [
                str(self.scope_path),
                BRANCH,
                str(self.plan_path),
                f"--input={path}",
            ],
        )
        self.assertEqual(proc.returncode, 3, self.combined(proc))
        self.assertIn("tests", self.dispatch_names())
        self.assertGreaterEqual(len(self.ladder_paths()), 1)

    def test_two_inputs_keep_their_order(self):
        alpha, beta = self.plant_start_inputs(
            (ALPHA_REL, ALPHA_BODY),
            (BETA_REL, BETA_BODY),
        )
        self.assertNotEqual(alpha.resolve(), beta.resolve())
        self.patch_plant(trip_stages=["tests"])
        proc = self.start_with_inputs(alpha, beta)
        self.assertEqual(proc.returncode, 3, self.combined(proc))
        record = self.ladder_record()
        self.assert_input_rows(
            record,
            [
                (alpha, self.file_digest(alpha)),
                (beta, self.file_digest(beta)),
            ],
        )
        self.assert_start_inputs_after_plan(
            self.dispatch_argv_for("tests"),
            [alpha, beta],
        )


class InputRefusals(_InputFixture):
    """Absent or unreadable --input refuses input; a bare flag is arguments."""

    def test_missing_input_refuses_input(self):
        missing = self.repo / "no-such-input-aa32.md"
        self.assertFalse(missing.exists(), "plant: input path must be absent")
        proc = self.invoke(
            [str(self.scope_path), BRANCH, "--input", str(missing)],
        )
        self.assert_leading_class(proc, "input")
        self.assert_refusal(
            proc, "input", named=(str(missing), NEEDED_INPUT),
        )
        self.assertEqual(self.ladder_paths(), [])
        self.assertEqual(self.dispatch_calls(), [])

    def test_unreadable_input_refuses_input(self):
        path = self.plant_start_input(ALPHA_REL, ALPHA_BODY)
        mode = path.stat().st_mode
        try:
            os.chmod(path, 0)
            self.assertFalse(
                os.access(path, os.R_OK),
                f"plant: input still readable: {path}",
            )
            proc = self.invoke(
                [str(self.scope_path), BRANCH, "--input", str(path)],
            )
            self.assert_leading_class(proc, "input")
            self.assert_refusal(
                proc, "input", named=(str(path), NEEDED_INPUT),
            )
            self.assertEqual(self.ladder_paths(), [])
            self.assertEqual(self.dispatch_calls(), [])
        finally:
            os.chmod(path, stat.S_IMODE(mode) or 0o644)

    def test_input_flag_without_a_path_refuses_arguments(self):
        proc = self.invoke(
            [str(self.scope_path), BRANCH, str(self.plan_path), "--input"],
        )
        line = self.assert_refusal(proc, "arguments")
        self.assertIn(START_FORM, line)
        self.assertNotIn(NEEDED_INPUT, line)
        self.assertEqual(self.ladder_paths(), [])
        self.assertEqual(self.dispatch_calls(), [])


class InputRecord(_InputFixture):
    """The ladder record key inputs is present, ordered, and digest-bearing."""

    def test_record_carries_inputs_with_path_and_sha256_in_order(self):
        alpha, beta = self.plant_start_inputs(
            (ALPHA_REL, ALPHA_BODY),
            (BETA_REL, BETA_BODY),
        )
        digest_a = self.file_digest(alpha)
        digest_b = self.file_digest(beta)
        self.assertNotEqual(digest_a, digest_b)
        self.patch_plant(trip_stages=["tests"])
        proc = self.start_with_inputs(alpha, beta)
        self.assertEqual(proc.returncode, 3, self.combined(proc))
        self.assert_input_rows(
            self.ladder_record(),
            [(alpha, digest_a), (beta, digest_b)],
        )

    def test_record_inputs_is_empty_list_when_no_input_flag(self):
        self.patch_plant(trip_stages=["tests"])
        proc = self.run_ladder()
        self.assertEqual(proc.returncode, 3, self.combined(proc))
        record = self.ladder_record()
        self.assertIn("inputs", record)
        self.assertIsInstance(record["inputs"], list)
        self.assertEqual(record["inputs"], [])


class InputDispatchArgv(_InputFixture):
    """Every dispatch stage carries --input <path> after the plan."""

    def test_dispatch_stage_argv_carries_input_after_the_plan(self):
        path = self.plant_start_input(ALPHA_REL, ALPHA_BODY)
        proc = self.start_with_inputs(path)
        self.assertEqual(proc.returncode, 0, self.combined(proc))
        calls = self.dispatch_calls()
        self.assertTrue(calls, "no dispatch stage ran")
        for row in calls:
            argv = row.get("argv") or []
            self.assert_start_inputs_after_plan(argv, [path])

    def test_dry_run_argv_carries_input_after_the_plan(self):
        path = self.plant_start_input(ALPHA_REL, ALPHA_BODY)
        proc = self.invoke(
            [
                "--dry-run",
                str(self.scope_path),
                BRANCH,
                "--input",
                str(path),
            ],
        )
        self.assertEqual(proc.returncode, 0, self.combined(proc))
        stages = self.parse_stage_table(proc)
        checked = 0
        wanted = path.resolve()
        for row in stages:
            if row.get("name") == "pr":
                continue
            argv = row.get("argv")
            self.assertIsInstance(argv, list, row)
            checked += 1
        self.assertGreater(checked, 0, stages)
        live = self.start_with_inputs(path)
        self.assertEqual(live.returncode, 0, self.combined(live))
        calls = self.dispatch_calls()
        self.assertTrue(calls, "no dispatch stage ran")
        for row in calls:
            if row.get("name") == "pr":
                continue
            argv = list(row.get("argv") or [])
            values = [
                self.resolve_recorded_path(str(item))
                for item in self.flag_values(argv, "--input")
            ]
            self.assertIn(wanted, values, argv)


class InputResume(_InputFixture):
    """Resume re-verifies recorded input bytes before any dispatch."""

    def _stop_after_tests_with_input(self, path: Path) -> str:
        self.patch_plant(trip_stages=["tests"])
        first = self.start_with_inputs(path)
        self.assertEqual(first.returncode, 3, self.combined(first))
        rec_id = self.close_tripped_record()
        self.assertTrue(rec_id)
        record = self.ladder_record()
        ladder_id = record.get("id")
        self.assertTrue(ladder_id, record)
        self.assertEqual(self.dispatch_names().count("tests"), 1)
        self.patch_plant(trip_stages=[])
        return str(ladder_id)

    def test_resume_refuses_when_input_bytes_changed(self):
        path = self.plant_start_input(ALPHA_REL, ALPHA_BODY)
        digest = self.file_digest(path)
        ladder_id = self._stop_after_tests_with_input(path)
        plant_bytes(path, CHANGED_BODY.encode("utf-8"))
        self.assertEqual(path.read_text(encoding="utf-8"), CHANGED_BODY)
        self.assertNotEqual(self.file_digest(path), digest)
        self.git("add", "--", ALPHA_REL)
        self.git("commit", "-m", "change start input bytes\n")
        second = self.invoke(["resume", ladder_id])
        self.assert_refusal(
            second, "record", named=(digest, NEEDED_RECORD),
        )
        self.assertEqual(self.dispatch_names().count("check-tests"), 0)
        self.assertEqual(self.dispatch_names().count("tests"), 1)

    def test_resume_continues_when_input_bytes_unchanged(self):
        path = self.plant_start_input(ALPHA_REL, ALPHA_BODY)
        before = path.read_bytes()
        digest = self.file_digest(path)
        ladder_id = self._stop_after_tests_with_input(path)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.file_digest(path), digest)
        second = self.invoke(["resume", ladder_id])
        self.assertEqual(second.returncode, 0, self.combined(second))
        self.assertGreaterEqual(self.dispatch_names().count("check-tests"), 1)


class ArgumentsForm(_InputFixture):
    """Refusal arguments names the start form that includes --input PATH."""

    def test_arguments_refusal_names_the_new_form(self):
        proc = self.invoke([])
        line = self.assert_refusal(proc, "arguments")
        self.assertIn(START_FORM, line)
        self.assertEqual(self.ladder_paths(), [])
