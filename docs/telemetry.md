# Active telemetry

Telemetry extends the active `xorchestrate/` application. The collector is the
shared `agent_telemetry` package from CCE, pinned by the `telemetry` installation
extra. The imported `devtools/` runtime remains reference source.

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

## Three-PR delivery

The implementation is divided into independently reviewable changes, with
dependent application integrations stacked as needed:

1. **Foundation:** active collection, optional composition, immutable context,
   current-authorized reads, payload retention and source/capture health.
2. **Producer capture:** evaluator request/response/error bodies, bounded process
   diagnostics, native observations and supported transcript sources.
3. **Preservation and observability:** verified snapshots/restore, analysis views,
   operational signals and explicit optional OTLP export.

Each PR is opened as a draft only after an adversarial sub-agent review.
Independent test authors retain behavioral baseline failures and candidate passing
evidence. The ordinary shared test-kit and hosted CI remain the execution owners.
The implementation does not create a new scheduler or a second proof runner.

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
- [Telemetry reference and research](https://chatgpt.com/library?file_id=libfile_442a72f860b081919c1ffc4836679e18&account_id=personal)
- [Scope and requirements](https://chatgpt.com/library?file_id=libfile_b8e13c37c7a081918768e2e9571b54ef&account_id=personal)

