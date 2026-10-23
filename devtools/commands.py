"""CO/XO outcomes over the existing work, binding, and live-session services."""

from __future__ import annotations

import json
from contextlib import ExitStack

from . import launch
from .control import BRIEF_FIELDS, Control, binding
from .role_packages import adapter, assignable_roles, catalog, default_role, names_for_adapter, require_capability
from .store import Store, canonical, digest, stamp
from .work import Work, record


def role_tools():
    return {name: set(package["mcp"]["command"]) for name, package in catalog()["roles"].items()
            if package["mcp"]["command"]}


def capabilities(spec, coordinator=None):
    from .planning import capabilities as planning_capabilities

    text = {"type": "string", "minLength": 1, "maxLength": 2000}
    name = {**text, "maxLength": 100}
    goal = {"objective": text, "done_when": text, "constraints": {**text, "minLength": 0}}
    return [
        spec(
            "direct",
            "CO: relay the owner's objective, boundaries and prepared context to XO; sessions are managed automatically.",
            {"title": name, **goal, "context": {"type": "array", "maxItems": 20,
             "items": {"type": "string", "pattern": "^artifact:[1-9][0-9]*$", "maxLength": 80},
             "description": "Retained specification or research artifacts to share as work-order context."}},
            ("title", "objective", "done_when", "constraints"),
        ),
        spec(
            "orchestrate",
            "Assign named roles within adopted jobs. XO commissions the work-order planner and appoints one NCO per job in a multi-job plan. NCO assigns specialists only in its own job. Sessions are managed automatically.",
            {
                "work": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 4,
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": name,
                            "job": {**name, "description": "Job name from the adopted plan. Omit for the XO's planner."},
                            "role": {"type": "string", "enum": list(assignable_roles(coordinator))},
                            "plan": {"type": "string", "pattern": "^update:[1-9][0-9]*$", "maxLength": 80},
                            "editable_paths": {"type": "array", "maxItems": 30, "items": {**name, "maxLength": 300},
                                               "description": "Assigned files or directories within role paths. Missing paths are files; end a new directory path with '/'. List new files explicitly when their parent contains protected paths."},
                            "context_paths": {"type": "array", "maxItems": 30, "items": {**name, "maxLength": 300}},
                            **{key: {**value, "maxLength": 800} for key, value in goal.items()},
                        },
                        "required": ["name", "role"],
                        "additionalProperties": False,
                    },
                }
            },
            ("work",),
        ),
        spec(
            "monitor",
            "Read scoped progress, blockers, observed sessions, usage and capacity. CO relays overview status; XO/NCO inspect delivery reviews and diagnostics.",
            {
                "offset": {"type": "integer", "minimum": 0},
                "work": {**name, "description": "Optional named job to inspect its current review packet and CI."},
                "section": {
                    "type": "string",
                    "enum": ["overview", "review", "ci", "feedback"],
                    "description": "Review or diagnostics for named work, or the integrated result when work is omitted.",
                },
                "path": {**text, "description": "Changed file to inspect within the named job's PR."},
                "page": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Files, CI failures or feedback page for a named job.",
                },
            },
            readonly=True,
        ),
        spec(
            "steer",
            "XO/NCO: guide collaborators, revise role briefs, or complete/cancel a role assignment. Only XO accepts/cancels a job; NCO reports job readiness to XO.",
            {
                "action": {"type": "string", "enum": ["guide", "pause", "resume", "stop", "replace", "complete", "accept", "cancel"]},
                "work": {**name, "description": "An adopted job name, or the planner's assignment name; omit to address your team."},
                "assignment": {**name, "description": "A named role assignment within the job; complete releases its session and retains its result."},
                "role": {
                    "type": "string",
                    "enum": list(assignable_roles(coordinator)),
                    "description": "Optional collaborator role when a job has both a producer and auditor.",
                },
                "direction": text,
                **{key: {**value, "maxLength": 1000} for key, value in goal.items()},
            },
            ("action",),
        ),
        spec(
            "conclude",
            "XO: accept the inspected, checked integrated result, deliver its outcome to CO and release the execution team; retain all history.",
            {"assessment": text},
            ("assessment",),
        ),
    ] + planning_capabilities(spec)


def coordination_brief(scope):
    try:
        value = json.loads(scope["brief"])
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}


class Commands:
    def __init__(self, store, member, control):
        self.store, self.member, self.control = store, member, control
        self.work = Work(store, member)

    def appointed_co(self):
        path = self.store.directory / "team.json"
        return path.is_file() and launch.team_config(self.store.directory).get("co") == self.member

    def role(self, scope):
        if scope:
            return scope["role"]
        if self.appointed_co():
            return default_role("co")
        from .standby import role

        return role(self.store, self.member)

    def _capability(self, capability):
        require_capability(self.role(self.control.current()), capability)

    def _config(self):
        config = launch.team_config(self.store.directory)
        self.store.initialize(config["members"], runtime=config.get("runtime", False))
        return config

    def _scope(self, db, roles, *, bootstrap=False):
        current, expected = binding(db, self.member), self.control.current()
        identity = ("role", "work_order", "job", "reports_to", "revision")
        if (tuple(current[key] for key in identity) if current else None) != (
            tuple(expected[key] for key in identity) if expected else None
        ):
            raise ValueError("Your role or work changed during this call; read your current context and retry.")
        if current is None and bootstrap and self.appointed_co():
            return None
        if not current or adapter(current["role"]) not in roles or (current["job"] is not None and adapter(current["role"]) != "nco"):
            raise ValueError("This command requires the corresponding work-order command role.")
        return current

    @staticmethod
    def _descendants(db, scope):
        return [
            dict(row)
            for row in db.execute(
                "WITH RECURSIVE team(member) AS (SELECT member FROM member_bindings WHERE reports_to=? "
                "AND work_order=? UNION SELECT b.member FROM member_bindings b JOIN team t ON b.reports_to=t.member "
                "WHERE b.work_order=?) SELECT b.* FROM member_bindings b JOIN team t ON t.member=b.member ORDER BY b.member",
                (scope["member"], scope["work_order"], scope["work_order"]),
            )
        ]

    def _slot(self, db, config, existing=None, role=None, selected=None):
        if config.get("runtime"):
            from .runtime import allocate

            return allocate(self.store, db, config, role or existing["role"], existing, selected=selected)
        import shutil

        from .configuration import arbitration_reservations
        from .role_packages import runtime_profile

        reservations = (arbitration_reservations(self.store)
                        if not existing and (role is None or adapter(role) != "arbitrator") else {})

        def available(row, wanted=None):
            if (row["name"] in {config.get("co"), self.member} or binding(db, row["name"])
                    or db.execute("SELECT 1 FROM standby_bindings WHERE member=?", (row["name"],)).fetchone()):
                return False
            session = db.execute("SELECT state,pid FROM members WHERE name=?", (row["name"],)).fetchone()
            if not session or session["state"] not in {"stopped", "failed"} or session["pid"]:
                return False
            if row["harness"] != "external":
                if not shutil.which(row.get("binary", row["harness"])):
                    return False
                if wanted:
                    try:
                        runtime_profile(wanted, row)
                    except ValueError:
                        return False
            return True

        def preserves_reservation(candidate):
            for arbitrator, required in reservations.items():
                occupied = db.execute("SELECT count(*) FROM member_bindings WHERE role=?", (arbitrator,)).fetchone()[0]
                free = sum(row["name"] != candidate["name"] and available(row, arbitrator)
                           for row in config["members"])
                if free < max(0, required - occupied):
                    return False
            return True

        if selected is not None:
            if not isinstance(selected, dict):
                raise ValueError("A selected configured collaborator is required")
            row = next((r for r in config["members"] if r["name"] == selected.get("name")), None)
            if not row or any(selected.get(key) != value for key, value in row.items()):
                raise ValueError("Configured collaborator changed; select its current settings")
            if row["name"] in {config.get("co"), self.member}:
                raise ValueError("Select a configured subordinate collaborator")
            if existing:
                if row["name"] != existing["member"]:
                    raise ValueError("An existing assignment must retain its configured collaborator")
            else:
                session = db.execute("SELECT state,pid FROM members WHERE name=?", (row["name"],)).fetchone()
                if (binding(db, row["name"])
                        or db.execute("SELECT 1 FROM standby_bindings WHERE member=?", (row["name"],)).fetchone()
                        or session["state"] not in {"stopped", "failed"} or session["pid"]):
                    raise ValueError("Selected configured collaborator is no longer available")
            if row["harness"] != "external":
                if not shutil.which(row.get("binary", row["harness"])):
                    raise ValueError("Selected configured collaborator executable is unavailable")
            if not preserves_reservation(row):
                raise ValueError("No ordinary collaborator capacity remains; reserved eligible sessions are required for Arbitrator decisions.")
            return row
        if existing:
            row = next((r for r in config["members"] if r["name"] == existing["member"]), None)
            if not row:
                raise ValueError("This collaborator is no longer configured; request operator assistance.")
            return row
        for row in config["members"]:
            if available(row, role) and preserves_reservation(row):
                return row
        if reservations:
            raise ValueError("No ordinary collaborator capacity remains; reserved eligible sessions are required for Arbitrator decisions.")
        raise ValueError("No available configured collaborator matches this work. Monitor capacity or ask CO for help.")

    @staticmethod
    def _touch(db, member):
        revision = Store.next_binding_revision(db, member)
        db.execute("UPDATE member_bindings SET revision=?,updated=? WHERE member=?", (revision, stamp(), member))

    @staticmethod
    def _brief(db, scope):
        order = record(db, "work_orders", scope["work_order"])
        result = {
            "role": scope["role"],
            "work_state": "paused" if scope["paused"] else "active",
            "responsibility": scope["brief"],
            "work": {key: order[key] for key in BRIEF_FIELDS},
        }
        if scope["job"]:
            job = record(db, "jobs", scope["job"])
            result["job"] = {key: job[key] for key in BRIEF_FIELDS}
        return result

    def _direction(self, db, scope, text):
        body = canonical({"direction": text, "context_revision": scope["revision"]})
        # A reused slot can restart its binding revision at one for another job/order.
        key = "direction:" + digest([
            scope["member"], scope["work_order"], scope["job"], scope["role"], scope.get("assignment_id"), scope["revision"], body
        ])
        sender = self.member
        exists = db.execute("SELECT id FROM messages WHERE sender=? AND request_key=?", (sender, key)).fetchone()
        message = self.store._send(
            db, sender, scope["member"], body, key=key, work_order=scope["work_order"], job=scope["job"]
        )
        if not exists:
            self.store._event(
                db,
                self.member,
                "direction_issued",
                {"message": message["id"], "work_order": scope["work_order"], "job": scope["job"]},
            )
        db.execute("UPDATE messages SET category='direction' WHERE id=?", (message["id"],))
        # Retain earlier direction as history, but do not deliver stale queued briefs.
        db.execute(
            "UPDATE messages SET delivery='superseded' WHERE recipient=? AND work_order=? AND job IS ? "
            "AND category='direction' AND delivery='queued' AND id<?",
            (scope["member"], scope["work_order"], scope["job"], message["id"]),
        )
        return {"reference": f"message:{message['id']}", "delivery": message["delivery"]}

    @staticmethod
    def _start_intent(db, scope, slot):
        session = db.execute("SELECT * FROM members WHERE name=?", (scope["member"],)).fetchone()
        if (
            slot["harness"] != "external"
            and not scope["paused"]
            and (session["state"] in {"stopped", "failed"} or session["desired"] == "stopped")
        ):
            db.execute(
                "UPDATE member_bindings SET launch_pending=1,launch_error=NULL WHERE member=?", (scope["member"],)
            )

    def direct(self, title, objective, done_when, constraints, context=None):
        self._capability("direct")
        config = self._config()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            scope = self._scope(db, {"co"}, bootstrap=True)
            if scope and record(db, "work_orders", scope["work_order"])["status"] in {"done", "cancelled"}:
                # CO remains connected to relay the completed outcome. A new owner
                # objective starts a new order rather than rewriting its history.
                scope = None
            fields = dict(title=title, objective=objective, done_when=done_when, constraints=constraints)
            if context is not None:
                if not scope and context:
                    raise ValueError("Establish the work order, retain its documents with work_report, then attach their context references.")
                for reference in context:
                    self.control._reference(db, scope, reference)
                context = canonical(list(dict.fromkeys(context)))
            changed = False
            if scope:
                old = record(db, "work_orders", scope["work_order"])
                changed = any(old[key] != value for key, value in fields.items())
                changed = changed or context is not None and old["context"] != context
                self.work.update_in(db, "work_orders", scope["work_order"], None, **fields)
            else:
                order = self.work.create_in(db, "work_orders", **fields, select=False)["work_order"]["id"]
                scope = self.control.bind_in(db, self.member, default_role("co"), order)
            if context is not None:
                db.execute("UPDATE work_orders SET context=? WHERE id=?", (context, scope["work_order"]))
            team = self._descendants(db, scope)
            candidates = [row for row in team if adapter(row["role"]) == "xo" and row["reports_to"] == self.member]
            if len(candidates) > 1:
                raise ValueError("More than one XO is bound to CO; resolve the trusted setup before directing work.")
            from .standby import consume_in

            existing = candidates[0] if candidates else consume_in(self.store, db, self.member)
            xo_role = existing["role"] if existing else default_role("xo")
            slot = self._slot(db, config, existing=existing, role=xo_role)
            xo = self.control.bind_in(
                db,
                slot["name"],
                xo_role,
                scope["work_order"],
                reports_to=self.member,
                brief=candidates[0]["brief"] if candidates else "",
            )
            if changed:
                for row in [scope, *self._descendants(db, scope)]:
                    self._touch(db, row["member"])
            xo = binding(db, xo["member"])
            self._start_intent(db, xo, slot)
            sent = self._direction(db, xo, "Carry out the current objective within its boundaries.")
            return {"work": title, "xo": self._participant(db, xo, config), "direction": sent}

    def orchestrate(self, work):
        from .workspaces import Workspaces

        self._capability("orchestrate")
        scope = self.control.current()
        spaces = Workspaces(self.store, self.member)
        with spaces.lock("order-" + str(scope["work_order"]), timeout=1), ExitStack() as locks:
            # Match the workspace lock order used by delivery: order, then jobs,
            # then the database transaction. Never revoke mounts during an edit.
            names = {item.get("job", item["name"]).strip().casefold() for item in work if adapter(item["role"]) != "planner"}
            with self.store.connect() as db:
                jobs = [row["id"] for row in db.execute(
                    "SELECT id,title FROM jobs WHERE work_order=? ORDER BY id", (scope["work_order"],))
                        if row["title"].casefold() in names]
            for job in jobs:
                locks.enter_context(spaces.lock("job-" + str(job)))
            return self._orchestrate(work)

    def plan_adopt(self, plan, jobs):
        from .planning import Planning

        self._capability("plan_adopt")
        return Planning(self).adopt(plan, jobs)

    def _orchestrate(self, work):
        from .planning import Planning

        self._capability("orchestrate")
        return Planning(self).assign(work)

    def restart_xo(self, reason):
        raise ValueError("XO session repair requires the trusted operator's session tools.")

    def steer(self, action, work=None, direction=None, role=None, assignment=None, **fields):
        from .planning import Planning, execution_current, writer_available

        self._capability("steer")
        if role and adapter(role) == "arbitrator":
            raise ValueError("Arbitration assignments are managed by the decision service")
        if action == "complete" or action == "cancel" and assignment:
            if not assignment or not direction or fields:
                raise ValueError("Name the role assignment and supply its completion assessment or cancellation reason.")
            return Planning(self).complete(work, assignment, role, direction, cancel=action == "cancel")
        if role and adapter(role) == "audit" and fields:
            raise ValueError(
                "Guide the audit with direction or revise its responsibility through orchestrate; job criteria apply to the whole job."
            )
        if action in {"accept", "cancel"}:
            if not work or not direction or fields or role:
                raise ValueError(
                    "Name one job and supply its acceptance assessment or cancellation reason as direction."
                )
            with self.store.connect() as db:
                scope = self._scope(db, {"xo"})
                job = self._named_job(db, scope, work)
            from .delivery import Delivery

            return Delivery(self.store, self.member).finish_job(job["id"], direction, cancel=action == "cancel")
        if fields and not work:
            raise ValueError("Name one responsibility when revising its objective, completion criteria or constraints.")
        if action == "guide" and not direction and not fields:
            raise ValueError("Supply direction or a revised job brief.")
        if action == "replace" and not direction:
            raise ValueError("Supply the reason for replacing the conversation as direction.")
        if fields:
            from .assignment_decisions import steer_assignment

            selected = steer_assignment(self, action, work, direction, role, assignment, fields)
            if selected is not None:
                return selected
        config = self._config()
        results = []
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            scope = self._scope(db, {"xo", "nco"})
            targets = self._descendants(db, scope)
            if assignment:
                targets = [row for row in targets if row.get("assignment_id") and
                           record(db, "role_assignments", row["assignment_id"])["name"].casefold() == assignment.casefold()]
            if role:
                targets = [row for row in targets if row["role"] == role]
            if scope["paused"] and action not in {"pause", "stop"}:
                raise ValueError("Your work is paused. Request guidance before restarting or redirecting it.")
            if work:
                targets = [
                    row
                    for row in targets
                    if (record(db, "jobs", row["job"])["title"] if row["job"] else
                        record(db, "role_assignments", row["assignment_id"])["name"] if row.get("assignment_id") else
                        coordination_brief(row).get("name", "") if adapter(row["role"]) == "nco" else "").casefold() == work.strip().casefold()
                ]
                if not targets:
                    raise ValueError("Name a responsibility in your current team.")
                # Pausing/stopping or changing a delegated mission affects its entire team.
                # An explicit NCO resume lets the coordinator refresh its paused
                # specialists before restarting them under the revised plan.
                for target in list(targets):
                    if adapter(target["role"]) == "nco" and (fields or action in {"pause", "stop"} or action == "resume" and not role):
                        targets.extend(row for row in self._descendants(db, target) if row not in targets)
            arbitrators = [row for row in targets if adapter(row["role"]) == "arbitrator"]
            if arbitrators and (assignment or len(arbitrators) == len(targets)):
                raise ValueError("Arbitration assignments are managed by the decision service")
            targets = [row for row in targets if adapter(row["role"]) != "arbitrator"]
            if not targets:
                raise ValueError("No collaborators are bound to this responsibility yet.")
            changed_jobs = set()
            delegated_fields = bool(fields and any(adapter(row["role"]) == "nco" for row in targets))
            delegated_change = bool(fields and any(adapter(row["role"]) == "nco" and any(
                coordination_brief(row).get(key) != value for key, value in fields.items()
            ) for row in targets))
            for target in targets:
                changed = False
                if fields and target.get("assignment_id"):
                    previous = record(db, "role_assignments", target["assignment_id"])
                    updated = {**json.loads(previous["brief"]), **fields}
                    changed = canonical(updated) != previous["brief"]
                    if changed:
                        db.execute("UPDATE role_assignments SET brief=?,revision=revision+1,result_update=NULL,updated=? WHERE id=?",
                                   (canonical(updated), stamp(), previous["id"]))
                elif fields and target["job"] and record(db, "work_orders", scope["work_order"])["plan_revision"]:
                    raise ValueError("Job scope changes belong to XO's revised plan; use plan_adopt.")
                elif fields and adapter(target["role"]) == "nco":
                    previous = coordination_brief(target)
                    changed = any(previous.get(key) != value for key, value in fields.items())
                    previous.update(fields)
                    db.execute("UPDATE member_bindings SET brief=? WHERE member=?", (canonical(previous), target["member"]))
                    target = binding(db, target["member"])
                elif fields and not delegated_fields:
                    old = record(db, "jobs", target["job"])
                    changed = any(old[key] != value for key, value in fields.items())
                    self.work.update_in(db, "jobs", scope["work_order"], target["job"], **fields)
                    if changed:
                        changed_jobs.add(target["job"])
                changed = changed or target["job"] in changed_jobs or delegated_change
                paused = 1 if action in {"pause", "stop"} else 0 if action in {"resume", "replace"} else target["paused"]
                if adapter(target["role"]) == "nco":
                    value = coordination_brief(target)
                    if direction is not None:
                        value["direction"] = direction
                    brief = canonical(value)
                else:
                    brief = direction if direction is not None else target["brief"]
                if changed or paused != target["paused"] or brief != target["brief"] or action == "replace":
                    db.execute(
                        "UPDATE member_bindings SET paused=?,brief=? WHERE member=?", (paused, brief, target["member"])
                    )
                    self._touch(db, target["member"])
                if action in {"pause", "stop"}:
                    db.execute("UPDATE member_bindings SET launch_pending=0 WHERE member=?", (target["member"],))
                if action in {"stop", "replace"}:
                    db.execute("UPDATE members SET desired='stopped' WHERE name=?", (target["member"],))
                target = binding(db, target["member"])
                if action in {"resume", "replace"}:
                    execution_current(db, target)
                    writer_available(db, target, target["role"], target["member"])
                    if target["job"] and adapter(target["role"]) != "nco":
                        Planning.dependencies(db, record(db, "jobs", target["job"]))
                    slot = self._slot(db, config, existing=target)
                    if action == "replace" and slot["harness"] == "external":
                        raise ValueError("This collaborator is external; its operator must reconnect it.")
                    self._start_intent(db, target, slot)
                sent = self._direction(
                    db,
                    target,
                    {
                        "guide": "Apply the current direction.",
                        "pause": "Pause new work; remain available for guidance.",
                        "resume": "Resume work within the current brief.",
                        "stop": "Stop work; the managed session will close.",
                        "replace": "Recover the existing brief, reports and evidence in a new conversation.",
                    }[action],
                )
                self.store._event(
                    db,
                    self.member,
                    "work_steered",
                    {
                        "action": action,
                        "member": target["member"],
                        "work_order": target["work_order"],
                        "job": target["job"],
                    },
                )
                results.append({**self._participant(db, target, config), "direction": sent})
        return {"work": results}

    def _participant(self, db, scope, config):
        from .duty import snapshot_in

        current = binding(db, scope["member"])
        session = db.execute("SELECT * FROM members WHERE name=?", (scope["member"],)).fetchone()
        slot = next(
            (row for row in config["members"] if row["name"] == scope["member"]),
            {"name": scope["member"], "harness": session["harness"]},
        )
        job = record(db, "jobs", scope["job"]) if scope["job"] else None
        from .assignments import current_update

        update = current_update(db, current)
        usage = db.execute(
            "SELECT id,at,data FROM events WHERE actor=? AND work_order=? AND job IS ? AND kind='native_usage' "
            "AND json_extract(data,'$.generation') IS ? ORDER BY id DESC LIMIT 1",
            (scope["member"], scope["work_order"], scope["job"], session["generation"]),
        ).fetchone()
        observed_usage = None
        data = json.loads(usage["data"]) if usage else {}
        if data.get("observation") is not None:
            value = data.get("observation")
            encoded = canonical(value)
            observed_usage = {"at": usage["at"], "observation": value, "truncated": False,
                              "generation": data.get("generation"), "native_id": data.get("native_id")}
            if len(encoded) > 3000:
                observed_usage.update(observation=None, preview=encoded[:3000], truncated=True)
        return {
            "role": scope["role"],
            "work": job["title"] if job else record(db, "role_assignments", scope["assignment_id"])["name"] if scope.get("assignment_id") else "work-order coordination",
            "assignment": record(db, "role_assignments", scope["assignment_id"])["name"] if scope.get("assignment_id") else None,
            "work_state": "paused" if current["paused"] else "active",
            "status": job["status"] if job else record(db, "work_orders", scope["work_order"])["status"],
            "blocker": job["blocker"] if job else "",
            "session": {
                "observed": session["state"],
                "desired": session["desired"],
                "launch_pending": bool(current["launch_pending"]),
                "error": current["launch_error"] or json.loads(session["detail"]).get("error"),
                "managed": slot.get("harness") != "external",
            },
            "latest_report": Control._update(update, preview=True) if update else None,
            "latest_usage": observed_usage,
            "readiness": snapshot_in(self.store, db, scope["member"]),
        }

    @staticmethod
    def _owns_job(db, scope, job):
        if scope["work_order"] != job["work_order"]:
            return False
        if adapter(scope["role"]) == "nco":
            return scope["job"] == job["id"]
        owners = {scope["member"]}
        if adapter(scope["role"]) == "xo":
            owners.update(row["member"] for row in Commands._descendants(db, scope) if adapter(row["role"]) == "nco")
            # Keep adopted history visible after a coordinator is released, without
            # exposing jobs belonging to an independent command team.
            adopted = db.execute("SELECT 1 FROM work_plans p,json_each(p.jobs) j "
                                 "WHERE p.work_order=? AND p.adopted_by=? "
                                 "AND lower(json_extract(j.value,'$.name'))=lower(?)",
                                 (scope["work_order"], scope["member"], job["title"])).fetchone()
            if adopted:
                return True
        return adapter(scope["role"]) == "co" or job["created_by"] in owners

    @staticmethod
    def _named_job(db, scope, name):
        rows = [
            dict(row)
            for row in db.execute("SELECT * FROM jobs WHERE work_order=?", (scope["work_order"],))
            if row["title"].casefold() == name.strip().casefold()
            and Commands._owns_job(db, scope, row)
        ]
        if len(rows) != 1:
            raise ValueError("Name a unique responsibility in your current work order.")
        return rows[0]

    def conclude(self, assessment):
        from .orders import Orders
        from .workspaces import Workspaces

        self._capability("conclude")
        with self.store.connect() as db:
            scope = self._scope(db, {"xo"})
        with Workspaces(self.store, self.member).lock("order-" + str(scope["work_order"])):
            delivery = Orders(self.store, self.member).finish(scope)
            return self._conclude(assessment, delivery)

    def _conclude(self, assessment, delivery):
        from .delivery import Delivery
        from .orders import Orders
        from .retirement import queue

        self._capability("conclude")

        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            scope = self._scope(db, {"xo"})
            Orders.settled(db, scope["work_order"])
            if delivery.get("_completion"):
                from .completion import Completion

                Completion(self.store, self.member).verify(delivery["_completion"])
            Work(self.store, self.member).update_in(
                db, "work_orders", scope["work_order"], None, status="done", next_action="", blocker=""
            )
            db.execute(
                "INSERT INTO order_deliveries(work_order,revision,merge_commit,assessment,created) VALUES (?,?,?,?,?)",
                (scope["work_order"], delivery.get("revision"), delivery.get("merge_commit"), assessment, stamp()),
            )
            if delivery.get("merge_commit"):
                queue(db, scope["work_order"], None, delivery["revision"])
            recipient = binding(db, scope["reports_to"])
            if recipient and adapter(recipient["role"]) == "co" and recipient["work_order"] == scope["work_order"]:
                self.store._send(
                    db, self.member, recipient["member"], "Work-order outcome: done\n" + assessment,
                    key="order-concluded:" + str(scope["work_order"]),
                    work_order=scope["work_order"], job=None,
                )
            for row in [*self._descendants(db, scope), scope]:
                Delivery.release(db, row["member"])
            self.store._event(
                db, self.member, "work_order_concluded", {"work_order": scope["work_order"], "assessment": assessment}
            )
        return {
            "outcome": "done",
            "assessment": assessment,
            "history": "retained",
            "next": "CO relays the outcome to the owner and uses direct for the next authorized objective.",
        }

    def monitor(self, offset=None, work=None, path=None, page=1, section="overview"):
        self._capability("monitor")
        scope = self.control.current()
        if adapter(self.role(scope)) == "co" and (work or section != "overview" or path is not None or page != 1):
            raise ValueError("CO relays overview status; delivery inspection belongs to XO and job coordinators.")
        if not work and section != "overview" and scope and adapter(scope["role"]) == "nco" and scope["job"]:
            with self.store.connect() as db:
                work = record(db, "jobs", scope["job"])["title"]
        if work:
            with self.store.connect() as db:
                scope = self._scope(db, {adapter(name) for name in role_tools()})
                job = self._named_job(db, scope, work)
            from .delivery import Delivery

            return {
                "work": {key: job[key] for key in BRIEF_FIELDS},
                "delivery": Delivery(self.store, self.member).inspect(
                    job=job["id"],
                    path=path,
                    offset=offset,
                    page=page,
                    section="review" if section == "overview" else section,
                ),
            }
        if section != "overview":
            from .orders import Orders

            return {
                "delivery": Orders(self.store, self.member).inspect(
                    section=section, path=path, offset=offset, page=page
                )
            }
        if path is not None or page != 1:
            raise ValueError("Choose section review for delivery details.")
        offset = offset or 0
        config = self._config()
        with self.store.connect() as db:
            db.execute("BEGIN")
            scope = self._scope(db, {adapter(name) for name in role_tools()}, bootstrap=True)
            team = self._descendants(db, scope) if scope else []
            jobs = (
                [
                    dict(row)
                    for row in db.execute("SELECT * FROM jobs WHERE work_order=? ORDER BY id", (scope["work_order"],))
                    if self._owns_job(db, scope, row)
                ]
                if scope
                else []
            )
            job_ids = {row["id"] for row in jobs}
            assignments = [dict(row) for row in db.execute("SELECT * FROM role_assignments WHERE work_order=? ORDER BY id", (scope["work_order"],))
                           if row["job"] in job_ids or row["job"] is None and row["created_by"] == self.member] if scope else []
            available = 0
            for slot in config["members"]:
                if (slot["name"] in {self.member, config.get("co")} or binding(db, slot["name"])
                        or db.execute("SELECT 1 FROM standby_bindings WHERE member=?", (slot["name"],)).fetchone()):
                    continue
                session = db.execute("SELECT state,pid FROM members WHERE name=?", (slot["name"],)).fetchone()
                if session["state"] not in {"stopped", "failed"} or session["pid"]:
                    continue
                available += 1
            capacity = [{"available": available}]
            if config.get("runtime"):
                from .runtime import load, resolve

                policy, _, _ = load(self.store.directory)
                used = db.execute("SELECT count(*) FROM member_bindings WHERE role NOT IN (SELECT value FROM json_each(?))", (canonical(names_for_adapter("co")),)).fetchone()[0]
                used += db.execute("SELECT count(*) FROM standby_bindings").fetchone()[0]
                ready = []
                for role in catalog()["roles"]:
                    if adapter(role) == "co":
                        continue
                    try:
                        resolve(self.store.directory, role)
                        ready.append(role)
                    except ValueError:
                        pass
                capacity = [{"available": max(0, policy["max_sessions"] - used), "roles": ready,
                             "shared_pool": True}]
            return {
                "work": self._brief(db, scope)["work"] if scope else None,
                "delivery": {"pr": record(db, "jobs" if scope["job"] else "work_orders", scope["job"] or scope["work_order"])["pull_request"] or None}
                if scope
                else None,
                "cleanup": [
                    dict(row)
                    for row in db.execute(
                        "SELECT job,state,detail FROM managed_worktrees WHERE work_order=? AND state!='active' "
                        "AND (? IS NULL OR job=?) ORDER BY updated DESC LIMIT 10",
                        (scope["work_order"], scope["job"], scope["job"]),
                    )
                ]
                if scope
                else [],
                "collaborators": [self._participant(db, row, config) for row in team[offset : offset + 10]],
                "next_offset": offset + 10 if len(team) > offset + 10 else None,
                "assignments": [{"name": row["name"], "role": row["role"], "status": row["status"],
                                 "job": record(db, "jobs", row["job"])["title"] if row["job"] else None,
                                 "result": "update:" + str(row["result_update"]) if row["result_update"] else None}
                                for row in assignments[offset:offset + 10]],
                "next_assignment_offset": offset + 10 if len(assignments) > offset + 10 else None,
                "capacity": capacity,
                "jobs": [
                    {key: row[key] for key in ("title", "status", "next_action", "blocker", "pull_request")}
                    for row in jobs[offset : offset + 10]
                ],
                "next_job_offset": offset + 10 if len(jobs) > offset + 10 else None,
                "usage_note": "Latest raw observations only; absent usage is unknown, and observations are not additive totals.",
            }

    def tick(self):
        """Resume persisted launch intents after a server restart; never dispatch work."""
        from .duty import tick as duty_tick

        duty_tick(self.store)
        for row in self.store.members():
            if row["harness"] != "external" and row["desired"] == "stopped" and row["pid"]:
                try:
                    launch.stop(self.store.directory, row["name"], expected_generation=row["generation"])
                except BlockingIOError:
                    pass
        from .standby import tick as standby_tick

        standby_tick(self.store)
        from .decisions import Decisions

        Decisions(self.store, self.member).tick()
        with self.store.connect() as db:
            pending = list(
                db.execute("SELECT member,revision FROM member_bindings WHERE launch_pending=1 AND paused=0")
            )
        for row in pending:
            try:
                launch.start(self.store.directory, row["member"], binding_revision=row["revision"])
            except BlockingIOError:
                continue
            except Exception as error:
                with self.store.connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    current = binding(db, row["member"])
                    if not current or current["revision"] != row["revision"] or not current["launch_pending"]:
                        continue
                    detail = str(error)[:600]
                    db.execute(
                        "UPDATE member_bindings SET launch_pending=0,launch_error=? WHERE member=?",
                        (detail, row["member"]),
                    )
                    parent = binding(db, current["reports_to"])
                    if parent and parent["work_order"] == current["work_order"]:
                        self.store._send(
                            db,
                            row["member"],
                            parent["member"],
                            "Session startup failed: " + detail,
                            key="launch-failed:" + digest([row["member"], current["revision"], detail]),
                            work_order=current["work_order"],
                            job=current["job"],
                        )
                    self.store._event(
                        db,
                        row["member"],
                        "command_launch_failed",
                        {"error": detail, "work_order": current["work_order"], "job": current["job"]},
                    )
