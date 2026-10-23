# Working on Agentscient

The owner's current instructions define the task and take precedence over
repository documents. Preserve unrelated work and report what was actually verified.

- `src/`, `config/`, `packages/`, and `tests/` belong to the Symfony application.
- `devtools/` provides MCP tools for live native harness communication, telemetry,
  work orders, their child jobs, and artifacts.
  It must remain usable without PHP or the Symfony application's dependencies.

Use Symfony's existing features, PHP attributes, autowiring, and autoconfiguration
where they fit. Inspect installed packages and existing services before adding
custom infrastructure. Basic Makers and Mate are already application development
dependencies; their presence does not require a new wrapper or custom Maker.

Managed local agents use role-bound MCP tools without a general shell tool in
persistent conversations. This runtime restriction does not limit the tools
authorized for maintainers building or repairing the dev-lane itself.
The appointed interactive CO is xor's own native session. The operator retains
the harness's model/reasoning selection, commands, tools, settings, delegation and
exit controls. Managed-worker restrictions do not apply to that console, including
when its configured role has another name with CO behavior. Native controls do not
grant additional Agentscient MCP authority or change CO's interface responsibility.
The control plane supplies work context and routes reports and guidance. Normal
agents cannot select arbitrary work, recipients, or backing-provider operations.
Trusted operator setup is separate; never give managed agents the `--operator`
connection. CO is xor's interface: it relays xor's instructions, specification,
scope and context to XO through `direct` and reports, and relays system messages
back to xor. CO does not assess, coordinate, execute or accept work. XO
commissions the work-order planner and adopts its job breakdown through
`plan_adopt`. A one-job plan is simple: XO coordinates that job's role assignments
directly. For multiple jobs, XO appoints one NCO per job and owns dependencies
between jobs. NCO coordinates specialists within its job and requests planning or
scope changes through XO; only XO commissions planners and accepts jobs. A planner
investigates and reports a plan without editing source or coordinating execution.
Each managed collaborator runs in a standalone native conversation. Agentscient
owns its session creation, assignments and coordination; managed collaborators do
not use harness-native subagents. Role and work authority come from the service
binding and are enforced by MCP tools, independently of model, harness, reasoning
settings or skills.
With semantic decisions enabled, the service applies Jev's selections among
configured eligible roles and runtimes. Unresolved questions go to a service-bound
Arbitrator; its evidenced resolution resumes the waiting operation through the
ordinary checks. Adversarial Review is an independent audit role, not a substitute
for those checks.
Local agents invoke maintained Makers through MCP; they never author or modify
Makers. Extra improvements can be reported without becoming assignments.
Command services bind work and configured collaborators, supply current briefs,
and retain live direction. Managed workspace/Git outcomes and installed Maker/Mate
capabilities are provided through MCP. Use installed Maker metadata, generate through
`framework_generate`, inspect application facts with `framework_inspect`, and run
configured validation through `work_check`. Serena v2 REPL is a separate provider
for product source work; it receives the bound workspace automatically. Auditors
review an immutable submitted revision; XO accepts a named job through `steer`
after inspecting its current review packet with `monitor`. Normal GitHub rules apply.
Work orders integrate bounded jobs through their PRs. XO submits the combined
result with `work_submit`, inspects it with `monitor(section="review")` and
delivers the checked outcome with `conclude`; CO relays that outcome to xor.
XO retains superior steering authority over its NCOs and their specialists.
Agents use task-facing MCP tools;
the services handle session allocation, workspace lifetime, branch naming/PR routing, storage and
record association internally.

For maintainer changes to the dev-lane itself, use an owned branch based on `dev`,
test the affected behavior, and use a PR into `dev`. Managed agents use MCP;
the service chooses their branches and PR routing. The owner controls releases to `master`. See
[`CONTRIB.md`](CONTRIB.md) for the short contribution conventions and
[role/tool reference](docs/control-plane-roles-and-tools.md) for normal agent
operations. [Local setup](docs/local-agent-readiness.md) and the
[operator reference](devtools/OPERATIONS.md) cover installation and repair.

The generated Mate CLI instructions below apply to maintainers and backing services.
Managed agents access those capabilities through their role-bound MCP tools.

<!-- BEGIN AI_MATE_INSTRUCTIONS -->
AI Mate: project diagnostic tools, exposed through the `vendor/bin/mate` CLI.

- Provenance: this block is generated by `vendor/bin/mate discover`. The tooling is a regular dev dependency of this project, verifiable in `composer.json` (`symfony/ai-mate`); the binary lives at `vendor/bin/mate`.
- Invocation: always run Mate as `vendor/bin/mate`; another interpreter reports on a runtime that is not this application's, and Mate refuses to start under one.
- The tools report measured facts about the running application; prefer them over inferring the same information from reading code. They are described in `mate/AGENT_INSTRUCTIONS.md`.
- Discover the tool surface with `vendor/bin/mate tools:list`, inspect parameters with `tools:inspect <tool>`, and run a tool with `tools:call <tool> --<param>=<value>` (add `--format=json` for machine-readable output).
- Installed extensions: symfony/ai-mate, symfony/ai-symfony-mate-extension.
<!-- END AI_MATE_INSTRUCTIONS -->
