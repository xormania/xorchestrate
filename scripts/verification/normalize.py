"""Normalize retained application records without trusting summary verdicts."""
from copy import deepcopy
from datetime import datetime

from .checker import ORIGINS, VerificationFailure


def normalize(raw, peer_sessions, provider_events, mode):
    by_session = {value["session_id"]: peer for peer, value in peer_sessions.items()}
    for records in raw.values():
        if not isinstance(records, list):
            continue
        identifiers = [record.get("id") for record in records]
        if any(not isinstance(identifier, str) or not identifier for identifier in identifiers) or len(identifiers) != len(set(identifiers)):
            raise VerificationFailure("duplicate_or_missing_raw_record_identity")
    session_records = {record["id"]: record for record in raw.get("sessions", [])}
    observations = raw["observations"]
    events = []

    def add(event, peer, at, **data):
        events.append({"event": event, "peer": peer, "at": at, **deepcopy(peer_sessions.get(peer, {})), **deepcopy(data)})

    for record in observations:
        item = record["observation"]
        peer = by_session.get(record["session_id"])
        if not peer or record.get("origin") != "adapter" or record.get("stale"):
            continue
        identity = {k: item.get(k) for k in ("native_id", "incarnation")}
        identity["session_id"] = record["session_id"]
        if "session_id" in item:
            identity["observation_session_id"] = item["session_id"]
        identity["session_work_id"] = session_records.get(record["session_id"], {}).get("work_id")
        kind, source = item.get("kind"), item.get("source", "")
        at = item.get("at", record["created_at"])
        source_valid = ((mode == "fixture" and source == "fixture") or
                        (peer == "codex" and source.startswith("codex")) or
                        (peer == "grok" and source.startswith("grok")) or
                        (peer == "claude" and source == "claude/PreToolUse"))
        if kind in {"native_tool_call", "native_tool", "native_discovery"} and source_valid:
            add("native_discovery" if kind == "native_discovery" else "native_tool", peer, at,
                **identity, call_id=item.get("call_id"), tool=item.get("action"), arguments=item.get("arguments"),
                origin=ORIGINS[peer], observation_work_id=record.get("work_id"), raw_observation_id=record["id"])
        activity_source_valid = ((mode == "fixture" and source == "fixture") or
                                 (peer in {"codex", "grok"} and source.startswith(peer)) or
                                 (peer == "claude" and source.startswith("claude/")))
        if (kind in {"activity", "native_completion", "native_completion_unknown", "turn_completed", "turn_failed"}
                and activity_source_valid and not item.get("stale", False)):
            # A completed turn does not establish that every overlapping turn
            # completed. Only the adapter's aggregate post-transition state can
            # establish idle. Retain missing/invalid state as uncertainty so a
            # later ambiguous completion cannot resurrect a historical idle.
            state = item.get("state") if kind == "activity" else item.get("activity")
            if state not in {"idle", "active", "unknown"}:
                state = "unknown"
            add("activity", peer, at, **identity, state=state, basis="native",
                activity_kind=kind, raw_observation_id=record["id"])

    def call_data(record):
        item = record["observation"]
        session = session_records.get(record["session_id"])
        recorded_identity = None if session is None else {
            "session_id": session["id"], "actor_id": session.get("actor_id"),
            **{key: session.get("backend", {}).get(key) for key in ("native_id", "incarnation")}}
        # Keep executed application arguments independent from native input.
        # The checker compares the two; normalization must never reconcile them.
        return {"call_id": item.get("call_id"), "tool": item.get("action"), "arguments": item.get("arguments"),
                "native_id": item.get("native_id"), "incarnation": item.get("incarnation"),
                "session_id": record["session_id"], "observation_work_id": record.get("work_id"),
                "session_work_id": session_records.get(record["session_id"], {}).get("work_id"),
                "recorded_session_identity": recorded_identity,
                "callback_actor": record.get("created_by", (session or {}).get("actor_id")),
                "callback_actor_basis": "observation" if "created_by" in record else "session",
                **({"observation_session_id": item["session_id"]} if "session_id" in item else {}),
                "callback_observation_id": record["id"]}

    messages = {m["id"]: m for m in raw["messages"]}
    evidence = {e["id"]: e for e in raw["evidence"]}
    provider_calls = []
    for record in observations:
        item = record["observation"]
        peer = by_session.get(record.get("session_id"))
        if peer is None:
            # Keep a callback that contradicts its recorded application session
            # visible when its native connection identifies a measured peer.
            # This projection does not grant authority: match_callback checks the
            # original session_id and will reject the disagreement.
            matches = [candidate for candidate, identity in peer_sessions.items()
                       if item.get("native_id") == identity.get("native_id")
                       and item.get("incarnation") == identity.get("incarnation")]
            peer = matches[0] if len(matches) == 1 else None
        if not peer or record.get("origin") != "adapter" or record.get("stale"):
            continue
        if item.get("phase") == "requested":
            add("tool_called", peer, record["created_at"], **call_data(record))
            if item.get("action") == "invoke":
                provider_calls.append(record)
        if item.get("phase") != "completed":
            continue
        add("tool_completed", peer, record["created_at"], **call_data(record))
        result, args = item.get("result", {}), item.get("arguments", {})
        if item.get("action") == "send_message":
            message = messages.get(result.get("message", {}).get("id"))
            if not message or not isinstance(message.get("body"), dict):
                continue
            payload = message["body"].get("verification", {})
            recipients = message.get("recipients", [])
            if not payload.get("case_id") or len(recipients) != 1:
                continue
            add("queued", peer, message["created_at"], **call_data(record),
                persisted_message=message, message={
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
            add("report", peer, saved["created_at"], **call_data(record), persisted_evidence=saved, report=report)
    for attempt in raw["attempts"]:
        # An unresolved/new attempt must survive even if its session is absent
        # or superseded; dropping it would let historical success mask it.
        peer = attempt.get("recipient") or by_session.get(attempt.get("session_id"))
        fields = {"message_id": attempt["message_id"], "attempt_id": attempt["id"],
                  "session_id": attempt.get("session_id"), "incarnation": attempt.get("incarnation"),
                  "native_id": attempt.get("native_id"), "recipient": attempt.get("recipient"),
                  "attempt_sequence": attempt.get("sequence"), "status": attempt.get("status"),
                  "origin": attempt.get("origin"), "submission": attempt.get("submission")}
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
    def observed_time(event):
        try:
            value = datetime.fromisoformat(event["at"])
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("A timezone is required")
            return value
        except (KeyError, TypeError, ValueError) as error:
            raise VerificationFailure("invalid_observation_timestamp") from error

    events.sort(key=observed_time)
    return [{**event, "seq": index + 1} for index, event in enumerate(events)]
