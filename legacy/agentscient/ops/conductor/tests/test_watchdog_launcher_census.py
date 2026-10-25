"""PR 66 item 1: launcher census pgrep ERE for absolute/versioned Python.

Contract: ops/conductor/pr66-fixes.md item 1. Reuses the watchdog
fixture; does not read the tool source. The pattern passed to pgrep
must be valid POSIX ERE, match python3 as a basename or absolute
versioned python3.N next to launch.py, and not count unrelated
commands. The census stays a named heuristic.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from test_watchdog import (
    REPO,
    RUN_CAP,
    SIBLINGS,
    TOOL,
    _live_store_paths,
    _WatchdogFixture,
)

from ops.testkit import Timeout, World, run

LAUNCH_CMDLINES = (
    "python3 ops/devlane/dispatch/launch.py --job author-tests",
    "/usr/bin/python3 ops/devlane/dispatch/launch.py --job author-tests",
    "python3.12 ops/devlane/dispatch/launch.py --job author-tests",
    "/usr/bin/python3.13 ops/devlane/dispatch/launch.py --job author-tests",
    "/usr/local/bin/python3.11 ops/devlane/dispatch/launch.py --once",
)

UNRELATED_CMDLINES = (
    "python2 ops/devlane/dispatch/launch.py --job author-tests",
    "/usr/bin/python ops/devlane/dispatch/launch.py --job author-tests",
    "bash ops/devlane/dispatch/launch.py --job author-tests",
    "python3 ops/devlane/dispatch/other.py --job author-tests",
    "python3.12-config --ldflags",
    "/usr/bin/python3.13-coverage run ops/devlane/dispatch/launch.py",
    "node ops/devlane/dispatch/launch.py",
)


def _pgrep_pattern(argv: list[str]) -> str:
    flags = {"-f", "-c", "-fc", "-cf", "-a", "-l"}
    tokens = [item for item in argv if item not in flags and not item.startswith("-")]
    if not tokens:
        raise AssertionError(f"pgrep argv has no pattern: {argv!r}")
    return tokens[-1]


def _ere_run(world: World, pattern: str, value: str):
    return run(
        ["grep", "-E", "-e", pattern],
        stdin=value if value.endswith("\n") else value + "\n",
        cwd=world.cwd,
        env=world.env({"POSIXLY_CORRECT": "1"}),
        timeout=10,
    )


class LauncherCensusPgrep(_WatchdogFixture):
    """The census pattern is valid ERE and matches versioned/absolute python3."""

    def setUp(self):
        self.world = World(prefix="watchdog-launcher-census-")
        self.world.require("grep")
        self._live = self.world.watch(*_live_store_paths(), contents=False)
        self.root = self.world.root
        self.home = self.world.home
        self.xdg = self.world.xdg_state
        self.scratch = self.world.path("scratch")
        self.scratch.mkdir()
        self.bin = self.world.bin
        self.store = self.xdg / "agentscient" / "dispatch"
        self.home_store = self.home / ".local" / "state" / "agentscient" / "dispatch"
        self.assertFalse(self.store.exists(), "plant: isolated XDG store must start absent")
        self.assertFalse(self.home_store.exists(), "plant: isolated HOME store must start absent")
        self.gh = self.bin / "gh"
        self.argv_log = self.scratch / "gh-argv.jsonl"
        self.staged = self.world.stage(
            TOOL,
            "watchdog.py",
            siblings=SIBLINGS if TOOL.is_file() else (),
        )
        self.conf = self.world.plant_text(
            "lane.conf",
            "LANE_NAME='agentscient'\nTRUNK_BRANCH='dev'\n",
        )

    def tearDown(self):
        try:
            self._live.assert_unchanged(self, "live dispatch store mutated")
        finally:
            self.world.close()

    def env(self, *, xdg=False, extra=None, home=None):
        self.assertIs(xdg, False, "this family uses the World's XDG state root")
        self.assertIsNone(home, "this family uses the World's HOME")
        overrides = {
            "LANE_CONF": str(self.conf),
            "PYTHONUNBUFFERED": "1",
        }
        if extra:
            overrides.update(extra)
        return self.world.env(overrides)

    def invoke(self, argv, *, extra_env=None, xdg=False, timeout=RUN_CAP, home=None):
        try:
            return run(
                [sys.executable, "-u", str(self.staged), *argv],
                cwd=REPO,
                env=self.env(xdg=xdg, extra=extra_env, home=home),
                timeout=timeout,
            )
        except Timeout as exc:
            self.fail(f"watchdog exceeded {timeout}s: {exc}")

    def plant_pgrep(self) -> Path:
        log = self.world.tools.log_path("pgrep")
        self.assertFalse(log.exists(), "plant: pgrep argv log must start absent")
        path = self.world.tools.plant("pgrep", stdout="0\n")
        self.assertEqual(path, self.bin / "pgrep")
        return log

    def pgrep_argvs(self, log: Path) -> list[list[str]]:
        self.assertEqual(log, self.world.tools.log_path("pgrep"))
        return [row["argv"] for row in self.world.tools.calls("pgrep")]

    def captured_pattern(self, log: Path) -> str:
        argvs = self.pgrep_argvs(log)
        self.assertTrue(argvs, f"watchdog did not invoke pgrep: {log}")
        patterns = [_pgrep_pattern(argv) for argv in argvs]
        self.assertTrue(patterns, f"pgrep invocations had no pattern: {argvs!r}")
        first = patterns[0]
        self.assertTrue(
            all(item == first for item in patterns),
            f"pgrep pattern drifted across invocations: {patterns!r}",
        )
        return first

    def test_pgrep_pattern_is_valid_posix_ere(self):
        log = self.plant_pgrep()
        self.ensure_store()
        proc = self.once(extra=["--heartbeat-every", "1"])
        self.assert_armed(proc, store=self.store)
        self.assertEqual(proc.exit, 0, self.combined(proc))
        beats = self.heartbeat_lines(proc)
        self.assertEqual(len(beats), 1, proc.stdout)
        self.assertIn("launchers=", beats[0])
        self.assertNotIn("launchers=unreachable", beats[0])
        pattern = self.captured_pattern(log)
        self.assertIsInstance(pattern, str)
        self.assertTrue(pattern, "pgrep pattern was empty")
        check = _ere_run(self.world, pattern, "python3 ops/devlane/dispatch/launch.py --once")
        self.assertNotEqual(
            check.exit,
            2,
            f"pgrep pattern is not valid POSIX ERE: {pattern!r} stderr={check.stderr!r}",
        )

    def test_pgrep_pattern_matches_basename_absolute_and_versioned_python3(self):
        log = self.plant_pgrep()
        self.ensure_store()
        proc = self.once(extra=["--heartbeat-every", "1"])
        self.assert_armed(proc, store=self.store)
        self.assertEqual(proc.exit, 0, self.combined(proc))
        pattern = self.captured_pattern(log)
        ere_ok = _ere_run(self.world, pattern, LAUNCH_CMDLINES[0])
        self.assertNotEqual(ere_ok.exit, 2, ere_ok.stderr)
        missed = []
        for cmdline in LAUNCH_CMDLINES:
            matched = _ere_run(self.world, pattern, cmdline)
            self.assertNotEqual(
                matched.exit,
                2,
                f"pattern {pattern!r} is not valid POSIX ERE on {cmdline!r}",
            )
            if matched.exit != 0:
                missed.append(cmdline)
        self.assertEqual(
            missed,
            [],
            f"pattern {pattern!r} missed launch.py under python3 paths: {missed!r}",
        )

    def test_pgrep_pattern_does_not_count_unrelated_commands(self):
        log = self.plant_pgrep()
        self.ensure_store()
        proc = self.once(extra=["--heartbeat-every", "1"])
        self.assert_armed(proc, store=self.store)
        self.assertEqual(proc.exit, 0, self.combined(proc))
        pattern = self.captured_pattern(log)
        hits = []
        for cmdline in UNRELATED_CMDLINES:
            matched = _ere_run(self.world, pattern, cmdline)
            self.assertNotEqual(matched.exit, 2, matched.stderr)
            if matched.exit == 0:
                hits.append(cmdline)
        self.assertEqual(
            hits,
            [],
            f"pattern {pattern!r} counted unrelated commands: {hits!r}",
        )
        beats = self.heartbeat_lines(proc)
        self.assertEqual(len(beats), 1, proc.stdout)
        self.assertRegex(beats[0], r"launchers=(\d+|unreachable)")


if __name__ == "__main__":
    unittest.main()
