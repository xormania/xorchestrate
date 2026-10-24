# Xorchestrate repair handoff — F01–F10

Prepared for xor, 2026-10-24. Intended for a fresh Work coding session.

## What to provide

Attach this document and `Xorchestrate-Independent-Review-Package.zip`. Give the session access to [xormania/xorchestrate](https://github.com/xormania/xorchestrate) and its GitHub Actions runs/artifacts. Repository write access is needed to publish the repair branch and draft PR.

The ZIP already includes:

- `review/owner-brief.md` — original complete implementation brief and H/B/A requirements.
- `deliverables/Xorchestrate-Independent-Review.md` — report describing all ten findings and their limits.
- `review/REPLAY.md` — source recovery and reproduction instructions.
- `review/findings.json`, `review/requirement-matrix.json` and `review/package-manifest.json`.
- Track and lead reproductions, controls, logs, raw inputs and outputs.

Separate copies of the original brief and report are optional. The Jev experiment prompt and Jev API key are not needed for this repair task. Authenticated vendor-harness acceptance remains xor's planned local work.

Paste this launch instruction:

> Read Xorchestrate-Repair-Handoff.md in full and execute its Repair prompt. Use the attached independent-review ZIP, including its embedded owner brief, report and reproductions. Repair F01–F10, retain sustained failure calibration, and deliver an exact-candidate evidence package for another session to verify. Continue through GitHub-hosted CI for checks the Work host cannot execute. Preserve the parallel Jev experiment and do not merge.

## Repair prompt

### Goal and authority

Repair the ten independently reproduced defects in the active Xorchestrate application and its verification machinery. Preserve the complete intended behavioral capabilities and configurable hexagonal architecture. Produce evidence that another session can independently replay and challenge.

xor's current instructions govern. Read the original brief and independent report in full. The brief supplies the complete H1–H8, B1–B5 and A1/A2 requirements; this task applies the review's counterevidence and the sustained-calibration requirement. Historical policies and incidental fixtures are not automatic requirements. Do not reduce scope to make tests pass or reopen settled architecture choices without a specific unresolved conflict supported by evidence.

This is implementation and verification work. You may make necessary source/test/documentation/workflow changes, create an isolated repair branch, commit and push it, run CI, and publish a draft repair PR. Do not merge. Preserve unrelated changes and the original submitted evidence.

The Jev correlation experiment is running independently. Do not wait for it, edit its branch/corpus/answer key, or change its historical inputs. This repair produces a new candidate; it does not retroactively change the reviewed candidate. Incorporate later Jev suggestions as hypotheses only when available and relevant, without making them a prerequisite for repair.

### Establish current state and baseline

Inspect repository instructions, current PR/branch state and local changes before choosing the repair base. At handoff preparation, [PR #5](https://github.com/xormania/xorchestrate/pull/5) was open, unmerged and draft, with:

- Base branch: `integration/hexagonal`.
- Base commit: `30254fd0f184b7e31aa7e0896347429004933ed4`.
- Head branch: `repair/behavioral-preservation`.
- Reviewed/current head at that check: `b0a00f1aed08d4ad8e066de8eb51f94e21da9f4a`.
- Reviewed tree: `0f4034870f40fb5a1d022c9f35ad0b866236455f`.
- Submitted evidence publication: `57f01ab3c18ecfa0e062ab3e2a1fc567068dff88`.
- Embedded owner brief SHA-256: `41545d3fab334e432f58f1f1b1367c70bed29bf2c39c1280c3eacd9cbdc783ad`.

Recheck these facts; do not assume the branch has remained stationary. Compare intervening changes with the reviewed candidate, retaining any existing fixes. Use a dedicated repair branch/worktree from the appropriate current state, preserving concurrent work. State the draft PR's base and relationship to PR #5 clearly.

Extract the review archive into an isolated directory, verify its manifest and retain the original archive unchanged. Read `review/REPLAY.md`. Reproduction scripts may write beside themselves: run copies with new stores, endpoints and output paths. Recover pinned source as required; adapt obsolete workspace prefixes without changing the scenario's semantics.

Reproduce each applicable defect with its valid control before changing the relevant code. A current finding already fixed by intervening changes still needs direct regression evidence. If Work blocks a reproduction, retain the exact error and continue the corresponding measurement in a capable hosted runner.

**An original reproduction script may exit zero because it successfully demonstrated the bug.** Keep command exit, reproduction outcome, product behavior and checker verdict distinct throughout the work.

### Repair the mechanisms

Use the complete report and raw reproductions as the authoritative description of demonstrated behavior. The following table is an orientation, not a replacement for those details.

| Finding | Required repair behavior |
|---|---|
| F01: cross-work disclosure through delivery inspection | Scope or independently authorize every publicly returned record under the selected policy. Prove allowed inspection cannot expose denied messages/evidence on both stores. Preserve trusted internal verification's access to the evidence it needs. |
| F02: old callback acquires replacement authority | Preserve the observed callback's original actor/work/session incarnation binding, or reject/retain uncertainty as appropriate. Reconnect must not grant it the replacement principal's authority. Exercise the actual bridge/application/store composition. |
| F03: applied effect becomes “not started” after reconnect | Preserve truthful effect certainty and recoverability across connection changes. A post-effect connection error cannot establish non-execution. Check the downstream recovery fence and actual effects of subsequent sends, not only the error name. Preserve legitimate observation/control during uncertainty. |
| F04: adapter caches replay stale acceptance | Ensure every built-in adapter's completed-cache/semantic-replay path respects acceptance currentness. Preserve safe duplicate handling and historical records without reexecuting side effects to refresh a read. Test through Codex, Claude and Grok adapter paths. |
| F05: material evaluator inputs omitted from acceptance binding | Bind assessments and current acceptance to the material inputs actually consumed, including applicable context/evidence. Cover changes during and after assessment and after acceptance. Retain the benign title-change control; avoid indiscriminate invalidation on unrelated changes. |
| F06: one Claude Stop declares global idle | Account for outstanding overlapping work and its identity. One completed callback must not establish idle while another relevant callback remains outstanding. Preserve the actual hook/subprocess/MCP path in the regression. Installed-Claude reachability remains a separate native measurement. |
| F07: unrelated exceptions earn fault-detection credit | Both calibrators must attribute detection to the intended executed assertion/condition. Marker text appearing in a traceback source line is insufficient. Runtime import failure, timeout, collection failure and skip must not earn target-detection credit. Preserve the original twelve genuine detections. |
| F08: normalization erases argument disagreement | Preserve native and executed-application observations separately and reject inconsistent request/effect evidence. Test from raw records through normalization and the independent checker. |
| F09: older success masks newer uncertainty | Preserve attempt sequence/status and adjudicate the applicable latest attempt. Historical success must not erase later unknown outcomes. Invalid evidence must not become unqualified success. |
| F10: required unfinished-work reply timing does not affect pass | Enforce the applicable unfinished-work timing requirement in the saved-run verdict. Preserve the distinct busy-tool semantics; do not invent token-generation interruption requirements. |

Inspect sibling paths for the same mechanisms: all relevant cache returns, inspection endpoints, identity replacements, post-error transitions, evidence joins and alternative stores/adapters. Record which paths were examined and exercised. Source-derived concerns, such as permission revocation through a cache, remain hypotheses until demonstrated; investigate materially related concerns without turning every discovery into a new requirement.

Keep enforcement responsibilities clear across ports/adapters. Do not “fix” these findings by removing supported configurations, disabling concurrency/reconnect/caching wholesale, introducing universal approvals or fixed evaluator thresholds, or weakening the accepted requirement. No exactly-once guarantee follows from F03. Preserve A1's composable responsibilities and A2's binding at applicable boundaries.

### Retain sustained failure calibration

Maintain two complementary forms of evidence:

1. **Product regressions:** the scenario fails its behavioral assertion on the vulnerable implementation and passes on the repaired implementation, with valid controls intact.
2. **Known-bad calibration:** isolated source faults, controlled schedules or corrupt raw evidence deliberately violate the target guarantee. The repaired verification machinery must continue to detect that violation for the intended reason. Valid and unrelated-failure controls must retain their distinct outcomes.

Keep the known-bad definitions and replay instructions as reusable, versioned tests. Prefer behavioral faults that survive refactoring over brittle source-string edits; if an existing mutation depends on source structure, verify that it actually activated its intended fault. Never count an unapplied mutation, setup failure, unrelated exception or skipped test as detection.

For each retained fault, identify the requirement, activation condition, expected observable effect, intended assertion/rejection and relevant passing control. A deliberately faulty subject remains faulty while the calibration suite succeeds by detecting it. Do not delete or relabel the fault to obtain green results.

Use controlled event gates for asynchronous cases, with isolated resources and ownership-aware cleanup. Assert actual persisted state, returned evidence and resulting effects. Specifically ask: **which successful return paths, including cache hits, and which post-error transitions could bypass this invariant?**

Keep the compact deterministic regression/calibration collection in normal CI as appropriate. Heavier composed and native checks retain their applicable execution lanes. Record selection/coverage explicitly rather than silently shrinking it for speed.

### Verify in Work and GitHub-hosted CI

Probe this Work session's actual runtime. An earlier measured Work host could allocate PTYs and run a tmux binary but could not create AF_UNIX sockets; that is evidence about that host, not an assumption about every Work session. Check actual socket creation and a private-socket tmux create/send/capture/cleanup cycle. An installed binary or a version string alone is not readiness.

Use GitHub-hosted CI for operations the Work host cannot perform. Reuse the repository's applicable Python 3.11/3.12, tmux, lint and composed acceptance lanes after checking their current definitions. Publish the repair branch and inspect the actual run logs and artifacts. If adding a small workflow is necessary, choose a supported branch-scoped trigger; do not make a new dispatch-only file on a nondefault branch an avoidable execution blocker. No merge is authorized to enable testing.

Bind hosted results to the repaired commit/tree or a verified integration tree with known parents. Old green runs for PR #5 are baseline evidence, not repair evidence. Hosted execution may satisfy a check unavailable locally, but label its origin accurately. Environment failures receive neither behavioral-pass nor product-defect credit.

Run focused regressions and calibration controls, then the appropriate complete active suite, composed protocol verification and required repository checks. Preserve original coverage; explain any legitimate inventory change. List every required case executed, skipped, blocked or pending. Do not replace a required live/composed assertion with a mock and claim equivalent coverage.

Treat F07–F10's checker repairs as first-class implementation work. The independent checker must derive its conclusion from retained observations; it must not merely accept production verdict booleans. Keep raw disagreements, ordering and uncertainty inspectable. Recompute saved reports after checker changes.

Use independent review within this session where available to challenge the corrected checker and invent fresh counterexamples at the repaired boundaries. Distinguish that review from the later fresh-session verification xor will perform. Keep candidate and checker identities separate and exact.

Authenticated vendor inference is not required to complete these deterministic repairs. Do not request Jev or vendor API keys for them. Retain the local native acceptance procedure and add the focused replay, overlapping-turn and reconnect schedules from the independent report. Mark installed-vendor/native acceptance pending unless actually executed with valid evidence.

### Required delivery

Publish the repair branch and a draft PR with a concise explanation of changed behavior, validation and remaining limits. Do not merge or claim complete native acceptance.

Provide a readable repair report and machine-readable closure matrix covering F01–F10. Each entry must identify:

- Original vulnerable candidate, reproduced failure and valid control.
- Root cause and affected composed/sibling paths.
- Repair and retained product regression.
- Sustained known-bad calibration and unrelated-failure controls.
- Exact repaired candidate/checker/configuration, commands, results and raw evidence locations.
- Independent challenge performed and remaining environmental/native limitations.

Update the H/B/A evidence mapping so finding closure does not substitute for the broader owner requirements. Distinguish implementation present, deterministic coverage verified and native behavior still pending.

Deliver an evidence package containing replayable scripts/fixtures, fault definitions, logs/JUnit, raw observations, reports, manifests, CI run/artifact URLs and reproduction instructions. Preserve original and new measurements separately. Record working changes and any untracked inputs that affected measurement; source and checker must be recoverable. If evidence publication creates a later commit, distinguish it from the measured code commit and prove the relevant relationship.

Supply commands another session can run to verify the repaired behaviors, recalibrate the checker with known faults, and inspect saved results without live vendor credentials. Explain any genuine blocker precisely and continue all independent authorized work. Do not ask xor to re-decide already established architecture choices.

Start by inspecting repository state and the review package, then carry the repair through implementation, verification, draft publication and evidence delivery.

## Handoff note

This document was prepared from the supplied review/evidence package and a read-only check of PR #5. Preparing it did not change the repository or execute repairs. The original review's counterexamples and their stated limits remain the basis for the task; the new session must establish its own measurements.
