# PR lifecycle validation

Source and metadata edits enter the same validation path. Deterministic behavior
can reuse a qualified execution of the exact candidate; static checks and both
required verdict jobs still run. GitHub remains the authority for checks and PR state; this does
not mark a draft ready or merge it.

## Events and surviving validation

The CI workflow handles `opened`, `synchronize`, `reopened` and `edited`.
Opening a draft and pushing source changes retain the full verification path;
the existing independently guarded documentation-only classification remains.
Marking a draft ready does not rerun CI: the checks for its existing candidate
remain authoritative. Full green verification still precedes owner handoff.
Master pushes and manual runs execute full validation.

Title/body edits, explicit base-retarget changes and unknown edit payloads use
ordinary candidate capture and qualified evidence reuse. Every selected CI run
publishes the stable `CI result` and `Hexagonal architecture` names. GitHub selects
the latest workflow run for the commit rollup; renaming and skipping required jobs
on metadata runs leaves those checks waiting even when an older run passed.

Metadata edits therefore run static checks and required verdict qualification.
Behavioral lanes skip only when existing evidence qualifies or the independently
guarded documentation-only decision applies. Missing, failed, stale or unavailable
evidence falls back to execution; it never becomes a skipped required success.
Title/body-only edits wait behind active validation of the same candidate before
the reuse planner runs. Strict up-to-date checking remains disabled, and
master-push integration validation remains enabled.

Validation concurrency is scoped to repository, PR number, source head and event
base. Source and metadata events share this group. A title/body-only edit has
`cancel-in-progress: false`, so it waits for active source validation to finish.
GitHub's default single-pending queue replaces earlier waiting metadata events
when more edits arrive. The surviving run then captures its candidate, performs
static checks and qualifies completed receipts through the ordinary reuse path.
An edit that also changes the base, an unknown edit, or a source event retains
cancellation within its candidate group. A delayed old-source event or manual
rerun cannot cancel a newer source or base candidate.

A metadata event can replace a pending source event for the same candidate. If
no completed qualified producer exists, the surviving event executes full
validation. Waiting or cancellation never counts as verification. Non-PR runs
retain cancellation by ref.

The separate `CI queue cleanup` workflow reacts when CI is requested or starts,
including reruns. It runs only trusted default-branch code at the event's immutable
SHA, with the narrow Actions write permission needed for ordinary cancellation.
It never checks out candidate code or consumes candidate artifacts. For
same-repository PRs, it authenticates the native CI workflow, PR association and
source branch, then compares the live PR with its canonical source ref. Only an
active older run for a different source head can be cancelled, and only while the
triggering current-head CI still survives. Current-head metadata runs can supply
that replacement because a pending metadata edit may replace a pending source
event and then perform full validation. A base-only advance cancels nothing.

Each cancellation rereads the old run's identity and attempt, the replacement,
and the current PR/ref. Missing or changed proof leaves work running. The retained
`ci-queue-*` report distinguishes a cancellation request from the run state later
observed; a request does not claim completion or passing evidence. The controller
does not poll, force-cancel, or change the CI result. It inspects up to 500 recent
runs on the source branch and reports a truncated history. A delayed stale run
whose ID is newer than the surviving replacement remains running; neither this
controller nor the startup guard suppresses that ordering;
foreign-repository runs are untouched. Requested/start events can produce up to
two small jobs, coalesced within the triggering run and attempt.

The startup supersession check below also avoids starting costly lanes when a
surviving replacement can be proved.

The actual checkout stays bound to the event's GitHub check association. Queue
startup observes the live PR before selecting reuse; collection ends with another
observation. A changed source, target, tested base or merge makes reuse unavailable.
The workflow never checks out newer code under an older event's check. These are
timestamped observations, not a continuing atomic view of GitHub.

When the source head has advanced, the planner can mark the old event
`superseded` only after finding a newer run for the same PR's current head. That
run must be an opened/source/reopen validation, still active or completed with a
non-cancelled material outcome when reread. The PR and canonical source branch
are checked again. Static and behavioral lanes then skip; the old aggregate
remains unsuccessful rather than asserting a pass without verification. The
plan retains the replacement run identity. No successful result is manufactured
for the new candidate.

A base-only advance, missing or cancelled replacement, unavailable API evidence
or a changing observation keeps ordinary execution. There is no automatic base
refresh service here; the repository's merge-freshness policy is unchanged.

An older head’s receipts cannot clear a newer source head.

A PR REST object can lag its branches. A matching PR observation is called
`current` only after canonical source and target branch refs also match, using
the source repository for a fork head. PR-observed, event and tested identities
remain distinct from `canonical_refs`; advanced or unavailable refs never qualify
current-candidate reuse.

Workflow-generated run titles contain only the event/action, PR number and Git
identities. They positively identify cancelled or queued/pending metadata runs
with **zero jobs in their first attempt**, after rereading the native run/attempt,
as never-executed observations. Queued/pending runs must have no conclusion, and
their job listing is read again after that observation to detect newly started
work. This allows an active metadata consumer to finish qualifying the original
producer while another edit waits behind it. An empty later retry cannot erase
prior behavioral work. Missing attempt ordering still requires ordinary execution.
Started or unknown work is material. This does not
guess from owner-supplied PR titles or assume every cancellation is harmless.

## Exact reuse and its evidence

`scripts/ci/reuse.py` uses the existing [candidate evidence](ci-candidate-evidence.md)
capture, bounded downloads and native receipt qualifier. It requires the same:

- Repository/source repository, PR number and refs, source head and tested base.
- Actual merge commit, ordered merge parents, clean tree and tree identity.
- Workflow, checker, dependency lock, validation profile and suite catalog inputs.
- Resolved Python, interpreter implementation, architecture, platform, uv, runner
  image/version and fixture mode for every reused lane; tmux for its acceptance
  lane. Missing relevant runtime identity requires execution.

History includes all outcomes. Latest **attempt start** orders attempts, including
a rerun of an older run; completion time cannot let a slow old green hide a newer
failure. The current consumer is excluded. A newer relevant failed, cancelled,
queued or running behavioral attempt prevents selection of older green evidence.
Positively identified, never-executed metadata observations retain their own
history and are neutral. Unknown identity or timing requires execution. Equally
started attempts with unclassified peers also execute; run numbers do not resolve
an ambiguous timestamp into a safe ordering.

The producer must be a completed successful eligible PR workflow. Its latest native
`CI result` job must succeed, and its schema-2 result must identify qualified
execution. Each selected artifact is downloaded by its authoritative API ID,
checked against its digest and bounded before use. The ordinary qualifier runs
again against the original lane manifests, native jobs and all receipts. This
includes complete shard/catalog reconciliation and installed-package evidence.
Reuse never promotes an unrelated branch or manual run into PR merge evidence.

A successful reuse summary can be consumed through **one bounded hop** to its
original full producer. The summary's own native aggregate and static receipts
must qualify; every reused reference must match the independently requalified
original lane. A cycle, a substituted original context/artifact, or a second reuse
hop is rejected. Each new summary points directly to the full original producer,
so repeated metadata edits keep the chain flat and retain original identities.

For validation events, static checks and the architecture jobs still execute.
Behavioral jobs, including the application shard aggregate, explicitly skip only
when the planner chose reuse. The final aggregate authenticates current static
evidence and rechecks the original producer and current PR. It then qualifies the
original behavioral receipts with their original run, attempt, job and artifact
identities. `evidence_references[].decision` distinguishes `executed` from `reused`;
old receipts are never rewritten as new execution.

Native reruns can copy prior job records into a later attempt without executing
those jobs. Lanes and the native aggregate use the shared producer resolver:
original execution IDs/attempts remain distinct from new API observation wrappers.
Historical wrapper claims are checked against native history at their recorded
attempt before producer identities are compared. Unknown copies require fresh
evidence; a missing later artifact is not proof that a job was copied.

If planning cannot establish reuse, ordinary validation executes. If proof becomes
unavailable **after** jobs skipped, `CI result` fails rather than inventing success;
rerun with reuse disabled to obtain new full evidence. A current aggregate failure,
cancellation or failed artifact upload remains a failure even if local JSON passed.

## Operating and measuring it

Set repository variable **`CI_REUSE_ENABLED=false`** to disable receipt reuse.
Proved source supersession remains independent of reuse.
Otherwise behavioral validation executes normally. Remove
the variable or set it to `true` to enable qualified selection again.

The `ci-reuse-plan--attempt-N` artifact records the decision, bounded reason,
attempt history, observed current PR and lookup time. Final `CI result` evidence
records current and original attempts, lane decisions and links to original
artifacts. Its summary shows planning/requalification overhead, fresh/reused lane
counts and historical shard command time. Historical work is not a measured net
saving. Compare lookup cost separately from avoided duplicate execution. Title/body events use this same planner and retain the same evidence summary.

The initial policy is exact and conservative. A new merge object, changed
base/runtime, old schema, expired evidence, incomplete history or deeper reuse
chain falls back to full execution. Provider-live and time-sensitive external
behavior is not qualified by matching Git inputs.

This workflow preserves both required names, `CI result` and `Hexagonal
architecture`. It does not change the repository's loose merge-freshness setting.
The PR delivery operations must still inspect current checks/reviews before an
actionable handoff; a tested base can advance after an observation.

## Verification boundaries

Shared scenario tables cover candidate/input substitution, runtime drift and
unknown runtime, latest-attempt ordering, cancelled metadata bursts, changed live
PR state, explicit skipped-lane qualification and malformed/missing proof. Planted
production faults demonstrate that the corresponding guards detect their intended
failure while healthy controls qualify. The pre-draft record links those receipts.

Hosted witnesses establish event delivery, queue behavior, real API/artifact wiring
and required-check association. Local policy tests alone do not establish those
platform behaviors. Native vendor acceptance remains separate from protocol fixtures.
