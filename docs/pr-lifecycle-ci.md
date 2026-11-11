# PR lifecycle validation

PR activity receives a current candidate observation while deterministic behavior
can reuse a qualified execution of that exact candidate. GitHub remains the
authority for checks and PR state; this does not mark a draft ready or merge it.

## Events and surviving validation

The CI workflow handles `opened`, `synchronize`, `reopened`, `edited` and
`ready_for_review`. Master pushes and manual runs execute full validation.
The existing independently guarded documentation-only classification remains.

PR concurrency is scoped to repository, PR number, source head and event base.
PR runs do not cancel an active run. Metadata observations for the same event
candidate wait behind useful validation; pending observations may replace each
other. Different candidates have separate groups, so a delayed old event cannot
cancel the current source candidate. This can leave obsolete work running.
A finite burst leaves its last pending observation eligible to run.
Non-PR runs retain cancellation by ref.

The actual checkout stays bound to the event's GitHub check association. Queue
startup observes the live PR before selecting reuse; collection ends with another
observation. A changed source, target, tested base or merge makes reuse unavailable.
The workflow never checks out newer code under an older event's check. These are
timestamped observations, not a continuing atomic view of GitHub. A known stale
event currently executes ordinary validation of its identified old checkout;
its lifecycle evidence says `superseded_candidate`. This preserves truthful
evidence and current-candidate liveness, but does not eliminate all obsolete work.

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

Static checks and the architecture jobs still execute for the current event.
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

Set repository variable **`CI_REUSE_ENABLED=false`** to disable reuse. Events and
fresh observations remain, and behavioral validation executes normally. Remove
the variable or set it to `true` to enable qualified selection again.

The `ci-reuse-plan--attempt-N` artifact records the decision, bounded reason,
attempt history, observed current PR and lookup time. Final `CI result` evidence
records current and original attempts, lane decisions and links to original
artifacts. Its summary shows planning/requalification overhead, fresh/reused lane
counts and historical shard command time. Historical work is not a measured net
saving: edited/readiness events add useful observations that the old event set
did not run. Compare their cost separately from genuinely avoided duplicate work.

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
