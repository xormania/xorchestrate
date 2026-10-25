"""The plan job's prompt keeps its placeholders and the envelope tail.

Written from the contract (in/contract-plan-prompt.md) at
0cea17ab4023f80c92c99591e4aaf825f9bac98a. Editing the plan prompt
must leave {ref}, {scope}, {out} as template tokens and keep the
shared envelope tail. The planning-rule sentences this file quotes
are absent at the ref, so the keep-constraint is proved red together
with those sentences rather than as a green-only pin.
"""

from __future__ import annotations

import json
import unittest

import support

from ops.testkit import World

JOBS_PATH = support.APP / "jobs.json"

ENVELOPE_CLAUSE = (
    "Answer with a single JSON object and nothing else, carrying exactly "
    "these keys: job, status, verdict, counts, findings, artifacts, spend, "
    "stamp, note"
)

# A sentence the new prompt must carry. Quoted here so the keep-constraint
# tests fail at this ref for the right reason: the planning rules are not
# yet in the prompt, and the placeholders must survive once they are.
PLANNING_MARKER = (
    "S is one to three files and one worker run under ten minutes"
)


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


class PlanPromptKeepsPlaceholdersAndTheEnvelopeTail(_PlanJob):
    """{ref}, {scope}, {out}, {out}/PLAN.md, and the shared envelope tail."""

    def test_placeholders_and_envelope_are_present_together(self):
        prompt = self.plan_prompt()
        required = (
            PLANNING_MARKER,
            "{ref}",
            "{scope}",
            "{out}",
            "{out}/PLAN.md",
            ENVELOPE_CLAUSE,
        )
        self.assertGreater(len(required), 0)
        words = _squish(prompt)
        missing = []
        for token in required:
            haystack = words if token == PLANNING_MARKER else prompt
            if token not in haystack:
                missing.append(token)
        self.assertEqual(
            missing, [],
            "plan prompt must keep placeholders and the envelope tail "
            "while carrying the planning rules; "
            f"missing {missing!r}",
        )


if __name__ == "__main__":
    unittest.main()
