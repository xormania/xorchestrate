"""Branch-census acceptance tests, written from the contracts.

Contracts (only): `ops/conductor/branch-census.md` and the inherited
`ops/conductor/instrument-conventions.md`. The tool
`ops/conductor/tools/branches.py` is the subject and is not read here.
Tests invoke it through the test kit inside a scratch git repository.

This repo must stay untouched — asserted in tearDown. The live,
host-shared dispatch store is not watched (LD-80): every invocation of
the staged tool goes through self.env()/self.world.env(), which carries
HOME and XDG_STATE_HOME redirected to this World's own tree, so the
subject cannot reach the real host's dispatch directory. Watching it
proved nothing about the subject and only failed when another session's
own dispatch wrote its own launcher entry mid-run (#250 run
35095709472, Stacked.test_census_exits_1_when_a_stacked_row_exists,
against 20260916T122524Z-review-codex-05a9f5).

The implementation lands in parallel. A missing tool file is one red
(`test_the_tool_file_is_present`). Every other case uses World.stage,
whose absent-subject stand-in lets each assertion go red on the contract
promise rather than Python's missing-file exit 2.
"""

from __future__ import annotations

import json
import os
import re
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ops.testkit import World, run

REPO = Path(__file__).resolve().parents[3]
TOOL = REPO / "ops" / "conductor" / "tools" / "branches.py"
# The tool imports these beside itself (instrument-conventions.md §The
# shared implementation); a staged copy run from a scratch directory
# needs them staged too, or it refuses `tooling` before the contract.
SIBLINGS = ("instrument.py", "laneconf.py")

AGENT = "Branch Census Tester <branches-tester@example.invalid>"
MALFORMED_AGENT = "JustAName"
REPO_USER = "Repo Config"
REPO_EMAIL = "repo-config@example.invalid"
RUN_CAP = 30

UNIT_MERGED = "conductor/merged-kept"
UNIT_ACTIVE = "conductor/active-pr"
UNIT_STRAY = "conductor/stray-old"
UNIT_IDLE = "conductor/idle-young"
UNKNOWN = "unshaped-hotfix"
STACK_HEAD = "conductor/stacked"
STACK_BASE = "conductor/other"

REC_OLD = "20260801T000000Z-old-closed"
REC_YOUNG = "20260825T000000Z-young-closed"
REC_UNLANDED = "20260801T000000Z-unlanded"
REC_OPEN = "20260801T000000Z-old-open"

GH_ERROR_L1 = "GH_ERROR_LINE1: boom"
GH_ERROR_L2 = "GH_ERROR_LINE2: still boom"

STACKED_RE = re.compile(
    r"^STACKED #(\d+) base=(\S+) retarget before merge$"
)
RETIRED_RE = re.compile(r"retired n=(\d+)")

# PIN_CENSUS_LINE: one line names the origin branch (dev or origin/dev),
# the role token, the state token, `behind dev by N` when the contract
# quotes that phrase, and the feeds edge (`-> dev` / `-> main`).
# PIN_SORT: strays and unknowns first, then active, then idle, then
# merged-kept — the contract's order, applied to the planted units.
# PIN_GH_PR: `gh pr list --json` includes `baseRefName` (named) plus
# `headRefName` and `number` so census can match an open PR to a unit
# and stacked can print `STACKED #N base=<b> retarget before merge`.
# PIN_STORE: record dir named by id under the dispatch store; running =
# no `exit` file; closed = `exit` file present (onboard's store shape).
# PIN_LANDED: patch-present on origin/dev via
# `rev-list --right-only --cherry-pick` (the land lever's reading), not
# ancestry — the planted original sha is not an ancestor of origin/dev.
# PIN_WINDOW: transport/retire `--window` is a positive integer of days;
# default retention is 30. Census stray default is 14.
# PIN_ARGUMENTS: extra positional argv is class `arguments` (the brief's
# unexpected-arguments case; the token is not restated in the census
# table, same shape as onboard).


def days_ago(days: int) -> str:
    t = datetime.now(timezone.utc) - timedelta(days=days)
    return t.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%S +0000")


class _CensusFixture(unittest.TestCase):
    """Scratch HOME + git repo, staged tool copy, stub gh, live store frozen."""

    def setUp(self):
        self.world = World(prefix="branch-census-")
        self.addCleanup(self.world.close)
        self.world.require("git")
        repo_env = self.world.env()
        self._head_before = run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO,
            env=repo_env,
            timeout=RUN_CAP,
            expect=0,
        ).stdout.strip()
        self._diff_before = run(
            ["git", "diff", "HEAD"],
            cwd=REPO,
            env=repo_env,
            timeout=RUN_CAP,
            expect=0,
        ).stdout
        self._dispatch_refs_before = run(
            ["git", "for-each-ref", "refs/dispatch"],
            cwd=REPO,
            env=repo_env,
            timeout=RUN_CAP,
            expect=0,
        ).stdout
        self._archive_refs_before = run(
            ["git", "for-each-ref", "refs/archive"],
            cwd=REPO,
            env=repo_env,
            timeout=RUN_CAP,
            expect=0,
        ).stdout
        root = self.world.root
        self.root = root
        self.home = self.world.home
        self.scratch = self.world.cwd
        self.bin = self.world.bin
        self.repo = root / "repo"
        self.xdg = self.world.xdg_state
        self.repo.mkdir()
        self.store = self.xdg / "agentscient" / "dispatch"
        self.home_store = (
            self.home / ".local" / "state" / "agentscient" / "dispatch"
        )
        self.assertFalse(
            self.store.exists(),
            "plant: isolated XDG store must start absent",
        )
        self.assertFalse(
            self.home_store.exists(),
            "plant: isolated HOME store must start absent",
        )
        self.gh = self.bin / "gh"
        self._stage_tool_bytes()
        self.assertEqual(self.world.tools.calls("gh"), [])
        self.assertFalse(self.gh.exists(), "plant: gh stub is not planted yet")
        self.plant_gh_prs([])
        self.base = ""
        self.dev_sha = ""
        self.main_sha = ""
        self.mid_sha = ""

    def tearDown(self):
        repo_env = self.world.env()
        head_after = run(
            ["git", "rev-parse", "HEAD"], cwd=REPO, env=repo_env,
            timeout=RUN_CAP, expect=0,
        ).stdout.strip()
        self.assertEqual(head_after, self._head_before, "real repo HEAD moved")
        diff_after = run(
            ["git", "diff", "HEAD"], cwd=REPO, env=repo_env,
            timeout=RUN_CAP, expect=0,
        ).stdout
        self.assertEqual(
            diff_after, self._diff_before, "real repo tracked files mutated"
        )
        refs_after = run(
            ["git", "for-each-ref", "refs/dispatch"], cwd=REPO,
            env=repo_env, timeout=RUN_CAP, expect=0,
        ).stdout
        self.assertEqual(
            refs_after, self._dispatch_refs_before,
            "real repo gained a dispatch ref",
        )
        archive_after = run(
            ["git", "for-each-ref", "refs/archive"], cwd=REPO,
            env=repo_env, timeout=RUN_CAP, expect=0,
        ).stdout
        self.assertEqual(
            archive_after, self._archive_refs_before,
            "real repo gained an archive ref",
        )

    def _stage_tool_bytes(self) -> None:
        self.staged = self.world.stage(TOOL, siblings=SIBLINGS)
        self.assertTrue(self.staged.is_file())
        # The staged copy has no tracked conf beside its reader; the
        # fixture names one, so the lane under XDG_STATE_HOME is the one
        # asserted.
        self.conf = self.staged.parent.parent / "lane.conf"
        self.world.plant_text(self.conf, "LANE_NAME='agentscient'\n")

    def _plant_bytes(self, path: Path, data: bytes) -> None:
        self.world.plant_bytes(path, data)
        landed = path.read_bytes()
        self.assertEqual(landed, data, f"plant did not land intact: {path}")
        self.assertEqual(len(landed), len(data))

    def plant_gh_prs(
        self,
        prs: list[dict],
        *,
        exit_code: int = 0,
        stderr: str = "",
    ) -> None:
        # Real gh always emits isCrossRepository when the field is
        # requested; the stub models that guarantee. A fixture that
        # plants the key explicitly (even as None) is left untouched.
        prs = [dict(row) for row in prs]
        for row in prs:
            row.setdefault("isCrossRepository", False)
        self.world.tools.plant(
            "gh",
            stdout=json.dumps(prs) + "\n" if exit_code == 0 else "",
            stderr=stderr,
            exit=exit_code,
        )

    def git_env(self) -> dict[str, str]:
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
                proc.exit,
                0,
                f"git {args}: {proc.stderr or proc.stdout}",
            )
        return proc

    def sha(self, rev: str, cwd: Path | None = None) -> str:
        out = self.git("rev-parse", rev, cwd=cwd).stdout.strip()
        self.assertRegex(out, r"^[0-9a-f]{40}$", out)
        return out

    def commit_file(
        self,
        relpath: str,
        content: str,
        message: str,
        *,
        when: str | None = None,
    ) -> str:
        path = self.repo / relpath
        self._plant_bytes(path, content.encode("utf-8"))
        self.git("add", relpath)
        env = self.git_env()
        if when is not None:
            env["GIT_AUTHOR_DATE"] = when
            env["GIT_COMMITTER_DATE"] = when
        proc = run(
            [
                "git",
                "-c",
                f"user.name={REPO_USER}",
                "-c",
                f"user.email={REPO_EMAIL}",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-m",
                message,
            ],
            cwd=self.repo,
            env=env,
            timeout=RUN_CAP,
        )
        self.assertEqual(proc.exit, 0, proc.stderr)
        sha = self.sha("HEAD")
        if when is not None:
            got = self.git("log", "-1", "--format=%cI", sha).stdout.strip()
            prefix = when.split()[0]
            self.assertTrue(
                got.startswith(prefix),
                f"plant: committer {got!r} does not match {when!r}",
            )
        return sha

    def seed_trunks(self) -> dict[str, str]:
        init = self.git("init", "-b", "main", cwd=self.repo, check=False)
        self.assertEqual(init.exit, 0, init.stderr)
        self.git("config", "user.name", REPO_USER)
        self.git("config", "user.email", REPO_EMAIL)
        name = self.git("config", "--get", "user.name").stdout.strip()
        email = self.git("config", "--get", "user.email").stdout.strip()
        self.assertEqual(name, REPO_USER)
        self.assertEqual(email, REPO_EMAIL)
        self.main_sha = self.commit_file("trunk.txt", "seed\n", "seed main")
        self.git("update-ref", "refs/remotes/origin/main", self.main_sha)
        self.assertEqual(self.sha("refs/remotes/origin/main"), self.main_sha)
        self.git("checkout", "-b", "dev")
        self.mid_sha = self.commit_file("trunk.txt", "dev-1\n", "dev one")
        self.dev_sha = self.commit_file("trunk.txt", "dev-2\n", "dev two")
        self.git("update-ref", "refs/remotes/origin/dev", self.dev_sha)
        self.assertEqual(self.sha("origin/dev"), self.dev_sha)
        self.assertEqual(self.sha("origin/main"), self.main_sha)
        anc = self.git(
            "merge-base", "--is-ancestor", self.main_sha, "origin/dev"
        )
        self.assertEqual(anc.exit, 0, "plant: main is not under dev")
        head = self.git("symbolic-ref", "--short", "HEAD").stdout.strip()
        self.assertEqual(head, "dev")
        self.base = self.dev_sha
        return {
            "main": self.main_sha,
            "mid": self.mid_sha,
            "dev": self.dev_sha,
        }

    def plant_origin_branch(
        self,
        name: str,
        *,
        parent: str,
        filename: str,
        content: str,
        when: str | None = None,
    ) -> str:
        self.git("checkout", "-B", "tmp-plant", parent)
        sha = self.commit_file(filename, content, f"plant {name}", when=when)
        self.git("update-ref", f"refs/remotes/origin/{name}", sha)
        self.assertEqual(self.sha(f"refs/remotes/origin/{name}"), sha)
        self.git("checkout", "dev")
        self.git("branch", "-D", "tmp-plant")
        gone = self.git("rev-parse", "--verify", "tmp-plant", check=False)
        self.assertNotEqual(gone.exit, 0, "plant: tmp-plant still here")
        return sha

    def behind_dev(self, rev: str) -> int:
        out = self.git("rev-list", "--count", f"{rev}..origin/dev").stdout
        return int(out.strip())

    def is_ancestor(self, commit: str, of: str) -> bool:
        proc = self.git("merge-base", "--is-ancestor", commit, of, check=False)
        return proc.exit == 0

    def patch_leftover(self, tip: str, onto: str = "origin/dev") -> list[str]:
        out = self.git(
            "rev-list", "--right-only", "--cherry-pick", f"{onto}...{tip}"
        ).stdout
        return [ln.strip() for ln in out.splitlines() if ln.strip()]

    def plant_closed_record(self, rec_id: str, *, exit_code: str = "0") -> Path:
        path = self.store / rec_id
        path.mkdir(parents=True)
        exit_path = path / "exit"
        data = f"{exit_code}\n".encode("utf-8")
        self._plant_bytes(exit_path, data)
        self.assertTrue(path.is_dir(), "INVALID: record dir plant did not land")
        self.assertEqual(exit_path.read_bytes(), data)
        return path

    def plant_running_record(self, rec_id: str) -> Path:
        path = self.store / rec_id
        path.mkdir(parents=True)
        self.assertTrue(path.is_dir())
        self.assertFalse(
            (path / "exit").exists(),
            "plant: running record must have no exit file",
        )
        return path

    def plant_landed_dispatch(
        self,
        rec_id: str,
        *,
        filename: str,
        content: str,
        when: str | None = None,
        closed: bool = True,
    ) -> str:
        """Original sha is not an ancestor of origin/dev; the patch is."""
        self.git("checkout", "-B", "tmp-dispatch", "origin/dev")
        original = self.commit_file(
            filename, content, f"dispatch {rec_id}", when=when
        )
        self.git("checkout", "dev")
        self.git("reset", "--hard", "origin/dev")
        self.git("cherry-pick", original)
        landed = self.sha("HEAD")
        self.assertNotEqual(landed, original)
        self.git("update-ref", "refs/remotes/origin/dev", landed)
        self.dev_sha = landed
        self.git("branch", "-D", "tmp-dispatch")
        self.git("update-ref", f"refs/dispatch/{rec_id}", original)
        self.assertEqual(self.sha(f"refs/dispatch/{rec_id}"), original)
        self.assertFalse(
            self.is_ancestor(original, "origin/dev"),
            "plant: original sha is an ancestor; need cherry equivalent",
        )
        leftover = self.patch_leftover(original)
        self.assertEqual(
            leftover,
            [],
            f"plant: patch not present on origin/dev: {leftover}",
        )
        if closed:
            self.plant_closed_record(rec_id)
            self.assertTrue((self.store / rec_id / "exit").is_file())
            if when is not None:
                stamp = datetime.strptime(
                    when, "%Y-%m-%dT%H:%M:%S %z"
                ).timestamp()
                os.utime(self.store / rec_id / "exit", (stamp, stamp))
        else:
            self.plant_running_record(rec_id)
            self.assertFalse((self.store / rec_id / "exit").exists())
        return original

    def plant_unlanded_dispatch(
        self,
        rec_id: str,
        *,
        filename: str,
        content: str,
        when: str | None = None,
        closed: bool = True,
    ) -> str:
        self.git("checkout", "-B", "tmp-dispatch", "origin/dev")
        original = self.commit_file(
            filename, content, f"dispatch {rec_id}", when=when
        )
        self.git("update-ref", f"refs/dispatch/{rec_id}", original)
        self.git("checkout", "dev")
        self.git("reset", "--hard", "origin/dev")
        self.git("branch", "-D", "tmp-dispatch")
        leftover = self.patch_leftover(original)
        self.assertTrue(
            leftover,
            "plant: unlanded patch is already present on origin/dev",
        )
        if closed:
            self.plant_closed_record(rec_id)
            if when is not None:
                stamp = datetime.strptime(
                    when, "%Y-%m-%dT%H:%M:%S %z"
                ).timestamp()
                os.utime(self.store / rec_id / "exit", (stamp, stamp))
        else:
            self.plant_running_record(rec_id)
        return original

    def env(self, *, agent: str | None = None, path: str | None = None) -> dict[str, str]:
        extra = {"LANE_CONF": str(self.conf)}
        if path is not None:
            extra["PATH"] = path
        if agent is not None:
            extra["WF_AGENT"] = agent
        return self.world.env(extra)

    def invoke(
        self,
        argv: list[str],
        *,
        agent: str | None = None,
        cwd: Path | None = None,
    ):
        env = self.env(agent=agent)
        if agent is None:
            self.assertNotIn("WF_AGENT", env)
        else:
            self.assertEqual(env.get("WF_AGENT"), agent)
        return run(
            [sys.executable, str(self.staged), *argv],
            cwd=cwd or self.repo,
            env=env,
            timeout=RUN_CAP,
        )

    def combined(self, proc) -> str:
        return f"{proc.stdout or ''}{proc.stderr or ''}"

    def gh_argvs(self) -> list[list[str]]:
        return [row["argv"] for row in self.world.tools.calls("gh")]

    def line_for(self, stdout: str, name: str) -> str:
        aliases = {
            name,
            f"origin/{name}",
            f"refs/remotes/origin/{name}",
        }
        found = []
        for ln in stdout.splitlines():
            first = ln.split("\t", 1)[0].strip().rstrip(":")
            if first in aliases:
                found.append(ln)
        self.assertTrue(
            found, f"no census line for {name!r} in {stdout!r}"
        )
        return found[0]

    def line_index(self, stdout: str, name: str) -> int:
        line = self.line_for(stdout, name)
        return stdout.splitlines().index(line)

    def labeled_int(self, text: str, *keys: str) -> int | None:
        for key in keys:
            match = re.search(
                rf"(?:^|[\s,;]){re.escape(key)}\s*[=:]\s*(-?\d+)",
                text,
                re.IGNORECASE,
            )
            if match:
                return int(match.group(1))
        return None

    def refs(self, prefix: str) -> dict[str, str]:
        out = self.git(
            "for-each-ref", "--format=%(refname) %(objectname)", prefix
        ).stdout
        rows: dict[str, str] = {}
        for ln in out.splitlines():
            if not ln.strip():
                continue
            name, sha = ln.split()
            rows[name] = sha
        return rows

    def assert_refusal(self, proc, class_name: str) -> None:
        err = proc.stderr or ""
        compact = err.lower().replace("_", "-")
        token = class_name.lower().replace("_", "-")
        spaced = token.replace("-", " ")
        self.assertTrue(
            token in compact or spaced in err.lower(),
            f"refusal class {class_name!r} not named in stderr: {err!r}",
        )
        self.assertEqual(proc.exit, 2, err)
        self.assertTrue(err.strip(), "refusal was silent")
        lines = [ln for ln in err.splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1, f"refusal must be one line: {err!r}")
        lower = err.lower()
        self.assertIn("expected", lower)
        self.assertIn("found", lower)
        self.assertIn("needed", lower)
        self.assertLess(lower.index("expected"), lower.index("found"), err)
        self.assertLess(lower.index("found"), lower.index("needed"), err)
        self.assertEqual(
            (proc.stdout or "").strip(),
            "",
            "refusal text belongs on stderr, not stdout",
        )


class ToolPresence(_CensusFixture):
    """Missing tool file counts once."""

    def test_the_tool_file_is_present(self):
        self.assertTrue(TOOL.is_file(), f"RED: tool is absent: {TOOL}")


class CensusRoles(_CensusFixture):
    """1. trunk/unit/unknown roles and the feeds edges."""

    def test_dev_is_trunk_feeding_main(self):
        self.seed_trunks()
        proc = self.invoke(["census"])
        stdout = proc.stdout or ""
        line = self.line_for(stdout, "dev")
        self.assertRegex(line, r"\btrunk\b", line)
        self.assertIn("-> main", line)
        self.assertNotIn("identity", (proc.stderr or "").lower())

    def test_conductor_unit_feeds_dev(self):
        self.seed_trunks()
        sha = self.plant_origin_branch(
            UNIT_IDLE,
            parent=self.mid_sha,
            filename="idle.txt",
            content="idle-body\n",
            when=days_ago(3),
        )
        self.assertEqual(self.behind_dev(sha), 1)
        self.assertFalse(self.is_ancestor(sha, "origin/dev"))
        proc = self.invoke(["census"])
        stdout = proc.stdout or ""
        line = self.line_for(stdout, UNIT_IDLE)
        self.assertRegex(line, r"\bunit\b", line)
        self.assertIn("-> dev", line)
        self.assertIn("behind dev by 1", line)

    def test_main_is_release(self):
        self.seed_trunks()
        proc = self.invoke(["census"])
        stdout = proc.stdout or ""
        line = self.line_for(stdout, "main")
        self.assertRegex(line, r"\brelease\b", line)

    def test_unshaped_branch_is_unknown_and_exits_1(self):
        self.seed_trunks()
        sha = self.plant_origin_branch(
            UNKNOWN,
            parent=self.mid_sha,
            filename="unknown.txt",
            content="unknown-body\n",
            when=days_ago(3),
        )
        self.assertEqual(self.sha(f"origin/{UNKNOWN}"), sha)
        proc = self.invoke(["census"])
        stdout = proc.stdout or ""
        line = self.line_for(stdout, UNKNOWN)
        self.assertRegex(line, r"\bunknown\b", line)
        self.assertEqual(proc.exit, 1, self.combined(proc))

    def test_dispatch_refs_are_not_census_unknowns(self):
        self.seed_trunks()
        self.git("update-ref", f"refs/dispatch/{REC_OLD}", self.dev_sha)
        self.assertEqual(self.sha(f"refs/dispatch/{REC_OLD}"), self.dev_sha)
        proc = self.invoke(["census"])
        stdout = proc.stdout or ""
        line = self.line_for(stdout, "dev")
        self.assertRegex(line, r"\btrunk\b", line)
        self.assertNotRegex(
            stdout,
            rf"\b{re.escape(REC_OLD)}\b.*\bunknown\b",
            stdout,
        )
        self.assertEqual(proc.exit, 0, self.combined(proc))


class CensusStates(_CensusFixture):
    """2. merged-kept / active / stray / idle; strays first; exit 1."""

    def test_unit_whose_tip_is_ancestor_of_dev_is_merged_kept(self):
        self.seed_trunks()
        self.git("update-ref", f"refs/remotes/origin/{UNIT_MERGED}", self.mid_sha)
        self.assertEqual(self.sha(f"origin/{UNIT_MERGED}"), self.mid_sha)
        self.assertTrue(self.is_ancestor(self.mid_sha, "origin/dev"))
        proc = self.invoke(["census"])
        stdout = proc.stdout or ""
        line = self.line_for(stdout, UNIT_MERGED)
        self.assertRegex(line, r"\bmerged-kept\b", line)
        self.assertRegex(line, r"\bunit\b", line)
        self.assertIn("-> dev", line)

    def test_unit_with_open_pr_is_active(self):
        self.seed_trunks()
        sha = self.plant_origin_branch(
            UNIT_ACTIVE,
            parent=self.mid_sha,
            filename="active.txt",
            content="active-body\n",
            when=days_ago(3),
        )
        self.assertFalse(self.is_ancestor(sha, "origin/dev"))
        self.plant_gh_prs(
            [
                {
                    "number": 42,
                    "baseRefName": "dev",
                    "headRefName": UNIT_ACTIVE,
                    "state": "OPEN",
                }
            ]
        )
        proc = self.invoke(["census"])
        stdout = proc.stdout or ""
        line = self.line_for(stdout, UNIT_ACTIVE)
        self.assertRegex(line, r"\bactive\b", line)
        argvs = self.gh_argvs()
        self.assertTrue(argvs, "census did not invoke gh for open PRs")
        joined = " ".join(argvs[0])
        self.assertIn("pr", argvs[0])
        self.assertIn("list", argvs[0])
        self.assertIn("baseRefName", joined)

    def test_old_tip_without_pr_is_stray_sorts_first_and_exits_1(self):
        self.seed_trunks()
        stray = self.plant_origin_branch(
            UNIT_STRAY,
            parent=self.mid_sha,
            filename="stray.txt",
            content="stray-body\n",
            when=days_ago(20),
        )
        idle = self.plant_origin_branch(
            UNIT_IDLE,
            parent=self.mid_sha,
            filename="idle.txt",
            content="idle-body\n",
            when=days_ago(3),
        )
        active = self.plant_origin_branch(
            UNIT_ACTIVE,
            parent=self.mid_sha,
            filename="active.txt",
            content="active-body\n",
            when=days_ago(3),
        )
        unknown = self.plant_origin_branch(
            UNKNOWN,
            parent=self.mid_sha,
            filename="unknown.txt",
            content="unknown-body\n",
            when=days_ago(3),
        )
        self.git("update-ref", f"refs/remotes/origin/{UNIT_MERGED}", self.mid_sha)
        self.assertFalse(self.is_ancestor(stray, "origin/dev"))
        self.assertFalse(self.is_ancestor(idle, "origin/dev"))
        self.assertFalse(self.is_ancestor(active, "origin/dev"))
        self.assertFalse(self.is_ancestor(unknown, "origin/dev"))
        self.assertTrue(self.is_ancestor(self.mid_sha, "origin/dev"))
        ct = int(self.git("log", "-1", "--format=%ct", stray).stdout.strip())
        now = datetime.now(timezone.utc).timestamp()
        self.assertLess(ct, now - 14 * 86400, "plant: stray tip is not old")
        self.plant_gh_prs(
            [
                {
                    "number": 42,
                    "baseRefName": "dev",
                    "headRefName": UNIT_ACTIVE,
                    "state": "OPEN",
                }
            ]
        )
        proc = self.invoke(["census"])
        stdout = proc.stdout or ""
        stray_line = self.line_for(stdout, UNIT_STRAY)
        self.assertRegex(stray_line, r"\bstray\b", stray_line)
        self.assertNotIn("-> dev", stray_line)
        self.assertNotIn("-> main", stray_line)
        idle_line = self.line_for(stdout, UNIT_IDLE)
        self.assertRegex(idle_line, r"\bidle\b", idle_line)
        unknown_line = self.line_for(stdout, UNKNOWN)
        self.assertRegex(unknown_line, r"\bunknown\b", unknown_line)
        i_stray = self.line_index(stdout, UNIT_STRAY)
        i_unknown = self.line_index(stdout, UNKNOWN)
        i_active = self.line_index(stdout, UNIT_ACTIVE)
        i_idle = self.line_index(stdout, UNIT_IDLE)
        i_merged = self.line_index(stdout, UNIT_MERGED)
        self.assertLess(i_stray, i_active, stdout)
        self.assertLess(i_unknown, i_active, stdout)
        self.assertLess(i_active, i_idle, stdout)
        self.assertLess(i_idle, i_merged, stdout)
        self.assertEqual(proc.exit, 1, self.combined(proc))

    def test_young_unit_without_pr_is_idle(self):
        self.seed_trunks()
        sha = self.plant_origin_branch(
            UNIT_IDLE,
            parent=self.mid_sha,
            filename="idle.txt",
            content="idle-body\n",
            when=days_ago(3),
        )
        ct = int(self.git("log", "-1", "--format=%ct", sha).stdout.strip())
        now = datetime.now(timezone.utc).timestamp()
        self.assertGreater(ct, now - 14 * 86400, "plant: idle tip is not young")
        proc = self.invoke(["census"])
        stdout = proc.stdout or ""
        line = self.line_for(stdout, UNIT_IDLE)
        self.assertRegex(line, r"\bidle\b", line)
        self.assertEqual(proc.exit, 0, self.combined(proc))


class Stacked(_CensusFixture):
    """3. STACKED line names the base; all-dev bases exit 0."""

    def test_stacked_pr_prints_the_line_naming_the_base_and_exits_1(self):
        self.seed_trunks()
        self.plant_origin_branch(
            STACK_HEAD,
            parent=self.mid_sha,
            filename="stacked.txt",
            content="stacked-body\n",
        )
        self.plant_gh_prs(
            [
                {
                    "number": 7,
                    "baseRefName": STACK_BASE,
                    "headRefName": STACK_HEAD,
                    "state": "OPEN",
                }
            ]
        )
        proc = self.invoke(["stacked"])
        stdout = proc.stdout or ""
        expected = f"STACKED #7 base={STACK_BASE} retarget before merge"
        self.assertIn(expected, stdout.splitlines(), stdout)
        match = STACKED_RE.search(stdout)
        self.assertIsNotNone(match, stdout)
        self.assertEqual(match.group(1), "7")
        self.assertEqual(match.group(2), STACK_BASE)
        self.assertEqual(proc.exit, 1, self.combined(proc))
        argvs = self.gh_argvs()
        self.assertTrue(argvs, "stacked did not invoke gh")
        self.assertIn("pr", argvs[0])
        self.assertIn("list", argvs[0])
        self.assertTrue(
            any("baseRefName" in a for a in argvs[0]),
            argvs[0],
        )

    def test_all_dev_and_main_bases_exit_0(self):
        self.seed_trunks()
        self.plant_gh_prs(
            [
                {
                    "number": 8,
                    "baseRefName": "dev",
                    "headRefName": "conductor/ok",
                    "state": "OPEN",
                },
                {
                    "number": 9,
                    "baseRefName": "main",
                    "headRefName": "conductor/release-pr",
                    "state": "OPEN",
                },
            ]
        )
        proc = self.invoke(["stacked"])
        argvs = self.gh_argvs()
        self.assertTrue(argvs, "stacked did not invoke gh")
        self.assertNotIn("STACKED", proc.stdout or "")
        self.assertEqual(proc.exit, 0, self.combined(proc))

    def test_census_exits_1_when_a_stacked_row_exists(self):
        self.seed_trunks()
        self.git("update-ref", f"refs/remotes/origin/{UNIT_MERGED}", self.mid_sha)
        self.plant_gh_prs(
            [
                {
                    "number": 7,
                    "baseRefName": STACK_BASE,
                    "headRefName": UNIT_MERGED,
                    "state": "OPEN",
                }
            ]
        )
        proc = self.invoke(["census"])
        stdout = proc.stdout or ""
        expected = f"STACKED #7 base={STACK_BASE} retarget before merge"
        self.assertIn(expected, stdout.splitlines(), stdout)
        self.assertEqual(proc.exit, 1, self.combined(proc))


class Transport(_CensusFixture):
    """4. total / landed / closed / candidates; young is not a candidate."""

    def test_transport_counts_total_landed_closed_and_candidates(self):
        self.seed_trunks()
        old = self.plant_landed_dispatch(
            REC_OLD,
            filename="old-closed.txt",
            content="old-closed-body\n",
            when=days_ago(40),
            closed=True,
        )
        young = self.plant_landed_dispatch(
            REC_YOUNG,
            filename="young-closed.txt",
            content="young-closed-body\n",
            when=days_ago(5),
            closed=True,
        )
        unlanded = self.plant_unlanded_dispatch(
            REC_UNLANDED,
            filename="unlanded.txt",
            content="unlanded-body\n",
            when=days_ago(40),
            closed=True,
        )
        opened = self.plant_landed_dispatch(
            REC_OPEN,
            filename="old-open.txt",
            content="old-open-body\n",
            when=days_ago(40),
            closed=False,
        )
        self.assertEqual(self.sha(f"refs/dispatch/{REC_OLD}"), old)
        self.assertEqual(self.sha(f"refs/dispatch/{REC_YOUNG}"), young)
        self.assertEqual(self.sha(f"refs/dispatch/{REC_UNLANDED}"), unlanded)
        self.assertEqual(self.sha(f"refs/dispatch/{REC_OPEN}"), opened)
        proc = self.invoke(["transport", "--window", "30"])
        stdout = proc.stdout or ""
        total = self.labeled_int(stdout, "total")
        landed = self.labeled_int(stdout, "landed")
        closed = self.labeled_int(stdout, "record-closed", "closed")
        candidates = self.labeled_int(
            stdout, "candidates", "candidate", "retire"
        )
        self.assertEqual(total, 4, stdout)
        self.assertEqual(landed, 3, stdout)
        self.assertEqual(closed, 3, stdout)
        self.assertEqual(candidates, 1, stdout)
        self.assertIn(REC_OLD, stdout)
        self.assertEqual(proc.exit, 0, self.combined(proc))
        self.assertNotIn("identity", (proc.stderr or "").lower())

    def test_landed_younger_than_the_window_is_not_a_candidate(self):
        self.seed_trunks()
        self.plant_landed_dispatch(
            REC_YOUNG,
            filename="young-closed.txt",
            content="young-closed-body\n",
            when=days_ago(5),
            closed=True,
        )
        proc = self.invoke(["transport", "--window", "14"])
        stdout = proc.stdout or ""
        candidates = self.labeled_int(
            stdout, "candidates", "candidate", "retire"
        )
        self.assertIsNotNone(candidates, f"no candidates count in {stdout!r}")
        self.assertEqual(candidates, 0, stdout)
        self.assertEqual(proc.exit, 0, self.combined(proc))

    def test_default_retention_window_is_30_days(self):
        self.seed_trunks()
        self.plant_landed_dispatch(
            REC_OLD,
            filename="twenty.txt",
            content="twenty-day-body\n",
            when=days_ago(20),
            closed=True,
        )
        default = self.invoke(["transport"])
        out_d = default.stdout or ""
        cand_d = self.labeled_int(out_d, "candidates", "candidate", "retire")
        self.assertIsNotNone(cand_d, f"no candidates count in {out_d!r}")
        self.assertEqual(cand_d, 0, out_d)
        wide = self.invoke(["transport", "--window", "14"])
        out_w = wide.stdout or ""
        cand_w = self.labeled_int(out_w, "candidates", "candidate", "retire")
        self.assertEqual(cand_w, 1, out_w)
        self.assertIn(REC_OLD, out_w)


class Retire(_CensusFixture):
    """5. dry-run changes no ref; retire archives then deletes; idempotent."""

    def _candidate(self) -> str:
        self.seed_trunks()
        original = self.plant_landed_dispatch(
            REC_OLD,
            filename="old-closed.txt",
            content="old-closed-body\n",
            when=days_ago(40),
            closed=True,
        )
        return original

    def test_retire_dry_run_prints_the_plan_and_changes_no_ref(self):
        original = self._candidate()
        before_d = self.refs("refs/dispatch")
        before_a = self.refs("refs/archive")
        self.assertEqual(
            before_d.get(f"refs/dispatch/{REC_OLD}"), original
        )
        self.assertNotIn(f"refs/archive/dispatch/{REC_OLD}", before_a)
        proc = self.invoke(
            ["retire", "--window", "30", "--dry-run"], agent=AGENT
        )
        stdout = proc.stdout or ""
        self.assertTrue(
            REC_OLD in stdout or f"refs/dispatch/{REC_OLD}" in stdout,
            f"dry-run plan does not name the candidate: {stdout!r}",
        )
        self.assertEqual(self.refs("refs/dispatch"), before_d)
        self.assertEqual(self.refs("refs/archive"), before_a)
        self.assertEqual(
            self.sha(f"refs/dispatch/{REC_OLD}"), original
        )

    def test_retire_archives_then_deletes_and_a_second_run_is_idempotent(self):
        original = self._candidate()
        first = self.invoke(["retire", "--window", "30"], agent=AGENT)
        stdout = first.stdout or ""
        self.assertRegex(stdout, r"retired n=1", stdout)
        archive = f"refs/archive/dispatch/{REC_OLD}"
        self.assertEqual(self.sha(archive), original)
        missing = self.git(
            "rev-parse", "--verify", f"refs/dispatch/{REC_OLD}", check=False
        )
        self.assertNotEqual(
            missing.exit,
            0,
            "dispatch ref still present after retire",
        )
        self.assertEqual(first.exit, 0, self.combined(first))
        second = self.invoke(["retire", "--window", "30"], agent=AGENT)
        self.assertRegex(second.stdout or "", r"retired n=0", second.stdout)
        self.assertEqual(self.sha(archive), original)
        still = self.git(
            "rev-parse", "--verify", f"refs/dispatch/{REC_OLD}", check=False
        )
        self.assertNotEqual(still.exit, 0)
        self.assertEqual(second.exit, 0, self.combined(second))

    def test_a_failed_archive_tag_skips_the_delete_and_exits_1(self):
        original = self._candidate()
        blocker = self.repo / ".git" / "refs" / "archive"
        self._plant_bytes(blocker, b"not-a-directory\n")
        self.assertTrue(blocker.is_file(), "plant: archive blocker missing")
        self.assertFalse(blocker.is_dir(), "plant: archive blocker is a dir")
        proc = self.invoke(["retire", "--window", "30"], agent=AGENT)
        text = self.combined(proc)
        self.assertIn(REC_OLD, text, f"failed tag did not name the ref: {text!r}")
        self.assertEqual(proc.exit, 1, text)
        self.assertEqual(
            self.sha(f"refs/dispatch/{REC_OLD}"),
            original,
            "dispatch ref was deleted without an archive tag",
        )
        archive = self.git(
            "rev-parse",
            "--verify",
            f"refs/archive/dispatch/{REC_OLD}",
            check=False,
        )
        self.assertNotEqual(archive.exit, 0, "archive ref was created")


class Refusals(_CensusFixture):
    """6. Conventions wire: exit 2, one stderr line, expected/found/needed."""

    def test_unexpected_arguments_refuse_class_arguments(self):
        self.seed_trunks()
        proc = self.invoke(["census", "leftover"])
        self.assertIn("leftover", proc.stderr or "")
        self.assert_refusal(proc, "arguments")

    def test_outside_a_work_tree_refuses_repo(self):
        nongit = self.root / "nongit"
        nongit.mkdir()
        self.assertFalse((nongit / ".git").exists())
        probe = run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=nongit,
            env=self.git_env(),
            timeout=RUN_CAP,
        )
        self.assertNotEqual(
            probe.exit,
            0,
            f"INVALID: nongit cwd is a work tree: {probe.stdout}",
        )
        proc = self.invoke(["census"], cwd=nongit)
        self.assert_refusal(proc, "repo")

    def test_window_0_and_non_integers_refuse_window(self):
        self.seed_trunks()
        for bad in ("0", "abc", "-1"):
            with self.subTest(window=bad):
                proc = self.invoke(["transport", "--window", bad])
                self.assertIn(
                    bad,
                    proc.stderr or "",
                    f"--window {bad!r} must appear in found: {proc.stderr!r}",
                )
                self.assert_refusal(proc, "window")

    def test_retire_without_identity_refuses(self):
        self.seed_trunks()
        proc = self.invoke(["retire"], agent=None)
        self.assert_refusal(proc, "identity")

    def test_malformed_wf_agent_refuses_identity(self):
        self.seed_trunks()
        proc = self.invoke(["retire"], agent=MALFORMED_AGENT)
        self.assert_refusal(proc, "identity")
        self.assertIn(MALFORMED_AGENT, proc.stderr or "")

    def test_retire_dry_run_needs_no_identity(self):
        self.seed_trunks()
        self.plant_landed_dispatch(
            REC_OLD,
            filename="old-closed.txt",
            content="old-closed-body\n",
            when=days_ago(40),
            closed=True,
        )
        proc = self.invoke(["retire", "--dry-run"], agent=None)
        self.assertEqual(proc.exit, 0, proc.stderr)
        self.assertNotIn("identity", (proc.stderr or "").lower())
        self.assertIn("PLAN archive", proc.stdout or "", "dry-run printed no plan")

    def test_stacked_gh_multiline_stderr_is_one_line_gh_error(self):
        self.seed_trunks()
        self.plant_gh_prs(
            [],
            exit_code=1,
            stderr=GH_ERROR_L1 + "\n" + GH_ERROR_L2 + "\n",
        )
        proc = self.invoke(["stacked"])
        err = proc.stderr or ""
        self.assertIn(GH_ERROR_L1, err, f"gh stderr line 1 missing: {err!r}")
        self.assertIn(GH_ERROR_L2, err, f"gh stderr line 2 missing: {err!r}")
        self.assert_refusal(proc, "gh-error")


class ReviewGates(_CensusFixture):
    """6. Gates from the #113 reviews: each class banned, its neighbor quiet."""

    def test_census_without_a_dev_trunk_refuses_before_gh(self):
        init = self.git("init", "-b", "scratch", cwd=self.repo, check=False)
        self.assertEqual(init.exit, 0, init.stderr)
        self.git("config", "user.name", REPO_USER)
        self.git("config", "user.email", REPO_EMAIL)
        self.commit_file("only.txt", "no-trunk\n", "seed scratch")
        self.plant_gh_prs([])
        proc = self.invoke(["census"])
        self.assert_refusal(proc, "repo")
        self.assertEqual(
            self.gh_argvs(), [], "gh was consulted before the trunk refusal"
        )

    def test_census_with_a_genuine_dev_trunk_is_still_accepted(self):
        self.seed_trunks()
        self.plant_gh_prs([])
        proc = self.invoke(["census"])
        self.assertEqual(proc.exit, 0, self.combined(proc))

    def test_census_json_matches_text_and_pins_the_shape(self):
        self.seed_trunks()
        self.git("update-ref", f"refs/remotes/origin/{UNIT_MERGED}", self.mid_sha)
        self.plant_gh_prs(
            [
                {
                    "number": 7,
                    "baseRefName": STACK_BASE,
                    "headRefName": UNIT_MERGED,
                    "state": "OPEN",
                }
            ]
        )
        text = self.invoke(["census"])
        self.assertEqual(text.exit, 1, self.combined(text))
        proc = self.invoke(["census", "--json"])
        self.assertEqual(proc.exit, 1, self.combined(proc))
        payload = json.loads(proc.stdout)
        self.assertEqual(set(payload), {"branches", "stacked"})
        self.assertEqual(payload["stacked"], [{"base": STACK_BASE, "number": 7}])
        by_name = {row["name"]: row for row in payload["branches"]}
        for line in (text.stdout or "").splitlines():
            if line.startswith("STACKED "):
                continue
            name, role, state = line.split("\t")[:3]
            self.assertIn(name, by_name, line)
            self.assertEqual(by_name[name]["role"], role, line)
            self.assertEqual(by_name[name]["state"], state, line)

    def test_census_json_clean_board_lists_trunks_with_empty_stacked(self):
        self.seed_trunks()
        self.plant_gh_prs([])
        proc = self.invoke(["census", "--json"])
        self.assertEqual(proc.exit, 0, self.combined(proc))
        payload = json.loads(proc.stdout)
        self.assertEqual(set(payload), {"branches", "stacked"})
        self.assertEqual(payload["stacked"], [])
        names = {row["name"] for row in payload["branches"]}
        self.assertLessEqual({"dev", "main"}, names, payload)

    def test_census_refuses_gh_error_when_gh_fails(self):
        self.seed_trunks()
        self.plant_gh_prs([], exit_code=1, stderr="boom\nsecond line\n")
        proc = self.invoke(["census"])
        self.assert_refusal(proc, "gh-error")

    def test_census_full_pr_page_refuses_as_unmeasured(self):
        self.seed_trunks()
        prs = [
            {
                "number": n,
                "baseRefName": "dev",
                "headRefName": f"unit/u{n}",
                "state": "OPEN",
            }
            for n in range(1000)
        ]
        self.plant_gh_prs(prs)
        proc = self.invoke(["census"])
        self.assert_refusal(proc, "gh-error")

    def test_census_page_below_the_limit_stays_measured(self):
        self.seed_trunks()
        prs = [
            {
                "number": n,
                "baseRefName": "dev",
                "headRefName": f"unit/u{n}",
                "state": "OPEN",
            }
            for n in range(999)
        ]
        self.plant_gh_prs(prs)
        proc = self.invoke(["census"])
        self.assertEqual(proc.exit, 0, self.combined(proc))

    def test_fresh_record_on_an_old_commit_is_not_a_candidate(self):
        self.seed_trunks()
        self.plant_landed_dispatch(
            "20260801T000000Z-fresh-record",
            filename="fresh-rec.txt",
            content="fresh-rec-body\n",
            when=days_ago(40),
            closed=True,
        )
        rec_exit = self.store / "20260801T000000Z-fresh-record" / "exit"
        now = datetime.now(timezone.utc).timestamp()
        os.utime(rec_exit, (now, now))
        proc = self.invoke(["transport", "--window", "30"])
        self.assertEqual(proc.exit, 0, self.combined(proc))
        self.assertIn("candidates=0", proc.stdout or "")

    def test_cross_repo_pr_head_does_not_mask_a_stray(self):
        self.seed_trunks()
        self.plant_origin_branch(
            "conductor/fork-shadow",
            parent=self.mid_sha,
            filename="fork-shadow.txt",
            content="fork-shadow-body\n",
            when=days_ago(30),
        )
        self.plant_gh_prs(
            [
                {
                    "number": 9,
                    "baseRefName": "dev",
                    "headRefName": "conductor/fork-shadow",
                    "state": "OPEN",
                    "isCrossRepository": True,
                }
            ]
        )
        proc = self.invoke(["census"])
        self.assertEqual(proc.exit, 1, self.combined(proc))
        line = self.line_for(proc.stdout or "", "conductor/fork-shadow")
        self.assertIn("stray", line)

    def test_same_repo_pr_head_still_marks_active(self):
        self.seed_trunks()
        self.plant_origin_branch(
            "conductor/live-unit",
            parent=self.mid_sha,
            filename="live-unit.txt",
            content="live-unit-body\n",
            when=days_ago(30),
        )
        self.plant_gh_prs(
            [
                {
                    "number": 10,
                    "baseRefName": "dev",
                    "headRefName": "conductor/live-unit",
                    "state": "OPEN",
                    "isCrossRepository": False,
                }
            ]
        )
        proc = self.invoke(["census"])
        self.assertEqual(proc.exit, 0, self.combined(proc))
        line = self.line_for(proc.stdout or "", "conductor/live-unit")
        self.assertIn("active", line)

    def test_pr_row_missing_base_refuses_gh_error(self):
        self.seed_trunks()
        self.plant_gh_prs(
            [{"number": 5, "headRefName": "unit/x", "state": "OPEN"}]
        )
        proc = self.invoke(["census"])
        self.assert_refusal(proc, "gh-error")

    def test_pr_row_with_boolean_number_refuses(self):
        self.seed_trunks()
        self.plant_gh_prs(
            [
                {
                    "number": True,
                    "baseRefName": "dev",
                    "headRefName": "unit/z",
                    "state": "OPEN",
                }
            ]
        )
        proc = self.invoke(["census"])
        self.assert_refusal(proc, "gh-error")

    def test_pr_row_without_boolean_scope_flag_refuses(self):
        self.seed_trunks()
        self.plant_gh_prs(
            [
                {
                    "number": 6,
                    "baseRefName": "dev",
                    "headRefName": "unit/y",
                    "state": "OPEN",
                    "isCrossRepository": None,
                }
            ]
        )
        proc = self.invoke(["census"])
        self.assert_refusal(proc, "gh-error")

    def test_retire_proceeds_past_a_matching_pre_existing_archive(self):
        self.seed_trunks()
        tip = self.plant_landed_dispatch(
            REC_OLD,
            filename="old-closed.txt",
            content="old-closed-body\n",
            when=days_ago(40),
            closed=True,
        )
        self.git("update-ref", f"refs/archive/dispatch/{REC_OLD}", tip)
        proc = self.invoke(["retire", "--window", "30"], agent=AGENT)
        self.assertEqual(proc.exit, 0, self.combined(proc))
        self.assertIn(f"RETIRED refs/dispatch/{REC_OLD}", proc.stdout or "")
        gone = self.git(
            "rev-parse", "--verify", "--quiet",
            f"refs/dispatch/{REC_OLD}", check=False,
        )
        self.assertNotEqual(gone.exit, 0, "dispatch ref survived retire")
        self.assertEqual(self.sha(f"refs/archive/dispatch/{REC_OLD}"), tip)

    def test_retire_keeps_the_ref_when_the_archive_mismatches(self):
        self.seed_trunks()
        tip = self.plant_landed_dispatch(
            REC_OLD,
            filename="old-closed.txt",
            content="old-closed-body\n",
            when=days_ago(40),
            closed=True,
        )
        self.git("update-ref", f"refs/archive/dispatch/{REC_OLD}", self.mid_sha)
        proc = self.invoke(["retire", "--window", "30"], agent=AGENT)
        self.assertEqual(proc.exit, 1, self.combined(proc))
        self.assertIn("kept", self.combined(proc))
        self.assertEqual(self.sha(f"refs/dispatch/{REC_OLD}"), tip)
        self.assertEqual(
            self.sha(f"refs/archive/dispatch/{REC_OLD}"), self.mid_sha
        )


if __name__ == "__main__":
    unittest.main()
