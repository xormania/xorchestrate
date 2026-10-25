"""Shared runtime selection arrangements without test methods."""

import json
import sys
import tempfile
from pathlib import Path

from support import runtime_policy

from devtools import runtime
from devtools.api import API
from devtools.launch import initialize, team_config
from devtools.store import Store


class RuntimeSelectionFixture:

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state, self.workspace = self.root / "state", self.root / "workspace"
        self.workspace.mkdir()
        initialize(self.state, self.workspace, ["owner:external"], co="owner", runtime=True)
        self.policy = runtime_policy()
        for name, profile in self.policy["profiles"].items():
            profile.update(binary=sys.executable, model="model-" + name, reasoning="high")
        self.write_policy()
        vendors = runtime.vendor_defaults(self.policy)
        for vendor in vendors["vendors"].values():
            vendor["available"] = True
        (self.state / "vendors.json").write_text(json.dumps(vendors))
        self.store = Store(self.state)
        self.operator = API(self.state, "owner", operator=True)
        self.order = self.operator.work.work_order_create(title="Runtime choice", objective="Retain selected profile",
                                                          done_when="Launch respects the decision")["work_order"]["id"]
        self.operator.control.bind("owner", "co", self.order)

    def write_policy(self):
        (self.state / "roles.json").write_text(json.dumps(self.policy))

    def choose(self, profile="claude", *, retain=True):
        choice = runtime.resolve(self.state, "coder", profile=profile)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            slot = runtime.allocate(self.store, db, team_config(self.state), "coder", selected=choice)
            scope = self.operator.control.bind_in(db, slot["name"], "coder", self.order, reports_to="owner")
            if retain:
                runtime.retain(self.store, db, scope, choice)
        return scope, choice
