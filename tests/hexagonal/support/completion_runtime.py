"""Composition and protocol adapters for the vendor-neutral completion contract.

Only this boundary knows concrete native peers, storage, bridge transports, raw
record layout, and the independently replaceable verification subject.
"""
import hashlib
import json
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from scripts.verification.checker import verify
from scripts.verification.multi_harness import snapshot
from tests.hexagonal.contracts.completion import CompletionObservation
from tests.hexagonal.support.native import ControlledCallbacks, application_message, peer_options, wait_for
from tests.hexagonal.support.native import retry as call
from tests.hexagonal.support.operation_controls import ProcessOperation
from tests.hexagonal.support.operation_observation import OperationLog, OperationObserver
from tests.hexagonal.support.temporal_evidence import StoreOrder
from xorchestrate.adapters.bridge_host import SessionBridgeHost
from xorchestrate.adapters.claude import ClaudeSessionDriver
from xorchestrate.adapters.codex import CodexSessionDriver
from xorchestrate.adapters.grok import GrokSessionDriver
from xorchestrate.composition import build


def await_condition(predicate, reason):
    return wait_for(predicate, timeout=12, message="fixture synchronization: " + reason)


def sha256_json(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


class ControlledCalls(ControlledCallbacks):
    def __init__(self):
        super().__init__((), timeout=30, argument="label")

    def invoke(self, name, arguments, context):
        super().invoke(name, arguments, context)
        return {"label": arguments["label"], "effect": "returned"}


class NativePeer:
    def message(self, label, gates):
        return application_message(self.name, "invoke", {
            "capability": "controlled/hold", "arguments": {"label": label}}, **gates)

    def options(self, directory):
        return peer_options(self.name, timeout=10, application_tools=True)

    def terminal(self, row):
        return row["observation"].get("kind") == "native_completion"


class CodexPeer(NativePeer):
    name = "codex"
    activity_source = "codex"
    driver = CodexSessionDriver


class GrokPeer(NativePeer):
    name = "grok"
    activity_source = "grok"
    driver = GrokSessionDriver

    def options(self, directory):
        return {**super().options(directory), "resume_supported": True}


class ClaudePeer(NativePeer):
    name = "claude"
    activity_source = "claude/Stop"
    driver = ClaudeSessionDriver

    def options(self, directory):
        return peer_options(self.name, state=directory / "claude", timeout=10,
                            application_tools=True, startup_timeout=10)

    def terminal(self, row):
        return row["observation"].get("kind") == "turn_completed"


class CheckerCompletionSubject:
    def is_complete(self, evidence):
        return verify(evidence["run"], evidence["criteria"], evidence["bindings"],
                      evidence["fixture"])["status"] == "passed"


class ComposedCompletionFixture:
    def __init__(self, application, peer, directory, read_activity=None):
        self.app, self.peer, self.directory = application, peer, directory
        self.control = ControlledCalls()
        self.app.providers["controlled"] = self.control
        call(self.app, "add_actor", {"actor_id": peer.name})
        work = call(self.app, "create_work", {"goal": "Independently controlled native completion"})["work"]
        self.session = call(self.app, "start_session", {"driver": "native", "specification": {},
            "actor_id": peer.name, "work_id": work["id"], "idempotency_key": "contract-session"})["session"]
        self.adapter = self.app.sessions["native"]
        self.read_activity = read_activity or (lambda: self.adapter.status(self.session["backend"])["activity"])
        self.gates = {}
        self.operations, self.completions = {}, {}
        self.observed_terminals = set()
        self.native_log = OperationLog(self.session["backend"]["native_id"], self.session["backend"]["incarnation"])
        self.native_observer = OperationObserver(self._native_observations)
        self.capture_sequence = 0

    def submit(self, label, *, callback=False, start=False, tail=False):
        if label in self.operations:
            raise ValueError("operation already submitted: " + label)
        self.control.prepare(label, callback)
        operation = ProcessOperation(self.directory, self.native_log.source, self.native_log.incarnation, label)
        self.operations[label] = operation
        gates = dict(operation.arguments)
        for stage, held in (("start", start), ("tail", tail)):
            if held:
                gates.update(operation.hold(stage).arguments)
        self.gates[label] = gates
        # The cursor is captured before delivery, including synchronous peers.
        self.completions[label] = self.native_observer.arm(
            self.native_log.source, self.native_log.incarnation, label, "completed")
        call(self.app, "send_session", {"session_id": self.session["id"],
            "message": self.peer.message(label, gates), "idempotency_key": label})
        if start:
            self._await_gate(label, "start")
        elif callback:
            self.control.tickets[label].wait(timeout=12)
        elif tail:
            self._await_gate(label, "tail")

    def _await_gate(self, label, stage):
        await_condition(self.operations[label].boundaries[stage].entered, label + " " + stage + " entered")

    def activation(self, label, stage):
        if stage == "callback":
            return self.control.activation(label)
        return self.operations[label].boundaries[stage].activation()

    def _native_observations(self):
        # Poll remains the public transport. The local journal's counter means
        # capture order only; exact native identity is the completion witness.
        self.adapter.poll(self.session["backend"])
        bindings = {}
        for label, operation in self.operations.items():
            activation = operation.activation()
            if activation is not None:
                bindings[activation["native_operation"]] = label
        raw = snapshot(self.app)
        rows = {row["id"]: row for row in raw["observations"]}
        # Import the persisted observation prefix using its store witnesses,
        # independent of record-list sorting and diagnostic timestamps.
        for event in sorted(raw["ordering"]["events"], key=lambda event: event["seq"]):
            if event.get("type") != "native_observation.created":
                continue
            identifier = event["record_id"]
            if identifier in self.observed_terminals or identifier not in rows:
                continue
            row, observation = rows[identifier], rows[identifier]["observation"]
            native_operation = observation.get("turn_id", observation.get("prompt_id"))
            if (not self.peer.terminal(row) or row.get("session_id") != self.session["id"]
                    or observation.get("native_id") != self.native_log.source
                    or observation.get("incarnation") != self.native_log.incarnation
                    or native_operation not in bindings):
                continue
            self.native_log.record(bindings[native_operation], "completed", {
                "native_operation": native_operation, "observation": observation,
                "record_id": identifier, "store_sequence": event["seq"]})
            self.observed_terminals.add(identifier)
        return self.native_log.snapshot()

    def completion(self, label):
        return self.completions[label].poll()

    def release_callback(self, label):
        self.control.release_operation(label)
        if "tail_gate" in self.gates[label]:
            self._await_gate(label, "tail")
        else:
            await_condition(self.control.calls[label][2].is_set, label + " callback returned")

    def _release_gate(self, label, stage):
        self.operations[label].boundaries[stage].release()
        if stage == "start" and not self.control.release[label].is_set():
            self.control.tickets[label].wait(timeout=12)
        elif stage == "start" and "tail" in self.operations[label].boundaries:
            self._await_gate(label, "tail")
        else:
            self.completions[label].wait(timeout=12)

    def release_start(self, label):
        self._release_gate(label, "start")

    def release_tail(self, label):
        self._release_gate(label, "tail")

    def _held(self, stage):
        return frozenset(label for label, gates in self.gates.items() if stage + "_gate" in gates
                         and not Path(gates[stage + "_gate"]).exists())

    def _evidence(self):
        self.adapter.poll(self.session["backend"])
        identity = {key: self.session["backend"][key] for key in ("native_id", "incarnation")}
        identity["session_id"] = self.session["id"]
        criteria = {"required_cases": [{"id": "completion", "kind": "completion", "peer": self.peer.name}]}
        fixture = {"batches": []}
        bindings = {"criteria_sha256": sha256_json(criteria), "fixture_sha256": sha256_json(fixture)}
        return {"criteria": criteria, "fixture": fixture, "bindings": bindings, "run": {
            "mode": "protocol-fixture", "raw_records": snapshot(self.app), "sessions": {self.peer.name: identity},
            "bindings": bindings, "executed_cases": ["completion"]}}

    def capture(self):
        observed = CompletionObservation(
            activity=self.read_activity(),
            held_callbacks=frozenset(label for label, (_, release, _) in self.control.calls.items() if not release.is_set()),
            held_starts=self._held("start"), held_tails=self._held("tail"),
            returned_callbacks=frozenset(label for label, (_, _, returned) in self.control.calls.items() if returned.is_set()),
            evidence=self._evidence())
        self.capture_sequence += 1
        controls = {label: {"operation": operation.activation(),
                            "callback": self.control.activation(label),
                            "boundaries": {stage: boundary.activation()
                                           for stage, boundary in operation.boundaries.items()}}
                    for label, operation in self.operations.items()}
        (self.directory / ("capture-%02d.json" % self.capture_sequence)).write_text(json.dumps({
            "activity": observed.activity, "held_callbacks": sorted(observed.held_callbacks),
            "held_starts": sorted(observed.held_starts), "held_tails": sorted(observed.held_tails),
            "returned_callbacks": sorted(observed.returned_callbacks), "controls": controls,
            "evidence": observed.evidence}, indent=2))
        return observed

    def with_false_idle(self, observation):
        evidence = deepcopy(observation.evidence)
        original = deepcopy(evidence["run"]["raw_records"])
        identity = evidence["run"]["sessions"][self.peer.name]
        at = datetime.now(timezone.utc).isoformat()
        evidence["run"]["raw_records"]["observations"].append({
            "id": "controlled-false-idle-" + uuid4().hex, "session_id": identity["session_id"],
            "origin": "adapter", "stale": False, "created_at": at, "created_by": self.peer.name, "observation": {
                **identity, "kind": "activity", "source": self.peer.activity_source, "at": at, "state": "idle"}})
        StoreOrder(evidence["run"]["raw_records"]).created("native_observation", evidence["run"]["raw_records"]["observations"][-1])
        unchanged = deepcopy(evidence["run"]["raw_records"])
        unchanged["observations"].pop()
        unchanged["ordering"]["events"].pop()
        assert unchanged == original, "negative control must preserve every original raw record"
        (self.directory / ("capture-%02d-false-idle.json" % self.capture_sequence)).write_text(json.dumps(evidence, indent=2))
        return evidence

    def with_completion_claim(self, observation, claim, value, *, absent=False):
        evidence = self.with_false_idle(observation)
        raw = evidence["run"]["raw_records"]
        if claim in {"native_actor", "source_work"}:
            target = raw["observations"][-1]
            field = {"native_actor": "created_by", "source_work": "work_id"}[claim]
        else:
            session = next(row for row in raw["sessions"] if row["id"] == self.session["id"])
            target = session if claim == "recorded_actor" else session["backend"]
            field = {"recorded_actor": "actor_id", "recorded_native": "native_id", "recorded_incarnation": "incarnation"}[claim]
        if absent:
            target.pop(field, None)
        else:
            target[field] = value
        name = claim + ("-absent" if absent else "-null" if value is None else "-conflicting")
        (self.directory / ("claim-" + name + ".json")).write_text(json.dumps(evidence, indent=2))
        return evidence

    def await_idle(self):
        await_condition(lambda: self.read_activity() == "idle", "live idle")

    def close(self):
        self.control.close()
        for operation in self.operations.values():
            operation.close()


@contextmanager
def composed_completion(peer_type, directory, store="memory", transport="direct"):
    directory.mkdir(parents=True, exist_ok=True)
    peer = peer_type()
    options = peer.options(directory)
    config = {"store": {"adapter": store, "options": {"path": str(directory / "state.sqlite")} if store == "sqlite" else {}},
              "sessions": {"native": {"adapter": peer.name, "options": options}}}
    with ExitStack() as stack:
        read_activity = None
        if transport == "fifo":
            with build() as catalogue:
                capabilities = catalogue.application.describe()
            host = SessionBridgeHost(peer.driver({key: value for key, value in options.items() if key != "application_tools"}),
                                     directory / "host", capabilities=capabilities, options={"transport": "fifo"})
            stack.callback(host.close)
            # The bridge port has inspect/poll, not a status extension. Observe
            # its owned native driver's public status using the host receipt.
            def read_activity():
                return host.driver.status(host.backend)["activity"]
            config["sessions"]["native"] = {"adapter": "bridge", "options": {
                "endpoint": str(host.endpoint), "transport": "fifo", "timeout": 10, "application_tools": True}}
        runtime = stack.enter_context(build(config, Path.cwd()))
        fixture = ComposedCompletionFixture(runtime.application, peer, directory, read_activity)
        stack.callback(fixture.close)
        yield fixture
