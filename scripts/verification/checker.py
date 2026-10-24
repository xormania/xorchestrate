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

from .capture import canonical, digest

PEERS = ("codex", "claude", "grok")
ORIGINS = {"codex": "codex/item/tool/call", "claude": "claude/PreToolUse",
           "grok": "grok/session/update"}


class VerificationFailure(Exception):
    def __init__(self, assertion):
        super().__init__(assertion)
        self.assertion = assertion


def require(value, assertion):
    if not value:
        raise VerificationFailure(assertion)


def native_inventory(peers=PEERS):
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


def default_criteria(brief_sha256):
    return {"version": 1, "owner_basis_sha256": brief_sha256,
            "requirements": [*(f"H{i}" for i in range(1, 9)), *(f"B{i}" for i in range(1, 6)), "A1", "A2"],
            "required_cases": native_inventory()}


def signature(event):
    return canonical({key: event.get(key) for key in
                      ("event", "tool", "arguments", "native_id", "incarnation", "session_id", "origin", "observation_work_id", "session_work_id")})


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


def native_call(events, peer, tool, arguments, sessions, call_id=None):
    for event in events:
        if not (event.get("event") == "native_tool" and event.get("peer") == peer
                and event.get("tool") == tool and arguments_match(event.get("arguments"), arguments)
                and isinstance(event.get("call_id"), str) and event["call_id"]
                and event.get("origin") == ORIGINS.get(peer)
                and identity_matches(event, sessions)
                and (call_id is None or event["call_id"] == call_id)):
            continue
        conflicts = [other for other in events
                     if other.get("event") in {"native_tool", "native_discovery"}
                     and other.get("peer") == peer and other.get("incarnation") == event["incarnation"]
                     and other.get("call_id") == event["call_id"] and signature(other) != signature(event)]
        if not conflicts:
            return event
    return None


def match_callback(events, event, sessions):
    # Native proof cannot lend its identity to a contradictory application
    # callback. Validate both sides against the applicable session before joining.
    require(identity_matches(event, sessions), "callback_identity_mismatch")
    require(isinstance(event.get("call_id"), str) and event["call_id"], "callback_call_binding")
    if "recorded_session_identity" in event:
        recorded = event["recorded_session_identity"]
        require(isinstance(recorded, dict)
                and identity_matches({**recorded, "peer": event["peer"]}, sessions)
                and recorded.get("actor_id") == event["peer"], "callback_session_binding")
    if "callback_actor" in event:
        require(event["callback_actor"] == event["peer"], "callback_actor_binding")
    native = native_call(events, event["peer"], event.get("tool"), event.get("arguments"),
                         sessions, event.get("call_id"))
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
    if event.get("session_work_id") is not None:
        require(event.get("observation_work_id") == event["session_work_id"], "callback_work_binding")
    if native.get("observation_work_id") is not None or event.get("observation_work_id") is not None:
        require(native.get("observation_work_id") == event.get("observation_work_id"),
                "callback_work_binding")
        if "work_id" in event.get("arguments", {}) and "work_id" not in native.get("arguments", {}):
            require(event["arguments"]["work_id"] == event["observation_work_id"], "callback_work_binding")
    if "persisted_evidence" in event:
        persisted, arguments = event["persisted_evidence"], event["arguments"]
        require(canonical(arguments.get("result")) == canonical(persisted.get("result"))
                and arguments.get("work_id") == persisted.get("work_id"),
                "application_effect_arguments_mismatch")
        if "source" in persisted or "created_by" in persisted:
            require(persisted.get("source") == {"type": "caller", "actor": event["peer"]}
                    and persisted.get("created_by") == event["peer"], "persisted_evidence_actor_mismatch")
    if "persisted_message" in event:
        persisted, arguments = event["persisted_message"], event["arguments"]
        require(canonical(arguments.get("body")) == canonical(persisted.get("body"))
                and canonical(arguments.get("destination")) == canonical(persisted.get("destination"))
                and arguments.get("work_id") == persisted.get("work_id")
                and event.get("peer") == persisted.get("sender"),
                "application_effect_arguments_mismatch")
    return native


def exchange_evidence(events, case, nonce, memory, sessions):
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
    observed = [match_callback(events, event, sessions) for event in (challenge, reply, receipt)]
    timing = {"challenge_ack_after_reply": submissions[0]["seq"] > reply["seq"],
              "reply_ack_after_receipt": submissions[1]["seq"] > receipt["seq"]}
    if case["kind"] == "busy":
        started = next((e for e in events if e.get("event") == "hold_started" and
                        e.get("peer") == target and e.get("case_id") == case_id), None)
        finished = next((e for e in events if e.get("event") == "hold_finished" and
                         e.get("peer") == target and e.get("case_id") == case_id and e.get("released") is True), None)
        require(started and finished, "outstanding_tool_hold_missing")
        match_callback(events, started, sessions)
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


def tool_audit(events, sessions):
    actual = [e for e in events if e.get("event") == "tool_called"]
    require(actual, "native_calls_not_exercised")
    unique_actual = set()
    for event in actual:
        match_callback(events, event, sessions)
        key = (event.get("peer"), event.get("incarnation"), event.get("call_id"))
        require(key not in unique_actual, "duplicate_application_invocation")
        unique_actual.add(key)
    for event in events:
        if event.get("event") == "tool_completed":
            match_callback(events, event, sessions)
        if event.get("event") in {"native_tool", "native_discovery", "native_session"}:
            require(identity_matches(event, sessions), "native_session_drift")
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


def lifecycle_evidence(case, run, lifecycle):
    peer, operation = case["peer"], case["operation"]
    if operation == "resume":
        raw = run.get("raw_records", {})
        raw = {"session": raw.get("sessions", []), "operation": raw.get("operations", []),
               "native_observation": raw.get("observations", []), "evidence": raw.get("evidence", [])}
    else:
        raw = next((item["records"] for item in run.get("lifecycle_records", []) if item.get("peer") == peer), {})
    def by_id(kind, identifier):
        return next((row for row in raw.get(kind, []) if row.get("id") == identifier), None)
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
    require(before.get("native_id") and before["native_id"] == after.get("native_id"), "lifecycle_native_identity")
    require(measured.get("result", {}).get("session", {}).get("id") == after["session_id"], "lifecycle_result_binding")
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
    raw_native, raw_callback = native.get("observation", {}), callback.get("observation", {})
    for row in (native, callback):
        observation = row["observation"]
        identity = {"peer": peer, "session_id": row.get("session_id"),
                    **{key: observation.get(key) for key in ("native_id", "incarnation")}}
        if "session_id" in observation:
            identity["observation_session_id"] = observation["session_id"]
        require(row.get("origin") == "adapter" and row.get("stale") is False
                and identity_matches(identity, {peer: after})
                and observation.get("call_id") == retained.get("call_id"),
                "lifecycle_retained_observation_binding")
    require(raw_native.get("kind") in {"native_tool_call", "native_tool"} and raw_native.get("source")
            and raw_native.get("action") == "record_result" and raw_callback.get("phase") == "completed"
            and raw_callback.get("action") == "record_result", "lifecycle_native_callback_missing")
    expected = {"verification": {"run_id": run["run_id"], "phase": "lifecycle", "case_id": case["id"], "memory": run["memories"][peer]}}
    require(saved.get("result") == expected and raw_native.get("arguments", {}).get("result") == expected
            and raw_callback.get("arguments", {}).get("result") == expected, "lifecycle_context_lost")
    require(raw_callback.get("result", {}).get("evidence", {}).get("id") == saved["id"], "lifecycle_evidence_binding")
    return {"operation_id": measured["id"], "native_observation_id": native["id"],
            "callback_observation_id": callback["id"], "evidence_id": saved["id"]}


def check_case(case, run, fixture):
    events, sessions = run["events"], run["sessions"]
    kind = case["kind"]
    if kind in {"exchange", "busy", "work_exchange"}:
        return exchange_evidence(events, case, run["nonces"][case["id"]], run["memories"][case["target"]], sessions)
    if kind == "audit":
        return tool_audit(events, sessions)
    peer = case["peer"]
    own = [e for e in events if e.get("peer") == peer]
    if kind == "startup":
        ready = next((e for e in own if e.get("event") == "report" and e.get("report") == {
            "phase": "ready", "case_id": "startup", "nonce": "", "memory": run["memories"][peer], "peer": ""}), None)
        require(ready, "private_marker_startup_missing")
        match_callback(events, ready, sessions)
        return {"event_seqs": [ready["seq"]]}
    if kind == "work":
        result = next((e for e in own if e.get("event") == "work_submitted" and e.get("batch") == case["batch"]), None)
        require(result, "work_batch_not_exercised")
        match_callback(events, result, sessions)
        require(result.get("answer") == expected_work(fixture["batches"][case["batch"]]), "incorrect_work")
        return {"event_seqs": [result["seq"]]}
    if kind == "completion":
        activity = [e for e in own if e.get("event") == "activity"
                    and e.get("basis") == "native" and identity_matches(e, sessions)]
        latest = max(activity, key=lambda event: event["seq"]) if activity else None
        calls = [e["seq"] for e in own if e.get("event") == "tool_called"]
        # A transport acknowledgement may be observed after the model finished.
        # Completion must follow actual callbacks and remain the latest aggregate
        # state. An old idle never masks later active or uncertain native work.
        require(latest and latest.get("state") == "idle" and calls
                and latest["seq"] > max(calls), "native_completion_missing")
        return {"event_seqs": [latest["seq"]]}
    if kind == "lifecycle":
        lifecycle = next((e for e in own if e.get("event") == "lifecycle" and e.get("operation") == case["operation"]), None)
        require(lifecycle, "lifecycle_not_exercised")
        return lifecycle_evidence(case, run, lifecycle)
    raise VerificationFailure("unsupported_required_case")


def verify(run, criteria, expected_bindings, fixture):
    """Compute coverage and predicates from evidence; ignore run['status']."""
    require(run.get("bindings") == expected_bindings, "measurement_binding_mismatch")
    require(expected_bindings.get("criteria_sha256") == digest(canonical(criteria)), "criteria_binding_mismatch")
    require(expected_bindings.get("fixture_sha256") == digest(canonical(fixture)), "fixture_binding_mismatch")
    normalization_error = None
    if "raw_records" in run:
        from .normalize import normalize
        try:
            run = {**run, "events": normalize(run["raw_records"], run["sessions"], run.get("provider_events", []), run.get("mode"))}
        except VerificationFailure as error:
            normalization_error = error.assertion
            run = {**run, "events": []}
        except (KeyError, TypeError, IndexError, ValueError, AttributeError):
            normalization_error = "malformed_raw_evidence"
            run = {**run, "events": []}
        for event in run.get("lifecycle_events", []):
            run["events"].append({**event, "seq": len(run["events"]) + 1})
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
                result.update(evidence=check_case(case, run, fixture), status="passed")
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
    if run.get("mode") == "native" and any(run.get("tool_versions", {}).get(peer, {}).get("status") != "passed" for peer in PEERS):
        complete = False
        diagnostics.append("installed_versions_not_measured")
    return {"status": "passed" if complete else "incomplete", "diagnostics": diagnostics,
            "mode": run.get("mode", "unknown"), "bindings": expected_bindings,
            "requested": ids, "discovered": run.get("discovered_cases", []), "executed": executed,
            "cases": results,
            "native_acceptance": "pending_local_run" if run.get("mode") != "native" else
                ("passed" if complete else "incomplete")}
