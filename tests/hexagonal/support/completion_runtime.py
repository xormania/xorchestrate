"""Composition and protocol adapters for the vendor-neutral completion contract.

Only this boundary knows concrete native peers, storage, bridge transports, raw
record layout, and the independently replaceable verification subject.
"""
import hashlib
import json
import sys
import time
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from uuid import uuid4

from scripts.verification.checker import verify
from tests.hexagonal.contracts.completion import CompletionObservation
from xorchestrate.adapters.bridge import SessionBridgeHost
from xorchestrate.adapters.claude import ClaudeSessionDriver
from xorchestrate.adapters.grok import GrokSessionDriver
from xorchestrate.adapters.sessions import CodexSessionDriver
from xorchestrate.composition import build
from xorchestrate.model import Capability, Conflict

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def await_condition(predicate, reason):
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError("fixture synchronization: " + reason)


def call(app, action, arguments):
    for attempt in range(8):
        try:
            return app.call(action, arguments)
        except Conflict as error:
            if not error.details.get("retry_safe") or attempt == 7:
                raise


def snapshot(app):
    kinds = {"sessions": "session", "messages": "message", "attempts": "delivery_attempt",
             "observations": "native_observation", "evidence": "evidence", "operations": "operation",
             "receipts": "delivery_receipt", "native_calls": "native_call"}
    with app.store.transaction() as tx:
        return {key: [{**deepcopy(row.data), "id": row.key, "version": row.version}
                      for row in tx.list(kind)] for key, kind in kinds.items()}


def sha256_json(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


class ControlledCalls:
    def __init__(self):
        self.calls = {}

    def discover(self):
        return [Capability("hold", "Controlled callback completion", {"type": "object"})]

    def prepare(self, label, held):
        entered, release, returned = Event(), Event(), Event()
        if not held:
            release.set()
        self.calls[label] = (entered, release, returned)

    def invoke(self, name, arguments, context):
        label = arguments["label"]
        entered, release, returned = self.calls[label]
        entered.set()
        assert release.wait(30), "test must release its controlled callback"
        returned.set()
        return {"label": label, "effect": "returned"}

    def close(self):
        for _, release, _ in self.calls.values():
            release.set()


class NativePeer:
    def message(self, label, gates):
        body = {"action": "tool", "name": "invoke", "arguments": {
            "capability": "controlled/hold", "arguments": {"label": label}}, **gates}
        return {"text": json.dumps(body)}

    def options(self, directory):
        return {"command": [sys.executable, str(FIXTURES / "native_progress_peer.py"), self.name],
                "timeout": 10, "application_tools": True}

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
        return {"command": [sys.executable, str(FIXTURES / "claude_native.py")],
                "env": {"CLAUDE_STATE_DIR": str(directory / "claude")},
                "timeout": 10, "application_tools": True, "terminal": "discard", "startup_timeout": 10}

    def message(self, label, gates):
        return {"application_call": {"action": "invoke", "arguments": {
            "capability": "controlled/hold", "arguments": {"label": label}}}, **gates}

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
        self.capture_sequence = 0

    def submit(self, label, *, callback=False, start=False, tail=False):
        self.control.prepare(label, callback)
        gates = {}
        for stage, held in (("start", start), ("tail", tail)):
            if held:
                gates[stage + "_gate"] = str(self.directory / (label + "." + stage + ".release"))
                gates[stage + "_entered"] = str(self.directory / (label + "." + stage + ".entered"))
        self.gates[label] = gates
        call(self.app, "send_session", {"session_id": self.session["id"],
            "message": self.peer.message(label, gates), "idempotency_key": label})
        if start:
            self._await_gate(label, "start")
        elif callback:
            await_condition(self.control.calls[label][0].is_set, label + " callback entered")
        elif tail:
            self._await_gate(label, "tail")

    def _await_gate(self, label, stage):
        await_condition(lambda: Path(self.gates[label][stage + "_entered"]).exists(), label + " " + stage + " entered")

    def _terminal_identities(self):
        # Poll is the session port's public observation transport. In a bridge,
        # callback evidence is pushed but ordinary native events are polled.
        self.adapter.poll(self.session["backend"])
        return frozenset(row["observation"].get("turn_id", row["observation"].get("prompt_id"))
                         for row in snapshot(self.app)["observations"] if self.peer.terminal(row))

    def release_callback(self, label):
        self.control.calls[label][1].set()
        if "tail_gate" in self.gates[label]:
            self._await_gate(label, "tail")
        else:
            await_condition(self.control.calls[label][2].is_set, label + " callback returned")

    def _release_gate(self, label, stage):
        before = self._terminal_identities()
        Path(self.gates[label][stage + "_gate"]).touch()
        await_condition(lambda: self._terminal_identities() - before, label + " native terminal observed")

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
        (self.directory / ("capture-%02d.json" % self.capture_sequence)).write_text(json.dumps({
            "activity": observed.activity, "held_callbacks": sorted(observed.held_callbacks),
            "held_starts": sorted(observed.held_starts), "held_tails": sorted(observed.held_tails),
            "returned_callbacks": sorted(observed.returned_callbacks), "evidence": observed.evidence}, indent=2))
        return observed

    def with_false_idle(self, observation):
        evidence = deepcopy(observation.evidence)
        original = deepcopy(evidence["run"]["raw_records"])
        identity = evidence["run"]["sessions"][self.peer.name]
        at = datetime.now(timezone.utc).isoformat()
        evidence["run"]["raw_records"]["observations"].append({
            "id": "controlled-false-idle-" + uuid4().hex, "session_id": identity["session_id"],
            "origin": "adapter", "stale": False, "created_at": at, "observation": {
                **identity, "kind": "activity", "source": self.peer.activity_source, "at": at, "state": "idle"}})
        unchanged = deepcopy(evidence["run"]["raw_records"])
        unchanged["observations"].pop()
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
        for gates in self.gates.values():
            for key, value in gates.items():
                if key.endswith("_gate"):
                    Path(value).touch()


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
