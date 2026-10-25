"""Shared coordination arrangements without test methods."""

import json

from support import adopt_plan

from devtools.api import API
from fixtures.commands import responsibility
from fixtures.workspaces import WorkspaceFixture


class AssignmentFixture:

    def setUp(self):
        WorkspaceFixture.setUp(self)
        team = json.loads((self.state / "team.json").read_text())
        team["members"] += [{"name": f"slot{i}", "harness": "external", "workspace": str(self.checkout)} for i in range(4)]
        team["project"].update(serena={"image": "agentscient-serena:fixture"}, development={})
        (self.state / "team.json").write_text(json.dumps(team))
        self.coordinator = API(self.state, "xo")

    def by_role(self, role):
        with self.store.connect() as db:
            name = db.execute("SELECT member FROM member_bindings WHERE role=?", (role,)).fetchone()[0]
        return API(self.state, name)

    def plan(self):
        _, report = adopt_plan(self.coordinator, [{"name": "Feature", "objective": "Feature", "done_when": "Works", "constraints": "Product only"}])
        return None, report

    def implementation(self, result):
        item = responsibility("Feature") | {"plan": result["reference"], "editable_paths": ["src/Example.php"], "context_paths": ["README.md"]}
        self.coordinator.call("orchestrate", {"work": [item]})
        self.coordinator.call("steer", {"action": "resume", "work": "Feature"})
        return self.by_role("coder"), item
