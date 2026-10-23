# The coordination core — contract

Standing: proposed, package B of the agent-coordination objective, at
the owner's word relayed by the Lead session (owner, 2026-09-16: "hand
grant ok'd", relayed by agentscient-11 on 2026-09-16 with the six
decisions this page implements). Source of demand: the objective's §6,
durable coordination state, and §7, one domain implementation behind
two projections. Measured 2026-09-16 at `46639344`: the lane binds a
handoff thread (`handoff-lever.md`) and a deliverable lock
(`deliverable.py`) and has nothing that binds a participant, carries an
assignment between sessions, or keeps a message an agent still owes an
answer to.

Package B is this core and its CLI projection. The MCP action
projection is built over the same module by another session.

**The seam.** Every verb of the core is one function taking typed
arguments and the resolved participant, returning a dict, and raising
`instrument.Refusal` with expected, found and needed. The authority
checks — participant, role, holder, generation, revision, producer —
live in those functions, so they run whoever calls them: a projection
cannot reach the store around them, and a hook the MCP plane is blind to
is never where authority sits. The resolved participant is an argument
so that both projections stay thin, and what that argument carries is an
id: every function reloads the row from `participants` by it, refuses
class `participant` unless the loaded row's `session_id` is
`instrument.session()`, and reads the loaded row from there on. Nothing
else the caller passes is believed — the roles above all, because a
mapping carrying this session's id beside another participant's id and
roles is what a comparison on `session_id` alone admits. `bind` reads the
session id rather than taking it, for the same reason: an argument by
which a process names another session is an argument by which it acts as
one. `bind-for` still takes one, because minting a participant for a
session that cannot bind itself is what it is for, and it is a
dispatcher's verb and records its issuer. The boundary binds the
effective caller; the argument carries it and never chooses it.

The session id is a correlation id, never a credential. The owner required
the remaining PR #261 session-replay finding repaired on 2026-09-18,
superseding the 2026-09-16 acceptance of session ids as bearer credentials.
Each participant now has an independent capability with 256 random bits.
Only its SHA256 digest (of the 64-character lowercase hexadecimal value,
encoded as ASCII) is stored in `participants.capability_digest`. The core
authenticates this capability whenever it resolves or revalidates a caller,
including an existing-session re-bind; copying an id, agent string, role or
participant mapping does not authenticate it. Comparison is constant-time.
Missing, malformed, mismatched and legacy-null credentials refuse class
`capability` before participant state changes. No refusal echoes a capability.

The existing validated native profile owns the raw capability in its private
`coordination_capability` field. An unprofiled CLI may carry its own value in
`COORDINATION_CAPABILITY`. A declared profile takes precedence and cannot fall
back to an ambient value. Native launch clears inherited credentials and
projects only its own. Profiles, their directory permissions, and the trusted
operator's filesystem are credential custody: this does not claim isolation
from arbitrary code running with that same operating-system account.

An owner terminal may use `bind --run R-n` without a capability. The run must
exist. Bind mints a private token, enrolls its digest and the run in the same
participant transaction, and writes a 0600 owner-terminal-profile/1 pointer
at `<state dir>/runs/R-n/terminals/PT-n/home/profile.json`. It prints only
`profile <path>`. Every verb accepts that path through `--profile PATH` or
`AGENTSCIENT_HOOK_PROFILE`; conflicting declarations refuse. Reads verify
the pointer's participant, run and digest against the enrolled row. Invalid
path components, symlinked ancestors and mismatched enrollment refuse.
If writing the pointer or committing enrollment fails, neither survives.
A terminal rebind authenticates its existing pointer and preserves its run
and digest. A bound participant without a terminal pointer uses `join` to
associate with a run; rebind does not convert or rekey its credential.

Self-bind requires the new caller's capability; a re-bind proves the existing
one and never replaces its digest. `bind-for` receives only a capability digest
for the worker, from an authenticated dispatcher. The launcher creates the
worker profile first and never puts the raw capability in argv, an acting MCP
config, dispatch records, messages, public responses or SQLite. Resume and
relocation retain that profile's credential. Generic participant listings omit
session ids; own bind/who and dispatch receipts retain correlation ids needed
by existing custody readers.

Existing rows and their history are preserved. A nullable-column migration
does not enroll a legacy participant. Such participants remain inert until an
explicit owner-controlled offline cutover installs digests from newly prepared
private profiles while holding the coordination writer lock. This maintenance
uses the core's private enrollment helper, outside `CORE`, the CLI and MCP action
catalogues; it does not expose a session-authenticated upgrade or a first-writer
claim on an existing row. There is no mixed legacy authentication mode. Active
participants are provisioned explicitly; retained historical rows may remain
inert. The operation must preserve roles, assignments and authority history.

Both projections are therefore thin and
neither holds authority of its own; the CLI verb parses argv, calls the
function and prints, and the MCP action does the same with a result
shape instead of a print.

**The tables.** The core is reached through `CORE`, keyed by the verb's
wire name, so a projection calls `CORE["return"]` by the string and
never imports a function named for a verb — `return` is a Python keyword
and a projection guessing `return` then `return_` is two guesses wearing
a refusal (ruled by the Lead session, 2026-09-16). The functions behind
the table are spelled `core_<verb>` and that spelling is private; the
table is the entry point. `VERBS` keys the CLI wrappers by the same wire
names, and `set(CORE) == set(VERBS)` is asserted by the suite, so a verb
cannot be added to one projection and quietly missed by the other.

A read verb takes the store connection and returns `{"store": "absent",
...}` when there is nothing to read, so "could not look" stays
distinguishable from "looked and found nothing"; the CLI prints `no
store` for the first and `no <things>` for the second.

## Conventions

Inherits `ops/conductor/instrument-conventions.md`: the refusal wire,
the four exit classes, the instrument shape, the identity rule and the
lint duty. Runs on the state engine (`machine-lever.md`): every row
lives in `state.db`, never in a file. Writing verbs require an identity and
an authenticated bound participant; public reads require neither and never
create the store. Reads scoped to the calling participant authenticate that
binding without requiring attribution.

**Authority is enforced inside the core, never by a hook.** Measured by
the wall session, 2026-09-16: the hook engine cannot see MCP tool calls
from any server but serena, and the grant escape is inert on them. A
check that lives in a hook does not run when package C calls the same
function, so every role, holder, generation and revision check below is
a gate inside the function it guards. Hooks are feedback only.

## Ids

`PT-n` a participant, `A-n` an assignment, `M-n` a message, `E-n` an
observation, `DC-n` a coordination decision, `R-n` a resource claim.
`P-` is not used: it is the probe corpus row id the evidence shapes
already accept. `D-` is not used: it is the owner-decision register of
`decisions.py`, whose ids are `D-WORD-n`. A coordination decision is
not an owner decision and never enters that register; when one needs
the owner's word, the link is recorded and the register keeps its own
row.

## Participants

A **participant** is one session acting in the lane, not a model and
not a display name. The table:

    participants(id INTEGER PRIMARY KEY AUTOINCREMENT,
                 session_id TEXT NOT NULL UNIQUE, agent TEXT NOT NULL,
                 harness TEXT, roles TEXT NOT NULL, endpoint TEXT,
                 probe TEXT, bound_at TEXT NOT NULL, bound_by INTEGER,
                 bound_for INTEGER, granted_by INTEGER, granted_at TEXT,
                 capability_digest TEXT)

`session_id` is `instrument.session()` and is unique, so two sessions
on one host are two participants; `agent` is `instrument.identity()`
and is attribution only. This is the defect the core exists to close:
the engine's holder is the agent string (`machine.py:255` compares
`row["holder"] == agent`), so three sessions sharing one `WF_AGENT`
value satisfy each other's holder checks. The participant is the
authority everywhere below; the agent string never is.

`bound_participant()` reads `instrument.session()`, refuses class
`session` when the harness set none, and refuses class `participant`
when no row is bound to it. It authenticates the private capability against
that row before returning it. Its optional `participant` keyword narrows the
expected binding for a configured server and never supplies authority.
It is not a verb and does not wear the wire
name of `resolve`, which is the message verb, so a projection mapping
its tools by wire name has one thing to reach. **No verb takes the acting participant as an argument.** A
caller-supplied participant or role is refused class `arguments`, so
neither a CLI flag nor an MCP field can confer another participant's
authority (objective §7). Package C resolves the same way at its own
boundary.

`roles` is a comma-separated set drawn from `lead`, `coordinator`,
`dispatcher`, `worker`, `verifier`. A verb whose authority the bound
roles do not carry refuses class `role`, naming the roles held and the
role needed.

### Authority is a chain, not an argument

A role a caller names for itself confers nothing (objective §6 and §7).
Codex and Grok both reproduced the same hole against `05b8aebe`: a bound
worker re-bound itself as `lead,dispatcher` and drafted its own work,
and a `bind-for` rewrote a bound lead's row. The chain, ruled by the
Lead session on 2026-09-16:

- The store's **first** participant is the bootstrap and may self-bind
  with any roles, because a store with nobody in it has nobody to grant
  them. The count that decides this and the insert that acts on it take
  the write lock together, the way a transition does: the store is opened
  in autocommit, so two first callers reading the same empty table
  between them would both be the bootstrap and the chain would have two
  roots. `bind-for` takes the same lock over its conflict check and its
  mint.
- Every **later** self-bind is `worker` alone; naming more refuses class
  `role`, with the grant verb as its remedy.
- **`grant PT-n --roles R[,R]`** is how roles above `worker` are held.
  Only a `lead` may run it; the row records `granted_by` and
  `granted_at`, so the chain is readable back to the bootstrap.
- **`revoke PT-n --roles R[,R]`** takes them back, and only a `lead` may
  run it. The row records `revoked_by` and `revoked_at` beside the grant
  fields, because a role put down is as much a fact as a role conferred.
  It refuses class `role` when the roles it would leave hold no `lead`
  anywhere in the store — counting what stands after the subtraction,
  whoever the subject is, so a last lead cannot take its own down — and
  when the participant does not hold what is named, since taking back
  what was never given is a refusal rather than a silent success. What
  is left is never nothing: a participant stripped of everything stands
  at `worker`, where a fresh self-bind would stand.
- A **re-bind** by the row's own session refreshes its `endpoint`, its
  `probe` and its `agent` string, and never its roles: a re-bind naming
  roles the row does not hold refuses class `role`.
- **`bind-for`** never overwrites. A session that already holds a row
  refuses class `conflict`, naming the participant that stands. A
  `dispatcher` mints `worker`; anything above it is a grant, so the
  issuer must also hold `lead`.

### Retired hand-grant recovery

Hand grants no longer authorize role changes. The grant-based `recover-roles`
operation is removed; historical recovery receipts remain readable. Existing
coordination roles still govern mutations inside this optional tool. They are
not prerequisites for ordinary edits, branch work, or collaboration.

The owner's recovery path for repository acceptance is GitHub administration,
outside agent-callable repository operations; see `AGENTS.md`.

### A participant for a dispatched session

A dispatched Codex or Grok worker has no harness session id in its
environment, so `bind` — which binds the calling session — leaves it
with no participant, and every mutation it attempts refuses class
`participant`. Raised by the acting-server session against this contract
at `537f3a35`. The launcher already mints a session id per dispatch, so
the material exists.

`bind-for --session ID --roles R[,R] --for A-n --capability-digest SHA256`
binds a participant to a
session id the caller names. Only a participant holding `dispatcher` may
issue one; another refuses class `role`, and a roles set above `worker`
needs `lead` as well. A session that already holds a row refuses class
`conflict`; the verb never overwrites. The row records `bound_by`, the
issuing participant, and `bound_for`, the assignment it was issued for,
and `participants` prints both, so a participant nobody dispatched is
visible as such. `bind` is unchanged and stays the interactive session's
verb.

Binding and offering the assignment are one state-engine transaction. A
drafted assignment is offered to the issued participant; an unaddressed or
matching role offer is addressed to that participant. Another participant's
offer, a role the issued participant does not hold, held work and finished
work refuse without creating a participant or changing the assignment.
Credential enrollment, recipient, generation and marking commit together or
roll back together. The issuing dispatcher owns this handoff; it does not
need the coordinator role and the worker does not gain permission to offer.
The receipt still names the participant, issuer and assignment.

`reassign` handles both offered and held work under its existing lead or
coordinator authority and generation check. Offered work uses the machine's
`address` transition. Thus a failed startup can be recovered through the
existing reassignment operation, with its reason retained in the marking.
Dispatch does not steal a named offer or infer that an existing worker died.

Setting that id into the worker's environment is the launcher's half and
belongs to the MCP package's second slice, not here. It is set as
`CLAUDE_SESSION_ID`, the older key `instrument.session()` already admits
(instrument.py), so `instrument.py` does not change.

## The assignment machine

Places: `drafted`, `offered`, `held`, `submitted`, `verified`,
`accepted`, `superseded`. Declared as data in the tool and read by the
tests:

| transition | from | to | actor | evidence | failure | hold | gate |
|---|---|---|---|---|---|---|---|
| offer | drafted | offered | any | none | `offered` | keep | offer: lead or coordinator; bind-for: dispatcher; generation |
| address | offered | offered | any | required (why) | `offered` | clear | reassign: lead or coordinator; bind-for: dispatcher with an unaddressed or matching role offer; generation |
| claim | offered | held | any | none | `taken` | take | the named recipient; the branch is in the lock; generation |
| release | held | offered | holder-or-lapsed | required (why) | `place` | clear | generation; bumps it |
| reassign | held | offered | any | required (why) | `place` | clear | lead or coordinator; generation; bumps it |
| submit | held | submitted | holder | required (a reading) | `place` | keep | generation; revision |
| verify | submitted | verified | any | required (a reading) | `place` | keep | verifier; not the producer; generation; revision |
| return | submitted | held | any | required (findings) | `place` | keep | verifier or lead; generation |
| accept | verified | accepted | any | required (a reading) | `place` | keep | lead; not the producer; generation |
| reopen | verified | held | any | required (the new revision) | `place` | keep | lead or coordinator; bumps generation; stales evidence |
| supersede | offered | superseded | any | required (the newer id) | `place` | keep | lead or coordinator |

Actor stays `any` wherever the authority is a role rather than the
hold, and the role is checked in the gate; the engine's `gate` callback
(`machine.py:291-292`) runs under the same write lock, before the
UPDATE, and its refusal rolls the whole transaction back
(`machine.py:338-343`). No change to `machine.py` is made by this
package.

`subjects.holder` is an opaque string the engine compares for equality
and nothing else (`machine.py:253-264`), so what it carries is each
machine's own choice. **These machines choose the participant**: the
actor handed to `machine.move` and `machine.create` is `PT-n`, never the
agent string. The `holder-or-lapsed` transitions — `release` and `free`
— therefore compare the participant, so the holder that changed its
agent string still holds and a stranger that shares one does not, and
`release` honours the lapse the transition declares. The marking's
attribution column takes the same value; the agent string behind it is
on the participant's own row, recorded at bind. Widening the engine
itself so that holder identity is typed rather than chosen per machine
stays a later question, recorded as idea I-034.

Submission, verification and acceptance are three separate places
because §11 requires three separate records. `return` keeps the holder,
so a returned candidate goes back to the same participant.

The side table carries what the subject row cannot, and its `id` is the
engine's subject id, the pattern `handoff.py:275-278` already uses:

    assignments(id INTEGER PRIMARY KEY, title TEXT NOT NULL,
                outcome TEXT NOT NULL, requirement TEXT,
                scope TEXT, inputs TEXT, recipient TEXT,
                depends TEXT, revision TEXT, generation INTEGER NOT NULL,
                duty TEXT, thread INTEGER, branch TEXT,
                opened_by INTEGER NOT NULL, opened_at TEXT NOT NULL,
                superseded_by INTEGER)

`thread` is the nullable link to a handoff thread (`T-n`). A thread is
one item of a closing session's package and is not redefined here
(objective §3): an assignment names its recipient before the claim,
carries dependencies, a revision, a generation and an evidence duty,
and has places a thread does not. A claim reuses the thread's own
rule — the branch must be named in the deliverable lock
(`handoff-lever.md:68-72`) — and refuses class `deliverable`
otherwise. The lock stays file-backed and out of this package; the link
is the branch name.

## Generation and revision

The engine's compare-and-swap is on the place alone
(`machine.py:302-307`), so a reassignment that returns a subject to a
place it held before cannot by itself refuse the old holder. Two
columns close it:

- `generation` starts at 1 and is bumped, in the same transaction as
  the move, by **every** transition. Bumping it only where the holder
  changed left the verbs that do not change the holder — `submit`,
  `verify`, `return`, `accept` — moving an assignment while its
  generation stood still, so a verifier that began a `return` against
  one submission had it land on the next one; and `supersede` and
  `offer` stated no generation at all, so a coordinator's stale view of
  an `offered` assignment superseded the one that stands there now. One
  bump a move closes both, because a delayed verb then refuses on the
  gate it already has. Every writing verb on an assignment states the
  generation it believes; the verb reads it first, so a stale caller
  reads class `generation` and not class `place`, and the gate reads the
  row again under the write lock and refuses class `generation` with
  expected, found and needed when they differ. A stale holder is
  refused, not silently applied. The cost is stated rather than hidden:
  a caller running a chain of verbs re-reads the generation between
  them, from `show`, because every one of them moved it.
- `revision` is the candidate the work is against. `submit` states the
  revision it produced; `verify` names it as `--candidate` and refuses
  class `revision` when it is not the assignment's. `reopen` sets a new
  revision and marks every observation of the assignment `stale`,
  keeping the old rows and their original scope (§11).

An assignment with **no revision** is a drafted one: `--revision` stays
optional on `draft`, because a drafted assignment can have no candidate
yet. Two rules keep that from becoming a hole through which evidence of
nothing passes (Grok #1 and Codex #2 against `05b8aebe`, objective §11 —
a changed candidate marks affected evidence for reassessment):

- `observe` refuses class `revision` on an assignment with no revision,
  needing a draft or a submit that names one first.
- `submit` marks `stale` every current observation whose revision is not
  the one submitted, keeping the rows and their scope.

Every transition names its gate, `return` included: it passes the same
generation gate under the write lock as its siblings, and its findings
go through the same evidence shapes as every other reading.

## Messages

Four facts, kept apart, because a transport receipt is not an answer:

- **queued** — `queued_at`, written in the same transaction as the
  state change that caused the message.
- **delivered** — `delivered_at` and `delivery_ref`, the transport's
  own receipt; for a Claude peer the `msg_id` the send call returned.
- **acknowledged** — `acked_at` and `acked_by`, the recipient's own
  ack verb. Measured 2026-09-16: no channels facility exists in Claude
  Code 2.1.273, so the route of record for Claude peers is native
  cross-session messaging, and for a cross-harness event a management
  session on a scheduled wakeup reading the inbox, its interval
  declared.
- **resolved** — a place, moved by the holder of the obligation, never
  by the ack.

The core never sends. Transports are adapters outside it; the core
records intent and receipt. The latency reading is `acked_at` minus
`queued_at`, both from the core, beside the transport's own stamp where
it gives one.

    messages(id INTEGER PRIMARY KEY, sender INTEGER NOT NULL,
             recipient TEXT NOT NULL, purpose TEXT NOT NULL,
             body TEXT NOT NULL, artifact TEXT, assignment INTEGER,
             decision INTEGER, reply_to INTEGER, queued_at TEXT NOT NULL,
             delivered_at TEXT, delivery_ref TEXT, acked_at TEXT,
             acked_by INTEGER)

The message machine: places `open`, `resolved`, `withdrawn`.

| transition | from | to | actor | evidence | failure | hold | gate |
|---|---|---|---|---|---|---|---|
| resolve | open | resolved | any | required (a reading) | `place` | keep | the recipient, or the holder of the named assignment |
| withdraw | open | withdrawn | any | required (why) | `place` | keep | the sender |

`purpose` is `inform`, `request` or `finding`. A `request` and a
`finding` are obligations and appear in `outstanding` until resolved;
an `inform` does not. `recipient` is a participant id or a role name,
so a finding can be addressed to whoever holds a role.

## Observations, decisions, resources

    observations(id INTEGER PRIMARY KEY AUTOINCREMENT,
                 assignment INTEGER NOT NULL, producer INTEGER NOT NULL,
                 requirement TEXT, execution TEXT, revision TEXT NOT NULL,
                 environment TEXT, observation TEXT NOT NULL,
                 artifact TEXT, applicability TEXT NOT NULL,
                 stale_reason TEXT, at TEXT NOT NULL)

`applicability` is `current` or `stale`; `execution` is a dispatch
record id, `artifact` a sha, `#N` or a record id. An observation is
never edited: `reopen` sets `applicability` and `stale_reason`, and the
row's own text and scope stay as produced.

`observe` names the generation the producer believes and the revision it
observed. It refuses class `generation` when the stated generation is
not the assignment's, and class `revision` when the assignment's current
revision is not the one observed — otherwise a participant that had not
seen a reopen could append an observation of the old candidate and it
would read as current evidence of the new one. The checks and the insert
take the write lock together, the way a transition does, so a reopen
committing between them cannot slip through. Filing the row `--stale` is
the declared escape and lifts both checks, because a reopen moves the
generation and the revision together; the row lands marked, with the
revision it was made against and the assignment's own in its reason, so
it is history and never evidence of what replaced it.

    decisions(id INTEGER PRIMARY KEY AUTOINCREMENT, asked_by INTEGER NOT NULL,
              question TEXT NOT NULL, alternatives TEXT NOT NULL,
              assignment INTEGER, counter TEXT, unknowns TEXT,
              choice TEXT, rationale TEXT, consequence TEXT,
              decided_by INTEGER, decided_at TEXT, asked_at TEXT NOT NULL)

Only a participant holding `lead` may decide; another refuses class
`role`. A decision with no `counter` and no `unknowns` recorded is a
declared gap on its brief, visible, not a silent pass (§11).

Resource claims need no new machinery: the engine's own hold is a
lease. Machine `resource`, places `free` and `held`; `reserve` takes
the hold with a ttl, `free` clears it under `holder-or-lapsed`
(`machine.py:227-233, 257-258`). The side table is
`resources(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
assignment INTEGER, note TEXT)`. A lapsed lease is evidence that a
reservation ran out, never proof that the previous process stopped
(§6); `reserve` after a lapse prints the previous holder and the
reconciliation the caller still owes.

## Instrument

`python3 ops/conductor/tools/coordination.py <verb>`:

| verb | effect |
|---|---|
| `bind --roles R[,R] [--harness H] [--endpoint E] [--probe TEXT]` | binds this session; prints `participant PT-n`; in an owner's terminal `--run R-n` names the run an owner's terminal joins and refuses `absent` when it names no run (sessions-lever.md, the owner's terminals). Every verb takes `--profile PATH`, read as `AGENTSCIENT_HOOK_PROFILE` is, and refuses `arguments` when both name different profiles |
| `bind-for --session ID --roles R[,R] --for A-n [--harness H]` | a dispatcher binds a participant for a dispatched worker's minted session |
| `grant PT-n --roles R[,R]` | a lead grants roles above worker; records the grantor |
| `revoke PT-n --roles R[,R]` | a lead takes them back; never the store's last lead |
| `who` | this session's participant and roles; `no store` and exit 0 when there is none |
| `participants [--role R]` | one line per participant |
| `draft TITLE --outcome TEXT [--requirement REF] [--revision SHA] [--depends A-n,..] [--duty TEXT] [--scope TEXT] [--inputs REF]` | prints `assignment A-n` |
| `offer A-n --to WHO --generation N` | names the recipient |
| `claim A-n --branch B --generation N [--ttl MIN]` | takes the hold |
| `release A-n --why TEXT --generation N` | gives it back; bumps the generation |
| `reassign A-n --to WHO --why TEXT --generation N` | lead or coordinator; bumps the generation |
| `submit A-n --evidence READING --revision SHA --generation N` | the candidate |
| `verify A-n --candidate SHA --evidence READING --generation N` | an independent verifier's pass: the candidate it ran against and a reading of the run |
| `return A-n --findings REF --generation N` | back to the holder |
| `accept A-n --evidence READING --generation N [--exception TEXT]` | the lead's acceptance |
| `reopen A-n --revision SHA --why TEXT --generation N` | a changed candidate; stales the observations |
| `supersede A-n --by A-m --generation N` | an offered assignment replaced |
| `assignments [--state S] [--to WHO] [--mine]` | one line each, oldest first |
| `show A-n` | the row, its marking, its observations and its open messages |
| `send --to WHO --purpose P --body TEXT [--assignment A-n] [--decision DC-n] [--reply-to M-n] [--artifact REF] [--key K]` | prints `message M-n queued`; with `--key`, the same key and payload again prints the existing `message M-n queued (repeated)`, another payload refuses `idempotency` (sessions slice 2) |
| `deliver M-n --ref REF` | the transport's receipt |
| `ack M-n` | the recipient's acknowledgment |
| `resolve M-n --evidence READING` | the obligation answered |
| `withdraw M-n --why TEXT` | the sender takes it back |
| `inbox [--unresolved]` | messages to this participant or its roles |
| `outstanding [--to WHO]` | requests and findings not yet resolved |
| `undelivered [--to WHO]` | queued to the caller and not yet delivered |
| `route --take ROUTE [--member SM-n]` | the courier takes the caller's delivery route (`stdout` or `native`) with its own pid and start ticks; a live holder refuses `route` (sessions slice 2) |
| `route --release` | the courier gives its route row back |
| `routes` | every live and dead route row |
| `attempt M-n --route ROUTE --generation N` | reserves one delivery attempt before the submission; prints `attempt AT-n reserved` |
| `attempted AT-n --outcome O [--ref REF] [--error TEXT]` | settles the attempt (`written`, `accepted`, `unsupported`, `failed`, `uncertain`); the first `written` or `accepted` records the message's `deliver` |
| `attempts M-n` | the attempts of one message, newest first |
| `observe A-n --observation TEXT --revision SHA --generation N [--stale] [--execution REC] [--artifact REF] [--requirement REF] [--environment TEXT]` | prints `observation E-n` |
| `observations A-n [--all]` | current observations, `--all` includes stale |
| `ask --question TEXT --alternatives TEXT [--assignment A-n]` | prints `decision DC-n` |
| `answer DC-n [--counter TEXT] [--unknowns TEXT] [--consequence TEXT]` | attaches what the brief needs |
| `decide DC-n --choice TEXT --rationale TEXT` | lead only |
| `decision DC-n` | the bounded decision brief |
| `decisions [--open]` | one line each |
| `reserve NAME --for A-n [--ttl MIN]` | takes a lease |
| `free NAME --why TEXT` | releases it |
| `resources` | one line per resource and its holder |
| `coverage [--requirement REF]` | requirement → assignment → candidate → observation → verification → acceptance or gap |
| `brief` | this session's bounded picture: held assignments, unresolved inbox, open decisions, stale observations |
| `entry [--role R]` | what a session of each role does first: duties, this session's state, the next steps (`coordination-roles.md`) |

`brief` is what a restarted or compacted participant reads to recover
its obligations (AC-12). Reads print `no store` and exit 0 when the
store is absent — `who` with them, since a read that refuses is a read
that claimed to have looked. Every form in this table is run against the
parser by the suite, so a row that goes stale where its verb moved is a
failure and not a reader's surprise.

## Refusals

| refusal | fires when |
|---|---|
| arguments | an unknown verb or option, a wrong positional count, a missing required option, or any attempt to supply the acting participant or role |
| identity | a writing verb without an identity |
| session | a bind or a resolve with no session id in the environment |
| participant | no participant bound to this session; a named participant id with no row; a participant argument whose session is not the calling process's |
| role | a verb whose authority the bound roles do not carry; a self-bind above `worker` after the first participant; a re-bind that would change its own roles |
| conflict | a `bind-for` for a session that already holds a participant row |
| id | an id not of the form `PT-n`, `A-n`, `M-n`, `E-n`, `DC-n` or `R-n`, or of the wrong kind for the verb |
| unknown | an id the store does not hold |
| generation | a move whose stated generation is not the assignment's |
| revision | a verify whose candidate is not the assignment's revision, an observe of a revision the assignment has moved past without `--stale`, or an observe on an assignment with no revision named |
| producer | a verify or accept by the participant that produced the candidate |
| recipient | a claim by a participant the assignment was not offered to |
| evidence | a reading with no known shape |
| deliverable | a claim whose branch the deliverable lock does not name |
| store | a read while the store exists and cannot be read |
| place, holder, taken, transition | surfaced from the engine unchanged |

The evidence shapes are the ones already pinned: a sha of 7 to 40 hex,
`#N`, a dispatch record id `YYYYMMDDTHHMMSSZ-...`, or a `P-` corpus row
(`handoff-lever.md:73-75`). Every reading a verb takes is held to them:
`submit --evidence`, `verify --evidence`, `accept --evidence`, `resolve
--evidence` and `return --findings`. `verify --candidate` is a revision
identity, not a reading of a run, which is why it is a second option and
not the same one.

## Where it sits in the session

- **Onboard** gains the `brief` section, so a session reads its
  assignments, unresolved messages and open decisions with its other
  current readings.
- **Close** requires `outstanding --to <participant>` to be empty
  before a session calls itself finished, the same duty `handoff.py
  held` already carries: an obligation this session accepted and
  neither resolved nor handed on is the hand-over failing, named.
- The handoff thread stays what it is. An assignment may name one; it
  never replaces one.

## State

`{"placement": "migrated", "state": {"engine": "sqlite"}}`. Rows in
`state.db` beside the other machines, never a file: this is state one
session changes and another must see now (`instrument-conventions.md`
§State). Test state is a testkit `World` with `XDG_STATE_HOME` pointed
at its own root, so no test touches the live host store.

The store is host-local (`machine-lever.md:42-45`). A second host is a
second store; coordination across hosts is not claimed by this
contract. If the host permits writes to `state.db` outside these
functions, this contract does not fence them — it records who wrote
what, and the marking is append-only under the engine's two triggers.

## Lint

Inherited; no per-file row. Registry row:
`ops/conductor/registry/coordination.py.json`. Tests:
`ops/conductor/tests/test_coordination.py`, on the test kit
(`docs/tests.md`). The standing rule is that a module's tests are
another producer's; this package lands under the hand grant instead,
one producer with the second producer's absence declared in the commit
and in the record (AGENTS.md §Ground rules, the dev-lane hand grant),
and independent review follows by dispatched Grok and Codex. Ruled by
the Lead session, 2026-09-16.
