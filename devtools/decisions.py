"""Service-owned semantic decisions and work-bound deeper investigation."""

from __future__ import annotations

import json
from contextlib import contextmanager
from contextvars import ContextVar

from .advice import Advice
from .assignments import snapshot
from .configuration import resolve as configuration
from .control import Control, binding, includes
from .failures import Failure, failure_info
from .role_packages import adapter, require_capability
from .store import canonical, digest, stamp
from .telemetry import clean
from .work import record

REQUEST = ContextVar("decision_request", default=None)


@contextmanager
def request(member, tool, arguments):
    token = REQUEST.set({"member": member, "tool": tool, "arguments": arguments})
    try:
        yield
    finally:
        REQUEST.reset(token)


def replay_request(member, secrets):
    """Retain exact replay inputs only when they contain no configured credential."""
    current = REQUEST.get()
    if (not current or current["member"] != member or current["tool"] not in
            {"orchestrate", "plan_adopt", "steer", "conclude", "work_assess", "runtime_start"}):
        return None
    # Freeze the checked inputs before evaluation; redacting them would change the
    # operation later replayed by the service, including typed choice identifiers.
    continuation = json.loads(canonical(current))

    def contains_secret(value):
        if isinstance(value, dict):
            return any(contains_secret(key) or contains_secret(item) for key, item in value.items())
        if isinstance(value, list):
            return any(contains_secret(item) for item in value)
        if isinstance(value, str):
            if any(secret in value for secret in secrets):
                return True
            try:
                decoded = json.loads(value)
            except (ValueError, RecursionError):
                return False
            if isinstance(decoded, (dict, list, str)):
                return contains_secret(decoded)
        return False

    if secrets and contains_secret(continuation["arguments"]):
        raise Failure("Remove configured credentials from the request before retrying; sensitive arguments cannot be retained for replay.",
                      category="invalid_request", reason="sensitive_replay_arguments")
    return continuation


def capabilities(spec):
    return [spec("decision_resolve", "Resolve your assigned ambiguous decision with investigated evidence. The service applies the resolution and resumes the waiting operation.", {
        "decisions": {"type": "object", "minProperties": 1, "maxProperties": 32,
                      "additionalProperties": {"anyOf": [{"type": "boolean"}, {"type": "number"}, {"type": "string", "maxLength": 100}]}},
        "reason": {"type": "string", "minLength": 1, "maxLength": 3000},
        "evidence": {"type": "array", "minItems": 1, "maxItems": 8, "uniqueItems": True,
                     "items": {"type": "string", "pattern": "^(artifact|operation|update):[1-9][0-9]*$", "maxLength": 80}},
    }, ("decisions", "reason", "evidence"))]


def classify(answer, question, rule, unknown):
    """Separate the selected value from the evaluator's original measurements."""
    kind = question["type"]
    if kind == "noul":
        value = answer["noul"]
        if value >= rule["noul"]["accept_at"]:
            return True
        if value < rule["noul"]["reject_below"]:
            return False
        return None
    probabilities = answer["probabilities"]
    values = sorted(probabilities.values(), reverse=True)
    margin = values[0] - (values[1] if len(values) > 1 else 0)
    if kind == "choice":
        choice = answer["choice"]
        confidence = min(answer["confidence"], probabilities[choice])
        if (choice in unknown or confidence < rule["choice"]["minimum_probability"] or
                probabilities[choice] != values[0] or margin < rule["choice"]["minimum_margin"]):
            return None
        return choice
    if (answer["confidence"] < rule["score"]["minimum_confidence"] or
            margin < rule["score"]["minimum_margin"]):
        return None
    return answer["score"]


class Decisions:
    def __init__(self, store, member):
        self.store, self.member = store, member
        self.control = Control(store, member)

    def context(self, db, scope, job):
        """Only authoritative work inputs; arbitration activity cannot stale itself."""
        current = binding(db, self.member)
        if not scope or not current or snapshot(scope) != snapshot(current) or current["paused"]:
            raise Failure("The decision's work binding changed or is paused.", category="not_ready", reason="decision_stale")
        order = record(db, "work_orders", scope["work_order"])
        if order["status"] in {"done", "cancelled"}:
            raise Failure("This decision's work is finished.", category="not_ready", reason="decision_stale")
        target = record(db, "jobs", job) if job is not None else None
        if target and (target["work_order"] != order["id"] or not includes(scope, order["id"], job)):
            raise Failure("The decision is outside the current work binding.", category="not_permitted", reason="scope_not_permitted")
        if target and adapter(scope["role"]) in {"xo", "nco"}:
            from .commands import Commands
            if not Commands._owns_job(db, scope, target):
                raise Failure("The decision belongs to another command team.", category="not_permitted", reason="scope_not_permitted")
        fields = ("id", "objective", "done_when", "constraints", "status")
        data = {"binding": snapshot(scope), "order": {k: order[k] for k in (*fields, "context", "plan_revision")}}
        if target:
            data["job"] = {k: target[k] for k in (*fields, "assignment", "dependencies")}
        if scope.get("assignment_id"):
            assigned = record(db, "role_assignments", scope["assignment_id"])
            data["assignment"] = {k: assigned[k] for k in ("id", "brief", "details", "revision", "status")}
        return data

    def _fresh(self, db, row):
        scope = json.loads(row["binding"])
        if canonical(self.context(db, scope, row["job"])) != row["context"]:
            raise Failure("Decision inputs changed; obtain a current selection.", category="not_ready", reason="decision_stale")
        current = configuration(self.store, role=scope["role"], job=row["job"])
        if current["revision"] != json.loads(row["policy"])["revision"]:
            raise Failure("Decision policy changed; obtain a current selection.", category="not_ready", reason="decision_stale")

    @staticmethod
    def receipt(row, cache_hit=False):
        return {"id": row["id"], "state": row["state"], "purpose": row["purpose"],
                "model": json.loads(row["policy"])["model"], "sources": json.loads(row["request"])["sources"],
                "decisions": json.loads(row["result"]),
                "answers": json.loads(row["answers"]), "evidence": row["evidence"],
                "cache_hit": cache_hit, "_identity": json.loads(row["identity"]),
                "_policy": json.loads(row["policy"])["revision"],
                "resolution": json.loads(row["resolution"]) if row["resolution"] else None}

    def verify(self, receipt, identity=None, db=None):
        if db is None:
            with self.store.connect() as connection:
                return self.verify(receipt, identity, connection)
        row = db.execute("SELECT * FROM decisions WHERE id=? AND member=?", (receipt["id"], self.member)).fetchone()
        if not row or row["state"] != "resolved" or json.loads(row["result"]) != receipt["decisions"]:
            raise Failure("A current resolved service decision is required.", category="not_ready", reason="decision_pending")
        self._fresh(db, row)
        if identity is not None and json.loads(row["identity"]) != identity:
            raise Failure("Decision evidence changed before use.", category="not_ready", reason="decision_stale")
        return dict(row)

    def select(self, purpose, state, questions, sources=(), policy=None, target_job=None, identity=None):
        from .configuration import decision_rule
        from .credentials import credential_values
        from .jev_config import clean_questions
        from .workspaces import Workspaces

        scope = self.control.current()
        if not scope or adapter(scope["role"]) in {"co", "arbitrator"}:
            raise Failure("This role cannot initiate semantic decisions.", category="not_permitted", reason="decision_role")
        job = scope["job"] if target_job is None else target_job
        policy = policy or configuration(self.store, role=scope["role"], job=job)
        with self.store.connect() as db:
            context = self.context(db, scope, job)
        secrets = credential_values(policy["connection"])
        continuation = replay_request(self.member, secrets)
        payload = {"state": clean(state, secrets=secrets), "questions": clean_questions(questions, secrets=secrets),
                   "sources": clean(list(sources), secrets=secrets)}
        fingerprint = digest([purpose, context, identity, policy, payload])
        subject = digest([self.member, purpose, job, self.subject(payload)])
        transport = policy["transport"]
        wait = transport["timeout"] * (transport["max_attempts"] + 1) + transport["max_retry_delay"] * (transport["max_attempts"] - 1)
        # Serialize revisions of one subject through pruning and seat allocation;
        # independent subjects still evaluate concurrently without a DB transaction.
        with Workspaces(self.store, self.member).lock("decision-" + subject, timeout=wait):
            with self.store.connect() as db:
                if self.context(db, scope, job) != context or configuration(self.store, role=scope["role"], job=job)["revision"] != policy["revision"]:
                    raise Failure("Decision inputs changed while waiting.", category="not_ready", reason="decision_stale")
            self.prune(purpose, payload, job, identity)
            with self.store.connect() as db:
                existing = db.execute("SELECT * FROM decisions WHERE fingerprint=? AND state!='stale'", (fingerprint,)).fetchone()
                if existing:
                    self._fresh(db, existing)
                    if existing["state"] == "resolved":
                        return self.receipt(existing, True)
            if existing:
                return self._pending(existing["id"])
            outcome, failed = None, None
            try:
                feature = {"completion": "completion", "context_relevance": "filtering", "assessment": "advice", "scope": "advice"}.get(purpose, "selection")
                outcome = Advice(self.store, self.member).evaluate(purpose, state, questions, list(sources), policy=policy, feature=feature)
            except ValueError as error:
                failed = clean(failure_info(error), secrets=secrets)
                if failed["category"] not in {"evaluation_unavailable", "evaluation_invalid"} or policy["decisions"]["failure_mode"] == "block":
                    raise
            rule = decision_rule(policy, purpose)
            answers = outcome["answers"] if outcome else {}
            selected = {name: classify(answers[name], question, rule, policy["decisions"]["unknown_choices"])
                        for name, question in questions.items()} if outcome else {name: None for name in questions}
            unresolved = [name for name, value in selected.items() if value is None]
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                if self.context(db, scope, job) != context or configuration(self.store, role=scope["role"], job=job)["revision"] != policy["revision"]:
                    raise Failure("Decision inputs changed while evaluating.", category="not_ready", reason="decision_stale")
                ident = db.execute("INSERT INTO decisions(fingerprint,member,work_order,job,purpose,binding,context,identity,policy,request,answers,result,unresolved,evidence,failure,state,continuation,created,updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                    fingerprint, self.member, scope["work_order"], job, purpose, canonical(scope), canonical(context), canonical(identity),
                    canonical(policy), canonical(payload), canonical(answers), canonical({k: v for k, v in selected.items() if v is not None}),
                    canonical(unresolved), outcome["evidence"] if outcome else None, canonical(failed) if failed else None,
                    "pending" if unresolved else "resolved", canonical(continuation) if continuation else None, stamp(), stamp())).lastrowid
                self.store._event(db, self.member, "decision_selected", {"decision": ident, "purpose": purpose,
                    "state": "pending" if unresolved else "resolved", "unresolved": unresolved,
                    "work_order": scope["work_order"], "job": job, "evidence": outcome["evidence"] if outcome else None,
                    "configuration_revision": policy["revision"]})
                row = db.execute("SELECT * FROM decisions WHERE id=?", (ident,)).fetchone()
            if unresolved:
                return self._pending(ident)
            return self.receipt(row, bool(outcome and outcome.get("cache_hit")))

    @staticmethod
    def subject(payload):
        state = payload["state"]
        if "target" in state:
            return state["target"]
        proposal = state.get("proposal")
        if isinstance(proposal, dict):
            return {k: proposal.get(k) for k in ("name", "job", "role")}
        return None

    def prune(self, purpose, payload, job, identity):
        """Retire obsolete investigations without consuming collaborator capacity."""
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for row in db.execute("SELECT * FROM decisions WHERE member=? AND state='pending'", (self.member,)).fetchall():
                try:
                    self._fresh(db, row)
                    stale = (row["purpose"] == purpose and row["job"] == job and
                             self.subject(json.loads(row["request"])) == self.subject(payload) and
                             json.loads(row["identity"]) != identity)
                except ValueError:
                    stale = True
                if stale:
                    self._supersede(db, row)

    def retire_stale(self):
        """Revoke obsolete investigations even when their requester makes no new call."""
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for row in db.execute("SELECT * FROM decisions WHERE state='pending'").fetchall():
                try:
                    Decisions(self.store, row["member"])._fresh(db, row)
                except ValueError:
                    self._supersede(db, row)

    def _supersede(self, db, row):
        """Retire the decision and its own investigator in the caller's transaction."""
        from .delivery import Delivery

        current = binding(db, row["arbitrator"]) if row["arbitrator"] else None
        if current and current.get("assignment_id") == row["assignment"]:
            Delivery.release(db, current["member"])
        if row["assignment"]:
            db.execute("UPDATE role_assignments SET status='cancelled',updated=? WHERE id=?", (stamp(), row["assignment"]))
        db.execute("UPDATE decisions SET state='stale',updated=? WHERE id=?", (stamp(), row["id"]))
        self.store._event(db, row["member"], "decision_superseded", {"decision": row["id"], "work_order": row["work_order"], "job": row["job"]})

    def _pending(self, ident):
        from .commands import Commands
        from .runtime import retain

        commands = Commands(self.store, self.member, self.control)
        config = commands._config()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM decisions WHERE id=? AND member=?", (ident, self.member)).fetchone()
            self._fresh(db, row)
            if row["state"] == "resolved":
                return self.receipt(row, True)
            if row["state"] != "pending":
                raise Failure("This investigation was superseded by current work.", category="not_ready", reason="decision_stale")
            role = json.loads(row["policy"])["decisions"]["arbitrator_role"]
            prior = binding(db, row["arbitrator"]) if row["arbitrator"] else None
            if not prior or prior.get("assignment_id") != row["assignment"]:
                slot = commands._slot(db, config, role=role)
                fields = {"objective": "Resolve the supplied ambiguous " + row["purpose"] + " decision.",
                          "done_when": "A supported resolution is recorded through decision_resolve.",
                          "constraints": "Investigate within this work binding. Resolve only the supplied unanswered questions; preserve mechanical gates and owner requirements."}
                assigned = row["assignment"]
                if assigned is None:
                    assigned = db.execute("INSERT INTO role_assignments(work_order,job,name,role,brief,details,created_by,created,updated) VALUES (?,?,?,?,?,?,?,?,?)",
                        (row["work_order"], row["job"], "Decision " + str(ident), role, canonical(fields), canonical({"decision": ident}), self.member, stamp(), stamp())).lastrowid
                else:
                    db.execute("UPDATE role_assignments SET status='active',revision=revision+1,updated=? WHERE id=?", (stamp(), assigned))
                target = self.control.bind_in(db, slot["name"], role, row["work_order"], row["job"], self.member)
                db.execute("UPDATE member_bindings SET assignment_id=? WHERE member=?", (assigned, target["member"]))
                target = binding(db, target["member"])
                if config.get("runtime"):
                    retain(self.store, db, target, slot)
                db.execute("UPDATE decisions SET assignment=?,arbitrator=?,updated=? WHERE id=?", (assigned, target["member"], stamp(), ident))
                commands._start_intent(db, target, slot)
                commands._direction(db, target, "Investigate the decision packet in work_context. Use decision_resolve with your resolution, reason and retained evidence.")
                self.store._event(db, self.member, "decision_escalated", {"decision": ident, "arbitrator": target["member"], "role": role,
                    "work_order": row["work_order"], "job": row["job"], "assignment": assigned})
            else:
                target = prior
        raise Failure("The service assigned Arbitrator to resolve this decision; the operation is waiting.", category="not_ready", reason="decision_pending",
                      details={"decision": ident, "arbitrator": target["member"], "references": [row["evidence"]] if row["evidence"] else []})

    def packet(self, scope, db):
        if adapter(scope["role"]) != "arbitrator" or not scope.get("assignment_id"):
            return None
        row = db.execute("SELECT * FROM decisions WHERE assignment=? AND arbitrator=?", (scope["assignment_id"], scope["member"])).fetchone()
        if not row:
            return None
        payload = json.loads(row["request"])
        return {"decision": row["id"], "purpose": row["purpose"], "status": row["state"],
                "question_names": json.loads(row["unresolved"]), **payload,
                "evaluation": json.loads(row["answers"]), "evidence": row["evidence"],
                "failure": json.loads(row["failure"]) if row["failure"] else None,
                "already_selected": json.loads(row["result"])}

    def resolve(self, decisions, reason, evidence):
        from .artifacts import Artifacts
        from .credentials import credential_values
        from .delivery import Delivery

        scope = self.control.current()
        if not scope or adapter(scope["role"]) != "arbitrator":
            raise Failure("Only the assigned Arbitrator resolves a pending decision.", category="not_permitted", reason="decision_role")
        require_capability(scope["role"], "decision_resolve")
        with self.store.connect() as db:
            pending = db.execute("SELECT * FROM decisions WHERE assignment=? AND arbitrator=? AND state='pending'", (scope.get("assignment_id"), self.member)).fetchone()
            if not pending:
                raise Failure("There is no pending decision assigned to this role binding.", category="not_permitted", reason="decision_assignment_required")
            Decisions(self.store, pending["member"])._fresh(db, pending)
        secrets = credential_values(json.loads(pending["policy"])["connection"])
        resolution = clean({"member": self.member, "reason": reason, "evidence": evidence, "decisions": decisions,
                            "evaluation": pending["evidence"], "decision": pending["id"]}, secrets=secrets)
        saved = Artifacts(self.store, self.member).save("decision-" + str(pending["id"]) + "-resolution.json",
            content=canonical(resolution), media_type="application/json", work_order=None, job=None)
        resolution_reference = "artifact:" + str(saved["artifact"]["id"])
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = binding(db, self.member)
            if not current or snapshot(current) != snapshot(scope) or current["paused"]:
                raise Failure("Your arbitration assignment changed or is paused.", category="not_ready", reason="decision_stale")
            row = db.execute("SELECT * FROM decisions WHERE assignment=? AND arbitrator=? AND state='pending'", (scope.get("assignment_id"), self.member)).fetchone()
            if not row:
                raise Failure("There is no pending decision assigned to this role binding.", category="not_permitted", reason="decision_assignment_required")
            owner = Decisions(self.store, row["member"])
            owner._fresh(db, row)
            names = json.loads(row["unresolved"])
            if not isinstance(decisions, dict) or set(decisions) != set(names):
                raise Failure("Resolve exactly the unanswered questions in your packet.", category="invalid_request", reason="invalid_decision")
            questions = json.loads(row["request"])["questions"]
            unknown = json.loads(row["policy"])["decisions"]["unknown_choices"]
            for name, value in decisions.items():
                question = questions[name]
                kind = question["type"]
                valid = (type(value) is bool if kind == "noul" else
                         isinstance(value, str) and value in question["criteria"] and value not in unknown if kind == "choice" else
                         type(value) in {int, float} and 0 <= value <= len(question["criteria"]) - 1)
                if not valid:
                    raise Failure("Resolution is outside the question's permitted choices.", category="invalid_request", reason="invalid_decision")
            if not reason.strip() or not evidence:
                raise Failure("Resolution needs its reasoning and retained evidence.", category="invalid_request", reason="resolution_evidence_required")
            for reference in evidence:
                self.control._reference(db, scope, reference)
            result = {**json.loads(row["result"]), **decisions}
            from .work import Work
            Work(self.store, self.member).attach(db, row["work_order"], row["job"], "artifact", str(saved["artifact"]["id"]))
            db.execute("UPDATE decisions SET state='resolved',result=?,resolution=?,evidence=?,updated=? WHERE id=?", (canonical(result), canonical(resolution), resolution_reference, stamp(), row["id"]))
            db.execute("UPDATE role_assignments SET status='done',updated=? WHERE id=?", (stamp(), row["assignment"]))
            self.store._event(db, self.member, "decision_resolved", {"decision": row["id"], "work_order": row["work_order"], "job": row["job"], **resolution})
            Delivery.release(db, self.member)
        return {"decision": row["id"], "state": "resolved", "decisions": result,
                "continuation": owner.resume(row["id"])}

    def resume(self, ident, *, wait=True):
        """Replay retained calls after the investigator has released its native seat."""
        from .api import API
        from .workspaces import Workspaces

        with Workspaces(self.store, self.member).lock("decision-resume-" + str(ident), timeout=10 if wait else 0):
            outcome = None
            with self.store.connect() as db:
                row = db.execute("SELECT * FROM decisions WHERE id=? AND member=?", (ident, self.member)).fetchone()
                if not row or not row["continuation"]:
                    return {"state": "available"}
                if row["state"] != "resolved":
                    return {"state": row["state"]}
                prior = json.loads(row["resumed"]) if row["resumed"] else None
                if prior and prior["state"] != "waiting_runtime":
                    return prior
                try:
                    self._fresh(db, row)
                except ValueError as error:
                    outcome = {"state": "blocked", "failure": failure_info(error)}
                if outcome is None and row["arbitrator"]:
                    investigator = db.execute("SELECT * FROM members WHERE name=?", (row["arbitrator"],)).fetchone()
                    current = binding(db, row["arbitrator"])
                    same_seat = not current or current.get("assignment_id") == row["assignment"]
                    if (investigator and investigator["harness"] != "external" and same_seat and
                            (investigator["pid"] or investigator["state"] not in {"stopped", "failed"})):
                        waiting = {"state": "waiting_runtime", "member": row["arbitrator"]}
                        if prior != waiting:
                            db.execute("UPDATE decisions SET resumed=?,updated=? WHERE id=?", (canonical(waiting), stamp(), ident))
                        return waiting
                continuation = json.loads(row["continuation"])
            if outcome is None:
                try:
                    if continuation["tool"] == "runtime_start":
                        # This continuation is retained only by the service's
                        # bound-runtime selector; it is not an agent MCP tool.
                        with self.store.connect() as db:
                            db.execute("BEGIN IMMEDIATE")
                            self._fresh(db, row)
                            if continuation["member"] != self.member or continuation["arguments"]:
                                raise ValueError("Invalid bound-runtime continuation")
                            db.execute("UPDATE member_bindings SET launch_pending=1,launch_error=NULL WHERE member=?", (self.member,))
                        outcome = {"state": "scheduled", "member": self.member}
                    else:
                        result = API(self.store.directory, self.member).call(continuation["tool"], continuation["arguments"])
                        outcome = {"state": "completed", "result": result}
                except ValueError as error:
                    outcome = {"state": "waiting" if failure_info(error)["reason"] == "decision_pending" else "blocked", "failure": failure_info(error)}
            with self.store.connect() as db:
                db.execute("UPDATE decisions SET resumed=?,updated=? WHERE id=?", (canonical(outcome), stamp(), ident))
                self.store._event(db, self.member, "decision_applied", {"decision": ident, "work_order": row["work_order"], "job": row["job"], "outcome": outcome["state"]})
            return outcome

    def tick(self):
        """Retire obsolete work, allocate waiting investigators, and resume resolved calls."""
        self.retire_stale()
        with self.store.connect() as db:
            waiting = db.execute("SELECT d.id,d.member FROM decisions d WHERE d.state='pending' AND NOT EXISTS "
                                 "(SELECT 1 FROM member_bindings b WHERE b.member=d.arbitrator "
                                 "AND b.assignment_id=d.assignment) ORDER BY d.id LIMIT 8").fetchall()
        for row in waiting:
            try:
                Decisions(self.store, row["member"])._pending(row["id"])
            except ValueError as error:
                failure = failure_info(error)
                if failure["reason"] == "decision_pending":
                    continue
                # Capacity and stale-work failures remain visible without
                # breaking launch recovery for the rest of the team.
                data = {"decision": row["id"], "failure": failure}
                with self.store.connect() as db:
                    previous = db.execute("SELECT data FROM events WHERE actor=? AND kind='decision_investigation_waiting' "
                                          "AND json_extract(data,'$.decision')=? ORDER BY id DESC LIMIT 1",
                                          (row["member"], row["id"])).fetchone()
                    if not previous or json.loads(previous["data"]) != data:
                        self.store._event(db, row["member"], "decision_investigation_waiting", data)
        with self.store.connect() as db:
            rows = db.execute("SELECT id,member FROM decisions WHERE state='resolved' AND resolution IS NOT NULL "
                              "AND continuation IS NOT NULL AND (resumed IS NULL OR "
                              "json_extract(resumed,'$.state')='waiting_runtime') ORDER BY id LIMIT 8").fetchall()
        for row in rows:
            try:
                Decisions(self.store, row["member"]).resume(row["id"], wait=False)
            except (BlockingIOError, TimeoutError, ValueError):
                continue
