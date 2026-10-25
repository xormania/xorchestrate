"""Composed verification provider and explicitly synthetic session fixture.

The work fixture/mechanism is adapted from multi-harness-proof a944f31b7527807d.
The deterministic driver exercises application composition, callbacks, storage,
delivery, and the checker. It does not claim vendor protocol or native coverage.
"""
from __future__ import annotations

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from xorchestrate.model import Capability, OrchestrationError

from .checker import expected_work


def utc():
    return datetime.now(timezone.utc).isoformat()


class VerificationProvider:
    def __init__(self, options):
        self.fixture = json.loads(Path(options["fixture"]).read_text())
        self.hold_timeout = options.get("hold_timeout", 30)
        self.events = []
        self.holds = {}
        self.progress = {}
        self.released = 0
        self.lock = threading.RLock()

    def emit(self, event, peer, **value):
        with self.lock:
            self.events.append({"event": event, "peer": peer, "at": utc(), **deepcopy(value)})

    def discover(self):
        def descriptor(name, properties, required):
            return Capability(name, "Local acceptance fixture: " + name,
                {"type": "object", "properties": properties, "required": required, "additionalProperties": False})
        return [
            descriptor("hold", {"case_id": {"type": "string"}}, ["case_id"]),
            descriptor("work_next", {}, []),
            descriptor("work_submit", {"batch": {"type": "integer"}, "failed_jobs": {"type": "array", "items": {"type": "string"}},
                                      "failed_tests": {"type": "integer"}}, ["batch", "failed_jobs", "failed_tests"]),
        ]

    def invoke(self, name, arguments, context):
        peer = context["actor"]
        if name == "hold":
            key = (peer, arguments["case_id"])
            with self.lock:
                if key in self.holds:
                    raise OrchestrationError("Hold already exists", "fixture_conflict")
                self.holds[key] = threading.Event()
            self.emit("hold_started", peer, case_id=arguments["case_id"])
            released = self.holds[key].wait(self.hold_timeout)
            self.emit("hold_finished", peer, case_id=arguments["case_id"], released=released)
            if not released:
                raise OrchestrationError("Hold deadline expired", "fixture_timeout")
            return {"released": True}
        with self.lock:
            if name == "work_next":
                if peer not in self.progress:
                    self.progress[peer] = {"batch": 0, "pending": False}
                    self.emit("work_started", peer)
                state = self.progress[peer]
                batch = state["batch"]
                if state["pending"] or batch > self.released:
                    raise OrchestrationError("Batch is pending or gated", "fixture_conflict")
                if batch >= len(self.fixture["batches"]):
                    return {"done": True}
                state["pending"] = True
                return {"done": False, "batch": batch, "rows": deepcopy(self.fixture["batches"][batch])}
            if name == "work_submit":
                state = self.progress.get(peer)
                if not state or not state["pending"] or state["batch"] != arguments["batch"]:
                    raise OrchestrationError("No pending batch", "fixture_conflict")
                self.emit("work_submitted", peer, batch=arguments["batch"],
                          answer={k: arguments[k] for k in ("failed_jobs", "failed_tests")})
                state.update(batch=state["batch"] + 1, pending=False)
                if state["batch"] == len(self.fixture["batches"]):
                    self.emit("work_completed", peer)
                return {"recorded": True}
        raise OrchestrationError("Unknown fixture capability", "not_found")

    def release_hold(self, peer, case_id):
        self.holds[(peer, case_id)].set()

    def release_batch(self, batch):
        with self.lock:
            if batch != self.released + 1:
                raise ValueError("Batches must be released in order")
            self.released = batch

    def snapshot(self):
        with self.lock:
            return deepcopy(self.events)

    def close(self):
        with self.lock:
            for event in self.holds.values():
                event.set()


class SyntheticSessionDriver:
    """Synthetic session port; its name and receipts never claim a native run."""
    def __init__(self, options):
        self.peer = options["peer"]
        self.observer = None
        self.callback = None
        self.sessions = {}
        self.pool = ThreadPoolExecutor(max_workers=8)
        self.configuration_id = "verification-fixture-v1-" + self.peer
        self.errors = []

    def bind_observer(self, observer):
        self.observer = observer

    def bind_application_tools(self, capabilities, callback):
        self.callback = callback

    def _emit(self, live, kind, **fields):
        self.observer(live["context"], {"native_id": live["native_id"], "incarnation": live["incarnation"],
                      "source": "fixture", "kind": kind, "at": utc(), **fields})

    def start(self, specification):
        context = deepcopy(specification.get("context", {}))
        live = {"native_id": "fixture-" + uuid.uuid4().hex, "incarnation": uuid.uuid4().hex,
                "context": context, "memory": None, "activity": "idle", "ownership": "owned", "pending": 0,
                "closed": False, "lock": threading.RLock()}
        self.sessions[live["native_id"]] = live
        return {k: live[k] for k in ("native_id", "incarnation", "ownership")} | {
            "protocol": "fixture", "connection_state": "connected", "settings": {"requested": {}, "resolved": {}, "observed": {}},
            "configuration_id": self.configuration_id}

    def _live(self, backend):
        live = self.sessions[backend["native_id"]]
        if live["incarnation"] != backend["incarnation"] or live["closed"]:
            raise OrchestrationError("Stale fixture connection", "stale_session")
        return live

    def resume(self, backend, specification):
        live = self.sessions[backend["native_id"]]
        live.update(incarnation=uuid.uuid4().hex, context=deepcopy(specification.get("context", {})), closed=False)
        return {**deepcopy(backend), "incarnation": live["incarnation"], "connection_state": "connected"}

    def reconnect(self, backend, specification):
        return self.resume(backend, specification)

    def _call(self, live, action, args):
        call_id = uuid.uuid4().hex
        self._emit(live, "native_tool_call", call_id=call_id, action=action, arguments=args)
        context = {**live["context"], "native_id": live["native_id"], "incarnation": live["incarnation"], "call_id": call_id}
        return self.callback(action, args, context, call_id)

    def _run(self, live, function):
        with live["lock"]:
            live["pending"] += 1
            live["activity"] = "active"
        try:
            function()
        except BaseException as exc:
            self.errors.append(exc)
            self._emit(live, "fixture_error", error=type(exc).__name__, detail=str(exc))
        finally:
            with live["lock"]:
                live["pending"] -= 1
                live["activity"] = "active" if live["pending"] else "idle"
                self._emit(live, "native_completion", stale=False, activity=live["activity"])

    def send(self, backend, message):
        live = self._live(backend)
        text = message["text"]
        instruction = json.loads(text.split("VERIFICATION_COMMAND=", 1)[1].splitlines()[0])
        def execute():
            kind = instruction["kind"]
            if kind == "startup":
                live["memory"] = instruction["memory"]
                self._call(live, "record_result", {"result": {"verification": {
                    "run_id": instruction["run_id"], "phase": "ready", "memory": live["memory"]}}})
            elif kind in {"send", "send_many"}:
                for message in instruction.get("messages", [instruction]):
                    self._call(live, "send_message", {"destination": {"actor": message["target"]},
                        "body": {"verification": message["payload"]}})
            elif kind == "hold":
                self._call(live, "invoke", {"capability": "verification/hold", "arguments": {"case_id": instruction["case_id"]}})
            elif kind == "lifecycle_probe":
                self._call(live, "record_result", {"result": {"verification": {
                    "run_id": instruction["run_id"], "phase": "lifecycle", "case_id": instruction["case_id"],
                    "memory": live["memory"]}}})
            elif kind == "work":
                result = self._call(live, "invoke", {"capability": "verification/work_next", "arguments": {}})
                rows = result["invocation"]["result"]
                self._call(live, "invoke", {"capability": "verification/work_submit",
                    "arguments": {"batch": rows["batch"], **expected_work(rows["rows"])}})
        self.pool.submit(self._run, live, execute)
        return {"submitted": True}

    def deliver(self, backend, message):
        live = self._live(backend)
        payload = deepcopy(message["body"]["verification"])
        def execute():
            if payload["kind"] == "challenge":
                self._call(live, "send_message", {"destination": {"actor": message["sender"]},
                    "body": {"verification": {**payload, "kind": "reply", "memory": live["memory"]}}})
            else:
                receipt = {k: payload[k] for k in ("run_id", "case_id", "nonce", "memory")}
                self._call(live, "record_result", {"result": {"verification": {
                    **receipt, "phase": "received", "peer": message["sender"]}}})
        self.pool.submit(self._run, live, execute)
        return {"submitted": True, "native_id": live["native_id"], "incarnation": live["incarnation"]}

    def poll(self, backend):
        self._live(backend)
        if self.errors:
            raise self.errors.pop(0)
        return []

    def stop(self, backend):
        live = self._live(backend)
        live["closed"] = True
        return {"stopped": True}

    def close(self):
        self.pool.shutdown(wait=True)
