# Jev failure correlation experiment — revision 1.1.0

**Result: mixed. Recommendation: revise and collect more evidence.** Authenticated Actions evaluation completed. Jev improved mechanism and candidate-probe agreement over the simple checklist, but relationship judgments became worse and false alarms did not improve. One additional Jev-selected probe reproduced a known assessment-currentness defect. This supports further advisory experiments, not automatic correctness, acceptance or correlation policy.

The question is whether typed Jev judgments over unfamiliar synthetic observations identify relevant known mechanisms and choose discriminating follow-up probes better than a simple deterministic checklist. Recommendations depend on measurements, not whether the result favors Jev.

## Method and boundaries

The source base is the unchanged reviewed repair candidate `b0a00f1aed08d4ad8e066de8eb51f94e21da9f4a`, tree `0f4034870f40fb5a1d022c9f35ad0b866236455f`. Current `repair/behavioral-preservation` and PR #5 still identify that commit; `master` is the initial README. The other current architecture-proposal branch is separate work, not a later repair. The original checkout and repair branch are preserved.

All 1,831 archive manifest files and the owner brief hash were verified. Ten reported finding families contribute 34 observations: 10 product-defect observations, seven checker-defect observations, 13 valid controls, two environment failures and two incomplete observations. These are not 34 independent fault families. There are eight relationship pairs. Selected raw excerpts, reversible IDs, extraction definitions and omissions are retained separately from model packets. The original ZIP remains unchanged.

Development families F01/F02/F04/F07 contribute 19 cases. Held-out families F03/F05/F06/F08/F09/F10 contribute 15. Four independently generated F07 variants change dependency and exception entry paths; they are development transfer, not unseen-family performance. Five relationship pairs involve held-out families; some also contain development cases, so their score is mixed-family transfer rather than wholly unseen pairs. The human authors knew the independent report: this is a frozen family split for a small semantic calibration, not a blind discovery benchmark.

The rubric has three typed Choice judgments: mechanism, pairwise relationship and next probe. Unknown/insufficient, legitimate behavior and infrastructure failures are allowed. The independent answer key admits ambiguity where packets do not identify implementation provenance. No output defines its own correctness. Exact joins, ordering, preservation and scoring are ordinary code. Probe implementations are authored by the experiment agents; Jev only selects supplied options.

The baseline is the versioned lexical/invariant checklist in `rubric.json` and `runner.py`. It reads the identical observations and candidate options, never hidden labels or diagnostic filenames. Rules were frozen before final held-out scoring and were not tuned to improve results. It is intentionally simple and is not representative of all deterministic analysis.

Revision 1.0.0 had an offline preflight before a chronology correction to F05 and inclusion of corpus tests in CI. Revision 1.1.0 explicitly records those changes before any live call. The rubric's decision text and baseline logic are unchanged. Historical preflight outputs are not live measurements.

## Declared live budget

One literal-value authenticated smoke precedes 34 two-question case requests and eight relationship requests: 43 successful calls if all complete, at most 86 attempts. Each attempt has a 20-second timeout, request/response byte limits, and at most one retry for HTTP 429/529. No repeat subset or open-ended tuning is used. Requested model is exactly `jev-1.13.0`, endpoint `https://api.typesafe.ai/v1/systemone`; no moving alias is allowed. Current official docs are blocked by this Cloud proxy, so availability must be established by the authenticated smoke. Pricing was not verified; monetary estimates remain unknown. Returned usage, latency, model identity and every attempted response are retained.

The same two distinct-probe budget applies to each selector. Only p4 (actual assertion versus helper exception) and p7 (material versus cosmetic assessment dependency) are executable. Other choices remain explicitly unexecuted and receive no observed utility credit. Repeated selections share one fixed batch and are not independent discoveries.

## Evidence and unresolved scope

See `README.md` for reproducible commands, `data/` for the frozen corpus/key/splits, `evidence/` for local checks and original independent generation, and the run's `matrix.json`, `requests/`, `attempts.jsonl`, scoring and probe reports for actual execution. `INDEPENDENT_REVIEW.md` documents leakage, fidelity, baseline and inference limits. The workflow always uploads artifacts and attempts separate sanitized evidence publication for Git retrieval.

Preserve the review's qualifications: F06's schedule was demonstrated with a protocol peer; installed Claude reachability remains unverified. F08–F10 are substituted evidence checker experiments, not live wrong dispatch demonstrations. No product repair, acceptance decision, merge or authenticated native harness run is part of this experiment. H1–H8, B1–B5 and A1/A2 remain unchanged.

## Measured results

Run: https://github.com/xormania/xorchestrate/actions/runs/40065048378 (successful), source `454f6f25cb01d0aca793e21c72a032f8428535a9`. All 43 attempts received HTTP 200 with schema-valid exact-model responses; no retries, provider failures, invalid responses or unrun decisions. Transport latency summed to 3.2602 seconds (not whole workflow elapsed time). Provider usage: 59,623 input tokens and 7,313 output tokens; cost unknown. These observations demonstrate live evaluation with the repository Actions secret; no secret value was retained.

| Measure | Baseline | Jev |
|---|---:|---:|
| Mechanism agreement, development | 6/19 | 10/19 |
| Mechanism agreement, held out | 5/15 | 7/15 |
| Mechanism agreement, all | 11/34 | 17/34 |
| Probe-option agreement, development | 6/19 | 12/19 |
| Probe-option agreement, held out | 5/15 | 7/15 |
| Probe-option agreement, all | 11/34 | 19/34 |
| Relationship agreement, development | 2/3 | 0/3 |
| Relationship agreement, held-out-involving pairs | 2/5 | 2/5 |
| Relationship agreement, all | 4/8 | 2/8 |
| False alarms on valid controls | 4/13 | 4/13 |
| Environment failures miscredited as target defects | 1/2 | 0/2 |
| Mechanism abstentions | 18/34 | 7/34 |
| Probe abstentions | 18/34 | 3/34 |

Agreement counts include answers accepted as uncertain by the frozen key. Abstentions can be correct or incorrect; unrun decisions are separately zero. Every family/split denominator and individual mistake is in retained scoring JSON. The original two environment controls and small family count do not justify broad statistical claims or probability calibration claims. Probabilities remain retained hypotheses, without a mandatory confidence threshold.

| Family | Cases | Mechanism baseline/Jev | Probe baseline/Jev |
|---|---:|---:|---:|
| F01 | 3 | 0/3 · 2/3 | 0/3 · 2/3 |
| F02 | 2 | 0/2 · 0/2 | 0/2 · 1/2 |
| F03 | 2 | 0/2 · 0/2 | 0/2 · 1/2 |
| F04 | 4 | 3/4 · 3/4 | 3/4 · 3/4 |
| F05 | 3 | 0/3 · 3/3 | 0/3 · 2/3 |
| F06 | 2 | 1/2 · 1/2 | 1/2 · 1/2 |
| F07 | 10 | 3/10 · 5/10 | 3/10 · 6/10 |
| F08 | 4 | 2/4 · 2/4 | 2/4 · 2/4 |
| F09 | 2 | 1/2 · 0/2 | 1/2 · 0/2 |
| F10 | 2 | 1/2 · 1/2 | 1/2 · 1/2 |

Jev did well on the material evaluator-input cases (F05: 3/3 mechanism), while it recognized neither bridge-family mechanism (F02/F03: 0/4 combined). It often treated the helper exception as infrastructure without recognizing the checker falsely crediting that exception (F07). It falsely flagged four valid controls, including a correctly rejected stale replay. F09 produced abstention instead of recognizing the later uncertainty. For F10 it selected evidence contradiction rather than the frozen assertion-proxy category. These errors remain errors under the prespecified key; no answers were relabeled after seeing output. Later rubric revisions should examine that category boundary independently.

## Executed probe outcomes and portability limitation

Both selectors chose p4; only Jev chose p7. Hosted p7 executed four cases: two material-context stale acceptances reproduced on memory/SQLite, with two cosmetic-title controls passing. This demonstrates one useful additional known-family follow-up selection. It exposed no previously unrecorded product defect. Seven Jev cases chose p4 and three chose p7, but shared batch executions are counted once, not as ten independent discoveries. Baseline chose p4 for five cases.

**The original hosted p4 result has an unresolved target-identity limitation.** Pytest's repository-qualified JUnit classnames differed from the experiment's hardcoded `test_probe::test_target`. The native classifier therefore returned `not_exercised` for all eight variants. The work classifier did execute and falsely credited three unrelated exceptions; zero in the combined hosted summary is not evidence that the known defect disappeared. `evidence/probe-platform-comparison.json` retains both platforms' raw keys, hashes and diagnoses. Original hosted results are preserved.

A separate, disclosed **probe harness revision 1.2.0** (`evidence/probe_replay_v1_2.py`) supplies an explicit per-fixture pytest root. It replays the actual frozen baseline/Jev selections locally, using identical fixture and candidate bytes, classifier code, assertions and budgets, with no additional model calls. The corrected local baseline p4 reproduced three known false detections; Jev p4 reproduced the same three, and Jev p7 reproduced two known stale acceptances with two passing controls. Per-attempt records bind the wrapper, original probe source and selected predictions. This is corrected local replay, not a retroactive correction of hosted execution. Unsupported p1/p2/p3/p5/p6 remain unexecuted; no replacement or utility credit is inferred.

## Reproducible publication and next step

- Measured source: `454f6f25cb01d0aca793e21c72a032f8428535a9`, based on `b0a00f1aed08d4ad8e066de8eb51f94e21da9f4a`.
- Evidence commit: `9dd1f434b27d41aa0297ccf120a38de1b5f90fb1`, branch `evidence/jev-failure-correlation-40065048378-1`.
- Artifact ID: `12549096846`; URL: https://github.com/xormania/xorchestrate/actions/runs/40065048378/artifacts/12549096846.
- Actions-reported artifact SHA-256: `a2ced473eaaabaf3c0c004ee8272a6067af6c18e5bfb66c15350349d7c0d000b`. The artifact ZIP itself was not downloaded through the blocked GitHub API; results were retrieved and verified through the separately published Git evidence branch.
- Corpus binding: `7ed4ec43c30855a03117d1a9f8624833bc6322ca29d1a0663cc3a1be89042e72`.
- Rubric hash: `597a03287ea90ef7a13164b3d5eb908423e634b1e755eae6ebaaa7f0b9ef0519`.
- Model-identity hash: `3b144d5e6bb7bf4cafa48404341aafa9ada977d2f3425ee65a4a0e800d2cc00c`.

Final local validation passed 35 tests and 29 subtests; all seven final corpus files regenerated byte-for-byte. The lead and independent reviewer both replayed scoring from retained requests and raw responses without a key. The final report/evidence commit adds no model/rubric/baseline change. The measured source stays reproducible at its exact commit.

The Cloud proxy still blocks GitHub API and current TypeSafe documentation access. A draft network update adds `api.github.com` and `docs.typesafe.ai`; it has not been applied or published. It was unnecessary for the successful live run and Git evidence retrieval. A draft PR was not created through the unavailable API; the pushed experiment branch is reviewable. Artifact ZIP retrieval and authenticated native harness acceptance remain explicitly unverified.

Before integrating beyond advisory use, independently expand held-out families and real observations, strengthen the deterministic baseline, resolve rubric/category ambiguity, improve relationship-specific evidence, and validate all selected probe types including the corrected hosted harness. Measure whether selection actually reduces diagnostic effort with comparable execution budgets. A narrow pilot may retain Jev recommendations as hypotheses; these results do not justify making them an acceptance or authorization gate.
