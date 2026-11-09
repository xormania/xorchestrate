# Telemetry local measurements — 7–8 November 2026

These finite fixture calibrations cover the implemented capture path and public
query, payload and preservation workflows. Each result applies to its sealed
source, workload and configuration. No latency SLO, overhead tolerance,
performance regression threshold or production recommendation is inferred.

## Public query and preservation — 8 November 2026

Three independently initialized trials each created eight work items and eight
results containing 65,536 ASCII text bytes. Each corpus retained 32 observations;
the paged query selected its 16 `application.completed` observations. The payload
workflow reconstructed the exact final `record_result` envelope: 131,499 bytes,
including both its arguments and returned evidence. All 36 declared workflows
completed their 273 public API calls. The complete individual wall/CPU times,
allocation peaks, operation counts, byte populations, receipts and source seals
are retained in [the raw 8 November dataset](telemetry-evidence/calibration-2026-11-08.json).

The table shows medians of **whole workflow elapsed time**, rather than latency
percentiles. A query workflow contains six public calls with a three-event page
limit and 16 KiB response budget; the payload workflow contains 33 public calls
with a 4 KiB range limit. Snapshot, verify and restore each contain one public
call. CPU and traced-allocation columns show the first-pass median. These values
are calculated from the dataset's `read_trials` and `read_summary` entries.

| Public workflow | Calls per workflow | First-pass wall ms | Repeat-pass wall ms | First-pass process CPU ms | First-pass traced peak bytes |
|---|---:|---:|---:|---:|---:|
| `telemetry_events` | 6 | 242.2 | 258.1 | 242.2 | 1,212,155 |
| `telemetry_payload` | 33 | 1,110.4 | 1,077.8 | 1,095.4 | 1,405,508 |
| `telemetry_snapshot` | 1 | 4,832.0 | 4,521.8 | 4,822.7 | 4,647,268 |
| `telemetry_verify` | 1 | 2,744.3 | 2,640.0 | 2,735.2 | 4,630,138 |
| `telemetry_restore` | 1 | 4,545.0 | 4,753.2 | 4,529.8 | 4,646,878 |

All three trials had 1,728,840 live corpus bytes after close, excluding exports.
Each verified bundle or restored copy occupied 1,699,741 bytes. The five saved
copies per trial occupied 8,498,705 bytes together. The report records actual
successful calls/second and canonical JSON response bytes/second for each
workflow; that response throughput includes receipt bytes and base64/text range
envelopes, and does not describe raw bundle-copy throughput. See each sample's
`completed_operations`, `returned_bytes`, `byte_basis` and storage fields in
[the dataset](telemetry-evidence/calibration-2026-11-08.json).

A separate bounded probe held a real SQLite `BEGIN IMMEDIATE` writer reservation
while querying and taking a snapshot, without adding uncommitted rows. Its
median query workflow was 238.8 ms and snapshot was 4,413.9 ms. Every declared
event and bundle population still completed. This records reader behavior under
that reservation; it does not establish a general lock-contention penalty.
The per-trial probe records are labeled `writer-reservation` in
[the dataset](telemetry-evidence/calibration-2026-11-08.json).

Measurement boundaries for these public workflows:

- `first-pass` and `repeat-pass` identify fixed execution order on the same
  corpus. The OS page cache was not flushed. Neither label claims a cold cache;
  each public read still uses the configured adapter's physical connections.
- The timed interval includes public calls, canonical response serialization,
  bounded pagination/range assembly, and the fixture correctness checks. Bundle
  directory sizing happens afterward. Construction, fixture seeding and close
  times are recorded separately.
- `tracemalloc` was enabled for every timed workflow. Its peak measures traced
  Python allocations, not total process RSS. CPU is process CPU. Other work on
  the shared host and filesystem caching can affect the samples.
- The query checks the complete declared event population and stable cutoff.
  The payload checks exact independently known fixture bytes and SHA256. Bundle
  operations check complete receipts, event counts, cutoffs and equal bundle
  identities. The temporary corpora are removed after each manual trial;
  individual measurements and receipt identities remain in the raw dataset.

The measured local commit was
`b7192f12122b9c3404a04b7279df1356177fb3ea`, with current production bytes from
[`f270e29b9263e1c44ba7c182a21abdd74e5c3ba9`](https://github.com/xormania/xorchestrate/commit/f270e29b9263e1c44ba7c182a21abdd74e5c3ba9).
Production Python files, `pyproject.toml`, the measurement procedure, and all
imported collector Python files were sealed separately before and after the run.
All seals and the local commit/tree remained unchanged. Python was 3.12.14 with
linked SQLite 3.53.1; the collector pin was
[`84229bb7497547d2db91d02a4633ccb3867f740b`](https://github.com/xormania/cce/tree/84229bb7497547d2db91d02a4633ccb3867f740b/tools/telemetry).
The measured procedure SHA256 is
`a99c843d0a28a6dfd8f6e9d88b187cd3ea0c573d605e9d003bd1f0abb25d78d5`;
the [raw dataset](telemetry-evidence/calibration-2026-11-08.json) SHA256 is
`e6d928db90fec8a3aabca0c8da414a474db25a6c5aee714e233aa21aa409a9eb`.
These source and runtime observations are recorded in its `source_before`,
`source_after`, `collector_dependency_before` and `collector_dependency_after`
entries. A later documentation commit or reconstructed upstream Git history
does not change the production and procedure file hashes measured here. The
[exact measured procedure](https://github.com/xormania/xorchestrate/blob/382c943fd424720af72aa38620db666704b121e3/docs/telemetry-evidence/measure_overhead.py)
is retained at that historical remote commit. The
[last revision of the procedure, with its updated output guard](https://github.com/xormania/xorchestrate/blob/957c404/docs/telemetry-evidence/measure_overhead.py)
additionally rejects any output path resolving inside the candidate checkout
before sealing sources or running workloads. That admission-only repair had
SHA256 `29458bcbe079bda2f7a00d5caddf84f0c4c85152e9f48199d1c966d3cc098f12`;
the timed workflow implementation and retained dataset are unchanged. The
[verification receipt](telemetry-evidence/read-calibration-verification-2026-11-08.json)
records the separate test, calibration, static-check and review checkpoints.
The procedure and its CI measurement-correctness checks are no longer in the
active tree; they remain available at that commit.

## Capture overhead — 7 November 2026

Each row has three independently initialized trials. The timed workload is
`create_work` through the active application, with an in-memory authoritative
state store. Enabled cases use the pinned CCE collector's local SQLite/file
backend. Small and large content, and serial and four-worker execution, have
matching disabled controls. Every declared operation's returned goal and final
work count were checked.

The time column is the **median of each trial's elapsed wall time divided by
its completed operations**. For four-worker rows this is amortized elapsed time
per completed operation, not a per-call latency percentile. Raw individual trial
wall/CPU times, traced allocations, construction/close times, retained bytes,
capture counts and export failure observations are in
[the compact raw dataset](telemetry-evidence/calibration-2026-11-07.json).

| Selected case | Creates per trial | Trial wall/completed operation, median ms | Corpus bytes after close, median |
|---|---:|---:|---:|
| Disabled, serial 32-byte fill | 20 | 9.361 | 0 |
| Local capture, serial 32-byte fill | 20 | 18.184 | 169,257 |
| Disabled, serial 64 KiB fill | 12 | 10.991 | 0 |
| Local capture, serial 64 KiB fill | 12 | 50.415 | 4,864,213 |
| Disabled, 4 workers, 32-byte fill | 20 | 10.907 | 0 |
| Local capture, 4 workers, 32-byte fill | 20 | 19.378 | 156,969 |
| Local capture + HTTP 503 operational sink | 20 | 43.460 | 169,257 |

All 21 trials completed all 372 declared `create_work` calls with the expected
results. Each enabled trial recorded the expected event count (26 for the
12-create cases and 42 for the 20-create cases) and zero collector capture
failures. The HTTP 503 fixture received 168 requests in each failed-export
trial. Successful application completion and retained corpus are distinct from
those failed operational exports.

The enabled local serial case measured about 8.8 additional milliseconds per
completed operation relative to its disabled control. The 64 KiB and concurrent
rows expose separate workload dimensions; they are not extrapolated into a
universal latency or throughput guarantee.

## Measurement boundaries

- Python was 3.12.14 and its linked SQLite was 3.53.1; the complete runtime/platform
  strings are retained in the dataset.
- `tracemalloc` was enabled equally around every timed operation workload. Its
  peak is traced Python allocation, not total RSS. CPU time is process CPU and
  includes the in-process HTTP fixture thread in failed-export cases.
- Construction and close/drain elapsed times are retained separately. The timed
  interval excludes the final public `list_work` validation and status query.
- Storage/event totals include that untimed `list_work` validation, which itself
  creates two capture observations. The status query is excluded from capture.
- The 32-byte and 64 KiB dimensions count repeated ASCII fill, excluding the short
  index prefix and the serialized JSON/application envelopes.
- Trials run in a fixed documented order on a shared Work host. Concurrent host
  work, filesystem state and tracing overhead can influence results. Three short
  trials per case do not establish a stable tail-latency distribution.
- The exporter failure was a real local HTTP 503 fixture, not a provider call or
  remote service outage. No authenticated model execution occurred.
- The 7 November capture-only calibration did not time snapshot or representative
  telemetry-query workflows; the 8 November section above adds those dimensions.

## Source identity

The capture-only calibration's measured local Git commit was
`b328158af20ecf53e6f847f1c4c55933457b8208`.
Production file hashes before and after calibration are sealed in the raw
dataset. They were unchanged, as was the local commit/tree identity. For that
capture-only run, collector dependency file hashes were recorded once after the
workload, without a separate before/after dependency check. The merged
commit can have a different Git identity from the measured local commit; the
recorded file hashes identify the measured production content.

The collector source pin is
[`84229bb7497547d2db91d02a4633ccb3867f740b`](https://github.com/xormania/cce/tree/84229bb7497547d2db91d02a4633ccb3867f740b/tools/telemetry).
The original capture-only procedure had SHA256
`67517eb36430f3ac87d7c3998aef6a6090725a522c33427f536effd9d0b4fb86`.
The [extended procedure](https://github.com/xormania/xorchestrate/blob/957c404/docs/telemetry-evidence/measure_overhead.py) and its
new source seals are identified in the 8 November section. If relevant production
or collector hashes change, each measurement remains evidence for its own sealed
source and should be refreshed for the changed path.

## Scoped reproduction

Install the telemetry extra and test dependencies as described in
[the active guide](telemetry.md), using a runtime accepted by the collector's
SQLite check. From a checkout of commit `957c404`, the last revision containing
the procedure, run this finite procedure:

```sh
TELEMETRY_CALIBRATION_ROOT="$PWD" \
TELEMETRY_CALIBRATION_OUTPUT="$PWD/../telemetry-measurement.json" \
python3 docs/telemetry-evidence/measure_overhead.py
```

The command runs the existing 21 capture trials and three public-read trials,
including the writer-reservation probe. It creates isolated temporary fixture
state, checks actual collector readiness, validates declared work and records
exact source/runtime identities. The output path is explicit and is outside the
checkout in this example. It does not alter product source, create provider
sessions, publish data or install a scheduler. Its sole role is reproducing this
bounded measurement; the shared test-kit remains the validation runner. An
imported application from a different checkout is rejected before measurement.

The 7 November compact dataset retains primitive per-trial values and source
seals. The extended procedure and 8 November raw dataset additionally retain each
capture operation's elapsed sample, safe status metadata and the public-read
workflow records. The capture summary still uses the same per-trial
wall-time/completed-work basis.
