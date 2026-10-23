# The state engine — contract

Standing: built at the owner's word (owner, 2026-09-13: "hand grant
ok'd - build the engine and the handoff manager"; "I don't like using
files - i like something more deterministic and that manages states -
and it can be improved over time"; "i'm not happy that we aren't SQLite
more - this got lost somewhere - but i don't files as state"). Source of
demand: the workflow genus (`docs/workflow.md`) says every workflow is a
declared graph executed by a generic engine, never a hand-coded step
chain, and the lane had none: the lock, the grants and the handoffs
each carried their own states in their own code, two of them in files.

## Conventions

This instrument inherits `ops/conductor/instrument-conventions.md`:
the refusal wire, four-class exits, instrument shape, and lint duty
apply as written there. The CLI verbs are reads and need no `WF_AGENT`;
the library's writing calls take the acting identity as an argument
and the instrument that calls them enforces the identity rule.

## Instrument

`python3 ops/conductor/tools/machine.py <verb>`:

| verb | what it prints |
|---|---|
| `machines` | one line per declared machine: name, version, initial place, places; `no state store` when the store is absent |
| `show MACHINE` | the transition table, one line per transition in declaration order |
| `history MACHINE ID` | the marking of one subject, oldest first: stamp, transition, source -> target, agent, evidence, note |

No verb creates the store. stdlib only; no subprocess.

## The store

`state.db` under the lane's state directory (`state_dir()` of the
shared module: `$XDG_STATE_HOME/<lane>/`, else `~/.local/state/<lane>/`),
SQLite in WAL mode with a 5 s busy timeout. One store for every machine
on the host: a session's claim is visible to every other session the
moment its transaction commits, which no file under a branch can do
before it merges. Tables: `machines`, `transitions`, `subjects` (one row
per subject: machine, place, holder, holder note, held since, ttl),
`marking` (one row per move, append-only under two triggers that abort
any update or delete). The store is host-local; a second host is a
second store, and the shared backend for that day is a ruling for the
register, not a change to this contract.

Concurrent first writers can meet SQLite's immediate BUSY response while
establishing WAL mode, before its normal busy handler waits. `connect` retries
that initialization within the same five-second budget, using the existing
state owner and store. An expired budget or a different database error closes
the incomplete connection and propagates to the calling instrument's refusal
wire; it never leaves an extra connection holding the initialization lock.

## The library

An instrument declares its machine as data and calls the engine:

- `Machine(name, version, initial, places, transitions, ttl_s)` and
  `Transition(name, source, target, actor, evidence, failure, hold)`.
  `actor` is `any`, `holder` or `holder-or-lapsed`; `evidence` is
  `none` or `required`; `failure` is the refusal class when the subject
  already sits at the transition's target; `hold` is `take` (the mover
  becomes the holder, with a note and a ttl), `keep` or `clear`.
- `connect(create=True|False)`: the store; a read with no store refuses
  `store` and creates nothing.
- `declare(conn, machine)`: the machine and its transitions as rows;
  declaring twice is once; a transition naming an undeclared place, an
  unknown actor class, duty or hold refuses `declaration`.
- `create(conn, machine, agent, note=None)`: a subject at the initial
  place; its first marking row is `create`.
- `move(conn, machine, id, transition, agent, evidence=None, note=None,
  gate=None, ttl_s=None)`: one transition in one `BEGIN IMMEDIATE`
  transaction. In order: the transition exists (else `transition`), the
  subject exists (else `unknown`), the subject is at the source (else
  the declared failure class when it is already at the target, `place`
  otherwise, both naming the holder, note and held-since when there is
  one), the actor may move it (else `holder`; `holder-or-lapsed` lets
  anyone move it once held-since plus the ttl is past), the evidence is
  present when required (else `evidence`), the caller's gate raises no
  refusal (it refuses on its own class). Then the place row is updated with the
  source in the `WHERE` clause, so a concurrent mover that lost the
  race reads the winner's place and refuses the same way, and the
  marking row is written. Any refusal rolls the transaction back.
- `move_many(conn, machine, ids, transition, agent, ...)`: several
  subjects through one transition in one `BEGIN IMMEDIATE` transaction
  at one stamp, the same checks per subject as `move`; `ids` is a list or
  a callable given the connection under the write lock, so the choice and
  the moves see one state. Any refusal rolls every move back; an empty
  choice moves nothing. The handoff manager's package take is the first
  caller (T-9, 2026-09-14).
- `subject`, `history`, `lapsed`, `history_line`: reads.

## Refusals

| refusal | fires when |
|---|---|
| arguments | an unknown verb or argument shape; a non-integer subject id |
| store | a read while the store is absent |
| machine | `show` of a machine no instrument declared |
| unknown | a subject id no machine has |
| declaration | a machine whose data contradicts itself |
| transition | a move by a name the machine does not declare |
| holder | a move reserved to the holder by another identity, before the ttl |
| evidence | a move whose duty is `required` given none |
| place | a move from a place other than the transition's source |
| *declared* | the transition's own failure class, when the subject already sits at its target |

## Instances

The handoff manager (`handoff-lever.md`) is the first. The deliverable
lock and the hand grants are the next two and stay on their own code
until a session moves them, each under its own grant; a placement file
declaring `file` names that debt (`instrument-conventions.md` §State).

## Lint

Inherited; no per-file row. Tests:
`ops/conductor/tests/test_machine.py`.
