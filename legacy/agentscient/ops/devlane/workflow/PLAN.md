# dev-lane workflow — plan

Status: **ratified for execution** (owner, 2026-08-20; decisions recorded
in §12). Scope is `ops/devlane/workflow/` only. Implementation starts from
§13's Slice 1 brief.

This document uses dev-lane vocabulary freely (work order, gate, stage,
receipt, red/green, chain, claim). That vocabulary stops at the wall in §10:
none of it ever appears in the product's design artifacts.

Source material was mined from loopflow at commit `5ec73bd`
(`github.com/xormania/loopflow`). Where this plan says "as-is", the
referenced schema or discipline is lifted from there.

---

## 1. Goals and non-goals

**Goals.**

- **Record the gate process as data.** Work orders, gate stages and their
  outcomes, open markers, sessions, artifacts produced, agent attempts and
  refusals, test-run receipts, commit SHAs — in SQLite, tamper-evident,
  git-tracked.
- **Speed.** Low ceremony per step: evidence capture is a side effect of
  running the step, never a separate ceremony. One short command per action;
  `next` answers "where does this stand and what advances it" in one call.
- **High evidence per step.** Every receipt carries the exact command, exit
  status, output hashes, timestamp, and the git state it ran against.
- **Agent-first.** `--json` everywhere, documented exit codes, no prompts,
  context-cheap outputs, idempotent where possible.

**Non-goals.**

- **Deciding.** Records are evidence, never authority. The system enforces
  *mechanical preconditions* (a receipt exists, a hash matches, a chain
  verifies) and refuses when they fail; it never judges *merit*. Merit
  verdicts — a review's finding, a denial, ratification — are recorded as
  stated by gates and the owner, never computed. Nothing here approves,
  ratifies, or blocks on its own judgement.
- No server, no network layer, no multi-machine coordination (loopflow's
  `serve`/remote and multi-harness machinery are dropped).
- Not a general project tracker. Only the gate process of the dev-lane.
- No vocabulary leakage into the product's design artifacts (§10).

---

## 2. What carries over from loopflow

| piece | disposition | note |
|:--|:--|:--|
| event chain (`events` table, canonical JSON, hash discipline) | **as-is** | canonical form is *defined* as byte-identical to Python's `json.dumps(obj, sort_keys=True, separators=(",",":"))` — the Python rewrite inherits the format natively; chains verify cross-language |
| derived-state pattern (`packet_state`) | **as-is** → `wo_state` | chain is authority; projection refuses when they disagree |
| refusal vs integrity distinction | **as-is** | refusal (exit 1) names the first failed precondition; integrity (exit 2) blocks, is never downgraded, never accompanied by a projection |
| `claims` (TTL, reclaim-as-heartbeat, exit 3) | **as-is** | `owner` column becomes `agent` |
| `attempts` (append-only; `record_kind`, `exhaustiveness`, before/after bindings, output tails + hashes) | **as-is + `agent` column** | the refusal record; a stored reason stays falsifiable via its bindings |
| `artifacts` CAS (sha256-addressed, verify-before-write) | **as-is** | holds full receipt outputs and frozen-set manifests |
| corrections via `supersedes_seq` | **as-is** | never edit; append the correction |
| exit-code map (0/1/2/3/64) | **as-is** | see §4 |
| migration discipline (numbered forward-only SQL) | **as-is** | |
| `worker_sessions` | **simplified** → `sessions` | single machine; drop the history table and the role/task/cycle key |
| `packets` + objective | **adapted** → `work_orders` | gains gate-kind pin |
| CLI verb design | **adapted** | §4 |
| marker protocol (`WORKFLOW-ERROR` …) | **dropped as wire protocol** | its classification survives as `attempts.record_kind`; our runners capture exit codes directly instead of wrapping foreign tools |
| server, remote, projects/state-root scoping, sqlc, Docker | **dropped** | single repo, single machine, stdlib `sqlite3` |

---

## 3. Data model

### 3.1 The chain is the authority

One append-only chain per work order. An event is a JSON object:

- `seq` (1-based), `prev` (previous event's hash; 64-zero digest at seq 1),
  `time` (RFC3339 UTC, exactly six fractional digits, literal `Z`, hashed as
  a string, non-decreasing per chain), `type`, type-specific fields,
  `state_sha256`, and `hash` = SHA-256 over the canonical JSON of the event
  without its own hash field.
- The stored payload is the **exact canonical bytes** that were hashed.
  Verification re-parses from payload; a rewritten row is a rewritten record
  even when it decodes to the same value. Indexed columns are checked against
  the payload they were derived from.
- Supplying `seq`/`prev` on append is compare-and-swap: a stale writer is
  refused, not overwritten.

`wo_state` rows are a derived read view carrying the seq/hash they were
derived from; reads that find them disagreeing with the chain refuse with an
integrity error.

### 3.2 Tables

Nine tables. Everything else that must be recorded lives **in the chain**,
not in tables — evidence belongs in the tamper-evident structure, and fewer
tables is the point. Queries over event payloads use SQLite's JSON functions;
a derived index table is added only if that is measured slow.

| table | holds | provenance |
|:--|:--|:--|
| `work_orders` | `wo_id`, `objective`, `kind`, `kind_version`, `spec_sha256`, `created_at` | packets, adapted |
| `events` | the chain (`wo_id`, `seq`, `hash`, `prev`, `time`, `payload`, `state_sha256`) | as-is |
| `wo_state` | derived projection: current stage, satisfied evidence, open markers | packet_state, as-is |
| `gate_kinds` | `kind`, `version`, `spec_json` — the gate-kind registry (§6) | new |
| `artifacts` | CAS: `digest`, `size`, `media_type`, `class` | as-is |
| `claims` | `wo_id` PK, `agent`, `note`, `acquired_at`, `expires_at` | as-is |
| `sessions` | `session_id`, `agent`, `wo_id`, `status`, `started_at`, `last_seen`, `ttl_seconds` | simplified |
| `attempts` | every attempted transition incl. refusals; before/after bindings; `agent` | as-is + agent |
| `schema_migrations` | bookkeeping | as-is |

Chain-resident records (event types):

| required record | event type(s) |
|:--|:--|
| stage transitions | `stage.entered` |
| gate outcomes | `outcome` (value from the stage's allowed set) |
| test receipts | `receipt.red`, `receipt.green` |
| security / generic check receipts | `receipt.check` |
| frozen test set | sealed in `stage.entered` (see §3.3) |
| open markers (findings, blockers) | `marker.open`, `marker.close` |
| commit SHAs | `commit.landed` (see §3.5) |
| artifacts produced | `artifact.recorded` (digest + repo path) |

### 3.3 Receipts — the TDD gate

Common receipt fields: stage, check name, `argv`, `cwd`, `exit_code`,
`duration_ms`, `stdout_sha256`/`stderr_sha256` (full outputs in the CAS,
short tails inline), `agent`, `head_sha`, `tree_dirty` — the git state the
command actually ran against.

- **`receipt.red`** — `wf red` runs the given test command, **requires a
  nonzero exit**, and records the receipt plus a `test_manifest` digest: the
  canonical JSON of sorted `(path, sha256(content))` pairs over the stage's
  declared test scope **and harness scope** — the inputs that select and
  drive tests: `Cargo.toml`/`Cargo.lock`, cargo config, `rust-toolchain*`,
  `build.rs`, declared fixture dirs (reviewer finding: deselecting a frozen
  test via a manifest edit must change the frozen hash). The receipt also
  records the toolchain version string. The manifest binds what is
  declared; whether the declaration is adequate is merit, judged at
  review. Manifest document stored in the CAS. A zero exit is a
  refusal: there is nothing red to prove. A nonzero exit proves *the command
  fails*, not that a test asserted red — a compile error satisfies it.
  Distinguishing the two is merit, and merit belongs to the review stage:
  the receipt records exit code and output so the reviewer judges the red's
  content.
- **Freezing is an effect, not a verb.** When the work order advances into
  the implementation stage, the *latest* red receipt's manifest digest is
  sealed into `stage.entered` as the frozen-set hash. This removes the
  red-then-freeze race (tests changing between two commands) and trims a
  command: prove-red and freeze cannot drift apart because they are one
  measurement.
- **`receipt.green`** — recomputes the manifest first; **refuses** (exit 1,
  refusal recorded in `attempts`) if it does not equal the frozen hash.
  Green runs the *same argv* the bound red recorded — a different command is
  a refusal, not a comparison. On a matching manifest it runs the command,
  requires exit 0, and records the receipt binding the red receipt's seq and
  the frozen hash.
- **A voided freeze has an exit** (reviewer finding: the first draft
  deadlocked here). `wf advance --to <stage> --reason "…"` re-enters an
  earlier stage by appending a new `stage.entered`; the latest entry
  governs, so re-proving red reseals a fresh frozen set on the next
  advance. Nothing is rewritten; the voided path stays in the chain.
- Behavior specs, where used, get identical treatment: the spec files are
  part of the declared scope, so the same manifest freezes them.

**Advance is mechanical.** `wf advance` refuses unless the projection shows
every receipt/outcome the gate-kind spec requires for leaving the current
stage. That is precondition enforcement (shape), not a verdict (merit).

### 3.4 Security gate and generic checks

`receipt.check` covers every registered check: security runners, `cue vet`,
the relational validator, conformance — same evidence shape as red/green.
Security is not special-cased in the engine: a security stage is a stage
whose spec requires receipts from checks of class `security`. The specific
scanners are pluggable data (§6): a check is a name, a class, and an argv
template in the gate-kind spec. Threat-derived *tests* run under red/green
inside a security stage when the discipline calls for it.

### 3.5 Artifact gates and outcomes

The draft → review → fix → ratify cycle is the seeded `artifact` gate kind.
Outcomes are recorded per stage from the spec's declared set —
`accepted`, `rejected`, `denied`, `aborted`, `finding`, `ratified` — as
distinct values, never collapsed. **A recorded denial exits 0**: recording a
denial succeeded. The spec declares each outcome's *effect* as data —
`advance`, `stay`, a named stage to re-enter, or `terminal` — and the
engine applies the declared effect mechanically, never interpreting outcome
values itself (§6). Outcomes the spec marks `actor: owner` are recorded
like any statement, but their effect is **not** applied on recording
(reviewer finding: an agent-typed `ratified` must never become workflow
state). The attestation is the one act only the owner can perform here —
the merge into protected `main` — and the effect applies when that merge
SHA lands in the chain; alternatives are D10. `wf outcome` records the
stating actor (`--by owner` /
`--by <agent>`); that is evidence a statement was made, not proof of who
made it — a single-machine trust boundary stated, not hidden. Review
findings open markers; `fix` cannot be left while
markers from the current review cycle are open (mechanical, again).

### 3.6 Commit cross-binding

An event cannot contain the hash of the commit that will contain it, so the
bind is one commit behind by construction:

- After each workflow commit, `commit.landed` is appended, recording the
  `sha` and the chain head (`seq`, `hash`) that landed in it. That event
  lands in the *next* commit.
- Every workflow commit message carries the work-order, stage, and head-event
  identifiers (§5).

What the bind proves — stated precisely, because the first draft
over-claimed (reviewer finding): every *bound* commit is tamper-evident; the
tip is not yet bound, because its bind can only land in a successor.
`wf verify --deep` checks three things:

- each `commit.landed` SHA exists, is an ancestor of HEAD, and its tree's
  chain file ends at exactly the recorded head;
- **append-only prefix**: each historical version of a chain file, walked
  through the ancestors that touched it, is a byte-prefix of its successor —
  dropped or rewritten rows are caught even when the rows naming them are
  gone;
- the tip is witnessed externally: CI runs `verify --deep` against the head
  SHA, and its recorded check run is the tip's bind until the next append
  binds it in-chain.

What survives a coordinated rewrite of *both* git history and the chain
(reviewer finding: the prefix walk alone does not — it would be walking the
rewritten ancestors) is only what the forge retains outside the tree:
protected-`main` refs that cannot be force-pushed, PR records, and check
runs pinned to observed SHAs. Those are the independent anchors. A
local-only clone has none, and this plan says so rather than claiming
otherwise.

---

## 4. Toolset

One line per command. Verbs from the brief that were merged: `migrate` into
`init` (idempotent: applies pending migrations), `freeze` into `red`/`advance`
(§3.3), `list` into `status`, `sec-run` into `check` (§3.4), `export` kept but
normally implicit (§5).

| command | does |
|:--|:--|
| `wf init` | create/upgrade the DB: apply pending migrations, sync gate-kind registry from tracked JSON; `--rebuild` reconstructs the DB from the tracked export |
| `wf open <wo> --kind K --objective "…"` | open a work order, pinning the gate kind at its current version |
| `wf claim <wo> --agent A [--ttl 30m]` / `wf release <wo> --agent A` | TTL claim; re-claim is the heartbeat; expiry frees a dead agent's work |
| `wf red <wo> -- <cmd…>` | run the test command, require failure, record the red receipt + test manifest |
| `wf green <wo> -- <cmd…>` | verify manifest against the frozen set, run, require success, record green |
| `wf check <wo> <check-name> [-- <cmd…>]` | run a registered check (security, cue vet, validator…), record the receipt; `--verify-only` runs and exits without recording (CI mode) |
| `wf outcome <wo> <value> --by <actor> [--note "…"]` | record a gate outcome and who stated it; effect applied per the spec (§3.5) |
| `wf marker <wo> open|close <id> [--note]` | open/close a marker (finding, blocker) |
| `wf advance <wo>` | enter the next stage; refuses if required evidence is missing; seals the frozen set on entry to implementation; `--to <stage> --reason` re-enters an earlier stage (§3.3) |
| `wf next [<wo>]` | the agent's home verb: current stage, satisfied/missing evidence, and the exact argv that advances it |
| `wf status [<wo>]` | no argument: all open work orders, one line each; with: full detail |
| `wf log <wo> [--attempts]` | the chain, human or `--json`; `--attempts` includes refusal history |
| `wf commit [<wo>]` | flush export + git commit with structured message + append `commit.landed` |
| `wf verify [--deep]` | recompute every hash and link; `--deep` adds receipt re-verification and commit cross-binds (§7 CI) |
| `wf export` | regenerate the tracked export from the DB (normally fired by `commit`) |

**Agent contract.** Agent identity is day-one: every chain-appending verb
requires `--agent` (or `$WF_AGENT`) — receipts, attempts, and commit
trailers need it before claims and sessions exist (reviewer finding; those
arrive in Slice 2 as coordination, never as the source of identity). Every
command: `--json` output mode; no interactive prompts, ever; outputs small by default (tails, not dumps; digests, not
bodies). Idempotent where the operation allows it: `init`, `export` always;
`open`/`claim` re-run with identical arguments succeed as no-ops;
chain-appending verbs are compare-and-swap instead (a stale writer is
refused, which is the correct behaviour, not a retry bug).

**Exit codes** (loopflow's, unchanged): `0` ok — including a recorded denial;
`1` refused or failed, with the first failed precondition named; `2` evidence
integrity — something did not re-hash to what was recorded; `3` claim held by
another agent; `64` usage.

`wf next --json` sketch:

```json
{"wo": "WO-012", "kind": "tdd@1", "stage": "red",
 "satisfied": [], "missing": ["receipt.red"],
 "advances": ["wf", "red", "WO-012", "--", "cargo", "test", "-p", "product-core"],
 "claim": {"agent": "fable-5", "expires_at": "2026-08-20T15:04:05Z"}}
```

---

## 5. Git integration

**When commits fire.** Every chain-appending verb (`open`, `red`, `green`,
`check`, `outcome`, `marker`, `advance`) auto-commits by default: export
updated, staged, committed, `commit.landed` appended. `--no-commit` defers;
an explicit `wf commit` flushes the batch. Receipt recording and gate-stage
completion therefore always commit — the minimum required — and the default
is every step, keeping commits small and frequent. Steady state: each
auto-commit sweeps in the previous commit's `commit.landed` event, so
exactly one bind is pending at any moment — the tip's, witnessed by CI
(§3.6) until the next append lands it. A `wf` auto-commit stages only
export paths, and its `Source: generated: wf` covers the serialization
only — content provenance rides in the events: every chain-appending verb
accepts a repeatable `--source`, recorded in the event payload and emitted
as additional commit trailers (reviewer finding: exports embed owner text,
external artifacts, and other tools' outputs; the serializer is not their
origin).

**Message format.**

```
wf(WO-012/red): receipt.red cargo-test

WO: WO-012
Stage: red
Event: 14 3f9c2a1b
Source: generated: wf
Co-Authored-By: <agent from the active claim/session>
```

The attribution trailer names the specific agent that did the work, per this
repo's attribution policy (fair per-agent attribution; author remains the
owner's identity, so public stats are unaffected).

**Where commits land** (D2, decided): the `lane` branch takes the
auto-commits, merged to protected `main` by PR at ratification.

---

## 6. Extensibility

**Gate kinds are data.** Source of truth is tracked JSON under
`gate-kinds/<kind>.json`; `wf init` syncs it into `gate_kinds`; `wf verify`
checks DB and files agree. A spec names, per version: ordered stages, the
receipts/outcomes required to leave each stage, allowed outcome values, the
test scope (globs), and registered checks (name, class, argv template).
Illustrative:

```json
{"kind": "tdd", "version": 1,
 "stages": [
   {"name": "spec",   "leave_requires": []},
   {"name": "red",    "leave_requires": ["receipt.red"]},
   {"name": "impl",   "leave_requires": ["receipt.green"]},
   {"name": "review", "outcomes": {"accepted":"advance","finding":"impl","denied":"terminal"}},
   {"name": "ratify", "actor": "owner", "outcomes": {"ratified":"terminal","denied":"terminal","aborted":"terminal"}}],
 "test_scope": ["crates/*/tests/**", "crates/*/src/**/*_test.rs"],
 "harness_scope": ["Cargo.toml", "Cargo.lock", ".cargo/**", "rust-toolchain*"],
 "checks": [
   {"name": "cargo-test", "class": "build",    "argv": ["cargo","test","--workspace"]},
   {"name": "audit",      "class": "security", "argv": ["cargo","audit"]}]}
```

- **New gate kind** (conformance, CI stage…): add a JSON file, `wf init`.
  No engine change — the engine reads the registry.
- **New check/tool**: add an entry to a spec's `checks`. Scanners are argv
  templates, never hardcoded.
- **Versioning**: specs are versioned; a work order pins
  `(kind, version, spec_sha256)` at `open` — digest recorded in the chain's
  opening event, spec document in the CAS. An in-place edit of a registered
  version is therefore detectable, and the engine refuses to act on a spec
  whose digest no longer matches the pin: the chain stays the sole
  authority, the registry a convenience mirror (reviewer finding). New
  versions are new entries; old ones are never edited.
- **Migrations**: numbered forward-only SQL in `migrations/`, applied by
  `init` inside a transaction, recorded in `schema_migrations`, never
  rewritten once applied. The export carries a format version in `meta.json`;
  `--rebuild` applies all migrations, then imports.

"More to come" is the design input: a new gate kind, check class, or event
type must require **zero** engine changes — data plus, at most, a migration.

---

## 7. CI — GitHub Actions (deliverable)

CI is a **verifier, never a decider**: it re-checks recorded evidence and
fails on mismatch; it originates no state and writes nothing to the DB. The
runner rebuilds its own throwaway DB from the tracked export; nothing flows
back. The YAML stays thin — every step is a `wf` call (stdlib only: no pip
install step exists or is needed).

**Day one — `.github/workflows/wf-verify.yml`:**

```yaml
# Job names are the check names the ruleset requires — renaming a job here
# means updating the ruleset by hand.
name: wf
on:
  pull_request:
  push:
    branches: [main]
permissions:
  contents: read
jobs:
  verify:
    name: "WF:verify"
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }   # cross-bind checks walk history
      - run: python3 ops/devlane/workflow/wf.py init --rebuild
      - run: python3 ops/devlane/workflow/wf.py verify --deep --json
  tests:
    name: "WF:tests"
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: cargo test --workspace
```

Tests run server-side from day one but as their own job, never inside
`WF:verify`: during a red stage a failing suite is the *recorded, correct*
state of the tree, so folding tests into the required verifier would turn
it into a merit gate that blocks legitimate red-stage commits (reviewer
finding). Which contexts are required beyond `dev: verify` is D9.

`verify --deep` re-checks, for every work order: chain hashes and links;
rebuild fidelity (payload bytes = export lines); **red receipts genuine** —
the frozen-set manifest re-hashed from the blobs in the tree at the recorded
commit matches the recorded digest; **green verified against the frozen
set** — the manifest at the green receipt's recorded commit equals the frozen
hash; commit cross-binds and the append-only prefix walk (§3.6); gate-kind
registry digests against chain pins (§6). Tree re-hashing applies to
receipts recorded with a clean tree; a `tree_dirty` receipt is verifiable
only against its recorded output hashes — D3's trade-off, recorded here
rather than silently decided.

**Growth — the build gate sequence as jobs**, added as gate kinds register:

```yaml
  gates:
    name: "WF:gates"
    needs: verify
    strategy:
      matrix:
        check: [cue-vet, relational-validator, cargo-test, audit]
    steps:
      - uses: actions/checkout@v4
      - run: python3 ops/devlane/workflow/wf.py init --rebuild
      - run: python3 ops/devlane/workflow/wf.py check --verify-only ${{ matrix.check }}
```

`check --verify-only` runs the registered command with a meaningful exit and
records nothing — the same gate command, server-side, no state originated.
(Later: derive the matrix from `wf status --checks --json`.)

**Branch protection**: `main` requires `WF:verify` green. Required status
checks make direct pushes to `main` effectively impossible, which forces the
lane-branch shape in D2 — auto-commits land on the lane branch, `main` moves
by PR.

---

## 8. Tracking policy

**Decided (D1): commit the export; gitignore the `.db`; rebuild is a
command.**

Why: the chain's export is one canonical-bytes JSON line per event —
append-only files whose git diffs are pure appends: reviewable, self-
verifying (every line carries its hash), and merge-trivial. A committed
SQLite binary is none of those, churns on every write, and can drift from
the chain silently; the export *is* the evidence, byte-for-byte the hashed
payloads. Rebuild is deterministic because the payload column stores exactly
the export line.

Not exported, by design: `claims` and `sessions` — transient coordination,
not evidence (loopflow's stance); losing them on rebuild is correct.
Attempts are evidence and are exported.

```
ops/devlane/workflow/
  PLAN.md              # this document
  wf.py                # entry point (small stdlib modules beside it)
  migrations/00NN_*.sql
  gate-kinds/*.json    # gate-kind registry, source of truth
  meta.json            # export format version
  chains/<wo>.jsonl    # tracked: event chains, payload bytes verbatim
  attempts/<wo>.jsonl  # tracked: attempt/refusal records
  objects/<aa>/<digest># tracked: CAS (full outputs, manifests)
  wf.db                # gitignored: rebuilt via `wf init --rebuild`
```

---

## 9. Integration seams (later phases — seams only)

- **Read-only MCP status server.** Serves `status`/`next`/`log` as MCP
  resources over the same `--json` surface. Read-only by construction: it
  imports the query layer, not the append layer. (CI is not a seam; it is
  §7.)
- **Serena diagnostics feed.** Diagnostics snapshots recorded as
  `receipt.check` receipts (class `diagnostics`) — same evidence shape, no
  new machinery.
- **Per-agent visibility.** Receipts, attempts, and claims already carry
  `agent`; per-agent views (and any future agent-facing stats or feedback
  surface) are projections over existing records — noted so the schema
  leaves room; not designed here.

---

## 10. Vocabulary wall

Dev-lane terms — *work order, gate, stage, receipt, red, green, frozen set,
chain, claim, marker, packet, wf* — exist only under `.dev/`, in the root
dev-lane surfaces (`AGENTS.md`, `CONTRIB.md`), and in dev-lane commits and
PRs. They never appear in the product's design artifacts, contracts,
or specs: the product describes itself in its own vocabulary, and process
words in a design artifact are scope leakage. Cheap enforcement once `check`
exists: a registered check greps the design tree for the reserved terms.

---

## 11. Phasing

**Slice 1 — usable in one day.** Work orders + TDD gate + commits + CI
verify + `next`:

- `init` (schema, migrations, seeded `tdd` and `artifact` gate kinds),
  `open`, `red`, `green`, `advance`, `outcome`, `next`, `status`, `commit`,
  `verify` (incl. `--deep`), `export` — each chain-appending verb carrying
  `--agent`/`$WF_AGENT` identity from the first commit.
- Chain + canonical hashing + attempts recording (refusals recorded from the
  first day — the wrapper is the verbs themselves).
- `ci-dev.yml` (`dev: verify` + app jobs) + lane branch + `main`
  protection requiring `dev: verify`; the rest is D9.

**Slice 2.** `claim`/`release` + `sessions`; `check` + the security class +
seeded security checks; `WF:gates` matrix job; `marker`.

**Slice 3.** `log` polish, per-agent projections, MCP read-only server,
Serena diagnostics class, registry-driven CI matrix.

---

## 12. Decisions (ratified by the owner, 2026-08-20)

Ratified with PR #1's three-reviewer rounds behind them; any can be
revisited by amending this plan through the normal gate process.

- **D1 — tracking policy: export-committed.** JSONL export in git; `wf.db`
  gitignored, rebuilt by `wf init --rebuild` (§8).
- **D2 — auto-commits land on the `lane` branch**, merged to protected
  `main` by PR at ratification.
- **D3 — dirty-tree receipts are refused.** `--allow-dirty` opts out and
  records `tree_dirty: true`, accepting hash-only re-verification.
- **D4 — CAS retention: full outputs.** Revisit if the repo bloats.
- **D5 — auto-commit default: on** for every chain-appending verb. Adopted
  as the standing recommendation, not separately put to the owner — flag
  freely.
- **D6 — work-order IDs: `WO-NNN`**, zero-padded, allocated by `open`.
- **D7 — attribution trailer** as seeded by `AGENTS.md` §Attribution:
  specific model name, vendor noreply address.
- **D8 — day-one security seed: still open.** Non-blocking (Slice 2);
  decide before the first `security`-class stage runs.
- **D9 — required CI contexts: `dev: verify`** (amended 08-25).
- **D10 — owner-outcome attestation: the protected-`main` merge** (§3.5).

---

## 13. Execution brief (Slice 1)

For the implementing agent. The contract is §§3–8; this section is the
build order and the bar. Work on branch `workflow/slice-1`, PR to `main`,
under `AGENTS.md` rules: your own model name in the trailers, every commit
sourced.

**Layout.** `wf.py` (entry point, argparse subcommands) beside small
stdlib-only modules: `canonical.py` (canonical JSON + digests), `store.py`
(sqlite3 + migrations), `chain.py` (append / verify / project),
`receipts.py` (red/green/manifest), `gitio.py` (export, commit,
cross-bind); `migrations/` and `gate-kinds/` as data; `tests/` on stdlib
`unittest`, run via `python3 -m unittest discover ops/devlane/workflow/tests`.

**Build order** — each step lands with its tests:

1. `canonical.py` — `dumps()` byte-identical to
   `json.dumps(obj, sort_keys=True, separators=(",",":"))`; sha256 helpers.
2. `store.py` + `0001_foundation.sql` — the §3.2 tables; `init` idempotent.
3. `chain.py` — append (compare-and-swap on seq/prev, monotonic time),
   verify, project; refusal vs integrity exactly per §3.1.
4. `open` / `next` / `status` — the first end-to-end path.
5. `red` / `advance` / `green` — manifest over test+harness scope, freeze
   seal on advance, same-argv rule, D3 dirty handling.
6. `outcome` — effects from the spec; owner-reserved outcomes record
   without effect (D10).
7. `gitio.py` — export files, auto-commit (D5), §5 message format,
   `commit.landed`, `--agent`/`$WF_AGENT` and `--source` flow-through.
8. `verify --deep` — receipt re-verification, cross-binds, append-only
   prefix walk, registry digests against pins.
9. `.github/workflows/wf-verify.yml` (§7) — the PR that delivers Slice 1
   arrives with `WF:verify` green on itself.

**The bar (acceptance).** The finishing PR demonstrates each item as a
command in its Evidence section:

- §4's exit-code map holds on every verb; `--json` everywhere; zero
  interactive prompts (`grep -rn 'input(' ops/devlane/workflow` finds none).
- `wf red` refuses a passing command; records a failing one.
- `wf green` refuses a changed manifest, a changed argv, and a dirty tree;
  records on the clean match.
- a full walk — open → red → advance → green → advance → outcome →
  terminal — then `wf verify --deep` green, then `wf init --rebuild` from
  the export reproduces byte-identical chains.
- **planted-fault suite** — the non-negotiable one: a corrupted payload
  byte, a dropped row, a rewritten indexed column, a broken prev link, a
  non-canonical re-encoding, an edited gate-kind spec, and a voided frozen
  set are each *detected* (exit 2 or refusal). A verifier proven only on
  clean data is worse than none — it converts "nobody looked" into "we
  checked". Every planted fault proves it landed (differs from clean,
  still recognisably the fixture) before any assertion runs.

**Out of scope for Slice 1** (§11): claims/sessions, `check` and the
security class, markers, `WF:gates`, MCP/Serena seams.
