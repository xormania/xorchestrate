# Instrument conventions — the lane's base contract

Current operation (owner direction, 2026-09-24): `AGENTS.md` describes the working
guidance. Hooks warn and guide; CI owns acceptance. Hand grants are retired,
and the pipeline, dispatcher and deliverable records are optional capabilities.
Historical procedure below does not impose prerequisites on ordinary work.


Scope: dev tooling, governed by
[AGENTS.md §Lane boundary and precedence](../../AGENTS.md#lane-boundary-and-precedence).
These instruments operate independently of the Symfony application and
its vendor directory. Shared wire and provenance remain binding; the
application's four projections do not prescribe these tools' runtime or
retirement. The 2026-09-05 owner correction overrides conflicting older
stack, migration and planning records, including when served by onboarding.

Standing: PROPOSED (D-LAW-8). Source of demand: the same conventions
were retyped from recall into three instrument contracts in one night
(register, ledger, watch) and the third retyping dropped a clause — the
refusal one-line rule — which two blind producers then implemented two
ways. The owner's diagnosis is the law here: conventions live as data
plus a gate, never as per-contract recall, "not point tipping the
stochastic scales."

An instrument contract **inherits this document by reference** and
states only what is specific to its instrument. Restating a convention
is drift: two statements of one rule will diverge, and the divergence
will be discovered by a reconciliation instead of a diff.

## The refusal wire

One line per refusal, on stderr, exit 2, stdout empty:

    class: expected <what>; found <what>; needed <remedy>

The class is a lower-case token from the instrument's refusal table.
Expected / found / needed are each present, in that order. Nothing else
accompanies a refusal.

## Identity

Writing verbs require an agent in `Name <address>` form and refuse class
`identity` without one. Three sources carry it, in this order:
`WF_AGENT_OVERRIDE`, then the row this session bound for itself with
`instrument.py identity --set 'Name <addr>'`, then `WF_AGENT`. The
session's own row outranks the ambient key because a session chose it
and nobody chose the ambient one; the override outranks the row because
a single command may always say otherwise. An absent store is an answer
— no session bound a name here — and a store that exists and cannot be
read refuses class `store` rather than falling through. The override
exists because
the ambient key is not always the command's own — a settings file may
bind `WF_AGENT` for every session on a host, and nothing reading the
environment can tell that from a value the command meant. A malformed
override refuses and never falls through to `WF_AGENT`, because falling
through would stamp a row with one agent while the command named
another; a blank override is not a declaration and is passed over. One
reader, `instrument.identity()`. Reads need no identity. Where rows are
stamped, `agent` is what that reader returns and `session` comes from
the harness:
`CLAUDE_CODE_SESSION_ID`, else `CLAUDE_SESSION_ID`, else
`CODEX_THREAD_ID`, else `CODEX_SESSION_ID`, else `unknown` (or
whatever word the caller chose for its own absence, as skills.py chooses
`unrecorded`). A blank value is not a declaration and is passed over.
The two Codex keys identify native interactive sessions. A dispatch's
explicit `CLAUDE_SESSION_ID` binding keeps precedence: the launcher
binds that minted session to its participant before starting the worker.
Native sessions with the same model attribution still bind distinct
participants. Session aliases carry correlation, not authentication: the
coordination core separately verifies the participant capability specified in
`coordination-lever.md`. Changing an alias or attribution cannot substitute
for that capability.
Guarded A-218 startup first validates `AGENTSCIENT_HOOK_PROFILE` through the
installed shared guard. Workers use its issued session; interactive callers
resolve its enrolled capability digest to one participant, retaining any
existing native messaging endpoint. Native aliases must
agree with the interactive association, including `GROK_SESSION_ID`; invalid,
missing or ambiguous associations refuse. The same projection supplies the
original authority HOME and state root when a harness uses a minimal home.
No new identity store or dispatch job is created for interactive terminals.
One reader, `instrument.session()`; a tool that reads the environment
for this itself is a finding (LD-50, measured 2026-09-16: the lane read
`CLAUDE_SESSION_ID` alone, which this harness does not set, so 251
handwork rows and 506 skill events recorded no session at all).

## The four classes on the wire

| exit | class | meaning |
|---|---|---|
| 0 | — | the verb did what it says |
| 1 | Finding | ran to completion; the result carries findings |
| 2 | Rejection | refused before acting; the refusal wire says why |
| 3 | Abort | stopped mid-act by wall, kill, or conflict — printing its last reading first, leaving commits, never residue |

## Instrument shape

`python3 ops/conductor/tools/<name>.py`, stdlib only. Contract at
`ops/conductor/<name>.md` (or `<unit>-lever.md`); tests at
`ops/conductor/tests/test_<name>.py`. Timestamps UTC. Stores honor
`XDG_STATE_HOME`, else `~/.local/state`, so tests plant state under a
temp `HOME`.

## Lint duty

Repo `ruff.toml` governs. An instrument that must shell out earns a
THIS-FILE-ONLY per-file row with the reason stated, or stays clean —
never a wildcard.

## The shared implementation

`ops/conductor/tools/instrument.py` is these conventions as code, and
an instrument imports it instead of carrying a copy: `Refusal` and
`refuse()` for the wire, `emit()` at the CLI boundary (one stderr line,
return 2), `identity()` and `identity_parts()` for `WF_AGENT`, `now()`,
`stamp()` and `parse_stamp()` for UTC stamps ending in `Z`,
`state_root()`, `lane_name()`, `state_dir()` and `conf()` for the state
directory and the lane conf through `laneconf.py`, and `run()` and
`git()` for one bounded, shell-less subprocess call that refuses
`<name>-error` on a timeout or a missing executable. Its two CLI verbs,
`identity` and `refuse CLASS EXPECTED FOUND NEEDED`, give shell levers
the same wire.

An instrument appends its own directory to `sys.path` and imports the
module, so the import resolves however the instrument was loaded: as
the script, by path from another tool, or as a copy a test staged
elsewhere. A suite that stages a copy of an instrument stages
`instrument.py` and `laneconf.py` beside it and names a lane conf
through `LANE_CONF`; the module carries no lane-name literal and no
name stands in for an absent conf. An instrument that finds no
`instrument.py` beside itself refuses `tooling`. Every instrument declares its placement in its own file,
`ops/conductor/placement/<tool>.py.json`, as migrated, pending, or
no-wire (a tool that returns outcomes and never refuses, with nothing to
import); the gate in `ops/conductor/tests/test_instrument.py` reads the
directory and closes it both ways, so an instrument carrying its own
`refuse`, `identity`, clock or lane-name reader is a failure naming it,
a new instrument must add its file, and no two sessions edit the same
line to place a tool (owner, 2026-09-13: "minimize parallel work
conflicts").
The census that demanded this is
`ops/conductor/reports/duplication-census-20260912.md`.

## The conformance gate

`ops/conductor/tests/test_instrument_conventions.py` holds a registry
of instruments that declare this inheritance, each with one hermetic
refusal-provoking invocation, and asserts the wire on every one: exit
2, a single non-empty stderr line, class token, expected / found /
needed, empty stdout. A new instrument joins the registry in the PR
that lands it. The two instruments that predated this document,
skills.py and co_doc.py, joined on 2026-09-13 when they adopted the
wire (owner rulings 1 and 2); no tool under ops/conductor/tools/ is
placed as pending.

## State

### Storage primitives

`instrument.connect_store(path, mode="ro" | "rw" | "rwc")` owns opening
file-backed SQLite connections. `ro` is the default and prohibits writes,
including temporary tables; `rw` can update an existing store; only `rwc`
creates a directory or store. Paths are URI-escaped before SQLite sees
them. Callers own their schema, migrations, row shape, timeout, connection
lifetime and classification of absent or unreadable stores. A synthetic
in-memory empty register is not a file-backed store.

`instrument.transaction(connection)` owns an immediate transaction. The
read that decides and the writes it authorizes belong inside the same
context. Success commits; every exception, including interruption and a
failed commit, rolls back. A nested transaction fails before taking
ownership of its caller's work. Domain operations compose under one
transaction; they do not independently commit steps of an enclosing action.

`machine.connect(create=False)` is a read connection. Writers use
`write=True` to update an existing store or `create=True` to initialize one;
the existing engine still owns its WAL initialization,
schema and busy retry budget. Coordination, handoff, ledger, sessions,
CI and decision read adapters consume the same read boundary.
Findings, hand and skills readers use read-only access, with their required
projection columns checked by `connect_store(..., required={table: columns})`.
Only their explicit writers request creation and run domain schema changes.
`instrument.store_failure` maps failed reads to the common wire with distinct
missing, unreadable, corrupt, locked and incompatible states. An absent store
is not an empty initialized one. The connection primitive retains native
exceptions for other callers with their own established absence policies.

`deliverable show` reads the existing target file and never measures. Its
compatibility option `--unmeasured` remains a read. `done BRANCH` explicitly
measures and records the outcome, source, candidate reference, time and actor
in that file. Legacy commands do not declare all verification inputs, so their
recorded results are historical and applicability remains unknown; an ordinary
status must not authorize work from them. See
[PR-01 contract and evidence](reports/pr01-cheap-reads.md).

The shared invariants and representative adapters are exercised through
`ops/testkit` in `tests/test_storage.py`. The same suite rejects private
file connection and transaction implementations in conductor tools.
Workflow state machines, assignment authority, evidence requirements,
ledger collision recovery and monitor routing remain domain policy.

### State, records and apparatus

Owner, 2026-09-13: "I don't like using files - i like something more
deterministic and that manages states - and it can be improved over
time"; "i'm not happy that we aren't SQLite more - this got lost
somewhere - but i don't files as state"; "i need to prevent future
default to files when SQLite is better option". Measured that day at
`8acab9aa`: four SQLite stores and nine tools on them, twelve tools on
JSON or text, the lock in a JSON file, two ledgers as one file per row,
455 dispatch records and 23 ladders as JSON in the tree.

Three kinds of thing, and only the first is state:

- **State** is what one session changes and another must see now: a
  lock, a grant, a claim, a ledger row, a disposition. State lives in
  SQLite under the lane's state directory, through the state engine
  (`machine-lever.md`) when it has places, in a plain table otherwise.
  A file under a branch is invisible to every other session until it
  merges, and a file under the state directory is a lost update waiting
  for two writers; a row is neither.
- **Records** are append-only evidence in git: exports the tool writes
  from its store, never edited by hand, and the corpus rows that pin
  them. A record carries the reading that produced it.
- **Apparatus** is contracts, corpora, declarations and fixtures: data
  the tools read, not state they keep.

Every tool declares its engine in its placement file,
`ops/conductor/placement/<tool>.py.json`, beside its placement:
`"state": {"engine": "sqlite" | "none" | "file", "files": "<reason>"}`.
`sqlite` means the tool imports sqlite3 or the engine; `none` means it
writes no file; `file` means its state is files, with the reason and
the date, and is the declared debt. The gate
`ops/conductor/tests/test_state_placement.py` reads the tools by ast
and closes the declaration both ways: a write call with no reason, a
`none` that writes, a `sqlite` that imports neither, and a `file` outside
the list the gate pins (`FILE_BACKED`, frozen 2026-09-13: control.py,
deliverable.py, ladder.py, ledger.py) each fail naming the tool and the
line. A new tool that would keep state in a file is refused by the gate
before review sees it; the remedy is a machine on the engine, or the
owner's word and a row in the pin. Shrinking the pin is the only edit it
invites.
