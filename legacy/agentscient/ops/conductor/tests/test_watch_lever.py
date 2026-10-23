"""Watch-lever acceptance tests, written from the contract.

Contract: `ops/conductor/watch-lever.md`. The tool
`ops/conductor/tools/watch.py` is the subject and is not read here.
Tests invoke it as a subprocess with a stub `gh` first on PATH.

The implementation lands in parallel. A missing tool file is one red
(`test_the_tool_file_is_present`). Every other case stages a
copy-tool-bytes stand-in so a missing file is not every test's failure:
when the tool exists its bytes are copied and run; when it does not, a
no-op stub (exit 0, no output) is planted so each assertion goes red on
the contract promise rather than Python's missing-file exit 2.
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

from ops.testkit import World, run

REPO = Path(__file__).resolve().parents[3]
TOOL = REPO / "ops" / "conductor" / "tools" / "watch.py"
# The tool imports these beside itself (instrument-conventions.md §The
# shared implementation); a staged copy run from a scratch directory
# needs them staged too, or it refuses `tooling` before the contract.
SIBLINGS = ("instrument.py", "laneconf.py")
# The staged reader has no tracked conf beside it. These cases read the
# tracked conf's CHECK_NAMES, as the copy did through the working
# directory before, so the tracked file is the one named; the cases that
# plant their own conf name it through `extra`.
TRACKED_CONF = REPO / "ops" / "lane.conf"

PR = "91"
INTERVAL = "5"
TIMEOUT = "11"
WALL = ("--interval", INTERVAL, "--timeout", TIMEOUT)
REPO_SLUG = "owner/name"
# The wall aborts at the first poll past ~15s on the 5s grid and the
# settle check precedes the wall check, so green paths never race the
# wall — but a loaded runner stacks interpreter startup on every gh
# stub spawn, and the abort paths were measured within half of the
# old 30s cap. 60 keeps the hang guard while removing the flake edge.
RUN_CAP = 60

POLL_RE = re.compile(
    r"poll rows=(\d+) pass=(\d+) fail=(\d+) pending=(\d+) skip=(\d+) "
    r"elapsed=([0-9]+(?:\.[0-9]+)?)s"
)
SETTLED_RE = re.compile(
    r"^settled rows=(\d+) pass=(\d+) fail=(\d+) skip=(\d+)\s*$"
)
GH_ERROR_TEXT = "GH_ERROR_QUOTED_MARKER: pr checks failed"

# PIN_JSON_ROWS: `gh pr checks --json name,state` is an array of
# objects with `name` and `state` (gh's --json field list).
# PIN_GH_ARGV: each poll is `pr checks NUMBER --json name,state`;
# `--repo OWNER/NAME` is inserted when given. Tests assert membership
# (and `--repo` then its value) rather than a total argv order the
# contract does not pin.
# PIN_REFUSAL_CLASS: the refusals table's left column is the token on
# the one stderr line (number, interval, gh-missing, gh-error, json).
# PIN_ABORT_COUNTS: abort leaves the last observed counts in the poll
# field shape `rows=R pass=P fail=F pending=D skip=S`.
# PIN_ELAPSED: elapsed is a number immediately followed by `s`.


def check_line(kind: str, name: str) -> str:
    return f"{kind}\t{name}"


class _WatchFixture(unittest.TestCase):
    """Scratch HOME, staged tool copy, stub gh first on PATH."""

    def setUp(self):
        self.world = World(prefix="watch-lever-")
        self.addCleanup(self.world.close)
        self.home = self.world.home
        self.scratch = self.world.cwd
        self.bin = self.world.bin
        self.gh = self.bin / "gh"
        self.counter = self.scratch / "gh-calls"
        self.staged = self.world.stage(TOOL, "watch.py", siblings=SIBLINGS)
        self.tracked_conf = self.world.stage(TRACKED_CONF, "lane.conf")
        self.assertEqual(self.staged.parent, self.tracked_conf.parent)
        self.assertEqual(self.staged.name, "watch.py")
        self.assertFalse(
            self.world.tools.log_path("gh").exists(),
            "plant: argv log must start absent",
        )
        self.assertFalse(self.counter.exists(), "plant: counter must start absent")
        self.assertFalse(self.gh.exists(), "plant: gh stub is not planted yet")

    def plant_gh_rows(self, rows: list[dict], *, exit_code: int = 0, stderr: str = "") -> None:
        self.assertEqual(
            self.world.tools.plant(
                "gh",
                stdout=json.dumps(rows) + "\n",
                stderr=stderr,
                exit=exit_code,
            ),
            self.gh,
        )

    def plant_gh_raw(self, stdout: str, *, exit_code: int = 0, stderr: str = "") -> None:
        self.assertEqual(
            self.world.tools.plant(
                "gh",
                stdout=stdout,
                stderr=stderr,
                exit=exit_code,
            ),
            self.gh,
        )

    def plant_gh_sequence(self, sequence: list[list[dict]]) -> None:
        self.plant_gh_steps(sequence)

    def plant_gh_steps(self, steps: list, *, delay: float = 0) -> None:
        """Like plant_gh_sequence, but a step that is a dict is an error
        exit: its `stderr` written, nothing on stdout, exit `exit`."""
        fixture = self.world.plant_text(
            "fixtures/gh-steps.json",
            json.dumps(steps),
        )
        log = self.world.tools.log_path("gh")
        script = f"""#!{sys.executable}
import json, os, sys, time
from pathlib import Path
time.sleep({delay!r})
log = Path({str(log)!r})
line = json.dumps({{"argv": sys.argv[1:], "cwd": os.getcwd()}}) + "\\n"
fd = os.open(log, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
os.write(fd, line.encode("utf-8"))
os.close(fd)
counter = Path({str(self.counter)!r})
n = int(counter.read_text(encoding="utf-8")) if counter.is_file() else 0
n += 1
counter.write_text(str(n), encoding="utf-8")
steps = json.loads(Path({str(fixture)!r}).read_text(encoding="utf-8"))
step = steps[n - 1] if n <= len(steps) else steps[-1]
if isinstance(step, dict):
    sys.stderr.write(step["stderr"])
    raise SystemExit(step["exit"])
sys.stdout.write(json.dumps(step) + "\\n")
raise SystemExit(0)
"""
        self.assertEqual(self.world.tools.plant("gh", script=script), self.gh)
        self.assertFalse(self.counter.exists(), "plant: step counter still absent")

    def env(
        self,
        *,
        path: str | None = None,
        agent: str | None = None,
        extra: dict[str, str | None] | None = None,
    ) -> dict[str, str]:
        overrides = {
            # Test apparatus: one virtual second of the 5s grid is 20ms real.
            "WATCH_TICK_SCALE": "0.02",
            "LANE_CONF": str(self.tracked_conf),
        }
        if path is not None:
            overrides["PATH"] = path
        if agent is not None:
            overrides["WF_AGENT"] = agent
        env = self.world.env(overrides)
        if extra:
            for name, value in extra.items():
                if value is None:
                    env.pop(name, None)
                else:
                    env[name] = value
        return env

    def path_without_gh(self) -> str:
        empty = self.world.path("no-gh")
        empty.mkdir()
        self.assertFalse((empty / "gh").exists(), "plant: empty PATH dir has a gh")
        return str(empty)

    def invoke(
        self,
        argv: list[str],
        *,
        path: str | None = None,
        agent: str | None = None,
        extra: dict[str, str | None] | None = None,
    ):
        env = self.env(path=path, agent=agent, extra=extra)
        if agent is None:
            self.assertNotIn("WF_AGENT", env)
        else:
            self.assertEqual(env.get("WF_AGENT"), agent)
        return run(
            [sys.executable, str(self.staged), *argv],
            cwd=REPO,
            env=env,
            timeout=RUN_CAP,
        )

    def watch(
        self,
        number: str = PR,
        extra: list[str] | None = None,
        expect_names: str | None = None,
        extra_env: dict[str, str | None] | None = None,
        **kw,
    ):
        argv = ["pr", number, *WALL]
        if expect_names is not None:
            argv.extend(["--expect-names", expect_names])
        if extra:
            argv.extend(extra)
        return self.invoke(argv, extra=extra_env, **kw)

    def plant_lane_conf(self, **overrides: object) -> Path:
        omit = tuple(overrides.pop("omit", ()) or ())
        keys = {
            "LANE_NAME": "lanetest",
            "TRUNK_BRANCH": "trunktest",
            "RELEASE_BRANCH": "reltest",
            "REPO_SLUG": "org/proj",
            "CHECK_NAMES": "lane app",
            "WF_AGENT_DEFAULT": "Conf Agent <conf@example.invalid>",
            "LANE_AUTHOR": "Lane Owner <lane-owner@example.invalid>",
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
        path = self.scratch / "lane.conf"
        text = "".join(f"{name}='{value}'\n" for name, value in keys.items())
        self.world.plant_text(path, text)
        landed = path.read_text(encoding="utf-8")
        self.assertEqual(landed, text, f"conf plant did not land: {path}")
        self.conf_path = path
        return path

    def combined(self, proc) -> str:
        return f"{proc.stdout or ''}{proc.stderr or ''}"

    def gh_argvs(self) -> list[list[str]]:
        return [call["argv"] for call in self.world.tools.calls("gh")]

    def poll_matches(self, text: str) -> list[re.Match[str]]:
        return list(POLL_RE.finditer(text))

    def last_counts(self, proc) -> dict[str, int] | None:
        matches = self.poll_matches(self.combined(proc))
        if not matches:
            return None
        rows, passed, failed, pending, skip, _elapsed = matches[-1].groups()
        return {
            "rows": int(rows),
            "pass": int(passed),
            "fail": int(failed),
            "pending": int(pending),
            "skip": int(skip),
        }

    def settled_line(self, stdout: str) -> str | None:
        lines = [ln for ln in stdout.splitlines() if ln.strip()]
        if not lines:
            return None
        last = lines[-1]
        if SETTLED_RE.match(last):
            return last
        return None

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
        self.assertEqual(
            (proc.stdout or "").strip(),
            "",
            "refusal text belongs on stderr, not stdout",
        )


class ToolPresence(_WatchFixture):
    """Missing tool file counts once."""

    def test_the_tool_file_is_present(self):
        self.assertTrue(TOOL.is_file(), f"RED: tool is absent: {TOOL}")


class SettledGreen(_WatchFixture):
    """1. All SUCCESS/SKIPPED → PASS/SKIP lines, settled summary, exit 0."""

    def test_success_and_skipped_settle_green(self):
        self.plant_gh_rows(
            [
                {"name": "lint", "state": "SUCCESS"},
                {"name": "docs", "state": "SKIPPED"},
            ]
        )
        proc = self.watch(expect_names="lint,docs")
        stdout = proc.stdout or ""
        self.assertIn(
            check_line("PASS", "lint"),
            stdout,
            f"missing PASS<tab>lint on stdout: {stdout!r}",
        )
        self.assertIn(check_line("SKIP", "docs"), stdout)
        self.assertEqual(
            self.settled_line(stdout),
            "settled rows=2 pass=1 fail=0 skip=1",
            stdout,
        )
        self.assertEqual(proc.exit, 0, self.combined(proc))
        counts = self.last_counts(proc)
        self.assertIsNotNone(counts, f"no poll line on stderr: {proc.stderr!r}")
        self.assertEqual(counts["rows"], 2)
        self.assertEqual(counts["pass"], 1)
        self.assertEqual(counts["fail"], 0)
        self.assertEqual(counts["pending"], 0)
        self.assertEqual(counts["skip"], 1)


class SettledFailure(_WatchFixture):
    """2. A FAILURE row settles with FAIL named and exit 1."""

    def test_failure_row_settles_exit_1_naming_fail(self):
        self.plant_gh_rows([{"name": "broken", "state": "FAILURE"}])
        proc = self.watch(expect_names="broken")
        stdout = proc.stdout or ""
        self.assertIn(
            check_line("FAIL", "broken"),
            stdout,
            f"missing FAIL<tab>broken on stdout: {stdout!r}",
        )
        self.assertEqual(
            self.settled_line(stdout),
            "settled rows=1 pass=0 fail=1 skip=0",
            stdout,
        )
        self.assertEqual(proc.exit, 1, self.combined(proc))


class ZeroRowsAbort(_WatchFixture):
    """3. Zero rows is not settled: poll to the wall, exit 3, leave counts."""

    def test_empty_check_table_aborts_at_the_wall_with_last_counts(self):
        self.plant_gh_rows([])
        proc = self.watch()
        text = self.combined(proc)
        self.assertIn(
            "rows=0",
            text,
            f"abort must print last observed counts; got {text!r}",
        )
        counts = self.last_counts(proc)
        self.assertIsNotNone(counts, f"abort printed no poll counts: {text!r}")
        self.assertEqual(counts["rows"], 0, counts)
        self.assertEqual(counts["pass"], 0, counts)
        self.assertEqual(counts["fail"], 0, counts)
        self.assertEqual(counts["pending"], 0, counts)
        self.assertEqual(counts["skip"], 0, counts)
        self.assertEqual(proc.exit, 3, text)
        self.assertNotEqual(proc.exit, 0)
        self.assertIsNone(self.settled_line(proc.stdout or ""))


class PendingThenSettled(_WatchFixture):
    """4. IN_PROGRESS keeps polling; a later settled poll re-reads and settles."""

    def test_poll_startup_time_is_not_multiplied_by_the_test_clock(self):
        self.plant_gh_steps([
            [{"name": "progress-check", "state": "IN_PROGRESS"}],
            [{"name": "progress-check", "state": "SUCCESS"}],
        ], delay=0.2)
        proc = self.watch(expect_names="progress-check")
        self.assertEqual(proc.exit, 0, self.combined(proc))
        self.assertEqual(int(self.counter.read_text()), 3)

    def test_in_progress_then_success_re_reads_and_settles(self):
        self.plant_gh_sequence(
            [
                [{"name": "progress-check", "state": "IN_PROGRESS"}],
                [{"name": "progress-check", "state": "SUCCESS"}],
            ]
        )
        proc = self.watch(expect_names="progress-check")
        self.assertTrue(
            self.counter.is_file(),
            "poll loop did not re-read gh (counter file missing)",
        )
        n = int(self.counter.read_text(encoding="utf-8"))
        self.assertGreaterEqual(
            n,
            2,
            f"poll loop did not re-read; gh calls={n}",
        )
        stdout = proc.stdout or ""
        self.assertIn(check_line("PASS", "progress-check"), stdout)
        self.assertEqual(proc.exit, 0, self.combined(proc))
        self.assertEqual(
            self.settled_line(stdout),
            "settled rows=1 pass=1 fail=0 skip=0",
            stdout,
        )


class UnrecognizedNeverSettles(_WatchFixture):
    """5. An unrecognized state is pending and never settles: exit 3, not 0."""

    def test_unrecognized_state_aborts_at_the_wall_not_exit_0(self):
        self.plant_gh_rows(
            [{"name": "odd-check", "state": "NOT_A_REAL_GH_STATE"}]
        )
        proc = self.watch()
        text = self.combined(proc)
        counts = self.last_counts(proc)
        self.assertIsNotNone(
            counts,
            f"unrecognized state produced no pending count: {text!r}",
        )
        self.assertGreaterEqual(
            counts["pending"],
            1,
            f"unrecognized state must count as pending: {counts}",
        )
        self.assertEqual(proc.exit, 3, text)
        self.assertNotEqual(proc.exit, 0, "unrecognized state settled")
        self.assertNotIn(check_line("PASS", "odd-check"), proc.stdout or "")
        self.assertIsNone(self.settled_line(proc.stdout or ""))


class StateMapping(_WatchFixture):
    """6. CANCELLED/TIMED_OUT → fail; NEUTRAL → skip."""

    def test_cancelled_settles_as_fail(self):
        self.plant_gh_rows(
            [{"name": "cancelled-check", "state": "CANCELLED"}]
        )
        proc = self.watch(expect_names="cancelled-check")
        stdout = proc.stdout or ""
        self.assertIn(
            check_line("FAIL", "cancelled-check"),
            stdout,
            f"CANCELLED must map to FAIL: {stdout!r}",
        )
        self.assertEqual(proc.exit, 1, self.combined(proc))
        self.assertEqual(
            self.settled_line(stdout),
            "settled rows=1 pass=0 fail=1 skip=0",
            stdout,
        )

    def test_timed_out_settles_as_fail(self):
        self.plant_gh_rows(
            [{"name": "timed-out-check", "state": "TIMED_OUT"}]
        )
        proc = self.watch(expect_names="timed-out-check")
        stdout = proc.stdout or ""
        self.assertIn(
            check_line("FAIL", "timed-out-check"),
            stdout,
            f"TIMED_OUT must map to FAIL: {stdout!r}",
        )
        self.assertEqual(proc.exit, 1, self.combined(proc))

    def test_neutral_settles_as_skip(self):
        self.plant_gh_rows(
            [{"name": "neutral-check", "state": "NEUTRAL"}]
        )
        proc = self.watch(expect_names="neutral-check")
        stdout = proc.stdout or ""
        self.assertIn(
            check_line("SKIP", "neutral-check"),
            stdout,
            f"NEUTRAL must map to SKIP: {stdout!r}",
        )
        self.assertEqual(proc.exit, 0, self.combined(proc))
        self.assertEqual(
            self.settled_line(stdout),
            "settled rows=1 pass=0 fail=0 skip=1",
            stdout,
        )


class Refusals(_WatchFixture):
    """7. Refusals exit 2, expected/found/needed, one line on stderr."""

    def test_number_not_a_positive_integer_refuses(self):
        self.plant_gh_rows([{"name": "lint", "state": "SUCCESS"}])
        for bad in ("0", "-1", "abc"):
            with self.subTest(number=bad):
                proc = self.watch(number=bad)
                self.assertIn(
                    bad,
                    proc.stderr or "",
                    f"NUMBER {bad!r} must appear in found: {proc.stderr!r}",
                )
                self.assert_refusal(proc, "number")

    def test_interval_under_5_refuses(self):
        self.plant_gh_rows([{"name": "lint", "state": "SUCCESS"}])
        proc = self.invoke(["pr", PR, "--interval", "4", "--timeout", TIMEOUT])
        err = proc.stderr or ""
        self.assertIn("4", err, f"found must name 4: {err!r}")
        self.assert_refusal(proc, "interval")

    def test_gh_missing_from_path_refuses(self):
        path = self.path_without_gh()
        proc = self.watch(path=path)
        self.assert_refusal(proc, "gh-missing")

    def test_gh_nonzero_exit_quotes_stderr_in_found(self):
        self.plant_gh_raw("", exit_code=1, stderr=GH_ERROR_TEXT + "\n")
        proc = self.watch()
        err = proc.stderr or ""
        self.assertIn(
            GH_ERROR_TEXT,
            err,
            f"gh stderr must be quoted in found: {err!r}",
        )
        self.assert_refusal(proc, "gh-error")

    def test_non_json_output_is_a_json_refusal(self):
        self.plant_gh_raw("this is not json\n")
        proc = self.watch()
        self.assert_refusal(proc, "json")


class ExpectMin(_WatchFixture):
    """8. --expect-min N: fewer than N rows do not settle even with none pending."""

    def test_rows_below_expect_min_do_not_settle(self):
        self.plant_gh_rows([{"name": "solo", "state": "SUCCESS"}])
        proc = self.watch(extra=["--expect-min", "2"])
        stdout = proc.stdout or ""
        self.assertNotIn(
            "settled rows=",
            stdout,
            f"rows < expect-min settled: {stdout!r}",
        )
        counts = self.last_counts(proc)
        self.assertIsNotNone(
            counts,
            f"expect-min abort printed no counts: {self.combined(proc)!r}",
        )
        self.assertEqual(counts["rows"], 1, counts)
        self.assertEqual(counts["pending"], 0, counts)
        self.assertEqual(proc.exit, 3, self.combined(proc))
        self.assertNotEqual(proc.exit, 0)


class NoIdentity(_WatchFixture):
    """9. Watching is a read: unset WF_AGENT, the watch still runs."""

    def test_unset_wf_agent_still_runs_a_settled_read(self):
        self.plant_gh_rows([{"name": "read-check", "state": "SUCCESS"}])
        proc = self.watch(agent=None, expect_names="read-check")
        err = (proc.stderr or "").lower()
        self.assertNotIn("identity", err)
        stdout = proc.stdout or ""
        self.assertIn(
            check_line("PASS", "read-check"),
            stdout,
            f"read without WF_AGENT produced no PASS: {stdout!r}",
        )
        self.assertEqual(proc.exit, 0, self.combined(proc))


class RepoPassthrough(_WatchFixture):
    """10. --repo OWNER/NAME is passed through to gh."""

    def test_repo_flag_is_passed_through_to_gh(self):
        self.plant_gh_rows([{"name": "repo-check", "state": "SUCCESS"}])
        proc = self.watch(extra=["--repo", REPO_SLUG], expect_names="repo-check")
        argvs = self.gh_argvs()
        self.assertTrue(
            argvs,
            "gh was not invoked; --repo cannot have been passed through",
        )
        found_repo = False
        found_value = False
        found_pair = False
        for argv in argvs:
            if "--repo" in argv:
                found_repo = True
                idx = argv.index("--repo")
                if idx + 1 < len(argv) and argv[idx + 1] == REPO_SLUG:
                    found_pair = True
            if REPO_SLUG in argv:
                found_value = True
            self.assertIn("pr", argv)
            self.assertIn("checks", argv)
            self.assertIn(PR, argv)
            self.assertTrue(
                "--json" in argv or any(item.startswith("--json=") for item in argv),
                f"gh argv missing --json: {argv!r}",
            )
            self.assertTrue(
                any("name" in item and "state" in item for item in argv),
                f"gh argv missing name,state: {argv!r}",
            )
        self.assertTrue(found_repo, f"--repo missing from gh argv: {argvs!r}")
        self.assertTrue(found_value, f"{REPO_SLUG!r} missing from gh argv: {argvs!r}")
        self.assertTrue(
            found_pair,
            f"--repo was not followed by {REPO_SLUG!r}: {argvs!r}",
        )
        self.assertEqual(proc.exit, 0, self.combined(proc))
        self.assertIn(check_line("PASS", "repo-check"), proc.stdout or "")


class TableCompleteness(_WatchFixture):
    """Codex security review of PR #102: a table still filling in must
    never settle; the caller can inject the expected-name authority."""

    def test_a_filling_table_does_not_settle_on_its_first_shape(self):
        one = [{"name": "fast-lint", "state": "SUCCESS"}]
        three = one + [
            {"name": "tests", "state": "SUCCESS"},
            {"name": "secret-scan", "state": "SUCCESS"},
        ]
        self.plant_gh_sequence([one, three, three])
        proc = self.watch(expect_names="fast-lint,tests,secret-scan")
        self.assertEqual(proc.exit, 0, self.combined(proc))
        self.assertIn("settled rows=3", proc.stdout or "")
        self.assertNotIn("settled rows=1", proc.stdout or "")
        self.assertIn("confirming", proc.stderr or "")

    def test_expect_names_missing_check_never_settles(self):
        self.plant_gh_rows([{"name": "fast-lint", "state": "SUCCESS"}])
        proc = self.watch(extra=["--expect-names", "fast-lint,secret-scan"])
        self.assertEqual(proc.exit, 3, self.combined(proc))
        self.assertIn("missing=1", proc.stderr or "")
        self.assertIn("abort", proc.stderr or "")

    def test_expect_names_all_reported_settles(self):
        self.plant_gh_rows([
            {"name": "fast-lint", "state": "SUCCESS"},
            {"name": "secret-scan", "state": "SUCCESS"},
        ])
        proc = self.watch(extra=["--expect-names", "fast-lint,secret-scan"])
        self.assertEqual(proc.exit, 0, self.combined(proc))
        self.assertIn("settled rows=2", proc.stdout or "")


class ConfExpectedSet(_WatchFixture):
    """Without --expect-names the expected set is the conf's CHECK_NAMES.
    The flag still wins; environment beats conf; a conf without the
    key refuses conf-key.

    Planted values are not the production literal 'lane app', so a
    reader that only checks key presence and then hard-codes those
    names cannot pass.
    """

    CONF_NAMES = "alpha beta"
    ENV_NAMES = "gamma delta"

    def conf_env(self, **extra: str) -> dict[str, str]:
        env = {"LANE_CONF": str(self.conf_path)}
        env.update(extra)
        return env

    def test_default_expected_set_comes_from_planted_check_names(self):
        self.assertNotEqual(self.CONF_NAMES, "lane app")
        conf = self.plant_lane_conf(CHECK_NAMES=self.CONF_NAMES)
        landed = conf.read_text(encoding="utf-8")
        self.assertIn(f"CHECK_NAMES='{self.CONF_NAMES}'", landed)
        self.assertNotIn("CHECK_NAME=", landed)
        self.assertNotIn("lane app", landed)
        self.plant_gh_rows([
            {"name": "alpha", "state": "SUCCESS"},
            {"name": "beta", "state": "SUCCESS"},
        ])
        proc = self.watch(extra_env=self.conf_env())
        self.assertEqual(proc.exit, 0, self.combined(proc))
        self.assertEqual(
            self.settled_line(proc.stdout or ""),
            "settled rows=2 pass=2 fail=0 skip=0",
            proc.stdout,
        )

    def test_conf_check_names_do_not_settle_on_production_names(self):
        conf = self.plant_lane_conf(CHECK_NAMES=self.CONF_NAMES)
        self.assertIn(
            f"CHECK_NAMES='{self.CONF_NAMES}'",
            conf.read_text(encoding="utf-8"),
        )
        self.plant_gh_rows([
            {"name": "lane", "state": "SUCCESS"},
            {"name": "app", "state": "SUCCESS"},
        ])
        proc = self.watch(extra_env=self.conf_env())
        self.assertEqual(proc.exit, 3, self.combined(proc))
        self.assertIn("missing=", proc.stderr or "")
        self.assertIsNone(self.settled_line(proc.stdout or ""))

    def test_expect_names_flag_wins_over_the_conf(self):
        conf = self.plant_lane_conf(CHECK_NAMES=self.CONF_NAMES)
        self.assertIn(
            f"CHECK_NAMES='{self.CONF_NAMES}'",
            conf.read_text(encoding="utf-8"),
        )
        self.plant_gh_rows([{"name": "fast-lint", "state": "SUCCESS"}])
        proc = self.watch(
            extra=["--expect-names", "fast-lint"],
            extra_env=self.conf_env(),
        )
        self.assertEqual(proc.exit, 0, self.combined(proc))
        self.assertEqual(
            self.settled_line(proc.stdout or ""),
            "settled rows=1 pass=1 fail=0 skip=0",
            proc.stdout,
        )

    def test_environment_check_names_beats_the_conf(self):
        self.assertNotEqual(self.ENV_NAMES, self.CONF_NAMES)
        self.assertNotEqual(self.ENV_NAMES, "lane app")
        conf = self.plant_lane_conf(CHECK_NAMES=self.CONF_NAMES)
        landed = conf.read_text(encoding="utf-8")
        self.assertIn(f"CHECK_NAMES='{self.CONF_NAMES}'", landed)
        self.assertNotIn("gamma", landed)
        self.assertNotIn("delta", landed)
        self.plant_gh_rows([
            {"name": "gamma", "state": "SUCCESS"},
            {"name": "delta", "state": "SUCCESS"},
        ])
        proc = self.watch(
            extra_env=self.conf_env(CHECK_NAMES=self.ENV_NAMES),
        )
        self.assertEqual(proc.exit, 0, self.combined(proc))
        self.assertEqual(
            self.settled_line(proc.stdout or ""),
            "settled rows=2 pass=2 fail=0 skip=0",
            proc.stdout,
        )

    def test_environment_check_names_do_not_settle_on_the_conf_value(self):
        conf = self.plant_lane_conf(CHECK_NAMES=self.CONF_NAMES)
        landed = conf.read_text(encoding="utf-8")
        self.assertIn(f"CHECK_NAMES='{self.CONF_NAMES}'", landed)
        self.assertNotIn(self.ENV_NAMES, landed)
        self.plant_gh_rows([
            {"name": "alpha", "state": "SUCCESS"},
            {"name": "beta", "state": "SUCCESS"},
        ])
        proc = self.watch(
            extra_env=self.conf_env(CHECK_NAMES=self.ENV_NAMES),
        )
        self.assertEqual(proc.exit, 3, self.combined(proc))
        self.assertIn("missing=", proc.stderr or "")
        self.assertIsNone(self.settled_line(proc.stdout or ""))

    def test_a_conf_without_check_names_refuses_conf_key(self):
        conf = self.plant_lane_conf(omit=("CHECK_NAMES",))
        landed = conf.read_text(encoding="utf-8")
        self.assertNotIn("CHECK_NAMES", landed)
        self.assertNotIn("CHECK_NAME", landed)
        self.plant_gh_rows([
            {"name": "alpha", "state": "SUCCESS"},
            {"name": "beta", "state": "SUCCESS"},
        ])
        proc = self.watch(extra_env=self.conf_env())
        self.assert_refusal(proc, "conf-key")



# PIN_GH_NO_CHECKS: gh reports a head with no check registered as an
# error even under --json: `no checks reported on the '<branch>' branch`
# on stderr, exit 1 (gh 2.99.0, pkg/cmd/pr/checks/checks.go: the error
# returns before the JSON write). The seconds after a push read so.
GH_NO_CHECKS = "no checks reported on the 'lane/session-fixture' branch\n"


class NoChecksReported(_WatchFixture):
    """gh's no-checks error is the zero-row table, not a failure to look.

    LD-37, 2026-09-14: every nonzero gh exit refused gh-error, so a watch
    started in the seconds after a push refused instead of polling, while
    the pinned reading says zero rows means not yet reported. One producer
    (Claude Opus 5) under hand grant 221 wrote these tests and the fix; the
    second producer is absent by the owner's word."""

    GREEN = [
        {"name": "lane", "state": "SUCCESS"},
        {"name": "app", "state": "SUCCESS"},
    ]

    def test_no_checks_reported_then_a_table_keeps_polling_and_settles(self):
        self.plant_gh_steps([{"exit": 1, "stderr": GH_NO_CHECKS}, self.GREEN])
        proc = self.watch(expect_names="lane,app")
        text = self.combined(proc)
        self.assertEqual(proc.exit, 0, text)
        self.assertIsNotNone(self.settled_line(proc.stdout or ""), text)
        polls = self.poll_matches(proc.stderr or "")
        self.assertTrue(polls, f"no poll lines: {text!r}")
        self.assertEqual(polls[0].group(1), "0", f"the first poll reads zero rows: {text!r}")

    def test_no_checks_reported_throughout_aborts_at_the_wall_with_zero_rows(self):
        self.plant_gh_raw("", exit_code=1, stderr=GH_NO_CHECKS)
        proc = self.watch(expect_names="lane,app")
        text = self.combined(proc)
        self.assertEqual(proc.exit, 3, text)
        counts = self.last_counts(proc)
        self.assertIsNotNone(counts, f"abort printed no poll counts: {text!r}")
        self.assertEqual(counts["rows"], 0, counts)
        self.assertIsNone(self.settled_line(proc.stdout or ""))

    def test_the_no_checks_text_on_another_exit_still_refuses_gh_error(self):
        # The clean neighbour: the reading is gh's exit 1 with that text,
        # not the words alone; exit 4 is gh's authentication failure.
        self.plant_gh_raw("", exit_code=4, stderr=GH_NO_CHECKS)
        proc = self.watch(expect_names="lane,app")
        self.assertIn("no checks reported", proc.stderr or "")
        self.assert_refusal(proc, "gh-error")


if __name__ == "__main__":
    unittest.main()
