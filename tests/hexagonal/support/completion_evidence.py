"""Raw completion evidence builders independent of verdict implementation."""

from scripts.verification.capture import canonical, digest


def completion_input(peer="claude"):
    identity = {"session_id": peer + "-app", "native_id": peer + "-native", "incarnation": peer + "-epoch"}
    records = []
    def add(kind, *, at=None, **fields):
        index = len(records) + 1
        source = "claude/Stop" if peer == "claude" else peer
        if kind == "native_completion" and peer != "claude":
            fields.setdefault("turn_id" if peer == "codex" else "prompt_id", "A")
        record = {"id": "observation-" + str(index), "session_id": identity["session_id"],
                  "origin": "adapter", "stale": False, "created_at": at or f"2026-10-24T00:00:{index:02}+00:00",
                  "observation": {**identity, "kind": kind, "source": source, **fields}}
        records.append(record)
        return record
    def turn(phase, name):
        if peer == "claude":
            add("turn_started" if phase == "start" else "turn_completed", turn_id=name, activity="idle" if name == "B" else "active")
        elif peer == "codex":
            add("native_notification", method="turn/started" if phase == "start" else "turn/completed",
                payload={"threadId": identity["native_id"], "turn": {"id": name}})
        else:
            add("native_notification", method="session/update" if phase == "start" else "_x.ai/session/prompt_complete",
                payload={"sessionId": identity["native_id"], "promptId": name,
                         "update": {"sessionUpdate": "agent_message_chunk"} if phase == "start" else {}})
    turn("start", "A")
    turn("start", "B")
    add("application_tool", phase="requested", action="invoke", arguments={}, call_id="A")
    add("application_tool", phase="requested", action="invoke", arguments={}, call_id="B")
    # These tests isolate aggregate activity: both callbacks actually finished.
    # Outstanding callback/effect contradictions have separate IV02 coverage.
    add("application_tool", phase="completed", action="invoke", arguments={}, call_id="A")
    add("application_tool", phase="completed", action="invoke", arguments={}, call_id="B")
    turn("end", "A")
    turn("end", "B")
    criteria = {"required_cases": [{"id": "completion-" + peer, "kind": "completion", "peer": peer}]}
    fixture = {"batches": []}
    bindings = {"criteria_sha256": digest(canonical(criteria)), "fixture_sha256": digest(canonical(fixture))}
    run = {"mode": "protocol-fixture", "raw_records": {"observations": records, "messages": [], "evidence": [], "attempts": []},
           "sessions": {peer: identity}, "bindings": bindings, "executed_cases": ["completion-" + peer]}
    return (run, criteria, bindings, fixture), add


