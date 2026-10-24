"""Complete exchange receipt predicate over application-owned durable records.

This policy is deliberately separate from queueing, authorization, assessment
and task acceptance. A successful native submission alone never verifies an
exchange. The verifier at scripts/verification independently checks raw evidence
and requested coverage; it does not trust this policy's verdict.
"""
from __future__ import annotations


class ExchangeFailure(Exception):
    pass


def require(condition, diagnostic):
    if not condition:
        raise ExchangeFailure(diagnostic)


def _payload(message):
    body = message.get("body")
    return body.get("verification", {}) if isinstance(body, dict) else {}


def _session(snapshot, session_id):
    return next((session for session in snapshot["sessions"] if session["id"] == session_id), None)


def _valid_observation(record, snapshot, actor):
    if record.get("origin") != "adapter" or record.get("stale") is not False:
        return False
    observation = record.get("observation", {})
    session = _session(snapshot, record.get("session_id"))
    if not session or session.get("actor_id") != actor:
        return False
    # Optional historical claims may be absent. An explicit claim, including
    # null, must agree with its own observed session rather than borrowing the
    # identity of a matching callback elsewhere in the snapshot.
    if ("created_by" in record and record["created_by"] != actor
            or "session_id" in observation and observation["session_id"] != record.get("session_id")
            or "work_id" in record and "work_id" in session and record["work_id"] != session["work_id"]):
        return False
    backend = session.get("backend", {})
    return (bool(observation.get("call_id")) and bool(observation.get("native_id"))
            and bool(observation.get("incarnation"))
            and observation["native_id"] == backend.get("native_id")
            and observation["incarnation"] == backend.get("incarnation"))


def _effect_matches(result, kind, effect, actor):
    """Join typed returned identities without interpreting opaque payloads.

    Historical callbacks may return only an id. Additional explicit identity
    claims must agree with the durable effect and its authenticated caller.
    """
    returned = result.get(kind, {}) if isinstance(result, dict) else {}
    fields = ("work_id",) + (("sender",) if kind == "message" else ("source",))
    return (isinstance(returned, dict) and returned.get("id") == effect["id"]
            and ("created_by" not in effect or effect["created_by"] == actor)
            and ("created_by" not in returned or returned["created_by"] == actor)
            and all(key not in returned or returned[key] == effect.get(key) for key in fields))


def _callback(snapshot, actor, action, arguments_match, result_match):
    records = snapshot["observations"]
    for completed in records:
        data = completed.get("observation", {})
        if not (_valid_observation(completed, snapshot, actor)
                and data.get("phase") == "completed" and data.get("action") == action
                and arguments_match(data.get("arguments", {})) and result_match(data.get("result", {}))):
            continue
        identity = (completed.get("session_id"), data["native_id"], data["incarnation"], data["call_id"])
        # A record that independently claims this application session or this
        # native connection belongs to the join even when another explicit
        # identity contradicts it. Separate historical sessions/incarnations
        # may reuse call labels without becoming evidence for the current call.
        same_call = [record for record in records
            if record.get("observation", {}).get("call_id") == identity[3]
            and (record.get("session_id") == identity[0]
                 or (record.get("observation", {}).get("native_id"),
                     record.get("observation", {}).get("incarnation")) == identity[1:3])]
        requests = [record for record in same_call if record.get("observation", {}).get("phase") == "requested"]
        require(requests, "native_request_observation_missing")
        native = [record for record in same_call if record.get("observation", {}).get("kind") in
                  {"native_tool_call", "native_tool"} and record.get("observation", {}).get("source")]
        require(native, "independent_native_observation_missing")
        for record in [r for r in same_call if r.get("observation", {}).get("phase") in {"requested", "completed", "failed"}]:
            observed = record["observation"]
            require(_valid_observation(record, snapshot, actor)
                    and observed.get("action") == action and observed.get("arguments") == data["arguments"]
                    and (observed.get("phase") != "completed" or result_match(observed.get("result", {}))),
                    "native_call_id_conflict")
        for record in native:
            observed = record["observation"]
            raw = observed.get("arguments", {})
            require(_valid_observation(record, snapshot, actor) and observed.get("action") == action
                    and isinstance(raw, dict) and all(data["arguments"].get(k) == v for k, v in raw.items()
                                                     if k != "idempotency_key")
                    and set(data["arguments"]) - set(raw) <= {"idempotency_key", "work_id"},
                    "independent_native_arguments_mismatch")
        require(all(_valid_observation(record, snapshot, actor) for record in requests), "native_request_identity_mismatch")
        return {"native": native[0]["id"], "requested": requests[0]["id"], "completed": completed["id"],
                "session_id": completed["session_id"], "call_id": data["call_id"],
                "native_id": data["native_id"], "incarnation": data["incarnation"]}
    raise ExchangeFailure("native_callback_missing_or_misbound")


def _submission(snapshot, message, recipient):
    attempts = [attempt for attempt in snapshot.get("all_attempts", snapshot["attempts"])
                if attempt.get("message_id") == message["id"] and attempt.get("recipient") == recipient]
    require(attempts, "delivery_attempt_missing")
    # A later failed/uncertain attempt must not be hidden by an old successful
    # one. Attempt sequence is assigned durably by the application.
    latest = max(attempts, key=lambda a: a.get("sequence", 0))
    require(latest.get("status") == "submitted" and latest.get("origin") == "adapter"
            and latest.get("submission", {}).get("submitted") is True, "native_submission_missing")
    session = _session(snapshot, latest.get("session_id"))
    require(session and session.get("actor_id") == recipient, "delivery_recipient_session_mismatch")
    backend = session.get("backend", {})
    require(latest.get("native_id") and latest.get("native_id") == backend.get("native_id")
            and latest.get("incarnation") and latest.get("incarnation") == backend.get("incarnation"),
            "stale_delivery_incarnation")
    return {"attempt_id": latest["id"], "session_id": session["id"],
            "incarnation": latest["incarnation"], "native_id": latest["native_id"]}


def evaluate_exchange(snapshot, recipient, expectation):
    """Return an inspectable receipt decision; never mutate caller snapshots.

    expectation identifies run_id, case_id, nonce, private memory, sender and
    recipient. Callers select this policy through composition. Only the trusted
    driver observation boundary may produce qualifying native observations.
    """
    try:
        require(all(isinstance(expectation.get(key), str) and expectation[key]
                    for key in ("run_id", "case_id", "nonce", "memory", "sender", "recipient")),
                "invalid_exchange_expectation")
        sender = expectation["sender"]
        require(expectation["recipient"] == recipient, "expectation_recipient_mismatch")
        common = {key: expectation[key] for key in ("run_id", "case_id", "nonce")}
        challenge = snapshot["message"]
        require(challenge.get("sender") == sender and recipient in challenge.get("recipients", [])
                and _payload(challenge) == {**common, "kind": "challenge", "memory": ""},
                "challenge_missing_or_misbound")
        challenge_call = _callback(snapshot, sender, "send_message",
            lambda args: args.get("body") == challenge["body"] and args.get("work_id") == challenge.get("work_id"),
            lambda result: _effect_matches(result, "message", challenge, sender))
        submitted = _submission(snapshot, challenge, recipient)
        replies = [message for message in snapshot["messages"] if message.get("sender") == recipient
                   and sender in message.get("recipients", []) and message.get("work_id") == challenge.get("work_id")
                   and _payload(message) == {**common, "kind": "reply", "memory": expectation["memory"]}]
        require(len(replies) == 1, "retained_context_reply_missing_or_duplicate")
        reply = replies[0]
        reply_call = _callback(snapshot, recipient, "send_message",
            lambda args: args.get("body") == reply["body"] and args.get("work_id") == reply.get("work_id"),
            lambda result: _effect_matches(result, "message", reply, recipient))
        require(reply_call["session_id"] == submitted["session_id"]
                and reply_call["incarnation"] == submitted["incarnation"], "reply_delivery_incarnation_mismatch")
        reply_submitted = _submission(snapshot, reply, sender)
        expected_receipt = {"verification": {**common, "phase": "received", "memory": expectation["memory"], "peer": recipient}}
        receipts = [evidence for evidence in snapshot.get("evidence", [])
                    if evidence.get("work_id") == challenge.get("work_id") and evidence.get("result") == expected_receipt
                    and evidence.get("source") == {"type": "caller", "actor": sender}]
        require(len(receipts) == 1, "sender_receipt_missing_or_duplicate")
        receipt = receipts[0]
        receipt_call = _callback(snapshot, sender, "record_result",
            lambda args: args.get("result") == expected_receipt and args.get("work_id") == challenge.get("work_id"),
            lambda result: _effect_matches(result, "evidence", receipt, sender))
        require(receipt_call["session_id"] == reply_submitted["session_id"]
                and receipt_call["incarnation"] == reply_submitted["incarnation"], "receipt_delivery_incarnation_mismatch")
        require(challenge.get("created_at") and reply.get("created_at") and receipt.get("created_at")
                and challenge["created_at"] <= reply["created_at"] <= receipt["created_at"], "exchange_event_order")
        return {"verified": True, "diagnostics": [], "message_id": challenge["id"], "reply_id": reply["id"],
                "receipt_evidence_id": receipt["id"], "submissions": [submitted, reply_submitted],
                "native_calls": [challenge_call, reply_call, receipt_call],
                "claims": {"submitted": True, "received": True, "responded": True,
                           "turn_completed": "not_measured", "task_accepted": "not_measured"}}
    except ExchangeFailure as exc:
        return {"verified": False, "diagnostics": [str(exc)]}
    except (KeyError, TypeError, ValueError):
        return {"verified": False, "diagnostics": ["malformed_exchange_evidence"]}
