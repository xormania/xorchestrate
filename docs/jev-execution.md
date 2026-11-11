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
selected evidence, ordered tasks/questions, execution mode and cache schema. Source context
is rebuilt and its measured hashes are checked before lookup. A changed input
requires new work. `cache_ttl_seconds` defaults to 86400; reuse retains its original
creation time. Malformed, corrupt, expired or unreadable entries are recomputed.
Provider failures and incomplete batches are never cached. Cache writing is
atomic and private, using the existing evidence writer. A digest detects damaged
content; it is not an attestation against a writer who controls this trusted cache.

The complete omission/exclusion ledger and repository identity stay in the retained
report. The model packet carries selected candidates, complete current and before
source units, their applicable role contracts and explicit missing inputs. It
omits unrelated roles, repository-wide inventory digests and omission bookkeeping.
Repeated source-file hashes and module identities live once in `source_files`,
keyed by path and revision; units retain their identity, location and full text.
Each packet has an exact context hash. An unrelated repository change can reuse a
packet only after current source measurements have been revalidated; changes to
selected source, supporting evidence or applicable contracts still invalidate it.

## Complete request budgets

`max_source_bytes` bounds source selection, not the model input. Before transport,
the request planner serializes the same model, state, questions and criteria as
the adapter. It estimates both the full request and the largest state-plus-one-
question packet. The defaults are `max_request_tokens: 64000`,
`max_question_tokens: 32000`, `bytes_per_token_estimate: 2`,
`token_headroom: 0.2` and `max_batches: 16`. Estimates include a 128-token framing
allowance and reserve the configured fraction of each token limit. These are
deliberately conservative heuristics, **not exact tokenizer counts or a guarantee**;
the reviewed [public API](https://docs.typesafe.ai/api/system-one) exposes no
documented token-count/preflight operation. Configure limits for the pinned
[model](https://docs.typesafe.ai/models#context-length).

An over-budget group first splits by candidate with its applicable contract and
owned supporting units, retaining unowned support conservatively. Questions over
the same coherent state stay batched where they fit. A remaining group can split
its questions; a complete indivisible packet that still exceeds the estimate
returns `request_budget_exceeded` without sending it. No source text is clipped
to force a request through. Reports retain each packet's estimate, exact evidence
and unfinished task IDs.

The same bounded split is available after an HTTP 400 only when the provider
returns the allowlisted `max_tokens_exceeded` code. An unchanged invalid request
is never retried. Successful leaf requests use the normal exact-request cache;
a successful partition plan lets a warm run reach those leaves without repeating
the rejected parent request. Plans are schema-, digest-, evidence- and TTL-checked;
failures and partial plans are not cached.

The generic TypeSafe adapter defaults to no retries. Architecture diagnostics
select two retries for connection failures and HTTP 429, 500, 502, 503, 504 or 529.
`max_retries` is configurable from zero to five, and `retry_backoff` defaults to
0.25 seconds with exponential growth. Numeric `Retry-After` is respected only
within the existing deadline. All packets, attempts, response reads and backoff
share the original `timeout`; retrying does not reset it. Permanent errors stop
immediately. `max_batches` additionally bounds logical packets, including failed
overflow attempts; each transport call has its own configured retry cap.

Provider error reports retain only the HTTP status, recognized context-overflow
code and a bounded safe `x-typesafe-request-id`. Arbitrary bodies, messages and
credential material do not become diagnostic error fields. See the provider's
[error contract](https://docs.typesafe.ai/api/errors).

Reports distinguish logical requests, actual HTTP attempts (`provider_calls`),
reused assessments, fresh usage and historical reused usage. A hit removes the old
usage from the fresh assessment counters and makes zero HTTP attempts. A failed
attempt has unknown usage, including when a later retry succeeds; aggregate usage
remains unknown while each successful packet retains its reported quantities.
Partial successful single calls remain
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

A failed hosted large-packet batch remains recorded in its original CI attempt. The transport now retains bounded HTTP status codes through advisory failure handling, without copying provider error bodies or credentials. Independent 400/529 controls reproduce the lost-status defect and verify the repair. The model packet was also reduced without removing selected source evidence; [TypeSafe documents separate request and question context limits](https://docs.typesafe.ai/models#context-length). Actual live compatibility is reported by the subsequent hosted attempt, not inferred from a green advisory job.
