---
name: state
description: Where state lives and how it is declared - SQLite through the state engine for anything one session changes and another must see, files only for records and apparatus, the placement declaration every tool carries, and what the state gate refuses. Load before writing any tool that keeps state, before adding a placement file, and the moment the state gate or a state-engine refusal names a tool.
---

# state — rows, not files

Owner, 2026-09-13: "I don't like using files - i like something more
deterministic and that manages states - and it can be improved over
time"; "i'm not happy that we aren't SQLite more - this got lost
somewhere - but i don't files as state"; "i need to prevent future
default to files when SQLite is better option". Measured that day at
`8acab9aa`: nine tools on four SQLite stores, twelve on JSON or text,
because "a file per row merges cleanly" was the nearest answer to
parallel sessions. Rows never merge at all; that is the point. The
law is `ops/conductor/instrument-conventions.md` §State.

## The question, one line

Does another session need to see this change before my branch merges?
Yes: it is state, and it is a row. No: it is a record (an export from a
store, a corpus row) or apparatus (a contract, a declaration), and it
is a file the tool writes from its rows, never a file a session edits.

## Building on the engine

`ops/conductor/tools/machine.py` (`machine-lever.md`) runs every
machine in one store, `state.db` under the lane's state directory:

1. Declare the machine as data in the tool: places, an initial place,
   transitions with actor class, evidence duty, failure class and
   hold. `handoff.py` is the worked example.
2. Keep the instrument's own columns in its own table, keyed by the
   engine's subject id; the engine owns place, holder and the marking.
3. Write through `create` and `move`; every move is one `BEGIN
   IMMEDIATE` transaction, so a race is a refusal, never a lost update.
4. Reads connect with `create=False`; a read must never create a store
   (the onboard brief checks the file before it runs the sibling).
5. Declare the engine in the placement file:
   `"state": {"engine": "sqlite"}`; a tool that also writes an export
   adds `"files": "<what and why>"`.

State with no places (a plain register) is a table in the same store
or a sibling `.db`, declared `sqlite` the same way.

## What the gate refuses

In session, before the wall: the engine row `file-state` (`ops/devlane/hooks/boundaries.json`) refuses at the write a JSON or text
store under the lane's state directory and a placement file declaring
`"engine": "file"`; its needed half names this page, and no grant lifts
it (T-5, 2026-09-14). The wall is the gate below.

`ops/conductor/tests/test_state_placement.py`, one line per failure:

| the line says | do this |
|---|---|
| `expected state.engine of sqlite, none or file` | add the `state` object to `ops/conductor/placement/<tool>.py.json` |
| `declares sqlite and imports neither sqlite3 nor machine` | the declaration is wrong, or the tool is not yet on the engine |
| `declares none and writes files (<call> at line N)` | the tool keeps state in a file: put it on the engine, or declare the export with a `files` reason |
| `writes files (...) and state.files gives no reason` | say what the file is and why it is a record, not state |
| `a new file-backed store` | the remedy is the engine; the owner's word and a row in `FILE_BACKED` is the only other way |
| `pinned as file-backed and not declared file; shrink FILE_BACKED` | the tool left files behind: drop it from the pin in the same change |

A refusal from the engine itself (`store`, `place`, `taken`, `holder`,
`evidence`, `transition`, `unknown`, `declaration`) is the wire: read
the found clause, do the needed half. `refused` has the spellings.

Record the use: `python3 ops/conductor/tools/skills.py log-use state "<tool>"`
with `WF_AGENT` set.
