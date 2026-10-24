# Prospective execution plan for a post-hoc probe-routing supplement

The main run is immutable. Its eleven paired cases contain five P4 probe routes,
six no-investigation routes, and no reasoning route. Thus its original zero-call
random-reasoning comparison does not measure random investigation. This supplement
addresses that gap, with explicit post-hoc exposure. It cannot repair the original
preregistration or establish a new independent holdout result.

Before any new execution, `prepare` binds the exact eleven IDs and available P3
packets, original decisions and probe traces, corpus, model attempts, source hashes,
script bytes, and this document into a hashed plan. The script and document are
committed before `execute`. Neither command changes the main run or measured source.

Each additional policy receives exactly five investigation slots, matching main
P4 by call count. Random selection samples five of the sorted eleven IDs using the
main seed 4017. The simple policy ranks visible missing observations first, then
the number of missing checks, then the frozen consequence-unit assumption, then
case ID to break ties. This ordering uses no outcomes or post-supplement evidence.
No choices will be retuned after execution. All eleven current P3 packets have
zero missing checks, so the simple selector here reduces to consequence units
and case-ID ties; this fact is disclosed before execution.

Both selectors receive the same applicable P3 evidence packets. A fixed non-Jev
candidate selector uses only visible invariant names, requirement notes, operand
types, and declared candidate availability. Hidden `scenario`, `probe_id`, answer
keys, and split labels are not inputs to this selector. The executable adapter
receives the controlled subject only after selection has been frozen. Unsupported
selection remains unresolved; no candidate substitution or utility credit occurs.

Each selected probe executes in fresh state. Successful decisive observations are
merged into the applicable P3 packet and interpreted by the same frozen structured
baseline used for main P4's successful probe path. Nonselected cases retain their
exact P3 decisions. The common P3 prefix, including its earlier Jev requests and
probe observations, is reused without new model calls. No subscription or prior
model computation is priced as free; only incremental model calls are zero.

Replay rederives the visible evidence from raw probe records, the structured
interpretation, unchanged nonselected P3 decisions, and the economic summary. For relocated artifacts use `replay --main PATH`; the original
plan remains immutable and all main evidence hashes and exact seeded selections
are revalidated.

The five-call match is a proxy, not an equal-dollar or equal-time claim. Report
actual probe durations and the mismatch relative to main P4. Actual compute billing
is unknown. Retain all raw probe observations, hashes, request/decision links,
source/run identities, action changes, corrections, damage, unresolved outcomes,
and losses under the two already-frozen economic scenarios. Decisive corrections
and damage are separated from resolution of prior uncertainty; unresolved counts
remain explicit. Unknown needed losses make total/gross/net/break-even unknown. Investigation and
deferral remain actions with the scenarios' handling/delay losses; unknown losses
are not zero. Apply the existing probe-second price sensitivity and show break-even
values, keeping human review and other unpriced work explicit.

Main P4 uses Jev-selected candidates; this supplement uses a fixed non-Jev candidate
selector. Candidate quality and routing jointly vary, so this comparison does not
isolate routing alone. The sample is one random seed and eleven clustered cases;
results are descriptive. Probes execute real controlled boundaries, while resulting
actions are replayed statically. No new production consequence or monetized reusable
learning is claimed.

Execution limits are ten new probe calls in total, zero retries, concurrency one,
and 180 seconds. Native adapters require local socket access. Incomplete execution
must remain incomplete and preserve partial evidence.

```sh
python experiments/jev_failure_correlation_v2/evidence/supplements/random_probe.py prepare \
  --main /workspace/jev-v2-runs/cloud-20261024-01 \
  --out /workspace/jev-v2-runs/supplement-20261024-02
# Commit the script and PLAN.md and review the prepared plan before execution.
python experiments/jev_failure_correlation_v2/evidence/supplements/random_probe.py execute \
  --out /workspace/jev-v2-runs/supplement-20261024-02
python experiments/jev_failure_correlation_v2/evidence/supplements/random_probe.py replay \
  --out /workspace/jev-v2-runs/supplement-20261024-02
```
