#!/usr/bin/env python3
"""skills.py — CRUD and telemetry for the project's skills, SQLite-backed.

  sync                      derive rows from .claude/skills/ on disk (upsert;
                            a vanished dir marks its row missing, never deletes;
                            a wished skill whose dir appears becomes active)
  add NAME DESC [--kind=K] [--status=draft]      a planned skill row
  list [--status=S]         the registry view
  show NAME                 one skill, with its recent event history
  note NAME TEXT            update notes
  retire NAME REASON        soft-retire (reason required; nothing vanishes)
  log-use NAME [DETAIL]     an invocation happened
  log-fail NAME DETAIL      an invocation failed; DETAIL carries the error's words
  log-miss NAME DETAIL      a potential use that did not happen — work was done
                            another way; DETAIL says what was done instead
  wish NAME DETAIL          a skill that should exist and does not
  report                    per-skill use/fail/miss counts; wishes ranked

The skills table syncs from disk, which stays the source of truth for what
exists; events are observations (the non-authoritative-mirror rule).
Reading needs no identity; recording an event does: set WF_AGENT
('Name <address>'), the same contract wf enforces. Inherits
ops/conductor/instrument-conventions.md: a refusal is one line on
stderr, `class: expected ...; found ...; needed ...`, exit 2, stdout
empty (owner, 2026-09-13, ruling 1: exit 1 with a sentence read as the
Finding class). The store lives under the lane's state directory.
"""
import os
import re
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
try:
    import instrument
    from instrument import Refusal, emit, identity, refuse, session, stamp, state_dir
except ModuleNotFoundError:
    print(
        "tooling: expected instrument.py beside this tool; found absent; "
        "needed restore ops/conductor/tools/instrument.py beside it",
        file=sys.stderr,
    )
    raise SystemExit(2) from None

REPO = Path(__file__).resolve().parents[3]

# A secret-shaped value never enters the store: the skill-use hook hands a
# Skill call's arguments here verbatim (security audit
# 20260913T104919Z-review-codex-9126d3, finding 4). The token shapes are
# those of ops/devlane/workflow/checks/secret_scan.py; the assignment form
# covers a password or token typed as a named value.
SECRET_SHAPES = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    re.compile(r"\bxox[abpsr]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
)
SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key|access[_-]?key)\b(\s*[=:]\s*)(\S+)")
REDACTED = "[redacted]"


def redact(text):
    """The text with every secret-shaped value replaced; None stays None."""
    if text is None:
        return None
    text = SECRET_ASSIGNMENT.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
    for shape in SECRET_SHAPES:
        text = shape.sub(REDACTED, text)
    return text
SKILLS_DIR = REPO / ".claude" / "skills"
KINDS = ("project", "conductor", "process", "personal")


def db_path():
    return state_dir() / "skills.db"


def con_(*, write=False) -> sqlite3.Connection:
    path = db_path()
    required = {
        "skills": ("name", "kind", "path", "description", "status", "created_ts", "updated_ts", "retire_reason", "notes"),
        "skill_events": ("id", "ts", "session", "agent", "skill", "event", "detail"),
    }
    try:
        con = instrument.connect_store(path, mode="rwc" if write else "ro",
                                       required=None if write else required)
    except (OSError, sqlite3.Error) as error:
        raise instrument.store_failure(path, error,
              "restore a compatible skills store; sync or add explicitly initializes an absent store") from error
    if not write:
        return con
    try:
        con.execute("""CREATE TABLE IF NOT EXISTS skills(
            name TEXT PRIMARY KEY, kind TEXT NOT NULL, path TEXT,
            description TEXT, status TEXT NOT NULL DEFAULT 'active',
            created_ts TEXT NOT NULL, updated_ts TEXT NOT NULL,
            retire_reason TEXT, notes TEXT)""")
        con.execute("""CREATE TABLE IF NOT EXISTS skill_events(
            id INTEGER PRIMARY KEY, ts TEXT NOT NULL, session TEXT NOT NULL,
            agent TEXT NOT NULL, skill TEXT NOT NULL, event TEXT NOT NULL,
            detail TEXT)""")
    except BaseException:
        con.close()
        raise
    return con

def frontmatter(path):
    """(name, description) from SKILL.md frontmatter; None where absent —
    absence is not emptiness, and the caller falls back explicitly.
    A file that cannot be read at all returns None (no tuple): an
    unperformed read is not an empty frontmatter."""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return None
    m = re.match(r"\A---\n(.*?)\n---", text, re.DOTALL)
    if not m:
        return None, None
    name = desc = None
    for line in m.group(1).splitlines():
        if line.startswith("name:") and name is None:
            name = line.split(":", 1)[1].strip() or None
        if line.startswith("description:") and desc is None:
            desc = line.split(":", 1)[1].strip() or None
    return name, desc

def sync(_):
    if not SKILLS_DIR.is_dir():
        refuse("scan", f"a skills directory at {SKILLS_DIR}", "not a directory",
               "restore .claude/skills/; an unperformed scan is not an empty result")
    con = con_(write=True)
    seen = set()
    for d in sorted(p for p in SKILLS_DIR.iterdir() if p.is_dir()):
        meta = frontmatter(d / "SKILL.md")
        if meta is None:
            print(f"UNRESOLVED (no readable SKILL.md, not registered): {d.name}")
            continue
        name, desc = meta
        name = name or d.name
        seen.add(name)
        row = con.execute("SELECT status FROM skills WHERE name=?",
                          (name,)).fetchone()
        if row is None:
            con.execute("INSERT INTO skills(name, kind, path, description,"
                        " status, created_ts, updated_ts) VALUES (?,?,?,?,?,?,?)",
                        (name, "project", str(d), desc, "active", stamp(), stamp()))
            print(f"new: {name}")
        else:
            if row[0] == "wished":
                print(f"wish fulfilled: {name}")
            con.execute("UPDATE skills SET path=?, description=?, updated_ts=?,"
                        " status=CASE WHEN status IN ('missing','wished')"
                        " THEN 'active' ELSE status END WHERE name=?",
                        (str(d), desc, stamp(), name))
    gone = [r[0] for r in con.execute(
        "SELECT name FROM skills WHERE path IS NOT NULL"
        " AND status NOT IN ('missing','retired','wished')") if r[0] not in seen]
    for name in gone:
        con.execute("UPDATE skills SET status='missing', updated_ts=?"
                    " WHERE name=?", (stamp(), name))
        print(f"MISSING (dir gone, row kept): {name}")
    con.commit()
    n = con.execute("SELECT COUNT(*) FROM skills").fetchone()[0]
    print(f"synced: {len(seen)} on disk, {n} rows total")

def add(argv):
    if len(argv) < 2:
        refuse("arguments", "add NAME DESCRIPTION", " ".join(argv) or "nothing",
               "name the skill and describe it")
    name, desc = argv[0], argv[1]
    kind = next((a.split("=", 1)[1] for a in argv if a.startswith("--kind=")),
                "project")
    status = next((a.split("=", 1)[1] for a in argv
                   if a.startswith("--status=")), "draft")
    if kind not in KINDS:
        refuse("kind", f"a kind in {KINDS}", repr(kind), "name one of them with --kind=")
    con = con_(write=True)
    if con.execute("SELECT 1 FROM skills WHERE name=?", (name,)).fetchone():
        refuse("exists", "a name with no row", f"{name!r} already has one",
               "`note` or `sync` updates it")
    con.execute("INSERT INTO skills(name, kind, description, status,"
                " created_ts, updated_ts) VALUES (?,?,?,?,?,?)",
                (name, kind, desc, status, stamp(), stamp()))
    con.commit()
    print(f"added {name} [{kind}/{status}]")

def list_(argv):
    status = next((a.split("=", 1)[1] for a in argv
                   if a.startswith("--status=")), None)
    with closing(con_()) as con:
        q = ("SELECT name, kind, status, COALESCE(description,'(no description)')"
             " FROM skills")
        rows = con.execute(q + (" WHERE status=?" if status else "")
                           + " ORDER BY kind, name",
                           (status,) if status else ()).fetchall()
        if not rows:
            print("no rows" + (f" with status={status}" if status else "")
                  + " — run `sync` first if the table should not be empty")
            return
        for name, kind, st, desc in rows:
            print(f"{name:<18} {kind:<10} {st:<8} {desc[:70]}")

def show(argv):
    if not argv:
        refuse("arguments", "show NAME", "no name", "name the skill")
    with closing(con_()) as con:
        row = con.execute("SELECT name, kind, path, description, status,"
                          " created_ts, updated_ts, retire_reason, notes"
                          " FROM skills WHERE name=?", (argv[0],)).fetchone()
        if row is None:
            refuse("skill", "a registered skill", f"no row for {argv[0]!r}",
                   "`list` shows rows; `add` or `wish` create them")
        ev = con.execute("SELECT ts, event, COALESCE(detail,'') FROM skill_events"
                         " WHERE skill=? ORDER BY ts", (argv[0],)).fetchall()
        for k, v in zip(("name", "kind", "path", "description", "status",
                         "created", "updated", "retire_reason", "notes"), row,
                        strict=True):
            if v is not None:
                print(f"{k}: {v}")
        print(f"events: {len(ev)}")
        for ts, event, detail in ev[-10:]:
            print(f"  {ts} {event:<8} {detail[:60]}")

def note(argv):
    if len(argv) < 2:
        refuse("arguments", "note NAME TEXT", " ".join(argv) or "nothing",
               "name the skill and the note")
    con = con_(write=True)
    if con.execute("UPDATE skills SET notes=?, updated_ts=? WHERE name=?",
                   (argv[1], stamp(), argv[0])).rowcount == 0:
        refuse("skill", "a registered skill", f"no row for {argv[0]!r}", "`list` shows rows")
    con.commit()
    print("noted")

def retire(argv):
    if len(argv) < 2:
        refuse("arguments", "retire NAME REASON", " ".join(argv) or "nothing",
               "give the reason; nothing leaves without one")
    con = con_(write=True)
    if con.execute("UPDATE skills SET status='retired', retire_reason=?,"
                   " updated_ts=? WHERE name=?",
                   (argv[1], stamp(), argv[0])).rowcount == 0:
        refuse("skill", "a registered skill", f"no row for {argv[0]!r}", "`list` shows rows")
    con.commit()
    print(f"retired {argv[0]} (row kept, reason recorded)")

def _event(kind):
    def go(argv):
        if not argv or (kind != "invoked" and len(argv) < 2):
            need = "NAME [DETAIL]" if kind == "invoked" else "NAME DETAIL"
            refuse("arguments", need, " ".join(argv) or "nothing",
                   "give the event its story; a count without one wears a claim's clothes")
        agent = identity()
        name = argv[0]
        detail = redact(argv[1]) if len(argv) > 1 else None
        con = con_(write=True)
        known = con.execute("SELECT status FROM skills WHERE name=?",
                            (name,)).fetchone()
        if not known and kind != "wish":
            refuse("skill", "a registered skill", f"no row for {name!r}",
                   "`wish` for a skill that should exist; `sync` first for one on disk")
        # A row whose status is still 'wished' is accumulated demand, not an
        # existing skill: wishing it again ranks it higher.
        if known and kind == "wish" and known[0] != "wished":
            refuse("exists", "a wish for an absent skill", f"{name!r} already exists",
                   "log-miss if it existed and was not used")
        if not known:
            con.execute("INSERT INTO skills(name, kind, description, status,"
                        " created_ts, updated_ts) VALUES (?,?,?,?,?,?)",
                        (name, "project", detail, "wished", stamp(), stamp()))
        con.execute("INSERT INTO skill_events(ts, session, agent, skill,"
                    " event, detail) VALUES (?,?,?,?,?,?)",
                    (stamp(), session("unrecorded"),
                     agent, name, kind, detail))
        con.commit()
        print(f"{kind}: {name}")
    return go

def report(_):
    with closing(con_()) as con:
        rows = con.execute("""
            SELECT s.name, s.status,
              SUM(CASE WHEN e.event='invoked' THEN 1 ELSE 0 END),
              SUM(CASE WHEN e.event='failed' THEN 1 ELSE 0 END),
              SUM(CASE WHEN e.event='miss' THEN 1 ELSE 0 END)
            FROM skills s LEFT JOIN skill_events e ON e.skill=s.name
            WHERE s.status != 'wished' GROUP BY s.name ORDER BY s.name""").fetchall()
        wishes = con.execute("""SELECT s.name, COUNT(e.id),
              COALESCE(MAX(e.detail),'') FROM skills s
            LEFT JOIN skill_events e ON e.skill=s.name AND e.event='wish'
            WHERE s.status='wished' GROUP BY s.name ORDER BY 2 DESC""").fetchall()
        misses = con.execute("SELECT COUNT(*) FROM skill_events WHERE event='miss'").fetchone()[0]
    print(f"{'skill':<18} {'status':<8} {'uses':>4} {'fails':>5} {'misses':>6}")
    for name, st, u, f, m in rows:
        print(f"{name:<18} {st:<8} {u or 0:>4} {f or 0:>5} {m or 0:>6}")
    if wishes:
        print("wished (demand, ranked):")
        for name, n, detail in wishes:
            print(f"  {n:>3}x {name:<18} {detail[:55]}")
    if misses == 0:
        print("misses: 0 logged — a declared zero says looked-and-found-none"
              " only while sessions actually declare them")


def main():
    cmds = {"sync": sync, "add": add, "list": list_, "show": show,
            "note": note, "retire": retire, "log-use": _event("invoked"),
            "log-fail": _event("failed"), "log-miss": _event("miss"),
            "wish": _event("wish"), "report": report}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        refuse("arguments", "one of " + "|".join(cmds), " ".join(sys.argv[1:]) or "no verb",
               "name a verb; the module doc has details")
    cmds[sys.argv[1]](sys.argv[2:])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Refusal as refusal:
        raise SystemExit(emit(refusal)) from None
    except (OSError, sqlite3.Error) as error:
        raise SystemExit(emit(instrument.store_failure(db_path(), error, "restore or retry the skills store"))) from None
