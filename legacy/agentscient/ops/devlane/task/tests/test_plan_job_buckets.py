"""The plan job's prompt quotes the owner's bucket rule.

Written from the contract (in/contract-plan-prompt.md) at
0cea17ab4023f80c92c99591e4aaf825f9bac98a, before the catalogue was
edited. Each assertion quotes words the plan prompt must carry; a
prompt that omits any of them is not the plan job.
"""

from __future__ import annotations

import json
import unittest

import support

from ops.testkit import World

JOBS_PATH = support.APP / "jobs.json"

S_BUCKET = "S is one to three files and one worker run under ten minutes"
M_BUCKET = "M is four to eight files as two named S buckets"
L_BUCKET = "L is split"
UNBLOCKS = "what it unblocks"
RISK_CLASS = "risk class"
RISK_DISPATCHER = "touches the dispatcher"
RISK_ARGV = "the launcher's argv building"
RISK_CONTRACT = "a contract: its own slice"
EDGES = "the edges it touches"
GUARANTEES = "the guarantees it must declare or keep"
COUPLINGS = "docs/verified-couplings.md"
DEPENDS = "what it depends on"
LANDS_GREEN = "lands green alone"
OWN_PR = "which slice deserves its own PR"
VALUE_ORDER = "slices ordered by value per cost with dependencies respected"


def _squish(text):
    return " ".join(text.split())


class _WorldCase(unittest.TestCase):
    def setUp(self):
        self.world = World(prefix="plan-buckets-")
        self.addCleanup(self.world.close)
        self.world.enter()


class _PlanJob(_WorldCase):
    def plan_prompt(self):
        self.assertTrue(JOBS_PATH.is_file(), f"jobs.json missing: {JOBS_PATH}")
        path = self.world.stage(JOBS_PATH)
        jobs = json.loads(path.read_text(encoding="utf-8"))
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


class PlanPromptQuotesTheBucketRule(_PlanJob):
    """S, M as two named S buckets, L split; each slice carries the rest."""

    def test_every_bucket_rule_sentence_is_present_together(self):
        required = (
            S_BUCKET,
            M_BUCKET,
            L_BUCKET,
            UNBLOCKS,
            RISK_CLASS,
            RISK_DISPATCHER,
            RISK_ARGV,
            RISK_CONTRACT,
            EDGES,
            GUARANTEES,
            COUPLINGS,
            DEPENDS,
            LANDS_GREEN,
            OWN_PR,
            VALUE_ORDER,
        )
        self.assertGreater(len(required), 0)
        words = self.words()
        missing = [sentence for sentence in required if sentence not in words]
        self.assertEqual(
            missing, [],
            "plan prompt must quote every bucket-rule sentence; "
            f"missing {missing!r}",
        )


if __name__ == "__main__":
    unittest.main()
