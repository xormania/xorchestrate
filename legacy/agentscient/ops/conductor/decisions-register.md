# The decisions register — contract

Standing: the tracked seed file, `decisions.seed.json`, is the register
(owner, 2026-09-11, in session: "register instrument ok" to amending this
contract rather than building the SQLite instrument commissioned on
2026-08-31). That commission is withdrawn; this contract stays as the
specification an instrument would be built against if a queue ever
outgrows the file. Ratifying the register's
place in the process is a batch decision; the tool exists so that batch
has a queryable home.

The problem it closes: pending decisions live scattered across design
docs, PR branches, and session transcripts, and decisions ratified in
chat exist nowhere durable. The register is the one queue: every act
awaiting the owner is a row; every ratification is recorded with its
evidence; `board.py decisions` (the ratified board source for "tracked
open questions awaiting the owner") gains a feedable store.

## Instrument

`python3 ops/conductor/tools/decisions.py <verb> …`, stdlib only
(sqlite3, argparse). Database: `~/.local/state/agentscient/decisions.db`
(created on first write). SQL: identifiers from code, values from `?`
parameters — the store.py invariant; no dynamic identifier
interpolation is needed here at all.

## Identity

Every writing verb requires `WF_AGENT` matching `.+ <addr>` (the
skills.py rule). Reads (`list`, `show`, `export`, `report`) need none.
Every write appends an event row (verb, decision id, agent, timestamp
UTC) — telemetry is not optional.

The aggregate reads (`list`, `export`, `report`) refuse when the store
file is absent — an unperformed read is not an empty register; the
tracked seed export names what the store held at birth. `show` refuses
`unknown` either way: no id can be found in a store that is not there.

## The row

| field | meaning |
|---|---|
| id | `^D-[A-Z0-9]+-[0-9]+$` — e.g. `D-WO-1`, `D-LAW-3`, `D-OPS-1`; unique |
| question | one sentence, the decision as a question or claim; four words minimum |
| home | where the full text lives — a repo path, optionally `#anchor` |
| standing | `proposed` \| `ratified` \| `rejected` \| `superseded` |
| blocked_on | optional free text naming what must happen first |
| decided_by | the principal who decided (only when standing leaves `proposed`) |
| decided_at | UTC stamp of the deciding act |
| evidence | pointer or verbatim quote backing the deciding act |
| recorded_by | the WF_AGENT that ran the verb (may differ from decided_by) |
| supersedes / superseded_by | amendment chain, both directions kept |
| created_at / created_by | intake stamp |

## Verbs

- `add ID QUESTION --home PATH [--blocked-on TEXT] [--supersedes ID]`
  — intake at standing `proposed`.
- `list [--standing S] [--blocked]` — one line per row, id, standing,
  question; default lists `proposed` first.
- `show ID` — the full row plus its event history.
- `ratify ID --evidence TEXT` / `reject ID --evidence TEXT` — the
  deciding acts. See "Standing is not self-conferred".
- `supersede ID --by NEWID --evidence TEXT` — `ID` must be `ratified`;
  `NEWID` must exist; both directions are written; the act is logged.
- `note ID TEXT` — append an event without changing standing.
- `export` — every row as deterministic JSON on stdout: sorted by id,
  sorted keys, UTC stamps; byte-identical for identical stores. This is
  what lands in evidence commits and what a later board adapter reads.
- `report` — counts by standing, oldest open decisions, open decisions
  blocked and on what (a decision no longer `proposed` has stopped
  waiting; its `blocked_on` is history, not a queue entry).

## Standing is not self-conferred

Ratification is the owner's act, non-delegable (looplaw
`ratify/authority`, adopted in looplaw-findings §4). Mechanically:

- `ratify`, `reject`, and `supersede` run by an agent whose address is a
  harness vendor (`noreply@anthropic.com`, `noreply@openai.com`,
  `noreply@x.ai`) **refuse unless `--evidence` is given**, and the row
  records `decided_by` from the evidence's named principal, never from
  `WF_AGENT`. Recording on the owner's behalf is legal only with the
  owner's words attached.
- The same verbs run by any other identity accept, with `decided_by` =
  that identity; `--evidence` remains recorded when given.
- A harness identity attempting `ratify` without evidence is refused
  with the class named: standing is not self-conferred.

## Transitions

`proposed → ratified`, `proposed → rejected`, `ratified → superseded`.
Everything else refuses naming the transition — a rejected decision is
re-raised as a new row citing the old (the intake immutability rule,
applied here).

## Refusals

Every refusal names expected / found / needed, one line, exit 2:

| refusal | fires when |
|---|---|
| identity | `WF_AGENT` unset or not `.+ <addr>` on a writing verb |
| id-shape | id fails the pattern |
| duplicate | `add` on an existing id |
| unknown | any verb on an id not in the store |
| question-short | fewer than four words (a stamp, not a question) |
| transition | any move not in the table above, named from → to |
| self-conferred | harness-vendor identity deciding without `--evidence` |
| successor | `supersede --by` naming a missing id, or itself |
| store | `list`/`export`/`report` when the store file is absent |

Exit 0 on success, 2 on refusal; refusal text on stderr, results on
stdout.

## Lint

Repo `ruff.toml` governs. The tool needs no subprocess and no dynamic
SQL identifiers, so it earns no per-file ignore rows.
