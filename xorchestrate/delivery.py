"""Durable routing-to-native delivery, independent of transport and storage.

An attempt records submission only. Adapter observations and exchange receipts
are separate records; a successful queue write or RPC never creates a receipt.
"""
import hashlib
import json
from copy import deepcopy

from .inspection import DELIVERY_RECORDS
from .model import Capability, Conflict, OrchestrationError
from .outcomes import before_effect, effect_outcome


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def view(record):
    return {**deepcopy(record.data), "id": record.key, "version": record.version}


def action(description, properties, required=(), read=False):
    return Capability("", description, {"type": "object", "properties": properties,
        "required": list(required), "additionalProperties": False}, {"readOnlyHint": read})


S = {"type": "string", "minLength": 1}
DELIVERY_ACTIONS = {
    "dispatch_deliveries": action("Submit eligible durable deliveries; uncertain attempts are never automatically repeated.",
        {"message_id": S, "limit": {"type": "integer", "minimum": 1, "maximum": 1000}}),
    "get_delivery": action("Read durable delivery attempts, observations and receipts.", {"message_id": S}, ("message_id",), True),
    "retry_delivery": action("Schedule a definitely failed delivery under the configured retry policy.",
        {"delivery_id": S, "expected_version": {"type": "integer", "minimum": 1}}, ("delivery_id", "expected_version")),
    "resolve_delivery": action("Record an explicit unknown-outcome disposition, retaining the original uncertain attempt.",
        {"delivery_id": S, "expected_version": {"type": "integer", "minimum": 1},
         "outcome": {"enum": ["failed", "submitted"]}, "reason": S}, ("delivery_id", "expected_version", "outcome", "reason")),
    "verify_exchange": action("Evaluate an exchange with the configured receipt policy against durable native observations.",
        {"message_id": S, "recipient": S, "expectation": {"type": "object"}}, ("message_id", "recipient", "expectation")),
    "resolve_native_call": action("Reconcile a native call using its retained application result; caller results cannot manufacture proof.",
        {"native_call_id": S, "expected_version": {"type": "integer", "minimum": 1}, "reason": S},
        ("native_call_id", "expected_version", "reason")),
    "native_observations": action("Read independently observed native calls and activity.",
        {"session_id": S, "after": {"type": "integer", "minimum": 0}}, (), True),
}


class DeliveryMixin:
    def _queue_deliveries(self, tx, message, actor):
        for recipient in message.data["recipients"]:
            self._create(tx, "delivery", actor, {"message_id": message.key,
                "recipient": recipient, "work_id": message.data.get("work_id"),
                "status": "queued", "attempts": [], "current_attempt": None}, digest([message.key, recipient]))

    def _delivery_snapshot(self, tx, message_id):
        message = self._require(tx, "message", message_id)
        deliveries = [view(r) for r in tx.list("delivery") if r.data["message_id"] == message_id]
        attempts = [view(r) for r in tx.list("delivery_attempt") if r.data["message_id"] == message_id]
        sessions = [view(r) for r in tx.list("session")]
        # The receipt policy may join reply and receipt messages across an exchange.
        return {"message": view(message), "deliveries": deliveries, "attempts": attempts,
            "evidence": [view(r) for r in tx.list("evidence")],
            "sessions": sessions, "messages": [view(r) for r in tx.list("message")],
            "all_attempts": [view(r) for r in tx.list("delivery_attempt")],
            "observations": [view(r) for r in tx.list("native_observation")],
            "receipts": [view(r) for r in tx.list("delivery_receipt") if r.data["message_id"] == message_id]}

    def _get_delivery(self, args, actor, context):
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            snapshot = self._delivery_snapshot(tx, args["message_id"])
        # Receipt evaluation needs the complete exchange graph, but a public
        # inspection is not authorization to disclose that graph. Reevaluate
        # the selected policy using each record's own resource bindings.
        projected, contexts = self.inspection.project_records("get_delivery", actor,
            {key: (kind, [snapshot[key]] if key == "message" else snapshot[key])
             for key, kind in DELIVERY_RECORDS.items()})
        selected = self.inspection.record_context(actor, "message", snapshot["message"])
        self._authorize("get_delivery", selected)
        if not projected["message"]:
            raise Conflict("Selected message inspection authority changed.", retry_safe=True)
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            self._verify_context(tx, selected)
            self.inspection.verify_contexts(tx, contexts)
            if digest(snapshot) != digest(self._delivery_snapshot(tx, args["message_id"])):
                raise Conflict("Delivery inspection records changed during authorization.", retry_safe=True)
        return {**projected, "message": projected["message"][0]}

    def _dispatch_deliveries(self, args, actor, context):
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            queue = [view(r) for r in tx.list("delivery") if r.data["status"] == "queued"
                and (not args.get("message_id") or r.data["message_id"] == args["message_id"])][:args.get("limit", 100)]
        results = []
        for item in queue:
            try:
                results.append(self._dispatch_one(item["id"], actor))
            except OrchestrationError as error:
                results.append({"delivery_id": item["id"], "error": {"code": error.code, "message": str(error), "details": error.details}})
        return {"deliveries": results}

    def _dispatch_one(self, delivery_id, actor):
        with self.store.transaction() as tx:
            delivery = self._require(tx, "delivery", delivery_id)
            if delivery.data["status"] != "queued":
                return {"delivery": view(delivery)}
            message = self._require(tx, "message", delivery.data["message_id"])
            candidates = [view(r) for r in tx.list("session") if r.data.get("actor_id") == delivery.data["recipient"]
                and r.data.get("status") == "active" and (not message.data.get("work_id") or r.data.get("work_id") == message.data.get("work_id"))]
        selected = self.session_selection.select(view(message), delivery.data["recipient"], deepcopy(candidates))
        if selected is None:
            return {"delivery": view(delivery), "deferred": "no_active_session"}
        selected_session = next((s for s in candidates if s["id"] == selected), None)
        if selected_session is None:
            raise OrchestrationError("Session selection returned an ineligible session.", "invalid_selection")
        if not self.delivery_scheduler.ready(view(delivery), deepcopy(selected_session), self.clock()):
            return {"delivery": view(delivery), "deferred": "schedule"}
        # Each effect is authorized against its real recipient session and work.
        session_context = self._context(actor, {"session_id": selected})
        session_context["delivery"] = view(delivery)
        session_context["message"] = view(message)
        self._authorize("deliver_message", session_context)
        driver = self.sessions.get(selected_session["driver"])
        deliver = getattr(driver, "deliver", None)
        if not callable(deliver):
            raise OrchestrationError("The selected driver has no native delivery capability.", "unsupported_operation")
        with self.store.transaction() as tx:
            self._verify_context(tx, session_context)
            current = self._require(tx, "delivery", delivery_id)
            session = self._require(tx, "session", selected)
            if current.version != delivery.version or current.data["status"] != "queued":
                raise Conflict("Delivery was claimed concurrently.")
            if session.version != selected_session["version"] or session.data["status"] != "active":
                raise Conflict("Selected session changed before delivery reservation.")
            self._check_recovered_effects(tx, session)
            if session.data.get("pending_operation"):
                raise Conflict("Unresolved session effect requires inspection before new delivery.")
            backend = deepcopy(session.data["backend"])
            incarnation = backend.get("incarnation")
            native_id = backend.get("native_id")
            if not incarnation or not native_id:
                raise OrchestrationError("Native delivery requires a verified conversation and incarnation.", "unverified_session_result")
            attempt = self._create(tx, "delivery_attempt", actor, {"message_id": message.key,
                "delivery_id": delivery_id, "recipient": delivery.data["recipient"], "session_id": selected,
                "incarnation": incarnation, "native_id": native_id, "status": "pending", "work_id": message.data.get("work_id"),
                "sequence": len(current.data["attempts"]) + 1, "submission": None})
            self._transition(tx, current, "reserve", {**current.data, "status": "pending", "current_attempt": attempt.key,
                "attempts": [*current.data["attempts"], attempt.key]}, actor)
            self._event(tx, "delivery.reserved", actor, delivery_id=delivery_id, attempt_id=attempt.key,
                session_id=selected, incarnation=incarnation)
        envelope = {"message_id": message.key, "recipient": delivery.data["recipient"], "attempt_id": attempt.key,
            "incarnation": incarnation, "body": deepcopy(message.data["body"]), "sender": message.data["sender"],
            "work_id": message.data.get("work_id")}
        raw = None
        try:
            raw = deliver(backend, envelope)
            if not isinstance(raw, dict) or raw.get("submitted") is not True:
                raise OrchestrationError("Native delivery did not acknowledge submission.", "invalid_submission")
            for field, expected in (("native_id", native_id), ("incarnation", incarnation), ("attempt_id", attempt.key), ("message_id", message.key)):
                if field in raw and raw[field] != expected:
                    raise OrchestrationError("Native submission returned conflicting identity.", "submission_identity")
            status, failure, known_outcome = "submitted", None, "completed"
        except Exception as error:
            known_outcome = effect_outcome(error)
            status = "failed" if known_outcome in {"failed", "not_started"} else "unknown"
            details = getattr(error, "details", {})
            if known_outcome == "completed":
                if "external_result" in details:
                    raw = deepcopy(details["external_result"])
                elif "result" in details:
                    raw = deepcopy(details["result"])
            failure = {"code": getattr(error, "code", type(error).__name__), "outcome": known_outcome,
                "message": str(error) if isinstance(error, OrchestrationError) else "Native submission outcome is unknown."}
        try:
            with self.store.transaction() as tx:
                current = self._require(tx, "delivery", delivery_id)
                measured = self._require(tx, "delivery_attempt", attempt.key)
                live = self._require(tx, "session", selected)
                stale = (current.data.get("current_attempt") != attempt.key or measured.data["status"] != "pending"
                    or live.data.get("backend", {}).get("incarnation") != incarnation or live.data.get("status") not in {"active", "unknown"})
                changes = {"late_result": raw, "late_error": failure} if stale else {
                    "status": status, "submission": raw if status == "submitted" else None,
                    "rejected_result": raw if status != "submitted" else None, "error": failure,
                    "effect_outcome": known_outcome, "finished_at": self.clock(), "origin": "adapter"}
                event = "retain_late" if stale else {"submitted": "submit", "failed": "fail", "unknown": "uncertain"}[status]
                self._transition(tx, measured, event, {**measured.data, **changes}, actor)
                if not stale:
                    current = self._transition(tx, current, event, {**current.data, "status": status}, actor)
                self._event(tx, "delivery.stale_result" if stale else "delivery." + status, actor,
                    delivery_id=delivery_id, attempt_id=attempt.key)
                return {"delivery": view(current), "attempt": view(self._require(tx, "delivery_attempt", attempt.key)), "stale": stale}
        except Exception as error:
            # A lifecycle guard may refuse settlement after the adapter already
            # returned. Retain that fact independently of mutable state guards;
            # the pending attempt still fences any automatic resubmission.
            outcome = known_outcome
            observation, recording_error = None, None
            try:
                observation = self.observe_native(selected_session["driver"],
                    {"session_id": selected, "actor": actor}, {
                        "kind": "delivery_result", "phase": "settlement_unapplied", "native_id": native_id,
                        "incarnation": incarnation, "attempt_id": attempt.key, "message_id": message.key,
                        "outcome": outcome, "result": deepcopy(raw), "error": deepcopy(failure)})
            except Exception as retained_error:
                recording_error = type(retained_error).__name__
            raise OrchestrationError("The adapter outcome could not be applied; inspect retained evidence before reconciliation.",
                "result_unapplied", {"delivery_id": delivery_id, "attempt_id": attempt.key, "outcome": outcome,
                "external_result": deepcopy(raw), "observation_id": observation["id"] if observation else None,
                "recording_error": recording_error}) from error

    def _retry_delivery(self, args, actor, context):
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            delivery = self._require(tx, "delivery", args["delivery_id"])
            if delivery.version != args["expected_version"]:
                raise Conflict()
            if delivery.data["status"] != "failed":
                raise OrchestrationError("Only a definite failed attempt can be retried; unknown outcomes require explicit reconciliation.", "delivery_unresolved")
            history = [view(self._require(tx, "delivery_attempt", key)) for key in delivery.data["attempts"]]
            if not self.retry_policy.permit(view(delivery), history):
                raise OrchestrationError("The retry policy denied another attempt.", "retry_denied")
            record = self._transition(tx, delivery, "retry", {**delivery.data, "status": "queued"}, actor)
            self._event(tx, "delivery.retry_scheduled", actor, delivery_id=delivery.key)
            return {"delivery": view(record)}

    def _resolve_delivery(self, args, actor, context):
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            delivery = self._require(tx, "delivery", args["delivery_id"])
            if delivery.version != args["expected_version"]:
                raise Conflict()
            if delivery.data["status"] not in {"pending", "unknown"}:
                raise OrchestrationError("Only an unresolved delivery can be reconciled.", "invalid_state")
            attempt = self._require(tx, "delivery_attempt", delivery.data["current_attempt"])
            # This explicit disposition never manufactures a native observation or receipt.
            self._transition(tx, attempt, "reconcile", {**attempt.data, "status": "reconciled", "original_status": attempt.data["status"],
                "disposition": args["outcome"], "resolution": args["reason"], "resolved_by": actor}, actor)
            updated = self._transition(tx, delivery, "resolve_" + args["outcome"], {**delivery.data, "status": args["outcome"],
                "resolution": args["reason"]}, actor)
            self._event(tx, "delivery.resolved", actor, delivery_id=delivery.key, attempt_id=attempt.key)
            return {"delivery": view(updated)}

    def observe_native(self, driver_name, context, observation):
        retained = self._observe_native(driver_name, context, observation)
        self.record_observation("native." + str(retained["observation"].get("kind", "observation")),
            retained["observation"], {**context, "driver": driver_name, "work_id": retained.get("work_id"),
                "session_id": retained["session_id"], "native_id": retained["observation"].get("native_id"),
                "incarnation": retained["observation"].get("incarnation")}, event_id="native:" + retained["id"])
        return retained

    def _observe_native(self, driver_name, context, observation):
        """Trusted driver boundary. Never exposed as an application action."""
        context, observation = deepcopy(context), deepcopy(observation)
        session_id = context.get("session_id")
        if not session_id:
            raise OrchestrationError("Native observation has no application session binding.", "session_identity")
        with self.store.transaction() as tx:
            session = self._require(tx, "session", session_id)
            if session.data["driver"] != driver_name:
                raise OrchestrationError("Native observation driver mismatch.", "session_identity")
            native_id, incarnation = observation.get("native_id"), observation.get("incarnation")
            if not native_id or not incarnation:
                raise OrchestrationError("Native observations require conversation and incarnation.", "session_identity")
            backend = session.data.get("backend", {})
            stale = session.data["status"] not in {"starting", "active", "unknown"} or (backend and
                (backend.get("native_id") != native_id or backend.get("incarnation") != incarnation))
            phase = observation.get("phase", observation.get("kind", "observation"))
            call_id = observation.get("call_id")
            key = digest([driver_name, session_id, incarnation, call_id, phase]) if call_id else self.identifiers()
            data = {"session_id": session_id, "driver": driver_name, "work_id": session.data.get("work_id"),
                "origin": "adapter", "stale": stale, "observation": observation}
            existing = tx.get("native_observation", key)
            if existing:
                # Duplicate native call identities may replay exact evidence, never different arguments.
                comparable = {k: existing.data[k] for k in data}
                comparable = deepcopy(comparable)
                incoming = deepcopy(data)
                comparable["observation"].pop("at", None)
                incoming["observation"].pop("at", None)
                if comparable != incoming:
                    raise Conflict("A native call identity was reused with conflicting evidence.")
                return view(existing)
            record = self._create(tx, "native_observation", context.get("actor", "native"), data, key)
            return view(record)

    def invoke_native(self, driver_name, action_name, arguments, context, *, replay_only=False):
        """Durably fence semantic native call IDs even for non-idempotent actions."""
        with before_effect():
            identity = digest([driver_name, context["native_id"], context["call_id"]])
            fingerprint = digest([action_name, arguments, context["session_id"], context["incarnation"], context["actor"]])
            native = {key: context[key] for key in ("native_id", "incarnation", "call_id")}
            native.update({"kind": "application_tool", "action": action_name, "arguments": deepcopy(arguments)})
            replay = None
            with self.store.transaction() as tx:
                self._verify_native_source(tx, context, context["actor"])
                previous = tx.get("native_call", identity)
                if previous:
                    if previous.data["fingerprint"] != fingerprint:
                        raise Conflict("A native call identity was reused with different bindings or arguments.")
                    if previous.data["status"] == "completed":
                        replay = deepcopy(previous.data["result"])
                    elif previous.data["status"] == "failed":
                        raise OrchestrationError("The native call has a retained definite failure and will not execute again.",
                            "native_call_failed", {"outcome": previous.data.get("effect_outcome", "failed"),
                            "cause": previous.data.get("error")})
                    else:
                        raise OrchestrationError("The native call has an unresolved effect and will not execute again.",
                            "native_call_unresolved", {"outcome": "unknown"})
                else:
                    if replay_only:
                        raise OrchestrationError("The native replay has no retained completed application call.", "native_call_unresolved")
                    self._create(tx, "native_call", context["actor"], {"fingerprint": fingerprint,
                    "status": "pending", "session_id": context["session_id"], "native_id": context["native_id"],
                    "incarnation": context["incarnation"], "call_id": context["call_id"],
                    "action": action_name, "arguments": deepcopy(arguments), "work_id": context.get("work_id")}, identity)
        if replay is not None:
            if "idempotency_key" in arguments:
                return self.call(action_name, arguments, actor=context["actor"], native_context=context, replay_only=replay_only)
            current = self._context(context["actor"], arguments)
            current["native_source"] = deepcopy(context)
            self._authorize(action_name, current)
            with self.store.transaction() as tx:
                self._verify_context(tx, current)
            if action_name == "accept_work":
                with self.store.transaction() as tx:
                    state = self.work_acceptance.view(tx, arguments["work_id"])
                    decision = next((row for row in state["acceptance_history"] if row["id"] == replay["acceptance"]["id"]), None)
                    if not decision or not decision["current"] or state["acceptance_history"][-1]["id"] != decision["id"]:
                        raise OrchestrationError("The retained acceptance decision no longer applies; request a fresh decision.", "acceptance_stale")
            descriptor = next((entry for entry in self.describe() if entry.name == action_name), None)
            if descriptor and descriptor.annotations.get("readOnlyHint"):
                observed = self.call(action_name, arguments, actor=context["actor"], native_context=context)
                if digest(observed) != digest(replay):
                    raise OrchestrationError("The retained read result is stale; issue a new native call to observe current state.", "stale_native_result")
            return replay
        completed = False
        try:
            with before_effect():
                self.observe_native(driver_name, context, {**native, "phase": "requested"})
            # Only pre-effect authorization snapshot conflicts can retry. Each
            # attempt builds and authorizes a fresh context; uncertain effects,
            # post-effect storage failures and native results are never retried.
            retryable = {"record_result", "send_message", "invoke", "create_work", "update_work",
                "start_session", "send_session", "poll_session", "stop_session", "attach_session", "detach_session"}
            for retry in range(8):
                try:
                    result = self.call(action_name, arguments, actor=context["actor"], native_context=context)
                    break
                except Conflict as conflict:
                    if not conflict.details.get("retry_safe") or action_name not in retryable or retry == 7:
                        raise
            completed = True
            self.observe_native(driver_name, context, {**native, "phase": "completed", "result": deepcopy(result)})
            with self.store.transaction() as tx:
                row = self._require(tx, "native_call", identity)
                self._transition(tx, row, "complete", {**row.data, "status": "completed",
                    "effect_outcome": "completed", "result": deepcopy(result)}, context["actor"])
        except Exception as error:
            # Once the application returned, a later observation/storage refusal
            # cannot reclassify its completed effect as never started.
            outcome = "completed" if completed else effect_outcome(error)
            original_error = error
            if completed:
                error = OrchestrationError("The application completed, but native-call settlement needs reconciliation.",
                    "result_unapplied", {"outcome": "completed", "external_result": deepcopy(result),
                    "cause": getattr(original_error, "code", type(original_error).__name__)})
            try:
                with self.store.transaction() as tx:
                    row = self._require(tx, "native_call", identity)
                    definite = outcome in {"not_started", "failed"}
                    if row.data["status"] == "pending":
                        event = "fail" if definite else "uncertain"
                        changes = {"status": "failed" if definite else "unknown", "effect_outcome": outcome,
                            "error": getattr(error, "code", type(error).__name__)}
                    else:
                        # Concurrent evidence-based reconciliation wins. Retain
                        # the late fact without reopening its settled lifecycle.
                        event = "retain_late"
                        changes = {"late_error": getattr(error, "code", type(error).__name__),
                            "late_effect_outcome": outcome}
                    self._transition(tx, row, event, {**row.data, **changes}, context["actor"])
            except Exception as recording_error:
                if isinstance(error, OrchestrationError):
                    error.details = {**error.details, "recording_error": type(recording_error).__name__}
                else:
                    error.add_note(f"Native call outcome retention failed: {type(recording_error).__name__}")
            if completed:
                raise error from original_error
            raise
        return result

    def _native_observations(self, args, actor, context):
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            records = [view(r) for r in tx.list("native_observation") if not args.get("session_id") or r.data["session_id"] == args["session_id"]]
            calls = [view(r) for r in tx.list("native_call") if not args.get("session_id") or r.data["session_id"] == args["session_id"]]
        projected, contexts = self.inspection.project_records("native_observations", actor,
            {"observations": ("native_observation", records[args.get("after", 0):]), "calls": ("native_call", calls)})
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            self.inspection.verify_contexts(tx, contexts)
            for kind, originals in (("native_observation", records), ("native_call", calls)):
                for original in originals:
                    if view(self._require(tx, kind, original["id"])) != original:
                        raise Conflict("Native inspection record changed during authorization.", retry_safe=True)
        return projected

    def _resolve_native_call(self, args, actor, context):
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            row = self._require(tx, "native_call", args["native_call_id"])
            if row.version != args["expected_version"]:
                raise Conflict()
            if row.data["status"] not in {"pending", "unknown"}:
                raise OrchestrationError("Only unresolved native calls require reconciliation.", "invalid_state")
            observations = [r for r in tx.list("native_observation") if r.data["session_id"] == row.data["session_id"]
                and r.data.get("origin") == "adapter" and not r.data.get("stale")
                and all(r.data["observation"].get(k) == row.data[k] for k in ("native_id", "incarnation", "call_id", "action", "arguments"))
                and r.data["observation"].get("phase") == "completed" and "result" in r.data["observation"]]
            if len(observations) != 1:
                raise OrchestrationError("Reconciliation requires the retained adapter-origin application result.", "unverified_native_result")
            result = observations[0].data["observation"]["result"]
            updated = self._transition(tx, row, "resolve", {**row.data, "status": "completed",
                "effect_outcome": "completed", "result": deepcopy(result),
                "resolved_by": actor, "resolution": args["reason"], "observation_id": observations[0].key}, actor)
            return {"native_call": view(updated)}

    def _verify_exchange(self, args, actor, context):
        if self.receipt_policy is None:
            raise OrchestrationError("No exchange receipt policy is configured.", "receipt_unavailable")
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            snapshot = self._delivery_snapshot(tx, args["message_id"])
        result = self.receipt_policy.evaluate(deepcopy(snapshot), args["recipient"], deepcopy(args["expectation"]))
        if not isinstance(result, dict) or type(result.get("verified")) is not bool:
            raise OrchestrationError("Receipt policy returned an invalid decision.", "invalid_receipt")
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            if digest(snapshot) != digest(self._delivery_snapshot(tx, args["message_id"])):
                raise Conflict("Exchange evidence changed during verification.")
            receipt = self._create(tx, "delivery_receipt", actor, {"message_id": args["message_id"],
                "recipient": args["recipient"], "expectation": args["expectation"], "decision": result,
                "evidence_digest": digest(snapshot)})
            return {"receipt": view(receipt)}

