import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from devtools.tests import run


class SuiteRunnerTests(unittest.TestCase):
    def cli(self, source, *arguments):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "test_required.py").write_text("import unittest\n" + source)
            report = root / "report.json"
            report.write_text('{"state":"passed","collected":999,"executed":999}')
            completed = subprocess.run(
                [sys.executable, "-m", "devtools.tests.run", "--strict", "-s", temporary, "--report", str(report), *arguments],
                cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=10, check=False,
            )
            return completed, json.loads(report.read_text())

    def test_strict_cli_requires_complete_execution_and_retains_failed_census(self):
        examples = {
            "passing": ("class Example(unittest.TestCase):\n def test_one(self): self.assertEqual(1, 1)\n", 0, 1, 1),
            "method_skip": ("class Example(unittest.TestCase):\n @unittest.skip('omitted')\n def test_one(self): pass\n", 1, 1, 0),
            "class_skip": ("@unittest.skip('omitted')\nclass Example(unittest.TestCase):\n def test_one(self): pass\n", 1, 1, 0),
            "module_skip": ("def setUpModule(): raise unittest.SkipTest('omitted')\nclass Example(unittest.TestCase):\n def test_one(self): pass\n", 1, 1, 0),
            "subtest_skip": ("class Example(unittest.TestCase):\n def test_one(self):\n  with self.subTest(input=1): self.skipTest('omitted')\n", 1, 1, 1),
            "expected_failure": ("class Example(unittest.TestCase):\n @unittest.expectedFailure\n def test_one(self): self.fail('known defect')\n", 1, 1, 1),
            "stopped": ("class Example(unittest.TestCase):\n def test_a_stop(self): self._outcome.result.stop()\n def test_b_unreached(self): self.fail('must execute')\n", 1, 2, 1),
            "empty": ("", 2, None, None),
        }
        for label, (source, status, collected, executed) in examples.items():
            with self.subTest(case=label):
                completed, census = self.cli(source)
                self.assertEqual(status, completed.returncode, completed.stderr)
                self.assertEqual("passed" if status == 0 else "collection_failed" if status == 2 else "failed", census["state"])
                if collected is not None:
                    self.assertEqual((collected, executed), (census["collected"], census["executed"]))

    def test_strict_skip_allowance_is_exact_and_cannot_hide_unexecuted_tests(self):
        allowed = "test_required.Example.test_b"
        examples = {
            "allowed_skip": (" def test_a(self): pass\n @unittest.skip('required elsewhere')\n def test_b(self): pass\n", allowed, 0, 1, 1, 0),
            "allowed_but_executed": (" def test_a(self): pass\n def test_b(self): pass\n", allowed, 0, 2, 0, 0),
            "allowed_but_failed": (" def test_a(self): pass\n def test_b(self): self.fail('real defect')\n", allowed, 1, 2, 0, 0),
            "allowed_expected_failure": (" def test_a(self): pass\n @unittest.expectedFailure\n def test_b(self): self.fail('known defect')\n", allowed, 1, 2, 0, 0),
            "allowed_subtest_skip": (" def test_a(self): pass\n def test_b(self):\n  with self.subTest(input=1): self.skipTest('omitted')\n", allowed, 1, 2, 0, 0),
            "wrong_case": (" @unittest.skip('unapproved')\n def test_a(self): pass\n def test_b(self): pass\n", allowed, 1, 1, 1, 0),
            "allowed_but_unreached": (" def test_a(self): self._outcome.result.stop()\n def test_b(self): pass\n", allowed, 1, 1, 0, 1),
            "allowed_only": (" @unittest.skip('required elsewhere')\n def test_b(self): pass\n", allowed, 1, 0, 1, 0),
            "stale_allowance": (" def test_a(self): pass\n", allowed, 2, None, None, None),
            "wildcard_allowance": (" def test_b(self): pass\n", "test_required.*", 2, None, None, None),
        }
        for label, (source, ident, status, executed, skipped, not_run) in examples.items():
            with self.subTest(case=label):
                completed, census = self.cli("class Example(unittest.TestCase):\n" + source, "--allow-skip", ident)
                self.assertEqual(status, completed.returncode, completed.stderr)
                self.assertEqual("passed" if status == 0 else "collection_failed" if status == 2 else "failed", census["state"])
                if executed is not None:
                    self.assertEqual((executed, skipped, not_run), tuple(census[key] for key in ("executed", "skipped", "not_run")))

    def test_empty_or_duplicate_collection_fails_before_running(self):
        calls = []

        class Example(unittest.TestCase):
            def test_one(self):
                calls.append("ran")

        for suite in (unittest.TestSuite(), unittest.TestSuite([Example("test_one"), Example("test_one")])):
            with self.subTest(suite=suite), self.assertRaisesRegex(ValueError, "zero|Duplicate"):
                run.run(suite, stream=io.StringIO())
        self.assertEqual([], calls)

    def test_census_counts_class_and_method_skips_without_double_counting_subtests(self):
        class Skipped(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                raise unittest.SkipTest("optional dependency")

            def test_one(self):
                pass

            def test_two(self):
                pass

        class Mixed(unittest.TestCase):
            @unittest.skip("not applicable")
            def test_skip(self):
                pass

            def test_subtests(self):
                for number in range(3):
                    with self.subTest(number=number):
                        if number == 0:
                            self.skipTest("one input omitted")
                        self.assertEqual(number, number)

        loader = unittest.TestLoader()
        result = run.run(unittest.TestSuite([loader.loadTestsFromTestCase(Skipped), loader.loadTestsFromTestCase(Mixed)]),
                         stream=io.StringIO())
        census = result.census()
        self.assertTrue(result.wasSuccessful())
        self.assertEqual((4, 1, 3, 0, 1), tuple(census[key] for key in
                         ("collected", "executed", "skipped", "not_run", "skipped_subtests")))
        self.assertEqual(3, len(census["skipped_cases"]))

    def test_setup_errors_and_unexpected_success_remain_failures(self):
        class Broken(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                raise RuntimeError("broken setup")

            def test_one(self):
                pass

        class Unexpected(unittest.TestCase):
            @unittest.expectedFailure
            def test_pass(self):
                pass

            @unittest.expectedFailure
            def test_expected_failure(self):
                self.fail("known defect")

        loader = unittest.TestLoader()
        result = run.run(unittest.TestSuite([loader.loadTestsFromTestCase(Broken), loader.loadTestsFromTestCase(Unexpected)]),
                         stream=io.StringIO())
        self.assertFalse(result.wasSuccessful())
        self.assertEqual((3, 2, 0, 1, 1, 1), tuple(result.census()[key] for key in
                         ("collected", "executed", "skipped", "not_run", "expected_failures", "unexpected_successes")))

    def test_module_skip_is_counted_but_import_failure_is_not_a_skip(self):
        self.addCleanup(sys.modules.pop, "test_runner_module_skip", None)
        with tempfile.TemporaryDirectory() as temporary, patch("sys.stderr", new_callable=io.StringIO):
            root = Path(temporary)
            (root / "test_runner_module_skip.py").write_text(
                "import unittest\ndef setUpModule(): raise unittest.SkipTest('module unavailable')\n"
                "class Example(unittest.TestCase):\n"
                "    def test_one(self): pass\n    def test_two(self): pass\n")
            report = root / "report.json"
            self.assertEqual(0, run.main(["-s", temporary, "--report", str(report)]))
            census = json.loads(report.read_text())
            self.assertEqual((2, 0, 2), tuple(census[key] for key in ("collected", "executed", "skipped")))
            (root / "test_runner_import_failure.py").write_text("raise RuntimeError('broken import')\n")
            self.assertEqual(2, run.main(["-s", temporary, "--report", str(report)]))
            self.assertEqual("collection_failed", json.loads(report.read_text())["state"])

    def test_subtest_failures_and_teardown_errors_do_not_inflate_method_counts(self):
        class Subtests(unittest.TestCase):
            def test_inputs(self):
                for number in (1, 2):
                    with self.subTest(number=number):
                        self.fail("wrong result")

        class Teardown(unittest.TestCase):
            def test_one(self):
                pass

            def tearDown(self):
                raise RuntimeError("cleanup failed")

        loader = unittest.TestLoader()
        result = run.run(unittest.TestSuite([loader.loadTestsFromTestCase(Subtests), loader.loadTestsFromTestCase(Teardown)]),
                         stream=io.StringIO())
        self.assertFalse(result.wasSuccessful())
        self.assertEqual((2, 2, 0, 2, 1), tuple(result.census()[key] for key in
                         ("collected", "executed", "not_run", "failures", "errors")))
