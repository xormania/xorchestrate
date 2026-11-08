# Codex review resolutions — 7 November 2026

All seven published findings on telemetry PRs #18 and #19 were practical. They
were reproduced with independently authored tests before repair. PR #21 had no
published review findings when this follow-up began. The PR descriptions bind
the final published heads, hosted checks and stack relationships.

## Evaluation and resolution

| Finding | Assessment and repair | Independent coverage |
|---|---|---|
| [Invocation source authority, #18](https://github.com/xormania/xorchestrate/pull/18#discussion_r4503439622) | Valid: a work-scoped requested invocation omitted provider/capability authority. Typed application envelopes now use the existing execution-scope projector, including retained artifact payloads. Unsupported request operation IDs cannot borrow another operation's grant. | [Source authority matrix](../../tests/hexagonal/test_telemetry_review.py): completed, rejected and missing-provider requests; provider/capability restrictions; inline/artifact events and exact payload reads; forged operation IDs. |
| [Retained usage, #18](https://github.com/xormania/xorchestrate/pull/18#discussion_r4503439625) | Valid on #18; #21 already hydrated analysis payloads. The foundation now hydrates and authorizes retained payloads before extracting usage, and bounds the response with explicit payload references or an explicit smaller-page error. | Same matrix: artifact usage and large measurement containers under default and selected byte budgets; original retained content remains retrievable. |
| [Status pagination, #18](https://github.com/xormania/xorchestrate/pull/18#discussion_r4503439629) | Valid: a short byte-limited page was mistaken for complete history. Status follows continuation at a fixed cutoff, within a total raw scan budget, and reports count/latest/source completeness explicitly. | Same matrix: multiple byte pages, correct visible count, latest capture and a source appearing on a later page. |
| [Sparse filtered scans, #18](https://github.com/xormania/xorchestrate/pull/18#discussion_r4503457131) | Valid: filtering inside an unbounded adapter loop bypassed the outer scan budget. The optional scanner reports bounded raw progress, including rejected rows, so an empty match page can advance without claiming end of history. | Same matrix: work/session/operation filters, an independent repository-row counter, honest continuation and a 10,000-row application scan horizon. |
| [Growing transcript starvation, #19](https://github.com/xormania/xorchestrate/pull/19#discussion_r4503513394) | Valid: every append restarted a multi-pass prefix check. Bounded verification progress now survives ordinary growth; concurrent-growth continuity is explicitly unconfirmed and requires later full-prefix revalidation. Mismatches still produce a gap and a new generation. | [Transcript contracts](../../tests/hexagonal/test_transcript_producer_capture.py): append before every pass, equal/longer midprefix replacement during growth, bounded progress and quiet recheck. |
| [HTTP failure-path diagnostics, #19](https://github.com/xormania/xorchestrate/pull/19#discussion_r4503513398) | Valid: diagnostics read a known HTTP error body even with telemetry disabled, and per-read timeouts let trickled bodies exceed the configured failure deadline. Disabled capture skips the body; enabled error capture shares the original absolute request deadline and stops when a read cannot be bounded. | [Producer contracts](../../tests/hexagonal/test_producer_capture.py): disabled read-count oracle and an actual local HTTP 429 trickle response. |
| [Nested callback telemetry, #19](https://github.com/xormania/xorchestrate/pull/19#discussion_r4503533140) | Valid: a second opaque base64 response inherited only the outer native session's grant. Trusted application-capable callback response writes now retain an explicit suppressed receipt and source byte count. Rich typed results remain under current nested authorization; protocol bytes are unchanged and unregistered callbacks retain ordinary raw capture. | [Real callback contracts](../../tests/hexagonal/test_telemetry_callback_review.py): denied work/provider content through event, projected payload and original artifact-digest reads; exact owner result/reply; opaque and generic callback controls; valid persisted suppression metadata. |

The read-scope fix reuses the application-owned
[execution projector](../../xorchestrate/inspection.py), rather than interpreting
arbitrary provider JSON as authority. The sparse scanner is a Xorchestrate adapter
extension over the same pinned CCE repository; no second collector was added.

## Independent evidence

Implementation and test authors remained separate: `collector_impl` /
`collector_tests` for foundation; `capture_impl` / `capture_tests` for transcript
and HTTP; `preserve_impl` / `preserve_tests` for callback capture.

- Foundation: **31 semantic failures** on the reviewed baseline, then **71 tests
  passed**. Repair review found two remaining edges within the original findings:
  output byte bounds and forged operation scope. Their independently authored
  controls produced **2** and **8** failures respectively on unchanged source;
  the final foundation suite passed **81 tests**.
- Transcript/HTTP: identical final test bytes produced **5 semantic failures,
  zero errors** on the unchanged producer baseline. The repaired producer/native/
  transcript suite passed **51 tests**.
- Callback capture: identical final controls produced **7 failures and 3 passing
  controls**, then **10 passing tests** after repair. Six failures were actual
  disclosures; the seventh required explicit durable suppression metadata.
- After integration, the foundation/producer/callback run passed **142 tests**.
  Wire decoding lives in the test adapter, retaining independent comparison
  predicates and the existing architecture dependency rules.
- Final local integration passed **210 tests and 4 subtests**. The scanner-aware
  fixture retains the same revocation/denial oracles: identical fixture bytes
  still produce **one semantic failure** on the old baseline, and the repaired
  stack passes all **18 public workflow tests**. All three publication candidates
  passed Ruff, current-source syntax and architecture checks.
- Manual real-callback preservation checks passed for both work and provider
  cases. Restricted and mid-read-revoked snapshot/verify/restore attempts are
  denied. With live artifacts physically unavailable and live lookups guarded,
  owner verification and fresh restore still use only bundle artifacts and
  preserve exact manifest/database/artifact closure. These are existing closure
  controls, not new RED claims.

These checkpoints overlap and must not be added as a total test count. The
[machine-readable follow-up receipts](codex-review-receipts-2026-11-07.json)
retain author/reviewer source identities, test bytes, commands, result metadata
and hashes of the underlying logs and receipts. The
original [verification receipt](verification-2026-11-07.md) remains evidence for
its stated earlier source checkpoints; this document records the review-driven
changes. Final integration results and hosted CI are recorded on the updated PRs.

## Source and evidence boundaries

The reviewed published baselines were #18 at
`523c256a45a79c1ea2a01ff26601ee8f5bec147e` and #19 at
`1cf8a7a423e2991ebc8ace3e4049552ad1dfe1d2`.
Local source reconstruction and publication preserve the same active bytes while
retaining the upstream archive tree. The final remote file hashes and merge trees
are checked when publishing the stack.

The repairs change telemetry reads, diagnostic capture and source continuity.
Earlier [calibration](../telemetry-measurements.md) remains bound to its recorded
source hashes; this follow-up does not claim a new overhead measurement or SLO.
Deterministic fixtures and real local transports do not establish authenticated
vendor qualification. Concurrent transcript growth is not labelled an atomic
filesystem snapshot.
