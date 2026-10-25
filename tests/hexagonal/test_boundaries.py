"""Enforce the declared application architecture through the shared checker.

The same profile and engine drive local CLI checks and the architecture CI job.
Generic valid/faulty structures calibrate that engine in
test_architecture_enforcement.py; port behavior remains in shared contracts.
"""
import json
import unittest
from pathlib import Path

from scripts.architecture.python import check

ROOT = Path(__file__).resolve().parents[2]


class DependencyBoundaryTests(unittest.TestCase):
    def test_application_and_its_helpers_do_not_depend_on_infrastructure(self):
        profile = json.loads((ROOT / "contracts" / "hexagonal.json").read_text(encoding="utf-8"))
        report = check(ROOT, profile)
        self.assertEqual(report["status"], "pass", report)
        self.assertEqual(report["coverage"]["classified"], report["coverage"]["modules"])
        self.assertGreater(report["coverage"]["modules"], 0)
        self.assertLessEqual({
            "xorchestrate.application", "xorchestrate.ports", "xorchestrate.state",
            "xorchestrate.composition", "xorchestrate.adapters.storage",
        }, {item["module"] for item in report["files"]})


if __name__ == "__main__":
    unittest.main()
