"""Onboard-lever acceptance tests, written from the contracts.

Contracts (only): `ops/conductor/onboard-lever.md` and the inherited
`ops/conductor/instrument-conventions.md`. The tool
`ops/conductor/tools/onboard.py` is the subject and is not read here.
Tests invoke it as a subprocess inside a scratch git repository.

The live dispatch store and this repo must stay untouched — asserted
in tearDown.

The implementation lands in parallel. A missing tool file is one red
(`test_the_tool_file_is_present`). Every other case stages a
copy-tool-bytes stand-in so a missing file is not every test's failure:
when the tool exists its bytes are copied and run; when it does not, a
no-op stub (exit 0, no output) is planted so each assertion goes red on
the contract promise rather than Python's missing-file exit 2.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

from live_store_guard import assert_unchanged, without_new_job_dirs

from ops.testkit import World, hermetic_env, run

REPO = Path(__file__).resolve().parents[3]
TOOL = REPO / "ops" / "conductor" / "tools" / "onboard.py"
# The tool imports these beside itself (instrument-conventions.md §The
# shared implementation); a staged copy run from a scratch directory
# needs them staged too, or it refuses `tooling` before the contract.
SIBLINGS = ("instrument.py", "laneconf.py")

BRANCH = "onboard-lineage"
ORIGIN_146 = "LD-146"
REPO_USER = "Repo Config"
REPO_EMAIL = "repo-config@example.invalid"
RUN_CAP = 30

# Contract table, verbatim one-liners, em dash as written there.
# onboard-lever.md §The brief: seven declared rows in table order.
LAW = (
    ("AGENTS.md", "the operating contract"),
    ("CONTRIB.md", "naming, PR bodies, fix commits, the review wire"),
    (
        "ops/conductor/instrument-conventions.md",
        "the lane's base contract: wire, identity, exits",
    ),
    (
        "ops/conductor/stack.md",
        "the stack: layers, incumbents, open seats",
    ),
    ("ops/conductor/coverage-zones.md", "what gates what"),
    (
        "ops/conductor/discovery-lever.md",
        "profile discovery, source custody, and uncertainty",
    ),
    (
        "ops/conductor/conductor-start-plan.md",
        "the conductor startup sequence and admission boundaries",
    ),
)
EM = "—"

# PIN_REGISTER_SHAPE: one labelled line; stub counts and blocked ids
# survive compaction.
# Adjudicated at reconciliation: the sibling is a landed instrument
# whose report is deterministic JSON by its own contract; the stub
# speaks that shape, not an invented one.
REGISTER_STDOUT = (
    '{"blocked":[{"blocked_on":"a PR","id":"D-OPS-9"}],'
    '"counts":{"proposed":3,"ratified":8,"rejected":0,"superseded":1},'
    '"oldest_open":[]}\n'
)
# PIN_LEDGER_SHAPE: the sibling's status line plus an open-grant count.
HAND_STDOUT = (
    "status: open=2, settled=4\nrecurrence by class:\n  other=2\n"
    "debt: open=2, settled-unverified=0, settled-late=0\n"
)
LOCK_STDOUT = (
    '{\n  "branches": [\n    "lane/session-a",\n    "lane/session-b"\n  ],\n'
    '  "why": "owner 2026-09-15: every test family onto the kit",\n'
    '  "by": "Owner <owner@example.test>",\n  "at": "2026-09-15T05:00:00Z"\n}\n'
    "target lane/session-b: until off_kit=0; reading modules=222 on_kit=15 exempt=2 off_kit=205; short\n"
)
PR_NUMBER = "42"
PR_TITLE = "Onboard the lever"
RUNNING_ID = "20260901T120000Z-run-alpha"
CLOSED_OLD = "20260901T100000Z-closed-old"
CLOSED_NEW = "20260901T110000Z-closed-new"
CLOSED_NEW_EXIT = "7"
# Distinctive so a substring of another planted id cannot satisfy a pin.
LIVE_RUN_ID = "20260904T181000Z-code-grok-aa41lv"
DIED_INVALID_ID = "20260904T181100Z-code-grok-aa41dd"
CLOSED_NOEXIT_ID = "20260904T181200Z-code-grok-aa41cl"
UNRECORDED_ID = "20260904T181300Z-code-grok-aa41ur"
UNRECORDED_LIVE_ID = "20260904T181400Z-code-grok-aa41ul"
LAUNCH_NOPID_ID = "20260904T181500Z-code-grok-aa41np"
LAUNCH_DEAD_ID = "20260904T181600Z-code-grok-aa41dp"
AAR_OLD = "AAR-2026-08-01.md"
AAR_NEW = "AAR-2026-09-02.md"
AAR_BARE = "AAR-2026-09-01.md"
DECOY_NAME = "note.md"
CAL_NEW = "NEW_AAR_CALIBRATION_UNIQUE_aa31"
CAL_OLD = "OLD_AAR_CALIBRATION_UNIQUE_aa30"
CAL_BODY = "CALIBRATION_BODY_UNIQUE_i008_7f3c"
AFTER_BODY = "AFTER_SECTION_UNIQUE_9a1b"
DECOY_CAL = "DECOY_CALIBRATION_MUST_NOT_APPEAR"
GH_FAIL = "GH_STUB_FAIL_MARKER: pr list failed"
SIBLING_REFUSAL = (
    "id-shape: expected D-WO-N; found bad-id; needed a well-formed id"
)

# PIN_SECTION_HEADERS: the contract writes `== guidance`, `== state`,
# `== handoffs` and prints them in that order on stdout.
# PIN_OK_ABSENT: `OK <path> — <one-liner>` / `ABSENT <path> — <one-liner>`.
# PIN_FINAL: last non-empty stdout line is `brief complete` or
# `brief complete, N absence(s)` with the (s) as written.
# PIN_NO_THREAD: no `== thread` section, and nothing read from any
# AAR-*.md (T-8, 2026-09-14: the board is `== handoffs`; the AAR that
# sorted last by file name was not the newest on a day with five).
# PIN_STORE: record dir named by id; closed `exit` file holds the
# code; newest closed by id filename order.
# PIN_RUNNING: `dispatches: running=` lists ids whose record status is
# `launched` (or equivalent in-flight) and whose worker pid is alive.
# A `died` or `closed` record is not running, even with no `exit` file
# (looked-and-closed is closed). A job directory without `record.json`
# is unrecorded, never in `running=`. Absence of a pid file plus
# status launched is not inferred live.
# PIN_ABSENT_STORE: naming the missing store is a measurement, not an
# unmeasured source — exit 0 when law and the other sources are OK.
# PIN_GH: `gh pr list --state open` (not `pr checks`); number and title
# each on the prs line.


def _store_snap(path: Path) -> tuple[str, tuple[str, ...]]:
    if not path.exists():
        return ("absent", ())
    if not path.is_dir():
        return ("other", ())
    return ("dir", tuple(sorted(p.name for p in path.iterdir())))


# The live-store comparison and its concurrency exclusions live in
# live_store_guard.py; see ../test-store-guard.md. The name below is the
# one test_store_guard.py binds to.
_without_new_job_dirs = without_new_job_dirs


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


def _git_out(world: World, args: list[str], cwd: Path) -> str:
    proc = run(args, cwd=cwd, env=world.env(), timeout=60)
    if proc.exit != 0:
        raise AssertionError(f"{args} in {cwd}: {proc.stderr}")
    return proc.stdout


def _tree_files(root: Path) -> tuple[str, ...]:
    if not root.exists():
        return ()
    names: list[str] = []
    for path in root.rglob("*"):
        if path.is_file():
            names.append(str(path.relative_to(root)))
    return tuple(sorted(names))


def _aar_with_calibration(body: str, after: str | None = None) -> str:
    text = f"# After-action\n\n## The calibration clause\n\n{body}\n"
    if after is not None:
        text += f"\n## After\n\n{after}\n"
    return text


def _aar_without_calibration() -> str:
    return "# After-action\n\n## Failures\n\nno clause here\n"


def ok_line(path: str, one_liner: str) -> str:
    return f"OK {path} {EM} {one_liner}"


def absent_line(path: str, one_liner: str) -> str:
    return f"ABSENT {path} {EM} {one_liner}"


class _OnboardFixture(unittest.TestCase):
    """Scratch HOME + git repo, staged tool copy, live store frozen."""

    def setUp(self):
        self.world = World(prefix="onboard-lever-")
        self.addCleanup(self.world.close)
        self.world.require("git")
        self._live_before = {str(p): _store_snap(p) for p in _live_store_paths()}
        self._head_before = _git_out(self.world, ["git", "rev-parse", "HEAD"], REPO).strip()
        self._diff_before = run(
            ["git", "diff", "HEAD"], cwd=REPO, env=self.world.env(), timeout=60
        ).stdout
        self.root = self.world.root
        self.home = self.world.home
        self.scratch = self.world.tmp
        self.bin = self.world.bin
        self.repo = self.world.cwd
        self.xdg = self.world.xdg_state
        self.store = self.home / ".local" / "state" / "agentscient" / "dispatch"
        self.xdg_store = self.xdg / "agentscient" / "dispatch"
        self.assertFalse(
            self.store.exists(),
            "plant: isolated store must start absent",
        )
        self.gh = self.bin / "gh"
        self._stage_tool_bytes()
        self.assertFalse(self.gh.exists(), "plant: gh stub is not planted yet")

    def tearDown(self):
        assert_unchanged(
            self,
            self._live_before,
            {str(p): _store_snap(p) for p in _live_store_paths()},
        )
        self.assertEqual(
            _git_out(self.world, ["git", "rev-parse", "HEAD"], REPO).strip(),
            self._head_before,
            "real repo HEAD moved",
        )
        diff_after = run(
            ["git", "diff", "HEAD"], cwd=REPO, env=self.world.env(), timeout=60
        ).stdout
        self.assertEqual(
            diff_after,
            self._diff_before,
            "real repo tracked files mutated",
        )

    def _stage_tool_bytes(self) -> None:
        self.staged = self.world.stage(TOOL, "onboard.py", siblings=SIBLINGS)
        self.assertTrue(self.staged.is_file())
        # The staged copy has no tracked conf beside its reader; the
        # fixture names one, so the lane under HOME is the one asserted.
        self.conf = self.staged.parent.parent / "lane.conf"
        self.world.plant_text(self.conf, "LANE_NAME='agentscient'\n")

    def _plant_bytes(self, path: Path, data: bytes) -> None:
        self.world.plant_bytes(path, data)
        landed = path.read_bytes()
        self.assertEqual(landed, data, f"plant did not land intact: {path}")
        self.assertEqual(len(landed), len(data))

    def git_env(self) -> dict[str, str]:
        return self.world.env()

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
            timeout=60,
        )
        if check:
            self.assertEqual(
                proc.exit,
                0,
                f"git {args}: {proc.stderr or proc.stdout}",
            )
        return proc

    def ensure_git(self) -> None:
        if (self.repo / ".git").exists():
            return
        init = self.git("init", "-b", BRANCH, cwd=self.repo, check=False)
        self.assertEqual(init.exit, 0, init.stderr)
        self.git("config", "user.name", REPO_USER)
        self.git("config", "user.email", REPO_EMAIL)
        name = self.git("config", "--get", "user.name").stdout.strip()
        email = self.git("config", "--get", "user.email").stdout.strip()
        self.assertEqual(name, REPO_USER)
        self.assertEqual(email, REPO_EMAIL)
        seed = self.repo / "README"
        self._plant_bytes(seed, b"scratch onboard repo\n")
        self.git("add", "README")
        self.git("commit", "-m", "seed\n")
        head_branch = self.git("symbolic-ref", "--short", "HEAD").stdout.strip()
        self.assertEqual(head_branch, BRANCH)
        self.assertEqual(self.git("status", "--porcelain").stdout, "")

    def plant_law(self, *, omit: tuple[str, ...] = (), empty: tuple[str, ...] = ()):
        omit_set = set(omit)
        empty_set = set(empty)
        for path, _one in LAW:
            dest = self.repo / path
            if path in omit_set:
                self.assertFalse(dest.exists(), f"omit did not leave {path} absent")
                continue
            if path in empty_set:
                self._plant_bytes(dest, b"")
                self.assertEqual(dest.stat().st_size, 0, f"{path} is not empty")
                continue
            payload = f"law body for {path}\n".encode()
            self._plant_bytes(dest, payload)
            self.assertGreater(dest.stat().st_size, 0, f"{path} stayed empty")
        for path in omit:
            self.assertFalse(
                (self.repo / path).exists(),
                f"INVALID: omitted law file landed: {path}",
            )

    def _plant_tool(
        self,
        name: str,
        paths: tuple[Path, ...],
        *,
        stdout: str = "",
        stderr: str = "",
        exit_code: int = 0,
    ) -> None:
        fake = self.world.tools.plant(
            name, stdout=stdout, stderr=stderr, exit=exit_code
        )
        for path in paths:
            self._plant_bytes(path, fake.read_bytes())

    def plant_siblings(
        self,
        *,
        register_stdout: str = REGISTER_STDOUT,
        register_stderr: str = "",
        register_exit: int = 0,
        ledger_stdout: str = HAND_STDOUT,
        ledger_stderr: str = "",
        ledger_exit: int = 0,
        lock_stdout: str = LOCK_STDOUT,
        lock_stderr: str = "",
        lock_exit: int = 0,
    ) -> None:
        locations = (self.staged.parent, self.repo / "ops" / "conductor" / "tools")
        for folder in locations:
            folder.mkdir(parents=True, exist_ok=True)
        self._plant_tool(
            "decisions",
            tuple(folder / "decisions.py" for folder in locations),
            stdout=register_stdout,
            stderr=register_stderr,
            exit_code=register_exit,
        )
        self._plant_tool(
            "hand",
            tuple(folder / "hand.py" for folder in locations),
            stdout=ledger_stdout,
            stderr=ledger_stderr,
            exit_code=ledger_exit,
        )
        self._plant_tool(
            "deliverable",
            tuple(folder / "deliverable.py" for folder in locations),
            stdout=lock_stdout,
            stderr=lock_stderr,
            exit_code=lock_exit,
        )

    def plant_gh(
        self,
        *,
        number: str = PR_NUMBER,
        title: str = PR_TITLE,
        exit_code: int = 0,
        stderr: str = "",
        rows: list | None = None,
    ) -> None:
        payload = json.dumps(
            rows if rows is not None
            else [{"number": int(number), "title": title}]
        ) + "\n"
        self.world.tools.plant(
            "gh", stdout=payload, stderr=stderr, exit=exit_code
        )
        self.assertTrue(self.gh.is_file(), "gh fake did not land")

    def plant_dispatch(
        self,
        *,
        store: Path | None = None,
        running: tuple[str, ...] = (),
        closed: tuple[tuple[str, str], ...] = (),
    ) -> Path:
        dest = store if store is not None else self.store
        dest.mkdir(parents=True, exist_ok=True)
        self.assertTrue(dest.is_dir(), "INVALID: dispatch store plant did not land")
        for rec_id in running:
            rec = dest / rec_id
            rec.mkdir(parents=True)
            marker = rec / "launched"
            self._plant_bytes(marker, b"running\n")
            self.plant_live_launched(rec_id, store=dest)
            self.assertFalse(
                (rec / "exit").exists(),
                f"INVALID: running record has an exit file: {rec_id}",
            )
        for rec_id, code in closed:
            rec = dest / rec_id
            rec.mkdir(parents=True)
            self._plant_bytes(rec / "exit", f"{code}\n".encode("utf-8"))
            self.assertTrue((rec / "exit").is_file())
        return dest

    def spawn_live(self) -> int:
        pid = os.getpid()
        os.kill(pid, 0)
        return pid

    def dead_pid(self) -> int:
        pid_max = int(Path("/proc/sys/kernel/pid_max").read_text(encoding="utf-8"))
        pid = pid_max + 1
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        return pid

    def plant_record_json(
        self,
        job_dir: Path,
        rec_id: str,
        *,
        status: str,
        extra: dict | None = None,
    ) -> dict:
        payload: dict = {"id": rec_id, "status": status}
        if extra:
            payload.update(extra)
        raw = json.dumps(payload, indent=2) + "\n"
        path = job_dir / "record.json"
        self._plant_bytes(path, raw.encode("utf-8"))
        landed = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(landed["id"], rec_id, "plant: record id")
        self.assertEqual(landed["status"], status, "plant: record status")
        return landed

    def plant_state_pid(self, job_dir: Path, pid: int) -> None:
        state = {"pid": pid, "pgid": pid}
        raw = json.dumps(state) + "\n"
        path = job_dir / "state.json"
        self._plant_bytes(path, raw.encode("utf-8"))
        landed = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(landed["pid"], pid, "plant: state.json pid")

    def plant_live_launched(
        self, rec_id: str, *, store: Path | None = None
    ) -> Path:
        dest = store if store is not None else self.store
        dest.mkdir(parents=True, exist_ok=True)
        job_dir = dest / rec_id
        job_dir.mkdir(parents=True, exist_ok=True)
        self.plant_record_json(job_dir, rec_id, status="launched")
        pid = self.spawn_live()
        self.plant_state_pid(job_dir, pid)
        self.assertFalse(
            (job_dir / "exit").exists(),
            f"INVALID: live launched has an exit file: {rec_id}",
        )
        os.kill(pid, 0)
        return job_dir

    def plant_job(
        self,
        rec_id: str,
        *,
        store: Path | None = None,
        status: str | None = None,
        extra: dict | None = None,
        with_record: bool = True,
        pid: int | None = None,
    ) -> Path:
        dest = store if store is not None else self.store
        dest.mkdir(parents=True, exist_ok=True)
        job_dir = dest / rec_id
        job_dir.mkdir(parents=True, exist_ok=True)
        if with_record:
            self.assertIsNotNone(status, "INVALID: record plant needs status")
            self.plant_record_json(
                job_dir, rec_id, status=str(status), extra=extra
            )
        else:
            marker = job_dir / "note.txt"
            self._plant_bytes(marker, b"no record copy\n")
            self.assertFalse(
                (job_dir / "record.json").exists(),
                f"INVALID: unrecorded plant grew a record.json: {rec_id}",
            )
        if pid is not None:
            self.plant_state_pid(job_dir, pid)
        else:
            self.assertFalse(
                (job_dir / "state.json").exists(),
                f"INVALID: pid-less plant has state.json: {rec_id}",
            )
            self.assertFalse(
                (job_dir / "pid").exists(),
                f"INVALID: pid-less plant has a pid file: {rec_id}",
            )
        self.assertFalse(
            (job_dir / "exit").exists(),
            f"INVALID: exit file present for {rec_id}",
        )
        return job_dir

    def running_field(self, stdout: str) -> str:
        line = self.labelled_line(stdout, "dispatches")
        self.assertIn("running=", line, f"dispatches line has no running=: {line!r}")
        after = line.split("running=", 1)[1]
        for sep in (";", " newest-closed=", " unrecorded=", " closed="):
            if sep in after:
                after = after.split(sep, 1)[0]
        nxt = re.search(r"\s+[a-z][a-z0-9-]*=", after)
        if nxt:
            after = after[: nxt.start()]
        return after.strip()

    def running_ids(self, stdout: str) -> list[str]:
        field = self.running_field(stdout)
        if not field:
            return []
        return [tok for tok in re.split(r"[,\s]+", field) if tok]

    def assert_running_only(
        self,
        stdout: str,
        *,
        live: tuple[str, ...],
        decoys: tuple[str, ...],
    ) -> None:
        found = self.running_ids(stdout)
        field = self.running_field(stdout)
        for rec_id in decoys:
            self.assertNotIn(
                rec_id,
                found,
                f"{rec_id} must not be in running={found!r} field={field!r}",
            )
            self.assertNotIn(
                rec_id,
                field,
                f"{rec_id} must not appear in running= field {field!r}",
            )
        for rec_id in live:
            self.assertIn(
                rec_id,
                found,
                f"{rec_id} missing from running={found!r} field={field!r}",
            )

    def plant_aars(self, files: dict[str, str], *, mtimes: dict[str, int] | None = None):
        folder = self.repo / "ops" / "conductor"
        folder.mkdir(parents=True, exist_ok=True)
        for name, body in files.items():
            path = folder / name
            self._plant_bytes(path, body.encode("utf-8"))
        if mtimes:
            for name, stamp in mtimes.items():
                path = folder / name
                os.utime(path, (stamp, stamp))
                self.assertAlmostEqual(
                    path.stat().st_mtime, stamp, delta=2, msg=str(path)
                )

    def commit_plant(self) -> None:
        self.git("add", "-A")
        porcelain = self.git("status", "--porcelain").stdout
        if porcelain.strip():
            self.git("commit", "-m", "plant\n")

    def ready(
        self,
        *,
        omit_law: tuple[str, ...] = (),
        empty_law: tuple[str, ...] = (),
        aars: dict[str, str] | None = None,
        aar_mtimes: dict[str, int] | None = None,
        running: tuple[str, ...] | None = (RUNNING_ID,),
        closed: tuple[tuple[str, str], ...] | None = (
            (CLOSED_OLD, "0"),
            (CLOSED_NEW, CLOSED_NEW_EXIT),
        ),
        store: Path | None = None,
        plant_store: bool = True,
        gh_exit: int = 0,
        gh_stderr: str = "",
        register_stderr: str = "",
        register_exit: int = 0,
        ledger_stderr: str = "",
        ledger_exit: int = 0,
        lock_stdout: str = LOCK_STDOUT,
        lock_stderr: str = "",
        lock_exit: int = 0,
    ) -> None:
        self.ensure_git()
        self.plant_law(omit=omit_law, empty=empty_law)
        self.plant_siblings(
            register_stderr=register_stderr,
            register_exit=register_exit,
            ledger_stderr=ledger_stderr,
            ledger_exit=ledger_exit,
            lock_stdout=lock_stdout,
            lock_stderr=lock_stderr,
            lock_exit=lock_exit,
        )
        self.plant_gh(exit_code=gh_exit, stderr=gh_stderr)
        handwork = self.store.parent / "handwork.db"
        handwork.parent.mkdir(parents=True, exist_ok=True)
        if not handwork.exists():
            self._plant_bytes(handwork, b"")
        if plant_store:
            run = () if running is None else running
            close = () if closed is None else closed
            self.plant_dispatch(store=store, running=run, closed=close)
        if aars is None:
            aars = {
                AAR_NEW: _aar_with_calibration(CAL_BODY, AFTER_BODY),
                AAR_OLD: _aar_with_calibration(CAL_OLD),
                DECOY_NAME: _aar_with_calibration(DECOY_CAL),
            }
        self.plant_aars(aars, mtimes=aar_mtimes)
        self.commit_plant()

    def env(self, *, xdg: Path | str | None = None) -> dict[str, str]:
        env = self.world.env({"LANE_CONF": str(self.conf)})
        self.assertNotIn("WF_AGENT", env)
        if xdg is not None:
            self.assertEqual(Path(xdg), self.world.xdg_state)
        else:
            env.pop("XDG_STATE_HOME")
        return env

    def invoke(
        self,
        argv: list[str] | None = None,
        *,
        cwd: Path | None = None,
        xdg: Path | str | None = None,
        extra_env: dict[str, str] | None = None,
    ):
        env = self.env(xdg=xdg)
        if extra_env:
            env.update(extra_env)
        self.assertNotIn("WF_AGENT", env)
        return run(
            [sys.executable, str(self.staged), *(argv or [])],
            cwd=cwd or self.repo,
            env=env,
            timeout=RUN_CAP,
        )

    def combined(self, proc) -> str:
        return f"{proc.stdout or ''}{proc.stderr or ''}"

    def last_line(self, stdout: str) -> str:
        lines = [ln for ln in stdout.splitlines() if ln.strip()]
        self.assertTrue(lines, f"stdout had no non-empty line: {stdout!r}")
        return lines[-1]

    def heading_index(self, stdout: str, heading: str) -> int:
        matches = [
            i for i, ln in enumerate(stdout.splitlines()) if ln.strip() == heading
        ]
        self.assertEqual(
            len(matches),
            1,
            f"want one {heading!r} heading, got {matches} in {stdout!r}",
        )
        return matches[0]

    def law_body_lines(self, stdout: str) -> list[str]:
        lines = stdout.splitlines()
        start = self.heading_index(stdout, "== guidance")
        end = self.heading_index(stdout, "== state")
        self.assertLess(start, end, stdout)
        return [ln for ln in lines[start + 1 : end] if ln.strip()]

    def expected_law_lines(
        self,
        table: tuple[tuple[str, str], ...] | None = None,
        *,
        absent: tuple[str, ...] = (),
        empty: tuple[str, ...] = (),
    ) -> list[str]:
        rows = LAW if table is None else table
        missing = set(absent) | set(empty)
        out: list[str] = []
        for path, one_liner in rows:
            if path in missing:
                out.append(absent_line(path, one_liner))
            else:
                out.append(ok_line(path, one_liner))
        return out

    def assert_exact_law_table(
        self,
        stdout: str,
        table: tuple[tuple[str, str], ...] | None = None,
        *,
        absent: tuple[str, ...] = (),
        empty: tuple[str, ...] = (),
    ) -> None:
        rows = LAW if table is None else table
        found = self.law_body_lines(stdout)
        expected = self.expected_law_lines(
            rows, absent=absent, empty=empty
        )
        self.assertEqual(len(rows), 7, "declared law table is seven rows")
        self.assertEqual(len(found), 7, stdout)
        self.assertEqual(found, expected, stdout)
        self.assertNotEqual(
            found,
            list(expected[1:]) + expected[:1],
            "order pin would accept a rotation of the same rows",
        )
        missing = set(absent) | set(empty)
        for (path, one_liner), line in zip(rows, found, strict=True):
            if path in missing:
                self.assertEqual(line, absent_line(path, one_liner), stdout)
            else:
                self.assertEqual(line, ok_line(path, one_liner), stdout)
                self.assertTrue(
                    line.endswith(f"{EM} {one_liner}"),
                    f"one-liner drift on {path}: {line!r}",
                )

    def labelled_line(self, stdout: str, label: str) -> str:
        prefix = label if label.endswith(":") else f"{label}:"
        found = [ln for ln in stdout.splitlines() if ln.startswith(prefix)]
        self.assertEqual(
            len(found),
            1,
            f"want one {prefix!r} line, got {found!r} in {stdout!r}",
        )
        return found[0]

    def gh_argvs(self) -> list[list[str]]:
        return [row["argv"] for row in self.world.tools.calls("gh")]

    def short_sha(self) -> str:
        out = self.git("rev-parse", "--short", "HEAD").stdout.strip()
        self.assertRegex(out, r"^[0-9a-f]{4,40}$", out)
        return out

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


class ToolPresence(_OnboardFixture):
    """Missing tool file counts once."""

    def test_the_tool_file_is_present(self):
        self.assertTrue(TOOL.is_file(), f"RED: tool is absent: {TOOL}")


class ExactLawTable(_OnboardFixture):
    """Exact seven-row table, order, and one-liners from onboard-lever.md."""

    def test_law_prints_exactly_the_seven_declared_rows_in_table_order(self):
        self.assertEqual(len(LAW), 7)
        self.assertEqual(
            [path for path, _one in LAW],
            [
                "AGENTS.md",
                "CONTRIB.md",
                "ops/conductor/instrument-conventions.md",
                "ops/conductor/stack.md",
                "ops/conductor/coverage-zones.md",
                "ops/conductor/discovery-lever.md",
                "ops/conductor/conductor-start-plan.md",
            ],
        )
        self.ready()
        proc = self.invoke()
        stdout = proc.stdout or ""
        expected = tuple(ok_line(path, one) for path, one in LAW)
        found = self.law_body_lines(stdout)
        self.assertEqual(len(found), 7, stdout)
        self.assertEqual(found, list(expected), stdout)
        self.assertNotEqual(found, list(expected[1:]) + [expected[0]])
        for (_path, one_liner), line in zip(LAW, found, strict=True):
            self.assertTrue(line.endswith(f"{EM} {one_liner}"), line)
        ok_lines = [ln for ln in stdout.splitlines() if ln.startswith("OK ")]
        self.assertEqual(len(ok_lines), 7, stdout)
        self.assertEqual(self.last_line(stdout), "brief complete")
        self.assertEqual(proc.exit, 0, self.combined(proc))


class HappyPath(_OnboardFixture):
    """1. Three sections in order; exit 0 with `brief complete`."""

    def test_three_sections_print_in_order_and_brief_completes(self):
        self.ready()
        proc = self.invoke()
        stdout = proc.stdout or ""
        law_i = self.heading_index(stdout, "== guidance")
        state_i = self.heading_index(stdout, "== state")
        handoffs_i = self.heading_index(stdout, "== handoffs")
        self.assertLess(law_i, state_i, stdout)
        self.assertLess(state_i, handoffs_i, stdout)
        self.assertNotIn("== thread", stdout)
        ok_lines = [ln for ln in stdout.splitlines() if ln.startswith("OK ")]
        self.assertEqual(len(ok_lines), 7, stdout)
        self.assertEqual(len(LAW), 7)
        self.assert_exact_law_table(stdout)
        register = self.labelled_line(stdout, "register")
        self.assertIn("proposed=3", register)
        self.assertIn("ratified=8", register)
        self.assertIn("D-OPS-9", register)
        prs = self.labelled_line(stdout, "prs")
        self.assertIn(PR_NUMBER, prs)
        self.assertIn(PR_TITLE, prs)
        dispatches = self.labelled_line(stdout, "dispatches")
        self.assertIn(RUNNING_ID, dispatches)
        self.assertIn(CLOSED_NEW, dispatches)
        self.assertIn(CLOSED_NEW_EXIT, dispatches)
        branch = self.labelled_line(stdout, "branch")
        self.assertIn(BRANCH, branch)
        self.assertIn(self.short_sha(), branch)
        self.assertNotIn(CAL_BODY, stdout)
        self.assertNotIn(AFTER_BODY, stdout)
        self.assertNotIn(CAL_OLD, stdout)
        self.assertNotIn(DECOY_CAL, stdout)
        self.assertEqual(self.last_line(stdout), "brief complete")
        self.assertEqual(proc.exit, 0, self.combined(proc))
        argvs = self.gh_argvs()
        self.assertTrue(argvs, "gh was not invoked for the prs line")
        for argv in argvs:
            self.assertIn("pr", argv)
            self.assertIn("list", argv)
            self.assertNotIn("checks", argv)


class TheLockLine(_OnboardFixture):
    """Owner, 2026-09-15: a session's compaction summary froze a plan's
    paraphrase of the order and the brief after it carried the handoff's
    version of done, while the brief never printed the lock or its why.
    The state section carries the lock: the branches, the owner's words,
    and every target with its live reading, from deliverable.py show."""

    def plant_lock(self) -> None:
        lock = self.store.parent / "deliverable.json"
        self.world.plant_text(
            lock, '{"branches": ["lane/session-a"], "why": "planted"}\n'
        )

    def test_the_lock_prints_with_its_why_and_every_target_reading(self):
        self.ready()
        self.plant_lock()
        proc = self.invoke()
        stdout = proc.stdout or ""
        lock = self.labelled_line(stdout, "lock")
        self.assertIn("lane/session-a, lane/session-b", lock)
        self.assertIn("owner 2026-09-15: every test family onto the kit", lock)
        target = self.labelled_line(stdout, "target lane/session-b")
        self.assertIn("until off_kit=0; reading modules=222 on_kit=15 exempt=2 off_kit=205; short", target)
        state_i = self.heading_index(stdout, "== state")
        handoffs_i = self.heading_index(stdout, "== handoffs")
        lock_i = next(i for i, ln in enumerate(stdout.splitlines()) if ln.startswith("lock: "))
        self.assertLess(state_i, lock_i)
        self.assertLess(lock_i, handoffs_i)
        self.assertEqual(proc.exit, 0, self.combined(proc))

    def test_no_lock_is_a_measured_fact_and_the_lever_is_not_run(self):
        self.ready(lock_stdout="LEVER_RAN_UNIQUE_9c1e\n")
        self.assertFalse((self.store.parent / "deliverable.json").exists(), "INVALID: a lock was planted")
        proc = self.invoke()
        stdout = proc.stdout or ""
        self.assertEqual(self.labelled_line(stdout, "lock"), "lock: none")
        self.assertNotIn("LEVER_RAN_UNIQUE_9c1e", stdout, "the lever is not run when the lock file is absent")
        self.assertEqual(proc.exit, 0, self.combined(proc))

    def test_a_lever_that_fails_is_unavailable_and_counts_an_absence(self):
        self.ready(lock_stdout="", lock_stderr="tooling: expected instrument.py beside this tool; found absent; needed restore it",
                   lock_exit=2)
        self.plant_lock()
        proc = self.invoke()
        line = self.labelled_line(proc.stdout or "", "lock")
        self.assertIn("unavailable", line)
        self.assertIn("tooling", line)
        self.assertEqual(proc.exit, 1, self.combined(proc))

    def plant_slow_lock_lever(self, seconds: float) -> None:
        """A deliverable.py that answers, but later than the first bound."""
        fake = self.world.tools.plant(
            "deliverable-slow", stdout=LOCK_STDOUT, exit=0, delay=seconds,
        )
        for folder in (self.staged.parent,
                       self.repo / "ops" / "conductor" / "tools"):
            self._plant_bytes(folder / "deliverable.py", fake.read_bytes())

    def test_a_lock_read_that_times_out_is_retried_once_with_a_longer_bound(self):
        """Origin LD-48: the brief printed the lock line unavailable after a
        5s bound expired under five concurrent sessions, while
        deliverable.py show answered on a direct retry a moment later.
        Contention on the store read as an absent lock. The read is retried
        once, with a longer bound, before anything is reported."""
        self.ready()
        self.plant_lock()
        self.plant_slow_lock_lever(1.5)
        proc = self.invoke(extra_env={"ONBOARD_TIMEOUT": "1",
                                      "ONBOARD_RETRY_TIMEOUT": "20"})
        # The fake logs every invocation before it sleeps, so the log
        # separates a read that was retried from one that answered first
        # time. Without the count this row passes at the base tree, where
        # the bound is not overridable and 1.5s fits inside the standing 5s.
        calls = self.world.tools.calls("deliverable-slow")
        self.assertEqual(len(calls), 2,
                         f"the lock read was not retried: {calls}")
        line = self.labelled_line(proc.stdout or "", "lock")
        self.assertIn("lane/session-a", line)
        self.assertNotIn("UNREACHABLE", line)
        self.assertNotIn("unavailable", line)
        self.assertEqual(proc.exit, 0, self.combined(proc))

    def test_a_lock_read_that_times_out_twice_is_unreachable_never_none(self):
        """The other half of LD-48: when the longer retry expires too, the
        line says UNREACHABLE and counts an absence. It never says none,
        which is the answer for a lock that is not set, and it never says
        unavailable, which is the answer for a lever that did report."""
        self.ready()
        self.plant_lock()
        self.plant_slow_lock_lever(3)
        proc = self.invoke(extra_env={"ONBOARD_TIMEOUT": "1",
                                      "ONBOARD_RETRY_TIMEOUT": "2"})
        stdout = proc.stdout or ""
        line = self.labelled_line(stdout, "lock")
        self.assertIn("UNREACHABLE", line)
        self.assertIn("timed out", line)
        self.assertNotIn("lock: none", stdout)
        self.assertEqual(proc.exit, 1, self.combined(proc))

    def plant_unmeasured_lock(self) -> Path:
        """The real lever beside the brief, and a lock whose one target's
        measure is a logged tool that would answer after a minute."""
        tools = self.repo / "ops" / "conductor" / "tools"
        for name in ("deliverable.py", "instrument.py", "laneconf.py"):
            self._plant_bytes(tools / name, (TOOL.parent / name).read_bytes())
        measure = self.world.tools.plant("slow-measure", stdout="never\n", exit=0, delay=60)
        lock = self.store.parent / "deliverable.json"
        self.world.plant_text(lock, json.dumps({
            "branches": ["lane/planted"], "why": "planted",
            "targets": {"lane/planted": {"measure": str(measure), "until": "never"}},
        }) + "\n")
        return lock

    def test_the_brief_reads_the_lock_unmeasured_inside_its_bound(self):
        """origin: LD-146

        A lock whose measure is slow no longer holds the brief: the lock
        is read with ``show --unmeasured``, every target reads unmeasured,
        and the measure is never run. The bound here is 3 s then 6 s."""
        self.ready()
        self.plant_unmeasured_lock()
        with self.subTest(id="onboard-lock-unmeasured", origin=ORIGIN_146):
            proc = self.invoke(extra_env={"ONBOARD_TIMEOUT": "3",
                                          "ONBOARD_RETRY_TIMEOUT": "6"})
            stdout = proc.stdout or ""
            target = self.labelled_line(stdout, "target lane/planted")
            self.assertTrue(target.startswith("target lane/planted: until never; reading unmeasured; unmeasured"))
            self.assertIn("measurement absent", target)
            self.assertIn("applicability unknown", target)
            self.assertIn("lane/planted", self.labelled_line(stdout, "lock"))
            self.assertEqual(self.world.tools.calls("slow-measure"), [])
            self.assertEqual(self.last_line(stdout), "brief complete")
            self.assertEqual(proc.exit, 0, self.combined(proc))

    def test_the_brief_asks_the_lever_for_show_unmeasured(self):
        """origin: LD-146

        The argv the brief gives the lever: a logged lever answers the lock
        to ``show --unmeasured`` at once and to any other argv after a
        minute, so a brief that asks for the measured form is unreachable."""
        self.ready()
        self.plant_lock()
        fake = self.world.tools.plant(
            "deliverable-argv", stdout=LOCK_STDOUT, exit=0, delay=60,
            responses=[{"match": ["show --unmeasured"], "stdout": LOCK_STDOUT, "exit": 0, "delay": 0}],
        )
        for folder in (self.staged.parent, self.repo / "ops" / "conductor" / "tools"):
            self._plant_bytes(folder / "deliverable.py", fake.read_bytes())
        with self.subTest(id="onboard-lock-unmeasured-argv", origin=ORIGIN_146):
            proc = self.invoke(extra_env={"ONBOARD_TIMEOUT": "3",
                                          "ONBOARD_RETRY_TIMEOUT": "6"})
            argvs = [row["argv"] for row in self.world.tools.calls("deliverable-argv")]
            self.assertEqual(argvs, [["show", "--unmeasured"]])
            self.assertIn("lane/session-a", self.labelled_line(proc.stdout or "", "lock"))
            self.assertEqual(proc.exit, 0, self.combined(proc))


class MissingLaw(_OnboardFixture):
    """2. A missing law file prints ABSENT and counts it; exit 1."""

    def test_missing_law_file_prints_absent_and_counts_it(self):
        missing = "ops/conductor/coverage-zones.md"
        one_liner = "what gates what"
        self.ready(omit_law=(missing,))
        self.assertFalse((self.repo / missing).exists(), "INVALID: omit failed")
        proc = self.invoke()
        stdout = proc.stdout or ""
        self.assertIn(absent_line(missing, one_liner), stdout)
        self.assertNotIn(ok_line(missing, one_liner), stdout)
        self.assert_exact_law_table(stdout, absent=(missing,))
        self.assertEqual(self.last_line(stdout), "brief complete, 1 absence(s)")
        self.assertEqual(proc.exit, 1, self.combined(proc))

    def test_missing_discovery_lever_counts_one_absence_in_table_order(self):
        missing = "ops/conductor/discovery-lever.md"
        one_liner = "profile discovery, source custody, and uncertainty"
        self.ready(omit_law=(missing,))
        self.assertFalse((self.repo / missing).exists(), "INVALID: omit failed")
        proc = self.invoke()
        stdout = proc.stdout or ""
        self.assertIn(absent_line(missing, one_liner), stdout)
        self.assertNotIn(ok_line(missing, one_liner), stdout)
        self.assert_exact_law_table(stdout, absent=(missing,))
        self.assertEqual(self.last_line(stdout), "brief complete, 1 absence(s)")
        self.assertEqual(proc.exit, 1, self.combined(proc))


class EmptyLawFile(_OnboardFixture):
    """Empty is not present: a zero-byte law file is ABSENT."""

    def test_empty_law_file_prints_absent(self):
        empty = "AGENTS.md"
        one_liner = "the operating contract"
        self.ready(empty_law=(empty,))
        dest = self.repo / empty
        self.assertTrue(dest.is_file(), "INVALID: empty plant missing")
        self.assertEqual(dest.stat().st_size, 0, "INVALID: empty plant not empty")
        proc = self.invoke()
        stdout = proc.stdout or ""
        self.assertIn(absent_line(empty, one_liner), stdout)
        self.assertNotIn(ok_line(empty, one_liner), stdout)
        self.assert_exact_law_table(stdout, empty=(empty,))
        self.assertEqual(self.last_line(stdout), "brief complete, 1 absence(s)")
        self.assertEqual(proc.exit, 1, self.combined(proc))


class GhFailure(_OnboardFixture):
    """3. Stubbed gh failure prints `gh unavailable` on prs; exit 1."""

    def test_stubbed_gh_failure_prints_unavailable_and_exits_1(self):
        self.ready(gh_exit=1, gh_stderr=GH_FAIL)
        proc = self.invoke()
        stdout = proc.stdout or ""
        prs = self.labelled_line(stdout, "prs")
        self.assertIn("gh unavailable:", prs)
        self.assertIn(GH_FAIL, prs)
        self.assertEqual(proc.exit, 1, self.combined(proc))
        self.assertIn("brief complete,", self.last_line(stdout))


class DispatchStore(_OnboardFixture):
    """3. Absent store; running listed by id; newest closed shows exit."""

    def test_absent_dispatch_store_says_so(self):
        self.ready(plant_store=False)
        self.assertFalse(self.store.exists(), "INVALID: store was planted")
        proc = self.invoke()
        stdout = proc.stdout or ""
        line = self.labelled_line(stdout, "dispatches")
        self.assertIn("absent", line.lower())
        self.assert_exact_law_table(stdout)
        self.assertEqual(proc.exit, 0, self.combined(proc))
        self.assertEqual(self.last_line(stdout), "brief complete")

    def test_running_dispatch_is_listed_by_id(self):
        self.ready()
        rec = self.store / RUNNING_ID
        self.assertTrue(rec.is_dir(), "INVALID: running record missing")
        self.assertFalse((rec / "exit").exists(), "INVALID: running has exit")
        proc = self.invoke()
        line = self.labelled_line(proc.stdout or "", "dispatches")
        self.assertIn(RUNNING_ID, line)

    def test_newest_closed_record_shows_its_exit_code(self):
        self.ready()
        old = self.store / CLOSED_OLD
        new = self.store / CLOSED_NEW
        self.assertEqual(
            (old / "exit").read_text(encoding="utf-8").strip(),
            "0",
        )
        self.assertEqual(
            (new / "exit").read_text(encoding="utf-8").strip(),
            CLOSED_NEW_EXIT,
        )
        # Newer mtime on the older id so mtime-sort would pick the wrong one.
        os.utime(old / "exit", (2_000_000_000, 2_000_000_000))
        os.utime(new / "exit", (1_700_000_000, 1_700_000_000))
        proc = self.invoke()
        line = self.labelled_line(proc.stdout or "", "dispatches")
        self.assertIn(CLOSED_NEW, line)
        self.assertIn(CLOSED_NEW_EXIT, line)

    def test_xdg_state_home_store_is_the_one_read(self):
        self.ready(plant_store=False)
        xdg_running = "20260901T130000Z-xdg-run"
        self.plant_dispatch(
            store=self.xdg_store,
            running=(xdg_running,),
            closed=(),
        )
        self.assertTrue((self.xdg_store / xdg_running).is_dir())
        self.assertFalse(self.store.exists(), "HOME store must stay unplanted")
        proc = self.invoke(xdg=self.xdg)
        line = self.labelled_line(proc.stdout or "", "dispatches")
        self.assertIn(xdg_running, line)
        self.assertFalse(self.store.exists(), "tool created a HOME store")


class NoThreadSection(_OnboardFixture):
    """4. T-8 (2026-09-14): the `== thread` section is retired. The board
    is `== handoffs`; no AAR is read, named or quoted, however many are
    planted and whatever their order or their clauses say."""

    def test_no_aar_is_named_or_quoted_and_no_thread_heading_prints(self):
        self.ready(
            aars={
                AAR_OLD: _aar_with_calibration(CAL_OLD, "old-after"),
                AAR_NEW: _aar_with_calibration(CAL_NEW, AFTER_BODY),
                DECOY_NAME: _aar_with_calibration(DECOY_CAL),
            },
            aar_mtimes={
                AAR_NEW: 1_700_000_000,
                AAR_OLD: 2_000_000_000,
            },
        )
        proc = self.invoke()
        stdout = proc.stdout or ""
        self.assertNotIn("== thread", stdout)
        self.assertNotIn("calibration", stdout)
        for name in (AAR_NEW, AAR_OLD, AAR_BARE):
            self.assertNotIn(name, stdout)
        for marker in (CAL_NEW, CAL_OLD, AFTER_BODY, DECOY_CAL):
            self.assertNotIn(marker, stdout)
        self.assertEqual(self.last_line(stdout), "brief complete")
        self.assertEqual(proc.exit, 0, self.combined(proc))

    def test_no_aar_at_all_changes_nothing(self):
        self.ready(aars={DECOY_NAME: _aar_with_calibration(DECOY_CAL)})
        self.assertFalse(
            list((self.repo / "ops" / "conductor").glob("AAR-*.md")),
            "INVALID: an AAR-*.md was planted",
        )
        proc = self.invoke()
        stdout = proc.stdout or ""
        self.assertNotIn("no AAR found", stdout)
        self.assertNotIn("== thread", stdout)
        self.assertEqual(self.last_line(stdout), "brief complete")
        self.assertEqual(proc.exit, 0, self.combined(proc))

    def test_an_aar_without_a_clause_prints_nothing_about_it(self):
        self.ready(aars={AAR_BARE: _aar_without_calibration()})
        proc = self.invoke()
        stdout = proc.stdout or ""
        self.assertNotIn("no calibration clause", stdout)
        self.assertNotIn(AAR_BARE, stdout)


class Refusals(_OnboardFixture):
    """5. Any argument → arguments; outside a git work tree → repo."""

    def test_any_argument_refuses_class_arguments(self):
        self.ensure_git()
        proc = self.invoke(["--help"])
        self.assert_refusal(proc, "arguments")

    def test_outside_a_git_work_tree_refuses_class_repo(self):
        nongit = self.root / "nongit"
        nongit.mkdir()
        self.assertFalse((nongit / ".git").exists())
        probe = run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=nongit,
            env=self.git_env(),
            timeout=60,
        )
        self.assertNotEqual(
            probe.exit,
            0,
            f"INVALID: nongit cwd is a work tree: {probe.stdout}",
        )
        outside = self.staged.with_name("onboard-outside.py")
        self._plant_bytes(outside, self.staged.read_bytes())
        env = self.env()
        proc = run(
            [sys.executable, str(outside)],
            cwd=nongit,
            env=env,
            timeout=RUN_CAP,
        )
        self.assert_refusal(proc, "repo")


class ReadOnly(_OnboardFixture):
    """6. A full run creates no store and writes no new HOME-state file."""

    def test_full_run_creates_no_store_and_writes_nothing(self):
        self.ready(plant_store=False)
        self.assertFalse(self.store.exists(), "INVALID: store planted")
        before_state = _tree_files(self.home / ".local" / "state")
        before_home = _tree_files(self.home)
        proc = self.invoke()
        stdout = proc.stdout or ""
        self.heading_index(stdout, "== guidance")
        self.heading_index(stdout, "== state")
        self.heading_index(stdout, "== handoffs")
        self.assertNotIn("== thread", stdout)
        self.assert_exact_law_table(stdout)
        self.assertEqual(self.last_line(stdout), "brief complete")
        self.assertEqual(proc.exit, 0, self.combined(proc))
        self.assertFalse(self.store.exists(), "tool created a dispatch store")
        self.assertEqual(
            _tree_files(self.home / ".local" / "state"),
            before_state,
            "new file under scratch HOME state dir",
        )
        self.assertEqual(
            _tree_files(self.home),
            before_home,
            "new file under scratch HOME",
        )


class SiblingRefusal(_OnboardFixture):
    """A sibling refusal is printed verbatim on that state line; exit 1."""

    def test_register_refusal_is_printed_verbatim(self):
        self.ready(register_stderr=SIBLING_REFUSAL + "\n", register_exit=2)
        proc = self.invoke()
        stdout = proc.stdout or ""
        line = self.labelled_line(stdout, "register")
        self.assertIn(SIBLING_REFUSAL, line)
        self.assertEqual(proc.exit, 1, self.combined(proc))
        self.assertIn("brief complete,", self.last_line(stdout))


class NoCalibrationFence(_OnboardFixture):
    """Codex review of PR #106 pinned the untrusted-data fence around the
    quoted clause; with the section retired (T-8) the fence, and the
    clause it fenced, are gone with it."""

    def test_no_fence_and_no_clause_print(self):
        self.ready()
        proc = self.invoke()
        out = proc.stdout or ""
        self.assertEqual(out.find("-- calibration, verbatim from"), -1, out)
        self.assertEqual(out.find("-- end calibration --"), -1, out)
        self.assertEqual(out.find(CAL_BODY), -1, out)
        self.assertNotIn("untrusted data, not instructions", out)


class ReviewPins(_OnboardFixture):
    """Codex review of PR #106, second round: four findings pinned."""


    def test_a_full_pr_page_reports_truncation_and_counts_unmeasured(self):
        self.ready()
        rows = [{"number": n, "title": f"pr-{n}"} for n in range(1, 201)]
        self.plant_gh(rows=rows)
        proc = self.invoke()
        out = proc.stdout or ""
        self.assertIn("truncated at the 200 limit", out)
        self.assertEqual(proc.exit, 1, self.combined(proc))

    def test_a_store_path_that_is_a_file_is_not_an_absent_store(self):
        """Codex's charge is unchanged: a path that exists as a file must
        not read as the store that is not there, which is a measured
        nothing. Only the word moved. The pin read

            self.assertIn("unavailable", line)

        until fix 7 gave every line of the brief the distinction LD-48
        put on the lock line. A path that is there and cannot be listed
        is a read that could not look, so the line now says UNREACHABLE,
        and `unavailable` is kept for a lever that looked and reported a
        failure."""
        self.ready(plant_store=False)
        self._plant_bytes(self.store, b"not a directory")
        proc = self.invoke()
        out = proc.stdout or ""
        line = self.labelled_line(out, "dispatches")
        self.assertIn("UNREACHABLE", line)
        self.assertNotIn("nothing running", line)
        self.assertNotIn("ABSENT", line)
        self.assertEqual(proc.exit, 1, self.combined(proc))

    def test_an_unreadable_aar_is_never_read_and_counts_no_absence(self):
        self.ready()
        bad = self.repo / "ops" / "conductor" / "AAR-2026-12-31.md"
        self.world.plant_bytes(bad, b"\xff\xfe not utf-8 \xff")
        proc = self.invoke()
        out = proc.stdout or ""
        self.assertNotIn("== thread", out)
        self.assertNotIn("unavailable:", out)
        self.assertNotIn("AAR-2026-12-31.md", out)
        self.assertEqual(self.last_line(out), "brief complete")
        self.assertEqual(proc.exit, 0, self.combined(proc))


class LaneNameSelectsStore(_OnboardFixture):
    """LANE_NAME from the conf selects the dispatch store under HOME."""

    def test_lane_name_from_conf_selects_the_dispatch_store(self):
        self.ready(plant_store=False)
        conf = self.scratch / "lane.conf"
        text = (
            "LANE_NAME='lanex'\n"
            "TRUNK_BRANCH='trunkx'\n"
            "RELEASE_BRANCH='relx'\n"
            "REPO_SLUG='org/projx'\n"
            "CHECK_NAMES='checkx'\n"
            "WF_AGENT_DEFAULT='Conf Agent <conf@example.invalid>'\n"
            "LANE_AUTHOR='Lane Owner <lane-owner@example.invalid>'\n"
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
        rec_id = "20260901T140000Z-lanex-run"
        lanex_store = self.home / ".local" / "state" / "lanex" / "dispatch"
        self.plant_dispatch(store=lanex_store, running=(rec_id,), closed=())
        self.assertTrue((lanex_store / rec_id).is_dir())
        self.assertFalse(
            self.store.exists(),
            "plant: agentscient dispatch store must stay absent",
        )
        proc = self.invoke(extra_env={"LANE_CONF": str(conf)})
        line = self.labelled_line(proc.stdout or "", "dispatches")
        self.assertIn(rec_id, line)
        self.assertFalse(
            self.store.exists(),
            "tool created the literal agentscient store",
        )


class RunningIsLiveLaunched(_OnboardFixture):
    """running= is live launched jobs, not died, closed, or unrecorded.

    Slice onboard-running: looked-and-closed is closed; a job directory
    without record.json is unrecorded, never running. Each case plants a
    live launched control so an empty running= field cannot satisfy the
    negative pin.
    """

    def _ready_store(self) -> None:
        self.ready(running=(), closed=())
        self.assertTrue(self.store.is_dir(), "INVALID: store missing")

    def test_died_invalid_record_is_not_listed_as_running(self):
        self._ready_store()
        self.plant_live_launched(LIVE_RUN_ID)
        died = self.plant_job(
            DIED_INVALID_ID,
            status="died",
            extra={"result": {"envelope": {"status": "invalid"}}},
        )
        rec = json.loads((died / "record.json").read_text(encoding="utf-8"))
        self.assertEqual(rec["status"], "died")
        self.assertEqual(rec["result"]["envelope"]["status"], "invalid")
        self.assertFalse((died / "exit").exists(), "INVALID: died plant has exit")
        proc = self.invoke()
        self.assert_running_only(
            proc.stdout or "",
            live=(LIVE_RUN_ID,),
            decoys=(DIED_INVALID_ID,),
        )

    def test_closed_record_without_exit_is_not_listed_as_running(self):
        self._ready_store()
        self.plant_live_launched(LIVE_RUN_ID)
        closed = self.plant_job(CLOSED_NOEXIT_ID, status="closed")
        rec = json.loads((closed / "record.json").read_text(encoding="utf-8"))
        self.assertEqual(rec["status"], "closed")
        self.assertFalse(
            (closed / "exit").exists(),
            "INVALID: looked-and-closed plant has an exit file",
        )
        proc = self.invoke()
        self.assert_running_only(
            proc.stdout or "",
            live=(LIVE_RUN_ID,),
            decoys=(CLOSED_NOEXIT_ID,),
        )

    def test_job_dir_without_record_json_is_not_listed_as_running(self):
        self._ready_store()
        self.plant_live_launched(LIVE_RUN_ID)
        bare = self.plant_job(UNRECORDED_ID, with_record=False)
        live_unrec = self.plant_job(
            UNRECORDED_LIVE_ID,
            with_record=False,
            pid=self.spawn_live(),
        )
        self.assertFalse((bare / "record.json").exists())
        self.assertFalse((live_unrec / "record.json").exists())
        self.assertTrue((live_unrec / "state.json").is_file())
        proc = self.invoke()
        self.assert_running_only(
            proc.stdout or "",
            live=(LIVE_RUN_ID,),
            decoys=(UNRECORDED_ID, UNRECORDED_LIVE_ID),
        )

    def test_launched_without_pid_file_is_not_inferred_live(self):
        self._ready_store()
        self.plant_live_launched(LIVE_RUN_ID)
        nopid = self.plant_job(LAUNCH_NOPID_ID, status="launched")
        rec = json.loads((nopid / "record.json").read_text(encoding="utf-8"))
        self.assertEqual(rec["status"], "launched")
        self.assertFalse((nopid / "state.json").exists())
        self.assertFalse((nopid / "pid").exists())
        self.assertFalse((nopid / "exit").exists())
        proc = self.invoke()
        self.assert_running_only(
            proc.stdout or "",
            live=(LIVE_RUN_ID,),
            decoys=(LAUNCH_NOPID_ID,),
        )

    def test_launched_with_dead_pid_is_not_listed_as_running(self):
        self._ready_store()
        self.plant_live_launched(LIVE_RUN_ID)
        dead = self.dead_pid()
        job = self.plant_job(LAUNCH_DEAD_ID, status="launched", pid=dead)
        rec = json.loads((job / "record.json").read_text(encoding="utf-8"))
        self.assertEqual(rec["status"], "launched")
        landed_pid = json.loads(
            (job / "state.json").read_text(encoding="utf-8")
        )["pid"]
        self.assertEqual(landed_pid, dead)
        with self.assertRaises(ProcessLookupError):
            os.kill(dead, 0)
        self.assertFalse((job / "exit").exists())
        proc = self.invoke()
        self.assert_running_only(
            proc.stdout or "",
            live=(LIVE_RUN_ID,),
            decoys=(LAUNCH_DEAD_ID,),
        )

    def test_running_field_lists_only_the_live_launched_id(self):
        self._ready_store()
        self.plant_live_launched(LIVE_RUN_ID)
        self.plant_job(
            DIED_INVALID_ID,
            status="died",
            extra={"result": {"envelope": {"status": "invalid"}}},
        )
        self.plant_job(CLOSED_NOEXIT_ID, status="closed")
        self.plant_job(UNRECORDED_ID, with_record=False)
        self.plant_job(LAUNCH_NOPID_ID, status="launched")
        self.plant_job(
            LAUNCH_DEAD_ID, status="launched", pid=self.dead_pid()
        )
        proc = self.invoke()
        decoys = (
            DIED_INVALID_ID,
            CLOSED_NOEXIT_ID,
            UNRECORDED_ID,
            LAUNCH_NOPID_ID,
            LAUNCH_DEAD_ID,
        )
        self.assert_running_only(
            proc.stdout or "",
            live=(LIVE_RUN_ID,),
            decoys=decoys,
        )
        planted = {LIVE_RUN_ID, *decoys}
        listed = [i for i in self.running_ids(proc.stdout or "") if i in planted]
        self.assertEqual(listed, [LIVE_RUN_ID])



# The handoffs section (grant 220, owner 2026-09-13: "hand grant ok'd -
# build the engine and the handoff manager"): `== handoffs` prints between
# `== state`, the last section; an absent state store is a measured fact on
# its line; a planted store prints the handoff manager's brief; a manager
# that cannot report is unavailable and counts as an absence.
HANDOFF_SIBLINGS = ("handoff.py", "machine.py", "instrument.py", "laneconf.py")
THREAD_TITLE = "HANDOFF_THREAD_TITLE_UNIQUE_c220"
HANDOFF_TITLE = "HANDOFF_PACKAGE_TITLE_UNIQUE_c220"
HANDOFF_AGENT = "Closing Session <noreply@example.test>"


class HandoffsSection(_OnboardFixture):
    def state_store(self) -> Path:
        return self.store.parent / "state.db"

    def plant_handoff_tools(self) -> Path:
        tools = self.repo / "ops" / "conductor" / "tools"
        tools.mkdir(parents=True, exist_ok=True)
        for name in HANDOFF_SIBLINGS:
            source = TOOL.parent / name
            self.assertTrue(source.is_file(), f"INVALID: sibling absent: {source}")
            self._plant_bytes(tools / name, source.read_bytes())
        return tools

    def run_handoff(self, tools: Path, *argv: str):
        env = self.env()
        env["WF_AGENT"] = HANDOFF_AGENT
        proc = run(
            [sys.executable, str(tools / "handoff.py"), *argv],
            cwd=self.repo,
            env=env,
            timeout=RUN_CAP,
        )
        self.assertEqual(proc.exit, 0, proc.stdout + proc.stderr)
        return proc.stdout

    def test_the_section_prints_after_state_and_is_the_last_section(self):
        self.ready()
        proc = self.invoke()
        stdout = proc.stdout or ""
        state_i = self.heading_index(stdout, "== state")
        handoffs_i = self.heading_index(stdout, "== handoffs")
        self.assertLess(state_i, handoffs_i, stdout)
        self.assertNotIn("== thread", stdout)
        headings = [ln for ln in stdout.splitlines() if ln.startswith("== ")]
        self.assertEqual(headings[-1], "== handoffs", headings)

    def test_an_absent_state_store_is_a_measured_fact_not_an_absence(self):
        self.ready()
        self.assertFalse(self.state_store().exists(), "INVALID: state store planted")
        proc = self.invoke()
        stdout = proc.stdout or ""
        line = self.labelled_line(stdout, "handoffs")
        self.assertIn("ABSENT", line)
        self.assertIn(str(self.state_store()), line)
        self.assertIn("(none recorded)", line)
        self.assertEqual(self.last_line(stdout), "brief complete")
        self.assertEqual(proc.exit, 0, self.combined(proc))
        self.assertFalse(self.state_store().exists(), "the brief created the state store")

    def test_a_planted_store_prints_the_managers_brief(self):
        self.ready()
        tools = self.plant_handoff_tools()
        self.run_handoff(tools, "open", HANDOFF_TITLE, "--summary", "what was left")
        self.run_handoff(tools, "thread", "H-1", THREAD_TITLE, "--body", "pick it up",
                         "--to", "next session")
        self.assertTrue(self.state_store().is_file(), "INVALID: store not planted")
        proc = self.invoke()
        stdout = proc.stdout or ""
        line = self.labelled_line(stdout, "handoffs")
        self.assertEqual(line, "handoffs: open=1 taken=0 done=0")
        self.assertIn(HANDOFF_TITLE, stdout)
        self.assertIn(f"T-1 open [H-1] {THREAD_TITLE}", stdout)
        self.assertIn("to next session", stdout)
        self.assertEqual(self.last_line(stdout), "brief complete")
        self.assertEqual(proc.exit, 0, self.combined(proc))

    def test_a_manager_that_cannot_report_is_unavailable_and_counted(self):
        self.ready()
        self.state_store().parent.mkdir(parents=True, exist_ok=True)
        self._plant_bytes(self.state_store(), b"")
        tools = self.repo / "ops" / "conductor" / "tools"
        self._plant_tool(
            "handoff-failure",
            (tools / "handoff.py",),
            stderr="HANDOFF_STUB_FAIL_MARKER",
            exit_code=1,
        )
        proc = self.invoke()
        stdout = proc.stdout or ""
        line = self.labelled_line(stdout, "handoffs")
        self.assertIn("unavailable", line)
        self.assertIn("HANDOFF_STUB_FAIL_MARKER", line)
        self.assertEqual(self.last_line(stdout), "brief complete, 1 absence(s)")
        self.assertEqual(proc.exit, 1, self.combined(proc))


class TheMemberBrief(_OnboardFixture):
    """LD-146: a brief run in a member, whose HOME is its private home and
    whose launch binding names the operator's HOME and state root as its
    authority, reads the stores where its siblings read them. The world's
    repository is the binding's source: it carries the policy closure the
    binding pins, the real brief and instrument, and the planted siblings.
    The binding is generated after the tree is final, since validate
    refuses a closure that changed after it."""

    def plant_closure(self) -> None:
        hooks = Path("ops") / "devlane" / "hooks"
        names = [hooks / "claude" / p.name for p in (REPO / hooks / "claude").glob("*.py")]
        names += [hooks / n for n in ("enforcement.py", "native.py", "profiles.py", "guidance.py", "boundaries.json")]
        names += [Path("ops/conductor/tools") / n for n in ("onboard.py", *SIBLINGS, *HANDOFF_SIBLINGS)]
        names += [Path("ops/devlane/harness/isolation.py")]
        for name in names:
            self._plant_bytes(self.repo / name, (REPO / name).read_bytes())
        self.world.plant_text(self.repo / "ops" / "lane.conf", "LANE_NAME='agentscient'\n")

    def plant_handoff(self) -> None:
        """One package and one thread in the authority's state store, by
        the real manager under the fixture's own HOME."""
        env = self.env()
        env["WF_AGENT"] = HANDOFF_AGENT
        tool = self.repo / "ops" / "conductor" / "tools" / "handoff.py"
        for argv in (("open", HANDOFF_TITLE, "--summary", "what was left"),
                     ("thread", "H-1", THREAD_TITLE, "--body", "pick it up", "--to", "next session")):
            proc = run([sys.executable, str(tool), *argv], cwd=self.repo, env=env, timeout=RUN_CAP)
            self.assertEqual(proc.exit, 0, proc.stdout + proc.stderr)
        self.assertTrue((self.store.parent / "state.db").is_file(), "INVALID: store not planted")

    def bind_member(self) -> tuple[Path, Path]:
        """A grok interactive binding generated with the fixture's HOME as
        the authority; the member's private home holds none of the stores."""
        private = self.root / "private"
        private.mkdir()
        spec = importlib.util.spec_from_file_location(
            "member_profiles", self.repo / "ops" / "devlane" / "hooks" / "profiles.py")
        profiles = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(profiles)
        # The authority is the fixture's HOME with its state root under it,
        # where plant_handoff and the ledger put their stores.
        with mock.patch.dict(os.environ, hermetic_env(self.home, bin=self.bin)):
            binding = profiles.generate("grok", self.root / "profile", source=self.repo,
                                        cwd=self.repo, mode="interactive", home=private)
        self.assertFalse((private / ".local").exists(), "INVALID: the private home holds a store")
        return private, Path(binding["binding"])

    def invoke_member(self, private: Path, binding: Path):
        # The member's own home, built as the world's is, with its XDG roots
        # under it; only the binding names the authority's.
        env = hermetic_env(private, tmp=self.scratch, bin=self.bin, ambient=self.world.ambient, extra={
            "LANE_CONF": str(self.repo / "ops" / "lane.conf"), "GROK_HOME": str(private),
            "AGENTSCIENT_HOOK_PROFILE": str(binding)})
        self.assertEqual(Path(env["XDG_STATE_HOME"]), private / ".local" / "state")
        self.assertNotIn("WF_AGENT", env)
        return run([sys.executable, str(self.repo / "ops" / "conductor" / "tools" / "onboard.py")],
                   cwd=self.repo, env=env, timeout=RUN_CAP)


    def test_the_state_roots_follow_the_binding(self):
        """origin: LD-146

        The lock, the dispatch store and the handoff store under the
        binding's XDG_STATE_HOME are the ones read, never the private
        home's."""
        self.ready()
        self.world.plant_text(self.store.parent / "deliverable.json",
                              '{"branches": ["lane/session-a"], "why": "planted"}\n')
        self.plant_closure()
        self.plant_handoff()
        private, binding = self.bind_member()
        with self.subTest(id="onboard-state-roots-follow-binding", origin=ORIGIN_146):
            proc = self.invoke_member(private, binding)
            stdout = proc.stdout or ""
            lock = self.labelled_line(stdout, "lock")
            self.assertIn("lane/session-a", lock)
            dispatches = self.labelled_line(stdout, "dispatches")
            self.assertIn(RUNNING_ID, dispatches)
            self.assertNotIn(str(private), dispatches)
            self.assertEqual(self.labelled_line(stdout, "handoffs"),
                             "handoffs: open=1 taken=0 done=0")
            self.assertIn(f"T-1 open [H-1] {THREAD_TITLE}", stdout)
            self.assertNotIn(str(private), stdout)
            self.assertEqual(self.last_line(stdout), "brief complete")
            self.assertEqual(proc.exit, 0, self.combined(proc))


class ReadsThatCouldNotLook(_OnboardFixture):
    """Origin LD-48, the gap its repair declared: the lock line learned to
    tell a read that timed out from a lever that reported, and the brief's
    three other lines did not. A line that says `unavailable` after a bound
    expired is read as an answer — `prs: 0 open` and `handoffs: ABSENT` are
    answers a session acts on — and under five concurrent sessions the bound
    is what expires first.

    The clean neighbours for these rows live in their own classes and are
    not repeated here: GhFailure pins a gh that ran and failed as
    `gh unavailable`, DispatchStore pins an absent store as ABSENT, and
    HandoffsSection pins both an absent store and a manager that reported a
    failure. One neighbour is repeated below, the gh that failed, because
    the prs line is the one this change rewrites most.

    The store path that exists as a file is not here either: that fault
    is Codex's finding on PR #106 and keeps its row in ReviewPins, which
    this change rewrites in place rather than copy.
    """

    def plant_slow_gh(self, seconds: float) -> None:
        """A gh that answers with the usual row, later than the first bound."""
        payload = json.dumps([{"number": int(PR_NUMBER), "title": PR_TITLE}]) + "\n"
        self.world.tools.plant("gh", stdout=payload, exit=0, delay=seconds)
        self.assertTrue(self.gh.is_file(), "slow gh fake did not land")

    def state_store(self) -> Path:
        return self.store.parent / "state.db"

    def plant_slow_handoff(self, seconds: float) -> None:
        """A handoff.py whose brief is well formed but late."""
        self.state_store().parent.mkdir(parents=True, exist_ok=True)
        if not self.state_store().exists():
            self._plant_bytes(self.state_store(), b"")
        fake = self.world.tools.plant(
            "handoff-slow", stdout="handoffs: open=0 taken=0 done=0\n",
            exit=0, delay=seconds,
        )
        tools = self.repo / "ops" / "conductor" / "tools"
        tools.mkdir(parents=True, exist_ok=True)
        self._plant_bytes(tools / "handoff.py", fake.read_bytes())

    def no_root(self) -> None:
        """A permission fault is not a fault for a user who has them all."""
        if os.geteuid() == 0:
            self.skipTest("root reads a mode-000 path; the fault cannot be planted")

    def test_a_prs_read_that_times_out_is_retried_once_with_a_longer_bound(self):
        self.ready()
        self.plant_slow_gh(1.5)
        proc = self.invoke(extra_env={"ONBOARD_TIMEOUT": "1",
                                      "ONBOARD_RETRY_TIMEOUT": "20"})
        # The fake logs each invocation before it sleeps, so the log is what
        # separates a read that was retried from one that answered first
        # time. Without the count this row passes at the base tree, where
        # 1.5s fits inside the standing 5s bound.
        calls = self.world.tools.calls("gh")
        self.assertEqual(len(calls), 2, f"the prs read was not retried: {calls}")
        line = self.labelled_line(proc.stdout or "", "prs")
        self.assertIn(f"#{PR_NUMBER}", line)
        self.assertNotIn("UNREACHABLE", line)
        self.assertNotIn("unavailable", line)
        self.assertEqual(proc.exit, 0, self.combined(proc))

    def test_a_prs_read_that_times_out_twice_is_unreachable_never_a_count(self):
        self.ready()
        self.plant_slow_gh(3)
        proc = self.invoke(extra_env={"ONBOARD_TIMEOUT": "1",
                                      "ONBOARD_RETRY_TIMEOUT": "2"})
        stdout = proc.stdout or ""
        line = self.labelled_line(stdout, "prs")
        self.assertIn("UNREACHABLE", line)
        self.assertIn("timed out", line)
        # `prs: 0 open` is the answer for a repository with no open PR. A
        # read that could not look must not spell it.
        self.assertNotIn("0 open", stdout)
        self.assertEqual(proc.exit, 1, self.combined(proc))

    def test_a_gh_that_ran_and_failed_is_unavailable_and_never_unreachable(self):
        """The clean neighbour: gh answered, with a failure. That is a lever
        reporting, not a read that could not look, and the line keeps the
        wording GhFailure pins."""
        self.ready(gh_exit=1, gh_stderr=GH_FAIL)
        proc = self.invoke()
        line = self.labelled_line(proc.stdout or "", "prs")
        self.assertIn("gh unavailable:", line)
        self.assertIn(GH_FAIL, line)
        self.assertNotIn("UNREACHABLE", line)
        self.assertEqual(proc.exit, 1, self.combined(proc))

    def test_a_handoff_read_that_times_out_is_retried_once(self):
        self.ready()
        self.plant_slow_handoff(1.5)
        proc = self.invoke(extra_env={"ONBOARD_TIMEOUT": "1",
                                      "ONBOARD_RETRY_TIMEOUT": "20"})
        calls = self.world.tools.calls("handoff-slow")
        self.assertEqual(len(calls), 2, f"the handoff read was not retried: {calls}")
        line = self.labelled_line(proc.stdout or "", "handoffs")
        self.assertEqual(line, "handoffs: open=0 taken=0 done=0")
        self.assertEqual(proc.exit, 0, self.combined(proc))

    def test_a_handoff_read_that_times_out_twice_is_unreachable_never_absent(self):
        self.ready()
        self.plant_slow_handoff(3)
        proc = self.invoke(extra_env={"ONBOARD_TIMEOUT": "1",
                                      "ONBOARD_RETRY_TIMEOUT": "2"})
        stdout = proc.stdout or ""
        line = self.labelled_line(stdout, "handoffs")
        self.assertIn("UNREACHABLE", line)
        self.assertIn("timed out", line)
        # ABSENT is the answer for a host with no store. The store is
        # planted here, so that word would be a false reading.
        self.assertNotIn("ABSENT", stdout)
        self.assertEqual(proc.exit, 1, self.combined(proc))

    def test_a_dispatch_store_that_cannot_be_listed_is_unreachable(self):
        self.no_root()
        self.ready()
        self.addCleanup(self.store.chmod, 0o755)
        self.store.chmod(0o000)
        proc = self.invoke()
        line = self.labelled_line(proc.stdout or "", "dispatches")
        self.assertIn("UNREACHABLE", line)
        # running=none is the answer for a store that was read and held no
        # live record. A store that could not be listed has no such answer.
        self.assertNotIn("running=none", line)
        self.assertEqual(proc.exit, 1, self.combined(proc))

    def test_a_dispatch_exit_file_that_cannot_be_read_is_unreachable(self):
        self.no_root()
        self.ready()
        exit_file = self.store / CLOSED_NEW / "exit"
        self.assertTrue(exit_file.is_file(), "INVALID: closed record has no exit")
        self.addCleanup(exit_file.chmod, 0o644)
        exit_file.chmod(0o000)
        proc = self.invoke()
        line = self.labelled_line(proc.stdout or "", "dispatches")
        self.assertIn("UNREACHABLE", line)
        # ABSENT is what the line says for a closed record whose exit file
        # holds nothing; an unreadable one is a different fact.
        self.assertNotIn("exit=ABSENT", line)
        self.assertEqual(proc.exit, 1, self.combined(proc))


if __name__ == "__main__":
    unittest.main()
