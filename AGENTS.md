# Working on Xorchestrate

Xorchestrate is a reusable system for coordinating agents across projects, tools,
execution environments, and organizational structures.

xor is the project owner and final authority on project direction. Current explicit
instructions take precedence over earlier plans, proposals, repository guidance,
and agent decisions. Keep assumptions and recommendations explicit and revisable.

## Practical delivery and scope

Issues and discoveries are not requirements. They may not even be practical to
address. Review findings, scanner alerts, test failures, historical behavior, and
agent recommendations are evidence to evaluate; their existence does not create
a product requirement, mandatory task, or merge prerequisite.

Good enough now is better than perfect tomorrow; perfection may not be achievable.
Evaluate practical consequences, likelihood, benefit, implementation and maintenance
cost, and the cost of delay. Use judgment within the authorized scope to fix, defer,
or decline work. Do not automatically turn every finding into a backlog obligation
or send every routine judgment back to xor.

A demonstrated failure of an agreed requirement is evidence of a defect against
that requirement. Explain its actual consequence and relevant options honestly;
xor decides its priority and whether it blocks integration. Do not invent a new
requirement to justify a finding, conceal material failures, or move an agreed
finish line without xor's direction.

Preserve the stated goal. Verify missing facts instead of converting uncertainty
into restrictions or silently narrowing the intended capabilities.

## Bounded verification and reviews

Use proportionate verification with a defined purpose, scope, and stopping point.
Complete required checks and the agreed acceptance work, then deliver the result.
Passing counts alone do not establish behavioral coverage or architectural quality.

Do not initiate repeated general audits or make another review an automatic
completion requirement. Reuse applicable evidence for unchanged work, clearly
bound to the revision and behavior it actually covers. Confirm repairs through
targeted checks of the changed property and affected behavior. Repeat or broaden
checks when changes, specific failures, or unresolved material questions justify
it; explain that reason rather than restarting the review cycle by default.

Separate demonstrated defects and evidence gaps from optional improvements and
preferences. A finding does not override xor's integration decision. Record
accepted limitations and deferred work accurately, then proceed with the authorized
delivery. Ask for a decision only when it is genuinely needed.

## Reuse existing work

Before proposing a new mechanism, replacement, or rewrite, inspect relevant
existing implementations, tests, acceptance fixtures, and records. xor has already
invested substantial effort in solving these problems. Search for and reuse that
work; do not assume an unfamiliar capability must be invented.

Start with the relevant sources, not an exhaustive survey for every task:

- This repository's active code, `devtools/`, `legacy/agentscient/`, extraction
  provenance, and referenced multi-harness PoC material.
- [uscient/agentscient](https://github.com/uscient/agentscient), including relevant
  `dev` and `shit` branch implementations and acceptance machinery.
- [xormania/xordination](https://github.com/xormania/xordination) for harness and
  MCP coordination.
- [uscient/agentscient-test-repo](https://github.com/uscient/agentscient-test-repo)
  for existing development tasks, fixtures, and independent product checks.
- [xormania/xorder](https://github.com/xormania/xorder) for execution environments,
  images, probes, and CI tooling.
- Other xor repositories identified by the task or by references in these sources.

Adapt useful code and tests into the active architecture. Preserve intended
capabilities while replacing incidental infrastructure coupling where necessary.
Explain concrete incompatibilities before replacing an existing solution; age,
different terminology, or placement under `legacy/` is not sufficient reason.

Copying code into an archive and passing its historical tests does not demonstrate
active integration. Show where the current application uses the reused mechanism
and how applicable tests exercise its guarantees there. Favor reusable behavioral
contracts and complete workflow scenarios; individual defect regressions support
that foundation.

Historical implementations provide mechanisms and evidence, not automatic policy.
Their role structures, wording, environment assumptions, and compatibility rules
become requirements only through current owner direction. Reuse does not require
maintaining an entire historical system. Tests should verify intended behavior and
evolve when that intent changes.

## Architecture and execution

Aim for an elegant, coherent architecture with clear responsibilities, sound
abstractions, separation of concerns, and appropriate design patterns. Nothing
should be hardwired: infrastructure, behavior, and policy choices should be
configurable, replaceable, and extensible. Keep basic use simple as capabilities grow.

Match the requested scope: discussion and planning do not authorize implementation.
Report what was actually checked, including material limitations. Preserve
unrelated work.

Prepare repository changes on a branch for review. xor controls merging and
releases. Use xormania attribution for commits; do not add AI co-author trailers.
