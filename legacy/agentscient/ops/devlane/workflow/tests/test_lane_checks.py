"""The shared gate catches missing work and reports actual check failures.

Origin: CI audit at dev 09e6e4c, 2026-09-23. Reused: testkit.world,
testkit.run, testkit.fakes and testkit.refusal-wire.
"""

from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path

from ops.devlane.workflow.checks import lane
from ops.testkit import World, run

ROOT = Path(__file__).resolve().parents[4]
GUARD_NAMES = {"ruff", "term-wall", "testkit-ratchet", "conf-closure", "conf-literals", "probes"}


class LaneGate(unittest.TestCase):
    def setUp(self):
        self.world = World(host_path=False, ambient_tools=("bash", "dirname", "python3"))
        self.addCleanup(self.world.close)
        self.root = self.world.cwd
        for path in (ROOT / "ops/testkit").glob("*.py"):
            self.world.plant_bytes(f"cwd/ops/testkit/{path.name}", path.read_bytes())
        for rel in ("ops/bin/suites.sh", "ops/devlane/workflow/checks/lane.py"):
            self.world.plant_bytes("cwd/" + rel, (ROOT / rel).read_bytes())
        example = (ROOT / "ops/testkit/tests/fixtures/one_case.py").read_bytes()
        for directory in lane.FAMILIES:
            names = ("test_c.py", "test_l.py", "test_o.py", "test_z.py") if directory in lane.SHARDS else ("test_example.py",)
            for name in names:
                content = ((ROOT / "ops/testkit/tests/fixtures/multiple_cases.py").read_bytes()
                           if directory in lane.PARTITIONS else example)
                self.world.plant_bytes(f"cwd/{directory}/{name}", content)
        self.world.tools.plant("ruff")
        for check in lane.GUARDS:
            for path in check.required:
                if path.endswith("/probes"):
                    (self.root / path).mkdir(parents=True, exist_ok=True)
                else:
                    self.world.plant_text("cwd/" + path, "fixture input\n")
            if check.name != "ruff":
                subject = self.world.tools.plant(Path(check.argv[1]).stem)
                self.world.plant_bytes("cwd/" + check.argv[1], subject.read_bytes())

    def invoke(self, *args, serial=False, command=None):
        argv = command or ["bash", "ops/bin/suites.sh", "--profile", "rehearsal", *args]
        return run(argv, cwd=self.root, env=self.world.env({"SUITES_SERIAL": "1" if serial else "0"}), timeout=30)

    def test_local_and_workflow_commands_run_the_same_complete_plan(self):
        workflow = json.loads((ROOT / ".github/workflows/ci.yml").read_text())
        command = next(s["run"] for s in workflow["jobs"]["dev-lane"]["steps"] if s.get("id") == "verify")
        self.assertEqual(command, "python3 -m ops.devlane.workflow.checks.ci lane")
        self.assertEqual({check.name for check in lane.GUARDS}, GUARD_NAMES)
        expected = {check.name for check in lane.plan(self.root)}
        for serial, argv in ((True, None), (False, None)):
            with self.subTest(serial=serial):
                proc = self.invoke(serial=serial, command=argv)
                self.assertEqual(proc.exit, 0, proc.stdout + proc.stderr)
                names = [line[3:] for line in proc.stdout.splitlines() if line.startswith("== ")]
                self.assertEqual(set(names), expected)
                self.assertEqual(len(names), len(expected))
                self.assertIn("REHEARSAL: PASS", proc.stdout)
        # Both modes executed the linter once; neither the workflow nor a
        # second discovery path invoked it again.
        self.assertEqual(len(self.world.tools.calls("ruff")), 2)
        self.assertEqual(len(self.world.tools.calls("probes")), 2)

    def test_every_required_guard_can_make_the_full_gate_red(self):
        for check in lane.GUARDS:
            with self.subTest(origin=check.name):
                name = "ruff" if check.name == "ruff" else Path(check.argv[1]).stem
                argv = list(check.argv[1:] if name == "ruff" else check.argv[2:])
                self.world.tools.plant(name, responses=[{"match": argv, "exit": 1, "stderr": "planted guard fault\n"}])
                proc = self.invoke()
                self.assertEqual(proc.exit, 1, proc.stdout + proc.stderr)
                self.assertIn("FAIL " + check.name + " (", proc.stdout)
                self.assertIn("REHEARSAL: FAIL", proc.stdout)
                self.world.tools.plant(name)

    def test_missing_one_or_all_families_refuses_before_lint_or_tests(self):
        shutil.rmtree(self.root / "ops/devlane/hooks/tests")
        proc = self.invoke()
        self.assertEqual(proc.refusal().cls, "checks")
        self.assertIn("hooks/tests", proc.stderr)
        for directory in lane.FAMILIES:
            shutil.rmtree(self.root / directory, ignore_errors=True)
        proc = self.invoke()
        self.assertEqual(proc.refusal().cls, "checks")
        self.assertEqual(self.world.tools.calls("ruff"), [])

    def test_a_failed_cheap_guard_prevents_expensive_suites_from_starting(self):
        self.world.tools.plant("ruff", exit=1, stderr="planted lint failure\n")
        self.world.plant_text("cwd/ops/devlane/dispatch/tests/test_example.py",
                              "raise AssertionError('expensive suite started')\n")
        proc = self.invoke()
        self.assertEqual(proc.exit, 1, proc.stdout + proc.stderr)
        self.assertIn("FAIL ruff", proc.stdout)
        self.assertNotIn("expensive suite started", proc.stdout + proc.stderr)
        self.assertNotIn("== ops/devlane/dispatch/tests", proc.stdout)
        self.assertIn("REHEARSAL: FAIL", proc.stdout)

    def test_partial_selection_is_explicit_and_cannot_claim_full_success(self):
        selected = "ops/devlane/task/tests"
        for directory in lane.FAMILIES:
            if directory != selected:
                shutil.rmtree(self.root / directory)
        partial = self.invoke("--suite", selected)
        self.assertEqual(partial.exit, 0, partial.stdout + partial.stderr)
        self.assertIn("PARTIAL: PASS", partial.stdout)
        self.assertNotIn("FULL", partial.stdout)
        self.assertNotIn("== probes", partial.stdout)
        self.assertEqual(self.invoke().refusal().cls, "checks")
        self.assertEqual(self.invoke("--suite", "missing").refusal().cls, "checks")
