#!/usr/bin/env python3
"""The state engine: declared machines, one place per subject, an append-only marking.

Inherits ops/conductor/instrument-conventions.md: the refusal wire, the
four exit classes, identity for writes, the state directory. The store
is `state.db` under the lane's state directory, SQLite in WAL mode with
a busy timeout, so several sessions on one host read consistently and
write serially; a claim is one transaction and cannot be lost.

    machine.py machines             every declared machine, its version, places
    machine.py show MACHINE         the transition table of one machine
    machine.py history MACHINE ID   the marking of one subject, oldest first

A machine is declared as data by the instrument that owns it
(docs/workflow.md): places, an initial place, and transitions that each
name an actor class (`any`, `holder`, `holder-or-lapsed`), an evidence
duty (`none`, `required`), a failure class (the refusal token when the
subject already sits at the transition's target; any other wrong place
refuses `place`), and what the move does to
the holder (`take`, `keep`, `clear`). The engine executes the
declaration and writes every move to the marking, which triggers keep
append-only; it carries no step chain of its own. The three CLI verbs
are reads and never create the store; instruments write through the
library (`connect`, `declare`, `create`, `move`).
"""

from __future__ import annotations

import os
import sqlite3
import sys
import time
from collections.abc import Callable, Sequence
from datetime import timedelta
from typing import NamedTuple

# The shared instrument module sits beside this file; its directory goes
# on the path so the import resolves however this file was loaded: as
# the script, by path from another tool, or as a copy a test staged
# with its siblings.
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
try:
    import instrument
    from instrument import (  # noqa: F401 - re-exported for instruments and tests
        EXIT_DONE,
        EXIT_FINDING,
        Refusal,
        emit,
        identity,
        now,
        parse_stamp,
        refuse,
        stamp,
        state_dir,
    )
except ModuleNotFoundError:
    print(
        "tooling: expected instrument.py beside this tool; found absent; "
        "needed restore ops/conductor/tools/instrument.py beside it",
        file=sys.stderr,
    )
    raise SystemExit(2) from None

STORE = "state.db"
BUSY_TIMEOUT_MS = 5000
ACTORS = ("any", "holder", "holder-or-lapsed")
EVIDENCE_DUTIES = ("none", "required")
HOLD_KINDS = ("take", "keep", "clear")

SCHEMA = """
CREATE TABLE IF NOT EXISTS machines(
    name TEXT PRIMARY KEY, version INTEGER NOT NULL, initial TEXT NOT NULL,
    places TEXT NOT NULL, declared_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS transitions(
    machine TEXT NOT NULL, name TEXT NOT NULL, source TEXT NOT NULL,
    target TEXT NOT NULL, actor TEXT NOT NULL, evidence TEXT NOT NULL,
    failure TEXT NOT NULL, hold TEXT NOT NULL,
    PRIMARY KEY(machine, name));
CREATE TABLE IF NOT EXISTS subjects(
    id INTEGER PRIMARY KEY AUTOINCREMENT, machine TEXT NOT NULL,
    place TEXT NOT NULL, holder TEXT, holder_note TEXT, held_since TEXT,
    ttl_s INTEGER, created_at TEXT NOT NULL, created_by TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS marking(
    id INTEGER PRIMARY KEY AUTOINCREMENT, machine TEXT NOT NULL,
    subject INTEGER NOT NULL, transition TEXT NOT NULL, source TEXT,
    target TEXT NOT NULL, agent TEXT NOT NULL, at TEXT NOT NULL,
    evidence TEXT, note TEXT);
CREATE TRIGGER IF NOT EXISTS marking_is_append_only_update
    BEFORE UPDATE ON marking BEGIN
    SELECT RAISE(ABORT, 'the marking is append-only'); END;
CREATE TRIGGER IF NOT EXISTS marking_is_append_only_delete
    BEFORE DELETE ON marking BEGIN
    SELECT RAISE(ABORT, 'the marking is append-only'); END;
"""


class Transition(NamedTuple):
    name: str
    source: str
    target: str
    actor: str
    evidence: str
    failure: str
    hold: str


class Machine(NamedTuple):
    name: str
    version: int
    initial: str
    places: tuple[str, ...]
    transitions: tuple[Transition, ...]
    ttl_s: int

    def transition(self, name: str) -> Transition:
        for candidate in self.transitions:
            if candidate.name == name:
                return candidate
        refuse(
            "transition",
            f"a transition of machine {self.name}",
            name,
            "one of " + ", ".join(t.name for t in self.transitions),
        )


def store_path():
    return state_dir() / STORE


def connect(*, create: bool, write: bool = False) -> sqlite3.Connection:
    """Read by default; write updates an existing store, create initializes it."""
    path = store_path()
    if not create and not path.is_file():
        refuse(
            "store",
            f"the state store at {path}",
            "absent",
            "a writing verb of an instrument creates it; nothing to read",
        )
    mode = "rwc" if create else "rw" if write else "ro"
    connection = instrument.connect_store(path, mode=mode,
                               timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        if create:
            deadline = time.monotonic() + BUSY_TIMEOUT_MS / 1000
            while True:
                try:
                    connection.execute("PRAGMA journal_mode = WAL")
                    connection.executescript(SCHEMA)
                    break
                except sqlite3.OperationalError as error:
                    remaining = deadline - time.monotonic()
                    if (getattr(error, "sqlite_errorcode", 0) & 0xff) != sqlite3.SQLITE_BUSY or remaining <= 0:
                        raise
                    # WAL initialization may return BUSY without invoking
                    # SQLite's busy handler. Keep the original total budget.
                    time.sleep(min(0.01, remaining))
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise
                    connection.execute(f"PRAGMA busy_timeout = {max(1, int(remaining * 1000))}")
            connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        return connection
    except Exception:
        connection.close()
        raise


def _check_declaration(machine: Machine) -> None:
    if machine.initial not in machine.places:
        refuse("declaration", f"an initial place among {','.join(machine.places)}",
               machine.initial, "declare the initial place in places")
    for t in machine.transitions:
        for end in (t.source, t.target):
            if end not in machine.places:
                refuse("declaration", f"transition {t.name} between declared places",
                       f"place {end}", "declare every place a transition names")
        if t.actor not in ACTORS:
            refuse("declaration", "an actor class of " + ", ".join(ACTORS), t.actor,
                   f"fix transition {t.name}")
        if t.evidence not in EVIDENCE_DUTIES:
            refuse("declaration", "an evidence duty of none or required", t.evidence,
                   f"fix transition {t.name}")
        if t.hold not in HOLD_KINDS:
            refuse("declaration", "a hold of take, keep or clear", t.hold,
                   f"fix transition {t.name}")


def declare(conn: sqlite3.Connection, machine: Machine) -> None:
    """Write the machine and its transitions as rows; declaring twice is once."""
    _check_declaration(machine)
    with instrument.transaction(conn):
        conn.execute(
            "INSERT OR REPLACE INTO machines(name, version, initial, places, declared_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (machine.name, machine.version, machine.initial, ",".join(machine.places), stamp()),
        )
        conn.execute("DELETE FROM transitions WHERE machine = ?", (machine.name,))
        conn.executemany(
            "INSERT INTO transitions(machine, name, source, target, actor, evidence, failure, hold) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(machine.name, *t) for t in machine.transitions],
        )


def create(conn: sqlite3.Connection, machine: Machine, agent: str, *, note: str | None = None) -> int:
    """A new subject at the initial place; its first marking row is `create`."""
    moment = stamp()
    with instrument.transaction(conn):
        cursor = conn.execute(
            "INSERT INTO subjects(machine, place, created_at, created_by) VALUES (?, ?, ?, ?)",
            (machine.name, machine.initial, moment, agent),
        )
        sid = cursor.lastrowid
        conn.execute(
            "INSERT INTO marking(machine, subject, transition, source, target, agent, at, evidence, note) "
            "VALUES (?, ?, 'create', NULL, ?, ?, ?, NULL, ?)",
            (machine.name, sid, machine.initial, agent, moment, note),
        )
    return sid


def subject(conn: sqlite3.Connection, machine: Machine, sid: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM subjects WHERE machine = ? AND id = ?", (machine.name, sid)
    ).fetchone()


def history(conn: sqlite3.Connection, machine: Machine, sid: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM marking WHERE machine = ? AND subject = ? ORDER BY id",
        (machine.name, sid),
    ).fetchall()


def lapsed(row: sqlite3.Row, moment=None) -> bool:
    """True when the hold's ttl has run out; a subject nobody holds is not lapsed."""
    if row["held_since"] is None or row["ttl_s"] is None:
        return False
    deadline = parse_stamp(row["held_since"]) + timedelta(seconds=row["ttl_s"])
    return (moment or now()) > deadline


def _held_clause(row: sqlite3.Row) -> str:
    if row["holder"] is None:
        return ""
    where = f" on {row['holder_note']}" if row["holder_note"] else ""
    return f", held by {row['holder']}{where} since {row['held_since']}"


def _wrong_place(t: Transition, row: sqlite3.Row) -> None:
    """The declared failure class names a subject already at the target
    (a second take reads `taken`); any other place is a plain `place`."""
    refuse(
        t.failure if row["place"] == t.target else "place",
        f"place {t.source} for {t.name}",
        f"place {row['place']}{_held_clause(row)}",
        f"a transition declared from {row['place']}, or wait for the holder's ttl",
    )


def _check_actor(t: Transition, row: sqlite3.Row, agent: str) -> None:
    if t.actor == "any" or row["holder"] == agent:
        return
    if t.actor == "holder-or-lapsed" and lapsed(row):
        return
    remedy = (
        "the holder moves it, or anyone after the ttl lapses"
        if t.actor == "holder-or-lapsed"
        else "the holder moves it; another actor releases it after the ttl"
    )
    refuse("holder", f"the holder {row['holder']} for {t.name}", agent, remedy)


def _move_locked(
    conn: sqlite3.Connection,
    machine: Machine,
    t: Transition,
    sid: int,
    agent: str,
    moment: str,
    *,
    evidence: str | None,
    note: str | None,
    gate: Callable[[sqlite3.Row], None] | None,
    ttl_s: int | None,
) -> int:
    """One subject through ``t`` inside a transaction the caller holds;
    returns the marking row id. Every check reads the row under the
    write lock; a refusal propagates and the caller rolls back."""
    row = subject(conn, machine, sid)
    if row is None:
        refuse("unknown", f"a subject of machine {machine.name}", str(sid),
               "an id shown by the instrument's list")
    if row["place"] != t.source:
        _wrong_place(t, row)
    _check_actor(t, row, agent)
    if t.evidence == "required" and not (evidence or "").strip():
        refuse("evidence", f"evidence for {t.name}", "none",
               "name the sha, record id, row id or reason the move rests on")
    if gate is not None:
        gate(row)
    if t.hold == "take":
        holder, holder_note, held_since = agent, note, moment
        ttl = ttl_s if ttl_s is not None else machine.ttl_s
    elif t.hold == "clear":
        holder, holder_note, held_since, ttl = None, None, None, None
    else:
        holder, holder_note, held_since, ttl = (
            row["holder"], row["holder_note"], row["held_since"], row["ttl_s"]
        )
    moved = conn.execute(
        "UPDATE subjects SET place = ?, holder = ?, holder_note = ?, held_since = ?, ttl_s = ? "
        "WHERE id = ? AND machine = ? AND place = ?",
        (t.target, holder, holder_note, held_since, ttl, sid, machine.name, t.source),
    )
    if moved.rowcount != 1:
        _wrong_place(t, subject(conn, machine, sid))
    cursor = conn.execute(
        "INSERT INTO marking(machine, subject, transition, source, target, agent, at, evidence, note) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (machine.name, sid, t.name, t.source, t.target, agent, moment, evidence, note),
    )
    return cursor.lastrowid


def move(
    conn: sqlite3.Connection,
    machine: Machine,
    sid: int,
    transition: str,
    agent: str,
    *,
    evidence: str | None = None,
    note: str | None = None,
    gate: Callable[[sqlite3.Row], None] | None = None,
    ttl_s: int | None = None,
) -> sqlite3.Row:
    """One transition in one transaction: the place, the actor, the evidence
    duty and the caller's gate are checked on the row as read under the
    write lock, so two sessions moving one subject serialize and the
    second reads the first's result."""
    t = machine.transition(transition)
    with instrument.transaction(conn):
        marking_id = _move_locked(conn, machine, t, sid, agent, stamp(),
                                  evidence=evidence, note=note, gate=gate, ttl_s=ttl_s)
    return conn.execute("SELECT * FROM marking WHERE id = ?", (marking_id,)).fetchone()


def move_many(
    conn: sqlite3.Connection,
    machine: Machine,
    sids: Sequence[int] | Callable[[sqlite3.Connection], Sequence[int]],
    transition: str,
    agent: str,
    *,
    evidence: str | None = None,
    note: str | None = None,
    gate: Callable[[sqlite3.Row], None] | None = None,
    ttl_s: int | None = None,
) -> list[sqlite3.Row]:
    """Several subjects through one transition in one transaction, at one
    stamp: every subject moves or none does (T-9, 2026-09-14: a package
    take that moved each thread on its own could be split by a second
    session). ``sids`` is a list, or a callable given the connection under
    the write lock so the choice and the moves see one state. An empty
    choice moves nothing and returns []."""
    t = machine.transition(transition)
    with instrument.transaction(conn):
        chosen = list(sids(conn)) if callable(sids) else list(sids)
        moment = stamp()
        ids = [
            _move_locked(conn, machine, t, sid, agent, moment,
                         evidence=evidence, note=note, gate=gate, ttl_s=ttl_s)
            for sid in chosen
        ]
    return [conn.execute("SELECT * FROM marking WHERE id = ?", (i,)).fetchone() for i in ids]


def history_line(row: sqlite3.Row) -> str:
    """One marking row as the CLI prints it: stamp, transition, arrow, agent."""
    arrow = f"{row['source']} -> {row['target']}" if row["source"] else f"-> {row['target']}"
    tail = "".join(
        f"; {label} {row[key]}" for label, key in (("evidence", "evidence"), ("note", "note"))
        if row[key]
    )
    return f"{row['at']} {row['transition']} {arrow} by {row['agent']}{tail}"


def _machines(conn: sqlite3.Connection) -> None:
    rows = conn.execute("SELECT * FROM machines ORDER BY name").fetchall()
    for row in rows:
        print(f"{row['name']} v{row['version']} initial={row['initial']} places={row['places']}")
    if not rows:
        print("no machines declared")


def _show(conn: sqlite3.Connection, name: str) -> None:
    rows = conn.execute(
        "SELECT * FROM transitions WHERE machine = ? ORDER BY rowid", (name,)
    ).fetchall()
    if not rows:
        refuse("machine", "a declared machine", name, "one named by machines")
    for row in rows:
        print(
            f"{row['name']}: {row['source']} -> {row['target']}; actor {row['actor']}; "
            f"evidence {row['evidence']}; failure {row['failure']}; hold {row['hold']}"
        )


def _history(conn: sqlite3.Connection, name: str, raw_id: str) -> None:
    if not raw_id.isdigit():
        refuse("arguments", "an integer subject id", raw_id, "history MACHINE ID")
    rows = conn.execute(
        "SELECT * FROM marking WHERE machine = ? AND subject = ? ORDER BY id", (name, int(raw_id))
    ).fetchall()
    if not rows:
        refuse("unknown", f"a subject of machine {name}", raw_id, "an id shown by the instrument's list")
    for row in rows:
        print(history_line(row))


def main(argv: list[str]) -> int:
    verb = argv[1] if len(argv) > 1 else ""
    rest = argv[2:]
    try:
        if verb == "machines" and not rest:
            if not store_path().is_file():
                print("no state store")
                return EXIT_DONE
            with connect(create=False) as conn:
                _machines(conn)
            return EXIT_DONE
        if verb == "show" and len(rest) == 1:
            with connect(create=False) as conn:
                _show(conn, rest[0])
            return EXIT_DONE
        if verb == "history" and len(rest) == 2:
            with connect(create=False) as conn:
                _history(conn, rest[0], rest[1])
            return EXIT_DONE
        refuse("arguments", "machines, show MACHINE, or history MACHINE ID",
               " ".join(argv[1:]) or "no verb", "name one verb with its arguments")
    except Refusal as refusal:
        return emit(refusal)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
