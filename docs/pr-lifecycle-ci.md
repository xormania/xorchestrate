# PR lifecycle validation

Source changes receive validation while ordinary PR metadata changes receive a
small observation. Deterministic behavior can reuse a qualified execution of the
exact candidate. GitHub remains the authority for checks and PR state; this does
not mark a draft ready or merge it.

## Events and surviving validation

The CI workflow handles `opened`, `synchronize`, `reopened` and `edited`.
Opening a draft and pushing source changes retain the full verification path;
the existing independently guarded documentation-only classification remains.
Marking a draft ready does not rerun CI: the checks for its existing candidate
remain authoritative. Full green verification still precedes owner handoff.
Master pushes and manual runs execute full validation.

A positively identified title/body edit with no base-retarget change in its event
payload runs one short metadata summary, without checkout, dependency setup,
tests or Jev. Explicit base-retarget changes and unknown edit payloads use the
ordinary validation path. On metadata events,
the skipped aggregate jobs have distinct names, not `CI result` or `Hexagonal
architecture`: a skipped job must not publish a successful required check over
an earlier failed candidate. Metadata observations do not certify current-base
freshness or change the existing validation verdict. The target branch may have
advanced independently: a title/body event still preserves the source's existing
checks and does not certify its new merge candidate. Strict up-to-date checking
remains disabled; master-push integration validation remains enabled.

Validation concurrency is scoped to repository, PR number, source head and event
base. A new run may cancel the same candidate's older run; a delayed old-source
event or manual rerun cannot cancel a newer source or base candidate. Metadata
events have their own group, so editing a description cannot cancel validation.
Non-PR runs retain cancellation by ref. Already-running work for an older
candidate can finish. The supersession check below avoids starting its costly
lanes when a surviving replacement can be proved; there is no custom cancellation
service.

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
identities. They positively identify cancelled metadata runs with **zero jobs in
their first attempt**, after rereading the current run/attempt, as never-executed
observations. An empty later retry cannot erase prior behavioral work.
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
Metadata routing and proved source supersession remain independent of reuse.
Otherwise behavioral validation executes normally. Remove
the variable or set it to `true` to enable qualified selection again.

The `ci-reuse-plan--attempt-N` artifact records the decision, bounded reason,
attempt history, observed current PR and lookup time. Final `CI result` evidence
records current and original attempts, lane decisions and links to original
artifacts. Its summary shows planning/requalification overhead, fresh/reused lane
counts and historical shard command time. Historical work is not a measured net
saving. Compare lookup cost separately from avoided duplicate execution. Ordinary
title/body events now retain their small summary without invoking the planner.

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
