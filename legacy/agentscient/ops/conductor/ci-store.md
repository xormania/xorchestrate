# The CI store — contract

Standing: ratified mandate, D-OPS-6 (owner: "data is gold", "CI data",
"go for the CI store", 2026-09-01). Source of demand: the flakiness
question of the same day cost an API spelunk that should have been a
query, and the runner scale-up was sized from check durations read by
hand. The forge only retains CI data; this store accumulates it — the
capture-first doctrine (I-010) applied to the gates themselves.

## Conventions

This instrument inherits `ops/conductor/instrument-conventions.md`:
the refusal wire, identity rule, four-class exits, instrument shape,
and lint duty apply as written there.

## Instrument

`python3 ops/conductor/tools/ci.py <verb> …`, stdlib only (sqlite3,
argparse, json; subprocess only for `gh`, every call under a bounded
timeout). Database: `$XDG_STATE_HOME/agentscient/ci.db` (else
`~/.local/state/agentscient/ci.db`), created on first ingest. Ingestion
writes and requires `WF_AGENT`; reports read and refuse when the store
file is absent — an unperformed read is not an empty history (the
register's store rule).

## The schema

Keyed for a multi-forge future — Gitea's act_runner rows land in the
same tables when local CI arrives:

```sql
CREATE TABLE IF NOT EXISTS runs(
    forge TEXT NOT NULL, repo TEXT NOT NULL,
    run_id INTEGER NOT NULL, attempt INTEGER NOT NULL,
    name TEXT NOT NULL, event TEXT, head_sha TEXT NOT NULL,
    branch TEXT, status TEXT, conclusion TEXT,
    created_at TEXT, started_at TEXT, completed_at TEXT,
    duration_s INTEGER, ingested_at TEXT NOT NULL,
    PRIMARY KEY (forge, repo, run_id, attempt));
CREATE TABLE IF NOT EXISTS checks(
    forge TEXT NOT NULL, repo TEXT NOT NULL,
    check_id INTEGER NOT NULL, name TEXT NOT NULL,
    head_sha TEXT NOT NULL, status TEXT, conclusion TEXT,
    started_at TEXT, completed_at TEXT, duration_s INTEGER,
    ingested_at TEXT NOT NULL,
    PRIMARY KEY (forge, repo, check_id));
```

`duration_s` is `completed_at - started_at` when both are known, else
NULL. Ingestion upserts by primary key (`INSERT OR REPLACE`), so
re-ingesting is free and a run first seen in progress gains its
conclusion on the next ingest — idempotence is the contract, not a
courtesy.

## Verbs

- `ingest [--repo OWNER/NAME] [--limit N]` — pull the newest N
  workflow runs (default 200, gh pagination bounded by it) via
  `gh api repos/<repo>/actions/runs`, upsert them with forge
  `github`, then pull check runs for each distinct `head_sha` seen in
  this ingest via `gh api repos/<repo>/commits/<sha>/check-runs` and
  upsert those. Without `--repo`, the repository is derived from
  `gh repo view`; failure to derive refuses class `repo`. Prints
  `ingested runs=R checks=C shas=S` and exits 0.
- `flakes [--days D]` — pairs `(name, head_sha)` in the window whose
  completed check runs carry **both** `success` and `failure`
  (`cancelled` and `skipped` are not verdicts and join no pair), one
  line each with the conclusion counts and last completed time,
  newest first. Exit 1 (Finding) when any flake is listed, 0 clean.
- `durations [--days D]` — per check name over the window: count, p50,
  p95, max of `duration_s` (completed runs with a duration), sorted
  p95 descending. Exit 0.
- `failures [--days D]` — per check name: completed count, failures,
  failure rate **as a percentage** to two decimals (a two-decimal
  fraction erases sub-percent rates), sorted by rate then count,
  descending. Exit 0.
- `report [--days D]` — totals (runs, checks), the window actually
  covered (min/max `created_at` present), the three slowest checks by
  p95, the flake count, and the top failure rate — one compact block.
  Exit 1 (Finding) when flakes exist in the window, else 0.

`--days` defaults to 14 and filters on the row's `created_at` (runs)
or `started_at` (checks).

## Refusals

Beyond the inherited wire and classes:

| refusal | fires when |
|---|---|
| repo | no `--repo` and the repository cannot be derived from `gh repo view` |
| gh-missing | `gh` is not on PATH at ingest |
| gh-error | gh exits nonzero — its stderr quoted in found, newlines normalized (the land lever's rule) |
| json | gh's output is not the JSON shape asked for |
| store | a report verb runs while the store file is absent |
| window | `--days` is not a positive integer |

## Lint

Inherited. The tool shells to `gh` — it earns its THIS-FILE-ONLY row
(S603), stated in the PR.
