"""Shared verification invariants; CI audit at dev 09e6e4c, 2026-09-23.

Reused: testkit.world, testkit.run, testkit.fakes, testkit.refusal-wire.
"""

from __future__ import annotations

import dataclasses
import unittest
from pathlib import Path

from ops.testkit import Check, World, execute_checks, suite_checks

KIT = Path(__file__).resolve().parents[1]
EXAMPLE = KIT / "tests/fixtures/one_case.py"


class CheckExecution(unittest.TestCase):
    def test_serial_and_parallel_execute_each_command_once_with_the_same_context(self):
        for workers in (1, 3):
            with self.subTest(workers=workers), World(host_path=False) as world:
                plan = []
                for name in ("first", "second", "third"):
                    world.tools.plant(name, stdout=name + "\n", stderr="diagnostic\n")
                    plan.append(Check(name, (name, "one argument; untouched"), 5))
                results = dict(execute_checks(plan, cwd=world.cwd, env=world.env(), workers=workers))
                self.assertEqual(set(results), {check.name for check in plan})
                for check in plan:
                    result = results[check.name]
                    self.assertEqual((result.exit, result.stdout, result.stderr),
                                     (0, check.name + "\n", "diagnostic\n"))
                    self.assertEqual(result.cwd, str(world.cwd))
                    self.assertEqual(result.argv, check.argv)
                    self.assertGreaterEqual(result.duration, 0)
                    calls = world.tools.calls(check.name)
                    self.assertEqual(len(calls), 1)
                    self.assertEqual(calls[0]["argv"], ["one argument; untouched"])
                    self.assertEqual(calls[0]["cwd"], str(world.cwd))

    def test_failures_missing_tools_and_timeouts_cannot_be_green(self):
        with World(host_path=False) as world:
            world.tools.plant("red", exit=1, stderr="planted failure\n")
            world.tools.plant("slow", delay=5)
            plan = (Check("failure", ("red",), 5),
                    Check("missing", ("absent-command",), 5),
                    Check("timeout", ("slow",), 0.2))
            results = dict(execute_checks(plan, cwd=world.cwd, env=world.env(), workers=3))
            self.assertEqual(set(results), {c.name for c in plan})
            for name, result in results.items():
                with self.subTest(name=name):
                    self.assertNotEqual(result.exit, 0)
                    self.assertTrue(result.stderr)
            self.assertIn("planted failure", results["failure"].stderr)
            self.assertIn("killed and reaped", results["timeout"].stderr)

    def test_invalid_plans_refuse_before_any_command_starts(self):
        with World(host_path=False) as world:
            world.tools.plant("subject")
            good = Check("subject", ("subject",), 5)
            faults = {
                "empty": (),
                "duplicate": (good, good),
                "missing-input": (good, Check("other", ("subject",), 5, ("missing",))),
                "empty-command": (dataclasses.replace(good, argv=()),),
                "zero-timeout": (dataclasses.replace(good, timeout=0),),
                "infinite-timeout": (dataclasses.replace(good, timeout=float("inf")),),
            }
            for origin, plan in faults.items():
                with self.subTest(origin=origin), self.assertRaisesRegex(ValueError, "checks: expected .*; found .*; needed "):
                    list(execute_checks(plan, cwd=world.cwd, env=world.env()))
            self.assertEqual(world.tools.calls("subject"), [])


class SuiteDiscovery(unittest.TestCase):
    def setUp(self):
        self.world = World(host_path=False)
        self.addCleanup(self.world.close)
        for path in KIT.glob("*.py"):
            self.world.plant_bytes(f"cwd/ops/testkit/{path.name}", path.read_bytes())

    def plant(self, name, content=None):
        self.world.plant_bytes("cwd/tests/" + name,
                               EXAMPLE.read_bytes() if content is None else content)

    def test_partition_runs_each_file_once_and_includes_new_names(self):
        patterns = ("test_[cl]*.py", "test_[!cl]*.py")
        for name in ("test_c.py", "test_l.py", "test_zzz_new.py"):
            self.plant(name)
        plan = suite_checks(self.world.cwd, "tests", patterns)
        results = dict(execute_checks(plan, cwd=self.world.cwd, env=self.world.env()))
        self.assertEqual(len(results), len(patterns))
        self.assertTrue(all(r.exit == 0 for r in results.values()), results)
        self.assertEqual(sum("Ran 2 tests" in r.stderr for r in results.values()), 1)
        self.assertEqual(sum("Ran 1 test" in r.stderr for r in results.values()), 1)

    def test_automatic_partition_runs_each_module_and_keeps_new_files(self):
        for name in ("test_first.py", "test_second.py", "test_new.py"):
            self.plant(name)
        plan = suite_checks(self.world.cwd, "tests", patterns=None)
        self.assertEqual({check.argv[-1] for check in plan},
                         {"test_first.py", "test_second.py", "test_new.py"})
        results = dict(execute_checks(plan, cwd=self.world.cwd, env=self.world.env(), workers=2))
        self.assertEqual(len(results), 3)
        self.assertTrue(all(r.exit == 0 and "Ran 1 test" in r.stderr for r in results.values()))

    def test_case_partitions_execute_every_actual_case_once_and_keep_failures(self):
        content = (KIT / "tests/fixtures/multiple_cases.py").read_bytes()
        self.plant("test_large.py", content)
        plan = suite_checks(self.world.cwd, "tests", patterns=None, partitions={"test_large.py": 4})
        results = dict(execute_checks(plan, cwd=self.world.cwd, env=self.world.env()))
        self.assertEqual(len(results), 4)
        self.assertTrue(all(r.exit == 0 for r in results.values()), results)
        cases = [line for result in results.values() for line in result.stdout.splitlines()]
        self.assertEqual(sorted(cases), ["test_large.First.test_a", "test_large.First.test_b",
                                         "test_large.Second.test_c", "test_large.Second.test_d"])
        self.plant("test_large.py", content.replace(b"2 + 2, 4", b"2 + 2, 5"))
        results = dict(execute_checks(plan, cwd=self.world.cwd, env=self.world.env()))
        self.assertEqual(sum(r.exit != 0 for r in results.values()), 1)
        self.plant("test_large.py", b"import nonexistent_test_dependency\n")
        results = dict(execute_checks(plan, cwd=self.world.cwd, env=self.world.env()))
        self.assertTrue(all(r.exit != 0 for r in results.values()))

    def test_empty_or_invalid_case_partitions_cannot_pass(self):
        self.plant("test_small.py")
        for count in (0, -1, True):
            with self.subTest(count=count), self.assertRaises(ValueError):
                suite_checks(self.world.cwd, "tests", patterns=None, partitions={"test_small.py": count})
        plan = suite_checks(self.world.cwd, "tests", patterns=None, partitions={"test_small.py": 2})
        results = dict(execute_checks(plan, cwd=self.world.cwd, env=self.world.env()))
        self.assertTrue(any(r.exit != 0 and "zero" in r.stderr for r in results.values()))

    def test_explicit_exclusion_is_reported_and_unknown_cases_refuse(self):
        self.plant("test_scope.py", (KIT / "tests/fixtures/multiple_cases.py").read_bytes())
        check = suite_checks(self.world.cwd, "tests")[0]
        for excluded, status in (("test_scope.First.test_a", 0), ("test_scope.First.absent", 2)):
            scoped = dataclasses.replace(check, argv=(*check.argv[:-2], "--exclude-case", excluded, *check.argv[-2:]))
            result = dict(execute_checks([scoped], cwd=self.world.cwd, env=self.world.env()))[check.name]
            self.assertEqual(result.exit, status, result.stderr)
            if status == 0:
                self.assertIn("EXCLUDED test_scope.First.test_a", result.stdout)
                self.assertIn("Ran 3 tests", result.stderr)
            else:
                self.assertIn("absent", result.stderr)

    def test_missing_empty_gapped_and_overlapping_families_refuse(self):
        with self.assertRaisesRegex(ValueError, "ABSENT"):
            suite_checks(self.world.cwd, "tests")
        (self.world.cwd / "tests").mkdir()
        with self.assertRaisesRegex(ValueError, "no test files"):
            suite_checks(self.world.cwd, "tests")
        self.plant("test_c.py")
        self.plant("test_new.py")
        for origin, patterns in (("gap", ("test_c*.py",)),
                                 ("overlap", ("test_*.py", "test_c*.py"))):
            with self.subTest(origin=origin), self.assertRaisesRegex(ValueError, "exactly once"):
                suite_checks(self.world.cwd, "tests", patterns)

    def test_zero_collected_cases_import_errors_and_failed_assertions_are_red(self):
        faults = {
            "zero-cases": b"# A file is not proof that a test was collected.\n",
            "import-error": b"import nonexistent_test_dependency\n",
            "assertion": EXAMPLE.read_bytes().replace(b"2 + 2, 4", b"2 + 2, 5"),
        }
        for origin, content in faults.items():
            with self.subTest(origin=origin):
                self.plant("test_subject.py", content)
                plan = suite_checks(self.world.cwd, "tests")
                result = dict(execute_checks(plan, cwd=self.world.cwd, env=self.world.env()))[plan[0].name]
                self.assertNotEqual(result.exit, 0, result)
                if origin == "zero-cases":
                    self.assertEqual(result.refusal().cls, "suite")
