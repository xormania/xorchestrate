# Independent verification prompt — Jev v2

Use this prompt in a separate session after the implementation task produces its evidence handoff. xor remains final authority. This is a read-only verification and adversarial review; do not merge, deploy, repair product code, or rewrite historical evidence.

## Objective
Determine whether the delivered experiment actually tests failure correlation, useful investigation, and economic routing under the declared conditions. Try to falsify its claimed gains. A polished report, green CI, agreement among models, or valid hashes is not sufficient.

Read the v2 assignment and specifications, then identify the exact measured experiment source commit/tree, product subject revisions, corpus/key versions, policy/model configuration, workflow runs and evidence commits. Verify the candidate identity before evaluating claims. Obtain missing raw artifacts where authorized.

## Independent replay
Recompute scores and cost calculations from retained requests/responses and trace records without making new paid model calls. Run the meaningful offline checks and representative behavioral probes in an isolated workspace. Report commands, versions, results and limitations.

A fresh live model run is separate from replay. Do not quietly replace original outcomes with fresh ones. If a live rerun is necessary, name its purpose, scope and cost basis and follow the owner's authorization.

## Adversarial questions
1. Does the experiment retain the original correlation/probe objective, or did it drift into classification-only benchmarking?
2. Can each expected stage-specific epistemic judgment be inferred from the evidence the policy actually received at that stage? Are independently established outcome labels, which may use later probe or execution evidence, separated from these judgments and withheld until observable? Look for hidden provenance requirements, answer leakage, permissive keys and circular model-authored truth.
3. Are dev/calibration/holdout groups genuinely separate? Are related variants or pairs counted as independent cases?
4. Can valid behavior, absent evidence, wrong-run evidence, multiple mechanisms, and infrastructure failure be distinguished?
5. Are the deterministic comparator and reasoning fallback given comparable evidence and constraints? Does a weak comparator manufacture an apparent win?
6. Do selected probes actually run the applicable behavior and assertions? Test a passing control, intended defect and unrelated exception. Look for source-string false credit, not-exercised tests credited as detections, and normalization that hides contradictions.
7. Does the adversarial stage correct errors more than it creates them? Are disagreements being counted as discoveries?
8. Does the fallback improve the cases the router chooses? Compare correction/damage counts with never/always and equal-budget random escalation.
9. Is the economic target explicit? Does a changed label actually change the correct action? Are operational value, learning value and unpriced assumptions separated?
10. Are costs complete, including retries, failed calls where billable, probes, candidate generation, latency and review? Are subscription/API routes or cached/live runs improperly conflated?
11. Was the initial route recorded before random audit selection? Can every outcome be sampled? Are inclusion probabilities retained and targeted cases excluded from routine-rate estimates?
12. Can an early tree decision suppress the only useful probe? Does a confidence threshold hide a missing observation? Does budget exhaustion become acceptance?
13. Are model aliases, option sets, prompt changes, batching, context transformations or stale caches masking different conditions?
14. Do end-to-end claims rest on actual executions where actions alter later state, rather than an invalid static replay?
15. Are existing architecture and behavioral requirements preserved? Look for accidental fixed roles, provider mandates, universal evidence records, tool-policy restrictions, or production acceptance gates.
16. Are the report's claims proportionate to independent sample sizes, intervals, disputed labels and missing arms?

Use controlled counterexamples, not speculative accusations. Keep ordinary robustness tests separate from adaptive attacks. Preserve original outcomes and label any new reviewer-created cases as post-hoc.

## Deliverable
Provide an independent report with:
- Verified candidate and artifact identities.
- What you executed versus only inspected.
- E01–E14 verdicts: demonstrated, contradicted, or unverified with concrete reasons.
- Findings prioritized by their effect on the owner's decision, with minimal reproductions and controls.
- Recomputed corrections/damage, routing/audit outcomes, probe utility and cost/sensitivity results.
- Conclusions that survive review, conclusions to withdraw or qualify, and the minimum additional evidence needed.
- Whether each policy appears useful, harmful or inconclusive under the stated economic scenarios.
- Reviewer context exposure and limits on independence.

Separate implementation correctness, validity of the measurement, and economic usefulness. An experiment that validly finds no benefit can pass review. Do not accept missing required arms as complete merely because the available ones passed.

