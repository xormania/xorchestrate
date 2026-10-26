"""Controlled application ports and composition shared by behavioral families.

Collected tests consume these fixtures; fixtures never import collected tests.
The production application remains the subject, not the expected-result oracle.
"""

import itertools
from contextlib import contextmanager
from copy import deepcopy

from xorchestrate.adapters.policies import ConfigurableRouting, RuleGuardrail
from xorchestrate.adapters.storage import MemoryStore, SQLiteStore
from xorchestrate.application import Application
from xorchestrate.model import Capability


class Provider:
    def __init__(self, callback=None):
        self.callback = callback
        self.discoveries = 0
        self.calls = []

    def discover(self):
        self.discoveries += 1
        return [Capability("change", "Perform a change", {
            "type": "object", "properties": {"value": {"type": "integer"}},
            "required": ["value"], "additionalProperties": False,
        })]

    def invoke(self, name, arguments, context):
        self.calls.append((name, deepcopy(arguments), deepcopy(context)))
        return self.callback(name, arguments, context) if self.callback else {"value": arguments["value"]}


class Sessions:
    def __init__(self):
        self.specifications = []
        self.calls = []

    def start(self, specification):
        self.specifications.append(deepcopy(specification))
        return {"handle": "owned-handle", "native_id": "conversation"}

    def send(self, session, message):
        self.calls.append(("send", deepcopy(session), deepcopy(message)))
        return {"accepted": True}

    def poll(self, session):
        self.calls.append(("poll", deepcopy(session)))
        return [{"text": "Feature checked"}]

    def stop(self, session):
        self.calls.append(("stop", deepcopy(session)))
        return {"stopped": True}


class ResumableSessions(Sessions):
    def __init__(self):
        super().__init__()
        self.resumes = []

    def resume(self, source_backend, specification):
        self.resumes.append((deepcopy(source_backend), deepcopy(specification)))
        return {"handle": f"resumed-{len(self.resumes)}", "opaque_identity": {"from": deepcopy(source_backend)}}


class Evaluator:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def assess(self, question, context):
        self.calls.append((question, deepcopy(context)))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


STORAGE_ADAPTERS = ("memory", "sqlite")


def make_store(implementation="memory", path=None):
    """Select declared persistence without duplicating adapter construction."""
    if implementation == "memory":
        return MemoryStore()
    if implementation == "sqlite" and path is not None:
        return SQLiteStore(str(path))
    raise ValueError("Select a declared storage adapter and supply a path for SQLite")


def application(store=None, *, provider=None, driver=None, providers=None, sessions=None,
                guardrail=None, routing=None, evaluators=None, assessment=None,
                clock=None, identifiers=None, acceptance=None, session_fallbacks=None,
                receipt_policy=None, session_selection=None, delivery_scheduler=None,
                retry_policy=None, provider_configurations=None,
                evaluator_configurations=None, state_engine=None):
    """One composition boundary for controlled application port implementations."""
    if provider is not None and providers is not None:
        raise ValueError("Select provider or providers, not both")
    if driver is not None and sessions is not None:
        raise ValueError("Select driver or sessions, not both")
    sequence = itertools.count(1)
    return Application(
        store=store if store is not None else make_store(),
        providers=providers if providers is not None else ({"tools": provider} if provider is not None else {}),
        sessions=sessions if sessions is not None else ({"agent": driver} if driver is not None else {}),
        guardrail=guardrail if guardrail is not None else RuleGuardrail(),
        routing=routing if routing is not None else ConfigurableRouting(),
        evaluators=evaluators if evaluators is not None else {},
        clock=clock if clock is not None else lambda: "2026-10-23T12:00:00Z",
        identifiers=identifiers if identifiers is not None else lambda: f"r{next(sequence)}",
        assessment=assessment, acceptance=acceptance, session_fallbacks=session_fallbacks,
        receipt_policy=receipt_policy, session_selection=session_selection,
        delivery_scheduler=delivery_scheduler, retry_policy=retry_policy,
        provider_configurations=provider_configurations,
        evaluator_configurations=evaluator_configurations, state_engine=state_engine,
    )


def invoke(app, key="effect", value=1, **kwargs):
    return app.call("invoke", {"capability": "tools/change", "arguments": {"value": value}, "idempotency_key": key, **kwargs})


class WriteFailureStore(MemoryStore):
    def __init__(self, failed_status):
        super().__init__()
        self.failed_status = failed_status
        self.failed = False

    @contextmanager
    def transaction(self):
        store = self
        with super().transaction() as transaction:
            class Proxy:
                def __getattr__(self, name):
                    return getattr(transaction, name)

                def put(self, kind, key, data, expected_version=None):
                    if not store.failed and kind == "invocation" and data.get("status") == store.failed_status:
                        store.failed = True
                        raise RuntimeError("secret database connection")
                    return transaction.put(kind, key, data, expected_version)
            yield Proxy()


class SessionCompletionFailure:
    def __init__(self, store):
        self.store, self.armed = store, False

    @contextmanager
    def transaction(self):
        wrapper = self
        with self.store.transaction() as transaction:
            class Proxy:
                def __getattr__(self, name):
                    return getattr(transaction, name)

                def put(self, kind, key, data, expected_version=None):
                    current = transaction.get(kind, key)
                    if wrapper.armed and kind == "session" and current and current.data["status"] == "starting" and data.get("status") == "active":
                        wrapper.armed = False
                        raise RuntimeError("Simulated local completion failure")
                    return transaction.put(kind, key, data, expected_version)
            yield Proxy()


def resume_setup(store, driver=None):
    driver = driver if driver is not None else ResumableSessions()
    app = application(store, driver=driver)
    for actor_id in ["alice", "bob"]:
        app.call("add_actor", {"actor_id": actor_id})
    source_work = app.call("create_work", {"goal": "Source project"})["work"]
    destination_work = app.call("create_work", {"goal": "Destination project"})["work"]
    source = app.call("start_session", {"driver": "agent", "specification": {}, "work_id": source_work["id"], "idempotency_key": "source"}, actor="alice")["session"]
    arguments = {"driver": "agent", "specification": {}, "resume_session_id": source["id"],
        "actor_id": "bob", "work_id": destination_work["id"], "idempotency_key": "resume"}
    return app, driver, source, source_work, destination_work, arguments
