# Live-state board output contract

This contract governs `agentscient.board/v1`, the read-only value passed from the
conductor's live-state board to its renderer and later to the ratified custody
capability. It is the durable part of the bridge. Source acquisition, local
cache custody, and provenance adapters are replaceable; this shape and its
refusals are not.

The board reports observations. It does not approve a merge, ratify a
decision, normalize a verdict, or become a source of truth.

## Ratified v1 scope

The ratification narrows the plan's public CLI. There are four source queries:

```text
python3 ops/conductor/tools/board.py branches   [--json]
python3 ops/conductor/tools/board.py prs        [--json] [--offline]
python3 ops/conductor/tools/board.py decisions  [--json]
python3 ops/conductor/tools/board.py dispatches [--json]
python3 ops/conductor/tools/board.py all        [--json] [--offline]
```

`all` is one aggregate read of those four sections, not a fifth source query.
The planned `lineages` and `verdicts` verbs are not in v1. The sections answer,
respectively:

- local branches ahead of their named target that have no open PR;
- PR checks as observed at a named time, without a board-invented green flag;
- tracked open questions awaiting the owner; and
- dispatches in flight, with elapsed time measured from a recorded launch.

A section verb invokes no unrelated provider. A later unit supplies the
automatic session-start digest required by D-BOARD-2; U0 defines only the value
it will carry.

## Envelope

The closed CUE definition is
[`contracts/board.cue`](contracts/board.cue). A JSON result has:

- schema `agentscient.board/v1` and the requested `query`;
- UTC `started_at` and `ended_at` values;
- a repository value with root, full captured HEAD, branch, and evidence;
- an observation registry; and
- exactly the requested section, or all four sections for `all`.

Every repository value, section, and item has a non-empty `evidence` list.
Every id in such a list resolves to an observation in the same envelope. JSON
mode writes only this envelope to stdout; diagnostics belong on stderr.

Exit status is part of the interface:

- `0` means every requested section was obtained in its declared mode;
- `4` means a verified prior forge observation was returned because offline
  mode was requested or a live refresh failed; and
- `2` means at least one requested section is `unmeasured`.

Argument and contract errors use another nonzero argparse/program exit and do
not print a partial JSON object.

## Observations

An observation records the command as an argv array, UTC start and end, cwd,
allowlisted non-secret environment, repository ref (or an explicit reason it
does not apply), exit status, and SHA-256 of the raw bytes. A `measured`
observation has exit `0`; a `failed` observation has a nonzero exit. Both are
measurements of what the command did, but only the first can supply a measured
value.

Allowed environment keys are `lane`, `METERS_DB`, `DISPATCH_JOBS`,
`XDG_STATE_HOME`, `LANG`, `LC_ALL`, and `TZ`. Absence is different from an
empty value; no other key, especially a credential, can appear.

Observation ids are unique JSON object keys and match `obs.<name>`. Times are
ordered, Git SHAs have 40 lowercase hexadecimal characters, and raw digests
have 64. These relations, evidence resolution, query/section correspondence,
and unique owner-decision ids are checked beside CUE by the acceptance driver.

## Section states

Each section has exactly one state:

- `measured` carries `items`, including a deliberate empty list;
- `cached` carries `items`, `observed_at`, `read_at`, `age_seconds`, the
  successful observation id, the current cache-read observation id, and an
  optional failed-refresh observation id; or
- `unmeasured` carries a refusal and no `items` key at all.

Every refusal has `code`, `expected`, `found`, and `satisfy`. An empty list is
an answer. An omitted list in an unmeasured section is the explicit statement
that there is no answer here. A source failure may never be represented by
`items: []`, even when a caller believes it remembers items from an earlier
read.

A cached PR value is visibly old. Human rendering prefixes it `CACHED —
refresh required` and preserves upstream text only as
`blocking_at_observation`; it never renders cached readiness as a current
instruction.

Derived values carry an expression and their evidence inputs. In particular,
dispatch `age_since_launch_s` uses the board's frozen end time. `tripped` and
`DIED` dispatches do not call that duration runtime. Missing or malformed
launch metadata keeps the dispatch visible and makes the duration an explicit
unmeasured scalar.

## Section items

`branches` items carry lineage, full local tip, target and target tip, a
strictly positive ahead count, `open_pr: false`, scope `local-head-ref`, and
evidence. The section makes no claim about an unfetched remote tip.

`prs` items pass through the forge adapter's PR identity, full head SHA,
base, draft/merge state, expected-set-aware check buckets, blocking text at
the observation, review age, and unresolved threads. The board does not add a
`green` or `ready` field.

`decisions` items carry the marker id, complete question, source path and line,
captured source ref, and evidence. They mean “tracked open at this ref,” not
“the owner has not answered elsewhere.”

`dispatches` items carry launcher-owned state and joined metadata when it is
available. Missing or ambiguous metadata is explicit and never removes the
launcher row.

## Owner-decision markers

An open tracked question is one Markdown checkbox item with exactly one
invisible marker immediately after the checkbox:

```text
- [ ] <!-- owner-decision:<id> --> <complete question>
```

`<id>` matches `[a-z0-9]+(?:-[a-z0-9]+)*` and is unique across the file. One
item contains one question; grouped questions are invalid. A checked item is
closed and is omitted from the board, but its id is not reused. The four owner
questions supplied for U0 are recorded in `ops/docs/TODO.md` without changing
their wording.

## Named contract refusals

The acceptance corpus pins these machine-facing names:

| condition | refusal |
|:--|:--|
| observation command absent or not argv | `board.observation-command` |
| cached section lacks custody time/age/observation ids | `board.cache-custody` |
| source is unmeasured but an `items` key is present | `board.absent-is-not-none` |
| owner-decision id is absent, malformed, reused, or grouped | `board.owner-decision-id` |
| evidence id does not resolve in the envelope | `board.evidence-reference` |
| time interval is malformed or reversed | `board.observation-time` |
| requested query and returned sections disagree | `board.query-sections` |

The fault documents under `contracts/faults/` each plant one named condition.
The positive example is intentionally measured-empty for `branches` and
`dispatches`, proving that a genuine empty answer remains legal.

## Bridge boundary

This contract creates no registry, dependency injection, service locator, MCP
server, durable repository record, authority, or workflow-ledger change. Board
reads never fetch or update Git refs and never ingest or rebuild Meters data.
Only the future forge adapter may atomically replace its derived cache under
the operator state directory. A failed refresh leaves the last successful
bytes untouched.

