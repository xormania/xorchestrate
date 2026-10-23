"""The census ratchet (plan slice 17): the three counts never rise; a fall
prints the floor; an absent baseline refuses on the wire; the baseline
is written only under an identity. The check is the subject and is not
read here; trees are planted in a World."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

from ops.testkit import World, run

CHECK = Path(__file__).resolve().parents[1] / "checks" / "testkit_ratchet.py"

CLEAN = "import unittest\nfrom ops.testkit import World\n\nclass T(unittest.TestCase):\n    def test_x(self):\n        pass\n"
HOME_BY_HAND = 'import os\n\ndef env(home):\n    return {"HOME": str(home), "PATH": "/usr/bin"}\n'
HOME_ASSIGNED = 'import os\n\ndef test_x():\n    os.environ["HOME"] = "/tmp/x"\n'
EMBEDDED = 'PROGRAM = "#!/usr/bin/env python3\\nprint(1)\\n"\n'
RUN_NO_TIMEOUT = 'import subprocess\n\ndef go():\n    return subprocess.run(["true"], capture_output=True)\n'
RUN_WITH_TIMEOUT = 'import subprocess\n\ndef go():\n    return subprocess.run(["true"], capture_output=True, timeout=5)\n'


class _Ratchet(unittest.TestCase):
    def setUp(self):
        self.world = World(prefix="ratchet-")
        self.addCleanup(self.world.close)
        self.root = self.world.root / "tree"
        self.baseline = self.world.root / "baseline.json"

    def plant(self, name: str, body: str) -> Path:
        return self.world.plant_text(self.root / "ops" / "x" / "tests" / name, body)

    def check(self, *extra, agent: str | None = None):
        env = self.world.env({"WF_AGENT": agent} if agent else None)
        if agent is None:
            env.pop("WF_AGENT", None)
        return run([sys.executable, str(CHECK), "--root", str(self.root), "--baseline", str(self.baseline), *extra],
                   cwd=self.world.cwd, env=env, timeout=60)

    def write_baseline(self):
        result = self.check("--write-baseline", agent="Ratchet Tester <ratchet@example.invalid>")
        self.assertEqual(result.exit, 0, result.stderr)
        self.assertTrue(self.baseline.is_file())
        return json.loads(self.baseline.read_text(encoding="utf-8"))


class TheCounts(_Ratchet):
    def test_the_three_counts_are_measured_by_ast(self):
        self.plant("test_a.py", HOME_BY_HAND + EMBEDDED + RUN_NO_TIMEOUT)
        self.plant("test_b.py", HOME_ASSIGNED + RUN_WITH_TIMEOUT)
        self.plant("test_c.py", CLEAN)
        (self.root / "ops" / "x" / "notes.py").parent.mkdir(parents=True, exist_ok=True)
        self.world.plant_text(self.root / "ops" / "x" / "tool.py", HOME_BY_HAND + RUN_NO_TIMEOUT)  # not under tests/
        baseline = self.write_baseline()
        self.assertEqual(baseline["counts"], {"home_by_hand": 2, "embedded_programs": 1, "run_without_timeout": 1})
        self.assertEqual(sorted(baseline["files"]["home_by_hand"]), ["ops/x/tests/test_a.py", "ops/x/tests/test_b.py"])


class TheCensus(_Ratchet):
    def test_census_prints_modules_on_and_off_the_kit_and_skips_fixture_applications(self):
        # owner 2026-09-15: the measure of "on the kit" the deliverable names.
        self.plant("test_a.py", CLEAN)
        self.plant("test_b.py", HOME_BY_HAND)
        self.plant("test_c.py", "# testkit: exempt reads a payload shape\nimport unittest\n")
        self.world.plant_text(self.root / "ops" / "devlane" / "fixtures" / "apps" / "svc" / "tests" / "test_app.py", HOME_BY_HAND)
        result = self.check("--census")
        self.assertEqual(result.exit, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), "modules=3 on_kit=1 exempt=1 off_kit=1")
        baseline = self.write_baseline()
        self.assertEqual(baseline["counts"]["home_by_hand"], 1, "a fixture application's tests are not lane tests")


class TheRatchet(_Ratchet):
    def test_a_rising_count_exits_one_naming_the_file(self):
        self.plant("test_a.py", CLEAN)
        self.write_baseline()
        self.plant("test_new.py", HOME_BY_HAND)
        result = self.check()
        self.assertEqual(result.exit, 1, result.stdout + result.stderr)
        self.assertIn("home_by_hand: 0 -> 1", result.stdout)
        self.assertIn("ops/x/tests/test_new.py", result.stdout)
        self.assertIn("never rise", result.stdout)

    def test_a_rising_count_under_a_nested_tests_path_is_reported(self):
        # Ruled rank 7 (record 20260915T025600Z-adjudicate-codex-68d3d9):
        # test_modules read only the immediate children of a tests/ directory,
        # so tests/fakes/ grew the debt uncounted.
        self.plant("test_a.py", CLEAN)
        self.write_baseline()
        self.world.plant_text(self.root / "ops" / "x" / "tests" / "fakes" / "prog.py", RUN_NO_TIMEOUT)
        result = self.check()
        self.assertEqual(result.exit, 1, result.stdout + result.stderr)
        self.assertIn("run_without_timeout: 0 -> 1", result.stdout)
        self.assertIn("ops/x/tests/fakes/prog.py", result.stdout)

    def test_an_equal_count_exits_zero(self):
        self.plant("test_a.py", HOME_BY_HAND + RUN_NO_TIMEOUT)
        self.write_baseline()
        result = self.check()
        self.assertEqual(result.exit, 0, result.stdout + result.stderr)
        self.assertIn("ratchet holds", result.stdout)

    def test_a_falling_count_exits_zero_and_prints_the_floor(self):
        path = self.plant("test_a.py", HOME_BY_HAND + RUN_NO_TIMEOUT + RUN_NO_TIMEOUT)
        self.write_baseline()
        self.world.plant_text(path, CLEAN)
        result = self.check()
        self.assertEqual(result.exit, 0, result.stdout + result.stderr)
        self.assertIn("home_by_hand: 1 -> 0; the new floor", result.stdout)
        self.assertIn("run_without_timeout: 2 -> 0; the new floor", result.stdout)

    def test_a_rise_in_one_count_beside_a_fall_in_another_is_still_a_finding(self):
        self.plant("test_a.py", HOME_BY_HAND)
        self.write_baseline()
        self.world.plant_text(self.root / "ops" / "x" / "tests" / "test_a.py", EMBEDDED)
        result = self.check()
        self.assertEqual(result.exit, 1, result.stdout)
        self.assertIn("embedded_programs: 0 -> 1", result.stdout)
        self.assertIn("home_by_hand: 1 -> 0", result.stdout)


class TheWire(_Ratchet):
    def refusal(self, result, cls):
        self.assertEqual(result.exit, 2, result.stdout + result.stderr)
        self.assertEqual(result.stdout, "")
        refusal = result.refusal()
        self.assertEqual(refusal.cls, cls, refusal.line)
        return refusal

    def test_an_absent_baseline_refuses_baseline(self):
        self.plant("test_a.py", CLEAN)
        self.refusal(self.check(), "baseline")

    def test_a_malformed_baseline_refuses_baseline(self):
        self.plant("test_a.py", CLEAN)
        self.world.plant_text(self.baseline, '{"counts": {"home_by_hand": 1}}')
        self.refusal(self.check(), "baseline")

    def test_write_baseline_needs_an_identity(self):
        self.plant("test_a.py", CLEAN)
        refusal = self.refusal(self.check("--write-baseline"), "identity")
        self.assertIn("WF_AGENT", refusal.line)
        self.assertFalse(self.baseline.exists())

    def test_an_unknown_flag_refuses_arguments(self):
        self.refusal(self.check("--nope"), "arguments")
