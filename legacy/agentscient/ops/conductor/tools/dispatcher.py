#!/usr/bin/env python3
"""The Dispatcher role: one session on the host sends the lane's dispatches.

Inherits ops/conductor/instrument-conventions.md: the refusal wire, the
four exit classes, identity for writes. The role is a subject of the
state engine's `dispatcher` machine (machine.py, `state.db` under the
lane's state directory), never a file: every session on the host reads
the same answer the moment it is written, and a hand-over is one
transaction. Owner, 2026-09-12, on one branch per session: "as long you
account for the fact that i'll have multiple sessions running soon".

    dispatcher.py set [SESSION] --why TEXT [--ttl MIN]   name the Dispatcher
    dispatcher.py show                                   who holds it now
    dispatcher.py clear --why TEXT                       give the role back

`set` with no SESSION names the session the harness gave this command,
so the Dispatcher claims the role from inside itself. The machine has
two places, unset and named; `set` takes the role from unset, `hand`
moves it between two named sessions and only the holder (or anyone once
the ttl has lapsed) may make that move, and `clear` releases it. A role
whose ttl has lapsed still answers `show` and still binds the boundary
row: a session that went away does not silently open the lane. What
lapsing changes is who may take the role next.

The `dispatcher` boundary row reads `holder_note` of this machine's
subject and compares it with the session the harness named for the
session running `ops/bin/dispatch.sh` or `ops/bin/go.sh`. That column
is the seam between this lever and the wall, and it holds the session
id and nothing else.
"""

from __future__ import annotations

import contextlib
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
    import machine
    from instrument import (
        EXIT_DONE,
        Refusal,
        emit,
        identity,
        refuse,
        session,
    )
except ModuleNotFoundError:
    print(
        "tooling: expected instrument.py and machine.py beside this tool; found absent; "
        "needed restore ops/conductor/tools/instrument.py and machine.py beside it",
        file=sys.stderr,
    )
    raise SystemExit(2) from None

T = machine.Transition
ROLE = machine.Machine(
    name="dispatcher",
    version=1,
    initial="unset",
    places=("unset", "named"),
    transitions=(
        T("set", "unset", "named", "any", "required", "named", "take"),
        T("hand", "named", "named", "holder-or-lapsed", "required", "unset", "take"),
        T("clear", "named", "unset", "holder-or-lapsed", "required", "unset", "clear"),
    ),
    ttl_s=12 * 3600,
)


SESSION_ID = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


def _a_session_id(value):
    """The value, when it is a session id and nothing else.

    The `dispatcher` boundary row reads `holder_note` and compares it with
    the id the harness names, so a note that is not that id names no
    Dispatcher. It is also echoed by `show` and carried into the engine's
    log, and a column that accepts lines and spaces is a column doing a
    second job it never declared
    (20260916T122524Z-review-codex-05a9f5#10). The refusal does not echo
    what it refused: the value may be the secret someone pasted."""
    if SESSION_ID.match(value):
        return value
    shape = f"{len(value)} characters, not that shape"
    return refuse("session",
                  "a session id: a letter or digit, then letters, digits, dot, "
                  "dash or underscore, at most 128 in all",
                  shape,
                  "name the session the harness gave, as `dispatcher.py show` prints it")


def _the_holder(row, verb):
    """Refuse unless this session is the one holding the role.

    The machine's `holder-or-lapsed` actor class compares the agent string,
    and every session of one model carries the same agent string, so a peer
    could take a live role from its holder
    (20260916T122524Z-review-codex-05a9f5#2). The seam this role is
    identified by is the session, so the check is on the session. A lapsed
    role is still anyone's to take: that is what lapsing is for."""
    if machine.lapsed(row):
        return
    held = (row["holder_note"] or "").strip()
    mine = session("").strip()
    if not mine:
        refuse("session", f"the Dispatcher session {held}",
               "the harness named no session for this one",
               f"run {verb} from {held}, or wait for the ttl to lapse")
    if mine != held:
        refuse("session", f"the Dispatcher session {held}", mine,
               f"run {verb} from {held}, or wait for the ttl to lapse")


def usage() -> str:
    return "see the verbs in the module doc of dispatcher.py"


def parse(rest, count, *, required=(), optional=()):
    """Positionals and --options; anything else refuses `arguments`."""
    positionals, options = [], {}
    tokens = list(rest)
    while tokens:
        token = tokens.pop(0)
        if token.startswith("--"):
            name = token[2:]
            if name not in required and name not in optional:
                refuse("arguments", "a known option", token, usage())
            if not tokens:
                refuse("arguments", f"a value after --{name}", "end of arguments", usage())
            options[name] = tokens.pop(0)
        else:
            positionals.append(token)
    if len(positionals) > count:
        refuse("arguments", f"at most {count} positional argument(s)",
               str(len(positionals)), usage())
    for name in required:
        if name not in options:
            refuse("arguments", f"--{name}", "absent", usage())
    return positionals, options


def _settle(conn) -> None:
    """Fold the write-ahead log back into the store, so the role is readable
    from `state.db` alone. The boundary row opens the store read-only on
    every dispatch; a main file that is stale until the next checkpoint is
    a reading that depends on when someone last looked."""
    # A checkpoint another connection holds open is not this command's
    # failure: the move is committed either way.
    with contextlib.suppress(sqlite3.Error):
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def _subject(conn, *, create):
    """The one subject of the role machine, or None. The machine holds a
    single subject on a host: the question "who dispatches" has one
    answer, so a second subject would be two answers (docs/workflow.md,
    one place per subject)."""
    try:
        row = conn.execute(
            "SELECT id FROM subjects WHERE machine = ? ORDER BY id LIMIT 1", (ROLE.name,)
        ).fetchone()
    except sqlite3.OperationalError:
        # A store with no engine tables answers "nobody holds it", which is
        # a reading; a writing verb declares the machine and creates them.
        if not create:
            return None
        raise
    if row is not None:
        return row["id"]
    if not create:
        return None
    return machine.create(conn, ROLE, identity(), note="the Dispatcher role")


def _ttl_seconds(options):
    raw = options.get("ttl")
    if raw is None:
        return None
    try:
        minutes = int(raw)
    except ValueError:
        refuse("arguments", "--ttl in whole minutes", raw, "a number, like --ttl 720")
    if minutes <= 0:
        refuse("arguments", "--ttl above zero", raw, "a number of minutes, like --ttl 720")
    return minutes * 60


def _set(rest) -> int:
    positionals, options = parse(rest, 1, required=("why",), optional=("ttl",))
    named = (positionals[0] if positionals else session("")).strip()
    if not named:
        refuse("session", "the session to name as Dispatcher",
               "the harness named no session and none was given",
               "run it from the Dispatcher session, or name the session id")
    _a_session_id(named)
    agent = identity()
    conn = machine.connect(create=True)
    machine.declare(conn, ROLE)
    sid = _subject(conn, create=True)
    row = machine.subject(conn, ROLE, sid)
    move = "hand" if row["place"] == "named" else "set"
    if move == "hand":
        _the_holder(row, "set")
    machine.move(conn, ROLE, sid, move, agent, evidence=options["why"],
                 note=named, ttl_s=_ttl_seconds(options))
    _settle(conn)
    print(f"dispatcher {named} by {agent}")
    return EXIT_DONE


def _clear(rest) -> int:
    _positionals, options = parse(rest, 0, required=("why",))
    agent = identity()
    conn = machine.connect(create=False, write=True)
    sid = _subject(conn, create=False)
    if sid is None:
        refuse("place", "a Dispatcher named", "none",
               "nothing to clear; dispatcher.py set names one")
    row = machine.subject(conn, ROLE, sid)
    if row["place"] == "named":
        _the_holder(row, "clear")
    machine.move(conn, ROLE, sid, "clear", agent, evidence=options["why"])
    _settle(conn)
    print("no Dispatcher named")
    return EXIT_DONE


def _show(rest) -> int:
    parse(rest, 0)
    if not machine.store_path().is_file():
        print("no Dispatcher named")
        return EXIT_DONE
    conn = machine.connect(create=False)
    sid = _subject(conn, create=False)
    row = machine.subject(conn, ROLE, sid) if sid is not None else None
    if row is None or row["place"] != "named":
        print("no Dispatcher named")
        return EXIT_DONE
    lapse = " lapsed" if machine.lapsed(row) else ""
    print(f"dispatcher {row['holder_note']} by {row['holder']} "
          f"since {row['held_since']}{lapse}")
    return EXIT_DONE


VERBS = {"set": _set, "show": _show, "clear": _clear}


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
