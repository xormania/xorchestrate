"""Tools: fakes driven by data, an append-only call log, failure by data,
a wrapped real tool logged the same way (docs/tests.md rule 6)."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest

from ops.testkit import PlantFailed, World, run


class Planting(unittest.TestCase):
    def test_responses_match_argv_and_inject_failure_by_data(self):
        with World() as world:
            world.tools.plant("gh", stdout="default\n", responses=[
                {"match": ["pr", "view"], "stdout": "https://example.invalid/pr/1\n"},
                {"match": ["api"], "stderr": "gh: authentication failed\n", "exit": 1},
            ])
            env = world.env()
            self.assertEqual(run(["gh", "pr", "view", "1"], cwd=world.cwd, env=env).stdout, "https://example.invalid/pr/1\n")
            failed = run(["gh", "api", "x"], cwd=world.cwd, env=env)
            self.assertEqual((failed.exit, failed.stderr), (1, "gh: authentication failed\n"))
            self.assertEqual(run(["gh", "other"], cwd=world.cwd, env=env).stdout, "default\n")
            calls = world.tools.calls("gh")
            self.assertEqual([c["argv"] for c in calls], [["pr", "view", "1"], ["api", "x"], ["other"]])
            self.assertEqual(calls[0]["cwd"], str(world.cwd))

    def test_plant_refuses_when_not_executable(self):
        with World() as world:
            planted = world.tools.plant("tool")
            self.assertTrue(os.access(planted, os.X_OK))
            with self.assertRaises(PlantFailed):
                world.tools.plant_executable(world.bin / "empty", "")
            self.assertEqual(world.tools.calls("never-called"), [])


class Logging(unittest.TestCase):
    def test_sixty_concurrent_calls_log_sixty_lines(self):
        # The planted fault this catches: a program that rewrites its log
        # (read, append in memory, write back) logged 11 of 60 concurrent
        # calls; one append per call logs 60 of 60.
        with World() as world:
            world.tools.plant("busy")
            env = world.env()

            # Sixty children started back to back, all alive at once, no
            # threads: the log's append is what is under test.
            children = [
                world.own(subprocess.Popen(["busy", str(i)], cwd=world.cwd, env=env, stdin=subprocess.DEVNULL,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE))
                for i in range(60)
            ]
            exits = []
            for child in children:
                child.communicate(timeout=300)
                exits.append(child.returncode)
            self.assertEqual(exits, [0] * 60)
            calls = world.tools.calls("busy")
            self.assertEqual(len(calls), 60)
            self.assertEqual(sorted(int(c["argv"][0]) for c in calls), list(range(60)))

    def test_wrap_execs_the_real_tool_and_logs_argv(self):
        with World() as world:
            world.tools.wrap("git")
            result = run(["git", "--version"], cwd=world.cwd, env=world.env())
            self.assertEqual(result.exit, 0, result.stderr)
            self.assertTrue(result.stdout.startswith("git version"), result.stdout)
            self.assertEqual([c["argv"] for c in world.tools.calls("git")], [["--version"]])
            with self.assertRaises(PlantFailed):
                world.tools.wrap("no-such-tool-anywhere")

    def test_a_script_program_is_logged_by_its_own_hand_or_not_at_all(self):
        with World() as world:
            world.tools.plant("raw", script=f"#!{sys.executable}\nimport sys\nsys.stdout.write('raw')\n")
            self.assertEqual(run(["raw"], cwd=world.cwd, env=world.env()).stdout, "raw")
            self.assertEqual(world.tools.calls("raw"), [])
