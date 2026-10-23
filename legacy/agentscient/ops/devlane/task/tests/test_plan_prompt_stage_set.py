"""The plan prompt names the class on the slice line and the table it comes from
(scope-stage-set-20260912.md item 2). Reads jobs.json only."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from ops.testkit import World

REPO = Path(__file__).resolve().parents[4]
JOBS = REPO / "ops" / "devlane" / "task" / "jobs.json"


class PlanPromptGrammar(unittest.TestCase):
    def setUp(self):
        self.world = World()
        self.addCleanup(self.world.close)
        jobs = self.world.stage(JOBS)
        self.prompt = json.loads(jobs.read_text(encoding="utf-8"))["plan"]["prompt"]

    def test_the_slice_line_carries_a_class(self):
        self.assertIn("`slice<TAB>name<TAB>S|M<TAB>class`", self.prompt)

    def test_the_class_comes_from_the_table(self):
        self.assertIn("ops/conductor/stage-sets.json", self.prompt)


if __name__ == "__main__":
    unittest.main()
