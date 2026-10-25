"""The plan job's prompt quotes the driver's machine-line grammar.

Written from the contract (in/contract-plan-prompt.md) at
0cea17ab4023f80c92c99591e4aaf825f9bac98a, before the catalogue was
edited. The plan ends with tab-separated lines in the ladder driver's
grammar: slice<TAB>name<TAB>S|M and bucket<TAB>name<TAB>S<TAB>files.
"""

from __future__ import annotations

import json
import unittest

import support

from ops.testkit import World

JOBS_PATH = support.APP / "jobs.json"

AT_THE_END = "at the end of the plan"
SLICE_LINE = "slice<TAB>name<TAB>S|M"
BUCKET_LINE = "bucket<TAB>name<TAB>S<TAB>files"
TAB_SEPARATED = "tab-separated lines"
TWO_NAMED_S = "exactly two named S buckets"


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


class PlanPromptQuotesTheDriversGrammar(_PlanJob):
    """Machine lines in the driver's grammar sit at the end of the plan."""

    def test_every_machine_line_sentence_is_present_together(self):
        required = (
            AT_THE_END,
            SLICE_LINE,
            BUCKET_LINE,
            TAB_SEPARATED,
            TWO_NAMED_S,
        )
        self.assertGreater(len(required), 0)
        prompt = self.plan_prompt()
        words = _squish(prompt)
        missing = []
        for sentence in required:
            haystack = prompt if "<TAB>" in sentence else words
            if sentence not in haystack:
                missing.append(sentence)
        self.assertEqual(
            missing, [],
            "plan prompt must quote the driver's machine-line grammar; "
            f"missing {missing!r}",
        )


if __name__ == "__main__":
    unittest.main()
