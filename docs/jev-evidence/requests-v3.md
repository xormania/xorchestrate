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

## Hosted PR #77 missing-evidence ledger repair

The published predecessor (`3c7e0c4`) could exhaust its request estimate before
sending any HTTP call: its packet contained 1,050 missing-evidence rows. The
model state now carries bounded per-candidate summaries with full counts, reason
counts, canonical full-set digests, representative rows and explicit omissions.
The outer diagnostic retains the complete raw ledger. Unshown missing-set changes
invalidate packet reuse; row ordering and repeated projection do not change the
summary. Complete source bodies remain intact, with repeated file/ownership
metadata represented separately.

Five independent ledger scenarios and the inherited wire contracts passed in an
83-case focused run. Eight meaningful red-to-green witnesses passed their healthy
controls, including two against the actual published request implementation and
faults for counts, hidden-set cache invalidation, idempotence, unowned evidence,
full report retention and forbidden source cutting. The JSON ledger retains their
input hashes, commands, native exits and intended assertion markers.

The actual archived PR #77 state was replayed with its `jev-1.14.0` model label and
the production diagnostic rubric under the default request limits: all nine tasks
completed through three local HTTP requests (43,189, 34,581 and 51,412 bytes). All
114 current/before source-unit bodies were preserved, while the 1,050 missing rows
remained bound by the compact summaries. This proves protocol sizing and evidence
preservation for that packet; it is not a live-provider or semantic-quality claim.
