# Request execution verification

The independent request scenarios cover full serialized request limits, whole
candidate grouping, task/answer alignment, bounded transient retries, permanent
errors, safe provider diagnostics, adaptive context-overflow handling, request and
time limits, successful partition reuse and source freshness before cache reuse.
The focused run passed 109 new and existing cases on Python 3.12.16, including 19
independent request cases and the retained timeout/incomplete-read assertions.

[The compact evidence ledger](requests-v3.json) records 24 meaningful red-to-green
witnesses with passing controls: 19 deliberate faults and five actual reviewed
predecessors. The latter reproduce framed Content-Length response failure, both
documented overflow-detail envelopes, lost interleaved partial results and an
interleaved warm-plan miss. Setup errors and absent APIs receive no detection
credit. The ledger deduplicates source patches and retains exact commands,
measured candidate/mutation identities, named assertions, native exits and
raw-output/JUnit identities.

Existing diagnostic tests now verify complete relevant source and contract
identity with full repository bookkeeping retained in the report. Default
transient-error diagnostics account for all three permitted attempts. The
incomplete-read fixture exposes deadline control while retaining its original
partial-byte assertion; no truncation or timeout guarantee was relaxed.

An archived PR #71 packet was also replayed through a local HTTP fixture: all nine
tasks, three candidates and 56 complete units fit one 58,334-byte request. The
original/projected state sizes were 93,234/48,744 bytes. The receipt retains its
archive and source hashes and labels token figures as estimates. This is protocol
and sizing evidence at those inputs, not authenticated TypeSafe behavior,
provider-tokenizer accuracy or semantic effectiveness. Later review repairs have
separate focused witnesses above.
