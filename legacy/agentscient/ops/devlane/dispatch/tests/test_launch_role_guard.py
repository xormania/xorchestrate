"""The launcher refuses a read-role job whose prompt writes an undeclared path (item 5, item 31).

The guard stands before the mint, through the correction seam, so the
refusal is exit 2 on the founding wire. launch.py is loaded by path and its
guard called directly; the recognition itself is proved in
ops/devlane/task/tests/test_write_path.py. One producer (grant 149).
"""

from __future__ import annotations

import json
import unittest

import launch_support as ls

from ops.testkit import World


class RoleGuard(unittest.TestCase):
    def setUp(self):
        self.world = World(prefix="launch-role-guard-")
        self.world.enter()
        self.addCleanup(self.world.close)
        self.launch = ls.require_module(self, "launch")

    def test_a_read_role_prompt_writing_an_undeclared_path_refuses_role(self):
        job = {"role": "read", "prompt": "Write the report to {out}/SKEPTIC.md and name that path in artifacts"}
        with self.assertRaises(self.launch.CORRECTION.Refusal) as caught:
            self.launch._read_role_write_guard("check-tests", job)
        self.assertEqual(caught.exception.kind, "role")
        line = caught.exception.line()
        self.assertRegex(line, r"^role: expected .+; found .+; needed .+$")
        self.assertIn("check-tests", line)
        self.assertIn("SKEPTIC.md", line)

    def test_a_declared_output_passes(self):
        job = {
            "role": "read",
            "prompt": "Write the plan to {out}/PLAN.md and name that path in artifacts",
            "outputs": [{"kind": "file", "path": "PLAN.md", "artifact": "plan"}],
        }
        self.assertIsNone(self.launch._read_role_write_guard("plan", job))

    def test_a_write_role_is_not_judged(self):
        job = {"role": "write", "prompt": "Write the code to src/x.py"}
        self.assertIsNone(self.launch._read_role_write_guard("implement", job))

    def test_the_live_read_jobs_pass_the_guard(self):
        jobs = json.loads(ls.JOBS_PATH.read_text(encoding="utf-8"))
        for name, job in jobs.items():
            if job.get("role") == "read":
                self.assertIsNone(self.launch._read_role_write_guard(name, job), name)


if __name__ == "__main__":
    unittest.main()
