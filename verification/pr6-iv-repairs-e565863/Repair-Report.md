# PR6 IV01–IV04 repair report

**Candidate:** `e565863ae72180571678c8f6118ee6e00b129e2e`  
**Tree:** `474de55c5947890ab4092daeca7ff39f9bbbc400`  
**Product branch:** `repair/independent-findings` · [PR #6](https://github.com/xormania/xorchestrate/pull/6)

IV01–IV04 are repaired in the examined deterministic scope. The original four
witnesses were reproduced on the reviewed baseline, then replayed against the
combined frozen candidate. Focused adversarial work found additional instances
of the same scope, completion and identity problems; those were repaired and
retained as regressions before final measurement. No known finding remains open
within this scoped review. The recommendation is to submit this exact candidate
to the next fresh-session independent verification. This session did not merge,
release, change Jev, or perform authenticated vendor acceptance.

This is an **implementation and repair verification report**. Its separate-agent
cross-reviews happened within the same session and do not replace the later
fresh-session review. Authenticated vendor acceptance remains the user's planned
local work; it is not an invented prerequisite for completing these repairs.

Paths below identify files in the restored evidence archive unless stated
otherwise. `REPLAY.md` reconstructs the archive, source and measurements.

## Basis and identities

The input review is the immutable publication
[`3ac2342b6655f1a30652380c85ceb55262b7ad0c`](https://github.com/xormania/xorchestrate/tree/3ac2342b6655f1a30652380c85ceb55262b7ad0c/verification/pr6-independent-ef75675).
Its README, full report, closure verdicts and replay instructions were read.
The archive SHA-256 is
`d161f2318727b8b52aa5dd78fa91d6d5ebc1aa4c12103ec8e385b328715833dc`.
The published blobs, archive CRC/content inventory and all 57,328 restored files
were revalidated (`evidence/provenance/input-verification.json`). Original review
scripts and raw evidence were not edited. Byte-identical minimal replay inputs
are copied under `inputs/`; their original paths and hashes are retained.

The reviewed product was `ef7567516520543970af55b6f19534cb66eda357`, tree
`a0813aae760e6e5bb6245693bd92b7e7c321348b`. The starting PR head still matched it;
there was no later product work to reconcile. The repair branch was updated by
ordinary fast-forward after a fresh remote ancestry check. No verification
publication was merged into the product branch. The PR remains stacked on
`b0a00f1aed08d4ad8e066de8eb51f94e21da9f4a`.

Final product and separately checked-out checker use the candidate above. Their
full file inventories, actual imported module paths, clean before/after states,
brief, criteria, fixture, configuration and content bindings are retained in
`evidence/final-e565863/`. `input-identities.json` indexes the exact hashes.
`evidence/root/repair-source.bundle` preserves final, reviewed, base, component
and hosted integration refs. Product changes since the reviewed baseline are
also retained as `evidence/root/final-product.patch` and commit history.

## Closure matrix

| Finding | Repaired invariant and decisive evidence | Verdict |
|---|---|---|
| IV01 — historical disclosure | Current same-action inspection policy evaluates each typed record using its actual session/work/provider/operation scope. Original same-work denied-session leak is absent; authorized records and raw trusted history remain intact on both stores. Final witness and seven adversarial probe programs also cover provider descriptors, requested phases, conflicting keys, measurements, catalogs and direct operation history. | Closed in examined scope |
| IV02 — false completion | Idle requires every observed turn, native continuation, submission and callback to finish. Codex BA and Grok ACB remain active while work is held. The checker independently vetoes unresolved callbacks, durable effects and native turns, including forged aggregate idle. Final original six schedules and four independent tail schedules pass. | Closed in examined scope |
| IV03 — identity contradiction | Requested/completed callbacks and independently observed native calls must each agree with applicable session, conversation, incarnation, call, actor and work bindings. One-field wrong/null identity mutations reject; pristine controls pass. Lifecycle inner-session and explicit actor/source contradictions also reject. | Closed in examined scope |
| IV04 — false attribution | Detection credit requires an unambiguous raising instruction inside the intended assertion. Same-line unrelated raise and separate-line controls receive no credit; genuine assertions remain detectable in both calibrators. All twelve original source faults remain valid and detected. | Closed in examined scope |

`closure-matrix.json` gives exact before/after, final, regression and adversarial
paths for each row. These verdicts cover exercised invariants, not every possible
policy configuration, vendor protocol or runtime schedule.

## What changed and what challenged it

**Inspection.** Projection follows explicit application-owned result schemas;
it does not reinterpret arbitrary provider/user payload keys as store records.
Historical `get_work`, acceptance views, nested native history, typed operation
results and message routing provenance receive current per-record authorization.
Direct work, acceptance and operation inspection use the same projection and
recheck their complete snapshots and authorization basis before return. Selected
denied records reject the inspection; visible aggregates retain allowed records.
Stored observations and trusted internal receipt/acceptance inputs stay intact.

Cross-review found that provider-bound invocations and measurements also needed
their retained operation descriptor, and direct operation reads needed the same
nested projection. Requested/unknown callback phases do not always have a result
operation ID, so their deterministic lookup uses the original executor and
checks the original request fingerprint. A conflicting old public operation
cannot lend its scope to a rejected private request. These are actual API/store
probes, including different inspectors and grant restoration, with no provider
reinvocation. Earlier component closure conclusions and the counterexamples that
superseded them are preserved. See `evidence/iv01/` and
`evidence/adversarial/iv01/REPORT.md`.

**Completion.** Codex now tracks each pending turn rather than a single slot.
Shared submission/callback accounting keeps running callbacks visible after an
early native completion. Unknown outcomes retain uncertainty. Grok tracks all
native prompts through their continuation tails; receiving a correlated tool
call or finishing a newer prompt does not finish an older one. ACP/Claude
callback controls and every three-callback completion permutation remain valid.

The first expanded implementation still exposed a Grok tail bug: with all three
callbacks returned, completing A then C falsely hid held native B. An independent
subprocess/MCP probe demonstrated this before the prompt-set repair, and the same
probe passed afterward. The checker now reconciles raw native start/end and
submission identities in addition to matching terminal callbacks and checking
durable native calls/operations. It preserves legitimate response-before-start
and single anonymous Claude controls while rejecting unmatched ends, ambiguous
overlap, unresolved submissions and end evidence later than claimed completion.
See `evidence/iv02/REPORT.md` and `evidence/adversarial/iv02/README.md`.

**Identity.** Normalization retains independent callback identity, terminal
callback evidence, durable session identity and explicit actor provenance.
It does not replace conflicting values with native observations. Main joins
validate both sides and require requested callback support for completed effects.
Lifecycle raw joins also reject present-but-conflicting nested session or actor
claims, while retaining compatible older records that genuinely lack optional
fields. Independent mutation probes covered duplicate/reassigned observations,
unknown sessions, missing completion, explicit null and unrelated/stale history.
See `evidence/iv03/SUMMARY.md` and `evidence/adversarial/iv03/REPORT.md`.

**Attribution.** The observer uses the terminal frame and executed bytecode
instruction's full source span, requiring one corresponding AST assertion.
An explicit raise beside a true assertion, an exception during assertion
evaluation, a helper frame, or missing/ambiguous positions cannot earn credit.
Real assertions still work under rewriting/plain mode, multiline/inline forms,
Unicode prefixes and multiple assertions on a line. Independent review added
14 rewritten/plain outcomes around raises, reraises and exception evaluation.
The twelve existing source fault detections were independently valid before this
repair; they were not disproven. See `evidence/iv04/REPORT.md` and
`evidence/adversarial/iv04/REPORT.md`.

## Frozen-candidate verification

- Full active suite: **1,127 passed + 164 subtests**, zero failures, errors or skips.
- Focused preservation/repair suite: **594 passed**, zero skips.
- Actual three-adapter subprocess protocol: **46/46 passed**, exact parsed raw-report recomputation.
- All **12 original source faults** detected; independent source/assertion audit passed.
- Static lint/security checks, compilation, **13 optional-toolchain tests**, and live tmux fixture passed.
- Candidate and checker source remained clean and unchanged. See `evidence/final-e565863/summary.json`, XML, raw protocol files and command ledger.

The full run measures the final commit and imports the checker from a separate
clean checkout of that commit. Original witness scripts receive semantic checks,
not merely exit-code checks. In particular, the unchanged IV01 script exits 1
at its old assertion that a disclosure must exist; the separately retained
two-line closed-expectation adaptation exits 0 with both-store controls.
All fresh raw composed reports are recomputed from their retained observations.

The original twelve source calibrations retain 36 passing controls, 19 intended
assertion failures and 17 unaffected mutant passes, with zero calibration errors
or skips. Raw observer instruction spans, JUnit outcomes, exact mutations and
source hashes are independently audited. Additional sustained known-bad subjects
and unrelated-error controls live in the normal regression suite.

[GitHub CI run 40132785317](https://github.com/xormania/xorchestrate/actions/runs/40132785317) passed **all six jobs**, without a rerun:

- Python 3.11 and 3.12 each passed **1,127 tests +164 subtests**, with zero skips; the new attribution cases executed successfully on both versions.
- Hosted protocol passed **46/46** and recomputed exactly from raw data. The hosted twelve original calibrations retain the same 36/19/17 control/assertion totals.
- Static checks, **285 recovered toolkit tests**, **825 historical runtime tests** (zero skips/not-run), and live tmux acceptance (**22 tests +11 fixture checks**, verified cleanup) passed.
- GitHub executed integration commit `cf424865c3e86f93b98e015621158c995d5ae187`. Its tree exactly equals the product tree, and its independently verified parents are the expected base and final candidate. The shallow checkout's empty parent line is supplemented by retained Git commit-object/API proof.

All five artifact ZIPs, API metadata, decoded job logs and extraction/audit results are retained under `evidence/ci/40132785317/`. Hosted configuration identity differs from local as expected and is separately recorded. CI artifacts are copied into this durable evidence package, so their Actions retention expiry does not remove them.

## Coverage and preservation

All original 89 selectors and the frozen 165-selector H/B/A mapping remain.
The final supplementary map adds 46 distinct test functions, for 211 mapped
selectors; parametrized case totals are reported separately. Counts document
coverage and are not closure targets. The 46 protocol cases and twelve original
fault definitions remain intact. H1–H8, B1–B5 and A1–A2 are explicitly reconciled
against raw local and hosted XML in `evidence/provenance/`.

The audit also compares source beneath preserved names. One frozen selector's
body adds required completed-callback evidence to its positive fixture; its
negative assertions remain. Supporting fixtures similarly add terminal/turn
proof, and the native polling helper retries only explicitly `retry_safe`
snapshot conflicts. Existing pending-turn assertions now inspect the set of
turn IDs. These adaptations are disclosed in `final-source-delta.json` and
`final-existing-test-support-changes.diff`, not hidden behind unchanged names.

The seven previously closed findings — F02, F03, F04, F05, F06, F09 and F10 —
retain their focused regressions and full-suite coverage. Earlier F01, F07,
F08 and aggregate-completion openings correspond to IV01, IV04, IV03 and IV02
and are addressed above. Configurable policy, provider/driver selection,
hexagonal ports, external process ownership, durable replay/recovery and work
acceptance remain supported. No Jev or imported historical runtime source was
changed; hosted preservation jobs exercise the historical suites.

Historical raw reports were also rechecked with the final checker, without
editing their original bindings or saved reports. The retained ef local,
ca3 hosted and prior independent fresh ef runs still pass 46/46. Their report
differences are evidence sequence numbers caused by retaining terminal callbacks.
Original b0 and superseded d4 remain incomplete: 44 cases pass and two completion
cases fail. These historical rechecks receive no fresh final-product credit.
See `historical-report-differences.json` and the `history-*` provenance outputs.

## Failed attempts, limitations and recommendation

All failed/intermediate work is preserved and labeled: baseline defect witnesses,
the Grok tail counterexample, provider/operation omissions, requested-phase and
conflicting-key counterexamples, test matcher/polling/timing adjustments, the
initial tail serializer failure, a provisional dirty-source cross-review,
the source auditor's initial inherited-selector resolution error, and a final
witness wrapper that initially omitted two newly added probe files. That wrapper
failure is retained and the affected scope review is replayed with the complete
script set; the four original witnesses themselves passed. Component
manifests and superseded closure statements retain their own source identities.
`command-run-ledger.json` indexes the exact command records. None of these is
silently substituted for final-candidate execution.

The evidence establishes bounded deterministic product and checker behavior.
It does not establish authenticated Codex, Claude or Grok vendor behavior or
exhaust all concurrent schedules and configured policies. The archive includes
raw observations, minimal probes, controls, source recovery, replay instructions
and an assignment for the next independent session. Proceed with that review of
the frozen candidate; retain the user's separate local vendor acceptance plan.
PR #6 has been updated for review, with no merge or release performed.
