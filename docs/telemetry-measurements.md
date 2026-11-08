# Telemetry local measurements — 7 November 2026

This is a finite calibration of the implemented capture path. Enabled capture
has measurable overhead in these workloads. No latency SLO, overhead tolerance,
performance regression threshold or production recommendation is inferred from
these measurements.

## Results and workload

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
- Snapshot and representative telemetry-query elapsed costs were not timed in
  this procedure; those calibration dimensions remain pending.

## Source identity

The measured local Git commit was
`b328158af20ecf53e6f847f1c4c55933457b8208`.
Production file hashes before and after calibration are sealed in the raw
dataset. They were unchanged, as was the local commit/tree identity. Collector
dependency file hashes were recorded once after the workload; the procedure
does not claim a separate before/after collector dependency check. The published
PR commit can have a different Git identity when reconstructed on its actual
upstream parent; the recorded file hashes identify the measured production
content.

The collector source pin is
[`84229bb7497547d2db91d02a4633ccb3867f740b`](https://github.com/xormania/cce/tree/84229bb7497547d2db91d02a4633ccb3867f740b/tools/telemetry).
The [measurement script](telemetry-evidence/measure_overhead.py) is preserved
unchanged with SHA256
`67517eb36430f3ac87d7c3998aef6a6090725a522c33427f536effd9d0b4fb86`.
If relevant production or collector hashes change, this measurement remains
evidence for its sealed source and should be refreshed for the changed path.

## Scoped reproduction

Install the telemetry extra and test dependencies as described in
[the active guide](telemetry.md), using a runtime accepted by the collector's
SQLite check. From the intended candidate checkout, run this finite procedure:

```sh
TELEMETRY_CALIBRATION_ROOT="$PWD" \
TELEMETRY_CALIBRATION_OUTPUT="$PWD/../telemetry-measurement.json" \
python3 docs/telemetry-evidence/measure_overhead.py
```

The script creates isolated temporary state, checks actual collector readiness,
validates returned work, runs the local failing HTTP endpoint and records exact
source/runtime identities. The output path is explicit and is outside the
checkout in this example. It does not alter product source, create provider
sessions, publish data or install a scheduler. Its sole role is reproducing this
bounded measurement; the shared test-kit remains the validation runner.

The committed compact dataset retains primitive per-trial values and source
seals. A fresh script run additionally emits each operation's elapsed sample
and fuller safe status metadata; the summary calculation uses the same
per-trial wall-time/completed-work basis.
