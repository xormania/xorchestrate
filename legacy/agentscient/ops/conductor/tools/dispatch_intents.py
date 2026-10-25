#!/usr/bin/env python3
"""Dispatch intents: the ask, persisted before anything is launched.

Contract: ops/conductor/sessions-lever.md, "Slice 3 — Dispatcher
integration", the shapes. The table lives in the coordination core's
store and is created here, through the core's own `writer()`; this
module edits `coordination.py` nowhere. `DI-n` is the table's own id; the
place is the engine's (machine.py), on the subject the `subject` column
names, and the `phase` column repeats the place inside the same
transaction so a plain SELECT reads it.

    dispatch_intents.py reserve --requester PT-n --dispatcher PT-n
        --assignment A-n --key K --job J --harness H --branch B
        --scope-ref TEXT --inputs PATH... [--correlation UUID]
    dispatch_intents.py launch --correlation UUID
    dispatch_intents.py mint --correlation UUID --record ID

The three verbs move one intent a step at a time, for the Dispatcher
alone; `dispatch_actions.py` is the launch that runs them in order with
the wrapper between, and `dispatch_mcp.py` its MCP projection. Stdlib
only.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
import uuid
from pathlib import Path

# The shared instrument module sits beside this file; its directory goes
# on the path so the import resolves however this file was loaded.
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
try:
    import coordination
    import dispatcher
    import machine
    from instrument import (
        Refusal,
        conf,
        connect_store,
        emit,
        identity,
        refuse,
        session,
        stamp,
        state_dir,
    )
except ModuleNotFoundError:
    print(
        "tooling: expected instrument.py, machine.py, coordination.py and dispatcher.py beside "
        "this tool; found absent; needed restore them beside ops/conductor/tools/dispatch_intents.py",
        file=sys.stderr,
    )
    raise SystemExit(2) from None

ID = re.compile(r"^DI-(\d+)$")
PHASES = ("reserved", "launching", "launched", "closed", "uncertain", "refused")

T = machine.Transition
# The contract's `refuse` leaves three places; the engine declares one
# source per transition, so it is one row per source, as sessions.py
# declares its stop and lose (XO ruling 2026-09-23 on S3's point 4).
INTENT = machine.Machine(
    name="dispatch-intent",
    version=1,
    initial="reserved",
    places=PHASES,
    transitions=(
        T("launch", "reserved", "launching", "any", "required", "place", "keep"),
        T("mint", "launching", "launched", "any", "required", "place", "keep"),
        T("close", "launched", "closed", "any", "required", "place", "keep"),
        T("lose", "launching", "uncertain", "any", "required", "place", "keep"),
        T("reconcile", "uncertain", "launched", "any", "required", "place", "keep"),
        *(T(f"refuse-{place}", place, "refused", "any", "required", "place", "keep")
          for place in ("reserved", "launching", "uncertain")),
    ),
    ttl_s=12 * 3600,
)
SCHEMA = """
CREATE TABLE IF NOT EXISTS dispatch_intents(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL, digest TEXT NOT NULL,
    requester INTEGER NOT NULL, dispatcher INTEGER NOT NULL,
    assignment INTEGER NOT NULL, generation INTEGER NOT NULL,
    job TEXT NOT NULL, harness TEXT NOT NULL,
    branch TEXT NOT NULL, scope_ref TEXT NOT NULL,
    inputs TEXT NOT NULL, correlation TEXT NOT NULL UNIQUE,
    subject INTEGER NOT NULL UNIQUE,
    phase TEXT NOT NULL, record TEXT, launcher_pid INTEGER,
    launcher_ticks TEXT, reserved_at TEXT NOT NULL,
    launched_at TEXT, closed_at TEXT, outcome TEXT, error TEXT,
    reported_at TEXT,
    UNIQUE (requester, key));
"""
TABLE = "dispatch_intents"
ROOT = Path(__file__).resolve().parents[3]
# The programs the facade runs and its cap, read through the lane conf
# (the environment first, so a test names a fake in their place). The
# keys are iterated from this tuple so laneconf's closure check sees
# each read (laneconf.py, `CONF_KEYS`).
CONF_KEYS = ("DISPATCH_WRAPPER", "DISPATCH_LAUNCHER", "DISPATCH_MONITOR", "DISPATCH_CAPACITY")
# The contract's defaults ("The programs it runs"), for a lane conf that
# does not declare a key; a value it does declare, or the environment's,
# always wins.
DEFAULTS = {
    "DISPATCH_WRAPPER": "ops/conductor/tools/dispatch.sh",
    "DISPATCH_LAUNCHER": "ops/devlane/dispatch/launch.py",
    "DISPATCH_MONITOR": "ops/conductor/tools/monitor.py",
    "DISPATCH_CAPACITY": "6",
}
# What each move writes beside the place, in the move's own transaction.
STAMPED = {"mint": "launched_at", "reconcile": "launched_at", "close": "closed_at"}


def writer() -> sqlite3.Connection:
    """The core's store with this table and machine in it."""
    try:
        conn = coordination.writer()
        conn.executescript(SCHEMA)
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({TABLE})")}
        if "reported_at" not in columns:
            # A store from before the follow-through kept closed intents: the
            # ones already closed are marked, so the first pass after the
            # column lands reports none of them again.
            with conn:
                conn.execute(f"ALTER TABLE {TABLE} ADD COLUMN reported_at TEXT")
                conn.execute(f"UPDATE {TABLE} SET reported_at = 'before reporting' "  # noqa: S608 - the module's own table
                             "WHERE phase = 'closed'")
    except sqlite3.DatabaseError as error:
        refuse("store", f"a writable SQLite store at {machine.store_path()}", str(error),
               "restore or move the store aside; the engine never rebuilds one to order")
    machine.declare(conn, INTENT)
    return conn


def reader() -> sqlite3.Connection | None:
    """The store read-only, or None when it holds no intents; never created."""
    path = machine.store_path()
    if not path.is_file():
        return None
    try:
        conn = connect_store(path, timeout=machine.BUSY_TIMEOUT_MS / 1000)
        conn.row_factory = sqlite3.Row
        held = {row["name"] for row in
                conn.execute("SELECT name FROM sqlite_schema WHERE type = 'table'")}
    except sqlite3.DatabaseError as error:
        refuse("store", f"a readable SQLite store at {path}", str(error),
               "restore or move the store aside; a read never rebuilds it")
    if TABLE not in held or "subjects" not in held:
        conn.close()
        return None
    return conn


def parse_id(text: str) -> int:
    match = ID.match(text or "")
    if match is None:
        refuse("arguments", "an intent id like DI-1", text or "none",
               "the id launch printed")
    return int(match.group(1))


def digest_of(*, assignment: int, job: str, harness: str, branch: str,
              scope_ref: str, inputs: list) -> str:
    """sha256 over the canonical JSON of the six fields the contract names."""
    body = {"assignment": assignment, "job": job, "harness": harness,
            "branch": branch, "scope_ref": scope_ref, "inputs": inputs}
    packed = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(packed.encode("utf-8")).hexdigest()


def row(conn, di: int):
    """One intent with its engine place, or refuse `unknown`."""
    found = None
    if conn is not None:
        found = conn.execute(
            "SELECT i.*, s.place FROM dispatch_intents i JOIN subjects s ON s.id = i.subject "
            "WHERE i.id = ?", (di,)).fetchone()
    if found is None:
        refuse("unknown", "an intent id in the store", f"DI-{di}",
               "an id printed by dispatch_actions.py launch")
    return found


def by_key(conn, requester: int, key: str):
    return conn.execute(
        "SELECT i.*, s.place FROM dispatch_intents i JOIN subjects s ON s.id = i.subject "
        "WHERE i.requester = ? AND i.key = ?", (requester, key)).fetchone()


def by_correlation(conn, correlation: str):
    found = conn.execute(
        "SELECT i.*, s.place FROM dispatch_intents i JOIN subjects s ON s.id = i.subject "
        "WHERE i.correlation = ?", (correlation,)).fetchone()
    if found is None:
        refuse("unknown", "an intent with this correlation in the store", correlation or "none",
               "the correlation reserve printed")
    return found


def by_record(conn, record: str):
    return conn.execute(
        "SELECT i.*, s.place FROM dispatch_intents i JOIN subjects s ON s.id = i.subject "
        "WHERE i.record = ?", (record,)).fetchone()


def watched(conn) -> list:
    """The intents whose outcome is still to reach the requester: every one
    at `launched`, and every one `closed` before its event was sent (a job
    waited and collected while the router was down or inside one tick)."""
    rows = in_phases(conn, ("launched", "closed"))
    return [found for found in rows if found["place"] == "launched"
            or ("reported_at" in found.keys()  # noqa: SIM118 - a Row's `in` reads values, not names
                and found["reported_at"] is None)]


def reported(conn, di: int) -> None:
    """The intent's outcome was sent; a closed intent leaves the pass."""
    with conn:
        conn.execute(f"UPDATE {TABLE} SET reported_at = ? WHERE id = ? AND reported_at IS NULL",  # noqa: S608 - the module's own table
                     (stamp(), di))


def in_phases(conn, phases) -> list:
    marks = ",".join("?" for _ in phases)
    return conn.execute(
        "SELECT i.*, s.place FROM dispatch_intents i JOIN subjects s ON s.id = i.subject "  # noqa: S608 - placeholders only
        f"WHERE s.place IN ({marks}) ORDER BY i.id", tuple(phases)).fetchall()


def reserve(conn, agent: str, *, key: str, digest: str, requester: int, dispatcher: int,
            assignment: int, generation: int, job: str, harness: str, branch: str,
            scope_ref: str, inputs: list, correlation: str | None = None):
    """The intent row at `reserved`: (row, repeated).

    The same (requester, key) with the same digest is the row already
    there; with another digest it refuses `idempotency` naming it and
    writes nothing. Two reservations racing on one key both create a
    subject; the loser's insert fails on the key, and its subject is
    moved to `refused` with the winner named, so the store holds a
    record of it rather than an orphan at `reserved`.
    """
    existing = by_key(conn, requester, key)
    if existing is not None:
        return repeat(existing, digest), True
    sid = machine.create(conn, INTENT, agent, note=f"key {key}")
    try:
        cursor = conn.execute(
            "INSERT INTO dispatch_intents(key, digest, requester, dispatcher, assignment, "
            "generation, job, harness, branch, scope_ref, inputs, correlation, subject, phase, "
            "reserved_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'reserved', ?)",
            (key, digest, requester, dispatcher, assignment, generation, job, harness,
             branch, scope_ref, json.dumps(inputs, sort_keys=True),
             correlation or str(uuid.uuid4()), sid, stamp()),
        )
    except sqlite3.IntegrityError:
        winner = by_key(conn, requester, key)
        machine.move(conn, INTENT, sid, "refuse-reserved", agent,
                     evidence=f"key {key} taken by DI-{winner['id'] if winner else '?'}")
        if winner is None:
            raise
        return repeat(winner, digest), True
    return row(conn, cursor.lastrowid), False


def repeat(existing, digest: str):
    if existing["digest"] != digest:
        refuse("idempotency", f"the request DI-{existing['id']} was reserved for under this key",
               f"DI-{existing['id']} with another digest",
               "ask again with a new key, or repeat the original request unchanged")
    return existing


def move(conn, di: int, transition: str, agent: str, *, evidence: str, **columns):
    """One move of the intent's machine, with the phase and any named
    columns written inside the engine's own transaction (the gate runs
    under its write lock), so the row and the place never disagree."""
    target = INTENT.transition(transition).target
    updates = dict(columns)
    updates["phase"] = target
    if transition in STAMPED:
        updates.setdefault(STAMPED[transition], stamp())
    names = sorted(updates)
    assignments = ", ".join(f"{name} = ?" for name in names)

    def gate(_subject):
        conn.execute(f"UPDATE dispatch_intents SET {assignments} WHERE id = ?",  # noqa: S608 - names are this module's own
                     (*(updates[name] for name in names), di))

    machine.move(conn, INTENT, row(conn, di)["subject"], transition, agent,
                 evidence=evidence, gate=gate)
    return row(conn, di)


def note(conn, di: int, **columns) -> None:
    """Columns that are not a move: the launcher's pid and ticks at spawn."""
    names = sorted(columns)
    assignments = ", ".join(f"{name} = ?" for name in names)
    conn.execute(f"UPDATE dispatch_intents SET {assignments} WHERE id = ?",  # noqa: S608 - names are this module's own
                 (*(columns[name] for name in names), di))


def intent_dict(found) -> dict:
    return {
        "intent": f"DI-{found['id']}", "phase": found["place"],
        "record": found["record"], "job": found["job"], "harness": found["harness"],
        "branch": found["branch"], "assignment": f"A-{found['assignment']}",
        "generation": found["generation"], "requester": f"PT-{found['requester']}",
        "dispatcher": f"PT-{found['dispatcher']}", "correlation": found["correlation"],
        "outcome": found["outcome"], "error": found["error"],
    }


# -- the programs, the jobs root and the launcher's liveness ----------------

def settings() -> dict:
    found = {}
    for key in CONF_KEYS:
        try:
            found[key] = conf(key)
        except Refusal as refusal:
            if refusal.kind != "conf-key":
                raise
            found[key] = DEFAULTS[key]
        except KeyError:
            found[key] = DEFAULTS[key]
    return found


def program(key: str, kind: str) -> list[str]:
    """One program as an argv of one path, or refuse `kind` naming it.

    A relative value is the repository's; nothing is looked up on PATH
    and nothing runs through a shell."""
    value = settings()[key].strip()
    path = Path(value) if os.path.isabs(value) else ROOT / value
    if not value or not path.is_file() or not os.access(path, os.X_OK):
        refuse(kind, f"{key} naming an executable file", str(path) if value else "empty",
               f"set {key} to an executable, or restore its default in ops/lane.conf")
    return [str(path)]


def capacity() -> int:
    raw = settings()["DISPATCH_CAPACITY"].strip()
    if not raw.isdigit() or int(raw) < 1:
        refuse("capacity", "DISPATCH_CAPACITY as a whole number above zero", raw or "empty",
               "set DISPATCH_CAPACITY in ops/lane.conf or the environment")
    return int(raw)


def jobs_root() -> Path:
    named = os.environ.get("DISPATCH_JOBS", "")
    return Path(named) if named else coordination.dispatch_store()


def intent_dir(di: int) -> Path:
    return state_dir() / "dispatch-intents" / f"DI-{di}"


start_ticks = coordination.process_ticks


def process_reading(pid: int | None, ticks: str | None) -> str:
    """The pid and its start ticks, both present and matching, or why not;
    a missing tick is never alive."""
    if pid is None:
        return "gone no launcher pid recorded"
    if ticks is None:
        return f"gone no start ticks recorded for pid={pid}"
    current = start_ticks(pid)
    if current is None:
        return f"gone pid={pid} start={ticks}"
    if current != ticks:
        return f"gone reused pid={pid} start={current}, recorded {ticks}"
    return f"alive pid={pid} start={ticks}"


def alive(pid: int | None, ticks: str | None) -> bool:
    return process_reading(pid, ticks).startswith("alive")


def state_of(job: Path) -> dict:
    try:
        data = json.loads((job / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def logged_record(log: Path, correlation: str) -> str | None:
    """The record a launcher log names when dispatch.sh wrote this
    correlation as its first line, or None."""
    try:
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    if not lines or lines[0] != f"correlation: {correlation}":
        return None
    if log.name == "launcher.log":
        return log.parent.name
    return next((line[len("record: "):].strip() for line in lines if line.startswith("record: ")), None)


def scan(correlation: str) -> list:
    """Every job directory carrying this correlation, one entry per record:
    its state.json's `correlation` (launch.py, once the wall admits the
    flag), or the launcher log whose first line dispatch.sh wrote as
    `correlation: <uuid>`, moved to <job>/launcher.log at mint or left in
    the jobs root as .launcher.* when the wrapper died after the record."""
    root = jobs_root()
    found: dict = {}
    try:
        states = sorted(root.glob("*/state.json"))
        logs = sorted(root.glob("*/launcher.log")) + sorted(root.glob(".launcher.*"))
    except OSError:
        states, logs = [], []
    for path in states:
        data = state_of(path.parent)
        if data.get("correlation") == correlation:
            found.setdefault(path.parent.name, (path.parent.name, path, data))
    for log in logs:
        record = logged_record(log, correlation)
        if record and (root / record).is_dir():
            found.setdefault(record, (record, log, state_of(root / record)))
    return list(found.values())


# -- the verbs ---------------------------------------------------------------

def usage() -> str:
    return ("reserve --requester PT-n --dispatcher PT-n --assignment A-n --key K --job J "
            "--harness H --branch B --scope-ref TEXT --inputs PATH... [--correlation UUID] "
            "| launch --correlation UUID | mint --correlation UUID --record ID")


def options(rest, *, required, optional=(), repeated=()) -> dict:
    found = {}
    index = 0
    while index < len(rest):
        word = rest[index]
        name = word[2:].replace("-", "_") if word.startswith("--") else None
        if name not in (*required, *optional, *repeated) or index + 1 >= len(rest):
            refuse("arguments", usage(), word, usage())
        if name in repeated:
            found.setdefault(name, [])
            index += 1
            while index < len(rest) and not rest[index].startswith("--"):
                found[name].append(rest[index])
                index += 1
            continue
        if name in found:
            refuse("arguments", f"--{word[2:]} once", f"--{word[2:]} twice", usage())
        found[name] = rest[index + 1]
        index += 2
    missing = [name for name in (*required, *repeated) if not found.get(name)]
    if missing:
        refuse("arguments", usage(), "no --" + missing[0].replace("_", "-"), usage())
    return found


def the_dispatcher(conn, part=None):
    """The caller holds the dispatcher role and the `dispatcher` machine, or
    refuses: the one check every mutation verb makes, these and
    dispatch_actions' alike (sessions-lever.md, "the four verbs act only for
    the participant whose role is dispatcher and who holds the dispatcher
    machine")."""
    if part is None:
        part = coordination.bound_participant(conn)
    coordination.require_role(part, ("dispatcher",))
    row = conn.execute(
        "SELECT * FROM subjects WHERE machine = ? ORDER BY id LIMIT 1", (dispatcher.ROLE.name,)
    ).fetchone()
    held = ""
    if row is not None and row["place"] == "named":
        held = (row["holder_note"] or "").strip()
    mine = session("").strip()
    if not held or held != mine:
        refuse("holder", "the session holding the dispatcher machine",
               f"held by {held or 'nobody'}; this session {mine or 'unnamed'}",
               "run dispatcher.py set --why TEXT from this session, then ask again")
    return part


def do_reserve(rest) -> int:
    given = options(rest, required=("requester", "dispatcher", "assignment", "key", "job",
                                    "harness", "branch", "scope_ref"),
                    optional=("correlation",), repeated=("inputs",))
    identity()
    conn = writer()
    part = the_dispatcher(conn)
    requester = coordination.parse_id(given["requester"], ("PT",))[1]
    dispatcher = coordination.parse_id(given["dispatcher"], ("PT",))[1]
    aid = coordination.parse_id(given["assignment"], ("A",))[1]
    assignment = coordination.assignment_row(conn, aid)
    entries = []
    for text in given["inputs"]:
        path = Path(text).resolve()
        try:
            data = path.read_bytes()
        except OSError as error:
            refuse("arguments", "--inputs naming readable files", f"{text}: {error.strerror}",
                   "name the files holding the assignment's text")
        entries.append({"path": str(path), "sha256": hashlib.sha256(data).hexdigest()})
    digest = digest_of(assignment=aid, job=given["job"], harness=given["harness"],
                       branch=given["branch"], scope_ref=given["scope_ref"], inputs=entries)
    found, repeated = reserve(
        conn, coordination.actor(part), key=given["key"], digest=digest, requester=requester,
        dispatcher=dispatcher, assignment=aid, generation=assignment["generation"],
        job=given["job"], harness=given["harness"], branch=given["branch"],
        scope_ref=given["scope_ref"], inputs=entries, correlation=given.get("correlation"))
    print(f"intent DI-{found['id']} {found['place']} correlation {found['correlation']}"
          + (" (repeated)" if repeated else ""))
    return 0


def do_step(verb, rest) -> int:
    given = options(rest, required=("correlation", "record") if verb == "mint"
                    else ("correlation",))
    identity()
    conn = writer()
    part = the_dispatcher(conn)
    found = by_correlation(conn, given["correlation"])
    if verb == "mint":
        found = move(conn, found["id"], "mint", coordination.actor(part),
                     evidence=given["record"], record=given["record"])
    else:
        found = move(conn, found["id"], "launch", coordination.actor(part),
                     evidence=f"by hand, correlation {found['correlation']}")
    print(f"intent DI-{found['id']} {found['place']} record {found['record'] or 'none'}")
    return 0


def main(argv: list[str]) -> int:
    try:
        verb, rest = (argv[0], argv[1:]) if argv else ("", [])
        if verb == "reserve":
            return do_reserve(rest)
        if verb in ("launch", "mint"):
            return do_step(verb, rest)
        refuse("arguments", "a verb: reserve, launch or mint", verb or "none", usage())
    except Refusal as refusal:
        return emit(refusal)
    except sqlite3.DatabaseError as error:
        return emit(Refusal("store", f"a usable store at {machine.store_path()}",
                            f"{type(error).__name__}: {error}",
                            "run again once the store is free"))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
