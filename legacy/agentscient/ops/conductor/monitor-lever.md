# Monitor lever — configurable observations and bounded messages

Standing: PROPOSED, owner review required. Owner request 2026-09-04:
dev-lane machinery first; configurable mini-cron for conditions, token/cache
and GitHub usage; minimal conductor context; removable harness adapters.
This tranche is stdlib Python in the existing lane, not Symfony work.
It inherits `instrument-conventions.md`. No merge/readiness is implied.

## Boundary and command wire

Observation adapters only read; deterministic conditions decide whether an
event exists; delivery adapters publish data; the conductor alone chooses
remediation. No model calls, generated shell, merges, agent spawning,
transcript forwarding, dynamic imports or expressions from configuration.

`python3 ops/conductor/tools/monitor.py [--config FILE] [--state DIR] VERB`

- `check`: read-only; validate everything before any source or state access.
  Emit JSON `{"valid":true,"monitors":N,"schedules":N,"conditions":N,"routes":N}`.
- `run --once|--loop [--now ISO]`: identity required; observe due monitors,
  enqueue events, persist dedup/schedule/cooldown state. `--now` is an
  injected timezone-aware ISO clock of at most 128 printable ASCII bytes for
  once only; it must normalize within the supported UTC range. No live clock
  assumptions in tests.
  Once emits JSON counts `observations,events,unavailable`; loop is silent
  between events/errors. There is no LLM per tick.
- `digest --consumer ID [--limit N] [--max-bytes N] [--ack]`: JSON, events
  in increasing id order plus `more` boolean. Default 10 events and 4096
  UTF-8 bytes, including metadata. Read-only unless explicit `--ack`,
  which requires identity and advances only that consumer's cursor through
  the last returned event. An omitted/foreign consumer must never leak a
  different consumer's messages. Reading without ack is repeatable.
  Acknowledgment means the named conductor consumed the data, NOT that an
  unsupported external harness received it; delivery status is unchanged.

Configuration defaults to `ops/conductor/monitor.example.json` relative to
this instrument's checkout; `--config` overrides it. Paths in parameters
resolve relative to the configuration file, not the caller's cwd, and each
fits in 1024 bytes in its JSON-escaped representation.
State is explicit `--state`, else `XDG_STATE_HOME/<LANE_NAME>/monitor`,
else the user's `.local/state/<LANE_NAME>/monitor`, through laneconf.
Use SQLite stdlib transactions and parameterized queries in this state
directory; queued payloads, dedup and cursor state commit atomically.
`LANE_NAME` is a registry identifier, and the default destination remains
below the resolved XDG state root. Explicit state may name any real isolated
directory, including an owned fixture under `/tmp`; it is not confined to the
default namespace. In either form, refuse source-repository destinations,
symlinked state/database components, non-regular databases, and overlap with
the configured registry, rollout, or dispatch stores before writing. Never
silently relocate an existing database. `check` does not inspect or create
state. Do not write source stores, configuration, repository files or user
config.

## Registry schema, version 1

The top-level keys are exactly `version,monitors,schedules,conditions,routes`.
Each registry is an object keyed by an identifier matching
`[a-z][a-z0-9_-]{0,63}`. Every entry explicitly has boolean `enabled`.

- schedules: `{enabled,every_s}`, a finite positive number, at least 1.
- monitors: `{enabled,adapter,schedule,params}`. Schedule reference required.
  Adapter-specific validation and reading are registered together; generic
  scheduler/condition/delivery code does not branch on provider names.
- conditions: `{enabled,monitor,op,field,threshold,cooldown_s}`.
  Ops: `changed,gt,lt,unavailable`. `gt/lt` require finite numeric threshold;
  `changed/unavailable` require null threshold. `unavailable` requires null
  field, others require a nonempty field. Cooldown is finite and >= 0.
- routes: `{enabled,condition,adapter,consumer}`. Adapters: `queue,stdout`,
  or `unsupported`; consumer is an explicit nonempty printable identifier
  of at most 128 characters with no control characters or path separators.

Reject unknown fields, duplicate JSON keys, wrong types (bool is not a
number), nonfinite values, missing/dangling references and unknown adapters,
even in disabled entries. Disabled entries are validated, never evaluated.
Operators add/change/disable/remove entries by editing this file and running
check. Reload and validate on each loop cycle; an invalid reload aborts
without evaluating it or deleting the last valid state. Removing/disabling
a route stops future events but never deletes already queued evidence.
Changing a condition or source definition re-arms only affected condition
state; changing an unrelated entry must not duplicate all other messages.

## First observation adapters

All readings are numeric/structural data only:
`{subject,state,metrics,generation,observed_at,evidence,reason}`.
States `ok,unknown,unavailable` are distinct; unknown is never zero.
No raw stderr, prompts, tool text or transcript content enters a reading.
An observation failure is an unavailable reading, not a successful empty
measurement. A reader bug must be named and contained, not silently ignored.

- `dispatch`: params `{store,ids}`, store path and explicit list of record
  IDs (empty selects none; max 256). Reuse the existing watchdog's JSON
  reader where useful, not its swallowed directory-error result. Read only
  selected record/state/exit files. Metrics: `status,changed,input,cached,
  output,total` where present; token values come from recorded session.spend,
  not invented live-worker measurements. Status: `completion` for closed
  env ok; `no-op` for that case with write role and changed_paths exactly [];
  `invalid` for invalid/tripped envelope; `death` for died record or a
  positively identified dead worker; `settling` for exit without closure;
  `running` only with positive integer PID existence, an exact recorded
  executable token in `/proc/<pid>/cmdline`, the recorded snapshot as process
  cwd, and the same nonempty session id in record and launcher state;
  otherwise `unknown`. This Linux `/proc` proof does not infer identity from
  an executable basename match. A missing process is
  `death` only when the static executable/snapshot/session linkage is complete.
  Missing or unreadable records are unknown/unavailable respectively. Never
  pass zero/negative/bool PID to kill.
  Generation includes attempt count and launched timestamp, so a resumed
  record may produce a new event even if it closes between observations.
  No-op is an observation, not proof that the producer failed.
- `codex-usage`: params `{path,max_bytes}`, one explicit rollout path and
  integer byte bound 1024..8388608. Read at most that many bytes from its tail;
  discard a cut leading fragment and an incomplete final JSONL row. Use the
  last complete event_msg/token_count row in that bounded window. No matching
  row means unknown with reason, NOT zero or a full-store fallback. Invalid
  retained usage fields remain unknown. Use the existing usage.py last-count
  semantics: input,cached,output,total from info.total_token_usage; last_input
  and last_cached from info.last_token_usage; context_window from
  info.model_context_window. Numeric values must be finite, nonnegative,
  signed-64-bit integers (not bool). Cache is a subset of input: never add it
  to input.
  Derive cache_fraction=cached/input and context_fraction=last_input/window
  only with positive denominators and valid operands. Keep the event's
  timestamp separately from poll time. Retain it only when its input is at
  most 128 ASCII bytes and parses as timezone-aware ISO, then normalize it to
  UTC. Otherwise `event_at` is null with a fixed bounded reason while
  independently valid usage metrics remain available. No raw malformed value
  enters generation, evidence, SQLite, or output. Cumulative input is
  re-prefill traffic, last_input/window is a last-request context indicator,
  neither is USD.
  The explicit source path is the subject; no scanning other sessions.
- `github-rate`: params `{timeout_s}`, finite 0 < timeout <= 30.
  Run only the code-built argv `gh api rate_limit`, resolving gh through PATH.
  One call per due monitor, never per condition/route/PR. Each resources
  core/graphql/search bucket gives a reading with limit,remaining,reset and
  remaining_fraction. Validate nonnegative signed-64-bit integer values,
  positive limit, remaining <= limit. Missing/malformed buckets are unknown; process, timeout,
  decode and authentication failures are unavailable without forwarding raw
  stderr. Generation is the bucket reset epoch; a new quota window re-arms
  its condition and is not charged as usage in the previous window.
  No inferred dollar cost and no assumption that a full quota means no past use.

Observation JSON files are opened nonblocking, verified as regular after
open, and read once from that descriptor. Dispatch `record.json` and
`state.json` have a 1048576-byte initial-extent bound; their job directory and
selected files may not be symlinks. The exit marker is likewise verified as a
regular file. A selected FIFO, device, escaping symlink, oversized file, or
open/read race is unavailable and cannot stall later observations. The
operator-selected Codex path may itself be a symlink, but its opened target
must be regular; only its configured 1024..8388608-byte tail is read.

The GitHub process has one 1048576-byte bound on stdout and another on stderr.
Both pipes are drained concurrently and nonblocking under the same monotonic
`timeout_s` deadline, including process completion and pipe EOF. Deadline or
either overflow makes all three buckets unavailable; stderr is never emitted.
Only the process created for that reading may be killed and it is always
reaped.

Independent due monitors may observe concurrently with bounded worker count
and subprocess deadlines. Each is read once per schedule, shared by its
conditions/routes. Advance due times using monotonic deadlines; skip missed
intervals rather than a catch-up storm. Source failures do not halt other
due observations. Unchanged file readings may be cached only with change
detection; no full transcript/store rescan every tick.

## Branch freshness adapter amendment

Owner direction (2026-09-09): observe both trunk and release through this
monitor; earlier synchronization proposals are investigation, not authority.
Implementation standing: `git-refs` is registered in `tools/monitor.py`'s
existing `ADAPTERS` interface; independent acceptance remains pending.
The example below leaves this source disabled; activation belongs
to the integrating session. The interface remains:
validator `(params, base)` and reader `(params, now)` returning the reading
list above. No new scheduler, condition operator or delivery mechanism.

Params are exactly `{repo,remote,branches,timeout_s}`: `repo` follows the
existing config-relative path rule; `remote` is an explicit Git remote name
matching `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`; `branches` is an ordered, unique
list of 1..32 literal short branch names, each <=255 ASCII bytes and valid
under Git's ref-name rules, with no patterns or leading `-`; `timeout_s`
is finite, non-bool, 0 < value <= 30. Deployment config must include `dev`
and `master`. Invalid params refuse `config`, including disabled entries.

Read local full `refs/heads/<branch>` OIDs, never abbreviated HEAD or
`refs/remotes/*`. Once per observation, build argv in code:
`git -C <repo> ls-remote --refs -- <remote> refs/heads/<branch> ...`.
One query covers every configured branch, shared across conditions/routes;
no retry or cached remote answer. Exact returned ref names only; validate
full 40/64 lowercase hexadecimal OIDs, reject duplicates/malformed output.
An exit-0 response omitting a requested ref proves remote absence. Spawn,
authentication, nonzero exit, timeout, decode or output failure cannot.
Local reads and the query share one monotonic `timeout_s` deadline, including
EOF and reap; each child pipe is capped at 1048576 bytes, drained concurrently.
Overflow/deadline kills and reaps only the observation's child. Never expose
raw stderr. Missing/unreadable repo or local read failure is unavailable.

One reading per branch in configured order: `subject=refs/heads/<branch>`,
`generation=[resolved_repo,remote,subject]` (stable across polls/ref moves),
`observed_at` is the supplied UTC poll time; `evidence` is
`{repo,remote,ref}`. Metrics are exactly `{local_oid,remote_oid,freshness}`;
unobserved/absent OIDs are null, distinguished by freshness and bounded fixed
`reason` (null on successful measurement). Remote failure takes precedence
over missing local; local read failure takes precedence over remote failure
and comparison.

| reading state | metrics.freshness | observable result |
|---|---|---|
| ok | equal | both OIDs present and identical |
| ok | different | both present and unequal |
| ok | missing-local | local absent, remote present |
| ok | missing-remote | local present, remote absent |
| ok | missing-both | both proved absent |
| unavailable | remote-unavailable | query failed; remote_oid null |
| unavailable | local-unavailable | local read failed; local_oid null |

No ancestry claims: absent remote objects still permit `different`, never
invented ahead/behind/divergence counts. Observation never fetches, resets,
merges, checks out, creates refs, pushes, edits config, invokes arbitrary
configured commands or calls models. Git reads run with system and global
configuration disabled, inherited `GIT_*` variables dropped, `GIT_SSH_COMMAND`
fixed to `ssh`, the credential, askpass and alternate-refs commands emptied,
and only the file, http, https and ssh transports allowed, so a source
repository's own configuration cannot name a command to execute; a remote
needing any of those reads as remote-unavailable. Monitor writes remain confined to its
existing queue/state, disjoint from the configured repo and Git common dir;
no other actor's checkout is touched and no tick synchronizes branches.
A readable invalid `.git` pointer names no external storage; its root remains
protected and its observation is unavailable. Uninspectable metadata still
refuses unsafe state writes. State in a linked worktree sharing the source's
Git storage is also refused.

Minimal version-1 registry example (repo path relative to this config):

```json
{
  "version": 1,
  "schedules": {"forge": {"enabled": true, "every_s": 300}},
  "monitors": {"branch_refs": {"enabled": false, "adapter": "git-refs", "schedule": "forge", "params": {"repo": "../..", "remote": "origin", "branches": ["dev", "master"], "timeout_s": 5}}},
  "conditions": {
    "refs_changed": {"enabled": true, "monitor": "branch_refs", "op": "changed", "field": "freshness", "threshold": null, "cooldown_s": 0},
    "refs_unavailable": {"enabled": true, "monitor": "branch_refs", "op": "unavailable", "field": null, "threshold": null, "cooldown_s": 0}
  },
  "routes": {
    "refs_changed": {"enabled": true, "condition": "refs_changed", "adapter": "queue", "consumer": "operator"},
    "refs_unavailable": {"enabled": true, "condition": "refs_unavailable", "adapter": "queue", "consumer": "operator"}
  }
}
```

Independent TDD cases before implementation: two equal refs; dev different
with master equal and vice versa; each missing-ref row above; stale tracking
refs equal to local while live remote differs; remote OID absent from local
objects still yields `different`; unreachable/auth failure, timeout, malformed
or duplicate rows and either pipe overflow yield unavailable, never absence.
Assert one remote argv for both branches even with both routes, bounded local
reads, stable generation, first/transition events and unchanged-poll/restart
dedup, recovery to equal, disabled/removed source making no query, invalid
params refusing before observation, and unchanged refs/index/worktree/config
with dirty files, local commits and another occupied worktree. After tests,
one live bounded adapter probe must report both dev/master OIDs and freshness,
stamped with repo, remote, tested revision and time; unreachable is unverified.

## Conditions, queue and context

Each condition evaluates per subject and generation. Numeric predicates
require an ok reading and known finite field; unknown never crosses a
threshold. Unavailable matches only state unavailable. Changed compares the
named field, emitting its first known value then transitions. Numeric and
unavailable conditions emit on entry into the matching state, not every
poll. Persistent matching state does not re-alert merely because cooldown
expired. A transition suppressed by cooldown stays pending while true,
emits when eligible, and is canceled if it stops matching. A new generation
re-arms without inheriting the previous generation's cooldown.

Persist state across process restart. Event creation and its dedup state are
one transaction; concurrent runs cannot double-enqueue the same transition.
Events contain id, route, consumer, condition, monitor, subject, state,
summary (<=240 UTF-8 bytes), evidence reference, observed_at and delivery.
Summary comes from the named metric/status, not arbitrary source text.
JSON must escape control characters and never emit terminal control bytes.
No stdout message may expose events addressed to another consumer through
digest. Queue is the default delivery; stdout is opt-in on a route.
Unsupported routes remain queued with delivery=unsupported, never falsely
marked delivered. Direct Codex hook/idle-wakeup integration is NOT admitted
by this tranche; a configured external adapter must be probed before use.
A queue/digest is a real usable conductor interface, not proof of wakeup.

Keep stable operating instructions in the contract; volatile data is only
the bounded digest. Evidence is referenced, not copied into every prompt.
No automatic compaction, model switching, budget changes, or tool permission
changes are authorized by a threshold notification.

## Refusals and verification

Before acting: config (unreadable/invalid JSON or schema), reference (missing
registry target), arguments (invalid verb/options/bounds/clock), identity
(write without valid WF_AGENT), consumer (invalid/missing consumer), state
(unreadable/corrupt existing state), conf-key (missing lane setting).
Inherited wire: one stderr line, expected/found/needed, exit 2, empty stdout.
A source unavailable during a valid run is data, not a config refusal.
Interrupted loop exits 3. After loop action begins, any config or state
refusal exits 3 with durable state preserved; argument, identity, initial
config and initial state validation remain pre-action exit 2 refusals.

Independent tests must exercise malformed/disabled/removed configuration,
controlled dispatch lifecycle/no-op/unknown PID, a bounded synthetic Codex
tail with duplicates/partial rows/missing currencies, replayable gh mocks,
quota resets, threshold recovery/cooldown, restart/concurrent dedup, and
consumer/ack/output bounds. No credentials, real stores or vendor calls in
tests. Real probes: one bounded quota read and one aggregate-only read of an
explicit operator-selected rollout. External harness delivery stays unprobed
until separately verified. Register the instrument's config refusal row.

## Host adapter and the leak record

Standing: proposed, at the owner's word (2026-09-23, 01:53Z: "we have
watchdog and monitor - maybe they can also track memory leaks?"),
written by the contract session (agentscient-87, Claude Fable 5.1) at
the Lead's order through the XO, beside the watchdog's "Host
conditions" section, which holds the readings' sources, the config keys
and their defaults, and the tmux liveness rule; the two read the same
four things so a line and a trend never disagree.

- `host`: params `{tmux_root,tmux_glob,grace_min}`, each optional with
  the watchdog's defaults. Metrics: `mem_available_mb, mem_total_mb,
  swap_used_mb, swap_total_mb, processes, tmux_servers,
  tmux_stale_sockets`, all integers; `tmux_servers` counts socket files
  older than the grace period that answer a connect, and
  `tmux_stale_sockets` the ones that refuse it. State `ok` when every
  source was read; `unavailable`, with the source named in `reason`,
  when any was not; never a partial row read as ok. Evidence is the
  paths read (`/proc/meminfo`, the proc root, the tmux root), not their
  contents. Subject is the host name; generation is the boot id
  (`/proc/sys/kernel/random/boot_id`), so a reboot re-arms every
  condition.

Conditions on it are the schema's `lt` and `gt` over those fields; the
lane's own registry carries `mem_available_mb lt 2048`, `swap_used_mb gt
4096`, `processes gt 3000` and `tmux_servers gt 0`, each with a
cooldown of 900 s, routed to the consumer `dispatch` so the Dispatcher's
digest carries the trend the watchdog's line points at.

The first rows, taken by hand on 2026-09-23 before the adapter existed,
recorded here so the trend has its origin:

| observed_at | mem_available_mb | mem_total_mb | swap_used_mb | swap_total_mb | processes | tmux_servers | by |
|:--|--:|--:|--:|--:|--:|--:|:--|
| 01:35:48Z | 2298 | 30909 | 8191 | 8192 | not measured | not measured | the XO, `free -m` |
| 01:40Z | not measured | not measured | not measured | not measured | 7360 | 1211 on `/tmp/sessions-*/tmp/tmux.sock` | the Lead |
| 01:44Z, after the owner's kill | 17469 | 30909 | 2098 | 8192 | 1253 | not measured | the XO |

Tests (`ops/conductor/tests/test_monitor_host.py`, on the kit): the
planted fault is the watchdog's, a world-owned listener on a temp socket
older than the grace period, which reads `tmux_servers` 1 and trips
`tmux_servers gt 0` once, not on the next poll; the clean neighbour,
younger than the grace period, reads 0 and trips nothing; a planted
`meminfo` under the proc root drives `mem_available_mb lt` and
`swap_used_mb gt`; an unreadable `meminfo` reads `unavailable` with its
reason and matches only an `unavailable` condition; a changed boot id
re-arms. Independent producer, as the section above.
