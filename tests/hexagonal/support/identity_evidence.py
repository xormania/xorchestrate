"""Concrete evidence fixture adapters for the vendor-neutral IV03 contract.

The exchange shape extends the raw-evidence calibration's minimal round trip;
this module owns construction rather than importing another test's helpers.
Only this adapter layer translates semantic fixture claims into stored rows or
concrete native observation formats. Its public subject calls the real checker
facade, including normal normalization, criteria bindings and case dispatch.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from scripts.verification.checker import verify
from scripts.verification.normalize import normalize
from tests.hexagonal.contracts.identity import IdentityOutcome
from xorchestrate.exchange import evaluate_exchange


@dataclass(frozen=True)
class NativeEncoding:
    peer: str
    kind: str
    source: str

    def encode(self, action: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {"kind": self.kind, "source": self.source, "action": action, "arguments": deepcopy(arguments)}


class CustomObservationDecoder:
    """Unlisted peer and packet kind prove that decoder injection is effective."""

    origin = "custom/native-command"

    def decode(self, record, mode):
        item = record["observation"]
        if item.get("source") != "custom/observation" or item.get("kind") != "custom_command":
            return []
        return [{"event": "native_tool", "at": item.get("at", record["created_at"]),
                 "call_id": item.get("call_id"), "tool": item.get("action"),
                 "arguments": deepcopy(item.get("arguments")), "origin": self.origin}]


CODEX = NativeEncoding("codex", "native_tool_call", "codex/item/tool/call")
CLAUDE = NativeEncoding("claude", "native_tool", "claude/PreToolUse")
GROK = NativeEncoding("grok", "native_tool_call", "grok/session/update")
CUSTOM = NativeEncoding("independent-peer", "custom_command", "custom/observation")
OTHER_CUSTOM = NativeEncoding("another-independent-peer", "custom_command", "custom/observation")


def _binding(value):
    # Canonical serialization is a documented input binding, not a verdict.
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


class IdentityEvidenceAdapter:
    def __init__(self, source, target, scope, operation, decoders=None):
        self.source, self.target, self.scope = source, target, scope
        self.decoders = decoders
        self.rows = {}
        self.sequence = 0
        self.raw = {name: [] for name in ("observations", "messages", "evidence", "attempts", "sessions", "operations")}
        self.sessions = {encoding.peer: {"session_id": encoding.peer + "-application", "native_id": encoding.peer + "-native",
                                       "incarnation": encoding.peer + "-incarnation"} for encoding in (source, target)}
        if scope == "exchange":
            self._exchange()
        elif scope == "lifecycle":
            self._lifecycle(operation)
        else:
            raise ValueError(scope)
        self.fixture = {"batches": []}
        self.criteria = {"required_cases": self.cases}
        self.bindings = {"criteria_sha256": _binding(self.criteria), "fixture_sha256": _binding(self.fixture)}
        self.run.update(raw_records=self.raw, sessions=self.sessions, mode="protocol-fixture", bindings=self.bindings,
                        executed_cases=[case["id"] for case in self.cases])

    def _at(self):
        self.sequence += 1
        return f"2026-10-24T00:00:{self.sequence:02}+00:00"

    def _session_rows(self):
        for peer, identity in self.sessions.items():
            row = {"id": identity["session_id"], "work_id": "session-work", "actor_id": peer, "backend": deepcopy(identity)}
            self.raw["sessions"].append(row)
            if peer == self.source.peer:
                self.rows["session"] = row

    def _call(self, encoding, action, arguments, result, saved):
        call_id = "call-" + str(self.sequence)
        records = []
        for side in ("native", "requested", "callback"):
            stamp = self._at()
            identity = {**self.sessions[encoding.peer], "call_id": call_id}
            if side == "native":
                observation = {**identity, **encoding.encode(action, arguments)}
            else:
                observation = {**identity, "kind": "application_tool", "phase": "requested" if side == "requested" else "completed",
                               "action": action, "arguments": {**deepcopy(arguments), "work_id": "session-work"}}
                if side == "callback":
                    observation["result"] = deepcopy(result)
            row = {"id": side + "-" + call_id, "session_id": identity["session_id"], "origin": "adapter", "stale": False,
                   "created_at": stamp, "work_id": "session-work", "created_by": encoding.peer, "observation": observation}
            self.raw["observations"].append(row)
            records.append(row)
            if side == "requested":
                saved["created_at"] = self._at()
        return dict(zip(("native", "requested", "callback"), records))

    def _message(self, encoding, recipient, kind, memory):
        body = {"verification": {"run_id": "identity-run", "case_id": "exchange", "kind": kind, "nonce": "fresh-nonce", "memory": memory}}
        saved = {"id": kind, "work_id": "session-work", "sender": encoding.peer,
                 "created_by": encoding.peer, "destination": {"actor": recipient.peer}, "recipients": [recipient.peer], "body": body}
        self.raw["messages"].append(saved)
        rows = self._call(encoding, "send_message", {"destination": saved["destination"], "body": body},
                          {"message": {"id": kind, "work_id": "session-work", "created_by": encoding.peer,
                                       "sender": encoding.peer}}, saved)
        rows["effect"] = saved
        self.raw["attempts"].append({"id": "delivery-" + kind, "message_id": kind, "recipient": recipient.peer,
                                    **self.sessions[recipient.peer], "status": "submitted", "sequence": 1,
                                    "origin": "adapter", "submission": {"submitted": True}, "created_at": self._at(), "finished_at": self._at()})
        return rows

    def _exchange(self):
        self._session_rows()
        self.rows.update(self._message(self.source, self.target, "challenge", ""))
        self._message(self.target, self.source, "reply", "retained-memory")
        result = {"verification": {"run_id": "identity-run", "phase": "received", "case_id": "exchange", "nonce": "fresh-nonce",
                                   "memory": "retained-memory", "peer": self.target.peer}}
        saved = {"id": "receipt", "work_id": "session-work", "created_by": self.source.peer,
                 "source": {"type": "caller", "actor": self.source.peer}, "result": result}
        self.raw["evidence"].append(saved)
        receipt_rows = self._call(self.source, "record_result", {"result": result}, {"evidence": deepcopy(saved)}, saved)
        self.rows.update({"receipt-" + side: row for side, row in receipt_rows.items()})
        self.rows["receipt-effect"] = saved
        self.cases = [{"id": "exchange", "kind": "exchange", "source": self.source.peer, "target": self.target.peer},
                      {"id": "audit", "kind": "audit"}]
        self.run = {"nonces": {"exchange": "fresh-nonce"}, "memories": {self.target.peer: "retained-memory"}}

    def _lifecycle(self, operation):
        peer = self.source.peer
        before = {**self.sessions[peer], "session_id": "old-application", "incarnation": "old-incarnation", "ownership": "owned"}
        after = {**self.sessions[peer], "ownership": "owned"}
        self.sessions = {peer: after}
        old_row = {"id": before["session_id"], "backend": before, "actor_id": peer, "work_id": "session-work"}
        new_row = {"id": after["session_id"], "backend": after, "actor_id": peer, "work_id": "session-work"}
        self.raw["sessions"].extend((old_row, new_row))
        self.rows["session"] = new_row
        self.cases = [{"id": "lifecycle", "kind": "lifecycle", "peer": peer, "operation": operation}]
        result = {"verification": {"run_id": "identity-run", "phase": "lifecycle", "case_id": "lifecycle", "memory": "retained-memory"}}
        saved = {"id": "retained-effect", "work_id": "session-work", "created_by": peer,
                 "source": {"type": "caller", "actor": peer}, "result": result, "created_at": self._at()}
        self.rows["effect"] = saved
        self.raw["evidence"].append(saved)
        arguments = {"work_id": "session-work", "result": result}
        for side in ("native", "callback"):
            observation = {**after, "call_id": "retained-call", **self.source.encode("record_result", arguments)}
            if side == "callback":
                observation.update(kind="application_tool", phase="completed", result={"evidence": deepcopy(saved)})
            row = {"id": side + "-retained", "session_id": after["session_id"], "origin": "adapter", "stale": False,
                   "created_at": self._at(), "work_id": "session-work", "created_by": peer, "observation": observation}
            self.raw["observations"].append(row)
            self.rows[side] = row
        action = {"resume": "start_session", "reconnect": "reconnect_session", "attach": "attach_session", "replace": "reconnect_session"}[operation]
        operation_row = {"id": "lifecycle-operation", "status": "completed", "action": action,
                         "result": {"session": {"id": after["session_id"], "actor_id": peer,
                                                "work_id": "session-work", "backend": deepcopy(after)}}}
        self.raw["operations"].append(operation_row)
        self.rows["operation"] = operation_row
        lifecycle = {"event": "lifecycle", "peer": peer, "operation": operation, "before": before, "after": after,
                     "operation_id": "lifecycle-operation",
                     "stale_errors": {side: {"code": "stale_connection"} for side in ("application", "adapter")},
                     "retained_observation": {"call_id": "retained-call", "native_observation_id": "native-retained",
                                              "callback_observation_id": "callback-retained", "evidence_id": "retained-effect"}}
        raw_lifecycle = {"session": self.raw["sessions"], "operation": self.raw["operations"],
                         "evidence": self.raw["evidence"], "native_observation": self.raw["observations"]}
        self.run = {"run_id": "identity-run", "memories": {peer: "retained-memory"}, "lifecycle_events": [lifecycle],
                    "lifecycle_records": [{"peer": peer, "records": raw_lifecycle}]}

    def change_claim(self, change):
        row = self.rows[change.record]
        if change.claim == "actor":
            container, key = row, "actor_id" if change.record == "session" else "created_by"
        elif change.claim == "record_work":
            container, key = row, "work_id"
        elif change.claim == "application_session":
            container, key = row, "session_id"
        elif change.claim.startswith("result_"):
            container = row["result"]["session"]
            if change.claim in {"result_native_session", "result_incarnation"}:
                container = container["backend"]
            key = {"result_actor": "actor_id", "result_work": "work_id", "result_native_session": "native_id",
                   "result_incarnation": "incarnation"}[change.claim]
        elif change.claim.startswith("embedded_"):
            effect_kind = "message" if row["observation"]["action"] == "send_message" else "evidence"
            container = row["observation"]["result"][effect_kind]
            key = {"embedded_work": "work_id", "embedded_actor": "created_by", "embedded_sender": "sender",
                   "embedded_source": "source"}[change.claim]
        elif change.claim == "argument_work":
            container, key = row["observation"]["arguments"], "work_id"
        else:
            container = row["observation"]
            key = {"native_session": "native_id", "incarnation": "incarnation", "nested_session": "session_id"}[change.claim]
        if change.absent:
            container.pop(key, None)
        else:
            container[key] = deepcopy(change.value)

    def duplicate_native(self):
        row = {**deepcopy(self.rows["native"]), "id": "independent-duplicate-native"}
        self.raw["observations"].append(row)
        self.rows["duplicate"] = row

    def duplicate_completed_callback(self):
        row = {**deepcopy(self.rows["callback"]), "id": "independent-duplicate-callback"}
        self.raw["observations"].append(row)
        self.rows["duplicate-callback"] = row

    def add_historical_call(self, scope, stale):
        identity = {**self.sessions[self.source.peer], "session_id": "historical-application",
                    "incarnation": "historical-incarnation"}
        if scope == "unrelated":
            identity["native_id"] = "unrelated-native-conversation"
        self.raw["sessions"].append({"id": identity["session_id"], "backend": deepcopy(identity),
                                     "actor_id": self.source.peer, "work_id": "session-work"})
        for side in ("native", "requested", "callback"):
            record = deepcopy(self.rows[side])
            record.update(id="historical-" + record["id"], session_id=identity["session_id"], stale=stale)
            record["observation"].update(identity)
            # Reusing a call ID in a different qualified historical connection
            # does not make that record evidence for the current connection.
            self.raw["observations"].append(record)

    def select_alternate_work(self):
        for row in self.raw["observations"]:
            row["observation"]["arguments"]["work_id"] = "explicit-alternate-work"
            for effect_kind in ("message", "evidence"):
                embedded = row["observation"].get("result", {}).get(effect_kind, {})
                if "work_id" in embedded:
                    embedded["work_id"] = "explicit-alternate-work"
        for row in self.raw["messages"] + self.raw["evidence"]:
            row["work_id"] = "explicit-alternate-work"

    def omit_optional_work(self):
        for row in self.raw["observations"]:
            row.pop("work_id", None)
            row["observation"]["arguments"].pop("work_id", None)
            row["observation"].get("result", {}).get("evidence", {}).pop("work_id", None)
        for row in self.raw["sessions"] + self.raw["evidence"]:
            row.pop("work_id", None)
        for row in self.raw["operations"]:
            row["result"]["session"].pop("work_id", None)

    def evaluate(self):
        kwargs = {} if self.decoders is None else {"decoders": self.decoders}
        report = verify(self.run, self.criteria, self.bindings, self.fixture, **kwargs)
        diagnostics = frozenset(case["assertion"] for case in report["cases"] if "assertion" in case)
        return IdentityOutcome(report["status"] == "passed", diagnostics, report)

    def native_facts(self):
        kwargs = {} if self.decoders is None else {"decoders": self.decoders}
        return [event for event in normalize(self.raw, self.sessions, [], self.run["mode"], **kwargs)
                if event["event"] == "native_tool"]

    def source_native_records(self):
        return [deepcopy(row) for row in self.raw["observations"] if row["observation"].get("phase") is None]


def identity_factory(source, target, *, custom=False):
    decoders = {source.peer: CustomObservationDecoder(), target.peer: CustomObservationDecoder()} if custom else None

    def create(scope, operation="reconnect"):
        return IdentityEvidenceAdapter(source, target, scope, operation, decoders)

    return create


class ProductReceiptEvidenceAdapter(IdentityEvidenceAdapter):
    """Apply the same raw fixture contract to the configured receipt policy."""

    def evaluate(self):
        expectation = {"run_id": "identity-run", "case_id": "exchange", "nonce": "fresh-nonce",
                       "memory": "retained-memory", "sender": self.source.peer, "recipient": self.target.peer}
        snapshot = {**self.raw, "message": self.raw["messages"][0]}
        decision = evaluate_exchange(snapshot, self.target.peer, expectation)
        return IdentityOutcome(decision["verified"], frozenset(decision["diagnostics"]), decision)


def receipt_factory(source, target):
    def create(scope, operation="reconnect"):
        if scope != "exchange":
            raise ValueError("Receipt policy covers complete exchanges")
        return ProductReceiptEvidenceAdapter(source, target, scope, operation)

    return create
