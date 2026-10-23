"""Pre-work XO identity and launch intent, separate from all work authority."""

import json
import time

from .role_packages import default_role
from .store import canonical, stamp


def binding(db, member):
    row = db.execute("SELECT * FROM standby_bindings WHERE member=?", (member,)).fetchone()
    if row is None:
        return None
    return {**dict(row), "standby": True, "work_order": None, "job": None,
            "assignment_id": None, "paused": 0, "brief": "Report startup readiness, then await the owner's work order."}


def role(store, member):
    with store.connect() as db:
        scope = binding(db, member)
        return scope["role"] if authorized(store, scope, db=db) else None


def authorized(store, scope, *, db):
    from .launch import team_config
    from .role_packages import adapter

    if not scope or adapter(scope["role"]) != "xo":
        return False
    parent = db.execute("SELECT harness FROM members WHERE name=?", (scope["reports_to"],)).fetchone()
    parent_work = db.execute("SELECT role FROM member_bindings WHERE member=?", (scope["reports_to"],)).fetchone()
    return (team_config(store.directory).get("co") == scope["reports_to"] and bool(parent)
            and parent["harness"] == "external" and (not parent_work or adapter(parent_work["role"]) == "co"))


def context(store, member):
    with store.connect() as db:
        scope = binding(db, member)
        if not authorized(store, scope, db=db):
            return None
    return {"role": scope["role"], "state": "standby", "context_revision": scope["revision"],
            "reporting_to": scope["reports_to"], "responsibility": scope["brief"],
            "next_action": "Complete onboard and report readiness; await a real work order before coordinating work."}


def _xo(api, db):
    from .control import binding as work_binding
    from .role_packages import adapter

    current = work_binding(db, api.member)
    if current:
        for row in api.commands._descendants(db, current):
            if row["reports_to"] == api.member and adapter(row["role"]) == "xo":
                return row
    row = db.execute("SELECT member FROM standby_bindings WHERE reports_to=?", (api.member,)).fetchone()
    return binding(db, row["member"]) if row else None


def status(api):
    from . import duty

    with api.store.connect() as db:
        scope = _xo(api, db)
    if scope is None:
        return {"state": "not_started", "initial": True, "preflight": None, "xo": None}
    session = api.store.member(scope["member"])
    receipt = duty.public(duty.current(api.store, scope["member"]))
    with api.store.connect() as db:
        evidence = db.execute("SELECT binding,generation FROM duty_readiness WHERE member=? ORDER BY id DESC LIMIT 1",
                              (scope["member"],)).fetchone()
    if evidence and (json.loads(evidence["binding"]).get("revision") != scope["revision"]
                     or evidence["generation"] != session["generation"]):
        receipt = None
    preflight = json.loads(scope["preflight"]) if scope.get("standby") else None
    state = receipt["status"] if receipt else "starting"
    if receipt and receipt["status"] == "timed_out":
        state = "timed_out"
    elif scope.get("launch_error"):
        state = "blocked"
    elif scope.get("standby") and not receipt and time.time() >= scope["deadline"]:
        state = "timed_out"
    return {"state": state, "initial": bool(scope.get("standby")), "preflight": preflight,
            "xo": {"member": scope["member"], "role": scope["role"],
                   "session": {"observed": session["state"], "desired": session["desired"]},
                   "readiness": receipt, "blocker": scope.get("launch_error")}}


def start(api):
    from .readiness import startup_preflight

    if not api.commands.appointed_co():
        raise ValueError("Only the appointed CO initiates initial readiness.")
    config = api.commands._config()
    with api.store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        reserved = _reserve(api, db, config)
    if reserved is None:
        return status(api)
    slot, revision, deadline = reserved
    preflight = startup_preflight(api.store.directory, timeout=max(0.01, min(10, deadline - time.time())))
    with api.store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        current = binding(db, slot["name"])
        if current and current["revision"] == revision:
            passed = preflight.get("status") == "preflight_passed" and time.time() < deadline
            error = None if passed else "Initial startup preflight did not pass; inspect its retained checks."
            db.execute("UPDATE standby_bindings SET preflight=?,launch_pending=?,launch_error=?,updated=? WHERE member=?",
                       (canonical(preflight), int(passed), error, stamp(), slot["name"]))
            if error:
                failure_message_in(api.store, db, current, error)
            api.store._event(db, api.member, "readiness_preflight", {"member": slot["name"], "revision": revision,
                             "result": preflight, "work_order": None, "job": None})
    return status(api)


def _reserve(api, db, config):
    from .configuration import resolve
    from .processes import registered_alive

    scope = _xo(api, db)
    if scope and not scope.get("standby"):
        return None
    if scope:
        session = db.execute("SELECT * FROM members WHERE name=?", (scope["member"],)).fetchone()
        dead = (session["desired"] == "running" and
                registered_alive(api.store, scope["member"], session["generation"], session["pid"]) is False)
        if not dead and (scope["launch_pending"] or session["desired"] == "running" or (
            not scope["launch_error"] and time.time() < scope["deadline"]
        )):
            return None
        if dead:
            # Launch's existing stop path reaps any registered descendants before
            # replacing the dead supervisor; never signal an unverified raw PID.
            db.execute("UPDATE members SET desired='stopped',updated=? WHERE name=?", (stamp(), scope["member"]))
        slot = api.commands._slot(db, config, existing=scope, role=scope["role"])
    else:
        available = config if config.get("runtime") else {
            **config, "members": [row for row in config["members"] if row["harness"] != "external"]}
        slot = api.commands._slot(db, available, role=default_role("xo"))
    if slot["harness"] == "external":
        raise ValueError("Initial readiness needs a managed XO; configure a native collaborator.")
    xo_role = scope["role"] if scope else default_role("xo")
    deadline = time.time() + resolve(api.store, role=xo_role)["readiness"]["timeout_seconds"]
    revision = api.store.next_binding_revision(db, slot["name"])
    if scope:
        db.execute("UPDATE messages SET delivery='superseded' WHERE sender=? AND request_key=? AND delivery='queued'",
                   (scope["member"], "standby-failure:" + scope["member"] + ":" + str(scope["revision"])))
    db.execute(
        "INSERT INTO standby_bindings(member,role,reports_to,revision,launch_pending,deadline,created,updated) "
        "VALUES (?,?,?,?,0,?,?,?) ON CONFLICT(member) DO UPDATE SET revision=excluded.revision,"
        "launch_pending=0,launch_error=NULL,preflight='{}',deadline=excluded.deadline,updated=excluded.updated",
        (slot["name"], xo_role, api.member, revision, deadline, stamp(), stamp()),
    )
    api.store._event(db, api.member, "readiness_requested", {"member": slot["name"], "role": xo_role,
                     "revision": revision, "deadline": deadline, "work_order": None, "job": None})
    return slot, revision, deadline


def consume_in(store, db, parent):
    """Retire standby authority atomically before binding its seat to real work."""
    row = db.execute("SELECT member FROM standby_bindings WHERE reports_to=?", (parent,)).fetchone()
    if row is None:
        return None
    scope = binding(db, row["member"])
    db.execute("UPDATE messages SET delivery='superseded' WHERE sender=? AND request_key=? AND delivery='queued'",
               (scope["member"], "standby-failure:" + scope["member"] + ":" + str(scope["revision"])))
    db.execute("DELETE FROM standby_bindings WHERE member=?", (scope["member"],))
    db.execute("UPDATE members SET desired='stopped',updated=? WHERE name=?", (stamp(), scope["member"]))
    db.execute("UPDATE duty_readiness SET status='superseded',updated=? WHERE member=? AND status='starting'",
               (stamp(), scope["member"]))
    store._event(db, parent, "standby_promoted", {"member": scope["member"], "revision": scope["revision"],
                 "work_order": None, "job": None})
    return scope


def failure_message_in(store, db, scope, detail):
    """Retain startup failures even when no native session could be registered."""
    if not authorized(store, scope, db=db):
        return None
    key = "standby-failure:" + scope["member"] + ":" + str(scope["revision"])
    existing = db.execute("SELECT id FROM messages WHERE sender=? AND request_key=?", (scope["member"], key)).fetchone()
    if existing:
        return existing["id"]
    message = store._send(db, scope["member"], scope["reports_to"],
                          scope["role"] + " unable to report ready — " + detail,
                          key=key, work_order=None, job=None)
    db.execute("UPDATE messages SET category='readiness' WHERE id=?", (message["id"],))
    return message["id"]


def failure_messages_in(store, db, member):
    identifiers = []
    for row in db.execute(
        "SELECT m.id,s.member FROM messages m JOIN standby_bindings s ON m.sender=s.member "
        "AND m.recipient=s.reports_to AND m.request_key='standby-failure:'||s.member||':'||s.revision "
        "WHERE m.recipient=? AND m.category='readiness'", (member,),
    ):
        if authorized(store, binding(db, row["member"]), db=db):
            identifiers.append(row["id"])
    return identifiers


def tick(store):
    from . import launch
    from .duty import snapshot_in

    # Failed diagnostics do not leave an idle native process consuming a seat.
    # Actual work sessions retain their normal coordinator-owned lifecycle.
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        for row in db.execute(
            "SELECT s.member,s.deadline,s.launch_error,m.generation,m.desired FROM standby_bindings s "
            "JOIN members m ON m.name=s.member"
        ).fetchall():
            scope = binding(db, row["member"])
            revoked = not authorized(store, scope, db=db)
            receipt = snapshot_in(store, db, row["member"])
            if receipt and receipt["status"] == "superseded":
                receipt = None
            failed = receipt and receipt["status"] in {"blocked", "timed_out"}
            expired = not receipt and time.time() >= row["deadline"]
            if failed or expired or revoked:
                detail = ("The standby CO appointment was revoked." if revoked else
                          "XO startup readiness " + (receipt["status"] if receipt else "timed_out") + ".")
                # The retained deadline already represents a pre-launch timeout.
                # Keep launch_error for an actual failure, so status remains
                # timed_out after this lifecycle tick as well as before it.
                error = row["launch_error"] or (detail if failed or revoked else None)
                db.execute("UPDATE standby_bindings SET launch_pending=0,launch_error=?,updated=? WHERE member=?",
                           (error, stamp(), row["member"]))
                db.execute("UPDATE members SET desired='stopped',updated=? WHERE name=?", (stamp(), row["member"]))
                if not receipt:
                    failure_message_in(store, db, scope, detail)
                if revoked:
                    db.execute("DELETE FROM standby_bindings WHERE member=?", (row["member"],))
                    store._event(db, row["member"], "standby_released", {"reason": detail,
                                 "work_order": None, "job": None})
    with store.connect() as db:
        rows = list(db.execute("SELECT member,revision,deadline FROM standby_bindings WHERE launch_pending=1"))
    for row in rows:
        try:
            if time.time() >= row["deadline"]:
                raise ValueError("Initial readiness timed out before XO could launch.")
            launch.start(store.directory, row["member"], standby_revision=row["revision"])
        except BlockingIOError:
            continue
        except Exception as error:
            with store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                current = binding(db, row["member"])
                if not current or current["revision"] != row["revision"] or not current["launch_pending"]:
                    continue
                detail = str(error)[:600]
                db.execute("UPDATE standby_bindings SET launch_pending=0,launch_error=?,updated=? WHERE member=?",
                           (detail, stamp(), row["member"]))
                failure_message_in(store, db, current, detail)
                store._event(db, row["member"], "standby_launch_failed", {"error": detail,
                             "work_order": None, "job": None})
