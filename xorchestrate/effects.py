"""The effect journal: durable intent, outcome and reconciliation of external effects.

Intent is recorded before external I/O, which runs outside any store transaction;
the outcome is then retained with its events. An uncertain outcome is never
silently retried. The host owns what an effect targets through four hooks:

- ``_reserve_effect(tx, action, key, args, actor, context, data)`` validates and
  reserves the target, adding its bindings to the pending operation ``data``;
- ``_apply_effect(tx, operation, raw, *, reconciling=False)`` applies a completed
  adapter result to the target and returns the public result;
- ``_release_effect(tx, operation, status)`` releases the target after a failed
  or unknown outcome;
- ``_check_reconciliation(operation, args)`` rejects a completion the target
  cannot accept from reconciliation.
"""
from copy import deepcopy
from typing import Any, Callable

from .model import (
    JSON,
    Conflict,
    OrchestrationError,
    Record,
    canonical_digest,
    project_operation_errors,
    public_error,
    public_error_message,
    record_view,
)
from .outcomes import before_effect, completed_effect_result, effect_outcome

PUBLIC_FAILURE_DETAILS = ("log",)
"""Failure details that reach the caller's response: locations of private diagnostics, never their content."""


class EffectJournalMixin:
    def _effect(self, action: str, args: JSON, actor: str, context: JSON, perform: Callable[[Record], Any]) -> JSON:
        with before_effect():
            key = canonical_digest([actor, args["idempotency_key"]])
            request = {k: deepcopy(v) for k, v in args.items() if k != "idempotency_key"}
            fingerprint = canonical_digest({"action": action, "arguments": request})
            with self.store.transaction() as tx:
                self._verify_context(tx, context)
                existing = tx.get("operation", key)
                if existing:
                    if existing.data["fingerprint"] != fingerprint:
                        raise Conflict("The idempotency key is already bound to a different request.")
                    return self._replay(existing)
                data = {"action": action, "arguments": request, "fingerprint": fingerprint, "actor": actor,
                        "idempotency_key": args["idempotency_key"], "status": "pending", "work_id": context["work"]["id"] if context.get("work") else None}
                self._reserve_effect(tx, action, key, args, actor, context, data)
                operation = self._create(tx, "operation", actor, data, key)
        try:
            raw = perform(operation)
        except Exception as error:
            known_outcome = effect_outcome(error)
            if known_outcome == "completed":
                retained = completed_effect_result(error)
                recording_error = self._retain_completed_effect(key, actor, error, retained)
                raise OrchestrationError("The adapter completed, but its result needs reconciliation.", "result_unapplied", {
                    "operation_id": key, "outcome": "completed", **retained,
                    "recording_error": recording_error}) from error
            unknown = known_outcome not in {"failed", "not_started"}
            status = "unknown" if unknown else "failed"
            recording_error = None
            try:
                self._fail_effect(key, error, status)
            except Exception as storage_error:
                recording_error = public_error_message(storage_error)
            cause = public_error(error)
            raise OrchestrationError(public_error_message(error), "operation_unknown" if unknown else "operation_failed", {
                "operation_id": key, "outcome": status, "cause": cause["code"],
                "recording_error": recording_error,
                # Where the failure's private diagnostics are kept (a location, never their content).
                **{name: cause["details"][name] for name in PUBLIC_FAILURE_DETAILS
                   if isinstance(cause["details"].get(name), str)},
            }) from error
        try:
            with self.store.transaction() as tx:
                current = self._require(tx, "operation", key)
                if current.data["status"] != "pending":
                    raise Conflict("The operation was reconciled while its external call was in progress.")
                result = self._apply_effect(tx, current, raw)
                self._transition(tx, current, "complete", {**current.data, "status": "completed", "result": result,
                    "external_result": deepcopy(raw), "external_result_origin": "adapter", "finished_at": self.clock()}, actor)
                self._event(tx, "operation.completed", actor, operation_id=key, work_id=current.data.get("work_id"))
                return result
        except Exception as error:
            # The external effect succeeded even if a local write did not. Preserve
            # that result for reconciliation instead of labelling it a failed call.
            recording_error = self._retain_completed_effect(key, actor, error, {"external_result": deepcopy(raw)})
            raise OrchestrationError("The external effect completed, but its result needs reconciliation.", "result_unapplied", {
                "operation_id": key, "outcome": "completed", "external_result": deepcopy(raw),
                "recording_error": recording_error,
            }) from error

    def _retain_completed_effect(self, key, actor, error, retained):
        """Keep adapter completion distinct from uncertainty without forcing settlement."""
        try:
            with self.store.transaction() as tx:
                current = self._require(tx, "operation", key)
                changes = {"effect_outcome": "completed", "application_error": public_error_message(error),
                           "application_error_schema_version": 1}
                if current.data["status"] in {"pending", "unknown"}:
                    changes.update({"status": "completed_unapplied", **retained})
                    if "external_result" in retained:
                        changes["external_result_origin"] = "adapter"
                elif "external_result" in retained:
                    changes["late_external_result"] = deepcopy(retained["external_result"])
                self._transition(tx, current, "retain_success" if "status" in changes else "retain_late",
                    {**current.data, **changes}, actor)
                self._event(tx, "operation.result_retained", actor, operation_id=key)
        except Exception as storage_error:
            # The caller still receives the actual outcome and response if
            # persistence or an independently configured retention rule refuses.
            return public_error_message(storage_error)
        return None

    @staticmethod
    def _replay(operation: Record) -> JSON:
        status = operation.data["status"]
        if status == "completed":
            return deepcopy(operation.data["result"])
        visible = project_operation_errors(operation.data)
        raise OrchestrationError(
            f"This key has an existing {status} operation; it will not execute again.",
            "operation_failed" if status == "failed" else "operation_unresolved",
            {"operation_id": operation.key, "outcome": "completed" if status == "completed_unapplied" else status,
             "status": status, "error": visible.get("error"),
             **({"legacy_error_retained": True} if visible.get("legacy_error_retained") else {}),
             **({"external_result": deepcopy(operation.data["external_result"])}
                if status == "completed_unapplied" and "external_result" in operation.data else {})},
        )

    def _fail_effect(self, key: str, error: Exception, status: str) -> None:
        with self.store.transaction() as tx:
            operation = self._require(tx, "operation", key)
            if operation.data["status"] != "pending":
                return
            details = public_error(error)
            self._transition(tx, operation, "uncertain" if status == "unknown" else "fail",
                {**operation.data, "status": status, "error": details, "error_schema_version": 1,
                 "finished_at": self.clock()}, operation.data["actor"])
            self._release_effect(tx, operation, status)
            self._event(tx, f"operation.{status}", operation.data["actor"], operation_id=key)

    def _get_operation(self, args: JSON, actor: str, context: JSON) -> JSON:
        return self.inspection.inspect_snapshot("get_operation", actor, context,
            lambda tx: {"operation": record_view(self._require(tx, "operation", args["operation_id"]))})

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
                self._check_reconciliation(operation, args)
                if "result" not in args and "external_result" not in operation.data:
                    raise OrchestrationError("A verified external result is required to resolve completion.")
                raw = args["result"] if "result" in args else operation.data["external_result"]
                origin = "caller" if "result" in args else operation.data.get("external_result_origin")
                data.update({"result": self._apply_effect(tx, operation, raw, reconciling=True), "external_result": deepcopy(raw), "external_result_origin": origin})
            else:
                self._release_effect(tx, operation, "failed")
                data["error"] = {"message": args["reason"], "code": "reconciled_failure"}
                data["error_schema_version"] = 1
            record = self._transition(tx, operation, "resolve_" + args["outcome"], data, actor)
            self._event(tx, "operation.resolved", actor, operation_id=operation.key, outcome=args["outcome"])
            return {"operation": project_operation_errors(record_view(record))}
