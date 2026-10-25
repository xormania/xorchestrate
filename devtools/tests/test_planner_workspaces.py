import unittest
from pathlib import Path

from fixtures.delivery import DeliveryFixture
from support import adopt_plan

from devtools.api import API
from devtools.workspaces import git


class PlannerWorkspaceTests(unittest.TestCase):
    setUp_base = DeliveryFixture.setUp_base
    prepared = DeliveryFixture.prepared

    def setUp(self):
        DeliveryFixture.setUp(self)
        adopt_plan(self.xo, [{"name": "Feature", "objective": "Feature", "done_when": "Works",
                              "constraints": "Product only"}])
        self.xo.call("steer", {"action": "resume", "work": "Feature"})
        self.api.call("work_report", {"kind": "result", "text": "Implemented and submitted the feature"})
        self.xo.call("steer", {"action": "complete", "work": "Feature", "assignment": "Feature",
                               "direction": "Contribution checked"})
        self.integration = self.xo.workspaces.ensure()
        self.initial = git(self.integration["root"], "rev-parse", "HEAD").stdout.decode().strip()

    def planner(self):
        self.xo.call("orchestrate", {"work": [{"name": "Next plan", "role": "planner",
                      "objective": "Inspect the integrated feature and plan follow-up work",
                      "done_when": "The follow-up jobs reflect current source", "constraints": "Read only"}]})
        with self.store.connect() as db:
            member = db.execute("SELECT member FROM member_bindings WHERE role='planner'").fetchone()[0]
        return API(self.state, member)

    def accept(self):
        self.xo.call("monitor", {"work": "Feature"})
        self.xo.call("steer", {"action": "accept", "work": "Feature", "direction": "Checked contribution accepted"})
        return git(self.remote, "rev-parse", self.integration["branch"]).stdout.decode().strip()

    def test_next_planner_reads_accepted_source_without_xo_refresh(self):
        accepted = self.accept()
        self.assertNotEqual(self.initial, accepted)
        self.assertEqual(self.initial, git(self.integration["root"], "rev-parse", "HEAD").stdout.decode().strip())
        planner = self.planner()
        self.assertNotIn("work_refresh", {tool["name"] for tool in planner.tools()})
        self.assertEqual(accepted, planner.call("work_inspect", {})["revision"])
        with planner.workspaces.use() as (_, workspace):
            self.assertEqual("Delivered implementation\n", Path(workspace["root"], "src/Example.php").read_text())
        self.assertEqual(self.head, git(self.workspace, "rev-parse", "HEAD").stdout.decode().strip())

    def test_existing_planner_refreshes_between_inspections_and_holds_order_lock(self):
        planner = self.planner()
        self.assertEqual(self.initial, planner.call("work_inspect", {})["revision"])
        accepted = self.accept()
        self.assertEqual(accepted, planner.call("work_inspect", {})["revision"])
        with planner.workspaces.use() as (_, workspace):
            with self.assertRaisesRegex(ValueError, "Another operation"):
                self.xo.call("work_refresh", {})
            self.assertEqual(accepted, git(workspace["root"], "rev-parse", "HEAD").stdout.decode().strip())

    def test_planner_refresh_preserves_unexpected_integration_edits(self):
        self.accept()
        path = Path(self.integration["root"], "README.md")
        path.write_text("Unexpected operator edit\n")
        planner = self.planner()
        with self.assertRaisesRegex(ValueError, "unexpected changes"):
            planner.call("work_inspect", {})
        self.assertEqual("Unexpected operator edit\n", path.read_text())
        self.assertEqual(self.initial, git(self.integration["root"], "rev-parse", "HEAD").stdout.decode().strip())
        self.assertEqual("Delivered implementation\n", Path(self.workspace, "src/Example.php").read_text())


if __name__ == "__main__":
    unittest.main()
