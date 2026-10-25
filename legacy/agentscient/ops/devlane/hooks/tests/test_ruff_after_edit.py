"""Moved ruff-after-edit.sh corpus, plus D4 as a stub ruff on PATH.

The in-file --test staged files inside the real worktree and needed a
runnable ruff. These cases use a throwaway git repo and a stub whose
answers are proved before the hook is asked anything.
"""

import json
import shutil
import unittest
from pathlib import Path

import support

from ops.testkit import World, run

HOOK = support.CLAUDE_DIR / "ruff-after-edit.sh"
TIMEOUT = 120


class RuffAfterEdit(unittest.TestCase):
    def setUp(self):
        self.world = World(prefix="ruff-hook-")
        self.addCleanup(self.world.close)
        self.world.require("git")
        self.world.require("jq")
        self.hook = self._subject()
        self.env = self.world.env()
        self.repo = self.world.cwd
        self._stub_kind = "corpus"

    def _subject(self):
        self.assertTrue(HOOK.is_file(), f"INVALID: hook missing: {HOOK}")
        return self.world.stage(HOOK)

    def _git_repo(self, *, ruff_toml: str | None = ""):
        self.world.git("-c", "init.defaultBranch=main", "init", "-q")
        if ruff_toml is not None:
            self.world.plant_text(
                self.repo / "ruff.toml", ruff_toml,
                recognisable=ruff_toml or None,
            )
            if ruff_toml == "":
                landed = (self.repo / "ruff.toml").read_text(encoding="utf-8")
                self.assertEqual(landed, "", "INVALID: ruff.toml plant drifted")
        return self.env

    def _stub(self, kind="corpus", *, check_out=""):
        self._stub_kind = kind
        if kind == "corpus":
            self.world.tools.plant(
                "ruff",
                stdout="",
                exit=0,
                responses=[
                    {"match": ["--version"], "stdout": "ruff 0.0.0-stub\n",
                     "stderr": "", "exit": 0},
                    {"match": ["format", "fmt.py"], "stdout": "", "stderr": "",
                     "exit": 1},
                    {"match": ["check", "bad.py"], "stdout": check_out,
                     "stderr": "", "exit": 1},
                ],
            )
        elif kind == "d4_format":
            self.world.tools.plant(
                "ruff",
                stdout="",
                exit=0,
                responses=[
                    {"match": ["--version"], "stdout": "ruff 0.0.0-stub\n",
                     "stderr": "", "exit": 0},
                    {"match": ["format"], "stdout": "", "stderr": "", "exit": 2},
                ],
            )
        elif kind == "d4_check":
            self.world.tools.plant(
                "ruff",
                stdout="",
                exit=0,
                responses=[
                    {"match": ["--version"], "stdout": "ruff 0.0.0-stub\n",
                     "stderr": "", "exit": 0},
                    {"match": ["check"], "stdout": "", "stderr": "", "exit": 2},
                ],
            )
        else:
            raise AssertionError(f"INVALID: unknown ruff stub {kind!r}")
        planted = self.world.bin / "ruff"
        which = shutil.which("ruff", path=self.env["PATH"])
        self.assertEqual(
            Path(which).resolve(), planted.resolve(),
            f"INVALID: which ruff is {which}, not the stub {planted}",
        )
        return planted

    def _prove_stub(self, env, *, version_rc=0, check_rc=None, check_out=None,
                    format_rc=None, file="x.py"):
        if self._stub_kind == "corpus" and check_out is not None:
            self._stub("corpus", check_out=check_out)
        v = run(
            ["ruff", "--version"], cwd=self.repo, env=env, timeout=TIMEOUT,
            expect=version_rc,
        )
        self.assertIn(
            "ruff", v.stdout.lower(),
            f"INVALID: stub --version did not identify itself: {v.stdout!r}",
        )
        if format_rc is not None:
            f = run(
                ["ruff", "format", "--check", file],
                cwd=self.repo, env=env, timeout=TIMEOUT,
            )
            self.assertEqual(
                f.exit, format_rc,
                f"INVALID: stub format --check rc {f.exit}, wanted {format_rc}",
            )
        if check_rc is not None:
            c = run(
                ["ruff", "check", "--quiet", file],
                cwd=self.repo, env=env, timeout=TIMEOUT,
            )
            self.assertEqual(
                c.exit, check_rc,
                f"INVALID: stub check rc {c.exit}, wanted {check_rc}",
            )
            if check_out is not None:
                self.assertEqual(
                    c.stdout, check_out,
                    f"INVALID: stub check stdout {c.stdout!r}, wanted {check_out!r}",
                )

    def _fire(self, env, payload: str):
        return run(
            ["bash", str(self.hook)],
            cwd=self.repo,
            env=env,
            stdin=payload,
            timeout=TIMEOUT,
        )

    def _plant_py(self, name: str, body: str, *, recognisable: str):
        path = self.world.plant_text(
            self.repo / name, body, recognisable=recognisable,
        )
        self.assertTrue(path.is_file(), f"INVALID: {name} missing")
        self.assertTrue(str(path).endswith(".py"), f"INVALID: {name} not .py")
        return path

    def test_catches_a_lint_finding(self):
        env = self._git_repo()
        self._stub("corpus")
        path = self._plant_py("bad.py", "import os\n", recognisable="import os")
        self._prove_stub(
            env, check_rc=1,
            check_out=f"{path}:1:1: F401 unused import\n",
            file=str(path),
        )
        proc = self._fire(env, support.write_payload(file_path=str(path)))
        self.assertTrue(
            support.has_additional_context(proc.stdout),
            f"wanted a lint complaint, got {proc.stdout!r} / {proc.stderr!r}",
        )

    def test_stays_quiet_on_a_clean_py(self):
        env = self._git_repo()
        self._stub("corpus")
        path = self._plant_py("good.py", "x = 1\n", recognisable="x = 1")
        self._prove_stub(env, check_rc=0, check_out="", file=str(path))
        proc = self._fire(env, support.write_payload(file_path=str(path)))
        self.assertFalse(
            support.has_additional_context(proc.stdout),
            f"nagged a clean file: {proc.stdout!r}",
        )

    def test_stays_quiet_on_format_only(self):
        env = self._git_repo()
        self._stub("corpus")
        path = self._plant_py(
            "fmt.py", 'x = {  "a":1 }\n', recognisable='"a":1'
        )
        self._prove_stub(
            env, format_rc=1, check_rc=0, check_out="", file=str(path)
        )
        proc = self._fire(env, support.write_payload(file_path=str(path)))
        self.assertFalse(
            support.has_additional_context(proc.stdout),
            f"format-only was treated as a failure: {proc.stdout!r}",
        )

    def test_reads_serena_relative_path(self):
        env = self._git_repo()
        self._stub("corpus")
        path = self._plant_py("bad.py", "import os\n", recognisable="import os")
        self._prove_stub(
            env, check_rc=1,
            check_out=f"{path}:1:1: F401 unused import\n",
            file=str(path),
        )
        proc = self._fire(
            env,
            support.write_payload(
                relative_path="bad.py", cwd=str(self.repo)
            ),
        )
        self.assertTrue(
            support.has_additional_context(proc.stdout),
            f"relative_path was not read: {proc.stdout!r}",
        )
        self.assertTrue(path.is_file(), "INVALID: relative_path target vanished")

    def test_ignores_a_non_py_file(self):
        env = self._git_repo()
        self._stub("corpus")
        path = self.world.plant_text(
            self.repo / "pyproject.toml", "[project]\nname='x'\n",
            recognisable="name=",
        )
        self.assertFalse(str(path).endswith(".py"))
        proc = self._fire(env, support.write_payload(file_path=str(path)))
        self.assertFalse(
            support.has_additional_context(proc.stdout),
            f"nagged a non-.py file: {proc.stdout!r}",
        )

    def test_ignores_a_file_that_is_not_there(self):
        env = self._git_repo()
        self._stub("corpus")
        gone = self.repo / "gone.py"
        self.assertFalse(gone.exists(), "INVALID: gone.py exists")
        proc = self._fire(env, support.write_payload(file_path=str(gone)))
        self.assertFalse(
            support.has_additional_context(proc.stdout),
            f"nagged a missing file: {proc.stdout!r}",
        )

    def test_ignores_a_payload_with_no_path(self):
        env = self._git_repo()
        self._stub("corpus")
        proc = self._fire(env, json.dumps({"tool_input": {}}))
        self.assertFalse(
            support.has_additional_context(proc.stdout),
            f"nagged an empty payload: {proc.stdout!r}",
        )

    def test_ignores_junk_input(self):
        env = self._git_repo()
        self._stub("corpus")
        proc = self._fire(env, "not json at all")
        self.assertFalse(
            support.has_additional_context(proc.stdout),
            f"nagged junk input: {proc.stdout!r}",
        )

    def test_ignores_empty_input(self):
        env = self._git_repo()
        self._stub("corpus")
        proc = self._fire(env, "")
        self.assertFalse(
            support.has_additional_context(proc.stdout),
            f"nagged empty input: {proc.stdout!r}",
        )

    def test_noop_where_ruff_is_not_configured(self):
        env = self._git_repo(ruff_toml=None)
        self._stub("corpus")
        path = self._plant_py(
            "bad.py", 'x = {  "a":1 }\n', recognisable='"a":1'
        )
        self.assertFalse(
            (self.repo / "ruff.toml").exists(),
            "INVALID: ruff.toml present in the unconfigured case",
        )
        self._prove_stub(
            env, check_rc=1,
            check_out=f"{path}:1:1: F401 unused import\n",
            file=str(path),
        )
        proc = self._fire(env, support.write_payload(file_path=str(path)))
        self.assertFalse(
            support.has_additional_context(proc.stdout),
            f"nagged an unconfigured repo: {proc.stdout!r}",
        )

    def test_d4_format_check_rc_2_is_not_a_formatting_violation(self):
        """Stub: --version ok, format --check rc 2, check rc 0 → no output."""
        env = self._git_repo()
        self._stub("d4_format")
        path = self._plant_py("x.py", "x = 1\n", recognisable="x = 1")
        self._prove_stub(
            env, format_rc=2, check_rc=0, check_out="", file=str(path)
        )
        proc = self._fire(env, support.write_payload(file_path=str(path)))
        self.assertFalse(
            support.has_additional_context(proc.stdout),
            f"D4: format rc 2 was reported as a formatting violation: "
            f"{proc.stdout!r}",
        )
        self.assertNotIn(
            "formatting", proc.stdout.lower(),
            f"D4: output mentioned formatting: {proc.stdout!r}",
        )

    def test_d4_check_rc_2_with_empty_stdout_is_silence(self):
        """If the format branch is gone: check rc 2 + empty stdout → silence."""
        env = self._git_repo()
        self._stub("d4_check")
        path = self._plant_py("x.py", "x = 1\n", recognisable="x = 1")
        self._prove_stub(
            env, check_rc=2, check_out="", file=str(path)
        )
        proc = self._fire(env, support.write_payload(file_path=str(path)))
        self.assertFalse(
            support.has_additional_context(proc.stdout),
            f"D4: check rc 2 empty stdout was not silence: {proc.stdout!r}",
        )
