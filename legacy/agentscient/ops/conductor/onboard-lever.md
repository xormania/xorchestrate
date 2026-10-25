# The onboard lever — contract

Current operation (owner direction, 2026-09-24): `AGENTS.md` describes the working
guidance. Hooks warn and guide; CI owns acceptance. Hand grants are retired,
and the pipeline, dispatcher and deliverable records are optional capabilities.
Historical procedure below does not impose prerequisites on ordinary work.


Standing: ratified mandate, D-OPS-4 ("session onboarding becomes an
instrument"; owner: "ratify D-OPS-4 - build onboard.py", 2026-09-01).
Source of demand: onboarding is hand-written handoff prose that rots
at the speed of the repo, while the three things a fresh session needs
are separable — law to read, state to measure, a board to pick up from.
This instrument prints all three; the hand-written handoff shrinks to
what only a person can say.

## Conventions

This instrument inherits `ops/conductor/instrument-conventions.md`:
the refusal wire, four-class exits, instrument shape, and lint duty
apply as written there. Onboarding is a read: no `WF_AGENT` is
required.

## Instrument

`python3 ops/conductor/tools/onboard.py` — no arguments (any
argument refuses class `arguments`). subprocess is used to run `git`,
`gh`, and the sibling instruments; nothing else. It never writes: no
store is created, no ref is touched.

## The brief, four sections in order

Instruction precedence comes from
[AGENTS.md §Lane boundary and precedence](../../AGENTS.md#lane-boundary-and-precedence)
and [ops/README.md](../README.md#lane-boundary-and-precedence). `AGENTS.md` is
a direct entry in the existing reading list; it instructs the reader to read
`ops/README.md` as a second hop, not a separate entry in that list.
A listed file's presence establishes availability,
not current authority for every passage. In particular, `stack.md` is dated
origin material: its replacement/convergence doctrine cannot override the
owner's 2026-09-05 separation. AARs, handoffs and register observations also
retain their provenance without reviving superseded policy. The reading list includes the accepted discovery contract and startup sequence;
its presence checks do not enforce work scope.

**`== guidance`** — the reading list, data in the tool, amended by commit
(the lever-registry pattern):

| path | one-liner |
|---|---|
| `AGENTS.md` | the operating contract |
| `CONTRIB.md` | naming, PR bodies, fix commits, the review wire |
| `ops/conductor/instrument-conventions.md` | the lane's base contract: wire, identity, exits |
| `ops/conductor/stack.md` | the stack: layers, incumbents, open seats |
| `ops/conductor/coverage-zones.md` | what gates what |
| `ops/conductor/discovery-lever.md` | profile discovery, source custody, and uncertainty |
| `ops/conductor/conductor-start-plan.md` | the conductor startup sequence and admission boundaries |

One line per entry: `OK <path> — <one-liner>` when the file exists and
is non-empty, `ABSENT <path> — <one-liner>` otherwise. An absence is
reported, never skipped — an unlisted gap is how law goes missing.

This seven-row table owns the full onboard reading list. Discovery freezes
the first five path/one-liner pairs, in this order, as its
[compact law subset](discovery-lever.md#local-source-composition); the two
additional rows remain onboard-only. An independent equality gate must check
both tables and projections for exact paths, order and one-liners. With all
other readings successful, onboard's complete fixture requires seven OK rows,
no ABSENT rows, `brief complete` and exit 0. One missing or empty declared
law file requires six OK rows, one ABSENT row, `brief complete, 1 absence(s)`
and exit 1. Discovery independently requires five present rows, or four
present and one missing for a missing shared entry, within its existing cap.
The independent test producer owns fixture and exact-count updates; preserving
a five-OK assertion is not acceptance of this seven-row table.

**`== state`** — measured at run time, one labelled line per source;
a source that cannot be measured prints its failure verbatim on that
line (an unperformed read is named, never silent):

- `register:` the output of `decisions.py report`, compacted to its
  counts and blocked ids — or the sibling's refusal line verbatim.
- `ledger:` the status line of `hand.py report` plus open-grant count
  — or the refusal verbatim.
- `prs:` open pull requests measured via
  `gh pr list --state open --limit 200`, number and title each — or
  `gh unavailable: <reason>` when gh ran and reported a failure, or
  `prs: UNREACHABLE: <first>, retried and <second>` under the retry
  rule below; a listing that fills the limit is reported truncated and
  counts unmeasured (a silent first page is a false total). `prs: 0
  open` is the answer for a repository with none, so a read that could
  not look never spells it.
- `dispatches:` record directories in the dispatch store
  (`$XDG_STATE_HOME/agentscient/dispatch`, else `~/.local/state/…`)
  are running only when `record.json` says `launched`, `state.json`
  names a positive integer pid for which signal zero succeeds, and no
  `exit` file exists; then the newest closed record with its exit code.
  This is a PID-existence observation, not proof of worker identity or
  progress: an unreaped zombie or a reused pid can still satisfy it.
  Non-integer, boolean, zero, and negative pids are not running.
  Terminal records and directories
  without `record.json` are not running. A genuinely nonexistent
  store path says ABSENT and is a measured fact. A path that could not
  be read reads `dispatches: UNREACHABLE: <what>` and counts
  unmeasured: a path that exists but is not a readable directory, a
  store that will not list, and a closed record whose `exit` file will
  not open. Each has an answer it must not borrow — ABSENT for the
  store that is not there, `running=none` for the store that held no
  live record, `exit=ABSENT` for the file that held nothing — and a
  read that could not look is entitled to none of them.
- `branch:` current branch and short sha of the repository the tool
  runs from.
- `lock:` the deliverable lock as `deliverable.py show --unmeasured`
  prints it: the branches named and the owner's words on one line, then
  every target on its own `target <branch>: until ...; reading
  unmeasured; unmeasured` line. No measure runs in the brief: `show` ran
  every target's measure in turn, 44 of them in 1m54.208s on 2026-09-23,
  and no brief read the lock inside its bounds (LD-146). The reading is
  `deliverable.py done <branch>`. `lock: none` when no lock is set, a
  measured fact; `lock: unavailable: <reason>` when the lever reports a
  failure, counted unmeasured. A read that expires is retried once with
  a longer bound, and if that expires too the line reads
  `lock: UNREACHABLE: <first>, retried and <second>`, also counted: the
  brief that could not look says so, and never in a word that reads as
  no lock (LD-48, measured 2026-09-16 under five concurrent sessions).
  The bounds are `ONBOARD_TIMEOUT` and `ONBOARD_RETRY_TIMEOUT` seconds,
  5 and 20 unless the environment names otherwise. Owner, 2026-09-15: a
  brief that never
  printed the lock let a plan's paraphrase of the order stand in for the
  order after a compaction; the owner's words and the reading now ride
  every brief.

  **The retry rule is every read's, not the lock's.** LD-48 was found on
  the lock line and repaired there, and the brief's other subprocess
  reads were the gap that repair declared. Each of them — the lock, the
  prs listing, the handoff brief — retries once with the longer bound
  when the first expired, then says `<label>: UNREACHABLE: <first>,
  retried and <second>` and counts unmeasured. `unavailable` stays the
  word for a lever that ran and reported a failure, which is a lever
  that looked.

Sibling outputs are as the siblings' own contracts define them — the
register's report is deterministic JSON, the ledger's report begins
with its `status:` line and carries a `debt:` line — and the read-only
mandate reaches through subprocesses: the ledger's store is checked
for existence before its instrument runs — at the path the sibling
actually opens (`<authority HOME>/.local/state/<lane>/handwork.db`,
the launch binding's HOME in a member whose own HOME is private, LD-146;
the dispatch store, the lock and the handoff store are likewise read
under the binding's XDG_STATE_HOME in a member, where the siblings
read them;
hand.py
predates the conventions' XDG rule, and its conformance gap is
registered) — because a sibling that creates its store on read would
make this tool a writer. An absent dispatch store is a measured fact
on its line, never an absence.

**`== handoffs`** — the handoff board, read from the state engine's
store (`handoff-lever.md`): the store is checked at
`$XDG_STATE_HOME/<lane>/state.db` (else `~/.local/state/<lane>/`)
before anything runs, because a read must never create it. Absent, the
one line is `handoffs: ABSENT <path> (none recorded)`, a measured fact.
Present, the section is `handoff.py brief` verbatim: a `handoffs:` line
with the open, taken and done counts, then each handoff with live
threads and its open and taken threads indented, holder and branch and
age on the taken ones. A brief the manager ran and failed to produce prints
`handoffs: unavailable: <reason>` and counts as unmeasured; a read that
expires twice under the retry rule prints
`handoffs: UNREACHABLE: <first>, retried and <second>` and is counted
the same way. ABSENT is the answer for a host with no store, so a read
that could not look never spells it. The section
shows and never claims: a session takes a thread with
`handoff.py take T-n --branch <its branch>` once the lock names the
branch, at the owner's word or its own choice.

**`== thread`, retired** (T-8, 2026-09-14): the brief once served the
calibration clause of the newest `AAR-*.md` by file name, fenced as
untrusted data (Codex review of PR #106). On a day with five AARs the
file that sorted last was not the newest, and the handoff manager made
the AAR a record that rides the work PR rather than the hand-over. The
board above is the hand-over; no AAR is read, named or quoted by the
brief, and an unreadable one counts no absence.

The final line is `brief complete` when every law entry was OK and
every state source measured, else `brief complete, N absence(s)`.

## A continuous process

The brief is never finished (owner: "the goal is to improve
onboarding - as a continuous process", 2026-09-01). The law table and
the state sources are registries amended by commit, and the demand
signal is declared, not guessed: every AAR answers the standing
question *what did onboarding fail to carry into this session?*, and
each answer lands as a registry amendment riding the AAR's PR — or,
between AARs, as a wish in the skills registry naming the gap. The
brief improves at the speed of the AARs, by the same
recurrence-as-demand doctrine that builds levers. A brief that has
not changed across several AARs is not finished either; it is
evidence the question stopped being asked.

## Exits

0 — brief complete. 1 (Finding) — brief printed with absences or
unmeasurable sources; the count is in the final line. 2 (Rejection) —
arguments, or the tool is run outside a git work tree (class `repo`:
the brief is repository-relative). Nothing here can Abort: every
subprocess is given a bounded timeout, and a source that exceeds it is
an absence on its line, not a hang.

## Refusals

| refusal | fires when |
|---|---|
| arguments | any command-line argument is given |
| repo | the working directory is not inside a git work tree |

## Lint

Inherited. The tool shells to `git`, `gh`, and sibling instruments —
it earns its THIS-FILE-ONLY row (S603), stated in the PR.

## Related

Discovery of homes, retained-ref handoffs, and owner action interfaces
is specified in [`discovery-lever.md`](discovery-lever.md). This
instrument's CLI and state acquisition remain unchanged. Its law table now
includes discovery and the startup sequence. Discovery/3 deliberately retains
the five-row compact law projection specified by its accepted envelopes.

The v3 runtime is projected by `tools/lane_mcp.py` through the shared
`tools/lane_read.py` core and `tools/lane_refs.py` acquisition coordinator.
The declared callable is `ops.conductor.tools.lane_refs.acquire(config, boot,
*, checkpoint=None)`; that module also owns the fixed Git read operations.
Both projections pass its immutable readings to lane_read. Independent race
fixtures import this symbol under the
[checkpoint contract](discovery-lever.md#deterministic-acquisition-checkpoints),
including exact event multiplicity and per-source partial order.
Trusted launch supplies a complete v3 descriptor; `check` validates it without
acquiring sources. `lane_start` requires a configured profile, then targeted
homes, handoffs and actions use the same source snapshot. Work continuations
use the returned cursor and snapshot through the CLI or MCP tools.

The adapter reads local SHA-1 loose objects and indexed Git packs directly,
with descriptor-relative access, byte verification and acquisition budgets.
Unsupported storage formats and unsafe bindings produce explicit unavailable
readings. It neither invokes Git nor interprets local Git configuration.
Definition bytes must arrive through the
[trusted --discovery-boot-fd transport](discovery-lever.md#trusted-discovery-boot-transport),
bound to the executed artifact rather than adjacent mutable paths. This is a
contract obligation pending independent runtime/fixture verification under
the [D-A47 executed-byte gate](discovery-lever.md#executed-byte-binding-gate).
The lane_mcp boot custodian checks all three modules' actual load evidence;
the trusted launch owner supplies bound execution inputs. Independent mismatch
and clean controls must verify both duties; digest equality alone does not
establish execution identity or authenticate the launcher.
Anchor identities are pinned at boot; each query reads
its profile's complete bounded source closure and checks it for change.
These interfaces enable later automatic onboarding; they do not start conduct,
activate host settings, enforce harness permissions or publish lifecycle records.

The v3 [local composition table](discovery-lever.md#local-source-composition)
explicitly leaves register/ledger/PR readings unavailable and projects only
authorized local dispatch summaries and pinned Git state. It does not inherit
this instrument's subprocess, home lookup or network acquisition.
