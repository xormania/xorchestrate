"""Orchestration use cases, with every external dependency supplied through a port.

Capability names are ``provider/name``. Observations and assessments are retained
as evidence; only the injected guardrail decides whether an action is authorized.
External effects have a durable operation record before execution. A key is never
silently retried after an uncertain outcome, including across application restarts.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import asdict
from typing import Any, Callable

from .acceptance import ACCEPTANCE_ACTIONS, AcceptanceMixin
from .delivery import DELIVERY_ACTIONS, DeliveryMixin
from .model import JSON, Assessment, Capability, Conflict, OrchestrationError, Record
from .ports import (
    CapabilityProvider,
    Clock,
    Evaluator,
    Guardrail,
    Identifier,
    RoutingPolicy,
    SessionDriver,
    StateStore,
    Transaction,
)
from .schema import validate

_STRING = {"type": "string", "minLength": 1}
_OBJECT = {"type": "object"}
_VERSION = {"type": "integer", "minimum": 1}


def _spec(description: str, properties: JSON, required: tuple[str, ...] = (), *, read: bool = False) -> Capability:
    return Capability("", description, {
        "type": "object", "properties": properties, "required": list(required),
        "additionalProperties": False,
    }, {"readOnlyHint": read})


_ACTIONS = {
    "create_work": _spec("Create work from a goal and caller-supplied context.", {
        "goal": _STRING, "title": _STRING, "inputs": _OBJECT, "context": _OBJECT, "metadata": _OBJECT,
    }, ("goal",)),
    "get_work": _spec("Read work and its assignments, evidence, and sessions.", {"work_id": _STRING}, ("work_id",), read=True),
    "list_work": _spec("List work, optionally filtered by status.", {"status": _STRING}, read=True),
    "update_work": _spec("Update work with an explicit record version.", {
        "work_id": _STRING, "expected_version": _VERSION,
        "changes": {"type": "object", "minProperties": 1, "additionalProperties": False, "properties": {
            "goal": _STRING, "title": _STRING, "status": _STRING, "inputs": _OBJECT, "context": _OBJECT, "metadata": _OBJECT,
        }},
    }, ("work_id", "expected_version", "changes")),
    "add_actor": _spec("Register an actor with caller-defined roles.", {
        "actor_id": _STRING, "name": _STRING, "roles": {"type": "array", "items": _STRING, "uniqueItems": True}, "metadata": _OBJECT,
    }, ("actor_id",)),
    "update_actor": _spec("Update an actor without prescribing a role hierarchy.", {
        "actor_id": _STRING, "expected_version": _VERSION,
        "changes": {"type": "object", "minProperties": 1, "additionalProperties": False, "properties": {
            "name": _STRING, "roles": {"type": "array", "items": _STRING, "uniqueItems": True}, "metadata": _OBJECT,
        }},
    }, ("actor_id", "expected_version", "changes")),
    "list_actors": _spec("List registered actors.", {}, read=True),
    "assign": _spec("Assign an actor to work in an optional caller-defined role.", {
        "work_id": _STRING, "actor_id": _STRING, "role": {"type": "string"}, "metadata": _OBJECT,
    }, ("work_id", "actor_id")),
    "remove_assignment": _spec("Remove an assignment at its observed version.", {
        "assignment_id": _STRING, "expected_version": _VERSION,
    }, ("assignment_id", "expected_version")),
    "relate": _spec("Record a named, directed relationship between actors.", {
        "source": _STRING, "target": _STRING, "relationship": _STRING, "metadata": _OBJECT,
    }, ("source", "target", "relationship")),
    "remove_relation": _spec("Remove a relationship at its observed version.", {
        "relation_id": _STRING, "expected_version": _VERSION,
    }, ("relation_id", "expected_version")),
    "send_message": _spec("Route and retain a message using the configured routing policy.", {
        "destination": _OBJECT, "body": {}, "work_id": _STRING, "thread_id": _STRING,
    }, ("destination", "body")),
    "inbox": _spec("Read a recipient's retained messages.", {
        "recipient": _STRING, "include_acknowledged": {"type": "boolean"}, "work_id": _STRING,
    }, read=True),
    "acknowledge": _spec("Acknowledge a message delivered to the caller.", {"message_id": _STRING}, ("message_id",)),
    "start_session": _spec("Start a configured session driver, once per explicit idempotency key.", {
        "driver": _STRING, "specification": _OBJECT, "idempotency_key": _STRING, "work_id": _STRING, "actor_id": _STRING,
        "resume_session_id": _STRING,
    }, ("driver", "specification", "idempotency_key")),
    "send_session": _spec("Send a message through a session driver, once per key.", {
        "session_id": _STRING, "message": _OBJECT, "idempotency_key": _STRING,
    }, ("session_id", "message", "idempotency_key")),
    "poll_session": _spec("Collect session output, retaining the result for replay of this key.", {
        "session_id": _STRING, "idempotency_key": _STRING,
    }, ("session_id", "idempotency_key")),
    "stop_session": _spec("Stop a session, once per key.", {
        "session_id": _STRING, "idempotency_key": _STRING,
    }, ("session_id", "idempotency_key")),
    "list_sessions": _spec("List sessions, optionally for one work item.", {"work_id": _STRING}, read=True),
    "capabilities": _spec("Discover provider capabilities using provider/name addresses.", {"provider": _STRING}, read=True),
    "invoke": _spec("Invoke a discovered capability, once per explicit idempotency key.", {
        "capability": _STRING, "arguments": _OBJECT, "idempotency_key": _STRING, "work_id": _STRING,
    }, ("capability", "arguments", "idempotency_key")),
    "assess": _spec("Obtain an assessment, escalating uncertainty using configured evaluators.", {
        "question": _STRING, "work_id": _STRING, "context": _OBJECT,
    }, ("question",)),
    "record_result": _spec("Retain caller-supplied work evidence without changing policy.", {
        "work_id": _STRING, "result": {}, "label": _STRING, "metadata": _OBJECT,
    }, ("work_id", "result")),
    "events": _spec("Read the event stream after a sequence number.", {
        "after": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
    }, read=True),
    "get_operation": _spec("Inspect an external operation and any preserved outcome.", {"operation_id": _STRING}, ("operation_id",), read=True),
    "resolve_operation": _spec("Record a verified outcome of an interrupted effect without executing it again.", {
        "operation_id": _STRING, "expected_version": _VERSION,
        "outcome": {"enum": ["completed", "failed"]}, "result": {}, "reason": _STRING,
    }, ("operation_id", "expected_version", "outcome", "reason")),
}


_ACTIONS.update(ACCEPTANCE_ACTIONS)
_ACTIONS.update(DELIVERY_ACTIONS)
for _lifecycle_action in ("attach_session", "reconnect_session"):
    _ACTIONS[_lifecycle_action] = deepcopy(_ACTIONS["start_session"])
_ACTIONS["detach_session"] = deepcopy(_ACTIONS["stop_session"])


def _view(record: Record) -> JSON:
    return {**deepcopy(record.data), "id": record.key, "version": record.version}


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _error_message(error: Exception) -> str:
    # Expected adapter errors have deliberately public messages. Arbitrary errors
    # may embed credentials, process input or SDK internals.
    return str(error) if isinstance(error, OrchestrationError) else f"External operation did not return a verified result ({type(error).__name__})."


class Application(AcceptanceMixin, DeliveryMixin):
    """Transport-independent orchestration; construction performs no I/O."""

    def __init__(
        self, store: StateStore, providers: dict[str, CapabilityProvider],
        sessions: dict[str, SessionDriver], guardrail: Guardrail, routing: RoutingPolicy,
        evaluators: dict[str, Evaluator], clock: Clock, identifiers: Identifier,
        assessment: JSON | None = None, acceptance=None, session_selection=None,
        delivery_scheduler=None, retry_policy=None, receipt_policy=None, session_fallbacks=None, provider_configurations=None, evaluator_configurations=None,
    ):
        self.store = store
        self.acceptance = acceptance
        self.session_selection = session_selection
        self.delivery_scheduler = delivery_scheduler
        self.retry_policy = retry_policy
        self.receipt_policy = receipt_policy
        if session_fallbacks is not None and (not isinstance(session_fallbacks, dict) or any(
            not isinstance(k, str) or not k or not isinstance(v, str) or not v for k, v in session_fallbacks.items())):
            raise OrchestrationError("Session fallbacks must map requested names to configured driver names.", "configuration_error")
        self.session_fallbacks = deepcopy(session_fallbacks or {})
        self._owned_session_ids = set()
        self.providers = dict(providers)
        self.provider_configurations = deepcopy(provider_configurations or {})
        self.sessions = dict(sessions)
        self.guardrail = guardrail
        self.routing = routing
        self.evaluators = dict(evaluators)
        self.evaluator_configurations = deepcopy(evaluator_configurations or {})
        self.clock = clock
        self.identifiers = identifiers
        try:
            validate(assessment if assessment is not None else {}, {
                "type": "object", "additionalProperties": False, "properties": {
                    "triage": _STRING, "escalate": _STRING,
                    "confidence_threshold": {"type": "number", "minimum": 0, "maximum": 1},
                    "uncertain_outcomes": {"type": "array", "items": _STRING},
                    "work_input_fields": {"type": "array", "uniqueItems": True, "items": _STRING},
                },
            })
        except OrchestrationError as error:
            raise OrchestrationError("Invalid assessment configuration.", "invalid_configuration", error.details) from error
        self.assessment = deepcopy(assessment if assessment is not None else {})

    def describe(self) -> list[Capability]:
        return [Capability(name, spec.description, deepcopy(spec.input_schema), deepcopy(spec.annotations)) for name, spec in _ACTIONS.items()]

    def call(self, action: str, arguments: JSON, actor: str = "owner", *, native_context=None, replay_only=False) -> JSON:
        if action not in _ACTIONS:
            raise OrchestrationError(f"Unknown action: {action}", "unknown_action")
        if not isinstance(actor, str) or not actor:
            raise OrchestrationError("A caller identity is required.")
        validate(arguments, _ACTIONS[action].input_schema)
        # Detach caller-owned structures so neither adapters nor a caller can mutate
        # the request whose identity is bound to an operation key.
        arguments = deepcopy(arguments)
        if action in {"start_session", "attach_session", "reconnect_session"} and "resume" in arguments["specification"]:
            raise OrchestrationError("Session identity is reserved; use resume_session_id to reference an application session.", "invalid_arguments")
        context = self._context(actor, arguments)
        if native_context is not None:
            context["native_source"] = deepcopy(native_context)
            with self.store.transaction() as tx:
                self._verify_native_source(tx, context["native_source"], actor)
        resume_id = arguments.get("resume_session_id") if action in {"start_session", "reconnect_session"} else None
        if action == "resolve_operation" and context["operation"]["action"] in {"start_session", "attach_session", "reconnect_session"}:
            resume_id = context["operation"]["arguments"].get("resume_session_id")
        if resume_id:
            source = self._resume_context(actor, resume_id)
            driver = arguments["driver"] if action in {"start_session", "reconnect_session"} else context["operation"]["arguments"]["driver"]
            if source["session"]["driver"] != driver:
                raise OrchestrationError("Resume source and destination must use the same configured driver.", "driver_mismatch")
            source["resume_destination"] = {"driver": driver, "actor_id": context["session_actor"], "work_id": context["work_id"]}
            context["resume_source"] = source
        if action == "invoke":
            context.update({"provider": arguments["capability"].partition("/")[0], "capability": {"name": arguments["capability"]}})
        if "idempotency_key" in arguments:
            with self.store.transaction() as tx:
                existing = tx.get("operation", _digest([actor, arguments["idempotency_key"]]))
            if existing:
                request = {k: v for k, v in arguments.items() if k != "idempotency_key"}
                if existing.data["fingerprint"] != _digest({"action": action, "arguments": request}):
                    raise Conflict("The idempotency key is already bound to a different request.")
                if existing.data.get("capability"):
                    context["capability"] = deepcopy(existing.data["capability"])
                context["operation"] = _view(existing)
                if action in {"start_session", "attach_session", "reconnect_session"}:
                    with self.store.transaction() as tx:
                        selected = self._require(tx, "session", existing.data["session_id"])
                    context["requested_arguments"] = deepcopy(context["arguments"])
                    context["resolved_driver"] = selected.data["driver"]
                    context["arguments"] = {**context["arguments"], "driver": selected.data["driver"]}
                self._authorize(action, context)
                with self.store.transaction() as tx:
                    self._verify_context(tx, context)
                    return self._replay(self._require(tx, "operation", existing.key))
        if replay_only:
            raise OrchestrationError("The replay has no retained application operation; no new effect was invoked.", "native_call_unresolved")
        if action not in {"invoke", "capabilities"}:
            self._authorize(action, context)
        return getattr(self, f"_{action}")(arguments, actor, context)

    def _context(self, actor: str, arguments: JSON) -> JSON:
        with self.store.transaction() as tx:
            caller = tx.get("actor", actor)
            work_id = arguments.get("work_id")
            session = None
            operation = self._require(tx, "operation", arguments["operation_id"]) if arguments.get("operation_id") else None
            if operation:
                work_id = operation.data.get("work_id")
            native_call = self._require(tx, "native_call", arguments["native_call_id"]) if arguments.get("native_call_id") else None
            session_id = arguments.get("session_id") or (operation.data.get("session_id") if operation else None) or (native_call.data.get("session_id") if native_call else None)
            if session_id:
                session = self._require(tx, "session", session_id)
                work_id = session.data.get("work_id")
            if arguments.get("assignment_id"):
                work_id = self._require(tx, "assignment", arguments["assignment_id"]).data.get("work_id")
            if arguments.get("native_call_id"):
                work_id = self._require(tx, "native_call", arguments["native_call_id"]).data.get("work_id")
            if arguments.get("delivery_id"):
                work_id = self._require(tx, "delivery", arguments["delivery_id"]).data.get("work_id")
            if arguments.get("message_id"):
                work_id = self._require(tx, "message", arguments["message_id"]).data.get("work_id")
            work = self._require(tx, "work", work_id) if work_id else None
            evidence = [_view(r) for r in tx.list("evidence") if r.data.get("work_id") == work.key] if work else []
        context = {
            "actor": actor, "actor_record": _view(caller) if caller else None,
            "arguments": deepcopy(arguments), "work": _view(work) if work else None,
            "inputs": deepcopy(work.data.get("inputs", {})) if work else {},
            "context": deepcopy(work.data.get("context", {})) if work else {},
            "evidence": evidence, "session": _view(session) if session else None,
            "work_id": work.key if work else None,
            "session_actor": arguments.get("actor_id", actor) if "driver" in arguments else (session.data.get("actor_id") if session else None),
            "operation": _view(operation) if operation else None,
            "native_call": _view(native_call) if native_call else None,
        }
        if operation and operation.data.get("capability"):
            context.update({"capability": deepcopy(operation.data["capability"]),
                "provider": operation.data["arguments"]["capability"].partition("/")[0]})
        context["acceptance_basis"] = self._acceptance_context(work.key) if work else None
        return context

    def _resume_context(self, actor: str, session_id: str) -> JSON:
        source = self._context(actor, {"session_id": session_id})
        session = source["session"]
        if not isinstance(session.get("backend"), dict) or not session.get("start_operation_id"):
            raise OrchestrationError("The resume source has no verified adapter start receipt; start a fresh session.", "unverified_session_result")
        with self.store.transaction() as tx:
            receipt = self._require(tx, "operation", session["start_operation_id"])
        if (receipt.data.get("action") not in {"start_session", "attach_session", "reconnect_session"} or receipt.data.get("session_id") != session_id
                or receipt.data.get("status") != "completed" or receipt.data.get("external_result_origin") != "adapter"
                or receipt.data.get("external_result") != session["backend"]):
            raise OrchestrationError("The resume source is not bound to a verified adapter start receipt.", "unverified_session_result")
        source["resume_receipt"] = _view(receipt)
        return source

    def _authorize(self, action: str, context: JSON) -> None:
        if action in {"start_session", "reconnect_session"} and context.get("resume_source"):
            self._authorize("send_session", context["resume_source"])
        elif action == "resolve_operation" and context.get("resume_source"):
            # Reconciliation cannot turn a denied resume into an authorized
            # destination binding merely by taking a different application path.
            start_context = {**context, "arguments": deepcopy(context["operation"]["arguments"])}
            self._authorize("start_session", start_context)
        verdict = self.guardrail.evaluate(action, deepcopy(context))
        if not verdict.allowed or verdict.review:
            raise OrchestrationError(
                verdict.reason or "The configured guardrail did not authorize this action.",
                "review_required" if verdict.review else "denied", deepcopy(verdict.details),
            )

    def _verify_native_source(self, tx, source, actor):
        session = self._require(tx, "session", source["session_id"])
        backend = session.data.get("backend", {})
        if session.data.get("actor_id") != actor or session.data["status"] not in {"starting", "active", "unknown"}:
            raise OrchestrationError("The native callback source is no longer live.", "stale_native_callback")
        if backend and (backend.get("native_id") != source.get("native_id") or backend.get("incarnation") != source.get("incarnation")):
            raise OrchestrationError("Native callback incarnation no longer matches its source.", "stale_native_callback")
        if not backend:
            observations = [r for r in tx.list("native_observation") if r.data["session_id"] == session.key
                and r.data.get("origin") == "adapter" and not r.data.get("stale")
                and r.data["observation"].get("native_id") == source.get("native_id")
                and r.data["observation"].get("incarnation") == source.get("incarnation")]
            if not observations:
                raise OrchestrationError("The starting callback has no observed native binding.", "session_identity")

    def _verify_context(self, tx: Transaction, context: JSON) -> None:
        if context.get("native_source"):
            self._verify_native_source(tx, context["native_source"], context["actor"])
        # Verify the records and evidence actually supplied to this decision;
        # changes to another work item do not invalidate this authorization.
        for kind, key in (("work", "work"), ("actor", "actor_record"), ("session", "session"), ("operation", "operation"), ("operation", "resume_receipt"), ("native_call", "native_call")):
            previous = context.get(key)
            if previous:
                current = tx.get(kind, previous["id"])
                if not current or current.version != previous["version"]:
                    raise Conflict("Authorization context changed; evaluate the current state before retrying.", retry_safe=True)
        if context.get("actor_record") is None and tx.get("actor", context["actor"]) is not None:
            raise Conflict("Caller context changed; evaluate the current state before retrying.", retry_safe=True)
        work = context.get("work")
        if work:
            previous_evidence = {(item["id"], item["version"]) for item in context["evidence"]}
            current_evidence = {(item.key, item.version) for item in tx.list("evidence") if item.data.get("work_id") == work["id"]}
            if current_evidence != previous_evidence:
                raise Conflict("Authorization evidence changed; evaluate the current state before retrying.", retry_safe=True)
        self._verify_acceptance_context(tx, context)
        if context.get("resume_source"):
            self._verify_context(tx, context["resume_source"])

    @staticmethod
    def _require(tx: Transaction, kind: str, key: str) -> Record:
        record = tx.get(kind, key)
        if record is None:
            raise OrchestrationError(f"Unknown {kind}: {key}", "not_found")
        return record

    def _event(self, tx: Transaction, name: str, actor: str, **data: Any) -> None:
        tx.emit({"type": name, "actor": actor, "at": self.clock(), **deepcopy(data)})

    def _create(self, tx: Transaction, kind: str, actor: str, data: JSON, key: str | None = None) -> Record:
        record = tx.put(kind, key or self.identifiers(), {**deepcopy(data), "created_by": actor, "created_at": self.clock()})
        self._event(tx, f"{kind}.created", actor, record_id=record.key, work_id=data.get("work_id"))
        return record

    def _create_work(self, args: JSON, actor: str, context: JSON) -> JSON:
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            record = self._create(tx, "work", actor, {
                "goal": args["goal"], "title": args.get("title", args["goal"]), "status": "open",
                "inputs": args.get("inputs", {}), "context": args.get("context", {}), "metadata": args.get("metadata", {}),
            })
            return {"work": _view(record)}

    def _get_work(self, args: JSON, actor: str, context: JSON) -> JSON:
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            work = self._require(tx, "work", args["work_id"])
            result = {"work": _view(work)}
            result.update(self._work_acceptance_view(tx, work.key))
            for kind, plural in (("assignment", "assignments"), ("evidence", "evidence"), ("session", "sessions")):
                result[plural] = [_view(r) for r in tx.list(kind) if r.data.get("work_id") == work.key]
            return result

    def _list_work(self, args: JSON, actor: str, context: JSON) -> JSON:
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            return {"work": [_view(r) for r in tx.list("work") if "status" not in args or r.data.get("status") == args["status"]]}

    def _update(self, kind: str, args: JSON, actor: str, context: JSON) -> JSON:
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            current = self._require(tx, kind, args[f"{kind}_id"])
            record = tx.put(kind, current.key, {**current.data, **deepcopy(args["changes"]), "updated_at": self.clock()}, args["expected_version"])
            self._event(tx, f"{kind}.updated", actor, record_id=record.key, version=record.version)
            return {kind: _view(record)}

    def _update_work(self, args: JSON, actor: str, context: JSON) -> JSON:
        return self._update("work", args, actor, context)

    def _add_actor(self, args: JSON, actor: str, context: JSON) -> JSON:
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            record = self._create(tx, "actor", actor, {
                "name": args.get("name", args["actor_id"]), "roles": args.get("roles", []), "metadata": args.get("metadata", {}),
            }, args["actor_id"])
            return {"actor": _view(record)}

    def _update_actor(self, args: JSON, actor: str, context: JSON) -> JSON:
        return self._update("actor", args, actor, context)

    def _list_actors(self, args: JSON, actor: str, context: JSON) -> JSON:
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            return {"actors": [_view(r) for r in tx.list("actor")]}

    def _assign(self, args: JSON, actor: str, context: JSON) -> JSON:
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            self._require(tx, "actor", args["actor_id"])
            self._require(tx, "work", args["work_id"])
            role = args.get("role", "")
            existing = next((record for record in tx.list("assignment")
                if record.data.get("work_id") == args["work_id"]
                and record.data.get("actor_id") == args["actor_id"]
                and record.data.get("role", "") == role), None)
            record = existing or self._create(tx, "assignment", actor, {**args, "role": role})
            return {"assignment": _view(record)}

    def _relate(self, args: JSON, actor: str, context: JSON) -> JSON:
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            self._require(tx, "actor", args["source"])
            self._require(tx, "actor", args["target"])
            existing = next((record for record in tx.list("relation")
                if all(record.data.get(field) == args[field] for field in ("source", "target", "relationship"))), None)
            record = existing or self._create(tx, "relation", actor, args)
            return {"relation": _view(record)}

    def _remove(self, kind: str, args: JSON, actor: str, context: JSON) -> JSON:
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            key = args[f"{kind}_id"]
            tx.delete(kind, key, args["expected_version"])
            self._event(tx, f"{kind}.removed", actor, record_id=key)
            return {"removed": key}

    def _remove_assignment(self, args: JSON, actor: str, context: JSON) -> JSON:
        return self._remove("assignment", args, actor, context)

    def _remove_relation(self, args: JSON, actor: str, context: JSON) -> JSON:
        return self._remove("relation", args, actor, context)

    def _send_message(self, args: JSON, actor: str, context: JSON) -> JSON:
        with self.store.transaction() as tx:
            actors = [_view(r) for r in tx.list("actor")]
            relations = [_view(r) for r in tx.list("relation")]
        recipients = list(dict.fromkeys(self.routing.recipients(actor, deepcopy(args["destination"]), actors, relations)))
        if not recipients:
            raise OrchestrationError("The destination did not resolve to any recipients.", "no_recipients")
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            for recipient in recipients:
                self._require(tx, "actor", recipient)
            record = self._create(tx, "message", actor, {
                **args, "sender": actor, "recipients": recipients, "acknowledgements": {},
                "routing_snapshot": {"actors": actors, "relations": relations, "semantics": "resolved_at_enqueue"},
            })
            self._queue_deliveries(tx, record, actor)
            result = {"message": _view(record)}
        if getattr(self.delivery_scheduler, "dispatch_on_enqueue", False):
            try:
                result["dispatch"] = self.call("dispatch_deliveries", {"message_id": record.key}, actor=actor)
            except OrchestrationError as error:
                # Queue creation already committed. Never retry that effect merely
                # because optional immediate dispatch needs a new authorization.
                result["dispatch"] = {"error": {"code": error.code, "message": str(error), "details": error.details}}
            except Exception:
                result["dispatch"] = {"error": {"code": "dispatch_unknown", "message": "Message retained; inspect durable delivery state."}}
        return result

    def _inbox(self, args: JSON, actor: str, context: JSON) -> JSON:
        recipient = args.get("recipient", actor)
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            return {"messages": [_view(r) for r in tx.list("message")
                if recipient in r.data["recipients"]
                and (args.get("include_acknowledged", False) or recipient not in r.data["acknowledgements"])
                and ("work_id" not in args or r.data.get("work_id") == args["work_id"])]}

    def _acknowledge(self, args: JSON, actor: str, context: JSON) -> JSON:
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            message = self._require(tx, "message", args["message_id"])
            if actor not in message.data["recipients"]:
                raise OrchestrationError("Only a message recipient can acknowledge delivery.", "invalid_recipient")
            if actor not in message.data["acknowledgements"]:
                data = deepcopy(message.data)
                data["acknowledgements"][actor] = self.clock()
                message = tx.put("message", message.key, data, message.version)
                self._event(tx, "message.acknowledged", actor, message_id=message.key)
            return {"message": _view(message)}

    def _capabilities(self, args: JSON, actor: str, context: JSON) -> JSON:
        names = [args["provider"]] if "provider" in args else list(self.providers)
        capabilities = []
        for name in names:
            if name not in self.providers:
                raise OrchestrationError(f"Unknown provider: {name}", "not_found")
            provider_context = {**context, "provider": name}
            self._authorize("capabilities", provider_context)
            with self.store.transaction() as tx:
                self._verify_context(tx, provider_context)
            seen = set()
            for capability in self.providers[name].discover():
                if capability.name in seen:
                    raise OrchestrationError(f"Provider {name} returned a duplicate capability.", "invalid_capability")
                seen.add(capability.name)
                capabilities.append({**asdict(capability), "name": f"{name}/{capability.name}", "provider": name})
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            return {"capabilities": capabilities}

    def _invoke(self, args: JSON, actor: str, context: JSON) -> JSON:
        provider_name, separator, name = args["capability"].partition("/")
        if not separator or provider_name not in self.providers:
            raise OrchestrationError("Capabilities must use an available provider/name address.", "not_found")
        provider = self.providers[provider_name]
        # Discovery and invocation have distinct policy decisions. Metadata can
        # authorize invocation only after discovery has supplied the descriptor.
        self._authorize("capabilities", context)
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
        matches = [cap for cap in provider.discover() if cap.name == name]
        if len(matches) != 1:
            raise OrchestrationError(f"Capability {args['capability']} was not uniquely discovered.", "not_found")
        capability = matches[0]
        validate(args["arguments"], capability.input_schema)
        context = {**context, "provider": provider_name, "capability": {**asdict(capability), "name": args["capability"]}}
        self._authorize("invoke", context)
        return self._effect("invoke", args, actor, context, lambda operation: provider.invoke(name, deepcopy(args["arguments"]), {
            "actor": actor, "work_id": context["work_id"], "operation_id": operation.key,
            "capability": asdict(capability),
        }))

    def _start_session(self, args: JSON, actor: str, context: JSON) -> JSON:
        return self._create_session("start_session", args, actor, context)

    def _attach_session(self, args, actor, context):
        if args.get("resume_session_id"):
            raise OrchestrationError("Use reconnect_session for an existing source binding.")
        return self._create_session("attach_session", args, actor, context)

    def _reconnect_session(self, args, actor, context):
        if not args.get("resume_session_id"):
            raise OrchestrationError("Reconnect requires a verified source session.")
        return self._create_session("reconnect_session", args, actor, context)

    def _create_session(self, action, args, actor, context):
        requested_driver = args["driver"]
        resolved = requested_driver
        if resolved not in self.sessions:
            resolved = self.session_fallbacks.get(requested_driver)
            if resolved not in self.sessions or context.get("resume_source"):
                raise OrchestrationError(f"Unknown session driver: {requested_driver}", "not_found")
        context = {**context, "resolved_driver": resolved, "requested_driver": requested_driver}
        driver = self.sessions[resolved]
        # Effective configured selection is visible to authorization before any effect.
        context["requested_arguments"] = deepcopy(context["arguments"])
        context["arguments"] = {**context["arguments"], "driver": resolved}
        self._authorize(action, context)
        source = context.get("resume_source")
        method = "reconnect" if action == "reconnect_session" else ("resume" if source else ("attach" if action == "attach_session" else "start"))
        if not callable(getattr(driver, method, None)):
            raise OrchestrationError("The configured driver does not support this lifecycle operation.", "unsupported_operation", {"outcome": "not_started"})
        trusted_context = {"actor": args.get("actor_id", actor), "delegated_by": actor, "work_id": context["work_id"]}
        def perform(operation):
            specification = {**deepcopy(args["specification"]), "context": {
                **trusted_context, "session_id": operation.data["session_id"]}}
            source = context.get("resume_source")
            method = "reconnect" if action == "reconnect_session" else ("resume" if source else ("attach" if action == "attach_session" else "start"))
            invoke = getattr(driver, method, None)
            if not callable(invoke):
                raise OrchestrationError("The configured driver does not support this lifecycle operation.", "unsupported_operation", {"outcome": "not_started"})
            return invoke(deepcopy(source["session"]["backend"]), specification) if source else invoke(specification)
        result = self._effect(action, args, actor, context, perform)
        self._owned_session_ids.add(result["session"]["id"])
        return result

    def _session_effect(self, action: str, args: JSON, actor: str, context: JSON) -> JSON:
        session = context["session"]
        name = session["driver"]
        if name not in self.sessions:
            raise OrchestrationError(f"Session driver {name} is not currently configured.", "not_found")
        driver = self.sessions[name]
        def perform(operation: Record) -> Any:
            backend = deepcopy(operation.data["backend"])
            if action == "send_session":
                return driver.send(backend, deepcopy(args["message"]))
            if action == "poll_session":
                return driver.poll(backend)
            if action == "detach_session":
                detach = getattr(driver, "detach", None)
                if not callable(detach):
                    raise OrchestrationError("Driver cannot detach.", "unsupported_operation", {"outcome": "not_started"})
                return detach(backend)
            return driver.stop(backend)
        return self._effect(action, args, actor, context, perform)

    def _send_session(self, args: JSON, actor: str, context: JSON) -> JSON:
        return self._session_effect("send_session", args, actor, context)

    def _poll_session(self, args: JSON, actor: str, context: JSON) -> JSON:
        return self._session_effect("poll_session", args, actor, context)

    def _stop_session(self, args: JSON, actor: str, context: JSON) -> JSON:
        return self._session_effect("stop_session", args, actor, context)

    def _detach_session(self, args, actor, context):
        return self._session_effect("detach_session", args, actor, context)

    def runtime_closed(self):
        with self.store.transaction() as tx:
            for session_id in self._owned_session_ids:
                session = tx.get("session", session_id)
                if session and session.data["status"] in {"active", "unknown"}:
                    tx.put("session", session_id, {**session.data, "status": "disconnected", "connection_state": "closed"}, session.version)
                    self._event(tx, "session.disconnected", "runtime", session_id=session_id)

    def _list_sessions(self, args: JSON, actor: str, context: JSON) -> JSON:
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            return {"sessions": [_view(r) for r in tx.list("session") if "work_id" not in args or r.data.get("work_id") == args["work_id"]]}

    def _effect(self, action: str, args: JSON, actor: str, context: JSON, perform: Callable[[Record], Any]) -> JSON:
        key = _digest([actor, args["idempotency_key"]])
        request = {k: deepcopy(v) for k, v in args.items() if k != "idempotency_key"}
        fingerprint = _digest({"action": action, "arguments": request})
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            existing = tx.get("operation", key)
            if existing:
                if existing.data["fingerprint"] != fingerprint:
                    raise Conflict("The idempotency key is already bound to a different request.")
                return self._replay(existing)
            data = {"action": action, "arguments": request, "fingerprint": fingerprint, "actor": actor,
                    "idempotency_key": args["idempotency_key"], "status": "pending", "work_id": context["work"]["id"] if context.get("work") else None}
            if action in {"start_session", "attach_session", "reconnect_session"}:
                if args.get("actor_id"):
                    self._require(tx, "actor", args["actor_id"])
                provenance = {}
                if context.get("resume_source"):
                    source = context["resume_source"]
                    if source["session"].get("pending_operation") and action != "reconnect_session":
                        raise Conflict("The resume source has a pending or unresolved operation; reconcile it before another effect.")
                    provenance["resume_source"] = {"session_id": source["session"]["id"], "version": source["session"]["version"],
                        "start_operation_id": source["resume_receipt"]["id"]}
                    data.update(deepcopy(provenance))
                    data["unresolved_effects"] = list(dict.fromkeys([
                        *source["session"].get("unresolved_effects", []),
                        *([source["session"]["pending_operation"]] if source["session"].get("pending_operation") else []),
                    ]))
                session = self._create(tx, "session", actor, {
                    "driver": context.get("resolved_driver", args["driver"]),
                    "selection": {"requested": args["driver"], "resolved": context.get("resolved_driver", args["driver"]),
                        "fallback_reason": "requested_driver_unavailable" if context.get("resolved_driver", args["driver"]) != args["driver"] else None}, "specification": args["specification"], "work_id": data["work_id"],
                    "actor_id": args.get("actor_id", actor), "status": "starting", "backend": {}, "pending_operation": key,
                    "start_operation_id": key, "unresolved_effects": data.get("unresolved_effects", []), **provenance,
                })
                data["session_id"] = session.key
            elif action.endswith("_session"):
                session = self._require(tx, "session", args["session_id"])
                independent = action in {"poll_session", "stop_session", "detach_session"}
                if not independent:
                    self._check_recovered_effects(tx, session)
                if session.data.get("pending_operation") and not independent:
                    raise Conflict("A session operation is pending or unresolved; inspect it before another effect.")
                if session.data["status"] not in ({"active", "unknown"} if independent else {"active"}):
                    raise OrchestrationError("The session is not active.", "invalid_state")
                if session.version != context["session"]["version"]:
                    raise Conflict("The session changed after authorization.")
                data.update({"session_id": session.key, "backend": deepcopy(session.data["backend"]), "independent": independent})
                if not independent:
                    tx.put("session", session.key, {**session.data, "pending_operation": key}, session.version)
            elif action == "invoke":
                data["capability"] = deepcopy(context["capability"])
                self._create(tx, "invocation", actor, {
                    "capability": args["capability"], "arguments": args["arguments"], "work_id": data["work_id"],
                    "operation_id": key, "status": "pending",
                }, key)
            operation = self._create(tx, "operation", actor, data, key)
        try:
            raw = perform(operation)
        except Exception as error:
            unknown = not isinstance(error, OrchestrationError) or error.details.get("outcome") not in {"failed", "not_started"}
            status = "unknown" if unknown else "failed"
            recording_error = None
            try:
                self._fail_effect(key, error, status)
            except Exception as storage_error:
                recording_error = _error_message(storage_error)
            raise OrchestrationError(_error_message(error), "operation_unknown" if unknown else "operation_failed", {
                "operation_id": key, "outcome": status, "cause": getattr(error, "code", type(error).__name__),
                "recording_error": recording_error,
            }) from error
        try:
            with self.store.transaction() as tx:
                current = self._require(tx, "operation", key)
                if current.data["status"] != "pending":
                    raise Conflict("The operation was reconciled while its external call was in progress.")
                result = self._apply_effect(tx, current, raw)
                tx.put("operation", key, {**current.data, "status": "completed", "result": result,
                    "external_result": deepcopy(raw), "external_result_origin": "adapter", "finished_at": self.clock()}, current.version)
                self._event(tx, "operation.completed", actor, operation_id=key, work_id=current.data.get("work_id"))
                return result
        except Exception as error:
            # The external effect succeeded even if a local write did not. Preserve
            # that result for reconciliation instead of labelling it a failed call.
            try:
                with self.store.transaction() as tx:
                    current = self._require(tx, "operation", key)
                    changes = {"external_result": deepcopy(raw), "external_result_origin": "adapter", "application_error": _error_message(error)}
                    if current.data["status"] in {"pending", "unknown"}:
                        changes["status"] = "completed_unapplied"
                    else:
                        changes = {"late_external_result": deepcopy(raw), "application_error": _error_message(error)}
                    tx.put("operation", key, {**current.data, **changes}, current.version)
                    self._event(tx, "operation.result_retained", actor, operation_id=key)
            except Exception:
                # Storage may be entirely unavailable; return the actual outcome
                # to the caller so it is not lost or mistaken for permission to retry.
                pass
            raise OrchestrationError("The external effect completed, but its result needs reconciliation.", "result_unapplied", {
                "operation_id": key, "outcome": "completed", "external_result": deepcopy(raw),
            }) from error

    @staticmethod
    def _replay(operation: Record) -> JSON:
        status = operation.data["status"]
        if status == "completed":
            return deepcopy(operation.data["result"])
        raise OrchestrationError(
            f"This key has an existing {status} operation; it will not execute again.",
            "operation_failed" if status == "failed" else "operation_unresolved",
            {"operation_id": operation.key, "outcome": status, "error": operation.data.get("error")},
        )

    def _check_recovered_effects(self, tx, session):
        unresolved = [key for key in session.data.get("unresolved_effects", [])
            if self._require(tx, "operation", key).data["status"] in {"pending", "unknown", "completed_unapplied"}]
        if unresolved:
            raise Conflict("The reconnected conversation has unresolved source effects; observation/control remain available.")

    def _apply_effect(self, tx: Transaction, operation: Record, raw: Any, *, reconciling=False) -> JSON:
        action, actor = operation.data["action"], operation.data["actor"]
        if action == "invoke":
            if not isinstance(raw, dict):
                raise OrchestrationError("Capability output must be a JSON object.", "invalid_result")
            invocation = self._require(tx, "invocation", operation.key)
            record = tx.put("invocation", invocation.key, {**invocation.data, "status": "completed", "result": deepcopy(raw)}, invocation.version)
            if operation.data.get("work_id"):
                self._evidence(tx, actor, operation.data["work_id"], raw, {"type": "capability", "operation_id": operation.key})
            return {"invocation": _view(record), "operation_id": operation.key}
        session = self._require(tx, "session", operation.data["session_id"])
        independent = operation.data.get("independent", False)
        if not independent and session.data.get("pending_operation") != operation.key:
            raise Conflict("The session is no longer reserved by this operation.")
        if action not in {"start_session", "attach_session", "reconnect_session"} and session.data.get("backend") != operation.data.get("backend"):
            raise Conflict("Session incarnation changed while the operation was outstanding.")
        if action == "send_session" and session.data["status"] in {"stopped", "detached", "disconnected", "superseded"}:
            if reconciling:
                tx.put("session", session.key, {**session.data, "pending_operation": None}, session.version)
                return {"session": _view(self._require(tx, "session", session.key)), "output": deepcopy(raw), "operation_id": operation.key}
            raise Conflict("The session closed before a late submission result.")
        data = {**session.data, "updated_at": self.clock()}
        if not independent:
            data["pending_operation"] = None
        if action in {"start_session", "attach_session", "reconnect_session"}:
            if not isinstance(raw, dict):
                raise OrchestrationError("Session start must return a JSON object.", "invalid_result")
            data.update({"backend": deepcopy(raw), "status": "active"})
            if operation.data.get("resume_source"):
                source = self._require(tx, "session", operation.data["resume_source"]["session_id"])
                tx.put("session", source.key, {**source.data, "status": "superseded", "replacement_session_id": session.key}, source.version)
                self._event(tx, "session.replaced", actor, session_id=source.key, replacement_session_id=session.key)
        elif action == "poll_session":
            if not isinstance(raw, list) or any(not isinstance(item, dict) for item in raw):
                raise OrchestrationError("Session poll must return a list of JSON objects.", "invalid_result")
            # Polling does not reconcile uncertainty or reopen a stopped connection.
        else:
            if not isinstance(raw, dict):
                raise OrchestrationError("Session operations must return a JSON object.", "invalid_result")
            if action in {"stop_session", "detach_session"}:
                data["status"] = "stopped" if action == "stop_session" else "detached"
                data["connection_state"] = "closed"
            elif not independent:
                data["status"] = "active"
        record = tx.put("session", session.key, data, session.version)
        self._event(tx, f"session.{action.removesuffix('_session')}", actor, session_id=session.key, operation_id=operation.key)
        if operation.data.get("work_id") and action in {"send_session", "poll_session"}:
            self._evidence(tx, actor, operation.data["work_id"], raw, {"type": "session", "session_id": session.key, "operation_id": operation.key})
        return {"session": _view(record), "output": deepcopy(raw), "operation_id": operation.key}

    def _fail_effect(self, key: str, error: Exception, status: str) -> None:
        with self.store.transaction() as tx:
            operation = self._require(tx, "operation", key)
            if operation.data["status"] != "pending":
                return
            details = {"message": _error_message(error), "code": getattr(error, "code", type(error).__name__), "details": deepcopy(getattr(error, "details", {}))}
            tx.put("operation", key, {**operation.data, "status": status, "error": details, "finished_at": self.clock()}, operation.version)
            self._mark_effect_failure(tx, operation, status)
            self._event(tx, f"operation.{status}", operation.data["actor"], operation_id=key)

    def _mark_effect_failure(self, tx: Transaction, operation: Record, status: str) -> None:
        if operation.data["action"] == "invoke":
            invocation = self._require(tx, "invocation", operation.key)
            tx.put("invocation", invocation.key, {**invocation.data, "status": status}, invocation.version)
        else:
            session = self._require(tx, "session", operation.data["session_id"])
            if operation.data.get("independent"):
                return
            if session.data.get("pending_operation") != operation.key:
                raise Conflict("The session reservation changed.")
            next_status = "unknown" if status == "unknown" else ("failed" if operation.data["action"] in {"start_session", "attach_session", "reconnect_session"} else "active")
            if session.data["status"] in {"stopped", "detached", "disconnected", "superseded"}:
                next_status = session.data["status"]
            tx.put("session", session.key, {**session.data, "status": next_status,
                "pending_operation": operation.key if status == "unknown" else None}, session.version)

    def _get_operation(self, args: JSON, actor: str, context: JSON) -> JSON:
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            return {"operation": _view(self._require(tx, "operation", args["operation_id"]))}

    def _resolve_operation(self, args: JSON, actor: str, context: JSON) -> JSON:
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            operation = self._require(tx, "operation", args["operation_id"])
            if operation.version != args["expected_version"]:
                raise Conflict()
            if operation.data["status"] not in {"pending", "unknown", "completed_unapplied"}:
                raise OrchestrationError("Only unresolved operations can be reconciled.", "invalid_state")
            data = {**operation.data, "status": args["outcome"], "resolved_by": actor, "resolution": args["reason"], "finished_at": self.clock()}
            if args["outcome"] == "completed":
                if operation.data["action"] in {"start_session", "attach_session", "reconnect_session"}:
                    if "result" in args or "external_result" not in operation.data or operation.data.get("external_result_origin") != "adapter":
                        raise OrchestrationError("Session start completion requires its retained adapter receipt; caller-supplied results cannot bind a session.", "unverified_session_result")
                    raw = operation.data["external_result"]
                else:
                    if "result" not in args and "external_result" not in operation.data:
                        raise OrchestrationError("A verified external result is required to resolve completion.")
                    raw = args["result"] if "result" in args else operation.data["external_result"]
                origin = "caller" if "result" in args else operation.data.get("external_result_origin")
                data.update({"result": self._apply_effect(tx, operation, raw, reconciling=True), "external_result": deepcopy(raw), "external_result_origin": origin})
            else:
                self._mark_effect_failure(tx, operation, "failed")
                data["error"] = {"message": args["reason"], "code": "reconciled_failure"}
            record = tx.put("operation", operation.key, data, operation.version)
            self._event(tx, "operation.resolved", actor, operation_id=operation.key, outcome=args["outcome"])
            return {"operation": _view(record)}

    def _assess(self, args: JSON, actor: str, context: JSON) -> JSON:
        triage = self.assessment.get("triage")
        if not triage:
            raise OrchestrationError("No triage evaluator is configured.", "evaluation_unavailable")
        if triage not in self.evaluators:
            raise OrchestrationError(f"Evaluator {triage} is unavailable.", "evaluation_unavailable")
        evaluation_context = {**deepcopy(context), "request_context": deepcopy(args.get("context", {}))}
        # Display titles and write bookkeeping must not silently become material
        # evaluator inputs. Everything supplied from mutable work/evidence is
        # bound to its observed content and rechecked when consumed.
        evaluation_context["work"] = self._assessment_work_input(context.get("work"))
        input_binding = self._assessment_input_binding(evaluation_context)
        evaluations = []
        first = self.evaluators[triage].assess(args["question"], deepcopy(evaluation_context))
        self._validate_assessment(first)
        evaluations.append({"evaluator": triage, **asdict(first), "evidence": list(deepcopy(first.evidence))})
        threshold = self.assessment.get("confidence_threshold", 0.8)
        uncertain = first.confidence is None or first.confidence < threshold or first.outcome in self.assessment.get("uncertain_outcomes", ["uncertain", "borderline", "review"])
        heavier = self.assessment.get("escalate")
        unresolved = uncertain
        escalation_error = None
        if uncertain and heavier:
            try:
                if heavier not in self.evaluators:
                    raise OrchestrationError(f"Evaluator {heavier} is unavailable.", "evaluation_unavailable")
                assessment = self.evaluators[heavier].assess(args["question"], {**deepcopy(evaluation_context), "triage": deepcopy(evaluations[0])})
                self._validate_assessment(assessment)
                evaluations.append({"evaluator": heavier, **asdict(assessment), "evidence": list(deepcopy(assessment.evidence))})
                unresolved = assessment.confidence is None or assessment.confidence < threshold or assessment.outcome in self.assessment.get("uncertain_outcomes", ["uncertain", "borderline", "review"])
            except Exception as error:
                escalation_error = {"code": getattr(error, "code", "evaluation_unavailable"), "message": _error_message(error)}
        data = {"question": args["question"], "work_id": args.get("work_id"), "evaluations": evaluations,
                "conclusion": deepcopy(evaluations[-1]), "unresolved": unresolved, "escalation_error": escalation_error}
        with self.store.transaction() as tx:
            # The evidence describes exactly the snapshot evaluated, even when the
            # work changes during inference. It confers no permission on later work.
            data["work_version"] = context["work"]["version"] if context.get("work") else None
            data["input_binding"] = input_binding
            data["request_context"] = deepcopy(args.get("context", {}))
            data["acceptance_binding"] = deepcopy((context.get("acceptance_basis") or {}).get("binding"))
            data["assessment_configuration_identity"] = (context.get("acceptance_basis") or {}).get("assessment_configuration_identity")
            record = self._create(tx, "assessment", actor, data)
            if args.get("work_id"):
                self._evidence(tx, actor, args["work_id"], data, {"type": "assessment", "assessment_id": record.key})
            return {"assessment": _view(record)}

    @staticmethod
    def _validate_assessment(assessment: Assessment) -> None:
        if not isinstance(assessment, Assessment) or not isinstance(assessment.outcome, str) or not assessment.outcome:
            raise OrchestrationError("The evaluator did not return an assessment.", "evaluation_invalid")
        confidence = assessment.confidence
        if confidence is not None and (isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1):
            raise OrchestrationError("Assessment confidence must be between zero and one, or unknown.", "evaluation_invalid")

    def _evidence(self, tx: Transaction, actor: str, work_id: str, result: Any, source: JSON, **extra: Any) -> Record:
        self._require(tx, "work", work_id)
        return self._create(tx, "evidence", actor, {"work_id": work_id, "result": deepcopy(result), "source": source, **extra})

    def _record_result(self, args: JSON, actor: str, context: JSON) -> JSON:
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            evidence = self._evidence(tx, actor, args["work_id"], args["result"], {"type": "caller", "actor": actor},
                label=args.get("label"), metadata=args.get("metadata", {}))
            return {"evidence": _view(evidence)}

    def _events(self, args: JSON, actor: str, context: JSON) -> JSON:
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            return {"events": tx.events(args.get("after", 0), args.get("limit", 100))}
