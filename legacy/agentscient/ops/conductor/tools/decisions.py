#!/usr/bin/env python3
"""Maintain the conductor's owner-decision register."""

from __future__ import annotations

import argparse
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
    from instrument import Refusal, emit, identity, refuse, stamp, state_dir
except ModuleNotFoundError:
    print(
        "tooling: expected instrument.py beside this tool; found absent; "
        "needed restore ops/conductor/tools/instrument.py beside it",
        file=sys.stderr,
    )
    raise SystemExit(2) from None

ID_RE = re.compile(r"^D-[A-Z0-9]+-[0-9]+$")
HARNESS_ADDRESSES = (
    "<noreply@anthropic.com>",
    "<noreply@openai.com>",
    "<noreply@x.ai>",
)
STANDINGS = ("proposed", "ratified", "rejected", "superseded")
WRITE_VERBS = {"add", "ratify", "reject", "supersede", "note"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    home TEXT NOT NULL,
    standing TEXT NOT NULL,
    blocked_on TEXT,
    decided_by TEXT,
    decided_at TEXT,
    evidence TEXT,
    recorded_by TEXT NOT NULL,
    supersedes TEXT,
    superseded_by TEXT,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    verb TEXT NOT NULL,
    decision_id TEXT NOT NULL,
    agent TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    detail TEXT
);
"""


def validate_id(value: str) -> None:
    if not ID_RE.fullmatch(value):
        refuse(
            "id-shape",
            "an id matching ^D-[A-Z0-9]+-[0-9]+$",
            repr(value),
            "use an id such as D-OPS-1",
        )


def connect(*, write: bool, absent_ok: bool = False) -> sqlite3.Connection:
    db_path = state_dir() / "decisions.db"
    if write:
        connection = instrument.connect_store(db_path, mode="rwc")
        connection.executescript(SCHEMA)
    elif db_path.exists():
        connection = instrument.connect_store(db_path)
    elif absent_ok:
        # show only: an unknown-id refusal is honest with or without a
        # store — no id can be found in a store that is not there.
        connection = sqlite3.connect(":memory:")
        connection.executescript(SCHEMA)
    else:
        refuse(
            "store",
            f"the decision store at {db_path}",
            "ABSENT",
            "an unperformed read is not an empty register; restore or "
            "seed the store (the tracked seed export names its birth rows)",
        )
    connection.row_factory = sqlite3.Row
    return connection


def get_row(connection: sqlite3.Connection, decision_id: str) -> sqlite3.Row:
    validate_id(decision_id)
    row = connection.execute(
        "SELECT * FROM decisions WHERE id = ?", (decision_id,)
    ).fetchone()
    if row is None:
        refuse(
            "unknown",
            "an existing decision id",
            decision_id,
            "add the decision before using this verb",
        )
    return row


def event(
    connection: sqlite3.Connection,
    verb: str,
    decision_id: str,
    agent: str,
    at: str,
    detail: str | None = None,
) -> None:
    connection.execute(
        "INSERT INTO events (verb, decision_id, agent, timestamp, detail) "
        "VALUES (?, ?, ?, ?, ?)",
        (verb, decision_id, agent, at, detail),
    )


def row_dict(row: sqlite3.Row) -> dict[str, object]:
    return dict(row)


def principal(agent: str, evidence: str | None) -> str:
    if not agent.endswith(HARNESS_ADDRESSES):
        return agent
    if not evidence:
        refuse(
            "self-conferred",
            "owner evidence for a harness-vendor deciding act",
            "harness identity without evidence",
            "attach --evidence naming the deciding principal",
        )
    match = re.match(r"\s*([^:\n]+?)\s*:", evidence)
    if not match:
        refuse(
            "self-conferred",
            "evidence beginning with the deciding principal and ':'",
            repr(evidence),
            "name the principal, for example 'owner: <words>'",
        )
    return match.group(1)


def add(args: argparse.Namespace, agent: str) -> None:
    validate_id(args.id)
    if len(args.question.split()) < 4:
        refuse(
            "question-short",
            "a question or claim of at least four words",
            f"{len(args.question.split())} words",
            "supply a complete decision question",
        )
    if args.supersedes:
        validate_id(args.supersedes)
        if args.supersedes == args.id:
            refuse(
                "successor", "a different predecessor id", args.id,
                "name the decision this new decision amends",
            )
    with connect(write=True) as connection:
        if connection.execute(
            "SELECT 1 FROM decisions WHERE id = ?", (args.id,)
        ).fetchone():
            refuse(
                "duplicate", "a new decision id", args.id,
                "choose an id not already in the register",
            )
        if args.supersedes:
            get_row(connection, args.supersedes)
        at = stamp()
        connection.execute(
            "INSERT INTO decisions "
            "(id, question, home, standing, blocked_on, recorded_by, "
            "supersedes, created_at, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                args.id, args.question, args.home, "proposed",
                args.blocked_on, agent, args.supersedes, at, agent,
            ),
        )
        event(connection, "add", args.id, agent, at)
    print(args.id)


def list_rows(args: argparse.Namespace) -> None:
    with connect(write=False) as connection:
        rows = connection.execute(
            "SELECT id, standing, question FROM decisions"
            " WHERE (? IS NULL OR standing = ?)"
            " AND (? = 0 OR blocked_on IS NOT NULL)"
            " ORDER BY CASE standing WHEN 'proposed' THEN 0 ELSE 1 END, id",
            (args.standing, args.standing, 1 if args.blocked else 0),
        )
        for row in rows:
            print(f"{row['id']}\t{row['standing']}\t{row['question']}")


def show(args: argparse.Namespace) -> None:
    with connect(write=False, absent_ok=True) as connection:
        decision = row_dict(get_row(connection, args.id))
        events = [
            row_dict(row)
            for row in connection.execute(
                "SELECT verb, decision_id, agent, timestamp, detail "
                "FROM events WHERE decision_id = ? ORDER BY event_id",
                (args.id,),
            )
        ]
    print(json.dumps({"decision": decision, "events": events}, sort_keys=True))


def decide(args: argparse.Namespace, agent: str, target: str) -> None:
    with connect(write=True) as connection:
        row = get_row(connection, args.id)
        if row["standing"] != "proposed":
            refuse(
                "transition", f"proposed -> {target}",
                f"{row['standing']} -> {target}",
                "start from a proposed decision",
            )
        decided_by = principal(agent, args.evidence)
        at = stamp()
        connection.execute(
            "UPDATE decisions SET standing = ?, decided_by = ?, decided_at = ?, "
            "evidence = ?, recorded_by = ? WHERE id = ?",
            (target, decided_by, at, args.evidence, agent, args.id),
        )
        event(connection, target, args.id, agent, at, args.evidence)
    print(args.id)


def supersede(args: argparse.Namespace, agent: str) -> None:
    validate_id(args.by)
    if args.by == args.id:
        refuse(
            "successor", "a different existing successor id", args.by,
            "name the replacement decision",
        )
    with connect(write=True) as connection:
        old = get_row(connection, args.id)
        new = connection.execute(
            "SELECT * FROM decisions WHERE id = ?", (args.by,)
        ).fetchone()
        if new is None:
            refuse(
                "successor", "an existing successor id", args.by,
                "add the successor decision before superseding into it",
            )
        if old["standing"] != "ratified":
            refuse(
                "transition", "ratified -> superseded",
                f"{old['standing']} -> superseded",
                "supersede only a ratified decision",
            )
        if new["supersedes"] not in (None, args.id):
            refuse(
                "successor", f"a successor of {args.id}",
                f"{args.by} supersedes {new['supersedes']}",
                "use a decision whose predecessor is this decision",
            )
        decided_by = principal(agent, args.evidence)
        at = stamp()
        connection.execute(
            "UPDATE decisions SET standing = ?, decided_by = ?, decided_at = ?, "
            "evidence = ?, recorded_by = ?, superseded_by = ? WHERE id = ?",
            ("superseded", decided_by, at, args.evidence, agent, args.by, args.id),
        )
        connection.execute(
            "UPDATE decisions SET supersedes = ? WHERE id = ?", (args.id, args.by)
        )
        event(connection, "supersede", args.id, agent, at, args.evidence)
    print(args.id)


def note(args: argparse.Namespace, agent: str) -> None:
    with connect(write=True) as connection:
        get_row(connection, args.id)
        event(connection, "note", args.id, agent, stamp(), args.text)
    print(args.id)


def export_rows() -> None:
    with connect(write=False) as connection:
        rows = [
            row_dict(row)
            for row in connection.execute("SELECT * FROM decisions ORDER BY id")
        ]
    print(json.dumps(rows, sort_keys=True, separators=(",", ":")))


def report() -> None:
    with connect(write=False) as connection:
        counts = {standing: 0 for standing in STANDINGS}
        for row in connection.execute(
            "SELECT standing, COUNT(*) AS count FROM decisions GROUP BY standing"
        ):
            counts[row["standing"]] = row["count"]
        oldest = [
            row_dict(row)
            for row in connection.execute(
                "SELECT id, question, created_at FROM decisions "
                "WHERE standing = ? ORDER BY created_at, id",
                ("proposed",),
            )
        ]
        blocked = [
            row_dict(row)
            for row in connection.execute(
                "SELECT id, blocked_on FROM decisions "
                "WHERE blocked_on IS NOT NULL AND standing = 'proposed' "
                "ORDER BY id"
            )
        ]
    print(json.dumps({"blocked": blocked, "counts": counts, "oldest_open": oldest},
                     sort_keys=True))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    commands = root.add_subparsers(dest="verb", required=True)
    add_parser = commands.add_parser("add")
    add_parser.add_argument("id")
    add_parser.add_argument("question")
    add_parser.add_argument("--home", required=True)
    add_parser.add_argument("--blocked-on")
    add_parser.add_argument("--supersedes")
    list_parser = commands.add_parser("list")
    list_parser.add_argument("--standing", choices=STANDINGS)
    list_parser.add_argument("--blocked", action="store_true")
    show_parser = commands.add_parser("show")
    show_parser.add_argument("id")
    for verb in ("ratify", "reject"):
        decision_parser = commands.add_parser(verb)
        decision_parser.add_argument("id")
        decision_parser.add_argument("--evidence")
    supersede_parser = commands.add_parser("supersede")
    supersede_parser.add_argument("id")
    supersede_parser.add_argument("--by", required=True)
    supersede_parser.add_argument("--evidence")
    note_parser = commands.add_parser("note")
    note_parser.add_argument("id")
    note_parser.add_argument("text")
    commands.add_parser("export")
    commands.add_parser("report")
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        agent = identity() if args.verb in WRITE_VERBS else ""
        if args.verb == "add":
            add(args, agent)
        elif args.verb == "list":
            list_rows(args)
        elif args.verb == "show":
            show(args)
        elif args.verb in ("ratify", "reject"):
            decide(args, agent, "ratified" if args.verb == "ratify" else "rejected")
        elif args.verb == "supersede":
            supersede(args, agent)
        elif args.verb == "note":
            note(args, agent)
        elif args.verb == "export":
            export_rows()
        else:
            report()
    except Refusal as error:
        return emit(error)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
