# CI candidate evidence

`CI result` qualifies receipts against a versioned observation of the actual
checkout. GitHub remains the authority for the PR, checks, and final job outcome.
This evidence does not assert that review happened or decide readiness.

## Candidate context

The path-classification job captures `candidate-context--attempt-N/context.json`
before validation. It exports the same small JSON object to downstream jobs.
Each lane checks its actual checkout, tree, parents, clean status, and validation
inputs against that context before producing its own `producer.json`.

Context schema version **1** records:

| Field | Meaning |
|---|---|
| `repository`, `source_repository` | Stable numeric repository ID and full name; a fork stays a separate source |
| `pr` | Number, source ref, target ref; `null` for push/manual execution |
| `source_sha` | Source head, separately from execution |
| `checkout` | Actual commit, tree, ordered parents, status and `pr_merge` or `branch` kind |
| `tested_base_sha` | Parent one, only after proving exactly two parents and source head as parent two |
| `event` | Event/action and event-reported base, retained separately from the tested base |
| `producer` | Workflow ref/revision, run ID, capture attempt and GitHub check SHA |
| `inputs` | SHA-256 identities of workflow, catalog, lock, project profile and CI/test-kit checker sources |

For a PR, `GITHUB_SHA` must equal the actual tested merge commit. GitHub's Actions
run and check association identifies the **source head**; the context records
that separately as `producer.check_sha`. The aggregate verifies the run event,
workflow path, source repository/ref, Actions check-suite association, and the
tested commit's tree/ordered parents through the GitHub API. The run's PR edge must
match its number, source/ref/repository, and target ref/repository. Workflow bytes
at the reported immutable workflow revision must match the captured workflow
digest. A manually dispatched
branch run remains branch evidence and does not claim PR integration coverage.

The event-reported base can differ from the observed merge's parent. Both remain
visible; copying event metadata does not prove what was tested. An unexpected
merge structure fails integration qualification instead of inventing a base.

## Lane producers and partial reruns

Existing receipt/inventory contents and logical lane names are preserved. Artifact
names append `--attempt-N` so retry uploads are immutable and unambiguous.
Each artifact's `producer.json` contains schema version 1, the original context,
the logical lane, its actual run attempt, and the resolved runtime manifest.

The aggregate enumerates jobs from every attempt and selects the newest actual
execution of each applicable lane. A successful original lane can therefore be
combined with a successful rerun of a failed shard. A newer failed, cancelled,
incomplete, missing, or contradictory lane cannot borrow an older green artifact.
The original attempt history remains in the final result.

The context capture itself may come from an earlier attempt when a partial rerun
does not rerun path classification. Each lane keeps its own producer attempt;
the aggregate never assigns the current attempt to all historical receipts.

The runtime manifest records resolved Python/implementation, uv, architecture,
platform, runner image identity, fixture mode, and tmux for the tmux lane. An
unknown tool/image input stays unknown. Recording it does not establish runtime
equivalence or authorize reuse; this change continues full applicable validation.

## Qualification and limits

Result schema version **2** adds `candidate_context`, `evidence_references`, and
`attempts` to the existing result. References retain original run/attempt/job,
artifact ID/name/API digest, runtime, and original context. The summary displays
source, tested base/merge/tree, check association, and lane producer identities.

The aggregate downloads exact artifacts from the authenticated GitHub API, verifies
the API's SHA-256 archive digest, and preflights paths before extraction. It rejects
duplicate or noncanonical members, traversal, links, encryption, and excessive
archive/member/expanded sizes. API credentials use an unredirected header, so an
artifact redirect does not forward the token to storage. Advice transfer failures
remain advisory. Missing evidence for a purportedly successful required lane fails
qualification.

Bounds are 20 run attempts, 20 pages of 100 items per collection, 4 MiB per API
JSON response, 64 MiB per archive/member, 128 MiB expanded per artifact, and 10,000
archive members. Exceeding a bound reports incomplete qualification; it does not
claim an empty collection or truncate evidence into success. These accommodate
the repository's current shard/receipt layout without a new evidence service.
Workflow contents have a separate 256 KiB decoded limit. A historical closed or
deleted PR whose run no longer exposes its PR association is insufficient lineage
for PR qualification; it cannot be promoted into current PR evidence.

The bounded API/archive approach adapts
[Xorder's validation context reader](https://github.com/xormania/xorder/blob/master/scripts/validation_base.py).
The contract adds complete lane/attempt attribution rather than reusing Xorder's
image-specific applicability rules.

Artifacts authenticate an identified producer, not the honesty of candidate code.
Existing review and independent behavioral assertions remain necessary. A local
`result.evaluate` call without context/metadata retains the receipt-only API for
focused existing tests; the hosted CLI requires both inputs and cannot use that
legacy path. No cross-run reuse or merge freshness setting changes here.
