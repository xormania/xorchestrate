"""Acceptance composed with explicit callbacks and no Application instance."""
import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from itertools import count

from xorchestrate.acceptance import WorkAcceptance
from xorchestrate.adapters.acceptance import RequiredChecksAcceptance
from xorchestrate.lifecycles import DURABLE_LIFECYCLES, default_definitions
from xorchestrate.model import Conflict, OrchestrationError
from xorchestrate.state import StateEngine

from .application import make_store
from .work_acceptance import BINDINGS, CANDIDATE, MeasuringProvider


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _view(record):
    return {**deepcopy(record.data), "id": record.key, "version": record.version}


class Policy:
    def __init__(self, identity):
        self.identity = identity

    def evaluate(self, snapshot):
        return RequiredChecksAcceptance().evaluate(snapshot)


class Fixture:
    kinds = ("work", "requirements", "requirement_revision", "candidate", "candidate_revision",
        "measurement", "operation", "acceptance_decision")

    def __init__(self, storage, path):
        self.storage, self.path = storage, path
        self.store = make_store(storage, path)
        self.provider = MeasuringProvider()
        self.policy = Policy("initial-policy")
        self.provider_identities = {"checks": "initial-provider"}
        self.invocations = []
        self.invocation_denied = False
        self.identifiers = count(1)
        self.engine = StateEngine(default_definitions())
        with self.store.transaction() as tx:
            tx.put("work", "work", {"goal": "Deliver measured behavior"})
        self.component = self.compose()

    @staticmethod
    def clock():
        return "2026-11-07T12:00:00Z"

    def compose(self):
        return WorkAcceptance(self.store, clock=self.clock, verify_authorization=self.verify_authorization,
            create=self.create, transition=self.transition, emit=self.emit, invoke=self.invoke,
            policy=lambda: self.policy, assessment_configuration=lambda: {},
            provider_configurations=lambda: self.provider_identities, evaluator_configurations=lambda: {})

    def close(self):
        self.store.close()

    def context(self):
        with self.store.transaction() as tx:
            work = _view(tx.get("work", "work"))
        return {"work_id": "work", "work": work, "acceptance_basis": self.component.context("work")}

    def verify_authorization(self, tx, context):
        if tx.get("work", "work").version != context["work"]["version"]:
            raise Conflict("Fixture work changed after authorization")
        self.component.verify_context(tx, context)

    def create(self, tx, kind, actor, data, key=None):
        key = key or f"fixture-{next(self.identifiers)}"
        payload = {**deepcopy(data), "created_by": actor, "created_at": self.clock()}
        record = (self.engine.apply(tx, kind, key, "create", payload, context={"actor": actor, "at": self.clock()})
            if kind in DURABLE_LIFECYCLES else tx.put(kind, key, payload))
        self.emit(tx, f"{kind}.created", actor, record_id=key, work_id=data.get("work_id"))
        return record

    def transition(self, tx, current, event, data, actor):
        return self.engine.apply(tx, current.kind, current.key, event, data, current.version,
            {"actor": actor, "at": self.clock()})

    def emit(self, tx, name, actor, **data):
        tx.emit({"type": name, "actor": actor, "at": self.clock(), **deepcopy(data)})

    def invoke(self, arguments, actor):
        self.invocations.append({"arguments": deepcopy(arguments), "actor": actor})
        if self.invocation_denied:
            raise OrchestrationError("Fixture invocation denied", "denied")
        raw = self.provider.invoke("measure", arguments["arguments"], {"actor": actor})
        key = _hash([actor, arguments["idempotency_key"]])
        with self.store.transaction() as tx:
            tx.put("operation", key, {"action": "invoke", "actor": actor, "work_id": arguments["work_id"],
                "arguments": {key: deepcopy(value) for key, value in arguments.items() if key != "idempotency_key"},
                "status": "completed", "external_result_origin": "adapter", "external_result": deepcopy(raw)})
        return {"invocation": {"result": raw}, "operation_id": key}

    def configure_measurement(self):
        requirements = self.component.set_requirements({"work_id": "work", "expected_revision": 0,
            "objective": "Deliver independently measured behavior", "outcomes": {"A": "Behavior"},
            "checks": {"A": {"capability": "checks/measure", "outcomes": ["A"], "bindings": BINDINGS}}},
            "worker", self.context())["requirements"]
        self.component.set_candidate({"work_id": "work", "expected_revision": 0, "candidate": CANDIDATE},
            "worker", self.context())
        return requirements

    def measure(self, key="measurement-1", actor="worker", arguments=None):
        args = {"work_id": "work", "check_id": "A", "measurement_key": key}
        if arguments is not None:
            args["arguments"] = arguments
        return self.component.run_measurement(args, actor, self.context())

    def deny_invocation(self, denied):
        self.invocation_denied = denied

    def invocation_requests(self):
        return deepcopy(self.invocations)

    def effect_count(self):
        return len(self.provider.calls)

    def accept(self):
        return self.component.accept_work({"work_id": "work"}, "worker", self.context())["acceptance"]

    def accepted(self):
        with self.store.transaction() as tx:
            return self.component.view(tx, "work")["accepted"]

    def replace_provider_identity(self, identity):
        self.provider_identities = {"checks": identity}

    def replace_policy(self, identity):
        self.policy = Policy(identity)

    def change_policy_during_evaluation(self):
        fixture, previous = self, self.policy

        class ChangingPolicy:
            identity = previous.identity

            def evaluate(self, snapshot):
                fixture.replace_policy("changed-during-evaluation")
                return previous.evaluate(snapshot)

        self.policy = ChangingPolicy()

    def rows(self, kind):
        with self.store.transaction() as tx:
            return [_view(row) for row in tx.list(kind)]

    def snapshot(self):
        with self.store.transaction() as tx:
            return {"records": {kind: [_view(row) for row in tx.list(kind)] for kind in self.kinds},
                "events": deepcopy(tx.events(limit=1000))}

    def history(self, kind, key):
        return [event for event in self.snapshot()["events"] if event.get("type") == "state.transition"
            and event.get("subject") == {"kind": kind, "key": key}]

    def set_transition_guard(self, kind, event, allowed):
        definitions = dict(self.engine.definitions)
        original = definitions[kind]
        definitions[kind] = replace(original, version=2,
            transitions=tuple(replace(edge, guard=lambda context: allowed) if edge.event == event else edge
                for edge in original.transitions))
        self.engine = StateEngine(definitions)

    def reconstruct(self):
        if self.storage == "sqlite":
            self.store.close()
            self.store = make_store(self.storage, self.path)
        self.engine = StateEngine(default_definitions())
        self.component = self.compose()
