"""The plan job's prompt quotes a calibration row per slice.

Written from the contract (in/contract-plan-prompt.md) at
0cea17ab4023f80c92c99591e4aaf825f9bac98a, before the catalogue was
edited. Each slice carries a calibration row: the declared bucket and
the estimated minutes per stage, in the shape of
.dev/wip/calibration-buckets.md.
"""

from __future__ import annotations

import json
import unittest

import support

from ops.testkit import World

JOBS_PATH = support.APP / "jobs.json"

CALIBRATION_ROW = "a calibration row per slice"
DECLARED_BUCKET = "the declared bucket"
MINUTES_PER_STAGE = "the estimated minutes per stage"
SHAPE_PATH = ".dev/wip/calibration-buckets.md"


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


class PlanPromptQuotesACalibrationRowPerSlice(_PlanJob):
    """Declared bucket and estimated minutes per stage, in the named shape."""

    def test_every_calibration_sentence_is_present_together(self):
        required = (
            CALIBRATION_ROW,
            DECLARED_BUCKET,
            MINUTES_PER_STAGE,
            SHAPE_PATH,
        )
        self.assertGreater(len(required), 0)
        prompt = self.plan_prompt()
        words = _squish(prompt)
        missing = [sentence for sentence in required if sentence not in prompt
                   and sentence not in words]
        self.assertEqual(
            missing, [],
            "plan prompt must quote a calibration row per slice; "
            f"missing {missing!r}",
        )


if __name__ == "__main__":
    unittest.main()
