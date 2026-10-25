"""An assessment refusal must precede the actual completion mutations."""

import unittest
from unittest.mock import patch

from fixtures import structure as test_structure
from fixtures.structure import StructureFixture
from support import adopt_plan


class CompletionBoundaryTests(unittest.TestCase):
    setUp = StructureFixture.setUp
    member = StructureFixture.member
    assign = StructureFixture.assign
    complete = StructureFixture.complete

    def test_planner_adoption_cannot_bypass_assignment_proof(self):
        with patch("devtools.completion.Completion.require", side_effect=ValueError("Unmet proof")) as gate:
            with self.assertRaisesRegex(ValueError, "Unmet proof"):
                adopt_plan(self.xo, [test_structure.job("Feature")])
        self.assertEqual("assignment", gate.call_args.args[0])
        with self.store.connect() as db:
            self.assertEqual(0, db.execute("SELECT count(*) FROM jobs").fetchone()[0])
            self.assertEqual("active", db.execute("SELECT status FROM role_assignments").fetchone()[0])

    def test_assignment_and_job_refusals_preserve_their_work(self):
        adopt_plan(self.xo, [test_structure.job("Feature")])
        coder = self.assign(self.xo, "coder", "Feature")
        coder.call("work_report", {"kind": "result", "text": "Observed result",
                                  "evidence": {"name": "proof.md", "content": "Measured output"}})
        with patch("devtools.completion.Completion.require", side_effect=ValueError("Unmet proof")) as gate:
            with self.assertRaisesRegex(ValueError, "Unmet proof"):
                self.xo.call("steer", {"action": "complete", "work": "Feature", "assignment": "coder",
                                      "direction": "Inspected"})
        self.assertEqual("assignment", gate.call_args.args[0])
        self.assertIsNotNone(coder.control.snapshot())
        self.xo.call("steer", {"action": "complete", "work": "Feature", "assignment": "coder",
                              "direction": "Inspected"})
        with patch("devtools.completion.Completion.require", side_effect=ValueError("Unmet owner goal")) as gate:
            with self.assertRaisesRegex(ValueError, "Unmet owner goal"):
                self.xo.call("steer", {"action": "accept", "work": "Feature", "direction": "Ready"})
        self.assertEqual("job", gate.call_args.args[0])
        with self.store.connect() as db:
            self.assertNotEqual("done", db.execute("SELECT status FROM jobs").fetchone()[0])
            self.assertIsNone(db.execute("SELECT * FROM deliveries").fetchone())

    def test_cancelling_jobs_does_not_bypass_the_owner_goal_gate(self):
        adopt_plan(self.xo, [test_structure.job("Feature")])
        self.xo.call("steer", {"action": "cancel", "work": "Feature", "direction": "Contribution withdrawn"})
        with patch("devtools.completion.Completion.require", side_effect=ValueError("Owner goal unproven")) as gate:
            with self.assertRaisesRegex(ValueError, "Owner goal unproven"):
                self.xo.call("conclude", {"assessment": "All jobs settled"})
        self.assertEqual("work_order", gate.call_args.args[0])
        self.assertIsNotNone(self.xo.control.snapshot())
        with self.store.connect() as db:
            self.assertNotEqual("done", db.execute("SELECT status FROM work_orders").fetchone()[0])
            self.assertIsNone(db.execute("SELECT * FROM order_deliveries").fetchone())


if __name__ == "__main__":
    unittest.main()
