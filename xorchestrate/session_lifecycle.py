"""Session lifecycle use cases over configured session drivers.

Start, attach, reconnect, resume, send, poll, stop and detach run as journaled
effects. The session-specific reservation, application and release of those
effects are the hooks this mixin supplies to the effect journal.
"""
from copy import deepcopy
from typing import Any

from .actions import SESSION_CREATIONS
from .model import JSON, Conflict, OrchestrationError, Record, record_view
from .outcomes import before_effect
from .ports import Transaction
from .worktrees import current_worktree, session_workspace, session_worktree

_CLOSED = {"stopped", "detached", "disconnected", "superseded"}
_INDEPENDENT = {"poll_session", "stop_session", "detach_session"}


class SessionLifecycleMixin:
    def _bind_resume_source(self, action: str, arguments: JSON, actor: str, context: JSON) -> None:
        resume_id = arguments.get("resume_session_id") if action in {"start_session", "reconnect_session"} else None
        if action == "resolve_operation" and context["operation"]["action"] in SESSION_CREATIONS:
            resume_id = context["operation"]["arguments"].get("resume_session_id")
        if not resume_id:
            return
        source = self._resume_context(actor, resume_id)
        driver = arguments["driver"] if action in {"start_session", "reconnect_session"} else context["operation"]["arguments"]["driver"]
        if source["session"]["driver"] != driver:
            raise OrchestrationError("Resume source and destination must use the same configured driver.", "driver_mismatch")
        source["resume_destination"] = {"driver": driver, "actor_id": context["session_actor"], "work_id": context["work_id"]}
        context["resume_source"] = source

    def _bind_replayed_session(self, context: JSON, operation: Record) -> None:
        """Authorize a replayed creation against the driver its session actually resolved."""
        with self.store.transaction() as tx:
            selected = self._require(tx, "session", operation.data["session_id"])
        context["requested_arguments"] = deepcopy(context["arguments"])
        context["resolved_driver"] = selected.data["driver"]
        context["arguments"] = {**context["arguments"], "driver": selected.data["driver"]}

    def _resume_context(self, actor: str, session_id: str) -> JSON:
        source = self._context(actor, {"session_id": session_id})
        session = source["session"]
        if not isinstance(session.get("backend"), dict) or not session.get("start_operation_id"):
            raise OrchestrationError("The resume source has no verified adapter start receipt; start a fresh session.", "unverified_session_result")
        with self.store.transaction() as tx:
            receipt = self._require(tx, "operation", session["start_operation_id"])
        if (receipt.data.get("action") not in SESSION_CREATIONS or receipt.data.get("session_id") != session_id
                or receipt.data.get("status") != "completed" or receipt.data.get("external_result_origin") != "adapter"
                or receipt.data.get("external_result") != session["backend"]):
            raise OrchestrationError("The resume source is not bound to a verified adapter start receipt.", "unverified_session_result")
        source["resume_receipt"] = record_view(receipt)
        return source

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
        with before_effect():
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
            invoke = getattr(driver, method, None)
            if not callable(invoke):
                raise OrchestrationError("The configured driver does not support this lifecycle operation.", "unsupported_operation", {"outcome": "not_started"})
            trusted_context = {"actor": args.get("actor_id", actor), "delegated_by": actor, "work_id": context["work_id"]}

        def perform(operation):
            specification = {**deepcopy(args["specification"]), "context": {
                **trusted_context, "session_id": operation.data["session_id"]}}
            if not source:
                return invoke(specification)
            # A resumed or reconnected session runs where its source was created.
            with session_workspace(session_worktree(source["session"])):
                return invoke(deepcopy(source["session"]["backend"]), specification)
        result = self._effect(action, args, actor, context, perform)
        # Also covers an idempotent replay of a start this runtime did not reserve.
        self._owned_session_ids.add(result["session"]["id"])
        return result

    def _session_effect(self, action: str, args: JSON, actor: str, context: JSON) -> JSON:
        with before_effect():
            session = context["session"]
            name = session["driver"]
            if name not in self.sessions:
                raise OrchestrationError(f"Session driver {name} is not currently configured.", "not_found")
            driver = self.sessions[name]

        def perform(operation: Record) -> Any:
            with session_workspace(session_worktree(session)):
                return drive(operation)

        def drive(operation: Record) -> Any:
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

    def live_sessions(self) -> list[str]:
        """Sessions this runtime's drivers own and that are not yet closed or failed.

        Ownership starts when this runtime reserves a session start, so a start
        whose receipt is unapplied or awaiting reconciliation is included.

        Closing the runtime records these as disconnected; a long-running input
        adapter uses this to decide whether stopping would end live work.
        """
        # Workers add to the set concurrently; copy it in one step (set.copy is
        # atomic for the builtin set) instead of iterating the live set.
        owned = self._owned_session_ids.copy()
        with self.store.transaction() as tx:
            return sorted(key for key in owned
                          if (record := tx.get("session", key)) and record.data["status"] not in _CLOSED | {"failed"})

    def sessions_needing_reconnect(self) -> list[JSON]:
        """Sessions recorded as live (or disconnected by a runtime close) that this runtime does not hold.

        After a server restart, a crash or a container failure these are the
        sessions whose conversations may still be reachable. Each names what to
        call: ``reconnect_session`` for a driver that reconnects (hosted and
        bridge sessions), ``start_session`` with ``resume_session_id`` for one
        that resumes, nothing for one that can do neither. Nothing is changed.
        """
        owned = self._owned_session_ids.copy()
        found = []
        with self.store.transaction() as tx:
            records = [r for r in tx.list("session") if r.key not in owned
                       and r.data.get("status") in {"starting", "active", "unknown", "disconnected"}
                       and isinstance(r.data.get("backend"), dict)]
        for record in sorted(records, key=lambda r: r.key):
            driver_name = record.data.get("driver")
            driver = self.sessions.get(driver_name)
            arguments = {"driver": driver_name, "resume_session_id": record.key}
            if callable(getattr(driver, "reconnect", None)):
                call = {"action": "reconnect_session", "arguments": arguments}
            elif callable(getattr(driver, "resume", None)):
                call = {"action": "start_session", "arguments": arguments}
            else:
                call = None
            found.append({"session_id": record.key, "driver": driver_name, "status": record.data["status"],
                          "actor_id": record.data.get("actor_id"), "work_id": record.data.get("work_id"),
                          "call": call})
        return found

    def runtime_closed(self):
        with self.store.transaction() as tx:
            for session_id in self._owned_session_ids.copy():
                session = tx.get("session", session_id)
                if session and session.data["status"] in {"active", "unknown"}:
                    self._transition(tx, session, "disconnect", {**session.data, "status": "disconnected", "connection_state": "closed"}, "runtime")
                    self._event(tx, "session.disconnected", "runtime", session_id=session_id)

    def _list_sessions(self, args: JSON, actor: str, context: JSON) -> JSON:
        with self.store.transaction() as tx:
            self._verify_context(tx, context)
            return {"sessions": [record_view(r) for r in tx.list("session") if "work_id" not in args or r.data.get("work_id") == args["work_id"]]}

    def _check_recovered_effects(self, tx, session):
        unresolved = [key for key in session.data.get("unresolved_effects", [])
            if self._require(tx, "operation", key).data["status"] in {"pending", "unknown", "completed_unapplied"}]
        if unresolved:
            raise Conflict("The reconnected conversation has unresolved source effects; observation/control remain available.")

    def _reserve_session_effect(self, tx: Transaction, action: str, key: str, args: JSON, actor: str,
                                context: JSON, data: JSON) -> None:
        if action in SESSION_CREATIONS:
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
            resolved = context.get("resolved_driver", args["driver"])
            # Where the session's processes run (``${repo}``): the creating
            # worktree, kept across resume and reconnect from other worktrees.
            source_session = (context.get("resume_source") or {}).get("session") or {}
            workspace = session_worktree(source_session) or current_worktree()
            if workspace is not None:
                provenance["workspace"] = workspace
            session = self._create(tx, "session", actor, {
                "driver": resolved,
                "selection": {"requested": args["driver"], "resolved": resolved,
                    "fallback_reason": "requested_driver_unavailable" if resolved != args["driver"] else None},
                "specification": args["specification"], "work_id": data["work_id"],
                "actor_id": args.get("actor_id", actor), "status": "starting", "backend": {}, "pending_operation": key,
                "start_operation_id": key, "unresolved_effects": data.get("unresolved_effects", []), **provenance,
            })
            data["session_id"] = session.key
            # This runtime is about to dispatch the start, so its driver may
            # hold the session from here on, even if the receipt is never
            # applied (result_unapplied) or is applied later by reconciliation.
            # A reservation that rolls back leaves no record and is ignored.
            self._owned_session_ids.add(session.key)
            return
        session = self._require(tx, "session", args["session_id"])
        independent = action in _INDEPENDENT
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
            self._transition(tx, session, "reserve", {**session.data, "pending_operation": key}, actor)

    def _apply_session_effect(self, tx: Transaction, operation: Record, raw: Any, reconciling: bool) -> JSON:
        action, actor = operation.data["action"], operation.data["actor"]
        session = self._require(tx, "session", operation.data["session_id"])
        independent = operation.data.get("independent", False)
        if not independent and session.data.get("pending_operation") != operation.key:
            raise Conflict("The session is no longer reserved by this operation.")
        if action not in SESSION_CREATIONS and session.data.get("backend") != operation.data.get("backend"):
            raise Conflict("Session incarnation changed while the operation was outstanding.")
        if action == "send_session" and session.data["status"] in _CLOSED:
            if reconciling:
                self._transition(tx, session, "release_closed", {**session.data, "pending_operation": None}, actor)
                return {"session": record_view(self._require(tx, "session", session.key)), "output": deepcopy(raw), "operation_id": operation.key}
            raise Conflict("The session closed before a late submission result.")
        data = {**session.data, "updated_at": self.clock()}
        if not independent:
            data["pending_operation"] = None
        if action in SESSION_CREATIONS:
            if not isinstance(raw, dict):
                raise OrchestrationError("Session start must return a JSON object.", "invalid_result")
            data.update({"backend": deepcopy(raw), "status": "active"})
            if operation.data.get("resume_source"):
                source = self._require(tx, "session", operation.data["resume_source"]["session_id"])
                self._transition(tx, source, "replace", {**source.data, "status": "superseded", "replacement_session_id": session.key}, actor)
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
        event = "activate" if action in SESSION_CREATIONS else action.removesuffix("_session")
        record = self._transition(tx, session, event, data, actor)
        self._event(tx, f"session.{action.removesuffix('_session')}", actor, session_id=session.key, operation_id=operation.key)
        if operation.data.get("work_id") and action in {"send_session", "poll_session"}:
            self._evidence(tx, actor, operation.data["work_id"], raw, {"type": "session", "session_id": session.key, "operation_id": operation.key})
        return {"session": record_view(record), "output": deepcopy(raw), "operation_id": operation.key}

    def _release_session_effect(self, tx: Transaction, operation: Record, status: str) -> None:
        session = self._require(tx, "session", operation.data["session_id"])
        if operation.data.get("independent"):
            return
        if session.data.get("pending_operation") != operation.key:
            raise Conflict("The session reservation changed.")
        creation = operation.data["action"] in SESSION_CREATIONS
        next_status = "unknown" if status == "unknown" else ("failed" if creation else "active")
        if session.data["status"] in _CLOSED:
            next_status = session.data["status"]
        event = "uncertain" if status == "unknown" else ("start_failed" if creation else "effect_failed")
        self._transition(tx, session, event, {**session.data, "status": next_status,
            "pending_operation": operation.key if status == "unknown" else None}, operation.data["actor"])

    @staticmethod
    def _check_session_reconciliation(operation: Record, args: JSON) -> None:
        if operation.data["action"] in SESSION_CREATIONS and (
                "result" in args or "external_result" not in operation.data
                or operation.data.get("external_result_origin") != "adapter"):
            raise OrchestrationError("Session start completion requires its retained adapter receipt; caller-supplied results cannot bind a session.", "unverified_session_result")
