# Experiment specification

## Goal and architectural continuity
Xorchestrate coordinates agents across configurable transports, harnesses, permissions, session lifecycles, routing, storage, and organizational arrangements. Jev is an assessment capability within that architecture. This experiment must preserve the intended persistent-session, busy-delivery, correlation, ownership/recovery, configurable-tooling, durable-routing, replay/uncertainty, and composed-acceptance capabilities established by the owner, Agentscient, and the multi-harness PoC.

Preserve revisable owner outcomes, current and correctly bound evidence, requested/resolved/observed configuration, assertion-specific calibration, and exact-candidate verification. Acceptance, evaluation, authorization, and policy remain composable. Bind evidence at applicable boundaries; do not impose one universal record requiring every possible identity field.

The experiment's primary outputs are an evaluation harness, reproducible evidence, and a recommendation. A larger telemetry service, production gate, fixed role hierarchy, or mandatory Jev dependency is not a prerequisite. Useful reusable components may be extracted when proportionate and justified; experiment isolation does not forbid future integration.

## E01 — provenance and scope
Retain the frozen v1 source and outcomes. Preserve concurrent repair work. Record the experimental source revision separately from each product subject revision, measurement run, and later evidence-publication commit.

The initial independent review F01–F10 identified: work-scope disclosure; reconnect authority rebinding; post-effect uncertainty and duplicate-execution risk; stale adapter replay; stale evaluation after material context changes; concurrent callback activity tracking; false fault-calibration credit; loss of argument contradiction; masking a later unknown attempt; and insufficient busy-delivery timing verification. Use their actual source evidence, not these descriptions alone.

Do not assume all failures have equal impact or require the same response. Do not treat a detected defect as automatic authorization for an unrelated repair.

## E02 — one reusable harness
Use one harness with preparation, execution, replay/scoring, probes, audit sampling, and report generation. Code controls side effects, identity checks, arithmetic, and policy composition. Jev and reasoning backends provide bounded assessments.

The harness must distinguish changes in question wording, evidence representation, batching, routing, and model version. Reuse an observed response across compatible policy replays only when the complete request and execution conditions match; count incurred collection cost separately from the modeled cost of deploying each policy.

## E03 — comparison policies
Implement these incremental policies on a common evaluation set:
- P0: strengthened deterministic baseline, using available structured evidence. Report the historical v1 lexical baseline separately where useful.
- P1: one Jev request with explicit, narrow independent questions and deterministic composition.
- P2: staged Jev where later stages obtain genuinely new evidence or depend on earlier assessments; retain competing plausible paths.
- P3: P2 plus a bounded adversarial Jev check of evidence applicability, missing support, counterevidence, and alternative explanations.
- P4: P3 plus selective escalation to a recorded reasoning-model evaluator, with a deterministic-probe option where that can resolve the uncertainty.

For P4 include never-escalate, always-escalate, and random-escalation comparators on a common paired evaluation subset. Match the random policy to expected total investigation cost or report a clear call-count proxy and cost mismatch. A fixed or risk-based routing comparator should isolate the added value of Jev's routing assessment from simply spending more. Reuse paired results where causally valid instead of building another framework.

On that paired subset, distinguish always running the full P3-plus-reasoning path from sending the applicable evidence directly to the reasoning evaluator. Include reasoning alone when comparing the stack's economics with a simpler alternative. Reuse responses only when the complete evaluator input and execution conditions match; a fallback that has seen Jev's diagnosis is a different condition from a blind reasoning-only assessment.

The independent random audit policy is distinct from the random-escalation comparator. Measure P3 and P4's incremental benefit and harm. Their presence is not evidence of value; a valid negative or inconclusive result satisfies the experiment when the required comparisons and evidence are complete. If an arm is blocked, keep it visible and runnable after access is supplied.

## E04 — question contracts
Separate:
1. Whether an observation applies to the claim's actual subject/run/attempt/revision.
2. Whether decisive evidence is present.
3. Whether the applicable evidence supports, contradicts, or leaves a claim unresolved.
4. Which individual invariant violations are supported; several may coexist.
5. Behavioral similarity, shared violated invariant, and evidence of a common implementation cause.
6. Whether a supplied probe discriminates among remaining hypotheses, and which observable outcome would matter.
7. Whether uncertainty is due to missing observation, hard inference, ambiguous policy, an uncovered hypothesis, or no remaining decision-relevant uncertainty.
8. Whether additional investigation is likely to change a consequential action, given explicit alternatives and costs.

These are separate judgments; a single exclusive failure taxonomy must not erase concurrent product, environment, and checker failures. Allow unsupported, insufficient, and none-of-the-supplied-hypotheses outcomes with distinct meanings. Strong probability for an unsupported option is not independent evidence.

Use direct wording and explicit boundary cases. Give the policy relevant facts and requirements; do not ask it to infer hidden code provenance. Common implementation cause requires source/trace evidence. Preserve a supported common invariant even when implementation identity is unknown.

Candidate hypotheses/probes may be generated by a reasoning model, then checked against observations. Record candidate-generation cost and omitted alternatives. Jev selects/evaluates bounded candidates; do not pretend it generated missing code or evidence.

## E05 — corpus and answer quality
Keep v1 as development/reference material. Build fresh controlled cases that include valid behavior, meaningful failures, concurrent mechanisms, incomplete evidence, unrelated infrastructure errors, confidently misleading summaries, and decisions with different consequences.

Preserve both cheap distinctions with little action value and consequential distinctions. Importance must be grounded in the declared downstream action and owner requirements, not assigned merely because a case has an F-number.

Separate development, calibration, and final holdout. Group related incidents, variants, and derived pairs so they do not cross partitions. Identify how much generalization is across new variants versus new mechanisms. If the corpus cannot support a family-level claim, report that limit; do not manufacture independence through renamed examples.

An independent evidence check should assess label sufficiency before final model evaluation. Preserve disputed or ambiguous cases with explicit admissible outcomes; do not make a key so permissive that almost any answer earns credit. Scoring must expose those cases separately. Record label authors, reviewers, supporting observations, and their access to evaluated outputs.

Separate the stage-specific epistemic key (what the supplied evidence supports now) from the outcome key (facts established independently, including through later probes). The latter scores investigation value and stays hidden until an observation is legitimately acquired. The initial policy must not be expected to infer hidden truth. Cases that require new evidence are essential, not invalid by definition.

Choose sample sizes from the intended claims, budget and independent units. Counts alone do not establish reliability. Include intervals or appropriately qualified descriptive results. A small pilot may identify configurations worth testing; it cannot establish a rare-error production guarantee.

## E06 — executable probes and sustained failure calibration
For each selected probe, retain a concrete operation, target revision, distinguishing observation, limits, and expected evidence type. Execute the probes used to claim usefulness; unexecuted suggestions receive no realized utility credit.

A known-bad subject remains bad. The calibration suite passes because the verifier detects the intended violation at the intended assertion. Pair it with a passing subject and unrelated failures, including setup/collection/timeout/helper exceptions.

Exercise the actual boundary at issue: adapter replay rather than only direct application calls; post-effect behavior and recovery fences rather than only error names; material context changes and cosmetic controls; original native versus executed arguments; ordered attempts including later uncertainty; concurrent busy-delivery ordering. Installed vendor harness behavior requires installed-vendor evidence. Protocol simulations remain separately labeled.

When evidence normalization is needed, retain originals and test that normalization cannot erase the contradiction being evaluated. Preserve not_exercised when a harness never reaches its target.

## E07 — adversarial assessment and robustness
The adversarial Jev stage should challenge specific claims, evidence applicability, excluded explanations, and proposed action value. It must be allowed to find the original assessment supported. It is not rewarded for manufacturing objections.

Keep the adversary's assessment separate from independent truth. Where useful, compare a blind assessment of the same raw evidence with a follow-up that sees the diagnosis; record anchoring exposure. Do not require every combination if a bounded comparison answers the question.

Separate invariance tests from evidence-sensitivity tests. Invariance tests change option/case order, use equivalent wording, or add genuinely irrelevant context; the intended judgment should remain stable. Sensitivity tests remove decisive observations, introduce contradictions or misleading success claims, or substitute wrong-run evidence; the expected judgment or uncertainty may legitimately change. Predeclare the expected behavior of each transformation. Keep targeted adaptive attacks in a separate stress set with their own generation/search budget. Repeated calls are repeated measurements, not new independent incidents.

## E08 — economic routing
Implement the economics and telemetry requirements in 03-TELEMETRY-AND-ECONOMICS.md. Value depends on the action that might change, consequences, investigation effectiveness, and cost. Low confidence alone is not an economic reason to escalate. High confidence alone is not a reason to skip all audits.

P4 must explicitly test Jev's proposed investigation route and expected decision relevance against a simple declared routing policy and equal-budget random routing. Give it only pre-decision facts, candidate investigation descriptions, applicable requirements, and declared cost/consequence assumptions. During cold start, estimates based on judgment are labeled uncalibrated; use development/audit outcomes to evaluate them before fitting a richer router. Do not use heldout outcomes or post-investigation features to choose the original route.

Start with a frozen provisional routing policy and declared consequence/cost scenarios. Jev's assessment is an experimental signal, not calibrated economic value. Fit any mapping from that signal to expected intervention benefit using development/calibration data only, and freeze it before holdout evaluation. Preserve unknown estimates and bounded fallbacks. Do not convert confidence directly into dollars or optimize confidence itself.

Missing evidence should make collection/probing a candidate. Hard inference may justify a reasoning model. Undefined owner requirements should remain unresolved rather than be invented by a larger model. If the routing assessment is itself uncertain, use a bounded configurable fallback based on consequences and available evidence; do not recurse indefinitely.

## E09 — independent auditing
Record the proposed route before sampling. Include every routing outcome, including confident clears, no probe, no escalation, rejection, and abstention. Freeze nonzero inclusion probabilities for all relevant strata, record the population, sample seed, selections, and actual inclusion probabilities.

Keep targeted high-risk audits separate. On the random sample, obtain independent evidence or adjudication and record uncertainty. Model agreement is not ground truth. Use the sample design when estimating overall misses; do not divide selectively reviewed errors by the full population.

## E10 — confidence evaluation
Record native confidence and all option probabilities separately. Evaluate error ranking, reliability, false-clear rate, abstention/coverage, and whether the routing signals predict improvement from investigation. Report these by decision type and supported slices, with counts.

Tune only on development/calibration material, then freeze. Reassess after a material change to model, prompt, state representation, candidate set, batching, or policy. Do not multiply confidence values along a tree and call the result an end-to-end correctness probability.

## E11 — execution accounting
Preserve requested, resolved, and observed model/backend configuration. Record every attempt, retry, timeout, invalid output, cache reuse, latency, token usage, monetary basis, and unknown value. Include reasoning effort where exposed. Keep successful return, semantic validity, and completion of the intended behavioral test distinct.

Native-session reasoning, API reasoning, cached replay, and fixture answers have different provenance. Only actual authorized invocations count as live model evidence.

## E12 — evaluation
Report classification/relationship outcomes, executable probe usefulness, routing decisions, correction/damage counts, independently verified downstream outcomes, coverage, missed opportunities, and full cost. Distinguish:
- semantic label agreement;
- action changes;
- confirmed behavioral improvement;
- within-case economic value under declared assumptions;
- learning gained for future decisions.

For each added stage, count wrong-to-right, right-to-wrong, unchanged-wrong, unchanged-right, and unresolved. A better label without a changed action earns no automatic operational benefit. Valid information or future learning can still have separate value.

Replay cached decisions only where later evidence and actions do not change the causal path. For policies that select different probes or alter subsequent execution, run controlled end-to-end cases; disclose what remains a simulation.

## E13 — deliverables
Publish source/tests/workflow; frozen run manifest and request inventory; corpus and independently supported labels; exact requests/raw typed responses; decision/audit/probe traces; raw control evidence; replayable scores; economic scenarios and sensitivity/break-even results; concise report; independent review; and an E01–E14 evidence matrix.

Bind measured source commit/tree separately from later evidence commits. Artifact hashes prove integrity, not sufficiency. Make a reviewer able to recompute results without provider keys and distinguish replay from a fresh live run.

## E14 — completion and recommendation
The experiment is complete only when required implementation and measured comparisons are present, or xor explicitly changes the scope. A blocked required arm remains pending even if all runnable work is finished. A scientifically inconclusive or negative outcome can be a completed experiment.

Recommend configurations by the conditions under which they help, harm, or remain uncertain. Identify the evidence that would change the recommendation. Do not infer a production mandate, mandatory model, universal threshold, or new authorization policy from benchmark performance.

