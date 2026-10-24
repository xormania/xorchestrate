# Independent economics and supplement review

Review subject: `cfa3549490ee9db1c3e6cad918523cd854902e51`. Main measured source: `9c6d7612e31bb78eb07868b5ac2a046629c5876c`. Supplement execution source: `4577eb2c09a211218cfb822f195127d6a8a3f918`. This review does not replace retained outcomes or repair implementation. xor remains final authority.

## Scope and independence

I read the handoff, independent-verification assignment, specifications 00–04, frozen economic plan, economic findings, supplement plan/script, relevant policies/runner/probes/economic code, and main/supplement raw archives. I was exposed to the published results and hypotheses before writing the recomputation; this is an independent implementation of arithmetic and counterfactual interpretation, not a blinded study. I coordinated with the separate methodology and raw-recomputation reviewers, including correction of an initially overbroad concern about stale missing-evidence notes.

Executed with Python 3.12.14: archive extraction into a separate review directory; the original supplement's keyless `replay` (passed); a new stdlib-only recomputation script (passed); and 96 synthetic interpreter controls (passed). No model calls, production actions, or fresh behavioral probes were made by this track. Root's other tracks cover behavioral execution and main keyless replay. This script does not import experiment evaluation/policy code or use `evaluation.json` or supplement `report.json`. It recalculates from decisions, request dependencies, raw provider response usage, probe ledgers and raw acceptance-action records. It uses the retained outcome key; independently proving every behavioral label is a separate review task.

## Decision-relevant findings

1. **Additional P4/supplement probes do not demonstrate new information value.** All ten executed P4 probes reproduce exactly the same semantic check lists already present in their P3 `available` packets. The paired P4 five, random five and simple five each contribute zero changed check lists. An independently written interpreter over the retained current facts returns 24/24 correct actions, including 11/11 paired, without another probe/model call. The P4 path explicitly switches to `baseline(updated)` after execution (`runner.py:228–234`), so action improvements jointly change execution and interpreter. The evidence supports “a changed interpretation was sufficient” and does not identify new-probe causation. This is consistent with the publication's qualification, and stronger than merely observing that probes sometimes look redundant.

   Minimal reproduction: run `recompute_economics.py` and inspect `P4_same_check_lists_before_after`, `supplement.*.new_semantic_check_lists` and `posthoc_no_new_evidence_ablation`. Controls replace check operands with healthy/faulty values, hide observation flags, or give foreign-subject identities: all 24 packets respond with accept/reject/investigate/investigate respectively. These are synthetic post-hoc controls, not new incidents or new Jev results. P0's eight initial missing-observation probes still acquire needed facts; this finding does not deny the value of deterministic probing in general.

2. **The original random comparison is degenerate; the supplement remains exploratory.** `runner.py:309–315` matches reasoning calls only. P4 requests zero reasoning calls, so original random and never have identical 11 actions, inputs and costs. They cannot estimate relative allocation of P4's five paired probe calls. The supplement genuinely uses a pre-execution frozen selection and five calls per policy, but its design followed main outcome exposure. The original P4 candidates come from Jev while random/simple candidates come from a visible-invariant dispatcher (`random_probe.py:49–73`). Thus case selection and candidate selection vary together. One random seed across seven dependency groups is no estimate of a general random-policy advantage.

   The supplement plan `197bd8547da565db8284bccee821864cb3307e50f9a28350f92098302a53e6ca` was prepared at `06:59:42Z`, execution began at `07:01:23Z`, and replay revalidated original main hashes and selections. Inspection found no outcome-key/scenario/split lookup in selection. All eleven P3 packets have zero missing checks, so the simple rule reduces to consequence weights plus case-ID ties. This disclosure is accurate. A visible-invariant dispatcher can encode workload-specific prior knowledge without constituting hidden-label leakage.

3. **The full-stack versus blind native gain is entirely on the three cases receiving additional decisive observations.** `runner.py:337–339` supplies both native conditions the same original evidence, but adds P3 answers and a P2 probe observation only to fallback. All action differences between `reasoning_only` and `always` are `investigate→reject` for `c19acb40f8797`, `c1c062765510d`, and `c51eabfc2dae5`; those are precisely the three initially missing-evidence cases in the paired population. All other eight actions agree. The 7→10 correct-action comparison is valid as a comparison of those complete conditions, but cannot show that Jev diagnosis improves native reasoning independently of new observations. An evidence-matched blind native comparator is missing. This does not erase always-versus-never's correction of one false clear, three uncertainty resolutions, and one new abstention.

4. **Known unresolved-terminal action is omitted from primary economic pairs.** `economics.py:225–227` excludes any non-`ok` row even when its action and truth are known. P4's unsupported `p_attempts` selection on `c5dd2167f348a` produces a known `defer` action on a healthy case. Including it changes P3→P4 gross value from $1/$8 on 23 to $1.15/$5 on all 24 under low/consequential scenarios, and P0→P4 from −$2.50/−$188 to −$2.60/−$193. This is an analysis limitation, already explicitly disclosed by the publication, not a reason to overwrite its historical artifact. Use all-known-action estimates for decisions and retain the published 23-case denominator for provenance.

5. **No measured production loss, benefit, or learning ROI exists.** The 23 raw Application/SQLite acceptance-action executions cover three related context cases in one group. Recomputed actual `after.accepted` matches the independently executed configured predicate in every row. Investigation/reject/defer all withhold acceptance in the bad-context case; this subset shows no policy advantage, and does not test the authority false clears' downstream consequences. Loss assumptions assign handling/delay costs to unresolved decisions even when withholding is behaviorally safe. These are declared analyst scenarios, not actual dollars saved or lost. Native invoice/token/effort attribution, review work, candidate construction, source retrieval and production recurrence remain unknown. Reusing an existing guard on later variants is not an estimated learning intervention or monetized reuse.

## Recomputed main policy results

“Correct/wrong/unresolved” distinguishes decisive correctness from uncertainty. Costs are hypothetical policy dependency subtotals; they are not extra collection invoices. All losses below are assumptions, and all native charges remain unknown.

| Policy | Correct / wrong / unresolved | Jev calls | Jev list-price USD | Probe records / seconds | Assumed total loss, low / consequential |
| --- | ---: | ---: | ---: | ---: | ---: |
| P0 | 16 / 0 / 8 | 0 | 0 | 0 / 0 | 2 / 16 |
| P0_probe | 24 / 0 / 0 | 0 | 0 | 8 / 2.616996 | 0 / 0 |
| P1 | 11 / 2 / 11 | 24 | .004811100 | 0 / 0 | 5.25 / 142 |
| P2 | 16 / 2 / 6 | 48 | .010677576 | 22 / 7.975212 | 5.50 / 212 |
| P3 | 15 / 2 / 7 | 72 | .016573242 | 22 / 7.975212 | 5.75 / 214 |
| P4 | 19 / 2 / 3 | 72 | .016573242 | 33 / 12.010905 | 4.60 / 209 |

P2 has one unsupported probe, P4 has one additional unsupported probe. Both remain charged by measured elapsed time and remain unresolved rather than detections. P4 spends 4.035693 extra seconds and makes eleven additional probe attempts; ten execute. The independent audit adds thirteen probes and 4.394689 seconds outside routine policy costs.

Raw provider responses confirm 103 Jev calls, 540,670 input and 89,137 output tokens. Multiplying returned input usage by the retained $0.042/million rate gives **$0.022708140** for the main collection. This includes all main request purposes once, not just routine P3 paths. Twenty-two native calls have unknown actual cost. Development/preflight outside the archive is not included in this main collection total.

| Incremental comparison | All known actions | Gross assumed gain, low / consequential | Interpretation |
| --- | ---: | ---: | --- |
| P0→P0_probe | 24 | 2 / 16 | Eight real missing facts acquired; before extra resource costs |
| P0→P1 | 24 | −3.25 / −126 | Negative gross value cannot be rescued by positive added price |
| P1→P2 | 24 | −.25 / −70 | Several resolutions do not offset decisive/action damage at these weights |
| P2→P3 | 24 | −.25 / −2 | No correction; one new abstention |
| P3→P4 | 24 | 1.15 / 5 | Interpreter change confounded with repeated probes |
| P0→P4 | 24 | −2.60 / −193 | Worse than initial structured comparator under declared losses |
| never→always | 11 | 2.50 / 104 | One false-clear correction, three resolutions, one new abstention |
| reasoning_only→always | 11 | .75 / 6 | Exactly three previously missing observations resolved |

On the paired eleven, P0_probe is 11/0/0 with three probes (0.724933 seconds); P4 is 9/1/1; never/random 7/1/3; always 10/0/1; risk 9/0/2; reasoning_only 7/0/4. The main data support conditional native corrective value relative to an erroneous P3 prefix, not an economic case for paying to construct that prefix when supplied structured facts already suffice.

## Supplemental costs and outcomes

| Added arm after common P3 | Correct / wrong / unresolved | Calls | Added seconds | Gross assumed gain, low / consequential |
| --- | ---: | ---: | ---: | ---: |
| P4 | 9 / 1 / 1 | 5 | 2.709180 | .50 / 4 |
| random_probe | 9 / 0 / 2 | 5 | .776317 | 2.25 / 102 |
| simple_probe | 9 / 0 / 2 | 5 | .175621 | 2.25 / 102 |

Random/simple each correct the paired authority false clear and resolve one uncertainty; P4 resolves two uncertainties and retains that false clear. No arm introduces a decisive wrong action in this comparison. At $0.01/second, net before other costs is .472908/3.972908, 2.242237/101.992237, and 2.248244/101.998244 respectively. Random uses 1.932863 fewer seconds and simple 2.533559 fewer than P4. Execution time can vary by boundary, interpreter process reuse, machine state and execution session; these numbers do not establish equal dollars, stable relative latency, or review cost.

**Prefix-cost check:** the supplement correctly carries identical P3 request/probe dependencies for all three arms: 33 Jev calls, $0.007946988 list-price estimate, eleven probes, 4.686494 seconds. That prefix cancels only in incremental comparisons among these supplement arms. It does not cancel against P0_probe or blind reasoning_only. Full-stack always versus blind reasoning_only additionally spends that prefix, and unknown native prompt-dependent costs cannot be assumed identical merely because both make eleven calls. A future comparison should report whole-workflow and incremental costs separately.

## Economic assessment and minimum additional evidence

For the supplied structured workload, P0 plus applicable probes is the best measured completed policy: higher action correctness and fewer measured calls/seconds than the Jev stack. This does not prove dominance on raw operational incidents where constructing trustworthy structured operands may be difficult and costly. P1/P2/P3/P4 are unfavorable against P0/P0_probe under the two specified loss scenarios; the size of those losses is not empirical. P4's incremental benefit over P3 is interpretable but cannot justify new observation costs when reinterpretation suffices. Always-native has positive conditional gross value versus never, with break-even $0.227273/$9.454545 per added call before other costs; at $1/native call the low scenario becomes −$8.50 and consequential +$93 before other costs. Blind native is conservative on missing observations, and its lower final-action count alone is not proof of inferior reasoning. Random/simple probe selection has no supported general advantage beyond this exposed pilot.

Minimum additional evidence before an adoption/allocation claim:

1. Freeze a zero-new-observation structured reinterpretation arm; hold candidate dispatch and available evidence constant while varying case routing. Separate routing, candidate quality and interpretation factorially or by a smaller targeted matched comparison.
2. Use a prospective independently sampled/grouped population containing cases where useful evidence is truly absent after the prefix. Retain random seeds or exact group-level randomization; match expected total cost or explicitly compare a cost frontier instead of equating unlike calls.
3. Give native blind and diagnosis-conditioned arms the same observed facts, pin/report exact resolved model and effort through an observable route, and measure usage/latency/cost. No such new paid calls were authorized or made in this review.
4. Include consequential end-to-end executions for the authority/recovery failures and action alternatives; record actual downstream effects, investigation/delay work and owner-valued loss inputs. The current context-only execution cannot establish those consequences.
5. For transfer to operational incidents, test extraction of structured facts from raw incident traces, with independent labels and extraction/review cost included. For learning ROI, retain a frozen intervention and subsequent independent incidents plus a credible reuse horizon.

E03 has implementations and a measured exploratory supplement, but the original nondegenerate matched allocation comparison and causal isolation remain incomplete. E08's causal/equal-cost allocation advantage is unverified. E11's known Jev accounting is demonstrated; full native attribution/total monetary cost is unverified. E12's descriptive action/scenario reporting is substantially demonstrated, with the explicit terminal-denominator limitation and narrow end-to-end scope. No complete economic sign-off or production adoption follows from these data.

## Replay commands

Extract the original `cloud-20261024-01.zip` and `supplement-20261024-02.zip` under any new directory, then run from the pinned candidate checkout (set `REVIEW_ARTIFACTS` to the directory containing this report/script):

```sh
python3 "$REVIEW_ARTIFACTS/recompute_economics.py" \
  --main /tmp/pr7-independent/cloud-20261024-01 \
  --supplement /tmp/pr7-independent/supplement-20261024-02 \
  --output /tmp/pr7-independent/economics-recomputed.json
python3 experiments/jev_failure_correlation_v2/evidence/supplements/random_probe.py replay \
  --out /tmp/pr7-independent/supplement-20261024-02 \
  --main /tmp/pr7-independent/cloud-20261024-01
```

The computation reads retained main/supplement evidence and writes only the explicitly supplied new output. Saved review outputs are `economics-recomputed.json`, `recompute.log`, and `supplement-replay.log`. Source manifests are available in the frozen archives; the new result includes hashes of its main inputs. No credentials are needed.
