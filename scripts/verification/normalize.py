"""Normalize retained application records without trusting summary verdicts."""
from copy import deepcopy

from .checker import ORIGINS


def normalize(raw, peer_sessions, provider_events, mode):
    by_session = {value["session_id"]: peer for peer, value in peer_sessions.items()}
    observations = raw["observations"]
    native = {}
    events = []

    def add(event, peer, at, **data):
        events.append({"event": event, "peer": peer, "at": at, **deepcopy(peer_sessions.get(peer, {})), **deepcopy(data)})

    def key(record):
        item = record["observation"]
        return (record["session_id"], item.get("incarnation"), item.get("call_id"))

    for record in observations:
        item = record["observation"]
        peer = by_session.get(record["session_id"])
        if not peer or record.get("origin") != "adapter" or record.get("stale"):
            continue
        identity = {k: item.get(k) for k in ("native_id", "incarnation")}
        identity["session_id"] = record["session_id"]
        kind, source = item.get("kind"), item.get("source", "")
        at = item.get("at", record["created_at"])
        source_valid = ((mode == "fixture" and source == "fixture") or
                        (peer == "codex" and source.startswith("codex")) or
                        (peer == "grok" and source.startswith("grok")) or
                        (peer == "claude" and source == "claude/PreToolUse"))
        if kind in {"native_tool_call", "native_tool", "native_discovery"} and source_valid:
            native[key(record)] = record
            add("native_discovery" if kind == "native_discovery" else "native_tool", peer, at,
                **identity, call_id=item.get("call_id"), tool=item.get("action"), arguments=item.get("arguments"),
                origin=ORIGINS[peer], raw_observation_id=record["id"])
        if kind in {"native_completion", "turn_completed"} and not item.get("stale", False):
            add("activity", peer, at, **identity, state=item.get("activity", "idle"), basis="native", raw_observation_id=record["id"])

    def call_data(record):
        item = record["observation"]
        source = native.get(key(record))
        # Missing native observation is not synthesized from the caller. Keep
        # resolved arguments so the independent call matcher will reject it.
        arguments = source["observation"].get("arguments") if source else item.get("arguments")
        return {"call_id": item.get("call_id"), "tool": item.get("action"), "arguments": arguments,
                "native_id": item.get("native_id"), "incarnation": item.get("incarnation"),
                "session_id": record["session_id"], "callback_observation_id": record["id"]}

    messages = {m["id"]: m for m in raw["messages"]}
    evidence = {e["id"]: e for e in raw["evidence"]}
    provider_calls = []
    for record in observations:
        item = record["observation"]
        peer = by_session.get(record["session_id"])
        if not peer or record.get("origin") != "adapter" or record.get("stale"):
            continue
        if item.get("phase") == "requested":
            add("tool_called", peer, record["created_at"], **call_data(record))
            if item.get("action") == "invoke":
                provider_calls.append(record)
        if item.get("phase") != "completed":
            continue
        result, args = item.get("result", {}), item.get("arguments", {})
        if item.get("action") == "send_message":
            message = messages.get(result.get("message", {}).get("id"))
            if not message or not isinstance(message.get("body"), dict):
                continue
            payload = message["body"].get("verification", {})
            recipients = message.get("recipients", [])
            if not payload.get("case_id") or len(recipients) != 1:
                continue
            add("queued", peer, message["created_at"], **call_data(record), message={
                "id": message["id"], "to": recipients[0], **payload})
        elif item.get("action") == "record_result":
            saved = evidence.get(result.get("evidence", {}).get("id"))
            if not saved or saved.get("result") != args.get("result"):
                continue
            payload = saved.get("result", {}).get("verification", {})
            if payload.get("phase") == "ready":
                report = {"phase": "ready", "case_id": "startup", "nonce": "", "memory": payload.get("memory"), "peer": ""}
            elif payload.get("phase") == "received":
                report = {key: payload.get(key) for key in ("phase", "case_id", "nonce", "memory", "peer")}
            else:
                continue
            add("report", peer, saved["created_at"], **call_data(record), report=report)
    for attempt in raw["attempts"]:
        peer = by_session.get(attempt.get("session_id"))
        if not peer:
            continue
        fields = {"message_id": attempt["message_id"], "attempt_id": attempt["id"],
                  "session_id": attempt["session_id"], "incarnation": attempt["incarnation"], "native_id": attempt["native_id"]}
        add("attempt", peer, attempt["created_at"], **fields)
        if attempt.get("status") == "submitted" and attempt.get("origin") == "adapter" and attempt.get("submission", {}).get("submitted") is True:
            add("submitted", peer, attempt["finished_at"], **fields, state="submitted")
    for event in provider_events:
        peer = event["peer"]
        matching = []
        for record in provider_calls:
            if by_session.get(record["session_id"]) != peer:
                continue
            args = record["observation"]["arguments"]
            capability, arguments = args.get("capability"), args.get("arguments", {})
            if event["event"] == "hold_started" and capability == "verification/hold" and arguments.get("case_id") == event.get("case_id"):
                matching.append(record)
            elif event["event"] == "work_submitted" and capability == "verification/work_submit" and arguments.get("batch") == event.get("batch"):
                matching.append(record)
        data = {k: v for k, v in event.items() if k not in {"event", "peer", "at"}}
        if matching:
            data.update(call_data(matching[0]))
        add(event["event"], peer, event["at"], **data)
    events.sort(key=lambda event: event["at"])
    return [{**event, "seq": index + 1} for index, event in enumerate(events)]
