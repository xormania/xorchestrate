"""run: one result shape, one timeout policy, the wire parsed
(docs/tests.md §The kit, rule 5)."""

from __future__ import annotations

import os
import sys
import time
import unittest

import support

from ops.testkit import NotOnTheWire, PlantFailed, Timeout, World, run
from ops.testkit.command import parse_wire


class OneShape(unittest.TestCase):
    def test_result_has_the_one_shape(self):
        with World() as world:
            result = run([sys.executable, "-c", "import sys; sys.stdout.write('o'); sys.stderr.write('e'); sys.exit(3)"],
                         cwd=world.cwd, env=world.env())
            self.assertEqual((result.exit, result.stdout, result.stderr), (3, "o", "e"))
            self.assertEqual(result.cwd, str(world.cwd))
            self.assertEqual(result.argv[0], sys.executable)
            self.assertGreaterEqual(result.duration, 0.0)
            self.assertEqual(set(result.__dataclass_fields__), {"argv", "exit", "stdout", "stderr", "duration", "cwd"})

    def test_stdin_reaches_the_child(self):
        with World() as world:
            result = run([sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"],
                         cwd=world.cwd, env=world.env(), stdin="abc")
            self.assertEqual(result.stdout, "ABC\n")


class TimeoutPolicy(unittest.TestCase):
    def test_timeout_kills_reaps_and_names_the_argv(self):
        with World() as world:
            argv = [sys.executable, "-c", "import os, sys, time; sys.stdout.write(str(os.getpid())); sys.stdout.flush(); time.sleep(5)"]
            with self.assertRaises(Timeout) as caught:
                run(argv, cwd=world.cwd, env=world.env(), timeout=0.5)
            self.assertIn("time.sleep(5)", str(caught.exception))
            self.assertIn("0.5", str(caught.exception))
        # no child survives: every python child of this process has been reaped
        alive = [p for p in os.listdir("/proc") if p.isdigit() and _parent(p) == os.getpid() and "time.sleep(5)" in _cmdline(p)]
        self.assertEqual(alive, [])

    def test_timeout_is_bounded_when_a_descendant_holds_the_pipes(self):
        # Ruled rank 3 (record 20260915T025600Z-adjudicate-codex-68d3d9): the
        # cleanup after the kill waited on the pipes with no bound, and a
        # grandchild holding them kept Timeout from arriving.
        marker = f"grandchild-of-{os.getpid()}"
        program = (
            "import subprocess, sys, time; "
            f"subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(5)  # {marker}']); "
            "sys.stdout.write('parent'); sys.stdout.flush(); time.sleep(5)"
        )
        with World() as world:
            started = time.monotonic()
            with self.assertRaises(Timeout):
                run([sys.executable, "-c", program], cwd=world.cwd, env=world.env(), timeout=0.2)
            self.assertLess(time.monotonic() - started, 2.0, "Timeout arrives within the cleanup allowance")
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            alive = [p for p in os.listdir("/proc") if p.isdigit() and marker in _cmdline(p)]
            if not alive:
                break
            time.sleep(0.05)
        self.assertEqual(alive, [], "the descendant did not outlive the run")


def _parent(pid: str) -> int:
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
            return int(fh.read().rsplit(")", 1)[1].split()[1])
    except OSError:
        return -1


def _cmdline(pid: str) -> str:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            return fh.read().replace(b"\0", b" ").decode(errors="replace")
    except OSError:
        return ""


class TheWire(unittest.TestCase):
    def refuser(self, world, *argv):
        return run([sys.executable, str(support.REFUSER), *argv], cwd=world.cwd, env=world.env())

    def test_refusal_parses_the_wire_line_and_rejects_two_lines_stdout_and_wrong_order(self):
        with World() as world:
            refusal = self.refuser(world, "bogus").refusal()
            self.assertEqual(refusal.cls, "arguments")
            self.assertEqual(refusal.expected, "serve NAME")
            self.assertEqual(refusal.found, "bogus")
            self.assertEqual(refusal.needed, "the verb and its one argument")
            clean = self.refuser(world, "serve", "x")
            with self.assertRaises(NotOnTheWire):
                clean.refusal()
            for program in (
                "import sys; sys.stderr.write('x: expected a; found b; needed c\\nsecond\\n'); sys.exit(2)",
                "import sys; sys.stdout.write('noise'); sys.stderr.write('x: expected a; found b; needed c\\n'); sys.exit(2)",
                "import sys; sys.stderr.write('x: found b; expected a; needed c\\n'); sys.exit(2)",
                "import sys; sys.stderr.write('no class token here\\n'); sys.exit(2)",
            ):
                with self.subTest(program=program), self.assertRaises(NotOnTheWire):
                    run([sys.executable, "-c", program], cwd=world.cwd, env=world.env()).refusal()

    def test_a_malformed_refusal_is_not_certified(self):
        # Ruled rank 2 (record 20260915T025600Z-adjudicate-codex-68d3d9): whitespace
        # stdout, an upper-case or underscored class token and an empty field
        # all passed as the wire.
        with World() as world:
            for program in (
                "import sys; sys.stdout.write(' \\n'); sys.stderr.write('x: expected a; found b; needed c\\n'); sys.exit(2)",
                "import sys; sys.stderr.write('INPUT: expected a; found b; needed c\\n'); sys.exit(2)",
                "import sys; sys.stderr.write('my_class: expected a; found b; needed c\\n'); sys.exit(2)",
                "import sys; sys.stderr.write('x: expected ; found b; needed c\\n'); sys.exit(2)",
                "import sys; sys.stderr.write('x: expected a; found ; needed c\\n'); sys.exit(2)",
                "import sys; sys.stderr.write('x: expected a; found b; needed\\n'); sys.exit(2)",
            ):
                with self.subTest(program=program), self.assertRaises(NotOnTheWire):
                    run([sys.executable, "-c", program], cwd=world.cwd, env=world.env()).refusal()
            clean = run([sys.executable, "-c",
                         "import sys; sys.stderr.write('input-2: expected a; found b; needed c\\n'); sys.exit(2)"],
                        cwd=world.cwd, env=world.env()).refusal()
            self.assertEqual((clean.cls, clean.expected, clean.found, clean.needed), ("input-2", "a", "b", "c"))
        parsed = parse_wire("arguments: expected serve NAME; found bogus; needed the verb and its one argument")
        self.assertEqual(parsed.cls, "arguments")

    def test_expect_turns_an_unexpected_exit_into_invalid_not_a_verdict(self):
        with World() as world:
            with self.assertRaises(PlantFailed):
                run([sys.executable, "-c", "raise SystemExit(1)"], cwd=world.cwd, env=world.env(), expect=0)
            ok = run([sys.executable, "-c", "raise SystemExit(1)"], cwd=world.cwd, env=world.env(), expect=1)
            self.assertEqual(ok.exit, 1)
            with self.assertRaises(PlantFailed):
                run(["no-such-program-anywhere"], cwd=world.cwd, env=world.env())
