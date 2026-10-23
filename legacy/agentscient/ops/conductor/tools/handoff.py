#!/usr/bin/env python3
"""The handoff manager: a closing session's package of threads, each with one place.

Inherits ops/conductor/instrument-conventions.md: the refusal wire, the
four exit classes, identity for writes. The state is rows in the state
engine's store (machine.py, `state.db` under the lane's state
directory), never a file: every session on the host sees a claim the
moment it is written, and a claim is one transaction.

    handoff.py open TITLE --summary TEXT [--branch B] [--prs TEXT]     a package: prints H-n
    handoff.py thread H-n TITLE --body TEXT [--to WHO]                  an item of it: prints T-n
    handoff.py take T-n|H-n --branch B [--ttl MIN]                      claim one thread, or every open thread of a package
    handoff.py release T-n --why TEXT                                   give a claim back
    handoff.py done T-n --evidence READING [--note TEXT]                close a claim on a reading
    handoff.py supersede T-n --by T-m                                   an open thread replaced by a newer one
    handoff.py list [--state S] [--to WHO] [--handoff H-n] [--all]      the board; open and taken unless --all
    handoff.py show T-n|H-n                                             one row with its marking
    handoff.py held --branch B                                          what a branch still holds; exit 1 while any
    handoff.py brief                                                    the onboard section

The thread machine, declared as data below, has four places: open,
taken, the closed place, and superseded. Its moves: take (open to
taken, any actor), release (taken to open, the holder or anyone once
the ttl lapsed, a reason required), the closing move (taken to the
closed place, the holder only, a reading required), supersede (open to
superseded, the newer id required). A take names the branch the work
is on, and that branch must be in the deliverable lock: a claim nobody
can find on the board is not a claim. Reads need no identity and never
create the store.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import timedelta

# The shared instrument module sits beside this file; its directory goes
# on the path so the import resolves however this file was loaded: as
# the script, by path from another tool, or as a copy a test staged
# with its siblings.
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
try:
    import machine
    from instrument import (
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
        "tooling: expected instrument.py and machine.py beside this tool; found absent; "
        "needed restore ops/conductor/tools/instrument.py and machine.py beside it",
        file=sys.stderr,
    )
    raise SystemExit(2) from None

LOCK = "deliverable.json"
STATES = ("open", "taken", "done", "superseded")
ID = re.compile(r"^([HT])-(\d+)$")
READING = re.compile(
    r"^(?:[0-9a-f]{7,40}|#\d+|\d{8}T\d{6}Z-[\w-]+|P-[\w-]+)$", re.IGNORECASE
)
T = machine.Transition
THREAD = machine.Machine(
    name="thread",
    version=1,
    initial="open",
    places=STATES,
    transitions=(
        T("take", "open", "taken", "any", "none", "taken", "take"),
        T("release", "taken", "open", "holder-or-lapsed", "required", "place", "clear"),
        T("done", "taken", "done", "holder", "required", "place", "keep"),
        T("supersede", "open", "superseded", "any", "required", "place", "keep"),
    ),
    ttl_s=8 * 3600,
)
SCHEMA = """
CREATE TABLE IF NOT EXISTS handoffs(
    id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, summary TEXT NOT NULL,
    branch TEXT, prs TEXT, opened_by TEXT NOT NULL, opened_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS threads(
    id INTEGER PRIMARY KEY, handoff INTEGER NOT NULL, title TEXT NOT NULL,
    body TEXT NOT NULL, addressee TEXT, opened_by TEXT NOT NULL, opened_at TEXT NOT NULL,
    superseded_by INTEGER);
"""


# -- arguments ---------------------------------------------------------------

def parse(rest, count, *, required=(), optional=(), flags=()):
    """Positionals and --options; anything else refuses `arguments`."""
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
    return "see the verbs in the module doc of handoff.py"


def parse_id(text: str, kinds: str = "HT") -> tuple[str, int]:
    match = ID.match(text)
    if match is None or match.group(1) not in kinds:
        wanted = " or ".join(f"{k}-1" for k in kinds)
        refuse("id", f"an id like {wanted}", text, "the id printed by open, thread or list")
    return match.group(1), int(match.group(2))


# -- the store ---------------------------------------------------------------

def writer():
    conn = machine.connect(create=True)
    conn.executescript(SCHEMA)
    machine.declare(conn, THREAD)
    return conn


def reader():
    """The store for a read, or None when there is nothing to read."""
    if not machine.store_path().is_file():
        return None
    conn = machine.connect(create=False)
    conn.executescript(SCHEMA)
    return conn


def handoff_row(conn, hid: int):
    return conn.execute("SELECT * FROM handoffs WHERE id = ?", (hid,)).fetchone()


def thread_rows(conn, *, hid=None, state=None, addressee=None, tid=None):
    sql = (
        "SELECT t.*, s.place, s.holder, s.holder_note, s.held_since, s.ttl_s, "
        "(SELECT evidence FROM marking m WHERE m.subject = t.id AND m.transition = 'done' "
        " ORDER BY m.id DESC LIMIT 1) AS reading "
        "FROM threads t JOIN subjects s ON s.id = t.id WHERE 1 = 1"
    )
    args: list = []
    for clause, value in (
        (" AND t.handoff = ?", hid), (" AND s.place = ?", state),
        (" AND t.addressee = ?", addressee), (" AND t.id = ?", tid),
    ):
        if value is not None:
            sql += clause
            args.append(value)
    return conn.execute(sql + " ORDER BY t.id", args).fetchall()


def require_thread(conn, tid: int):
    rows = thread_rows(conn, tid=tid) if conn is not None else []
    if not rows:
        refuse("unknown", "a thread id in the store", f"T-{tid}", "an id printed by list")
    return rows[0]


def require_handoff(conn, hid: int):
    row = handoff_row(conn, hid) if conn is not None else None
    if row is None:
        refuse("unknown", "a handoff id in the store", f"H-{hid}", "an id printed by list")
    return row


def lock_branches() -> list[str] | None:
    path = state_dir() / LOCK
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    branches = data.get("branches") if isinstance(data, dict) else None
    return [b for b in branches if isinstance(b, str)] if isinstance(branches, list) else None


def require_lock(branch: str) -> None:
    branches = lock_branches()
    found = "no lock" if branches is None else "lock names " + (", ".join(branches) or "nothing")
    if branches is None or branch not in branches:
        refuse(
            "deliverable",
            f"the branch {branch} named in the deliverable lock",
            found,
            f"python3 ops/conductor/tools/deliverable.py add {branch} --why '<the owner's words>'",
        )


# -- rendering ---------------------------------------------------------------

def age(text: str) -> str:
    seconds = max(0, int((now() - parse_stamp(text)).total_seconds()))
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"


def ttl_text(seconds) -> str:
    if seconds is None:
        return "no ttl"
    return f"ttl {timedelta(seconds=int(seconds))}"


def thread_line(row) -> str:
    head = f"T-{row['id']} {row['place']} [H-{row['handoff']}] {row['title']}"
    if row["addressee"]:
        head += f" — to {row['addressee']}"
    if row["place"] == "taken":
        return (
            f"{head} — {row['holder']} on {row['holder_note']} since {age(row['held_since'])} ago "
            f"({ttl_text(row['ttl_s'])})"
        )
    if row["place"] == "done":
        return f"{head} — {row['holder']}: {row['reading']}"
    if row["place"] == "superseded":
        return f"{head} — by T-{row['superseded_by']}"
    return f"{head} — opened by {row['opened_by']} {age(row['opened_at'])} ago"


def handoff_line(row) -> str:
    tail = "".join(
        f"; {label} {row[key]}" for label, key in (("branch", "branch"), ("prs", "prs")) if row[key]
    )
    return f"H-{row['id']} {row['title']} — opened by {row['opened_by']} {age(row['opened_at'])} ago{tail}"


# -- writes --------------------------------------------------------------------

def do_open(rest):
    agent = identity()
    (title,), opts = parse(rest, 1, required=("summary",), optional=("branch", "prs"))
    with writer() as conn:
        cursor = conn.execute(
            "INSERT INTO handoffs(title, summary, branch, prs, opened_by, opened_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (title, opts["summary"], opts.get("branch"), opts.get("prs"), agent, stamp()),
        )
    print(f"handoff H-{cursor.lastrowid}")


def do_thread(rest):
    agent = identity()
    (raw, title), opts = parse(rest, 2, required=("body",), optional=("to",))
    _, hid = parse_id(raw, "H")
    with writer() as conn:
        require_handoff(conn, hid)
        sid = machine.create(conn, THREAD, agent)
        conn.execute(
            "INSERT INTO threads(id, handoff, title, body, addressee, opened_by, opened_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (sid, hid, title, opts["body"], opts.get("to"), agent, stamp()),
        )
    print(f"thread T-{sid}")


def do_take(rest):
    agent = identity()
    (raw,), opts = parse(rest, 1, required=("branch",), optional=("ttl",))
    kind, number = parse_id(raw)
    branch = opts["branch"]
    ttl_s = int(opts["ttl"]) * 60 if "ttl" in opts and opts["ttl"].isdigit() else None
    require_lock(branch)
    with writer() as conn:
        if kind == "T":
            require_thread(conn, number)
            machine.move(conn, THREAD, number, "take", agent, note=branch, ttl_s=ttl_s)
            print(f"taken T-{number} on {branch}")
            return
        require_handoff(conn, number)

        def open_threads(locked):
            """The package as read under the write lock: another holder on
            any thread refuses before anything moves (T-9)."""
            rows = thread_rows(locked, hid=number)
            for row in rows:
                if row["place"] == "taken" and row["holder"] != agent:
                    refuse(
                        "taken",
                        f"every thread of H-{number} open or mine",
                        f"T-{row['id']} held by {row['holder']} on {row['holder_note']} "
                        f"since {row['held_since']}",
                        "take the open threads one by one, or wait for the holder's ttl",
                    )
            return [row["id"] for row in rows if row["place"] == "open"]

        for marking in machine.move_many(conn, THREAD, open_threads, "take", agent, note=branch, ttl_s=ttl_s):
            print(f"taken T-{marking['subject']} on {branch}")


def do_release(rest):
    agent = identity()
    (raw,), opts = parse(rest, 1, required=("why",))
    _, tid = parse_id(raw, "T")
    with writer() as conn:
        require_thread(conn, tid)
        machine.move(conn, THREAD, tid, "release", agent, evidence=opts["why"])
    print(f"released T-{tid}")


def do_finish(rest):
    agent = identity()
    (raw,), opts = parse(rest, 1, required=("evidence",), optional=("note",))
    _, tid = parse_id(raw, "T")
    evidence = opts["evidence"].strip()
    if READING.match(evidence) is None:
        refuse(
            "evidence",
            "a reading: a sha, #PR, a dispatch record id or a P- row id",
            evidence,
            "cite the reading the close rests on",
        )
    with writer() as conn:
        require_thread(conn, tid)
        machine.move(conn, THREAD, tid, "done", agent, evidence=evidence, note=opts.get("note"))
    print(f"done T-{tid}: {evidence}")


def do_supersede(rest):
    agent = identity()
    (raw,), opts = parse(rest, 1, required=("by",))
    _, tid = parse_id(raw, "T")
    _, newer = parse_id(opts["by"], "T")
    with writer() as conn:
        require_thread(conn, tid)
        require_thread(conn, newer)
        machine.move(conn, THREAD, tid, "supersede", agent, evidence=f"T-{newer}")
        conn.execute("UPDATE threads SET superseded_by = ? WHERE id = ?", (newer, tid))
    print(f"superseded T-{tid} by T-{newer}")


# -- reads -----------------------------------------------------------------------

def do_list(rest):
    _, opts = parse(rest, 0, optional=("state", "to", "handoff"), flags=("all",))
    state = opts.get("state")
    if state is not None and state not in STATES:
        refuse("arguments", "a state of " + ", ".join(STATES), state, "list --state open")
    hid = parse_id(opts["handoff"], "H")[1] if "handoff" in opts else None
    conn = reader()
    if conn is None:
        print("no handoffs")
        return EXIT_DONE
    with conn:
        rows = thread_rows(conn, hid=hid, state=state, addressee=opts.get("to"))
    if not opts.get("all") and state is None:
        rows = [row for row in rows if row["place"] in ("open", "taken")]
    if not rows:
        print("no handoffs")
        return EXIT_DONE
    for row in rows:
        print(thread_line(row))
    return EXIT_DONE


def do_show(rest):
    (raw,), _ = parse(rest, 1)
    kind, number = parse_id(raw)
    conn = reader()
    if kind == "T":
        row = require_thread(conn, number)
        with conn:
            marking = machine.history(conn, THREAD, number)
        print(thread_line(row))
        print(row["body"])
        for entry in marking:
            print(machine.history_line(entry))
        return EXIT_DONE
    row = require_handoff(conn, number)
    with conn:
        rows = thread_rows(conn, hid=number)
    print(handoff_line(row))
    print(row["summary"])
    for thread in rows:
        print(thread_line(thread))
    return EXIT_DONE


def do_held(rest):
    _, opts = parse(rest, 0, required=("branch",))
    conn = reader()
    rows = []
    if conn is not None:
        with conn:
            rows = [
                row for row in thread_rows(conn, state="taken")
                if row["holder_note"] == opts["branch"]
            ]
    if not rows:
        print("none held")
        return EXIT_DONE
    for row in rows:
        print(thread_line(row))
    return EXIT_FINDING


def do_brief(rest):
    parse(rest, 0)
    conn = reader()
    if conn is None:
        print("no handoffs")
        return EXIT_DONE
    with conn:
        rows = thread_rows(conn)
        handoffs = conn.execute("SELECT * FROM handoffs ORDER BY id").fetchall()
    counts = {state: sum(1 for row in rows if row["place"] == state) for state in STATES}
    summary = " ".join(f"{state}={counts[state]}" for state in ("open", "taken", "done"))
    if counts["superseded"]:
        summary += f" superseded={counts['superseded']}"
    print(f"handoffs: {summary}")
    live = [row for row in rows if row["place"] in ("open", "taken")]
    for handoff in handoffs:
        mine = [row for row in live if row["handoff"] == handoff["id"]]
        if not mine:
            continue
        print(handoff_line(handoff))
        for row in mine:
            print(f"  {thread_line(row)}")
    return EXIT_DONE


VERBS = {
    "open": do_open, "thread": do_thread, "take": do_take, "release": do_release,
    "done": do_finish, "supersede": do_supersede, "list": do_list, "show": do_show,
    "held": do_held, "brief": do_brief,
}


def main(argv: list[str]) -> int:
    verb = argv[1] if len(argv) > 1 else ""
    try:
        if verb not in VERBS:
            refuse("arguments", "one of " + ", ".join(VERBS), verb or "no verb",
                   "name one verb; the module doc has the arguments")
        return VERBS[verb](argv[2:]) or EXIT_DONE
    except Refusal as refusal:
        return emit(refusal)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
