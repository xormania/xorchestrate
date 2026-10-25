"""The probe reports INVALID when an arm did not run.

This app's whole argument is that "nobody looked" must never print the
same way as "we checked and it was fine", and the probe is where that
is decided: a harness that died before loading anything shows no leak,
and reading that as isolation working is a test proving its own setup
never happened.
"""

import importlib.util
import unittest
from unittest import mock
from pathlib import Path

from ops.testkit import World

HARNESS = Path(__file__).resolve().parents[1]

#: What `grok inspect` prints when the operator's files ARE loaded, and
#: when none are. The second is a WELL-FORMED answer, which is exactly
#: why a nonzero exit beside it is dangerous.
LEAKING = ("Project Instructions (2)\n"
           "├ /home/op/.claude/CLAUDE.md\n"
           "└ /home/op/rules.md\n")
NOTHING = "Project Instructions (0)\n└ (none)\n"


def load(name):
    spec = importlib.util.spec_from_file_location(name, HARNESS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AnArmThatDidNotRunIsNotAnAnswer(unittest.TestCase):
    def setUp(self):
        self.probe = load("probe")

    def verdict(self, isolated_exit, isolated_out=NOTHING):
        """Run both arms; the unisolated one always succeeds and leaks.

        The PROCESS is faked, not `_run`. The nonzero-exit rule lives
        inside `_run` now — deliberately, so a new arm cannot be written
        without it — and a test that replaces `_run` would step over the
        very rule it is checking and pass either way.
        """
        with World(prefix="probe-visible-") as visible, World(prefix="probe-isolated-") as isolated:
            visible.tools.plant("grok", stdout=LEAKING)
            isolated.tools.plant(
                "grok",
                stdout=isolated_out,
                stderr="grok: could not read config",
                exit=isolated_exit or 0,
            )
            isolated_env = isolated.env()
            if isolated_exit is None:
                isolated.bin.joinpath("grok").chmod(0o644)
                isolated_env = isolated.env({"PATH": str(isolated.bin)})
            with mock.patch.object(
                self.probe.isolation,
                "isolated",
                lambda harness, root, env=None: (isolated_env, []),
            ):
                result = self.probe.behavioural_grok(env=visible.env())
            self.assertEqual(
                len(visible.tools.calls("grok")), 1,
                "the unisolated arm must have been attempted",
            )
            self.assertEqual(
                len(isolated.tools.calls("grok")),
                0 if isolated_exit is None else 1,
                "the isolated arm must have reached its declared fake",
            )
            return result

    def test_a_nonzero_exit_is_invalid_even_when_stdout_parses(self):
        # A grok that failed to initialise still prints a well-formed
        # `Project Instructions (0)` header, and taking that as an
        # answer certified isolation off a broken probe.
        self.assertEqual(self.verdict(1).verdict, self.probe.INVALID)

    def test_a_clean_run_still_reports_clean(self):
        # The twin. A guard that turned a working probe into INVALID
        # would trade a false pass for a false alarm, and the only
        # free isolation check we have would stop being run.
        self.assertEqual(self.verdict(0).verdict, self.probe.CLEAN)

    def test_a_leak_is_still_reported_as_leaking(self):
        self.assertEqual(self.verdict(0, LEAKING).verdict, self.probe.LEAKING)

    def test_the_claude_arm_refuses_a_nonzero_exit_too(self):
        # The rule was fixed in the grok arm and left in this one, and
        # the arm that was missed is the one the next reviewer found
        # (Codex, PR #40, twice). It lives in `_run` now, so a third arm
        # cannot be written without it.
        for exit_code, want in ((1, self.probe.INVALID),
                                (0, self.probe.CLEAN)):
            with self.subTest(exit_code=exit_code):
                with World(prefix="probe-claude-") as world:
                    world.tools.plant(
                        "claude",
                        stdout="YES",
                        exit=exit_code,
                        responses=[{
                            "match": ["--setting-sources"],
                            "stdout": "NO",
                            "exit": exit_code,
                        }],
                    )
                    with mock.patch.object(
                        self.probe.isolation,
                        "isolated",
                        lambda harness, root, env=None: (world.env(), []),
                    ):
                        result = self.probe.behavioural_claude(env=world.env())
                    self.assertEqual(result.verdict, want)

    def test_a_transport_failure_is_invalid(self):
        # `_run` answers None for an OSError or a timeout; the same
        # branch has to cover it.
        self.assertEqual(self.verdict(None).verdict, self.probe.INVALID)


if __name__ == "__main__":
    unittest.main()
