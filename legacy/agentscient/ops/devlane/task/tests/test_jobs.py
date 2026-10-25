"""Harness job prompts ask for the envelope.

Written from ops/devlane/task/jobs.json: every harness job's prompt except
author-tests ends with the envelope clause. The author-tests brief must
ask for the same envelope the other harness jobs do.

The clause, verbatim:

    Answer with a single JSON object and nothing else, carrying exactly
    these keys: job, status, verdict, counts, findings, artifacts,
    spend, stamp, note
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

SPEND_TO_THE_LAUNCHER = (
    'Leave `spend` as `{"harness": "<harness>", "total": 0, "out": 0, '
    '"runs": 0}`; the launcher fills it from the session store. Do not '
    "read the store."
)


class _WorldCase(unittest.TestCase):
    def setUp(self):
        self.world = World(prefix="jobs-")
        self.addCleanup(self.world.close)
        self.world.enter()

    def jobs(self):
        self.assertTrue(JOBS_PATH.is_file(), f"jobs.json missing: {JOBS_PATH}")
        path = self.world.stage(JOBS_PATH)
        return json.loads(path.read_text(encoding="utf-8"))


class AuthorTestsBriefAsksForTheEnvelope(_WorldCase):
    """The author-tests brief asks for the envelope."""

    def test_the_author_tests_brief_asks_for_the_envelope(self):
        jobs = self.jobs()
        self.assertIsInstance(jobs, dict)
        self.assertIn("author-tests", jobs)
        spec = jobs["author-tests"]
        self.assertIsInstance(spec, dict)
        prompt = spec.get("prompt")
        self.assertIsInstance(prompt, str)
        self.assertTrue(prompt.strip(), "author-tests prompt is empty")
        self.assertIn(
            ENVELOPE_CLAUSE,
            prompt,
            "author-tests brief must ask for the envelope; "
            f"last 240 chars: {prompt[-240:]!r}",
        )


class SecurityAuditIsInTheCatalogue(_WorldCase):
    """security-audit is a read, whole-tree job with the envelope."""

    def test_security_audit_is_a_read_whole_job_with_the_envelope(self):
        jobs = self.jobs()
        self.assertIsInstance(jobs, dict)
        self.assertIn("security-audit", jobs)
        spec = jobs["security-audit"]
        self.assertIsInstance(spec, dict)
        self.assertEqual(spec.get("role"), "read")
        self.assertEqual(spec.get("snapshot"), "whole")
        prompt = spec.get("prompt")
        self.assertIsInstance(prompt, str)
        self.assertTrue(prompt.strip(), "security-audit prompt is empty")
        for token in ("{ref}", "{base}", "{diff}", "{scope}"):
            self.assertIn(token, prompt, f"security-audit prompt missing {token}")
        self.assertIn(
            ENVELOPE_CLAUSE,
            prompt,
            "security-audit brief must ask for the envelope; "
            f"last 240 chars: {prompt[-240:]!r}",
        )

    def test_security_audit_declares_caps_timeout_3600(self):
        jobs = self.jobs()
        self.assertIn("security-audit", jobs)
        self.assertIn("adversarial-review", jobs)
        audit = jobs["security-audit"]
        sibling = jobs["adversarial-review"]
        self.assertIsInstance(audit, dict)
        self.assertIsInstance(sibling, dict)
        audit_caps = audit.get("caps")
        sibling_caps = sibling.get("caps")
        self.assertIsInstance(audit_caps, dict, "security-audit must declare caps")
        self.assertIsInstance(sibling_caps, dict)
        # adversarial-review moved to 1800 on 2026-09-11 (Grok reviews ran
        # to 19 of 20 minutes); both moved to 3600 on 2026-09-22 with every
        # other job (dispatch CONTRACT.md, owner: "raise them to 60 mins").
        self.assertEqual(sibling_caps.get("timeout"), 3600)
        self.assertEqual(
            audit_caps.get("timeout"), 3600,
            "security-audit declares caps.timeout 3600, like adversarial-review",
        )


class EveryHarnessPromptLeavesSpendToTheLauncher(_WorldCase):
    """Harness jobs do not hunt usage; the launcher fills spend."""

    def test_every_harness_prompt_leaves_spend_to_the_launcher(self):
        jobs = self.jobs()
        self.assertIsInstance(jobs, dict)
        harness_jobs = [
            name for name, spec in jobs.items()
            if isinstance(spec, dict) and spec.get("adapter") == "harness"
        ]
        self.assertGreater(
            len(harness_jobs), 0,
            "jobs.json must name at least one harness job",
        )
        missing = []
        for name in harness_jobs:
            prompt = jobs[name].get("prompt")
            self.assertIsInstance(prompt, str, f"{name} prompt is not a string")
            self.assertTrue(prompt.strip(), f"{name} prompt is empty")
            if SPEND_TO_THE_LAUNCHER not in prompt:
                missing.append(name)
        self.assertEqual(
            missing, [],
            "every harness job prompt leaves spend to the launcher; "
            f"missing from {missing!r}",
        )


if __name__ == "__main__":
    unittest.main()
