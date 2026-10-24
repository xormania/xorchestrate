# PR #6: IV01–IV03 repairs and focused hexagonal separation

The reviewed repair candidate is **`1fef55c0c0cadd5e1417c0e010f272f6447f8ea4`**, product tree **`75ecb3ef4edbc381012fe74e416a9abd3fb5c667`**. It is pushed to PR #6's existing `repair/independent-findings` branch. This evidence publication is a separate, artifact-only child of that product commit. Do not substitute its commit or tree for the product identity.

The focused IV01–IV03 blockers are repaired in the demonstrated scope. IV04's intended-assertion attribution remains closed in its tested scope. Encoding sensitivity, the previously reported test synchronization issue, and the remaining architecture migration are explicit follow-ups. No merge or release was performed; xor retains that decision.

## Identities and inputs

| Role | Immutable identity |
| --- | --- |
| Original product reviewed for these repairs | [`e565863ae72180571678c8f6118ee6e00b129e2e`](https://github.com/xormania/xorchestrate/commit/e565863ae72180571678c8f6118ee6e00b129e2e), tree `474de55c5947890ab4092daeca7ff39f9bbbc400` |
| Independent review publication | [`0f55a1a031a047b8730ef37bc6eda20e17e3cdb0`](https://github.com/xormania/xorchestrate/tree/0f55a1a031a047b8730ef37bc6eda20e17e3cdb0/verification/pr6-independent-e565863) |
| Superseded first repair candidate | `42c4d8b9feec2d54b6a9bc0e3fb27fc90af2a004`, tree `e8e185ee9af57c67212f0bfca08f20a9ef5bf32a` |
| Final product candidate | [`1fef55c0c0cadd5e1417c0e010f272f6447f8ea4`](https://github.com/xormania/xorchestrate/commit/1fef55c0c0cadd5e1417c0e010f272f6447f8ea4), tree `75ecb3ef4edbc381012fe74e416a9abd3fb5c667` |
| PR base | `b0a00f1aed08d4ad8e066de8eb51f94e21da9f4a` |
| Hosted integration commit | `2894c5eb2d210656e6d29293a85836a846672c45`; exact final product tree, independently verified base/head parents |

The initial working trees and PR head were inspected before edits. Changes were developed in an isolated worktree. Both updates to the existing PR branch were ordinary fast-forwards after checking the expected remote head; no concurrent work was overwritten. Inputs were retrieved from pinned GitHub publications. The original evidence archive SHA-256 is `8a5cfc2a8e3ac7ce50b4730d060974f1dc9c29f88430533e377178ac789d1916`; all 30,893 manifest entries were verified. Input identities, original scripts, hashes and retrieval records are retained in `evidence/replay/inputs` and `evidence/setup` inside the archive.

## Findings, changes and decisive evidence

| Finding | Before | Final result and valid controls |
| --- | --- | --- |
| IV01 | Current catalog filtering worked, but denied actor snapshots leaked through three typed assessment-history paths on both stores. | Current permissions now govern typed assessment actor/work bindings in current evidence and nested history. Original 14 checks per store show exactly three disclosures closed and 11 controls preserved; the separately named closure adaptation passes 14/14 on both Memory and SQLite. Authorized views, grant restoration, internal evaluator inputs, configured partial work fields and opaque caller/provider content remain intact. |
| IV02 | The live product stayed active while a Grok interjection was accepted before native start. The checker nevertheless accepted forged idle evidence. | Decoder facts expose accepted submissions; the independent completion ledger requires their completion as well as callback and continuation completion. The unchanged experiment, with a pinned peer compatibility adaptation, rejects false completion for both applicable peers and permits eventual completion. Shared live protocol contracts exercise overlaps, callbacks, tails, new work after idle, direct/FIFO configurations and both stores where applicable. |
| IV03 | Original minimal witness: 15 false accepts among 30 cases; duplicate witness: 3 false accepts among 9. Additional actual raw native-return records exposed 6 false accepts in 8 minimal cases even on the first repair candidate. | Explicit actor/session/incarnation/source-work and selected-work claims are preserved and reconciled across native input, requested/completed callbacks, typed returns, durable effects and lifecycle results. Coherent 30-case replay, 9 duplicate cases, 8 native-return cases and 6 requested-phase controls all pass on both historical and newly generated raw runs. A separate 75-case adversarial replay rejects 54 contradictions and preserves 21 valid controls; the product receipt probe passes 47/47. Persistent shared identity contracts pass 2,867 cases across default/custom decoders and applicable product receipt implementations. |
| IV04 preservation | Earlier same-line exception credit defect was repaired; encoding sensitivity remained. | Independent raw audits confirm 32 attribution cases: 12 genuine detections, 20 unrelated/ambiguous rejections, 32 passing controls and zero false detection credit. Four BOM/Latin-1 sensitivity misses in 10 position cases remain explicitly open follow-ups. |

The product receipt predicate received its own narrow identity and typed-return repair. It does not import the independent checker's verdict logic. This concerns receipt evidence validation; the review did not demonstrate unauthorized live dispatch.

A separate transport preservation case exposed missing terminal observations for Claude/Grok through the bridge, while native activity remained correct. The bridge now associates a coherent native tool's previously unclaimed turn/prompt with its current call-qualified lease. It preserves existing ownership and rejects stale/conflicting binding. Shared Unix/FIFO ownership controls and direct/FIFO completion controls retain both valid eventual completion and negative cases. This is documented separately from the original IV02 verifier defect.

### Corrected positive control and retained failed candidate

The unchanged original IV03 script initially reported three apparent false rejections. Raw inspection showed its alternate-work control changed native input, completed callback and saved effect but left the same call's requested callback and typed native-result copies claiming the original work. Its oracle examined only selected records. The original bytes and failed replay remain retained. A separately named adaptation updates every applicable selected-work claim, strengthens the independent oracle, and pairs coherent alternatives with a requested-only contradiction. Source/session work remains unchanged. It passes; no rejection was waived to obtain closure.

That inspection also exposed a genuine blocker in `42c4d8b`: contradictory typed native-return work/actor claims were ignored. Its full suite, all 12 calibrations and hosted CI passed, but the additional raw counterexamples failed. The candidate and every observation remain retained. The final follow-up adds decoded native-return facts and independently reconciles their claims. Missing optional metadata, metadata-only returns, coherent work enrichment, distinct historical sessions and opaque provider results remain valid.

## Architectural boundary

Concrete native protocol decoding now lives behind `NativeObservationDecoder` adapters. `model.py`, `ports.py`, `evidence.py` and `core.py` form the protected shared verification layer: no concrete vendor, transport, store, process or product-verdict dependency. Normalization retains raw references, untouched source records and contradictory explicit claims. Compatibility facades select defaults and retain established calibration hooks. Checker provenance recursively includes adapter sources.

Shared scenarios and independently declared expected outcomes live in `tests/hexagonal/contracts`; interchangeable fixture/subject implementations live in `support`, with concrete protocol and store selection at composition roots. Automated transitive dependency checks cover the affected layers, package initializers and dynamic-loading escape routes. Deliberately forbidden dependencies and positive injection controls exercise the guard itself. These are regression guards, not a proof about arbitrary Python programs.

The exact boundary rationale and remaining migration inventory are in [architecture-iv123.md](https://github.com/xormania/xorchestrate/blob/1fef55c0c0cadd5e1417c0e010f272f6447f8ea4/docs/repair/architecture-iv123.md). The wider test suite and orchestration tooling have **not** been declared fully migrated. Follow-up areas include collected-test helper imports, older concrete fixtures, broader orchestration factories and expansion of automated boundary coverage after each migration.

## Preservation and validation

* Local Python 3.12 and normal hosted Python 3.11/3.12: **4,067 passed +164 passing subtests**, zero failures/errors/skips. Final source stayed clean and byte-identical throughout recorded measurements.
* Actual composed three-peer protocol: **46/46**, locally and hosted. Isolated checker reports recompute exactly. An additional standard-library audit independently checks 69 callback joins, 15 exchanges, held-work timing, work answers and the requested inventory. A second checkout provides isolation, not an independent correctness oracle.
* All **12 original source faults** remain unchanged and are detected: 36 passing controls, 19 intended assertion failures and 17 unaffected mutant passes. Independent audits inspect raw XML, source/AST locations, mutation boundaries and subprocess exits.
* All **211 published preservation selectors** resolve and are reconciled to passing JUnit entries; all 450 previous direct test bodies remain unchanged. Existing callback authority, replay/currentness, uncertainty, work acceptance, timing and ownership checks remain present. Configurable transports, native capabilities, busy delivery and verified delivery are preserved in the affected checks. Jev is unchanged and remains separate.
* Static/security-lint coverage, optional-tool helpers, live tmux protocol acceptance and 285 recovered toolkit tests pass. All six normal CI jobs pass, including 825 historical runtime tests. See [CI-RESULTS.md](CI-RESULTS.md) for raw audit details.

The final [normal CI run](https://github.com/xormania/xorchestrate/actions/runs/40141188920), its integration identity, raw logs and downloadable artifacts are retained in `evidence/hosted-ci-final`. `evidence/final-v2` contains exact final local checks. A runtime reconnection temporarily hid the ongoing suite handle; the original process completed successfully, and a precautionary repeat is separately retained in `final-v3`. The provisional interruption diagnosis and correction are documented rather than discarded.

## Handoff, replay and recommendation

[REPLAY.md](REPLAY.md) gives fresh-checkout commands and targeted counterexamples. [README.md](README.md) describes checksummed archive restoration. The archive contains original and adapted witnesses, complete raw observations, source identities, XML, CI logs/artifacts, failed attempts, superseded results and component notes. Intermediate fixture mistakes, compatibility regressions, path-length failures and retrieval adaptations are explained in their local READMEs.

Recommend this exact candidate for xor's integration review on the demonstrated focused properties. No demonstrated IV01–IV03 blocker remains in the retained final controls. This is repair-session verification with independent expectations and cross-review, not a claim of exhaustive correctness or authenticated vendor acceptance. Fresh independent verification can replay the same candidate and design further counterexamples. Authenticated vendor acceptance remains the owner's planned local work and is not a prerequisite for completing this handoff. The separate encoding, synchronization and architecture migration follow-ups are not silently counted as closed.
