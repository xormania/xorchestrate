# Decision telemetry and economics

## The economic question
Evaluate whether an additional investigation improves the owner's decisions enough to justify its total incremental cost. Confidence, novelty, and the existence of an error are signals; none by itself establishes economic value.

Separate three decisions: whether to investigate a finding, whether the finding is supported, and whether to act on it. Finding an issue does not make every possible repair worthwhile. Existing owner-authorized scope still applies.

Use a value-of-information model:
- Expected operational benefit: the reduction in expected loss from choosing a better downstream action after investigation.
- Incremental resource cost: model/probe execution, retries, waiting, review, and implementation effort. Account for harmful decisions in outcome loss.
- Learning value: evidence that improves future routing, probes, or tools across repeated decisions.

Estimate net operational value as expected loss before investigation minus expected loss after the resulting action, minus incremental investigation cost. Account for intervention damage inside outcome loss so it is not also double-counted as an independent penalty. Track learning value separately. Monetize it only when an explicit reuse horizon, frequency, benefit model, and basis support the estimate.

No certainty is required about every cost to learn something useful. Report physical measures, cost ranges, dominance, and break-even values when precise valuation is unavailable. Keep owner-provided values, measured costs, and analyst assumptions distinguishable.

## Cases and valuation
For every decision, identify the affected action, applicable invariant, consequence if wrong, reversibility, potential repetition/exposure, urgency or delay cost, and available alternatives. Use only fields relevant to that decision. Distinguish observations from estimated consequences.

Include cases such as:
- Two plausible mechanism labels that lead to the same probe and action.
- A claimed delivery that lacks recipient evidence and could lead to false acceptance.
- An unresolved effect where retrying could duplicate work.
- A material requirement/context change that makes a previously valid assessment stale.
- A checker false alarm that could waste an engineer's time or trigger an unnecessary fix.
- An uncertain assessment that a cheap deterministic probe resolves.
- A confident assessment missing a decisive observation.
- A costly audit with little immediate action value but potential repeated-use learning value.

Declare configurable utility/loss scenarios before final evaluation. Consequence weights are assumptions unless independently established. Use sensitivity analysis; do not present a chosen weight as measured business loss. If cost components use different units, report them separately until an explicit conversion is supplied.

The owner has not declared all published benchmark corrections worthless, nor supplied a universal dollar value for a defect, delay, or human minute. Do not bake either assumption into labels or routing.

## Trace design
Extend the project's existing evidence/telemetry conventions where suitable. Keep a common decision trace with backend-specific assessment fields. A local JSONL/artifact implementation is sufficient for the experiment; do not require a new service or storage vendor.

Each trace should support these linked records:

| Record | Required meaning |
| --- | --- |
| Identity | Experiment/run, case and related-incident group, policy version, applicable requirement, subject commit/tree, and relevant run/attempt/revision/object identities. |
| Evidence | Raw source references/hashes, exact model-visible packet/hash, transforms and omissions, observed facts, and declared missing observations. |
| Assessment | Question/schema/version/hash; complete request and response references; requested/resolved/observed provider/model/effort; typed answers, full distributions, native confidence, and status. |
| Decision | Available actions/probes, initial proposed action, alternative hypotheses, route, routing signals and reason codes, and decision timestamp/order before audit selection. |
| Economic estimate | Expected consequence/benefit/cost, units/ranges, provenance of assumptions, estimate method/version, and whether purpose is operational, learning, or both. |
| Execution | Attempts/retries/cache use, duration/queue time where known, tokens, cost basis and date, probe execution, artifact references, and boundary observations. |
| Audit | Population/stratum, selection type, seed, inclusion probability, selection order, reviewer/evaluator identity and exposure, findings and uncertainty. |
| Outcome | Initial/final judgment and action, independent outcome evidence, action correctness, correction or damage, actual resource costs, remaining unknowns, and next-observation needs. |

Preserve initial predictions before final outcomes are known. Later updates append or version the record; they must not rewrite what was predicted. Keep unsupported rationale distinct from evidence. Record concise explanations and evidence references where available, not private hidden reasoning.

For Jev, a stable request identity must include the complete evidence view, instructions/criteria, question set/batch arrangement, candidate options/order, and pinned model configuration. For an economic decision, also bind the policy and valuation scenario. Cached assessments must be re-evaluated or explicitly invalidated when material inputs change; cosmetic changes need not invalidate a correctly defined assessment.

Protect credentials and unrelated private information. A hash without retrievable evidence is insufficient for independent replay. Redaction must be declared when it affects interpretation.

## Independence and sampling
Freeze the initial decision before the random audit. Sample independently of Jev's decision to request review. Preserve nonzero sampling probability across confident acceptance, rejection, abstention, no-probe and no-escalation outcomes.

Separate:
1. Random audits estimating routine routing performance.
2. Targeted audits testing suspected risks.
3. Adaptive adversarial searches.
4. The random-escalation comparison policy.

These streams answer different questions and have different denominators. Use inclusion probabilities and the sampling design for population estimates. Report small or empty strata plainly.

On a declared paired sample, obtain the reasoning/probe result even when the router would not request it. Compare the initial action with the hypothetical escalation action against independent outcomes. This can estimate missed opportunities and harmful escalation. Do not count a model's agreement with itself as correctness.

If a different action changes later evidence or state, a static replay is not a causal test of the whole workflow. Use controlled end-to-end executions for that subset. State which consequences are measured, simulated, or assumed.

## Costs and budgets
Predeclare finite maximum cases, questions, attempts, context/output budgets, concurrency, timeouts and wall time. Keep the implementation/development budget, the measurement/audit budget, and the projected routine-operation budget separate.

Account for:
- All initial and subsequent assessments, including failed attempts and retries where billing is known.
- Candidate generation, source retrieval and deterministic probes.
- Transport and queue latency, critical-path delay and review effort when measured.
- Costs introduced by false alarms or changed actions, under explicit scenarios.
- Shared/cached computation, with both incurred collection cost and hypothetical per-policy deployment cost.

Do not price a native subscription call at zero because usage is unavailable. Use unknown plus explicit sensitivity scenarios. Do not claim tokens were billed when only an estimated token count is available. Record price source, date, currency, units and calculation.

The policy should have bounded fallbacks and terminal unresolved states. A budget exhaustion must not become a confident clearance. A clear missing owner policy should not trigger an endless series of model opinions.

## Evaluation outputs
Produce:
- Errors corrected and errors introduced by each added stage.
- Consequential false clears and unnecessary investigations/actions.
- Useful executed probes per cost/time budget, with outcome-based usefulness criteria.
- Operational value by declared scenario, plus break-even assumptions and sensitivity.
- Separate learning-budget expenditure and evidence of reusable learning.
- Error/coverage/cost curves and uncertainty at supported sample sizes.
- The difference between Jev-selected, simple/risk-based, and equal-budget random investigation.
- A clear list of what remains unpriced, unobserved, disputed, or too sparse to estimate.

Do not optimize a dashboard number at the expense of the owner's actual goal. Retain enough evidence to detect apparent gains caused by easy-case filtering, omitted costs, missing runs, leaked answer keys, permissive labels, or a weak comparator.

