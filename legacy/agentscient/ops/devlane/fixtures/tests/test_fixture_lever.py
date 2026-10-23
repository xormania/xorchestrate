"""Fixture-lever acceptance tests, written from the slice-1 contract.

Contracts (only): the fixture-apps slice-1 plan (make, prove, grade),
`docs/wire.md`, `docs/probes.md`. The lever `ops/bin/fixture.sh` is the
subject and is not read here. Tests invoke it as a subprocess inside a
scratch git repository that holds a synthetic mini-app.

The live fixture state and this repo must stay untouched — asserted in
tearDown.

The implementation lands in parallel. A missing lever is one red
(`test_the_lever_file_is_present_and_executable`). Every other case
stages a copy-tool-bytes stand-in so a missing file is not every test's
failure: when the lever exists its bytes are copied and run; when it
does not, a no-op stub (exit 0, no output) is planted so each assertion
goes red on the contract promise rather than a missing-file spawn.
Prove cases plant their own DIR and never wait on `make`.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import unittest
from pathlib import Path

from ops.testkit import Result, Timeout, World, make_executable, run

REPO = Path(__file__).resolve().parents[4]
LEVER = REPO / "ops" / "bin" / "fixture.sh"

APP = "synth"
AGENT = "Fixture Lever Tester <fixture-tester@example.invalid>"
MALFORMED_AGENT = "JustAName"
REPO_USER = "Repo Config"
REPO_EMAIL = "repo-config@example.invalid"
TRAILER = "Source: original"
BRANCH = "fixture-lineage"
RUN_CAP = 30
PROBE_WALL = 20
COPY_PAYLOAD = "COPY_LIST_PAYLOAD_SYNTH\n"
IGNORED_PAYLOAD = "IGNORED_MUST_NOT_RIDE\n"
UNTRACKED_PAYLOAD = "UNTRACKED_MUST_NOT_RIDE\n"
EXTRA_PAYLOAD = "MUST_NOT_APPEAR_IN_DIR\n"
DECLARED_01 = "Input is passed as arguments."
DECLARED_02 = "Failures are reported."
MARKER_01 = "PLANTED_FAULT_SYN_01"
MARKER_02 = "PLANTED_FAULT_SYN_02"
FIXED_01 = "FIXED_SYN_01"
FIXED_02 = "FIXED_SYN_02"

ABSENT_STUB = b"#!/usr/bin/env bash\nexit 0\n"

CLI_PLANTED = '''"""cli — synthetic fixture app (apparatus)."""

import os
import subprocess


def greet(name: str) -> str:
    os.system("echo " + name)  # PLANTED_FAULT_SYN_01
    return name


def greet_argv(name: str) -> str:
    subprocess.run(["echo", name], check=False)
    return name


def load_rows(path: str) -> list:
    try:
        text = open(path, encoding="utf-8").read()
        return text.splitlines()
    except Exception:
        return []  # PLANTED_FAULT_SYN_02


def load_rows_strict(path: str) -> list:
    text = open(path, encoding="utf-8").read()
    return text.splitlines()
'''

CLI_GREET_FIXED = CLI_PLANTED.replace(
    '    os.system("echo " + name)  # PLANTED_FAULT_SYN_01\n',
    '    subprocess.run(["echo", name], check=False)  # FIXED_SYN_01\n',
)

CLI_LOAD_FIXED = CLI_PLANTED.replace(
    "        return []  # PLANTED_FAULT_SYN_02\n",
    "        raise  # FIXED_SYN_02\n",
)

README_TEXT = f"""# synth

This tree is apparatus of the lane, never product code.

{DECLARED_01}

{DECLARED_02}
"""

PROBE_01 = f"""#!/bin/sh
printenv > probes/env-dump.txt
date +%z > probes/tz-offset.txt
if grep -q '{MARKER_01}' src/cli.py; then
  exit 1
fi
exit 0
"""

PROBE_02 = f"""#!/bin/sh
if grep -q '{MARKER_02}' src/cli.py; then
  exit 1
fi
exit 0
"""

COLOUR_RE = re.compile(r"\b(RED|GREEN|UNREACHABLE)\b")
COUNTS_RE = re.compile(
    r"red=(?P<red>\d+)\s+green=(?P<green>\d+)\s+"
    r"unreachable=(?P<unreachable>\d+)"
)


def _live_fixture_roots() -> list[Path]:
    paths: list[Path] = []
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        paths.append(Path(xdg) / "agentscient" / "fixtures")
    paths.append(Path.home() / ".local" / "state" / "agentscient" / "fixtures")
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = path if not path.exists() else path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(path)
    return unique


def _rel_files(root: Path) -> set[str]:
    found: set[str] = set()
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if ".git" in rel.parts:
            continue
        found.add(rel.as_posix())
    return found


def _line_of(text: str, marker: str) -> int:
    hits = [
        index
        for index, line in enumerate(text.splitlines(), 1)
        if marker in line
    ]
    if len(hits) != 1:
        raise AssertionError(f"marker {marker!r} hits {hits} in {text!r}")
    return hits[0]


def _unified_patch(relpath: str, before: str, after: str) -> str:
    diff = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"a/{relpath}",
        tofile=f"b/{relpath}",
    )
    body = "".join(diff)
    return f"diff --git a/{relpath} b/{relpath}\n{body}"


def assert_refusal_wire(testcase: unittest.TestCase, proc, class_name: str) -> None:
    """Pin `class: expected …; found …; needed …` as a leading token."""
    refusal = proc.refusal()
    token = class_name.lower().replace("_", "-")
    testcase.assertEqual(
        refusal.cls,
        token,
        f"refusal class must be the leading token {token!r}: {refusal.line!r}",
    )


class _LeverFixture(unittest.TestCase):
    """Scratch HOME + git repo, staged lever copy, live fixture roots frozen."""

    def setUp(self):
        self.world = World(prefix="fixture-lever-")
        self.world.require("git")
        self.world.require("bash")
        self._live = self.world.watch(*_live_fixture_roots(), contents=False)
        self._head_before = run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO,
            env=self.world.env(),
            timeout=RUN_CAP,
        ).stdout
        self._diff_before = run(
            ["git", "diff", "HEAD"],
            cwd=REPO,
            env=self.world.env(),
            timeout=RUN_CAP,
        ).stdout
        self.root = self.world.root
        self.home = self.world.home
        self.repo = self.world.cwd
        self.staged = self.repo / "ops" / "bin" / "fixture.sh"
        self._stage_lever_bytes()
        self.app_root = (
            self.repo / "ops" / "devlane" / "fixtures" / "apps" / APP
        )
        self.copy_rel = "ops/bin/copied.txt"
        self.conf_path = self.root / "lane.conf"
        # The lane conf the lever reads its lane name from, apart from the copy list.
        self.lane_conf_path = self.world.plant_text(
            self.root / "lane-conf.sh",
            "LANE_NAME='agentscient'\n",
        )
        self.seeded = False
        self._linked = None

    def tearDown(self):
        try:
            if self._linked is not None:
                self.git(
                    "worktree",
                    "remove",
                    "--force",
                    str(self._linked),
                    check=False,
                )
            self._live.assert_unchanged(self, "live fixture state mutated")
            head_after = run(
                ["git", "rev-parse", "HEAD"],
                cwd=REPO,
                env=self.world.env(),
                timeout=RUN_CAP,
            ).stdout
            self.assertEqual(head_after, self._head_before, "real repo HEAD moved")
            diff_after = run(
                ["git", "diff", "HEAD"],
                cwd=REPO,
                env=self.world.env(),
                timeout=RUN_CAP,
            ).stdout
            self.assertEqual(
                diff_after,
                self._diff_before,
                "real repo tracked files mutated",
            )
        finally:
            self.world.close()

    def _stage_lever_bytes(self) -> None:
        staged = self.world.stage(LEVER, "fixture.sh")
        data = staged.read_bytes() if LEVER.is_file() else ABSENT_STUB
        self.staged = self.world.plant_bytes(self.staged, data)
        make_executable(self.staged)
        landed = self.staged.read_bytes()
        self.assertEqual(landed, data, "copy-lever-bytes did not land intact")
        self.assertEqual(len(landed), len(data))
        if LEVER.is_file():
            self.assertEqual(landed, LEVER.read_bytes())
            self.assertNotEqual(landed, ABSENT_STUB)
        else:
            self.assertEqual(landed, ABSENT_STUB, "stub plant did not land intact")
        self.assertTrue(self.staged.is_file())
        self.assertTrue(os.access(self.staged, os.X_OK), self.staged)

    def _plant_bytes(self, path: Path, data: bytes) -> None:
        self.world.plant_bytes(path, data)

    def git_env(self) -> dict[str, str]:
        """The world's environment with the scratch owner's identity."""
        return self.world.env({
            "GIT_AUTHOR_NAME": REPO_USER,
            "GIT_AUTHOR_EMAIL": REPO_EMAIL,
            "GIT_COMMITTER_NAME": REPO_USER,
            "GIT_COMMITTER_EMAIL": REPO_EMAIL,
        })

    def git(self, *args: str, check: bool = True, cwd: Path | None = None):
        proc = run(
            [
                "git",
                "-c",
                f"user.name={REPO_USER}",
                "-c",
                f"user.email={REPO_EMAIL}",
                "-c",
                "commit.gpgsign=false",
                *args,
            ],
            cwd=cwd or self.repo,
            env=self.git_env(),
            timeout=RUN_CAP,
        )
        if check:
            self.assertEqual(
                proc.returncode,
                0,
                f"git {args}: {proc.stderr or proc.stdout}",
            )
        return proc

    def env(
        self,
        *,
        agent: str | None = AGENT,
        lane_conf: str | bool | None = True,
        extra: dict[str, str] | None = None,
    ) -> dict[str, str]:
        extra_env: dict[str, str] = {"TZ": "UTC"}
        if agent is not None:
            extra_env["WF_AGENT"] = agent
        extra_env["LANE_CONF"] = str(self.lane_conf_path)
        if lane_conf is True:
            extra_env["FIXTURE_COPY_LIST"] = str(self.conf_path)
        elif isinstance(lane_conf, str):
            extra_env["FIXTURE_COPY_LIST"] = lane_conf
        if extra:
            extra_env.update(extra)
        env = self.world.env(extra_env)
        # World.env always sets XDG_STATE_HOME; the lever's default DIR
        # is under HOME when that name is unset, which this family pins.
        if "XDG_STATE_HOME" not in extra_env:
            env.pop("XDG_STATE_HOME", None)
            self.assertNotIn("XDG_STATE_HOME", env)
        # The lever's make-commit identity is WF_AGENT, not the world's.
        for name in [k for k in env if k.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_"))]:
            del env[name]
        return env

    def invoke(
        self,
        argv: list[str],
        *,
        agent: str | None = AGENT,
        lane_conf: str | bool | None = True,
        extra: dict[str, str] | None = None,
        timeout: int = RUN_CAP,
    ):
        env = self.env(agent=agent, lane_conf=lane_conf, extra=extra)
        if agent is None:
            self.assertNotIn("WF_AGENT", env)
        try:
            return run(
                [str(self.staged), *argv],
                cwd=self.repo,
                env=env,
                timeout=timeout,
            )
        except Timeout as exc:
            self.fail(f"fixture.sh {argv} exceeded {timeout}s: {exc}")

    def assert_refusal(self, proc, class_name: str) -> None:
        assert_refusal_wire(self, proc, class_name)

    def write_conf(self, paths: list[str]) -> Path:
        text = "".join(f"{path}\n" for path in paths)
        self._plant_bytes(self.conf_path, text.encode("utf-8"))
        return self.conf_path

    def seed_repo(self) -> None:
        if self.seeded:
            return
        init = self.git("init", "-b", BRANCH, cwd=self.repo, check=False)
        self.assertEqual(init.returncode, 0, init.stderr)
        self.git("config", "user.name", REPO_USER)
        self.git("config", "user.email", REPO_EMAIL)
        self._plant_synth_app()
        copy_path = self.repo / self.copy_rel
        self._plant_bytes(copy_path, COPY_PAYLOAD.encode("utf-8"))
        extra = self.repo / "extra-tracked.txt"
        self._plant_bytes(extra, EXTRA_PAYLOAD.encode("utf-8"))
        gitignore = self.repo / ".gitignore"
        self._plant_bytes(gitignore, b"secret.env\n")
        ignored = self.repo / "secret.env"
        self._plant_bytes(ignored, IGNORED_PAYLOAD.encode("utf-8"))
        self.assertTrue(ignored.is_file())
        self.assertEqual(ignored.read_text(encoding="utf-8"), IGNORED_PAYLOAD)
        self.write_conf([self.copy_rel])
        self.git("add", "-A")
        self.git("commit", "-m", f"seed synth app\n\n{TRAILER}")
        tracked = self.git("ls-files").stdout.splitlines()
        self.assertIn(self.copy_rel, tracked)
        self.assertIn("extra-tracked.txt", tracked)
        self.assertNotIn("secret.env", tracked)
        self.assertEqual(self.git("status", "--porcelain").stdout, "")
        self.seeded = True

    def add_linked_worktree(self) -> Path:
        self.seed_repo()
        linked = self.root / "linked-wt"
        added = self.git(
            "worktree", "add", "--detach", str(linked), check=False
        )
        self.assertEqual(
            added.returncode,
            0,
            f"INVALID: git worktree add failed: {added.stderr}",
        )
        common = self.git(
            "rev-parse", "--git-common-dir", cwd=linked
        ).stdout.strip()
        main_common = self.git("rev-parse", "--git-common-dir").stdout.strip()
        common_path = Path(common)
        if not common_path.is_absolute():
            common_path = (linked / common_path).resolve()
        else:
            common_path = common_path.resolve()
        main_path = Path(main_common)
        if not main_path.is_absolute():
            main_path = (self.repo / main_path).resolve()
        else:
            main_path = main_path.resolve()
        self.assertEqual(
            common_path,
            main_path,
            "linked worktree must share the invoking repository common-dir",
        )
        self._linked = linked
        return linked

    def _plant_synth_app(self) -> None:
        app = self.app_root
        cli_path = app / "src" / "cli.py"
        readme_path = app / "README.md"
        self._plant_bytes(cli_path, CLI_PLANTED.encode("utf-8"))
        self._plant_bytes(readme_path, README_TEXT.encode("utf-8"))
        self._plant_bytes(
            app / "probes" / "syn-01.sh", PROBE_01.encode("utf-8")
        )
        self._plant_bytes(
            app / "probes" / "syn-02.sh", PROBE_02.encode("utf-8")
        )
        patch_01 = _unified_patch("src/cli.py", CLI_PLANTED, CLI_GREET_FIXED)
        patch_02 = _unified_patch("src/cli.py", CLI_PLANTED, CLI_LOAD_FIXED)
        self.assertIn(MARKER_01, CLI_PLANTED)
        self.assertNotIn(MARKER_01, CLI_GREET_FIXED)
        self.assertIn(FIXED_01, CLI_GREET_FIXED)
        self.assertNotIn(FIXED_01, CLI_PLANTED)
        self.assertIn(MARKER_02, CLI_PLANTED)
        self.assertNotIn(MARKER_02, CLI_LOAD_FIXED)
        self.assertIn(FIXED_02, CLI_LOAD_FIXED)
        self.assertNotIn(FIXED_02, CLI_PLANTED)
        self.assertIn(MARKER_01, patch_01)
        self.assertIn(FIXED_01, patch_01)
        self.assertIn(MARKER_02, patch_02)
        self.assertIn(FIXED_02, patch_02)
        self._plant_bytes(
            app / "fixes" / "syn-01.patch", patch_01.encode("utf-8")
        )
        self._plant_bytes(
            app / "fixes" / "syn-02.patch", patch_02.encode("utf-8")
        )
        loc_01 = _line_of(CLI_PLANTED, MARKER_01)
        loc_02 = _line_of(CLI_PLANTED, MARKER_02)
        neigh_01 = _line_of(CLI_PLANTED, "def greet_argv")
        neigh_02 = _line_of(CLI_PLANTED, "def load_rows_strict")
        dec_01 = _line_of(README_TEXT, DECLARED_01)
        dec_02 = _line_of(README_TEXT, DECLARED_02)
        practiced_01 = [
            line
            for line in CLI_PLANTED.splitlines()
            if MARKER_01 in line
        ][0].strip()
        practiced_02 = [
            line
            for line in CLI_PLANTED.splitlines()
            if MARKER_02 in line
        ][0].strip()
        manifest = {
            "name": APP,
            "check": "python3 -c 'print(0)'",
            "faults": [
                {
                    "id": "SYN-01",
                    "kind": "K1",
                    "severity": "p1",
                    "location": {
                        "path": "src/cli.py",
                        "lines": [loc_01, loc_01],
                    },
                    "declared": {
                        "text": DECLARED_01,
                        "path": "README.md",
                        "lines": [dec_01, dec_01],
                    },
                    "practiced": {
                        "text": practiced_01,
                        "path": "src/cli.py",
                        "lines": [loc_01, loc_01],
                    },
                    "finding": (
                        "untrusted name is interpolated into a shell line"
                    ),
                    "neighbour": {
                        "path": "src/cli.py",
                        "lines": [neigh_01, neigh_01],
                        "why_clean": "greet_argv passes name as argv",
                    },
                    "probe": "sh probes/syn-01.sh",
                    "fix": "fixes/syn-01.patch",
                    "visible_to": "audit",
                },
                {
                    "id": "SYN-02",
                    "kind": "K5",
                    "severity": "p2",
                    "location": {
                        "path": "src/cli.py",
                        "lines": [loc_02, loc_02],
                    },
                    "declared": {
                        "text": DECLARED_02,
                        "path": "README.md",
                        "lines": [dec_02, dec_02],
                    },
                    "practiced": {
                        "text": practiced_02,
                        "path": "src/cli.py",
                        "lines": [loc_02, loc_02],
                    },
                    "finding": (
                        "load_rows swallows every exception and returns empty"
                    ),
                    "neighbour": {
                        "path": "src/cli.py",
                        "lines": [neigh_02, neigh_02],
                        "why_clean": "load_rows_strict lets errors propagate",
                    },
                    "probe": "sh probes/syn-02.sh",
                    "fix": "fixes/syn-02.patch",
                    "visible_to": "test",
                },
            ],
        }
        payload = json.dumps(manifest, indent=2) + "\n"
        self._plant_bytes(
            app / "FAULTS.json", payload.encode("utf-8")
        )
        loaded = json.loads((app / "FAULTS.json").read_text(encoding="utf-8"))
        self.assertEqual(len(loaded["faults"]), 2)
        self.assertEqual(loaded["faults"][0]["id"], "SYN-01")

    def plant_made_dir(self, name: str = "made-scratch") -> Path:
        self._plant_synth_app()
        made = self.root / name
        if made.exists():
            shutil.rmtree(made)
        shutil.copytree(self.app_root, made)
        self.assertTrue((made / "FAULTS.json").is_file())
        self.assertTrue((made / "src" / "cli.py").is_file())
        self.assertTrue((made / "probes" / "syn-01.sh").is_file())
        self.assertTrue((made / "fixes" / "syn-01.patch").is_file())
        return made

    def plant_made_repo(self, name: str = "made-repo") -> Path:
        made = self.plant_made_dir(name)
        init = self.git("init", "-b", BRANCH, cwd=made, check=False)
        self.assertEqual(init.returncode, 0, init.stderr)
        self.git("config", "user.name", REPO_USER, cwd=made)
        self.git("config", "user.email", REPO_EMAIL, cwd=made)
        self.git("add", "-A", cwd=made)
        self.git("commit", "-m", f"planted made repo\n\n{TRAILER}", cwd=made)
        return made

    def make(self, app: str = APP, target: Path | None = None, **kwargs):
        argv = ["make", app]
        if target is not None:
            argv.append(str(target))
        return self.invoke(argv, **kwargs)

    def parse_dir(self, proc) -> Path:
        lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
        self.assertEqual(
            len(lines),
            1,
            f"make prints one directory on stdout: {proc.stdout!r} "
            f"stderr={proc.stderr!r}",
        )
        path = Path(lines[0].strip())
        self.assertTrue(path.is_dir(), f"printed DIR is not a directory: {path}")
        return path

    def parse_prove(self, stdout: str) -> dict[str, str]:
        rows: dict[str, str] = {}
        for line in stdout.splitlines():
            colour_match = COLOUR_RE.search(line)
            if not colour_match:
                continue
            colour = colour_match.group(1)
            fault_id = None
            for token in line.replace("\t", " ").split():
                if token in {"SYN-01", "SYN-02"}:
                    fault_id = token
                    break
            self.assertIsNotNone(
                fault_id,
                f"colour line names a fault id: {line!r}",
            )
            self.assertNotIn(fault_id, rows, f"duplicate colour for {fault_id}")
            rows[fault_id] = colour
        return rows

    def parse_counts(self, stdout: str) -> dict[str, int]:
        match = COUNTS_RE.search(stdout)
        self.assertIsNotNone(
            match,
            f"prove prints red= green= unreachable= counts: {stdout!r}",
        )
        return {key: int(value) for key, value in match.groupdict().items()}


class TheLeverFile(unittest.TestCase):
    def test_the_lever_file_is_present_and_executable(self):
        self.assertTrue(
            LEVER.is_file(),
            f"{LEVER} is missing; the contract requires ops/bin/fixture.sh",
        )
        self.assertTrue(
            os.access(LEVER, os.X_OK),
            f"{LEVER} must be executable",
        )


class RefusalShape(_LeverFixture):
    def test_embedded_class_token_is_not_a_refusal(self):
        proc = Result(
            argv=("fixture.sh",), duration=0.0, cwd="",
            exit=2,
            stdout="",
            stderr="not-identity garbage expected x; found y; needed z\n",
        )
        with self.assertRaises(AssertionError):
            self.assert_refusal(proc, "identity")

    def test_abort_exit_is_not_a_refusal(self):
        proc = Result(
            argv=("fixture.sh",), duration=0.0, cwd="",
            exit=3,
            stdout="",
            stderr=(
                "identity: expected Name <address>; found unset; "
                "needed export WF_AGENT\n"
            ),
        )
        with self.assertRaises(AssertionError):
            self.assert_refusal(proc, "identity")


class MakeRefuses(_LeverFixture):
    def test_make_refuses_identity_without_wf_agent(self):
        self.seed_repo()
        proc = self.make(agent=None)
        self.assert_refusal(proc, "identity")

    def test_make_refuses_identity_on_malformed_agent(self):
        self.seed_repo()
        proc = self.make(agent=MALFORMED_AGENT)
        self.assert_refusal(proc, "identity")

    def test_make_refuses_conf_when_lane_conf_is_unset(self):
        self.seed_repo()
        proc = self.make(lane_conf=None)
        self.assert_refusal(proc, "conf")
        err = (proc.stderr or "").upper()
        self.assertIn("FIXTURE_COPY_LIST", err)

    def test_make_refuses_conf_when_lane_conf_file_is_missing(self):
        self.seed_repo()
        missing = self.root / "no-such-lane.conf"
        self.assertFalse(missing.exists())
        proc = self.make(lane_conf=str(missing))
        self.assert_refusal(proc, "conf")

    def test_make_refuses_conf_when_the_copy_list_is_empty(self):
        self.seed_repo()
        self.write_conf([])
        self.assertEqual(self.conf_path.read_text(encoding="utf-8"), "")
        proc = self.make()
        self.assert_refusal(proc, "conf")

    def test_make_refuses_app_when_manifest_is_absent(self):
        self.seed_repo()
        proc = self.make("no-such-app")
        self.assert_refusal(proc, "app")

    def test_make_refuses_target_when_dir_is_inside_the_invoking_worktree(self):
        self.seed_repo()
        inside = self.repo / "inside-worktree"
        proc = self.make(target=inside)
        self.assert_refusal(proc, "target")
        self.assertFalse(
            inside.exists(),
            "a refused make leaves no directory behind",
        )

    def test_make_refuses_target_inside_a_linked_worktree(self):
        linked = self.add_linked_worktree()
        inside = linked / "nested-target"
        proc = self.make(target=inside)
        self.assert_refusal(proc, "target")
        self.assertFalse(
            inside.exists(),
            "a refused make leaves no directory behind",
        )

    def test_make_refuses_target_when_dir_is_the_linked_worktree_root(self):
        linked = self.add_linked_worktree()
        proc = self.make(target=linked)
        self.assert_refusal(proc, "target")

    def test_make_refuses_conf_when_a_listed_path_is_missing_and_leaves_no_dir(
        self,
    ):
        self.seed_repo()
        self.write_conf([self.copy_rel, "ops/bin/missing-listed.txt"])
        target = self.root / "missing-listed-out"
        self.assertFalse(target.exists())
        proc = self.make(target=target)
        self.assert_refusal(proc, "conf")
        self.assertFalse(
            target.exists(),
            "a make that cannot prove its copy leaves no directory behind",
        )

    def test_make_refuses_when_an_ignored_path_is_on_the_copy_list(self):
        self.seed_repo()
        self.write_conf([self.copy_rel, "secret.env"])
        target = self.root / "ignored-on-list"
        self.assertTrue((self.repo / "secret.env").is_file())
        tracked = self.git("ls-files").stdout.splitlines()
        self.assertNotIn("secret.env", tracked)
        proc = self.make(target=target)
        self.assert_refusal(proc, "conf")
        self.assertFalse(
            target.exists(),
            "an ignored listed path must not be copied; make leaves no dir",
        )

    def test_make_refuses_when_an_untracked_path_is_on_the_copy_list(self):
        self.seed_repo()
        untracked_rel = "ops/bin/untracked-copy.txt"
        untracked = self.repo / untracked_rel
        self._plant_bytes(untracked, UNTRACKED_PAYLOAD.encode("utf-8"))
        tracked = self.git("ls-files").stdout.splitlines()
        self.assertNotIn(untracked_rel, tracked)
        self.write_conf([self.copy_rel, untracked_rel])
        target = self.root / "untracked-on-list"
        proc = self.make(target=target)
        self.assert_refusal(proc, "conf")
        self.assertFalse(
            target.exists(),
            "an untracked listed path must not ride; make leaves no dir",
        )


class MakeCopies(_LeverFixture):
    def test_make_prints_one_directory_and_proves_the_copy_both_ways(self):
        self.seed_repo()
        target = self.root / "made-out"
        proc = self.make(target=target)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        made = self.parse_dir(proc)
        self.assertEqual(made.resolve(), target.resolve())
        present = _rel_files(made)
        self.assertIn("FAULTS.json", present)
        self.assertIn("src/cli.py", present)
        self.assertIn("probes/syn-01.sh", present)
        self.assertIn("fixes/syn-01.patch", present)
        self.assertIn(self.copy_rel, present)
        self.assertEqual(
            (made / self.copy_rel).read_text(encoding="utf-8"),
            COPY_PAYLOAD,
        )
        self.assertNotIn("secret.env", present)
        self.assertNotIn("extra-tracked.txt", present)
        self.assertFalse(
            any("IGNORED_MUST_NOT_RIDE" in name for name in present)
        )
        app_files = _rel_files(self.app_root)
        expected = app_files | {self.copy_rel}
        self.assertEqual(
            present,
            expected,
            "copy proof both ways: app files plus the copy list, nothing else",
        )

    def test_make_commits_once_with_trailers_from_wf_agent(self):
        self.seed_repo()
        target = self.root / "made-commit"
        proc = self.make(target=target)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        made = self.parse_dir(proc)
        author = self.git("log", "-1", "--format=%an <%ae>", cwd=made)
        committer = self.git("log", "-1", "--format=%cn <%ce>", cwd=made)
        self.assertEqual(author.returncode, 0, author.stderr)
        self.assertEqual(committer.returncode, 0, committer.stderr)
        self.assertEqual(author.stdout.strip(), AGENT)
        self.assertEqual(committer.stdout.strip(), AGENT)
        shas = self.git("rev-list", "--count", "HEAD", cwd=made).stdout.strip()
        self.assertEqual(shas, "1", "make commits once")
        body = self.git("log", "-1", "--format=%B", cwd=made)
        self.assertEqual(body.returncode, 0, body.stderr)
        parsed = run(
            ["git", "interpret-trailers", "--parse"],
            cwd=made,
            env=self.git_env(),
            stdin=body.stdout,
            timeout=RUN_CAP,
        )
        self.assertEqual(parsed.returncode, 0, parsed.stderr)
        trailers = parsed.stdout
        self.assertRegex(
            trailers,
            r"(?m)^Source: \S",
            f"Source trailer missing: {trailers!r}",
        )
        self.assertRegex(
            trailers,
            rf"(?m)^Co-Authored-By: {re.escape(AGENT)}$",
            f"Co-Authored-By must be the WF_AGENT identity: {trailers!r}",
        )

    def test_make_without_dir_uses_state_home_and_prints_it(self):
        self.seed_repo()
        proc = self.make()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        made = self.parse_dir(proc)
        parent = self.home / ".local" / "state" / "agentscient" / "fixtures"
        self.assertTrue(
            str(made.resolve()).startswith(str(parent.resolve())),
            f"default DIR {made} is under {parent}",
        )
        self.assertTrue(made.is_dir())

    def test_make_without_dir_uses_xdg_state_home_when_set(self):
        self.seed_repo()
        xdg = self.root / "xdg-state"
        xdg.mkdir()
        proc = self.make(extra={"XDG_STATE_HOME": str(xdg)})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        made = self.parse_dir(proc)
        parent = xdg / "agentscient" / "fixtures"
        self.assertTrue(
            str(made.resolve()).startswith(str(parent.resolve())),
            f"default DIR {made} is under XDG_STATE_HOME {parent}",
        )
        home_default = self.home / ".local" / "state" / "agentscient" / "fixtures"
        self.assertFalse(
            str(made.resolve()).startswith(str(home_default.resolve())),
            "XDG_STATE_HOME must win over HOME for the default DIR",
        )

    def test_two_makes_without_dir_get_distinct_directories(self):
        self.seed_repo()
        first = self.parse_dir(self.make())
        second = self.parse_dir(self.make())
        self.assertNotEqual(first.resolve(), second.resolve())
        self.assertTrue(first.is_dir())
        self.assertTrue(second.is_dir())


class ProveColours(_LeverFixture):
    def test_prove_refuses_manifest_when_faults_json_is_absent(self):
        empty = self.root / "no-manifest"
        empty.mkdir()
        proc = self.invoke(["prove", str(empty)], agent=None)
        self.assert_refusal(proc, "manifest")

    def test_prove_reports_every_probe_red_on_a_fresh_make(self):
        made = self.plant_made_dir()
        proc = self.invoke(["prove", str(made)], agent=None)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        rows = self.parse_prove(proc.stdout)
        self.assertEqual(set(rows), {"SYN-01", "SYN-02"})
        self.assertEqual(rows["SYN-01"], "RED")
        self.assertEqual(rows["SYN-02"], "RED")
        counts = self.parse_counts(proc.stdout)
        self.assertEqual(counts, {"red": 2, "green": 0, "unreachable": 0})

    def test_prove_exits_1_when_any_fresh_probe_is_the_wrong_colour(self):
        made = self.plant_made_dir()
        probe = made / "probes" / "syn-01.sh"
        always_green = b"#!/bin/sh\nexit 0\n"
        self._plant_bytes(probe, always_green)
        self.assertEqual(probe.read_bytes(), always_green)
        proc = self.invoke(["prove", str(made)], agent=None)
        self.assertEqual(proc.returncode, 1, proc.stderr or proc.stdout)
        rows = self.parse_prove(proc.stdout)
        self.assertEqual(rows["SYN-01"], "GREEN")
        self.assertEqual(rows["SYN-02"], "RED")
        counts = self.parse_counts(proc.stdout)
        self.assertEqual(counts["green"], 1)
        self.assertEqual(counts["red"], 1)
        self.assertEqual(counts["unreachable"], 0)

    def test_prove_fixed_reports_every_probe_green_without_identity(self):
        made = self.plant_made_repo()
        proc = self.invoke(["prove", "--fixed", str(made)], agent=None)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        err = (proc.stderr or "").lower()
        self.assertNotIn("identity", err)
        rows = self.parse_prove(proc.stdout)
        self.assertEqual(set(rows), {"SYN-01", "SYN-02"})
        self.assertEqual(rows["SYN-01"], "GREEN")
        self.assertEqual(rows["SYN-02"], "GREEN")
        counts = self.parse_counts(proc.stdout)
        self.assertEqual(counts, {"red": 0, "green": 2, "unreachable": 0})
        cli = (made / "src" / "cli.py").read_text(encoding="utf-8")
        self.assertNotIn(MARKER_01, cli)
        self.assertNotIn(MARKER_02, cli)
        self.assertIn(FIXED_01, cli)
        self.assertIn(FIXED_02, cli)

    def test_prove_fixed_exits_1_when_a_probe_stays_red(self):
        made = self.plant_made_repo()
        always_red = b"#!/bin/sh\nexit 1\n"
        probe = made / "probes" / "syn-01.sh"
        self._plant_bytes(probe, always_red)
        self.assertEqual(probe.read_bytes(), always_red)
        proc = self.invoke(["prove", "--fixed", str(made)], agent=None)
        self.assertEqual(proc.returncode, 1, proc.stderr or proc.stdout)
        rows = self.parse_prove(proc.stdout)
        self.assertEqual(rows["SYN-01"], "RED")
        self.assertEqual(rows["SYN-02"], "GREEN")
        counts = self.parse_counts(proc.stdout)
        self.assertEqual(counts["red"], 1)
        self.assertEqual(counts["green"], 1)
        self.assertEqual(counts["unreachable"], 0)

    def test_prove_fixed_refuses_fix_when_a_patch_does_not_apply(self):
        made = self.plant_made_repo()
        bad = (
            b"diff --git a/no-such-synth.py b/no-such-synth.py\n"
            b"--- a/no-such-synth.py\n"
            b"+++ b/no-such-synth.py\n"
            b"@@ -1 +1 @@\n"
            b"-old\n"
            b"+new\n"
        )
        patch = made / "fixes" / "syn-01.patch"
        self._plant_bytes(patch, bad)
        self.assertEqual(patch.read_bytes(), bad)
        proc = self.invoke(["prove", "--fixed", str(made)], agent=None)
        self.assert_refusal(proc, "fix")

    def test_prove_spawn_failure_is_unreachable_never_a_colour(self):
        made = self.plant_made_dir()
        manifest_path = made / "FAULTS.json"
        before = manifest_path.read_bytes()
        manifest = json.loads(before.decode("utf-8"))
        manifest["faults"][0]["probe"] = "./probes/does-not-exist-syn"
        planted = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
        self._plant_bytes(manifest_path, planted)
        self.assertEqual(manifest_path.read_bytes(), planted)
        self.assertNotEqual(manifest_path.read_bytes(), before)
        proc = self.invoke(["prove", str(made)], agent=None)
        self.assertEqual(proc.returncode, 1, proc.stderr or proc.stdout)
        rows = self.parse_prove(proc.stdout)
        self.assertEqual(rows["SYN-01"], "UNREACHABLE")
        self.assertEqual(rows["SYN-02"], "RED")
        self.assertNotEqual(rows["SYN-01"], "RED")
        self.assertNotEqual(rows["SYN-01"], "GREEN")
        counts = self.parse_counts(proc.stdout)
        self.assertEqual(counts["unreachable"], 1)
        self.assertEqual(counts["red"], 1)
        self.assertEqual(counts["green"], 0)

    def test_prove_timeout_is_unreachable_never_a_colour(self):
        made = self.plant_made_dir()
        sleeper = b"#!/bin/sh\nexec sleep 3600\n"
        probe = made / "probes" / "syn-01.sh"
        self._plant_bytes(probe, sleeper)
        self.assertEqual(probe.read_bytes(), sleeper)
        proc = self.invoke(
            ["prove", str(made)],
            agent=None,
            timeout=PROBE_WALL,
        )
        self.assertEqual(proc.returncode, 1, proc.stderr or proc.stdout)
        self.assertNotEqual(proc.returncode, 3, "timeout is UNREACHABLE, not abort")
        rows = self.parse_prove(proc.stdout)
        self.assertEqual(rows["SYN-01"], "UNREACHABLE")
        self.assertNotEqual(rows["SYN-01"], "RED")
        self.assertNotEqual(rows["SYN-01"], "GREEN")
        counts = self.parse_counts(proc.stdout)
        self.assertGreaterEqual(counts["unreachable"], 1)

    def test_prove_strips_identity_and_pins_locale_and_time(self):
        made = self.plant_made_dir()
        proc = self.invoke(
            ["prove", str(made)],
            agent=AGENT,
            extra={
                "LC_ALL": "fr_FR.UTF-8",
                "LANG": "fr_FR.UTF-8",
                "LANGUAGE": "fr_FR",
                "TZ": "Europe/Paris",
            },
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        dump_path = made / "probes" / "env-dump.txt"
        self.assertTrue(
            dump_path.is_file(),
            "the SYN-01 probe dumps its environment",
        )
        dump = dump_path.read_text(encoding="utf-8")
        self.assertNotIn("WF_AGENT", dump)
        self.assertIn("LC_ALL=C", dump)
        self.assertIn("LANG=C", dump)
        self.assertNotIn("fr_FR", dump)
        self.assertIn("TZ=UTC", dump)
        self.assertNotIn("Europe/Paris", dump)
        offset_path = made / "probes" / "tz-offset.txt"
        self.assertTrue(offset_path.is_file(), "the SYN-01 probe dumps date +%z")
        self.assertEqual(offset_path.read_text(encoding="utf-8").strip(), "+0000")

    def test_prove_does_not_require_identity(self):
        made = self.plant_made_dir()
        proc = self.invoke(["prove", str(made)], agent=None)
        err = (proc.stderr or "").lower()
        self.assertNotIn("identity", err)
        rows = self.parse_prove(proc.stdout)
        self.assertEqual(set(rows), {"SYN-01", "SYN-02"})
        self.assertEqual(rows["SYN-01"], "RED")
        self.assertEqual(rows["SYN-02"], "RED")


class GradeRefusesOnTheLever(_LeverFixture):
    def test_grade_refuses_identity_without_wf_agent(self):
        rec = self.root / "missing-record.json"
        proc = self.invoke(["grade", str(rec), APP], agent=None)
        self.assert_refusal(proc, "identity")

    def test_grade_refuses_record_when_the_file_is_absent(self):
        rec = self.root / "missing-record.json"
        self.assertFalse(rec.exists())
        proc = self.invoke(["grade", str(rec), APP])
        self.assert_refusal(proc, "record")

    def test_grade_refuses_app_when_the_manifest_is_absent(self):
        rec = self.root / "review.json"
        payload = json.dumps(
            {
                "id": "rec-1",
                "job": "security-audit",
                "harness": {"name": "grok"},
                "model": {"ran": "grok-4.6"},
                "result": {"envelope": {"findings": []}},
            }
        ) + "\n"
        self._plant_bytes(rec, payload.encode("utf-8"))
        self.assertEqual(rec.read_bytes(), payload.encode("utf-8"))
        proc = self.invoke(["grade", str(rec), "no-such-app"])
        self.assert_refusal(proc, "app")


if __name__ == "__main__":
    unittest.main()
