"""PR02 runner selection and quota recovery; reuse: testkit.world/run/fakes."""

from __future__ import annotations

import copy
import json
import unittest

from ops.devlane.workflow.checks import ci_runners
from ops.testkit import World

REPO = "example/project"
SHA = "a" * 40


class RunnerSelection(unittest.TestCase):
    def test_default_local_return_and_invalid_setting(self):
        for value, expected in (("", "hosted"), ("hosted", "hosted"), ("local", "local"), ("hosted", "hosted")):
            with self.subTest(value=value):
                self.assertEqual(ci_runners.mode(value), expected)
        for value in ("HOSTED", "local ", "remote", "false"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "CI_RUNNER_MODE"):
                ci_runners.mode(value)

    def test_receipt_requires_the_actual_environment_and_binds_image_not_runner_name(self):
        env = {"CI_RUNNER_MODE": "hosted", "CI_RUNNER_ENVIRONMENT": "github-hosted",
               "ImageOS": "ubuntu24", "ImageVersion": "fixture-image", "RUNNER_NAME": "first"}
        first = ci_runners.runner_info(env)
        env["RUNNER_NAME"] = "second"
        self.assertEqual(first, ci_runners.runner_info(env))
        env["ImageVersion"] = "new-image"
        self.assertNotEqual(first, ci_runners.runner_info(env))
        env["CI_RUNNER_ENVIRONMENT"] = "self-hosted"
        with self.assertRaisesRegex(ValueError, "github-hosted"):
            ci_runners.runner_info(env)
        env["CI_RUNNER_MODE"] = "local"
        self.assertEqual(ci_runners.runner_info(env)["requested_mode"], "local")


class Recovery(unittest.TestCase):
    def setUp(self):
        self.world = World(host_path=False)
        self.addCleanup(self.world.close)
        self.run = {"id": 41, "path": ci_runners.WORKFLOW, "event": "pull_request",
                    "status": "completed", "conclusion": "startup_failure", "head_sha": SHA,
                    "repository": {"full_name": REPO}, "head_repository": {"full_name": REPO}}

    def gh(self, payload=None):
        self.world.tools.plant("gh", responses=[
            {"match": ["api", f"repos/{REPO}/actions/runs/41"], "stdout": json.dumps(payload or self.run)},
            {"match": ["variable", "set", "CI_RUNNER_MODE", "--repo", REPO, "--body", "local"]},
            {"match": ["variable", "get", "CI_RUNNER_MODE", "--repo", REPO], "stdout": "local\n"},
            {"match": ["run", "rerun", "41", "--repo", REPO]},
        ])

    def test_nonstarted_quota_fixture_recovers_same_run_and_candidate_without_a_commit(self):
        self.gh()
        result = ci_runners.recover(REPO, 41, "local", cwd=self.world.cwd, env=self.world.env())
        self.assertEqual(result["source_sha"], SHA)
        self.assertEqual(result["state"], "requested")
        self.assertEqual(result["run_id"], 41)
        calls = [c["argv"] for c in self.world.tools.calls("gh")]
        self.assertEqual(calls[-1], ["run", "rerun", "41", "--repo", REPO])
        self.assertFalse(any("commit" in c or "push" in c for c in calls))

    def test_wrong_source_or_active_run_refuses_before_changing_the_setting(self):
        faults = {"workflow": {"path": "other.yml"}, "fork": {"head_repository": {"full_name": "other/project"}},
                  "active": {"status": "in_progress"}, "missing-sha": {"head_sha": ""},
                  "wrong-repository": {"repository": {"full_name": "other/project"}}}
        for name, change in faults.items():
            with self.subTest(name=name):
                payload = copy.deepcopy(self.run)
                payload.update(change)
                self.gh(payload)
                with self.assertRaises(ValueError):
                    ci_runners.recover(REPO, 41, "local", cwd=self.world.cwd, env=self.world.env())
                self.assertFalse(any(c["argv"][0] == "variable" for c in self.world.tools.calls("gh")))

    def test_queued_offline_failed_missing_or_wrong_runner_is_not_completed_verification(self):
        job = {"name": "lane", "status": "completed", "conclusion": "success", "runner_name": "private-runner",
               "labels": ["self-hosted"]}
        complete = dict(self.run, conclusion="success")
        self.assertEqual(ci_runners.verdict(complete, [job], "local", SHA)["state"], "passed")
        for jobs in ([], [job, job], [dict(job, status="queued", runner_name="")],
                     [dict(job, conclusion="failure")], [dict(job, labels=["ubuntu-24.04"])]):
            with self.subTest(jobs=jobs):
                self.assertNotEqual(ci_runners.verdict(complete, jobs, "local", SHA)["state"], "passed")
        with self.assertRaisesRegex(ValueError, "candidate"):
            ci_runners.verdict(dict(complete, head_sha="b" * 40), [job], "local", SHA)
