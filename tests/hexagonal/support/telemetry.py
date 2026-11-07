"""Telemetry subjects composed through the existing runtime and native fixtures."""

import base64
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

from tests.hexagonal.support.application import Provider, Sessions, application, invoke
from tests.hexagonal.support.native import ControlledCallbacks, application_message, peer_options, wait_for
from xorchestrate.adapters.policies import RuleGuardrail
from xorchestrate.composition import build
from xorchestrate.model import OrchestrationError


def contains(value, expected):
    if value == expected:
        return True
    if isinstance(value, dict):
        return any(contains(item, expected) for item in value.values())
    if isinstance(value, list):
        return any(contains(item, expected) for item in value)
    return False


class FailingCapture:
    def __init__(self, mode):
        self.mode, self.calls = mode, 0

    def new_id(self):
        return "independent-telemetry-attempt"

    def record(self, *args, **kwargs):
        self.calls += 1
        if self.mode == "throws":
            raise RuntimeError("optional telemetry failed")
        return {"status": self.mode, "receipt": None, "reason": "controlled pressure"}


class ChangingGuardrail:
    def __init__(self, app, work):
        self.app, self.work, self.armed = app, work, True

    def evaluate(self, action, context):
        if self.armed and action.startswith("telemetry") and context.get("work_id") == self.work:
            self.armed = False
            with self.app.store.transaction() as transaction:
                current = transaction.get("work", self.work)
                transaction.put("work", current.key, {**current.data, "changed": True}, current.version)
        return RuleGuardrail().evaluate(action, context)


class Fixture:
    def __init__(self, root, *, local_options=None, **configuration):
        self.root = root
        self.configuration = {
            "store": {"adapter": "memory"},
            "telemetry": {"adapter": "local", "options": {
                "runtime_root": str(root / "corpus"), "project_id": "project-tests",
                "dataset_id": "dataset-tests", "collector_id": "collector-tests",
                "sources": ["application", "native", "capability", "evaluator", "process", "fixture"],
                "known_secrets": ["configured-secret"], **(local_options or {}),
            }},
            **deepcopy(configuration),
        }
        self.runtime = None

    @property
    def app(self):
        if self.runtime is not None:
            return self.runtime.application
        try:
            self.runtime = build(self.configuration, self.root)
        except OrchestrationError as error:
            if error.code == "configuration_error" and "telemetry" in error.details.get("sections", []):
                raise AssertionError("TELEMETRY_COMPOSITION_MUST_ACCEPT_EXPLICIT_LOCAL_CAPTURE") from error
            raise
        return self.runtime.application

    def close(self):
        if self.runtime is not None:
            self.runtime.close()

    def actions(self):
        return {capability.name for capability in self.app.describe()}

    def create_work(self, goal):
        return self.app.call("create_work", {"goal": goal})["work"]["id"]

    def record_result(self, work, result):
        return self.app.call("record_result", {"work_id": work, "result": result})

    def events(self, **filters):
        return self.event_page(**filters)["events"]

    def event_page(self, *, actor="owner", **filters):
        return self.app.call("telemetry_events", {"limit": 1000, **filters}, actor=actor)

    def payload(self, event_id, *, actor="owner", **arguments):
        return self.app.call("telemetry_payload", {"event_id": event_id, **arguments}, actor=actor)

    def payload_bytes(self, part):
        if "data_base64" in part:
            data = base64.b64decode(part["data_base64"], validate=True)
            if part["content"] is not None:
                assert data == part["content"].encode("utf-8")
            return data
        return part["content"].encode("utf-8")

    def usage(self, **arguments):
        return self.app.call("telemetry_usage", arguments)

    def status(self, *, actor="owner"):
        return self.app.call("telemetry_status", {}, actor=actor)

    def capture(self, event_type, payload, context=None, **identity):
        return self.app.telemetry.record(event_type, payload, {"actor": "owner", **(context or {})}, **identity)

    def artifact_reference(self, event_id):
        return deepcopy(self.app.telemetry.get_event(event_id)["artifacts"][0])

    def reopen(self):
        self.close()
        self.runtime = None

    def restrict_work(self, work):
        self.app.guardrail = RuleGuardrail({"rules": [{"actions": ["telemetry*"],
            "actors": ["observer"], "where": {"work.id": work}, "effect": "deny"}]})

    def mixed_snapshot(self, public, private):
        self.app.sessions["agent"] = Sessions()
        session = self.app.call("start_session", {"driver": "agent", "specification": {"content": "private canary"},
            "work_id": private, "idempotency_key": "private-session"})["session"]
        work = self.app.call("get_work", {"work_id": public})["work"]
        return {"action": "get_work", "arguments": {"work_id": public},
            "result": {"work": work, "sessions": [session]}}

    def payload_denied(self, event_id, actor):
        try:
            self.payload(event_id, actor=actor)
        except OrchestrationError as error:
            return error.code in {"denied", "not_found"}
        return False

    def change_during_read(self, work):
        self.app.guardrail = ChangingGuardrail(self.app, work)

    def read_failure(self, boundary, event_id):
        try:
            if boundary == "events":
                self.event_page()
            else:
                self.payload(event_id)
        except OrchestrationError as error:
            return error.code
        return None

    def noninterference(self, mode, outcome):
        capture = None if mode == "disabled" else FailingCapture(mode)
        observed_provider = Provider()
        control_provider = Provider()
        observed = application(provider=observed_provider)
        control = application(provider=control_provider)
        observed.telemetry = capture

        def exercise(app, provider):
            failure = SystemExit(37) if outcome == "exit" else RuntimeError("source failure")
            if outcome != "return":
                def callback(*args):
                    raise failure
                provider.callback = callback
            else:
                provider.callback = lambda *args: {"stdout": "original\nbytes", "stderr": "exact diagnostic", "exit_code": 0}
            try:
                result = invoke(app)
            except BaseException as error:
                assert error is failure or error.__cause__ is failure, "Original failure identity must remain reachable"
                return {"error": type(error).__name__, "args": error.args,
                    "code": getattr(error, "code", None), "details": getattr(error, "details", None)}
            assert invoke(app) == result, "Replayed operation returns the original result"
            return result

        try:
            expected = exercise(control, control_provider)
            actual = exercise(observed, observed_provider)
            return {"control": expected, "observed": actual, "calls": len(observed_provider.calls),
                "capture_calls": capture.calls if capture is not None else 0}
        finally:
            observed.store.close()
            control.store.close()

    def concurrent_calls(self):
        callbacks = ControlledCallbacks()
        self.app.providers = {"tools": callbacks}
        identities = {label: {"work_id": self.create_work("work " + label), "actor": "actor-" + label}
            for label in ("A", "B")}

        def run(label):
            return self.app.call("invoke", {"capability": "tools/hold", "arguments": {"nonce": label},
                "idempotency_key": label, "work_id": identities[label]["work_id"]}, actor=identities[label]["actor"])

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(run, "A")
            try:
                assert callbacks.entered["A"].wait(5)
                second = executor.submit(run, "B")
                assert callbacks.entered["B"].wait(5)
                callbacks.release["B"].set()
                second.result(timeout=5)
            finally:
                callbacks.close()
            first.result(timeout=5)
        return self.events(), identities

    def native_episode(self):
        work = self.create_work("native semantic capture")
        session = self.app.call("start_session", {"driver": "native", "specification": {},
            "work_id": work, "idempotency_key": "native-start"})["session"]
        message = application_message("codex", "record_result", {"result": {"native": "native rich result"}})
        self.app.call("send_session", {"session_id": session["id"], "message": message,
            "idempotency_key": "native-send"})
        wait_for(lambda: any(contains(row["payload"], "native rich result")
            and row["event_type"] == "application.completed" for row in self.events(work_id=work)))
        return session, self.events(work_id=work)

    def configuration_states(self):
        results = []
        for name, configuration in (("disabled", {"telemetry": {"enabled": False}}),
                ("invalid", {"telemetry": {"adapter": "local", "options": {"profile": "missing.toml"}}}),
                ("empty", {})):
            subject = Fixture(self.root / name, **configuration)
            try:
                subject.create_work("unrelated action remains available") if name != "empty" else None
                results.append(subject.app.call("telemetry_status", {}))
            finally:
                subject.close()
        return results

    def prepare_denied_prefix(self):
        private, public = self.create_work("denied prefix"), self.create_work("allowed suffix")
        self.app.guardrail = RuleGuardrail({"rules": [{"actions": ["telemetry*", "get_work"],
            "where": {"work.id": private}, "effect": "deny"}]})
        sink = self.app.telemetry
        original_status = sink.status
        horizon = 20001

        def query(*, after=0, limit=100, **filters):
            return [{"event_id": "allowed-after-denied-prefix" if index == horizon else f"denied-{index}",
                "sequence": index, "event_type": "fixture.scan", "payload": {"index": index},
                "context": {"work_id": public if index == horizon else private}}
                for index in range(after + 1, min(after + limit, horizon) + 1)]

        sink.query = query
        sink.status = lambda: {**original_status(), "max_sequence": horizon}

    def existing_scope(self, kind):
        if kind == "work":
            return self.create_work("deleted source")
        if kind == "session":
            self.app.sessions["agent"] = Sessions()
            return self.app.call("start_session", {"driver": "agent", "specification": {},
                "idempotency_key": "deleted-session"})["session"]["id"]
        self.app.providers["tools"] = Provider()
        return invoke(self.app, key="deleted-operation")["operation_id"]

    def delete_scope(self, kind, identity):
        with self.app.store.transaction() as transaction:
            record = transaction.get(kind, identity)
            transaction.delete(kind, identity, record.version)

    def reject_early_request(self, goal):
        try:
            self.app.call("create_work", {"goal": goal, "unexpected": True})
        except OrchestrationError as error:
            assert error.code == "invalid_arguments"
            return
        raise AssertionError("The malformed request must fail before creating work")

    contains = staticmethod(contains)


def native_fixture(root):
    return Fixture(root, sessions={"native": {"adapter": "codex", "options": peer_options("codex", application_tools=True)}})

