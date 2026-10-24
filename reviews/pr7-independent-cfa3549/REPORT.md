# Independent adversarial review of PR #7

Review date: 2026-10-24. Subject: **`cfa3549490ee9db1c3e6cad918523cd854902e51`**, PR #7, `experiment/jev-failure-correlation-v2`. Publication branch: `verification/pr7-independent-cfa3549`. xor remains final authority.

**The bounded negative result survives review: this measured Jev stack does not improve on structured checks plus applicable probes. The evidence does not establish that Jev, reasoning, or investigation is generally useless on operational incidents.** The retained principal counts are reproducible. Adoption as a production gate is unsupported. Isolated economic-routing effects and attribution to a named reasoning model remain unverified; a green execution is not their substitute.

Implementation correctness is substantially supported by replay and executable controls, with a concrete semantic-replay coverage gap. Measurement validity is adequate for a small, prepared-evidence pilot; it is insufficient for isolated routing effects, broad correlation ability, or operational prevalence. Economic usefulness is conditional on analyst loss assumptions, with no measured downstream improvement in the only actual acceptance-state comparison.

## Scope, identities, and independence

PR HEAD equaled the pinned candidate at both initial and final pre-publication inspection. PR #6 was separately open/draft at `ef7567516520543970af55b6f19534cb66eda357`. Initial API snapshots are retained in `evidence/`; a final state check accompanies publication. Existing workspace material was inspected for repository identity and guidance, then a separate clone and virtual environment were used. No experiment implementation, original evidence, frozen v1, product source, or PR #6 work was edited. No paid model calls, merges, deployments, or PR comments were made.

| Role | Commit | Tree |
| --- | --- | --- |
| Product subject / frozen v1 | `d2d2123bb8d4e866454c8e8e364f7749fa63a198` | `7bb88d5fd01a21478b720ad2d42b2b627b74bb92` |
| Main measured source | `9c6d7612e31bb78eb07868b5ac2a046629c5876c` | `5313e3a176515e7b160315a56da430fcde63b189` |
| Original hosted validation | `0d8d5187209c1fb2c92e8d2a4305c6f7b40da9aa` | `febf6a8676f5231ff585019b6374dbc190b75be4` |
| Post-hoc supplement source | `4577eb2c09a211218cfb822f195127d6a8a3f918` | `58930924d0d7fe0db7def67625cd09354d0b9c00` |
| Evidence publication / reviewed candidate | `cfa3549490ee9db1c3e6cad918523cd854902e51` | `a5d9f1d77aca18c7a5540f22a0b628474fcc878a` |
| Independent hosted workflow | `d5af81334d5742968a138665d738d3b20084b72a` | `c858073fd6512cfbb79be90aa19196ac9c3afc4d` |

All 1,735 base tracked files are unchanged in the candidate. Its 197 additions are scoped to v2 and its workflow. All 99 measured-source hashes, 28 corpus inventory hashes, five archive identities, and the complete 1,203-file main seal verify. The hosted-only revision changes checkout history depth; the supplement and evidence publication do not change the main measured executable/data bytes. Corpus version is `2.0.1`; policy version is `policies-2.0.0`; main requested/returned Jev is `jev-1.14.0`. See `notes/provenance.md` and `results/provenance-results.json`.

This is a fresh review conversation, but **not a blinded review**. The reviewer saw the owner's concerns, PR description, handoff, implementation report, economics, existing preparatory reviews, keys, and outcomes. Five parallel reviewer tracks shared this conversation's instructions and communicated findings. They are independent of the original implementation session, not independent statistical replications or different-model votes. The new arithmetic scripts avoid importing the original scorer; behavioral checks necessarily exercise its source and fixtures. Original label authors and the preflight reviewer knew prior v1 mechanisms. Their recorded pre-key review chronology is internally consistent, not independently attested historical blinding. No new reviewer-authored case is called a holdout.

## Executed and inspected

Executed: relocated keyless main replay with seal verification before and after; separate supplement replay; independent raw-ledger arithmetic; independent provenance/native joins; integrated v2/v1/hexagonal tests; eight behavioral families with healthy/intended-fault controls and setup/helper/timeout controls; four actual acceptance-action controls; bounded-failure composition checks; and twelve controlled evidence mutations, each paired with the untouched baseline and outer-seal check.

The assertion calibration retains the intentionally bad product behavior: its old classifier calls both a wrong return and an unrelated `AssertionError('TARGET_REQUIREMENT')` a detection. The experiment separates them using executed phase/actual/expected records: wrong return reaches the comparison; helper failure reaches only entry. Healthy return passes. Thus assertion text or an exception name alone receives no intended-detection credit. The 24 wrapper setup/helper/timeout injections occur before the operation and do not cover every internal failure location; the assertion-family helper additionally fails inside actual pytest. See `notes/ADVERSARIAL-CHECKS.md`.

The initial restricted local suite retained **25 socket-permission failures, 666 passes, eight skips, 172 passing subtests**. With authorized local sockets it produced **691 passes, eight tmux skips, 172 passing subtests**. Those failures/skips were not counted as detections. Independent behavioral controls passed **40/40** with sockets, plus four actual action controls. Fresh GitHub-hosted run [40131762088](https://github.com/xormania/xorchestrate/actions/runs/40131762088) explicitly checks out the pinned candidate, supplies no provider secret, and runs the remaining tmux coverage, corpus, and both replays. Exact result, original ZIP identity, and environment versions are recorded in `EXECUTION.md` and `evidence/`.

Inspected and recomputed, without fresh provider execution: 103 main Jev attempts, 22 native request/result/invocation records, 54 main probe records, 13 sampled audits, 23 main acceptance-action executions, 30 robustness requests, and the ten additional supplemental probes. No new installed-vendor execution, operational incident sample, paid response, native invoice, or provider model attestation was obtained. Original hosted run [40071712425](https://github.com/xormania/xorchestrate/actions/runs/40071712425) independently cross-checks to **699 tests + 172 subtests**, no failures/errors/skips, with its live job skipped. Its preceding failure is retained, not retroactively relabeled.

## Independently recomputed outcomes

`scripts/recompute_raw.py` reconstructs 141 model-derived and 58 structured actions, checks 103 raw wire/response/usage joins, 22 native joins, 52 executed probe raw-hash/operand joins, all 24 P4 routes, and 23 raw action/decision bindings and summaries. All original joins examined agree. `scripts/recompute_economics.py` separately recomputes costs, supplemental selection, and the no-additional-evidence control. These scripts treat the retained outcome keys as declared targets; executable controls and manual stage review supply the separate check on those targets.

| Policy | N | Correct final action | Wrong decisive action | Abstention | Unsupported terminal |
| --- | ---: | ---: | ---: | ---: | ---: |
| P0 | 24 | 16 | 0 | 8 | 0 |
| P0 + probe | 24 | 24 | 0 | 0 | 0 |
| P1 | 24 | 11 | 2 | 11 | 0 |
| P2 | 24 | 16 | 2 | 6 | 0 |
| P3 | 24 | 15 | 2 | 7 | 0 |
| P4 | 24 | 19 | 2 | 2 | 1 |
| Never / original random reasoning | 11 | 7 | 1 | 3 | 0 |
| Always reason after P3 | 11 | 10 | 0 | 1 | 0 |
| Simple risk | 11 | 9 | 0 | 2 | 0 |
| Reasoning only | 11 | 7 | 0 | 4 | 0 |

P0 is **24/24 epistemically correct initially**: its eight unresolved outcomes require facts that were not then observable. Do not count those as confident mistakes. P1→P2 resolves six abstentions, turns one abstention into a false clear, changes one false rejection into abstention, and changes one correct acceptance into abstention. P2→P3 corrects no final action and adds one abstention. P3→P4 resolves four abstentions and retains both false clears; a fifth changed row terminates unsupported/defer. Always versus never corrects one wrong decisive action, resolves three abstentions, and changes one correct acceptance into abstention. Full transition matrices retain unchanged-right, unchanged-wrong, and unresolved categories.

The two P4 false clears are `cef7128badec1` and `c51eabfc2dae5`, both authority-binding variants in **one dependency group**. Jev's native judgment-confidence field is 0.95 and 0.85. The first already exposes the mismatched final actor/work against the original authority; the second initially lacks observations but receives them in P2. Both still have route `none` at P4. There are 11 probe routes, 13 no-investigation routes, and **zero reasoning routes**. The router uses categorical relevance/uncertainty/probe signals plus assumed consequence bounds, **not a numeric confidence threshold**. These cases do not show that the sole useful observation was never collected.

The 13-case random audit observed no terminal errors. Routes and sampling reconstruct correctly, with inclusion probabilities 0.5, 4/7, and 0.6. Both errors lie in the four-case `accept:none:high` stratum, of which two were sampled. Given this fixed population, missing both has probability `C(2,2)/C(4,2) = 1/6`. Zero Horvitz–Thompson point estimate is compatible with the known two misses; it neither establishes safety nor demonstrates biased sampling. It also does not measure all missed useful investigations.

## Prioritized findings and minimal controls

### F1 — High: the allocation comparison does not isolate Jev routing value

The original random arm allocates zero reasoning calls because P4 selected zero. It equals never-reason and does not match P4's five paired probe calls. The supplement is an honestly labeled, pre-execution-frozen **post-hoc** remedy: five additional probes each, one seed, eleven related cases. It changes candidate selection as well as case selection; P4 uses Jev's candidate while random/simple use visible-invariant dispatch. It also compares original P4 timings with later fresh executions. Five calls are neither equal time nor equal monetary cost. No Jev advantage was observed in this composite-policy comparison; it does **not** prove a general random-policy advantage.

Control/reproduction: the economic script reconstructs seed 4017 selection from the frozen plan, candidate dispatch from visible packets, and prefix/request/probe accounting. It finds no hidden-key selector. On the paired set, P4 is 9 correct / 1 wrong / 1 unresolved; random and simple each 9 / 0 / 2. Their durations are 2.709180, 0.776317, and 0.175621 seconds. The shared P3 prefix is $0.007946988 Jev list-price usage plus 4.686494 probe seconds and cancels only in incremental comparisons. Blind reasoning sees initial evidence; fallback additionally sees P2 observations and P3 answers, so that comparison does not isolate the value of Jev's diagnosis either.

### F2 — High: additional probe benefit is confounded with changing the interpreter

All ten executed P4 probes repeat the decisive check operands already present in their available P3 packets. Structured reinterpretation of those packets produces **24/24 correct actions, including 11/11 paired**, without additional calls or observations. P4's four resolutions therefore cannot be credited uniquely to its new probes. The same caution applies to supplemental gains. This is a post-hoc ablation, not a prospectively selected new winning arm, but it is sufficient to falsify unique attribution to new information on the retained packets.

The positive control matters: P0 + probe really acquires eight initially missing outcomes, resolving all eight in 2.616996 seconds. Thus the review does not conclude that probes have no information value. `probes.py:375` assigns `distinguishing=True` to every applicable executed probe; `evaluation.py:199` turns that flag into `information_gain_observed` without testing prior availability. That field overstates incremental information; the report already acknowledges this limitation. Reproduction: compare the independent interpreter before/after each selected probe, not merely its execution flag.

### F3 — High: transfer to operational incidents and broad failure correlation remains unmeasured

Inputs expose invariant names, subject bindings, operators, and left/right operands with a fixed candidate inventory. P0 evaluates that supplied structure directly, and the key generation uses corresponding assertion comparisons. This is an appropriate strong comparator for the declared input contract; replacing it with a weak lexical baseline would manufacture a gain. Its construction advantage also means perfect results do not measure extracting correct structure from ambiguous logs, finding an unknown mechanism, or generating a probe. Those costs and failures are absent.

There are 24 cases but seven dependency groups: development 6/2, calibration 6/1, holdout 12/4. Holdout varies known trajectories, not new mechanisms. All eight initially missing-outcome variants ultimately have adverse outcomes; missing-but-healthy controls are needed to distinguish evidence acquisition from a conservative prior. Only three linked views of one cross-adapter family test relationships, with no positive common-cause source control. The initial/outcome keys are usefully distinct and no direct key/split/injection field leakage was found in inspected model packets. However semantic schema encodes much of the task. These are limits on both favorable and unfavorable generalization, not evidence that the observed authority errors are fictitious. The methodology script supplies a minimal representation control: retaining raw authority observations but removing `facts.checks` changes P0's healthy/fault accept/reject to investigate/investigate; merely reversing check order preserves both answers. No model call was made on the stripped packets, so this demonstrates structural dependence, not a model win.

### F4 — Medium: the semantic scorer does not independently verify every raw-to-summary claim

Untouched replay passes. Six mutations are rejected by semantic scoring: decision action, request wire body, returned pinned model, probe raw bytes, action raw bytes, and audit inclusion probability. Six others pass `runner.score`: an end-to-end success total changed to 999, an action decision hash changed, a native raw response changed, native invocation metadata changed, an audit miss result changed, and P4 route metadata changed. See `results/tamper-results.json` and `scripts/tamper_replay.py`.

**The unchanged complete seal rejects all twelve mutations.** This is not a bypass of the documented verify→score→verify sequence, and no corresponding corruption was found in the original archive. The gap is that scoring alone is not the complete semantic reconstruction suggested by broad replay language. `runner.py:489` trusts imported native responses rather than joining the raw invocation files; `runner.py:547` checks audit design but not its adjudication; `runner.py:607` checks action rows against raw files but copies aggregate summaries and does not check decision binding. Our independent arithmetic joins and recomputes those original fields successfully. No implementation repair was made.

### F5 — Medium: robust final uncertainty is partly composition, not correct raw judgment

All six removed-evidence and all six wrong-subject transforms end with appropriate unresolved/investigate behavior. But **0/12 raw `judgment` choices are unresolved**; the applicability/sufficiency composition supplies the correct final action and uncertainty. All twelve raw judgments specifically say `contradicts`; composed violation lists can still retain unsupported allegations, so this is not a fully correct semantic record. This supports the hybrid guard and qualifies a claim about Jev's standalone judgment sensitivity. Reverse-options and irrelevant-context transformations each preserve all six judgments yet only four are correct; stable wrong answers remain wrong. Equivalent wording preserves five and is correct on five. These 30 calls cover three groups (context, arguments, replay), excluding authority, where both false clears occur. Adaptive search budget was zero. Replay scripts separate raw answers, composed actions, invariance, and correctness.

### F6 — Medium: economic and completion labels require narrower scope

The original monetary pair excludes P4's unsupported row because `status != ok`, despite its known defer action and healthy outcome. Preserve the original result, but include that known terminal in sensitivity reporting: P3→P4 gross reduction changes from $1/$8 over 23 rows to **$1.15/$5 over 24**. The report discloses this; the independent computation confirms it. Likewise 23 actual context acceptance executions all succeed across policies, so no comparative downstream improvement was observed. Model labels, static action differences, and assumed avoided losses are not realized business effects.

Native raw records consistently support 22 separate recorded invocations and distinct conditions, but exact observed model/effort, token counts, latency, and billing are absent; requested model/effort inherit the session. This does not justify fabrication allegations or zero prices. It limits attribution to the recorded native route. Execution completion does not establish causal routing value or named-model attribution.

## Costs and conditional usefulness

Raw usage sums to 540,670 input and 89,137 output tokens over 103 successful main Jev attempts, with no retries. At the retained $0.042/million-input list price and free output, the estimate is **$0.022708140**; the separately retained earlier development smoke adds $0.000013566. The 103 main attempts already include their own smoke. This is not an invoice or total study cost. Native charges, some queue/review/development/extraction costs, and production consequences remain unknown. Twenty-one rounded probability distributions sum to approximately 0.99, within the retained validator tolerance; no normalized substitute or material action difference is inferred.

| Comparison | Gross assumed loss reduction: low / consequential | Interpretation before unpriced costs |
| --- | ---: | --- |
| P0 → P0 + probe | $2 / $16 | Useful evidence acquisition on this corpus; break-even total extra cost $2/$16 |
| P0 → P1 | −$3.25 / −$126 | Harmful under both frozen scenarios |
| P1 → P2 | −$0.25 / −$70 | More resolved actions, worse assumed loss because of a false clear |
| P2 → P3 | −$0.25 / −$2 | Added uncertainty, no corrected final action, added model cost |
| P3 → P4, all 24 known actions | $1.15 / $5 | Conditional benefit over a weaker predecessor; new-probe causality unproved |
| Never → always, paired 11 | $2.50 / $104 | One correction + three resolutions − one new abstention; eleven unknown-price native calls |

Always-reason breaks even at at most $0.227273/$9.454545 per added native call before other costs; at $1/call its gross-minus-native value is −$8.50/$93. This gives conditional value relative to P3, not superiority to P0 + probe. P4 remains worse than P0 on all 24 known terminal actions by $2.60/$193 before resources. P0 + probe reaches 24 correct actions with fewer probes and no model-call requirement; developer/structure-extraction effort is not priced, so do not extrapolate complete operational cost dominance.

Random/simple supplement each reduce assumed loss by $2.25/$102 versus P3, compared with P4's $0.50/$4 on that subset; candidate, time, seed, and exposure confounds remain. Reasoning-only has 7 correct and 4 abstentions with no wrong decisive action, but three abstentions correctly lack observations. Its unmeasured native cost and different evidence prevent a general cost ranking. Never/original-random retain known errors and are weaker than full reasoning under these losses; simple risk helps conditionally but uses ten native calls with unknown charges. P1/P2/P3 are unsupported additions over the stronger comparator; P4 and reasoning are at most conditionally useful relative to a flawed predecessor. No policy shows actual comparative acceptance-state benefit. Deterministic guard reuse in four holdout groups is scoped reuse evidence, not monetized learning ROI.

## Minimum additional evidence and owner decision

No new paid calls are needed to establish this review's current negative finding. Before an adoption or broader allocation claim, the smallest useful next evidence package is:

1. Freeze a common post-evidence population and identical candidate/interpreter rules; include structured reinterpretation at zero new observations, probe-only, never/always, simple, and nondegenerate randomized allocation. Match measured resource budgets (or bound a declared proxy) prospectively and retain multiple seeds or an exact allocation analysis. Isolate candidate generation from routing.
2. Add missing-but-healthy controls, positive/negative common-cause source controls, authority robustness transformations, and a clean updated-evidence versus original-plus-delta representation control. Initial missing notes remaining beside new observations are a plausible presentation burden, not a demonstrated cause of either false clear; the initially decisive error cannot be explained by such notes.
3. For operational transfer, independently sample incidents and charge the work/error of structure extraction and probe availability. Keep variants grouped and broaden relationship families. Measure actual downstream effects for authority/effect errors and false alarms, not just the already-safe context subset.
4. For a named reasoning comparison, obtain a route exposing requested/resolved/observed model and effort, invocation provenance, usage, time, and billing or explicit bounds. Reasoning alone and fallback must receive matched observations to isolate diagnosis value.
5. Before calling replay a complete independent verifier, add semantic joins for native raw records, audit findings, routes, action bindings, and aggregate outcomes, with the demonstrated mutations as sustained controls. This is a future repair recommendation; this branch contains review code only.

The existing report is unusually explicit about many of these limitations; the review confirms that candor rather than treating disclosure as proof the gaps are closed. Retain the bounded negative result and useful harness/evidence. Withhold production adoption and full routing/model-attribution sign-off. The E01–E14 decisions in `VERDICTS.md` separate demonstrated requirement scope from unverified extensions. xor decides whether to close this as an exploratory negative pilot or authorize the additional scope.

See `REPLAY.md` for keyless commands, `EXECUTION.md` for environments and blocked checks, `VERDICTS.md` for all requirements, `notes/` for independent tracks, and `results/` for machine-readable recomputation and controls.
