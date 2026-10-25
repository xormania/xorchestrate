"""Shared diagnostics arrangements without test methods."""

import json

from devtools.api import API
from devtools.diagnostics import Diagnostics
from fixtures.advice import answer
from fixtures.control import ControlFixture


class DiagnosticFixture:
    setUp_base = ControlFixture.setUp
    client = ControlFixture.client

    def setUp(self):
        self.setUp_base()
        self.api = API(self.state, "coder")
        config = json.loads((self.state / "team.json").read_text())
        config.setdefault("project", {})["configuration_file"] = "configuration.json"
        config["project"]["development"] = {}
        (self.state / "team.json").write_text(json.dumps(config))
        self.configure(project={"features": {"advice": True, "filtering": True, "guidance": True, "completion": True}})
        self.diagnostics = Diagnostics(self.store, "coder")

    def configure(self, **overrides):
        value = {"version": 1, **overrides}
        (self.state / "configuration.json").write_text(json.dumps(value))

    def report(self, text, kind="finding", evidence=None, member="coder"):
        args = {"text": text, "kind": kind}
        if evidence:
            args["evidence"] = {"name": "observation.txt", "content": evidence}
        return API(self.state, member).call("work_report", args)

    def context(self):
        return self.api.control.work_context(project=False)

    @staticmethod

    def relevance(state, questions, model):
        response = answer(state, questions, model)
        for index, fact in enumerate(state["facts"]):
            response["answers"][f"fact_{index}"]["noul"] = 0.01 if fact["text"] == "Unrelated color preference" else 0.9
        return response
