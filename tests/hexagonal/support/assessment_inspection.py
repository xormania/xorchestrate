"""Concrete composition adapters for the shared assessment inspection contract."""
from copy import deepcopy
from itertools import count

from tests.hexagonal.contracts.assessment_inspection import InspectionFailure
from xorchestrate.adapters.policies import RuleGuardrail
from xorchestrate.model import Assessment, Capability, OrchestrationError

from .application import application, make_store


class SessionDriver:
    def start(self, specification):
        return {"native_id": "fixture-native", "incarnation": "fixture-incarnation"}


class EchoProvider:
    def discover(self):
        return [Capability("echo", "Return opaque content", {"type": "object"})]

    def invoke(self, name, arguments, context):
        return deepcopy(arguments)


class Evaluator:
    def __init__(self):
        self.contexts = []

    def assess(self, question, context):
        self.contexts.append(deepcopy(context))
        # Record-shaped evaluator content is opaque too.
        return Assessment("pass", 1.0, "public conclusion", evidence=({
            "input_binding": {"actor_record": {"id": "worker", "opaque": True}}},))


class Fixture:
    def __init__(self, implementation, path, *, work_fields=None, fault=None):
        self.store = make_store(implementation, path)
        self.evaluator = Evaluator()
        identifiers = count()
        assessment = {"triage": "review"}
        if work_fields is not None:
            assessment["work_input_fields"] = work_fields
        self.app = application(self.store, providers={"fixture": EchoProvider()}, sessions={"fixture": SessionDriver()},
            evaluators={"review": self.evaluator}, clock=lambda: "2026-10-24T00:00:00Z",
            identifiers=lambda: f"record-{next(identifiers)}", assessment=assessment)
        if fault == "omit_assessment_projection":
            self.app._inspection_assessment_binding = lambda *args: []
        elif fault == "unrelated_error":
            def unavailable(*args):
                raise TimeoutError("unrelated inspection failure")
            self.app._inspection_assessment_binding = unavailable

    def close(self):
        self.store.close()

    def call(self, action, arguments, actor="owner"):
        try:
            return self.app.call(action, arguments, actor=actor)
        except OrchestrationError as error:
            raise InspectionFailure(error.code) from error

    def native(self, session, action, arguments, call_id):
        source = {"actor": session["actor_id"], "work_id": session["work_id"], "session_id": session["id"],
            "native_id": session["backend"]["native_id"], "incarnation": session["backend"]["incarnation"],
            "call_id": call_id}
        return self.app.invoke_native("fixture", action, arguments, source)

    def start_session(self, work_id, actor):
        return self.call("start_session", {"driver": "fixture", "actor_id": actor, "work_id": work_id,
            "specification": {}, "idempotency_key": "start"})["session"]

    def provider_evidence(self, session, payload):
        return self.native(session, "invoke", {"capability": "fixture/echo", "work_id": session["work_id"],
            "arguments": payload, "idempotency_key": "provider"}, "provider")

    def restrict(self, kind, identity, effect="deny"):
        self.app.guardrail = RuleGuardrail({"rules": [{"actors": ["observer"],
            "actions": ["native_observations", "get_work"],
            "where": {"inspection.kind": kind, "record.id": identity}, "effect": effect}]})

    def permit_all(self):
        self.app.guardrail = RuleGuardrail()

    def raw_records(self):
        with self.store.transaction() as tx:
            return {kind: [(row.key, row.version, deepcopy(row.data)) for row in tx.list(kind)]
                for kind in ("actor", "work", "assessment", "evidence", "native_call", "native_observation")}

    def evaluated_contexts(self):
        return deepcopy(self.evaluator.contexts)

    def change_during_actor_inspection(self, target, change):
        fixture = self
        original = self.app.guardrail
        changed = []

        class ChangingPolicy:
            def evaluate(self, action, context):
                if context.get("inspection", {}).get("kind") == "actor" and not changed:
                    with fixture.store.transaction() as tx:
                        row = tx.get("actor", "observer") if target == "inspector" else tx.list("work")[0]
                        if change == "delete":
                            tx.delete(row.kind, row.key, row.version)
                        else:
                            tx.put(row.kind, row.key, {**row.data, "metadata": {"changed": True}}, row.version)
                    changed.append(True)
                return original.evaluate(action, context)

        self.app.guardrail = ChangingPolicy()
