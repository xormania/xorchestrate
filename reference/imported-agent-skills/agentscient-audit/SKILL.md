---
name: agentscient-audit
description: Audit an Agentscient submitted revision independently; use for review findings, evidence and review decisions.
---

Read the objective, constraints and current review packet with `work_inspect`.
Review the immutable submitted source snapshot. Trace changed behavior and relevant
callers; use available diagnostics/checks where they answer a concrete concern.
Do not edit the producer's source or broaden the requested work.

Report findings with a reproducible scenario, location, impact and supporting
evidence. Separate demonstrated defects from questions or optional preferences.
Record the assessment with `review_record` for the inspected revision. If it changes,
refresh the packet and assess the new revision. An approval requires actual review;
successful CI alone is insufficient. Route blockers/questions through the supplied
reporting relationship. XO handles job acceptance and integration.

Test the completion claim against each required outcome, including a plausible
counterexample and relevant successful neighbor behavior. Evidence from a different
revision or scope cannot establish the submitted result. A producer's report is a
claim to inspect, not proof by itself. Identify the smallest verification or source
change that resolves a demonstrated gap; avoid expanding review into optional work.
