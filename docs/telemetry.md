# Active telemetry

Telemetry extends the active `xorchestrate/` application. The collector ships
with it in [`xorchestrate/adapters/telemetry_collector/`](../xorchestrate/adapters/telemetry_collector/);
there is no separate installation extra.

## Install and enable

```sh
uv pip install --python python3 -e '.[test]' -r .github/requirements-ci.txt
```

Until xcontracts is published on PyPI, install with uv: it reads the pinned
xcontracts git source in `pyproject.toml`, which plain pip ignores (pip stops with
`No matching distribution found for xcontracts`). Once xcontracts is on PyPI,
`python3 -m pip install ...` works again.

Use a Python build whose linked SQLite includes the WAL-reset fix (3.51.3, or a
fixed backport: 3.50.7 or later in 3.50, 3.44.6 or later in 3.44). Check the
library linked to Python, rather than a separate sqlite3 executable. The SQLite
corpus adapter refuses an unfixed library; the application then continues with an
explicit unavailable telemetry state. CI uses uv 0.13.7 and Python 3.12.16, and
`doctor` reports the selected interpreter's SQLite as `telemetry_sqlite`.

Telemetry is always on for a [project](projects.md#telemetry): without a
`telemetry` section the local collector writes to
`~/.xorchestrate/projects/<project>/telemetry` with the project name as
`project_id`, the instance id as `collector_id` and `dataset_id` `default`;
`{"enabled": false}` turns it off. The
[telemetry example project](../examples/telemetry/.xorchestrate/config.json)
changes only the dataset and artifact threshold of those defaults with a
`telemetry` section that has no `adapter`. An explicit section with an
`adapter` is used as written; its relative paths resolve against the instance
directory. Separate hosts use separate collector lineages (each machine has its
own instance id); do not share a live WAL database over a network filesystem.

Inside a set-up project:

```sh
xorchestrate call create_work --json '{"goal":"Retain an inspectable work episode"}'
xorchestrate call telemetry_status --json '{}'
xorchestrate call telemetry_events --json '{"limit":20}'
```

Use the persistent `run` or MCP `serve` interface for native sessions. The same
application operations are available through both input adapters.

## Configuration options

The top-level `telemetry` section takes `adapter` (built-in `local`), `options`
and an optional boolean `enabled`. `enabled: false` constructs no collector. When
the section is absent, or explicitly `null`, no collector is constructed unless
the caller of `composition.build()` supplies `TelemetryDefaults` (an absolute
runtime root, project id, collector id and a dataset id that defaults to
`default`, validated like the explicit options); the local collector is then
constructed with those values. The command-line entry points do not supply
defaults yet. Unknown option names are rejected.

If construction fails, the application continues with an explicit unavailable
telemetry state. `telemetry_status` then reports `reason:
configuration_unavailable` and a bounded `cause`, for example `{"code":
"unknown_option", "detail": "profile"}`, `{"code": "unknown_adapter", "detail":
"corpus: <name>"}`, `{"code": "adapter_unavailable", ...}`, `{"code":
"invalid_option", "detail": "<validation message>"}` or `{"code":
"sqlite_version", "detail": null}`. The detail names options or adapters, never
option values.

### Migrating from the `profile` option

The separate collector's TOML `profile` option has been removed; a configuration
that still uses it reports `cause.code` `unknown_option` with detail `profile`.
Move the profile's settings into inline options: `runtime_root`, `project_id`,
`dataset_id`, `collector_id`, `producer_id` and `capture_lock_timeout_ms`, plus
`database` and `artifact_root` (or the `corpus`/`artifacts` selections).
Profiles defaulted the database to `live.sqlite3`, whereas inline options default
to `events.sqlite3`: set `database` explicitly (for example `"database":
"live.sqlite3"`) to keep using an existing corpus. Keep the same dataset and
collector identities, or the corpus refuses the collector as a different lineage.
A profile's `runtime_root` was relative to the profile file; the inline option is
relative to the configuration file's directory. A profile's `[artifacts] enabled =
false` becomes `"artifacts": {"adapter": "filesystem", "enabled": false}`.

| Option | Default | Validation and meaning |
| --- | --- | --- |
| `runtime_root` | required | Corpus root, resolved against the configuration directory; no symbolic-link components. |
| `project_id`, `dataset_id`, `collector_id` | required | Nonempty text identities for the collector lineage. |
| `producer_id` | `xorchestrate` | Nonempty text. |
| `corpus` | `{"adapter": "sqlite"}` | Corpus storage selection; see below. |
| `artifacts` | `{"adapter": "filesystem"}` | Artifact storage selection; see below. `"enabled": false` keeps large payloads inline. |
| `database`, `capture_lock_timeout_ms` | `events.sqlite3`, `250` | Shorthand for the `sqlite` corpus options; cannot be combined with `corpus`. |
| `artifact_root` | `artifacts` | Shorthand for the `filesystem` artifact `root`; cannot be combined with `artifacts`. |
| `export_root` | `exports` | Relative path inside the runtime root; snapshot and restore destinations are new children of it. |
| `snapshot_timeout` | `5.0` | Seconds; greater than 0 and at most 3600. |
| `artifact_threshold_bytes` | `65536` | Positive integer; larger payloads are stored as content-addressed artifacts. |
| `known_secrets` | `[]` | List of nonempty strings redacted from payloads and context; excluded from the configuration identity. |
| `sources` | `application`, `native`, `capability`, `evaluator`, `process` | List of source names reported as configured. When set explicitly, observations from other sources are not recorded (`source_disabled`). |
| `operational` | none | Optional operational-signal export; see below. |
| `snapshots` | `{"triggers": ["before_upgrade"], "archive": false}` | When the operator snapshots the corpus; see [snapshots at chosen moments](#snapshots-at-chosen-moments). Unknown triggers are refused. Excluded from the configuration identity. |

The `operational` object accepts only these fields:

| Field | Default | Validation and meaning |
| --- | --- | --- |
| `sink` | none | Explicit nested sink selection: `{"adapter": "local", "options": {"capacity": 1000}}`, `otlp`, or a trusted installed `module:factory`. Cannot be combined with the flat `adapter`, `endpoint`, `timeout` or `capacity` fields below. |
| `adapter` | `local` | `local` keeps a bounded in-process signal buffer; `otlp` exports OTLP/HTTP JSON. |
| `endpoint` | none | Required for `otlp`: an `http` or `https` base URL with a host and no query, fragment or user information. A trailing `/` is removed and `/v1/traces`, `/v1/metrics` or `/v1/logs` is appended per signal. |
| `timeout` | `1.0` | `otlp` request timeout in seconds; greater than 0 and at most 60. No retry. |
| `capacity` | `1000` | `local` buffer size (positive integer); the oldest signal is dropped when full. |
| `trace_sample_rate` | `1.0` | Number from 0 to 1; applies to spans only. |
| `max_pending` | `64` | Positive integer bound on queued operational observations. |
| `max_bytes` | `8388608` | Positive integer bound on queued operational bytes. |
| `drain_timeout` | `0.05` | Positive number of seconds to wait for queued observations to drain. |

An invalid `operational` object marks operational export unavailable in
`telemetry_status`; it does not disable the local corpus.

The flat `adapter`, `endpoint`, `timeout` and `capacity` fields remain supported.
With `sink`, put those adapter-specific options inside `sink.options`; sampling
and dispatch limits remain in `operational`. For example:

```json
"operational": {
  "sink": {"adapter": "my_package.telemetry:signals", "options": {"destination": "diagnostics"}},
  "trace_sample_rate": 0.25,
  "max_pending": 64,
  "max_bytes": 8388608,
  "drain_timeout": 0.05
}
```

Composition resolves the sink through the same trusted adapter resolver as the
corpus and artifacts. Its factory is called as `factory(options, scope)`, where
`scope` is the collector's `CollectorScope`; it returns an
[`OperationalSink`](../xorchestrate/adapters/telemetry_collector/ports.py) with
`emit(signal)`. Signals have already passed the shared safe metadata mapping and
span sampling; source prompts and results are not supplied to the sink. Factories
and sinks bound their own construction and emission work. The interface provides
no background-resource or shutdown lifecycle; the collector owns its existing
bounded delivery worker and drain. A factory failure, missing `emit`, or emission
failure cannot remove local corpus observations or change an application result.
Omit `operational` to disable export; a disabled nested sink is refused.
`telemetry_operational_sink` is an internal nested kind, not a project section or
a public xcontracts kind.

Project approval summaries and `doctor` list external operational sink factories
as `repository_code` without importing them. As with other configured factories,
source outside `.xorchestrate/` remains outside the configuration approval hash;
see [trust and approval](projects.md#trust-and-approval).

### Storage adapters

Corpus and artifact storage are selected like other adapters, with `adapter`, an
optional `options` object and an optional boolean `enabled`. A name is a built-in
alias or a trusted installed `module:factory`; the factory receives its options
and a `CollectorScope` (runtime root, dataset and collector identities) and
implements the `TelemetryCorpusStorage` or `TelemetryArtifacts` port in
[`ports.py`](../xorchestrate/adapters/telemetry_collector/ports.py). An unknown
name or option is refused, also for a selection with `"enabled": false`.

```json
"telemetry": {"adapter": "local", "options": {
  "runtime_root": "../telemetry-runtime", "project_id": "example", "dataset_id": "default",
  "collector_id": "workstation-1",
  "corpus": {"adapter": "sqlite", "options": {"database": "events.sqlite3", "capture_lock_timeout_ms": 250}},
  "artifacts": {"adapter": "filesystem", "options": {"root": "artifacts"}}
}}
```

| Adapter | Options | Behavior |
| --- | --- | --- |
| `sqlite` corpus | `database` (relative path inside `runtime_root`, default `events.sqlite3`), `capture_lock_timeout_ms` (0 to 60000, default `250`) | The `agent-telemetry-v1` format (`user_version` 1; tables `metadata`, `events`, `links`, `artifacts`, `cursors`; immutable events). WAL journaling with `synchronous=FULL` is fixed, because the corpus's durability depends on it. Corpora written by the earlier standalone collector open unchanged. |
| `filesystem` artifacts | `root` (relative path inside `runtime_root`, default `artifacts`) | Content-addressed `sha256/<prefix>/<digest>` files, installed and synced before an observation refers to them. |

The database and artifact paths cannot overlap or traverse symbolic links.
Snapshot bundles are SQLite/file closures, so `telemetry_snapshot`,
`telemetry_verify` and `telemetry_restore` require the `sqlite` corpus; another
corpus adapter answers them with `telemetry_unsupported`. With artifacts
disabled, a snapshot succeeds only while the corpus refers to no captured
artifacts; otherwise it fails with `telemetry_integrity` and the bundle stays
marked incomplete.

The `export_root` is contained in `runtime_root` but is not checked for overlap
with the database or artifact paths.

## Snapshots at chosen moments

Inside a project, the operator layer snapshots the corpus at moments the
`snapshots` option selects, not on a timer:

```json
"telemetry": {"options": {"snapshots": {"triggers": ["before_upgrade", "before_sync"], "archive": true}}}
```

| Trigger | When |
| --- | --- |
| `before_upgrade` (default) | after [`xorchestrate upgrade`](upgrade.md) stops the server and before the release procedure starts |
| `before_sync` | before a render that changes the rendered configuration (an approved change, an overlay or companion policy change) |
| `before_approve` | before a newly approved tracked configuration is first rendered |
| `manual` | always available: `xorchestrate telemetry snapshot [--destination NAME] [--archive]` |

The running configuration's option decides, and its corpus is snapshotted, so
a change that selects a trigger applies from the render after it. A render that
is both a sync and a newly approved configuration takes one bundle naming both
triggers. Bundles are written to the export root as `<trigger>-<UTC time>`;
the last 20 triggered snapshots are listed in `instance.json` `snapshots`.
A `before_sync` or `before_approve` snapshot is taken only while the new
configuration also selects that trigger, so a change that deselects a failing
trigger takes effect. A selected snapshot that fails refuses the render (or the
upgrade) with `snapshot_failed`, naming the triggers, the bundle path and the
cause, and leaves the previous rendered configuration in place. The global
option `--skip-snapshots` (for example `xorchestrate --skip-snapshots describe`
or `xorchestrate --skip-snapshots upgrade`) proceeds without the snapshot and
records it as skipped in `instance.json` `snapshots` (or the upgrade report);
`up` and `connect` pass it on to the server they start.
`"triggers": []` selects none. A
configuration with telemetry disabled takes none. An unknown trigger name,
including `manual`, is refused when rendering (`configuration_error`) and by the
collector (`cause` `{"code": "invalid_option", "detail": "snapshots"}`).

### Archiving a bundle in the companion

`xorchestrate telemetry archive BUNDLE` copies a complete, verified bundle (a
name or path inside the export root) into the [companion](projects.md), through
the `Companion` port as `telemetry` records: the git-directory adapter writes
`<companion>/telemetry/<project>/<instance>/<bundle_id>/`. `archive: true`
archives every triggered and manual snapshot. The archived `manifest.json`
gains an `archive` object:

| Field | Value |
| --- | --- |
| `companion_commit` | the companion's git `HEAD` when archived (`null` without one); uncommitted overlay changes are not in it |
| `config_sha256` | the approval hash of the tracked configuration last rendered |
| `overlay_sha256` | SHA-256 of the project's overlay record as of the last render (recorded in `instance.json`), `null` when absent |
| `release` | the release the instance runs |
| `project`, `instance_id`, `archived_at` | where and when it was archived |

The `archive` object is not part of the bundle identity, so the archived copy
keeps its `bundle_id` and still verifies and restores (copy it to a private
directory first: the corpus adapter requires private files). Every member is
read back through the port and the reassembled bundle is verified before its
manifest is written; a bundle without a manifest is incomplete. Archiving an
already archived bundle only reports it. Nothing is committed: review and
commit the files yourself. Companions created by `companion init` track
`telemetry/**/*.sqlite3` with Git LFS through `.gitattributes`; run
`git lfs install` in the companion once (it is not needed to write archives).

Bundles contain the redacted corpus: only `known_secrets` values and explicit
credential fields are protected. A secrets scan of the companion before
publishing archived bundles is not provided; keep the companion private.

## Operations

Telemetry is available through a regular capability provider. Select it in the
project configuration, independently of the collector's `telemetry` section:

```json
{"providers": {"telemetry": {"adapter": "telemetry"}}}
```

The provider name is configurable: a selection named `observations` exposes
`observations/events`. `enabled: false` disables that provider without stopping
automatic collection. `options.operations` can select a nonempty list of exposed
operation names; omitted, it exposes all nine below. Discovery and invocation use
the ordinary provider guardrail checks. Existing work, session, operation and
bundle-closure authorization still applies within the service.

| Capability | Execution |
|---|---|
| `telemetry/status`, `telemetry/events`, `telemetry/payload` | Fresh authorized query |
| `telemetry/usage`, `telemetry/compare`, `telemetry/verify` | Fresh authorized query |
| `telemetry/snapshot`, `telemetry/restore`, `telemetry/report` | Keyed effect with a payload-free completion receipt |

```bash
xorchestrate call capabilities --json '{"provider":"telemetry"}'
xorchestrate call invoke --json '{"capability":"telemetry/events","arguments":{"limit":20}}'
xorchestrate call invoke --json '{"capability":"telemetry/snapshot","arguments":{"destination":"episode-1"},"idempotency_key":"snapshot-episode-1"}'
```

Queries return `{"result": ...}` and recheck current authority on each call. They
do not copy their result into operation records or acceptance evidence. The three
effects require an idempotency key. Their first response contains `result` and
`operation_id`; repeating a completed key returns only its completion status and
`result_retained: false`. A lost response never causes an uncertain effect to run
again. Inspect the corpus or bundle with a fresh query to recover its current
authorized view.

These defaults come from trusted installed provider code through the shared
capability execution policy. They omit application and native-tool capture of
telemetry requests/results, preventing these envelopes from recursively copying
the corpus. A trusted `capability_execution` configuration can override the
defaults; choosing full retention or observation deliberately permits those
copies. This policy does not control a harness's own transcripts.

The existing `telemetry_status`, `telemetry_events`, `telemetry_usage`,
`telemetry_compare`, `telemetry_payload`, `telemetry_snapshot`, `telemetry_verify`
and `telemetry_restore` application actions remain compatibility entrypoints to
the same service. Their schemas and response shapes are unchanged. The operator
commands for snapshots and companion archives retain their existing scope.

### Caller reports

`telemetry/report` accepts `name`, an object `payload`, and optional `work_id` or
`session_id`. For example:

```bash
xorchestrate call invoke --json '{"capability":"telemetry/report","arguments":{"name":"manual-check","payload":{"outcome":"pass"}},"idempotency_key":"manual-check-1"}'
```

The service writes a fixed `telemetry.reported` event with `basis: reported` and
the actual caller's identity. Names and payloads remain reported content; they
cannot declare an observed runtime event, choose another actor or producer, or
create acceptance evidence. Resource-scoped reports require current authorization
for that scope. A configuration with an explicit source filter must include
`telemetry` to accept reports. The result contains the collector's actual capture
status and receipt, including disabled or unavailable states.

## Capture and reads

Collection retains rich semantic inputs/results after explicit credential
treatment. Large payloads use content-addressed artifacts referenced by SQLite.
Retained bytes and parsed/projected views have separate identities and range
metadata. Unknown, zero and empty values remain distinct. A corpus receipt means
durable local capture; it is not task completion, acceptance or remote publication.

Source status distinguishes configured sources, observations actually seen,
disabled collection and unavailable capture. Absence of events leaves coverage
unknown. Reads do not prove that an agent understood delivered content.

Use `telemetry_events` for bounded pages and retain the returned continuation and
cutoff when reading subsequent pages. Use `telemetry_payload` with an event ID for
bounded content reads. `telemetry_usage` retains source quantities and explicit
normalization/price availability. Source records remain available when an analysis
cannot assign a reliable total.

Telemetry reads apply the current work/session/operation authorization and the
existing typed result projection. Caller/provider payloads keep their opaque
meaning outside declared application-owned envelopes. Payload and export reads
must not become a route around the existing guardrail.

Optional capture failure preserves the observed action result, error and effect
count. Existing required operation/receipt/acceptance recording keeps its current
failure semantics. Invalid or unavailable optional telemetry remains visible
through status; it does not silently grant permission or retry an external action.

## Capability layers

Telemetry currently provides:

1. **Foundation:** active collection, optional composition, immutable context,
   current-authorized reads, payload retention and source/capture health.
2. **Producer capture:** evaluator request/response/error bodies, bounded process
   diagnostics, native observations and supported transcript sources.
3. **Preservation and observability:** verified snapshots/restore, analysis views,
   operational signals and explicit optional OTLP export.

Its tests run through the ordinary shared test-kit and hosted CI; telemetry does
not add a scheduler or a second proof runner.

## Native qualification and first workload

Deterministic protocol fixtures exercise real local transport and adapter
boundaries. Actual installed vendor behavior is qualified locally through the
[managed proof workflow](managed-proofs.md); record each driver's coverage
separately. A protocol fixture cannot supply that authenticated witness.

The proposed substantive workload is the existing private
[Symfony greeting/MCP fixture](https://github.com/uscient/agentscient-test-repo/tree/7d1abb3c4ac01451d83e1c1e12742b9fa2067561).
Keep its independent product checker outside the editable candidate and adapt its
task to current Xorchestrate orchestration. Its old role hierarchy and delivery
gates are historical policies, not current application requirements.

Capture the exact brief, candidate/configuration identities, actual tool/native
interactions, check outputs, outcome and source gaps. Preserve and restore the
episode, then answer which inputs and attempts produced the checked result.
A positive tool-improvement claim requires its own comparable baseline/candidate
evidence; useful telemetry does not depend on manufacturing such a result.

## References

- [Collector storage ports](../xorchestrate/adapters/telemetry_collector/ports.py)
- [Compatibility fixtures written by the earlier standalone collector](../tests/fixtures/telemetry/README.md)
- [SQLite WAL and fixed runtimes](https://www.sqlite.org/wal.html)
- [SQLite online backup](https://www.sqlite.org/backup.html)
- [Existing application architecture](hexagonal-architecture.md)
- [Shared testing framework](testing-framework.md)


## Preserve and investigate an episode

The preservation layer adds public snapshot, verification, restore and comparison
operations. Destinations are new children of the configured export root; existing
bundles are never overwritten:

```sh
xorchestrate call telemetry_snapshot --json '{"destination":"episode-1"}'
xorchestrate call telemetry_verify --json '{"destination":"episode-1"}'
xorchestrate call telemetry_restore --json '{"bundle":"episode-1","destination":"episode-1-restored"}'
```

Snapshot creation uses SQLite's completed backup result to derive the cutoff,
event count, source cursors and artifact closure. Verification reads the closed
database and checks the retained artifact bytes. Current authorization covers the
actual bundle closure for all three operations, including artifact-backed typed
results. Failure leaves an incomplete receipt and does not advertise a successful
preservation result.

The manifest's `software` block records the Xorchestrate version, the SQLite
version and `agent_telemetry_format: "agent-telemetry-v1"`. Bundles written while
the collector was a separate package also name that package's version; they still
verify and restore.

Public bundle responses are compact receipts with counts and an exact saved
manifest reference (path, SHA-256 and byte length). The complete manifest remains
in the bundle even when its link closure exceeds a normal MCP response frame.

Multi-record reads detect configured guardrail replacement and the built-in rule
guardrail's configuration revision. A custom guardrail whose decisions change
in place can expose `authority_revision()` for the same check. An unversioned
external policy has no coherent revision signal in the existing evaluate-only
port; per-record checks do not establish an atomic external-policy snapshot.

The bundle is independently inspectable in a fresh directory without the original
live WAL/SHM or absolute artifact paths. It preserves the telemetry corpus;
Xorchestrate's authoritative orchestration database has its separate owner.
Artifact APIs currently buffer one full object in memory. No streaming-blob
guarantee or power-loss qualification is implied.

`telemetry_usage` provides versioned interpretation of identified actual source
counters and keeps raw observations. Native transcript reingestion does not charge
the same explicitly identified message twice. Missing counter categories remain
unknown. Cost remains unknown until a suitable price schedule is selected.

`telemetry_compare` requires explicit baseline/candidate work or run selectors.
Its default reported-trial cohort is `trial.result`, `run.result` and
`candidate.result`. Explicitly selecting an assessment population does not turn
it into accepted work. The response identifies its population, source cutoff,
denominators, unknown outcomes and conditions; an empty cohort is inconclusive.
Large analysis responses use exact payload references or request a smaller page.

## Producer coverage and optional operational export

Producer observers use ordered bounded queues. Queue admission is deferred
capture, not a durable receipt. Source health/drain APIs distinguish pending work,
capacity, rejected captures and observer failure. Protocol input/output deadlines
remain independent of optional observers.

Codex/Grok capture the exposed protocol stream. Claude additionally ingests the
exact transcript path supplied by its native hook, with session/path checks,
complete-record acknowledgment and bounded consumed-prefix continuity
verification. Ingestion runs on hooks, polling, status and stopping; it does not
scan the host or start a background transcript-search service. A new driver
incarnation reingests the exact session file under a fresh source generation.

Partial, read-bound and malformed transcript records cannot safely establish
nested tool-result authority. They retain explicit suppressed source/range/parser
metadata without reversible content or a content digest. Partial records remain
pending; complete malformed records advance only after that gap is acknowledged.
Once a complete record can be classified, ordinary non-application content keeps
its protected bytes and application/unattributed tool replies keep their existing
typed-observation policy. Completing a private reply must not leave an earlier
readable fragment behind, including after resuming with no current tool catalog.

The table separates implemented boundaries, deterministic fixture coverage and
authenticated qualification. Fixture results do not establish installed vendor
behavior; each pending witness follows the [local qualification workflow](managed-proofs.md).
Protected protocol frames ([shared capture](../xorchestrate/adapters/telemetry_capture.py)) retain only bytes available within the configured
[transport bounds](../xorchestrate/adapters/jsonrpc.py), rather than every native
field or callback.
The [native tool fixtures](../tests/hexagonal/test_native_producer_capture.py)
exercise preservation of token-shaped arguments; authenticated usage accounting
remains pending.

| Source | Implemented capture boundary | Usage and source position | Deterministic fixture evidence | Authenticated witness | Remaining coverage gaps |
| --- | --- | --- | --- | --- | --- |
| [Codex](../xorchestrate/adapters/codex.py) | Serialized JSON-RPC requests, bounded stdout frames and drained stderr; emitted notification, tool/permission, turn and settings observations. | Exposed usage payloads retained; no Codex-specific normalized counter semantics. Frame ranges and request/session/incarnation links; no file cursor. | [Native identity/context/tool observations](../tests/hexagonal/test_native_producer_capture.py); [transport failures/bounds](../tests/hexagonal/test_producer_capture.py). | Pending installed/authenticated Codex run. | No separate transcript reader or verified native usage total; unexposed fields remain unknown. |
| [Claude](../xorchestrate/adapters/claude.py) | Gateway-delivered hook packets, emitted semantic observations and protected records from the exact hook-selected session JSONL file ([transcript reader](../xorchestrate/adapters/claude_transcript.py)). | Transcript usage uses `overwrite` with a stable message ID, otherwise `unknown`; [normalization](../xorchestrate/telemetry_views.py) preserves raw fields. Acknowledged complete records advance the live cursor; tails wait and changed prefixes create a gap/new generation. | [Hook identity/tool observations](../tests/hexagonal/test_native_producer_capture.py); [source checks, acknowledgment, tails, rotation, rewrites and bounded verification](../tests/hexagonal/test_transcript_producer_capture.py). | Pending installed/authenticated Claude Code run. | Missing/rejected sources stay explicit. Terminal/debug output is outside this transcript source; a new driver incarnation reingests from zero. |
| [Grok](../xorchestrate/adapters/grok.py) | ACP/JSON-RPC frames and drained stderr through the [shared session transport](../xorchestrate/adapters/process_sessions.py) and [ACP driver](../xorchestrate/adapters/acp.py); emitted session-update, tool/permission, interjection, completion and configuration observations. | Exposed usage payloads retained; no Grok-specific normalized counter semantics. Frame ranges and native session/incarnation/prompt links; no file cursor. | [Native identity/context/tool observations](../tests/hexagonal/test_native_producer_capture.py); [shared transport failures/bounds](../tests/hexagonal/test_producer_capture.py). | Pending installed/authenticated Grok Build run. | No separate transcript reader or verified native usage total; unexposed provider state remains unknown. |
| [Evaluator](../xorchestrate/adapters/evaluation.py) | TypeSafe serialized HTTP inputs, bounded response/error bodies and validation; tool-evaluator arguments/results plus its configured [CLI/MCP provider](../xorchestrate/adapters/capabilities.py). | Valid TypeSafe usage is `incremental` per fresh attempt; missing usage stays absent. Response ranges identify observed prefixes and known/unknown total length. Tool results have no universal usage-counter semantics. | [Local HTTP/process errors, bounds and credential treatment](../tests/hexagonal/test_producer_capture.py); [fixture-provider usage through public normalization](../tests/hexagonal/test_telemetry_workflow.py). | Pending authenticated live-provider witness. | [Local fixtures](../tests/hexagonal/support/producer_capture.py) establish adapter behavior, not internal provider accounting, hidden context or service latency. |

The optional [`operational` object](#configuration-options) inside local
telemetry options selects `{"adapter":"local"}` for bounded in-process diagnostics
or `{"adapter":"otlp","endpoint":"<base URL>"}` for OTLP/HTTP export. The
equivalent nested `sink` selection also accepts trusted installed factories.
`trace_sample_rate` affects operational spans only. Remote
export uses its own bounded dispatch; a blocked endpoint does not block the
observed action or remove durable local observations. No endpoint is inferred or
contacted by the default configuration.

The OTLP mapping pins protocol version 1.11.1 and Xorchestrate's own finite
operational names. It exports safe metadata, capture quantities and observed
request/terminal timing. It does not claim to expose a model's hidden reasoning,
all native context, or an unobserved service's internal latency.

Remote publication to a data branch, lakeFS, MLflow or OCFL remains a selected
deployment adapter rather than a default destination. Telemetry currently
provides a verified portable local bundle and a replaceable publication boundary
([`publish_bundle`](../xorchestrate/adapters/telemetry_snapshot.py);
[lost-acknowledgment, unverified-receipt and refusal checks](../tests/hexagonal/test_telemetry_publication.py)).
