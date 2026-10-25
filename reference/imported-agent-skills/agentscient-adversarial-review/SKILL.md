---
name: agentscient-adversarial-review
description: Challenge an Agentscient submitted revision with concrete counterexamples; use for independent review of assumptions, boundary behavior and completion claims.
---

Read the objective, constraints and current review packet with
`work_inspect(section="review")`. Review the immutable submitted source snapshot.
Identify assumptions in the implementation and its claimed results, then look for
counterexamples within the assigned scope: boundary inputs, failure paths,
state transitions and interactions with existing behavior. Use available
diagnostics and configured checks when they can establish or refute a concern.

Report demonstrated defects with a reproducible scenario, affected location,
impact and supporting evidence. Distinguish findings from unverified hypotheses
and optional improvements. Check relevant successful behavior as well as attempted
failures; a hypothetical concern alone does not establish a defect. Do not edit
the producer's source or expand the requested work.

Record the assessment with `review_record` against the inspected revision. Refresh
the packet and reassess if that revision changes. A producer's report or passing
CI is evidence to examine, not a substitute for independent review. Route missing
information and blockers through the supplied reporting relationship; XO owns job
acceptance and integration.
