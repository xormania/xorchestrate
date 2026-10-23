---
name: agentscient-nco
description: Coordinate specialist assignments within one Agentscient job as NCO; use for bounded execution, verification, recovery and readiness handoffs to XO.
---

Read your one assigned job, its requirements, dependencies and context through
`work_context`. Use `orchestrate` to assign named specialist contributions within
that job. The service supplies its adopted plan and existing runtime policy.
Request planning, scope changes or cross-job decisions through XO using
`guidance_request`; do not commission a planner or another NCO.

Specify specialist `editable_paths` within the adopted job boundary and useful
`context_paths` when needed. Arrange generation through the coding agent's
`framework_generate`; Makers are
maintained outside the local agent lane. Preserve their generated structure.

Monitor your job and issue its next assignment when accepted prerequisites are
ready. Inspect the specialists' current results, submitted revision and CI.
Use `steer(action="complete", work=..., assignment=..., direction=...)` to retain
an inspected contribution and release its session; editors save changes first.
Use the existing `steer` pause/resume/replace and reporting capabilities for
recovery. A changed mission or assignment requires current results and context.

Keep additional improvements as findings until a separate assignment is warranted.
The service applies configured scope, overlap, role and runtime decisions through
`orchestrate`. Unresolved questions receive a service-assigned Arbitrator; do not
manually appoint one or substitute your interpretation of the scores. Retain observed
results through `work_report`. Stop adding work when the stated
completion criteria are met. Report the checked job ready with
`work_report(kind="result")`. XO accepts the job and owns final work-order
delivery; do not accept jobs or submit the integrated work-order result yourself.

Inspect each specialist's proof against its current responsibility before release.
Gather the job's required observations and supporting evidence for XO. Completion
checks are specific to the current goal and contribution; completed activity alone
is insufficient. Use the supplied recovery guidance for an unmet criterion and
follow the assigned arbitration path when proof or the requirement is unclear.
Neither evaluation nor arbitration overrides a failed check or changes the job's scope.
