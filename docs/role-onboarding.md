# Shared, role-specific onboarding

Configured harness adapters receive the same startup essentials through MCP. The current
role determines responsibilities, tools, work/source boundaries, reporting and
skills. Harness/model/reasoning choices do not change that content or authority.

At startup, after handover or after a role change, call `onboard`. For other work
changes, refresh `work_context` without reloading unchanged role guidance. Onboarding
returns the shared essentials, customized role instructions, current brief, available tool
names and a compact skill catalog. Entries marked `loaded` are already included;
use `skill_read(name)` only for a relevant entry whose body you still need. Full
source provenance and hashes remain in skill reads, and exact delivered revisions
are recorded in telemetry. A delivery record means the content was supplied, not proof a model
understood it. Onboarding does not create work or invent an acceptance gate.

For a managed startup, onboarding also supplies a readiness challenge and its
revision. The agent returns these through `readiness_report`, including actual
blockers; the service validates and retains the duty receipt and routes it to
the commissioning parent. This explicit response is distinct from supplying
instructions or observing a live process. Initial XO standby has readiness
authority only. See the [readiness contract](control-plane-roles-and-tools.md#reporting-for-duty).

The shipped catalog supplies the defaults below. Role identities, behavior
adapters and skill references are configured in `devtools/role-packages.cue`;
the table is not a required role list.

| Default role | Customized focus | Role skill |
| --- | --- | --- |
| CO | Relay xor's instructions and context to XO, and system messages back to xor; interface only | `agentscient-co` |
| XO | Planning, named jobs, role allocation, superior steering, review, integration and acceptance | `agentscient-xo` |
| NCO | Specialist coordination and verification within one job; readiness reported to XO | `agentscient-nco` |
| Planner | Work-order discovery and an evidence-backed job breakdown for XO | `agentscient-planner` |
| Coder | Scoped source changes, installed framework capabilities, validation | `agentscient-coder` |
| Audit | Immutable revision review, reproducible findings, independent assessment | `agentscient-audit` |
| Security Auditor | Immutable revision security review, attack prerequisites, impact and reproducible evidence | `agentscient-security-auditor` |
| Adversarial Review | Immutable revision review of assumptions, counterexamples, boundary behavior and completion claims | `agentscient-adversarial-review` |
| Docs | Accurate documentation, examples, reader needs and limits | `agentscient-docs` |
| Research | Source provenance, facts versus inference, supported recommendations | `agentscient-research` |
| Arbitrator | Evidence-backed resolution of an assigned pending decision, without source edits or execution coordination | `agentscient-arbitrator` |
| Test Writer | Behavior tests, regressions, useful boundaries and measured evidence | `agentscient-test-writer` |
| Architect | Existing capabilities, interfaces, design tradeoffs and migration | `agentscient-architect` |

Coordinator onboarding, MCP role arguments and assignment selection use the same
configured role projection. Arbitrator is assigned by the service; NCO receives
specialist options without Planner or NCO. Work scope, permissions and available
runtime capacity still determine whether a proposed assignment can proceed.

Each role receives its package's configured `skills.shared` content; the shipped
packages select `agentscient-context`. Canonical skill content lives in
`.agents/skills`; role selection and delivery live in `devtools/onboarding.py`.
There are no per-harness copies to drift. Unbound, unappointed members have no role
catalog. A role change changes subsequent onboarding and skill access immediately.
An in-flight response keeps its captured role binding for its context, tools and
catalog, so a concurrent rebind takes effect on the next request.

When installed development tools are enabled for the bound job, the catalog also
includes the package's relevant configured Mate skills. Shipped Coder, Audit, Security Auditor,
Adversarial Review, Research, Planner and Test Writer packages declare all six; Docs and Architect
declare system information and service inspection. XO/NCO declare all six for
eligible job contexts, while available framework tools still depend on the binding
and provider prerequisites. CO declares interface guidance without framework skills.
Arbitrator declares decision guidance and read-only source inspection without
framework execution skills or direct Jev assessment.
Repository Mate skill guidance uses `framework_inspect` directly, with focused
parameters and diagnostic interpretation. It does not append a second CLI adapter.
When refreshing upstream Mate exports, retain the managed MCP examples before committing. The profiler resource path is available as `kind="mate_resource"`
with the URI returned by Mate. No native shell capability is added.
Installed Composer versions use `kind="packages"` with a package name or family filter.
Skill reads retain `sha256` for the source file and `guidance_sha256` for the
complete delivered payload. The onboarding revision and skill-delivery events
track guidance changes without repeating per-skill hashes in the startup catalog.

```json
{"name":"onboard","arguments":{}}
{"name":"skill_read","arguments":{"name":"mate-symfony-service-inspection"}}
{"name":"framework_inspect","arguments":{"kind":"mate","name":"symfony-services","parameters":{"query":"Mailer"}}}
{"name":"framework_inspect","arguments":{"kind":"mate_resource","resource":"symfony-profiler://profile/abc123"}}
```

Skills guide use of the tools already supplied by the current role; they cannot
grant another role, expand writable paths, select another work order or bypass
review requirements. Missing source/search/setup capabilities are reported through
the existing guidance path. Operator initialization and local authenticated
acceptance are described in [local readiness](local-agent-readiness.md).
