"""Durable routing-to-native delivery, independent of transport and storage.

An attempt records submission only. Adapter observations and exchange receipts
are separate records; a successful queue write or RPC never creates a receipt.
"""
import hashlib
import json
from copy import deepcopy

from .model import Capability, Conflict, OrchestrationError


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def view(record):
    return {**deepcopy(record.data), "id": record.key, "version": record.version}


def action(description, properties, required=(), read=False):
    return Capability("", description, {"type": "object", "properties": properties,
        "required": list(required), "additionalProperties": False}, {"readOnlyHint": read})


S = {"type": "string", "minLength": 1}
_DELIVERY_RECORDS = {"message": "message", "deliveries": "delivery", "attempts": "delivery_attempt",
    "evidence": "evidence", "sessions": "session", "messages": "message", "all_attempts": "delivery_attempt",
    "observations": "native_observation", "receipts": "delivery_receipt"}
_ACCEPTANCE_RECORDS = {"requirements": "requirements", "candidate": "candidate", "measurements": "measurement",
    "acceptance_history": "acceptance_decision", "contributions": "contribution"}
# Only application-owned result structures are traversed. In particular an
# evidence result, provider output, session specification or native payload is
# not a record merely because its user-defined keys resemble these fields.
_INSPECTION_RESULTS = {"get_delivery": _DELIVERY_RECORDS,
    "delivery_result": {"delivery": "delivery", "attempt": "delivery_attempt"},
    "native_observations": {"observations": "native_observation", "calls": "native_call"},
    "get_work": {"work": "work", "assignments": "assignment", "evidence": "evidence", "sessions": "session",
        **_ACCEPTANCE_RECORDS},
    "get_acceptance": _ACCEPTANCE_RECORDS, "get_operation": {"operation": "operation"},
    "inbox": {"messages": "message"}, "list_sessions": {"sessions": "session"}, "list_work": {"work": "work"},
    "list_actors": {"actors": "actor"},
    **{name: {field: kind} for name, field, kind in (
        ("create_work", "work", "work"), ("update_work", "work", "work"),
        ("add_actor", "actor", "actor"), ("update_actor", "actor", "actor"),
        ("assign", "assignment", "assignment"), ("relate", "relation", "relation"),
        ("send_message", "message", "message"), ("acknowledge", "message", "message"),
        ("record_result", "evidence", "evidence"), ("assess", "assessment", "assessment"),
        ("set_requirements", "requirements", "requirements"), ("set_candidate", "candidate", "candidate"),
        ("create_contribution", "contribution", "contribution"), ("update_contribution", "contribution", "contribution"),
        ("run_measurement", "measurement", "measurement"), ("accept_work", "acceptance", "acceptance_decision"),
        ("invoke", "invocation", "invocation"),
        ("resolve_operation", "operation", "operation"), ("resolve_native_call", "native_call", "native_call"),
        ("retry_delivery", "delivery", "delivery"), ("resolve_delivery", "delivery", "delivery"),
        ("verify_exchange", "receipt", "delivery_receipt"))},
    **{name: {"session": "session"} for name in ("start_session", "attach_session", "reconnect_session",
        "send_session", "poll_session", "stop_session", "detach_session")}}
_INSPECTION_NESTED_RESULTS = {"send_message": {"dispatch": "dispatch_deliveries"},
    "dispatch_deliveries": {"deliveries": "delivery_result"}}
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
        projected, contexts = self._inspection_records("get_delivery", actor,
            {key: (kind, [snapshot[key]] if key == "message" else snapshot[key])
             for key, kind in _DELIVERY_RECORDS.items()})
        selected = self._inspection_context(actor, "message", snapshot["message"])
        self._authorize("get_delivery", selected)
        if not projected["message"]:
            raise Conflict("Selected message inspection authority changed.", retry_safe=True)
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            self._verify_context(tx, selected)
            self._verify_inspection_contexts(tx, contexts)
            if digest(snapshot) != digest(self._delivery_snapshot(tx, args["message_id"])):
                raise Conflict("Delivery inspection records changed during authorization.", retry_safe=True)
        return {**projected, "message": projected["message"][0]}

    def _inspection_context(self, actor, kind, record, cache=None):
        arguments = {key: record[key] for key in ("work_id", "session_id", "message_id") if record.get(key)}
        if kind == "evidence" and isinstance(record.get("source"), dict):
            # Session/capability evidence carries its real source binding in
            # the application's typed source envelope, not at the top level.
            arguments.update({key: record["source"][key] for key in ("session_id", "operation_id")
                if record["source"].get(key)})
        if kind == "message":
            arguments["message_id"] = record["id"]
        elif kind == "work":
            arguments["work_id"] = record["id"]
        elif kind == "session":
            arguments["session_id"] = record["id"]
        elif kind == "native_call":
            arguments["native_call_id"] = record["id"]
        elif kind == "operation":
            arguments["operation_id"] = record["id"]
        elif kind in {"invocation", "measurement"}:
            arguments["operation_id"] = record["operation_id"]
        elif kind == "actor":
            arguments["actor_id"] = record["id"]
        elif kind == "assignment":
            arguments["actor_id"] = record["actor_id"]
        current = self._inspection_basis(actor, arguments, cache)
        current["inspection"] = {"kind": kind, "id": record["id"]}
        current["record"] = deepcopy(record)
        return current

    def _inspection_basis(self, actor, arguments, cache):
        key = digest(arguments)
        if cache is None:
            return self._context(actor, arguments)
        if key not in cache:
            cache[key] = self._context(actor, arguments)
        return dict(cache[key])

    def _inspection_records(self, action_name, actor, sections, cache=None):
        result, contexts = {}, []
        cache = {} if cache is None else cache
        for section, (kind, records) in sections.items():
            result[section] = []
            for record in records:
                try:
                    current = self._inspection_context(actor, kind, record, cache)
                    self._authorize(action_name, current)
                    payload = record if kind == "native_call" else record.get("observation", {}) if kind == "native_observation" else {}
                    executed = None
                    if payload.get("action") and isinstance(payload.get("arguments"), dict):
                        # A callback's source session need not own the resource
                        # selected by its actual arguments. Inspecting the
                        # source must not lend its grant to a different target.
                        executed = self._inspection_execution(actor, payload, cache, record["created_by"])
                        executed["inspection"] = {**current["inspection"], "action": payload["action"],
                            "binding": "executed_arguments"}
                        executed["record"] = deepcopy(record)
                        self._authorize(action_name, executed)
                except OrchestrationError as error:
                    if error.code not in {"denied", "review_required", "not_found"}:
                        raise
                    continue
                visible = deepcopy(record)
                payload = visible if kind == "native_call" else visible.get("observation", {}) if kind == "native_observation" else {}
                if kind == "operation":
                    payload = visible
                if isinstance(payload.get("result"), dict):
                    payload["result"], child_contexts = self._inspection_result(
                        action_name, actor, payload.get("action"), payload["result"], cache)
                    contexts.extend(child_contexts)
                if kind == "message" and isinstance(visible.get("routing_snapshot"), dict):
                    # Enqueue-time actor/relationship provenance is retained
                    # internally but does not inherit the message's read grant.
                    snapshot = visible["routing_snapshot"]
                    children, child_contexts = self._inspection_records(action_name, actor,
                        {key: (child_kind, snapshot[key]) for key, child_kind in
                         (("actors", "actor"), ("relations", "relation")) if isinstance(snapshot.get(key), list)}, cache)
                    snapshot.update(children)
                    contexts.extend(child_contexts)
                if digest(visible) != digest(record):
                    visible["inspection_redacted"] = True
                result[section].append(visible)
                contexts.append(current)
                if executed is not None:
                    contexts.append(executed)
        return result, contexts

    def _inspection_execution(self, actor, payload, cache, executing_actor):
        """Retain the provider/capability scope of a recorded invocation."""
        arguments = payload["arguments"]
        scope_arguments = dict(arguments)
        if payload.get("action") in {"invoke", "run_measurement"}:
            result = payload.get("result", {})
            if isinstance(result, dict):
                resource = result.get("invocation", result.get("measurement", {}))
                operation_id = result.get("operation_id") or (resource.get("operation_id") if isinstance(resource, dict) else None)
                if operation_id:
                    scope_arguments["operation_id"] = operation_id
            if "operation_id" not in scope_arguments:
                # Requested/uncertain observations can precede the result, but
                # still refer to an already reserved operation. Its key belongs
                # to the original executor, never the inspecting caller.
                operation = None
                with self.store.transaction() as tx:
                    if payload["action"] == "invoke" and isinstance(arguments.get("idempotency_key"), str):
                        operation = tx.get("operation", digest([executing_actor, arguments["idempotency_key"]]))
                        fingerprint = digest({"action": "invoke", "arguments": {
                            key: value for key, value in arguments.items() if key != "idempotency_key"}})
                        if operation and operation.data["fingerprint"] != fingerprint:
                            operation = None
                    elif payload["action"] == "run_measurement" and isinstance(arguments.get("measurement_key"), str):
                        measurement = tx.get("measurement", digest([executing_actor, "measurement", arguments["measurement_key"]]))
                        if measurement and measurement.data["fingerprint"] == digest(arguments):
                            operation = tx.get("operation", measurement.data["operation_id"])
                if operation:
                    scope_arguments["operation_id"] = operation.key
        current = self._inspection_basis(actor, scope_arguments, cache)
        # A requested native invoke can precede its durable result. Its typed
        # capability address already identifies the provider/name. Completed
        # invocations additionally carry the retained descriptor via operation.
        if payload.get("action") == "invoke" and isinstance(arguments.get("capability"), str):
            current.setdefault("provider", arguments["capability"].partition("/")[0])
            current.setdefault("capability", {"name": arguments["capability"]})
        elif payload.get("action") == "capabilities" and isinstance(arguments.get("provider"), str):
            current.setdefault("provider", arguments["provider"])
        elif payload.get("action") == "run_measurement" and "operation_id" not in scope_arguments:
            basis = current.get("acceptance_basis") or {}
            check = (basis.get("requirements") or {}).get("checks", {}).get(arguments.get("check_id"), {})
            if isinstance(check.get("capability"), str):
                current.setdefault("provider", check["capability"].partition("/")[0])
                current.setdefault("capability", {"name": check["capability"]})
        return current

    def _inspection_result(self, action_name, actor, original_action, result, cache):
        """Project the typed records of a historical application result.

        Current inspection authority governs each nested record, independently
        of the source session or the permissions in force when it was saved.
        Stored results and trusted internal receipt inputs remain unchanged.
        """
        visible = deepcopy(result)
        sections = {key: (kind, values if isinstance(values, list) else [values])
            for key, kind in _INSPECTION_RESULTS.get(original_action, {}).items()
            if isinstance(values := visible.get(key), (dict, list))}
        children, contexts = self._inspection_records(action_name, actor, sections, cache)
        for key, values in children.items():
            visible[key] = values if isinstance(visible[key], list) else values[0] if values else None
        if original_action == "capabilities" and isinstance(visible.get("capabilities"), list):
            descriptors = []
            for descriptor in visible["capabilities"]:
                current = self._inspection_basis(actor, {}, cache)
                current.update({"capability": deepcopy(descriptor),
                    "provider": descriptor["name"].partition("/")[0],
                    "inspection": {"kind": "capability", "id": descriptor["name"]}, "record": deepcopy(descriptor)})
                try:
                    self._authorize(action_name, current)
                except OrchestrationError as error:
                    if error.code not in {"denied", "review_required"}:
                        raise
                    continue
                descriptors.append(descriptor)
                contexts.append(current)
            visible["capabilities"] = descriptors
        for key, child_action in _INSPECTION_NESTED_RESULTS.get(original_action, {}).items():
            nested = visible.get(key)
            if isinstance(nested, (dict, list)):
                children = []
                for item in nested if isinstance(nested, list) else [nested]:
                    child, child_contexts = self._inspection_result(action_name, actor, child_action, item, cache)
                    children.append(child)
                    contexts.extend(child_contexts)
                visible[key] = children if isinstance(nested, list) else children[0]
        return visible, contexts

    def _inspect_snapshot(self, action_name, actor, context, snapshot):
        """Authorize a public aggregate without changing its trusted source."""
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            original = snapshot(tx)
        projected, contexts = self._inspection_result(action_name, actor, action_name, original, {})
        for kind in ("work", "operation"):
            if kind in original:
                selected = self._inspection_context(actor, kind, original[kind])
                self._authorize(action_name, selected)
                if projected[kind] is None:
                    raise Conflict("Selected record inspection authority changed.", retry_safe=True)
                contexts.append(selected)
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            self._verify_inspection_contexts(tx, contexts)
            if digest(original) != digest(snapshot(tx)):
                raise Conflict("Inspection records changed during authorization.", retry_safe=True)
        if digest(original) != digest(projected):
            projected["inspection_redacted"] = True
        return projected

    def _verify_inspection_contexts(self, tx, contexts):
        checked = set()
        for current in contexts:
            key = digest(current["arguments"])
            if key not in checked:
                self._verify_context(tx, current)
                checked.add(key)

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
        selected = self.session_selection.select(view(message), delivery.data["recipient"], deepcopy(candidates)) if self.session_selection else (max(candidates, key=lambda s: (s.get("created_at", ""), s["id"]))["id"] if candidates else None)
        if selected is None:
            return {"delivery": view(delivery), "deferred": "no_active_session"}
        selected_session = next((s for s in candidates if s["id"] == selected), None)
        if selected_session is None:
            raise OrchestrationError("Session selection returned an ineligible session.", "invalid_selection")
        if self.delivery_scheduler and not self.delivery_scheduler.ready(view(delivery), deepcopy(selected_session), self.clock()):
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
            tx.put("delivery", delivery_id, {**current.data, "status": "pending", "current_attempt": attempt.key,
                "attempts": [*current.data["attempts"], attempt.key]}, current.version)
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
            status, failure = "submitted", None
        except Exception as error:
            status = "failed" if isinstance(error, OrchestrationError) and error.details.get("outcome") in {"failed", "not_started"} else "unknown"
            failure = {"code": getattr(error, "code", type(error).__name__), "message": str(error) if isinstance(error, OrchestrationError) else "Native submission outcome is unknown."}
        with self.store.transaction() as tx:
            current = self._require(tx, "delivery", delivery_id)
            measured = self._require(tx, "delivery_attempt", attempt.key)
            live = self._require(tx, "session", selected)
            stale = (current.data.get("current_attempt") != attempt.key or measured.data["status"] != "pending"
                or live.data.get("backend", {}).get("incarnation") != incarnation or live.data.get("status") not in {"active", "unknown"})
            changes = {"late_result": raw, "late_error": failure} if stale else {
                "status": status, "submission": raw if status == "submitted" else None,
                "rejected_result": raw if status != "submitted" else None, "error": failure, "finished_at": self.clock(), "origin": "adapter"}
            tx.put("delivery_attempt", attempt.key, {**measured.data, **changes}, measured.version)
            if not stale:
                current = tx.put("delivery", delivery_id, {**current.data, "status": status}, current.version)
            self._event(tx, "delivery.stale_result" if stale else "delivery." + status, actor,
                delivery_id=delivery_id, attempt_id=attempt.key)
            return {"delivery": view(current), "attempt": view(self._require(tx, "delivery_attempt", attempt.key)), "stale": stale}

    def _retry_delivery(self, args, actor, context):
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            delivery = self._require(tx, "delivery", args["delivery_id"])
            if delivery.version != args["expected_version"]:
                raise Conflict()
            if delivery.data["status"] != "failed":
                raise OrchestrationError("Only a definite failed attempt can be retried; unknown outcomes require explicit reconciliation.", "delivery_unresolved")
            history = [view(self._require(tx, "delivery_attempt", key)) for key in delivery.data["attempts"]]
            if self.retry_policy and not self.retry_policy.permit(view(delivery), history):
                raise OrchestrationError("The retry policy denied another attempt.", "retry_denied")
            record = tx.put("delivery", delivery.key, {**delivery.data, "status": "queued"}, delivery.version)
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
            tx.put("delivery_attempt", attempt.key, {**attempt.data, "status": "reconciled", "original_status": attempt.data["status"],
                "disposition": args["outcome"], "resolution": args["reason"], "resolved_by": actor}, attempt.version)
            updated = tx.put("delivery", delivery.key, {**delivery.data, "status": args["outcome"],
                "resolution": args["reason"]}, delivery.version)
            self._event(tx, "delivery.resolved", actor, delivery_id=delivery.key, attempt_id=attempt.key)
            return {"delivery": view(updated)}

    def observe_native(self, driver_name, context, observation):
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
                else:
                    raise OrchestrationError("The native call has an unresolved effect and will not execute again.", "native_call_unresolved")
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
                    state = self._work_acceptance_view(tx, arguments["work_id"])
                    decision = next((row for row in state["acceptance_history"] if row["id"] == replay["acceptance"]["id"]), None)
                    if not decision or not decision["current"] or state["acceptance_history"][-1]["id"] != decision["id"]:
                        raise OrchestrationError("The retained acceptance decision no longer applies; request a fresh decision.", "acceptance_stale")
            descriptor = next((entry for entry in self.describe() if entry.name == action_name), None)
            if descriptor and descriptor.annotations.get("readOnlyHint"):
                observed = self.call(action_name, arguments, actor=context["actor"], native_context=context)
                if digest(observed) != digest(replay):
                    raise OrchestrationError("The retained read result is stale; issue a new native call to observe current state.", "stale_native_result")
            return replay
        try:
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
            self.observe_native(driver_name, context, {**native, "phase": "completed", "result": deepcopy(result)})
        except Exception as error:
            with self.store.transaction() as tx:
                row = self._require(tx, "native_call", identity)
                tx.put("native_call", identity, {**row.data, "status": "unknown",
                    "error": getattr(error, "code", type(error).__name__)}, row.version)
            raise
        with self.store.transaction() as tx:
            row = self._require(tx, "native_call", identity)
            tx.put("native_call", identity, {**row.data, "status": "completed", "result": deepcopy(result)}, row.version)
        return result

    def _native_observations(self, args, actor, context):
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            records = [view(r) for r in tx.list("native_observation") if not args.get("session_id") or r.data["session_id"] == args["session_id"]]
            calls = [view(r) for r in tx.list("native_call") if not args.get("session_id") or r.data["session_id"] == args["session_id"]]
        projected, contexts = self._inspection_records("native_observations", actor,
            {"observations": ("native_observation", records[args.get("after", 0):]), "calls": ("native_call", calls)})
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            self._verify_inspection_contexts(tx, contexts)
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
            updated = tx.put("native_call", row.key, {**row.data, "status": "completed", "result": deepcopy(result),
                "resolved_by": actor, "resolution": args["reason"], "observation_id": observations[0].key}, row.version)
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
