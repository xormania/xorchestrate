"""Controlled native driver and delivery setup behind application ports."""

import threading
from copy import deepcopy

from .application import Sessions, application


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
