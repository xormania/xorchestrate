"""A strict world's PATH is its own bin; host tools enter by ambient_tools
or plant (LD-72, PR #241 comment 4023387510).

``host_path`` chooses: False is the strict PATH these rows are about, and
True, today's default, puts the host's PATH after the world's bin. Both
values are pinned here, because the default moves to False once the sweep
in ``ops/conductor/handoff/HANDOFF-2026-09-16-hermetic-path-sweep.md``
lands, and one row should flip then, not a promise.
"""

from __future__ import annotations

import os
import unittest

from ops.testkit import NotOnTheWire, PlantFailed, World, hermetic_env, run
from ops.testkit.command import parse_wire

ORIGIN = "LD-72, PR #241 comment 4023387510"


def strict_world(case, **kwargs):
    """A world with the hermetic PATH. A kit that does not take the kwarg
    fails the case rather than erroring it, so the red reads as a red."""
    try:
        return World(host_path=False, **kwargs)
    except TypeError as exc:
        case.fail(f"World(host_path=False) is not accepted: {exc}")


class HermeticPath(unittest.TestCase):
    """Regression rows for LD-72: hermetic PATH, ambient_tools, the wire."""

    def test_strict_world_path_is_exactly_the_worlds_bin(self):
        with self.subTest(id="bare-path", origin=ORIGIN):
            with strict_world(self) as world:
                self.assertEqual(world.env()["PATH"], str(world.bin))

    def test_host_path_defaults_on_and_follows_the_worlds_bin(self):
        """Today's default, and the row that flips when the sweep lands."""
        with self.subTest(id="host-path-default", origin=ORIGIN):
            host = os.environ["PATH"]
            with World() as world:
                self.assertEqual(
                    world.env()["PATH"], f"{world.bin}{os.pathsep}{host}"
                )

    def test_a_host_path_that_is_not_a_choice_is_invalid(self):
        """Origin: 20260916T122524Z-review-codex-05a9f5#9 at aea65bb9 —
        bool('false') is True, so a caller who spelled a false value got the
        host's PATH appended and no word about it. A World's host_path is a
        choice, so only a choice is taken."""
        for value in ("false", "true", "0", "1", "", 0, 1, None, object()):
            with self.subTest(value=repr(value), origin="05a9f5#9"):
                with self.assertRaises(PlantFailed):
                    World(host_path=value).close()

    def test_hermetic_env_refuses_a_word_that_means_a_choice(self):
        """The other door takes a string as the PATH to follow, so the same
        word cannot be told from a one-entry PATH. It is refused rather than
        read either way."""
        with World() as world:
            for value in ("false", "TRUE", "0", "no"):
                with self.subTest(value=value, origin="05a9f5#9"):
                    with self.assertRaises(PlantFailed):
                        hermetic_env(world.home, bin=world.bin, host_path=value)

    def test_hermetic_env_still_takes_a_path(self):
        """The clean neighbour: a real PATH value is still a PATH value."""
        with World() as world:
            env = hermetic_env(world.home, bin=world.bin, host_path="/opt/x")
            self.assertEqual(env["PATH"], f"{world.bin}{os.pathsep}/opt/x")

    def test_hermetic_env_takes_the_same_choice(self):
        """The other door: an env built without a World."""
        with self.subTest(id="hermetic-env-host-path", origin=ORIGIN):
            with World() as world:
                try:
                    strict = hermetic_env(world.home, bin=world.bin, host_path=False)
                    named = hermetic_env(world.home, bin=world.bin, host_path="/opt/x")
                except TypeError as exc:
                    self.fail(f"hermetic_env does not take host_path: {exc}")
                self.assertEqual(strict["PATH"], str(world.bin))
                self.assertEqual(named["PATH"], f"{world.bin}{os.pathsep}/opt/x")

    def test_unnamed_unplanted_tool_raises_plantfailed_on_the_wire(self):
        tool = "no-such-tool-ld72"
        with self.subTest(id="unplanted-wire", origin=ORIGIN):
            with strict_world(self) as world:
                try:
                    result = run([tool], cwd=world.cwd, env=world.env())
                except PlantFailed as exc:
                    line = str(exc)
                else:
                    self.fail(
                        f"run fabricated a Result exit={result.exit!r} "
                        f"instead of PlantFailed"
                    )
            self.assertEqual(
                len(line.splitlines()), 1, f"wire is one line, got {line!r}"
            )
            try:
                parsed = parse_wire(line)
            except NotOnTheWire as exc:
                raise self.failureException(
                    f"PlantFailed message is not a wire line: {line!r}"
                ) from exc
            self.assertEqual(parsed.cls, "unplanted")
            self.assertIn(tool, parsed.found)
            self.assertIn("plant", parsed.needed)
            self.assertIn("ambient_tools", parsed.needed)

    def test_ambient_tools_git_runs_and_the_call_is_logged(self):
        with self.subTest(id="ambient-tools-git", origin=ORIGIN):
            try:
                ctx = World(ambient_tools=("git",))
            except TypeError as exc:
                self.fail(
                    f"World(ambient_tools=('git',)) is not accepted: {exc}"
                )
            with ctx as world:
                wrapper = world.bin / "git"
                self.assertTrue(wrapper.is_file(), wrapper)
                self.assertTrue(os.access(wrapper, os.X_OK), wrapper)
                result = run(
                    ["git", "--version"], cwd=world.cwd, env=world.env()
                )
                self.assertEqual(result.exit, 0, result.stderr)
                self.assertTrue(
                    result.stdout.startswith("git version"), result.stdout
                )
                self.assertEqual(
                    [c["argv"] for c in world.tools.calls("git")],
                    [["--version"]],
                )

    def test_ambient_path_raises_plantfailed(self):
        with self.subTest(id="ambient-path-refused", origin=ORIGIN):
            with self.assertRaises(PlantFailed) as caught:
                World(ambient=("PATH",))
            line = str(caught.exception)
            self.assertIn("PATH", line)
            self.assertIn("ambient_tools", line)

    def test_world_git_works_with_no_ambient_tools(self):
        with self.subTest(id="world-git", origin=ORIGIN):
            with World() as world:
                try:
                    result = world.git("--version")
                except PlantFailed as exc:
                    raise self.failureException(
                        f"World.git() failed without ambient_tools: {exc}"
                    ) from exc
                self.assertEqual(result.exit, 0, result.stderr)
                self.assertTrue(
                    result.stdout.startswith("git version"), result.stdout
                )
                self.assertTrue(
                    (world.bin / "git").is_file(),
                    "World.git() declares git ambient for itself",
                )
                calls = world.tools.calls("git")
                self.assertEqual(len(calls), 1, calls)
                self.assertIn("--version", calls[0]["argv"])

    def test_planted_tool_wins_over_ambient_of_the_same_name(self):
        with self.subTest(id="plant-wins", origin=ORIGIN):
            try:
                ctx = World(ambient_tools=("git",))
            except TypeError as exc:
                self.fail(
                    f"World(ambient_tools=('git',)) is not accepted: {exc}"
                )
            with ctx as world:
                marker = "planted-wins-ld72\n"
                planted = world.tools.plant("git", stdout=marker)
                self.assertTrue(os.access(planted, os.X_OK), planted)
                result = run(
                    ["git", "--version"], cwd=world.cwd, env=world.env()
                )
                self.assertEqual(result.stdout, marker)
                self.assertNotIn("git version", result.stdout)
