"""Dispatcher pass-through of --answers.

Written from scope-gate-20260910.md items 1 and 2, and the landed
Dispatch §Verbs line `[--answers KEY]...`. The wrapper is the subject
and is not read here. Callee flag names come from
ops/devlane/dispatch/CONTRACT.md §Verbs, not from launch.py.

Item 2: a correction names finding keys through the declared dispatch
field --answers KEY, repeatable. Every given key must reach the launcher
argv. Item 1: one --follows naming a review record is enough to
establish the correction shape; these tests do not require a second or
third follow. Option order and undocumented-flag policy are not numbered
Contract items.

The fixture is the existing wrapper harness; this module does not restyle it.
"""

from __future__ import annotations

import re
import unittest

import test_dispatch_wrapper as wrap

from ops.testkit import Result, World, run

# CONTRACT.md §Verbs: key shape accepted on the declared field.
# The wrapper is pass-through; tests plant keys of that shape.
RECORD_ID = re.compile(
    r"^\d{8}T\d{6}Z-[a-z-]+-[a-z]+-[0-9a-f]{6}$"
)
ANSWER_KEY = re.compile(
    r"^\d{8}T\d{6}Z-[a-z-]+-[a-z]+-[0-9a-f]{6}#[1-9][0-9]*$"
)

FOLLOW = "20260901T010000Z-check-tests-codex-bbbbbb"
KEY_A = f"{FOLLOW}#1"
KEY_B = f"{FOLLOW}#2"


def flag_values(argv: list[str], flag: str) -> list[str]:
    values: list[str] = []
    i = 0
    while i < len(argv):
        if argv[i] == flag:
            if i + 1 >= len(argv):
                raise AssertionError(f"{flag} is last with no value: {argv!r}")
            values.append(argv[i + 1])
            i += 2
            continue
        i += 1
    return values


class DispatcherAnswers(wrap._WrapperFixture):
    """Item 2 field on the wrapper; item 1 needs one review follow."""

    maxDiff = None

    def setUp(self):
        super().setUp()
        self.assertRegex(FOLLOW, RECORD_ID, FOLLOW)
        for key in (KEY_A, KEY_B):
            self.assertRegex(key, ANSWER_KEY, key)
        self.assertNotEqual(KEY_A, KEY_B)
        self.world = World(prefix="dispatch-answers-")
        self.addCleanup(self.world.close)
        self.world.require("bash")

    def invoke(self, flags, *, extra_env=None, timeout=30):
        argv = ["bash", str(wrap.WRAPPER), *flags]
        if not wrap.WRAPPER.is_file():
            return Result(
                argv=tuple(argv), exit=0, stdout="", stderr="", duration=0.0, cwd=str(wrap.REPO),
            )
        return run(
            argv,
            cwd=str(wrap.REPO),
            env=self.env(extra_env),
            timeout=timeout if timeout is not None else 30,
        )

    def answers_flags(self, answers: list[str], *extra: str, **kw) -> list[str]:
        self.assertTrue(answers, "plant: answers must be non-empty")
        flags: list[str] = []
        for key in answers:
            flags.extend(["--answers", key])
        flags.extend(extra)
        return self.full_flags(*flags, follows=kw.pop("follows", FOLLOW), **kw)

    def assert_answers_reach(self, argv: list[str], answers: list[str]) -> None:
        self.assertTrue(answers, "plant: answers must be non-empty")
        got = flag_values(argv, "--answers")
        self.assertEqual(len(got), len(answers), argv)
        self.assertCountEqual(got, answers)
        for key in answers:
            self.assertIn(key, got)
        self.assertIn(FOLLOW, flag_values(argv, "--follows"), argv)

    def test_one_answers_key_reaches_dry_run_argv(self):
        """Item 2: --answers KEY is a documented field and reaches argv."""
        answers = [KEY_A]
        proc = self.invoke(self.answers_flags(answers))
        text = self.combined(proc)
        self.assertEqual(proc.returncode, 0, text)
        argv, _env = self.parse_dry_run(proc)
        self.assert_answers_reach(argv, answers)
        payload = self.parse_dry_run_object(proc)
        self.assertEqual(flag_values(payload["argv"], "--answers"), [KEY_A])
        self.assert_no_launch()

    def test_two_answers_keys_both_reach_dry_run_argv(self):
        """Item 2: every named key reaches argv; last-wins drops a finding."""
        answers = [KEY_A, KEY_B]
        proc = self.invoke(self.answers_flags(answers))
        text = self.combined(proc)
        self.assertEqual(proc.returncode, 0, text)
        argv, _env = self.parse_dry_run(proc)
        self.assert_answers_reach(argv, answers)
        payload = self.parse_dry_run_object(proc)
        self.assertCountEqual(
            flag_values(payload["argv"], "--answers"),
            answers,
        )
        self.assert_no_launch()

    def test_two_answers_keys_reach_the_foreground_launcher(self):
        """Item 2: the exec path carries every named key."""
        answers = [KEY_A, KEY_B]
        fake = self.world.tools.plant("launcher", exit=0)
        self.world.tools.plant_executable(self.pin, fake.read_text())
        proc = self.invoke(
            self.answers_flags(answers, dry_run=False),
            timeout=8,
        )
        text = self.combined(proc)
        self.assertEqual(proc.returncode, 0, text)
        calls = self.world.tools.calls("launcher")
        self.assertTrue(calls, "foreground path must launch")
        launched = calls[0]["argv"]
        self.assertIsInstance(launched, list, launched)
        self.assertTrue(
            all(isinstance(item, str) for item in launched),
            launched,
        )
        self.assert_answers_reach(launched, answers)


if __name__ == "__main__":
    unittest.main()
