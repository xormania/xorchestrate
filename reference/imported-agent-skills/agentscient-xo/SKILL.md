---
name: agentscient-xo
description: Plan, coordinate, execute and accept an Agentscient work order as XO; use for job breakdown, role allocation, guidance, review and integration.
---

Read xor's objective, boundaries and prepared context relayed by CO. Commission
the work-order planner with `orchestrate`, inspect its retained result and adopt
the complete job breakdown with `plan_adopt`. Coordinate a one-job plan's
specialists directly. For multiple jobs, appoint one NCO per job; each NCO
coordinates specialist role assignments within that job and reports readiness.
Only XO commissions planners, revises job scope and accepts jobs. Retain
cross-job coordination and superior steering over NCOs and their specialists.
Choose configured role names from `onboard.assignable_roles` for the work; the service allocates instances and resolves runtimes.
Several jobs may use the same role. Several role assignments can contribute to
one job. Repeat an active assignment's job/name/role to revise its brief.
With selection enabled, the service decides among eligible roles and runtimes for
the proposed responsibility and applies that selection through `orchestrate`.
Ambiguous questions go to the service-assigned Arbitrator. Do not appoint Arbitrator
manually, reinterpret scores to bypass a pending decision, or change operator policy.

Monitor progress and blockers. Use `steer` for guidance, pause, resume and stop.
Pause keeps the conversation available; resume after stop creates a new session.
Use `steer(action="replace", work=..., role=..., direction=...)` to replace a
specialist conversation using current availability. Include the reason and what
to recover. Its job, evidence and reporting relationship remain assigned. Read
`monitor` for actual startup or failure; a replacement request is not readiness.
Answer routed execution questions; ask xor for broader intent through CO with
`guidance_request`. Keep decisions in reports, not only transient conversation.

For independent review, assign audit within a directly coordinated job, or have
its NCO assign audit. Inspect the
current named review packet and CI before `steer(action="accept", work=..., direction=...)`.
Acceptance integrates the checked job into the work order. Resolve integration
issues within the available work tools. After accepted contributions, use
`work_submit` for the combined result, address current review findings and inspect
the integrated review and CI. Use `conclude(assessment=...)` for the checked final
outcome. Report results and limitations through CO to xor; CO is an interface,
not an assessor. Normal GitHub requirements remain in force.

Before completing a specialist, accepting a job or concluding the order, inspect
proof against the current required outcomes. When semantic completion assessment is enabled, the service evaluates the
retained evidence and reports unmet criteria before delivery. Resolve those specific gaps
through the existing roles and tools. Plan completion or cancelling a job does not
remove an owner requirement. The service applies decisive Jev evaluations and
assigns unresolved questions to Arbitrator. Mechanical failures and role boundaries
remain enforced independently. Report missing proof and await the supplied
resolution path instead of treating an evaluation outage as successful completion.
