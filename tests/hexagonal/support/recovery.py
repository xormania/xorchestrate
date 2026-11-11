"""Recovery subjects: applications sharing one durable SQLite store, recording drivers, a real project server."""
from __future__ import annotations

import itertools
import tempfile
import threading
import time
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from tests.hexagonal.support.application import application, make_store
from tests.hexagonal.support.project_server import InProcessServer
from xorchestrate.adapters.recovery import ManualRecovery, ReconnectRecovery
from xorchestrate.model import OrchestrationError


class RecordingDriver:
    """A session driver that records reconnects and sends; ``can_reconnect`` decides whether it has reconnect."""

    def __init__(self, subject, can_reconnect):
        self.subject, self.can_reconnect = subject, can_reconnect

    @property
    def handles(self):
        return self.subject.handles  # one sequence for every driver: native ids never repeat

    def start(self, specification):
        return {"handle": f"h-{next(self.handles)}", "native_id": f"n-{next(self.handles)}"}

    def send(self, session, message):
        self.subject.send_count += 1
        return {"accepted": True}

    def poll(self, session):
        return []

    def stop(self, session):
        return {}

    def close(self):
        pass

    def __getattr__(self, name):
        if name == "reconnect" and self.can_reconnect:
            return self._reconnect
        raise AttributeError(name)

    def _reconnect(self, source, specification):
        context = (specification or {}).get("context", {})
        source_id = context.get("resume_session_id") or context.get("session_id") or source.get("native_id")
        session_id = self.subject.source_of(source)
        with self.subject.lock:
            self.subject.reconnect_calls[session_id] += 1
        time.sleep(self.subject.reconnect_delay)
        if session_id in self.subject.failing:
            raise OrchestrationError("The conversation could not be reached.", "session_unavailable",
                                     {"outcome": "not_started", "source": source_id})
        return {**source, "handle": f"h-{next(self.handles)}"}


class RecoverySubject:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.database = self.directory / "state.sqlite3"
        self.send_count = 0
        self.reconnect_calls = Counter()
        self.handles = itertools.count(1)
        self.reconnect_delay = 0.0
        self.failing = set()
        self.lock = threading.Lock()
        self.native_ids = {}
        self.drivers = {"hosted": RecordingDriver(self, True), "owned": RecordingDriver(self, False)}
        self.stores = []
        # One sequence for every runtime on the store, as production's random identifiers never repeat.
        self.identifiers = itertools.count(1)

    # -- runtimes ---------------------------------------------------------------------
    # The existing session authorisation: a worker may neither reconnect nor stop the owner's sessions.
    WORKER_LIMITS = {"rules": [{"actions": ["reconnect_session", "stop_session"], "actors": ["worker"],
                                "where": {"session_actor": "owner"}, "effect": "deny"}]}

    def runtime(self, policy=None, *, rules=None):
        store = make_store("sqlite", self.database)
        self.stores.append(store)
        app = application(store, sessions=self.drivers, identifiers=lambda: f"s{next(self.identifiers)}")
        app.recovery_policy = ManualRecovery() if policy == "manual" else ReconnectRecovery()
        if rules is not None:
            from xorchestrate.adapters.policies import RuleGuardrail

            app.guardrail = RuleGuardrail(rules)
        return app

    def start(self, app, driver, actor="owner"):
        session = app.call("start_session", {"driver": driver, "specification": {},
                                             "idempotency_key": f"start-{len(self.native_ids)}"}, actor)["session"]
        self.native_ids[session["backend"]["native_id"]] = session["id"]
        app.call("send_session", {"session_id": session["id"], "message": {"text": "work"},
                                  "idempotency_key": f"send-{session['id']}"}, actor)
        return session["id"]

    def stop(self, app, session_id):
        app.call("stop_session", {"session_id": session_id, "idempotency_key": f"stop-{session_id}"})

    def _record(self, app, session_id):
        with app.store.transaction() as tx:
            return tx.get("session", session_id).data

    def status(self, app, session_id):
        return self._record(app, session_id)["status"]

    def reason(self, app, session_id):
        return self._record(app, session_id).get("disconnect_reason")

    def events(self, app, kind, session_id):
        found, after = [], 0
        while True:
            page = app.call("events", {"after": after, "limit": 100})["events"]
            if not page:
                return found
            found += [event for event in page if event.get("type") == kind and event.get("session_id") == session_id]
            after = page[-1].get("sequence", after + len(page))

    def source_of(self, backend):
        return self.native_ids.get(backend.get("native_id"))

    # -- observations and faults ------------------------------------------------------
    @property
    def reconnects(self):
        return {key: count for key, count in self.reconnect_calls.items() if count}

    @property
    def sends(self):
        return self.send_count

    def fail_reconnect(self, session_id):
        self.failing.add(session_id)

    def slow_reconnect(self, seconds):
        self.reconnect_delay = seconds

    @staticmethod
    def concurrently(calls):
        errors = []

        def run(call):
            try:
                call()
            except Exception as error:  # noqa: BLE001 - surfaced below
                errors.append(error)

        threads = [threading.Thread(target=run, args=(call,)) for call in calls]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        assert errors == [], errors

    # -- the server ---------------------------------------------------------------------
    def _server(self, app):
        return InProcessServer(app, Path(tempfile.mkdtemp(prefix="xrc-")) / "s.sock")

    def server_recovery(self, app, unclean_stop):
        server = self._server(app)
        thread = server.server.start_recovery(unclean_stop)
        if thread is None:
            return None
        thread.join(30)
        return app.recovery_status()["report"]

    def server_status(self, app, unclean_stop):
        server = self._server(app)
        thread = server.server.start_recovery(unclean_stop)
        if thread is not None:
            thread.join(30)
        return {"recovery": server.server.recovery(),
                "sessions_needing_reconnect": server.server.sessions_needing_reconnect()}

    def server_recovery_with_policy(self, app):
        """The server's automatic run, with every reconnectable session selected (as the default policy would)."""
        app.recovery_policy = ReconnectRecovery()
        server = self._server(app)
        thread = server.server.start_recovery(True)
        if thread is not None:
            thread.join(30)

    def live_replacements(self, app):
        """How many live sessions continue each original conversation (by native id)."""
        counts = Counter()
        with app.store.transaction() as tx:
            for record in tx.list("session"):
                if record.data.get("status") in {"starting", "active", "unknown"}:
                    counts[self.source_of(record.data.get("backend") or {})] += 1
        return dict(counts)

    def server_answers(self, app):
        return isinstance(app.call("list_sessions", {}), dict)

    def doctor_recovery(self):
        from xorchestrate.instance.doctor import recovery_check

        rendered = SimpleNamespace(config={"version": 1, "store": {"adapter": "sqlite",
                                                                    "options": {"path": str(self.database)}}})
        return recovery_check(rendered, self.directory, {"running": False, "unclean_stop": True})

    def one_shot_runtime(self):
        """Build a one-shot runtime from configuration (as a CLI command does) with a policy that records any use."""
        from xorchestrate.composition import build

        used = []
        policy = SimpleNamespace(select=lambda interrupted: used.append(interrupted) or [])
        config = {"version": 1, "store": {"adapter": "sqlite", "options": {"path": str(self.database)}}}
        with build(config) as runtime:
            runtime.application.recovery_policy = policy
            runtime.application.call("list_sessions", {})
        return bool(used) or bool(self.reconnects)

    def close(self):
        for store in self.stores:
            try:
                store.close()
            except Exception:  # noqa: BLE001 - best-effort teardown
                pass
