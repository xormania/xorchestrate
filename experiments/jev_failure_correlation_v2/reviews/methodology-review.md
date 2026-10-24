# Within-run methodology and source/result review

Reviewer: separate native agent `/root/evidence_review`. This follows the preparation chronology in `preflight.md` and exact-corpus approval in `label-review.json`. The reviewer authored no experiment implementation, corpus, model response, economics logic, or product change, and made no paid model call. It shares filesystem/context lineage with the implementation team and communicated corrections before measurement. It is not the later separate adversarial-review session required by package document 05.

## Exposure

The reviewer recorded initial packet judgments before seeing the author's initial key; then compared initial keys, separately inspected outcome evidence/source, and independently replayed all eight probe boundaries before approving the exact corpus. Historical v1 outputs were known throughout. The first v2 result exposure was the parent's aggregate completion notice after approval. The bound approval must remain unchanged; subsequent results cannot justify changing labels.

## Frozen candidate and Jev replay

The reviewer independently confirmed measured source `9c6d7612e31bb78eb07868b5ac2a046629c5876c`, tree `5313e3a176515e7b160315a56da430fcde63b189`, run `cloud-20261024-01`, at `/workspace/jev-v2-runs/cloud-20261024-01`. The exact-corpus label approval timestamp is `2026-10-24T06:44:43.936813+00:00`; its unchanged hash was bound at `06:45:55.671382`, before the run's smoke at `06:45:55.680458`.

Read-only `runner.verify_evidence` passed, checking 102 measured harness requests and 54 probe records against frozen source, complete transport requests/wire bytes, typed raw returns, derived answers/distributions/confidence, decisions, probe artifact hashes/projections, and audit sampling. The reviewer separately validated the authenticated literal smoke and its order before the first measured request. All 103 attempts (smoke plus 102 assessments) were HTTP/schema/model successes at exact `jev-1.14.0`, without retries: 540,670 input tokens, 89,137 output tokens, summed transport latency 21.6020 seconds. This is not full elapsed workflow time or an invoice. At the retained USD 0.042/million-input list rate, the run estimate is USD 0.02270814; native subscription charges and other unpriced costs remain unknown.

The reviewer called the pure evaluation function without changing run files. After native comparator import, `runner.verify_evidence` passed for 124 assessments (102 Jev harness assessments plus 22 native assessments) and 54 probe records. Independent pure evaluation reproduced every saved core report field without differences. The following main-policy totals remained unchanged:

| Policy | Exact stage-key agreement | Eventual action correct | Wrong terminal action | Unresolved eventual action |
| --- | ---: | ---: | ---: | ---: |
| P0 | 24/24 | 16 | 0 | 8 |
| P0 plus selected deterministic probe | 24/24 | 24 | 0 | 0 |
| P1 | 6/24 | 11 | 2 | 11 |
| P2 | 2/24 | 16 | 2 | 6 |
| P3 | 2/24 | 15 | 2 | 7 |
| P4 | 12/24 | 19 | 2 | 3 |

The initially unresolved P0 cases are correct epistemic judgments; their eventual action is unresolved until evidence arrives. Exact agreement requires all keyed fields and should not replace per-question results. P4 proposed 13 no-investigation routes and 11 probe routes, with **zero reasoning routes**. Its one unsupported probe produced an unresolved status, not a clearance. Ten of its eleven selected probes executed; the same unsupported selection also occurred at P2. This terminal result is measured, although the evaluator's generic non-OK category calls it incomplete. It must remain visible and cannot earn successful detection or action credit.

The reviewer additionally checked all 22 native raw response files, request files, hashes and invocation records against the imported assessments. There are eleven blind reasoning-only and eleven post-P3 fallback evaluations, with distinct invocation identities. The retained prompts limit each evaluator to one exact packet; the parent reports fresh `fork_turns=none` sessions. Tool access was instruction-limited rather than isolated by the operating system. The reviewer did not directly observe every session's full execution history and does not claim perfect blinding. Requested model/effort are inherited session settings; exact resolved/observed model, effort, latency, usage and invoice are unexposed. These are live native-route results, not measurements of a named pinned reasoning model.

| Policy on the same paired eleven | Correct eventual action | Wrong terminal action | Unresolved | Extra reasoning calls |
| --- | ---: | ---: | ---: | ---: |
| P0 plus applicable probe | 11 | 0 | 0 | 0 |
| P4 | 9 | 1 | 1 | 0 |
| Never / original random | 7 | 1 | 3 | 0 |
| Always post-P3 reasoning | 10 | 0 | 1 | 11 |
| Main simple risk router | 9 | 0 | 2 | 10 |
| Blind reasoning-only | 7 | 0 | 4 | 11 |

Always reasoning corrected one false clear and resolved three uncertainties, but changed one correct acceptance into uncertainty. P3 corrected no wrong eventual action and changed one correct P2 acceptance into uncertainty. P4 resolved four P3 uncertainties on the full corpus without repairing either false clear. These are different events: resolving uncertainty is useful information, but not correction of a previously asserted wrong answer.

The reviewer ran 37 existing evaluation, policy and transport tests offline: all passed. Those tests supplement, rather than replace, raw evidence inspection and the independently executed premeasurement probe controls. Retained calculations are `final-replay-check.json`, `final-evaluation-recomputed.json`, and `native-e2e-bindings.json` in `/tmp/jev-v2-review-results/` for evidence publication.

## Source findings that must survive reporting

- The candidate descriptions lag the disclosed execution variants: `p_busy` describes two callbacks but runs three with middle-first completion; `p_replay` says an owner revision but runs two with an intermediate replay. The probe boundary is relevant, but the exact schedule is underspecified in the evaluated candidate description. This was noticed after the source freeze. Preserve measured source/requests, disclose the mismatch, and correct only in a subsequent version.
- The initial evidence includes explicit invariant names and comparison operands/operators. Strong deterministic performance is therefore expected and is a useful comparator. Results concern evidence composition under this contract, not discovery of unnamed invariants from raw unfamiliar traces.
- Seven scenario dependency groups contain 24 rows (two development groups, one calibration group, four holdout groups). Arguments and attempts share the same durable-record source group. Group counts describe chosen scenarios, not a random sample of production incidents. Holdout schedules are material variants of known mechanisms.
- Three relationship views concern one adapter-replay family. Similar behavior, common violated invariant, and common implementation cause are distinguished, but this small set cannot establish broad failure correlation.
- Missing initial evidence is correct uncertainty even where the eventual controlled outcome is adverse. All eight withheld main-case outcomes are adverse by construction; no production prevalence or blanket-investigation recommendation follows.
- Probe-generated observations and modeled accept/reject choices have different causal status. Only actual controlled action executions establish downstream behavioral consequences; static loss scenarios remain analyst assumptions.

## Audit implementation inspection

The inspected source writes each proposed P4 route before acquiring P4 evidence, freezes the routing population, and then samples independently using a seeded stratified simple random sample. Strata include action, proposed route, and confidence band. `ceil(stratum_size * fraction)` ensures a positive recorded inclusion probability in every nonempty stratum; targeted/random-escalation streams are distinct.

Selected audits execute a new applicable boundary probe and derive the audit finding from executed assertions rather than agreement among models. A nondecisive probe leaves adjudication unresolved. This is independence from the evaluated model's answer, backed by independently reviewed fixture semantics and controls; it is not an external production incident oracle.

The current `independent_miss` event is an incorrect terminal accept/reject against independently observed truth. An investigate/defer decision is not automatically a terminal miss. Therefore the Horvitz–Thompson rate must be described as a terminal decision-error rate; missed investigation opportunities are a separate paired-routing analysis. It must not be described as the rate of every routing failure.

Actual-run audit verification passed: 24 population records, 13 selected, inclusion probabilities 0.5, 4/7, and 3/5. Every selected audit has a fresh executed probe with retrievable raw evidence; all 13 terminal-error findings independently recompute, yielding weighted total/rate zero. **This sample missed both known population false clears:** `cef7128badec1` at Jev-reported judgment confidence 0.95 and `c51eabfc2dae5` at 0.85. Both authority-binding cases were routed to no further investigation and not selected. The full controlled population therefore has two known terminal errors while the realized random sample has zero. Neither the sample estimate nor confidence establishes absence of missed errors. Independent calculations and selected evidence links are retained in `/tmp/jev-v2-review-results/source-audit-check.json`.

## Robustness result interpretation

The reviewer inspected actual retained wire bytes and confirmed all six reverse-option requests changed the probe-candidate order; `none`/`insufficient` remained last. The emitted `passed` field measures agreement with the key, not paired invariance of the model's answer. Independently paired with each original P1 judgment:

| Transformation | Key agreement | Judgment flips versus original |
| --- | ---: | ---: |
| Reverse candidate order | 4/6 | 0/6 |
| Irrelevant context | 4/6 | 0/6 |
| Equivalent wording | 5/6 | 1/6 |
| Remove decisive evidence | 6/6 | 5/6 |
| Foreign-subject evidence | 6/6 | 5/6 |

Stable wrong judgments remain wrong. The sensitivity changes correctly make the actual claim unresolved; a flip is expected there. These are 30 repeated diagnostic views of six parents, not 30 new incidents. Wire and paired calculations are retained in `/tmp/jev-v2-review-results/robustness-wire-check.json` and `robustness-paired.json`.

## Behavioral and economic interpretation

All 23 retained end-to-end action executions were independently checked against raw hashes, decision hashes and actual durable acceptance state. They cover three related context variants in one group: six policies on all three variants, and five paired comparators on one variant. All actual acceptance outcomes were correct. This confirms a bounded workflow behavior; it establishes no incremental behavioral improvement by Jev. Other boundaries have real probe executions and static action replay. Actual production consequences, scheduling effects, repair success and loss are not measured.

On declared low-stakes/consequential loss scenarios, P0→P1 loses USD 3.25/126, P1→P2 loses USD 0.25/70, and P2→P3 loses USD 0.25/2 before resource costs. P3→P4 gains USD 1/8 across the 23 priced actions and consumes 4.035693 additional probe seconds; the unsupported terminal is excluded and remains unpriced. P0 plus probes resolves all eight missing observations in 2.616996 probe seconds, with USD 2/16 gross scenario improvement. These losses are analyst assumptions, not measured business costs. Negative gross benefit cannot become positive net benefit by adding nonnegative costs.

On the paired eleven, always reasoning versus never gains USD 2.50/104 before eleven unknown-cost native invocations and other costs, or at most USD 0.227273/9.454545 per extra native call before exhausting that observed scenario gain. This does not establish value for P4's actual selection: it selected no reasoning calls. Human review, queue/critical-path delay, native charges, invoices, development and implementation costs remain unknown. The retained economics keep actual net value unknown and show explicit sensitivities rather than silently pricing these at zero.

The fixed applicability/sufficiency guard correctly handles four missing-observation holdout cases across four groups. This is scoped reuse of an existing artifact. Neither prospective learning uplift nor monetized learning ROI was measured. Confidence tables and case-level Wilson intervals are descriptive diagnostics on correlated constructed rows, not independent-incident confidence bounds or production calibration guarantees.

## Main comparison gap and completed supplemental review

The main random comparator matched P4's **zero reasoning calls**. P4 nevertheless selected five additional probes on the paired eleven. Thus the original random arm is identical to never and does not match total investigation call count or cost. The main P4-versus-random difference cannot support allocation superiority. The main risk arm also used a different number and modality of investigations. This is a design gap, not evidence that random allocation would win.

A separate supplement sampled five of the same eleven cases using the original seed 4017 and compared a simple five-slot router, beginning from exactly the available P3 evidence. The reviewer checked the script, exact visible inputs, selections and hashed plan before any supplemental execution. Plan `197bd8547da565db8284bccee821864cb3307e50f9a28350f92098302a53e6ca` and script `501c7eda6570523df62a7b0700303eea1bf8029068829859fa08bec30eb38d3d` were executed at commit `4577eb2c09a211218cfb822f195127d6a8a3f918`, tree `58930924d0d7fe0db7def67625cd09354d0b9c00`, run `supplement-20261024-02`. Preparation and source commitment precede the retained execution start `2026-10-24T07:01:23.168603+00:00`. The original main source and records remain unchanged.

The fixed candidate selector reads visible requirements and facts only; hidden keys and execution selectors enter only afterward in the executable adapter. All eleven available P3 packets contain zero unknown checks, so the simple rule reduces to declared consequence units and case-ID tie breaks. The design is post hoc with known main outcomes; subsequent fresh execution does not make it a confirmatory holdout experiment. Candidate dispatch also differs from main P4, so the comparison jointly measures routing and candidate selection. Equal call count is only a proxy; elapsed time and monetary mismatch remain explicit.

Read-only supplement replay passed. The reviewer separately checked all ten executed raw probes, decisive assertions, projected observations, deterministic interpretations, 22 policy rows, unchanged nonselected decisions, exact common P3 request prefixes, retained plan bindings and arithmetic. No new model calls occurred. The observations support:

| Paired policy | Correct actions | Wrong terminal actions | Unresolved | Additional probes | Measured probe seconds |
| --- | ---: | ---: | ---: | ---: | ---: |
| Main P4 | 9 | 1 | 1 | 5 | 2.709180 |
| Supplemental random | 9 | 0 | 2 | 5 | 0.776317 |
| Supplemental simple | 9 | 0 | 2 | 5 | 0.175621 |

Each supplementary policy corrects the authority false clear and resolves one uncertainty; neither damages a correct action. Main P4 resolves two uncertainties and retains the false clear. Random and simple each incur assumed outcome loss USD 0.50/4 versus P4's USD 2.25/102 under the low-stakes/consequential scenarios: USD 1.75/98 lower before additional resource costs. Both use fewer measured seconds here; durations are serial execution measurements, not stable production performance estimates. Their actual compute charges and human review costs remain unknown. This single seeded comparison contradicts an observed allocation advantage for P4 under these scenarios, without proving a population advantage for random or simple routing. `supplement-plan-review.json` and `supplement-result-review.json` retain the independent checks.

A reviewer-created read-only diagnostic applies the frozen `baseline` directly to every already-available P3 packet. It yields **24/24 correct actions, including 11/11 paired, with zero additional observations or probe/model calls**, retained in `postP3-structured-diagnostic.json`. This is explicitly post hoc, not a new measured policy arm. It establishes that the supplied P3 evidence already suffices under the structured contract. P4 and supplementary probe paths both switch the final interpreter from Jev to the structured baseline; their action improvements therefore cannot be credited uniquely to newly acquired information. In particular, the main evaluator's `information_gain_observed` field denotes acquired decisive probe evidence, not proof that previously unavailable decision-relevant information was gained. This confound strengthens the practical case for the structured comparator and limits any claim of realized investigation value.

## E01–E14 assessment after supplementary measurement

“Demonstrated” below means the stated implementation/measurement within this bounded pilot, not that a model was useful or that a production guarantee was established. A negative measured result is valid. Publication and the separately requested adversarial review are distinct remaining steps.

| Requirement | Verdict | Evidence and limit |
| --- | --- | --- |
| E01 provenance/scope | Demonstrated | Original archive manifest and v1 replay checked; product subject, measured experiment source/tree, run and later publication identity separated. No product repairs by this reviewer. |
| E02 reusable harness | Demonstrated | Common preparation, execution, typed replay, scoring, probes, audits and accounting. Exact requests/raw returns revalidated. Blind and post-P3 native inputs remain distinct. |
| E03 comparisons | Demonstrated with post-hoc proxy limits | All named main arms observed, including 22 native calls. Original random arm degenerates to zero calls; separate random/simple supplement supplies five additional probes each on the same eleven cases. Calls match, dollars/time do not. Original preregistration is not retroactively repaired. |
| E04 contracts | Demonstrated with bounds | Applicability, evidence, judgment, concurrent violations, relationship distinctions, bounded probe and routing questions explicit. Same-batch discrimination describes any supplied candidate; exact selected usefulness requires execution. Only one relationship family. |
| E05 corpus/keys | Demonstrated with bounds | Before-output packet judgment and later independent outcome checks; exact approval binding; seven grouped scenarios, material known-mechanism holdout variants, 30 derivative diagnostics. No new-mechanism or prevalence claim. |
| E06 probes/calibration | Demonstrated with limits | Eight boundaries independently replayed with controls; actual targets and not-exercised failures preserved. Protocol peers are not installed-vendor evidence. Two evaluated candidate descriptions underspecify the actual schedules. |
| E07 adversary/robustness | Demonstrated, usefulness contradicted here | P3 has zero eventual error corrections and one harmful loss of a correct answer. Retained invariance and sensitivity tests have distinct expectations; paired stability separately recomputed. |
| E08 economic routing | Demonstrated exploratorily; exact-cost allocation unverified | Frozen routing/scenarios, bounded actions, physical costs and sensitivities exist. The completed supplement provides the explicit call-count proxy with mismatch disclosed; P4 has higher assumed loss than random/simple here. No confirmatory or uniquely causal information-gain claim, exact-dollar comparison, or production value established. |
| E09 independent audits | Demonstrated with limits | Route population frozen before positive-probability sampling; 13 actual independent executed audits; weighted terminal-error estimator verified. Realized sample misses two known population false clears. |
| E10 confidence | Demonstrated descriptively | Typed native confidence/distributions, reliability, ranking and coverage diagnostics by question/split; counts exposed. Sparse correlated groups prevent strong calibration or rare-error conclusions. |
| E11 accounting | Demonstrated; native configuration attestation unverified | All 103 Jev attempts, 22 distinct native invocations, probe durations and exact request conditions retained. Native model/effort was requested as inherited session configuration, not an exact pinned name. Resolved/observed configuration, usage, latency and invoice are unexposed; no provider attestation or zero-price inference is claimed. |
| E12 evaluation | Demonstrated for the bounded pilot | Semantics, actions, corrections/damage, uncertainty, probe utility, audit/routing opportunities, scenarios and learning reuse separated. Actual E2E only one context group and no incremental gain; other actions static. |
| E13 deliverables | Unverified until publication | Source, raw evidence and replayable calculations exist locally. Final report/matrix, retrievable artifact publication and later independent adversarial review remain to be checked at handoff. |
| E14 completion/recommendation | Demonstrated for measurement; final handoff unverified | Required bounded policy measurements now exist with the explicit post-hoc comparison amendment and scientific limitations. Negative/inconclusive recommendation is supported. Final publication and document-05 separate-session adversarial review remain pending at this review checkpoint. |

## Recommendation

Retain structured checks plus applicable probes for this explicitly structured workload. The measured Jev stack and adversarial stage do not justify a production gate: they consume additional resources and produce worse actions than that comparator. Native reasoning has conditional corrective value on this paired set, with unknown cost and unexposed exact configuration; the actual P4 policy did not exploit it. The exploratory supplement gives P4 no allocation advantage under either declared loss scenario. A stronger routing claim needs a prospectively frozen, larger grouped sample with pinned observable configuration, controls separating new evidence from reinterpretation, broader causal action executions and meaningful total-cost accounting. The separate document-05 adversarial handoff remains necessary; this within-run review does not replace it.

## Retained evidence and replay commands

Paths here are relative to `experiments/jev_failure_correlation_v2/`. The main execution directory `/workspace/jev-v2-runs/cloud-20261024-01` is retained in `evidence/cloud-20261024-01.zip`; the supplement is in `evidence/supplement-20261024-02.zip`. The premeasurement reviewer executions at `/tmp/jev-v2-independent-probe-review` and `/tmp/jev-v2-independent-probe-review-sockets` are retained in `evidence/independent-probe-preflight.zip` and `evidence/independent-probe-preflight-sockets.zip`, preserving those root directory names. The default-sandbox socket-denial records remain alongside the separately successful socket executions. Reviewer calculations originally at `/tmp/jev-v2-review-results` are copied to `evidence/preflight/review-results/`. ZIP inventories and hashes belong to the publication index; hashes alone do not establish semantic sufficiency.

The reviewer compared every included file in those four archives to its original execution file: 1,204 main-run, 82 supplement, 67 default-sandbox preflight and 27 socket-enabled preflight entries. All matched; no original was missing. `archive-content-check.json` retains archive hashes and counts. The premeasurement label approval hash remains `62c50cd0fe5b58c6e91e926f832c19a050340d53070e3181ab39534979949020` after all review updates.

From the measured checkout and retained run directory, use `runner.verify_evidence(Path(RUN))` for read-only raw replay. The reviewer additionally called `evaluation.evaluate` with retained cases, decisions, transport/native attempt ledgers, audits, probes and paired populations; `final-replay-check.json` records zero core report mismatches. The ordinary `runner score` command rewrites its derived report, so use an extracted working copy for that command. Supplement read-only replay is:

```sh
python experiments/jev_failure_correlation_v2/evidence/supplements/random_probe.py replay \
  --out /path/to/supplement-20261024-02 --main /path/to/cloud-20261024-01
```

The reviewer independently executed eight probe boundaries and calibration controls before measurement, then inspected/recomputed retained main and supplementary evidence after measurement. It did not rerun paid models, write their responses, repair product code, author the experiment implementation, or commit changes. Its comments influenced premeasurement fixes and the supplementary design, so this is a participating within-run review with declared exposure, not an untouched external replication.
