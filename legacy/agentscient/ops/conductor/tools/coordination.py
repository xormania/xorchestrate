#!/usr/bin/env python3
"""The coordination core: participants, assignments, messages, evidence.

Contract: ops/conductor/coordination-lever.md. Inherits
ops/conductor/instrument-conventions.md (the refusal wire, the four exit
classes, identity for writes) and runs on the state engine (machine.py):
every row lives in state.db, never in a file.

The defect this module exists to close: the engine's holder is the agent
string (machine.py compares row["holder"] == agent), and one host binds
one WF_AGENT for every session on it, so three sessions of the same
model satisfy each other's holder checks. Here the participant, keyed on
the harness session id, is the authority; the agent string is
attribution only. Authority is checked inside these functions, never in
a hook: the hook engine cannot see MCP tool calls, so a check that lived
there would not run when the MCP projection calls the same function.

    coordination.py bind --roles R[,R] [--run R-n] [--harness H] [--endpoint E] [--probe TEXT]
    coordination.py bind-for --session ID --roles R[,R] --for A-n --capability-digest SHA256 [--harness H]
    coordination.py grant PT-n --roles R[,R]
    coordination.py revoke PT-n --roles R[,R]
    coordination.py who | participants [--role R]
    coordination.py draft TITLE --outcome TEXT [--requirement REF] [--revision SHA]
                              [--depends A-n,..] [--duty TEXT] [--scope TEXT] [--inputs REF]
    coordination.py offer A-n --to WHO --generation N
    coordination.py claim A-n --branch B --generation N [--ttl MIN]
    coordination.py release A-n --why TEXT --generation N
    coordination.py reassign A-n --to WHO --why TEXT --generation N
    coordination.py submit A-n --evidence READING --revision SHA --generation N
    coordination.py verify A-n --candidate SHA --evidence READING --generation N
    coordination.py return A-n --findings REF --generation N
    coordination.py accept A-n --evidence READING --generation N [--exception TEXT]
    coordination.py reopen A-n --revision SHA --why TEXT --generation N
    coordination.py supersede A-n --by A-m --generation N
    coordination.py assignments [--state S] [--to WHO] [--mine] | show A-n|M-n
    coordination.py send --to WHO --purpose P --body TEXT [--assignment A-n]
                         [--decision DC-n] [--reply-to M-n] [--artifact REF] [--key K]
    coordination.py deliver M-n --ref REF | ack M-n | resolve M-n --evidence READING
    coordination.py withdraw M-n --why TEXT | inbox [--unresolved] | outstanding [--to WHO]
    coordination.py undelivered [--to WHO]
    coordination.py route --take stdout|native [--member SM-n] | route --release | routes
    coordination.py attempt M-n --route R --generation G | attempts M-n
    coordination.py attempted AT-n --outcome O [--ref REF] [--error TEXT]
    coordination.py observe A-n --observation TEXT --revision SHA --generation N
                            [--stale] [--execution REC] [--artifact REF]
                            [--requirement REF] [--environment TEXT]
    coordination.py observations A-n [--all]
    coordination.py ask --question TEXT --alternatives TEXT [--assignment A-n]
    coordination.py answer DC-n [--counter TEXT] [--unknowns TEXT] [--consequence TEXT]
    coordination.py decide DC-n --choice TEXT --rationale TEXT
    coordination.py decision DC-n | decisions [--open]
    coordination.py reserve NAME --for A-n [--ttl MIN] | free NAME --why TEXT | resources
    coordination.py coverage [--requirement REF] | brief
    coordination.py entry [--role R]

In an owner's terminal (sessions-lever.md, the owner's terminals) bind
also takes --run R-n, the run it joins, refused `absent` when it names
none; and every verb takes --profile PATH, the hook profile read in
place of AGENTSCIENT_HOOK_PROFILE.

Reads need no identity and no participant and never create the store.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sqlite3
import sys

# The shared instrument module sits beside this file; its directory goes
# on the path so the import resolves however this file was loaded: as
# the script, by path from another tool, or as a copy a test staged
# with its siblings.
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
try:
    import instrument
    import machine
    from instrument import (
        EXIT_DONE,
        Refusal,
        capability_digest,
        emit,
        identity,
        now,
        parse_stamp,
        refuse,
        session,
        stamp,
        state_dir,
    )
except ModuleNotFoundError:
    print(
        "tooling: expected instrument.py and machine.py beside this tool; found absent; "
        "needed restore ops/conductor/tools/instrument.py and machine.py beside it",
        file=sys.stderr,
    )
    raise SystemExit(2) from None

LOCK = "deliverable.json"
ROLES = ("lead", "coordinator", "dispatcher", "worker", "verifier")
ENTRY = {
    "lead": (
        "decide what is asked, accept what the evidence carries",
        (
            "read coverage: requirement, candidate, examination, acceptance or the gap",
            "decide an open question from its own row, with the alternatives it names",
            ("accept only what the gate allows, and record a permitted exception "
             "in the acceptance rather than leaving it unsaid"),
        ),
        (
            "coverage",
            "decisions --open",
            "decide DC-n --choice TEXT --rationale TEXT",
            "accept A-n --evidence READING --generation N",
        ),
    ),
    "coordinator": (
        "place the work and clear what blocks it",
        (
            "read the open assignments and the resources they contend for",
            "offer work to a participant, and reassign what a blocked holder cannot move",
            "clear a dependency before it becomes a stall, and say so in the row",
        ),
        (
            "assignments --state open",
            "resources",
            "offer A-n --to WHO --generation N",
            "reassign A-n --to WHO --why TEXT --generation N",
        ),
    ),
    "dispatcher": (
        "launch against held work and reconcile what is uncertain",
        (
            "read what is outstanding before launching anything",
            "launch against an assignment a participant holds, never against a title",
            "reconcile a launch whose outcome is uncertain rather than launching again",
        ),
        (
            "outstanding",
            "assignments --state held",
            ("observe A-n --observation TEXT --revision SHA --generation N "
             "--execution REC"),
        ),
    ),
    "worker": (
        "recover your obligations, then move the work you hold",
        (
            ("read the brief: what you hold, what runs against it, what is owed "
             "both ways, what is open, and whether your evidence still applies"),
            "claim what is offered on a branch the deliverable lock names",
            "observe what the work measures, and submit the candidate with its revision",
            "ask rather than guess; a question is a row, not a message in a transcript",
        ),
        (
            "brief",
            "claim A-n --branch BRANCH --generation N",
            "observe A-n --observation TEXT --revision SHA --generation N",
            "submit A-n --evidence READING --revision SHA --generation N",
            "ask --question TEXT --alternatives TEXT",
        ),
    ),
    "verifier": (
        "examine a candidate against the requirement's own words",
        (
            "read coverage for the requirement, not for the title",
            "examine the candidate you did not produce; a role label is not independence",
            "observe what you found, then verify it or return it with the findings",
        ),
        (
            "coverage --requirement REF",
            "observations A-n --all",
            "verify A-n --candidate SHA --evidence READING --generation N",
            "return A-n --findings REF --generation N",
        ),
    ),
}
PURPOSES = ("inform", "request", "finding")
OBLIGATIONS = ("request", "finding")
# Slice 2 of sessions-lever.md: one live route per participant, a delivery
# attempt reserved before each native submission and settled after it.
ROUTES = ("stdout", "native")
OUTCOMES = ("written", "accepted", "unsupported", "failed", "uncertain")
ATTEMPT_TTL = 60
ATTEMPTS_OUTSTANDING = 4
ID = re.compile(r"^(PT|DC|AT|A|M|E|R)-(\d+)$")
READING = re.compile(
    r"^(?:[0-9a-f]{7,40}|#\d+|\d{8}T\d{6}Z-[\w-]+|P-[\w-]+)$", re.IGNORECASE
)

T = machine.Transition
ASSIGNMENT = machine.Machine(
    name="assignment",
    version=2,
    initial="drafted",
    places=("drafted", "offered", "held", "submitted", "verified", "accepted", "superseded"),
    transitions=(
        T("offer", "drafted", "offered", "any", "none", "offered", "keep"),
        T("address", "offered", "offered", "any", "required", "offered", "clear"),
        T("claim", "offered", "held", "any", "none", "taken", "take"),
        T("release", "held", "offered", "holder-or-lapsed", "required", "place", "clear"),
        T("reassign", "held", "offered", "any", "required", "place", "clear"),
        T("submit", "held", "submitted", "any", "required", "place", "keep"),
        T("verify", "submitted", "verified", "any", "required", "place", "keep"),
        T("return", "submitted", "held", "any", "required", "place", "keep"),
        T("accept", "verified", "accepted", "any", "required", "place", "keep"),
        T("reopen", "verified", "held", "any", "required", "place", "keep"),
        T("supersede", "offered", "superseded", "any", "required", "place", "keep"),
    ),
    ttl_s=8 * 3600,
)
MESSAGE = machine.Machine(
    name="message",
    version=1,
    initial="open",
    places=("open", "resolved", "withdrawn"),
    transitions=(
        T("resolve", "open", "resolved", "any", "required", "place", "keep"),
        T("withdraw", "open", "withdrawn", "any", "required", "place", "keep"),
    ),
    ttl_s=8 * 3600,
)
RESOURCE = machine.Machine(
    name="resource",
    version=1,
    initial="free",
    places=("free", "held"),
    transitions=(
        T("reserve", "free", "held", "any", "none", "taken", "take"),
        T("free", "held", "free", "holder-or-lapsed", "required", "place", "clear"),
    ),
    ttl_s=2 * 3600,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS participants(
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL UNIQUE,
    agent TEXT NOT NULL, harness TEXT, roles TEXT NOT NULL, endpoint TEXT,
    probe TEXT, bound_at TEXT NOT NULL, bound_by INTEGER, bound_for INTEGER,
    granted_by INTEGER, granted_at TEXT, revoked_by INTEGER, revoked_at TEXT);
CREATE TABLE IF NOT EXISTS assignments(
    id INTEGER PRIMARY KEY, title TEXT NOT NULL, outcome TEXT NOT NULL,
    requirement TEXT, scope TEXT, inputs TEXT, recipient TEXT, depends TEXT,
    revision TEXT, generation INTEGER NOT NULL, duty TEXT, thread INTEGER,
    branch TEXT, holder INTEGER, submitted_by INTEGER, verified_by INTEGER,
    accepted_by INTEGER, opened_by INTEGER NOT NULL, opened_at TEXT NOT NULL,
    superseded_by INTEGER);
CREATE TABLE IF NOT EXISTS messages(
    id INTEGER PRIMARY KEY, sender INTEGER NOT NULL, recipient TEXT NOT NULL,
    purpose TEXT NOT NULL, body TEXT NOT NULL, artifact TEXT, assignment INTEGER,
    decision INTEGER, reply_to INTEGER, queued_at TEXT NOT NULL,
    delivered_at TEXT, delivery_ref TEXT, acked_at TEXT, acked_by INTEGER);
CREATE TABLE IF NOT EXISTS observations(
    id INTEGER PRIMARY KEY AUTOINCREMENT, assignment INTEGER NOT NULL,
    producer INTEGER NOT NULL, requirement TEXT, execution TEXT,
    revision TEXT NOT NULL, environment TEXT, observation TEXT NOT NULL,
    artifact TEXT, applicability TEXT NOT NULL, stale_reason TEXT,
    at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS decisions(
    id INTEGER PRIMARY KEY AUTOINCREMENT, asked_by INTEGER NOT NULL,
    question TEXT NOT NULL, alternatives TEXT NOT NULL, assignment INTEGER,
    counter TEXT, unknowns TEXT, consequence TEXT, choice TEXT, rationale TEXT,
    decided_by INTEGER, decided_at TEXT, asked_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS resources(
    id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, assignment INTEGER,
    holder INTEGER, note TEXT);
"""
# Additive, and outside STORE_TABLES on purpose: a store populated before
# slice 2 still reads, and its first writer adds these with its rows intact.
DELIVERY_SCHEMA = """
CREATE TABLE IF NOT EXISTS delivery_routes(
    participant INTEGER PRIMARY KEY, route TEXT NOT NULL, member INTEGER,
    pid INTEGER NOT NULL, start_ticks TEXT NOT NULL, since TEXT NOT NULL,
    last_pass_at TEXT);
CREATE TABLE IF NOT EXISTS delivery_attempts(
    id INTEGER PRIMARY KEY AUTOINCREMENT, message INTEGER NOT NULL,
    participant INTEGER NOT NULL, generation INTEGER NOT NULL, route TEXT NOT NULL,
    reserved_at TEXT NOT NULL, submitted_at TEXT, outcome TEXT, native_ref TEXT,
    error TEXT);
CREATE TABLE IF NOT EXISTS send_requests(
    participant INTEGER NOT NULL, key TEXT NOT NULL, digest TEXT NOT NULL,
    message INTEGER NOT NULL, at TEXT NOT NULL, PRIMARY KEY (participant, key));
"""


# -- arguments ---------------------------------------------------------------

def parse(rest, count, *, required=(), optional=(), flags=()):
    """Positionals and --options; anything else refuses `arguments`.

    The acting participant is never among them: it is resolved from the
    session, so a supplied one falls through to the unknown-option
    refusal and confers nothing (objective §7).
    """
    positionals, options = [], {}
    tokens = list(rest)
    while tokens:
        token = tokens.pop(0)
        if token.startswith("--"):
            name = token[2:]
            if name in flags:
                options[name] = True
                continue
            if name not in required and name not in optional:
                refuse("arguments", "a known option", token, usage())
            if not tokens:
                refuse("arguments", f"a value after --{name}", "end of arguments", usage())
            options[name] = tokens.pop(0)
        else:
            positionals.append(token)
    if len(positionals) != count:
        refuse("arguments", f"{count} positional argument(s)", str(len(positionals)), usage())
    for name in required:
        if name not in options:
            refuse("arguments", f"--{name}", "absent", usage())
    return positionals, options


def usage() -> str:
    return "see the verbs in the module doc of coordination.py"


def parse_id(text: str, kinds=("PT", "A", "M", "E", "DC", "R")) -> tuple[str, int]:
    match = ID.match(text)
    kinds = (kinds,) if isinstance(kinds, str) else tuple(kinds)
    if match is None or match.group(1) not in kinds:
        wanted = " or ".join(f"{k}-1" for k in kinds)
        refuse("id", f"an id like {wanted}", text, "an id printed by a listing verb")
    return match.group(1), int(match.group(2))


def reading(value: str, what: str) -> str:
    if READING.match(value) is None:
        refuse("evidence", f"{what} as a sha, #N, a dispatch record id or a P- row",
               value, "name the reading the move rests on")
    return value


def roles_of(text: str) -> str:
    named = [part.strip() for part in text.split(",") if part.strip()]
    if not named:
        refuse("arguments", "at least one role", text or "empty", "one of " + ", ".join(ROLES))
    for role in named:
        if role not in ROLES:
            refuse("arguments", "a known role", role, "one of " + ", ".join(ROLES))
    return ",".join(named)


# -- the store ---------------------------------------------------------------

def ensure_columns(conn) -> None:
    """A store written before the dispatched-worker binding or the grant
    chain existed gains their columns here; ALTER ... ADD COLUMN is
    idempotent under the guard and costs one PRAGMA on every open."""
    have = {row["name"] for row in conn.execute("PRAGMA table_info(participants)")}
    for name, kind in (("bound_by", "INTEGER"), ("bound_for", "INTEGER"),
                       ("granted_by", "INTEGER"), ("granted_at", "TEXT"),
                       ("revoked_by", "INTEGER"), ("revoked_at", "TEXT"),
                       ("owner_grant", "INTEGER"), ("capability_digest", "TEXT"),
                       ("terminal_run", "INTEGER")):
        if name not in have:
            conn.execute(f"ALTER TABLE participants ADD COLUMN {name} {kind}")


def writer():
    conn = machine.connect(create=True)
    conn.executescript(SCHEMA)
    conn.executescript(DELIVERY_SCHEMA)
    ensure_columns(conn)
    for declared in (ASSIGNMENT, MESSAGE, RESOURCE):
        machine.declare(conn, declared)
    return conn


STORE_TABLES = tuple(sorted(set(
    re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", SCHEMA)
    + re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", machine.SCHEMA))))


def reader():
    """The store for a read, or None when there is nothing to read.

    Read-only by the connection itself, and migrating nothing. It used to
    connect read-write, run `executescript(SCHEMA)` and then
    `ensure_columns`, so every read of this instrument created tables and
    added columns on the way past — an unidentified writer of exactly the
    reach of a read, because reads need no identity (codex-beb7b6#3,
    grok-7bbe10#2). A store this module cannot read is a refusal, class
    `state`, and never a store built to order: "could not look" is a
    marker, not an empty answer (docs/wire.md, absence discipline).
    """
    path = machine.store_path()
    if not path.is_file():
        return None
    conn = instrument.connect_store(path, timeout=machine.BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {machine.BUSY_TIMEOUT_MS}")
    # `sqlite_schema` is the name SQLite 3.33 gave the table it has always
    # had; the older name carries a word the lane's literals verb forbids
    # in code, and this is the same table under the name that does not.
    held = {row["name"] for row in
            conn.execute("SELECT name FROM sqlite_schema WHERE type = 'table'")}
    missing = [name for name in STORE_TABLES if name not in held]
    if missing:
        conn.close()
        refuse("state", f"the coordination schema in the store at {path}",
               "a store with no tables at all" if not held
               else f"a store without {', '.join(missing)}",
               "a writing verb creates the schema and migrates it; "
               "a read does neither")
    return conn


# -- participants ------------------------------------------------------------

def _authenticate(row):
    expected = row["capability_digest"] if "capability_digest" in row.keys() else None  # noqa: SIM118 - sqlite Row
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        refuse("capability", "an enrolled participant capability",
               "a legacy participant without a credential",
               "the owner must provision this retained participant during offline cutover")
    if not hmac.compare_digest(expected, capability_digest()):
        refuse("capability", "the capability enrolled for this participant",
               "a different credential",
               "keep this session's own private profile and participant binding")


def _install_capability(conn, pid, digest):
    """Enrollment under the caller's existing exclusive transaction.

    New binds use this after their authority checks. Legacy enrollment is an
    explicit operator maintenance operation, never a CORE/CLI/MCP verb and
    never an automatic upgrade based on a replayable session id.
    """
    if not conn.in_transaction:
        refuse("state", "an exclusive enrollment transaction", "none",
               "hold BEGIN IMMEDIATE over the complete enrollment")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        refuse("capability", "a SHA256 capability digest", "invalid digest",
               "hash the private 256-bit hexadecimal capability using ASCII")
    row = participant_row(conn, pid)
    if row["capability_digest"] is not None:
        refuse("capability", "a participant not yet enrolled", "already enrolled",
               "reuse the existing credential; enrollment never replaces it")
    if conn.execute("SELECT 1 FROM participants WHERE capability_digest = ?", (digest,)).fetchone():
        refuse("capability", "a distinct capability for each participant", "already enrolled",
               "mint a fresh private capability for the new participant")
    conn.execute("UPDATE participants SET capability_digest = ? WHERE id = ?", (digest, pid))


def bound_participant(conn, *, participant=None):
    """Resolve a correlation id and authenticate its private capability.

    An optional participant is an expected binding, not caller authority.
    The MCP config uses it without reading the public participant catalogue.
    """
    key = session("")
    if not key:
        refuse("session", "a session id in the environment", "unset",
               "run under a harness that sets CLAUDE_CODE_SESSION_ID, then bind")
    expected = None
    if participant is not None:
        _, expected = parse_id(participant, "PT")
        if conn is None:
            refuse("participant", "the participant issued to this call", "no store",
                   "bind the participant before issuing its configuration")
        participant_row(conn, expected)
    row = None
    if conn is not None:
        row = conn.execute(
            "SELECT * FROM participants WHERE session_id = ?", (key,)
        ).fetchone()
    if row is None:
        if expected is not None:
            refuse("state", "the participant issued to this call", "a different session",
                   "use this worker's own startup binding")
        refuse("participant", "a participant bound to this session", "no bound row",
               "bind --roles ROLE[,ROLE] first")
    if expected is not None and row["id"] != expected:
        refuse("state", "the participant issued to this call", "a different participant",
               "use this worker's own startup binding")
    _authenticate(row)
    return row


def maybe_participant(conn):
    """The participant of this session, or None, without refusing.

    Every other read resolves through bound_participant, which refuses a
    session that has not bound. `entry` is the read a session takes
    before it can bind, so it asks this instead (coordination-roles.md
    §The entry points).
    """
    key = session("")
    if not key or conn is None:
        return None
    row = conn.execute(
        "SELECT * FROM participants WHERE session_id = ?", (key,)
    ).fetchone()
    if row is not None:
        _authenticate(row)
    return row


def same_session(conn, part):
    """The row a caller passes is an id, and everything else is read.

    The first repair compared the passed mapping's own `session_id`
    against the environment and read nothing else, which moved the
    boundary rather than closing it: a mapping carrying this session's id
    beside another participant's id and roles passed, and the function
    then acted on the roles it had been handed (Codex 834580#2, Daybreak
    1833de#3 against b1d868b0). So the id is all a caller supplies. The
    row is loaded from `participants` by that id, its `session_id` must
    be this process's, and the row this returns — never the argument — is
    what the function reads from there on.

    The argument shape stays, because passing the resolved row is what
    keeps the CLI and the MCP server thin. What it carries is a claim
    about which participant is acting, and the store answers it.

    `None` is the two declared cases and not a caller's choice: bind has
    no row yet, and a read with no store has nothing to resolve from.
    """
    if part is None:
        return None
    key = session("")
    row = None
    if conn is not None:
        row = conn.execute(
            "SELECT * FROM participants WHERE id = ?", (part["id"],)
        ).fetchone()
    if row is None or row["session_id"] != key:
        stood = (f"PT-{part['id']}, bound to a different session"
                 if row is not None else
                 f"PT-{part['id']}, which the store does not hold")
        refuse("participant", "the participant bound to the calling session",
               stood,
               "act as the participant this process is bound to; "
               "the caller does not choose it")
    _authenticate(row)
    return row


def actor(part) -> str:
    """What the engine is told the mover is.

    `subjects.holder` is an opaque string the engine compares for
    equality and nothing else, so what it carries is each machine's own
    decision (`machine.py:253-264`, idea I-034). These machines carry
    the participant: the agent string is one value per host, so a holder
    that changed it was refused and a stranger that shared it was not
    (Grok #4 against 05b8aebe). The marking's attribution column takes
    the same value; the agent string behind it is on the participant's
    own row, recorded at bind.
    """
    return f"PT-{part['id']}"


def require_role(part, roles) -> None:
    held = {role for role in part["roles"].split(",") if role}
    if held & set(roles):
        return
    refuse("role", "a participant holding " + " or ".join(roles),
           f"PT-{part['id']} holding {part['roles']}",
           "bind that role, or hand the verb to a participant that holds it")


def participant_row(conn, pid: int):
    row = conn.execute("SELECT * FROM participants WHERE id = ?", (pid,)).fetchone()
    if row is None:
        refuse("participant", "a participant id in the store", f"PT-{pid}",
               "an id printed by participants")
    return row


def recipient_of(conn, text: str) -> str:
    """A participant id or a role name; anything else refuses."""
    if text in ROLES:
        return text
    _, number = parse_id(text, "PT")
    participant_row(conn, number)
    return f"PT-{number}"


def addressed(part, recipient: str) -> bool:
    if recipient == f"PT-{part['id']}":
        return True
    return recipient in {role for role in part["roles"].split(",") if role}


# -- rows --------------------------------------------------------------------

def assignment_row(conn, aid: int):
    row = None
    if conn is not None:
        row = conn.execute(
            "SELECT a.*, s.place, s.holder AS agent_holder, s.held_since, s.ttl_s "
            "FROM assignments a JOIN subjects s ON s.id = a.id WHERE a.id = ?", (aid,)
        ).fetchone()
    if row is None:
        refuse("unknown", "an assignment id in the store", f"A-{aid}",
               "an id printed by assignments")
    return row


def message_row(conn, mid: int):
    row = None
    if conn is not None:
        row = conn.execute(
            "SELECT m.*, s.place FROM messages m JOIN subjects s ON s.id = m.id "
            "WHERE m.id = ?", (mid,)
        ).fetchone()
    if row is None:
        refuse("unknown", "a message id in the store", f"M-{mid}",
               "an id printed by inbox")
    return row


def decision_row(conn, did: int):
    row = None
    if conn is not None:
        row = conn.execute("SELECT * FROM decisions WHERE id = ?", (did,)).fetchone()
    if row is None:
        refuse("unknown", "a decision id in the store", f"DC-{did}",
               "an id printed by decisions")
    return row


def advance(conn, aid: int, transition: str, part, *, evidence=None, note=None,
            ttl_s=None, gate=None):
    """Every move on an assignment bumps its generation, inside the move.

    The generation is what refuses a caller acting on a view the
    assignment has moved past, and it only does that if every move moves
    it. Grok reproduced the hole against b1d868b0: `return` and `submit`
    both left it where it was, so a verifier that began a return against
    one submission had it land on the next one. The Daybreak audit found
    the same shape in `supersede`, which stated no generation at all.
    Ruled by the Lead, 2026-09-16: one bump a move, so a delayed verb
    refuses on the gate it already has.

    The bump runs last inside the caller's own gate, so it is in the
    engine's `BEGIN IMMEDIATE` with the rest of the move and a refusal
    from the gate rolls it back with everything else.
    """

    def bump(subject):
        if gate is not None:
            gate(subject)
        conn.execute(
            "UPDATE assignments SET generation = generation + 1 WHERE id = ?", (aid,)
        )

    return machine.move(conn, ASSIGNMENT, aid, transition, actor(part),
                        evidence=evidence, note=note, ttl_s=ttl_s, gate=bump)


def require_generation(row, stated: str) -> int:
    """The stated generation against the row's, before the engine sees the
    place: a reassignment returns the subject to a place it held before,
    so the place alone cannot refuse a stale holder (AC-03)."""
    if not stated.isdigit():
        refuse("arguments", "--generation as a whole number", stated,
               "the generation printed by show")
    if int(stated) != row["generation"]:
        refuse("generation", f"generation {row['generation']} for A-{row['id']}",
               f"generation {stated}", "read show for the current generation; "
               "the assignment moved on without this holder")
    return int(stated)


def require_holder(part, row) -> None:
    if row["holder"] != part["id"]:
        refuse("holder", f"the holder PT-{row['holder']} of A-{row['id']}",
               f"PT-{part['id']}", "the holder moves it, or it is reassigned first")


def not_the_producer(part, row, what: str) -> None:
    """AC-11: an independent pass is not a role label away."""
    if row["submitted_by"] is not None and row["submitted_by"] == part["id"]:
        refuse("producer", f"a participant other than the producer PT-{row['submitted_by']}",
               f"PT-{part['id']}, which submitted A-{row['id']}",
               f"another participant {what} this candidate")


def lock_branches():
    path = state_dir() / LOCK
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    branches = data.get("branches") if isinstance(data, dict) else None
    return [str(b) for b in branches] if isinstance(branches, list) else None


def require_lock(branch: str) -> None:
    """A claim nobody can find on the board is not a claim
    (handoff-lever.md, the same rule the thread take carries)."""
    named = lock_branches()
    if named is None:
        refuse("deliverable", "the deliverable lock naming the branch", "no lock on this host",
               f"deliverable.py add {branch} --why '<the words>'")
    if branch not in named:
        refuse("deliverable", "a branch the deliverable lock names", branch,
               f"deliverable.py add {branch} --why '<the words>'; the lock holds "
               + ", ".join(named or ["nothing"]))


# -- what a core function returns --------------------------------------------

def participant_dict(row) -> dict:
    result = {
        "participant": f"PT-{row['id']}", "id": row["id"], "roles": row["roles"],
        "agent": row["agent"],
        "harness": row["harness"], "endpoint": row["endpoint"],
        "bound_by": f"PT-{row['bound_by']}" if row["bound_by"] else None,
        "bound_for": f"A-{row['bound_for']}" if row["bound_for"] else None,
        "granted_by": f"PT-{row['granted_by']}" if row["granted_by"] else None,
    }
    if "owner_grant" in row.keys() and row["owner_grant"] is not None:  # noqa: SIM118 (sqlite Row)
        result["owner_grant"] = row["owner_grant"]
    return result


def assignment_dict(row) -> dict:
    return {
        "assignment": f"A-{row['id']}", "id": row["id"], "title": row["title"],
        "outcome": row["outcome"], "place": row["place"],
        "generation": row["generation"], "revision": row["revision"],
        "recipient": row["recipient"], "requirement": row["requirement"],
        "depends": row["depends"], "branch": row["branch"],
        "holder": f"PT-{row['holder']}" if row["holder"] else None,
        "submitted_by": f"PT-{row['submitted_by']}" if row["submitted_by"] else None,
        "verified_by": f"PT-{row['verified_by']}" if row["verified_by"] else None,
        "accepted_by": f"PT-{row['accepted_by']}" if row["accepted_by"] else None,
    }


def message_dict(row) -> dict:
    return {
        "message": f"M-{row['id']}", "id": row["id"], "purpose": row["purpose"],
        "sender": f"PT-{row['sender']}", "recipient": row["recipient"],
        "body": row["body"], "artifact": row["artifact"], "place": row["place"],
        "assignment": f"A-{row['assignment']}" if row["assignment"] else None,
        "queued_at": row["queued_at"], "delivered_at": row["delivered_at"],
        "delivery_ref": row["delivery_ref"], "acked_at": row["acked_at"],
        "acked_by": f"PT-{row['acked_by']}" if row["acked_by"] else None,
    }


def observation_dict(row) -> dict:
    return {
        "observation": f"E-{row['id']}", "id": row["id"],
        "assignment": f"A-{row['assignment']}", "producer": f"PT-{row['producer']}",
        "revision": row["revision"], "execution": row["execution"],
        "requirement": row["requirement"], "environment": row["environment"],
        "text": row["observation"], "artifact": row["artifact"],
        "applicability": row["applicability"], "stale_reason": row["stale_reason"],
    }


def decision_dict(row) -> dict:
    return {
        "decision": f"DC-{row['id']}", "id": row["id"], "question": row["question"],
        "asked_by": f"PT-{row['asked_by']}",
        "alternatives": row["alternatives"], "counter": row["counter"],
        "unknowns": row["unknowns"], "consequence": row["consequence"],
        "choice": row["choice"], "rationale": row["rationale"],
        "decided_by": f"PT-{row['decided_by']}" if row["decided_by"] else None,
        "decided_at": row["decided_at"],
        "assignment": f"A-{row['assignment']}" if row["assignment"] else None,
    }


def named_revision(value, where: str) -> str:
    """A revision is the identity of the candidate the work is against,
    and the empty string is not an identity.

    Every revision gate reads `if row["revision"] and ...`, so a falsy
    column lifts all of them at once: Grok and Codex both submitted
    `--revision ''` against b1d868b0 and then verified a candidate the
    submission had never named. Leaving a revision out and naming an
    empty one are different acts and the second says so.
    """
    text = (value or "").strip()
    if not text:
        refuse("revision", f"the revision {where} is against",
               "an empty string" if value == "" else repr(value),
               "name it; leaving it out where it is optional is a "
               "different thing and reads as one")
    return text


def no_store(**rest) -> dict:
    """Could not look, said as a marker: a read with no store behind it
    returns this and never an empty list dressed as an answer
    (docs/wire.md, absence discipline)."""
    return {"store": "absent", **rest}


# -- the core: one function per verb, keyed by the wire name in CORE ----------

def core_bind(conn, part, *, agent, roles, harness=None,
              endpoint=None, probe=None, run=None) -> dict:
    """This session's own participant, and no other session's.

    `part` is None here by construction: binding is how a session comes
    to have one. The session id is read here and is not an argument —
    taking it as one let a worker process mint participants for sessions
    that do not exist and rewrite a bound lead's agent, endpoint and
    probe (Grok bcfee6#2 against b1d868b0).
    """
    part = same_session(conn, part)
    named = roles_of(roles)
    key = session("").strip()
    if not key:
        refuse("session", "a session id in the environment", "unset",
               "run under a harness that supplies a Claude or Codex session id")
    # The read that decides and the write that acts take the lock
    # together. `writer()` is autocommit, so between the count and the
    # insert two first callers both read zero and both took roles above
    # worker: all three reviewers of round two reproduced two roots in
    # that window against b1d868b0.
    pointer = None
    minted = False
    try:
        with instrument.transaction(conn):
            rid = run_named(conn, run) if run is not None else None
            if rid is not None and instrument.hook_profile() is not None:
                refuse("arguments", "--run only in an owner terminal", "a managed profile",
                       "use the managed member's existing run")
            row = conn.execute(
                "SELECT * FROM participants WHERE session_id = ?", (key,)
            ).fetchone()
            if row is not None:
                _authenticate(row)
                if rid is not None:
                    if row["terminal_run"] != rid:
                        refuse("capability", "the run enrolled for this terminal", "a different or unminted run",
                               "use the existing terminal pointer; join associates other bound participants")
                    profile = instrument.terminal_profile()
                    if profile is None:
                        refuse("capability", "this terminal's existing pointer", "missing profile",
                               "name the pointer through --profile or AGENTSCIENT_HOOK_PROFILE")
                    pointer = instrument.terminal_pointer(run, f"PT-{row['id']}")
                # A re-bind is how a session refreshes where it can be reached
                # and what its last probe said. It is not how it changes what
                # it may do: Codex reproduced a bound worker re-binding itself
                # as a lead and drafting its own work against 05b8aebe.
                if set(named.split(",")) != set(row["roles"].split(",")):
                    refuse("role", f"the roles PT-{row['id']} holds, {row['roles']}", named,
                           "a re-bind refreshes the endpoint and the probe; "
                           "a lead grants roles with grant")
                conn.execute(
                    "UPDATE participants SET agent = ?, harness = ?, endpoint = ?, "
                    "probe = ?, bound_at = ? WHERE id = ?",
                    (agent, harness, endpoint, probe, stamp(), row["id"]),
                )
                pid = row["id"]
            else:
                bootstrap = conn.execute(
                    "SELECT COUNT(*) AS n FROM participants"
                ).fetchone()["n"] == 0
                if not bootstrap and named != "worker":
                    # Authority is a chain and the store's first participant is
                    # its root. Announcing yourself is worth worker and nothing
                    # else; everything above it is somebody's grant.
                    refuse("role", "worker for a self-bind after the first participant",
                           named,
                           "bind --roles worker, then a lead grants the rest with "
                           "grant PT-n --roles R")
                pid = conn.execute(
                    "INSERT INTO participants(session_id, agent, harness, roles, endpoint, "
                    "probe, bound_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (key, agent, harness, named, endpoint, probe, stamp()),
                ).lastrowid
                if rid is None:
                    _install_capability(conn, pid, capability_digest())
                else:
                    declared = (os.environ.get("AGENTSCIENT_HOOK_PROFILE", "").strip()
                                or "COORDINATION_CAPABILITY" in os.environ)
                    token = instrument.capability_value() if declared else instrument.mint_token()
                    _install_capability(conn, pid, hashlib.sha256(token.encode("ascii")).hexdigest())
                    conn.execute("UPDATE participants SET terminal_run = ? WHERE id = ?", (rid, pid))
                    pointer = instrument.mint_terminal_profile(run, f"PT-{pid}", token)
                    minted = True
    except BaseException:
        if minted:
            instrument.remove_terminal_profile(run, f"PT-{pid}", token)
        raise
    shown = {**participant_dict(participant_row(conn, pid)), "session": key}
    if pointer is not None:
        shown["profile"] = str(pointer)
    return shown


def core_grant(conn, part, *, agent, participant, roles) -> dict:
    """Roles above worker come from a lead, and the row says whose.

    The bootstrap aside, this is the only way a participant comes to hold
    anything but worker, which is what makes authority a chain that can
    be read back rather than a label a caller wrote about itself
    (objective sections 6 and 7; ruled by the Lead, 2026-09-16).
    """
    part = same_session(conn, part)
    require_role(part, ("lead",))
    named = roles_of(roles)
    _, pid = parse_id(participant, "PT")
    row = participant_row(conn, pid)
    held = dict.fromkeys(role for role in row["roles"].split(",") if role)
    held.update(dict.fromkeys(named.split(",")))
    conn.execute(
        "UPDATE participants SET roles = ?, granted_by = ?, granted_at = ? WHERE id = ?",
        (",".join(held), part["id"], stamp(), pid),
    )
    return participant_dict(participant_row(conn, pid))




def core_revoke(conn, part, *, agent, participant, roles) -> dict:
    """A role that can be given can be taken back, by a lead, on the record.

    `grant` is additive and nothing in the table subtracted, so a store
    accumulated authority and never shed it: the trial store stands at
    two leads because the roles were granted for a trial and could not be
    put down afterwards. Found in the live run, not in a review.

    One thing it will not do. A store with no lead in it has nobody who
    can ever grant one again — the chain's root is gone and no verb here
    can restore it — so the count is of the leads that would be left
    after the subtraction, whoever the subject is, the caller included. A
    last lead taking its own lead role down is the same store either way.

    What is left is never nothing: announcing yourself is worth worker,
    so a participant stripped of everything stands where a fresh
    self-bind would stand.
    """
    part = same_session(conn, part)
    require_role(part, ("lead",))
    named = roles_of(roles)
    _, pid = parse_id(participant, "PT")
    row = participant_row(conn, pid)
    held = [role for role in row["roles"].split(",") if role]
    taking = named.split(",")
    absent = [role for role in taking if role not in held]
    if absent:
        # Taking back what was never given is a refusal, not a silent
        # no-op that reads back as success (docs/wire.md, absence
        # discipline).
        refuse("role", f"roles PT-{pid} holds, {row['roles']}", ", ".join(absent),
               "name a role the participant holds; nothing was taken")
    left = [role for role in held if role not in taking] or ["worker"]
    standing = conn.execute("SELECT id, roles FROM participants").fetchall()
    leads = [other["id"] for other in standing
             if "lead" in (left if other["id"] == pid
                           else [role for role in (other["roles"] or "").split(",")
                                 if role])]
    if not leads:
        refuse("role", "a store left holding at least one lead",
               f"no lead at all once PT-{pid} stands at {','.join(left)}",
               "grant lead to another participant first; a store with no "
               "lead has nobody who can ever grant one")
    conn.execute(
        "UPDATE participants SET roles = ?, revoked_by = ?, revoked_at = ? WHERE id = ?",
        (",".join(left), part["id"], stamp(), pid),
    )
    return participant_dict(participant_row(conn, pid))


def core_bind_for(conn, part, *, agent, session_id, roles, assignment,
                  capability_digest=None, harness=None, endpoint=None, probe=None) -> dict:
    """Issue a participant and its claimable work in one engine transition."""
    part = same_session(conn, part)
    require_role(part, ("dispatcher",))
    named = roles_of(roles)
    key = (session_id or "").strip()
    if not key:
        refuse("arguments", "a non-empty session id", repr(session_id),
               "the session id the launcher minted for the worker")
    if set(named.split(",")) - {"worker"}:
        # Minting a worker is a dispatcher's. Minting anything above one
        # is a grant, and a grant is a lead's.
        require_role(part, ("lead",))
    _, aid = parse_id(assignment, "A")
    work = assignment_row(conn, aid)
    issued = []

    def issue():
        # _offer_to runs this under the engine's write lock. Nothing is
        # enrolled unless this assignment can be offered to the new worker.
        live = assignment_row(conn, aid)
        if live["place"] == "offered" and (
                live["holder"] is not None or live["agent_holder"] is not None
                or (live["recipient"] and live["recipient"] not in named.split(","))):
            refuse("recipient", "an unaddressed offer or a role the issued worker holds",
                   f"A-{aid} offered to {live['recipient'] or 'nobody'}, "
                   f"holder {live['holder'] or live['agent_holder'] or 'none'}",
                   "reassign it to the intended role with the current generation, "
                   "or dispatch a different assignment")
        row = conn.execute(
            "SELECT id FROM participants WHERE session_id = ?", (key,)
        ).fetchone()
        if row is not None:
            # It never overwrites: Grok reproduced a bind-for rewriting a
            # bound lead's own row as a worker against 05b8aebe.
            refuse("conflict", f"no participant for session {key}",
                   f"PT-{row['id']}, bound already",
                   "that session is bound; a lead grants roles with grant PT-n --roles R")
        # Roles above worker are a grant, and a grant says who made it
        # and when, the way `grant` does. `bound_by` is not that record:
        # it says who ran the verb, not that anything was conferred. The
        # audit minted a verifier through this verb against b1d868b0 and
        # the store carried no grant at all (codex-1833de#5).
        granted = part["id"] if set(named.split(",")) - {"worker"} else None
        pid = conn.execute(
            "INSERT INTO participants(session_id, agent, harness, roles, endpoint, "
            "probe, bound_at, bound_by, bound_for, granted_by, granted_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (key, agent, harness, named, endpoint, probe, stamp(), part["id"], aid,
             granted, stamp() if granted else None),
        ).lastrowid
        _install_capability(conn, pid, capability_digest)
        issued.append(pid)
        return f"PT-{pid}"

    _offer_to(conn, part, aid=aid, to=issue, generation=str(work["generation"]),
              transition="address" if work["place"] == "offered" else "offer",
              evidence="dispatch handoff", note="issued worker")
    return {**participant_dict(participant_row(conn, issued[0])), "session": key}


def core_who(conn, part) -> dict:
    """A read: with no store it could not look, and says so rather than
    refusing class participant, which would read as having looked."""
    part = same_session(conn, part)
    if conn is None or part is None:
        return no_store(participant=None)
    return {**participant_dict(part), "session": part["session_id"]}


def core_participants(conn, part, *, role=None) -> dict:
    part = same_session(conn, part)
    if conn is None:
        return no_store(participants=[])
    rows = conn.execute("SELECT * FROM participants ORDER BY id").fetchall()
    shown = [r for r in rows if not role or role in r["roles"].split(",")]
    return {"participants": [participant_dict(r) for r in shown]}


def core_draft(conn, part, *, agent, title, outcome, requirement=None, revision=None,
               depends=None, duty=None, scope=None, inputs=None) -> dict:
    part = same_session(conn, part)
    require_role(part, ("lead", "coordinator"))
    if revision is not None:
        revision = named_revision(revision, "the draft")
    sid = machine.create(conn, ASSIGNMENT, actor(part))
    conn.execute(
        "INSERT INTO assignments(id, title, outcome, requirement, scope, inputs, "
        "depends, revision, generation, duty, opened_by, opened_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
        (sid, title, outcome, requirement, scope, inputs, depends, revision,
         duty, part["id"], stamp()),
    )
    return assignment_dict(assignment_row(conn, sid))


def _offer_to(conn, part, *, aid, to, generation, transition, evidence=None, note=None):
    """One recipient update for offers, handoffs and returns to offered.

    A recipient factory may enroll the dispatched participant inside the
    same transaction. The engine owns locking, place checks and rollback;
    advance owns the generation bump. Public operations check authority.
    """
    require_generation(assignment_row(conn, aid), generation)

    def gate(_subject):
        require_generation(assignment_row(conn, aid), generation)
        recipient = recipient_of(conn, to() if callable(to) else to)
        conn.execute("UPDATE assignments SET holder = NULL, recipient = ? WHERE id = ?",
                     (recipient, aid))

    advance(conn, aid, transition, part, note=note, evidence=evidence, gate=gate)
    return assignment_dict(assignment_row(conn, aid))


def core_offer(conn, part, *, agent, assignment, to, generation) -> dict:
    part = same_session(conn, part)
    require_role(part, ("lead", "coordinator"))
    _, aid = parse_id(assignment, "A")
    return _offer_to(conn, part, aid=aid, to=to, generation=generation,
                     transition="offer", note=to)


def core_claim(conn, part, *, agent, assignment, branch, generation, ttl=None) -> dict:
    part = same_session(conn, part)
    _, aid = parse_id(assignment, "A")
    ttl_s = int(ttl) * 60 if str(ttl or "").isdigit() else None
    require_lock(branch)
    row = assignment_row(conn, aid)
    require_generation(row, generation)

    def gate(_subject):
        live = assignment_row(conn, aid)
        require_generation(live, generation)
        if not addressed(part, live["recipient"] or ""):
            refuse("recipient", f"the named recipient {live['recipient'] or 'nobody'} "
                   f"of A-{aid}", f"PT-{part['id']} ({part['roles']})",
                   "the recipient claims it, or it is reassigned first")
        conn.execute(
            "UPDATE assignments SET holder = ?, branch = ? WHERE id = ?",
            (part["id"], branch, aid),
        )

    advance(conn, aid, "claim", part, note=branch, ttl_s=ttl_s, gate=gate)
    return assignment_dict(assignment_row(conn, aid))


def _hand_back(conn, part, *, agent, assignment, why, generation, transition, to=None) -> dict:
    """release and reassign: the hold goes back and the generation bumps."""
    _, aid = parse_id(assignment, "A")
    row = assignment_row(conn, aid)
    require_generation(row, generation)
    if transition == "reassign":
        require_role(part, ("lead", "coordinator"))
        recipient = to
        if row["place"] == "offered":
            transition = "address"
    else:
        held = machine.subject(conn, ASSIGNMENT, aid)
        if row["holder"] != part["id"] and not machine.lapsed(held):
            refuse("holder", f"the holder PT-{row['holder']} of A-{aid}",
                   f"PT-{part['id']}",
                   "the holder releases it, anyone once the lease lapses, "
                   "or a lead reassigns it")
        recipient = row["recipient"]

    return _offer_to(conn, part, aid=aid, to=recipient, generation=generation,
                     transition=transition, evidence=why)


def core_release(conn, part, *, agent, assignment, why, generation) -> dict:
    part = same_session(conn, part)
    return _hand_back(conn, part, agent=agent, assignment=assignment, why=why,
                      generation=generation, transition="release")


def core_reassign(conn, part, *, agent, assignment, to, why, generation) -> dict:
    part = same_session(conn, part)
    return _hand_back(conn, part, agent=agent, assignment=assignment, why=why,
                      generation=generation, transition="reassign", to=to)


def core_submit(conn, part, *, agent, assignment, evidence, revision, generation) -> dict:
    part = same_session(conn, part)
    _, aid = parse_id(assignment, "A")
    evidence = reading(evidence, "the candidate")
    row = assignment_row(conn, aid)
    require_generation(row, generation)
    require_holder(part, row)
    revision = named_revision(revision, "the submission")
    if row["revision"] and revision != row["revision"]:
        refuse("revision", f"the applicable revision {row['revision']} of A-{aid}",
               revision, "submit against the applicable revision, or reopen it")

    def gate(_subject):
        live = assignment_row(conn, aid)
        require_generation(live, generation)
        conn.execute(
            "UPDATE assignments SET submitted_by = ?, revision = ? WHERE id = ?",
            (part["id"], revision, aid),
        )
        # Defence in depth: with observe gated on a named candidate no
        # verb can produce a current row of another revision, but a store
        # written before that gate can hold one. The row is marked, never
        # deleted, and keeps its text and its scope (objective §11).
        conn.execute(
            "UPDATE observations SET applicability = 'stale', stale_reason = ? "
            "WHERE assignment = ? AND applicability = 'current' AND revision != ?",
            (f"the candidate submitted is {revision}, not the revision observed",
             aid, revision),
        )

    advance(conn, aid, "submit", part, evidence=evidence, gate=gate)
    return assignment_dict(assignment_row(conn, aid))


def core_verify(conn, part, *, agent, assignment, candidate, evidence,
                generation) -> dict:
    """Two readings, and they are not the same reading.

    `candidate` is the revision's identity and must be the one the
    assignment stands at; `evidence` is a reading of the verification run
    itself. One option carried both before, so a candidate submitted with
    a P- corpus row or a dispatch record id as its reading could never be
    verified at all (Grok #8 against 05b8aebe). Both go through the
    contract's pinned evidence shapes.
    """
    part = same_session(conn, part)
    _, aid = parse_id(assignment, "A")
    candidate = reading(candidate, "the candidate")
    evidence = reading(evidence, "the verification")
    row = assignment_row(conn, aid)
    require_generation(row, generation)
    require_role(part, ("verifier", "lead"))
    not_the_producer(part, row, "verifies")
    candidate = named_revision(candidate, "the verification")
    if not row["revision"]:
        # Defence in depth, and the half the check on submit cannot
        # reach: a store written before that check can hold such a row,
        # and comparing a candidate against nothing verified anything.
        refuse("revision", f"a candidate named on A-{aid}", "none",
               "submit names the revision the work is against; an "
               "assignment standing at none cannot be verified")
    if candidate != row["revision"]:
        refuse("revision", f"the applicable revision {row['revision']} of A-{aid}",
               candidate,
               "verify the candidate that was submitted, or reopen the assignment")

    def gate(_subject):
        live = assignment_row(conn, aid)
        require_generation(live, generation)
        conn.execute("UPDATE assignments SET verified_by = ? WHERE id = ?",
                     (part["id"], aid))

    advance(conn, aid, "verify", part, evidence=evidence, gate=gate)
    return assignment_dict(assignment_row(conn, aid))


def core_return(conn, part, *, agent, assignment, findings, generation) -> dict:
    """The candidate goes back to its holder with what was found.

    The generation is checked twice, as every other generation-bearing
    transition checks it: once here so a stale caller reads class
    generation and not class place, and once inside the gate under the
    write lock, so a candidate that moved between the two is not moved
    on (docs/workflow.md: every transition names its gate).
    """
    part = same_session(conn, part)
    _, aid = parse_id(assignment, "A")
    findings = reading(findings, "the findings")
    row = assignment_row(conn, aid)
    require_generation(row, generation)
    require_role(part, ("verifier", "lead"))

    def gate(_subject):
        require_generation(assignment_row(conn, aid), generation)

    advance(conn, aid, "return", part, evidence=findings, gate=gate)
    return assignment_dict(assignment_row(conn, aid))


def acceptance_evidence(conn, row, *, exception=None) -> dict:
    """AC-10: a missing examination and stale evidence both stop an
    acceptance that nothing supports.

    Read against the applicable revision, which is the candidate the
    assignment stands at. An observation of an earlier candidate is
    history: `reopen` stales what was read against the revision it
    replaced, and holding that against the new candidate would make an
    assignment unacceptable for ever. What refuses is a reading of *this*
    candidate that has gone stale — something moved under evidence that
    was supposed to cover what is being accepted.

    An observation carries its own requirement or none; one taken against
    the assignment as a whole counts for the requirement the assignment
    names, and one taken against a different requirement does not.

    `exception` is the Lead's explicitly permitted exception (objective
    §11). It does not lift the gate quietly: the acceptance records it,
    and `show` reads it back beside the transition.
    """
    applicable = row["revision"]
    observations = conn.execute(
        "SELECT * FROM observations WHERE assignment = ? ORDER BY id", (row["id"],)
    ).fetchall()
    of_candidate = [entry for entry in observations
                    if not applicable or entry["revision"] == applicable]
    required = row["requirement"]
    current = [entry for entry in of_candidate
               if entry["applicability"] == "current"
               and (not required or entry["requirement"] in (None, required))]
    stale = [entry for entry in of_candidate if entry["applicability"] == "stale"]
    if not current and exception is None:
        refuse("evidence",
               f"an examination of {required or 'the outcome'} at the applicable "
               f"revision {applicable or 'unstated'}",
               f"none of the {len(observations)} observation(s) of A-{row['id']}",
               "an independent observation of this candidate before acceptance, "
               "or a permitted exception recorded with --exception")
    if stale and exception is None:
        refuse("evidence",
               f"every observation of {applicable} current at acceptance",
               f"{len(stale)} stale: "
               + (stale[0]["stale_reason"] or "reason unstated"),
               "reassess the changed candidate, or record a permitted exception "
               "with --exception")
    return {"current": len(current), "stale": len(stale)}


def core_accept(conn, part, *, agent, assignment, evidence, generation,
                exception=None) -> dict:
    part = same_session(conn, part)
    _, aid = parse_id(assignment, "A")
    evidence = reading(evidence, "the acceptance")
    if exception is not None and not exception.strip():
        refuse("arguments", "words in --exception", "an exception with no words",
               "say what the permitted exception is, or leave the option out")
    row = assignment_row(conn, aid)
    require_generation(row, generation)
    require_role(part, ("lead",))
    not_the_producer(part, row, "accepts")
    standing = acceptance_evidence(conn, row, exception=exception)

    def gate(_subject):
        live = assignment_row(conn, aid)
        require_generation(live, generation)
        conn.execute("UPDATE assignments SET accepted_by = ? WHERE id = ?",
                     (part["id"], aid))

    advance(conn, aid, "accept", part, evidence=evidence, gate=gate,
            note=f"exception: {exception.strip()}" if exception else None)
    shown = assignment_dict(assignment_row(conn, aid))
    shown["evidence"] = standing
    if exception:
        shown["exception"] = exception.strip()
    return shown


def core_reopen(conn, part, *, agent, assignment, revision, why, generation) -> dict:
    """A changed candidate: the observations go stale and keep their scope."""
    part = same_session(conn, part)
    revision = named_revision(revision, "the reopen")
    _, aid = parse_id(assignment, "A")
    row = assignment_row(conn, aid)
    require_generation(row, generation)
    require_role(part, ("lead", "coordinator"))

    def gate(_subject):
        live = assignment_row(conn, aid)
        require_generation(live, generation)
        conn.execute(
            "UPDATE assignments SET revision = ?, verified_by = NULL WHERE id = ?",
            (revision, aid),
        )
        conn.execute(
            "UPDATE observations SET applicability = 'stale', stale_reason = ? "
            "WHERE assignment = ? AND applicability = 'current'",
            (f"the candidate moved to {revision}: {why}", aid),
        )

    advance(conn, aid, "reopen", part, evidence=revision, gate=gate)
    return assignment_dict(assignment_row(conn, aid))


def core_supersede(conn, part, *, agent, assignment, by, generation) -> dict:
    """Superseding is a writing verb on an assignment like any other.

    It moves an `offered` assignment, and `offered` is exactly the place
    a `release` or a `reassign` returns one to, so the place alone cannot
    tell the assignment that stands there now from the one the caller
    read. The Daybreak audit reproduced that ABA against b1d868b0.
    """
    part = same_session(conn, part)
    require_role(part, ("lead", "coordinator"))
    _, aid = parse_id(assignment, "A")
    _, newer = parse_id(by, "A")
    row = assignment_row(conn, aid)
    require_generation(row, generation)
    assignment_row(conn, newer)

    def gate(_subject):
        require_generation(assignment_row(conn, aid), generation)
        conn.execute("UPDATE assignments SET superseded_by = ? WHERE id = ?", (newer, aid))

    advance(conn, aid, "supersede", part, evidence=f"A-{newer}", gate=gate)
    shown = assignment_dict(assignment_row(conn, aid))
    shown["superseded_by"] = f"A-{newer}"
    return shown


def core_assignments(conn, part, *, state=None, to=None) -> dict:
    """`part` filters to what that participant holds; None shows every row."""
    part = same_session(conn, part)
    if conn is None:
        return no_store(assignments=[])
    rows = conn.execute(
        "SELECT a.*, s.place FROM assignments a JOIN subjects s ON s.id = a.id "
        "ORDER BY a.id"
    ).fetchall()
    holder = part["id"] if part is not None else None
    shown = [
        row for row in rows
        if (not state or row["place"] == state)
        and (not to or row["recipient"] == to)
        and (holder is None or row["holder"] == holder)
    ]
    return {"assignments": [assignment_dict(row) for row in shown]}


QUEUE_MESSAGE = ("INSERT INTO messages(id, sender, recipient, purpose, body, artifact, "
                 "assignment, decision, reply_to, queued_at) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")


def core_send(conn, part, *, agent, to, purpose, body, assignment=None, decision=None,
              reply_to=None, artifact=None, key=None) -> dict:
    """With `key`, idempotent per caller (sessions-lever.md, Slice 2): the
    same key with the same payload returns the message it first queued,
    marked `repeated`; with another payload it refuses `idempotency` and
    writes nothing. Without a key, every send queues a new message."""
    part = same_session(conn, part)
    if purpose not in PURPOSES:
        refuse("arguments", "a purpose of " + ", ".join(PURPOSES), purpose,
               "an inform carries no obligation; a request and a finding do")
    digest = None
    if key is not None:
        if not isinstance(key, str) or not key.strip() or len(key) > 200:
            refuse("arguments", "a non-empty key of at most 200 characters", repr(key),
                   "name the caller's own request key")
        digest = send_digest(to=to, purpose=purpose, body=body, assignment=assignment,
                             decision=decision, reply_to=reply_to, artifact=artifact)
        repeated = keyed_message(conn, part, key, digest)
        if repeated is not None:
            return repeated
    recipient = recipient_of(conn, to)
    aid = parse_id(assignment, "A")[1] if assignment else None
    did = parse_id(decision, "DC")[1] if decision else None
    reply = parse_id(reply_to, "M")[1] if reply_to else None
    if aid is not None:
        assignment_row(conn, aid)
    sid = machine.create(conn, MESSAGE, actor(part))
    queued = (sid, part["id"], recipient, purpose, body, artifact, aid, did, reply, stamp())
    if key is None:
        conn.execute(QUEUE_MESSAGE, queued)
        return message_dict(message_row(conn, sid))

    def take_key(conn):
        # The key's row and the message's row land in one transaction under
        # the write lock. A send that lost the key to a concurrent one never
        # wrote a message row, so no courier can carry what it queued: the
        # restart or the race does not replay the effect.
        if keyed_message(conn, part, key, digest) is not None:
            return False
        conn.execute(QUEUE_MESSAGE, queued)
        conn.execute("INSERT INTO send_requests(participant, key, digest, message, at) "
                     "VALUES (?, ?, ?, ?, ?)", (part["id"], key, digest, sid, stamp()))
        return True

    try:
        with instrument.transaction(conn):
            took = take_key(conn)
    except Refusal:
        # Another payload took the key after the check above: the subject
        # this send opened carries no message and is withdrawn, never deleted.
        machine.move(conn, MESSAGE, sid, "withdraw", actor(part),
                     evidence=f"idempotency: key {key} already named another payload")
        raise
    if not took:
        machine.move(conn, MESSAGE, sid, "withdraw", actor(part),
                     evidence=f"idempotency: key {key} already named a message")
        return keyed_message(conn, part, key, digest)
    return message_dict(message_row(conn, sid))


def core_deliver(conn, part, *, message, ref) -> dict:
    """The transport's own receipt. The core never sends; it records."""
    part = same_session(conn, part)
    _, mid = parse_id(message, "M")
    row = message_row(conn, mid)
    if row["sender"] != part["id"] and not addressed(part, row["recipient"]):
        refuse("recipient", f"the sender PT-{row['sender']} or the recipient "
               f"{row['recipient']} of M-{mid}", f"PT-{part['id']}",
               "the route that carried it records its receipt")
    conn.execute("UPDATE messages SET delivered_at = ?, delivery_ref = ? WHERE id = ?",
                 (stamp(), ref, mid))
    return message_dict(message_row(conn, mid))


def core_ack(conn, part, *, message) -> dict:
    part = same_session(conn, part)
    _, mid = parse_id(message, "M")
    row = message_row(conn, mid)
    if not addressed(part, row["recipient"]):
        refuse("recipient", f"the recipient {row['recipient']} of M-{mid}",
               f"PT-{part['id']} ({part['roles']})",
               "the recipient acknowledges its own message")
    conn.execute("UPDATE messages SET acked_at = ?, acked_by = ? WHERE id = ?",
                 (stamp(), part["id"], mid))
    return message_dict(message_row(conn, mid))


def core_resolve(conn, part, *, agent, message, evidence) -> dict:
    """Resolution is a place, moved by the obligation's holder, never by
    the acknowledgment (AC-04)."""
    part = same_session(conn, part)
    _, mid = parse_id(message, "M")
    evidence = reading(evidence, "the resolution")
    row = message_row(conn, mid)
    allowed = addressed(part, row["recipient"])
    if not allowed and row["assignment"] is not None:
        allowed = assignment_row(conn, row["assignment"])["holder"] == part["id"]
    if not allowed:
        refuse("recipient", f"the recipient {row['recipient']} of M-{mid}, "
               "or the holder of its assignment", f"PT-{part['id']} ({part['roles']})",
               "the participant that owes the answer resolves it")
    machine.move(conn, MESSAGE, mid, "resolve", actor(part), evidence=evidence)
    shown = message_dict(message_row(conn, mid))
    shown["evidence"] = evidence
    return shown


def core_withdraw(conn, part, *, agent, message, why) -> dict:
    part = same_session(conn, part)
    _, mid = parse_id(message, "M")
    row = message_row(conn, mid)
    if row["sender"] != part["id"]:
        refuse("recipient", f"the sender PT-{row['sender']} of M-{mid}",
               f"PT-{part['id']}", "the sender withdraws its own message")
    machine.move(conn, MESSAGE, mid, "withdraw", actor(part), evidence=why)
    return message_dict(message_row(conn, mid))


def core_inbox(conn, part, *, unresolved=False) -> dict:
    part = same_session(conn, part)
    if conn is None:
        return no_store(messages=[])
    rows = conn.execute(
        "SELECT m.*, s.place FROM messages m JOIN subjects s ON s.id = m.id ORDER BY m.id"
    ).fetchall()
    shown = [row for row in rows if addressed(part, row["recipient"])
             and (not unresolved or row["place"] == "open")]
    return {"messages": [message_dict(row) for row in shown]}


def core_outstanding(conn, part, *, to=None) -> dict:
    """An obligation stays here until it is resolved; an ack does not lift it."""
    part = same_session(conn, part)
    if conn is None:
        return no_store(outstanding=[])
    rows = conn.execute(
        "SELECT m.*, s.place FROM messages m JOIN subjects s ON s.id = m.id "
        "WHERE s.place = 'open' ORDER BY m.id"
    ).fetchall()
    shown = [row for row in rows if row["purpose"] in OBLIGATIONS
             and (not to or row["recipient"] == to)]
    return {"outstanding": [message_dict(row) for row in shown]}


def core_undelivered(conn, part, *, to=None) -> dict:
    """The messages queued to the calling participant that have not left
    the store yet — what a deliverer asks before it carries anything.

    `outstanding` is not this read and cannot stand in for it: an
    obligation stays outstanding long after it was delivered, and an
    inform is never outstanding at all yet still has to be carried. A
    withdrawn message is not carried, so only the open ones are here.
    Addressing is the inbox's rule: a participant by id, or a role its
    holder answers to.
    """
    part = same_session(conn, part)
    if conn is None:
        return no_store(undelivered=[])
    rows = conn.execute(
        "SELECT m.*, s.place FROM messages m JOIN subjects s ON s.id = m.id "
        "WHERE m.delivered_at IS NULL AND s.place = 'open' ORDER BY m.id"
    ).fetchall()
    shown = [row for row in rows if addressed(part, row["recipient"])
             and (not to or row["recipient"] == to)]
    return {"undelivered": [message_dict(row) for row in shown]}


def core_observe(conn, part, *, assignment, observation, revision, generation,
                 stale=False, execution=None, artifact=None, requirement=None,
                 environment=None) -> dict:
    """An observation names the candidate it was made against.

    Raised by the Codex review of package C and ruled by the Lead session
    on 2026-09-16: without these two checks a participant that had not
    seen a reopen could append an observation of the old revision and it
    read as current evidence of the new one. The checks and the insert
    take the write lock together, the way the engine's own move does, so
    a reopen committing between the read and the insert cannot slip
    between them.

    Filing the row as stale is the declared escape and it is not a pass:
    the row lands marked, carrying the revision it was made against, so
    it is readable as history and never as evidence of what replaced it.
    Both checks lift for it, because a reopen moves the generation and
    the revision together and an old observation could otherwise never be
    filed at all.
    """
    part = same_session(conn, part)
    _, aid = parse_id(assignment, "A")
    if not str(generation).isdigit():
        refuse("arguments", "--generation as a whole number", str(generation),
               "the generation printed by show")
    with instrument.transaction(conn):
        row = assignment_row(conn, aid)
        if not row["revision"]:
            refuse("revision", f"a candidate named on A-{aid}", "no revision",
                   "draft it with --revision, or submit one, before observing it")
        reason = None
        if stale:
            reason = (f"filed as stale by PT-{part['id']}: observed at {revision}, "
                      f"the assignment stands at {row['revision'] or 'no revision'} "
                      f"at generation {row['generation']}")
        else:
            require_generation(row, generation)
            if row["revision"] and revision != row["revision"]:
                refuse("revision", f"the current revision {row['revision']} of A-{aid}",
                       revision,
                       "observe the candidate that stands, or file it with --stale")
        cursor = conn.execute(
            "INSERT INTO observations(assignment, producer, requirement, execution, "
            "revision, environment, observation, artifact, applicability, "
            "stale_reason, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (aid, part["id"], requirement, execution, revision, environment,
             observation, artifact, "stale" if stale else "current", reason, stamp()),
        )
    row = conn.execute(
        "SELECT * FROM observations WHERE id = ?", (cursor.lastrowid,)
    ).fetchone()
    return observation_dict(row)


def core_observations(conn, part, *, assignment, include_stale=False) -> dict:
    part = same_session(conn, part)
    _, aid = parse_id(assignment, "A")
    if conn is None:
        return no_store(observations=[])
    assignment_row(conn, aid)
    rows = conn.execute(
        "SELECT * FROM observations WHERE assignment = ? ORDER BY id", (aid,)
    ).fetchall()
    shown = [row for row in rows
             if include_stale or row["applicability"] == "current"]
    return {"observations": [observation_dict(row) for row in shown]}


def core_ask(conn, part, *, question, alternatives, assignment=None) -> dict:
    part = same_session(conn, part)
    aid = parse_id(assignment, "A")[1] if assignment else None
    if aid is not None:
        assignment_row(conn, aid)
    cursor = conn.execute(
        "INSERT INTO decisions(asked_by, question, alternatives, assignment, asked_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (part["id"], question, alternatives, aid, stamp()),
    )
    return decision_dict(decision_row(conn, cursor.lastrowid))


def core_answer(conn, part, *, decision, counter=None, unknowns=None,
                consequence=None) -> dict:
    part = same_session(conn, part)
    if counter is None and unknowns is None and consequence is None:
        refuse("arguments", "at least one of --counter, --unknowns, --consequence",
               "none", "say what the brief is missing")
    _, did = parse_id(decision, "DC")
    decision_row(conn, did)
    for value, sql in (
        (counter, "UPDATE decisions SET counter = ? WHERE id = ?"),
        (unknowns, "UPDATE decisions SET unknowns = ? WHERE id = ?"),
        (consequence, "UPDATE decisions SET consequence = ? WHERE id = ?"),
    ):
        if value:
            conn.execute(sql, (value, did))
    return decision_dict(decision_row(conn, did))


def core_decide(conn, part, *, decision, choice, rationale) -> dict:
    part = same_session(conn, part)
    _, did = parse_id(decision, "DC")
    require_role(part, ("lead",))
    decision_row(conn, did)
    conn.execute(
        "UPDATE decisions SET choice = ?, rationale = ?, decided_by = ?, "
        "decided_at = ? WHERE id = ?", (choice, rationale, part["id"], stamp(), did),
    )
    return decision_dict(decision_row(conn, did))


def core_decision(conn, part, *, decision) -> dict:
    """The bounded brief: what is missing is named, never passed over."""
    part = same_session(conn, part)
    _, did = parse_id(decision, "DC")
    if conn is None:
        return no_store(decision=None)
    row = decision_row(conn, did)
    observations = conn.execute(
        "SELECT * FROM observations WHERE assignment IS ? AND applicability = 'current' "
        "ORDER BY id", (row["assignment"],)
    ).fetchall() if row["assignment"] else []
    brief = decision_dict(row)
    brief["gaps"] = [name for name in ("counter", "unknowns", "consequence")
                     if not row[name]]
    brief["observations"] = [observation_dict(entry) for entry in observations]
    return brief


def core_decisions(conn, part, *, undecided_only=False) -> dict:
    part = same_session(conn, part)
    if conn is None:
        return no_store(decisions=[])
    rows = conn.execute("SELECT * FROM decisions ORDER BY id").fetchall()
    shown = [row for row in rows if not undecided_only or row["decided_at"] is None]
    return {"decisions": [decision_dict(row) for row in shown]}


def core_reserve(conn, part, *, agent, name, assignment, ttl=None) -> dict:
    part = same_session(conn, part)
    _, aid = parse_id(assignment, "A")
    ttl_s = int(ttl) * 60 if str(ttl or "").isdigit() else None
    assignment_row(conn, aid)
    row = conn.execute("SELECT * FROM resources WHERE name = ?", (name,)).fetchone()
    if row is None:
        sid = machine.create(conn, RESOURCE, actor(part))
        conn.execute("INSERT INTO resources(id, name, assignment) VALUES (?, ?, ?)",
                     (sid, name, aid))
    else:
        sid = row["id"]

    def gate(_subject):
        conn.execute("UPDATE resources SET assignment = ?, holder = ? WHERE id = ?",
                     (aid, part["id"], sid))

    machine.move(conn, RESOURCE, sid, "reserve", actor(part), note=f"A-{aid}",
                 ttl_s=ttl_s, gate=gate)
    return {"resource": f"R-{sid}", "id": sid, "name": name, "place": "held",
            "assignment": f"A-{aid}", "assignment_id": aid,
            "holder": f"PT-{part['id']}"}


def core_free(conn, part, *, agent, name, why) -> dict:
    part = same_session(conn, part)
    row = conn.execute("SELECT * FROM resources WHERE name = ?", (name,)).fetchone()
    if row is None:
        refuse("unknown", "a reserved resource", name, "a name printed by resources")
    previous = row["holder"]

    def gate(subject):
        """A lapsed lease is evidence a reservation ran out, never proof
        the previous process stopped: the reconciliation is reported."""
        if previous != part["id"] and not machine.lapsed(subject):
            refuse("holder", f"the holder PT-{previous} of {name}",
                   f"PT-{part['id']}",
                   "the holder frees it, or anyone once the lease lapses")
        conn.execute("UPDATE resources SET holder = NULL WHERE id = ?", (row["id"],))

    machine.move(conn, RESOURCE, row["id"], "free", actor(part), evidence=why, gate=gate)
    return {"resource": f"R-{row['id']}", "id": row["id"], "name": name,
            "place": "free", "previous_holder": f"PT-{previous}",
            "reconciled": previous == part["id"]}


def core_resources(conn, part) -> dict:
    part = same_session(conn, part)
    if conn is None:
        return no_store(resources=[])
    rows = conn.execute(
        "SELECT r.*, s.place, s.held_since, s.ttl_s FROM resources r "
        "JOIN subjects s ON s.id = r.id ORDER BY r.id"
    ).fetchall()
    return {"resources": [{
        "resource": f"R-{row['id']}", "id": row["id"], "name": row["name"],
        "place": row["place"], "held_since": row["held_since"], "ttl_s": row["ttl_s"],
        "holder": f"PT-{row['holder']}" if row["holder"] else None,
        "assignment": f"A-{row['assignment']}" if row["assignment"] else None,
        "assignment_id": row["assignment"],
    } for row in rows]}


def core_show(conn, part, *, target) -> dict:
    part = same_session(conn, part)
    kind, number = parse_id(target, ("A", "M", "DC"))
    if kind == "DC":
        return core_decision(conn, part, decision=target)
    if conn is None:
        return no_store(target=target)
    if kind == "M":
        return message_dict(message_row(conn, number))
    shown = assignment_dict(assignment_row(conn, number))
    counts = conn.execute(
        "SELECT applicability, COUNT(*) AS n FROM observations WHERE assignment = ? "
        "GROUP BY applicability", (number,)
    ).fetchall()
    tally = {r["applicability"]: r["n"] for r in counts}
    shown["observations"] = {"current": tally.get("current", 0),
                             "stale": tally.get("stale", 0)}
    shown["marking"] = [machine.history_line(entry)
                        for entry in machine.history(conn, ASSIGNMENT, number)]
    return shown


def core_coverage(conn, part, *, requirement=None) -> dict:
    """requirement -> assignment -> candidate -> observation -> verification
    -> acceptance or the gap, named."""
    part = same_session(conn, part)
    if conn is None:
        return no_store(coverage=[])
    rows = conn.execute(
        "SELECT a.*, s.place FROM assignments a JOIN subjects s ON s.id = a.id "
        "ORDER BY a.id"
    ).fetchall()
    shown = [r for r in rows if not requirement or r["requirement"] == requirement]
    coverage = []
    for row in shown:
        current = conn.execute(
            "SELECT COUNT(*) AS n FROM observations WHERE assignment = ? "
            "AND applicability = 'current'", (row["id"],)
        ).fetchone()["n"]
        if row["place"] == "accepted":
            closure, gap = f"accepted by PT-{row['accepted_by']}", None
        elif row["verified_by"]:
            closure = f"verified by PT-{row['verified_by']}, acceptance gap"
            gap = "acceptance"
        else:
            closure, gap = "verification gap", "verification"
        entry = assignment_dict(row)
        entry.update({"observations": current, "closure": closure, "gap": gap})
        coverage.append(entry)
    return {"coverage": coverage}


def dispatch_store():
    """The dispatcher's store, read here and never written."""
    return state_dir() / "dispatch"


def executions_of(held) -> dict:
    """The live dispatch records for the work this participant holds.

    Joined by the assignment id the record's lineage carries, and, while
    the dispatcher writes no id, by the branch the claim named — every
    record in the store today carries `lineage.branch` and no assignment
    (20260916T113912Z-review-claude-5a8da1). The store belongs to another
    instrument, so a brief that could not open it says so: `none` for a
    store that is not there would read as a participant with nothing
    running (docs/wire.md, absence discipline).
    """
    root = dispatch_store()
    if not root.is_dir():
        return {"executions": None, "executions_store": "absent",
                "executions_reason": f"no dispatch store at {root}"}
    try:
        directories = sorted(root.iterdir())
    except OSError as exc:
        return {"executions": None, "executions_store": "unreadable",
                "executions_reason": f"{root}: {exc.strerror or exc}"}
    by_id = {row["assignment"]: row for row in held}
    by_branch = {row["branch"]: row for row in held if row["branch"]}
    found, unreadable = [], []
    for directory in directories:
        path = directory / "record.json"
        if not path.is_file():
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            unreadable.append(directory.name)
            continue
        if not isinstance(record, dict):
            unreadable.append(directory.name)
            continue
        lineage = record.get("lineage")
        lineage = lineage if isinstance(lineage, dict) else {}
        row = by_id.get(lineage.get("assignment"))
        joined = "the assignment id in its lineage"
        if row is None:
            row = by_branch.get(lineage.get("branch"))
            joined = "the branch its lineage names"
        if row is None:
            continue
        found.append({
            "execution": record.get("id") or directory.name,
            "assignment": row["assignment"],
            "status": record.get("status") or "unstated",
            "job": record.get("job"), "role": record.get("role"),
            "at": record.get("at"), "base_sha": lineage.get("base_sha"),
            "joined_by": joined,
        })
    reading = {"executions": found, "executions_store": "read"}
    if unreadable:
        reading["executions_unreadable"] = unreadable
    return reading


def core_brief(conn, part) -> dict:
    """What a restarted or compacted participant reads to recover its
    obligations (AC-12), in five readings: the assignments it holds, the
    executions live against them, the requests owed in both directions,
    the open decision rows themselves, and whether its own evidence still
    applies. A count is not something a restarted session can act on, so
    the decisions and the observations are rows here; the counts stay
    beside them for the readers that had only counts."""
    part = same_session(conn, part)
    if conn is None:
        return no_store(brief=None)
    held = conn.execute(
        "SELECT a.*, s.place FROM assignments a JOIN subjects s ON s.id = a.id "
        "WHERE a.holder = ? ORDER BY a.id", (part["id"],)
    ).fetchall()
    messages = conn.execute(
        "SELECT m.*, s.place FROM messages m JOIN subjects s ON s.id = m.id "
        "WHERE s.place = 'open' ORDER BY m.id"
    ).fetchall()
    decisions = conn.execute(
        "SELECT * FROM decisions WHERE decided_at IS NULL ORDER BY id"
    ).fetchall()
    observations = conn.execute(
        "SELECT * FROM observations WHERE producer = ? ORDER BY id", (part["id"],)
    ).fetchall()
    stale = [row for row in observations if row["applicability"] == "stale"]
    holdings = [assignment_dict(row) for row in held]
    obligations = [row for row in messages if row["purpose"] in OBLIGATIONS]
    shown = {
        "participant": participant_dict(part),
        "held": holdings,
        "unresolved": [message_dict(row) for row in messages
                       if addressed(part, row["recipient"])],
        "owed": [message_dict(row) for row in obligations
                 if addressed(part, row["recipient"])],
        "awaited": [message_dict(row) for row in obligations
                    if row["sender"] == part["id"]],
        "decisions": [decision_dict(row) for row in decisions],
        "decisions_open": len(decisions),
        "evidence": {
            "current": [observation_dict(row) for row in observations
                        if row["applicability"] == "current"],
            "stale": [observation_dict(row) for row in stale],
        },
        "observations_stale": len(stale),
    }
    shown.update(executions_of(holdings))
    return shown


def core_entry(conn, part, *, role=None) -> dict:
    """What a session of this role does first, here, now.

    Three parts, and each is a reading rather than a recital: the role's
    duties, the state this session is in, and the next steps with the
    argument each verb needs. It never refuses an absent store — a
    participant that cannot bind yet is the reader this verb exists for
    — and it never refuses for want of an identity, because it writes
    nothing (docs/wire.md: a layer able to refuse a read is itself a
    finding).
    """
    part = same_session(conn, part)
    if role is not None and role not in ROLES:
        refuse("arguments", "a known role", role, "one of " + ", ".join(ROLES))
    if role:
        named = [role]
    elif part is not None:
        named = [name for name in ROLES if name in part["roles"].split(",")]
    else:
        named = list(ROLES)
    state = {"bound": part is not None, "store": "read" if conn is not None else "absent"}
    if part is not None:
        state["participant"] = f"PT-{part['id']}"
        state["roles"] = part["roles"]
        state["held"] = conn.execute(
            "SELECT COUNT(*) AS n FROM assignments a JOIN subjects s ON s.id = a.id "
            "WHERE a.holder = ? AND s.place NOT IN ('accepted', 'superseded')",
            (part["id"],),
        ).fetchone()["n"]
        messages = conn.execute(
            "SELECT m.*, s.place FROM messages m JOIN subjects s ON s.id = m.id "
            "WHERE s.place = 'open' ORDER BY m.id"
        ).fetchall()
        obligations = [row for row in messages if row["purpose"] in OBLIGATIONS]
        state["owed"] = len([row for row in obligations
                             if addressed(part, row["recipient"])])
        state["awaited"] = len([row for row in obligations
                                if row["sender"] == part["id"]])
        state["decisions_open"] = conn.execute(
            "SELECT COUNT(*) AS n FROM decisions WHERE decided_at IS NULL"
        ).fetchone()["n"]
    blocks = []
    for name in named:
        headline, duties, steps = ENTRY[name]
        first = list(steps)
        if part is None or name not in part["roles"].split(","):
            first.insert(0, f"bind --roles {name}")
        blocks.append({"role": name, "headline": headline,
                       "duties": list(duties), "next": first})
    return {"state": state, "roles": blocks,
            "contract": "ops/conductor/coordination-roles.md",
            "ladder": "ops/conductor/coordination-lever.md"}


# -- delivery: routes, attempts, send keys (sessions-lever.md, Slice 2) -------

def process_ticks(pid) -> str | None:
    """Start ticks for a live process; absent, malformed and zombie records are gone."""
    try:
        with open(f"/proc/{int(pid)}/stat", encoding="utf-8") as handle:
            text = handle.read()
    except (OSError, ValueError, TypeError):
        return None
    try:
        fields = text.rsplit(")", 1)[1].split()
        return None if fields[0] in {"Z", "X", "x"} else fields[19]
    except IndexError:
        return None


def holder_alive(row) -> bool:
    """A route's holder is alive when its pid still carries the recorded
    start ticks; a missing tick is never alive."""
    return bool(row["start_ticks"]) and process_ticks(row["pid"]) == row["start_ticks"]


def has_table(conn, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_schema WHERE type = 'table' AND name = ?", (name,)
    ).fetchone() is not None


def route_dict(row) -> dict:
    return {
        "participant": f"PT-{row['participant']}", "route": row["route"],
        "member": f"SM-{row['member']}" if row["member"] is not None else None,
        "pid": row["pid"], "start_ticks": row["start_ticks"], "since": row["since"],
        "last_pass_at": row["last_pass_at"], "alive": holder_alive(row),
    }


def attempt_dict(row) -> dict:
    return {
        "attempt": f"AT-{row['id']}", "id": row["id"], "message": f"M-{row['message']}",
        "participant": f"PT-{row['participant']}", "generation": row["generation"],
        "route": row["route"], "reserved_at": row["reserved_at"],
        "submitted_at": row["submitted_at"], "outcome": row["outcome"],
        "native_ref": row["native_ref"], "error": row["error"],
    }


def holder_line(row) -> str:
    return f"{row['route']} route held by pid={row['pid']} since {row['since']}"


def core_route(conn, part, *, action, pid, start_ticks, route=None, member=None) -> dict:
    """A courier's route: `take`, `pass` or `release`, by the holder's pid
    and start ticks. One verb, so the CLI and the core offer the same
    table; the three moves are the functions below."""
    part = same_session(conn, part)
    if not isinstance(pid, int) or pid < 1 or not start_ticks:
        refuse("arguments", "the holder's pid and its start ticks", f"pid={pid} start={start_ticks}",
               "name the route from the courier's own live process")
    if action == "take":
        return route_take(conn, part, route=route, pid=pid, start_ticks=start_ticks,
                          member=member)
    if action == "pass":
        return route_pass(conn, part, pid=pid, start_ticks=start_ticks)
    if action == "release":
        return route_release(conn, part, pid=pid, start_ticks=start_ticks)
    return refuse("arguments", "a route action of take, pass or release", str(action),
                  "route --take stdout|native or route --release")


def member_id(member) -> int | None:
    if member is None or isinstance(member, int):
        return member
    match = re.fullmatch(r"SM-(\d+)", str(member))
    if match is None:
        refuse("id", "a member id like SM-1", str(member), "the id sessions.py status prints")
    return int(match.group(1))


def route_take(conn, part, *, route, pid, start_ticks, member=None) -> dict:
    """One live route per participant, taken under the write lock.

    A second courier of either route refuses while the holder lives; a dead
    holder's row is taken over and the takeover is the new row's `since`
    (plan §7, competing couriers).
    """
    if route not in ROUTES:
        refuse("arguments", "a route of " + " or ".join(ROUTES), str(route),
               "stdout for a TUI session's courier, native for a bridge")
    member = member_id(member)

    def take(conn):
        row = conn.execute("SELECT * FROM delivery_routes WHERE participant = ?",
                           (part["id"],)).fetchone()
        moment = stamp()
        if row is not None and (row["pid"], row["start_ticks"]) == (pid, str(start_ticks)):
            conn.execute("UPDATE delivery_routes SET route = ?, member = ?, last_pass_at = ? "
                         "WHERE participant = ?", (route, member, moment, part["id"]))
            return None
        if row is not None and holder_alive(row):
            refuse("route", f"no live courier holding PT-{part['id']}", holder_line(row),
                   "stop that courier first; a route is taken over only from a dead holder")
        conn.execute("INSERT OR REPLACE INTO delivery_routes(participant, route, member, pid, "
                     "start_ticks, since, last_pass_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (part["id"], route, member, pid, str(start_ticks), moment, moment))
        return None if row is None else holder_line(row)

    with instrument.transaction(conn):
        previous = take(conn)
    shown = route_dict(conn.execute("SELECT * FROM delivery_routes WHERE participant = ?",
                                    (part["id"],)).fetchone())
    shown["took_over"] = previous
    return shown


def route_pass(conn, part, *, pid, start_ticks) -> dict:
    """A courier's pass stamps the route it still holds; one that lost it refuses."""
    moment = stamp()
    changed = conn.execute(
        "UPDATE delivery_routes SET last_pass_at = ? WHERE participant = ? AND pid = ? "
        "AND start_ticks = ?", (moment, part["id"], pid, str(start_ticks))).rowcount
    row = conn.execute("SELECT * FROM delivery_routes WHERE participant = ?",
                       (part["id"],)).fetchone()
    if not changed:
        refuse("route", f"the route PT-{part['id']} took with pid={pid}",
               holder_line(row) if row is not None else "no route row",
               "take the route again; another courier holds it or it was released")
    return route_dict(row)


def route_release(conn, part, *, pid, start_ticks) -> dict:
    """The holder gives its route up; a live holder that is not the caller refuses."""

    def release(conn):
        row = conn.execute("SELECT * FROM delivery_routes WHERE participant = ?",
                           (part["id"],)).fetchone()
        if row is None:
            return None
        mine = (row["pid"], row["start_ticks"]) == (pid, str(start_ticks))
        if not mine and holder_alive(row):
            refuse("route", f"a route held by pid={pid} or by a dead holder", holder_line(row),
                   "the holding courier releases its own route")
        conn.execute("DELETE FROM delivery_routes WHERE participant = ?", (part["id"],))
        return holder_line(row)

    with instrument.transaction(conn):
        released = release(conn)
    return {"participant": f"PT-{part['id']}", "released": released}


def core_routes(conn, part) -> dict:
    """Every route row; a store without the table has taken no route yet."""
    if conn is None:
        return no_store(routes=[])
    if not has_table(conn, "delivery_routes"):
        return {"routes": [], "table": "absent: no writer since slice 2's tables"}
    rows = conn.execute("SELECT * FROM delivery_routes ORDER BY participant").fetchall()
    return {"routes": [route_dict(row) for row in rows]}


def live_holders(conn, role: str) -> list[int]:
    """Participants holding `role` whose courier holds a live route."""
    if not has_table(conn, "delivery_routes"):
        return []
    rows = conn.execute(
        "SELECT r.*, p.roles FROM delivery_routes r JOIN participants p ON p.id = r.participant "
        "ORDER BY r.participant").fetchall()
    return [row["participant"] for row in rows
            if role in row["roles"].split(",") and holder_alive(row)]


def carriable(conn, part) -> dict:
    """What this participant's courier carries now, and what no courier may.

    A message to `PT-n` is carried by that participant's courier. A
    role-addressed message is carried only by the courier of the single
    live holder of that role; with two live holders it is carried by
    neither and is listed as ambiguous (plan §7, role-addressed messages).
    The live holders are read across the store's routes, not per run: the
    core knows no runs.
    """
    part = same_session(conn, part)
    if conn is None:
        return no_store(carry=[], ambiguous=[])
    rows = conn.execute(
        "SELECT m.*, s.place FROM messages m JOIN subjects s ON s.id = m.id "
        "WHERE m.delivered_at IS NULL AND s.place = 'open' ORDER BY m.id"
    ).fetchall()
    carry, ambiguous = [], []
    for row in rows:
        if not addressed(part, row["recipient"]):
            continue
        if row["recipient"] == f"PT-{part['id']}":
            carry.append(message_dict(row))
            continue
        holders = live_holders(conn, row["recipient"])
        if holders == [part["id"]]:
            carry.append(message_dict(row))
        elif len(holders) > 1:
            ambiguous.append({"message": f"M-{row['id']}", "role": row["recipient"],
                              "holders": [f"PT-{pid}" for pid in holders]})
    return {"carry": carry, "ambiguous": ambiguous}


def member_kin(conn, pid: int) -> list[int]:
    """The participants of every generation of the member PT-`pid` is: the
    same run and member name in sessions.py's `session_member`, in the
    same store. `pid` alone when no member row names it or the store has
    no such table. Attempt exclusion and reconciliation key on these, so a
    replacement generation sees, waits on and settles what its
    predecessor left unsettled (A-522 #1): a restart neither erases the
    obligation nor replays its effects."""
    if not has_table(conn, "session_member"):
        return [pid]
    rows = conn.execute(
        "SELECT DISTINCT k.participant FROM session_member m JOIN session_member k "
        "ON k.run = m.run AND k.name = m.name "
        "WHERE m.participant = ? AND k.participant IS NOT NULL ORDER BY k.participant",
        (pid,)).fetchall()
    return [row[0] for row in rows] or [pid]


def marks(values) -> str:
    """One `?` per value, for an `IN (...)` whose values stay bound."""
    return ",".join("?" for _ in values)


def attempt_row(conn, aid: int):
    row = None
    if conn is not None and has_table(conn, "delivery_attempts"):
        row = conn.execute("SELECT * FROM delivery_attempts WHERE id = ?", (aid,)).fetchone()
    if row is None:
        refuse("unknown", "a delivery attempt id in the store", f"AT-{aid}",
               "an id printed by attempts")
    return row


def core_attempt(conn, part, *, message, route, generation) -> dict:
    """Reserve one submission of one message to this participant, before it.

    Nothing is resubmitted while an attempt of the message is unsettled, and
    at most ATTEMPTS_OUTSTANDING attempts per participant are unsettled at
    once, a participant counting with its member's earlier generations
    (member_kin). A delivered message may take a later attempt: its outcome settles
    that attempt and never records `deliver` again. The courier does not
    select a delivered message; `carriable` is where that holds.
    """
    part = same_session(conn, part)
    _, mid = parse_id(message, "M")
    if route not in ROUTES:
        refuse("arguments", "a route of " + " or ".join(ROUTES), str(route),
               "the route the courier holds")
    stated = str(generation)
    if not stated.isdigit() or int(stated) < 1:
        refuse("arguments", "--generation as a whole number of one or more", stated,
               "the member's generation sessions.py status prints")
    row = message_row(conn, mid)
    if not addressed(part, row["recipient"]):
        refuse("recipient", f"the recipient {row['recipient']} of M-{mid}",
               f"PT-{part['id']} ({part['roles']})", "the recipient's own courier carries it")

    def reserve(conn):
        current = message_row(conn, mid)
        if current["place"] != "open":
            refuse("place", f"an open message M-{mid}", f"M-{mid} {current['place']}",
                   "a resolved or withdrawn message is not carried")
        kin = member_kin(conn, part["id"])
        pending = conn.execute(
            f"SELECT * FROM delivery_attempts WHERE message = ? AND participant IN ({marks(kin)}) "  # noqa: S608 - marks() is only placeholders
            "AND outcome IS NULL ORDER BY id", (mid, *kin)).fetchone()
        if pending is not None:
            refuse("place", f"no unsettled attempt of M-{mid}",
                   f"AT-{pending['id']} reserved {pending['reserved_at']}, unsettled",
                   "settle it with attempted, or let the next pass reconcile it past the ttl")
        held = conn.execute(
            f"SELECT COUNT(*) FROM delivery_attempts WHERE participant IN ({marks(kin)}) "  # noqa: S608 - marks() is only placeholders
            "AND outcome IS NULL", kin).fetchone()[0]
        if held >= ATTEMPTS_OUTSTANDING:
            refuse("place", f"fewer than {ATTEMPTS_OUTSTANDING} unsettled attempts for PT-{part['id']}",
                   f"{held} unsettled", "settle or reconcile the outstanding attempts first")
        return conn.execute(
            "INSERT INTO delivery_attempts(message, participant, generation, route, reserved_at) "
            "VALUES (?, ?, ?, ?, ?)", (mid, part["id"], int(stated), route, stamp())).lastrowid

    with instrument.transaction(conn):
        aid = reserve(conn)
    return attempt_dict(attempt_row(conn, aid))


def outcome_text(outcome, ref, error) -> str:
    """The recorded outcome, one of the contract's six shapes."""
    needs = {"accepted": ref, "unsupported": ref or error, "failed": error, "uncertain": error}
    if outcome not in OUTCOMES:
        refuse("arguments", "an outcome of " + ", ".join(OUTCOMES), str(outcome),
               "written, accepted with --ref, unsupported with the method, failed or "
               "uncertain with --error")
    if outcome == "written":
        return "written"
    if not needs[outcome]:
        refuse("arguments", f"an outcome {outcome} with its reading",
               "no --ref" if outcome == "accepted" else "no --error",
               "accepted takes --ref; unsupported the method; failed and uncertain --error")
    return f"{outcome} {needs[outcome]}"


def core_attempted(conn, part, *, attempt, outcome, ref=None, error=None) -> dict:
    """Settle an attempt after the submission; the first written or accepted
    one records the core's `deliver` once, ref `native:AT-n:<ref>`. The
    recipient's own `ack` is never written here."""
    part = same_session(conn, part)
    _, aid = parse_id(attempt, "AT")
    text = outcome_text(outcome, ref, error)

    def settle(conn):
        row = attempt_row(conn, aid)
        if row["participant"] not in member_kin(conn, part["id"]):
            refuse("recipient", f"the courier of PT-{row['participant']} for AT-{aid}, "
                   "or of a later generation of its member",
                   f"PT-{part['id']}", "the courier that reserved it settles it")
        if row["outcome"] is not None:
            refuse("place", f"an unsettled attempt AT-{aid}", f"settled {row['outcome']}",
                   "reserve a new attempt; a settled one is not rewritten")
        conn.execute("UPDATE delivery_attempts SET outcome = ?, native_ref = ?, error = ?, "
                     "submitted_at = ? WHERE id = ?", (text, ref, error, stamp(), aid))
        if outcome in ("written", "accepted"):
            conn.execute("UPDATE messages SET delivered_at = ?, delivery_ref = ? "
                         "WHERE id = ? AND delivered_at IS NULL",
                         (stamp(), f"native:AT-{aid}:{ref or 'written'}", row["message"]))

    with instrument.transaction(conn):
        settle(conn)
    return attempt_dict(attempt_row(conn, aid))


def core_attempts(conn, part, *, message=None) -> dict:
    """The attempts of one message, or of every message, oldest first."""
    if conn is None:
        return no_store(attempts=[])
    if not has_table(conn, "delivery_attempts"):
        return {"attempts": [], "table": "absent: no writer since slice 2's tables"}
    if message is None:
        rows = conn.execute("SELECT * FROM delivery_attempts ORDER BY id").fetchall()
    else:
        _, mid = parse_id(message, "M")
        rows = conn.execute("SELECT * FROM delivery_attempts WHERE message = ? ORDER BY id",
                            (mid,)).fetchall()
    return {"attempts": [attempt_dict(row) for row in rows]}


def stale_attempts(conn, part) -> dict:
    """This participant's unsettled attempts, and its member's earlier
    generations', reserved longer than ATTEMPT_TTL ago: a crash between
    the submission and its settlement."""
    part = same_session(conn, part)
    if conn is None or not has_table(conn, "delivery_attempts"):
        return {"stale": []}
    kin = member_kin(conn, part["id"])
    rows = conn.execute(f"SELECT * FROM delivery_attempts WHERE participant IN ({marks(kin)}) "  # noqa: S608 - marks() is only placeholders
                        "AND outcome IS NULL ORDER BY id", kin).fetchall()
    moment = now()
    stale = [row for row in rows
             if (moment - parse_stamp(row["reserved_at"])).total_seconds() > ATTEMPT_TTL]
    return {"stale": [attempt_dict(row) for row in stale]}


def send_digest(*, to, purpose, body, assignment, decision, reply_to, artifact) -> str:
    payload = {"to": to, "purpose": purpose, "body": body, "assignment": assignment,
               "decision": decision, "reply_to": reply_to, "artifact": artifact}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def keyed_message(conn, part, key: str, digest: str):
    """The message a key already named, or None; another digest refuses."""
    if not has_table(conn, "send_requests"):
        return None
    row = conn.execute("SELECT * FROM send_requests WHERE participant = ? AND key = ?",
                       (part["id"], key)).fetchone()
    if row is None:
        return None
    if row["digest"] != digest:
        refuse("idempotency", f"key {key} with the payload it first named",
               f"M-{row['message']} under that key with another payload",
               "reuse a key only for the same message; a new message takes a new key")
    shown = message_dict(message_row(conn, row["message"]))
    shown["repeated"] = True
    return shown


CORE = {
    "bind": core_bind, "bind-for": core_bind_for, "grant": core_grant,
    "revoke": core_revoke,
    "who": core_who,
    "participants": core_participants,
    "draft": core_draft, "offer": core_offer, "claim": core_claim,
    "release": core_release, "reassign": core_reassign, "submit": core_submit,
    "verify": core_verify, "return": core_return, "accept": core_accept,
    "reopen": core_reopen, "supersede": core_supersede,
    "assignments": core_assignments,
    "show": core_show, "send": core_send, "deliver": core_deliver, "ack": core_ack,
    "resolve": core_resolve, "withdraw": core_withdraw, "inbox": core_inbox,
    "outstanding": core_outstanding, "undelivered": core_undelivered,
    "observe": core_observe,
    "observations": core_observations, "ask": core_ask, "answer": core_answer,
    "decide": core_decide, "decision": core_decision, "decisions": core_decisions,
    "reserve": core_reserve, "free": core_free, "resources": core_resources,

    "coverage": core_coverage, "brief": core_brief, "entry": core_entry,
    "route": core_route, "routes": core_routes, "attempt": core_attempt,
    "attempted": core_attempted, "attempts": core_attempts,
}


# -- the CLI projection: parse argv, call the function, print -----------------

def run_named(conn, run: str) -> int:
    """The sessions lever's run `bind --run` names, or refuse `absent`: a
    terminal with no managed launch names the run it joins (sessions-lever.md,
    the owner's terminals). A store no run was started in has none."""
    named = re.fullmatch(r"R-(\d+)", run)
    row = None
    if named:
        try:
            row = conn.execute("SELECT id FROM session_run WHERE id = ?", (int(named[1]),)).fetchone()
        except sqlite3.OperationalError:
            row = None
    if row is None:
        refuse("absent", "a run the sessions lever started, R-n", f"no run {run}",
               "sessions.py status lists the runs; bind --run names one of them")
    return row["id"]


def profile_named(rest: list[str]) -> list[str]:
    """Take `--profile PATH` off a verb's arguments and hand it to every read
    of the hook profile, as AGENTSCIENT_HOOK_PROFILE would; the flag and a
    different exported profile are two answers, refused `arguments`."""
    places = [index for index, word in enumerate(rest) if word == "--profile"]
    if not places:
        return rest
    if len(places) > 1 or places[0] + 1 >= len(rest):
        refuse("arguments", "--profile PATH once", " ".join(rest[places[0]:]) or "--profile",
               "name the terminal's profile.json once")
    path = rest[places[0] + 1]
    exported = os.environ.get("AGENTSCIENT_HOOK_PROFILE", "").strip()
    if exported and exported != path:
        refuse("arguments", "one hook profile", f"--profile {path} and AGENTSCIENT_HOOK_PROFILE {exported}",
               "drop one; the terminal reads a single profile")
    os.environ["AGENTSCIENT_HOOK_PROFILE"] = path
    return rest[:places[0]] + rest[places[0] + 2:]


def do_bind(rest):
    agent = identity()
    _, opts = parse(rest, 0, required=("roles",), optional=("run", "harness", "endpoint", "probe"))
    with writer() as conn:
        shown = CORE["bind"](conn, None, agent=agent,
                             roles=opts["roles"], harness=opts.get("harness"),
                             endpoint=opts.get("endpoint"), probe=opts.get("probe"), run=opts.get("run"))
    print(f"participant {shown['participant']} bound to session {shown['session']}")
    if "profile" in shown:
        print(f"profile {shown['profile']}")


def do_bind_for(rest):
    agent = identity()
    _, opts = parse(
        rest, 0, required=("session", "roles", "for", "capability-digest"),
        optional=("harness", "endpoint", "probe"),
    )
    with writer() as conn:
        shown = CORE["bind-for"](conn, bound_participant(conn), agent=agent,
                                 session_id=opts["session"], roles=opts["roles"],
                                 assignment=opts["for"], harness=opts.get("harness"),
                                 capability_digest=opts["capability-digest"],
                                 endpoint=opts.get("endpoint"), probe=opts.get("probe"))
    print(f"participant {shown['participant']} bound for session {shown['session']} "
          f"by {shown['bound_by']} for {shown['bound_for']}")


def do_grant(rest):
    agent = identity()
    (raw,), opts = parse(rest, 1, required=("roles",))
    with writer() as conn:
        shown = CORE["grant"](conn, bound_participant(conn), agent=agent,
                              participant=raw, roles=opts["roles"])
    print(f"granted {shown['roles']} to {shown['participant']} "
          f"by {shown['granted_by']}")


def do_revoke(rest):
    agent = identity()
    (raw,), opts = parse(rest, 1, required=("roles",))
    with writer() as conn:
        shown = CORE["revoke"](conn, bound_participant(conn), agent=agent,
                               participant=raw, roles=opts["roles"])
    print(f"revoked {opts['roles']} from {shown['participant']}; "
          f"{shown['roles']} left")




def do_who(rest):
    parse(rest, 0)
    conn = reader()
    if conn is None:
        print("no store")
        return EXIT_DONE
    shown = CORE["who"](conn, bound_participant(conn))
    print(f"participant {shown['participant']} {shown['roles']} "
          f"session {shown['session']}")
    return EXIT_DONE


def do_participants(rest):
    _, opts = parse(rest, 0, optional=("role",))
    shown = CORE["participants"](reader(), None, role=opts.get("role"))
    if shown.get("store") == "absent":
        print("no store")
        return EXIT_DONE
    if not shown["participants"]:
        print("no participants")
        return EXIT_DONE
    for row in shown["participants"]:
        issued = (f", bound by {row['bound_by']} for {row['bound_for']}"
                  if row["bound_by"] else "")
        if row.get("owner_grant"):
            issued += f", owner grant {row['owner_grant']}"
        print(f"{row['participant']} {row['roles']} — {row['agent']} "
              f"harness {row['harness'] or 'unrecorded'}"
              f"{issued}")
    return EXIT_DONE


# -- assignments: verbs ------------------------------------------------------

def do_draft(rest):
    agent = identity()
    (title,), opts = parse(
        rest, 1, required=("outcome",),
        optional=("requirement", "revision", "depends", "duty", "scope", "inputs"),
    )
    with writer() as conn:
        shown = CORE["draft"](conn, bound_participant(conn), agent=agent, title=title,
                              outcome=opts["outcome"], requirement=opts.get("requirement"),
                              revision=opts.get("revision"), depends=opts.get("depends"),
                              duty=opts.get("duty"), scope=opts.get("scope"),
                              inputs=opts.get("inputs"))
    print(f"assignment {shown['assignment']}")


def do_offer(rest):
    agent = identity()
    (raw,), opts = parse(rest, 1, required=("to", "generation"))
    with writer() as conn:
        shown = CORE["offer"](conn, bound_participant(conn), agent=agent, assignment=raw,
                              to=opts["to"], generation=opts["generation"])
    print(f"offered {shown['assignment']} to {shown['recipient']}")


def do_claim(rest):
    agent = identity()
    (raw,), opts = parse(rest, 1, required=("branch", "generation"), optional=("ttl",))
    with writer() as conn:
        shown = CORE["claim"](conn, bound_participant(conn), agent=agent, assignment=raw,
                              branch=opts["branch"], generation=opts["generation"],
                              ttl=opts.get("ttl"))
    print(f"claimed {shown['assignment']} by {shown['holder']} on {shown['branch']}")


def do_release(rest):
    agent = identity()
    (raw,), opts = parse(rest, 1, required=("why", "generation"))
    with writer() as conn:
        shown = CORE["release"](conn, bound_participant(conn), agent=agent, assignment=raw,
                                why=opts["why"], generation=opts["generation"])
    print(f"released {shown['assignment']} to {shown['recipient']}; "
          f"generation {shown['generation']}")


def do_reassign(rest):
    agent = identity()
    (raw,), opts = parse(rest, 1, required=("to", "why", "generation"))
    with writer() as conn:
        shown = CORE["reassign"](conn, bound_participant(conn), agent=agent, assignment=raw,
                                 to=opts["to"], why=opts["why"],
                                 generation=opts["generation"])
    print(f"reassigned {shown['assignment']} to {shown['recipient']}; "
          f"generation {shown['generation']}")


def do_submit(rest):
    agent = identity()
    (raw,), opts = parse(rest, 1, required=("evidence", "revision", "generation"))
    with writer() as conn:
        shown = CORE["submit"](conn, bound_participant(conn), agent=agent, assignment=raw,
                               evidence=opts["evidence"], revision=opts["revision"],
                               generation=opts["generation"])
    print(f"submitted {shown['assignment']} at {shown['revision']}")


def do_verify(rest):
    agent = identity()
    (raw,), opts = parse(rest, 1, required=("candidate", "evidence", "generation"))
    with writer() as conn:
        shown = CORE["verify"](conn, bound_participant(conn), agent=agent, assignment=raw,
                               candidate=opts["candidate"], evidence=opts["evidence"],
                               generation=opts["generation"])
    print(f"verified {shown['assignment']} by {shown['verified_by']}")


def do_return(rest):
    agent = identity()
    (raw,), opts = parse(rest, 1, required=("findings", "generation"))
    with writer() as conn:
        shown = CORE["return"](conn, bound_participant(conn), agent=agent, assignment=raw,
                               findings=opts["findings"], generation=opts["generation"])
    print(f"returned {shown['assignment']} to {shown['holder']}")


def do_accept(rest):
    agent = identity()
    (raw,), opts = parse(rest, 1, required=("evidence", "generation"),
                         optional=("exception",))
    with writer() as conn:
        shown = CORE["accept"](conn, bound_participant(conn), agent=agent, assignment=raw,
                               evidence=opts["evidence"], generation=opts["generation"],
                               exception=opts.get("exception"))
    line = f"accepted {shown['assignment']} by {shown['accepted_by']}"
    if shown.get("exception"):
        line += f", under a recorded exception: {shown['exception']}"
    print(line)


def do_reopen(rest):
    agent = identity()
    (raw,), opts = parse(rest, 1, required=("revision", "why", "generation"))
    with writer() as conn:
        shown = CORE["reopen"](conn, bound_participant(conn), agent=agent, assignment=raw,
                               revision=opts["revision"], why=opts["why"],
                               generation=opts["generation"])
    print(f"reopened {shown['assignment']} at {shown['revision']}; "
          f"generation {shown['generation']}")


def do_supersede(rest):
    agent = identity()
    (raw,), opts = parse(rest, 1, required=("by", "generation"))
    with writer() as conn:
        shown = CORE["supersede"](conn, bound_participant(conn), agent=agent, assignment=raw,
                                  by=opts["by"], generation=opts["generation"])
    print(f"superseded {shown['assignment']} by {shown['superseded_by']}")


def assignment_line(row: dict) -> str:
    who = row["holder"] or row["recipient"] or "nobody"
    return (f"{row['assignment']} {row['title']} — {row['place']}, {who}, "
            f"generation {row['generation']}, revision {row['revision'] or 'none'}")


def do_assignments(rest):
    _, opts = parse(rest, 0, optional=("state", "to"), flags=("mine",))
    conn = reader()
    part = bound_participant(conn) if opts.get("mine") else None
    shown = CORE["assignments"](conn, part, state=opts.get("state"), to=opts.get("to"))
    if shown.get("store") == "absent":
        print("no store")
        return EXIT_DONE
    if not shown["assignments"]:
        print("no assignments")
        return EXIT_DONE
    for row in shown["assignments"]:
        print(assignment_line(row))
    return EXIT_DONE


# -- messages: verbs ---------------------------------------------------------

def do_send(rest):
    agent = identity()
    _, opts = parse(
        rest, 0, required=("to", "purpose", "body"),
        optional=("assignment", "decision", "reply-to", "artifact", "key"),
    )
    with writer() as conn:
        shown = CORE["send"](conn, bound_participant(conn), agent=agent, to=opts["to"],
                             purpose=opts["purpose"], body=opts["body"],
                             assignment=opts.get("assignment"),
                             decision=opts.get("decision"),
                             reply_to=opts.get("reply-to"), artifact=opts.get("artifact"),
                             key=opts.get("key"))
    print(f"message {shown['message']} queued" + (" (repeated)" if shown.get("repeated") else ""))


def do_deliver(rest):
    identity()
    (raw,), opts = parse(rest, 1, required=("ref",))
    with writer() as conn:
        shown = CORE["deliver"](conn, bound_participant(conn), message=raw, ref=opts["ref"])
    print(f"delivered {shown['message']} ref {shown['delivery_ref']}")


def do_ack(rest):
    identity()
    (raw,), _ = parse(rest, 1)
    with writer() as conn:
        shown = CORE["ack"](conn, bound_participant(conn), message=raw)
    print(f"acknowledged {shown['message']} by {shown['acked_by']}; not resolved")


def do_resolve(rest):
    agent = identity()
    (raw,), opts = parse(rest, 1, required=("evidence",))
    with writer() as conn:
        shown = CORE["resolve"](conn, bound_participant(conn), agent=agent, message=raw,
                                evidence=opts["evidence"])
    print(f"resolved {shown['message']}: {shown['evidence']}")


def do_withdraw(rest):
    agent = identity()
    (raw,), opts = parse(rest, 1, required=("why",))
    with writer() as conn:
        shown = CORE["withdraw"](conn, bound_participant(conn), agent=agent, message=raw,
                                 why=opts["why"])
    print(f"withdrew {shown['message']}")


def message_line(row: dict) -> str:
    tail = f" — {row['place']}" if row["place"] != "open" else ""
    return (f"{row['message']} {row['purpose']} from {row['sender']} to "
            f"{row['recipient']}: {row['body']}{tail}")


def do_inbox(rest):
    _, opts = parse(rest, 0, flags=("unresolved",))
    conn = reader()
    if conn is None:
        print("no store")
        return EXIT_DONE
    shown = CORE["inbox"](conn, bound_participant(conn), unresolved=bool(opts.get("unresolved")))
    if not shown["messages"]:
        print("no messages")
        return EXIT_DONE
    for row in shown["messages"]:
        print(message_line(row))
    return EXIT_DONE


def do_undelivered(rest):
    _, opts = parse(rest, 0, optional=("to",))
    conn = reader()
    if conn is None:
        print("no store")
        return EXIT_DONE
    shown = CORE["undelivered"](conn, bound_participant(conn), to=opts.get("to"))
    if not shown["undelivered"]:
        print("none undelivered")
        return EXIT_DONE
    for row in shown["undelivered"]:
        print(message_line(row) + f", queued {row['queued_at']}")
    return EXIT_DONE


def do_outstanding(rest):
    _, opts = parse(rest, 0, optional=("to",))
    shown = CORE["outstanding"](reader(), None, to=opts.get("to"))
    if shown.get("store") == "absent":
        print("no store")
        return EXIT_DONE
    if not shown["outstanding"]:
        print("none outstanding")
        return EXIT_DONE
    for row in shown["outstanding"]:
        seen = f", acknowledged {row['acked_at']}" if row["acked_at"] else ", not acknowledged"
        print(message_line(row) + seen)
    return EXIT_DONE


# -- delivery: verbs ---------------------------------------------------------

def do_route(rest):
    """`route --take R [--member SM-n]` or `route --release`, held by the
    calling process's parent: the shell or courier that ran this verb."""
    identity()
    _, opts = parse(rest, 0, optional=("take", "member"), flags=("release",))
    if ("take" in opts) == bool(opts.get("release")):
        refuse("arguments", "one of --take stdout|native or --release",
               "both" if "take" in opts else "neither", usage())
    holder = os.getppid()
    ticks = process_ticks(holder)
    with writer() as conn:
        if opts.get("release"):
            shown = CORE["route"](conn, bound_participant(conn), action="release",
                                  pid=holder, start_ticks=ticks)
            print(f"released {shown['released']}" if shown["released"] else "no route held")
            return EXIT_DONE
        shown = CORE["route"](conn, bound_participant(conn), action="take", route=opts["take"],
                              pid=holder, start_ticks=ticks, member=opts.get("member"))
    tail = f"; took over from {shown['took_over']}" if shown["took_over"] else ""
    print(f"route {shown['route']} held by pid={shown['pid']} for {shown['participant']}{tail}")
    return EXIT_DONE


def do_routes(rest):
    parse(rest, 0)
    shown = CORE["routes"](reader(), None)
    if shown.get("store") == "absent":
        print("no store")
        return EXIT_DONE
    if "table" in shown:
        print(f"no routes: {shown['table']}")
        return EXIT_DONE
    if not shown["routes"]:
        print("no routes")
        return EXIT_DONE
    for row in shown["routes"]:
        member = f" {row['member']}" if row["member"] else ""
        print(f"{row['participant']}{member} route {row['route']} pid={row['pid']} "
              f"since {row['since']}, {'alive' if row['alive'] else 'dead'}")
    return EXIT_DONE


def do_attempt(rest):
    identity()
    (raw,), opts = parse(rest, 1, required=("route", "generation"))
    with writer() as conn:
        shown = CORE["attempt"](conn, bound_participant(conn), message=raw,
                                route=opts["route"], generation=opts["generation"])
    print(f"attempt {shown['attempt']} reserved for {shown['message']}")
    return EXIT_DONE


def do_attempted(rest):
    identity()
    (raw,), opts = parse(rest, 1, required=("outcome",), optional=("ref", "error"))
    with writer() as conn:
        shown = CORE["attempted"](conn, bound_participant(conn), attempt=raw,
                                  outcome=opts["outcome"], ref=opts.get("ref"),
                                  error=opts.get("error"))
    print(f"attempt {shown['attempt']} {shown['outcome']}")
    return EXIT_DONE


def attempt_line(row: dict) -> str:
    return (f"{row['attempt']} {row['message']} to {row['participant']} generation "
            f"{row['generation']} route {row['route']} reserved {row['reserved_at']}: "
            f"{row['outcome'] or 'unsettled'}")


def do_attempts(rest):
    (raw,), _ = parse(rest, 1)
    shown = CORE["attempts"](reader(), None, message=raw)
    if shown.get("store") == "absent":
        print("no store")
        return EXIT_DONE
    if "table" in shown:
        print(f"no attempts: {shown['table']}")
        return EXIT_DONE
    if not shown["attempts"]:
        print(f"no attempts of {raw}")
        return EXIT_DONE
    for row in shown["attempts"]:
        print(attempt_line(row))
    return EXIT_DONE


# -- observations, decisions, resources --------------------------------------

def do_observe(rest):
    identity()
    (raw,), opts = parse(
        rest, 1, required=("observation", "revision", "generation"),
        optional=("execution", "artifact", "requirement", "environment"),
        flags=("stale",),
    )
    with writer() as conn:
        shown = CORE["observe"](conn, bound_participant(conn), assignment=raw,
                                observation=opts["observation"], revision=opts["revision"],
                                generation=opts["generation"],
                                stale=bool(opts.get("stale")),
                                execution=opts.get("execution"),
                                artifact=opts.get("artifact"),
                                requirement=opts.get("requirement"),
                                environment=opts.get("environment"))
    print(f"observation {shown['observation']}")


def do_observations(rest):
    (raw,), opts = parse(rest, 1, flags=("all",))
    shown = CORE["observations"](reader(), None, assignment=raw,
                                 include_stale=bool(opts.get("all")))
    if shown.get("store") == "absent":
        print("no store")
        return EXIT_DONE
    if not shown["observations"]:
        print("no observations")
        return EXIT_DONE
    for row in shown["observations"]:
        mark = "" if row["applicability"] == "current" else f" [stale: {row['stale_reason']}]"
        where = f" ({row['execution']})" if row["execution"] else ""
        print(f"{row['observation']} {row['producer']} {row['revision']} — "
              f"{row['text']}{where}{mark}")
    return EXIT_DONE


def do_ask(rest):
    identity()
    _, opts = parse(rest, 0, required=("question", "alternatives"), optional=("assignment",))
    with writer() as conn:
        shown = CORE["ask"](conn, bound_participant(conn), question=opts["question"],
                            alternatives=opts["alternatives"],
                            assignment=opts.get("assignment"))
    print(f"decision {shown['decision']}")


def do_answer(rest):
    identity()
    (raw,), opts = parse(rest, 1, optional=("counter", "unknowns", "consequence"))
    with writer() as conn:
        shown = CORE["answer"](conn, bound_participant(conn), decision=raw,
                               counter=opts.get("counter"), unknowns=opts.get("unknowns"),
                               consequence=opts.get("consequence"))
    print(f"answered {shown['decision']}")


def do_decide(rest):
    identity()
    (raw,), opts = parse(rest, 1, required=("choice", "rationale"))
    with writer() as conn:
        shown = CORE["decide"](conn, bound_participant(conn), decision=raw, choice=opts["choice"],
                               rationale=opts["rationale"])
    print(f"decided {shown['decision']}: {shown['choice']}")


def print_decision(brief: dict) -> int:
    print(f"{brief['decision']} {brief['question']}")
    print(f"alternatives: {brief['alternatives']}")
    for label, key in (("counterevidence", "counter"), ("unknowns", "unknowns"),
                       ("consequence", "consequence")):
        print(f"{label}: {brief[key] or 'a declared gap, not a pass'}")
    print(f"observations: {len(brief['observations'])} current")
    if brief["decided_at"]:
        print(f"decided by {brief['decided_by']} at {brief['decided_at']}: "
              f"{brief['choice']} — {brief['rationale']}")
    else:
        print("undecided")
    return EXIT_DONE


def do_decision(rest):
    (raw,), _ = parse(rest, 1)
    shown = CORE["decision"](reader(), None, decision=raw)
    if shown.get("store") == "absent":
        print("no store")
        return EXIT_DONE
    return print_decision(shown)


def do_decisions(rest):
    _, opts = parse(rest, 0, flags=("open",))
    shown = CORE["decisions"](reader(), None, undecided_only=bool(opts.get("open")))
    if shown.get("store") == "absent":
        print("no store")
        return EXIT_DONE
    if not shown["decisions"]:
        print("no decisions")
        return EXIT_DONE
    for row in shown["decisions"]:
        state = row["choice"] if row["decided_at"] else "undecided"
        print(f"{row['decision']} {row['question']} — {state}")
    return EXIT_DONE


def do_reserve(rest):
    agent = identity()
    (name,), opts = parse(rest, 1, required=("for",), optional=("ttl",))
    with writer() as conn:
        shown = CORE["reserve"](conn, bound_participant(conn), agent=agent, name=name,
                                assignment=opts["for"], ttl=opts.get("ttl"))
    print(f"reserved {shown['name']} for {shown['assignment']} by {shown['holder']}")


def do_free(rest):
    agent = identity()
    (name,), opts = parse(rest, 1, required=("why",))
    with writer() as conn:
        shown = CORE["free"](conn, bound_participant(conn), agent=agent, name=name, why=opts["why"])
    note = ("" if shown["reconciled"]
            else f"; {shown['previous_holder']}'s run is not reconciled")
    print(f"freed {shown['name']}{note}")


def do_resources(rest):
    parse(rest, 0)
    shown = CORE["resources"](reader(), None)
    if shown.get("store") == "absent":
        print("no store")
        return EXIT_DONE
    if not shown["resources"]:
        print("no resources")
        return EXIT_DONE
    for row in shown["resources"]:
        print(f"{row['resource']} {row['name']} — {row['place']}, "
              f"{row['holder'] or 'free'}, A-{row['assignment_id'] or 0}")
    return EXIT_DONE


# -- the joined views --------------------------------------------------------

def do_show(rest):
    (raw,), _ = parse(rest, 1)
    shown = CORE["show"](reader(), None, target=raw)
    if shown.get("store") == "absent":
        print("no store")
        return EXIT_DONE
    if raw.startswith("DC-"):
        return print_decision(shown)
    if raw.startswith("M-"):
        print(message_line(shown))
        print(f"queued {shown['queued_at']}")
        if shown["delivered_at"]:
            print(f"delivered {shown['delivered_at']} ref {shown['delivery_ref']}")
        if shown["acked_at"]:
            print(f"acknowledged {shown['acked_at']} by {shown['acked_by']}")
        return EXIT_DONE
    print(assignment_line(shown))
    print(f"outcome: {shown['outcome']}")
    print(f"requirement: {shown['requirement'] or 'none'}; "
          f"branch: {shown['branch'] or 'none'}; depends: {shown['depends'] or 'none'}")
    print(f"observations: {shown['observations']['current']} current, "
          f"{shown['observations']['stale']} stale")
    print("marking:")
    for line in shown["marking"]:
        print(f"  {line}")
    return EXIT_DONE


def do_coverage(rest):
    _, opts = parse(rest, 0, optional=("requirement",))
    shown = CORE["coverage"](reader(), None, requirement=opts.get("requirement"))
    if shown.get("store") == "absent":
        print("no store")
        return EXIT_DONE
    if not shown["coverage"]:
        print("no assignments")
        return EXIT_DONE
    for row in shown["coverage"]:
        print(f"{row['requirement'] or 'requirement unstated'} → {row['assignment']} "
              f"{row['title']} → candidate {row['revision'] or 'none'} → "
              f"{row['observations']} observation(s) → {row['closure']}")
    return EXIT_DONE


def do_brief(rest):
    parse(rest, 0)
    conn = reader()
    if conn is None:
        print("no store")
        return EXIT_DONE
    shown = CORE["brief"](conn, bound_participant(conn))
    who = shown["participant"]
    print(f"brief for {who['participant']} {who['roles']}")
    print("assignments held:")
    for row in shown["held"]:
        print(f"  {assignment_line(row)}")
    if not shown["held"]:
        print("  none")
    print("inbox unresolved:")
    for row in shown["unresolved"]:
        print(f"  {message_line(row)}")
    if not shown["unresolved"]:
        print("  none")
    if shown["executions"] is None:
        print(f"executions: could not look — {shown['executions_reason']}")
    elif not shown["executions"]:
        print("executions: none")
    else:
        print("executions:")
        for row in shown["executions"]:
            print(f"  {row['execution']} {row['status']} for {row['assignment']}"
                  f" ({row['job'] or 'job unstated'}, joined by {row['joined_by']})")
    for label, rows in (("requests owed by me", shown["owed"]),
                        ("requests I am waiting on", shown["awaited"])):
        if not rows:
            print(f"{label}: none")
            continue
        print(f"{label}:")
        for row in rows:
            state = "acknowledged, not resolved" if row["acked_at"] else "not acknowledged"
            print(f"  {row['message']} {row['purpose']} from {row['sender']} "
                  f"to {row['recipient']}: {row['body']} ({state})")
    if not shown["decisions"]:
        print("decisions open: none")
    else:
        print("decisions open:")
        for row in shown["decisions"]:
            print(f"  {row['decision']} asked by {row['asked_by']}"
                  f": {row['question']} ({row['alternatives']})")
    evidence = shown["evidence"]["current"] + shown["evidence"]["stale"]
    if not evidence:
        print("evidence of mine: none")
    else:
        print("evidence of mine:")
        for row in shown["evidence"]["current"]:
            print(f"  {row['observation']} current at {row['revision']}: {row['text']}")
        for row in shown["evidence"]["stale"]:
            print(f"  {row['observation']} stale: taken at {row['revision']}, "
                  f"{row['stale_reason'] or 'reason unstated'}")
    return EXIT_DONE


def do_entry(rest):
    _, opts = parse(rest, 0, optional=("role",))
    conn = reader()
    shown = CORE["entry"](conn, maybe_participant(conn), role=opts.get("role"))
    state = shown["state"]
    print("entry point for the coordination lane")
    if state["bound"]:
        print(f"state: bound as {state['participant']}, roles {state['roles']}; "
              f"{state['held']} assignment held, {state['owed']} request owed by me, "
              f"{state['awaited']} awaited, {state['decisions_open']} decision open")
    else:
        print("state: not bound in this session; the store is "
              + ("read" if state["store"] == "read" else "absent"))
    for block in shown["roles"]:
        print(f"{block['role']} — {block['headline']}")
        print("  duties:")
        for line in block["duties"]:
            print(f"    - {line}")
        print("  next:")
        for line in block["next"]:
            print(f"    - {line}")
    print(f"contract: {shown['contract']}; the ladder it stands on: {shown['ladder']}")
    return EXIT_DONE


VERBS = {
    "bind": do_bind, "bind-for": do_bind_for, "grant": do_grant,
    "revoke": do_revoke,
    "who": do_who,
    "participants": do_participants,
    "draft": do_draft, "offer": do_offer, "claim": do_claim,
    "release": do_release, "reassign": do_reassign, "submit": do_submit,
    "verify": do_verify, "return": do_return, "accept": do_accept,
    "reopen": do_reopen, "supersede": do_supersede, "assignments": do_assignments,
    "show": do_show, "send": do_send, "deliver": do_deliver, "ack": do_ack,
    "resolve": do_resolve, "withdraw": do_withdraw, "inbox": do_inbox,
    "outstanding": do_outstanding, "undelivered": do_undelivered,
    "observe": do_observe,
    "observations": do_observations, "ask": do_ask, "answer": do_answer,
    "decide": do_decide, "decision": do_decision, "decisions": do_decisions,
    "reserve": do_reserve, "free": do_free, "resources": do_resources,

    "coverage": do_coverage, "brief": do_brief, "entry": do_entry,
    "route": do_route, "routes": do_routes, "attempt": do_attempt,
    "attempted": do_attempted, "attempts": do_attempts,
}


def main(argv: list[str]) -> int:
    verb = argv[1] if len(argv) > 1 else ""
    try:
        if verb not in VERBS:
            refuse("arguments", "one of " + ", ".join(sorted(VERBS)), verb or "no verb",
                   "name one verb; the module doc has the arguments")
        return VERBS[verb](profile_named(argv[2:])) or EXIT_DONE
    except Refusal as refusal:
        return emit(refusal)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
