# Jev request reuse and batching

Architecture diagnostics can reuse an unchanged assessment request and send
independent typed questions together. Runtime `assess` remains a single-question
operation; its existing transport validation and telemetry behavior are retained.

```sh
python -m scripts.architecture --jev-config contracts/hexagonal-jev.json \
  --base-ref HEAD^ --cache-dir ../jev-cache --output ../architecture.json
```

The diagnostic profile enables `batch: true`. `--batch` also enables it for an
operator profile; removing that profile option selects independent requests.
Batch mode uses the same model, state and criteria with distinct named questions.
Every expected answer must be present exactly once, and all model/distribution
validation must pass before a batch supplies assessments. Request-wide usage is
retained once, on the first answer; every answer identifies the shared batch.
Batch and independent modes may produce different judgments and use separate
cache identities. Protocol fixtures establish binding, not equal model judgments.

`cache_dir` selects a trusted local directory, omitted by default for local use.
The request identity includes the endpoint, pinned model, criteria, complete
context, ordered tasks/questions, execution mode and cache schema. Source context
is rebuilt and its measured hashes are checked before lookup. A changed input
requires new work. `cache_ttl_seconds` defaults to 86400; reuse retains its original
creation time. Malformed, corrupt, expired or unreadable entries are recomputed.
Provider failures and incomplete batches are never cached. Cache writing is
atomic and private, using the existing evidence writer. A digest detects damaged
content; it is not an attestation against a writer who controls this trusted cache.

Reports distinguish provider calls, reused assessments, fresh usage and historical
reused usage. A hit removes the old usage from the fresh assessment counters;
unknown provider quantities remain unknown. Partial successful single calls remain
available if a later call fails. A batch has no partially accepted answer set.

CI restores the cache outside the checkout through a pinned optional cache action.
A failed restore/save does not change deterministic qualification. The internal
request identity, rather than the outer storage key, controls reuse. CI summaries
show the execution mode and fresh versus historical quantities; raw reports retain
their provenance. The paired effectiveness pilot records its own mode and provider
results and remains advisory.

## Verification

An independent test author used real local HTTP boundaries for named batches,
strict returned answer identities, usage accounting, CLI invocation and cold/warm
diagnostics. Eighteen isolated unit faults and eleven integration/CI faults were
detected with passing healthy controls. Four additional baseline assertion failures
verify that CI reporting distinguishes fresh single calls, fresh batches, historical
reuse and invalid quantities. Source, command and JUnit receipts were retained.

One pre-draft adversarial review found that extreme numeric or deeply nested cache
entries could escape the optional-cache boundary. Targeted regressions exercise
the actual predecessor and repaired fallback. These results establish execution,
binding and reuse behavior; the hosted provider's batch response is a separate
live witness in the PR checks.

After the final stack merge, shared static checks and all **166 architecture cases** passed; **139 reporting/evaluator/producer tests plus eight subtests** also passed. The three review regressions each reproduced the actual predecessor failure and passed after repair. The [compact witness ledger](jev-evidence/efficiency.json) retains test identities, applied patches, source digests and observed command results.
