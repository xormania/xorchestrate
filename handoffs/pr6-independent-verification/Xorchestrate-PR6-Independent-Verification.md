# Xorchestrate PR #6 — independent verification and adversarial review

Prepared for xor on 2026-10-24. This is a new review assignment, not a verification result.

## Start here

Use a fresh session with this document and `Xorchestrate-Repair-Evidence.zip`. The ZIP already contains the repair report, original independent-review ZIP and owner brief, raw repair measurements, manifests, assertion selectors, recovery helpers, and replay instructions. The Jev v2 package is not needed for this task.

Read the assignment below in full, then execute it. Current explicit instructions from xor govern. Earlier briefs and reports supply intended capabilities, rationale, claims, and evidence; their old implementation assignments are not new work orders.

## Assignment

Independently determine whether PR #6 actually closes F01–F10 and the subsequent aggregate-completion defect, whether its verification machinery can still falsely pass broken behavior, and whether the repairs preserve the intended H1–H8, B1–B5 and A1–A2 capabilities and configurable hexagonal architecture.

Be adversarial toward the claims and the evidence. Try to construct concrete counterexamples through the relevant application, adapter, transport, persistence, and checker boundaries. A green test suite or a confident repair report is evidence to examine, not the conclusion. A clean review is a valid result: do not manufacture findings or turn optional improvements into acceptance requirements.

This is verification, not another repair implementation or a new architectural reconciliation. Do not change the candidate, its retained evidence, or the parallel Jev experiment. You may install dependencies and create disposable checkouts, scripts, fault injections, and fresh measurement outputs for the review. Keep those changes separate and identify them. Do not merge, release, rewrite branches, resolve PR threads, or send review-trigger comments. Report repairs needed with minimal reproductions rather than silently fixing the subject under review.

## Exact subject and inputs

| Item | Pinned identity |
|---|---|
| Repository / PR | https://github.com/xormania/xorchestrate/pull/6 |
| Candidate | `ef7567516520543970af55b6f19534cb66eda357` |
| Candidate tree | `a0813aae760e6e5bb6245693bd92b7e7c321348b` |
| Candidate branch | `repair/independent-findings` |
| Original reviewed base | `b0a00f1aed08d4ad8e066de8eb51f94e21da9f4a` |
| Superseded first repair | `d4bd82946bfb6e78f4ba533d7ed2e01c37ea601e` |
| Evidence publication | `663f0634f59c5d6f6c54910c8f995a6af9e6a490` |
| Evidence branch | `evidence/independent-findings-ef75675` |
| Hosted run | https://github.com/xormania/xorchestrate/actions/runs/40066679349 |
| Recorded CI integration commit | `ca3f79ee6d04c55223fa0c907eeb26f9340d6439` |
| Repair ZIP SHA-256 | `27db9e669dc136c866a37b2f4164471f8ce2bd581532225217711ac062df69fe` |
| Nested original-review ZIP SHA-256 | `67218f0e6a1e180d3368ea25ed8f766a03dc921e96b085b9ee287c1c82b40086` |

Refresh PR metadata and record its current head. If it has moved, retain a clear verdict for the pinned candidate and examine the delta before making any claim about the new head. Do not silently substitute the latest branch tip for the measured subject.

Verify the candidate tree and working state independently. Verify the CI integration tree and parents rather than assuming a PR check ran the candidate unchanged. Verify the evidence publication is the documented child containing only the listed evidence/documentation changes. Bind every result to its product, checker, configuration, criteria, fixture, input, and run identities at the applicable boundaries.

## Preparation and packaging caveat

During handoff preparation, all 29 outer manifest entries matched their byte counts and SHA-256 values; the ZIP integrity check passed. The standalone repair report matched the copy inside the ZIP. Those checks establish package identity only; they do not establish behavioral closure.

**The ZIP contains a historical `source.bundle`, despite text in its replay guide/report saying no Git bundle is included.** Inspection confirmed that this bundle contains `b0a00f1` and `d4bd829`, but does not contain `ef75675` or the later evidence publication. Its advertised repair branch points to the superseded repair. Do not use that branch as the final candidate. Follow the replay guide's GitHub clone/fetch route and explicitly check out the pinned candidate. Preserve the bundle unchanged as historical input; treat the documentation inconsistency proportionately and separately from product correctness. If network access is unavailable, identify the missing final Git objects instead of reviewing the old repair as though it were current.

Before executing supplied helpers, inspect their behavior. Extract into a fresh directory and verify `package-manifest.json` using independent hashing or the inspected `verify_package.py`. Read, in order:

1. `START-HERE.md`, `Xorchestrate-Repair-Report.md`, `final-manifest.json`, and `publication.json`.
2. `original-review.zip`: `review/owner-brief.md`, `review/findings.json`, the original report and `review/REPLAY.md`.
3. `closure-matrix.json`, `requirement-matrix.json`, both selector inventories, `REPLAY.md`, and `PACKAGING.md`.
4. The relevant candidate source and tests, followed by retained raw measurements and the checker implementation.

Extract `measurements.tar.xz`, recover omitted exact Git-backed inputs with the inspected `restore_git_inputs.py`, and run its verification mode. Check the measurement manifest/recovery map and preserve original files. Use fresh output directories. Some original reproduction scripts write beside themselves: copy them into a disposable replay area before running them.

## Required verification

### 1. Reproduce and challenge closure

For each F01–F10, identify the intended behavior, original counterexample, actual repair, regression assertion, activated faulty subject, passing control, and retained result. Inspect the assertion and the code path it reaches. Where original probes are applicable, run them against the original and repaired subjects and interpret their observations. Several original reproduction scripts exit zero when they successfully demonstrate a defect; exit status alone is not behavioral correctness.

Try material variants beyond the exact repair fixtures. Use the actual composed boundary when the claim is about cross-component behavior. If a cheaper focused test resolves a question, use it; if mocks conceal the contested boundary, exercise that boundary. Retain minimal witnesses for new defects and control cases that distinguish the intended failure from a broken environment or probe.

| Finding | Required adversarial focus |
|---|---|
| F01 — inspection scope | Authorized inspection must not disclose denied work, actors, messages, historical aggregates, or nested native evidence. Test cross-work and cross-actor records, historical `get_work` content, and valid authorized projections. Trusted internal receipt joins must still work. |
| F02 — callback authority | A callback observed before replacement retains its original qualified native identity, actor/work authority, and lease. Replacement must not lend it new authority. Test stale, missing, and unbound identities alongside valid callbacks. |
| F03 — post-effect recovery | Reconnect after a possible or completed native effect preserves application uncertainty, inspectable host evidence, and fences against later effects. Test pre-effect reconnect, post-effect reconnect, valid reconciliation, and prevention of duplicate execution. |
| F04 — cached native calls | Identical cached calls revisit current application authorization and acceptance basis without repeating effects. Cover built-in adapters and external bridge composition, owner revisions, revoked authority, and missing durable operations. |
| F05 — assessment applicability | Material consumed work, evidence, actor, and contribution inputs invalidate stale assessments. Challenge boolean/number equality, changes during evaluation or acceptance, and stale replay. Include harmless or unconsumed changes as controls; do not require every edit to invalidate everything. |
| F06 — overlapping activity | A Stop for one turn cannot establish global idle while another turn or callback is outstanding. Exercise duplicates, unmatched stops, anonymous overlap, and uncertainty. Follow emitter → normalizer → checker: explicit post-transition aggregate state is required, and later active/unknown observations must defeat stale idle evidence. |
| F07 — calibration attribution | A faulty subject earns detection credit only when its intended assertion actually executes and fails. Challenge helper `AssertionError`, traceback/source marker text, setup/import errors, timeouts, skips, malformed JUnit, unapplied faults, and ambiguous observer records. Controls must expose false detection. |
| F08 — normalization and joins | Preserve disagreements between independent native calls, application receipts, arguments, and persisted effects. Challenge wrong work/actor/subject, duplicate identities, explicit null, JSON type differences, and plausible callbacks joined to the wrong effect. |
| F09 — attempts and uncertainty | Later unknown, failed, or incomplete attempts must not disappear behind earlier success. Preserve historical uncertainty and reject ambiguous ordering or invalid latest-recipient evidence. |
| F10 — timing | Unfinished-work replies must precede completion by actual timestamp instant. Test equality, timezone offsets, missing timestamps, and late replies. Busy-tool coverage remains submission during a hold followed by the full exchange; token-generation interruption is not an added requirement. |

The closure matrix's F06 replay shortcut names only `test_claude_overlap.py`. That is insufficient for the later completion repair: also inspect and execute `test_completion_evidence.py`, `test_native_completion_state.py`, and the raw-record completion challenges.

### 2. Verify the verifier

Read the normalizer, checker, assertion observer, both calibrators, and criteria/coverage generation. Seek common-mode failures where producer and checker agree because they share an unsupported assumption, or where normalization manufactures the evidence the predicate expects. Separate checkouts alone do not establish independence.

Use both valid controls and deliberately invalid behavior/evidence. Distinguish an intended assertion failure from unrelated errors. Inspect actual JUnit, trusted execution attribution, applied source mutations, and control results; do not accept a marker or summary counter as proof. A mutation that changes semantics beyond the intended defect needs its confound recorded.

Recompute saved final protocol reports from raw observations using their recorded criteria, bindings, and fixture identities. Compare substantive results and inspect discrepancies. Execute a fresh composed protocol run through the actual adapters in the declared fixture mode; preserve its inputs and raw outputs separately.

Explicitly replay the aggregate-completion challenge: one Claude turn stops while another callback is held, with valid before-stop and after-all-stops controls. Check both actual product activity and normalized/checker observations. Inspect Codex/Grok/ACP aggregate-state emission paths as applicable. Do not infer global idle merely from a completion event.

The original and superseded d4 raw runs reportedly recompute to **44 passed and two incomplete Codex/Grok completion cases** under the corrected checker because aggregate activity was absent. Independently verify that explanation. Missing observations cannot be invented, and those historical runs cannot receive the final run's 46/46 credit.

### 3. Verify coverage and preserved capabilities

Run the full active application suite and the focused repair tests described in `REPLAY.md`. Reconcile actual executed assertion identities against the original 89-selector inventory and the final 165-selector H/B/A mapping; test totals alone are insufficient. Distinguish tests, subtests, collected items, skipped cases, and overlapping component totals. Investigate meaningful coverage loss rather than assuming a count difference is a defect.

Run the seven original native/delivery and five work source-fault calibrations, including controls and intended assertion attribution. Retained known-bad subjects must continue to be rejected in normal verification. Inspect whether the new calibration cases would catch reintroduction of the repaired faults.

Reconcile the same 46-case composed protocol inventory against raw observations. Assess preservation of persistent contexts, busy delivery, correlated exchanges, lifecycle ownership/recovery, configurable native tools/providers, durable routing, uncertainty/stale fences, revisable owner outcomes, current acceptance evidence, and composable policies at the applicable boundaries. Trace material contradictions through the active implementation. Historical test passes do not substitute for active application coverage.

Preserve transport/provider/policy/storage/ownership configurability. Do not import historical role structures or incidental fixture restrictions, or add exactly-once, token interruption, universal approval, or a fixed routing/evaluator policy as requirements. Broaden the investigation when a concrete finding implicates an adjacent boundary; keep optional redesign suggestions separate from closure findings.

### 4. Execution environment and evidence limits

Use Python 3.11/3.12 and the declared dependencies. Begin with the retained `REPLAY.md` and the saved-audit command in `START-HERE.md`; inspect and adapt only workspace/output paths as necessary. Keep those adaptations separate from immutable records.

The recorded completed run's old `exact-ef75675` prefix maps to `repair-evidence/final`. Superseded d4 records that formerly used `final` map to `repair-evidence/superseded-d4bd829`. The collided-output attempt receives no final behavioral credit. Record which evidence you selected and why.

If the current host cannot execute tmux/private Unix sockets or a required process interaction, establish the limitation and continue unaffected checks. Inspect exact-candidate GitHub-hosted raw evidence where it covers the claim. If new reviewer probes require hosted execution, an isolated verification-only branch/workflow may be used; leave product/evidence/experiment branches untouched and retain the reviewer diff, run identity, and outputs. Do not pass a new unexecuted challenge merely because an older CI run is green. Clearly separate newly executed review results, rechecked retained results, and unverified claims.

Authenticated vendor harness acceptance remains xor's planned local work. Its absence limits claims about actual vendor behavior; it does not automatically become a new prerequisite to integrating the repaired code. The review can give a scoped integration recommendation while keeping native acceptance pending. No Jev API calls or v2 experiment work are needed.

GitHub code/security reviews already completed without findings on the pinned candidate. Inspect their scope if relevant, but do not routinely retrigger them or equate their result with this fresh independent review.

## Required deliverables

Produce `Xorchestrate-PR6-Independent-Verification-Report.md` and a replayable evidence ZIP, with:

1. A clear verdict for the pinned candidate: recommend integration, recommend holding for specific defects, or verification incomplete. State the verified scope and remaining native acceptance separately.
2. An F01–F10 closure matrix, with the later completion-normalization challenge separately visible. Use **closed**, **open**, or **not verified**, with evidence and limitations for every row. Do not silently convert missing proof into a product defect or a pass.
3. A preserved-capability/coverage assessment for H1–H8, B1–B5 and A1–A2, including actual selector and protocol inventory reconciliation.
4. Findings ordered by consequence. Each material finding needs expected behavior, actual behavior, affected boundary, exact candidate, minimal reproduction, relevant raw output, and a valid control. Separate product defects, checker/evidence defects, packaging issues, and optional improvements.
5. A command/run ledger and input manifest containing product/checker/configuration identities, environments, commands, exit statuses, executed assertions, raw outputs, fault activation, control outcomes, and hashes. Preserve failed attempts. Include reviewer-created scripts and explicit replay instructions.
6. A concise account of claims challenged without finding a defect, remaining uncertainties, and what evidence would resolve each material uncertainty. Do not equate review effort with assurance.

The report and evidence must let another session check your conclusions without trusting your narrative. Do not stop at a proposed review plan. Execute the review to the available boundaries, finish independent work if something is blocked, and state any precise remaining execution blocker.

## Reference links

- PR: https://github.com/xormania/xorchestrate/pull/6
- Candidate: https://github.com/xormania/xorchestrate/tree/ef7567516520543970af55b6f19534cb66eda357
- Published evidence documents: https://github.com/xormania/xorchestrate/tree/663f0634f59c5d6f6c54910c8f995a6af9e6a490/docs/repair/evidence
- CI: https://github.com/xormania/xorchestrate/actions/runs/40066679349
- Code/security review summary: https://github.com/xormania/xorchestrate/pull/6#issuecomment-6380716130
- Historical capability sources, only when needed to resolve a concrete preservation question: https://github.com/uscient/agentscient/tree/dev ; https://github.com/uscient/agentscient/tree/shit ; https://github.com/xormania/multi-harness-proof

The complete raw repair package is the attached ZIP. The seven evidence-branch documents alone do not supply all replay inputs.
