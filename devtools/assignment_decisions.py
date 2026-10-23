"""Service-owned assignment selection before atomic planning mutations."""

from __future__ import annotations

import json

from .advice import Advice
from .configuration import resolve as configuration
from .control import binding
from .failures import Failure
from .role_packages import adapter, assignable_roles, catalog
from .store import digest
from .work import record


def select_bound_runtime(store, scope):
    """Select the bound XO's launch profile before its first native session."""
    from .decisions import Decisions, request
    from .runtime import load, resolve, retain, selected

    selector = Decisions(store, scope["member"])
    with store.connect() as db:
        context = selector.context(db, scope, scope["job"])
        retained = db.execute("SELECT 1 FROM runtime_selections WHERE member=?", (scope["member"],)).fetchone()
    if retained:
        try:
            return selected(store, scope, required=True)
        except ValueError:
            # A changed mission or operator policy requires a fresh service
            # choice; it does not authorize reuse of the previous profile.
            pass
    policy = configuration(store, role=scope["role"], job=scope["job"])
    profiles, _, revision = load(store.directory)
    candidates = {}
    for name in profiles["roles"].get(scope["role"], []):
        try:
            candidates[name] = resolve(store.directory, scope["role"], profile=name)
        except ValueError:
            continue
    if not candidates:
        return resolve(store.directory, scope["role"])  # Preserve the existing actionable unavailable diagnostic.
    if len(candidates) > policy["assignment"]["max_profiles"]:
        raise Failure("Configured command runtime profiles exceed the selection candidate budget.",
                      category="invalid_request", reason="candidate_budget_exceeded")
    receipt = None
    choice = next(iter(candidates))
    identity = {"runtime_revision": revision, "work": context}
    if policy["features"]["selection"] and len(candidates) > 1:
        names = {f"p{i}": name for i, name in enumerate(candidates)}
        evidence = policy["assignment"]["profile_evidence"]
        options = {key: {"name": name, **{field: candidates[name].get(field) for field in ("harness", "model", "reasoning", "provider")},
                         "evidence": evidence.get(name, policy["assignment"]["missing_profile_evidence"])}
                   for key, name in names.items()}
        question = {**policy["assignment"]["questions"]["runtime"],
                    "criteria": {**options, "unknown": policy["assignment"]["unknown_profile"]}}
        with request(scope["member"], "runtime_start", {}):
            receipt = selector.select("runtime", {"brief": context, "role": scope["role"], "profiles": options},
                                      {"runtime": question}, policy=policy, identity=identity)
        choice = names.get(receipt["decisions"]["runtime"])
        if choice is None:
            raise Failure("The bound command role needs a resolved configured runtime.", category="not_ready",
                          reason="assignment_runtime_unresolved", details={"decision": receipt["id"]})
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        current = binding(db, scope["member"])
        if (not current or selector.context(db, current, current["job"]) != context or
                load(store.directory)[2] != revision or
                configuration(store, role=current["role"], job=current["job"])["revision"] != policy["revision"]):
            raise Failure("Work or runtime policy changed during command runtime selection.", category="not_ready",
                          reason="assignment_selection_stale")
        if receipt:
            selector.verify(receipt, identity=identity, db=db)
        return retain(store, db, current, candidates[choice])


def steer_assignment(commands, action, work, direction, role, assignment, fields):
    """A semantic brief revision uses the same selection as a new assignment."""
    from .api import API

    with commands.store.connect() as db:
        scope = commands._scope(db, {"xo", "nco"})
        targets = commands._descendants(db, scope)
        targets = [target for target in targets if (not role or target["role"] == role)
                   and (not assignment or target.get("assignment_id") and
                        record(db, "role_assignments", target["assignment_id"])["name"].casefold() == assignment.casefold())
                   and (record(db, "jobs", target["job"])["title"] if target["job"] else
                        record(db, "role_assignments", target["assignment_id"])["name"] if target.get("assignment_id") else "").casefold() == work.strip().casefold()]
        if not targets or any(adapter(target["role"]) == "nco" for target in targets):
            return None  # Existing adopted-plan/delegation checks handle job-scope changes.
        if any(adapter(target["role"]) == "arbitrator" for target in targets):
            raise ValueError("Arbitration assignments are managed by the decision service")
        if not any(configuration(commands.store, role=scope["role"], job=target["job"])["features"]["selection"] for target in targets):
            return None
        owners = {target["reports_to"] for target in targets}
        if len(owners) != 1:
            raise ValueError("Revise responsibilities through their current job coordinator.")
        owner = next(iter(owners))
        requests = []
        for target in targets:
            assigned = record(db, "role_assignments", target["assignment_id"]) if target.get("assignment_id") else None
            job = record(db, "jobs", target["job"]) if target["job"] else None
            requests.append({"name": assigned["name"] if assigned else job["title"], "role": target["role"],
                             **({"job": job["title"]} if job else {}), **fields})
    arguments = {"action": action, "work": work, **({"direction": direction} if direction is not None else {}),
                 **({"role": role} if role else {}), **({"assignment": assignment} if assignment else {})}
    if owner != commands.member:
        # XO's superior steering is executed through the bound NCO's ordinary
        # assignment path, so pending arbitration retains the complete steering
        # operation and its reporting scope.
        return API(commands.store.directory, owner).call("steer", {**arguments, **fields})
    result = commands.orchestrate(requests)
    if action == "guide" and direction is None:
        return result
    # NCO assignment updates advance its binding revision. The second operation
    # takes a fresh service binding while retaining the caller's requested action.
    return API(commands.store.directory, commands.member).call("steer", arguments)


class AssignmentDecisions:
    def __init__(self, planning):
        self.planning = planning
        self.commands = planning.commands
        self.store, self.member = planning.store, planning.member

    def _context(self, db, scope, config):
        """Only the coordinator's jobs; arbitration cannot invalidate itself."""
        jobs = [dict(row) for row in db.execute("SELECT * FROM jobs WHERE work_order=? ORDER BY id", (scope["work_order"],))
                if self.commands._owns_job(db, scope, dict(row))]
        ids = {row["id"] for row in jobs}
        assignments = [dict(row) for row in db.execute(
            "SELECT * FROM role_assignments WHERE work_order=? AND status='active' ORDER BY id", (scope["work_order"],))
            if adapter(row["role"]) != "arbitrator" and
            (row["job"] in ids or row["job"] is None and row["created_by"] == self.member)]
        bindings = [dict(row) for row in db.execute("SELECT * FROM member_bindings WHERE work_order=? ORDER BY member",
                                                   (scope["work_order"],))
                    if adapter(row["role"]) != "arbitrator" and
                    (row["member"] == self.member or row["job"] in ids or row["reports_to"] == self.member)]
        runtime = None
        if config.get("runtime"):
            from .runtime import load

            runtime = load(self.store.directory)[2]
        return {"binding": scope, "brief": self.commands._brief(db, scope),
                "work_order": dict(record(db, "work_orders", scope["work_order"])),
                "jobs": jobs, "assignments": assignments, "bindings": bindings,
                "team": config, "runtime_revision": runtime}

    def _job(self, db, scope, item):
        return None if adapter(item["role"]) == "planner" else self.commands._named_job(
            db, scope, item.get("job", item["name"]))

    def _preview(self, db, scope, config, work, selections=None):
        """Exercise the real structural checks without retaining seats or messages."""
        db.execute("SAVEPOINT assignment_selection_preview")
        try:
            rows = []
            for index, item in enumerate(work):
                self.planning._assign(db, scope, config, item, selected=selections[index] if selections else None)
                job = self._job(db, scope, item)
                assigned = db.execute(
                    "SELECT * FROM role_assignments WHERE work_order=? AND job IS ? AND name=? COLLATE NOCASE AND role=?",
                    (scope["work_order"], job["id"] if job else None, item["name"].strip(), item["role"]),
                ).fetchone()
                details = json.loads(assigned["details"]) if assigned else {}
                details.pop("selection_decision", None)
                fields = json.loads(assigned["brief"]) if assigned else {
                    key: job[key] for key in ("objective", "done_when", "constraints")}
                rows.append({"item": {**item, **fields}, "job": dict(job) if job else None, "details": details})
            return rows
        finally:
            db.execute("ROLLBACK TO assignment_selection_preview")
            db.execute("RELEASE assignment_selection_preview")

    def _prior(self, db, scope, request):
        job = self._job(db, scope, request)
        if adapter(request["role"]) == "nco":
            from .commands import coordination_brief

            existing = self._existing(db, scope, request, job)
            marker = coordination_brief(existing).get("selection_decision") if existing else None
            return request, marker
        for row in db.execute("SELECT * FROM role_assignments WHERE work_order=? AND job IS ? AND name=? COLLATE NOCASE AND status='active'",
                              (scope["work_order"], job["id"] if job else None, request["name"].strip())):
            marker = json.loads(row["details"]).get("selection_decision")
            if marker and all(marker.get("request", {}).get(key) == request.get(key) for key in ("name", "job", "role")):
                return {**request, "role": row["role"]}, marker
        return request, None

    def _existing(self, db, scope, item, job):
        if adapter(item["role"]) == "nco":
            row = db.execute("SELECT * FROM member_bindings WHERE job=? AND role=? AND reports_to=?",
                             (job["id"], item["role"], self.member)).fetchone()
        else:
            row = db.execute("SELECT b.* FROM role_assignments a JOIN member_bindings b ON b.assignment_id=a.id "
                             "WHERE a.work_order=? AND a.job IS ? AND a.name=? COLLATE NOCASE AND a.role=?",
                             (scope["work_order"], job["id"] if job else None, item["name"].strip(), item["role"])).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _marker_context(context, descriptor, policy):
        order = context["work_order"]
        mission = {key: order[key] for key in ("id", "objective", "done_when", "constraints", "context", "plan_revision")}
        job = descriptor["job"]
        work = ({key: job[key] for key in ("id", "objective", "done_when", "constraints", "assignment", "dependencies")}
                if job else None)
        return digest([mission, descriptor["item"], descriptor["details"], work,
                            policy["revision"], context["runtime_revision"]])

    def prepare(self, work, config):
        from .decisions import Decisions

        prepared = []
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            scope = self.commands._scope(db, {"xo", "nco"})
            if scope["paused"]:
                raise ValueError("Your work is paused. Request guidance before expanding it.")
            context = self._context(db, scope, config)
            prior = [self._prior(db, scope, item) for item in work]
            descriptors = self._preview(db, scope, config, [item for item, _ in prior])
            for request, (item, marker), descriptor in zip(work, prior, descriptors):
                job = descriptor["job"]
                existing = self._existing(db, scope, item, job)
                policy = configuration(self.store, role=scope["role"], job=job["id"] if job else None)
                reuse = marker and marker.get("context") == self._marker_context(context, descriptor, policy)
                enabled = policy["features"].get("selection", False)
                candidates = {}
                if enabled and not reuse:
                    if len(work) > policy["assignment"]["max_work"] or len(context["jobs"]) > policy["assignment"]["max_jobs"]:
                        raise Failure("Assignment selection exceeds the configured work/context budget.",
                                      category="invalid_request", reason="assignment_budget_exceeded")
                    eligible = assignable_roles(scope["role"])
                    allowed = policy["assignment"]["roles"] or eligible
                    structural = adapter(item["role"])
                    for role in allowed:
                        behavior = adapter(role)
                        if existing and role != item["role"]:
                            continue  # A revised responsibility keeps its assigned role identity.
                        if role != item["role"] and db.execute(
                                "SELECT 1 FROM role_assignments WHERE work_order=? AND job IS ? AND name=? COLLATE NOCASE AND role=?",
                                (scope["work_order"], job["id"] if job else None, item["name"].strip(), role)).fetchone():
                            continue  # Selection cannot silently revise another role's responsibility.
                        if role not in eligible:
                            continue
                        if structural in {"planner", "nco"}:
                            if behavior != structural:
                                continue
                        elif behavior in {"planner", "nco"}:
                            continue
                        try:
                            self._preview(db, scope, config, [{**item, "role": role}])
                        except ValueError:
                            continue
                        candidates[role] = catalog()["roles"][role]["responsibility"]
                    if not candidates:
                        raise Failure("No configured role can perform this assignment within its current boundaries.",
                                      category="not_ready", reason="assignment_role_unavailable")
                    if len(candidates) > 32:
                        raise Failure("Configured assignment roles exceed the selection candidate budget.",
                                      category="invalid_request", reason="candidate_budget_exceeded")
                prepared.append({"request": request, "item": item, "descriptor": descriptor, "policy": policy,
                                 "marker": marker if reuse else None, "enabled": enabled and not reuse,
                                 "candidates": candidates, "runtime": None, "receipts": [], "existing": existing})

        identity = digest([context, work])
        selector = Decisions(self.store, self.member)
        reserved = set()
        reserved_roles = {}
        for row in prepared:
            if not row["enabled"]:
                key = (row["descriptor"]["job"]["id"] if row["descriptor"]["job"] else None,
                       row["item"]["name"].strip().casefold())
                reserved_roles.setdefault(key, set()).add(row["item"]["role"])
        for row in prepared:
            if not row["enabled"]:
                continue
            policy, descriptor = row["policy"], row["descriptor"]
            job = descriptor["job"]
            target_job = job["id"] if job else None
            assignment_key = (target_job, row["item"]["name"].strip().casefold())
            row["candidates"] = {role: value for role, value in row["candidates"].items()
                                 if role not in reserved_roles.get(assignment_key, set())}
            if not row["candidates"]:
                raise Failure("Selected responsibilities would occupy the same role assignment; revise their names or scope.",
                              category="not_ready", reason="assignment_selection_collision")
            settings = policy["assignment"]
            others = [assigned for assigned in context["assignments"]
                      if not (assigned["job"] == target_job and assigned["name"].casefold() == row["item"]["name"].strip().casefold()
                              and assigned["role"] == row["item"]["role"])]
            state = {"brief": context["brief"], "work_order": context["work_order"], "job": job,
                     "proposal": descriptor["item"], "other_proposals": [item for item in work if item is not row["request"]],
                     "current_assignments": [{key: assigned[key] for key in ("id", "job", "name", "role", "brief", "revision")}
                                             for assigned in others], "role_candidates": row["candidates"]}
            questions = {"scope": policy["scope"]["questions"]["scope"],
                         "overlap": settings["questions"]["overlap"]}
            if len(row["candidates"]) > 1:
                questions["role"] = {**settings["questions"]["role"], "criteria": row["candidates"]}
            if others or len(work) > 1:
                questions["parallel"] = settings["questions"]["parallel"]
            receipt = selector.select("assignment", state, questions, policy=policy,
                                      target_job=target_job, identity=identity)
            row["receipts"].append(receipt)
            choices = receipt["decisions"]
            if choices["scope"] != "required":
                raise Failure("The proposed responsibility is outside the current required work; report it separately or obtain current direction.",
                              category="not_permitted", reason="assignment_scope_not_required",
                              details={"decision": receipt["id"], "selection": choices["scope"]})
            if choices["overlap"]:
                raise Failure("This responsibility duplicates existing or proposed work; revise the assignment before allocating another agent.",
                              category="not_ready", reason="assignment_overlap", details={"decision": receipt["id"]})
            if choices.get("parallel") is False:
                raise Failure("This responsibility must wait for the other work identified by the selection; retry after its prerequisite is ready.",
                              category="not_ready", reason="assignment_not_ready", details={"decision": receipt["id"]})
            chosen_role = choices.get("role", next(iter(row["candidates"])))
            if chosen_role not in row["candidates"]:
                raise Failure("Assignment selection returned a role outside the current candidates.",
                              category="evaluation_invalid", reason="assignment_role_invalid")
            row["item"] = {**row["item"], "role": chosen_role}
            reserved_roles.setdefault(assignment_key, set()).add(chosen_role)
            runtimes = Advice(self.store, self.member).runtimes([chosen_role], settings)[chosen_role]
            profiles = runtimes["profiles"]
            if row["existing"] and not config.get("runtime"):
                member = next(member for member in config["members"] if member["name"] == row["existing"]["member"])
                profiles = {member["name"]: {**{key: member.get(key) for key in ("harness", "model", "reasoning")},
                                             "evidence": settings["profile_evidence"].get(member["name"], settings["missing_profile_evidence"])}}
            if not config.get("runtime"):
                profiles = {name: value for name, value in profiles.items() if name not in reserved}
                from .role_packages import runtime_profile

                for name in list(profiles):
                    member = next(member for member in config["members"] if member["name"] == name)
                    if member["harness"] != "external":
                        try:
                            runtime_profile(chosen_role, member)
                        except ValueError:
                            del profiles[name]
            if not profiles:
                raise Failure("No available configured runtime can perform this responsibility.", category="not_ready",
                              reason="assignment_runtime_unavailable")
            names = {f"p{i}": name for i, name in enumerate(profiles)}
            if len(profiles) > 1:
                question = {**settings["questions"]["runtime"], "criteria": {
                    **{key: {"name": name, **profiles[name]} for key, name in names.items()},
                    "unknown": settings["unknown_profile"]}}
                receipt = selector.select("runtime", {**state, "proposal": {**descriptor["item"], "role": chosen_role},
                                                       "profiles": profiles, "policy_revision": runtimes.get("policy_revision")},
                                          {"runtime": question}, policy=policy, target_job=target_job, identity=identity)
                row["receipts"].append(receipt)
                name = names.get(receipt["decisions"]["runtime"])
                if name is None:
                    raise Failure("Runtime selection needs a resolved configured profile before assignment.",
                                  category="not_ready", reason="assignment_runtime_unresolved", details={"decision": receipt["id"]})
            else:
                name = next(iter(profiles))
            if config.get("runtime"):
                from .runtime import resolve

                row["runtime"] = resolve(self.store.directory, chosen_role, profile=name)
            else:
                row["runtime"] = next(member for member in config["members"] if member["name"] == name)
                reserved.add(name)

        # A selected role changes paths/capacity just like an explicit assignment.
        # Reuse the actual structural checks on the complete selected batch.
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            scope = self.commands._scope(db, {"xo", "nco"})
            self._fresh(db, scope, config, context)
            descriptors = self._preview(db, scope, config, [row["item"] for row in prepared],
                                        [row["runtime"] for row in prepared])
            for row, descriptor in zip(prepared, descriptors):
                if row["enabled"]:
                    row["marker"] = {"request": row["request"],
                                     "context": self._marker_context(context, descriptor, row["policy"]),
                                     "decisions": [receipt["id"] for receipt in row["receipts"]]}
        return {"rows": prepared, "context": context, "config": config, "identity": identity}

    def _fresh(self, db, scope, config, context):
        from .launch import team_config

        if team_config(self.store.directory) != config or self._context(db, scope, config) != context:
            raise Failure("Work or runtime policy changed while selecting this assignment; retry the current work.",
                          category="not_ready", reason="assignment_selection_stale")

    def verify(self, db, scope, selection):
        from .decisions import Decisions

        self._fresh(db, scope, selection["config"], selection["context"])
        selector = Decisions(self.store, self.member)
        for row in selection["rows"]:
            for receipt in row["receipts"]:
                selector.verify(receipt, identity=selection["identity"], db=db)
