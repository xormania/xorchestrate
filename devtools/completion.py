"""Proof-backed completion boundaries over existing reports, artifacts and Jev telemetry."""

from __future__ import annotations

import json

from .artifacts import Artifacts
from .assignments import current_update, snapshot
from .commands import Commands
from .configuration import resolve
from .control import Control, binding, includes
from .decisions import Decisions
from .failures import Failure, failure_info
from .role_packages import adapter, names_for_adapter
from .store import canonical, digest
from .telemetry import clean
from .work import record

FIELDS = ("objective", "done_when", "constraints")
BOUNDARIES = {"assignment", "job", "work_order"}


def requirements(row):
    return {key: row.get(key, "") for key in FIELDS}


class Completion:
    def __init__(self, store, member):
        self.store, self.member = store, member
        self.control = Control(store, member)
        self.artifacts = Artifacts(store, member)

    def _boundary(self, db, scope, boundary, job, assignment):
        """Resolve current work authority for both policy and proof collection."""
        if not scope or scope != binding(db, self.member) or adapter(scope["role"]) not in {"xo", "nco"}:
            raise Failure("Completion requires the current active execution officer.", category="not_permitted", reason="scope_not_permitted")
        if scope["paused"]:
            raise Failure("Completion cannot proceed while this work is paused.", category="not_ready", reason="work_paused")
        team = {self.member, *(r["member"] for r in Commands._descendants(db, scope))}
        assigned = None
        if boundary == "assignment":
            ident = assignment["id"] if isinstance(assignment, dict) else assignment
            assigned = record(db, "role_assignments", ident)
            if assigned["work_order"] != scope["work_order"]:
                raise Failure("The completion assignment is outside current work.", category="not_permitted", reason="scope_not_permitted")
            if assigned["job"] is None and assigned["created_by"] not in team:
                raise Failure("The completion assignment is outside the current command team.", category="not_permitted", reason="scope_not_permitted")
            job = assigned["job"]
        work = record(db, "jobs", job) if job is not None else None
        if work and (not includes(scope, work["work_order"], job) or not Commands._owns_job(db, scope, work)):
            raise Failure("The completion job is outside current work.", category="not_permitted", reason="scope_not_permitted")
        if boundary == "job" and job is None or boundary == "work_order" and (job is not None or adapter(scope["role"]) != "xo"):
            raise Failure("Select the existing job or XO work-order boundary.", category="invalid_request", reason="invalid_boundary")
        return work, assigned, team

    def settings(self, boundary, job=None, assignment=None):
        """Resolve only trusted role/job identities, including disabled gate receipts."""
        scope = self.control.current()
        with self.store.connect() as db:
            work, _, _ = self._boundary(db, scope, boundary, job, assignment)
        return resolve(self.store, role=scope["role"], job=work["id"] if work else None), digest(scope)

    def collect(self, boundary, job, assignment, revision, mechanical):
        scope = self.control.current()
        with self.store.connect() as db:
            work, assigned, team = self._boundary(db, scope, boundary, job, assignment)
            job = work["id"] if work else None
            order = record(db, "work_orders", scope["work_order"])
            if boundary == "work_order" and db.execute("SELECT 1 FROM member_bindings WHERE work_order=? AND role IN (SELECT value FROM json_each(?)) AND member!=?", (order["id"], canonical(names_for_adapter("xo")), self.member)).fetchone():
                raise Failure("Completion requires owner-level coordination across independent command teams.", category="not_permitted", reason="scope_not_permitted")
            target = json.loads(assigned["brief"]) if assigned else work if work else order
            target = requirements(target)
            if not target["objective"] or not target["done_when"]:
                raise Failure("Completion requires the original objective and completion conditions.", category="not_ready", reason="requirements_missing")
            clause, args = "work_order=?", [order["id"]]
            if boundary != "work_order":
                clause += " AND job IS ?"
                args.append(job)
            assignments = [dict(r) for r in db.execute("SELECT * FROM role_assignments WHERE " + clause + " ORDER BY id", args)]
            assignments = [r for r in assignments if r["job"] is not None or r["created_by"] in team]
            arbitration = {r[0] for r in db.execute(
                "SELECT assignment FROM decisions WHERE work_order=? AND assignment IS NOT NULL", (order["id"],),
            )}
            # Resolving this decision must not invalidate its own proof identity.
            # Only service-owned arbitration assignments are excluded; ordinary
            # role work and every measured check remain part of the boundary.
            assignments = [r for r in assignments if r["id"] not in arbitration]
            if assigned:
                assignments = [assigned]
            scopes = [dict(r) for r in db.execute("SELECT * FROM member_bindings WHERE " + clause + " ORDER BY member", args)]
            scopes = [r for r in scopes if r["member"] in team and r.get("assignment_id") not in arbitration]
            if assigned:
                scopes = [r for r in scopes if r.get("assignment_id") == assigned["id"]]
                if assigned["status"] == "active" and not scopes:
                    raise Failure("The completion assignment is outside the current command team.", category="not_permitted", reason="scope_not_permitted")
            reports = {}
            for current in scopes:
                result = current_update(db, current, kind="result")
                if result:
                    reports[result["id"]] = result
            for item in assignments:
                if item["status"] == "done" and item["result_update"]:
                    row = record(db, "work_updates", item["result_update"])
                    reports[row["id"]] = row
            rows = [dict(r) for r in db.execute("SELECT * FROM operations WHERE " + clause + " ORDER BY id DESC", args)]
            rows = [r for r in rows if r["action"] in {"work_check", "work_submit"}
                    or json.loads(r["binding"]).get("assignment_id") not in arbitration]
            retained_members = {r["actor"] for r in reports.values()}
            rows = [r for r in rows if r["job"] is not None or r["member"] in team or r["member"] in retained_members]
            if assigned:
                identities = {canonical(snapshot(r)) for r in scopes}
                identities.update(r["binding"] for r in reports.values() if r["binding"])
                rows = [r for r in rows if canonical(snapshot(json.loads(r["binding"]))) in identities]
            if boundary == "work_order":
                if any(not Commands._owns_job(db, scope, dict(r)) for r in db.execute("SELECT * FROM jobs WHERE work_order=?", (order["id"],))):
                    raise Failure("Completion requires owner-level coordination across independent command teams.", category="not_permitted", reason="scope_not_permitted")
            jobs = [dict(r) for r in db.execute("SELECT j.id,j.title,j.objective,j.done_when,j.constraints,j.status,j.updated,d.revision AS accepted_revision FROM jobs j LEFT JOIN deliveries d ON d.job=j.id WHERE j.work_order=? ORDER BY j.id", (order["id"],))] if boundary == "work_order" else []
            accepted_revisions = {r["id"]: r["accepted_revision"] for r in jobs if r["accepted_revision"]}
        operations, checks, artifact_ids, measured, seen = [], {}, {r["artifact"] for r in reports.values() if r["artifact"]}, {}, set()
        stale = {"references": [], "checks": [], "revision": revision, "job": job, "assignment": assigned["id"] if assigned else None}
        stale_seen = set()
        for row in rows:
            result = json.loads(row["result"]) if row["result"] else None
            validation = (result.get("validation") or result) if isinstance(result, dict) else {}
            expected_revision = accepted_revisions.get(row["job"], revision)
            if row["action"] not in {"work_check", "work_submit"}:
                continue
            key = (row["action"], row["arguments"] if row["action"] == "work_check" else None, row["job"])
            if key in seen:
                continue
            details = {"references": ["operation:" + str(row["id"])],
                       "checks": list(dict.fromkeys(check["check"] for check in validation.get("checks", []))),
                       "revision": expected_revision, "job": row["job"], "assignment": assigned["id"] if assigned else None}
            details["references"].extend(check["evidence"] for check in validation.get("checks", [])
                                         if check.get("evidence", "").startswith("artifact:"))
            if expected_revision is not None and validation.get("revision") not in {None, expected_revision}:
                # Stale measurements remain useful recovery references, never current proof.
                if key not in stale_seen:
                    stale["references"].extend(details["references"])
                    if row["job"] == job:
                        stale["checks"].extend(details["checks"])
                    stale_seen.add(key)
                continue
            seen.add(key)
            if row["state"] in {"queued", "running"}:
                raise Failure("Completion awaits an active measurement operation.", category="not_ready", reason="measurement_active", details=details)
            if row["action"] == "work_check" and row["state"] != "completed":
                raise Failure("Completion needs a completed current measurement; inspect operation:" + str(row["id"]), category="not_ready", reason="measurement_incomplete", details=details)
            if validation.get("uncommitted"):
                raise Failure("Completion needs measurements on saved source; the retained check included uncommitted changes.", category="not_ready", reason="source_unsaved", details=details)
            if validation.get("passed") is False and not validation.get("checks"):
                raise Failure("Completion needs the current retained measurement to pass; inspect operation:" + str(row["id"]), category="not_ready", reason="check_failed", details=details)
            # Each named check's latest result wins. A later unrelated check cannot erase a failure.
            for check in validation.get("checks", []):
                name = ("job " + str(row["job"]) + ": " if row["job"] is not None else "") + check["check"]
                if name not in checks:
                    checks[name] = {"exit_code": check["exit_code"], "operation": row["id"], "check": check["check"], "job": row["job"], "evidence": check.get("evidence")}
                    reference = check.get("evidence")
                    if reference and reference.startswith("artifact:"):
                        artifact = int(reference.split(":")[1])
                        artifact_ids.add(artifact)
                        measured.setdefault(artifact, []).append({"job": row["job"], "revision": validation.get("revision"), "expected_revision": expected_revision})
            compact = {key: validation[key] for key in ("passed", "revision") if key in validation}
            compact["checks"] = [{key: check[key] for key in ("check", "exit_code", "evidence") if key in check} for check in validation.get("checks", [])]
            operations.append({"reference": "operation:" + str(row["id"]), "job": row["job"], "expected_revision": expected_revision, "state": row["state"], "action": row["action"], "measurement": compact, "error": row["error"][:500] if row["error"] else None})
        state = {"boundary": boundary, "target": {"work_order": order["id"], "job": job, "assignment": assigned["id"] if assigned else None}, "requirements": target, "owner": requirements(order),
                 "job": requirements(work) if work else None, "jobs": jobs,
                 "assignment": {"role": assigned["role"], "requirements": target} if assigned else None,
                 "reports": [{"reference": "update:" + str(r["id"]), "text": r["text"], "artifact": "artifact:" + str(r["artifact"]) if r["artifact"] else None} for r in reports.values()],
                 "operations": operations, "mechanical": mechanical or {}, "source_revision": revision}
        identity = digest([scope, order, work, assigned, assignments, list(reports.values()), rows, mechanical, revision])
        stale["references"] = list(dict.fromkeys(stale["references"]))
        stale["checks"] = list(dict.fromkeys(stale["checks"]))
        return state, sorted(artifact_ids), checks, identity, measured, stale

    def require(self, boundary, *, job=None, assignment=None, revision=None, mechanical=None):
        if boundary not in BOUNDARIES:
            raise Failure("Unknown completion boundary.", category="invalid_request", reason="invalid_boundary")
        parameters = {"boundary": boundary, "job": job, "assignment": assignment["id"] if isinstance(assignment, dict) else assignment,
                      "revision": revision, "mechanical": mechanical}
        config, scope_identity = self.settings(boundary, job, assignment)
        receipt = {"boundary": boundary, "_configuration": config["revision"], "_scope": scope_identity, "_parameters": parameters}
        if not config["features"]["completion"]:
            return {**receipt, "state": "disabled"}
        evidence, target = None, {}
        try:
            policy = config["completion"]
            state, artifacts, checks, identity, measured, stale = self.collect(boundary, job, assignment, revision, mechanical)
            target = state["target"]
            details = {"references": ["artifact:" + str(ident) for ident in artifacts], "revision": revision, "job": target["job"], "assignment": target["assignment"]}
            state["boundary_rule"] = policy["boundaries"][boundary]
            for name, check in (mechanical or {}).items():
                if isinstance(check, dict) and (check.get("passed") is False or check.get("clean") is False):
                    raise Failure("Completion needs the current " + name + " check to pass.", category="not_ready", reason="check_failed", details=details)
                if isinstance(check, dict) and revision and check.get("revision", revision) != revision:
                    raise Failure("Completion needs " + name + " evidence for the current source revision.", category="not_ready", reason="proof_stale", details=details)
            failed = {name: check for name, check in checks.items() if check["exit_code"] != 0}
            if failed:
                references = ["operation:" + str(check["operation"]) for check in failed.values()]
                references.extend(check["evidence"] for check in failed.values() if check.get("evidence"))
                raise Failure("Completion needs passing measurements: " + ", ".join(failed), category="not_ready", reason="check_failed",
                              details={**details, "references": list(dict.fromkeys(references)),
                                       "checks": list(dict.fromkeys(check["check"] for check in failed.values() if check["job"] == target["job"]))})
            if not artifacts:
                raise Failure("Completion needs retained proof of the objective and completion conditions; a result claim alone is insufficient.", category="not_ready",
                              reason="proof_stale" if stale["references"] else "proof_missing", details=stale if stale["references"] else details)
            sources = []
            for ident in artifacts:
                # References originate only in these trusted current reports/operations; verify their retained work link.
                with self.store.connect() as db:
                    scope = self.control.current()
                    self.control._reference(db, scope, "artifact:" + str(ident))
                value = self.artifacts.read(ident, limit=policy["artifact_bytes"])
                if value["encoding"] != "utf8":
                    continue
                measurement_scopes = measured.get(ident) or []
                measurement = measurement_scopes[0] if measurement_scopes else {}
                source_revision = measurement.get("revision")
                expected_revision = measurement.get("expected_revision", revision)
                proof_kind, workspace_clean = ("measured_check", True) if measurement else ("retained_artifact", None)
                if not measurement:
                    reports = {r["reference"] for r in state["reports"] if r["artifact"] == "artifact:" + str(ident)}
                    captures = [p for p in value["sources"] if p.get("provider") == "work_report" and p.get("reference") in reports]
                    metadata = [p["metadata"] if isinstance(p.get("metadata"), dict) else p for p in captures]
                    capture = next((p for p in metadata if p.get("source_revision") == expected_revision and p.get("workspace_clean") is True), metadata[0] if metadata else {})
                    source_revision = capture.get("source_revision")
                    proof_kind = capture.get("revision_kind", proof_kind)
                    workspace_clean = capture.get("workspace_clean")
                source = {"reference": "artifact:" + str(ident), "content": value["content"],
                          "partial": value["next_offset"] is not None, "source_revision": source_revision,
                          "expected_revision": expected_revision, "measurement_scopes": measurement_scopes,
                          "kind": proof_kind, "workspace_clean": workspace_clean,
                          "current_source": None if expected_revision is None or source_revision is None else source_revision == expected_revision and workspace_clean is not False}
                state.setdefault("proof", []).append(source)
                with self.store.connect() as db:
                    sha256 = db.execute("SELECT sha256 FROM artifacts WHERE id=?", (ident,)).fetchone()[0]
                sources.append({"reference": source["reference"], "source_sha256": sha256, "partial": source["partial"]})
            if not state.get("proof"):
                raise Failure("Completion needs readable retained proof, such as measured output or a verifiable deliverable.", category="not_ready", reason="proof_unreadable", details=details)
            if revision and not any(source["current_source"] is True for source in state["proof"]):
                raise Failure("Completion needs retained proof measured or captured against the current source revision.", category="not_ready", reason="proof_stale",
                              details={**details, "checks": stale["checks"], "references": list(dict.fromkeys(details["references"] + stale["references"]))})
            if len(canonical(clean(state)).encode()) > policy["state_bytes"]:
                raise Failure("Completion proof exceeds the bounded assessment; retain a concise measurement that covers all requirements.", category="not_ready", reason="proof_oversize", details=details)
            result = Decisions(self.store, self.member).select(
                "completion", state, policy["questions"], sources, policy=config,
                target_job=target["job"], identity=identity,
            )
            self._unchanged(receipt, during=True)
            if not isinstance(result, dict) or not isinstance(result.get("evidence"), str) or not result["evidence"].startswith("artifact:"):
                raise Failure("Completion assessment is incomplete; retain a valid assessment before accepting work.", category="evaluation_invalid", reason="invalid_response")
            evidence = result["evidence"]
            if self.collect(boundary, job, assignment, revision, mechanical)[3] != identity:
                raise Failure("Completion requirements or proof changed during assessment; inspect the current work before retrying.", category="not_ready", reason="requirements_changed")
            decisions = result.get("decisions")
            if not isinstance(decisions, dict) or set(decisions) != set(FIELDS):
                raise Failure("Completion assessment is incomplete; obtain a valid judgment for every required criterion.", category="evaluation_invalid", reason="invalid_response")
            if any(type(value) is not bool for value in decisions.values()):
                raise Failure("Completion assessment has an invalid required-criterion decision.", category="evaluation_invalid", reason="invalid_response")
            Decisions(self.store, self.member).verify(result, identity=identity)
            missing = [name for name in FIELDS if not decisions[name]]
            self.store.event(self.member, "completion_assessed", **target, boundary=boundary, outcome="blocked" if missing else "ready", evidence=evidence, decision=result["id"], required=list(FIELDS), unresolved=missing, policy=config["revision"])
            if missing:
                raise Failure("Completion needs stronger current proof for " + ", ".join(missing) + ". Inspect " + evidence + " and measure the unmet requirement.", category="not_ready", reason="proof_insufficient",
                              details={**details, "references": [evidence], "criteria": missing})
            return {**receipt, "state": "ready", "evidence": evidence, "decision": result["id"],
                    "cache_hit": result.get("cache_hit", False), "_identity": identity, "_decision": result}
        except ValueError as error:
            self.store.event(self.member, "completion_blocked", **target, boundary=boundary, evidence=evidence, reason=str(error), failure=failure_info(error))
            raise

    def _unchanged(self, receipt, *, during=False):
        params = receipt["_parameters"]
        config, scope_identity = self.settings(params["boundary"], params["job"], params["assignment"])
        when = "during" if during else "after"
        if config["revision"] != receipt["_configuration"] or scope_identity != receipt["_scope"]:
            raise Failure("Completion policy changed " + when + " assessment; obtain a current assessment.", category="not_ready", reason="policy_changed")

    def verify(self, receipt):
        """Recheck an internal receipt immediately before a service commits acceptance."""
        self._unchanged(receipt)
        if receipt["state"] == "disabled":
            return
        params = receipt["_parameters"]
        if self.collect(params["boundary"], params["job"], params["assignment"], params["revision"], params["mechanical"])[3] != receipt["_identity"]:
            raise Failure("Completion requirements or proof changed after assessment; inspect current work before acceptance.", category="not_ready", reason="requirements_changed")
        if receipt.get("_decision"):
            Decisions(self.store, self.member).verify(receipt["_decision"], identity=receipt["_identity"])
