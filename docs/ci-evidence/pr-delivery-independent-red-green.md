# Independent PR delivery verification — 2026-11-10

The independent test author derived delivery scenarios from the PR-pipeline
specification §7 and failure review F04–F10 before reading the implementation.
The implementation author supplied the port/result shapes. Tests use the shared
application builder and memory/SQLite store selector, existing Git helper,
provisioned project fixture, and B's candidate-context fixture.

Final production subject: `182044c` (including the effect-error repair and
candidate-provenance work). Final test source: `1d69a5c`. The
[structured evidence](pr-delivery-independent-red-green.json) records exact input
and mutant source hashes, literal fault substitutions, commands, exit outcomes
and concise failing assertions. Faults ran only in the independent disposable
worktree; healthy source was restored after every probe.

## Observed behavior

The final healthy control passed **75 cases**. Every one of **16 final planted
faults** produced the intended assertion failure; the restored healthy control
passed the same 75 cases. These counts are execution evidence, not a coverage or
quality score.

| Boundary | Independent observable guarantee | Final fault witness |
| --- | --- | --- |
| Application/MCP and memory/SQLite | Lost remote creation response remains unknown; same-key replay and another-key target contention cannot repeat the write; authoritative reconciliation permits historical replay | Earlier unknown-as-normal-JSON and target-exclusion probes; final healthy control retains these scenarios |
| Mutable PR observation | Checks, attempts, threads, readiness and target identity can supersede a handoff while Git SHAs stay fixed; old journal reads remain historical | Earlier disabled-observation comparison; final healthy scenarios |
| Review applicability | Supplied source head/tree determines applicable versus historical/unverified review evidence; independence is never machine-certified; draft readiness stays separate | Disabled review-tree applicability check |
| Configured state policy | PR target/progress writes use configured operation transitions and leave transition history; a refused progress guard stops before workspace/remote writes | Ignored configured progress engine |
| Actual local Git/worktree | Selected tracked work is checkpointed, unrelated untracked bytes excluded, unrelated staging refused; conflicts retain checkpoint/index; editor races and stale expected heads cannot silently commit unintended bytes | Earlier byte/head/hook probes; final healthy conflict/preservation scenarios |
| Isolated Git publication | Normal push preserves another writer's remote advancement; credentials enter only the trusted bare publication directory; configured proxy/CA settings reach that directory and stay out of candidate processing | Force push enabled; origin guard disabled; publisher proxy/trust omitted |
| GitHub HTTP observation | Required check source identity, newest relevant attempt, producer workflow and retained candidate inputs agree; original captured context remains valid across a legitimate partial rerun | Source association removed; workflow association removed; attempt ordering reversed; valid partial rerun rejected |
| Retained evidence transfer | Authoritative digest required; duplicate auxiliary members, traversal/symlink members and expansion budget rejected; API token absent on signed artifact redirect | Digest optional; duplicate/unsafe/expanded guards disabled; token forwarded |
| Collection completeness | REST exhaustion and incomplete review-thread pages cannot become a complete no-blockers result | Exhaustion returned as complete; healthy GraphQL budget scenario |
| Configured product composition and HTTP creation | Built-in PR provider discovers/invokes through a provisioned project; actual POST carries literal supplied metadata and `draft:true` | Draft field changed to false; healthy configured-provider scenario |

Initial independent tests also exposed a real incomplete-observation defect:
`complete=false` retained `technical_validation=passed`. The same regression
failed for memory and SQLite before repair and passes in the final control.
Earlier probes retain their original source hashes and remain supplemental
evidence; they are not mislabeled as execution of the final commit.

The duplicate-archive fixture was refined after a calibration probe showed that
duplicating `result.json` also triggered the independent result-count guard.
It now duplicates an auxiliary member, so disabling the duplicate-member guard
produces the intended red result. Unsafe archive controls carry the correct
authoritative digest, ensuring content guards are actually reached.

## Reproduction and limits

Run the three declared modules with the repository's locked validation runtime:

```bash
python -m pytest -q tests/hexagonal/test_pr_operations.py tests/hexagonal/test_pr_workspace.py tests/hexagonal/test_pr_github.py
```

These local witnesses used Python **3.12.14**, as recorded in the structured
evidence; CI's required interpreter baseline remains 3.12.16.

Apply one recorded substitution in an isolated copy, execute its recorded
selector, then restore the exact healthy source and rerun the control. A setup
failure is not accepted as red proof. All final probes recorded assertion
failures rather than missing imports or unavailable services.

The HTTP fixtures run on loopback. Git refresh/push/conflict scenarios use real
disposable repositories. HTTPS credential tests use inert canaries and a
controlled transport after actual Git preparation. These witnesses do not prove
that a product-configured GitHub credential can push/open a PR and trigger the
eligible required check. Connector publication and hosted CI are separate
evidence. The implementation's full test-kit/static checks and pre-draft review
are separate delivery steps owned by the candidate integrator.

## Narrow follow-up: ordering retries across workflow runs

After the preceding candidate was checked, the integrator identified a concrete
F07 defect: ordering by `(run_id, run_attempt)` treats an older-created run's new
retry as older than a later-created run's original attempt. The hosting adapter
now uses the authoritative attempt endpoint's timezone-aware `run_started_at`.
Missing, inconsistent or tied ordering cannot certify the latest successful
attempt. Completion time does not establish attempt order.

Independent test commit `5fb9a32` adds the exact lower-run-ID/later-retry
counterexample, the inverse slow-old-completion control, and an unavailable-start
case. The same tests ran against the **actual defective adapter at `182044c`**:
two failed on the intended assertions and the healthy inverse case passed.
After restoring the repaired adapter, all **21 HTTP cases passed**. The
[separate regression record](pr-delivery-attempt-ordering-regression.json)
retains both source hashes, commands and assertion excerpts. This is affected
HTTP verification of the narrow repair; it does not relabel the earlier
75-case/16-fault execution as execution of the later source.

## Focused review repairs: remote arguments and late staging

The pre-draft reviewer demonstrated two concrete workspace defects at `7c234d4`.
Independent test commits `36f0ce5` and `c5cf8a6` reproduce them against that
actual reviewed implementation, using real Git and an inert executable canary:

- A candidate remote value beginning `--upload-pack=` reached `git ls-remote`
  as an option and executed the candidate program from the trusted publisher.
  The regression requires rejection before any publication command and verifies
  that the canary never executes.
- Another writer could stage `shared.txt` immediately before the checkpoint's
  commit, after the preceding staged-diff inspection. The checkpoint absorbed
  the unrelated bytes. The regression checks the actual checkpoint tree and
  requires the external bytes/staging and selected work to remain preserved;
  either successful preparation or an explicit recoverable conflict is allowed.

The reviewed predecessor produced **two intended assertion failures** while
**three normal local/HTTPS/preparation controls passed**. After repair, all
**15 workspace cases passed**, including those same regressions and controls.
The [separate source-bound record](pr-delivery-workspace-review-regressions.json)
retains commands, assertion excerpts and old/repaired source hashes.

The repair rejects option-like remotes, terminates Git options explicitly and
keeps local publication processes free of proxy/credential settings. Checkpoints
use the selected immutable tree and a private index, then revalidate and install
the index under Git's standard index lock. The new late-staging test injects the
real write immediately before either `commit` or `commit-tree`, so changing the
commit mechanism does not silently eliminate the attempted interleaving.

These narrow checks supplement the preceding evidence; they do not relabel its
older source hashes. The local/controlled-HTTPS limits above still apply.

## Current branch refs versus unchanged PR metadata

An unchanged PR payload can itself be stale while its source or target branch
has advanced. Repeating that payload is insufficient to establish a current
candidate. Independent test commit `35a3617` gives the loopback API a separate
canonical branch-ref map and advances the source and base independently while
leaving the PR payload and retained tested context unchanged.

Against the actual pre-guard adapter at `232c4cb`, both cases fail on the intended
assertion: the snapshot still claims `complete=true`. The unchanged-ref healthy
control passes. With repaired adapter `82ca388`, all **23 HTTP cases pass**.
The [current-ref regression record](pr-delivery-current-ref-regression.json)
retains exact source/test hashes, commands and failing assertions.

The corrected observation resolves explicit `refs/heads/...` source and target
refs separately. A mismatch prevents a technical clear and records the newer
canonical identities. The tests also require the reported source, tested base
and tested merge to retain their original identities; current branch refs do
not rewrite evidence about an older execution. This is a time-bound observation,
not an atomic promise that a branch cannot move afterward.

## Legal tracked names: NUL-delimited Git path observations

PR #66 [review comment 4530869800](https://github.com/xormania/xorchestrate/pull/66#discussion_r4530869800)
identified that Git's quoted `--name-only` display was compared with literal
selected paths. A tracked `café.txt` could be rejected as unrelated work; tabs
and newlines required the same correction. The narrow repair uses NUL-delimited
Git output for both dirty-path selection and recoverable conflict reporting.

Independent test commit `a0c89ee` reuses the existing real-repository fixture and
Git helper. Its table covers ASCII, Unicode, tab and newline names in successful
and conflicted refreshes. Assertions inspect the actual immutable checkpoint's
NUL-delimited changed paths and file bytes, the literal returned conflict path,
and Git's retained unmerged index. An additional case requires an unrelated
tracked change to remain refused and preserved.

The exact predecessor `ffc49303b3006feb5fc83107a0e562a3bc991e74` produced **seven
intended assertion failures and two passing ASCII controls**. With the repair,
all **24 workspace cases passed**, including the existing custody and credential
isolation scenarios. A separate fault restored only the old conflict parser,
leaving dirty-path parsing repaired: the three non-ASCII/control-character cases
failed on literal conflict reporting while ASCII passed. Restoring the repair
made all four conflict cases pass.

The [pathname regression record](pr-delivery-legal-pathnames-regression.json)
retains input/mutant hashes, the exact narrow source diff, commands and failing
assertions. These are local real-Git witnesses; they require no remote mutation
or credentials.
