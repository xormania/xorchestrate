# Independent raw recomputation

Subject: `cfa3549490ee9db1c3e6cad918523cd854902e51`, main archive `cloud-20261024-01`. This review track independently reexpressed arithmetic and decision composition in Python's standard library. `recompute_raw.py` imports no experiment modules and never reads `evaluation.json`. It made no provider calls. It treats the supplied outcome keys as declared targets; this is not independent validation of how those targets were established. Corpus validity, raw projection semantics, executable controls and original replay coverage are separate review tracks.

The reviewer read the user request, PR metadata and favorable/unfavorable conclusions in the handoff, report and economics before constructing this recomputation. It also read implementation source to identify the frozen decision and routing contracts. It was therefore independent code and a fresh reviewer context, not a blind adjudication or independent sample. All counts are descriptive; 24 cases represent seven dependency groups. Original raw outcomes were not overwritten.

Run with an extracted original archive:

```sh
python3 recompute_raw.py /path/to/cloud-20261024-01 --output recomputed.json
```

Python 3.12.14 executed successfully. The complete machine-readable result is `recomputed.json`.

## Original byte and semantic joins

All these original-artifact checks passed:

| Check | Count |
| --- | ---: |
| Request identity, exact wire body, typed raw response, usage and price | 103 |
| Native raw request/response/invocation joins | 22 |
| Executed probe raw-file hashes and supplied operand assertions | 52 |
| Structured policy actions recomputed from visible operands | 58 |
| Model policy actions recomputed from raw option choices | 141 |
| Frozen P4 routes recomputed from raw P3 signals | 24 |
| Actual raw action records joined to decision hashes and recomputed acceptance outcomes | 23 |

The native joins validate the retained evidence relationships, not provider-attested model/effort or actual billing. Those fields remain unknown. End-to-end policy summaries exactly match independently accumulated raw execution records. The original audit selection, inclusion probabilities and independent-miss booleans also match recomputation. These results matter because the separate tamper review found some fields are not semantically revalidated by `runner score` alone. No discrepancy in these original fields was found here.

Twenty-one raw option distributions sum to 0.99 (within the frozen validator's ±0.01 allowance) rather than exactly 1; they remain unnormalized. This is rounded provider data, not a discovered action-scoring error. Main model name matches requested/returned `jev-1.14.0` throughout.

## Actions and transitions

| Policy | Correct | Wrong decisive | Abstention | Unsupported terminal |
| --- | ---: | ---: | ---: | ---: |
| P0 | 16 | 0 | 8 | 0 |
| P0_probe | 24 | 0 | 0 | 0 |
| P1 | 11 | 2 | 11 | 0 |
| P2 | 16 | 2 | 6 | 0 |
| P3 | 15 | 2 | 7 | 0 |
| P4 | 19 | 2 | 2 | 1 |
| Never, paired 11 | 7 | 1 | 3 | 0 |
| Always, paired 11 | 10 | 0 | 1 | 0 |
| Random, paired 11 | 7 | 1 | 3 | 0 |
| Risk, paired 11 | 9 | 0 | 2 | 0 |
| Reasoning-only, paired 11 | 7 | 0 | 4 | 0 |

These match the report. P1→P2 resolves six abstentions correctly, converts one abstention to a false clear, converts a false rejection to an abstention, and converts one correct action to an abstention. P2→P3 changes exactly one action: a correct acceptance becomes an abstention; both false clears persist. P3→P4 resolves four abstentions correctly, changes an unsupported case's `investigate` to `defer`, and retains both false clears. Never→always corrects one false clear and resolves three abstentions, while changing one correct acceptance to an abstention. Calling all five changes improvements would be wrong.

Full staged reasoning exceeds blind initial reasoning by three correct actions on the paired set. The three cases gained P2 observations absent from the blind condition; this is not isolated evidence of superior Jev diagnosis or routing. Both conditions use eleven native invocations, but their prompt/token costs are not known equal.

## False clears, routing and audit

`cef7128badec1` is a known authority fault with explicit initial mismatches `final-actor != old-actor` and `final-work != old-work`. `c51eabfc2dae5` initially withholds those observed operands, then obtains the authority probe. Both terminate at P4 `accept`, route `none`, with raw native judgment confidences 0.95 and 0.85. A direct structured reading rejects both post-P3 packets. The fault and missing variant belong to the same authority dependency group; they are not two independently sampled incidents.

P4 routing is 11 probes, 13 no-investigation, zero reasoning. The original random reasoning comparator is exactly the same as never because it matches zero reasoning calls. This is an executed but degenerate control, not a test of equal investigation allocation.

Do not assign a causal role to a nonexistent numeric confidence threshold: `policies.py:175–200` routes using route, uncertainty, relevance, candidate discrimination and assumed consequence bounds. Native confidence is descriptive here and is used in audit stratification, not in those route predicates.

The audit independently samples 13/24 cases. Both errors are in `accept:none:high`, where two of four cases are sampled. The retained seeded sample selects the two healthy cases. Given the frozen population and actual SRS design, missing both errors has probability `choose(2,2)/choose(4,2) = 1/6`. It is unsurprising enough to refute a claim that zero sampled misses demonstrates no errors, but is not evidence of biased randomization. All cases have positive inclusion probability; the reconstructed Horvitz–Thompson point estimate is zero. Do not apply an IID binomial production guarantee to these strata and grouped fixtures.

## Probe information versus reinterpretation

| Stage | Records/executed | Seconds | Structured correct before→after on selected cases | Policy action improvements |
| --- | --- | ---: | --- | ---: |
| P0_probe | 8/8 | 2.616996 | 0→8 of 8 | 8 |
| P2 | 22/21 | 7.975212 | 14→22 of 22 | 6 |
| P4 | 11/10 | 4.035693 | 11→11 of 11 | 4 |
| Independent audits | 13/13 | 4.394689 | Not an operational policy comparison | 0 credited |

The P2 count includes one unsupported probe on an already structured-solvable case; it did not supply new evidence. All 11 P4 selected cases were already correct under structured interpretation before their extra probe. Independently applying the structured interpreter to all available post-P3 packets gives 24/24 correct actions with no new observations. This directly controls the interpretation confound in `runner.py:232–234`, which runs a probe and then switches to the structured baseline. The four observed P4 action gains cannot be uniquely attributed to the additional probes. Conversely, P0_probe's eight acquisitions genuinely fill initially missing operands and resolve all eight uncertainties; do not dismiss all probe utility based on the later redundant probes.

`evaluation.py:199–200` defines information from execution plus a distinguishing flag, without comparing prior information. That is not an incremental information-gain measurement. The report already qualifies it; machine-readable consumers must retain the qualification.

## Robustness belongs to the composed system

| Transformation | Cases | Raw judgment invariant vs P1 | Raw judgment correct for transformed stage | Composed action correct |
| --- | ---: | ---: | ---: | ---: |
| Reversed options | 6 | 6 | 4 | 4 |
| Irrelevant context | 6 | 6 | 4 | 4 |
| Equivalent wording | 6 | 5 | 5 | 5 |
| Removed decisive evidence | 6 | 4 | 0 | 6 |
| Wrong subject | 6 | 4 | 0 | 6 |

The raw judgment question never answers `unresolved` in the twelve removed-evidence/wrong-subject checks, while the composed action safely becomes `investigate` in all twelve. The applicability/sufficiency guards in `contracts.py:143–146` supply that protection. A claim of robust hybrid behavior is supported; a claim that Jev's judgment itself responds correctly to missing evidence is contradicted here. Stable wrong judgments on irrelevant context and option reversal are not instability failures, but are still wrong.

Raw stage-relative judgment correctness is P1 12/24, P2 17/24, P3 17/24. Composed judgment correctness is 19/24, 16/24, 15/24 respectively. P3 raw judgment error-ranking AUC is 0.8361, yet confidence ≥0.8 retains two errors among thirteen cases and ≥0.95 retains one among nine. Descriptive ranking ability does not establish a useful acceptance threshold or calibrated probabilities.

## Costs and actual consequence

Raw main usage totals 540,670 input and 89,137 output tokens across 103 attempts. With the retained input list price $0.042/million and free output, independent Decimal arithmetic yields $0.022708140. This includes the main archive's smoke request. The separately retained earlier development smoke is outside that archive and must not be silently folded into its 103 calls. Native actual costs remain unknown.

Per-policy request dependencies reproduce P1 $0.004811100, P2 $0.010677576 and P3/P4 $0.016573242. Main collection expenditure and hypothetical deployment subtotals are different accounting scopes, not additive bills. The P3 incremental Jev cost is $0.005895666 for no final-action correction and one added abstention.

Under the declared analyst scenarios, gross improvements (low/consequential, before resource costs) independently reproduce:

- P0→P0_probe: $2/$16 over 24 cases.
- P0→P1: −$3.25/−$126; P1→P2: −$0.25/−$70; P2→P3: −$0.25/−$2.
- P3→P4: $1/$8 over the original 23 priced `ok` rows; including the retained unsupported terminal gives $1.15/$5 over all 24.
- P0→P4 including the terminal: −$2.60/−$193.
- Never→always: $2.50/$104 over eleven paired cases, giving at most $0.227273/$9.454545 per native invocation before other costs.

All 23 actual acceptance-state executions in the one context family have the correct resulting state for all policies. This yields no measured comparative consequential improvement. Scenario improvements outside that subset are hypothetical action-loss changes. Source extraction, development, queueing, human review, machine charges, native token costs and invoices remain partly or wholly unpriced. Nothing in this recomputation converts label improvement or reusable checking into realized business profit.

The bounded negative conclusion survives: this recorded stack has no demonstrated advantage over its strong structured-plus-probe comparator. It remains unjustified to extrapolate that to naturally occurring unstructured incidents, other Jev versions, broader correlations, or a prospective matched-cost policy study.
