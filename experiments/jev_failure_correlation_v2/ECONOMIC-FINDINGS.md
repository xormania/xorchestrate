# Economic findings from the frozen main run

The strongest supported result favors structured checks plus applicable probes.
Jev's additional stages did not improve this comparator. Reasoning corrected a
consequential false clear in a paired comparison, but the selected P4 policy
never requested reasoning. The main run therefore does not establish that Jev
allocates investigations better than an equal-budget random policy.

Evidence: `cloud-20261024-01`, source commit
`9c6d7612e31bb78eb07868b5ac2a046629c5876c`, tree
`5313e3a176515e7b160315a56da430fcde63b189`. Figures below are recomputed from
`evaluation.json`, `decisions.json`, `probes.json`, `comparison-design.json`,
`transport/attempts.jsonl`, and `reasoning-attempts.json` in that run. There are
24 controlled cases in seven related-incident groups; the holdout has 12 cases in
four groups. These are descriptive pilot results, not production rates.

## Decisions and resource use

“Correct” means the final action matches the independent outcome key.
An initially unresolved action can be epistemically correct while still requiring
an investigation. The final column is a modeled policy deployment subtotal from
the requests it needs, not an additional bill incurred for every replayed policy.

| Policy, all 24 cases | Correct final actions | Wrong decisive actions | Unresolved actions | New probes at this stage | Jev list-price subtotal |
| --- | ---: | ---: | ---: | ---: | ---: |
| P0 structured | 16 | 0 | 8 | 0 | $0 |
| P0 plus selected probe | 24 | 0 | 0 | 8 | $0 |
| P1 initial Jev | 11 | 2 | 11 | 0 | $0.004811100 |
| P2 staged Jev | 16 | 2 | 6 | 22 | $0.010677576 |
| P3 adversarial Jev | 15 | 2 | 7 | 0 | $0.016573242 |
| P4 selected investigation | 19 | 2 | 3 | 11 | $0.016573242 |

P0's initial evidence judgments were correct in all 24 cases. Its eight selected
probes resolved eight missing observations and took 2.616996 seconds. P2 executed
21 of 22 selected probes in 7.975212 seconds; six produced an action improvement.
P4 executed ten of eleven additional probes in 4.035693 seconds; four were followed
by an action improvement. Those changes combine probing with a switch to the
structured interpreter; they do not establish that new observations caused the
gain. An executed discriminating observation is not automatically
an incremental gain when the needed fact was already known.

The same unsupported selection occurred at P2 and P4:
`c5dd2167f348a` selected `p_attempts` for an arguments-boundary subject. P4 retained
an unresolved terminal status and `defer`; it received no detection or utility
credit. P4's other unresolved actions are two abstentions. Both false clears
remain authority-binding cases, `cef7128badec1` and `c51eabfc2dae5`, which P4 sent
to no additional investigation.

P1→P2 resolved six uncertainties correctly, introduced a false clear from an
uncertain case, converted one false rejection into uncertainty, and converted one
correct acceptance into uncertainty. P2→P3 changed exactly one action: correct
acceptance became uncertainty. The adversarial stage corrected no wrong final
action. P3→P4 resolved four uncertainties correctly through structured probe
observations; it did not repair either false clear. Transition tables that retain
an “unresolved” category should not be read as counting these resolutions as
wrong-to-right corrections.

## Conditional monetary value

The frozen `ECONOMIC-PLAN.md` assigns analyst losses, not measured business loss:
false acceptance $2/$100, duplicate effect $5/$500, unnecessary rejection
$0.50/$20, investigation handling/delay $0.25/$2, and deferral delay $0.10/$5
for the low-stakes/consequential scenarios. Correct action has zero scenario
outcome loss. Resource costs are separate. A label improvement without an action
change earns no operational credit.

| Added stage | Common observed actions | Gross loss reduction: low stakes | Gross loss reduction: consequential | Incremental Jev list-price estimate |
| --- | ---: | ---: | ---: | ---: |
| P0→P0 plus probe | 24 | $2.00 | $16.00 | $0 |
| P0→P1 | 24 | −$3.25 | −$126.00 | $0.004811100 |
| P1→P2 | 24 | −$0.25 | −$70.00 | $0.005866476 |
| P2→P3 | 24 | −$0.25 | −$2.00 | $0.005895666 |
| P3→P4 | 23 | $1.00 | $8.00 | $0 |

P4's unsupported-probe terminal is excluded from the last monetary pair because
the frozen scorer excludes `status != ok`. Its retained `defer` action and healthy
outcome are known: this is an analysis-denominator limitation, not missing outcome
truth. Including that row under the same declared delay assumptions changes the
24-case P3→P4 gross reduction to $1.15/$5.00. P4 remains worse than P0 on the
original 23 priced actions by $2.50/$188 before
model and probe costs. P2 is worse than P0 plus probes by $5.50/$212 before
resource costs. Including P4's known terminal action makes its 24-case
P0-relative reduction −$2.60/−$193. These transparent sensitivity calculations do
not replace the original scoring artifact. Negative gross value cannot be
rescued by a nonnegative price.

For P3→P4, the extra probes can consume at most $1/$8 total before exhausting the
observed scenario gain on those 23 cases. Pricing their measured 4.035693 seconds
at the declared $0.01/second leaves $0.959643/$7.959643 before unmeasured review,
terminal-failure handling, and other costs. For P0 plus probes, break-even is
$2/$16 total, or at most two/sixteen review minutes at an assumed $1/minute before
other costs. These are sensitivity boundaries, not realized profit.

## Paired reasoning and routing

The paired population contains eleven cases; never/always/random/risk and
reasoning-only use that same population. The native evaluations were 22 real
fresh sessions, one per packet. Requested model and effort were inherited session
configuration; the host did not expose exact observed model/effort. Conclusions
therefore concern this recorded native route, not a named pinned reasoning model.

| Policy on paired 11 | Correct | Wrong decisive | Unresolved | Extra reasoning invocations |
| --- | ---: | ---: | ---: | ---: |
| P0 plus selected probe | 11 | 0 | 0 | 0 |
| P4 selected | 9 | 1 | 1 | 0 |
| Never / original random | 7 | 1 | 3 | 0 |
| Always full P3 plus reasoning | 10 | 0 | 1 | 11 |
| Simple risk router | 9 | 0 | 2 | 10 |
| Blind reasoning-only | 7 | 0 | 4 | 11 |

Always versus never corrected one false clear and resolved three uncertainties,
but changed one correct acceptance to uncertainty. Its gross benefit is
$2.50/$104; break-even is $0.227273/$9.454545 per added native call before other
incremental costs. At $1/native call the low-stakes gain becomes −$8.50 and the
consequential gain $93, both before other costs. Native charges are unknown, not
zero. Always versus blind reasoning-only gains three correct resolutions with
gross value $0.75/$6, but also consumes $0.007946988 of Jev list-price usage and
4.686494 seconds of probes; the two native prompts are different conditions, so
their unreported token costs cannot be assumed identical.

P4 selected eleven probes and zero reasoning calls across all cases, including
five probes on the paired eleven. The original random policy matched **zero
reasoning calls**, so it is identical to never. `comparison-design.json` declares
the call-count proxy and cost mismatch. E03's named arms are present, but this
degenerate proxy cannot demonstrate the allocation advantage requested by E08:
it matches neither P4's five paired probe calls nor their investigation cost.
The P4-versus-random gross gain of $0.50/$4 on the paired population is therefore
not evidence that Jev selects investigations better at equal spending.

The supplemental comparison below executes the missing probe-call comparison.
It supplies exploratory measurements, while preserving the main run's design gap
and the distinction between equal call counts and equal monetary cost.

## Exploratory comparison with five probe calls per policy

`supplement-20261024-02` used source
`4577eb2c09a211218cfb822f195127d6a8a3f918`, tree
`58930924d0d7fe0db7def67625cd09354d0b9c00`, and frozen plan SHA-256
`197bd8547da565db8284bccee821864cb3307e50f9a28350f92098302a53e6ca`.
It retained the main P4 observations and executed five fresh random-selected and
five fresh simple-rule probes on the same eleven paired cases. It made no new
model calls. The ten new probes consumed 0.951938 seconds in total; actual compute
charges remain unknown. Replay from retained raw observations passed.

| Additional policy after the common P3 evidence | Correct / wrong / unresolved | Added probe calls | Measured added seconds | Gross scenario gain over P3, low / consequential |
| --- | --- | ---: | ---: | ---: |
| Main P4 | 9 / 1 / 1 | 5 | 2.709180 | $0.50 / $4 |
| Seeded random probes | 9 / 0 / 2 | 5 | 0.776317 | $2.25 / $102 |
| Simple consequence rule | 9 / 0 / 2 | 5 | 0.175621 | $2.25 / $102 |

Random and simple each corrected the false clear missed by P4 and resolved one
uncertainty; P4 resolved two uncertainties but retained that false clear. No arm
introduced a decisive wrong action in this comparison. Under the declared
$0.01/second compute conversion, the respective net scenario gains before other
unpriced costs are $0.472908/$3.972908, $2.242237/$101.992237, and
$2.248244/$101.998244. All policies share the already incurred P3 prefix:
$0.007946988 of Jev list-price usage and 4.686494 probe seconds, which cancel in
incremental comparisons. Random used 1.932863 fewer added seconds than P4 and
simple used 2.533559 fewer. Five calls each therefore does not mean equal dollars,
latency, or review burden.

This is not a clean estimate of Jev's routing advantage. The design was selected
after main outcomes were visible, although selections were frozen before these
new executions. P4 uses Jev's candidate, whereas the random/simple arms use a fixed
visible-invariant dispatch rule: both case selection and candidate selection vary.
One seed across eleven correlated cases cannot establish a general random-policy
advantage. E03/E08 now have an executed nondegenerate probe-call comparison with
explicit mismatch; a prospective equal-cost causal allocation claim remains
unverified. The observed comparison provides no support for a Jev advantage.

A further read-only, post-hoc diagnostic applied the existing structured
interpreter directly to all 24 retained post-P3 evidence packets and obtained
24/24 correct actions, including 11/11 paired actions, with **zero new probes or
model calls**. That diagnostic was
not a prospectively frozen policy arm. It nevertheless shows why the added-probe
gains above cannot uniquely be credited to new information: switching interpretation
already sufficed on the retained population. The earlier P0-plus-probe arm still
demonstrates eight actual acquisitions of initially missing facts. A future
allocation test should freeze a structured reinterpretation arm and hold the
candidate-dispatch rule constant before acquiring more observations.

## Collection, learning, and limits

The main run made 103 successful direct Jev calls to `jev-1.14.0`: 540,670 input
and 89,137 output tokens. At the retained public rate of $0.042 per million input
tokens, with free output, its list-price estimate is **$0.022708140**. All 22 native
charges are unknown. Actual invoices, native token usage/latency, development
labor, source retrieval, human review, and future implementation cost remain
unknown. Preflight calls outside this run are accounted separately by the session
ledger; the main-run number is not a claim about total session spend.

Thirteen independently sampled audits consumed 4.394689 measured probe seconds.
Their weighted estimate found zero observed decisive misses, but the full
controlled population contains two false clears outside the selected audits; the
paired subset contains one of them. A zero
estimate from this small sample is not a production guarantee. The reusable
evidence guard correctly handled four missing-observation holdout cases in four
groups with the same frozen artifact. This demonstrates scoped artifact reuse;
it does not estimate a prospective learning intervention or learning ROI.

The controlled end-to-end check exercised actual Application/SQLite acceptance
for three related context variants. Every evaluated policy preserved the correct
acceptance outcome there; no incremental consequential improvement was observed.
Other economic comparisons are static action replays supported by executed
boundary probes, not measured downstream production losses.

Recommendation: retain the structured comparator and applicable probes for this
bounded workload. Do not add the measured Jev stack or its adversarial stage as a
production gate. Reasoning has conditional corrective value on this paired set,
but its cost, exact model identity, and selective-allocation value remain
unresolved. The exploratory supplement strengthens the case against adopting this
measured stack; a prospectively frozen comparison with constant candidate rules,
structured reinterpretation, and a larger independent sample is needed for an
allocation claim.
