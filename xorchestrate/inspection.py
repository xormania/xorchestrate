"""Authorized public projections of application-owned records and history.

The store and explicit authority callbacks keep this concern independent of
message delivery, acceptance workflows and the application facade. Trusted raw
records remain unchanged; projected reads revalidate authority and snapshots.
"""
import hashlib
import json
from copy import deepcopy
from typing import Callable

from .model import JSON, Conflict, OrchestrationError
from .ports import StateStore, Transaction


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


DELIVERY_RECORDS = {"message": "message", "deliveries": "delivery", "attempts": "delivery_attempt",
    "evidence": "evidence", "sessions": "session", "messages": "message", "all_attempts": "delivery_attempt",
    "observations": "native_observation", "receipts": "delivery_receipt"}
_ACCEPTANCE_RECORDS = {"requirements": "requirements", "candidate": "candidate", "measurements": "measurement",
    "acceptance_history": "acceptance_decision", "contributions": "contribution"}
# Only application-owned result structures are traversed. In particular an
# caller/provider evidence result, session specification or native payload is
# not a record merely because its user-defined keys resemble these fields.
# Application-generated assessment evidence is a typed exception below.
_INSPECTION_RESULTS = {"get_delivery": DELIVERY_RECORDS,
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


class InspectionProjector:
    def __init__(
        self, store: StateStore, context: Callable[[str, JSON], JSON],
        authorize: Callable[[str, JSON], None], verify_context: Callable[[Transaction, JSON], None],
    ):
        self.store = store
        self._context = context
        self._authorize = authorize
        self._verify_context = verify_context

    def record_context(self, actor, kind, record, cache=None):
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
        current = self._basis(actor, arguments, cache)
        current["inspection"] = {"kind": kind, "id": record["id"]}
        current["record"] = deepcopy(record)
        return current

    def _basis(self, actor, arguments, cache):
        key = digest(arguments)
        if cache is None:
            return self._context(actor, arguments)
        if key not in cache:
            cache[key] = self._context(actor, arguments)
        return dict(cache[key])

    def project_records(self, action_name, actor, sections, cache=None):
        result, contexts = {}, []
        cache = {} if cache is None else cache
        for section, (kind, records) in sections.items():
            result[section] = []
            for record in records:
                try:
                    current = self.record_context(actor, kind, record, cache)
                    self._authorize(action_name, current)
                    payload = record if kind == "native_call" else record.get("observation", {}) if kind == "native_observation" else {}
                    executed = None
                    if payload.get("action") and isinstance(payload.get("arguments"), dict):
                        # A callback's source session need not own the resource
                        # selected by its actual arguments. Inspecting the
                        # source must not lend its grant to a different target.
                        executed = self._execution(actor, payload, cache, record["created_by"])
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
                    payload["result"], child_contexts = self.project_result(
                        action_name, actor, payload.get("action"), payload["result"], cache)
                    contexts.extend(child_contexts)
                assessment = visible if kind == "assessment" else (
                    visible.get("result") if kind == "evidence"
                    and visible.get("source", {}).get("type") == "assessment" else None)
                if isinstance(assessment, dict):
                    contexts.extend(self._assessment_binding(
                        action_name, actor, assessment, cache))
                if kind == "message" and isinstance(visible.get("routing_snapshot"), dict):
                    # Enqueue-time actor/relationship provenance is retained
                    # internally but does not inherit the message's read grant.
                    snapshot = visible["routing_snapshot"]
                    children, child_contexts = self.project_records(action_name, actor,
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

    def _assessment_binding(self, action_name, actor, assessment, cache):
        """Project only the application's typed assessment input snapshots.

        Request context, material inputs/context, evaluator output and caller or
        provider evidence remain opaque. The retained binding is never changed;
        this method receives an inspection copy, after its envelope is allowed.
        """
        binding = assessment.get("input_binding")
        if not isinstance(binding, dict):
            return []
        sections = {}
        for field, kind in (("actor_record", "actor"), ("work", "work")):
            record = binding.get(field)
            if not isinstance(record, dict):
                continue
            # Configured evaluator work fields may omit id. Its application-
            # owned assessment envelope still supplies the real work binding.
            if kind == "work":
                record = {**record, "id": assessment["work_id"]}
            sections[field] = (kind, [record])
        children, contexts = self.project_records(action_name, actor, sections, cache)
        for field, records in children.items():
            if not records:
                binding[field] = None
        return contexts

    def _execution(self, actor, payload, cache, executing_actor):
        """Retain the provider/capability scope of a recorded invocation."""
        arguments = payload["arguments"]
        scope_arguments = dict(arguments)
        if payload.get("action") in {"invoke", "run_measurement"}:
            # These actions do not accept an operation selector. Only their
            # typed result or a matching reservation can establish that binding,
            # including when an invalid request was retained before validation.
            scope_arguments.pop("operation_id", None)
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
        current = self._basis(actor, scope_arguments, cache)
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

    def project_result(self, action_name, actor, original_action, result, cache):
        """Project the typed records of a historical application result.

        Current inspection authority governs each nested record, independently
        of the source session or the permissions in force when it was saved.
        Stored results and trusted internal receipt inputs remain unchanged.
        """
        visible = deepcopy(result)
        sections = {key: (kind, values if isinstance(values, list) else [values])
            for key, kind in _INSPECTION_RESULTS.get(original_action, {}).items()
            if isinstance(values := visible.get(key), (dict, list))}
        children, contexts = self.project_records(action_name, actor, sections, cache)
        for key, values in children.items():
            visible[key] = values if isinstance(visible[key], list) else values[0] if values else None
        if original_action == "capabilities" and isinstance(visible.get("capabilities"), list):
            descriptors = []
            for descriptor in visible["capabilities"]:
                current = self._basis(actor, {}, cache)
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
                    child, child_contexts = self.project_result(action_name, actor, child_action, item, cache)
                    children.append(child)
                    contexts.extend(child_contexts)
                visible[key] = children if isinstance(nested, list) else children[0]
        return visible, contexts

    def inspect_snapshot(self, action_name, actor, context, snapshot):
        """Authorize a public aggregate without changing its trusted source."""
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            original = snapshot(tx)
        projected, contexts = self.project_result(action_name, actor, action_name, original, {})
        for kind in ("work", "operation"):
            if kind in original:
                selected = self.record_context(actor, kind, original[kind])
                self._authorize(action_name, selected)
                if projected[kind] is None:
                    raise Conflict("Selected record inspection authority changed.", retry_safe=True)
                contexts.append(selected)
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            self.verify_contexts(tx, contexts)
            if digest(original) != digest(snapshot(tx)):
                raise Conflict("Inspection records changed during authorization.", retry_safe=True)
        if digest(original) != digest(projected):
            projected["inspection_redacted"] = True
        return projected

    def verify_contexts(self, tx, contexts):
        checked = set()
        for current in contexts:
            key = digest(current["arguments"])
            if key not in checked:
                self._verify_context(tx, current)
                checked.add(key)
