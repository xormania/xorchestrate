"""Existing telemetry subjects reached through ordinary provider invocation."""

import json
from copy import deepcopy

from tests.hexagonal.support.telemetry import Fixture
from xorchestrate.adapters.policies import RuleGuardrail
from xorchestrate.model import OrchestrationError, record_view


class ProviderFixture(Fixture):
    def __init__(self, root, *, provider_name="telemetry", provider_options=None,
                 provider_enabled=True, **configuration):
        self.provider_name = provider_name
        providers = {provider_name: {"adapter": "telemetry", "options": provider_options or {},
                                     "enabled": provider_enabled}}
        super().__init__(root, providers=providers, **configuration)
        self.configuration["telemetry"]["options"]["sources"].append("telemetry")

    def invoke(self, name, arguments=None, *, actor="owner", key=None):
        request = {"capability": f"{self.provider_name}/{name}",
                   "arguments": deepcopy(arguments or {})}
        if key is not None:
            request["idempotency_key"] = key
        return self.app.call("invoke", request, actor=actor)

    def query(self, name, arguments=None, *, actor="owner"):
        return self.invoke(name, arguments, actor=actor)["result"]

    def descriptors(self):
        return self.app.call("capabilities", {"provider": self.provider_name})["capabilities"]

    def event_page(self, *, actor="owner", **filters):
        return self.query("events", {"limit": 1000, **filters}, actor=actor)

    def payload(self, event_id, *, actor="owner", **arguments):
        return self.query("payload", {"event_id": event_id, **arguments}, actor=actor)

    def usage(self, **arguments):
        return self.query("usage", arguments)

    def status(self, *, actor="owner"):
        return self.query("status", actor=actor)

    def deny(self, actions, *, actor="observer", capability=None, work_id=None):
        rule = {"actions": actions, "actors": [actor], "effect": "deny"}
        if capability is not None:
            rule["capabilities"] = [f"{self.provider_name}/{capability}"]
        if work_id is not None:
            rule["where"] = {"work.id": work_id}
        self.app.guardrail = RuleGuardrail({"rules": [rule]})

    def refusal(self, name, arguments=None, *, actor="observer", key=None):
        try:
            self.invoke(name, arguments, actor=actor, key=key)
        except OrchestrationError as error:
            return error.code
        return None

    def retained(self):
        with self.app.store.transaction() as tx:
            return {kind: [record_view(row) for row in tx.list(kind)] for kind in (
                "operation", "invocation", "evidence", "native_call", "native_observation")}

    def collector_count(self):
        return self.app.telemetry.status()["event_count"]

    def bundle_path(self, name):
        return self.root / name

    def read_manifest(self, name):
        return json.loads((self.bundle_path(name) / "manifest.json").read_text())

    def bundle_exists(self, name):
        path = self.bundle_path(name)
        return path.exists() and any(path.rglob("*.sqlite3"))


def without_capture(root):
    subject = ProviderFixture(root)
    subject.configuration["telemetry"] = {"enabled": False}
    return subject
