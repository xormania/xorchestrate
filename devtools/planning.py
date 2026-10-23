"""XO-owned plans and role assignments over the existing jobs and sessions."""

from __future__ import annotations

import json
from contextlib import ExitStack

from .assignments import covers, current_update, prepare, valid_path
from .control import binding
from .delivery import Delivery
from .role_packages import adapter, names_for_adapter
from .store import canonical, stamp
from .work import record
from .workspaces import EDITORS, Workspaces, git


def capabilities(spec):
    name = {"type": "string", "minLength": 1, "maxLength": 100}
    text = {"type": "string", "minLength": 1, "maxLength": 800}
    paths = {"type": "array", "maxItems": 30, "items": {**name, "maxLength": 300}}
    return [spec(
        "plan_adopt", "XO: adopt a retained planner result and the complete job list. One job is coordinated by XO; multiple jobs each need an NCO. Revisions preserve job identities and history.",
        {"plan": {"type": "string", "pattern": "^update:[1-9][0-9]*$", "maxLength": 80},
         "jobs": {"type": "array", "minItems": 1, "maxItems": 20, "items": {
             "type": "object", "additionalProperties": False,
             "properties": {"name": name, "objective": text, "done_when": text,
                            "constraints": {**text, "minLength": 0},
                            "dependencies": {"type": "array", "maxItems": 19, "items": name},
                            "editable_paths": paths, "context_paths": paths},
             "required": ["name", "objective", "done_when", "constraints"]}}},
        ("plan", "jobs"))]


def mission(db, order):
    row = record(db, "work_orders", order)
    return {key: row[key] for key in ("objective", "done_when", "constraints", "context")}


def current_plan(db, order):
    work = record(db, "work_orders", order)
    row = db.execute("SELECT * FROM work_plans WHERE work_order=? AND revision=?",
                     (order, work["plan_revision"])).fetchone()
    if not row:
        raise ValueError("XO must commission a planner and use plan_adopt before assigning execution jobs.")
    if json.loads(row["mission"]) != mission(db, order):
        raise ValueError("The work-order context changed after planning; ask XO for a current plan.")
    return dict(row)


def writer_available(db, scope, role, existing=None):
    """One editable worktree per job: editors take turns; readers can overlap."""
    if role not in EDITORS or not scope["job"]:
        return
    rows = db.execute("SELECT * FROM member_bindings WHERE job=? AND paused=0", (scope["job"],))
    if any(row["role"] in EDITORS and row["member"] != existing for row in rows):
        raise ValueError("This job already has an active editing assignment. Complete or pause it before starting another editor; other jobs and read-only roles can run concurrently.")


def execution_current(db, scope):
    if not scope["job"]:
        return
    order = record(db, "work_orders", scope["work_order"])
    if not order["plan_revision"]:  # Existing operator-bound work remains recoverable.
        return
    plan = current_plan(db, order["id"])
    job = record(db, "jobs", scope["job"])
    if adapter(scope["role"]) == "nco":
        return
    Planning.dependencies(db, job)
    if scope.get("assignment_id"):
        assigned = record(db, "role_assignments", scope["assignment_id"])
        details = json.loads(assigned["details"])
        if details.get("plan", {}).get("reference") != json.loads(job["assignment"]).get("plan", {}).get("reference"):
            raise ValueError("The job plan changed; refresh this role assignment with orchestrate before resuming execution.")
        parent = binding(db, scope["reports_to"])
        expected = "xo" if len(json.loads(plan["jobs"])) == 1 else "nco"
        if not parent or adapter(parent["role"]) != expected:
            raise ValueError("XO must establish the job's coordinator for the current plan before execution resumes.")


class Planning:
    def __init__(self, commands):
        self.commands = commands
        self.store, self.member, self.control = commands.store, commands.member, commands.control
        self.work = commands.work

    def adopt(self, plan, jobs):
        self.commands._capability("plan_adopt")
        scope = self.control.current()
        spaces = Workspaces(self.store, self.member)
        with spaces.lock("order-" + str(scope["work_order"]), timeout=1), ExitStack() as locks:
            with self.store.connect() as db:
                ids = [r[0] for r in db.execute("SELECT id FROM jobs WHERE work_order=? ORDER BY id", (scope["work_order"],))]
            for ident in ids:
                locks.enter_context(spaces.lock("job-" + str(ident)))
            with self.store.connect() as db:
                _, proposed = self.control._reference(db, scope, plan)
                author = binding(db, proposed["actor"])
                assigned = (dict(record(db, "role_assignments", author["assignment_id"]))
                            if author and author.get("assignment_id") and adapter(author["role"]) == "planner"
                            and author["reports_to"] == self.member and author["job"] is None else None)
            gate = None
            if assigned:
                from .completion import Completion

                gate = Completion(self.store, self.member).require("assignment", assignment=assigned)
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                scope = self.commands._scope(db, {"xo"})
                if scope["paused"]:
                    raise ValueError("Work is paused.")
                if gate:
                    Completion(self.store, self.member).verify(gate)
                return self._adopt(db, scope, plan, jobs)

    def _adopt(self, db, scope, plan, jobs):
        definitions = []
        for item in jobs:
            item = dict(item, name=item["name"].strip())
            item["dependencies"] = [name.strip() for name in item.get("dependencies", [])]
            if not item["name"]:
                raise ValueError("Job names cannot be empty.")
            for key in ("editable_paths", "context_paths"):
                if key in item and (len(set(item[key])) != len(item[key]) or any(not valid_path(p) for p in item[key])):
                    raise ValueError("Job paths must be distinct relative project paths.")
            definitions.append(item)
        by_name = {item["name"].casefold(): item for item in definitions}
        if len(by_name) != len(definitions):
            raise ValueError("Supply distinct job names.")
        visited, visiting = set(), set()

        def visit(name):
            if name not in by_name or name in visiting:
                raise ValueError("Job dependencies must name jobs in this plan and cannot form a cycle.")
            if name in visited:
                return
            visiting.add(name)
            for dependency in by_name[name]["dependencies"]:
                visit(dependency.casefold())
            visiting.remove(name)
            visited.add(name)

        for name in by_name:
            visit(name)
        order = record(db, "work_orders", scope["work_order"])
        if order["status"] in {"done", "cancelled"}:
            raise ValueError("This work order is finished.")
        payload, brief = canonical(definitions), canonical(mission(db, order["id"]))
        previous = db.execute("SELECT * FROM work_plans WHERE work_order=? AND revision=?",
                              (order["id"], order["plan_revision"])).fetchone()
        if previous and previous["adopted_by"] != self.member:
            raise ValueError("This work-order plan belongs to another command team.")
        if previous and previous["result_update"] == int(plan.split(":")[1]) and previous["jobs"] == payload and previous["mission"] == brief:
            return self._receipt(db, order["id"])
        _, result = self.control._reference(db, scope, plan)
        author = binding(db, result["actor"])
        accepted_source = previous and previous["result_update"] == result["id"] and previous["mission"] == brief
        if not accepted_source and (result["kind"] != "result" or not result["artifact"] or not author or adapter(author["role"]) != "planner"
                or author["job"] is not None or author["reports_to"] != self.member
                or (current_update(db, author, kind="result") or {}).get("id") != result["id"]
                or json.loads(result["mission"]) != json.loads(brief)):
            raise ValueError("Use the XO planner's current retained result for this work-order context.")
        existing = {row["title"].casefold(): dict(row) for row in db.execute("SELECT * FROM jobs WHERE work_order=?", (order["id"],))}
        if any(name in existing and not self.commands._owns_job(db, scope, existing[name]) for name in by_name):
            raise ValueError("A proposed job belongs to another command team.")
        omitted = [row["title"] for name, row in existing.items() if name not in by_name and row["status"] not in {"done", "cancelled"}
                   and self.commands._owns_job(db, scope, row)]
        if omitted:
            raise ValueError("Explicitly cancel omitted unfinished jobs before adopting this plan: " + ", ".join(omitted))
        revision = order["plan_revision"] + 1
        identities = {}
        for name, item in by_name.items():
            old = existing.get(name)
            fields = {key: item[key] for key in ("objective", "done_when", "constraints")}
            details = {key: item[key] for key in ("editable_paths", "context_paths") if key in item}
            details["plan"] = {"reference": plan, "evidence": "artifact:" + str(result["artifact"]),
                               "author": result["actor"], "revision": revision}
            if old and old["status"] in {"done", "cancelled"}:
                if any(old[key] != value for key, value in fields.items()):
                    raise ValueError("Finished jobs retain their scope; create a new job for additional work.")
                identities[name] = old["id"]
                continue
            if old:
                ident = old["id"]
                self.work.update_in(db, "jobs", order["id"], ident, **fields)
            else:
                ident = self.work.create_in(db, "jobs", title=item["name"], **fields, work_order=order["id"], select=False)["job"]["id"]
            identities[name] = ident
            db.execute("UPDATE jobs SET assignment=? WHERE id=?", (canonical(details), ident))
            self.work.attach(db, order["id"], ident, "artifact", str(result["artifact"]), label="Adopted implementation plan")
            # Existing sessions keep their history. A changed plan invalidates old
            # operation bindings and pauses execution until the coordinator refreshes it.
            for member in list(db.execute("SELECT * FROM member_bindings WHERE job=?", (ident,))):
                if adapter(member["role"]) != "nco" and not member["assignment_id"]:
                    assigned = db.execute("INSERT INTO role_assignments(work_order,job,name,role,brief,details,created_by,created,updated) VALUES (?,?,?,?,?,?,?,?,?)",
                                          (order["id"], ident, item["name"], member["role"], canonical(fields), canonical(details),
                                           self.member, stamp(), stamp())).lastrowid
                    db.execute("UPDATE member_bindings SET assignment_id=? WHERE member=?", (assigned, member["member"]))
                db.execute("UPDATE member_bindings SET paused=1,launch_pending=0 WHERE member=?", (member["member"],))
                self.commands._touch(db, member["member"])
                self.commands._direction(db, binding(db, member["member"]), "XO revised the plan. Refresh the assignment and resume within the new job boundaries.")
        for name, item in by_name.items():
            if existing.get(name, {}).get("status") not in {"done", "cancelled"}:
                dependencies = [identities[d.casefold()] for d in item["dependencies"]]
                db.execute("UPDATE jobs SET dependencies=? WHERE id=?", (canonical(dependencies), identities[name]))
        db.execute("INSERT INTO work_plans(work_order,revision,result_update,jobs,mission,adopted_by,created) VALUES (?,?,?,?,?,?,?)",
                   (order["id"], revision, result["id"], payload, brief, self.member, stamp()))
        db.execute("UPDATE work_orders SET plan_revision=? WHERE id=?", (revision, order["id"]))
        if len(definitions) == 1:
            ident = next(iter(identities.values()))
            for nco in list(db.execute("SELECT * FROM member_bindings WHERE work_order=? AND role IN (SELECT value FROM json_each(?)) AND job=?", (order["id"], canonical(names_for_adapter("nco")), ident))):
                for child in list(db.execute("SELECT member FROM member_bindings WHERE reports_to=?", (nco["member"],))):
                    db.execute("UPDATE member_bindings SET reports_to=? WHERE member=?", (self.member, child["member"]))
                    self.commands._touch(db, child["member"])
                Delivery.release(db, nco["member"])
        if not accepted_source:
            self._complete(db, author, result)
        self.store._event(db, self.member, "plan_adopted", {"work_order": order["id"], "revision": revision, "jobs": list(identities.values()), "plan": plan})
        return self._receipt(db, order["id"])

    @staticmethod
    def _receipt(db, order):
        plan = current_plan(db, order)
        definitions = json.loads(plan["jobs"])
        return {"revision": plan["revision"], "plan": "update:" + str(plan["result_update"]),
                "coordination": "xo" if len(definitions) == 1 else "nco_per_job",
                "jobs": [{"name": item["name"], "dependencies": item["dependencies"]} for item in definitions]}

    def assign(self, work):
        self.commands._capability("orchestrate")
        config = self.commands._config()
        names = [(item.get("job", "").casefold(), item["name"].strip().casefold(), item["role"]) for item in work]
        if not work or any(not name for _, name, _ in names) or len(set(names)) != len(names):
            raise ValueError("Supply distinct, nonempty assignment names and roles within each job.")
        if any(adapter(item["role"]) == "arbitrator" for item in work):
            raise ValueError("Arbitrator assignments are created by the service for unresolved decisions.")
        from .assignment_decisions import AssignmentDecisions

        decisions = AssignmentDecisions(self)
        selection = decisions.prepare(work, config)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            scope = self.commands._scope(db, {"xo", "nco"})
            if scope["paused"]:
                raise ValueError("Your work is paused. Request guidance before expanding it.")
            decisions.verify(db, scope, selection)
            return {"work": [self._assign(db, scope, config, row["item"], selected=row["runtime"],
                                         decision=row["marker"]) for row in selection["rows"]]}

    def _assign(self, db, scope, config, item, *, selected=None, decision=None):
        role = item["role"]
        job = None
        if adapter(role) == "planner":
            if adapter(scope["role"]) != "xo" or item.get("job"):
                raise ValueError("Only XO commissions the work-order planner; NCO requests planning through XO.")
            if any(key in item for key in ("plan", "editable_paths")):
                raise ValueError("The planner inspects and reports; it has no editable assignment or implementation plan.")
            if db.execute("SELECT 1 FROM member_bindings WHERE work_order=? AND role IN (SELECT value FROM json_each(?)) AND "
                          "coalesce(assignment_id,0) NOT IN (SELECT id FROM role_assignments WHERE name=? COLLATE NOCASE AND role=? AND job IS NULL)",
                          (scope["work_order"], canonical(names_for_adapter("planner")), item["name"].strip(), role)).fetchone():
                raise ValueError("Revise or complete the existing work-order planning assignment first.")
        else:
            order = record(db, "work_orders", scope["work_order"])
            plan = current_plan(db, scope["work_order"]) if order["plan_revision"] else None
            try:
                job = self.commands._named_job(db, scope, item.get("job", item["name"]))
            except ValueError:
                if not plan:
                    raise ValueError("XO must commission a planner and use plan_adopt before assigning execution jobs.") from None
                raise
            if job["status"] in {"done", "cancelled"}:
                raise ValueError("That job is finished; adopt a new job for additional work.")
            multi = len(json.loads(plan["jobs"])) > 1 if plan else adapter(scope["role"]) == "nco"
            if adapter(role) == "nco":
                if adapter(scope["role"]) != "xo":
                    raise ValueError("XO appoints NCOs; NCO cannot appoint another coordinator.")
                if not multi:
                    raise ValueError("XO coordinates the single job directly.")
                return self._appoint(db, scope, config, job, role, selected=selected, decision=decision)
            if multi and adapter(scope["role"]) != "nco":
                raise ValueError("Each job in a multi-job plan is coordinated by its NCO.")
            if not multi and adapter(scope["role"]) != "xo":
                raise ValueError("XO coordinates the single job directly.")
            if adapter(scope["role"]) == "nco" and scope["job"] != job["id"]:
                raise ValueError("NCO can assign specialists only within its own job.")
            self.dependencies(db, job)
        prior_assignment = db.execute("SELECT * FROM role_assignments WHERE work_order=? AND job IS ? AND name=? COLLATE NOCASE AND role=?",
                                      (scope["work_order"], job["id"] if job else None, item["name"].strip(), role)).fetchone()
        if prior_assignment and prior_assignment["status"] != "active":
            raise ValueError("This role assignment is complete; use a new assignment name for additional work.")
        prior = db.execute("SELECT * FROM member_bindings WHERE assignment_id=?", (prior_assignment["id"],)).fetchone() if prior_assignment else None
        if not prior_assignment and job:
            prior = db.execute("SELECT * FROM member_bindings WHERE job=? AND role=? AND assignment_id IS NULL AND reports_to=?",
                               (job["id"], role, self.member)).fetchone()
        prior = dict(prior) if prior else None
        target_scope = {**scope, "job": job["id"] if job else None}
        writer_available(db, target_scope, role, prior["member"] if prior else None)
        fields = json.loads(prior_assignment["brief"]) if prior_assignment else {}
        for key in ("objective", "done_when", "constraints"):
            fields[key] = item.get(key, fields.get(key, job[key] if job else None))
        if any(value is None for value in fields.values()):
            raise ValueError("Supply the planning assignment's objective, done_when and constraints.")
        details = json.loads(prior_assignment["details"]) if prior_assignment else {}
        if job:
            job_details = json.loads(job["assignment"])
            adopted = job_details.get("plan", {}).get("reference")
            if adopted and item.get("plan", adopted) != adopted:
                raise ValueError("Use this job's currently adopted plan.")
            proposed = {**item, **({"plan": adopted} if adopted else {})}
            if role in EDITORS and "editable_paths" not in proposed and ("editable_paths" in details or "editable_paths" in job_details):
                allowed = Workspaces(self.store, self.member)
                proposed["editable_paths"] = [p for p in details.get("editable_paths", job_details.get("editable_paths", []))
                                                if allowed.permitted_path(p, role, assignment={})]
            if "context_paths" not in proposed and "context_paths" in job_details:
                proposed["context_paths"] = job_details["context_paths"]
            if "editable_paths" in job_details and any(not any(covers(parent, path) for parent in job_details["editable_paths"])
                                                       for path in proposed.get("editable_paths", [])):
                raise ValueError("Role assignment paths must stay within the job's adopted scope.")
            details = json.loads(prepare(db, self.control, target_scope, proposed, details))
        elif "context_paths" in item:
            if any(not valid_path(p) for p in item["context_paths"]):
                raise ValueError("Context paths must be relative project paths.")
            details["context_paths"] = item["context_paths"]
        if decision is not None:
            details["selection_decision"] = decision
        brief, details_text = canonical(fields), canonical(details)
        changed = prior_assignment and (prior_assignment["brief"] != brief or prior_assignment["details"] != details_text)
        if prior_assignment:
            ident = prior_assignment["id"]
            if changed:
                db.execute("UPDATE role_assignments SET brief=?,details=?,revision=revision+1,result_update=NULL,updated=? WHERE id=?",
                           (brief, details_text, stamp(), ident))
        else:
            ident = db.execute("INSERT INTO role_assignments(work_order,job,name,role,brief,details,created_by,created,updated) VALUES (?,?,?,?,?,?,?,?,?)",
                               (scope["work_order"], job["id"] if job else None, item["name"].strip(), role, brief, details_text, self.member, stamp(), stamp())).lastrowid
        slot = self.commands._slot(db, config, existing=prior, role=role, selected=selected)
        target = self.control.bind_in(db, slot["name"], role, scope["work_order"], job["id"] if job else None, self.member,
                                     prior["brief"] if prior else "")
        db.execute("UPDATE member_bindings SET assignment_id=? WHERE member=?", (ident, target["member"]))
        if changed:
            self.commands._touch(db, target["member"])
        if adapter(scope["role"]) == "nco" and (changed or not prior_assignment):
            self.commands._touch(db, scope["member"])
        target = binding(db, target["member"])
        if selected is not None and config.get("runtime"):
            from .runtime import retain

            retain(self.store, db, target, selected)
            # A newly selected profile is applied by the normal stop/start path;
            # a live native conversation must never claim another model's work.
            if prior:
                db.execute("UPDATE members SET desired='stopped' WHERE name=? AND pid IS NOT NULL", (target["member"],))
        self.commands._start_intent(db, target, slot)
        direction = self.commands._direction(db, target, "Carry out your role assignment within the supplied context and job boundaries.")
        return {**self.commands._participant(db, target, config), "assignment": item["name"].strip(), "direction": direction}

    def _appoint(self, db, scope, config, job, role, *, selected=None, decision=None):
        prior = db.execute("SELECT * FROM member_bindings WHERE work_order=? AND role IN (SELECT value FROM json_each(?)) AND job=?",
                           (scope["work_order"], canonical(names_for_adapter("nco")), job["id"])).fetchone()
        prior = dict(prior) if prior else None
        slot = self.commands._slot(db, config, existing=prior, role=role, selected=selected)
        target = self.control.bind_in(db, slot["name"], role, scope["work_order"], job["id"], self.member,
                                     prior["brief"] if prior else "")
        if decision is not None:
            from .commands import coordination_brief

            brief = canonical({**coordination_brief(target), "selection_decision": decision})
            if brief != target["brief"]:
                db.execute("UPDATE member_bindings SET brief=? WHERE member=?", (brief, target["member"]))
                self.commands._touch(db, target["member"])
                target = binding(db, target["member"])
        # Transfer an existing single-job team without changing assignments or evidence.
        for child in list(db.execute("SELECT * FROM member_bindings WHERE job=? AND role NOT IN (SELECT value FROM json_each(?))", (job["id"], canonical(names_for_adapter("nco"))))):
            if child["reports_to"] != target["member"]:
                db.execute("UPDATE member_bindings SET reports_to=? WHERE member=?", (target["member"], child["member"]))
                self.commands._touch(db, child["member"])
                self.commands._direction(db, binding(db, child["member"]), "Your job is now coordinated by NCO. Refresh context; retained work and evidence remain available.")
        if selected is not None and config.get("runtime"):
            from .runtime import retain

            retain(self.store, db, target, selected)
            if prior:
                db.execute("UPDATE members SET desired='stopped' WHERE name=? AND pid IS NOT NULL", (target["member"],))
        self.commands._start_intent(db, target, slot)
        sent = self.commands._direction(db, target, "Coordinate this job's specialist assignments. Request planning changes through XO; report the checked job ready for XO acceptance.")
        return {**self.commands._participant(db, target, config), "direction": sent}

    @staticmethod
    def dependencies(db, job):
        for ident in json.loads(job["dependencies"]):
            dependency = record(db, "jobs", ident)
            if dependency["status"] != "done":
                raise ValueError("This job awaits accepted dependency: " + dependency["title"])

    @staticmethod
    def _complete(db, target, result, cancel=False):
        if target.get("assignment_id"):
            db.execute("UPDATE role_assignments SET status=?,result_update=?,updated=? WHERE id=?",
                       ("cancelled" if cancel else "done", result["id"] if result else None, stamp(), target["assignment_id"]))
        Delivery.release(db, target["member"])

    def complete(self, work, assignment, role, assessment, cancel=False):
        self.commands._capability("steer")
        scope = self.control.current()
        spaces = Workspaces(self.store, self.member)
        with spaces.lock("order-" + str(scope["work_order"]), timeout=1), ExitStack() as locks:
            with self.store.connect() as db:
                ids = [r[0] for r in db.execute("SELECT DISTINCT job FROM role_assignments WHERE work_order=? AND name=? COLLATE NOCASE "
                                               "AND job IS NOT NULL ORDER BY job", (scope["work_order"], assignment))]
            for ident in ids:
                locks.enter_context(spaces.lock("job-" + str(ident)))
            with self.store.connect() as db:
                scope = self.commands._scope(db, {"xo", "nco"})
                if scope["paused"]:
                    raise ValueError("Work is paused.")
                targets = [r for r in self.commands._descendants(db, scope) if r.get("assignment_id")]
                targets = [r for r in targets if (not role or r["role"] == role)
                           and (not work or (record(db, "jobs", r["job"])["title"] if r["job"] else
                                            record(db, "role_assignments", r["assignment_id"])["name"]).casefold() == work.casefold())
                           and record(db, "role_assignments", r["assignment_id"])["name"].casefold() == assignment.casefold()]
                if len(targets) != 1:
                    # A repeated completion can return the retained outcome after its session is released.
                    rows = [dict(r) for r in db.execute("SELECT * FROM role_assignments WHERE work_order=? AND name=? COLLATE NOCASE AND status!='active'",
                                                       (scope["work_order"], assignment))
                            if (not role or r["role"] == role) and (adapter(scope["role"]) == "xo" or r["job"] == scope["job"])
                            and (not work or (record(db, "jobs", r["job"])["title"] if r["job"] else r["name"]).casefold() == work.casefold())]
                    if len(rows) == 1:
                        return {"assignment": rows[0]["name"], "outcome": rows[0]["status"], "retained": True}
                    raise ValueError("Name one role assignment in your team.")
                target = targets[0]
                if adapter(target["role"]) == "arbitrator":
                    raise ValueError("Arbitrator completes its service-owned decision through decision_resolve.")
                if adapter(scope["role"]) == "xo" and target["job"] and target["reports_to"] != self.member:
                    raise ValueError("The job's NCO completes its specialist assignments; XO accepts the job.")
                result = current_update(db, target, kind="result")
                if not cancel and not result:
                    raise ValueError("A role assignment needs a current retained result before completion.")
                if db.execute("SELECT 1 FROM operations WHERE member=? AND state IN ('queued','running')", (target["member"],)).fetchone():
                    raise ValueError("The assignment still has an active operation.")
                revision = None
                if target["role"] in EDITORS and not cancel:
                    workspace = db.execute("SELECT * FROM job_workspaces WHERE job=?", (target["job"],)).fetchone()
                    if workspace and (spaces.changes(workspace["root"]) or git(workspace["root"], "diff", "--cached", "--quiet", check=False).returncode):
                        raise ValueError("Save the editor's remaining changes before completing its role assignment.")
                    if workspace:
                        revision = git(workspace["root"], "rev-parse", "HEAD").stdout.decode().strip()
                assigned = dict(record(db, "role_assignments", target["assignment_id"]))
            gate = None
            if not cancel:
                from .completion import Completion

                gate = Completion(self.store, self.member).require("assignment", job=target["job"], assignment=assigned, revision=revision)
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                if self.commands._scope(db, {"xo", "nco"}) != scope or binding(db, target["member"]) != target:
                    raise ValueError("The assignment changed during completion; inspect its current result.")
                latest = current_update(db, target, kind="result")
                if not cancel and (not latest or latest["id"] != result["id"]):
                    raise ValueError("The result changed during completion; inspect its current proof.")
                if dict(record(db, "role_assignments", assigned["id"])) != assigned:
                    raise ValueError("The assignment requirements changed during completion.")
                if db.execute("SELECT 1 FROM operations WHERE member=? AND state IN ('queued','running')", (target["member"],)).fetchone():
                    raise ValueError("The assignment still has an active operation.")
                if gate:
                    Completion(self.store, self.member).verify(gate)
                if revision and (spaces.changes(workspace["root"]) or git(workspace["root"], "rev-parse", "HEAD").stdout.decode().strip() != revision):
                    raise ValueError("The source changed during completion; retain current proof before completing it.")
                self._complete(db, target, result, cancel)
                if adapter(scope["role"]) == "nco":
                    self.commands._touch(db, scope["member"])
                self.store._event(db, self.member, "assignment_completed", {"work_order": scope["work_order"], "job": target["job"],
                                 "assignment": target["assignment_id"], "assessment": assessment, "cancelled": cancel})
                return {"assignment": assignment, "outcome": "cancelled" if cancel else "done", "retained": True}
