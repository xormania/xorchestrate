# Active telemetry

Telemetry extends the active `xorchestrate/` application. The collector is the
shared `agent_telemetry` package from CCE, pinned by the `telemetry` installation
extra.

## Install and enable

Install the optional collector alongside the application:

```sh
python3 -m pip install -e '.[test,telemetry]' -r .github/requirements-ci.txt
```

Use a Python build whose linked SQLite includes the WAL-reset fix (3.51.3 or a
fixed backport). Check the library linked to Python, rather than a separate
sqlite3 executable. CI uses the same fixed-runtime approach as CCE:
uv 0.13.7 and Python 3.12.16. Static validation remains independent of this extra.

The [telemetry configuration example](../examples/telemetry.json) selects local
collection explicitly. Paths are resolved against the configuration directory;
its corpus directory is outside this checkout. Change the project, dataset and
collector identities for your deployment. Separate hosts use separate collector
lineages; do not share a live WAL database over a network filesystem.

With this profile:

```sh
python3 -m xorchestrate --config examples/telemetry.json call create_work --json '{"goal":"Retain an inspectable work episode"}'
python3 -m xorchestrate --config examples/telemetry.json call telemetry_status --json '{}'
python3 -m xorchestrate --config examples/telemetry.json call telemetry_events --json '{"limit":20}'
```

Use the persistent `run` or MCP `serve` interface for native sessions. The same
application operations are available through both input adapters.

## Configuration options

The top-level `telemetry` section takes `adapter` (built-in `local`), `options`
and an optional boolean `enabled`. When the section is absent or `enabled` is
`false`, no collector is constructed. If construction fails, the application
continues with an explicit unavailable telemetry state. Unknown option names are
rejected.

| Option | Default | Validation and meaning |
| --- | --- | --- |
| `runtime_root` | required unless `profile` | Corpus root, resolved against the configuration directory; no symbolic-link components. |
| `project_id`, `dataset_id`, `collector_id` | required unless `profile` | Nonempty text identities for the collector lineage. |
| `producer_id` | `xorchestrate` | Nonempty text. |
| `database` | `events.sqlite3` | Relative path inside `runtime_root`; no `..` or symbolic links. |
| `artifact_root` | `artifacts` | Relative path inside `runtime_root`; cannot overlap `database`. |
| `capture_lock_timeout_ms` | `250` | Integer from 0 to 60000. |
| `profile` | none | Shared `agent_telemetry` profile path, resolved against the configuration directory. It supplies the root, paths, identities and lock wait, so it cannot be combined with the preceding options. |
| `export_root` | `exports` | Relative path inside the runtime root; snapshot and restore destinations are new children of it. |
| `snapshot_timeout` | `5.0` | Seconds; greater than 0 and at most 3600. |
| `artifact_threshold_bytes` | `65536` | Positive integer; larger payloads are stored as content-addressed artifacts. |
| `known_secrets` | `[]` | List of nonempty strings redacted from payloads and context; excluded from the configuration identity. |
| `sources` | `application`, `native`, `capability`, `evaluator`, `process` | List of source names reported as configured. When set explicitly, observations from other sources are not recorded (`source_disabled`). |
| `operational` | none | Optional operational-signal export; see below. |

The `operational` object accepts only these fields:

| Field | Default | Validation and meaning |
| --- | --- | --- |
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

## Operations

The application exposes these telemetry operations: `telemetry_status`,
`telemetry_events`, `telemetry_usage`, `telemetry_compare`, `telemetry_payload`,
`telemetry_snapshot`, `telemetry_verify` and `telemetry_restore`. The operator
tools with similar names in the historical Agentscient runtime are distinct.

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

- [Shared CCE collector and limits](https://github.com/xormania/cce/blob/84229bb7497547d2db91d02a4633ccb3867f740b/tools/telemetry/README.md)
- [SQLite WAL and fixed runtimes](https://www.sqlite.org/wal.html)
- [SQLite online backup](https://www.sqlite.org/backup.html)
- [Existing application architecture](hexagonal-architecture.md)
- [Shared testing framework](testing-framework.md)


## Preserve and investigate an episode

The preservation layer adds public snapshot, verification, restore and comparison
operations. Destinations are new children of the configured export root; existing
bundles are never overwritten:

```sh
python3 -m xorchestrate --config examples/telemetry.json call telemetry_snapshot --json '{"destination":"episode-1"}'
python3 -m xorchestrate --config examples/telemetry.json call telemetry_verify --json '{"destination":"episode-1"}'
python3 -m xorchestrate --config examples/telemetry.json call telemetry_restore --json '{"bundle":"episode-1","destination":"episode-1-restored"}'
```

Snapshot creation uses SQLite's completed backup result to derive the cutoff,
event count, source cursors and artifact closure. Verification reads the closed
database and checks the retained artifact bytes. Current authorization covers the
actual bundle closure for all three operations, including artifact-backed typed
results. Failure leaves an incomplete receipt and does not advertise a successful
preservation result.

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
Protected protocol frames retain only bytes available within the configured
[transport bounds](../xorchestrate/adapters/jsonrpc.py), rather than every native
field or callback.
The [native tool fixtures](../tests/hexagonal/test_native_producer_capture.py)
exercise preservation of token-shaped arguments; authenticated usage accounting
remains pending.

| Source | Implemented capture boundary | Usage and source position | Deterministic fixture evidence | Authenticated witness | Remaining coverage gaps |
| --- | --- | --- | --- | --- | --- |
| [Codex](../xorchestrate/adapters/sessions.py) | Serialized JSON-RPC requests, bounded stdout frames and drained stderr; emitted notification, tool/permission, turn and settings observations. | Exposed usage payloads retained; no Codex-specific normalized counter semantics. Frame ranges and request/session/incarnation links; no file cursor. | [Native identity/context/tool observations](../tests/hexagonal/test_native_producer_capture.py); [transport failures/bounds](../tests/hexagonal/test_producer_capture.py). | Pending installed/authenticated Codex run. | No separate transcript reader or verified native usage total; unexposed fields remain unknown. |
| [Claude](../xorchestrate/adapters/claude.py) | Gateway-delivered hook packets, emitted semantic observations and protected records from the exact hook-selected session JSONL file. | Transcript usage uses `overwrite` with a stable message ID, otherwise `unknown`; [normalization](../xorchestrate/telemetry_views.py) preserves raw fields. Acknowledged complete records advance the live cursor; tails wait and changed prefixes create a gap/new generation. | [Hook identity/tool observations](../tests/hexagonal/test_native_producer_capture.py); [source checks, acknowledgment, tails, rotation, rewrites and bounded verification](../tests/hexagonal/test_transcript_producer_capture.py). | Pending installed/authenticated Claude Code run. | Missing/rejected sources stay explicit. Terminal/debug output is outside this transcript source; a new driver incarnation reingests from zero. |
| [Grok](../xorchestrate/adapters/grok.py) | ACP/JSON-RPC frames and drained stderr through the [shared session transport](../xorchestrate/adapters/sessions.py); emitted session-update, tool/permission, interjection, completion and configuration observations. | Exposed usage payloads retained; no Grok-specific normalized counter semantics. Frame ranges and native session/incarnation/prompt links; no file cursor. | [Native identity/context/tool observations](../tests/hexagonal/test_native_producer_capture.py); [shared transport failures/bounds](../tests/hexagonal/test_producer_capture.py). | Pending installed/authenticated Grok Build run. | No separate transcript reader or verified native usage total; unexposed provider state remains unknown. |
| [Evaluator](../xorchestrate/adapters/evaluation.py) | TypeSafe serialized HTTP inputs, bounded response/error bodies and validation; tool-evaluator arguments/results plus its configured [CLI/MCP provider](../xorchestrate/adapters/capabilities.py). | Valid TypeSafe usage is `incremental` per fresh attempt; missing usage stays absent. Response ranges identify observed prefixes and known/unknown total length. Tool results have no universal usage-counter semantics. | [Local HTTP/process errors, bounds and credential treatment](../tests/hexagonal/test_producer_capture.py); [fixture-provider usage through public normalization](../tests/hexagonal/test_telemetry_workflow.py). | Pending authenticated live-provider witness. | [Local fixtures](../tests/hexagonal/support/producer_capture.py) establish adapter behavior, not internal provider accounting, hidden context or service latency. |

The optional [`operational` object](#configuration-options) inside local
telemetry options selects `{"adapter":"local"}` for bounded in-process diagnostics
or `{"adapter":"otlp","endpoint":"<base URL>"}` for OTLP/HTTP export.
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
