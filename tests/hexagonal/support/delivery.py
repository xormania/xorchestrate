"""Controlled native driver and delivery setup behind application ports."""

import threading
from copy import deepcopy
from itertools import count

from .application import Sessions, application, make_store


class Native(Sessions):
    def __init__(self):
        super().__init__()
        self.count = 0
        self.failure = None
        self.entered = threading.Event()
        self.release = None

    def start(self, specification):
        self.count += 1
        return {"native_id": "conversation", "incarnation": str(self.count), "ownership": "owned"}

    def deliver(self, session, message):
        self.calls.append(("deliver", deepcopy(session), deepcopy(message)))
        self.entered.set()
        if self.release:
            assert self.release.wait(3)
        if self.failure:
            raise self.failure
        return {"submitted": True}


def dispatch(app, message):
    return app.call('dispatch_deliveries', {'message_id': message['id']})['deliveries'][0]


class SessionSelectionFixture:
    """Real delivery consumer with controlled UTC labels and store reconstruction."""

    def __init__(self, storage, path):
        self.storage, self.path = storage, path
        self.store = make_store(storage, path)
        self.driver = Native()
        self.now = "2026-11-12T12:00:00Z"
        self.identifiers = count(99999, -1)
        self.app = self._application()
        self.app.call("add_actor", {"actor_id": "bob"})

    def _application(self):
        return application(self.store, driver=self.driver, clock=lambda: self.now,
                           identifiers=lambda: f"r{next(self.identifiers):05d}")

    def close(self):
        self.store.close()

    def reconstruct(self):
        if self.storage == "sqlite":
            self.store.close()
            self.store = make_store(self.storage, self.path)
        self.app = self._application()

    def start(self, label):
        return self.app.call("start_session", {"driver": "agent", "actor_id": "bob",
            "specification": {}, "idempotency_key": label})["session"]

    def dispatch(self):
        message = self.app.call("send_message", {"destination": {"actor": "bob"},
            "body": {"text": "selection witness"}})["message"]
        return dispatch(self.app, message)

    def emit(self, event, repetitions=1):
        with self.store.transaction() as tx:
            for _ in range(repetitions):
                tx.emit(event)

    def creation_sequences(self):
        # Used only by the small provenance scenario, below one history page.
        with self.store.transaction() as tx:
            return {event["record_id"]: event["seq"] for event in tx.events(limit=1000)
                    if event.get("type") == "session.created"}

    def legacy_session(self, source_id, key, created_at, claimed_sequence):
        with self.store.transaction() as tx:
            source = tx.get("session", source_id)
            tx.put("session", key, {**source.data, "created_at": created_at,
                                    "creation_sequence": claimed_sequence})

    def stop(self, session_id):
        self.app.call("stop_session", {"session_id": session_id, "idempotency_key": "stop"})


class CapturingSessionSelection:
    def __init__(self, selected):
        self.selected = selected
        self.candidates = []

    def select(self, message, recipient, sessions):
        self.candidates = deepcopy(sessions)
        return self.selected


class Driver:
    def __init__(self):
        self.starts = []
        self.callback = None
        self.entered = threading.Event()
        self.release = threading.Event()
        self.send_error = None

    def start(self, specification):
        self.starts.append(specification)
        return {"native_id": "conversation", "incarnation": "generation-1", "handle": "connection"}

    def bind_application_tools(self, capabilities, callback):
        self.callback = callback

    def send(self, backend, message):
        self.entered.set()
        assert self.release.wait(2)
        if self.send_error:
            raise self.send_error
        return {"submitted": True}

    def stop(self, backend):
        return {"stopped": True}

    def detach(self, backend):
        return {"detached": True}

    def poll(self, backend):
        return []


def adversarial_application(driver, *, guardrail=None, fallbacks=None, receipt_policy=None):
    return application(driver=driver, guardrail=guardrail,
                       session_fallbacks=fallbacks, receipt_policy=receipt_policy)


def start(app):
    return app.call("start_session", {"driver": "agent", "specification": {}, "idempotency_key": "start"})["session"]
