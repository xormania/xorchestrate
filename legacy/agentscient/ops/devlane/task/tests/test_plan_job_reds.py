"""The plan job's prompt quotes tests by file and why each is red.

Written from the contract (in/contract-plan-prompt.md) at
0cea17ab4023f80c92c99591e4aaf825f9bac98a, before the catalogue was
edited. The plan names the tests by file and why each is red at the
ref.
"""

from __future__ import annotations

import json
import unittest

import support

from ops.testkit import World

JOBS_PATH = support.APP / "jobs.json"

TESTS_BY_FILE = "the tests by file"
WHY_RED = "why each is red at the ref"
FULL = "the tests by file and why each is red at the ref"


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


class PlanPromptQuotesTestsByFileAndWhyRed(_PlanJob):
    """The plan lists tests by file and the reason each is red at the ref."""

    def test_every_reds_sentence_is_present_together(self):
        required = (TESTS_BY_FILE, WHY_RED, FULL)
        self.assertGreater(len(required), 0)
        words = self.words()
        missing = [sentence for sentence in required if sentence not in words]
        self.assertEqual(
            missing, [],
            "plan prompt must quote tests by file and why each is red; "
            f"missing {missing!r}",
        )


if __name__ == "__main__":
    unittest.main()
