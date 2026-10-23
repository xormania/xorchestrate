# Managed sessions — contract (slice 1: one managed native participant)

Standing: proposed, slice 1 of
`.xor/in/multi-agents/agentscient-dev-lane-multi-harness-integration-plan-2026-09-22.md`
(§6, §9, §11 "Slice 1", §12, §13 I-01, I-02, I-11, I-12), at the owner's
word (owner, 2026-09-22: "i created two other claude sessions you can
use - contact them - you take lead - you're CO"). Written by the Lead
(PT-20, branch lane/session-20260922-af333fa2, grant 320) as the plan
artifact of the pipeline; the tests are written against this page by a
producer that did not write it, the implementation by a third. Baseline
dev `f81be8f1`; the proof adapted from is
`/home/scientdb/projects/poc/multi-harness-proof` at `a944f31` (MIT).

## Conventions

This instrument inherits `ops/conductor/instrument-conventions.md` and
runs on the state engine (`machine-lever.md`): its rows live in
`state.db`, never in a file. Writing verbs require `WF_AGENT`; reads do
not and never create the store. It runs from the repository root with
no Symfony, no `vendor/`, stdlib only. It never edits a global harness
configuration: every native home is the private profile
`profiles.py prepare-interactive` already makes.

## What slice 1 is, and is not

In: `doctor`, `start`, `status`, `attach`, `stop`, `export`; the run and
member rows; tmux ownership; profile composition through the existing
`prepare-interactive → check → run-interactive` path unchanged; the
adapter boundary with one adapter live (`claude`, the native TUI) and
the shared framing module tested offline; `ops/bin/conduct.sh`;
`ops/conductor/team.default.json`.

Out, declared: message delivery (courier native route, `join`,
`recover`, ingress readiness, the correlated startup exchange) is slice
2; the Codex app-server and Grok ACP adapters wait on the transport
projection in `profiles.py`, whose tests live under
`ops/devlane/hooks/tests/` and so are wall work for a separate wall
session; dispatch actions are slice 3. A verb this page does not list
refuses `arguments`. The readiness dimensions slice 2 owns print
`unmeasured` here, never `ok`.

## The shape

A **run** (`R-n`) is one started team: the team file it was read from
and that file's sha256, the tmux socket and session name it owns, who
started it. A **member** (`SM-n`) is one managed native participant of
a run: its name and harness from the team file, the roles the file asks
for, its generation (1 at first launch, +1 per replacement), the
launch identity the supervisor minted, the native identity the hooks
observed, the participant `PT-n` the store bound, the pid and the pid's
start ticks, the tmux pane, the worktree, the profile directory, and
its readiness. The engine owns place, holder and marking; the tool's
own columns sit in `session_run` and `session_member`, keyed by the
engine's subject id.

The member machine, declared as data in the tool:

| transition | from | to | actor | evidence | failure | hold |
|---|---|---|---|---|---|---|
| launch | reserved | launched | starter | the pane id and pid | `place` | keep |
| bind | launched | bound | starter | the observed native id and PT-n | `native` | keep |
| ready | bound | ready | starter | the readiness reading | `readiness` | keep |
| stop | launched, bound, ready | stopped | any | the process outcome | `place` | keep |
| lose | launched, bound, ready | lost | any | what was found missing | `place` | keep |

The run machine: `open → stopping` (stop) `→ stopped` (every member
stopped or lost, evidence the member list). A run with a member still
`launched` or `bound` is not stopped.

### The team file

`ops/conductor/team.default.json` is the default; `--team FILE`
names another; `.dev/coordination/team.json` is a gitignored local
override read only when present. Schema `agentscient-team/1`:

```json
{"schema": "agentscient-team/1",
 "run": {"name": "lane", "tmux": "agentscient"},
 "members": [
   {"name": "co", "harness": "claude", "roles": ["lead"]},
   {"name": "xo", "harness": "claude", "roles": ["coordinator"]},
   {"name": "dispatcher", "harness": "codex", "roles": ["dispatcher"]},
   {"name": "grunt", "harness": "grok", "roles": ["worker"]}]}
```

`name` matches `[a-z][a-z0-9-]{0,31}` and is unique in the file;
`harness` is one of `claude`, `codex`, `grok`; `roles` is a non-empty
subset of the coordination store's `ROLES`; an optional `executable` is
an absolute path. Nothing else is accepted (`team`). A model is never
named in the file. A role in the file is a request the store answers,
never a promotion.

### Readiness, eight dimensions

Each is one of `ok <evidence>`, `waiting <what>`, `refused <class>`, or
`unmeasured <owner slice>`:

| dimension | slice 1 reading |
|---|---|
| process | `ok pid=N start=TICKS` from `/proc/N/stat`, or `waiting`/`refused` |
| profile | `ok <profile dir>` when `profiles.py check` exits 0 on it; its refusal line otherwise |
| identity | `ok <session id>` when a participant bound to the launch id appears in the coordination store; `refused native` when the store shows a participant for this member under another id, or when the member's `pane.log` carries the profile's own `native` refusal line (the profile refuses a mismatched payload before any bind) |
| participant | `ok PT-n` |
| role | `ok <roles>` when the store's roles cover the request; `waiting grant PT-n --roles <missing>` otherwise |
| tools | `unmeasured slice 2` |
| ingress | `unmeasured slice 2` |
| response | `unmeasured slice 2` |

`ready` requires the first five `ok`, except that `role` may read
`waiting` when the request exceeds `worker` and no lead granted; the
member is then `ready` and the line says what it waits on. A living
pane, an existing profile, or a participant row alone never reads
`ready`.

### Identity, without impersonation

The supervisor mints a launch identity (a uuid4) and passes it to the
harness the way that harness accepts one (`claude --session-id`). The
native identity is what the profile's `SessionStart` hook binds through
`initialize` and `instrument.py`, read back from the coordination store
with `create=False`. The two are recorded separately and compared; a
mismatch is `refused native`, never overwritten. The supervisor never
carries a participant's capability, never binds on a member's behalf,
never grants: a role above `worker` is granted by the live lead, either
the operator's own lead capability in `COORDINATION_CAPABILITY` when
`start` runs from the lead's shell, or by the lead's own
`coordination.py grant` afterwards, which `status` then observes. The
first native bind in an empty store is a worker like every other
(`initialize` binds `worker` and the core never promotes); lead comes
only from a lead's grant or an owner-authorized recovery, and the
cold-start row asserts worker and no lead. On a store that does not
exist or lacks the coordination schema, `start` creates the schema
through the core's writer and writes no participant row, because the
profile's `initialize` refuses a missing store and the first bind must
stay the profile's (slice 1; the cleaner form, `initialize` creating
the store it binds into, is a wall change filed for after slice 1). A
populated store is the ordinary case and is never wiped.

### tmux ownership

One tmux session per run, on the socket `AGENTSCIENT_TMUX_SOCKET` or
`--tmux-socket NAME` (default: the operator's own server, shared; the
tests use a private one). The session is
`<team.run.tmux>-<R-n>` with the session option `@agentscient_owner`
set to the run id, one window per member named by the member, and
`remain-on-exit on`. Option operations target the exact session
component (`=name:`); session commands use their documented target.
A session of that name without the option, or with another run's id,
refuses `ownership`; the supervisor never sets, kills or attaches it.
Never `kill-server`. Output is captured with `pipe-pane` into the
member's evidence directory.

### The adapter boundary

`ops/conductor/tools/session_adapters/` holds one module per harness
and `framing.py`, the JSON-lines framing and request correlation
adapted from the proof's `rpc.py`. An adapter is a class with these
methods and no others the supervisor calls:

| method | returns |
|---|---|
| `argv(member, binding, launch_id)` | the fixed launch argv for the profile's `run-interactive`, never operator text |
| `observe_identity(store, launch_id)` | the native id the store shows for this launch, or `None` |
| `probe(executable)` | `{capability: "ok <reading>" | "unsupported <reading>" | "unmeasured"}`, with the installed version |
| `stop(handle, terminate)` | `stopped <how>` or `unresolved <why>` |
| `diagnostics(member_dir)` | the list of evidence paths it retained |

Slice 1 ships `claude.py` (native TUI; `probe` reports the version and
the channel capability as `unmeasured slice 2`) and `framing.py`. The
framing parser refuses, with a reading that names the line: a
non-JSON line, a response whose id matches no request, two requests
with one id, a result missing its id. Codex and Grok modules are
absent: `doctor` reads `missing adapter` for each codex and grok member
of the default team and so exits 1 in slice 1, the declared absence
slice 2 closes.

## Instrument

`python3 ops/conductor/tools/sessions.py <verb>`; `ops/bin/conduct.sh
<verb> …` is the same call from the repository root.

| verb | effect |
|---|---|
| `doctor [--team FILE]` | read-only; one line per check, `ok|missing|refused <check>: <reading>`: tmux (path, version), bwrap, the state store (`absent` is a reading), the coordination store and whether a lead is bound, python, each member's harness executable with its sha256 and adapter presence; exit 0 when every line is `ok`, 1 (Finding) otherwise |
| `start --member NAME [--team FILE] [--run R-n] [--worktree PATH] [--ready-timeout SEC]` | reserves the run (new unless `--run`) and the member, creates the owned tmux window, runs `prepare-interactive`, `check`, then `run-interactive` in the pane with the adapter's argv, waits up to `--ready-timeout` (default 120) for identity and participant; prints `started <name> SM-n as PT-n in R-n pane %N gen G` and the eight readiness lines; exit 0 when `ready`, 1 when the timeout lapsed with the member `launched` (the row stays, `status` reads it), 2 on a refusal before launch |
| `status [--run R-n] [--member NAME]` | read-only; per member: `R-n <name> SM-n <place> PT-n|- gen G pane %N|- process ok|gone` then the readiness lines; liveness is pid plus start ticks, never pid alone; `no runs` when the store has none; exit 0 |
| `attach --member NAME [--run R-n]` | execs `tmux attach-session` on the run's socket at the member's window after the ownership check; refuses `absent` for no such pane, `ownership` for an unowned session |
| `stop [--run R-n] [--terminate] [--drain SEC]` | moves the run to `stopping` and records the request; without `--terminate` it waits `--drain` (default 0) for members to exit on their own and prints each member's outcome, exit 1 while any is still running; with `--terminate` it signals only a pid whose start ticks still match the record, then the owned window, and records `stopped <how>` or `unresolved <why>` per member; a stale pid is `unresolved stale pid`, never signalled; exit 0 only when every member reads `stopped` |
| `export [--run R-n] --to DIR` | writes `run.json` (the run and member rows, readiness, the team file's sha256, tmux and process identity), each member's pane capture, `launch.json` and adapter diagnostics; a binding's `coordination_capability` and every `credential_environment` value are omitted; the first failure is kept and named, exit 1 for a partial export, `exported <dir> (N members, M files)` exit 0 |

Runtime evidence lives under the state root at
`sessions/<R-n>/<member>/`: `profile/` (the private profile),
`launch.json` (pid, start ticks, pane, launch id, argv with secrets
removed), `pane.log`, `stderr.log`. It is residue and export, never
state, and the placement file says so.

## Refusals

| class | when |
|---|---|
| `identity` | a writing verb without `WF_AGENT` in full form |
| `arguments` | an unknown verb or option, a verb slice 1 does not carry |
| `team` | the team file is malformed, names an unknown harness or role, or `--member` names no member |
| `tmux` | tmux missing, or the server on the named socket cannot be started |
| `ownership` | a tmux session of the run's name without `@agentscient_owner` equal to the run id |
| `profile` | `prepare-interactive` or `check` refused; their line in the found clause |
| `worktree` | `--worktree` is not a git worktree of this repository |
| `native` | a participant bound to the launch id shows another native id |
| `readiness` | `ready` asked while a required dimension is not `ok` |
| `authority` | a role above worker on a member whose start ran with a capability that is not the lead's; the remedy names `coordination.py grant` |
| `place` | a move from the wrong place (`start` on a member already `launched`, `stop` on a stopped run) |
| `absent` | no such run, member or pane |
| `store` | the engine's own refusals, passed through |

## Tests

`ops/conductor/tests/test_sessions.py` and
`ops/conductor/tests/test_session_adapters.py` on `ops/testkit`, run by
`ops/bin/suites.sh`. The world plants a fake harness through the kit's
registry, with `script=`, that reads the `SessionStart` payload its
launcher would send, runs the profile's `guard.sh SessionStart` so the
real `initialize` binds a participant, and then blocks until its stdin
closes; a second spec variant binds a different session id (the
`native` fault). tmux tests use a private socket in the world and skip
with the reason when tmux is absent. The fixture set, each a row with
an origin of this page: tmux absent (`doctor`); malformed team file;
an unowned session of the run's name; native id mismatch; role above
worker with no lead capability; the timeout with no bind; a stale pid
on `stop --terminate` (a pid the world reaped and reused); an
unreadable diagnostics file on `export`; a member already launched;
each framing fault. Clean neighbours: cold start on an empty store,
start on a populated store with a lead already bound, a second member
of the same harness with distinct profile, identity and pane, attach,
a clean stop, a full export. No live harness runs in the suite; the
live trial (plan §13 steps 1 and 7, I-01, I-02, I-11, I-12) is run by
hand and its readings go to probe rows under
`ops/conductor/handoff/probes/`.

## Placement and provenance

`ops/conductor/placement/sessions.py.json` declares
`"state": {"engine": "sqlite", "files": "profiles, pane captures and exports under the state root: residue and export, never state"}`;
`session_adapters/` needs no placement (no state). The commits that
adapt `Tmux`, `rpc.py` and the capture shapes carry
`Source: /home/scientdb/projects/poc/multi-harness-proof@a944f31`, and
`session_adapters/SOURCES.md` lists each adapted function beside its
origin. The proof's test-suite termination policy and its
`queue.Queue` are not adapted.

## Where it sits in the session

`doctor` first, then `start --member <name>`; `attach` shows the real
process; `status` is the board line; `stop` before the terminal is
closed, `export` before a claim about the run. Slice 2 adds the
courier route on the same rows; nothing here is rewritten for it.

## Slice 2 — persistent native conversation

Standing: proposed, slice 2 of the plan (§11 "Slice 2"; the mechanics
are §7 the courier and its traps, §9 stop and recovery, §12 the cases).
Written by the contract session (agentscient-87, Claude Fable 5.1, grant
335) against slice 1's text at 11d77a56 and the file split
`ops/conductor/handoff/2026-09-23-sessions-slices-2-3-file-ownership.md`
(cc8b5f5f). This section holds what the tests need: the shapes, the
verbs, the refusals and the fixture set. Everything else is the plan's,
cited, not restated. Nothing above this heading is rewritten.

**Exit** (plan §11): participants on different harnesses exchange a
request and a response in their existing sessions while work stays open;
a second member on the same harness routes correctly; a restart or a
timeout neither erases the obligation nor replays its effects.

### Owners

One owner per file (the split). S2 owns `courier.py`, `sessions.py`,
`session_adapters/` (`framing.py`, `claude.py`, `__init__.py`, and new
`codex.py`, `grok.py`, `channel.py`), the additive tables and verbs in
`coordination.py`, and `coord_send`'s `key` in `coordination_mcp.py`.
The wall (W, `matrix.py WALL_PATHS`) owns `profiles.py`, `native.py`,
`enforcement.py` and `launch.py`; what this slice needs from them is in
"Required of the wall" below, and until W lands the rows run against a
planted fake of that surface, never the real one. The test world uses
the source lane's name (`profiles.py:151` pins `LANE_CONF`).

### The shapes

**A route** is how queued messages reach one participant. Two routes:
`stdout`, slice 1's courier armed as a Monitor inside a Claude TUI
session (`courier.py watch`, unchanged in shape); `native`, the bridge
below submitting through the member's protocol channel. One live route
per participant, in `coordination.py`'s store:

    delivery_routes(participant INTEGER PRIMARY KEY, route TEXT NOT NULL,
                    member INTEGER, pid INTEGER NOT NULL,
                    start_ticks TEXT NOT NULL, since TEXT NOT NULL,
                    last_pass_at TEXT)

A courier takes the row under the write lock with its own pid and start
ticks. A second courier of either route for a participant whose holder
is alive (pid plus ticks; a missing tick is never alive) refuses `route`
naming the holder; a dead holder's row is taken over and the takeover is
the new row's `since`. A printed receipt from the stdout route never
suppresses a native delivery, because the two cannot hold one
participant at once (plan §7, competing couriers).

**A delivery attempt** (`AT-n`) is one submission of one message to one
concrete participant at one generation:

    delivery_attempts(id INTEGER PRIMARY KEY AUTOINCREMENT,
                      message INTEGER NOT NULL, participant INTEGER NOT NULL,
                      generation INTEGER NOT NULL, route TEXT NOT NULL,
                      reserved_at TEXT NOT NULL, submitted_at TEXT,
                      outcome TEXT, native_ref TEXT, error TEXT)

Reserved before the submission, settled after it. `outcome` is one of
`written` (the request left, no response yet: grok's acceptance),
`accepted <native ref>` (the native response: codex's turn id),
`unsupported <method>`, `failed <error>`, `uncertain <why>`, `accepted
reconciled`. A NULL outcome older than `ATTEMPT_TTL` (60 s) is a crash
between the two writes: the next pass asks the adapter's `observe` for
the message in the native session's evidence; found reads `accepted
reconciled`, not found reads `uncertain` and one new attempt is reserved.
Nothing is resubmitted while an attempt is unresolved. At most
`ATTEMPTS_OUTSTANDING` (4) attempts per participant are reserved and
unsettled at once. The core's `deliver` is recorded once, on the first
`written` or `accepted` outcome, with `delivery_ref` `native:AT-n:<ref>`;
`acked_at` stays the recipient's own (`coord_ack`), never the bridge's.
A role-addressed message is carried only by the courier of the run's
single live holder of that role; with two live holders it is carried by
neither, and `status` prints `ambiguous recipient <role>: PT-a, PT-b`
for it (declared gap: no fixture in this slice). The attempt binds the
concrete `PT-n`; the message row's `recipient` stays as sent.

**A send request key** makes `send` idempotent per caller:

    send_requests(participant INTEGER NOT NULL, key TEXT NOT NULL,
                  digest TEXT NOT NULL, message INTEGER NOT NULL,
                  at TEXT NOT NULL, PRIMARY KEY (participant, key))

`digest` is sha256 over the canonical JSON of `to, purpose, body,
assignment, decision, reply_to, artifact`. The same key with the same
digest returns the existing message (`message M-n queued (repeated)`);
the same key with another digest refuses `idempotency` and writes
nothing; no key is slice 1's behaviour. Slice 3's event routing relies
on this key.

The three tables are additive `CREATE TABLE IF NOT EXISTS` rows in the
core's schema; a store populated by the current schema opens with its
rows intact.

**The bridge** is a codex or grok member's own process: `sessions.py
bridge --run R-n --member NAME`, launched by `start` in the member's
pane under the profile's authority environment, in place of the harness
itself. It spawns the harness with pipes through `profiles.py
run-interactive --binding B -- <channel argv>`, speaks the protocol
through `framing.Parser` (a reader thread feeds the parser; one writer
lock; no SQLite transaction is ever held across a native call; a
response whose id matches no request this side sent is dropped and
written to `channel.log` with its id, never fatal, measured on grok
1.0.40's mid-turn `skills-reload` response at 8191d6a1, while every
other framing refusal ends the channel), takes
the native id from the handshake, runs the profile's `guard.sh
SessionStart` with that id so `initialize` binds the participant under
the profile's own capability, then loops: lines on its stdin are
operator text and go to `submit`; every `tick` it carries its
participant's undelivered messages through the native route; native
output goes to the pane, so `pane.log` holds it. The supervisor still
binds nothing and carries no capability: the bridge is the member. Its
pid is the member's pid, so liveness, `stop` and `export` are slice 1's.
A delivery error never exits the bridge (plan §9): it records the
outcome, backs off to `BACKOFF` (2 s, doubling to 60 s) and reports the
degradation on its readiness. A Claude member has no bridge: slice 1's
launch stands and its route is `stdout`.

**Unix socket paths**: every socket path this slice or the tests bind
(the tmux server's under `--tmux-socket`, and the world's) is under 108
bytes, or the verb refuses `socket` naming the length before tmux is
called. Measured on 2026-09-22 under a long `TMPDIR`.

### The adapter boundary, extended

Every adapter carries slice 1's five methods, these five, and
`connect(argv, cwd=, stderr=)`, which opens the channel and returns
`rpc` (measured 2026-09-23 at fed6a4ce: `do_bridge` calls it once);
the supervisor and the bridge call no others. Helpers an adapter
keeps beyond them (codex and grok's `reconcile`, `answer`,
`refuse_channel`, `other_native`, `settled`, reached only inside
`observe`) are adapter-private and no finding. The claude adapter's
`handshake`, `submit` and `observe` refuse `transport`:

| method | returns |
|---|---|
| `transport()` | `tui` (claude) or `protocol` (codex, grok) |
| `channel(member, binding)` | protocol: the argv after `run-interactive --binding B --`: codex `app-server --listen stdio://`, grok `--no-auto-update agent stdio`; tui: `None` |
| `handshake(rpc)` | the native id: codex `initialize`, `initialized`, `thread/start` → `thread.id`; grok `initialize` (protocolVersion 1), `authenticate` when advertised, `session/new` → `sessionId`; refuses `channel` naming the method that failed |
| `submit(rpc, text, *, busy)` | codex `turn/start` with `toolOutput` into the active turn when busy, else a new turn → `accepted turn <id>`; grok `_x.ai/interject` when busy, `session/prompt` when idle → `written`; a -32601 reply → `unsupported <method>` |
| `observe(rpc, frame)` | one received frame classified: `native <id>` (from `threadId` or `sessionId`; a frame naming another id is `refused native <id>`, the foreign id kept so the pane says which conversation was refused), `activity active|idle`, `output <text>`, `tool <name>`, `permission <title>` (answered `decline`, the prompt printed, with one exception: a grok `use_tool` call whose `rawInput.tool_name` is exactly `coordination__<tool>` with an input object is answered `allow-once`, read `permission coordination <tool> allowed`, because the coordination server is the lane's own and a member that can never call `coord_ack` or `coord_send` can never read `ok` on its response dimension; measured live by agentscient-b3 on grok 1.0.40, `session/request_permission` for `coordination__coord_probe` with options always-allow, allow-once, reject-once, the bridge's reject-once reading the call `failed`; everything else, `always-allow` included, stays declined; pre-approving the server in the profile's config is the wall's later shape, not tonight's), `done <turn>` |

`rpc` is `session_adapters/channel.py`: the pipes, the reader thread,
the parser and the writer lock, adapted from the proof's `RPC`
(`SOURCES.md` gains the row). The claude adapter's `probe` measures the
channel capability it left `unmeasured` in slice 1 and reads
`unsupported channel` on the installed version, which is not a refusal.

### Readiness, the three slice 2 dimensions

| dimension | reading |
|---|---|
| tools | `ok <evidence>` when the native session shows the `coordination` acting server loaded: codex and grok, the adapter's `tool` or discovery frame naming it; claude, the profile home's debug log line for the server's connection, which reads `unmeasured no debug log` until the profile admits `--debug-file` (W's surface, not yet on the tree). `waiting tools` until then, never `ok` from the binding's server list alone |
| ingress | `ok <route> pid=N` when a live `delivery_routes` row exists for the participant; `waiting courier` otherwise; `refused channel` when the bridge's last attempt read `unsupported`; `unmeasured the profile arms no courier` when no `*.json` in the member's profile names `courier.py watch`, so no route can exist by construction |
| response | `ok M-n <latency>` when the member acked the readiness probe: when `start` runs from a bound shell, after `bound` it sends `inform` "readiness probe R-n SM-n <nonce>" to `PT-n` and waits for the recipient's own ack; `waiting ack M-n` until then; `unmeasured no starter participant` when the starter is unbound |

`ready` requires slice 1's five `ok` (with slice 1's rule that `role`
may read `waiting grant`) and each of the three slice 2 dimensions
`ok`. A member whose eight lines are measured and whose tools, ingress
or response reads `unmeasured <reason>` reaches `admitted`, a place of
its own in LIVE: `start` exits 0 on it and its line reads
`admitted, unverified: <the unmeasured dimensions>`. `waiting` or
`refused` on any of the three refuses `readiness`, naming the
dimension. An
`unmeasured` reading is a declared absence, never a pass in disguise:
the line carries it. `admitted` is never `ready`: no consumer of the
place, the run's `status`, `send`, `coord_launch` or a trial record,
reads an admitted member as verified readiness (owner, 2026-09-23: "A session
admitted with unmeasured tools, ingress, or response must not be
presented or consumed as verified operational readiness"; this
withdraws the 2026-09-23 slice 2 ruling that admitted `unmeasured` into
`ready`). The readiness probe is an `inform`, so it leaves no
obligation.

### Verbs added

| verb | effect |
|---|---|
| `bridge --run R-n --member NAME` | the member's own process, above; launched by `start`, runnable by hand for a reserved member; refuses `transport` for a claude member |
| `join --run R-n --member NAME --participant PT-n` | records a participant the supervisor did not launch (the owner's own terminal, a session started before the run) as a member at `bound`, generation 1, no pid, `native_id` the participant's session id; `status` reads its process `unmanaged`; `stop` prints `unresolved unmanaged` and signals nothing |
| `recover [--run R-n]` | the observer restart (plan §9): re-reads every live member's pid and ticks, pane and route row; a gone process moves `lose-<place>` with what was found; a live one is left alone; prints one line per member; exit 0 when every live member was found alive, 1 otherwise; never launches. A replacement is `start --member NAME --run R-n` after the loss, which mints the next generation and keeps the old row |
| `input --run R-n --member NAME TEXT` | operator text to a protocol member: `send-keys -l` and `Enter` to the owned window, which is the bridge's stdin; refuses `transport` for a claude member (type in the pane) |
| `status` | adds, per member, the route line `route <stdout|native> pid=N since <stamp>` or `route none`, and the attempts line `attempts reserved N written N accepted N uncertain N` |
| `stop` | as slice 1, judging `absent`, `place` and `ownership` before its first move (A-481 #1), and when the starter is a bound participant sending each live member `inform` "stop requested R-n" before the drain |

`courier.py` gains `--route stdout|native`; `native` is the bridge's
in-process call (`carry(module, submit=...)`), never a second process.
`coordination.py` gains `route --take stdout|native --member SM-n`,
`route --release`, `attempt M-n --route R --generation G`, `attempted
AT-n --outcome O [--ref REF] [--error TEXT]`, `attempts M-n`, `routes`,
and `send --key K`; `coord_send` gains optional `key`.

### Refusals added

| class | when |
|---|---|
| `route` | a courier taking a route while a live holder of either route has the participant; found names the holder's route and pid |
| `idempotency` | `send --key` reused with a different digest; found names the existing M-n |
| `transport` | a protocol verb on a tui member or the reverse |
| `channel` | the handshake or a submission failed on the protocol channel; found is the adapter's reading with the method |
| `socket` | a unix socket path of 108 bytes or more; found is the length |
| `unmanaged` | a verb that needs a pid on a joined member |

### Required of the wall

Stated as interface requirements; W lands them from one brief written
from this section and slice 3's: (1) `profiles.py run-interactive`
admits the codex `app-server --listen stdio://` and grok `agent stdio`
argv shapes (T-486); (2) the profile's `guard.sh SessionStart` accepts
the bridge's payload naming a codex thread id or a grok session id, and
`initialize` binds it as slice 1 binds the claude launch id; (3) the
interactive profile registers the `coordination` acting server for all
three harnesses, and its start line names `courier.py watch` so a Claude
member arms its route; (4) two members of one harness compose two
profiles with nothing shared (slice 1's `WITHHELD` keys stay withheld).

### Tests

`ops/conductor/tests/test_session_messages.py` (sessions.py: bridge,
join, recover, input, readiness, stop), `test_courier_native.py`
(courier routes, attempts, the send key, the additive schema),
`test_session_adapters_codex.py` and `test_session_adapters_grok.py`,
all on `ops/testkit`, run by `ops/bin/suites.sh`. New fakes through the
registry, no program in a test module (`docs/tests.md` rule 6, A-481
#7): `fakes/session_codex.py` speaks the app-server shape above and
emits `turn/started`, `item/agentMessage/delta`, `turn/completed` with
its `threadId`; `fakes/session_grok.py` speaks the ACP shape and emits
`session/update` and `_x.ai/session/prompt_complete`; each logs one JSON
line per request it receives; each takes a spec that says which native
id to answer, whether to answer -32601 to a method, and whether to die
after a given request. Slice 1's fakes are S2's to extend and S3 does
not touch them.

The fixture set, the practical failures on the exit path (owner,
2026-09-23: "practical issues, not theoretical"), each a row with an
origin of this section:

1. the cross-harness exchange: a claude member and a codex member; a
   `request` from the claude participant is carried by the codex bridge
   (one `AT-n` reserved then `accepted turn <id>`, `delivered_at` set with
   the `native:` ref, `acked_at` NULL until the fake acks as its own
   participant); the codex reply is carried to the claude world by the
   stdout courier and printed;
2. two codex members: a message to the second is written on the second
   fake's channel only (the fakes' logs);
3. `send --key` twice with one body → one `M-n`; a third with another
   body → `idempotency`, no row;
4. crash after submission: an attempt with NULL outcome past the ttl and
   the fake's log showing the message → `accepted reconciled`, no second
   `turn/start`; the log without it → `uncertain` and one new attempt;
5. competing couriers: a live planted holder (the world owns it) on the
   stdout route → the bridge refuses `route` naming its pid; the holder
   reaped → the row taken over;
6. -32601 to `turn/start` → `unsupported turn/start`, the message still
   undelivered, ingress `refused channel`, the bridge alive after the
   backoff;
7. `recover` with the bridge alive → nothing moved, no launch; with the
   bridge's pid reaped and reused by a planted child → `lost` with the
   reading, the child not signalled; `start` again → generation 2, the
   old row kept;
8. `stop` on a stopped run → exit 2 and the marking row count unchanged;
9. a `--tmux-socket` path of 108 bytes → `socket` before any tmux call
   (the fake tmux log empty);
10. `join` a bound participant → `bound`, generation 1, no pid; `stop
    --terminate` → `unresolved unmanaged`, nothing signalled;
11. the readiness probe: a fake that acks → `response ok M-n`; a fake
    that never acks → `waiting ack M-n` and `start` exits 1 at the
    timeout with the row at `bound`;
12. the additive schema on a populated store: the rows before are the
    rows after.

Clean neighbours: a codex member reading all eight `ok`; a grok member
the same through `session/prompt`; an `inform` submitted while the fake
is busy (`toolOutput` into the active turn, `_x.ai/interject`); `input`
reaching the bridge's stdin (the fake's log shows the operator text).
No live harness runs in the suite; the live exchange is plan §13 step 3
by hand, its readings to probe rows.

## Slice 3 — Dispatcher integration

Standing: proposed, slice 3 of the plan (§11 "Slice 3"; the mechanics
are §8 the launch transaction, correlation, the scope cap and the
event follow-through, §9 stop and recovery, §12 the cases). Written by
the contract session (agentscient-87, Claude Fable 5.1, grant 335)
against slice 2 above at b9bc1444, `coordination-mcp-lever.md` slice
two (the four verbs' names and their standalone entry), and the file
split at cc8b5f5f. As slice 2: the shapes, the verbs, the refusals and
the fixture set; the plan for the rest.

**Exit** (plan §11): a participant asks the Dispatcher for a job by
message or by MCP verb and gets one record id back, at most once per
request; the launch's outcome reaches the requester as a message with
the record id, once, in order, and a Dispatcher restart between the
ask and the launch neither loses the ask nor launches it twice.

### Owners

S3 owns three new modules under `ops/conductor/tools/`:
`dispatch_intents.py` (the table and its verbs, through the core's
`writer()`; it edits `coordination.py` nowhere), `dispatch_actions.py`
(the four verbs on the CLI), `dispatch_mcp.py` (the same four as MCP
tools, standalone entry), and `dispatch_events.py` (the follow-through);
`dispatch.sh` under `ops/conductor/tools/` (not wall); tests
`test_dispatch_actions.py`, `test_dispatch_mcp.py`,
`test_dispatch_events.py` and `fakes/dispatch_*`, never `fakes/session_*`.
The one registration line that adds the four tools to `CATALOGUE` in
`coordination_mcp.py` is an integration commit by S2's owner after both
land. The wall (W) owns `launch.py`; what this slice needs from it is in
"Required of the wall". Until W lands, the rows run against a planted
fake `dispatch.sh` and a planted job directory, never the real launcher.
Cancellation of a running job is declared out of this slice: `close`
and `--terminate` stay slice 1's and `launch.py`'s.

### The shapes

**A dispatch intent** (`DI-n`) is the ask persisted before anything is
launched, in the core's store:

    dispatch_intents(id INTEGER PRIMARY KEY AUTOINCREMENT,
                     key TEXT NOT NULL, digest TEXT NOT NULL,
                     requester INTEGER NOT NULL, dispatcher INTEGER NOT NULL,
                     assignment INTEGER NOT NULL, generation INTEGER NOT NULL,
                     job TEXT NOT NULL, harness TEXT NOT NULL,
                     branch TEXT NOT NULL, scope_ref TEXT NOT NULL,
                     inputs TEXT NOT NULL, correlation TEXT NOT NULL UNIQUE,
                     phase TEXT NOT NULL, record TEXT, launcher_pid INTEGER,
                     launcher_ticks TEXT, reserved_at TEXT NOT NULL,
                     launched_at TEXT, closed_at TEXT, outcome TEXT, error TEXT,
                     reported_at TEXT,
                     UNIQUE (requester, key))

`reported_at` is written once the router pass's sends for the intent
commit, in that transaction; a pass watches every `launched` intent and
every `closed` intent whose `reported_at` is NULL, so a job collected
while the router was down, restarting or inside one tick still reaches
its requester once (the reviewer agentscient-0e's F1 at d0cbc6b0:
monitors built from `launched` alone, `collect` moving the row to
`closed` with nothing sent; fixed at eec4eddc). A store from before the
column gets it at the next writer, with already-closed intents marked
`before reporting`, so an upgrade sends nothing old again.

`digest` is sha256 over the canonical JSON of `assignment, job, harness,
branch, scope_ref, inputs`. `correlation` is a uuid4 minted at the
reservation and passed to the launcher; it is the only join between the
intent and the job directory. `phase` is the machine `dispatch-intent`
(engine, ttl 12 h): `reserved` → `launching` → `launched` → `closed`;
`launching` → `uncertain` when the launcher exits without a `record:`
line or dies (pid plus ticks; a missing tick is never alive);
`uncertain` → `launched` on reconciliation (`reconcile`) or → `refused`
(`refuse`) with the wrapper's refusal line as `error`; `reserved` →
`refused` when validation fails after the row (never before it, so a
refused ask is still a record). Transitions: `launch` (actor
dispatcher), `mint` (dispatcher, evidence the record id), `close`
(dispatcher, evidence `launch.py`'s closed reading), `lose`
(dispatcher, evidence the pid reading), `reconcile` (dispatcher,
evidence the `state.json` path), `refuse-reserved`, `refuse-launching`
and `refuse-uncertain` (dispatcher, evidence the line; three
transitions because the engine allows one source per transition; the
verbs and the tests call them `refuse`). The launcher is the holder's own child: `launcher_pid` and
`launcher_ticks` are its, read at spawn.

**The launch, in order** (plan §8, the transaction boundary): the
holder validates the ask; reserves the intent; commits; spawns
`dispatch.sh --job J --harness H --branch B --scope-file <ref file>
--input <assignment file> --for A-n --agent <ident> --detach
--correlation <uuid>` with the intent's `--timeout` from `jobs.json`;
moves `launching`; reads the wrapper's `record: <id>` and
`launcher-pid:` lines; moves `launched` with the id; then answers. The
scope file is a reference, never the assignment truncated (plan §8, the
cap): one line naming `A-n`, the job, the contract path and ref, and
the input file; the assignment's full text rides as `--input`, so
`SCOPE_CAP` is never the reason an ask fails. A second ask with the same
`(requester, key)` and digest returns the existing `DI-n` and its phase
as `repeated`; with another digest refuses `idempotency`. The store is
never held across the spawn.

**Reconciliation**: an intent at `launching` whose launcher is gone
moves `uncertain`; the holder then scans the jobs root (`$DISPATCH_JOBS`
or `$XDG_STATE_HOME/<lane>/dispatch`) for a launcher log whose first
line is `correlation: <uuid>` matching the intent's, `<id>/launcher.log`
beside a `state.json` once the wrapper minted, or `.launcher.*` in the
jobs root when the wrapper died before the mint; found beside a
`state.json` moves `launched` with that record id;
absent leaves `uncertain` and prints it, and the ask is never relaunched
by the holder: the requester asks again with a new key or the owner
launches by hand. Restart is `dispatch_actions.py recover`: every
`launching` row reconciled, every `launched` row read through
`launch.py status`; nothing launched.

**Capacity**: at most `DISPATCH_CAPACITY` (env, default 6) intents at
`launching` or `launched` with `launch.py` reading `running`; the next
ask refuses `capacity` naming the count, and is not reserved.

**Holder**: the four verbs act only for the participant whose role is
`dispatcher` and who holds the `dispatcher` machine (`dispatcher.py`,
holder_note the session id); a caller without the role refuses `role`,
a dispatcher who is not the holder refuses `holder` with the remedy
`dispatcher.py set`. Only the Dispatcher can call `launch`, so the
caller is always the Dispatcher; the requester is the holder of the
`--for A-n` assignment (XO ruling, 2026-09-23), and both are on the
intent.

**The programs it runs** (agentscient-10's five points, the Lead's
ruling 2026-09-23), each an argv with no shell, each named by an
environment key so a test plants a fake in its place:
`DISPATCH_WRAPPER`, default `ops/conductor/tools/dispatch.sh`
(`fakes/dispatch_wrapper.py` stands in); `DISPATCH_LAUNCHER`, default
`ops/devlane/dispatch/launch.py` (`fakes/dispatch_launch.py`);
`DISPATCH_MONITOR`, default `ops/conductor/tools/monitor.py`
(`fakes/dispatch_digest.py`). A value that is not an executable file
refuses `launcher` for the wrapper and the launcher, and `store` for
the monitor, before any row is written. `DISPATCH_CAPACITY` defaults
to 6. The jobs root is `$DISPATCH_JOBS`, else
`$XDG_STATE_HOME/<lane>/dispatch`. Each intent's scope file is written
at `<state dir>/dispatch-intents/DI-n/scope.txt` and that path is what
`--scope-file` carries.

**Verification names**: `collect --verify` takes a name keyed in the
tracked table `ops/conductor/verifications.json` (`{name: [argv...]}`,
read from the repository root, run from it); a caller's text never reaches
`launch.py collect --verify`, and an unknown name refuses `verify`.

### Verbs

`ops/conductor/tools/dispatch_actions.py`, with slice 1's conventions
(identity for writes, the wire):

| verb | effect |
|---|---|
| `launch --for A-n --job J --harness H --branch B --scope-ref TEXT --input PATH --key K` | the launch above; prints `intent DI-n launched <record id>`; exit 0. `harness` is one of the worker harnesses in `lane.conf`'s model keys; `job` a key of `jobs.json`; `B` the assignment's branch; `A-n` open at the caller's generation |
| `status DI-n` | `intent DI-n <phase> record <id|none> job <unlaunched|running|finished|tripped|DIED> closed <reading|none>`; reads only; no identity |
| `wait DI-n [--timeout S] [--interval S]` | blocks until `launch.py wait` reads clean, not clean or the timeout; exit 0 clean, 1 not clean or timeout (a Finding, printed as `wait DI-n timeout after S`), never 2 for the timeout |
| `collect DI-n --verify NAME` | `launch.py collect <record> --verify <argv from verifications.json>`; moves `close` with the reading; prints it |
| `recover` | the restart above; one line per row touched; exit 0 |
| `reconcile DI-n` | the scan above for one row |

`ops/conductor/tools/dispatch_mcp.py` serves the same four (`launch`,
`status`, `wait`, `collect`) as `coord_launch`, `coord_status`,
`coord_wait`, `coord_collect` under `coordination-mcp-lever.md`'s tool
shape (`Tool(name, verb, mutates, ...)`): `coord_launch` and
`coord_collect` mutate, the others do not; the MCP entry binds the
caller as `coordination_mcp.py` does and applies the same `role` and
`holder` checks; the standalone entry (`python3 dispatch_mcp.py serve`)
runs without the registration line, which is how the tests reach it.

`ops/conductor/tools/dispatch_events.py route --consumer dispatch
[--once] [--tick N]` is the follow-through (plan §8): it reads
`monitor.py digest --consumer dispatch`, maps each event to the intent
by the record id, and sends by the dispatch metric's own words
(`monitor.py`, measured by agentscient-10 on 2026-09-23): `completion`
and `no-op` → `inform` to the requester with the record id and the
exit; `invalid` and `death` → `finding` to the Dispatcher and to the
requester; `settling`, `running`, `unknown` and any other word → acked,
never sent. The dispatch adapter reads a static id list (`params.ids`,
at most 256, `monitor.py:129-137`), so each pass first writes a config
under the state directory naming the launched intents' record ids,
runs `monitor.py run --once` over it, then `digest`; there is no
tracked `monitor.dispatch.json`. Each send is `send --key event:<event id>:<recipient>`
(slice 2's key), so a replay of the digest sends nothing twice; the
cursor is acked (`digest --ack`) only after every send of the batch is
committed, so a failed send leaves the batch to the next pass. Events
for a record with no intent are logged and acked, never sent.

**The per-intent pass config** (XO ruling, 2026-09-23, withdrawing the
seed reading written at 1df9fbf0, which rested on a false reading): the
per-pass config names one monitor, one condition and one route per
launched intent (`di-<n>`), never one shared `ids` list, because a
shared list reset every baseline and re-sent old completions
(agentscient-10's fix at 1d865da7). The monitor's first reading of a
record emits its status ({"observations": 1, "events": 1}, measured by
agentscient-10 on the real `monitor.py` at 8bf3d710; the earlier
"events 0" came from a record with no `record.json`), so a job that
finished before the router's first pass is reported by that first
event, and a seed read would have sent it twice under two keys. There
is no seed. Two rows for the test fix pass (agentscient-5c): an early
finish gives exactly one inform; a later launch, added to the pass
config beside it, repeats nothing for the first.

### Refusals

| class | when |
|---|---|
| `role` | the caller is not a `dispatcher` participant |
| `holder` | the caller does not hold the `dispatcher` machine; needed `dispatcher.py set` |
| `generation` | `--for A-n` is not open at the caller's generation |
| `job` | `--job` is not a key of `jobs.json` |
| `harness` | `--harness` is not a worker harness of `lane.conf` |
| `idempotency` | the same `(requester, key)` with another digest; found names the `DI-n` |
| `capacity` | the cap above; found is the count |
| `launcher` | the wrapper refused; the intent is `refused`; found is the wrapper's own line verbatim (`stale-base`, `scope-cap`, …) |
| `uncertain` | `wait` or `collect` on an intent at `uncertain`; needed `reconcile` |
| `unknown` | `DI-n` not in the store |
| `verify` | `--verify` not a key of `verifications.json` |
| `correlation` | two launcher logs carry one correlation |
| `store` | the core's store absent or unwritable |

### Required of the wall

None. The first shape, `launch.py` admitting `--correlation` and
writing it into `state.json` at mint, was withdrawn on 2026-09-23 when
the reviewers A-524 and A-525 measured a real launch dying in
`launch.py`'s argparse on the flag the fake alone admitted (records
20260923T034128Z-review-grok-4461aa #1, 20260923T040654Z-review-codex-8649a7
#1). `launch.py` is wall and is not touched: `dispatch.sh` (S3's)
keeps `--correlation`, passes it to no launcher, and writes
`correlation: <uuid>` as the first line of the detached launch log,
which moves to `<jobs root>/<id>/launcher.log` at mint or stays as
`.launcher.*` in the jobs root when the wrapper dies first;
`dispatch_intents.scan` reads it beside `state.json`, one entry per
record, and the facade reads the launcher's `prog: error:` line as a
refusal of class `launcher` (built at 1c31eb58).

### Tests

`ops/conductor/tests/test_dispatch_actions.py` (launch, status, wait,
collect, recover, reconcile, the refusals), `test_dispatch_mcp.py`
(the four tools over the standalone entry) and
`test_dispatch_events.py` (the follow-through), all on `ops/testkit`.
Fakes through the registry, no program in a test module (rule 6):
`fakes/dispatch_wrapper.py` stands in for `dispatch.sh`: logs its argv as
one JSON line, and per its spec prints `record: <id>` and
`launcher-pid: <pid>` and writes `<jobs root>/<id>/state.json` with the
correlation from its argv, or refuses with a given line, or exits
without a `record:` line, or writes `state.json` then dies;
`fakes/dispatch_launch.py` stands in for `launch.py` (`status`, `wait`,
`collect` readings by spec, argv logged); `fakes/dispatch_digest.py`
stands in for `monitor.py` (`run --once` takes the per-pass config and
logs its ids; `digest` answers events by spec; `--ack` calls logged).

The fixture set, the practical failures on the exit path, each a row
with an origin of this section:

1. the launch: a bound dispatcher holding the machine → one intent, the
   fake wrapper's argv carrying `--for A-n`, `--detach`,
   `--correlation <the row's uuid>` and a `--scope-file` of one line;
   `launched` with the record id; `status` reads it;
2. the same key twice → one row, `repeated`; another digest under the
   key → `idempotency`, no row;
3. the wrapper exits without a `record:` line → `uncertain`; with a
   planted `launcher.log` opening `correlation: <uuid>` beside a
   `state.json`, `reconcile` →
   `launched`; without it → still `uncertain` and a second `launch` with
   the same key is `repeated`, never a second wrapper call (the log has
   one line);
4. the wrapper refuses `stale-base` → `launcher` with that line
   verbatim, the intent `refused`;
5. a dispatcher who is not the holder → `holder` before any row; a
   worker → `role`;
6. the seventh ask at the cap → `capacity`, not reserved;
7. `wait` on a fake reading not clean → exit 1 and the reading; on the
   timeout → exit 1 with `timeout`, not 2;
8. `collect --verify tests` (a key) → the fake `launch.py` argv carries
   the table's argv, and `closed`; `--verify 'rm -rf'` → `verify`, no
   call;
9. the follow-through: a digest of `completion` → one `inform` to the
   assignment's holder with the record id, and the per-pass config the
   fake received names that intent's record id; `death` → a `finding`
   to the Dispatcher and the holder; `running` → acked, no message; the
   same digest replayed → no second message; a send that fails (the store locked by the world) → the
   cursor not acked;
10. `recover` after a launcher death with `state.json` present → the row
    `launched`, nothing launched (the wrapper log unchanged);
11. the MCP entry: `coord_launch` over the standalone server with a bound
    dispatcher → the same row as the CLI; without the role → the
    server's `error` result carrying `role`.

Clean neighbours: a launch with `--input` past `SCOPE_CAP` in size
(the cap binds the scope file only); `status` by a worker (reads need
no identity); an event for a record with no intent (acked, no send).
No live launcher runs in the suite; the live ask is plan §13 step 4 by
hand, its readings to probe rows.

## Slice 4 — operational trial and closure

Standing: proposed, from plan §11 "Slice 4" and §13 "Live trial and final
acceptance" (`.xor/in/multi-agents/agentscient-dev-lane-multi-harness-integration-plan-2026-09-22.md`),
written 2026-09-23 at the Lead's order so slice 4 starts the minute
slice 3 lands. Slice 4 adds no machinery: it runs what slices 1 to 3
built on one bounded real deliverable, with independent verification,
and closes the integration with evidence by acceptance id. Where a
slice 1 to 3 clause already covers a row, this section cites it and
adds nothing.

### The trial deliverable

The trial is a real defect with a fix that runs through the whole path
(plan §13: "one already-authorized small deliverable ... actual
repository tools and an artifact/candidate"):

| order | deliverable | why it qualifies |
|---|---|---|
| trial | LD-123: `coordination.py` `offer` and `reassign` refuse an assignment already `offered`, so a coordinator cannot re-target work offered to the wrong participant (met live on A-479, 2026-09-22; ruled by the Lead: a coordinator may re-target an offered assignment, the prior offer kept in the marking) | one file, one verb pair, a planted fault the fixture set already knows (an `offered` row), a fix a dispatched worker can carry and a verifier can examine against the contract |
| fallback | LD-117: the amend-published row in `unsafe-command.py` judges the cwd's HEAD, never the repository named by `git -C` | taken only when LD-123 is refused at its start (its file under another slice's open edit, or the store refusing the planted row); a hook row is wall, so the fallback's fix is a wall session's and the trial then exercises the wall boundary instead of the coordination store |

The XO names which one at the trial's start, in the run's objective
record, with the reading that decided it. The trial never carries both.

### Owners

| path | owner | why |
|---|---|---|
| `ops/conductor/tools/onboard.py`, `ops/conductor/onboard-lever.md`, role guidance under `ops/conductor/`, `.claude/skills/*` | S4 | the one documented start path (I-01) and what a new session reads |
| `ops/conductor/handoff/runbook-managed-sessions.md` (new) | S4 | the short runbook: start, inspect, stop, resume, disable; it says in its first lines that `sessions start` is the one guarded start and that a bare `codex`, `grok` or `claude` launched by hand takes no profile (the 2026-09-19 debrief, `.xor/out/codex/fuckup.md` :346, left that unestablished and a reader expected otherwise) |
| `ops/conductor/handoff/<date>-slice4-trial.md` (new) | S4 | the trial record: the acceptance matrix with evidence by id |
| the trial deliverable's file (`coordination.py` for LD-123) | the dispatched worker of the trial job, never a management seat | the candidate is produced by the path under test |
| the independent verifier's record | the verifier session, never the builder or the XO | I-10 and I-14 need a producer who wrote none of the candidate |
| the thin onboard wrapper inside a codex or grok member's private native home (`profiles.py` `prepare_interactive`, the profile's skills directory) | the wall (a wall session, one PR, a planted row red then green), never S4 | the one connection the 2026-09-19 debrief found missing (`.xor/out/codex/fuckup.md` :82-84, :255-270): the guarded profile carried the hooks and no `onboard`, the operator's home carried `onboard` and no hooks; step 1's "onboard brief exit 0" per member cannot read for a codex or grok member until the wrapper is in the profile home. Measured 2026-09-23 at 1bd5e764: `grep -c onboard` reads 0 in `profiles.py` and `isolation.py` |
| `ops/conductor/tools/instrument.py` `bind` for an owner-opened terminal (LD-125) | the wall for the minting in `instrument.py` (a wall path, `matrix.py` WALL_PATHS :239 at 89b11dab); S4, the session that takes the thread the minute slice 3 is green in the integration PR and nobody before then, for the `join` reading and the planted rows | the owner's own terminals must `join` a run (the 2026-09-19 ask, `.xor/out/codex/fuckup.md` :25); today `bind` refuses class `capability` for a plain terminal (`instrument.py` :175 at 89b11dab: no `coordination_capability` in a profile and no `COORDINATION_CAPABILITY` in the environment reads "missing or malformed credential") |

### The shapes

**The objective record**, one file, written by the CO before any
assignment: the deliverable's id, the acceptance requirements (the
contract clauses the fix must satisfy, by line), the candidate's
starting ref, and the two controlled cases the verifier will plant (an
omitted behaviour and a changed candidate). It is written by
`sessions.py trial objective <run> --deliverable <id> --clause <file:line>
... --candidate <ref> --case omitted:<clause> --case changed:<ref>
[--fake <name> ...]` into `<state dir>/runs/R-n/objective.json`; a call missing the deliverable,
a clause, the candidate or either case is refused `objective`, and so
is `trial accept` on a run whose `objective.json` is absent: acceptance
is judged against the objective, never without one.

**The trial record** is `ops/conductor/handoff/<date>-slice4-trial.md`,
the date the run's start date in `YYYY-MM-DD`, checked by `sessions.py
trial record <path>`, which refuses `evidence` on the first row without a
reading or `not measured <why>` and exits 0 on a complete record. An
acceptance claim is `sessions.py trial accept <run> --candidate <sha>
--record <path>`, which refuses `acceptance` when `<sha>` is not the
40-hex sha in row I-14's evidence cell (`verified <sha>`, the sha first
after the word, the verifier's row; a cell without one refuses
`acceptance` naming I-14, a mismatch names both shas), when any clause in the run's `objective.json` appears by its
`file:line` token in no row's evidence or reading (the omitted
behaviour, refused naming the clause), or when an evidence cell reads
`live` while its row's reading names a program the objective's `fakes`
list declares planted, or reads `mock <name>` and `live` together.
`objective.json` therefore carries `fakes`, the planted programs by
name, possibly empty and always present. It is a table with one row per
acceptance id I-01 to I-14 (plan §13), each row `id | required result |
evidence | reading`,
where `evidence` is a path plus sha, a record id, or a probe row id, and
`reading` is the measured line, or `not measured` with why. A row whose
evidence is a mock's pass is written `mock <name>`, never `live` (plan
§13: "No result is labeled live merely because a mock matched"). The
crosswalk AC-01 to AC-14 is a second table over the same evidence; it
never declares the September 16 objective complete.

**The run export**: `sessions stop` (slice 1) then `telemetry` for the
run, preserving every attempt including the failed ones, plus
`coordination.py` `decisions` and `observations` for the run's
participants, into `<state dir>/runs/R-n/export/` (plan §10). The
runbook's export command is `sessions.py export <run>`, the run
positional and the destination `<state dir>/runs/R-n/export/` unless
slice 1's `--to DIR` names another; it exits 0 only when it names every
managed member's pane log and every dispatch record id the run
launched. "Launched by the run" is read from the store, never from a
message: the `dispatch_intents` rows (slice 3) whose requester or
dispatcher is one of the run's participants, reserved at or after the
run's `started_at`, and whose intent reached a record. The run's
participants are its members and the participant that started it:
`session_run` carries `started_participant`, written by `start` from
the caller's binding (an ALTER adds the column to an older store), and
export, `decisions` and `observations` count the starter with the
members. The window has both ends: a row counts when it was reserved
at or after the run's `started_at` and before the window's end, which
is the earliest of three stamps that exist: the stamp of the run's
`stop` request (the move to `stopping`, never the later close to
`stopped`, which a run with an unresolved member never reaches), the
`started_at` of the next run the same binding started, and the
export's own stamp. Two runs of one starter therefore never share a
row: a launch after R-2 started belongs to R-2 alone, even while R-1
is still open. A member is bound to one run; the starter is
not, and without the upper bound an export of R-1 taken after R-2
started would list R-2's launches as R-1's (the reviewer agentscient-0e
at cff1115e). The intents table gains no run column. A managed
member with no pane log, or a record id whose record is not under the
jobs root, refuses `export` (exit 2, stdout empty) before anything is
written; a log that exists but cannot be read is slice 1's partial
export, exit 1 with the failure named. The export writes `run.json`
with the records, decisions and observations of the run's participants,
and the run's telemetry rows when the telemetry store selects by run;
otherwise `run.json` declares `telemetry not exported <why>`, a known
gap in the handoff, never a silent absence. A joined member has no pane
log and is listed `joined <id> no pane log`, a declared absence, never a
refusal.

**The handoff** (plan §13): the candidate revision, the source-adaptation
manifest (`SOURCES.md` rows), the actual configuration and command
examples, acceptance evidence by id, native version and probe results,
trial timings, total usage where the harness exposes it, known gaps, and
the disable and rollback procedure. Written as a `handoff.py` package
with one thread per open gap.

### The trial steps, as transitions

Each step is a transition in the run's workflow (docs/workflow.md):
actor, evidence duty, failure class, gate. A step whose evidence duty is
unmet does not advance; the trial record says which step stopped it.

| step | actor | evidence duty | failure class | gate |
|---|---|---|---|---|
| 1 start | owner's terminal | `sessions start` line per member with eight `ok` or declared `unmeasured` (slice 2 readiness), the onboard brief run from the member's own environment with its exit and every absence line it printed (exit 0, or exit 1 on absences the brief declares by name, gh auth in a private home among them; exit 2 or an `UNREACHABLE` line is the step failing, read by the reviewer agentscient-aa against #279 at d5c15d96: the lock line timed out for every member and the operator, filed by aa), enforcement observed separately from readiness | `readiness` | I-01, I-02, I-03 |
| 2 objective and conflict | CO, then XO | the objective record; one controlled conflict: two claims on one resource, the refusal line, the release, the later progress, all from the store | `conflict` | I-10 first half |
| 3 cross-harness question | a persistent member | a `send` to another harness's member, its native delivery (slice 2 `delivery_attempts`), the reply in the recipient's existing session, delivery and acknowledgment latency from the store's stamps | `ingress` | I-05, I-04 |
| 4 dispatch | Dispatcher | `coord_launch` of a catalogue job with full input references and `--for A-n` (slice 3), the execution identity received by requester and XO from the store, not by message | `launch` | I-07 |
| 5 worker blocker | the dispatched worker | a question through acting MCP received by management while the assignment stays open; the record states whether the return path was live ingress or a checkpoint (never "live steering" for a one-shot route) | `ingress` | I-09 |
| 6 result and verification | Dispatcher, then verifier | `coord_collect` result; the verifier's examination of the candidate against the objective; the omitted-behaviour case and the changed-candidate case each **fail** acceptance, with the failing line | `acceptance` | I-10 second half, I-14 |
| 7 detach and recover | owner, then observer | `attach` and re-attach with the native sessions untouched; `recover` (slice 2) over the same pending and complete records; a process-loss case against a sacrificial member only, the `lose-<place>` marking and the replacement's next generation | `recovery` | I-11 |
| 8 stop and export | XO | `stop` accounting for owned sessions and detached jobs, and per joined member whether the hold was acknowledged: the `inform` "stop requested" asks for no ack, so the accounting reads the member's own reply from the store (a `send --purpose inform --reply-to M-n` answering the stop-requested message M-n), `acknowledged <stamp>` with the reply's `queued_at` stamp (the messages table's column) in ISO 8601 UTC, `unacknowledged` when the stop-requested message reached the member and no reply answers it, or `unacknowledged no stop request reached it` when no such message exists for the member; never a default (the 2026-09-19 debrief's F6, `.xor/out/codex/fuckup.md` :182-197: a hold counted before anyone acknowledged it); unrelated tmux resources surviving (measured by a server list before and after); the run export with failed attempts kept | `export` | I-12, I-13 |

### Refusals added

| class | when |
|---|---|
| `objective` | an objective record without the deliverable id, the requirements by clause, or the two controlled cases |
| `acceptance` | an acceptance claim whose candidate sha is not the verified sha (stale evidence), or whose evidence is a mock written as live; the controlled cases must be refused here or the verifier is not independent |
| `evidence` | a trial record row with neither a reading nor `not measured <why>` |
| `export` | an export missing a member's pane log or a dispatch record id the run launched |

Every other refusal in the trial is one of slices 1 to 3 and keeps its
class there.

### Closure

Closure is measured, not declared:

1. the trial record has a reading or a declared absence in all fourteen
   rows, and rows I-10 and I-14 read `refused` for both controlled cases;
2. `ops/bin/suites.sh` green on the integrated candidate, and the
   repository's required checks on its PR;
3. the runbook's five commands (start, inspect, stop, resume, disable)
   each measured once in the record with their exit;
4. the disable procedure measured: the managed-session entry point
   disabled, coordination history and dispatch records intact, the
   existing CLI, dispatch and read-MCP paths still usable, no branch
   history touched.

At closure the dev-lane change stops expanding (plan §13, last line);
what follows is operating evidence, not more rows.

### Required of the wall

Two rows, declared. The `bind` minting for an owner-opened terminal is
an `instrument.py` change ("The owner's terminals" below), one wall
session's deliverable, red then green with the two planted rows there.
The onboard wrapper inside a codex or grok member's
private home is a `profiles.py` change (the owners table above), so
slice 4 needs one wall session whose deliverable is that row, red then
green with a planted row that reads the wrapper from the profile home
and refuses its absence. Also declared, not inferred: the LD-117
fallback is a wall fix (`unsafe-command.py`), so choosing the fallback
adds a second wall session whose deliverable is that row and nothing
else.

### The owner's terminals

Current implementation: #284 supplies atomic bind-time enrollment, the private
pointer and run/participant/digest validation. The owning coordination contract
documents rollback and authenticated rebind. The LD-125 trial cases below now
run as ordinary regressions. Earlier ownership and absence notes below retain
their original planning context.

A terminal the owner opened by hand joins a run as a member without a
managed launch (plan §1, the first ask of 2026-09-19). `join` needs a
bound participant, and `bind` today refuses class `capability` for a
plain terminal (LD-125). The clause: `bind` in an owner-opened terminal
mints the participant's private capability at bind time, writes it into
that terminal's own profile home, `<state dir>/runs/R-n/terminals/PT-n/home`,
and nowhere shared, and refuses `capability` only when it can neither
read nor mint one; the refusal names which, `missing` or `malformed
<what>`. The terminal names its run: `bind --run R-n` in a terminal
with no managed launch, refused class `absent` when `R-n` names no run
(the class an unknown run takes everywhere in this lever; `missing` and
`malformed <what>` are the found of a `capability` refusal, never a
class);
the mint writes `<home>/profile.json` carrying the coordination
capability and prints `profile <path>`, and every later verb in that
terminal reads it through `AGENTSCIENT_HOOK_PROFILE`, exported by the
shell as the runbook shows, or `--profile <path>` on the verb. A bound plain terminal joins with the same eight readiness
lines as a managed member plus a ninth line that `status` prints for
every member, `enforcement unmeasured` until the hook profile is
observed and `enforcement ok <profile id>` after, never `ok` by default. The minting is a wall change:
`instrument.py` is on the wall list (`matrix.py` WALL_PATHS :239 at
89b11dab, read by the builder agentscient-10, who refused to write it
under a grant of class `other`, the LD-131 shape), so it is one wall
session's row, and slice 4's first non-wall item is the `join` reading
and the planted rows, taken by the session the owners table names: a world with no profile and no
environment capability binds, joins, and reads `unmeasured` on
enforcement; the same world with a malformed capability refuses
`capability` naming the malformation.

Process identity uses coordination's shared start-tick reader for sessions,
dispatch intents and courier routes. A zombie or malformed /proc record is gone,
even while its PID remains visible (LD-150). The shared instrument transaction
owns commit/rollback, and the dispatch MCP adapter uses the shared transport.

### Tests

Slice 4 owns one suite, `ops/conductor/tests/test_sessions_trial.py`
with `sessions_trial_faults.py` beside it (written red by the reviewer
agentscient-cf at 662f9407, repointed at 3f17367a, 2026-09-23), rows by
id for the four refusals, the trial record's shape, step 8's accounting
and the owner's-terminal clause. Which rows ride the run is declared,
never left to a red count: the acceptance rows write their own
objective through `trial objective` first and assert `fakes` present;
a row over the trial record skips, naming the reason, while no record
exists at `ops/conductor/handoff/<date>-slice4-trial.md`; the
owner's-terminal rows are marked expected failures citing LD-125 until
the wall session lands the minting, and turn red-for-real the commit it
lands. The controlled cases below are planted in the
live trial by the verifier and recorded in the verifier's record; the
suite carries their fixture shape, the verifier carries their reading:

- **omitted behaviour**: the candidate lacks one requirement clause; the
  verifier's acceptance reads `refused acceptance` naming the clause;
- **changed candidate**: the candidate sha moves after verification; the
  acceptance reads `refused acceptance` naming both shas;
- **process loss**: a sacrificial member's process is ended outside the
  supervisor; `recover` marks `lose-<place>`, a replacement mints the
  next generation, the old row stays (slice 2 `recover`);
- **stop leaves strangers alone**: a tmux server on another socket
  started before the run survives `stop`; the server list before and
  after is the reading (slice 1 tmux ownership).

The existing suites are the regression floor: slices 1 to 3's modules,
`test_dispatch_wrapper`, `test_handoff`, `test_monitor*`, and the
read-MCP rows, all green at the candidate. A probes corpus row per
acceptance id (`P-sessions4-I-nn`) serves the trial record's evidence
cell, so the hand-over of the trial is computable (docs/probes.md).
