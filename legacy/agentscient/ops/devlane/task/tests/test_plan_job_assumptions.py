"""The plan job's prompt quotes the measured-or-refusal rule.

Written from the contract (in/contract-plan-prompt.md) at
0cea17ab4023f80c92c99591e4aaf825f9bac98a, before the catalogue was
edited. Every assumption is measured by a command at write time or
made a refusal the run will hit; one that is neither is a finding
against the plan, and the plan says so of itself.
"""

from __future__ import annotations

import json
import unittest

import support

from ops.testkit import World

JOBS_PATH = support.APP / "jobs.json"

MEASURED_OR_REFUSAL = (
    "Every assumption measured by a command at write time or made a "
    "refusal the run will hit"
)
NEITHER_IS_A_FINDING = (
    "one that is neither is a finding against the plan"
)
PLAN_SAYS_SO = "the plan says so of itself"


def _squish(text):
    return " ".join(text.split())


class _PlanJob(unittest.TestCase):
    def setUp(self):
        self.world = World()
        self.addCleanup(self.world.close)
        self.jobs_path = self.world.stage(JOBS_PATH)

    def plan_prompt(self):
        self.assertTrue(self.jobs_path.is_file(), f"jobs.json missing: {self.jobs_path}")
        jobs = json.loads(self.jobs_path.read_text(encoding="utf-8"))
        self.assertIsInstance(jobs, dict)
        self.assertIn("plan", jobs)
        spec = jobs["plan"]
        self.assertIsInstance(spec, dict)
        prompt = spec.get("prompt")
        self.assertIsInstance(prompt, str)
        self.assertTrue(prompt.strip(), "plan prompt is empty")
        return prompt

    def words(self):
        return _squish(self.plan_prompt())


class PlanPromptQuotesTheAssumptionRule(_PlanJob):
    """Measured, or a refusal; neither is a finding the plan names."""

    def test_every_assumption_rule_sentence_is_present_together(self):
        required = (MEASURED_OR_REFUSAL, NEITHER_IS_A_FINDING, PLAN_SAYS_SO)
        self.assertGreater(len(required), 0)
        words = self.words()
        missing = [sentence for sentence in required if sentence not in words]
        self.assertEqual(
            missing, [],
            "plan prompt must quote every assumption-rule sentence; "
            f"missing {missing!r}",
        )


if __name__ == "__main__":
    unittest.main()
