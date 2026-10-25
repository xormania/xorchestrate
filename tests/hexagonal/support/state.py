"""Concrete state-contract fixtures; the shared scenarios own expectations."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from itertools import count
from threading import Barrier

from tests.hexagonal.contracts.state import StateObservation, StateOutcome
from xorchestrate.adapters.acceptance import RequiredChecksAcceptance
from xorchestrate.adapters.policies import ConfigurableRouting, RuleGuardrail
from xorchestrate.adapters.storage import MemoryStore, SQLiteStore
from xorchestrate.application import Application
from xorchestrate.lifecycles import default_definitions
from xorchestrate.model import Capability
from xorchestrate.state import MachineDefinition, StateEngine, Transition


def parcel_definition():
    return MachineDefinition("parcel-flow", 7, frozenset({"draft", "held", "done"}), (
        Transition("register", frozenset({None}), "draft"),
        Transition("claim", frozenset({"draft"}), "held"),
        Transition("release", frozenset({"held"}), "done", lambda context: context.get("permit") is True),
        Transition("reset", frozenset({"done"}), "draft"),
        Transition("touch", frozenset({"draft"}), "draft"),
    ))


class FailingHistory:
    """An adapter failure after the state write, not an engine verdict stub."""
    def __init__(self, transaction, after_append):
        self.transaction = transaction
        self.after_append = after_append

    def __getattr__(self, name):
        return getattr(self.transaction, name)

    def emit(self, event):
        if self.after_append:
            self.transaction.emit(event)
        raise RuntimeError("fixture history publication unavailable")


class StoredStateFixture:
    def __init__(self, storage, path):
        self.storage = storage
        self.path = path
        self.store = self._store()
        self.engine = StateEngine({"parcel-flow": parcel_definition()})

    def _store(self):
        return MemoryStore({}) if self.storage == "memory" else SQLiteStore({"path": str(self.path)})

    def close(self):
        self.store.close()

    def observe(self, key="parcel"):
        with self.store.transaction() as tx:
            row = tx.get("parcel", key)
            events = tuple(deepcopy(event) for event in tx.events(limit=1000)
                           if event.get("type") == "state.transition"
                           and event.get("subject") == {"kind": "parcel", "key": key})
        return StateObservation(deepcopy(row.data) if row else None, row.version if row else None, events)

    @staticmethod
    def _error(error):
        return StateOutcome(False, getattr(error, "code", type(error).__name__))

    def _apply(self, tx, event, version, permitted=False, key="parcel", wrong_target=False):
        # Fixed fixture inputs, authored separately from the subject definition.
        target = {"register": "draft", "claim": "held", "release": "done", "reset": "draft", "touch": "draft"}[event]
        if wrong_target:
            target = "done"
        data = {"status": target, "payload": {"label": "owned parcel"}}
        return self.engine.apply(tx, "parcel", key, event, data, version,
                                 {"permit": permitted, "actor": "fixture", "at": "fixture-time"},
                                 machine_name="parcel-flow")

    def change(self, event, version, *, permitted=False, key="parcel", wrong_target=False):
        try:
            with self.store.transaction() as tx:
                row = self._apply(tx, event, version, permitted, key, wrong_target)
            return StateOutcome(True, "", row.data["status"], row.version)
        except Exception as error:
            return self._error(error)

    def inspect(self, event, *, permitted=False):
        observed = self.observe()
        try:
            decision = self.engine.decide("parcel-flow", observed.data["status"] if observed.data else None,
                                          event, {"permit": permitted})
            return StateOutcome(True, "", decision.target, observed.version)
        except Exception as error:
            return self._error(error)

    def fail_history(self, *, catch_inside, after_append):
        version = self.observe().version
        retained = None
        try:
            with self.store.transaction() as tx:
                if catch_inside:
                    try:
                        self._apply(FailingHistory(tx, after_append), "claim", version)
                    except RuntimeError as error:
                        retained = error
                else:
                    self._apply(FailingHistory(tx, after_append), "claim", version)
            return self._error(retained) if retained else StateOutcome(True, "")
        except Exception as error:
            return self._error(error)

    def reconstruct(self):
        self.engine = StateEngine({"parcel-flow": parcel_definition()})
        if self.storage == "sqlite":
            self.store.close()
            self.store = self._store()

    def contend(self, version):
        barrier = Barrier(2, timeout=5)

        def run(_):
            separate = self._store() if self.storage == "sqlite" else self.store
            try:
                barrier.wait()
                try:
                    with separate.transaction() as tx:
                        row = self._apply(tx, "claim", version)
                    return StateOutcome(True, "", row.data["status"], row.version)
                except Exception as error:
                    return self._error(error)
            finally:
                if separate is not self.store:
                    separate.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            return tuple(pool.map(run, range(2)))


class ObservedProvider:
    """Controlled external facts, separate from requested candidate identity."""
    def __init__(self):
        self.calls = []
        self.failure = None

    def discover(self):
        return [Capability("check", "Observe the fixture product", {"type": "object"})]

    def invoke(self, name, arguments, context):
        self.calls.append(deepcopy(arguments))
        if self.failure is not None:
            raise self.failure
        if "measurement" not in arguments:
            return {"observed": "fixture-product"}
        request = arguments["measurement"]
        identity = {key: deepcopy(request[key]) for key in
                    ("work_id", "check_id", "requirements_identity", "requirement_revision")}
        return {"measurement": {**identity, "candidate": {"identity": "tree:fixture-product"},
                "bindings": {}, "status": "completed", "passed": True,
                "outcomes": {"delivery": True}}}


class ObservedDriver:
    def __init__(self):
        self.start_calls = 0
        self.delivery_calls = 0
        self.delivery_failure = None

    def start(self, specification):
        self.start_calls += 1
        return {"native_id": "fixture-conversation", "incarnation": str(self.start_calls), "ownership": "owned"}

    def deliver(self, session, message):
        self.delivery_calls += 1
        if self.delivery_failure is not None:
            raise self.delivery_failure
        return {"submitted": True}

    def send(self, session, message):
        return {"accepted": True}

    def poll(self, session):
        return [{"text": "fixture output"}]

    def stop(self, session):
        return {"stopped": True}


class LiveStateFixture:
    """Exercise the real Application, with explicit controlled external ports."""
    kinds = ("actor", "work", "operation", "session", "invocation", "delivery", "delivery_attempt",
             "native_call", "measurement", "message", "requirements", "candidate", "evidence", "native_observation")

    def __init__(self, storage, path):
        self.storage = storage
        self.path = path
        self.store = self._store()
        self.driver = ObservedDriver()
        self.provider = ObservedProvider()
        self.identifiers = count(1)
        self.engine = StateEngine(default_definitions())
        self.app = self._application()
        self.app.call("add_actor", {"actor_id": "worker"})
        self.work = self.app.call("create_work", {"goal": "Deliver the fixture product"})["work"]
        self.session = None
        self.message = None

    def _store(self):
        return MemoryStore({}) if self.storage == "memory" else SQLiteStore({"path": str(self.path)})

    def _application(self):
        return Application(self.store, {"fixture": self.provider}, {"fixture": self.driver},
                           RuleGuardrail(), ConfigurableRouting(), {},
                           lambda: "2026-10-25T12:00:00Z", lambda: f"fixture-{next(self.identifiers)}",
                           acceptance=RequiredChecksAcceptance(), state_engine=self.engine)

    def close(self):
        self.store.close()

    def snapshot(self):
        with self.store.transaction() as tx:
            records = {kind: [(row.key, row.version, deepcopy(row.data)) for row in tx.list(kind)]
                       for kind in self.kinds}
            return {"records": records, "events": deepcopy(tx.events(limit=1000))}

    def rows(self, kind):
        with self.store.transaction() as tx:
            return [{"id": row.key, "version": row.version, **deepcopy(row.data)} for row in tx.list(kind)]

    def history(self, kind, key):
        return [event for event in self.snapshot()["events"] if event.get("type") == "state.transition"
                and event.get("subject") == {"kind": kind, "key": key}]

    def set_creation_guard(self, kind, allowed):
        self.set_transition_guard(kind, "create", allowed)

    def set_transition_guard(self, kind, event, allowed):
        definitions = dict(self.engine.definitions)
        original = definitions[kind]
        transitions = tuple(replace(edge, guard=lambda context: allowed) if edge.event == event else edge
                            for edge in original.transitions)
        definitions[kind] = replace(original, version=2, transitions=transitions)
        self.engine = StateEngine(definitions)
        self.app.state_engine = self.engine

    def start_raw(self):
        result = self.app.call("start_session", {"driver": "fixture", "specification": {},
            "actor_id": "worker", "work_id": self.work["id"], "idempotency_key": "start"})
        self.session = result["session"]
        return result

    def start(self):
        try:
            self.start_raw()
            return StateOutcome(True, "", self.session["status"], self.session["version"])
        except Exception as error:
            return StoredStateFixture._error(error)

    def queue(self):
        self.message = self.app.call("send_message", {"destination": {"actor": "worker"},
            "body": "Check the delivered product", "work_id": self.work["id"]})["message"]
        return self.rows("delivery")[0]

    def dispatch(self):
        return self.app.call("dispatch_deliveries", {"message_id": self.message["id"]})["deliveries"]

    def retry(self, delivery):
        return self.app.call("retry_delivery", {"delivery_id": delivery["id"],
            "expected_version": delivery["version"]})["delivery"]

    def resolve_delivery(self, delivery):
        return self.app.call("resolve_delivery", {"delivery_id": delivery["id"],
            "expected_version": delivery["version"], "outcome": "failed",
            "reason": "Independent fixture observation established no submission"})["delivery"]

    def reconstruct(self):
        if self.storage == "sqlite":
            self.store.close()
            self.store = self._store()
        self.engine = StateEngine(default_definitions())
        self.app = self._application()

    def configure_measurement(self):
        self.app.call("set_requirements", {"work_id": self.work["id"], "expected_revision": 0,
            "objective": "Deliver the fixture product", "outcomes": {"delivery": "Product observed"},
            "checks": {"inspect": {"capability": "fixture/check", "outcomes": ["delivery"]}}})
        self.app.call("set_candidate", {"work_id": self.work["id"], "expected_revision": 0,
            "candidate": {"identity": "tree:fixture-product"}})

    def measure(self):
        return self.app.call("run_measurement", {"work_id": self.work["id"], "check_id": "inspect",
            "measurement_key": "measurement-1"})

    def live_trace(self):
        start = self.start_raw()
        invocation = self.app.call("invoke", {"capability": "fixture/check", "arguments": {},
            "idempotency_key": "invoke", "work_id": self.work["id"]})["invocation"]
        self.queue()
        submitted = self.dispatch()[0]
        source = {"session_id": self.session["id"], "native_id": "fixture-conversation", "incarnation": "1",
                  "call_id": "evidence-1", "actor": "worker", "work_id": self.work["id"]}
        self.app.invoke_native("fixture", "record_result", {"work_id": self.work["id"], "result": "Observed product"}, source)
        self.configure_measurement()
        measurement = self.measure()["measurement"]
        records = {"operation": self.rows("operation")[0], "session": self.session,
                   "invocation": invocation, "delivery": submitted["delivery"],
                   "delivery_attempt": submitted["attempt"], "native_call": self.rows("native_call")[0],
                   "measurement": measurement}
        records["operation"] = next(row for row in self.rows("operation") if row["id"] == start["operation_id"])
        return {"records": records, "histories": {kind: self.history(kind, row["id"]) for kind, row in records.items()},
                "start_calls": self.driver.start_calls, "delivery_calls": self.driver.delivery_calls,
                "provider_calls": len(self.provider.calls),
                "acceptance": self.app.call("get_acceptance", {"work_id": self.work["id"]})}
