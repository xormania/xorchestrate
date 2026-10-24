# Jev v2 measured pilot

**Recommendation: retain the structured comparator and applicable deterministic probes; do not adopt this Jev stack as a production gate.** On this controlled corpus, Jev did not improve on structured checks plus evidence collection. Reasoning sometimes repaired the stack, but the Jev router selected no reasoning calls and missed consequential errors. These are results for supplied structured evidence and known failure mechanisms, not a general verdict on Jev or production reliability.

The experiment changes no product code or policy. The frozen v1 publication and the separate repair PR remain intact. A separate adversarial review remains the owner's next review step.

## Identities and execution

- Branch: `experiment/jev-failure-correlation-v2`; PR base: `experiment/jev-failure-correlation-v1`.
- Main measured source: `9c6d7612e31bb78eb07868b5ac2a046629c5876c`; tree `5313e3a176515e7b160315a56da430fcde63b189`.
- Product subject: `d2d2123bb8d4e866454c8e8e364f7749fa63a198`; tree `7bb88d5fd01a21478b720ad2d42b2b627b74bb92`. Product/test/helper paths are checked against that revision before actual execution. Product behavior is inherited from the v1 base; no repair branch was merged.
- Main run: `cloud-20261024-01`, direct TypeSafe API using the configured runtime variable. Model requested and returned: **`jev-1.14.0`**. One smoke, 72 staged case requests, and 30 robustness requests: **103/103 successful, schema-valid attempts**, no retries or substitutions.
- Native reasoning: **22 actual separate invocations**, one fixed packet per fresh session, for 11 paired cases in each of the reasoning-only and post-P3 conditions. Their prompts, results and invocation IDs are retained. Requested configuration inherited the session; the interface did not expose resolved model, effort, token usage, latency or charges. No claim of an independently attested native model version is made.
- Hosted validation: [run 40071712425](https://github.com/xormania/xorchestrate/actions/runs/40071712425), commit `0d8d5187209c1fb2c92e8d2a4305c6f7b40da9aa`, tree `febf6a8676f5231ff585019b6374dbc190b75be4`. Its only source difference is fetching history for the frozen-product identity check. The hosted live job was deliberately skipped; this is offline validation, not a second Jev measurement.
- Hosted artifact `12551771552`; downloaded ZIP SHA-256 `237c592593cb76f1695f9824fbe160cf53dfcbb4db5b4ffb33aa3e7e048a18df`, independently matching GitHub's digest.

The initial hosted run [40071474440](https://github.com/xormania/xorchestrate/actions/runs/40071474440) failed because checkout lacked the historical object required by the identity guard. That failure is retained. Fetching full history fixed it without changing measured code, inputs or outcomes.

Local integrated verification: **691 passed, eight skipped, 172 subtests passed**. Hosted artifacts contain **871 passing test/subtest records**, zero failures, errors or skips. The evidence handoff distinguishes these platforms and their capabilities.

## Design and what this measures

There are 24 cases in **seven dependency groups**: six development cases in two groups, six calibration cases in one group, and 12 holdout cases in four groups. Arguments and ordered-attempt cases share a fixture and are one group. Cases within groups, robustness variants, repeated probes and paired evaluations are not independent incidents.

The holdout schedules materially extend known mechanisms: two owner revisions with intervening replay, two connection replacements for effects and authority, and three overlapping callbacks with middle-first completion. Prior v1/review knowledge is disclosed. Initial packets were independently reviewed before keys and outcomes were opened, then outcome labels were checked against separate executions. Old reproduction runs remain reference evidence, not new holdout incidents.

Packets provide structured operands, local requirements, candidate descriptions and some raw observations. The deterministic baseline can compare these operands directly. This tests assessment and investigation over pre-extracted evidence; it does not establish unsupervised discovery or the cost of extracting such structure from arbitrary incidents. All policies see the same applicable initial packet. Outcome truth remains withheld until its probe observation is acquired.

P1 batches narrow independent questions. P2 can execute a selected probe and then assess the new observation. P3 adds an anchored adversarial assessment. P4 routes to a further probe, separate reasoning, no investigation, or unresolved termination. P0_probe provides the critical control for benefits obtainable simply by collecting evidence with a deterministic selector.

## Main outcomes

| Policy | Cases | Correct final action | Wrong terminal action | Abstention | Unsupported-probe terminal |
| --- | ---: | ---: | ---: | ---: | ---: |
| P0, initial structured checks | 24 | 16 | 0 | 8 | 0 |
| P0_probe | 24 | 24 | 0 | 0 | 0 |
| P1, one Jev batch | 24 | 11 | 2 | 11 | 0 |
| P2, staged Jev | 24 | 16 | 2 | 6 | 0 |
| P3, adversarial Jev | 24 | 15 | 2 | 7 | 0 |
| P4, selective investigation | 24 | 19 | 2 | 2 | 1 |
| Never reason, paired subset | 11 | 7 | 1 | 3 | 0 |
| Always reason after P3 | 11 | 10 | 0 | 1 | 0 |
| Random reasoning, original control | 11 | 7 | 1 | 3 | 0 |
| Simple risk routing, paired subset | 11 | 9 | 0 | 2 | 0 |
| Reasoning only, initial evidence | 11 | 7 | 0 | 4 | 0 |

Initial unresolved judgments can be epistemically correct. P0's initial judgments were 24/24 correct while eight final outcomes still required evidence collection. Three of the four native reasoning-only abstentions correctly retain missing evidence; the fourth unnecessarily abstains on an initially supported case. The table does not price abstentions as wrong terminal actions or pretend unavailable facts were inferable.

P1's wrong terminal actions comprise one false clear and one unnecessary rejection. P2, P3 and P4 retain two false clears. P3 adds one abstention and corrects no final-action error. P4's deterministic probes resolve four additional P3 abstentions but do not repair its two false clears. In the paired reasoning comparison, fallback corrects one wrong terminal decision, resolves three abstentions, and changes one previously correct action into an abstention. All transitions and stage-relative semantic scores remain in the replayable evaluation.

P4 chose **11 probes, 13 no-investigation routes and zero reasoning routes**. Its two false clears concern authority binding, with native judgment confidence 0.95 and 0.85. High confidence did not establish correctness or appropriate routing.

The original random comparator matches the zero reasoning-call count. It therefore coincides with never-reason and does not inform comparison of total investigation allocation. A separately frozen post-hoc probe-count comparison addresses that specific gap; it cannot retrospectively become a prespecified main result.

The supplement `supplement-20261024-02` was prepared before its ten new probe executions, at source `4577eb2c09a211218cfb822f195127d6a8a3f918`, tree `58930924d0d7fe0db7def67625cd09354d0b9c00`. Plan SHA-256: `197bd8547da565db8284bccee821864cb3307e50f9a28350f92098302a53e6ca`. It uses the same 11 paired cases and exact P3 packets, five additional probes per policy, and the original random seed 4017. No new model calls were made.

| Additional probe policy | Correct / wrong / unresolved | Probe calls | Measured seconds | Assumed loss, low stakes / consequential |
| --- | --- | ---: | ---: | ---: |
| Main P4 | 9 / 1 / 1 | 5 | 2.709180 | $2.25 / $102 |
| Seeded random plus fixed candidate selector | 9 / 0 / 2 | 5 | 0.776317 | $0.50 / $4 |
| Simple consequence rule plus fixed candidate selector | 9 / 0 / 2 | 5 | 0.175621 | $0.50 / $4 |

Both supplemental policies correct one false clear and resolve one uncertainty; P4 resolves two uncertainties while retaining the false clear. The five-call match is a proxy: durations, candidate selection and possible compute charges differ. This is one post-hoc seed on clustered cases, not a general demonstration that random routing wins.

An independent post-hoc diagnostic is more decisive for interpreting probe value: applying the frozen structured baseline to the already available P3 packets produces **24/24 correct actions with no additional observations or calls**. Consequently, P4 and supplemental gains cannot be attributed uniquely to new probes; switching the interpreter to structured checks already suffices on these packets. The additional probe executions and their costs are real, but an `information_gain_observed` flag in the main summary must not be treated as proof of incremental information when the fact was already present.

## Probes, correlation, audits and actual consequences

P0_probe made eight applicable executions and resolved all eight missing-outcome cases in 2.617 measured seconds. P2 selected 22 probes: 21 executed and one was unsupported; six execution-plus-assessment paths improved the action relative to P1. P4 selected 11 additional probes: ten executed, one was unsupported, and four execution-plus-structured-interpretation paths resolved an additional action. The main run contains 54 probe records including 13 independent random audits; 52 executed and two unsupported records concern the same mismatched selection at separate stages. Counts overlap cases and stages and are not independent discoveries or automatically new information.

Eight probe families exercise actual application, adapter, receipt/checker or protocol boundaries. Healthy, intended-fault and unrelated setup/helper/timeout controls remain distinguishable. Protocol peers establish adapter behavior, not installed-vendor behavior. Selected observations are re-projected from retained raw outputs during replay. The busy candidate description mentions two callbacks although the executable schedule uses three; the replay description understates the two revisions and intervening replay. These post-freeze description discrepancies are disclosed rather than silently repaired.

Relationship evidence is only three related views of one cross-adapter replay family. P0 agrees with all three initial relationship keys; P1 agrees with two. P2/P3/P4 agree with all three after acquiring the applicable observation. No gain over the deterministic comparator or broad correlation capability is established. There is no positive common-implementation-cause control; source identity must remain insufficient where absent.

Independent audit selection occurred after routes were frozen, with nonzero stratum inclusion probabilities 0.5, 4/7 and 0.6. All 13 selected cases obtained independent executable observations. The sample observed zero terminal-decision errors, while the complete controlled outcome population contains two unsampled false clears. Its Horvitz–Thompson point estimate of zero must not be read as a guarantee or absence of missed investigation opportunities.

The end-to-end subset performs real actions in fresh Application/SQLite states for the context family. Main policies each execute three related cases; paired comparators execute their one declared context case. All 23 executions produce the correct actual acceptance state, with no measured comparative improvement. Other final-action consequences are static replay under declared loss assumptions, not observed production outcomes.

## Economics and reusable learning

Main Jev usage is 540,670 input and 89,137 output tokens. At the retrieved public price of $0.042 per million input tokens, with output free, estimated main collection cost is **$0.022708140**. The earlier development smoke adds **$0.000013566**. These are usage-based list-price estimates, not invoices. All 22 native invocation costs remain unknown. The spending reservation includes attempts independently of whether their billing is known.

`ECONOMIC-FINDINGS.md` gives the full scenario and break-even analysis, including probe time, unknown native prices, human review sensitivity, inherited computation and incremental stages. No production dollar loss, labor cost or subscription-call price was supplied by the owner. Assumed values are not measurements. The adversarial stage costs additional computation, makes no final-action correction, and adds an abstention; it has no demonstrated incremental benefit here. The always-reason arm has conditional value relative to the flawed P3 path under some consequence assumptions, but does not outperform the stronger structured-plus-probe alternative.

The frozen applicability/sufficiency guard is reused successfully on four missing-observation cases in four holdout groups. This is evidence of reusable deterministic checking, not a measured learning intervention, training uplift or monetized future ROI. Development, measurement/auditing and hypothetical routine deployment costs remain separate. Source retrieval, development labor, queue time, implementation effort and native billing remain partly or wholly unpriced.

## Review and limits

`reviews/preflight.md`, `reviews/label-review.json` and `reviews/methodology-review.md` disclose reviewer exposure and independent recomputation. The implementation agents did not label model agreement as truth. Native assessments used separate contexts, but share the available model family and were instruction-isolated rather than operating-system isolated. Their exact backend model/effort was not exposed; cross-provider/model-version comparisons are therefore unsupported.

The source evaluator calls P4 "incomplete" because one row is a measured unsupported-probe terminal. That row is an observed bounded fallback, not a missing provider call or a passed action. Main economic paired calculations exclude that row and retain its resource costs; interpretation must retain this denominator limitation. The supplemental analysis explicitly accounts for observed terminal actions.

The evidence supports further targeted assessment research, not a production gate, mandatory provider, universal confidence threshold, or new authorization rule. A more informative next study would use independently sampled operational incidents with realistic structure-extraction costs, several correlation families, provider-attested reasoning configuration, and prospective investigation-cost matching. The owner's separate adversarial review should attempt to falsify these conclusions before any integration decision.
