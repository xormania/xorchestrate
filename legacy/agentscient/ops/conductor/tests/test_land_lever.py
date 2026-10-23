"""Land-lever acceptance tests, written from the contracts.

Contracts (only): `ops/conductor/land-lever.md` and the inherited
`ops/conductor/instrument-conventions.md`. The tool
`ops/conductor/tools/land.py` is the subject and is not read here.
Tests invoke it as a subprocess inside a scratch git repository.

The live dispatch store and this repo must stay untouched — asserted
in tearDown.

World owns HOME and the XDG roots; every subprocess goes through run.
The implementation lands in parallel. A missing tool file is one red
(`test_the_tool_file_is_present`). Every other case stages a
copy-tool-bytes stand-in so a missing file is not every test's failure:
when the tool exists its bytes are copied and run; when it does not, a
no-op stub (exit 0, no output) is planted so each assertion goes red on
the contract promise rather than Python's missing-file exit 2.
"""

from __future__ import annotations

import json
import os
import re
import sys
import unittest
from pathlib import Path

from live_store_guard import IGNORE, TOLERATE_NEW

from ops.testkit import Timeout, World, run

REPO = Path(__file__).resolve().parents[3]
TOOL = REPO / "ops" / "conductor" / "tools" / "land.py"
# The tool imports these beside itself (instrument-conventions.md §The
# shared implementation); a staged copy run from a scratch directory
# needs them staged too, or it refuses `tooling` before the contract.
SIBLINGS = ("instrument.py", "laneconf.py")

BRANCH = "land-lineage"
REC = "land-rec-1"
REF = f"refs/dispatch/{REC}"
AGENT = "Land Lever Tester <land-tester@example.invalid>"
MALFORMED_AGENT = "JustAName"
PRODUCER = "Producer Author"
PRODUCER_EMAIL = "producer@example.invalid"
REPO_USER = "Repo Config"
REPO_EMAIL = "repo-config@example.invalid"
LANE_AUTHOR_NAME = "Lane Owner"
LANE_AUTHOR_EMAIL = "lane-owner@example.invalid"
LANE_AUTHOR = f"{LANE_AUTHOR_NAME} <{LANE_AUTHOR_EMAIL}>"
CO_AUTHOR = "Worker Model <noreply@vendor.example>"
TRAILER = f"Source: original\nCo-Authored-By: {CO_AUTHOR}"
SUBJECT = "producer-one"
RUN_CAP = 30

LANDED_RE = re.compile(r"^LANDED\t([0-9a-f]{7,40})\t(.*)$")
SUMMARY_RE = re.compile(r"^landed n=(\d+) ref=(refs/dispatch/.+)$")

# PIN_LANDED_STREAM: success prints LANDED lines and the summary on
# stdout (refusals already own stderr; the act's reading is the result).
# PIN_N0_LINE: empty range prints exactly `landed n=0`, no ref= field
# (the contract names that shape separately from the n>=1 summary).
# PIN_ABORT_LINE: conflict Abort's last stdout line names the conflicted
# producer sha and remainder=N, N = commits not landed (conflicted plus
# any after it).
# PIN_SHA: LANDED sha is a git object name that rev-parse's to HEAD
# after a one-commit land (the cherry-pick's new sha, not the producer
# object).
# PIN_COMMITTER: WF_AGENT `Name <addr>` becomes committer name/email;
# repo local user.name is a third identity so forgetting WF_AGENT is a
# red, not a lucky pass.


def _live_store_paths() -> list[Path]:
    paths: list[Path] = []
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        paths.append(Path(xdg) / "agentscient" / "dispatch")
    paths.append(Path.home() / ".local" / "state" / "agentscient" / "dispatch")
    snap = REPO.resolve()
    if snap.name == "snapshot":
        job = snap.parent
        if job.parent.name == "dispatch":
            paths.append(job.parent)
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = path if not path.exists() else path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(path)
    return unique


def _git_out(args: list[str], cwd: Path, env: dict[str, str]) -> str:
    result = run(args, cwd=cwd, env=env, timeout=RUN_CAP)
    if result.exit != 0:
        raise AssertionError(f"{args} in {cwd}: {result.stderr}")
    return result.stdout


class _LandFixture(unittest.TestCase):
    """Scratch HOME + git repo, staged tool copy, live store frozen."""

    def setUp(self):
        self.world = World(prefix="land-lever-")
        self.addCleanup(self.world.close)
        self.world.require("git")
        # Presence, kind and immediate names only: the guard's contract
        # (ops/conductor/test-store-guard.md); the live store is large and
        # shared with running dispatches, and reading its bytes is neither
        # promised nor affordable.
        self._live = self.world.watch(
            *_live_store_paths(),
            ignore=IGNORE,
            tolerate_new=TOLERATE_NEW,
            contents=False,
        )
        host_env = self.world.env()
        self._head_before = _git_out(
            ["git", "rev-parse", "HEAD"], REPO, host_env
        ).strip()
        self._diff_before = run(
            ["git", "diff", "HEAD"], cwd=REPO, env=host_env, timeout=RUN_CAP
        ).stdout
        self._dispatch_refs_before = run(
            ["git", "for-each-ref", "refs/dispatch"],
            cwd=REPO,
            env=host_env,
            timeout=RUN_CAP,
        ).stdout
        self.root = self.world.root
        self.home = self.world.home
        self.scratch = self.world.path("scratch")
        self.repo = self.world.path("repo")
        self.scratch.mkdir()
        self.store = self.world.xdg_state / "agentscient" / "dispatch"
        self.assertFalse(
            self.store.exists(),
            "plant: isolated store must start absent",
        )
        self._stage_tool_bytes()
        self.conf_path = self.scratch / "lane.conf"
        self.plant_lane_conf()
        self.base = ""
        self.origin: Path | None = None

    def tearDown(self):
        host_env = self.world.env()
        self._live.assert_unchanged(self, "live dispatch store mutated")
        self.assertEqual(
            _git_out(["git", "rev-parse", "HEAD"], REPO, host_env).strip(),
            self._head_before,
            "real repo HEAD moved",
        )
        diff_after = run(
            ["git", "diff", "HEAD"], cwd=REPO, env=host_env, timeout=RUN_CAP
        ).stdout
        self.assertEqual(
            diff_after,
            self._diff_before,
            "real repo tracked files mutated",
        )
        refs_after = run(
            ["git", "for-each-ref", "refs/dispatch"],
            cwd=REPO,
            env=host_env,
            timeout=RUN_CAP,
        ).stdout
        self.assertEqual(
            refs_after,
            self._dispatch_refs_before,
            "real repo gained a dispatch ref",
        )
        self.assertFalse(
            (self.store / REC).exists()
            and str(self.store).startswith(str(Path.home())),
            "isolated record leaked into live HOME",
        )

    def _stage_tool_bytes(self) -> None:
        self.staged = self.world.stage(
            TOOL, "land.py", siblings=SIBLINGS if TOOL.is_file() else ()
        )
        if TOOL.is_file():
            self.assertEqual(
                self.staged.read_bytes(),
                TOOL.read_bytes(),
                "copy-tool-bytes did not land intact",
            )
        self.assertTrue(self.staged.is_file())

    def _lane_conf_text(self, **overrides: object) -> str:
        omit = tuple(overrides.pop("omit", ()) or ())
        keys = {
            "LANE_NAME": "agentscient",
            "TRUNK_BRANCH": "dev",
            "RELEASE_BRANCH": "master",
            "REPO_SLUG": "uscient/agentscient",
            "CHECK_NAMES": "lane app",
            "WF_AGENT_DEFAULT": AGENT,
            "LANE_AUTHOR": LANE_AUTHOR,
            "MODEL_GROK": "model-grok",
            "MODEL_CODEX": "model-codex",
            "MODEL_CLAUDE_READ": "model-claude-read",
            "MODEL_CLAUDE_PLAN": "model-claude-plan",
            "EFFORT_GROK": "high",
            "EFFORT_CODEX": "high",
            "EFFORT_CLAUDE": "high",
            "WALL_DEFAULT": "900",
            "SCOPE_CAP": "1024",
        }
        for name, value in overrides.items():
            keys[name] = str(value)
        for name in omit:
            keys.pop(name, None)
        lines = ["# scratch lane conf\n"]
        for name, value in keys.items():
            lines.append(f"{name}='{value}'\n")
        return "".join(lines)

    def plant_lane_conf(self, **overrides: object) -> Path:
        text = self._lane_conf_text(**overrides)
        self.world.plant_text(self.conf_path, text)
        landed = self.conf_path.read_text(encoding="utf-8")
        self.assertEqual(landed, text, "conf plant did not land intact")
        return self.conf_path

    def _plant_bytes(self, path: Path, data: bytes) -> None:
        self.world.plant_bytes(path, data)

    def git_env(
        self,
        *,
        author_name: str | None = None,
        author_email: str | None = None,
    ) -> dict[str, str]:
        return self.world.env(
            extra={
                "GIT_AUTHOR_NAME": author_name or REPO_USER,
                "GIT_AUTHOR_EMAIL": author_email or REPO_EMAIL,
                "GIT_COMMITTER_NAME": REPO_USER,
                "GIT_COMMITTER_EMAIL": REPO_EMAIL,
            }
        )

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

    def sha(self, rev: str, cwd: Path | None = None) -> str:
        out = self.git("rev-parse", rev, cwd=cwd).stdout.strip()
        self.assertRegex(out, r"^[0-9a-f]{40}$", out)
        return out

    def porcelain(self) -> str:
        return self.git("status", "--porcelain").stdout

    def message(self, rev: str) -> str:
        return self.git("log", "-1", "--format=%B", rev).stdout

    def author(self, rev: str) -> str:
        return self.git("log", "-1", "--format=%an <%ae>", rev).stdout.strip()

    def committer(self, rev: str) -> str:
        return self.git("log", "-1", "--format=%cn <%ce>", rev).stdout.strip()

    def subject(self, rev: str) -> str:
        return self.git("log", "-1", "--format=%s", rev).stdout.strip()

    def parsed_trailers(self, rev: str) -> str:
        msg = self.message(rev)
        proc = run(
                ["git", "interpret-trailers", "--parse"],
                cwd=self.repo,
                env=self.git_env(),
                stdin=msg,
                timeout=RUN_CAP,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout

    def range_shas(self, left: str, right: str) -> list[str]:
        out = self.git("rev-list", "--reverse", f"{left}..{right}").stdout
        shas = [ln.strip() for ln in out.splitlines() if ln.strip()]
        return shas

    def env(
        self,
        *,
        agent: str | None = AGENT,
        extra: dict[str, str] | None = None,
    ) -> dict[str, str]:
        env = self.world.env(
            extra={
                "LANE_CONF": str(self.conf_path),
            }
        )
        # The landed committer is WF_AGENT, the author is LANE_AUTHOR;
        # World's fixed git identity must not leak into the act.
        for name in [k for k in env if k.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_"))]:
            del env[name]
        if agent is not None:
            env["WF_AGENT"] = agent
        if extra:
            env.update(extra)
        return env

    def invoke(
        self,
        argv: list[str],
        *,
        agent: str | None = AGENT,
        extra_env: dict[str, str] | None = None,
    ):
        env = self.env(agent=agent, extra=extra_env)
        if agent is None:
            self.assertNotIn("WF_AGENT", env)
        else:
            self.assertEqual(env.get("WF_AGENT"), agent)
        try:
            return run(
                    [sys.executable, str(self.staged), *argv],
                    cwd=self.repo,
                    env=env,
                    timeout=RUN_CAP,
                )
        except Timeout as exc:
            self.fail(f"land exceeded {RUN_CAP}s: {exc}")

    def land(
        self,
        record: str = REC,
        extra: list[str] | None = None,
        *,
        agent: str | None = AGENT,
        extra_env: dict[str, str] | None = None,
    ):
        argv = [record]
        if extra:
            argv.extend(extra)
        return self.invoke(argv, agent=agent, extra_env=extra_env)

    def combined(self, proc) -> str:
        return f"{proc.stdout or ''}{proc.stderr or ''}"

    def landed_rows(self, stdout: str) -> list[tuple[str, str]]:
        rows: list[tuple[str, str]] = []
        for line in stdout.splitlines():
            match = LANDED_RE.match(line)
            if match:
                rows.append((match.group(1), match.group(2)))
        return rows

    def summary_line(self, stdout: str) -> str | None:
        lines = [ln for ln in stdout.splitlines() if ln.strip()]
        if not lines:
            return None
        last = lines[-1]
        if SUMMARY_RE.match(last) or last == "landed n=0":
            return last
        return None

    def assert_refusal(self, proc, class_name: str) -> None:
        refusal = proc.refusal()
        token = class_name.lower().replace("_", "-")
        compact = refusal.line.lower().replace("_", "-")
        spaced = token.replace("-", " ")
        self.assertTrue(
            token in compact or spaced in refusal.line.lower(),
            f"refusal class {class_name!r} not named in stderr: {refusal.line!r}",
        )

    def seed_repo(self, *, branch: str = BRANCH, record: str = REC) -> str:
        self.repo.mkdir()
        init = self.git("init", "-b", branch, cwd=self.repo, check=False)
        self.assertEqual(init.returncode, 0, init.stderr)
        self.git("config", "user.name", REPO_USER)
        self.git("config", "user.email", REPO_EMAIL)
        name = self.git("config", "--get", "user.name").stdout.strip()
        email = self.git("config", "--get", "user.email").stdout.strip()
        self.assertEqual(name, REPO_USER)
        self.assertEqual(email, REPO_EMAIL)
        tracked = self.repo / "tracked.txt"
        shared = self.repo / "shared.txt"
        self._plant_bytes(tracked, b"seed\n")
        self._plant_bytes(shared, b"base\n")
        envelope = (
            self.repo / ".dev" / "records" / "dispatches" / f"{record}.json"
        )
        payload = json.dumps(
            {
                "lineage": {"branch": branch},
                "result": {"envelope": {"status": "ok"}},
            },
            indent=2,
        ) + "\n"
        self._plant_bytes(envelope, payload.encode("utf-8"))
        loaded = json.loads(envelope.read_text(encoding="utf-8"))
        self.assertEqual(loaded["lineage"]["branch"], branch)
        self.git("add", "tracked.txt", "shared.txt", str(
            envelope.relative_to(self.repo)
        ))
        self.git("commit", "-m", f"seed envelope\n\n{TRAILER}")
        self.base = self.sha("HEAD")
        head_branch = self.git(
            "symbolic-ref", "--short", "HEAD"
        ).stdout.strip()
        self.assertEqual(head_branch, branch)
        self.assertEqual(self.porcelain(), "")
        return self.base

    def plant_producers(
        self,
        commits: list[tuple[str, str, str, bool]],
        *,
        record: str = REC,
        author_name: str = PRODUCER,
        author_email: str = PRODUCER_EMAIL,
    ) -> list[str]:
        """commits: (relpath, content, subject, with_trailer).

        Point refs/dispatch/<id> at the tip and reset the lineage
        branch back to the seed.
        """
        self.assertTrue(self.base, "INVALID: seed_repo before producers")
        shas: list[str] = []
        for relpath, content, subject, with_trailer in commits:
            path = self.repo / relpath
            self._plant_bytes(path, content.encode("utf-8"))
            self.git("add", relpath)
            body = f"{subject}\n\n{TRAILER}\n" if with_trailer else f"{subject}\n"
            proc = run(
                    [
                        "git",
                        "-c",
                        "commit.gpgsign=false",
                        "commit",
                        "-m",
                        body,
                    ],
                    cwd=self.repo,
                    env=self.git_env(
                        author_name=author_name,
                        author_email=author_email,
                    ),
                    timeout=RUN_CAP,
                )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            sha = self.sha("HEAD")
            self.assertEqual(
                self.author(sha), f"{author_name} <{author_email}>",
            )
            trailers = self.parsed_trailers(sha)
            if with_trailer:
                self.assertIn("Source:", trailers)
            else:
                self.assertEqual(trailers.strip(), "")
            shas.append(sha)
        tip = self.sha("HEAD")
        self.git("update-ref", f"refs/dispatch/{record}", tip)
        self.git("reset", "--hard", self.base)
        self.assertEqual(self.sha(f"refs/dispatch/{record}"), tip)
        self.assertEqual(self.sha("HEAD"), self.base)
        self.assertNotEqual(tip, self.base)
        for sha in shas:
            probe = self.git("cat-file", "-e", sha, check=False)
            self.assertEqual(
                probe.returncode,
                0,
                f"INVALID: producer {sha} not reachable",
            )
        return shas

    def plant_record(self, *, exit_code: str = "0", record: str = REC) -> Path:
        path = self.store / record
        path.mkdir(parents=True)
        exit_path = path / "exit"
        data = f"{exit_code}\n".encode("utf-8")
        self._plant_bytes(exit_path, data)
        self.assertTrue(path.is_dir(), "INVALID: record dir plant did not land")
        self.assertEqual(exit_path.read_bytes(), data)
        self.assertNotEqual(
            self.store.resolve(),
            (Path.home() / ".local" / "state" / "agentscient" / "dispatch"),
        )
        return path

    def ready(self, *, producers: int = 1) -> list[str]:
        self.seed_repo()
        commits: list[tuple[str, str, str, bool]] = []
        for i in range(producers):
            commits.append(
                (f"landed-{i}.txt", f"body-{i}\n", f"producer-{i}", True)
            )
        if producers == 1:
            commits = [("landed.txt", "landed-body\n", SUBJECT, True)]
        shas = self.plant_producers(commits)
        self.plant_record()
        self.assertEqual(self.porcelain(), "")
        return shas

    def plant_bare_origin(self) -> str:
        origin = self.root / "origin.git"
        origin.mkdir()
        init = self.git("init", "--bare", "-b", BRANCH, cwd=origin, check=False)
        self.assertEqual(init.returncode, 0, init.stderr)
        self.origin = origin
        self.git("remote", "add", "origin", str(origin))
        self.git("push", "origin", f"{BRANCH}:{BRANCH}")
        remote = self.git(
            "rev-parse", BRANCH, cwd=origin
        ).stdout.strip()
        self.assertEqual(remote, self.sha("HEAD"))
        return remote

    def origin_sha(self) -> str:
        self.assertIsNotNone(self.origin, "INVALID: origin not planted")
        return self.git("rev-parse", BRANCH, cwd=self.origin).stdout.strip()


class ToolPresence(_LandFixture):
    """Missing tool file counts once."""

    def test_the_tool_file_is_present(self):
        self.assertTrue(TOOL.is_file(), f"RED: tool is absent: {TOOL}")


class HappyPath(_LandFixture):
    """1. One producer commit lands: LANDED line, n=1 summary, exit 0."""

    def test_one_producer_commit_lands_verbatim_with_author_and_committer(self):
        producers = self.ready()
        self.assertEqual(len(producers), 1)
        producer = producers[0]
        original = self.message(producer)
        self.assertIn(TRAILER, original)
        self.assertNotIn("cherry picked from commit", original)
        proc = self.land()
        stdout = proc.stdout or ""
        self.assertEqual(proc.returncode, 0, self.combined(proc))
        rows = self.landed_rows(stdout)
        self.assertEqual(len(rows), 1, stdout)
        sha, subject = rows[0]
        self.assertEqual(subject, SUBJECT)
        self.assertEqual(self.sha(sha), self.sha("HEAD"))
        self.assertEqual(
            self.summary_line(stdout),
            f"landed n=1 ref={REF}",
            stdout,
        )
        self.assertEqual(self.message("HEAD"), original)
        self.assertNotIn("cherry picked from commit", self.message("HEAD"))
        self.assertIn("Source:", original)
        self.assertIn("Co-Authored-By:", original)
        self.assertIn(CO_AUTHOR, original)
        self.assertEqual(self.author("HEAD"), LANE_AUTHOR)
        self.assertNotEqual(
            self.author("HEAD"),
            f"{PRODUCER} <{PRODUCER_EMAIL}>",
        )
        self.assertEqual(self.committer("HEAD"), AGENT)
        self.assertNotEqual(
            self.committer("HEAD"),
            f"{REPO_USER} <{REPO_EMAIL}>",
        )
        landed_body = (self.repo / "landed.txt").read_text(encoding="utf-8")
        self.assertEqual(landed_body, "landed-body\n")


class Idempotence(_LandFixture):
    """2. A second run prints landed n=0, exit 0."""

    def test_a_second_run_prints_landed_n0(self):
        self.ready()
        first = self.land()
        self.assertNotIn(
            "landed n=0",
            first.stdout or "",
            f"first run was already n=0: {first.stdout!r}",
        )
        head_after = self.sha("HEAD") if first.returncode == 0 else self.base
        second = self.land()
        stdout = second.stdout or ""
        self.assertEqual(second.returncode, 0, self.combined(second))
        self.assertEqual(
            self.summary_line(stdout),
            "landed n=0",
            stdout,
        )
        if first.returncode == 0:
            self.assertEqual(self.sha("HEAD"), head_after)


class EmptyRange(_LandFixture):
    """Nothing to land (HEAD..ref empty) is n=0, not an error."""

    def test_empty_range_prints_landed_n0(self):
        self.seed_repo()
        self.git("update-ref", REF, self.sha("HEAD"))
        self.assertEqual(self.sha(REF), self.sha("HEAD"))
        self.assertEqual(self.range_shas("HEAD", REF), [])
        self.plant_record()
        proc = self.land()
        stdout = proc.stdout or ""
        self.assertEqual(proc.returncode, 0, self.combined(proc))
        self.assertEqual(self.summary_line(stdout), "landed n=0", stdout)
        self.assertEqual(self.sha("HEAD"), self.base)


class IdentityRefusal(_LandFixture):
    """identity: WF_AGENT unset or malformed, checked first."""

    def test_unset_wf_agent_refuses_identity_before_anything_else(self):
        self.seed_repo()
        self.assertFalse(self.store.exists(), "plant: no record dir")
        proc = self.land(agent=None)
        self.assert_refusal(proc, "identity")
        err = (proc.stderr or "").lower()
        self.assertNotIn("record", err.split(":", 1)[0])

    def test_malformed_wf_agent_refuses_identity(self):
        self.ready()
        proc = self.land(agent=MALFORMED_AGENT)
        self.assert_refusal(proc, "identity")
        self.assertEqual(self.sha("HEAD"), self.base)


class RecordRefusal(_LandFixture):
    """record: no record directory named RECORD_ID in the store."""

    def test_missing_record_directory_refuses_record(self):
        self.seed_repo()
        self.plant_producers([("landed.txt", "x\n", SUBJECT, True)])
        self.assertFalse(self.store.exists(), "plant: store dir absent")
        proc = self.land()
        self.assert_refusal(proc, "record")
        self.assertEqual(self.sha("HEAD"), self.base)


class ExitStatusRefusal(_LandFixture):
    """exit-status: exit file missing or nonzero — 137 is the briefed case."""

    def test_nonzero_exit_file_refuses_exit_status(self):
        self.seed_repo()
        self.plant_producers([("landed.txt", "x\n", SUBJECT, True)])
        self.plant_record(exit_code="137")
        planted = (self.store / REC / "exit").read_text(encoding="utf-8")
        self.assertEqual(planted.strip(), "137")
        self.assertNotEqual(planted.strip(), "0")
        proc = self.land()
        self.assert_refusal(proc, "exit-status")
        self.assertEqual(self.sha("HEAD"), self.base)


class RefRefusal(_LandFixture):
    """ref: record present, refs/dispatch/RECORD_ID does not resolve."""

    def test_absent_dispatch_ref_refuses_ref(self):
        self.seed_repo()
        self.plant_record()
        missing = self.git("rev-parse", "--verify", REF, check=False)
        self.assertNotEqual(missing.returncode, 0, "plant: ref must be absent")
        proc = self.land()
        self.assert_refusal(proc, "ref")
        self.assertEqual(self.sha("HEAD"), self.base)


class EnvelopeRefusal(_LandFixture):
    """envelope: ref present, envelope absent from the working tree."""

    def test_absent_envelope_refuses_envelope(self):
        self.seed_repo()
        self.plant_producers([("landed.txt", "x\n", SUBJECT, True)])
        self.plant_record()
        envelope = (
            self.repo / ".dev" / "records" / "dispatches" / f"{REC}.json"
        )
        self.assertTrue(envelope.is_file(), "plant: envelope present before rm")
        envelope.unlink()
        self.assertFalse(envelope.exists(), "plant: envelope still present")
        self.git("add", "-A")
        self.git("commit", "-m", f"drop envelope\n\n{TRAILER}")
        self.assertFalse(envelope.exists())
        self.assertEqual(self.porcelain(), "")
        before = self.sha("HEAD")
        proc = self.land()
        self.assert_refusal(proc, "envelope")
        self.assertEqual(self.sha("HEAD"), before)


class LineageRefusal(_LandFixture):
    """lineage: current branch is not the envelope's lineage.branch."""

    def test_wrong_branch_refuses_lineage(self):
        self.ready()
        self.git("checkout", "-b", "other-lineage")
        current = self.git("symbolic-ref", "--short", "HEAD").stdout.strip()
        self.assertEqual(current, "other-lineage")
        self.assertNotEqual(current, BRANCH)
        proc = self.land()
        self.assert_refusal(proc, "lineage")
        self.assertEqual(self.sha("HEAD"), self.base)


class DirtyRefusal(_LandFixture):
    """dirty: staged or unstaged changes."""

    def test_uncommitted_change_refuses_dirty(self):
        self.ready()
        tracked = self.repo / "tracked.txt"
        before = tracked.read_bytes()
        self._plant_bytes(tracked, b"dirty-uncommitted\n")
        self.assertNotEqual(tracked.read_bytes(), before)
        self.assertTrue(self.porcelain().strip(), "plant: tree still clean")
        proc = self.land()
        self.assert_refusal(proc, "dirty")
        self.assertEqual(self.sha("HEAD"), self.base)


class StaleRefusal(_LandFixture):
    """stale: branch behind origin; --no-fetch still refuses."""

    def test_origin_ahead_with_no_fetch_refuses_stale(self):
        self.ready()
        self._plant_bytes(self.repo / "ahead.txt", b"origin-ahead\n")
        self.git("add", "ahead.txt")
        self.git("commit", "-m", f"origin moved on\n\n{TRAILER}")
        ahead = self.sha("HEAD")
        self.assertNotEqual(ahead, self.base)
        self.git("remote", "add", "origin", "https://example.invalid/land.git")
        self.git("update-ref", f"refs/remotes/origin/{BRANCH}", ahead)
        self.git("reset", "--hard", self.base)
        self.assertEqual(self.sha("HEAD"), self.base)
        self.assertEqual(self.sha(f"origin/{BRANCH}"), ahead)
        count = self.git(
            "rev-list", "--count", f"HEAD..origin/{BRANCH}"
        ).stdout.strip()
        self.assertGreaterEqual(int(count), 1, "plant: origin is not ahead")
        proc = self.land(extra=["--no-fetch"])
        self.assert_refusal(proc, "stale")
        self.assertEqual(self.sha("HEAD"), self.base)


class AttributionRefusal(_LandFixture):
    """attribution: a commit with no trailer, checked before any pick."""

    def test_commit_without_trailer_refuses_attribution_before_any_pick(self):
        self.seed_repo()
        shas = self.plant_producers(
            [
                ("ok.txt", "ok\n", "has-trailer", True),
                ("bad.txt", "bad\n", "no-trailer", False),
            ]
        )
        self.assertEqual(len(shas), 2)
        self.assertIn("Source:", self.parsed_trailers(shas[0]))
        self.assertEqual(self.parsed_trailers(shas[1]).strip(), "")
        self.plant_record()
        before = self.sha("HEAD")
        proc = self.land()
        self.assert_refusal(proc, "attribution")
        self.assertEqual(self.sha("HEAD"), before)
        self.assertFalse((self.repo / "ok.txt").exists())
        self.assertFalse((self.repo / "bad.txt").exists())
        self.assertEqual(self.porcelain(), "")


class ConflictAbort(_LandFixture):
    """5. Second commit conflicts: exit 3, first kept, tree clean."""

    def test_conflicting_second_commit_aborts_keeping_the_first(self):
        self.seed_repo()
        shas = self.plant_producers(
            [
                ("landed.txt", "first\n", "producer-one", True),
                ("shared.txt", "producer\n", "producer-two", True),
            ]
        )
        self.assertEqual(len(shas), 2)
        first, conflicted = shas
        self._plant_bytes(self.repo / "shared.txt", b"lineage\n")
        self.git("add", "shared.txt")
        self.git("commit", "-m", f"lineage moved shared.txt\n\n{TRAILER}")
        start = self.sha("HEAD")
        self.assertNotEqual(
            (self.repo / "shared.txt").read_bytes(),
            b"producer\n",
        )
        self.assertEqual(len(self.range_shas("HEAD", REF)), 2)
        self.plant_record()
        proc = self.land()
        text = self.combined(proc)
        self.assertEqual(proc.returncode, 3, text)
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(self.porcelain(), "", "abort left a dirty tree")
        cherry = self.repo / ".git" / "CHERRY_PICK_HEAD"
        self.assertFalse(cherry.exists(), "CHERRY_PICK_HEAD remains")
        self.assertTrue(
            (self.repo / "landed.txt").is_file(),
            "first commit was not kept",
        )
        self.assertEqual(
            (self.repo / "landed.txt").read_text(encoding="utf-8"),
            "first\n",
        )
        self.assertEqual(
            (self.repo / "shared.txt").read_text(encoding="utf-8"),
            "lineage\n",
        )
        kept = self.range_shas(start, "HEAD")
        self.assertEqual(len(kept), 1, kept)
        self.assertEqual(self.subject("HEAD"), "producer-one")
        self.assertEqual(self.author("HEAD"), LANE_AUTHOR)
        stdout_lines = [
            ln for ln in (proc.stdout or "").splitlines() if ln.strip()
        ]
        stderr_lines = [
            ln for ln in (proc.stderr or "").splitlines() if ln.strip()
        ]
        last = None
        if stdout_lines:
            last = stdout_lines[-1]
        elif stderr_lines:
            last = stderr_lines[-1]
        self.assertIsNotNone(last, f"abort printed no last line: {text!r}")
        self.assertIn(conflicted, last)
        self.assertRegex(last, r"remainder[=:\s]+1")
        rows = self.landed_rows(proc.stdout or "")
        self.assertTrue(
            any(subj == "producer-one" for _, subj in rows),
            f"first land not reported: {text!r}",
        )
        still = self.git("cat-file", "-e", first, check=False)
        self.assertEqual(still.returncode, 0, "first producer sha lost")


class Push(_LandFixture):
    """6. --push updates origin after n>=1; without it, origin stays."""

    def test_without_push_origin_is_unchanged(self):
        self.ready()
        pre = self.plant_bare_origin()
        proc = self.land()
        stdout = proc.stdout or ""
        self.assertEqual(proc.returncode, 0, self.combined(proc))
        self.assertEqual(
            self.summary_line(stdout),
            f"landed n=1 ref={REF}",
            stdout,
        )
        self.assertNotEqual(self.sha("HEAD"), self.base)
        self.assertEqual(self.origin_sha(), pre)
        self.assertNotEqual(self.origin_sha(), self.sha("HEAD"))

    def test_push_updates_origin_after_a_successful_land(self):
        self.ready()
        pre = self.plant_bare_origin()
        proc = self.land(extra=["--push"])
        stdout = proc.stdout or ""
        self.assertEqual(proc.returncode, 0, self.combined(proc))
        self.assertEqual(
            self.summary_line(stdout),
            f"landed n=1 ref={REF}",
            stdout,
        )
        self.assertNotEqual(self.sha("HEAD"), pre)
        self.assertEqual(self.origin_sha(), self.sha("HEAD"))

    def test_push_failure_is_a_finding_and_keeps_the_landing(self):
        self.ready()
        self.git("remote", "add", "origin", "/no/such/land-origin")
        # Adjudicated twice at review: --no-fetch presumes prior fetch
        # evidence, so the last-known origin ref is planted at HEAD; the
        # broken remote then breaks only the push.
        self.git("update-ref", f"refs/remotes/origin/{BRANCH}", "HEAD")
        proc = self.land(extra=["--push", "--no-fetch"])
        self.assertEqual(proc.returncode, 1, self.combined(proc))
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotEqual(proc.returncode, 2)
        self.assertNotEqual(proc.returncode, 3)
        self.assertNotEqual(self.sha("HEAD"), self.base)
        self.assertEqual(self.subject("HEAD"), SUBJECT)
        self.assertEqual(
            (self.repo / "landed.txt").read_text(encoding="utf-8"),
            "landed-body\n",
        )


class EnvelopeStatus(_LandFixture):
    def test_invalid_envelope_status_refuses_before_any_pick(self):
        # The live proof: record adec30 carried envelope.status invalid
        # (schema-invalid answer) while its exit read 0.
        self.ready()
        envelope = (
            self.repo / ".dev" / "records" / "dispatches" / f"{REC}.json"
        )
        payload = json.dumps(
            {
                "lineage": {"branch": BRANCH},
                "result": {"envelope": {"status": "invalid"}},
            },
            indent=2,
        ) + "\n"
        self._plant_bytes(envelope, payload.encode("utf-8"))
        self.git("add", str(envelope.relative_to(self.repo)))
        self.git("commit", "-m", f"invalid envelope\n\n{TRAILER}")
        pre = self.sha("HEAD")
        proc = self.land()
        self.assert_refusal(proc, "envelope-status")
        self.assertEqual(self.sha("HEAD"), pre, "nothing may land")


class FreshnessThreeWay(_LandFixture):
    def test_no_fetch_with_remote_but_no_ref_refuses_stale(self):
        self.ready()
        self.git("remote", "add", "origin", "/no/such/land-origin")
        proc = self.land(extra=["--no-fetch"])
        self.assert_refusal(proc, "stale")

    def test_upstream_without_the_branch_is_fresh_evidence(self):
        self.ready()
        bare = self.scratch / "fresh-origin.git"
        bare.mkdir()
        init = self.git("init", "--bare", "-b", BRANCH, cwd=bare, check=False)
        self.assertEqual(init.returncode, 0, init.stderr)
        self.git("remote", "add", "origin", str(bare))
        proc = self.land()
        self.assertEqual(proc.returncode, 0, self.combined(proc))
        self.assertIn("landed n=1", proc.stdout or "")

    def test_unreachable_remote_refusal_is_one_line(self):
        self.ready()
        self.git("remote", "add", "origin", "/no/such/land-origin")
        proc = self.land()
        self.assert_refusal(proc, "stale")


class CollectedTrip(_LandFixture):
    """A tripped dispatch whose tree was collected lands; others refuse.

    Defect 12: exit-status and envelope-status accept a result.collected
    mark whose verify.exit is 0 and whose head is the dispatch ref.
    Uncollected trips, a mismatched collected head, and a failing
    verify still refuse. Existing ExitStatusRefusal and EnvelopeStatus
    cases stay the pin for records with no collection.
    """

    COLLECTED_AT = "2026-09-03T22:56:29Z"
    VERIFY_CMD = "test -f landed.txt"

    def collected_payload(
        self,
        *,
        head: str,
        verify_exit,
        status: str = "invalid",
        reason: str | None = "tripped before commit",
    ) -> dict:
        return {
            "lineage": {"branch": BRANCH},
            "result": {
                "envelope": {"status": status},
                "collected": {
                    "at": self.COLLECTED_AT,
                    "by": AGENT,
                    "head": head,
                    "verify": {"command": self.VERIFY_CMD, "exit": verify_exit},
                    "reason": reason,
                },
            },
        }

    def commit_working_tree_envelope(self, payload: dict) -> Path:
        envelope = (
            self.repo / ".dev" / "records" / "dispatches" / f"{REC}.json"
        )
        data = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
        self._plant_bytes(envelope, data)
        loaded = json.loads(envelope.read_text(encoding="utf-8"))
        self.assertEqual(loaded, json.loads(data.decode("utf-8")))
        self.git("add", str(envelope.relative_to(self.repo)))
        self.git("commit", "-m", f"plant collected envelope\n\n{TRAILER}")
        self.assertEqual(self.porcelain(), "")
        self.assertTrue(envelope.is_file())
        return envelope

    def seed_tripped_producers(self) -> list[str]:
        self.seed_repo()
        shas = self.plant_producers(
            [("landed.txt", "landed-body\n", SUBJECT, True)]
        )
        self.plant_record(exit_code="137")
        planted = (self.store / REC / "exit").read_text(encoding="utf-8")
        self.assertEqual(planted.strip(), "137")
        self.assertNotEqual(planted.strip(), "0")
        self.assertEqual(len(shas), 1)
        return shas

    def test_a_tripped_record_with_a_collected_result_lands(self):
        producers = self.seed_tripped_producers()
        producer = producers[0]
        original = self.message(producer)
        self.assertIn(TRAILER, original)
        self.assertNotIn("cherry picked from commit", original)
        tip = self.sha(REF)
        self.assertEqual(tip, producer)
        envelope = self.commit_working_tree_envelope(
            self.collected_payload(head=tip, verify_exit=0)
        )
        loaded = json.loads(envelope.read_text(encoding="utf-8"))
        collected = loaded["result"]["collected"]
        self.assertEqual(collected["verify"]["exit"], 0)
        self.assertEqual(collected["head"], tip)
        self.assertEqual(loaded["result"]["envelope"]["status"], "invalid")
        self.assertEqual(self.sha(REF), tip)

        proc = self.land()
        stdout = proc.stdout or ""
        self.assertEqual(proc.returncode, 0, self.combined(proc))
        rows = self.landed_rows(stdout)
        self.assertEqual(len(rows), 1, stdout)
        sha, subject = rows[0]
        self.assertEqual(subject, SUBJECT)
        self.assertEqual(self.sha(sha), self.sha("HEAD"))
        self.assertEqual(self.message("HEAD"), original)
        self.assertNotIn("cherry picked from commit", self.message("HEAD"))

    def test_a_tripped_record_without_a_collection_names_the_collect_verb(self):
        self.seed_tripped_producers()
        envelope = (
            self.repo / ".dev" / "records" / "dispatches" / f"{REC}.json"
        )
        loaded = json.loads(envelope.read_text(encoding="utf-8"))
        self.assertNotIn("collected", loaded.get("result", {}))
        proc = self.land()
        self.assert_refusal(proc, "exit-status")
        err = proc.stderr or ""
        self.assertIn("launch.py collect", err)
        self.assertEqual(self.sha("HEAD"), self.base)

    def test_a_collected_head_that_is_not_the_ref_refuses_ref(self):
        self.seed_tripped_producers()
        tip = self.sha(REF)
        wrong = self.base
        self.assertNotEqual(wrong, tip)
        envelope = self.commit_working_tree_envelope(
            self.collected_payload(head=wrong, verify_exit=0)
        )
        loaded = json.loads(envelope.read_text(encoding="utf-8"))
        self.assertEqual(loaded["result"]["collected"]["head"], wrong)
        self.assertEqual(loaded["result"]["collected"]["verify"]["exit"], 0)
        self.assertEqual(self.sha(REF), tip)
        pre = self.sha("HEAD")
        proc = self.land()
        self.assert_refusal(proc, "ref")
        err = proc.stderr or ""
        self.assertIn(wrong, err)
        self.assertIn(tip, err)
        self.assertEqual(self.sha("HEAD"), pre)
        self.assertFalse((self.repo / "landed.txt").exists())

    def test_a_collected_result_with_a_failing_verify_still_refuses(self):
        self.seed_tripped_producers()
        tip = self.sha(REF)
        envelope = self.commit_working_tree_envelope(
            self.collected_payload(head=tip, verify_exit=1)
        )
        loaded = json.loads(envelope.read_text(encoding="utf-8"))
        self.assertEqual(loaded["result"]["collected"]["head"], tip)
        self.assertEqual(loaded["result"]["collected"]["verify"]["exit"], 1)
        self.assertNotEqual(loaded["result"]["collected"]["verify"]["exit"], 0)
        pre = self.sha("HEAD")
        proc = self.land()
        self.assert_refusal(proc, "exit-status")
        err = (proc.stderr or "").lower()
        self.assertRegex(err, r"collect|verify")
        self.assertEqual(self.sha("HEAD"), pre)
        self.assertEqual(self.landed_rows(proc.stdout or ""), [])
        self.assertFalse((self.repo / "landed.txt").exists())

    def test_a_collected_result_with_json_false_verify_exit_is_not_green(self):
        """Security finding 5: JSON false == 0 must not satisfy
        verify.exit is 0."""
        self.seed_tripped_producers()
        tip = self.sha(REF)
        envelope = self.commit_working_tree_envelope(
            self.collected_payload(head=tip, verify_exit=False)
        )
        loaded = json.loads(envelope.read_text(encoding="utf-8"))
        exit_value = loaded["result"]["collected"]["verify"]["exit"]
        self.assertIs(exit_value, False)
        self.assertEqual(exit_value, 0)
        raw = envelope.read_text(encoding="utf-8")
        self.assertRegex(raw, r'"exit":\s*false')
        self.assertNotRegex(raw, r'"exit":\s*0\b')
        pre = self.sha("HEAD")
        proc = self.land()
        self.assertNotEqual(
            proc.returncode, 0,
            "JSON false must not land as a verified collection: "
            f"{self.combined(proc)!r}",
        )
        self.assert_refusal(proc, "exit-status")
        self.assertEqual(self.sha("HEAD"), pre)
        self.assertEqual(self.landed_rows(proc.stdout or ""), [])
        self.assertFalse((self.repo / "landed.txt").exists())


class EnvelopeStatusNamesCollect(_LandFixture):
    """Adversarial finding 5: envelope-status needed text names
    launch.py collect, never a by-hand adjudication."""

    def test_an_invalid_envelope_with_exit_zero_names_the_collect_verb(self):
        self.ready()
        envelope = (
            self.repo / ".dev" / "records" / "dispatches" / f"{REC}.json"
        )
        payload = json.dumps(
            {
                "lineage": {"branch": BRANCH},
                "result": {"envelope": {"status": "invalid"}},
            },
            indent=2,
        ) + "\n"
        self._plant_bytes(envelope, payload.encode("utf-8"))
        loaded = json.loads(envelope.read_text(encoding="utf-8"))
        self.assertEqual(loaded["result"]["envelope"]["status"], "invalid")
        self.assertNotIn("collected", loaded.get("result", {}))
        exit_text = (self.store / REC / "exit").read_text(encoding="utf-8")
        self.assertEqual(exit_text.strip(), "0")
        self.git("add", str(envelope.relative_to(self.repo)))
        self.git("commit", "-m", f"invalid envelope names collect\n\n{TRAILER}")
        pre = self.sha("HEAD")
        proc = self.land()
        self.assert_refusal(proc, "envelope-status")
        err = proc.stderr or ""
        self.assertIn("launch.py collect", err)
        self.assertNotIn("adjudicate the rejected dispatch by hand", err)
        self.assertEqual(self.sha("HEAD"), pre, "nothing may land")


class LaneNameSelectsStore(_LandFixture):
    """LANE_NAME from the conf selects the store under HOME.

    Unit 1 contract: every state-root reader reads LANE_NAME through
    the reader. Tests plant LANE_CONF and never write the tracked file.
    """

    def test_lane_name_from_conf_selects_the_store(self):
        self.seed_repo()
        self.plant_producers(
            [("landed.txt", "landed-body\n", SUBJECT, True)]
        )
        conf = self.scratch / "lane.conf"
        text = (
            "# scratch lane conf\n"
            "LANE_NAME='lanex'\n"
            "TRUNK_BRANCH='dev'\n"
            "RELEASE_BRANCH='master'\n"
            "REPO_SLUG='uscient/agentscient'\n"
            "CHECK_NAMES='lane app'\n"
            "WF_AGENT_DEFAULT='Conf Agent <conf@example.invalid>'\n"
            f"LANE_AUTHOR='{LANE_AUTHOR}'\n"
            "MODEL_GROK='model-grok'\n"
            "MODEL_CODEX='model-codex'\n"
            "MODEL_CLAUDE_READ='model-claude-read'\n"
            "MODEL_CLAUDE_PLAN='model-claude-plan'\n"
            "EFFORT_GROK='high'\n"
            "EFFORT_CODEX='high'\n"
            "EFFORT_CLAUDE='high'\n"
            "WALL_DEFAULT='900'\n"
            "SCOPE_CAP='1024'\n"
        )
        self._plant_bytes(conf, text.encode("utf-8"))
        landed = conf.read_text(encoding="utf-8")
        self.assertIn("LANE_NAME='lanex'", landed)
        store = self.world.xdg_state / "lanex" / "dispatch"
        rec_dir = store / REC
        rec_dir.mkdir(parents=True)
        self._plant_bytes(rec_dir / "exit", b"0\n")
        self.assertTrue((store / REC / "exit").is_file())
        self.assertFalse(
            (self.store / REC).exists(),
            "plant: agentscient record dir must be absent",
        )
        proc = self.land(extra_env={"LANE_CONF": str(conf)})
        stdout = proc.stdout or ""
        self.assertEqual(proc.returncode, 0, self.combined(proc))
        self.assertEqual(
            self.summary_line(stdout),
            f"landed n=1 ref={REF}",
            stdout,
        )
        self.assertEqual(
            (self.repo / "landed.txt").read_text(encoding="utf-8"),
            "landed-body\n",
        )
        self.assertEqual(self.author("HEAD"), LANE_AUTHOR)


class LaneAuthor(_LandFixture):
    """LANE_AUTHOR is the author of every landed commit. The worker's
    message, including Source and Co-Authored-By trailers, is kept
    verbatim. A commit already authored as LANE_AUTHOR is landed
    unchanged. Missing LANE_AUTHOR or LANE_NAME is conf-key before any
    pick.
    """

    def test_already_correct_author_is_landed_unchanged(self):
        self.assertNotEqual(LANE_AUTHOR, f"{PRODUCER} <{PRODUCER_EMAIL}>")
        self.seed_repo()
        shas = self.plant_producers(
            [("landed.txt", "landed-body\n", SUBJECT, True)],
            author_name=LANE_AUTHOR_NAME,
            author_email=LANE_AUTHOR_EMAIL,
        )
        self.assertEqual(len(shas), 1)
        producer = shas[0]
        self.assertEqual(self.author(producer), LANE_AUTHOR)
        original = self.message(producer)
        self.assertIn("Source:", original)
        self.assertIn("Co-Authored-By:", original)
        self.plant_record()
        proc = self.land()
        self.assertEqual(proc.returncode, 0, self.combined(proc))
        self.assertEqual(self.message("HEAD"), original)
        self.assertEqual(self.author("HEAD"), LANE_AUTHOR)

    def test_author_flag_leaves_a_lane_author_commit_unchanged(self):
        other = Reauthor.AUTHOR
        self.assertNotEqual(other, LANE_AUTHOR)
        self.assertNotEqual(other, f"{PRODUCER} <{PRODUCER_EMAIL}>")
        self.seed_repo()
        shas = self.plant_producers(
            [("landed.txt", "landed-body\n", SUBJECT, True)],
            author_name=LANE_AUTHOR_NAME,
            author_email=LANE_AUTHOR_EMAIL,
        )
        self.assertEqual(len(shas), 1)
        producer = shas[0]
        self.assertEqual(self.author(producer), LANE_AUTHOR)
        original = self.message(producer)
        self.assertEqual(original.count(f"Co-Authored-By: {CO_AUTHOR}"), 1)
        self.plant_record()
        proc = self.land(extra=["--author", other, "--co-author", CO_AUTHOR])
        self.assertEqual(proc.returncode, 0, self.combined(proc))
        self.assertEqual(self.author("HEAD"), LANE_AUTHOR)
        self.assertNotEqual(self.author("HEAD"), other)
        self.assertEqual(self.message("HEAD"), original)
        self.assertEqual(self.committer("HEAD"), AGENT)

    def test_missing_lane_author_refuses_conf_key_before_any_pick(self):
        self.plant_lane_conf(omit=("LANE_AUTHOR",))
        planted = self.conf_path.read_text(encoding="utf-8")
        self.assertNotIn("LANE_AUTHOR=", planted)
        self.ready()
        pre = self.sha("HEAD")
        proc = self.land()
        self.assert_refusal(proc, "conf-key")
        err = proc.stderr or ""
        self.assertIn("LANE_AUTHOR", err)
        self.assertIn(str(self.conf_path), err)
        self.assertEqual(self.sha("HEAD"), pre, "nothing may land")
        self.assertEqual(self.landed_rows(proc.stdout or ""), [])

    def test_missing_lane_name_refuses_conf_key_before_any_pick(self):
        self.plant_lane_conf(omit=("LANE_NAME",))
        planted = self.conf_path.read_text(encoding="utf-8")
        self.assertNotIn("LANE_NAME=", planted)
        self.ready()
        pre = self.sha("HEAD")
        proc = self.land()
        self.assert_refusal(proc, "conf-key")
        err = proc.stderr or ""
        self.assertIn("LANE_NAME", err)
        self.assertIn(str(self.conf_path), err)
        self.assertEqual(self.sha("HEAD"), pre, "nothing may land")
        self.assertEqual(self.landed_rows(proc.stdout or ""), [])


class Reauthor(_LandFixture):
    """--author and --co-author are additive. Without them the lever
    behaves as today. --author sets each landed commit's author and
    keeps the committer from WF_AGENT. --co-author appends
    Co-Authored-By once and does not duplicate one already present.
    Both flags apply to every cherry-picked commit, not only the tip.
    """

    AUTHOR = "Scratch Owner <scratch-owner@example.invalid>"
    CO = "Ladder Coauthor <noreply-ladder@example.invalid>"

    def test_author_sets_the_landed_author_and_keeps_the_wf_agent_committer(self):
        self.assertNotEqual(self.AUTHOR, LANE_AUTHOR)
        self.assertNotEqual(self.AUTHOR, f"{PRODUCER} <{PRODUCER_EMAIL}>")
        producers = self.ready(producers=2)
        self.assertEqual(len(producers), 2)
        proc = self.land(extra=["--author", self.AUTHOR])
        self.assertEqual(proc.returncode, 0, self.combined(proc))
        landed = self.range_shas(self.base, "HEAD")
        self.assertEqual(len(landed), 2, landed)
        self.assertEqual(len(self.landed_rows(proc.stdout or "")), 2)
        for sha in landed:
            self.assertEqual(self.author(sha), self.AUTHOR)
            self.assertNotEqual(self.author(sha), LANE_AUTHOR)
            self.assertEqual(self.committer(sha), AGENT)
            self.assertNotEqual(
                self.committer(sha),
                f"{REPO_USER} <{REPO_EMAIL}>",
            )

    def test_co_author_appends_the_trailer_once(self):
        producers = self.ready(producers=2)
        self.assertEqual(len(producers), 2)
        for sha in producers:
            original = self.message(sha)
            self.assertIn("Co-Authored-By:", original)
            self.assertNotIn(self.CO, original)
        proc = self.land(extra=["--co-author", self.CO])
        self.assertEqual(proc.returncode, 0, self.combined(proc))
        landed = self.range_shas(self.base, "HEAD")
        self.assertEqual(len(landed), 2, landed)
        for sha in landed:
            msg = self.message(sha)
            self.assertIn(f"Co-Authored-By: {self.CO}", msg)
            self.assertEqual(msg.count(f"Co-Authored-By: {self.CO}"), 1)
            self.assertIn("Source:", msg)

    def test_co_author_does_not_duplicate_an_already_present_trailer(self):
        producers = self.ready(producers=2)
        self.assertEqual(len(producers), 2)
        for sha in producers:
            original = self.message(sha)
            self.assertEqual(original.count(f"Co-Authored-By: {CO_AUTHOR}"), 1)
        proc = self.land(extra=["--co-author", CO_AUTHOR])
        self.assertEqual(proc.returncode, 0, self.combined(proc))
        landed = self.range_shas(self.base, "HEAD")
        self.assertEqual(len(landed), 2, landed)
        for sha in landed:
            msg = self.message(sha)
            self.assertEqual(msg.count(f"Co-Authored-By: {CO_AUTHOR}"), 1)

    def test_both_flags_apply_to_every_cherry_picked_commit(self):
        producers = self.ready(producers=2)
        self.assertEqual(len(producers), 2)
        proc = self.land(
            extra=["--author", self.AUTHOR, "--co-author", self.CO],
        )
        self.assertEqual(proc.returncode, 0, self.combined(proc))
        landed = self.range_shas(self.base, "HEAD")
        self.assertEqual(len(landed), 2, landed)
        self.assertEqual(len(self.landed_rows(proc.stdout or "")), 2)
        for sha in landed:
            self.assertEqual(self.author(sha), self.AUTHOR)
            self.assertEqual(self.committer(sha), AGENT)
            msg = self.message(sha)
            self.assertIn(f"Co-Authored-By: {self.CO}", msg)
            self.assertEqual(msg.count(f"Co-Authored-By: {self.CO}"), 1)
            self.assertIn("Source:", msg)


if __name__ == "__main__":
    unittest.main()

