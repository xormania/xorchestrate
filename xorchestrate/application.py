"""Orchestration use cases, with every external dependency supplied through a port.

Capability names are ``provider/name``. Observations and assessments are retained
as evidence; only the injected guardrail decides whether an action is authorized.
External effects have a durable operation record before execution. A key is never
silently retried after an uncertain outcome, including across application restarts.
Session lifecycle, the effect journal, delivery, assessment and acceptance are
composed from their own modules; this facade owns dispatch, authorization context
and record mutation.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from typing import Any

from .acceptance import WorkAcceptance
from .actions import ACTIONS, SESSION_CREATIONS, STRING
from .assessment import WorkAssessment
from .delivery import DeliveryMixin
from .effects import EffectJournalMixin
from .inspection import InspectionProjector
from .lifecycles import DURABLE_LIFECYCLES, default_definitions
from .model import JSON, Capability, Conflict, OrchestrationError, Record, canonical_digest, record_view
from .outcomes import before_effect
from .ports import (
    AcceptancePolicy,
    CapabilityProvider,
    Clock,
    DeliveryRetry,
    DeliveryScheduling,
    Evaluator,
    Guardrail,
    Identifier,
    ReceiptPolicy,
    RecoveryPolicy,
    RoutingPolicy,
    SessionDriver,
    SessionSelection,
    StateStore,
    TelemetrySink,
    Transaction,
)
from .schema import validate
from .session_lifecycle import SessionLifecycleMixin
from .state import StateEngine
from .telemetry import TelemetryViews, dispatch_pressure, record_observation
from .worktrees import bound_worktree, current_worktree, worktree_identity


class Application(SessionLifecycleMixin, EffectJournalMixin, DeliveryMixin):
    """Transport-independent orchestration; construction performs no I/O."""

    def __init__(
        self, store: StateStore, providers: dict[str, CapabilityProvider],
        sessions: dict[str, SessionDriver], guardrail: Guardrail, routing: RoutingPolicy,
        evaluators: dict[str, Evaluator], clock: Clock, identifiers: Identifier, *,
        session_selection: SessionSelection, delivery_scheduler: DeliveryScheduling, retry_policy: DeliveryRetry,
        receipt_policy: ReceiptPolicy | None = None, assessment: JSON | None = None,
        acceptance: AcceptancePolicy | None = None, session_fallbacks: dict[str, str] | None = None,
        provider_configurations: JSON | None = None, evaluator_configurations: JSON | None = None,
        state_engine: StateEngine | None = None, telemetry: TelemetrySink | None = None,
        worktree: JSON | None = None, recovery_policy: RecoveryPolicy | None = None,
    ):
        self.store = store
        # The worktree of calls that do not bring their own (the process's own
        # worktree for a CLI run; the server's starting worktree otherwise).
        self.worktree = worktree_identity(worktree)
        self.state_engine = StateEngine(default_definitions()) if state_engine is None else state_engine
        self.acceptance = acceptance
        self.session_selection = session_selection
        self.delivery_scheduler = delivery_scheduler
        self.retry_policy = retry_policy
        self.recovery_policy = recovery_policy
        self.receipt_policy = receipt_policy
        if session_fallbacks is not None and (not isinstance(session_fallbacks, dict) or any(
            not isinstance(k, str) or not k or not isinstance(v, str) or not v for k, v in session_fallbacks.items())):
            raise OrchestrationError("Session fallbacks must map requested names to configured driver names.", "configuration_error")
        self.session_fallbacks = deepcopy(session_fallbacks or {})
        self._owned_session_ids = set()
        # One token per recovery running in this runtime (set add/discard are atomic for the builtin set).
        self._running_recoveries = set()
        self.providers = dict(providers)
        self.provider_configurations = deepcopy(provider_configurations or {})
        self.sessions = dict(sessions)
        self.guardrail = guardrail
        self.routing = routing
        self.evaluators = dict(evaluators)
        self.evaluator_configurations = deepcopy(evaluator_configurations or {})
        self.clock = clock
        self.identifiers = identifiers
        self.telemetry = telemetry
        try:
            validate(assessment if assessment is not None else {}, {
                "type": "object", "additionalProperties": False, "properties": {
                    "triage": STRING, "escalate": STRING,
                    "confidence_threshold": {"type": "number", "minimum": 0, "maximum": 1},
                    "uncertain_outcomes": {"type": "array", "items": STRING},
                    "work_input_fields": {"type": "array", "uniqueItems": True, "items": STRING},
                },
            })
        except OrchestrationError as error:
            raise OrchestrationError("Invalid assessment configuration.", "invalid_configuration", error.details) from error
        self.assessment = deepcopy(assessment if assessment is not None else {})
        self.inspection = InspectionProjector(store, self._context, self._authorize, self._verify_context)
        self.work_acceptance = WorkAcceptance(
            store, clock=lambda: self.clock(), verify_authorization=self._verify_context,
            create=self._create, transition=self._transition, emit=self._event,
            invoke=lambda arguments, actor: self.call("invoke", arguments, actor),
            policy=lambda: self.acceptance, assessment_configuration=lambda: self.assessment,
            provider_configurations=lambda: self.provider_configurations,
            evaluator_configurations=lambda: self.evaluator_configurations,
        )
        self.work_assessment = WorkAssessment(
            store, evaluators=lambda: self.evaluators, configuration=lambda: self.assessment,
            create=self._create, evidence=self._evidence,
        )

    def describe(self) -> list[Capability]:
        return [Capability(name, spec.description, deepcopy(spec.input_schema), deepcopy(spec.annotations)) for name, spec in ACTIONS.items()]

    def call(self, action: str, arguments: JSON, actor: str = "owner", *, native_context=None, replay_only=False,
             telemetry_run_id=None, worktree=None) -> JSON:
        """Run one action for ``actor``.

        ``worktree`` is the identity of the git worktree the call came from
        (root, name, branch, configuration hash), bound by the input adapter
        like the actor. It reaches the guardrail context, records, events and
        telemetry. A nested call keeps its caller's worktree; a call without
        one uses the application's default.
        """
        origin = worktree_identity(worktree) or current_worktree() or self.worktree
        with bound_worktree(origin):
            return self._call_with_worktree(action, arguments, actor, native_context=native_context,
                                            replay_only=replay_only, telemetry_run_id=telemetry_run_id)

    def _call_with_worktree(self, action, arguments, actor, *, native_context, replay_only, telemetry_run_id):
        if telemetry_run_id is not None and (not isinstance(telemetry_run_id, str) or not telemetry_run_id):
            raise OrchestrationError("Telemetry run identity must be nonempty text.", "invalid_arguments")
        if self.telemetry is None or (isinstance(action, str) and action.startswith("telemetry_")):
            guarded = action in {"telemetry_events", "telemetry_status", "telemetry_payload", "telemetry_usage"}
            authority = self._telemetry_authority() if guarded else None
            result = self._call(action, arguments, actor, native_context=native_context, replay_only=replay_only)
            if guarded and self._telemetry_authority() != authority:
                raise OrchestrationError("Telemetry authority changed during the read.", "conflict")
            return result
        scope = {"actor": actor, "action": action}
        if telemetry_run_id is not None:
            # This is trusted host-supplied correlation, never a tool-result
            # instruction or an extra field inferred from caller action JSON.
            scope["run_id"] = telemetry_run_id
        try:
            scope["attempt_id"] = self.telemetry.new_id()
        except Exception:
            pass
        if isinstance(arguments, dict):
            scope.update({key: arguments[key] for key in ("work_id", "session_id", "operation_id") if arguments.get(key)})
        if (worktree := current_worktree()) is not None:
            scope["worktree"] = worktree
        if isinstance(native_context, dict):
            scope.update({key: native_context[key] for key in ("session_id", "native_id", "incarnation", "native_call_id")
                          if native_context.get(key)})
        self.record_observation("application.requested", {"action": action, "arguments": arguments}, scope)
        try:
            result = self._call(action, arguments, actor, native_context=native_context,
                                replay_only=replay_only, capture_context=scope)
        except BaseException as error:
            self.record_observation("application.failed", {"action": action, "arguments": arguments,
                "error": {"type": type(error).__name__, "code": getattr(error, "code", None)}},
                self._capture_context(scope, arguments, actor))
            raise
        self.record_observation("application.completed", {"action": action, "arguments": arguments, "result": result},
                                self._capture_context(scope, arguments, actor, result))
        return result

    def _capture_context(self, scope, arguments, actor, result=None):
        scope = deepcopy(scope)
        try:
            if isinstance(result, dict):
                if isinstance(result.get("work"), dict):
                    scope["work_id"] = result["work"]["id"]
                if isinstance(result.get("session"), dict):
                    session = result["session"]
                    scope.update(session_id=session["id"], work_id=session.get("work_id"))
                    scope.update({key: session.get("backend", {})[key] for key in ("native_id", "incarnation")
                                  if session.get("backend", {}).get(key)})
            if isinstance(arguments, dict) and "idempotency_key" in arguments:
                operation_id = canonical_digest([actor, arguments["idempotency_key"]])
                with self.store.transaction() as tx:
                    operation = tx.get("operation", operation_id)
                if operation is not None:
                    scope["operation_id"] = operation_id
                    if operation.data.get("work_id"):
                        scope["work_id"] = operation.data["work_id"]
        except Exception:
            # Enrichment is optional, just like capture itself.
            pass
        return scope

    def record_observation(self, event_type, payload, context, **metadata):
        return record_observation(self.telemetry, event_type, payload, context, **metadata)

    def _telemetry_views(self):
        return TelemetryViews(self.telemetry, self.store, self._context, self._authorize,
                              self._verify_context, self.inspection, self._telemetry_authority)

    def _telemetry_authority(self):
        policy = self.guardrail
        revision = getattr(policy, "authority_revision", None)
        return id(policy), revision() if callable(revision) else None

    def _telemetry_status(self, arguments, actor, context):
        if self.telemetry is None:
            return {"status": "disabled", "coverage": "unknown", "event_count": 0, "sources": {}, "publication": "unknown"}
        result = self._telemetry_views().status(actor)
        health = {}
        for kind, resources in (("providers", self.providers), ("sessions", self.sessions), ("evaluators", self.evaluators)):
            selected = {}
            for name, resource in resources.items():
                report = getattr(resource, "telemetry_health", None)
                if callable(report):
                    try:
                        selected[name] = dispatch_pressure(report())
                    except Exception:
                        selected[name] = {"status": "unavailable", "coverage": "unknown"}
            if selected:
                health[kind] = selected
        result["source_dispatch"] = health
        return result

    def _telemetry_events(self, arguments, actor, context):
        return self._telemetry_views().events(arguments, actor)

    def _telemetry_payload(self, arguments, actor, context):
        return self._telemetry_views().payload(arguments, actor)

    def _telemetry_usage(self, arguments, actor, context):
        return self._telemetry_views().usage(arguments, actor)

    def _telemetry_compare(self, arguments, actor, context):
        return self._telemetry_views().comparison(arguments, actor)

    def _telemetry_snapshot(self, arguments, actor, context):
        views = self._telemetry_views()
        sink = views._configured()
        authorize = views.bundle_authorizer(actor, "telemetry_snapshot", sink.snapshot_source())
        return sink.snapshot(arguments["destination"], authorize=authorize, receipt=True)

    def _telemetry_verify(self, arguments, actor, context):
        views = self._telemetry_views()
        sink = views._configured()
        authorize = views.bundle_authorizer(actor, "telemetry_verify", sink.bundle_source(arguments["destination"]))
        return sink.verify(arguments["destination"], authorize=authorize, receipt=True)

    def _telemetry_restore(self, arguments, actor, context):
        views = self._telemetry_views()
        sink = views._configured()
        authorize = views.bundle_authorizer(actor, "telemetry_restore", sink.bundle_source(arguments["bundle"]))
        return sink.restore(arguments["bundle"], arguments["destination"], authorize=authorize, receipt=True)

    def _call(self, action: str, arguments: JSON, actor: str = "owner", *, native_context=None, replay_only=False,
              capture_context=None) -> JSON:
        with before_effect():
            if action not in ACTIONS:
                raise OrchestrationError(f"Unknown action: {action}", "unknown_action")
            if not isinstance(actor, str) or not actor:
                raise OrchestrationError("A caller identity is required.")
            validate(arguments, ACTIONS[action].input_schema)
            # Detach caller-owned structures so neither adapters nor a caller can mutate
            # the request whose identity is bound to an operation key.
            arguments = deepcopy(arguments)
            if action in SESSION_CREATIONS and "resume" in arguments["specification"]:
                raise OrchestrationError("Session identity is reserved; use resume_session_id to reference an application session.", "invalid_arguments")
            context = self._context(actor, arguments)
            if capture_context is not None:
                context.update({key: capture_context[key] for key in ("attempt_id", "run_id") if capture_context.get(key)})
            if native_context is not None:
                context["native_source"] = deepcopy(native_context)
                with self.store.transaction() as tx:
                    self._verify_native_source(tx, context["native_source"], actor)
            self._bind_resume_source(action, arguments, actor, context)
            if action == "invoke":
                context.update({"provider": arguments["capability"].partition("/")[0], "capability": {"name": arguments["capability"]}})
            if "idempotency_key" in arguments:
                with self.store.transaction() as tx:
                    existing = tx.get("operation", canonical_digest([actor, arguments["idempotency_key"]]))
                if existing:
                    request = {k: v for k, v in arguments.items() if k != "idempotency_key"}
                    if existing.data["fingerprint"] != canonical_digest({"action": action, "arguments": request}):
                        raise Conflict("The idempotency key is already bound to a different request.")
                    if existing.data.get("capability"):
                        context["capability"] = deepcopy(existing.data["capability"])
                    context["operation"] = record_view(existing)
                    if action in SESSION_CREATIONS:
                        self._bind_replayed_session(context, existing)
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
            evidence = [record_view(r) for r in tx.list("evidence") if r.data.get("work_id") == work.key] if work else []
        context = {
            "actor": actor, "actor_record": record_view(caller) if caller else None,
            "arguments": deepcopy(arguments), "work": record_view(work) if work else None,
            "inputs": deepcopy(work.data.get("inputs", {})) if work else {},
            "context": deepcopy(work.data.get("context", {})) if work else {},
            "evidence": evidence, "session": record_view(session) if session else None,
            "work_id": work.key if work else None,
            "session_actor": arguments.get("actor_id", actor) if "driver" in arguments else (session.data.get("actor_id") if session else None),
            "operation": record_view(operation) if operation else None,
            "native_call": record_view(native_call) if native_call else None,
            # Where the call came from; guardrails may use it for per-worktree rules.
            "worktree": current_worktree(),
        }
        if operation and operation.data.get("capability"):
            context.update({"capability": deepcopy(operation.data["capability"]),
                "provider": operation.data["arguments"]["capability"].partition("/")[0]})
        context["acceptance_basis"] = self.work_acceptance.context(work.key) if work else None
        return context

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
        self.work_acceptance.verify_context(tx, context)
        if context.get("resume_source"):
            self._verify_context(tx, context["resume_source"])

    @staticmethod
    def _require(tx: Transaction, kind: str, key: str) -> Record:
        record = tx.get(kind, key)
        if record is None:
            raise OrchestrationError(f"Unknown {kind}: {key}", "not_found")
        return record

    def _event(self, tx: Transaction, name: str, actor: str, **data: Any) -> None:
        event = {"type": name, "actor": actor, "at": self.clock(), **deepcopy(data)}
        if (worktree := current_worktree()) is not None:
            event.setdefault("worktree", worktree)
        tx.emit(event)

    def _create(self, tx: Transaction, kind: str, actor: str, data: JSON, key: str | None = None) -> Record:
        key = key or self.identifiers()
        payload = {**deepcopy(data), "created_by": actor, "created_at": self.clock()}
        if (worktree := current_worktree()) is not None:
            payload["created_worktree"] = worktree
        record = (self.state_engine.apply(tx, kind, key, "create", payload, context={"actor": actor, "at": payload["created_at"]})
            if kind in DURABLE_LIFECYCLES else tx.put(kind, key, payload))
        self._event(tx, f"{kind}.created", actor, record_id=record.key, work_id=data.get("work_id"))
        return record

    def _transition(self, tx: Transaction, current: Record, event: str, data: JSON, actor: str) -> Record:
        """Join the use case's transaction; domain guards and grouped writes stay atomic."""
        return self.state_engine.apply(tx, current.kind, current.key, event, data, current.version,
            {"actor": actor, "at": self.clock()})

    def _create_work(self, args: JSON, actor: str, context: JSON) -> JSON:
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            record = self._create(tx, "work", actor, {
                "goal": args["goal"], "title": args.get("title", args["goal"]), "status": "open",
                "inputs": args.get("inputs", {}), "context": args.get("context", {}), "metadata": args.get("metadata", {}),
            })
            return {"work": record_view(record)}

    def _get_work(self, args: JSON, actor: str, context: JSON) -> JSON:
        return self.inspection.inspect_snapshot("get_work", actor, context, lambda tx: self._work_snapshot(tx, args["work_id"]))

    def _work_snapshot(self, tx: Transaction, work_id: str) -> JSON:
        work = self._require(tx, "work", work_id)
        result = {"work": record_view(work)}
        result.update(self.work_acceptance.view(tx, work.key))
        for kind, plural in (("assignment", "assignments"), ("evidence", "evidence"), ("session", "sessions")):
            result[plural] = [record_view(r) for r in tx.list(kind) if r.data.get("work_id") == work.key]
        return result

    def _list_work(self, args: JSON, actor: str, context: JSON) -> JSON:
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            return {"work": [record_view(r) for r in tx.list("work") if "status" not in args or r.data.get("status") == args["status"]]}

    def _update(self, kind: str, args: JSON, actor: str, context: JSON) -> JSON:
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            current = self._require(tx, kind, args[f"{kind}_id"])
            record = tx.put(kind, current.key, {**current.data, **deepcopy(args["changes"]), "updated_at": self.clock()}, args["expected_version"])
            self._event(tx, f"{kind}.updated", actor, record_id=record.key, version=record.version)
            return {kind: record_view(record)}

    def _update_work(self, args: JSON, actor: str, context: JSON) -> JSON:
        return self._update("work", args, actor, context)

    def _add_actor(self, args: JSON, actor: str, context: JSON) -> JSON:
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            record = self._create(tx, "actor", actor, {
                "name": args.get("name", args["actor_id"]), "roles": args.get("roles", []), "metadata": args.get("metadata", {}),
            }, args["actor_id"])
            return {"actor": record_view(record)}

    def _update_actor(self, args: JSON, actor: str, context: JSON) -> JSON:
        return self._update("actor", args, actor, context)

    def _list_actors(self, args: JSON, actor: str, context: JSON) -> JSON:
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            return {"actors": [record_view(r) for r in tx.list("actor")]}

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
            return {"assignment": record_view(record)}

    def _relate(self, args: JSON, actor: str, context: JSON) -> JSON:
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            self._require(tx, "actor", args["source"])
            self._require(tx, "actor", args["target"])
            existing = next((record for record in tx.list("relation")
                if all(record.data.get(field) == args[field] for field in ("source", "target", "relationship"))), None)
            record = existing or self._create(tx, "relation", actor, args)
            return {"relation": record_view(record)}

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
            actors = [record_view(r) for r in tx.list("actor")]
            relations = [record_view(r) for r in tx.list("relation")]
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
            result = {"message": record_view(record)}
        if self.delivery_scheduler.dispatch_on_enqueue:
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
            return {"messages": [record_view(r) for r in tx.list("message")
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
            return {"message": record_view(message)}

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
        with before_effect():
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

    def _reserve_effect(self, tx: Transaction, action: str, key: str, args: JSON, actor: str, context: JSON, data: JSON) -> None:
        if action != "invoke":
            return self._reserve_session_effect(tx, action, key, args, actor, context, data)
        data["capability"] = deepcopy(context["capability"])
        self._create(tx, "invocation", actor, {
            "capability": args["capability"], "arguments": args["arguments"], "work_id": data["work_id"],
            "operation_id": key, "status": "pending",
        }, key)

    def _apply_effect(self, tx: Transaction, operation: Record, raw: Any, *, reconciling=False) -> JSON:
        if operation.data["action"] != "invoke":
            return self._apply_session_effect(tx, operation, raw, reconciling)
        if not isinstance(raw, dict):
            raise OrchestrationError("Capability output must be a JSON object.", "invalid_result")
        actor = operation.data["actor"]
        invocation = self._require(tx, "invocation", operation.key)
        record = self._transition(tx, invocation, "complete", {**invocation.data, "status": "completed", "result": deepcopy(raw)}, actor)
        if operation.data.get("work_id"):
            self._evidence(tx, actor, operation.data["work_id"], raw, {"type": "capability", "operation_id": operation.key})
        return {"invocation": record_view(record), "operation_id": operation.key}

    def _release_effect(self, tx: Transaction, operation: Record, status: str) -> None:
        if operation.data["action"] != "invoke":
            return self._release_session_effect(tx, operation, status)
        invocation = self._require(tx, "invocation", operation.key)
        self._transition(tx, invocation, "uncertain" if status == "unknown" else "fail",
            {**invocation.data, "status": status}, operation.data["actor"])

    def _check_reconciliation(self, operation: Record, args: JSON) -> None:
        if operation.data["action"] != "invoke":
            self._check_session_reconciliation(operation, args)

    def _set_requirements(self, args, actor, context):
        return self.work_acceptance.set_requirements(args, actor, context)

    def _set_candidate(self, args, actor, context):
        return self.work_acceptance.set_candidate(args, actor, context)

    def _create_contribution(self, args, actor, context):
        return self.work_acceptance.create_contribution(args, actor, context)

    def _update_contribution(self, args, actor, context):
        return self.work_acceptance.update_contribution(args, actor, context)

    def _run_measurement(self, args, actor, context):
        return self.work_acceptance.run_measurement(args, actor, context)

    def _accept_work(self, args, actor, context):
        return self.work_acceptance.accept_work(args, actor, context)

    def _get_acceptance(self, args, actor, context):
        return self.inspection.inspect_snapshot("get_acceptance", actor, context,
            lambda tx: self.work_acceptance.view(tx, args["work_id"]))

    def _assess(self, args: JSON, actor: str, context: JSON) -> JSON:
        return self.work_assessment.assess(args, actor, context)

    def _evidence(self, tx: Transaction, actor: str, work_id: str, result: Any, source: JSON, **extra: Any) -> Record:
        self._require(tx, "work", work_id)
        return self._create(tx, "evidence", actor, {"work_id": work_id, "result": deepcopy(result), "source": source, **extra})

    def _record_result(self, args: JSON, actor: str, context: JSON) -> JSON:
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            evidence = self._evidence(tx, actor, args["work_id"], args["result"], {"type": "caller", "actor": actor},
                label=args.get("label"), metadata=args.get("metadata", {}))
            return {"evidence": record_view(evidence)}

    def _events(self, args: JSON, actor: str, context: JSON) -> JSON:
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            return {"events": tx.events(args.get("after", 0), args.get("limit", 100))}

