"""Shared runtime arrangements without test methods."""

import json
import tempfile
from pathlib import Path

from support import initialize, runtime_policy

from devtools.api import API
from devtools.store import Store
from fixtures.commands import GOAL


class RuntimeFixture:

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.state = self.root / "state"
        initialize(self.state, self.workspace, ["owner:external"], co="owner", runtime=True)
        self.store = Store(self.state)
        self.co = API(self.state, "owner")
        policy = runtime_policy()
        for row in policy["profiles"].values():
            row.update(binary="/bin/true", model="configured-model", reasoning="high")
        self.write("roles", policy)
        self.vendor("openai", True)

    def write(self, name, value):
        (self.state / (name + ".json")).write_text(json.dumps(value))

    def vendor(self, name, available):
        value = json.loads((self.state / "vendors.json").read_text())
        value["vendors"][name] = {"available": available, "reason": "Quota unavailable" if not available else "Enabled"}
        self.write("vendors", value)

    def xo(self):
        self.co.call("direct", GOAL)
        with self.store.connect() as db:
            name = db.execute("SELECT member FROM member_bindings WHERE role='xo'").fetchone()[0]
        return API(self.state, name)
