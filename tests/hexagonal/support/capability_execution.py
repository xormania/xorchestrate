"""Controlled provider, authority and failure adapters for execution policies."""

from contextlib import contextmanager
from copy import deepcopy

from tests.hexagonal.support.application import application
from tests.hexagonal.support.delivery import Native
from xorchestrate.adapters.policies import RuleGuardrail
from xorchestrate.composition import build, validate_config
from xorchestrate.model import Capability, OrchestrationError, record_view


class Provider:
    def __init__(self, options=None):
        self.calls = 0
        self.callback = None
        self.annotations = deepcopy((options or {}).get("annotations", {}))

    def discover(self):
        return [Capability("inspect", "Controlled provider operation", {
            "type": "object", "properties": {"payload": {"type": "string"}},
            "required": ["payload"], "additionalProperties": False,
        }, deepcopy(self.annotations))]

    def invoke(self, name, arguments, context):
        self.calls += 1
        if self.callback is not None:
            return self.callback(arguments, context)
        return {"payload": "PRIVATE_PROVIDER_RESULT", "value": self.calls}


class TrustedQueryProvider(Provider):
    def execution_policies(self):
        return {"inspect": {"mode": "query", "observation": "none"}}


class Capture:
    def __init__(self):
        self.observations = []

    def new_id(self):
        return "capture-attempt"

    def record(self, event_type, payload, context, **metadata):
        self.observations.append(deepcopy({"event_type": event_type, "payload": payload,
                                           "context": context, **metadata}))
        return {"status": "retained", "receipt": None}


class RevisionGuardrail:
    def __init__(self):
        self.revision = 0
        self.denied = None

    def authority_revision(self):
        return self.revision

    def evaluate(self, action, context):
        return RuleGuardrail({"rules": [{"actions": [self.denied], "effect": "deny"}]}
                             if self.denied else {}).evaluate(action, context)


class CompletionFailure:
    """Fail exactly one local completion write after the provider returned."""

    def __init__(self, store):
        self.store, self.armed = store, True

    @contextmanager
    def transaction(self):
        parent = self
        with self.store.transaction() as tx:
            class Transaction:
                def __getattr__(self, name):
                    return getattr(tx, name)

                def put(self, kind, key, data, expected_version=None):
                    if parent.armed and kind == "operation" and data.get("status") == "completed":
                        parent.armed = False
                        raise RuntimeError("Controlled local settlement failure")
                    return tx.put(kind, key, data, expected_version)
            yield Transaction()


class Subject:
    def __init__(self, store, mode=None, observation="none"):
        self.store = store
        self.provider = Provider()
        self.guardrail = RevisionGuardrail()
        policies = None if mode is None else {"tools/inspect": {"mode": mode, "observation": observation}}
        self.app = application(store, providers={"tools": self.provider}, driver=Native(),
                               guardrail=self.guardrail, capability_execution=policies)
        self.capture = Capture()
        self.app.telemetry = self.capture
        self.work_id = None
        self.native_context = None

    def arguments(self, *, key="one", payload="PRIVATE_PROVIDER_ARGUMENT"):
        result = {"capability": "tools/inspect", "arguments": {"payload": payload}}
        if key is not None:
            result["idempotency_key"] = key
        if self.work_id:
            result["work_id"] = self.work_id
        return result

    def invoke(self, *, native=False, replay_only=False, **kwargs):
        arguments = self.arguments(**kwargs)
        if native:
            return self.app.invoke_native("agent", "invoke", arguments, self.native_context,
                                          replay_only=replay_only)
        return self.app.call("invoke", arguments)

    def failure(self, call):
        try:
            call()
        except OrchestrationError as error:
            return {"code": error.code, "details": deepcopy(error.details), "message": str(error)}
        raise AssertionError("CONTROL_CALL_MUST_FAIL")

    def retained(self):
        with self.store.transaction() as tx:
            records = {kind: [record_view(row) for row in tx.list(kind)]
                       for kind in ("operation", "invocation", "evidence", "native_call", "native_observation")}
            records["events"] = tx.events(limit=1000)
        records["capture"] = deepcopy(self.capture.observations)
        return records

    def make_work(self):
        self.work_id = self.app.call("create_work", {"goal": "Provider policy witness"})["work"]["id"]
        self.capture.observations.clear()

    def make_native(self):
        self.make_work()
        session = self.app.call("start_session", {"driver": "agent", "specification": {},
            "work_id": self.work_id, "idempotency_key": "native-session"})["session"]
        self.native_context = {"actor": "owner", "work_id": self.work_id, "session_id": session["id"],
            "native_id": session["backend"]["native_id"], "incarnation": session["backend"]["incarnation"],
            "call_id": "provider-call"}
        self.capture.observations.clear()

    def deny(self, action):
        self.guardrail.denied = action
        self.guardrail.revision += 1

    def change_during_call(self, target):
        def callback(arguments, context):
            if target == "authority":
                self.guardrail.revision += 1
            else:
                with self.store.transaction() as tx:
                    work = tx.get("work", self.work_id)
                    tx.put("work", work.key, {**work.data, "title": "changed during query"}, work.version)
            return {"payload": "PRIVATE_PROVIDER_RESULT"}
        self.provider.callback = callback

    def observe_during_call(self):
        def callback(arguments, context):
            self.app.record_observation("capability.observed", {"payload": "PRIVATE_PROVIDER_RESULT"}, context)
            return {"payload": "PRIVATE_PROVIDER_RESULT", "value": self.provider.calls}
        self.provider.callback = callback

    def fail_provider(self, outcome):
        def callback(arguments, context):
            if outcome == "unknown":
                raise RuntimeError("PRIVATE_PROVIDER_RESULT")
            raise OrchestrationError("PRIVATE_PROVIDER_RESULT", "provider_failed", {
                "outcome": outcome, "external_result": {"payload": "PRIVATE_PROVIDER_RESULT"}})
        self.provider.callback = callback

    def fail_settlement(self):
        self.app.store = CompletionFailure(self.store)

    def operation(self, operation_id):
        return self.app.call("get_operation", {"operation_id": operation_id})["operation"]

    def resolve(self, operation_id):
        operation = self.operation(operation_id)
        return self.app.call("resolve_operation", {"operation_id": operation_id,
            "expected_version": operation["version"], "outcome": "completed",
            "result": {"payload": "PRIVATE_RECONCILIATION_RESULT"}, "reason": "Verified independently"})


def configured_subject(root, *, defaults=False, policy=None, annotations=None):
    name = "TrustedQueryProvider" if defaults else "Provider"
    configuration = {"providers": {"reports": {"adapter":
        "tests.hexagonal.support.capability_execution:" + name,
        "options": {"annotations": annotations or {}}}}}
    if policy is not None:
        configuration["capability_execution"] = {"reports/inspect": policy}
    return build(configuration, root)


def configuration_error(value, root):
    try:
        validate_config({"capability_execution": value}, root)
    except OrchestrationError as error:
        return error.code
    return None
