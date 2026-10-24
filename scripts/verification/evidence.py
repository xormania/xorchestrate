"""Normalize retained application records without trusting summary verdicts."""
from copy import deepcopy
from datetime import datetime

from .model import VerificationFailure
from .ports import NativeObservationDecoder


def observation_peer(record, peer_sessions):
    """Retain contradictions when either independent connection identity applies."""
    by_session = {value["session_id"]: peer for peer, value in peer_sessions.items()}
    peer = by_session.get(record.get("session_id"))
    if peer is None:
        item = record.get("observation", {})
        matches = [candidate for candidate, identity in peer_sessions.items()
                   if item.get("native_id") == identity.get("native_id")
                   and item.get("incarnation") == identity.get("incarnation")]
        peer = matches[0] if len(matches) == 1 else None
    return peer


def decode_observations(observations, peer_sessions, session_records, mode, decoders):
    """Preserve each raw observation and explicit claim around adapter facts.

    Peers and decoder origins are supplied by composition. This module knows the
    application record envelope, but never interprets a native wire protocol.
    No fact is reconciled with or repaired from another record's identity.
    """
    facts = []
    for record in observations:
        item = record.get("observation", {})
        peer = observation_peer(record, peer_sessions)
        if peer is None or peer not in decoders or record.get("origin") != "adapter" or record.get("stale") or item.get("stale"):
            continue
        session = session_records.get(record.get("session_id"), {})
        identity = {key: item.get(key) for key in ("native_id", "incarnation")}
        identity.update(peer=peer, session_id=record.get("session_id"))
        if "session_id" in item:
            identity["observation_session_id"] = item["session_id"]
        if "created_by" in record:
            identity["native_actor"] = record["created_by"]
        if "work_id" in record:
            identity["observation_work_id"] = record["work_id"]
        if "work_id" in session:
            identity["session_work_id"] = session["work_id"]
        if session:
            identity["recorded_session_identity"] = {"session_id": session["id"],
                **({"actor_id": session["actor_id"]} if "actor_id" in session else {}),
                **{key: session.get("backend", {}).get(key) for key in ("native_id", "incarnation")}}
        for fact in decoders[peer].decode(deepcopy(record), mode):
            facts.append({**deepcopy(fact), **identity,
                          "at": fact.get("at", item.get("at", record.get("created_at"))),
                          "expected_origin": decoders[peer].origin,
                          "raw_ref": {"kind": "native_observation", "id": record["id"]},
                          "raw_observation_id": record["id"], "raw_observation": deepcopy(record)})
    return facts


def normalize(raw, peer_sessions, provider_events, mode, *, decoders: dict[str, NativeObservationDecoder]):
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

    for fact in decode_observations(observations, peer_sessions, session_records, mode, decoders):
        # Turn facts are consumed independently by the completion ledger. Keep
        # this established application event sequence free of protocol ticks.
        if fact["event"] == "native_turn":
            continue
        event, peer, at = fact["event"], fact["peer"], fact.get("at")
        add(event, peer, at, **{key: value for key, value in fact.items() if key not in {"event", "peer", "at"}})

    def call_data(record):
        item = record["observation"]
        session = session_records.get(record["session_id"])
        recorded_identity = None if session is None else {
            "session_id": session["id"], "actor_id": session.get("actor_id"),
            **{key: session.get("backend", {}).get(key) for key in ("native_id", "incarnation")}}
        # Keep executed application arguments independent from native input.
        # The checker compares the two; normalization must never reconcile them.
        data = {"call_id": item.get("call_id"), "tool": item.get("action"), "arguments": item.get("arguments"),
                "native_id": item.get("native_id"), "incarnation": item.get("incarnation"),
                "session_id": record["session_id"],
                **({"observation_work_id": record["work_id"]} if "work_id" in record else {}),
                **({"session_work_id": session["work_id"]} if session and "work_id" in session else {}),
                "recorded_session_identity": recorded_identity,
                "callback_actor": record.get("created_by", (session or {}).get("actor_id")),
                "callback_actor_basis": "observation" if "created_by" in record else "session",
                **({"observation_session_id": item["session_id"]} if "session_id" in item else {}),
                "callback_observation_id": record["id"], "callback_result": deepcopy(item.get("result", {})),
                "raw_ref": {"kind": "native_observation", "id": record["id"]},
                "raw_observation": deepcopy(record)}
        if item.get("phase") == "completed":
            for kind, saved_records in (("message", messages), ("evidence", evidence)):
                embedded = item.get("result", {}).get(kind)
                if isinstance(embedded, dict) and embedded.get("id") in saved_records:
                    data["persisted_" + kind] = deepcopy(saved_records[embedded["id"]])
        return data

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
                message={
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
