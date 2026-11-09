"""Raw evidence fixtures shared by callback contracts and fault calibrations.

These adapters construct observations independently of normalization and verdict
logic. They never derive expected outcomes from the subject under test.
"""
from copy import deepcopy

from scripts.verification.model import canonical, digest


class ObservationBuilder:
    """One construction path for independent native/request/effect observations.

    The encoding is a fixture input; every row carries the application-written
    actor stamp, as the product's durable observations do. Native arguments
    are snapshotted before the application adds its default work scope. This
    distinction is material to the identity and raw-evidence contracts.
    """

    def __init__(self, raw, sessions, *, work_id="work", completed_side="completed", separator=""):
        self.raw, self.sessions = raw, sessions
        self.work_id = work_id
        self.completed_side, self.separator = completed_side, separator
        self.sequence = 0

    def at(self):
        self.sequence += 1
        return f"2026-10-24T00:00:{self.sequence:02}+00:00"

    def call(self, peer, action, arguments, result=None, saved=None, *, native):
        call_id = "call-" + str(self.sequence)
        identity = {**self.sessions[peer], "call_id": call_id}
        records = {}
        for side in ("native", "requested", self.completed_side):
            stamp = self.at()
            if side == "native":
                observation = {**identity, **deepcopy(native)}
            else:
                observation = {**identity, "kind": "application_tool", "action": action,
                    "phase": "requested" if side == "requested" else "completed",
                    "arguments": {**deepcopy(arguments), "work_id": self.work_id}}
                if side == self.completed_side:
                    observation["result"] = deepcopy(result or {})
            row = {"id": side + self.separator + call_id, "session_id": identity["session_id"],
                   "origin": "adapter", "stale": False, "created_at": stamp,
                   "work_id": self.work_id, "created_by": peer, "observation": observation}
            self.raw["observations"].append(row)
            records[side] = row
            if side == "requested" and saved is not None:
                saved["created_at"] = self.at()
        return records


def raw_exchange(kind="exchange"):
    peers = ("codex", "grok")
    sessions = {p: {"native_id": p + "-native", "session_id": p + "-app", "incarnation": p + "-epoch"}
                for p in peers}
    raw = {"observations": [], "messages": [], "evidence": [], "attempts": [],
           "sessions": [{"id": sessions[peer]["session_id"], "work_id": "work", "actor_id": peer,
                         "backend": deepcopy(sessions[peer])} for peer in peers]}
    observations = ObservationBuilder(raw, sessions)
    at = observations.at

    def call(peer, action, arguments, result=None, saved=None):
        return observations.call(peer, action, arguments, result, saved,
            native={"kind": "native_tool_call", "source": peer, "action": action, "arguments": arguments})

    def message(peer, target, identifier, memory):
        body = {"verification": {"case_id": "case", "kind": identifier, "nonce": "nonce", "memory": memory}}
        saved = {"id": identifier, "work_id": "work", "sender": peer, "created_by": peer,
                 "destination": {"actor": target}, "recipients": [target], "body": body}
        raw["messages"].append(saved)
        call(peer, "send_message", {"destination": saved["destination"], "body": body}, {"message": deepcopy(saved)}, saved)
        raw["attempts"].append({"id": "attempt-" + identifier, "message_id": identifier, "recipient": target,
            **sessions[target], "status": "submitted", "sequence": 1, "origin": "adapter",
            "submission": {"submitted": True}, "created_at": at(), "finished_at": at()})

    message("codex", "grok", "challenge", "")
    message("grok", "codex", "reply", "private")
    saved = {"id": "receipt", "work_id": "work", "created_by": "codex",
             "source": {"type": "caller", "actor": "codex"}, "result": {"verification": {"phase": "received", "case_id": "case",
        "nonce": "nonce", "memory": "private", "peer": "grok"}}}
    raw["evidence"].append(saved)
    call("codex", "record_result", {"result": saved["result"]}, {"evidence": deepcopy(saved)}, saved)
    providers = []
    if kind == "work_exchange":
        providers = [{"event": "work_started", "peer": "grok", "at": "2026-10-24T00:00:00+00:00"},
                     {"event": "work_completed", "peer": "grok", "at": "2026-10-24T00:00:59+00:00"}]
    if kind == "busy":
        call("grok", "invoke", {"capability": "verification/hold", "arguments": {"case_id": "case"}})
        providers = [{"event": "hold_started", "peer": "grok", "case_id": "case", "at": "2026-10-24T00:00:00+00:00"},
                     {"event": "hold_finished", "peer": "grok", "case_id": "case", "released": True,
                      "at": "2026-10-24T00:00:07+00:00"}]
    case = {"id": "case", "source": "codex", "target": "grok", "kind": kind}
    criteria = {"required_cases": [case, {"id": "audit", "kind": "audit"}]}
    fixture = {"batches": []}
    bindings = {"criteria_sha256": digest(canonical(criteria)), "fixture_sha256": digest(canonical(fixture))}
    run = {"raw_records": raw, "provider_events": providers, "mode": "protocol-fixture", "sessions": sessions,
           "nonces": {"case": "nonce"}, "memories": {"grok": "private"}, "executed_cases": ["case", "audit"],
           "bindings": bindings}
    return run, criteria, bindings, fixture



def select_explicit_work(inputs, work_id):
    """Coherently select explicit work in arguments, effects and returned views."""
    raw = inputs[0]["raw_records"]
    for record in raw["observations"]:
        observation = record["observation"]
        observation["arguments"]["work_id"] = work_id
        for kind in ("message", "evidence"):
            if isinstance(observation.get("result", {}).get(kind), dict):
                observation["result"][kind]["work_id"] = work_id
    for record in raw["messages"] + raw["evidence"]:
        record["work_id"] = work_id
    return raw


def lifecycle_input(operation="resume"):
    case = {"peer": "codex", "operation": operation, "id": "lifecycle-codex-" + operation}
    before = {"session_id": "old-app", "native_id": "retained-native", "incarnation": "old", "ownership": "owned"}
    after = {**before, "session_id": "new-app", "incarnation": "new"}
    result = {"verification": {"run_id": "run", "phase": "lifecycle", "case_id": case["id"], "memory": "private"}}
    sessions = [{"id": view["session_id"], "actor_id": "codex", "backend": view} for view in (before, after)]
    saved = {"id": "saved", "created_by": "codex", "source": {"type": "caller", "actor": "codex"}, "result": result}
    raw = {"session": sessions,
           "operation": [{"id": "op", "status": "completed", "action": "start_session" if operation == "resume" else "reconnect_session",
                          "result": {"session": deepcopy(sessions[1])}}],
           "evidence": [saved], "native_observation": []}
    for side in ("native", "callback"):
        observation = {"native_id": after["native_id"], "incarnation": after["incarnation"],
                       "action": "record_result", "call_id": "retained-call", "arguments": {"result": result}}
        observation.update({"kind": "native_tool_call", "source": "codex"} if side == "native" else
                           {"phase": "completed", "result": {"evidence": deepcopy(saved)}})
        raw["native_observation"].append({"id": side, "origin": "adapter", "stale": False, "created_by": "codex",
                                          "session_id": after["session_id"], "observation": observation})
    lifecycle = {"before": before, "after": after, "operation_id": "op",
                 "stale_errors": {side: {"code": "stale_connection"} for side in ("application", "adapter")},
                 "retained_observation": {"call_id": "retained-call", "native_observation_id": "native",
                                          "callback_observation_id": "callback", "evidence_id": "saved"}}
    run = {"run_id": "run", "memories": {"codex": "private"},
           "raw_records": {"sessions": raw["session"], "operations": raw["operation"],
                           "observations": raw["native_observation"], "evidence": raw["evidence"]},
           "lifecycle_records": [{"peer": "codex", "records": raw}]}
    return (case, run, lifecycle), raw["native_observation"]

