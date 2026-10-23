# Control-plane roles and tools

> Historical reference: this document describes the imported Agentscient runtime in
> `devtools/`. Its roles, defaults, restrictions, and setup procedures are not
> requirements of the current Xorchestrate application. Start with the
> [current architecture](hexagonal-architecture.md) and [README](../README.md).

This is the reference for the implemented normal MCP interface in this repository.
[Setup](local-agent-readiness.md) configures the local services;
[operator documentation](../devtools/OPERATIONS.md) covers their backing APIs.
Each line in a tool example is a separate MCP call.

## Responsibilities and context

**CO is xor's interface.** It relays xor's instructions, boundaries and prepared
scope/specification context to XO, and system messages back to xor. It does not
assess, coordinate, execute or accept work. **XO commissions a planner** to propose
jobs and adopts the plan.
A plan with **one job is simple**: XO coordinates its specialists directly. For
**multiple jobs**, XO appoints one **NCO per job** and coordinates dependencies
between jobs. NCO manages specialists and verification within its job, reports
readiness to XO, and asks XO for planning or scope changes. **XO accepts jobs and
the integrated work-order result**, retaining superior steering over its team.
Planner investigates and reports to XO without editing source or directing execution.
Specialists receive the context and capabilities needed for their responsibility.

| Concept | Meaning |
| --- | --- |
| Work order | The numbered overall effort and integrated result: objective, completion criteria, boundaries, decisions and evidence. |
| Job | A planned outcome with scope, requirements, completion criteria and dependencies; its source changes are delivered through one PR into the work order. |
| Role assignment | One specialist's bounded contribution to a job, with its own brief and retained result. Several assignments can contribute to the same job. The work-order planner has an assignment without a job. |
| Role | The tools and default editable paths available to a participant. |
| Work binding | The work to which that role's calls and context apply. |
| Reporting relationship | Where findings, questions, results and guidance go. |

The services supply repository, workspace, branch, target, PR and artifact context.
Agents name responsibilities and request outcomes. Returned evidence/checkpoint
references identify information; they do not retarget the caller's work.

CO relays questions requiring xor's intent and returns xor's answer. XO handles coordination and
execution decisions within the brief. Specialists investigate and act within their
responsibilities, asking for guidance when needed. A message does not grant new
role or work authority. Every managed collaborator runs in a standalone native
session. Agentscient owns its session creation and coordination; managed
collaborators do not use harness-native subagents. The appointed interactive CO
retains the operator's native commands, tools, settings, model/reasoning selection,
delegation and exit controls. Model, harness, reasoning and skills do not widen
the service-enforced role or work boundary.

## Role catalog

The table describes the shipped behavior packages. Configured role names and their
number come from the registry. Coordinators receive their available names and
responsibilities in `onboard.assignable_roles`; use those names in tool calls.

Every bound role gets **`onboard`, `skill_read`, `readiness_report`, `work_context`, `work_report`, `guidance_request`**.
When a call fails, read its original diagnostic and structured `failure` category
and reason. Enabled diagnostic guidance may include concrete `actions` with tool
arguments, retained evidence references and a `when` condition. Follow that
condition before attempting the action. Suggestions apply to the supplied current
context and configuration; they confer no authority. Refresh the brief after a
binding change and use your reporting relationship for decisions outside your role.
A queued or completed operation receipt does not by itself establish that its
checks passed; inspect the measured result. Provider unavailability and an invalid
provider response both mean no valid judgment was obtained.
Read the [role onboarding reference](role-onboarding.md) for customized responsibilities
and skill catalogs shared across all supported harnesses.
When the operator enables `features.advice` in the resolved configuration, eligible
bound roles get **`work_assess`**; CO and Arbitrator do not receive it.
This exposes optional investigative assessments. Assignment, runtime, relevance and
completion decisions run in the service at their configured boundaries without
requiring an agent to call `work_assess` first.
Filtering and completion assessment have independent feature switches and role/job
policies. Filtering can omit clearly irrelevant background from routine briefs.
Enabled completion assessment checks retained proof. Decisive results are applied
through normal service checks; ambiguous decisions are assigned to Arbitrator.
Pending arbitration keeps completion waiting. Provider failure handling is configured
under `decisions`, with arbitration as the default. Repair and reporting remain available.
See [service decisions and arbitration](../devtools/OPERATIONS.md#service-decisions-and-arbitration).
With a project configured, specialists also get **`work_inspect`**. Enabling
`project.development` adds configured **`framework_inspect` and `work_check`** tools;
Arbitrator has source inspection without framework execution capabilities.
The **editing tools** below are `work_save`, `work_refresh`, `work_recover`,
`work_submit` and `review_respond`.

| Role | Additional control-plane tools | Default Serena source access |
| --- | --- | --- |
| CO | `roll_call`, `direct`, overview-only `monitor`; relays xor's context and system messages | None |
| XO | `plan_adopt`, `orchestrate`, `monitor`, `steer`, `conclude`; with a project: `work_inspect`, `work_refresh`, `work_submit`, `review_respond`; with development enabled: `work_check` | None |
| NCO (`nco`) | `orchestrate`, `monitor`, `steer` within its one job; with a project: `work_inspect`; with development enabled: `work_check`. Completes specialist assignments and reports readiness to XO. Cannot commission planners/NCOs, accept jobs or submit the integrated result. | None |
| Planner (`planner`) | Shared and specialist inspection/reporting tools at work-order scope; retained plan required for a result to XO | Read-only source |
| Coder (`coder`) | Editing tools; `framework_generate` | Product paths |
| Audit (`audit`) | `review_record` | Read-only review snapshot |
| Security Auditor (`security_auditor`) | `review_record` with a security review focus | Read-only review snapshot |
| Adversarial Review (`adversarial_review`) | `review_record` focused on counterexamples and completion claims | Read-only review snapshot |
| Docs (`docs`) | Editing tools | `docs/` and `README.md`; other source read-only |
| Research (`research`) | Shared and specialist inspection/reporting tools | Read-only source |
| Arbitrator (`arbitrator`) | `decision_resolve` for its assigned pending decision; `work_inspect` when source is configured | Read-only source |
| Test Writer (`test_writer`) | Editing tools; `framework_generate` for `make:test` | `tests/` and PHPUnit configuration |
| Architect (`architect`) | Editing tools | `docs/`; other source read-only |

Serena is a separate provider, enabled by `project.serena.image`. It exposes
`serena_repl` to configured specialists and the work-order planner. The operator can customize editing
paths. The current research profile covers repository material, supplied sources
and retained evidence; a general external web-search provider is not configured.

An unbound, unappointed member gets only `work_context`. The external member explicitly
appointed as CO also gets `onboard`, `skill_read`, `readiness_report`, `roll_call`, `direct`
and `monitor` before its first work order. Its cached catalogue advertises reporting tools,
but ordinary work reports still require a work binding. An XO appointed in initial standby
gets only onboarding, skills, context and `readiness_report`; it cannot coordinate work.
Member names alone confer no role. Normal connections cannot invoke the operator
catalog, even by guessing a backing capability's name.

## Reporting for duty

CO can initiate `roll_call()` before receiving a work order. It runs bounded,
read-only preflight checks for configured dependencies, then starts the configured
XO in standby. No work order, job or project worktree is created. Use
`roll_call(action="status")` to inspect the retained result; CO relays readiness
and blockers to the owner. The tool records diagnostic state and uses normal MCP
approval. A native approval denial may prevent the request reaching the service;
the CO must relay that denial, not claim a retained successful check.

Every managed collaborator receives a fresh readiness challenge when its session
starts. `onboard` supplies that challenge, its `onboarding_revision`, current role
instructions and context. The agent calls
`readiness_report(challenge, onboarding_revision, blockers)` to report for duty.
The service verifies the current session, role/context, startup tool surface and
workspace, retains measured checks, and sends a receipt to the commissioning
parent: XO to CO, NCO/specialist to XO, or specialist to NCO. Callers cannot choose
another recipient or submit their own passing check results.

The current result is `starting`, `ready`, `blocked` or `timed_out`; replaced
receipts remain historical as `superseded`. A missing reply, stale challenge,
changed assignment, stopped session or failed required check cannot establish
current readiness. The default deadline is 30 seconds and uses the existing
project/role/job configuration overlay. Parents see readiness in `monitor` and
normal messages. Readiness is diagnostic; CO's native controls and existing work
authority remain in force.
Live context and message lists exclude obsolete readiness receipts. Explicit
references retain work-scoped history and mark its `readiness_current` status.

When `direct` supplies the real work, the service retires standby and starts XO
in the actual workspace with the work-specific runtime and tools. This requires
a fresh acknowledgement. It also avoids reusing a native cached tool catalogue
or working directory from standby.

“All required startup checks passed” describes those retained checks only.
Preflight configuration, an image label, or a listed tool does not prove provider
authentication or every tool's execution. The explicit agent reply proves a live
MCP round trip; it does not prove model comprehension, per-tool native approval
for `direct`, product correctness, CI or acceptance. Those retain their own gates.

## Shared context, reports and guidance

### Service decisions and investigation

With selection enabled, `orchestrate` evaluates proposed responsibilities against
the current objective, existing assignments, permitted roles and available runtime
profiles. The service applies decisive selections within those boundaries; it
does not ask the coordinator to reinterpret the provider's scores. Selection does
not change the owner's objective or the operator's runtime policy.

When a typed result is ambiguous, the service assigns the configured Arbitrator
and returns a pending decision. Its `work_context.decision` contains the unresolved
questions, prior evaluation, current context and retained evidence. The Arbitrator
investigates through read-only tools and submits `decision_resolve` with exactly
those answers, reasoning and evidence. It cannot change already-selected answers,
appoint collaborators or bypass a failed check. Once resolved, the service resumes
the retained initiating operation through its normal gates. Coordinators do not
appoint Arbitrator through `orchestrate` or substitute a different decision.

### Investigative assessments

For a consistent mission assessment, supply a proposal and optional evidence:

```json
{"name":"work_assess","arguments":{"proposal":"Add response caching while implementing the required JSON service","sources":[{"path":"src/Service/Example.php"}]}}
```

This uses the operator's versioned rubric and considers the brief, proposal and
all sources together. Source-by-source questions remain available with `sources`
and `questions`; do not combine custom questions with `proposal`.

Agents retain their interpretation and observed outcome using ordinary reports:

```json
{"name":"work_report","arguments":{"kind":"recommendation","text":"Keep caching as a separate finding; finish the current JSON behavior.","evaluation":{"reference":"artifact:12","stage":"decision","label":"deferred"}}}
{"name":"work_report","arguments":{"kind":"result","text":"The agreed JSON behavior passed; caching remained a separate finding.","evaluation":{"reference":"artifact:12","stage":"outcome","decision":"update:34","label":"useful"}}}
```

Use the actual returned references. Decision/outcome links are retained and
validated against the current work scope. Reports retain context; `orchestrate`
and `steer` enact authorized assignment changes. A finding does not become an assignment by
being reported or assessed. Your current brief still determines completion.


`work_assess` answers typed questions over candidate sources within the configured role/job budgets
in one evaluation. Each source names exactly one relative workspace `path` or an
accessible `artifact:`/`operation:` reference. The service reads it; the agent
receives answers and source locations without the raw source text. Use Serena to
find candidate paths and inspect or edit the relevant source afterward. Auditors
use their submitted review snapshot. CO and Arbitrator have no assessment tool;
eligible roles assess only their current scope.

```json
{"name":"work_assess","arguments":{"sources":[{"path":"src/SessionService.php"},{"reference":"artifact:12"}],"questions":[{"name":"recovery","instructions":"Does this describe recovery after a supervisor crash?"}]}}
```

Questions default to `noul` (a yes probability). Set `type` to `choice` and supply
2–16 distinct `options`, or to `score` and supply 2–10 ordered descriptive `levels`.
Choice and Score include the distribution and confidence. Configured thresholds
produce a decisive value or send the unresolved question to Arbitrator. A high
score alone is not proof of correctness or completeness.
Instructions and explicit `criteria` can also contain structured JSON; Noul
criteria use `true` and `false`, Choice uses named options, and Score uses ordered
levels. Existing `options` and `levels` aliases remain supported.

Sources default to 2,000 units with an optional `limit` up to 8,000 and `offset`.
Units are bytes for files/artifacts and characters for operation results, matching
their existing read conventions. Each result includes `partial`, `next_offset`,
and a content/range digest; files also include the workspace revision. Follow the
returned offset to assess the next range. **A negative result on a partial range
does not establish absence in the whole file.** Binary sources, symlinks, Git and
Serena state, and `.env*` paths are excluded from hosted source assessment.

Identical requests over the same scoped inputs reuse the retained assessment.
Changed source ranges, questions, work context, model or runtime evidence cause a
new evaluation. The returned `evidence` can be read through `work_context` for the
full rubric and typed results. Unresolved questions retain an arbitration assignment.
An input-budget error means the request needs fewer sources or smaller ranges.
On provider failure, follow the configured pending-decision or blocked result;
inspection and reporting remain available.

### Reports and routed guidance

| Tool | Use |
| --- | --- |
| `work_context` | Read the supplied brief, scoped activity, incoming messages or evidence. |
| `work_report` | Retain and route progress, findings, blockers, recommendations or results, optionally with evidence. |
| `guidance_request` | Route a question or request for assistance through the reporting relationship. |

MCP and native startup instructions identify the role/current context and direct
the agent to `onboard`, which supplies the complete brief once. Live direction
contains the current context revision and a refresh instruction, without copying
the work-order/job text. Read `work_context` before acting on changed direction;
use `onboard` for a new conversation or role change. Recovery uses these same tools.
Superseded direction is identified and never replayed as current guidance.

```json
{"name":"work_context","arguments":{}}
{"name":"work_report","arguments":{"kind":"finding","text":"The installed service already supports this behavior."}}
{"name":"guidance_request","arguments":{"text":"Should this behavior also apply to existing records?"}}
{"name":"work_report","arguments":{"kind":"result","text":"Validation evidence","evidence":{"name":"observations.md","content":"Measured results..."}}}
```

`work_context.section` accepts `brief`, `activity`, `messages` or `evidence`.
Continue listings with the returned `next_after` as `after`. A returned `reference`
retrieves an update/message or a bounded artifact/operation read; continue reads
with `next_offset` as `offset`. Operation pages contain JSON text; concatenate their
`content` fields to recover the complete result. Workers and their covering command
roles can read an operation reference; unrelated jobs cannot.

Reports and guidance accept `responding_to` with an update/message reference.
The service routes execution-role responses in their original work context. CO's
reports always relay to XO, including when `responding_to` identifies accessible
specialist evidence; CO cannot use a reply to direct a specialist. Responding to an
incoming message also acknowledges it. Agents do not choose recipient IDs or
perform a separate acknowledgement step.

Reports, evidence associations and outbound messages commit together. Identical
contents and binding reuse the report on retry. A failed evidence publication can
leave an unlinked blob for reuse; it does not expose a partially published report.
CO retains xor's instructions and context through reports to XO; XO reports system
outcomes through CO for relay to xor.

A specialist reads its job and shared work-order material. Command-level private
reports and evidence stay with the relevant participants. Scope checks apply to
references, listings and live delivery. In-flight reports retain the binding under
which they started; subsequent calls use the current binding.

### Reporting capability gaps

When the available tools cannot express a needed operation, attach a
`capability_gap` to an ordinary `work_report` or `guidance_request`. Report it
even when a permitted workaround succeeds. Describe the needed capability in
stable terms so later encounters can be recognized; describe the encounter and
its effect on the work in the report text.

```json
{"name":"guidance_request","arguments":{"text":"I need to inspect the configured check names before choosing validation, but cannot find that information in my available tools.","capability_gap":{"capability":"Discover configured validation checks","category":"discovery","occurrence_key":"validation-check-discovery-1","attempted_tool":"work_check"}}}
```

The required fields are `capability` (1–160 characters), `category`, and
`occurrence_key` (1–80 characters). Reuse the occurrence key when retrying the
same report; use a new key for a new encounter, even if its description is the
same. The category records the agent's assessment:

| Category | Observed problem |
| --- | --- |
| `missing_tool` | No available tool appears to provide the needed operation. |
| `discovery` | The capability may exist, but its tool or use could not be found. |
| `permission` | The operation appears unavailable to the current role or scope. |
| `tool_failure` | An available tool failed while attempting the operation. |
| `unknown` | The cause is not established. |

Optional fields are `attempted_tool` (up to 160 characters), `reference` (an
accessible `update:`, `message:`, `artifact:` or `operation:` reference), and
`workaround` (up to 1,000 characters describing what was done and its outcome).
Keep credentials and other secrets out of all report fields. References undergo
the same work-scope checks as ordinary evidence reads.

Reporting a gap preserves the normal reporting relationship and does not grant
permission, create an assignment, or authorize a workaround. A permission denial
may be intentional; a failed call alone does not prove that a capability is
missing. The operator reviews recurring reports through the
[capability-gap backlog](../devtools/OPERATIONS.md#capability-gap-review).

## CO and XO tools

| Role / tool | Outcome |
| --- | --- |
| CO — `direct` | Relay xor's authorized objective, completion criteria, boundaries and shared context references; bind XO and deliver its brief. |
| CO/XO/NCO — `monitor` | CO reads overview status for relay to xor. XO inspects progress and the combined delivery; NCO sees its job. |
| XO — `conclude` | Accept and deliver the inspected, passing integrated result, retain the assessment and release the execution team; CO remains available to xor. |
| XO — `plan_adopt` | Adopt a retained planner result and the complete job breakdown, or revise the existing plan. |
| XO/NCO — `orchestrate` | Create/update role assignments within their authority, bind configured collaborators and start their sessions. XO also commissions planners and job NCOs. |
| XO/NCO — `steer` | Guide, pause, resume, stop or replace collaborators; revise a role brief or complete/cancel a role assignment. Only XO accepts/cancels jobs. |

`direct` requires `title`, `objective`, `done_when` and `constraints`. Its first call
creates the work order and claims an available XO slot. Later calls revise that
work order and deliver the revised instructions to XO. Changed context invalidates
older plans and assignments; XO coordinates the execution team's response. CO
remains xor's existing session and does not choose or revise the objective itself.
CO's configured reporting tools are advertised from session startup so native
clients that cache their initial tool list can use them after `direct` binds the
work order. Reports and guidance still require that current binding; discovery
does not grant authority to call them before work exists.
To pass prepared documents, first establish the work order with `direct`, retain
them as evidence with CO's `work_report`, then call `direct` again with `context`
containing the returned `artifact:` references. The
service shares those documents with the team; unrelated evidence stays scoped.
Omitting `context` on a later call preserves it; an empty list clears it.

`orchestrate.work` contains up to four assignments per call. Each has `name` and
`role`; execution assignments also name their adopted `job`. Brief fields
`objective`, `done_when` and `constraints` default to the job or existing assignment.
A planner has no job and requires all three fields. Repeating a name and role in
the same job updates the same assignment/collaborator. Names are case-insensitive;
use a new assignment name after completion. Several roles may share a job without
overwriting its completion criteria.

Runtime choices belong to the operator's [role and vendor policy](shared-runtime-config.md).
Neither tool takes harness/model/reasoning selectors. The service allocates role
instances and starts [owned tmux terminals](tmux-control.md). Existing collaborators
keep their launch settings. Failed allocation batches roll back assignments, bindings and messages.

The following illustrates an authorized product job, not a request to implement it:

```json
{"name":"direct","arguments":{"title":"Project summary","objective":"Provide a basic project summary command","done_when":"The command and tests meet the agreed behavior","constraints":"Use native Symfony capabilities; keep devtools unchanged"}}
{"name":"orchestrate","arguments":{"work":[{"name":"Plan summary command","role":"planner","objective":"Inspect the supplied scope and propose bounded jobs","done_when":"The plan describes requirements, dependencies and verification","constraints":"Use existing Symfony capabilities; planning only"}]}}
{"name":"monitor","arguments":{}}
```

`monitor` returns bounded collaborator and job listings. Continue with the returned
`next_offset`, `next_job_offset` or `next_assignment_offset` as `offset`. CO sees its
work-order team; XO sees its descendants and jobs; NCO sees its job and specialists.
Completed assignments and their results remain visible after sessions are released.
Usage is the latest raw observation, not an invented cost
or an additive total. Missing usage stays unknown.
The default `section="overview"` shows progress. With a named `work`, the default
shows its review packet for XO/NCO. CO can read only the default overview, without
a named `work` or review/CI/feedback selector. XO may omit `work` and select
`section="review"`, `"ci"` or `"feedback"` to inspect the integrated work-order
delivery; NCO uses those sections for its bound job.

For live steering, omit `work` to address the team or name a job. Optional
`assignment` and `role` filters select a contribution. `guide` retains direction;
`pause` retains the conversation and prevents new work actions; `resume` clears the
pause and starts a stopped session if needed. `stop` requests orderly shutdown and
preserves the unfinished job and binding. Pause is not a native mid-token interrupt.
Stopping/restarting creates a new native conversation.
`replace` requests shutdown and a fresh conversation using current vendor policy;
include a reason in `direction`. XO replacement belongs to trusted operator
repair; CO cannot request session replacement through managed tools.
See [session handover](session-handover.md) for switching the user-facing CO and
the retained context and generation boundaries.

A role brief can revise `objective`, `done_when` or `constraints`; it does not
rewrite the job or xor's objective. XO changes job scope through `plan_adopt`.
XO retains superior steering authority over NCOs and their specialists; the job's
NCO normally assigns and completes its specialists, while XO accepts the job.
Commands recheck scope when committing.
Pending startup survives MCP restart and is serviced while a team MCP server is
connected. Startup failures are retained and sent to the supervisor; `direct`
retries XO startup, and `steer(action="resume")` retries a collaborator.

## Planning and bounded handoffs

XO commissions the work-order planner using the preceding example. Planner uses
Serena, installed Maker/Mate metadata, Symfony Console introspection and checks,
then records a `work_report(kind="result")` with plan evidence. It has no
generation, editing, publication or delegation tools. XO reads the result and
artifact, then adopts the complete job list (up to twenty jobs). For example:

```json
{"name":"plan_adopt","arguments":{"plan":"update:21","jobs":[{"name":"Summary command","objective":"Implement the agreed summary command","done_when":"Generated command, behavior tests and checks pass","constraints":"Use installed Makers and attributes; product paths only","editable_paths":["src/Command/SummaryCommand.php","tests/Command/SummaryCommandTest.php"]}]}}
{"name":"orchestrate","arguments":{"work":[{"name":"Implement command","job":"Summary command","role":"coder"}]}}
```

Use actual returned references and paths. Adoption completes the planning
assignment and releases its session for reuse. There is no separate planning job.
The service rejects superseded results and plans for changed work-order context.
Specialists receive the adopted plan reference, readable evidence and job brief.
Planner source inspections automatically synchronize the work-order workspace
with accepted contributions before reading it.

For a multi-job plan, XO assigns `role="nco"` with a `job` for each job. Each NCO
then assigns specialists only to that job. Job `dependencies` name other jobs in
the same plan; execution waits until those jobs are accepted. Only XO can request
more planning or change the job list. NCO raises gaps through `guidance_request`.

Each job has one workspace. Editing assignments take turns; read-only roles and
separate jobs can run concurrently. The coordinator inspects a specialist's current
result, then uses `steer(action="complete", work=..., assignment=..., direction=...)`
to retain the contribution and release the session. An editor saves remaining
changes first. Cancellation also names the assignment and requires a reason.
NCO completes its specialists and reports the checked job ready. XO inspects
current evidence/review, accepts the job with `steer(action="accept", work=...,
direction=...)`, submits the integrated outcome and concludes it after verification.
CO relays the system's outcome to xor.

Replanning preserves job identities, assignments, evidence and delivery history.
XO may revise the breakdown using the same adopted source while the work-order
context is unchanged; changed context requires a fresh planner result. Omitted
unfinished jobs must be explicitly cancelled. Revisions pause existing job teams;
for a multi-job plan, XO resumes each coordinator with
`steer(action="resume", work=..., role="nco")`, leaving its specialists paused.
The job's coordinator refreshes assignments with `orchestrate` when the plan
source changes, then resumes them. XO performs this refresh directly for a one-job
plan. A change between one and multiple jobs transfers the existing team without
resetting its work.

Explicit edit paths must fit both the job scope and the operator's role policy,
and exclude protected Maker source. Assignment changes serialize with active
workspace operations. Changed views require a new Serena connection; a file
replaced by generation or Git resets its mounted view. Extra improvements can be
reported without becoming assignments. Diagnostics supply a relevant permitted
role or framework skill directly. Completion is assessed against the current
assignment, job or owner goal, with source context and provenance handled by the service.

## Workspaces and Git

The service prepares an integration workspace for each work order and a separate
workspace for each job. Managed harnesses and file-backed evidence use the bound
workspace automatically. Jobs incorporate accepted contributions through their
parent work order. Git operations, Makers, checks and Serena calls coordinate
through scoped locks. Branch routing and local paths are service responsibilities.

| Tool | Outcome / input |
| --- | --- |
| `work_inspect` | Read changes, checkpoints, scope, retained operations or delivery evidence. |
| `work_save` | Checkpoint allowed changes and retain a diff; takes `note`. |
| `work_refresh` | For a job, checkpoint and incorporate accepted contributions plus current development changes. Resolve any listed conflicts with the editing tools, then `work_save` and refresh again. For XO, synchronize the integrated result. |
| `work_recover` | Restore a returned `checkpoint` as a new commit; takes `reason` and preserves current work first. |
| `work_submit` | Submit a checked contribution, or let XO submit the combined result once jobs are finished; takes `summary`. Publication and CI observation are automatic. |

For review fixes before acceptance, keep working in the same job and call
`work_submit` again. It updates the existing PR and starts observation of the new
CI result. XO inspects the changed revision again before accepting it; GitHub's
required checks still apply. Branch names and PR targets remain service-managed.

`work_inspect` defaults to `overview`. Use `section="changes"` with optional `path`
and `offset` for bounded local diffs. `section="operations"` reads retained long
operation summaries, without diagnostic bodies. Continue with `next_offset` as
`offset`; this cursor remains stable when new operations finish. Read one returned
`operation:N` reference through `work_context` for detail. Delivery sections are described below.

Default coder paths cover `src`, `config`, `packages`, `tests`, `templates`,
`assets`, `translations`, `public`, `migrations`, Composer/Symfony lockfiles and
PHPUnit configuration. Other editing roles have the narrower paths in the role
table. The brief includes the editable scope. Unexpected changes remain available
for guidance; checkpoint/submission refuses changes outside the role's paths.
Git hooks and external diff programs are disabled in managed operations.

Submission retains completed checkpoints and publication across retry. With
`project.development` enabled, failed checks or files changed by checks return
`needs_attention` with evidence. Submission does not accept the job or merge its PR.

## Symfony Makers, Mate and checks

These tools use the installed application in the job worktree. `project.development`
enables them; each worktree owns its locked Composer installation.

| Tool | Outcome |
| --- | --- |
| `framework_inspect` | Discover installed Maker metadata, Mate diagnostics, package versions, profiler resources or allowed Symfony Console introspection. |
| `framework_generate` | Run an installed basic Maker with structured arguments and options. |
| `work_check` | Run a named configured check or the full configured suite, with retained evidence. |

```json
{"name":"framework_inspect","arguments":{"kind":"makers","name":"make:command"}}
{"name":"framework_generate","arguments":{"maker":"make:command","arguments":{"name":"app:example"}}}
{"name":"framework_inspect","arguments":{"kind":"mate","name":"server-info","parameters":{}}}
{"name":"framework_inspect","arguments":{"kind":"console","name":"debug:container","parameters":{}}}
{"name":"work_check","arguments":{}}
```

Omit `name` to list Makers or Mate tools. For a named Mate tool, omit `parameters`
to inspect its schema; supply an object, including `{}`, to call it. Maker inputs
come from installed Console metadata. Symfony performs the generation using its
current attributes and conventions. Test writers can generate through `make:test`.
For `kind="console"`, omit `name` to discover installed, operator-approved commands;
omit `parameters` to inspect a name's metadata, or pass an object with optional
`arguments` and `options` to run it. The default policy allows `about`, `debug:*`
and `router:match`, exposing the diagnostics of the installed Symfony bundles.
Local agents cannot invoke `make:maker` or edit protected Maker source/templates.
A Maker needing an additional bundle or interactive answers reports its actual
failure and preserves partial changes for inspection.

Default validation is container lint, YAML lint and PHPUnit. Operators can configure
named fixed commands; agents provide a name, never a shell command. These application
commands and Composer plugins run as the local service user. The tool interface does
not provide OS containment for application code.
Development subprocesses filter inherited environment variables and do not set
`APP_ENV` or `APP_DEBUG`; the project's application and test bootstrap choose them.
Composer remains noninteractive.

Framework inspection, generation, checks and configured submission return retained
operation receipts promptly. Compact completion notices (including failed-check
counts) and evidence references are delivered to the original worker and supervisor
while their scope still covers that work. Full results remain available through
`work_context(reference="operation:N")`.
The worker's failure notice also includes its permitted recovery skill; supervisors
receive the outcome without another role's skill instructions. Workers survive
an MCP disconnect. An identical active call reuses its receipt; a lost worker becomes
`uncertain` without automatically replaying external effects. Pausing prevents new
processes; an already-running command finishes or reaches its timeout.

## Serena v2 REPL

Serena is the separate source-navigation/editing provider. Managed Codex, Claude
and Grok sessions attach it automatically for configured specialist jobs. CO,
XO and NCO do not receive it. External specialist clients can attach the gateway described
in [operator setup](../devtools/OPERATIONS.md#external-specialist-connections).

```json
{"name":"serena_repl","arguments":{"code":"s.info()"}}
```

The gateway supplies the workspace and persistent session. `s.info()` and targeted
facade documentation support progressive discovery. Python variables, functions and
classes persist within the provider session; large output becomes job evidence.
A restart loses Python variables while preserving source files and managed evidence.
The beta source revision and PHPactor dependency are pinned in the
[Dockerfile](../devtools/serena/Dockerfile); the image retains upstream source/license.

The REPL executes unrestricted Python inside its container. The container has no
runtime network, host home/credentials, control-plane database or Docker socket.
Source is mounted read-only with only the editing role's assigned paths writable;
source for auditors, researchers, planners and arbitrators stays read-only. Explicit job context paths curate
the visible project. Protected Maker directories stay read-only beneath broader
editing paths. Provider configuration/cache/memories have a separate
private mount. Writable aliases/symlinks are rejected.

The image records supported Python process-launch attempts for operator review.
This monitoring does not prevent the REPL from starting a process and does not
grant permission to replace named control-plane operations with shell commands.
Records distinguish attempts from successful execution; missing findings are not
proof that no process ran. See [process observations](runtime-telemetry.md#serena-process-observations)
for coverage limits and the local operator report.

Each call holds the job lock. The container pauses before releasing it and resumes
on the next call, preserving REPL state while preventing background Python from
racing Git, Makers or checks. Work pause rejects new REPL calls. Role, job,
supervisor or provider-configuration changes invalidate an existing gateway.
Refreshing an auditor's source snapshot also resets its provider before further use.
Force-killed providers can require operator cleanup of a leftover container.

## Review, CI and external feedback

The job's coordinator can add `role="audit"`, `role="security_auditor"` or
`role="adversarial_review"` to the producer's named job. They reuse the audit
behavior; `audit` remains its default role, and the specializations add no
acceptance gate. Each uses a detached
snapshot of the submitted revision, or the current checkpoint before submission. Producer
edits cannot silently change that view. `work_inspect(section="review")` refreshes
the snapshot and returns the PR, CI, findings and changed files.

| Inspection | Details |
| --- | --- |
| `section="review"` | Changed-file `page`; optional `path`/`offset` for a provider patch. GitHub may omit large/binary patches. |
| `section="ci"` | Failed steps, annotations and log excerpts for the bound PR. `page` continues two failed checks at a time; `offset=0` begins consecutive log reads. |
| `section="feedback"` | GitHub conversation, review and inline comments, with file/line/revision context. Continue with `page`. |

Diagnostics retain a full packet as scoped evidence and return a small preview.
Read its reference through `work_context`. Missing or expired logs are explicit.
XO/NCO use the same sections through `monitor(work="Job name", section="ci")`
or `section="feedback"`. Neither diagnostic section replaces review inspection
before acceptance. CI changes are delivered to the worker and its current supervisor.

| Tool | Outcome |
| --- | --- |
| `review_record` | An auditor records `ready`, `changes_requested` or `comment` with a `summary`, against its inspected revision. |
| `review_respond` | Producer responds with `text`; optional `review` selects a returned review reference. XO supplies a work-order thread reference from `monitor(section="feedback")` and can set `resolve:true` after addressing the finding. |

The service retains and routes findings/responses to the job participants and XO.
GitHub receives a COMMENT review; a local `ready` conclusion does not fabricate a
formal approval from an independent GitHub account. Repository review rules apply.
Stable markers reconcile successful publications after lost responses; unknown
write outcomes are not silently repeated.

## Acceptance and turnover

XO inspects the current delivery with `monitor(work="Job name")`, then uses
`steer(action="accept", work="Job name", direction="Assessment")`. Acceptance
requires the same inspected PR revision, passing CI, the managed branch/target and
a clean producer workspace at that revision. The service merges into the work order through normal
GitHub rules, retains the assessment/evidence and marks the job done.

For report-only work, a retained `work_report(kind="result")` and no unsubmitted
source changes are required. XO can assign an auditor when the responsibility
needs review.

`steer(action="cancel", work="Job name", direction="Reason")` preserves the
worktree, PR and evidence and marks the job cancelled. An existing PR remains open
for the owner to decide its disposition. Acceptance/cancellation releases bindings
and requests shutdown; slots become reusable after the native processes stop.

Completed jobs stay visible in `monitor`. New responsibilities use new names and
start from the integrated work. Merged job worktrees and audit snapshots are
retired after their processes and providers stop. Unexpected changes remain for
inspection; cleanup reports its status without blocking other work. Messages,
checkpoints, assessments, PR references and artifacts remain available.

When every job is done or cancelled, XO uses `work_submit(summary)` to validate
and submit the combined result. Both Codex Code Review and Security Review are
requested for that commit. Review and CI changes arrive as scoped notifications.
XO inspects findings with `monitor(section="feedback")`, coordinates fixes through
jobs, and uses `review_respond(text, review, resolve=true)` to explain and resolve
addressed threads. After integrating fixes, XO submits again for reviews of the new
commit. Missing or stale reviews remain pending; a clean CI result alone is not
review completion. Job PRs do not request these additional reviews.

XO inspects the combined outcome with `monitor(section="review")`, then calls
`conclude(assessment)` after CI and both current reviews pass and findings are
addressed. Delivery verifies the current result and releases the
remaining execution team and reports the outcome to CO. CO stays available to
relay messages to xor. The integration workspace retires after this final merge and
shutdown. Report-only work with no source changes can conclude without a PR.
The next authorized `direct` creates a new work order. Submission, contribution
acceptance and delivery of the overall outcome remain distinct observations.
