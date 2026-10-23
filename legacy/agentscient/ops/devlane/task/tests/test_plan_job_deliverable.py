"""The plan job's deliverable line names every required section.

Written from the contract (in/contract-plan-prompt.md) at
0cea17ab4023f80c92c99591e4aaf825f9bac98a, before the catalogue was
edited. The deliverable names the bucket rule, the first-run
walkthrough, the assumption rule, the tests by file with why red, the
machine lines, and the calibration row, so a plan missing any section
is not the deliverable.
"""

from __future__ import annotations

import json
import unittest

import support

from ops.testkit import World

JOBS_PATH = support.APP / "jobs.json"

MISSING_IS_NOT = "a plan missing any section is not the deliverable"
BUCKETS = "bucket rule"
WALKTHROUGH = "walkthrough of the first run"
ASSUMPTION = "every assumption"
TESTS_BY_FILE = "tests by file"
MACHINE_LINES = "machine lines"
CALIBRATION = "calibration row"


def _squish(text):
    return " ".join(text.split())


class _PlanJob(unittest.TestCase):
    def setUp(self):
        self.world = World()
        self.addCleanup(self.world.close)
        self.jobs_path = self.world.stage(JOBS_PATH)

    def plan_spec(self):
        self.assertTrue(self.jobs_path.is_file(), f"jobs.json missing: {self.jobs_path}")
        jobs = json.loads(self.jobs_path.read_text(encoding="utf-8"))
        self.assertIsInstance(jobs, dict)
        self.assertIn("plan", jobs)
        spec = jobs["plan"]
        self.assertIsInstance(spec, dict)
        return spec

    def plan_deliverable(self):
        spec = self.plan_spec()
        deliverable = spec.get("deliverable")
        self.assertIsInstance(deliverable, str)
        self.assertTrue(deliverable.strip(), "plan deliverable is empty")
        return deliverable

    def words(self):
        return _squish(self.plan_deliverable())


class PlanDeliverableNamesEveryRequiredSection(_PlanJob):
    """A plan that omits a named section is not the deliverable."""

    def test_every_deliverable_section_name_is_present_together(self):
        required = (
            MISSING_IS_NOT,
            BUCKETS,
            WALKTHROUGH,
            ASSUMPTION,
            TESTS_BY_FILE,
            MACHINE_LINES,
            CALIBRATION,
        )
        self.assertGreater(len(required), 0)
        words = self.words()
        missing = [sentence for sentence in required if sentence not in words]
        self.assertEqual(
            missing, [],
            "plan deliverable must name every required section; "
            f"missing {missing!r}",
        )


if __name__ == "__main__":
    unittest.main()
