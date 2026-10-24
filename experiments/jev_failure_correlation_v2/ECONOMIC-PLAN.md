# Frozen economic and scoring plan

This plan is declared before live v2 evaluation. The values are analyst scenarios,
not xor's business valuations. The purpose is to expose break-even conditions,
including cases where an extra stage has negative value.

## Quantities and units

Every policy is scored twice: against the stage-specific evidence key and against
the independently established eventual action. An initially unresolved judgment
can be correct. A missing provider response remains unverified. The outcome key is
available only to scoring; acquiring a relevant executed observation changes the
epistemic layer. Several supported violations must match as a set.

The outcome actions are `accept`, `reject`, `investigate`, and `defer`. Resource
costs and outcome loss are distinct. If an observed action matches the outcome
action, its scenario outcome loss is zero. An unnecessary investigation/defer has
the stated handling/delay loss; no hidden model or probe fees are included there.
Harm from a changed action enters outcome loss once. A corrected mechanism with
the same action earns no operational benefit.

| Assumed loss in USD | Low stakes | Consequential |
| --- | ---: | ---: |
| False acceptance | 2 | 100 |
| Duplicate effect | 5 | 500 |
| Wasted investigation consequence | 1 | 10 |
| Low impact distinction | 0.10 | 0.10 |
| Unnecessary rejection | 0.50 | 20 |
| Unnecessary investigate action handling/delay | 0.25 | 2 |
| Unnecessary defer action delay | 0.10 | 5 |

The visible packet's consequence class chooses the false-acceptance loss. It
does not originate from an F-number. These are scenario outcomes; this experiment
does not observe dollars lost by a production user. The action alternatives and
consequence descriptions are present before routing. The router must not use the
outcome key or the post-investigation observation to select its initial route.

For each policy and incremental pair, compute loss before minus loss after minus
incremental resource cost, on common observed case actions. Report excluded cases
and unknowns. P0-relative values and P1→P2→P3→P4 increments are separate.
Never/always, random/selective, risk/selective, and reasoning-only/full-stack
comparisons use the same paired members when causally valid. Static replay is not
evidence of end-to-end stateful benefit. Actual product consequences require a
separate controlled execution.

## Charges, timing, and price sensitivity

All failed calls and retries count when they incurred cost. A cached reference is
not another incurred provider call. A policy deployment estimate includes all
request dependencies needed by that policy, while collection cost counts each
actual attempt once across the experiment. Missing linked requests and probes
remain explicit unknowns.

TypeSafe's models page (`https://docs.typesafe.ai/models`, retrieved 2026-10-24)
lists USD 0.042 per million input tokens and free output tokens. Multiply returned
input-token usage by this list rate to obtain a **list-price estimate**, separate
from invoiced cost. Never infer billed usage from a character count. Preserve the
retrieved price source with the run evidence. Actual provider charges remain
unknown unless the provider reports them. Native Codex session cost and usage are
unknown where the host does not expose them; they are never called free.

Frozen sensitivity values for an unknown model call are USD 0.001, 0.01, 0.10,
and 1.00. Probe execution conversion is USD 0, 0.001, or 0.01 per measured second.
A zero-price scenario is not a claim of zero cost. Human-review scenarios use
0, 1, or 3 USD per minute at 0, 1, 5, 15, or 60 total minutes. Report the maximum
total investigation cost and review time consistent with break-even. Negative
break-even means that no nonnegative price rescues the scenario's actions.
Unmeasured probe duration, human effort, queue latency, candidate construction,
source retrieval, implementation effort, and development costs remain unknown.
Sensitivity values do not establish an actual net monetary value.

The primary grounded sensitivity uses known actual charges where reported,
otherwise Jev's returned input usage times the public list price, then varies only
the remaining unpriced calls (including native reasoning) and probe-time
conversion. The all-unknown-invoice grid is retained as an additional sensitivity,
not substituted for the available Jev usage estimate. Record native invocation
identities separately from assessment identities. A future shared native
invocation is one monetary unit only when its ID and invocation billing scope are
explicit; the present plan uses one separate invocation per evaluated packet.

## Audits, uncertainty, and learning

Random audits are separate from targeted audits, adaptive attacks, and random
escalation. The initial route is frozen before selection. Only independently
adjudicated selected observations enter the Horvitz–Thompson total; divide it by
the relevant sampled population size, never by a population from a different
stream. Missing adjudication leaves the estimated miss rate unknown. Sampling
probability must be positive in every routed stratum. Small/empty strata are
reported without invented precision.

Report native confidence separately from complete option distributions. Frozen
confidence thresholds are 0, 0.5, 0.8, 0.95, and 1.0; no threshold is fitted to the
holdout. Reliability bins are [0,.5), [.5,.8), [.8,.95), [.95,1]. Error ranking is
the probability that an erroneous answer receives lower confidence than a
correct answer, with half credit for ties. Undefined ranks and empty bins remain
null. Case Wilson intervals are descriptive because related variants are grouped;
the number of independent incident groups is always exposed. This pilot does not
establish a production rare-error bound or cross-family generalization.

Learning expenditure and operational expenditure must be identifiable separately
in raw execution records. A discovery is a candidate learning item, not proven
transfer. Reusable learning requires a frozen revision to a probe/router/tool and
a measured change on distinct subsequent incident groups. Monetizing it further
requires a supported reuse horizon, frequency, benefit model, and cost basis.
Until those records exist, reusable-learning value is explicitly unverified and
its monetary value is unknown.

A narrower reusable-artifact check is predeclared before v2 live outputs: apply
the unchanged `policies.check_fact` / `baseline` applicability and sufficiency
guard to packets with missing or foreign-boundary observations in each split.
Compare its sufficiency and judgment with independently checked initial keys.
Report the artifact's SHA-256 and both case and incident-group counts for
development, calibration, and fresh holdout. This tests reuse of an existing
lesson across boundaries. It does not estimate learning-intervention uplift,
training gains, prospective recurrence, or economic return from learning.

## Replay

The pure functions `evaluation.evaluate(cases, decisions, attempts, audits,
probes)` and `economics.economic_report(...)` make no provider calls. The CLI
`python -m experiments.jev_failure_correlation_v2.evaluation RUN --output REPORT`
requires frozen `cases.json`, and reads `decisions`, `attempts`, `audits`, and
`probes` as JSONL or JSON lists. Missing required arms remain incomplete. The
measurement harness owns source/model/request identity validation before replay;
the evaluator rejects duplicate final policy/case decisions and duplicate attempt
identities instead of silently choosing one.
