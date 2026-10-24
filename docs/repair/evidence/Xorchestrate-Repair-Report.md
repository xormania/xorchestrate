# Xorchestrate F01–F10 repair report

F01–F10 and the subsequent completion-normalization sibling finding are repaired and their deterministic regressions/calibrations pass on the exact final candidate. All 6 GitHub-hosted jobs passed. Authenticated native acceptance and fresh-session verification remain pending; no merge was performed. The current owner instructions and complete H1–H8, B1–B5 and A1–A2 requirements remain in force.

## Candidate and authority

- Original reviewed candidate: `b0a00f1aed08d4ad8e066de8eb51f94e21da9f4a`; tree `0f4034870f40fb5a1d022c9f35ad0b866236455f`.
- Repair branch: `repair/independent-findings`, stacked on PR #5’s `repair/behavioral-preservation` head. PR #5 and its original evidence remain unchanged.
- Original owner brief SHA-256: `41545d3fab334e432f58f1f1b1367c70bed29bf2c39c1280c3eacd9cbdc783ad`. Original ZIP manifest: 1,831 entries verified.
- Candidate: `ef7567516520543970af55b6f19534cb66eda357`; tree `a0813aae760e6e5bb6245693bd92b7e7c321348b`; content SHA-256 `03e3b0e28cfaaa03e87f3a2e68906cc24ae9ea25f67169a1d2b42fd25e704693`. Checker SHA-256 `63e1e86ce96c0349c64040258b557f48c55e2d1a3fca2de802d698ee6eebab82`. Exact configuration/criteria/fixture identities are in `final-manifest.json`.
- [Draft PR #6](https://github.com/xormania/xorchestrate/pull/6), stacked on #5; [final CI run](https://github.com/xormania/xorchestrate/actions/runs/40066679349). Synthetic merge `ca3f79ee6d04c55223fa0c907eeb26f9340d6439` has the exact candidate tree and verified parents. Later evidence publication is separate; see publication.json.
- Parallel Jev experiment: separate unchanged historical inputs; it is not a repair prerequisite.

## Changed behavior

Component evidence paths in this table are relative to `repair-evidence/`.

| Finding | Repair | Retained proof |
|---|---|---|
| F01 | Public inspection filters every record with actual scope; native source and target grants remain separate; internal receipt joins retain their full input. | `delivery_scope/closure.json`; named product/calibration selectors in closure-matrix.json |
| F02 | Observed callbacks retain qualified native identity and original application lease across replacement. | `bridge_lifecycle/closure.json`; named product/calibration selectors in closure-matrix.json |
| F03 | Post-effect replacement retains unknown application certainty and inspectable completed host evidence; subsequent effects remain fenced until resolution. | `bridge_lifecycle/closure.json`; named product/calibration selectors in closure-matrix.json |
| F04 | Completed adapter caches revisit durable application replay authority and currentness without reexecuting effects, including external bridge composition. | `native_adapters/closure.json`; named product/calibration selectors in closure-matrix.json |
| F05 | Assessment applicability binds material consumed work, evidence, actor and contribution inputs with JSON-type-sensitive equality; benign presentation changes remain possible. | `assessment_binding/closure.json`; named product/calibration selectors in closure-matrix.json |
| F06 | Claude accounts for outstanding turn/callback identities; completion checks require the latest aggregate native activity. | `native_adapters/closure.json`; named product/calibration selectors in closure-matrix.json |
| F07 | Both calibrators require independently attributed execution of the expected assertion, not traceback text or a helper exception. | `checker_repairs/closure.json`; named product/calibration selectors in closure-matrix.json |
| F08 | Normalizer preserves independent arguments and joins the actual persisted effect; inconsistent identities, scopes, actors and arguments reject. | `checker_repairs/closure.json`; named product/calibration selectors in closure-matrix.json |
| F09 | Checker retains attempt order/status and judges the latest unambiguous attempt without erasing historical uncertainty. | `checker_repairs/closure.json`; named product/calibration selectors in closure-matrix.json |
| F10 | Unfinished-work replies must precede completion by actual timestamp instant; busy-tool submission semantics remain unchanged. | `checker_repairs/closure.json`; named product/calibration selectors in closure-matrix.json |

## Verification interpretation

Original reproduction scripts often exit zero because the vulnerable behavior and controls were successfully demonstrated. Their command status is not a behavioral pass. New product assertions fail on the original implementation; repaired assertions and retained deliberately faulty subjects have separate expected outcomes.

Component totals overlap and must not be summed. Final full-suite, protocol, calibration and CI results belong to one frozen candidate or a verified integration tree with known parents. Earlier capture failures caused by concurrent writes, setup errors and environmental skips remain retained and receive no final pass credit.

| Exact-candidate check | Result |
|---|---|
| Active suite, local | 799 passed, 164 subtests; 10 environmental skips |
| Hosted Python 3.11 | 809 passed, 164 subtests; 0 skips |
| Hosted Python 3.12 | 809 passed, 164 subtests; 0 skips |
| Original mapped assertion selectors | All 89 passed locally and hosted |
| Original and additional H/B/A selectors | All 165 passed locally and hosted |
| Additional primary test cases since reviewed baseline | 211; per-module repair census retained |
| Actual composed protocol | 46/46 locally and hosted; both saved raw reports recomputed exactly |
| Original source calibrations | 12 detected; audit: 36 passing controls, 19 intended parameterized failures, 0 errors and 0 skips |
| Static checks | Ruff/security lint/compile/boundaries passed; 13 optional-toolchain checks |
| Hosted tmux | 22 tests and live fixture passed; cleanup verified with no forced termination or remaining processes |
| Historical preservation | 285 recovered toolkit and 825 imported-runtime tests; 0 skips |
| Authenticated vendor/native acceptance | Pending xor’s local run |
| Fresh-session verification | Pending next independent session |

## Independent challenges and expectation changes

In-session reviewers challenged the repaired boundaries. Retained counterexamples required additional fixes for historical native `get_work` disclosure through a public source session, JSON boolean/number equality in assessment binding, helper AssertionError attribution in calibrators, duplicate/misbound raw records, and timezone ordering. These are preserved as regression inputs with passing controls; they are distinct from the later independent session.

The checker now rejects contradictions at normalization/join boundaries, uses the latest attempt, and requires unfinished-work replies before completion. Busy-tool semantics remain submission during a hold followed by the full exchange; no token-generation interruption requirement was added. No exactly-once guarantee, fixed evaluator threshold, universal approval or historical role hierarchy was introduced.

## Environmental and native limits

This Work host permits PTYs but rejects AF_UNIX with EPERM. A separately extracted tmux binary ran, but its private-socket create/send/capture/cleanup cycle failed with the same permission error. Local skipped-case names/reasons are retained in final-manifest.json. Hosted lanes execute the full active inventory; environmental skips receive neither local pass nor product-defect credit.

Authenticated native coverage remains: three persistent contexts/private markers, six directed exchanges, three held-tool exchanges, six unfinished-work exchanges, nine work batches, three native completions, fifteen lifecycle cases and every actual native-call join. Focused replay, overlapping Claude callbacks and pre/post-effect reconnect schedules remain in the local native procedure.

## Evidence and replay

`closure-matrix.json` retains every component report, original failure/control, root cause, implementation, product assertion, activated fault, intended rejection, commands, raw locations and limitations. `requirement-matrix.json` preserves the broader owner coverage, including all original 89 distinct selectors. Use `REPLAY.md` for fresh commands. Final identities, command results and CI URLs are in final-manifest.json; publication.json proves the later evidence-only relationship. The original ZIP remains unchanged. See REPLAY.md for exact published Git recovery and restore_git_inputs.py for deduplicated measurement inputs; no Git bundle is included.


## Superseded first repair and completion challenge

The first repair `d4bd82946bfb6e78f4ba533d7ed2e01c37ea601e` had 6 successful hosted CI jobs.
Those measurements are retained under `repair-evidence/superseded-d4bd829` and receive no
final-candidate credit. An independent composed Claude/SQLite/hook/MCP probe then
showed that after A's Stop, callback B remained held and actual product activity
was active, but normalization invented global idle and the saved completion case
passed. Before-stop rejection and after-both-stops acceptance were valid controls.
See `repair-evidence/claude-completion-normalization-challenge/`.

The final repair records aggregate activity after native pending-state transitions,
preserves explicit activity through normalization, and rejects completion lacking
that proof or superseded by later active/unknown observations. Codex, Claude, Grok
and ACP emission paths plus raw-checker boundaries have direct retained tests:
`test_completion_evidence.py`, `test_native_completion_state.py`.

The corrected checker gives **44 passed and two incomplete Codex/Grok completion
cases** when recomputing the original reviewed run and superseded d4 raw records:
those old emitters did not record aggregate activity. This is not repaired by
inventing missing historical facts. New final raw observations establish the
unchanged 46-case inventory; old results remain historical.
`repair-evidence/completion-checker-repair/` retains these reevaluations.

## Final run provenance and coverage

The completed exact-candidate measurement was captured under
`repair-evidence/exact-ef75675/` and then moved to `repair-evidence/final/`.
Command records retain the original output prefix; REPLAY.md documents that path
mapping without changing raw provenance. An earlier mixed-output collision attempt
is retained under `repair-evidence/collided-output-attempt-ef75675/` with no
behavioral or final-candidate credit. The completed run supplies all
46 passing protocol cases and all 12
original source-calibration detections shown above, with valid controls and named
assertion failures audited separately.

No original mapped assertion was removed. The primary inventory increased from
598 to 809; the net 211
new cases and dedicated-module census are in `final-manifest.json` and
`repair-evidence/final/repair-test-census.json`. Component totals overlap and are
not summed. Earlier concurrent snapshots and superseded green results remain
retained without final credit. Historical toolkit/runtime passes preserve imported
source behavior and do not replace active application checks.

Existing assessments without the new input binding require reassessment; their
historical records remain inspectable. Public inspection omits denied records and
marks changed nested projections. Native completion records without aggregate
activity cannot establish global idle. These changes preserve the complete owner
requirements and current evidence boundaries.
