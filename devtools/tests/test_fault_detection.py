"""Calibrate three existing boundary tests against deliberate, reversible faults.

Each control must pass first. A fault is caught only by the named assertion in
that same test; setup errors, unrelated failures and skips are not detections.
Patches are confined to this process and every case owns a fresh fixture.
"""

import unittest
from unittest.mock import patch


class FaultDetectionTests(unittest.TestCase):
    def measure(self, factory):
        result = unittest.TestResult()
        factory().run(result)
        self.assertEqual([], result.errors, "A fixture/runtime error is not detection of the intended fault")
        self.assertEqual([], result.skipped, "Calibration must execute its assertion")
        self.assertEqual(1, result.testsRun)
        return result

    def caught(self, factory, mutation, assertion, failure):
        baseline = self.measure(factory)
        self.assertTrue(baseline.wasSuccessful(), baseline.failures)
        with mutation:
            result = self.measure(factory)
        self.assertEqual(1, len(result.failures), "The deliberate fault must fail its boundary assertion")
        case, diagnostic = result.failures[0]
        self.assertEqual(factory().id(), case.id())
        self.assertIn(assertion, diagnostic)
        self.assertIn(failure, diagnostic)

    def test_stale_completion_receipt_cannot_pass_if_commit_recheck_is_removed(self):
        import test_completion

        def factory():
            return test_completion.CompletionTests("test_acceptance_receipt_rechecks_owner_goal_and_operator_policy")

        self.caught(factory, patch("devtools.completion.Completion.verify", return_value=None),
                    'with self.assertRaisesRegex(ValueError, "changed after assessment")', "ValueError not raised")

    def test_retained_runtime_test_detects_first_profile_fallback(self):
        import test_runtime_selection

        from devtools import runtime

        def fallback(store, scope, **kwargs):
            return runtime.resolve(store.directory, scope["role"])

        def factory():
            return test_runtime_selection.RuntimeSelectionTests("test_nonfirst_profile_reaches_configuration_and_survives_ordinary_restart")

        self.caught(factory, patch("devtools.runtime.selected", side_effect=fallback),
                    'self.assertEqual("claude", config["harness"])', "'claude' != 'codex'")

    def test_arbitrator_evidence_test_detects_unrestricted_operation_reads(self):
        import test_decision_references

        from devtools.control import Control

        original = Control._reference

        def unrestricted(control, db, scope, reference):
            kind, ident = reference.split(":", 1)
            if kind == "operation":
                return kind, dict(db.execute("SELECT * FROM operations WHERE id=?", (int(ident),)).fetchone())
            return original(control, db, scope, reference)

        def factory():
            return test_decision_references.DecisionReferenceTests("test_order_arbitrator_can_follow_its_exact_operation_evidence")

        self.caught(factory, patch.object(Control, "_reference", unrestricted),
                    'with self.assertRaisesRegex(ValueError, "not available")', "ValueError not raised")

    def test_setup_error_cannot_be_counted_as_a_detected_fault(self):
        class Broken(unittest.TestCase):
            def runTest(self):
                raise OSError("fixture executable missing")

        with self.assertRaisesRegex(AssertionError, "fixture/runtime error is not detection"):
            self.measure(Broken)
