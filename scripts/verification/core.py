"""Independent predicates adapted from multi-harness-proof a944f31b7527807d.

The exchange and native-call predicates preserve the PoC's complete round trip
and acknowledgement timing interpretation. Xorchestrate adds durable attempt,
application-session and incarnation bindings. No report-level success flag is an
input to a verdict. This checker must be selected/reviewed outside the candidate
when testing whether candidate changes preserved the owner requirements.
"""
from __future__ import annotations

import itertools
from datetime import datetime

from .model import VerificationFailure, canonical, digest, require


class VerificationContext:
    """Injected protocol scope and observation hooks; no concrete peer defaults."""
    def __init__(self, origins, callback_matcher=None, identity_matcher=None):
        self.origins = origins
        self.callback_matcher = callback_matcher
        self.identity_matcher = identity_matcher or identity_matches

    def callback(self, events, event, sessions):
        if self.callback_matcher is not None:
            return self.callback_matcher(events, event, sessions)
        return match_callback(events, event, sessions, self)


def native_inventory(peers):
    """Requested coverage is declared before discovering or executing cases."""
    cases = [{"id": "startup-" + p, "kind": "startup", "peer": p} for p in peers]
    for source, target in itertools.permutations(peers, 2):
        cases.append({"id": f"direct-{source}-{target}", "kind": "exchange", "source": source, "target": target})
    for i, target in enumerate(peers):
        source = peers[(i + 1) % len(peers)]
        cases.append({"id": f"busy-{source}-{target}", "kind": "busy", "source": source, "target": target})
    for i, source in enumerate(peers):
        target = peers[(i + 1) % len(peers)]
        for burst in range(2):
            cases.append({"id": f"work-{source}-{target}-{burst}", "kind": "work_exchange",
                          "source": source, "target": target})
    for peer in peers:
        for batch in range(3):
            cases.append({"id": f"work-batch-{peer}-{batch}", "kind": "work", "peer": peer, "batch": batch})
        cases.append({"id": "completion-" + peer, "kind": "completion", "peer": peer})
        for operation in ("resume", "reconnect", "attach", "detach", "replace"):
            cases.append({"id": f"lifecycle-{peer}-{operation}", "kind": "lifecycle",
                          "peer": peer, "operation": operation})
    cases.append({"id": "native-tool-audit", "kind": "audit"})
    return cases


def signature(event):
    return canonical({key: event.get(key) for key in
                      ("event", "tool", "arguments", "native_id", "incarnation", "session_id", "origin")})


def native_claims_match(event, sessions, *, source_actor=False):
    peer = event.get("peer")
    recorded = event.get("recorded_session_identity")
    actor = sessions.get(peer, {}).get("actor_id", peer)
    if source_actor and "actor_id" not in sessions.get(peer, {}) and isinstance(recorded, dict):
        # Completion peers may name a protocol fixture, while the application
        # session belongs to its configured caller. Reconcile that caller's two
        # explicit records instead of deriving an actor from the protocol label.
        actor = recorded.get("actor_id", peer)
    if not identity_matches(event, sessions) or ("native_actor" in event and event["native_actor"] != actor):
        return False
    recorded = event.get("recorded_session_identity")
    if recorded is not None and (not identity_matches({**recorded, "peer": peer}, sessions)
                                or ("actor_id" in recorded and recorded["actor_id"] != actor)):
        return False
    return not ("session_work_id" in event and "observation_work_id" in event
                and event["session_work_id"] != event["observation_work_id"])


def native_conflict(first, other, sessions):
    # Omitted optional claims are compatible. Explicit null/wrong claims remain
    # evidence and cannot borrow the valid identity of the selected observation.
    return (signature(first) != signature(other) or not native_claims_match(other, sessions)
            or any(key in first and key in other and first[key] != other[key]
                   for key in ("observation_work_id", "session_work_id", "observation_session_id")))


def identity_matches(event, sessions):
    peer = event.get("peer")
    session = sessions.get(peer, {})
    return (all(isinstance(event.get(key), str) and bool(event[key])
                and event[key] == session.get(key)
                for key in ("native_id", "session_id", "incarnation"))
            and ("observation_session_id" not in event
                 or event["observation_session_id"] == event["session_id"]))


def arguments_match(native, application):
    """Allow only documented application enrichment, preserving native values.

    Composition injects a missing work_id and replaces/removes idempotency_key
    with its trusted session/call-derived key; it never trusts a native key.
    """
    return (isinstance(native, dict) and isinstance(application, dict)
            and all(key in application and canonical(application[key]) == canonical(value) for key, value in native.items()
                    if key != "idempotency_key")
            and set(application) - set(native) <= {"work_id", "idempotency_key"})


def native_call(events, peer, tool, arguments, sessions, call_id, context):
    for event in events:
        if not (event.get("event") == "native_tool" and event.get("peer") == peer
                and event.get("tool") == tool and arguments_match(event.get("arguments"), arguments)
                and isinstance(event.get("call_id"), str) and event["call_id"]
                and event.get("origin") == context.origins.get(peer)
                and peer in context.origins
                and event.get("expected_origin", context.origins[peer]) == context.origins[peer]
                and native_claims_match(event, sessions)
                and (call_id is None or event["call_id"] == call_id)):
            continue
        conflicts = [other for other in events
                     if other.get("event") in {"native_tool", "native_discovery"}
                     and other.get("peer") == peer
                     and ((other.get("incarnation") == event["incarnation"] and other.get("native_id") == event["native_id"])
                          or other.get("session_id") == event["session_id"])
                     and other.get("call_id") == event["call_id"] and native_conflict(event, other, sessions)]
        if not conflicts:
            return event
    return None


def effect_claims_match(embedded, persisted, peer, sessions):
    """Check only typed identity/work claims; arbitrary payloads stay opaque."""
    if not isinstance(embedded, dict):
        return False
    for key in ("id", "work_id", "created_by", "sender", "source", "session_id", "native_id", "incarnation"):
        if key in embedded and key in persisted and canonical(embedded[key]) != canonical(persisted[key]):
            return False
    for value in (embedded, persisted):
        if any(key in value and value[key] != peer for key in ("created_by", "sender")):
            return False
        if "source" in value and value["source"] != {"type": "caller", "actor": peer}:
            return False
        if any(key in value and value[key] != sessions.get(peer, {}).get(key)
               for key in ("session_id", "native_id", "incarnation")):
            return False
    return True


def match_callback(events, event, sessions, context):
    # Native proof cannot lend its identity to a contradictory application
    # callback. Validate both sides against the applicable session before joining.
    require(identity_matches(event, sessions), "callback_identity_mismatch")
    require(isinstance(event.get("call_id"), str) and event["call_id"], "callback_call_binding")
    if "recorded_session_identity" in event:
        recorded = event["recorded_session_identity"]
        require(isinstance(recorded, dict)
                and identity_matches({**recorded, "peer": event["peer"]}, sessions)
                and ("actor_id" not in recorded or recorded["actor_id"] == event["peer"]), "callback_session_binding")
    if "callback_actor" in event:
        require(event["callback_actor"] == event["peer"], "callback_actor_binding")
    if "session_work_id" in event and "observation_work_id" in event:
        require(event["observation_work_id"] == event["session_work_id"], "callback_work_binding")
    native = native_call(events, event["peer"], event.get("tool"), event.get("arguments"),
                         sessions, event.get("call_id"), context)
    require(native, "native_call_missing_or_misbound")
    # The app side effect must be tied to this callback, not just have matching
    # free-form arguments somewhere in a transcript.
    require(event.get("call_id") == native["call_id"], "callback_call_binding")
    if event.get("event") != "tool_called":
        require(any(request.get("event") == "tool_called"
                    and request.get("peer") == event["peer"]
                    and request.get("call_id") == event["call_id"]
                    and request.get("tool") == event.get("tool")
                    and identity_matches(request, sessions)
                    and canonical(request.get("arguments")) == canonical(event.get("arguments"))
                    for request in events), "callback_request_missing_or_misbound")
    if "observation_work_id" in native and "observation_work_id" in event:
        require(native["observation_work_id"] == event["observation_work_id"], "callback_work_binding")
    if "work_id" in event.get("arguments", {}) and "work_id" not in native.get("arguments", {}):
        for key in ("observation_work_id", "session_work_id"):
            if key in event:
                require(event["arguments"]["work_id"] == event[key], "callback_work_binding")
    if "persisted_evidence" in event:
        persisted, arguments = event["persisted_evidence"], event["arguments"]
        require(canonical(arguments.get("result")) == canonical(persisted.get("result"))
                and arguments.get("work_id") == persisted.get("work_id"),
                "application_effect_arguments_mismatch")
        if "source" in persisted or "created_by" in persisted:
            require(("source" not in persisted or persisted["source"] == {"type": "caller", "actor": event["peer"]})
                    and ("created_by" not in persisted or persisted["created_by"] == event["peer"]), "persisted_evidence_actor_mismatch")
        embedded = event.get("callback_result", {}).get("evidence", {})
        require(effect_claims_match(embedded, persisted, event["peer"], sessions), "application_effect_arguments_mismatch")
        if "work_id" in embedded:
            require(embedded["work_id"] == arguments.get("work_id"), "application_effect_arguments_mismatch")
    if "persisted_message" in event:
        persisted, arguments = event["persisted_message"], event["arguments"]
        require(canonical(arguments.get("body")) == canonical(persisted.get("body"))
                and canonical(arguments.get("destination")) == canonical(persisted.get("destination"))
                and arguments.get("work_id") == persisted.get("work_id")
                and event.get("peer") == persisted.get("sender"),
                "application_effect_arguments_mismatch")
        embedded = event.get("callback_result", {}).get("message", {})
        require(effect_claims_match(embedded, persisted, event["peer"], sessions), "application_effect_arguments_mismatch")
        if "work_id" in embedded:
            require(embedded["work_id"] == arguments.get("work_id"), "application_effect_arguments_mismatch")
    return native


def exchange_evidence(events, case, nonce, memory, sessions, context):
    case_id, source, target = case["id"], case["source"], case["target"]
    queued = [e for e in events if e.get("event") == "queued" and e.get("message", {}).get("case_id") == case_id]
    challenge = next((e for e in queued if e.get("peer") == source and
                      e["message"].get("to") == target and e["message"].get("kind") == "challenge"
                      and e["message"].get("nonce") == nonce and e["message"].get("memory") == ""), None)
    require(challenge, "challenge_missing_or_misbound")
    reply = next((e for e in queued if e.get("peer") == target and e["message"].get("to") == source
                  and e["message"].get("kind") == "reply" and e["message"].get("nonce") == nonce
                  and e["message"].get("memory") == memory), None)
    require(reply, "retained_context_reply_missing")
    receipt = next((e for e in events if e.get("event") == "report" and e.get("peer") == source
                    and e.get("report") == {"phase": "received", "case_id": case_id, "nonce": nonce,
                                            "memory": memory, "peer": target}), None)
    require(receipt, "sender_receipt_missing")
    require(challenge["seq"] < reply["seq"] < receipt["seq"], "exchange_event_order")
    submissions = []
    for message, recipient in ((challenge, target), (reply, source)):
        attempts = [e for e in events if e.get("event") == "attempt"
                    and e.get("message_id") == message["message"]["id"]]
        require(attempts, "delivery_attempt_missing_or_misbound")
        require(all(e.get("recipient") == recipient for e in attempts), "delivery_attempt_recipient_mismatch")
        sequences = [e.get("attempt_sequence") for e in attempts]
        require(all(type(value) is int and value > 0 for value in sequences)
                and len(sequences) == len(set(sequences))
                and len({e.get("attempt_id") for e in attempts}) == len(attempts),
                "delivery_attempt_sequence_invalid")
        latest = max(attempts, key=lambda e: e["attempt_sequence"])
        require(latest.get("status") == "submitted" and latest.get("origin") == "adapter"
                and isinstance(latest.get("submission"), dict)
                and latest["submission"].get("submitted") is True,
                "latest_delivery_attempt_not_submitted")
        candidates = [e for e in events if e.get("event") == "submitted" and e.get("peer") == recipient
                      and e.get("message_id") == message["message"]["id"] and e.get("attempt_id")
                      and e.get("state") == "submitted" and identity_matches(e, sessions)]
        require(candidates, "native_submission_missing_or_misbound")
        submitted = next((e for e in candidates if e["attempt_id"] == latest["attempt_id"]), None)
        require(submitted, "delivery_attempt_missing_or_misbound")
        require(all(latest.get(k) == submitted.get(k) for k in
                    ("message_id", "attempt_id", "peer", "session_id", "native_id", "incarnation")),
                "delivery_attempt_missing_or_misbound")
        require(message["seq"] < submitted["seq"], "submission_before_message")
        submissions.append(submitted)
    observed = [context.callback(events, event, sessions) for event in (challenge, reply, receipt)]
    timing = {"challenge_ack_after_reply": submissions[0]["seq"] > reply["seq"],
              "reply_ack_after_receipt": submissions[1]["seq"] > receipt["seq"]}
    if case["kind"] == "busy":
        started = next((e for e in events if e.get("event") == "hold_started" and
                        e.get("peer") == target and e.get("case_id") == case_id), None)
        finished = next((e for e in events if e.get("event") == "hold_finished" and
                         e.get("peer") == target and e.get("case_id") == case_id and e.get("released") is True), None)
        require(started and finished, "outstanding_tool_hold_missing")
        context.callback(events, started, sessions)
        require(started["seq"] < submissions[0]["seq"] < finished["seq"], "submission_outside_tool_hold")
        timing["reply_before_hold_finished"] = reply["seq"] < finished["seq"]
    if case["kind"] == "work_exchange":
        started = next((e for e in events if e.get("event") == "work_started" and e.get("peer") == target), None)
        finished = next((e for e in events if e.get("event") == "work_completed" and e.get("peer") == target), None)
        require(started and finished and started["seq"] < submissions[0]["seq"] < finished["seq"],
                "submission_outside_unfinished_work")
        timing["reply_before_work_completed"] = reply["seq"] < finished["seq"]
        if reply.get("at") is not None and finished.get("at") is not None:
            # Equal timestamps do not prove "before"; normalized insertion order
            # is not a replacement for an independently observed deadline.
            timing["reply_before_work_completed"] = datetime.fromisoformat(reply["at"]) < datetime.fromisoformat(finished["at"])
        require(timing["reply_before_work_completed"], "reply_after_work_completion")
    return {"event_seqs": [challenge["seq"], submissions[0]["seq"], reply["seq"], submissions[1]["seq"], receipt["seq"]],
            "native_seqs": [e["seq"] for e in observed], "timing": timing,
            "attempt_ids": [e["attempt_id"] for e in submissions]}


def tool_audit(events, sessions, context):
    actual = [e for e in events if e.get("event") == "tool_called"]
    require(actual, "native_calls_not_exercised")
    unique_actual = set()
    for event in actual:
        context.callback(events, event, sessions)
        key = (event.get("peer"), event.get("incarnation"), event.get("call_id"))
        require(key not in unique_actual, "duplicate_application_invocation")
        unique_actual.add(key)
    for event in events:
        if event.get("event") == "tool_completed":
            context.callback(events, event, sessions)
        if event.get("event") in {"native_tool", "native_discovery", "native_session"}:
            require(native_claims_match(event, sessions), "native_session_drift")
        require(event.get("event") not in {"native_identity_error", "tool_violation", "unsupported"},
                "native_protocol_violation")
    return {"audited_calls": len(actual)}


def expected_work(rows):
    latest = {}
    for row in rows:
        if row["job"] not in latest or row["attempt"] > latest[row["job"]]["attempt"]:
            latest[row["job"]] = row
    failures = [r for r in latest.values() if r["status"] == "FAIL"]
    return {"failed_jobs": sorted(r["job"] for r in failures),
            "failed_tests": sum(r["failed_tests"] for r in failures)}


def lifecycle_evidence(case, run, lifecycle, context):
    peer, operation = case["peer"], case["operation"]
    if operation == "resume":
        raw = run.get("raw_records", {})
        raw = {"session": raw.get("sessions", []), "operation": raw.get("operations", []),
               "native_observation": raw.get("observations", []), "evidence": raw.get("evidence", [])}
    else:
        raw = next((item["records"] for item in run.get("lifecycle_records", []) if item.get("peer") == peer), {})
    def by_id(kind, identifier):
        matches = [row for row in raw.get(kind, []) if row.get("id") == identifier]
        return matches[0] if len(matches) == 1 else None
    before, after = lifecycle.get("before", {}), lifecycle.get("after", {})
    measured = by_id("operation", lifecycle.get("operation_id"))
    actions = {"resume": "start_session", "attach": "attach_session", "detach": "detach_session",
               "reconnect": "reconnect_session", "replace": "reconnect_session"}
    require(measured and measured.get("status") == "completed" and measured.get("action") == actions[operation],
            "lifecycle_operation_binding")
    actual_before, actual_after = by_id("session", before.get("session_id")), by_id("session", after.get("session_id"))
    require(actual_before and actual_after, "lifecycle_session_binding")
    for view, actual in ((before, actual_before), (after, actual_after)):
        require(all(view.get(k) == actual.get("backend", {}).get(k) for k in ("native_id", "incarnation", "ownership")),
                "lifecycle_session_binding")
        require("actor_id" not in actual or actual["actor_id"] == peer, "lifecycle_actor_binding")
    require(before.get("native_id") and before["native_id"] == after.get("native_id"), "lifecycle_native_identity")
    returned = measured.get("result", {}).get("session", {})
    require(returned.get("id") == after["session_id"], "lifecycle_result_binding")
    # The typed returned session is an independent observation, not an identity
    # source that can be repaired from the durable session with the same id.
    expected_actor = actual_after.get("actor_id", run.get("sessions", {}).get(peer, {}).get("actor_id", peer))
    require(("actor_id" not in returned or returned["actor_id"] == expected_actor)
            and ("session_id" not in returned or returned["session_id"] == after["session_id"])
            and all(key not in returned or key not in actual_after or returned[key] == actual_after[key]
                    for key in ("work_id", "created_by"))
            and all(key not in returned or returned[key] == actual_after.get("backend", {}).get(key)
                    for key in ("native_id", "incarnation", "ownership")), "lifecycle_result_binding")
    if "backend" in returned:
        backend = returned["backend"]
        require(isinstance(backend, dict) and all(key not in backend or backend[key] == actual_after.get("backend", {}).get(key)
                for key in ("native_id", "incarnation", "ownership")), "lifecycle_result_binding")
    if operation == "detach":
        output = measured.get("result", {}).get("output", {})
        require(before.get("ownership") == "external" and output.get("terminated") is False,
                "external_process_terminated")
        require(measured.get("result", {}).get("session", {}).get("status") == "detached", "detach_connection_state")
        host = lifecycle.get("host_process", {})
        require(type(host.get("pid")) is int and host.get("returncode") is None and host.get("observed_at"),
                "external_process_liveness_not_observed")
        return {"operation_id": measured["id"], "host_process_observation": host}
    require(before.get("incarnation") != after.get("incarnation"), "lifecycle_incarnation_not_replaced")
    errors = lifecycle.get("stale_errors", {})
    # Explicitly recorded errors must be stale/retired connection rejections,
    # not unrelated missing arguments, authentication or timeout failures.
    require(errors.get("application", {}).get("code") in {"stale_session", "stale_connection", "invalid_state", "session_inactive", "session_unavailable"}
            and errors.get("adapter", {}).get("code") in {"stale_session", "stale_connection", "session_unavailable"},
            "stale_connection_rejection_not_measured")
    retained = lifecycle.get("retained_observation", {})
    native = by_id("native_observation", retained.get("native_observation_id"))
    callback = by_id("native_observation", retained.get("callback_observation_id"))
    saved = by_id("evidence", retained.get("evidence_id"))
    require(native and callback and saved, "lifecycle_retained_observation_binding")
    require("created_by" not in saved or saved["created_by"] == peer, "lifecycle_actor_binding")
    require("source" not in saved or saved["source"] == {"type": "caller", "actor": peer},
            "lifecycle_actor_binding")
    raw_callback = callback.get("observation", {})
    for row in (native, callback):
        require("created_by" not in row or row["created_by"] == peer, "lifecycle_actor_binding")
        observation = row["observation"]
        identity = {"peer": peer, "session_id": row.get("session_id"),
                    **{key: observation.get(key) for key in ("native_id", "incarnation")}}
        if "session_id" in observation:
            identity["observation_session_id"] = observation["session_id"]
        require(row.get("origin") == "adapter" and row.get("stale") is False
                and context.identity_matcher(identity, {peer: after})
                and observation.get("call_id") == retained.get("call_id"),
                "lifecycle_retained_observation_binding")
    facts = run.get("lifecycle_native_facts", {}).get((peer, operation), [])
    selected = next((fact for fact in facts if fact.get("raw_ref", {}).get("id") == native["id"]
                     and fact.get("event") == "native_tool"), None)
    require(selected and selected.get("tool") == "record_result"
            and selected.get("origin") == context.origins.get(peer)
            and raw_callback.get("phase") == "completed" and raw_callback.get("action") == "record_result",
            "lifecycle_native_callback_missing")
    require(not any(fact.get("event") in {"native_tool", "native_discovery"}
                    and fact.get("call_id") == selected.get("call_id")
                    and ((fact.get("incarnation") == selected.get("incarnation") and fact.get("native_id") == selected.get("native_id"))
                         or fact.get("session_id") == selected.get("session_id"))
                    and fact.get("raw_ref", {}).get("id") != native["id"]
                    and native_conflict(selected, fact, {peer: after}) for fact in facts),
            "lifecycle_retained_observation_binding")
    # Source/session scope is independent from an explicitly selected work.
    # Every applicable explicit selected-work claim must agree, including null.
    source_work = [row["work_id"] for row in (actual_after, native, callback) if "work_id" in row]
    require(not source_work or all(value == source_work[0] for value in source_work), "lifecycle_work_binding")
    native_args, callback_args = selected.get("arguments", {}), raw_callback.get("arguments", {})
    embedded = raw_callback.get("result", {}).get("evidence", {})
    selected_work = [value["work_id"] for value in (native_args, callback_args, embedded, saved) if "work_id" in value]
    require(not selected_work or all(value == selected_work[0] for value in selected_work), "lifecycle_work_binding")
    if "work_id" not in native_args and "work_id" in callback_args and source_work:
        require(callback_args["work_id"] == source_work[0], "lifecycle_work_binding")
    for row in raw.get("native_observation", []):
        observation = row.get("observation", {})
        if (row.get("id") == callback["id"] or observation.get("phase") not in {"requested", "completed"}
                or observation.get("call_id") != retained.get("call_id")
                or row.get("stale") or observation.get("stale")
                or not (row.get("session_id") == after["session_id"]
                        or (observation.get("native_id") == after["native_id"]
                            and observation.get("incarnation") == after["incarnation"]))):
            continue
        identity = {"peer": peer, "session_id": row.get("session_id"),
                    **{key: observation.get(key) for key in ("native_id", "incarnation")}}
        if "session_id" in observation:
            identity["observation_session_id"] = observation["session_id"]
        other_args = observation.get("arguments", {})
        other_effect = observation.get("result", {}).get("evidence", {})
        work_claims = [*selected_work, *[value["work_id"] for value in (other_args, other_effect) if "work_id" in value]]
        require(row.get("origin") == "adapter" and context.identity_matcher(identity, {peer: after})
                and ("created_by" not in row or row["created_by"] == peer)
                and observation.get("action") == "record_result" and arguments_match(native_args, other_args)
                and ("work_id" not in row or not source_work or row["work_id"] == source_work[0])
                and (not work_claims or all(value == work_claims[0] for value in work_claims))
                and effect_claims_match(other_effect, saved, peer, {peer: after}),
                "lifecycle_retained_observation_binding")
    expected = {"verification": {"run_id": run["run_id"], "phase": "lifecycle", "case_id": case["id"], "memory": run["memories"][peer]}}
    require(saved.get("result") == expected and selected.get("arguments", {}).get("result") == expected
            and raw_callback.get("arguments", {}).get("result") == expected, "lifecycle_context_lost")
    require(raw_callback.get("result", {}).get("evidence", {}).get("id") == saved["id"], "lifecycle_evidence_binding")
    require(effect_claims_match(embedded, saved, peer, {peer: after}), "lifecycle_evidence_binding")
    return {"operation_id": measured["id"], "native_observation_id": native["id"],
            "callback_observation_id": callback["id"], "evidence_id": saved["id"]}


def completed_native_turns(facts, sessions, peer, latest):
    """Reconcile independent accepted-work obligations and native transitions."""
    transitions, submitted = [], set()
    for fact in facts:
        if fact.get("event") != "native_turn" or fact.get("peer") != peer:
            continue
        require(native_claims_match(fact, sessions, source_actor=True), "completion_turn_identity_mismatch")
        phase, turn = fact.get("phase"), fact.get("turn_id")
        require(phase in {"submitted", "start", "end"}, "completion_turn_phase_invalid")
        if fact.get("anonymous") is True and turn in (None, "") and phase != "submitted":
            turn = None
        else:
            require(isinstance(turn, str) and turn, "completion_turn_identity_missing")
        require(all(value == turn for value in fact.get("turn_claims", {}).values()), "completion_turn_identity_mismatch")
        at = datetime.fromisoformat(fact["at"])
        require(at.tzinfo is not None and at.utcoffset() is not None, "invalid_observation_timestamp")
        if phase == "submitted":
            submitted.add(turn)
        else:
            transitions.append((at, phase, turn))
    if not transitions and not submitted:
        return
    cutoff = datetime.fromisoformat(latest["at"])
    active, finished, seen = set(), set(), {}
    anonymous, uncertain = 0, False
    for at, phase, turn in sorted(transitions, key=lambda row: (row[0], row[1], row[2] or "")):
        previous = seen.setdefault((at, turn), phase)
        require(previous == phase, "completion_turn_order_ambiguous")
        if turn is None:
            if phase == "start":
                anonymous += 1
                uncertain = uncertain or anonymous > 1
            elif anonymous:
                anonymous -= 1
                require(at <= cutoff, "completion_before_turn_finished")
            else:
                uncertain = True
            continue
        if phase == "start" and turn not in finished:
            active.add(turn)
        elif phase == "end":
            require(turn in active or turn in finished or turn in submitted, "completion_turn_unbound")
            if turn not in finished:
                require(at <= cutoff, "completion_before_turn_finished")
            active.discard(turn)
            finished.add(turn)
    require(not active and submitted <= finished and not anonymous and not uncertain, "completion_native_turn_unresolved")


def completion_evidence(own, run, peer):
    """Native idle is necessary; independent callback/effect records can veto it."""
    sessions = run["sessions"]
    session = sessions[peer]
    activity = [event for event in own if event.get("event") == "activity"
                and event.get("basis") == "native"
                and event.get("incarnation") == session["incarnation"]
                and (event.get("session_id") == session["session_id"] or event.get("native_id") == session["native_id"])]
    latest = max(activity, key=lambda event: event["seq"]) if activity else None
    calls = [event for event in own if event.get("event") == "tool_called"]
    require(latest and latest.get("state") == "idle" and calls
            and latest["seq"] > max(event["seq"] for event in calls), "native_completion_missing")
    require(native_claims_match(latest, sessions, source_actor=True), "completion_activity_identity_mismatch")
    completed = [event for event in own if event.get("event") == "tool_completed"]
    keys = ("call_id", "tool", "arguments", "session_id", "native_id", "incarnation", "callback_actor")
    for call in calls:
        require(identity_matches(call, sessions) and call.get("call_id"), "completion_callback_identity")
        matches = [event for event in completed if all(canonical(event.get(key)) == canonical(call.get(key)) for key in keys)
                   and call["seq"] < event["seq"] < latest["seq"]]
        require(len(matches) == 1, "completion_callback_unresolved")

    raw = run.get("raw_records", {})
    completed_native_turns(run.get("native_facts", []), sessions, peer, latest)
    session = sessions[peer]
    for native in raw.get("native_calls", []):
        if native.get("session_id") == session["session_id"] and native.get("incarnation") == session["incarnation"]:
            require(native.get("native_id") == session["native_id"] and native.get("status") == "completed",
                    "completion_native_call_unresolved")
    if "operations" in raw:
        operations = raw["operations"]
        for call in calls:
            key = call.get("arguments", {}).get("idempotency_key")
            if key is None:
                continue
            matches = [op for op in operations if op.get("idempotency_key") == key and op.get("actor") == call.get("callback_actor")]
            require(len(matches) == 1 and matches[0].get("status") == "completed"
                    and matches[0].get("action") == call.get("tool")
                    and canonical(matches[0].get("arguments")) == canonical({k: v for k, v in call["arguments"].items() if k != "idempotency_key"}),
                    "completion_operation_unresolved")
        for operation in operations:
            if operation.get("session_id") != session["session_id"]:
                continue
            backend = operation.get("backend", {})
            if backend and backend.get("incarnation") != session["incarnation"]:
                continue
            require(operation.get("status") in {"completed", "failed"}, "completion_operation_unresolved")
    return {"event_seqs": [latest["seq"]]}


def check_case(case, run, fixture, context):
    events, sessions = run["events"], run["sessions"]
    kind = case["kind"]
    if kind in {"exchange", "busy", "work_exchange"}:
        return exchange_evidence(events, case, run["nonces"][case["id"]], run["memories"][case["target"]], sessions, context)
    if kind == "audit":
        return tool_audit(events, sessions, context)
    peer = case["peer"]
    own = [e for e in events if e.get("peer") == peer]
    if kind == "startup":
        ready = next((e for e in own if e.get("event") == "report" and e.get("report") == {
            "phase": "ready", "case_id": "startup", "nonce": "", "memory": run["memories"][peer], "peer": ""}), None)
        require(ready, "private_marker_startup_missing")
        context.callback(events, ready, sessions)
        return {"event_seqs": [ready["seq"]]}
    if kind == "work":
        result = next((e for e in own if e.get("event") == "work_submitted" and e.get("batch") == case["batch"]), None)
        require(result, "work_batch_not_exercised")
        context.callback(events, result, sessions)
        require(result.get("answer") == expected_work(fixture["batches"][case["batch"]]), "incorrect_work")
        return {"event_seqs": [result["seq"]]}
    if kind == "completion":
        return completion_evidence(own, run, peer)
    if kind == "lifecycle":
        lifecycle = next((e for e in own if e.get("event") == "lifecycle" and e.get("operation") == case["operation"]), None)
        require(lifecycle, "lifecycle_not_exercised")
        return lifecycle_evidence(case, run, lifecycle, context)
    raise VerificationFailure("unsupported_required_case")


def verify(run, criteria, expected_bindings, fixture, context, normalization_error=None):
    """Compute coverage and predicates from evidence; ignore run['status']."""
    require(run.get("bindings") == expected_bindings, "measurement_binding_mismatch")
    require(expected_bindings.get("criteria_sha256") == digest(canonical(criteria)), "criteria_binding_mismatch")
    require(expected_bindings.get("fixture_sha256") == digest(canonical(fixture)), "fixture_binding_mismatch")
    require(run.get("mode") != "native" or "raw_records" in run, "native_raw_records_missing")
    requested = criteria["required_cases"]
    ids = [case["id"] for case in requested]
    require(len(ids) == len(set(ids)), "duplicate_required_case")
    seqs = [e.get("seq") for e in run.get("events", [])]
    require(all(type(seq) is int for seq in seqs) and seqs == sorted(set(seqs)), "event_sequence_integrity")
    executed = run.get("executed_cases", [])
    require(len(executed) == len(set(executed)), "duplicate_executed_case")
    results = []
    for case in requested:
        result = {"id": case["id"], "status": "not_exercised"}
        if normalization_error is not None:
            result.update(status="invalid_evidence", assertion=normalization_error)
        elif case["id"] in executed:
            try:
                result.update(evidence=check_case(case, run, fixture, context), status="passed")
            except VerificationFailure as exc:
                result.update(status="failed", assertion=exc.assertion)
            except (KeyError, TypeError, IndexError, ValueError) as exc:
                result.update(status="invalid_evidence", error=type(exc).__name__)
        results.append(result)
    complete = bool(results) and all(r["status"] == "passed" for r in results)
    diagnostics = [normalization_error] if normalization_error else []
    if "cleanup" in run and run["cleanup"].get("status") != "passed":
        complete = False
        diagnostics.append("cleanup_incomplete")
    if "candidate_integrity" in run and run["candidate_integrity"].get("passed") is not True:
        complete = False
        diagnostics.append("candidate_changed_during_measurement")
    if run.get("mode") == "native" and any(run.get("tool_versions", {}).get(peer, {}).get("status") != "passed" for peer in run.get("sessions", {})):
        complete = False
        diagnostics.append("installed_versions_not_measured")
    return {"status": "passed" if complete else "incomplete", "diagnostics": diagnostics,
            "mode": run.get("mode", "unknown"), "bindings": expected_bindings,
            "requested": ids, "discovered": run.get("discovered_cases", []), "executed": executed,
            "cases": results,
            "native_acceptance": "pending_local_run" if run.get("mode") != "native" else
                ("passed" if complete else "incomplete")}
