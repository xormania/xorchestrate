import json
import sys
import unittest
from unittest.mock import patch

from fixtures.commands import GOAL, CommandFixture, responsibility

from devtools.api import API
from devtools.development import Development
from devtools.operations import Operations, execute


class PlannerOperationTests(unittest.TestCase):
    setUp = CommandFixture.setUp

    def test_planner_reads_own_complete_diagnostics_but_not_command_operations(self):
        config = json.loads((self.state / "team.json").read_text())
        config["project"] = {**config.get("project", {}), "development": {"checks": {
            "fixture": [sys.executable, "-c", "print('fixture')"]}}}
        (self.state / "team.json").write_text(json.dumps(config))
        self.co.call("direct", GOAL)
        xo = API(self.state, "xo")
        xo.call("orchestrate", {"work": [responsibility("Plan", "planner")]})
        with self.store.connect() as db:
            member = db.execute("SELECT member FROM member_bindings WHERE role='planner'").fetchone()[0]
        planner = API(self.state, member)
        failure = "Diagnostic prelude " * 60 + " REQUIRED REMEDIATION: package incompatible"
        with patch.object(Operations, "launch"), patch.object(Development, "inspect", side_effect=ValueError(failure)):
            own = planner.call("framework_inspect", {"kind": "packages", "package": "symfony/*"})
            execute(self.state, member, int(own["reference"].split(":")[1]))
        overview = planner.call("work_inspect", {"section": "operations"})["operations"][0]
        self.assertEqual("failed", overview["state"])
        self.assertNotIn("REQUIRED REMEDIATION", overview["error"])
        details = json.loads(planner.call("work_context", {"reference": own["reference"]})["content"])
        self.assertIn("REQUIRED REMEDIATION", details["error"])

        with patch.object(Operations, "launch"), patch.object(Development, "check", return_value={"passed": True}):
            other = xo.call("work_check", {})
            execute(self.state, "xo", int(other["reference"].split(":")[1]))
        with self.assertRaisesRegex(ValueError, "not available"):
            planner.call("work_context", {"reference": other["reference"]})


if __name__ == "__main__":
    unittest.main()
