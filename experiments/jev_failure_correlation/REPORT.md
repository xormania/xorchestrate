# Jev failure correlation experiment — revision 1.1.0

Status at source publication: offline calibration prepared; authenticated measurement pending the first scoped Actions run. This report will be updated with retrieved results, including any live execution blocker. No improvement or native harness acceptance is claimed from preparation.

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
