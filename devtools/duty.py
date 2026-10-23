"""Generation-bound startup acknowledgements through ordinary team messages."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from .store import canonical, stamp

IDENTITY = ("member", "role", "reports_to", "work_order", "job", "revision", "assignment_id", "standby")


def _scope(db, member):
    from .control import binding
    from .standby import binding as standby_binding

    return binding(db, member) or standby_binding(db, member)


def _identity(scope):
    from .role_packages import descriptor

    return canonical({**{key: scope.get(key) for key in IDENTITY}, "package_revision": descriptor(scope["role"])["revision"]})


def _latest(db, member):
    row = db.execute("SELECT * FROM duty_readiness WHERE member=? ORDER BY id DESC LIMIT 1", (member,)).fetchone()
    return dict(row) if row else None


def public(value):
    """A parent may inspect a receipt, but never answers its child's challenge."""
    return ({key: item for key, item in value.items() if key not in {"challenge", "onboarding_revision"}}
            if value is not None else None)


def _view(row):
    if not row:
        return None
    value = {"reference": "readiness:" + str(row["id"]), "role": row["role"], "status": row["status"],
             "challenge": row["challenge"], "deadline": row["deadline"], "checks": json.loads(row["checks"]),
             "onboarding_revision": row["onboarding_revision"], "generation": row["generation"],
             "context_revision": json.loads(row["binding"])["revision"], "updated": row["updated"]}
    if row["receipt_message"]:
        value["receipt"] = "message:" + str(row["receipt_message"])
    return value


def begin_in(store, db, member, generation):
    """Called in the launch transaction; creates evidence, never work or authority."""
    scope = _scope(db, member)
    if not scope or scope.get("reports_to") is None:
        return None
    previous = _latest(db, member)
    if previous and previous["generation"] == generation and previous["binding"] == _identity(scope):
        return previous
    if previous:
        if previous["status"] in {"starting", "ready"}:
            db.execute("UPDATE duty_readiness SET status='superseded',updated=? WHERE id=?", (stamp(), previous["id"]))
        db.execute("UPDATE messages SET delivery='superseded' WHERE id IN (?,?) AND delivery='queued'",
                   (previous["challenge_message"], previous["receipt_message"]))
    from .configuration import resolve

    deadline = scope["deadline"] if scope.get("standby") else time.time() + resolve(
        store, role=scope["role"], job=scope.get("job"))["readiness"]["timeout_seconds"]
    challenge = uuid.uuid4().hex
    created = stamp()
    ident = db.execute(
        "INSERT INTO duty_readiness(member,parent,role,generation,binding,challenge,deadline,status,created,updated) "
        "VALUES (?,?,?,?,?,?,?,'starting',?,?)",
        (member, scope["reports_to"], scope["role"], generation, _identity(scope), challenge, deadline, created, created),
    ).lastrowid
    message = store._send(
        db, scope["reports_to"], member,
        "Readiness challenge. Report for duty: call onboard, then readiness_report with its readiness.challenge "
        "and onboarding_revision. Report any startup blockers. This checks startup readiness, not completed work.",
        key="readiness-challenge:" + challenge, work_order=scope.get("work_order"), job=scope.get("job"),
    )
    db.execute("UPDATE messages SET category='readiness' WHERE id=?", (message["id"],))
    db.execute("UPDATE duty_readiness SET challenge_message=? WHERE id=?", (message["id"], ident))
    store._event(db, member, "readiness_started", {"readiness": ident, "role": scope["role"],
                 "generation": generation, "deadline": deadline, "work_order": scope.get("work_order"), "job": scope.get("job")})
    return _latest(db, member)


def _parent_current(db, row, store):
    scope = _scope(db, row["member"])
    if not scope or _identity(scope) != row["binding"] or scope.get("reports_to") != row["parent"]:
        return False
    if scope.get("standby"):
        from .standby import authorized

        return authorized(store, scope, db=db)
    parent = _scope(db, row["parent"])
    return bool(parent and parent.get("work_order") == scope.get("work_order") and
                (parent.get("job") is None or parent.get("job") == scope.get("job")))


def _finish(store, db, row, status, checks):
    if row["status"] == status:
        return row
    now = stamp()
    db.execute("UPDATE messages SET delivery='superseded' WHERE id=? AND delivery='queued'", (row["receipt_message"],))
    db.execute("UPDATE duty_readiness SET status=?,checks=?,updated=?,receipt_message=NULL WHERE id=?",
               (status, canonical(checks), now, row["id"]))
    row = {**row, "status": status, "checks": canonical(checks), "updated": now, "receipt_message": None}
    store._event(db, row["member"], "readiness_" + status,
                 {"readiness": row["id"], "role": row["role"], "generation": row["generation"], "checks": checks,
                  "work_order": json.loads(row["binding"]).get("work_order"), "job": json.loads(row["binding"]).get("job")})
    if _parent_current(db, row, store):
        scope = json.loads(row["binding"])
        text = (row["role"] + " reporting for duty — all required startup checks passed. Communications confirmed."
                if status == "ready" else row["role"] + " unable to report ready — " + status + ".")
        body = text + "\n" + canonical(public(_view(row)))
        message = store._send(db, row["member"], row["parent"], body,
                              key="readiness-receipt:" + row["challenge"] + ":" + status,
                              reply_to=row["challenge_message"], work_order=scope.get("work_order"), job=scope.get("job"))
        db.execute("UPDATE messages SET category='readiness' WHERE id=?", (message["id"],))
        db.execute("UPDATE duty_readiness SET receipt_message=? WHERE id=?", (message["id"], row["id"]))
        row["receipt_message"] = message["id"]
    return row


def _status_in(store, db, row):
    """Derive current readiness once for retained updates and read-only views."""
    from .processes import registered_alive

    session = db.execute("SELECT generation,state,desired,pid FROM members WHERE name=?", (row["member"],)).fetchone()
    if not session or session["generation"] != row["generation"] or not _parent_current(db, row, store):
        return "superseded"
    if row["status"] in {"starting", "ready"}:
        alive = registered_alive(store, row["member"], row["generation"], session["pid"])
        if (session["desired"] != "running" or session["state"] in {"stopped", "failed"}
                or alive is False or row["status"] == "ready" and alive is not True):
            return "blocked"
    if row["status"] == "starting" and time.time() >= row["deadline"]:
        return "timed_out"
    return row["status"]


def _refresh(store, db, row):
    if not row:
        return None
    status = _status_in(store, db, row)
    if status == "superseded":
        if row["status"] in {"starting", "ready"}:
            db.execute("UPDATE duty_readiness SET status='superseded',updated=? WHERE id=?", (stamp(), row["id"]))
            db.execute("UPDATE messages SET delivery='superseded' WHERE id IN (?,?) AND delivery='queued'",
                       (row["challenge_message"], row["receipt_message"]))
            row = {**row, "status": "superseded"}
        return row
    if status == "blocked" and row["status"] != status:
        return _finish(store, db, row, "blocked", json.loads(row["checks"]) + [
            {"name": "session", "status": "failed", "detail": "Native session stopped before current readiness could be established or retained."}])
    if status == "timed_out" and row["status"] != status:
        return _finish(store, db, row, "timed_out", [
            {"name": "acknowledgement", "status": "failed", "detail": "No complete startup acknowledgement before the deadline."}])
    return row



def snapshot_in(store, db, member):
    """Fresh public projection inside the caller's transaction; never writes."""
    row = _latest(db, member)
    if not row:
        return None
    status = _status_in(store, db, row)
    if status != row["status"]:
        row = {**row, "status": status, "receipt_message": None,
               "checks": canonical([{ "name": "current_session", "status": "failed",
                                      "detail": "Current session/context is " + status + "; earlier evidence is historical."}])}
    return public(_view(row))


def current(store, member):
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        session = db.execute("SELECT * FROM members WHERE name=?", (member,)).fetchone()
        row = _refresh(store, db, _latest(db, member))
        scope = _scope(db, member)
        if (session and session["harness"] != "external" and session["generation"] and session["desired"] == "running"
                and scope and (row is None or row["generation"] != session["generation"] or row["binding"] != _identity(scope))):
            row = begin_in(store, db, member, session["generation"])
        return _view(_refresh(store, db, row))


def onboard(api, value):
    """Record actual onboarding delivery; initialize/instructions never calls this."""
    current(api.store, api.member)
    with api.store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = _refresh(api.store, db, _latest(db, api.member))
        if not row:
            return value
        scope = _scope(db, api.member)
        captured = api.control.current()
        if scope and not scope.get("standby") and captured != scope:
            raise ValueError("Readiness context changed during onboarding; call onboard again.")
        if row["status"] == "starting":
            context = value.get("context", {})
            if (value.get("role") != row["role"] or context.get("role") != row["role"]
                    or context.get("context_revision") != scope["revision"]
                    or context.get("readiness", {}).get("challenge") != row["challenge"]
                    or scope.get("standby") and context.get("reporting_to") != scope["reports_to"]
                    or value.get("package", {}).get("revision") != json.loads(row["binding"])["package_revision"]):
                raise ValueError("Onboarding did not supply the current readiness role and context; call onboard again.")
            db.execute("UPDATE duty_readiness SET onboarding_revision=?,updated=? WHERE id=?",
                       (value["revision"], stamp(), row["id"]))
            row["onboarding_revision"] = value["revision"]
            api.store._event(db, api.member, "readiness_onboarded", {"readiness": row["id"], "revision": value["revision"]})
        return {**value, "readiness": _view(row)}


def _checks(api, scope, session):
    from .processes import registered_alive
    from .role_packages import role

    package = role(scope["role"])
    checks = []

    def check(name, condition, detail):
        checks.append({"name": name, "status": "passed" if condition else "failed", "detail": detail})

    check("native_session", bool(session["native_id"] and session["desired"] == "running" and
                                 session["state"] in {"starting", "idle", "active"} and
                                 registered_alive(api.store, api.member, session["generation"], session["pid"]) is True),
          "Current native session identity and live process state.")
    check("harness", session["harness"] in package["runtime"]["harnesses"],
          "Current harness is permitted by the configured role package.")
    check("workspace", Path(session["workspace"]).is_dir(), "Configured workspace exists; product behavior is not exercised.")
    available = {item["name"] for item in api._tools(api.control.current())}
    required = set(package["mcp"]["session"]) | {"work_context", "readiness_report"}
    check("capabilities", required <= available,
          "Required startup tool surface is available. Missing: " + ", ".join(sorted(required - available)) +
          ". Other tool execution is not exercised.")
    return checks


def report(api, challenge, onboarding_revision, blockers=None):
    blockers = blockers or []
    with api.store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = _refresh(api.store, db, _latest(db, api.member))
        if not row or row["challenge"] != challenge:
            raise ValueError("Readiness challenge is not current for this session; call onboard.")
        reported = next((item for item in json.loads(row["checks"]) if item["name"] == "agent_report"), None)
        if (row["status"] in {"ready", "blocked"} and row["onboarding_revision"] == onboarding_revision
                and reported is not None and reported["detail"] == blockers):
            return _view(row)
        if row["status"] != "starting":
            raise ValueError("Readiness request is " + row["status"] + "; obtain a fresh current request.")
        if not row["onboarding_revision"] or row["onboarding_revision"] != onboarding_revision:
            raise ValueError("Call onboard for the current readiness request before reporting for duty.")
        scope = _scope(db, api.member)
        session = dict(db.execute("SELECT * FROM members WHERE name=?", (api.member,)).fetchone())
        if not _parent_current(db, row, api.store):
            raise ValueError("Readiness reporting relationship changed; call onboard.")
        checks = _checks(api, scope, session)
        checks += [{"name": "onboarding", "status": "passed", "detail": "Current role instructions and context delivered."},
                   {"name": "communications", "status": "passed", "detail": "Current challenge returned through the role-bound MCP reporting tool."},
                   {"name": "agent_report", "status": "failed" if blockers else "passed", "detail": blockers}]
        api.store._acknowledge(db, api.member, row["challenge_message"])
        status = "blocked" if any(item["status"] == "failed" for item in checks) else "ready"
        return _view(_finish(api.store, db, row, status, checks))


def tick(store):
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        for row in db.execute("SELECT * FROM duty_readiness WHERE status IN ('starting','ready')").fetchall():
            _refresh(store, db, dict(row))


def permitted_message_in(db, member, store):
    """Current readiness messages only, independently of ordinary work scope."""
    from .standby import failure_messages_in

    identifiers = failure_messages_in(store, db, member)
    for source in db.execute(
        "SELECT d.* FROM duty_readiness d JOIN members m ON m.name=d.member AND m.generation=d.generation "
        "WHERE (d.member=? OR d.parent=?) AND d.status!='superseded'", (member, member)
    ):
        row = dict(source)
        if not _parent_current(db, row, store):
            continue
        if row["member"] == member and row["status"] == "starting" and time.time() < row["deadline"]:
            identifiers.append(row["challenge_message"])
        observed = snapshot_in(store, db, row["member"])
        if (row["parent"] == member and row["receipt_message"] and observed
                and observed.get("receipt") == "message:" + str(row["receipt_message"])):
            identifiers.append(row["receipt_message"])
    if not identifiers:
        return "0", ()
    return "category='readiness' AND id IN (" + ",".join("?" for _ in identifiers) + ")", tuple(identifiers)


def messages(store, member, after=0, reference=None):
    with store.connect() as db:
        clause, args = permitted_message_in(db, member, store)
        if reference is not None:
            if not reference.startswith("message:") or not reference[8:].isdigit():
                raise ValueError("Only current readiness message references are available before work.")
            clause += " AND id=?"
            args += (int(reference[8:]),)
        rows = db.execute("SELECT * FROM messages WHERE recipient=? AND id>? AND (" + clause + ") ORDER BY id LIMIT 11",
                          (member, after, *args)).fetchall()
        from .control import Control

        if reference is not None:
            if not rows:
                raise ValueError("Readiness message reference is not available in this context.")
            return Control._message(rows[0])
        return {"messages": [Control._message(row, preview=True) for row in rows[:10]],
                "next_after": rows[9]["id"] if len(rows) > 10 else None}
