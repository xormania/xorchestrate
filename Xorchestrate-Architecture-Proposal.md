# Xorchestrate Architecture Proposal

| | |
| --- | --- |
| Prepared | 2026-10-23 04:00 UTC; finalized 2026-10-23 04:17 UTC |
| Basis | `xormania/xorchestrate` pull request 1 (`feat/extract-agentscient`, head `af2d52f`). The core runtime modules (store, configuration, API, MCP, control, commands, planning, delivery, native sessions, launch, harnesses, profiles, runtime, role packages, work, orders, artifacts, telemetry) and the runtime documentation were read directly. The remaining runtime modules, the test suite, CI and the retained toolkit were surveyed by delegated reads with sampled verification. |
| Status | Independent proposal for the project owner's evaluation. Nothing in this document has been implemented, committed or published. |
| Citations | `path:line` references are to the PR head. `devtools/` lines were read directly. `legacy/` lines come from a delegated survey of which a sample was verified directly against the source. |

---

## 1. Summary

Pull request 1 delivers a working, well-defended coordination runtime. Its correctness mechanisms are strong: generation-fenced sessions, idempotent request keys, atomic claim-and-deliver with an explicit "uncertain" state, incarnation-checked bindings, content-addressed artifacts, credential redaction and fail-closed configuration. Those mechanisms are worth keeping almost unchanged.

The same runtime hardwires most of its policy. The CO/XO/NCO chain of command, the reporting rules, who may plan, assign, accept and conclude, GitHub as the only delivery target, SQLite as the only store, MCP over stdio as the only agent surface, tmux as the only process host, Symfony and Serena as the only development providers and Jev as the only evaluator are all expressed as code paths, not as replaceable components. Behaviour identifiers are compared by literal in 122 places across 21 modules, and 515 SQL statements are spread across 31 modules. A reader cannot change the organization without editing the message router, the planner, the delivery service and the storage layer at once.

This proposal keeps the mechanisms and moves the policy and the infrastructure behind explicit boundaries:

- A **kernel** that owns the domain model (participants, sessions, bindings, work, messages, reports, artifacts, events, decisions), its state machines and its scope algebra, with no SQL, no subprocesses and no knowledge of GitHub, tmux or any vendor.
- **Ports** (typed protocols) for storage, conversation transport, participant runtime (harness), process host, agent-facing API, change integration, workspace provider, tool providers, semantic evaluation, secrets and telemetry. Today's implementations become the default adapters.
- **Policies** as replaceable, data-described components: an organization model (roles, reporting rules, authorities, coordination mode, capacity), a routing policy (destinations and visibility), a workflow policy (gate pipelines on lifecycle transitions), a capability catalog (the existing role packages, generalized) and a runtime selection policy (the existing profiles and vendors).
- A **composition** layer that reads one manifest, wires adapters and policies, layers configuration by concern and records a policy revision on every binding, session, operation and decision so that running work has defined behaviour when configuration changes.

The default composition reproduces the current behaviour, so the existing 825 executing tests remain the characterization suite during the transition. Nine representative changes in section 8 show that an infrastructure swap touches one adapter and one manifest line, a behaviour change touches one policy module or one configuration section, and an organizational change touches configuration only. Section 10 gives a transition in six phases, numbered 0 to 5, that keeps the suite green at each step. Section 11 proposes contract tests per port, golden decision tables per policy and architecture conformance checks that make the separation measurable.

The main tradeoffs are indirection in exchange for replaceability, the discipline of keeping policy vocabularies closed rather than programmable, and the cost of making the storage port honest about transactional needs that SQLite satisfies cheaply today.

---

## 2. How to read this document

Three kinds of statements are kept distinct throughout:

- **Finding (F-n):** something observed in the code, tests or documentation, with a citation.
- **Assumption (A-n):** something this proposal takes as given because the material does not settle it. Each is revisable and listed in section 14.
- **Recommendation (R-n):** a design choice this proposal makes. Alternatives and their costs are stated where the choice is contestable.

"Mechanism" means code that makes the system correct regardless of which policy is selected. "Policy" means a choice a deployment could legitimately make differently. The distinction is the backbone of the proposal.

---

## 3. What exists today

### 3.1 Inventory

| Area | Size | Notes |
| --- | --- | --- |
| Active runtime `devtools/` | 62 top-level modules, 18,214 lines of Python (excluding tests, scripts, Serena image and PHP integration helpers) | Standard library only. One package, flat namespace. |
| Runtime tests `devtools/tests/` | 84 `test_*.py` files (18,280 lines, 853 test methods) plus 19 shared fixture modules, a strict census runner, a real MCP client and six synthetic harness scripts; 20,731 lines in all | 56 of the 84 files execute raw SQL against the store; 14 spawn the real stdio MCP server; 3 require real tmux. The PR reports 825 executed in CI. |
| Configuration assets | `configuration.cue` (416 lines) with generated `configuration.json`; `role-packages.cue` (461 lines) with generated `role-packages.json`; `adapters.json`; `runtime-defaults.json`; `assignment-policy.json` | CUE is a maintainer tool; the runtime reads JSON and checks the export is current (`devtools/configuration.py:329-336`, `devtools/role_packages.py:58-62`). |
| Retained toolkit `legacy/agentscient/` | 921 files, about 176,000 lines of Python including synthetic application fixtures | Not imported by the runtime (`README.md`, "The earlier toolkit is not imported by the active runtime"). Surveyed in section 12. |
| Reference CI `reference/agentscient-dev/` | 23 files | Original CI machinery, not run. |
| State | `live.sqlite3` with 33 tables at schema version 18 (`devtools/store.py:18`, tables created at `devtools/store.py:40-331`), `telemetry/<member>.jsonl`, `artifacts/<sha256>`, `profiles/<member>/<generation>/`, `processes/`, lock directories | Private state directory outside the source tree. |

### 3.2 Anatomy of the runtime as built

The runtime is organized as one Python package with services that share a `Store` and a `member` identity. Reading it as layers:

**Agent-facing surface.** `devtools/mcp.py` serves MCP over stdio, one process per connection, with a thread pool for concurrent calls, a lease that fences the connection to one session generation (`devtools/sessions.py:24-49`), an optional Claude channel for push delivery (`devtools/mcp.py:52-76`) and background threads for launch intents, GitHub watches and worktree retirement (`devtools/mcp.py:164-205`). `devtools/api.py` computes the tool catalog per role and binding (`devtools/api.py:435-470`), validates arguments against a reduced JSON Schema (`devtools/api.py:371-411`), and dispatches by a 130-line if-chain (`devtools/api.py:618-749`). A separate operator catalog (`capability_search`, `capability_describe`, `capability_invoke`) exposes setup and repair tools that are never attached to managed agents (`devtools/api.py:291-307`, `devtools/api.py:510-547`).

**Coordination.** `devtools/control.py` owns bindings (role, work order, job, reports_to, brief, revision, paused, launch_pending, assignment_id), the reporting and routing decisions (`Control._destination`, `devtools/control.py:502-567`), scoped context projection (`work_context`, `devtools/control.py:349-489`) and report retention with atomic routing (`Control.report`, `devtools/control.py:618-727`). `devtools/commands.py` implements the command tools `direct`, `orchestrate`, `steer`, `conclude`, `monitor` and the slot allocation for fixed and runtime teams (`Commands._slot`, `devtools/commands.py:167-239`). `devtools/planning.py` implements plan adoption and role assignment with the coordination rules (`devtools/planning.py:250-355`).

**Work and evidence.** `devtools/work.py` (work orders, jobs, links), `devtools/artifacts.py` (content-addressed snapshots), `devtools/store.py` (events, messages, sessions, schema), `devtools/telemetry.py` (redacted JSONL traces).

**Sessions and transport.** `devtools/launch.py` starts and stops managed processes through a one-use abstract Unix socket handoff (`devtools/launch.py:196-237`) into a tmux pane (`devtools/terminals.py`) with an isolated bootstrap (`devtools/bootstrap.py`). `devtools/native.py` runs the Codex app-server and Grok ACP conversations and delivers messages through their native protocols (`devtools/native.py:374-410`); Claude runs as a TUI with channel notifications (`devtools/native.py:439-480`). `devtools/processes.py` retains Linux process identities and signals through pidfds (`devtools/processes.py:95-107`). `devtools/harnesses.py` and `devtools/adapters.py` provide a registry of harness, role and evaluation adapters.

**Delivery and workspaces.** `devtools/workspaces.py` owns Git worktrees per job and per work order with file locks (`devtools/workspaces.py:98-114`), editable path ceilings (`devtools/workspaces.py:260-285`), checkpoints and submission. `devtools/github.py` wraps the GitHub REST and GraphQL APIs for PRs, reviews, threads, CI and watches. `devtools/delivery.py` and `devtools/orders.py` implement review, acceptance and conclusion with revision checks and gates.

**Decisions.** `devtools/decisions.py`, `devtools/assignment_decisions.py`, `devtools/completion.py` and `devtools/advice.py` call the Jev evaluator through `devtools/jev.py`, classify typed answers against thresholds (`devtools/decisions.py:73-96`) and escalate ambiguity to an Arbitrator role.

**Configuration.** `devtools/configuration.py` validates a single settings document and layers a private overlay by project, role and job (`devtools/configuration.py:425-516`). `devtools/runtime.py` validates `roles.json` and `vendors.json` and resolves profiles (`devtools/runtime.py:109-138`). `devtools/role_packages.py` validates the role catalog export.

### 3.3 Mechanisms worth keeping

These are correctness mechanisms. The proposal keeps each one and only changes where it lives.

| Mechanism | Where | Why it matters |
| --- | --- | --- |
| F-1 Session generations and leases | `devtools/store.py:490-544` (`begin_session`), `devtools/sessions.py:24-49` (`Lease`), `devtools/store.py:627-655` (`take_delivery` checks generation and desired state inside the claim transaction) | A replaced session can neither act nor consume its replacement's messages. |
| F-2 Idempotent requests | `messages UNIQUE(sender, request_key)` (`devtools/store.py:45-50`), `work_updates UNIQUE(actor, request_key)` (`devtools/store.py:175-180`), work orders and jobs keyed by `(created_by, request_key)` with payload comparison (`devtools/work.py:73-79`) | Retries are safe; a reused key with a different payload is rejected. |
| F-3 Delivery state machine with explicit uncertainty | `queued → delivering → submitted → acknowledged`, plus `uncertain` and `superseded` (`devtools/store.py:627-692`, `devtools/commands.py:279-284`); crashed transports mark in-flight deliveries uncertain (`devtools/store.py:533-538`); only the sender may retry (`devtools/store.py:682-692`) | Transport ambiguity is recorded, never resolved by silent replay. |
| F-4 Binding incarnations | `next_binding_revision` allocated in the binding transaction (`devtools/store.py:390-400`); scope rechecked before commit in reports, commands, delivery and planning (`devtools/control.py:663-664`, `devtools/commands.py:142-153`, `devtools/delivery.py:381-383`, `devtools/planning.py:455-456`) | A change of role or work between read and write is detected, not raced. |
| F-5 Superseded direction | New direction supersedes queued direction for the same member and scope (`devtools/commands.py:279-284`); render shows "superseded" when the context revision differs (`devtools/control.py:769-780`) | Stale briefs are never delivered as current. |
| F-6 Process identity and ownership | Boot id plus start time plus session id recorded at launch (`devtools/processes.py:20-58`); signals only through `pidfd_send_signal` after re-verifying identity (`devtools/processes.py:95-107`); family capture for orphaned groups (`devtools/processes.py:35-48`) | Recovery never signals a reused PID. |
| F-7 Launch handoff without files | One-use abstract Unix socket, peer uid check, environment and config passed in memory, ten second registration deadline, failed handoff stops the terminal (`devtools/launch.py:196-237`, `devtools/bootstrap.py`) | No credential-bearing launch files; a half-started agent cannot linger unreported. |
| F-8 Credential redaction | Key-name and value based filtering on every event and trace frame, including JSON nested in MCP text (`devtools/telemetry.py:18-57`); unavailable filters suppress the payload rather than leak (`devtools/telemetry.py:48-57`) | Telemetry stays shareable. |
| F-9 Fail-closed configuration | Duplicate keys rejected, non-finite numbers rejected, size budgets, stale generated exports rejected (`devtools/configuration.py:311-336`, `devtools/role_packages.py:58-62`); every selectable overlay combination validated, not just the active one (`devtools/configuration.py:494-500`) | Misconfiguration fails before work is served. |
| F-10 Content-addressed artifacts | Bytes published by hard link before the catalogue row; fsync of file and directory; integrity check on read; symlinks refused (`devtools/artifacts.py:75-152`, `devtools/artifacts.py:24-30`) | Evidence is immutable and never half-visible. |
| F-11 Scoped visibility | Context, messages, activity and evidence are filtered by binding scope and audience (`devtools/control.py:103-153`); shared with native delivery through the same predicate (`devtools/store.py:655-658`) | A specialist cannot read command-level traffic. |
| F-12 Operator and member separation | Operator catalog is a different tool set on a different connection flag (`devtools/api.py:430-433`, `devtools/run.py:76-79`) | Setup authority is never attached to a managed agent. |
| F-13 MCP-only gate on managed sessions | Native tool allowances built from the live catalogue (`devtools/profiles.py:149-207`), hooks deny by default on error (`devtools/run.py:167-181`), the Codex bridge refuses non-MCP tool activity (`devtools/native.py:273-279`) | The server-side role check cannot be bypassed from the harness. |
| F-14 Write boundaries | Editable paths bounded by role package ceiling, project narrowing, protected paths and assignment paths (`devtools/workspaces.py:260-285`, `devtools/assignments.py:44-52`) | A role's reach cannot be widened by an agent argument. |
| F-15 Retained long operations | Queued operations with a partial unique index guaranteeing one active operation per job or order (`devtools/store.py:242-244`, `devtools/store.py:300-306`); workers recheck scope and capability before executing (`devtools/operations.py:232-238`) | Long tool calls survive disconnects without duplicating effects. |
| F-16 Decision freshness | A retained decision is reusable only while its context and policy revision are unchanged (`devtools/decisions.py:128-134`) | A stale judgment never authorizes new work. |

### 3.4 Policy embedded in mechanism

These are choices a deployment might make differently. Each is currently fixed by code.

| Policy choice | Where it is fixed | Example of a deployment that would choose differently |
| --- | --- | --- |
| F-17 Chain of command: XO reports to CO, NCO reports to XO with a job, planner reports to XO at work-order scope, CO has no superior | `devtools/control.py:190-201` | A flat team with one lead; a team where a human reviewer sits above the coordinator. |
| F-18 One coordinator per job; multi-job plans require an NCO per job; single-job plans are coordinated by XO | `devtools/control.py:202-207`, `devtools/planning.py:273-283`, `devtools/planning.py:76-78`, `devtools/delivery.py:323-327` | A team that always coordinates directly; a team that assigns coordinators by workload. |
| F-19 Only XO plans, adopts, accepts and concludes; only CO directs; CO may only use `direct`, `monitor`, `roll_call` | `devtools/planning.py:253-255`, `devtools/delivery.py:291`, `devtools/orders.py:68-72`, `devtools/role_packages.py:105-107` (the validator forbids any other interface package) | A deployment with no interface role, where the owner directs the coordinator directly. |
| F-20 Routing: CO always relays to the single XO; planner always to XO; others to `reports_to`; job findings visible to job participants, command traffic only to participants | `devtools/control.py:502-567`, `devtools/control.py:131-142` | Broadcasting findings to all job participants; topic subscriptions; direct peer addressing with an allowlist. |
| F-21 GitHub is the delivery target; a job is delivered through one PR; the integration PR requires Codex code and security reviews before conclusion | `devtools/github.py`, `devtools/delivery.py:331-366`, `devtools/orders.py:150-151`, `devtools/orders.py:292` | GitLab or Gitea; local branch integration without a hosting service; human review instead of a vendor review bot. |
| F-22 Branch naming, target branch default `dev`, remote `origin`, worktree layout | `devtools/orders.py:40-43`, `devtools/workspaces.py:125-136`, `devtools/workspaces.py:208-212` | Any other repository convention. |
| F-23 SQLite with direct SQL in 31 modules, including authorization predicates emitted as SQL fragments | `devtools/store.py`, `devtools/control.py:108-153`, counts in Appendix C | Postgres for a multi-host installation; an in-memory store for tests. |
| F-24 MCP over stdio as the agent surface, one server process per connection | `devtools/mcp.py`, `devtools/profiles.py:290-291` | MCP over HTTP; an HTTP API for external agents; a message bus. |
| F-25 tmux as the process host, Linux `/proc` and pidfd as the process model | `devtools/terminals.py`, `devtools/processes.py:13-18` | Containers; systemd scopes; a remote runner. |
| F-26 Three harness protocols are built in despite the registry; protocol names are compared by literal | `devtools/native.py:38`, `devtools/profiles.py:160`, `devtools/profiles.py:234-345`, `devtools/profiles.py:138` | A fourth harness installed as a plugin without editing `profiles.py`. |
| F-27 Symfony and Serena are the development providers; Symfony diagnostics are configuration defaults; default editable paths describe a Symfony layout | `devtools/development.py`, `devtools/serena_provider.py`, `devtools/configuration.cue:269-309`, `devtools/role-packages.cue:171-185`, `devtools/role-packages.cue:78` | A TypeScript or Rust project; a documentation-only project; a project with no source at all. |
| F-28 Jev over TypeSafe is the evaluator; its naming leaks into generic records (`jev_evaluation`, `jev_feedback` events, `evaluation` field on `work_updates`) | `devtools/configuration.cue:137-144`, `devtools/control.py:577-579`, `devtools/control.py:724-726` | Any other judge model; no semantic evaluation at all. |
| F-29 Arbitration by a dedicated role with reserved session capacity | `devtools/configuration.py:104-126`, `devtools/runtime.py:246-254`, `devtools/commands.py:197-204` | Escalation to a human; blocking until an operator answers; majority of two judges. |
| F-30 Work topology: work order, job, role assignment; a job carries repository, branch and PR columns | `devtools/store.py:57-83`, `devtools/store.py:310-317` | Two-level task lists; research programmes without code; several repositories per job. |

### 3.5 Structural issues

- **F-31 Behaviour literals spread across the codebase.** `adapter(role) == "xo"` style checks appear 122 times in 21 modules (Appendix C). Authority, routing, workspace writability (`devtools/workspaces.py:77-83`), operation permissions (`devtools/operations.py:29-39`), tool surface (`devtools/api.py:439-462`) and migrations (`devtools/store.py:332-346`) each re-derive the organization. The role catalog is configurable in name only (`docs/role-capability-packages.md`, "The catalog is a map of configured role identities, with no required role count"); the behaviours themselves and their relations are fixed.
- **F-32 Import tangle.** 273 function-level imports exist to break cycles (for example `store.py` imports `control.message_sql` inside `take_delivery`, `devtools/store.py:296`, and `control.py` imports `decisions`, `assignments`, `workspaces`, `serena_provider`, `diagnostics` inside methods). The module-level graph has `api` depending on 15 modules and `control`, `commands`, `planning`, `delivery`, `workspaces` depending on each other through late imports. There is no layer a reader can rely on.
- **F-33 `Store` is several things.** Schema and migration (230 lines in one `upgrade`, including data repairs that depend on the role catalog, `devtools/store.py:117-367`), session lifecycle, messaging, work-context resolution and event writing share one class. `State` versus `Store` separates read-only from writable access, which is useful, but the write side has no internal boundaries.
- **F-34 Authorization predicates are SQL strings.** `scope_sql`, `message_sql`, `update_sql`, `evidence_sql` (`devtools/control.py:108-153`) compile the audience rules directly into SQL and are consumed by `store.take_delivery`, `Control.work_context`, `Control._reference` and duty readiness. Any storage change rewrites authorization; any routing change edits SQL.
- **F-35 One configuration document for unrelated concerns.** `configuration.cue` holds evaluator transport settings, decision thresholds, assignment rubrics, context filtering, diagnostic guidance routes with Symfony patterns, and completion rubrics. The validator enumerates the exact field set (`devtools/configuration.py:37-38`), so adding a policy area means editing the validator, the CUE schema and the merge rules (`devtools/configuration.py:350-366`, which special-cases field names).
- **F-36 Dispatch by name.** `API.invoke` routes tool names by prefix and membership tests (`devtools/api.py:618-749`); operators and services are located by string. This works but makes the surface hard to extend from outside the package.
- **F-37 Duplicated allocation logic.** Fixed-team slot selection (`devtools/commands.py:180-239`) and runtime-team allocation (`devtools/runtime.py:230-276`) implement capacity and reservation twice.
- **F-38 Vendor-specific gates in generic flows.** `CodexReviews.require_ready` is a hard precondition of concluding a work order (`devtools/orders.py:292`).
- **F-39 Operator-facing identifiers are product names.** Server name `agentscient_dev` (`devtools/api.py:31`), environment variables `AGENTSCIENT_*`, hook file names, review markers `agentscient-review:` (`devtools/delivery.py:223`), the Docker label `agentscient.workspace`, the tmux server prefix and `vendor/.agentscient-dependencies`. The README states these are retained deliberately for compatibility.
- **F-40 Authorization is split three ways.** `require_capability` is the intended catalog check (`devtools/role_packages.py:201-210`), but `Operations.start` adds a per-behaviour action matrix (`devtools/operations.py:29-39`), `Workspaces.scope` adds an editor-or-XO write rule (`devtools/workspaces.py:77-83`) and `Development.generate` adds Maker-specific role rules (`devtools/development.py:384-386`). The three can drift independently.
- **F-41 Services reach upward into the API façade.** `Decisions.resume` replays a retained operation through `API(...).call` (`devtools/decisions.py:428`); `steer_assignment` re-enters `API.call("steer")` twice (`devtools/assignment_decisions.py:110`, `:116`); diagnostics and duty read `API(...)._tools` (`devtools/diagnostics.py:284`, `devtools/duty.py:233`); onboarding is built around an `API` instance (`devtools/onboarding.py:47-51`). A second agent surface would have to reproduce the MCP façade to satisfy these callers.
- **F-42 The GitHub provider owns part of the routing.** CI watch notifications choose recipients inside `GitHub.tick` by role behaviour and reporting relationship (`devtools/github.py:921-932`); the watcher daemon exists only for GitHub (`devtools/watcher.py`); project setup refuses any non-GitHub repository or remote (`devtools/readiness.py:43-47`, `devtools/readiness.py:299-303`); `Workspaces.submit` is push, PR and CI watch in one method (`devtools/workspaces.py:606-612`); the review bot is one vendor's bot by login and id (`devtools/codex_reviews.py:9-12`).
- **F-43 The filesystem layout is an implicit contract.** `team.json` is parsed independently by `launch.team_config`, `workspaces.project_config` and `GitHub.__init__` (`devtools/launch.py:58`, `devtools/workspaces.py:29`, `devtools/github.py:64`); the state sub-directories (`work-locks`, `launch-locks`, `session-locks`, `telemetry`, `profiles`, `processes`), the `wt-ag` worktree parent and the skills root are literals in the modules that use them. Locks are `flock` on files, so the system is single-host by construction.
- **F-44 Repeated logic.** Runtime profile enumeration appears three times (`Advice.runtimes`, `assignment_decisions.select_bound_runtime`, `readiness.doctor`); redaction is borrowed from the GitHub transport by modules unrelated to GitHub (`devtools/workspaces.py:51-53`, `devtools/development.py:137`, `devtools/operations.py:249-252`); the evaluator imports `NoRedirect` from the GitHub transport (`devtools/jev.py:25`).
- **F-45 Two existing patterns already point the right way.** `adapters.factory(kind, name)` with the fail-closed closure check in `devtools/consistency.py` is a clean plugin seam and is used fully for the evaluator (`devtools/jev.py`, `Jev.__init__`). `devtools/diagnostics.py` reads its routes, actions, exclusions and byte budgets entirely from configuration (`Diagnostics.project`, `Diagnostics.recovery_actions`, `Diagnostics.guidance`); it is the template for policy as data that the rest of the runtime does not yet follow.

None of these is a defect in the delivered behaviour. They are the reasons a change of policy or infrastructure is currently expensive.

### 3.6 The retained toolkit

- **F-46 It is a different design of the same problem.** `legacy/agentscient/ops/` holds 44 conductor instruments (about 26,700 lines, the largest being `coordination.py` at 3,170 lines and `sessions.py` at 2,350), a dispatch launcher of 3,503 lines, task, harness, hook, telemetry and workflow sub-packages, CUE contracts, a testkit and roughly 700 KB of contract and doctrine pages. Its coordination model is participants with granted roles (`lead`, `coordinator`, `dispatcher`, `worker`, `verifier`, `legacy/agentscient/ops/conductor/tools/coordination.py:104`), assignments and messages as declared state machines, couriers, and dispatch jobs. The runtime's binding, work order and job model with service-owned live sessions is not a refactoring of it; `docs/extraction.md` says the two are "not interchangeable modules".
- **F-47 Much of it is superseded by its own documentation.** Hand grants and mandatory boundaries are retired (`legacy/agentscient/ops/devlane/hooks/CONTRACT.md:3-14`); the read-only MCP ruling was replaced by an acting plane; the Symfony-twin recommendations were withdrawn (`legacy/agentscient/ops/conductor/MCP-RESEARCH-2026-09-05.md:3-9`); the workflow engine `wf.py` and the board instrument were never built; the `fileset` snapshot mode is unimplemented; the retirement of dispatch and conductor is recorded as undecided (`legacy/agentscient/ops/devlane/workflow/checks/CONTRACT-ci.md:122-132`).
- **F-48 Its quality is uneven.** Duplicated process helpers across `session_adapters/__init__.py` and `claude.py`, several private copies of the refusal helper, two MCP argument validators, three `canonical()` implementations, modules loaded by file path across sub-packages (`legacy/agentscient/ops/devlane/dispatch/launch.py:70-85`), harness name tuples in at least six places, five files over 2,000 lines, and contract pages interleaved with session narrative.
- **F-49 Only a slice is certified.** CI runs the testkit tests and five conductor suites (`coordination`, `coordination_mcp`, `courier`, `session_adapters`, `machine`) through `scripts/check_legacy.py`; `legacy/README.md` states the rest "has not been certified as a complete application here".

Section 12 lists the ideas the proposal adopts from it and what it leaves.

### 3.7 Tests and CI

- **F-50 The suite is a serious characterization baseline.** A census runner that fails on uncollected, skipped or unexecuted tests in strict mode (`devtools/tests/run.py:53-61`, `:112-119`); a hermetic environment that strips credentials and redirects every home directory (`devtools/tests/support.py:18-41`); a real stdio MCP client (`devtools/tests/support.py:129-281`); synthetic Codex app-server, Grok ACP and Claude TUI harnesses that exercise the actual protocols (`devtools/tests/native_fixture.py`, `claude_fixture.py`, `co_fixture.py`), a plugin harness (`harness_plugin_fixture.py`) and a GitHub REST and GraphQL emulator (`devtools/tests/fixtures/github.py:23-345`). Boundary tests are calibrated against injected faults (`devtools/tests/test_fault_detection.py`).
- **F-51 It mixes mechanism and policy.** Of the 84 test files, 25 test mechanisms, 6 test only policy choices, 13 test integrations or operator scripts, and 40 mix the two, most of them mechanism-dominant tests whose fixtures bind the literal roles `co`, `xo`, `coder` and `audit` (`devtools/tests/fixtures/control.py:39-50`). Role names are already configurable in tests (`devtools/tests/test_role_bindings.py:54-130` runs a whole workflow with `custom_*` names); role behaviours and their authorities are not (`devtools/tests/test_structure.py:16-79`, `devtools/tests/test_authority.py:24-55`).
- **F-52 CI gates the right things and names what it does not verify.** Three jobs: static checks (Ruff with a calibrated security rule set, CUE export agreement for role packages and configuration, registry closure), the strict runtime suite with real tmux and abstract sockets and retained evidence, and the five toolkit suites. `.github/CI.md` states that authenticated vendor acceptance, Serena, Symfony, GitHub mutation and the evaluator endpoint are not verified and "not silently substituted with passing stubs".

Section 11 uses this classification to plan verification during and after the transition.

---

## 4. Goals, non-goals and assumptions

### 4.1 Goals

1. Coordination of agents across projects, tools, execution environments and organizational structures from one reusable core.
2. Every infrastructure choice (storage, transport, harness, host, agent API, integration, workspace, providers, evaluation, secrets, telemetry) replaceable behind a port with a contract test suite.
3. Every policy choice (organization, routing, workflow gates, capability catalog, runtime selection) replaceable behind a policy interface, with the common cases expressible as configuration.
4. Defaults that reproduce today's behaviour so ordinary use stays simple and the current tests remain meaningful.
5. Defined behaviour for running work when configuration changes.

### 4.2 Non-goals

- Rewriting the mechanisms in section 3.3.
- A general workflow language. Policies use closed vocabularies with enumerated actions and relations, not scripts.
- Distributed multi-host operation in the first release. The design does not preclude it (storage and locking are ports), but the default adapters stay single-host.
- Preserving the `legacy/` toolkit as runnable code.

### 4.3 Assumptions

- **A-1** Compatibility with Agentscient state formats and identifiers is valuable during the transition but not permanent. The proposal keeps a compatibility profile and a state migration path and expects the owner to decide when to drop them.
- **A-2** Python remains the implementation language and in-process Python plugins (module and attribute references, as in `adapters.json`) are an acceptable extension mechanism for the first release. Out-of-process extension through MCP servers is a second step.
- **A-3** The three-level work vocabulary (work order, job, assignment) is a good default but not the only shape Xorchestrate must support.
- **A-4** Human participants are first-class. Today they appear as `external` members and as the operator; the proposal treats "external" as a transport and harness choice, not a special participant kind.
- **A-5** Single-host, single-operator installations remain the primary deployment for the first release; file locks and SQLite remain the default adapters.
- **A-6** Software delivery through a code host remains the most common workflow but must be optional; coordination of report-only work with no repository must work with the integration port absent.

---

## 5. Target architecture

### 5.1 Overview

```
                 agents (any harness, any transport)        operators (CLI, operator API)
                        │                                            │
   ┌────────────────────▼───────────────────────┐    ┌───────────────▼───────────────┐
   │  Agent API adapters                        │    │  Operator CLI / API            │
   │  mcp_stdio (today) · mcp_http · http_json  │    │  init · status · doctor · co   │
   └────────────────────┬───────────────────────┘    └───────────────┬───────────────┘
                        │ typed requests, tool surface                │
   ┌────────────────────▼────────────────────────────────────────────▼───────────────┐
   │  KERNEL  (no I/O, no SQL, no subprocess, no vendor names)                        │
   │  model: Participant · Session · Binding · WorkItem · Message · Update            │
   │         Artifact · Event · Decision · Operation                                  │
   │  services: Coordination · Messaging · Reporting · Work · Sessions · Delivery    │
   │            Decisions · Onboarding · Diagnostics                                   │
   │  authorization: Organization × Capability catalog × Binding → permitted actions  │
   │  scope algebra: covers · same · parent · participant_of                          │
   └──┬──────────┬──────────┬───────────┬──────────┬───────────┬──────────┬──────────┘
      │ policies │          │ ports     │          │           │          │
   ┌──▼──────────▼──┐  ┌────▼─────┐ ┌───▼────┐ ┌───▼────┐ ┌────▼────┐ ┌───▼──────┐
   │ Organization   │  │ Storage  │ │Transport│ │Harness │ │Integr.  │ │Evaluation│ ...
   │ Routing        │  │ sqlite   │ │ codex   │ │ codex  │ │ github  │ │ jev      │
   │ Workflow gates │  │ postgres │ │ grok    │ │ claude │ │ gitlab  │ │ none     │
   │ Capabilities   │  │ memory   │ │ claude  │ │ grok   │ │ local   │ │ (judge)  │
   │ Runtime select.│  │          │ │ pull    │ │ extern.│ │         │ │          │
   └────────────────┘  └──────────┘ └────────┘ └────────┘ └─────────┘ └──────────┘
                        Workspace · Host (tmux, container) · Providers (symfony, serena, …)
                        Secrets · Telemetry · Clock/Ids/Process
   ┌──────────────────────────────────────────────────────────────────────────────────┐
   │  COMPOSITION: manifest → wiring; configuration layers; policy revisions; migrations│
   └──────────────────────────────────────────────────────────────────────────────────┘
```

Dependency rule: kernel depends on nothing above it; policies depend on the kernel model only; adapters depend on ports only; composition depends on everything and is the only place that names concrete classes. Section 11.3 proposes making this rule a test.

### 5.2 Kernel model

The kernel keeps today's entities and makes their relations explicit.

| Entity | Today | In the kernel |
| --- | --- | --- |
| Installation | state directory with `team.json` | `Installation` with participants, projects, organization and policy revisions. "Team" stays as the default display name. |
| Participant | `members` row (name, harness, workspace, native_id, state, desired, pid, generation) | `Participant` identity plus `Session` (generation, process handle, transport handle, desired and observed state). Harness and workspace move to the `RuntimeProfile` of a launch. |
| Binding | `member_bindings` | `Binding(participant, role, scope, reports_to, brief, incarnation, paused, launch_pending, assignment)`. Unchanged semantics. |
| Work | `work_orders`, `jobs`, `role_assignments`, `work_links`, `work_plans` | `WorkItem(kind, parent, fields, status, request_key)` under a configurable `WorkTopology`; default topology `work_order > job > assignment` with the current constraints. Delivery attributes (repository, branch, change reference) become typed `WorkLink`s instead of columns. |
| Scope | `(work_order, job)` pairs with `includes` and SQL fragments | `Scope(item)` plus a small algebra: `covers(a, b)`, `same(a, b)`, `parent(a)`, `participants(scope)`. Policies and storage both consume the algebra; only the storage adapter compiles it to a query. |
| Message | `messages` with delivery states | `Message(sender, recipient, body, category, scope, request_key, reply_to, delivery)` with the state machine of F-3 as an explicit type. |
| Update | `work_updates` | `Update(kind, text, scope, binding_snapshot, evidence, evaluation, capability_gap, request_key)`. |
| Artifact | `artifacts`, `artifact_sources` | Unchanged. |
| Event | `events` | Unchanged; event kinds become a registered vocabulary per module instead of free strings (today 43 distinct kinds are emitted from 20 modules). |
| Decision | `decisions` | `Decision(purpose, questions, answers, classification, policy_revision, context_fingerprint, state, arbiter)`; the evaluator becomes a port. |
| Operation | `operations` | Unchanged; the executor becomes an adapter of the host port. |

State machines are written down once, in the kernel, and tested as tables:

- Session: `stopped → starting → idle ↔ active → stopped | failed`, with `desired ∈ {running, stopped}` and `generation` fencing (today in `devtools/store.py:490-544`, `devtools/launch.py:254-297`, `devtools/native.py:59-63`).
- Delivery: `queued → delivering → submitted → acknowledged`; `delivering → uncertain` (transport error or crash); `queued → superseded` (newer direction); `uncertain → queued` (sender retry only).
- Binding: `absent → bound(incarnation n) → bound(n+1) | released`; `paused` and `launch_pending` are flags, not states.
- Work item: `open → in_progress → blocked → done | cancelled` (today `devtools/work.py:7`), with gate pipelines on `accept`, `submit`, `conclude`, `adopt`.

**R-11** Execute these machines with one declared-machine engine rather than scattered `UPDATE … WHERE state=` statements. The toolkit's state engine is the model: places and transitions declared as data with an actor class, an evidence duty, a failure class and an optional hold; a move is a compare-and-swap on the current place inside the unit of work, and the marking is append-only (`legacy/agentscient/ops/conductor/tools/machine.py:66-104`, `:278-350`). The kernel owns the declarations and the executor; the storage port provides the compare-and-swap primitive. This replaces the state updates spread across `devtools/store.py`, `devtools/launch.py`, `devtools/native.py` and `devtools/delivery.py` with one tested component and makes the tables above checkable against the code.

**R-1** Keep the three-level default topology and the current table shapes for them; generalize only the scope algebra and the delivery links. A fully generic work graph would cost clarity now and buy nothing until a second topology is needed (A-3). The topology is configuration, so a second shape can be added later without changing the kernel's interfaces.

### 5.3 Ports

Each port is a `typing.Protocol` in `kernel/ports.py`. Signatures below are sketches; Appendix A gives the compact list. The contract for each port includes behavioural rules the adapter must honour, and section 11.2 proposes a shared test suite that every adapter must pass.

**StoragePort.** The kernel's transactional needs, named explicitly rather than as SQL:

```python
class Storage(Protocol):
    def unit_of_work(self, *, exclusive: bool = False) -> ContextManager[UnitOfWork]: ...
    def migrate(self, steps: Sequence[MigrationStep]) -> None: ...
    def read_only(self) -> ReadView: ...

class UnitOfWork(Protocol):            # one transaction; exclusive maps to BEGIN IMMEDIATE today
    participants: ParticipantRepo       # get, list, upsert_session_fields(generation_check)
    bindings: BindingRepo               # get, upsert(binding) -> incarnation, release, descendants(scope, graph)
    work: WorkRepo                      # get, create_idempotent(key, payload), update, links
    messages: MessageRepo               # send_idempotent, claim_next(recipient, visibility, generation) -> Message | None,
                                        # mark(attempt, outcome), supersede(category, scope, before)
    updates: UpdateRepo                 # record_idempotent, latest(binding_snapshot, kind), query(visibility)
    artifacts: ArtifactRepo
    events: EventLog                    # append(actor, kind, data, scope)
    decisions: DecisionRepo
    operations: OperationRepo           # enqueue with one-active-per-scope guarantee
    def compile(self, visibility: Visibility) -> Query: ...   # adapter-specific
```

Rules: `claim_next` must mark the message `delivering` with a fresh attempt id in the same transaction it selects it (F-3). `upsert` on bindings must allocate the incarnation inside the transaction (F-4). Idempotent inserts must compare payloads and reject mismatches (F-2). `Visibility` is a kernel value object (scope, audience rules, categories) that the adapter compiles; the kernel never emits a query string. The SQLite adapter is today's `Store` plus the SQL from `control.py` moved into a `VisibilityCompiler`.

**TransportPort.** How a message reaches a participant's live conversation:

```python
class Transport(Protocol):
    name: str                                                  # recorded on delivery events
    def attach(self, session: Session, profile: RuntimeProfile) -> Conversation: ...

class Conversation(Protocol):
    async def deliver(self, rendered: Rendered) -> DeliveryOutcome: ...   # submitted | uncertain(error)
    def observe(self) -> AsyncIterator[Observation]: ...                  # usage, turn boundaries, native ids
    async def close(self) -> None: ...
```

Rules: `deliver` returns `submitted` only after the native protocol acknowledged the write; any exception after the write is `uncertain`, never a retry (today `devtools/native.py:298-329`). Default adapters: `codex_appserver` (turn/start toolOutput), `grok_acp` (session/prompt, interject), `claude_channel` (MCP notification), `mcp_pull` (external participants read their inbox through `message_inbox` and `message_wait`). A fifth adapter, `http_push`, is a representative addition in section 8.

**HarnessPort.** How a participant process is prepared and run. This port already exists in outline (`devtools/harnesses.py:1-17`); the proposal makes the built-in branching in `profiles.py` adapter-owned:

```python
class Harness(Protocol):
    name: str; vendor: str; supports_provider: bool; supports_initial_prompt: bool
    def prepare(self, launch: LaunchRequest) -> LaunchPlan: ...        # private dir, env, argv, api config, hooks
    def permitted(self, tool: str, arguments: dict, catalogue: Catalogue) -> bool: ...
    def hook(self, payload: dict, session: Session) -> HookDecision: ...
    def run(self, plan: LaunchPlan, session: Session, transport_factory) -> Awaitable[None]: ...
    def readiness(self, binary: str, probe, profile) -> dict: ...
    def retain_login(self, plan: LaunchPlan, generation: str) -> None: ...
```

**HostPort.** Where a process lives: `start(plan) -> ProcessHandle`, `status(handle)`, `read(handle, lines)`, `stop(handle, grace)`. Default `tmux` (today `devtools/terminals.py`); alternatives `subprocess`, `container`. The launch handoff (F-7) becomes a `HandoffPort` with the abstract-socket adapter as default, since a container host may need a different channel.

**AgentAPIPort.** The agent-facing surface: `serve(connection: ConnectionContext)`. The kernel exposes a **verb registry**: each service verb is registered once with its name, argument schema, mutation flag and the conditions under which it is visible. Today's `capabilities(spec)` lists in `control.py`, `commands.py`, `delivery.py`, `planning.py`, `decisions.py` and `github_tools.py` already have this shape; the registry makes them one table. An agent API adapter projects the registry into its protocol, asks `Authorization.tool_surface(binding)` for the visible subset and dispatches by lookup, which replaces the if-chain dispatcher (F-36). The toolkit's MCP projection is the precedent: a catalogue row per tool naming the core verb, and a composer that derives the wire schema from the verb's signature (`legacy/agentscient/ops/conductor/tools/coordination_mcp.py:103-124`, `:491-529`). No verb accepts role, participant, session or identity arguments; the binding supplies them. That is the runtime's rule today and the toolkit's `FORBIDDEN_ARGUMENTS` (`legacy/agentscient/ops/conductor/tools/coordination_mcp.py:100`); section 11.3 makes it a conformance check. Default adapter `mcp_stdio` (today `devtools/mcp.py`). The reduced JSON Schema validator (`devtools/api.py:371-411`) moves to the kernel as `ArgumentValidation`, because it is a mechanism all surfaces share.

**IntegrationPort.** Change hosting: open, read, diff, review, comment, threads, merge, CI status and failures, watches. "Change" generalizes PR and MR. Default `github`. Required capability flags (`supports_threads`, `supports_required_checks`, `supports_merge_methods`) let gates degrade explicitly rather than fail obscurely.

**WorkspacePort.** `ensure(work_item, policy) -> Workspace`, `lock(key)`, `changes`, `checkpoint`, `refresh`, `recover`, `submit`, `review_snapshot(revision)`, `retire`. Default `git_worktrees` (today `devtools/workspaces.py`, `devtools/orders.py`, `devtools/retirement.py`). The lock becomes part of the port so a Postgres or distributed deployment can supply advisory locks.

**ToolProviderPort.** Capability providers that contribute tools under declared prerequisites:

```python
class ToolProvider(Protocol):
    name: str
    def available(self, project: Project, binding: Binding) -> bool: ...     # prerequisites (image built, PHP installed…)
    def tools(self, binding: Binding) -> Sequence[ToolSpec]: ...
    def invoke(self, tool: str, arguments: dict, ctx: InvocationContext) -> Result: ...
    def skills(self) -> Sequence[str]: ...                                   # installed skill references
```

Symfony and Serena become providers. The role package groups `job_framework` and `work_order_framework` become `provider:<name>` groups.

**EvaluationPort.** `evaluate(request: TypedQuestions, sources, policy) -> TypedAnswers` with fingerprinting and usage. Default `jev_typesafe` (today `devtools/jev.py`); `none` returns `unavailable` so `decisions.failure_mode` applies. Event kinds become `evaluation_*`; the adapter name is a field.

**SecretsPort.** `read(reference) -> str`, `references() -> list` for redaction. Default `env_file` (today `devtools/credentials.py`).

**TelemetryPort.** `record(frame)`, `context()`. Default `jsonl` (today `devtools/telemetry.py`). Redaction stays in the kernel because it is a mechanism.

**ClockPort, IdsPort, ProcessPort.** Time, identifiers and process identity; the Linux adapter is today's `devtools/processes.py`. These exist so the kernel is testable without the host.

### 5.4 Policies

Policies are objects with small, closed interfaces. Each ships with a data-driven default implementation whose configuration reproduces today's behaviour, and each can be replaced by a Python implementation registered in the manifest when data is not expressive enough.

**OrganizationModel.** Answers the questions the code asks today by literal:

```python
class OrganizationModel(Protocol):
    def roles(self) -> Mapping[str, RoleDefinition]: ...                 # identity -> behaviour, package
    def may_report_to(self, role: str, superior_role: str | None, relation: ScopeRelation) -> bool: ...
    def authority(self, role: str, action: Action, relation: ScopeRelation) -> bool: ...
    def coordination_mode(self, plan: PlanShape) -> CoordinationMode: ...  # who coordinates jobs
    def max_per_scope(self, role: str, kind: str) -> int | None: ...       # e.g. one job coordinator
    def capacity_rules(self) -> CapacityRules: ...                         # reservations
    def interface_roles(self) -> Set[str]: ...                             # roles that never hold work authority
```

`Action` is an enumeration: `work.create`, `work.direct`, `plan.commission`, `plan.adopt`, `assignment.create`, `assignment.complete`, `job.accept`, `job.cancel`, `work.submit`, `work.conclude`, `session.steer`, `review.record`, `review.respond`, `decision.resolve`. `ScopeRelation` is `own`, `child`, `covering`, `any`. The default `chain_of_command` model is configured as:

```json
{
  "version": 1,
  "roles": {"co": "interface", "xo": "coordinator", "nco": "job_coordinator", "planner": "planner",
            "coder": "producer", "docs": "producer", "test_writer": "producer", "architect": "producer",
            "audit": "reviewer", "security_auditor": "reviewer", "adversarial_review": "reviewer",
            "research": "advisor", "arbitrator": "arbiter"},
  "reporting": [
    {"role": "coordinator",     "to": ["interface"],                 "scope": "work_order"},
    {"role": "job_coordinator", "to": ["coordinator"],               "scope": "job", "max_per_scope": 1},
    {"role": "planner",         "to": ["coordinator"],               "scope": "work_order"},
    {"role": "*",               "to": ["coordinator", "job_coordinator"], "scope": "covering"}
  ],
  "authority": {
    "work.direct": ["interface"], "plan.commission": ["coordinator"], "plan.adopt": ["coordinator"],
    "assignment.create": {"coordinator": "any", "job_coordinator": "own"},
    "assignment.complete": {"coordinator": "child_without_job_coordinator", "job_coordinator": "own"},
    "job.accept": ["coordinator"], "job.cancel": ["coordinator"], "work.submit": ["coordinator"],
    "work.conclude": ["coordinator"], "review.record": ["reviewer"], "decision.resolve": ["arbiter"]
  },
  "coordination": {"single_job": "coordinator", "multi_job": "job_coordinator_per_job"},
  "capacity": {"reserved": {"arbiter": 1}}
}
```

This document encodes exactly F-17, F-18, F-19 and F-29. Section 8.7 shows an alternative.

**RoutingPolicy.** Destinations and visibility:

```python
class RoutingPolicy(Protocol):
    def destinations(self, update: Update, sender: Binding, graph: BindingGraph) -> Sequence[Destination]: ...
    def visibility(self, binding: Binding) -> Visibility: ...          # what this binding may read and receive
    def reply_target(self, update: Update, original: Message | Update) -> Destination | None: ...
```

Default `reporting_chain` reproduces `Control._destination` and the four SQL fragments as a `Visibility` value: scope coverage, plus "job traffic is visible to job participants, command traffic only to its participants", plus readiness messages at work-order level. Alternatives: `job_broadcast`, `topics`, `direct_with_allowlist`.

**WorkflowPolicy.** Gate pipelines on transitions:

```python
class Gate(Protocol):
    name: str
    requires: Set[str]                                        # ports it needs: integration, workspace, evaluation…
    def check(self, transition: Transition, ctx: GateContext) -> GateResult: ...   # ok | blocked(reason, evidence) | unmeasured(reason)
```

A `GateResult` distinguishes `blocked` (the gate looked and the condition failed) from `unmeasured` (the gate could not look: a port absent, an evaluator unavailable, a credential missing). The workflow configuration states per gate whether `unmeasured` blocks or passes with a recorded gap; the default is to block, which is what `decisions.failure_mode` does for the evaluator today (`devtools/configuration.cue:43`). The distinction is taken from the toolkit's board contract, where a section is `measured`, `cached` or `unmeasured` and an absence is never read as a pass (`legacy/agentscient/ops/conductor/contracts/board.cue:145-169`). **R-13** applies the same rule to `monitor`, `evidence` and readiness output: every field that could not be observed says so instead of defaulting.

Default pipelines, assembled from today's checks: `assignment.complete` = [`result_retained`, `no_active_operation`, `editor_workspace_clean`, `completion_assessed(assignment)`]; `job.accept` = [`dependencies_done`, `assignments_complete`, `coordinator_reported_ready(if multi_job)`, `change_inspected_at_revision`, `managed_branch_matches`, `producer_workspace_clean`, `ci_passed`, `completion_assessed(job)`]; `work.submit` = [`jobs_settled`, `validation_passed`]; `work.conclude` = [`jobs_settled`, `no_active_operation`, `change_inspected`, `ci_passed`, `vendor_reviews_ready`, `completion_assessed(work_order)`]; `plan.adopt` = [`planner_result_current`, `mission_unchanged`, `omitted_jobs_cancelled`, `completion_assessed(assignment)`]. Each gate declares which ports it requires, so a deployment without an integration adapter simply cannot select `ci_passed` and the composition reports that at startup.

**CapabilityCatalog.** The role packages stay in CUE with their validator (F-9). Two generalizations: groups become named conditions (`bound`, `session`, `command`, `advice`, `job`, `work_order`, `provider:<name>`) evaluated by the kernel's `Authorization` rather than by the literal logic in `devtools/api.py:439-462`; and the validator's structural rules that encode policy (`devtools/role_packages.py:105-107` forbidding any other interface package shape, `devtools/role_packages.py:111-114`) move into the organization model's own validation.

**RuntimeSelectionPolicy.** `roles.json` and `vendors.json` stay as they are (`devtools/runtime.py`). The selection strategy (`ordered_preference`, or `semantic_selection` through the evaluation port) is the policy; the capacity and reservation logic is written once (F-37) and consumes `OrganizationModel.capacity_rules()`.

### 5.5 Application services

Services are the use cases, written against ports and policies. They are today's classes with their SQL and literals removed:

| Service | From | Responsibilities |
| --- | --- | --- |
| `Coordination` | `commands.py`, `planning.py`, `control.bind_in` | direct, bind, assign, appoint, steer, adopt, complete; consults `OrganizationModel` and `WorkflowPolicy` |
| `Messaging` | `store.send/inbox/acknowledge/take_delivery/retry` | queue, claim, outcomes, supersession; consults `RoutingPolicy.visibility` |
| `Reporting` | `control.report`, `control.work_context` | retain updates with evidence, route through `RoutingPolicy.destinations`, project context |
| `Work` | `work.py`, `orders.py` partly | topology, links, status |
| `Sessions` | `launch.py`, `standby.py`, `duty.py` | launch intents, generations, readiness challenges |
| `Delivery` | `delivery.py`, `orders.py` | review, accept, submit, conclude through gate pipelines and the integration port |
| `Decisions` | `decisions.py`, `assignment_decisions.py`, `completion.py`, `advice.py` | typed questions, classification, arbitration, freshness |
| `Onboarding` | `onboarding.py` | skills and instructions per role |
| `Diagnostics` | `diagnostics.py`, `recovery.py`, `capability_gaps.py` | guidance routes, recovery correlation, gap backlog |
| `Authorization` | `api._tools`, `require_capability`, scattered checks | tool surface and action permission from organization, catalog and binding |

**Failure contract.** The kernel keeps `Failure(category, reason, details)` (`devtools/failures.py:13-21`) and adds an optional `expected`, `found`, `needed` triple inside `details`, after the toolkit's one-line refusal wire (`legacy/agentscient/ops/conductor/tools/instrument.py:47-70`). Each agent API adapter renders it in its own protocol; for MCP that is today's `isError` plus `structuredContent` (`devtools/mcp.py:149-156`). The three authorization paths of F-40 collapse into `Authorization`, which is the only producer of `not_permitted` failures.

**Readiness contract.** A session's readiness is reported per dimension (process, profile, identity, participant, role, tools, ingress, response, each `ok`, `waiting`, `refused` or `unmeasured`), after the toolkit's session lever (`legacy/agentscient/ops/conductor/tools/sessions.py:105`), instead of one state. The runtime's challenge and receipt protocol (`devtools/duty.py`) stays as the `response` dimension; `monitor` can then say which part of a launch is late.

### 5.6 Default adapters

Every default adapter is existing code relocated: `storage/sqlite` (store, migrations, visibility compiler), `transports/{codex_appserver, grok_acp, claude_channel, mcp_pull}`, `harnesses/{codex, claude, grok, external}`, `hosts/tmux`, `handoff/abstract_socket`, `agent_api/mcp_stdio`, `integrations/github`, `workspaces/git_worktrees`, `providers/{symfony, serena}`, `evaluation/{jev_typesafe, none}`, `secrets/env_file`, `telemetry/jsonl`, `process/linux`.

### 5.7 Composition and configuration

**Manifest.** `adapters.json` grows into the composition manifest. It keeps the trusted `module:attribute` references and adds the port each adapter serves, the contract version it implements, and the policy selections:

```json
{
  "version": 2,
  "storage":     {"adapter": "sqlite"},
  "agent_api":   {"adapters": ["mcp_stdio"], "server_name": "agentscient_dev"},
  "transports":  {"codex": "codex_appserver", "grok": "grok_acp", "claude": "claude_channel", "external": "mcp_pull"},
  "harnesses":   {"codex": "xorchestrate.adapters.harnesses.codex:CodexHarness", "...": "..."},
  "host":        {"adapter": "tmux"},
  "integration": {"adapter": "github"},
  "workspace":   {"adapter": "git_worktrees"},
  "providers":   ["symfony", "serena"],
  "evaluation":  {"adapter": "jev_typesafe"},
  "policies":    {"organization": "chain_of_command", "routing": "reporting_chain",
                  "workflow": "code_delivery", "capabilities": "role_packages", "runtime": "ordered_preference"}
}
```

A `Composition` root resolves the manifest, instantiates adapters, verifies each against its port (as `devtools/harnesses.py:26-35` does for harnesses today), checks that every selected gate's required ports are present, and hands the kernel its dependencies. The manifest is trusted configuration, never an agent input (as `adapters.json` is today).

**Configuration layers.** The precedence `defaults → project → role → job` (`devtools/configuration.py:1-5`) is kept. Two changes: the single document is split by concern, and each policy or adapter validates its own section through a `ConfigSection` protocol (`schema()`, `validate(value, references)`, `merge(base, overlay)`), so the central validator no longer enumerates every field (F-35). Sections: `organization`, `routing`, `workflow`, `capabilities`, `runtime`, `providers.<name>`, `integration`, `evaluation`, `diagnostics`, `telemetry`. The Symfony diagnostic routes move to `providers.symfony.diagnostics`.

**Revisions.** Today's digests are kept and unified: catalog revision, configuration revision (`devtools/configuration.py:515`), runtime policy revision (`devtools/runtime.py:105`), package descriptor revision (`devtools/role_packages.py:194-198`), onboarding revision (`devtools/onboarding.py:113-117`). The composition computes one `PolicyRevision` (a digest over all sections plus the manifest) and the kernel records it on bindings at bind time, sessions at launch, operations at enqueue and decisions at request. Section 7 defines what each recorded revision means.

### 5.8 Package layout and dependency rules

```
xorchestrate/
  kernel/        model.py  ports.py  policies.py  authorization.py  scope.py  services/…
  policies/      organization/  routing/  workflow/  capabilities/  runtime/
  adapters/      storage/  transports/  harnesses/  hosts/  handoff/  agent_api/
                 integrations/  workspaces/  providers/  evaluation/  secrets/  telemetry/  process/
  composition/   manifest.py  config.py  revisions.py  wiring.py  migrations.py
  cli/           run.py  (operator commands: init, status, doctor, config, connection, evidence, co, watch)
  compat/        devtools namespace shims, agentscient profile, state import
```

Rules enforced by a conformance test (section 11.3): `kernel` imports only the standard library and itself; `policies` import `kernel`; `adapters` import `kernel.ports` and their own dependencies; only `composition` and `compat` import adapters and policies by name; `sqlite3` appears only under `adapters/storage/sqlite`; `subprocess`, `fcntl`, `socket`, `signal` appear only under `adapters/`; behaviour identifiers appear only under `policies/organization` and in configuration.

---

## 6. Extension mechanisms

| To add or replace | Do this | Nothing else changes |
| --- | --- | --- |
| A storage backend | Implement `Storage` and `UnitOfWork` (including `compile(Visibility)` and the migration runner); pass the storage contract suite; name it in the manifest | Kernel, policies, agent API, tests at kernel level |
| A transport | Implement `Transport`/`Conversation`; pass the transport contract suite (submitted versus uncertain semantics, observation stream); map a harness or participant to it in the manifest | Message state machine, routing, visibility |
| A harness | Implement `Harness` (prepare, permitted, hook, run, readiness, retain_login); register the factory | Launch handoff, generations, catalog gating |
| A process host | Implement `Host` (and a `Handoff` if the abstract socket does not apply) | Everything above the launch service |
| An agent API | Implement `AgentAPI.serve`; use `Authorization.tool_surface` and `ArgumentValidation` | Tool semantics; authorization |
| An integration (code host) | Implement `Integration` with capability flags; gates that need missing capabilities are rejected at composition | Delivery service, gate pipeline order |
| A tool provider | Implement `ToolProvider`; declare skills; declare prerequisites | Role packages reference `provider:<name>` groups by configuration |
| An evaluator | Implement `Evaluation`; map purposes to it | Decision classification and arbitration |
| An organization | Write an `organization` configuration document for the default model, or implement `OrganizationModel` for shapes the vocabulary cannot express | Routing, workflow, storage, transports |
| A routing rule | Select or implement `RoutingPolicy` | Organization, workflow |
| A workflow gate | Implement `Gate` and add it to a pipeline in `workflow` configuration | Everything else |
| A role identity | Edit the CUE catalog and regenerate (as today) | Everything else |

Plugin contract: every adapter and policy class declares `contract_version`; the composition refuses mismatches with a clear message. In-process Python is the first mechanism (A-2). Out-of-process providers are already possible for tools (Serena is an MCP server behind a gateway, `devtools/serena_provider.py`); the `ToolProvider` port should accept an MCP server definition as one implementation so new providers do not need Python at all.

---

## 7. Running work under configuration change

The runtime already records revisions and rechecks the current policy on every call (`docs/role-capability-packages.md`, "Current job/revision/role authorization uses the current catalog on each MCP call. A running managed session retains its launch settings"). The proposal states the rules once and applies them to every concern.

**Recorded revisions.** A binding records the `PolicyRevision` at bind time; a session records it at launch along with the resolved runtime profile; an operation records it at enqueue; a decision records it at request; an onboarding delivery records it in its receipt.

**Rules.**

| Rule | Meaning | Today's precedent |
| --- | --- | --- |
| Authorize with current policy | Every action and every tool surface computation uses the current `PolicyRevision`. Narrowing a role takes effect on the next call; nothing waits for a restart. | `devtools/api.py:479-508` recomputes `_tools` per call; `require_capability` reads the current catalog |
| Launch with current policy | New sessions resolve runtime profiles, harness, host and transport from current configuration. | `devtools/launch.py:107-134` |
| Run with the launch snapshot | A live session keeps its native settings, tool allowlist ceiling and transport until it stops. Expanded capabilities need a restart; narrowed capabilities are enforced server-side immediately. | `docs/role-capability-packages.md`, `devtools/profiles.py:189-207` (package ceiling) |
| Decide with the request snapshot, verify with the current one | A retained decision is reusable only while context and policy revision match; otherwise it is stale and a fresh decision is required. | `devtools/decisions.py:128-134` |
| Flag, do not break, invalid bindings | If a policy change makes an existing binding violate the organization (a role removed, a reporting rule changed), the kernel marks the binding `stale`, pauses new work actions for it, delivers a `direction` message asking the coordinator to refresh, and keeps reports and context readable. | `devtools/store.py:332-346` refuses to upgrade with an invalid coordinator binding; the proposal replaces refusal with flagging because an operator must be able to repair without downtime |
| Infrastructure changes require a controlled restart | Changing storage, host, agent API or transport adapters is a composition change. Running sessions are stopped through the normal stop path, state is migrated through the storage port's export and import, and sessions relaunch under the new composition. The kernel refuses to start if the manifest's storage adapter does not match the state directory's recorded adapter. | `devtools/launch.py:240-297` (stop semantics) |
| Schema migrations are per module and run once | Migration steps are registered by the module that owns the tables, ordered by a global sequence, and run by the first writer under an exclusive lock. Steps never consult policy; data repairs that today depend on the role catalog (`devtools/store.py:332-346`) become kernel checks at bind time. | `devtools/store.py:117-367` |
| Runtime profile changes apply to new launches | Model, reasoning, vendor availability. A retained per-assignment selection must be refreshed by its coordinator before relaunch. | `docs/shared-runtime-config.md`, `devtools/runtime.py:168-179` |
| Revision visible everywhere | `config_status`, `runtime_status` and `onboard` report the current revision; telemetry frames carry it; `monitor` shows a binding's recorded revision next to the current one so a coordinator can see drift. | `devtools/readiness.py` (`config`), `devtools/runtime.py:279-294` |

**Change classes and their effect on running work.**

| Class | Examples | New launches | Running sessions | Existing bindings and work |
| --- | --- | --- | --- | --- |
| Additive policy | new role identity, new tool in a package, new provider, new gate that only adds evidence | use it | unaffected until restart (ceiling) | unaffected |
| Narrowing policy | tool removed from a package, editable path narrowed, authority removed, gate added that can block | use it | calls outside the new surface fail with `not_permitted`; a `direction` message says the context changed | gates apply at the next transition; bindings that became invalid are flagged `stale` |
| Organization change | reporting rule changed, coordination mode changed | use it | as narrowing | bindings re-validated; invalid ones flagged; coordinators refresh through `orchestrate` |
| Routing change | destinations or visibility | immediate for new reports | immediate (visibility is computed per call) | queued messages keep their recorded recipients; nothing is re-routed retroactively |
| Runtime profiles | model, reasoning, vendor availability | use it | keep launch settings | retained selections must be refreshed |
| Infrastructure | storage, host, transport, agent API adapter | after restart | stopped and relaunched | preserved through export and import |

---

## 8. Representative changes

Each scenario names what changes, what remains unaffected, and how running work behaves. "Unaffected" is checkable: the conformance and contract tests in section 11 fail if a scenario needs edits outside the named places.

### 8.1 Infrastructure: replace SQLite with Postgres for a shared installation

- **Changes.** New `adapters/storage/postgres` implementing `Storage`, `UnitOfWork`, the repositories, `compile(Visibility)` and the migration runner. `exclusive` units of work map to `SELECT … FOR UPDATE` or serializable transactions where SQLite used `BEGIN IMMEDIATE`; the one-active-operation guarantee uses a partial unique index as today (`devtools/store.py:242-244`); JSON scope fields use `jsonb`. Workspace locks, which are file locks today (`devtools/workspaces.py:98-114`), come from the storage adapter's advisory locks when the `Workspace` adapter is configured to use them. One manifest line.
- **Unaffected.** Kernel services, policies, agent API, harnesses, transports, GitHub adapter, role catalog, tests at kernel and policy level. The storage contract suite runs against both adapters.
- **Running work.** Infrastructure class: stop sessions, export state through the SQLite adapter, import through Postgres, relaunch. Bindings, messages, updates and artifacts keep their identifiers.
- **Cost.** Honest port design: every place the kernel relied on SQLite semantics implicitly (same-transaction claim, `json_each` membership tests, `coalesce` in unique indexes) must be named in the port. That work is the point.

### 8.2 Infrastructure: add an HTTP push transport for an external agent service

- **Changes.** New `adapters/transports/http_push` implementing `Conversation.deliver` as a signed POST to a configured endpoint; `submitted` on a 2xx response, `uncertain` on timeout or 5xx, never an automatic retry. Participants with harness `external` can select transport `http_push` instead of `mcp_pull` per participant in the installation configuration.
- **Unaffected.** Message state machine, routing, visibility, idempotency, generation fencing (an external seat already has a generation boundary, `devtools/sessions.py:30-31`).
- **Running work.** A participant's transport is part of its launch snapshot; switching takes effect at the participant's next session.

### 8.3 Infrastructure: run managed sessions in containers instead of tmux panes

- **Changes.** New `adapters/hosts/container` implementing `Host`, and a `Handoff` adapter for the launch channel if the abstract Unix socket is not shared with the container (a stdin pipe or a mounted socket). Process identity for stop and recovery comes from the container runtime rather than `/proc`, so the `Process` adapter has a container variant.
- **Unaffected.** Sessions service, generations, harness adapters, MCP gate, catalog, policies.
- **Running work.** Infrastructure class; per-participant host selection means a team can move one role at a time.

### 8.4 Behaviour: require two independent reviews before a job is accepted

- **Changes.** New gate `independent_reviews(minimum=2)` under `policies/workflow/gates`, reading `review.record` updates from distinct reviewer participants at the inspected revision. Add it to the `job.accept` pipeline in the project's `workflow` section.
- **Unaffected.** Delivery service code, organization, routing, storage, GitHub adapter (the gate reads retained reviews, not the code host).
- **Running work.** Narrowing class: the gate applies at the next `job.accept`; jobs already accepted are untouched; coordinators see the new requirement in `monitor`.

### 8.5 Behaviour: broadcast job findings to every participant on the job

- **Changes.** Select `routing: job_broadcast` for the project. The policy's `destinations` fans out `finding` and `blocker` updates to all bindings covering the job in addition to `reports_to`; `visibility` is unchanged.
- **Unaffected.** Reporting service, organization, gates, storage. Idempotency keys already include the recipient (`devtools/delivery.py:283`).
- **Running work.** Routing class: immediate for new reports; queued messages keep their recipients.

### 8.6 Behaviour: run without semantic evaluation

- **Changes.** `evaluation: {adapter: none}` and `decisions.failure_mode: block` or `arbitrate`. The `completion_assessed` gates receive `unavailable` and follow the configured failure mode; the `semantic_selection` runtime policy is replaced by `ordered_preference`.
- **Unaffected.** Everything else, including the typed decision model, which remains available for a future evaluator.
- **Running work.** Decisions recorded under the old policy revision are stale and are not reused.

### 8.7 Organization: a flat team with one lead and no interface or job coordinator roles

- **Changes.** An `organization` document:

```json
{"version": 1,
 "roles": {"lead": "coordinator", "coder": "producer", "docs": "producer", "audit": "reviewer", "research": "advisor"},
 "reporting": [{"role": "*", "to": ["coordinator"], "scope": "covering"}],
 "authority": {"work.direct": ["coordinator"], "plan.commission": ["coordinator"], "plan.adopt": ["coordinator"],
               "assignment.create": {"coordinator": "any"}, "assignment.complete": {"coordinator": "any"},
               "job.accept": ["coordinator"], "work.submit": ["coordinator"], "work.conclude": ["coordinator"],
               "review.record": ["reviewer"]},
 "coordination": {"single_job": "coordinator", "multi_job": "coordinator"},
 "capacity": {"reserved": {}}}
```

  with a matching role catalog (five packages) and `runtime` preferences. The owner connects as the lead's external seat or appoints a lead agent; `direct` is a coordinator authority here, so no interface role exists.
- **Unaffected.** Kernel services (they ask the model; they do not know "XO"), routing, gates, storage, transports, agent API. The `arbiter` behaviour is absent, so decision policy must use `failure_mode: block`, which the composition checks at startup.
- **Running work.** Organization class: existing bindings are re-validated; a team mid-work under the chain-of-command model would see its `xo` and `nco` bindings flagged `stale` and would need a fresh `direct`. The proposal does not attempt automatic translation between organizations.

### 8.8 Organization: a human reviewer with acceptance authority

- **Changes.** Add role `reviewer_human` with behaviour `reviewer` and authority `job.accept` in the `organization` section; a participant with harness `external` and transport `mcp_pull` or `http_push` (section 8.2); a workflow pipeline for `job.accept` that includes `review_recorded_by(role=reviewer_human)`.
- **Unaffected.** Everything else. Human participants are ordinary participants (A-4).
- **Running work.** Narrowing class at the next acceptance.

### 8.9 Organization: several projects in one installation

- **Changes.** `team.json.project` (singular, `devtools/workspaces.py:28-33`) becomes `projects: {name: {...}}`; a work order is bound to a project at creation; `Workspace`, `Integration` and `ToolProvider` adapters are resolved per project; per-project `organization`, `routing` and `workflow` overlays are allowed in the configuration layers (`defaults → installation → project → role → job`).
- **Unaffected.** Kernel services (they already carry a scope; the project becomes the root of the scope tree), transports, harnesses, storage schema beyond one column.
- **Running work.** Additive class; the single-project form is the degenerate case with one entry.

---

## 9. Tradeoffs

| Choice | Benefit | Cost | Mitigation |
| --- | --- | --- | --- |
| Ports with contract suites instead of direct calls | Replaceable infrastructure; honest statement of transactional needs | Indirection; two implementations to keep green from the start (SQLite and memory) | The memory adapter doubles as the fast test double, so the second implementation pays for itself |
| Policy as closed-vocabulary data | Organizations and routing are configuration; auditable; no code execution from config | Some shapes will not fit the vocabulary | Every policy interface accepts a Python implementation registered in the manifest; the vocabulary grows only when two deployments need the same thing |
| Visibility as a kernel value compiled by storage | Authorization is one definition, not four SQL fragments | Each storage adapter writes a compiler; complex predicates may be slower than hand-tuned SQL | Keep the `Visibility` algebra small (scope coverage, audience, categories); benchmark on the SQLite adapter |
| Keeping the three-level work topology as default | No loss of clarity; current tests keep meaning | A second topology needs the configuration mechanism exercised for the first time | Make the topology a configuration document from the start even with one entry |
| Per-connection MCP processes retained | No daemon to operate; matches today's tested behaviour | Background duties (launch intents, watches, retirement) run in whichever process is connected | Keep as default; a daemon host adapter is an infrastructure change, not a kernel change |
| Flagging stale bindings instead of refusing upgrades | Operators repair without downtime | A flagged team can sit idle until someone refreshes it | `monitor` and `roll_call` surface stale bindings; a `direction` message is delivered automatically |
| In-process Python plugins first | Simple; matches `adapters.json` | Plugins run with service privileges | Manifest is trusted configuration; MCP-server providers give an out-of-process path for tools |
| Compatibility profile during transition | Existing teams keep working; tests stay valid | Two names for one thing (`devtools` shims, `agentscient_dev`) for a while | Time-box in the transition plan; make the server name a manifest field now |

---

## 10. Transition plan

The plan is a strangler sequence. Each phase ends with the existing suite green (`python3 -m devtools.tests.run --strict`) and with a measurable reduction in the coupling counts of Appendix C.

**Phase 0: freeze and measure.** Record the current test census as the characterization baseline. Add the architecture conformance test (section 11.3) in reporting mode, so the counts in Appendix C are printed on every run. Make `SERVER` a configuration value defaulting to `agentscient_dev` (`devtools/api.py:31`) and read it in the test fixtures that currently spell it (`devtools/tests/claude_fixture.py:33`, `co_fixture.py:28`, `test_readiness.py:65`, `test_native_boundaries.py:135-146`). Introduce the `xorchestrate` package with `compat/` re-exporting `devtools` names, so imports can move file by file.

**Phase 1: extract ports behind existing code, no behaviour change.**
1. `StoragePort`: wrap `Store` as the SQLite adapter; move `scope_sql`, `message_sql`, `update_sql`, `evidence_sql` into `adapters/storage/sqlite/visibility.py` behind a `Visibility` value object built in `control.py`. This removes the `store → control` cycle (`devtools/store.py:296`).
2. `TransportPort`: split `Native` into `codex_appserver` and `grok_acp` conversations and the `claude_channel` writer; the harness `run` loop stays and calls `Conversation.deliver`.
3. `HarnessPort`: move the branches of `_prepare_builtin`, `_permitted_builtin` and `_retain_login_builtin` (`devtools/profiles.py`) into the three harness classes.
4. `IntegrationPort`: name the `GitHub` methods the delivery service actually uses and type them; the adapter is unchanged.
5. `EvaluationPort`: wrap `jev.py`; rename event kinds with a compatibility alias.
6. `HostPort` and `HandoffPort`: wrap `terminals.py`, `bootstrap.py`, the socket code in `launch.py`.
7. Declared state machines (R-11): declare the session, delivery and binding machines as data and run them through one executor; rewrite the toolkit's `test_machine.py` against it.
Exit: the memory storage adapter exists and passes the same contract suite as SQLite; kernel-level tests run against it; the 56 SQL-using test files are reclassified as SQLite adapter tests and keep running unchanged.

**Phase 2: lift policy out of code.**
1. Introduce `OrganizationModel` with the `chain_of_command` default and replace the 122 behaviour-literal checks with model queries, module by module, starting with `control.bind_in`, then `planning`, `commands`, `delivery`, `workspaces.scope`, `operations.start`, `api._tools`. A golden-table test (section 11.2) asserts that the default model answers every (role, action, relation) exactly as the literals did.
2. Introduce `RoutingPolicy.reporting_chain` from `Control._destination` and the visibility fragments.
3. Introduce `Gate` and assemble the default pipelines from the checks in `delivery.py`, `orders.py`, `planning.py`; `CodexReviews.require_ready` becomes the `vendor_reviews_ready` gate.
4. Unify slot allocation (F-37) on `capacity_rules()`, and the three authorization paths (F-40) on `Authorization`.
5. Replace the if-chain dispatcher with the verb registry (section 5.3) and remove the upward calls into the API façade (F-41) by giving `Decisions.resume` and `steer_assignment` a kernel command interface to replay through.
6. Port the toolkit's refusal-table and mutant-calibration patterns (`legacy/agentscient/ops/testkit/cases.py`) for the golden tables.
Exit: Appendix C counts for behaviour literals outside `policies/organization` reach zero; no module below `adapters/agent_api` imports it.

**Phase 3: configuration by concern and composition.** Split `configuration.cue` into sections with per-section validators; move Symfony routes to the provider; introduce the manifest (version 2 of `adapters.json`) and the `Composition` root; compute and record `PolicyRevision`; implement stale-binding flagging; register migrations per module. Exit: `python3 -m xorchestrate.configuration --check` validates all sections; the compatibility profile reproduces today's effective configuration byte for byte in `config_status`.

**Phase 4: prove the seams.** Deliver at least one alternative per port that matters: Postgres or a second file-backed storage adapter, the `http_push` transport, the `flat_team` organization fixture, the `independent_reviews` gate, `evaluation: none`. Move Symfony and Serena into `providers/`. Exit: the scenarios of section 8 are executable tests.

**Phase 5: retire compatibility.** Rename identifiers (server name default, environment variables, review markers) with a deprecation window; drop the `devtools` shims; decide the fate of `legacy/` per section 12.

**Risks.** The suite's real-process fixtures are slow and environment-sensitive; keep them as the integration tier and grow a fast kernel tier on the memory adapter. Lifting the literals risks subtle changes in authorization; the golden table is the safeguard. The owner's compatibility needs (A-1) decide how long Phase 5 waits.

---

## 11. Verification

### 11.1 What the existing suite pins

The runtime suite is the characterization baseline. Its 84 files split into 25 mechanism tests, 6 policy-only tests, 13 integration or script tests and 40 mixed files (F-51). Mechanism tests must keep passing unchanged throughout the transition. Policy tests become tests of the default policy set and gain a second instance where an alternative policy is introduced. Integration tests stay with their adapters.

Mechanism anchors to keep verbatim include: generation fencing and replaced-client rejection (`devtools/tests/test_sessions.py:64-80`, `:169-172`); idempotent work creation and schema upgrade from the raw schema (`devtools/tests/test_work.py:60`, `:94-120`); four concurrent reports yielding one reference (`devtools/tests/test_control.py:275-278`); two concurrent decisions yielding one provider call and one arbitration (`devtools/tests/test_decision_lifecycle.py:237-241`); redaction never changing wire arguments and suppression on an unreadable key (`devtools/tests/test_telemetry.py:196`, `:210-219`); hook failure denying the native tool (`devtools/tests/test_live.py:197-198`); a stale start time never signalling a reused pid (`devtools/tests/test_launch.py:167-174`); a fourth registered harness with no built-in fallback (`devtools/tests/test_harness_plugins.py:73-80`); read-only `State` refusing writes and upgrades (`devtools/tests/test_state.py:19-62`); coordinator uniqueness enforced by behaviour rather than by a literal index (`devtools/tests/test_role_bindings.py:32-37`).

Policy anchors that become default-policy tests include: "Only XO" and "XO appoints" (`devtools/tests/test_structure.py:49-56`); CO's fixed tool set (`devtools/tests/test_authority.py:86-87`, `devtools/tests/test_commands.py:26`, `:45`); instruction text such as "one NCO per job" (`devtools/tests/test_onboarding.py:106-138`); the two vendor review comments required before conclusion (`devtools/tests/test_codex_reviews.py:54-75`); the maintained model lineup (`devtools/tests/test_runtime_defaults.py:19-33`); CI, unsubmitted work and target branch rejections (`devtools/tests/test_delivery.py:129-138`); the `dev` base and `wt-ag` parent (`devtools/tests/test_orders.py:46`, `:111`); the `work-order/` branch prefix (`devtools/tests/test_workspaces.py:149`).

### 11.2 New verification proportionate to the proposed qualities

| Quality | Verification |
| --- | --- |
| Replaceable storage | One storage contract suite (claim atomicity under concurrent claimers, idempotent insert semantics, incarnation allocation, visibility compilation equivalence, migration idempotence) run against SQLite and memory, later Postgres |
| Replaceable transport | Transport contract suite with a fault-injecting fake protocol: write succeeds then connection drops must yield `uncertain`; write fails before send must yield `uncertain` with the message still unacknowledged; no autonomous retry |
| Replaceable harness and host | Existing synthetic harness fixtures (`devtools/tests/native_fixture.py` and siblings) re-pointed at the ports; a host contract test for start, status, stop and ownership verification |
| Correct default policies | Golden decision tables: for every (role behaviour, action, scope relation) and every (reporting pair, relation), the `chain_of_command` model must produce the same answer as the pre-extraction code path; generated from the literals before they are removed |
| Alternative policies | The `flat_team` organization and `job_broadcast` routing fixtures exercised through the same kernel tests |
| Gate pipelines | Table tests per gate with a `GateContext` fake; pipeline order tests; composition rejects a pipeline whose gate requires a missing port |
| State machines | Property tests over random event sequences for delivery and session machines: no transition outside the table; `uncertain` never leaves without a sender retry; a superseded generation never claims |
| Configuration | Round-trip tests per section; revision changes when and only when a section changes; stale export detection as today |
| Running work under change | Scenario tests: narrow a package while a session is live and assert `not_permitted` on the next call plus a `direction` message; change a reporting rule and assert the binding is flagged `stale`; change a runtime profile and assert the live session keeps its snapshot |
| Compatibility | A fixture state directory from the current schema migrates and yields identical `config_status`, tool surfaces and routing decisions under the compatibility profile |

### 11.3 Architecture conformance

A test that parses imports and literals and asserts the dependency rules of section 5.8. It reports, then enforces, four counts: behaviour identifiers outside `policies/organization`, `sqlite3` or `.execute(` outside `adapters/storage`, `subprocess`/`fcntl`/`socket` outside `adapters/`, and function-level imports inside `kernel/`. Starting values are in Appendix C; the target for the first three is zero and for the fourth is zero because the kernel has no cycles to break.

### 11.4 Seam impact on the existing suite

| Seam | What breaks or must move | What survives | Handling |
| --- | --- | --- | --- |
| Storage port | 56 files and 9 fixture modules issue SQL. `test_state.py` asserts `sqlite3` exception types and WAL mode (`:19-62`); migration tests seed `PRAGMA user_version` and alter tables (`test_report_binding.py:147-153`, `test_role_bindings.py:32`, `test_github.py:244-246`, `test_capability_gap_storage.py:263`, `test_commands.py:395`, `test_orders.py:348`); `support.fixture_work` and `adopt_plan` write through `BEGIN IMMEDIATE` (`support.py:92`, `:106-115`); `test_write_boundaries.py:77-93` wraps `connect()` to intercept transactions. | Everything exercised through service classes. | Reclassify the SQL-using files as SQLite adapter tests and keep them unchanged. Grow a kernel tier on the memory adapter from the contract suite. Rewrite the two fixture helpers over service calls so they run on both adapters. |
| Configurable roles and organization | `devtools.control.ROLES` iteration (`test_onboarding.py:28-47`, `test_runtime.py:23`, `test_sessions.py:89`); literal `co`, `xo`, `nco` bindings in fixtures (`fixtures/control.py:39-50`, `fixtures/workspaces.py:54`) and in 63 files; authority semantics (`test_structure.py:16-79`, `test_authority.py:24-55`, `test_configuration.py:196-197`, `:314`, `test_role_packages.py:157`, `test_duty.py:47`). | Name configurability (`test_role_packages.py:84-131`, `test_role_bindings.py:54-130`, `test_co_configuration.py:66-75`, `test_diagnostics.py:315`); mechanism tests that only need a catalog with equivalent behaviours. | Fixtures select the `chain_of_command` policy explicitly; authority tests become golden tables for that policy; the `flat_team` fixture gets its own, smaller set. |
| Pluggable routing and delivery | GitHub pins: `test_delivery.py:125-139`, `test_orders.py:44-61`, `test_workspaces.py:149`, `test_codex_reviews.py:54-75`, `test_github.py` with `fixtures/github.py`, `test_feedback.py`, `test_watcher.py`, `test_structure.py:190-192`, the provider duck type in `fixtures/delivery.py:29-80`; routing through the binding chain (`test_authority.py:68`, `:107-108`, `test_control.py:179-219`). | Changed-revision rejection (`test_delivery.py:91`), review retry reconciliation (`:70`), lost-merge reconciliation (`test_orders.py:153`), pagination (`test_github.py:205-214`). | GitHub tests become `integrations/github` adapter tests and `code_delivery` workflow tests; routing tests become `reporting_chain` policy tests; the survivors move to the kernel tier. |
| Several agent APIs | `support.Client` and the 14 files using it; exact `tools/list` contents (`test_control.py:104-107`, `:126`, `:238`, `test_commands.py:26-57`, `test_live.py:41`); `notifications/tools/list_changed` (`test_control.py:235`); the Claude channel (`test_live.py:136-141`); the `structuredContent` failure envelope (`test_failure_transport.py:30-38`); the server name `agentscient_dev` and the `mcp__agentscient_dev__` prefix (`test_readiness.py:65`, `test_native_boundaries.py:135-146`, `claude_fixture.py:33`, `co_fixture.py:28`). | Round trips asserted through the Python `API` class (onboarding, readiness, fencing, redaction, upgrades). | These are `mcp_stdio` adapter tests and stay; the server name becomes a fixture parameter read from the manifest (Phase 0). A second agent API adapter gets the same contract suite with its own protocol fixtures. |

---

## 12. The retained toolkit

### 12.1 What it contains

`legacy/agentscient/ops/` is the conductor-lane toolkit from the historical branch. Its parts, by purpose:

| Part | Purpose | Notes |
| --- | --- | --- |
| `conductor/tools/instrument.py`, `laneconf.py`, `machine.py` | Foundation: refusal wire and exit classes, identity and session resolution, storage primitives; literal-only configuration with precedence and closure checks; a declared state-machine engine | The three pieces the toolkit's own README identified as "shared primitives" to consolidate on |
| `conductor/tools/coordination.py`, `courier.py`, `sessions.py`, `session_adapters/` | Coordination store (participants, assignments, messages, observations, decisions, resources), couriers, team and member lifecycle, native protocol adapters for Claude, Codex and Grok with framing and routing | The runtime's `native.py`, `rpc.py` and `profiles.py` are a later, smaller descendant of the same proof |
| `conductor/tools/mcp_transport.py`, `coordination_mcp.py`, `lane_mcp.py`, `dev_mcp.py` | MCP projections: acting plane composed from a verb table, read plane with resources, progressive discovery with `capability_search`, `capability_describe`, `capability_invoke` | The runtime's operator catalog uses the same three names (`devtools/api.py:291-307`) |
| `devlane/dispatch/`, `devlane/task/` | Dispatch launcher with snapshot per job and a validated record; task envelope and jobs as data | Superseded in direction by live sessions (`legacy/agentscient/ops/conductor/dev-mcp.md:9-12`) |
| `devlane/harness/` | Context and evidence law in CUE, isolation, staging, receipts, runaway detection | Specifications of what a delegated brief must contain and what counts as evidence |
| `devlane/hooks/`, `devlane/telemetry/`, `devlane/workflow/`, `devlane/infra/`, `devlane/providers/`, `devlane/fixtures/` | Hook registry as data and vendor normalization; usage, breaker, pulse and worth telemetry; workflow specification without its engine; Gitea infrastructure; Serena and Symfony providers; synthetic faulty applications | Workflow engine absent; providers and infrastructure product-specific |
| `capabilities/contracts/` | `#Capability`, `#Plan`, `#Outcome` CUE vocabularies | Design vocabulary, not consumed by code |
| `testkit/` | Hermetic `World`, spec-driven fakes with call logs, refusal tables, mutant calibration, census | Covered by CI through `scripts/check_legacy.py` |
| `conductor/*.md`, `docs/` | Contracts, levers and lessons | The explicit rationale behind most of the above |

### 12.2 Ideas the proposal adopts

| Toolkit idea | Source | Where it lands |
| --- | --- | --- |
| One refusal wire with `expected`, `found`, `needed` and fixed exit classes, enforced by a registry gate | `conductor/tools/instrument.py:47-70`, `conductor/instrument-conventions.md` | The failure contract of section 5.5; the per-adapter rendering rule |
| Absence discipline: `measured`, `cached`, `unmeasured`; readers never create stores; "could not look is never a pass" | `conductor/contracts/board.cue:145-169`, `docs/cue-sys.md` | `GateResult.unmeasured` and R-13 (section 5.4); `State` read-only views stay as they are (`devtools/store.py:86-108`) |
| Declared state machines run by one engine with actor class, evidence duty, hold and compare-and-swap moves | `conductor/tools/machine.py:66-104`, `:278-350` | R-11 (section 5.2) |
| Participant is not agent; authority is a chain, never an argument; binding configurations narrow but never widen | `conductor/tools/coordination_mcp.py:100`, `:431-447` | Confirms the runtime's bindings-as-trusted-data rule; becomes a conformance check (section 11.3) |
| Messages carry four separate facts (queued, delivered, acknowledged, resolved) and outstanding obligations | `conductor/tools/coordination.py` (`core_send`, `outstanding`), `conductor/coordination-lever.md` | The runtime already has the first three. `Messaging` gains an `outstanding` projection for coordinators over existing `reply_to` and `responding_to` data; no schema change |
| Transport adapter boundary and readiness in eight dimensions | `conductor/tools/sessions.py:105`, `session_adapters/__init__.py` | The `Transport` and `Harness` ports; the readiness contract of section 5.5 |
| MCP projection from a verb table; resources are reads by construction; progressive discovery | `conductor/tools/coordination_mcp.py:103-124`, `:491-529`, `dev_mcp.py:60-70` | The verb registry in section 5.3; the operator triple already exists in the runtime |
| Configuration closure: unread declarations and undeclared reads fail; literals in source are refused with an explicit exemption table | `conductor/tools/laneconf.py` (`closure`, `literals`) | Per-section validators gain a closure check; the literal rules of the conformance test (section 11.3) |
| Requested versus observed, applied versus observed, recorded on every launch | `devlane/dispatch/record.py:132-238`, `devlane/harness/context.cue` | The runtime already records `runtime_requested` and `runtime_observed` (`devtools/telemetry.py:130-189`); the session record adds applied versus observed isolation from the host adapter |
| Receipts versus attestations; only receipts are admissible proof | `devlane/harness/evidence.cue:52-206` | Artifact provenance kinds in `artifact_sources`: `receipt` (argv, cwd, revision, exit, digest) and `attestation` (claim with grounding). Gates such as `ci_passed` consume receipts; completion gates label attestation-only proof as such, which today's rubric already demands in prose (`devtools/configuration.cue:371`) |
| Hook registry as data with vendor envelope normalization | `devlane/hooks/boundaries.json`, `devlane/hooks/native.py` | A later phase: harness adapters load hook policy as rows; the runtime's fixed `mcp_only` policy becomes the default row set |
| Testkit patterns: hermetic world, spec-driven fakes with call logs, refusal tables, mutant calibration | `testkit/world.py`, `testkit/fakes.py`, `testkit/cases.py` | The contract suites and golden tables of section 11.2; the runtime's `test_fault_detection.py` already calibrates |
| Process doctrine: the contract arbitrates; the contract author is not the implementer; "the reusable asset is the extractor, not the contract" | `conductor/cdd-draft.md`, `docs/cue-sys.md`, `docs/mini-app-lessons.md` | Working practice for the port contract suites: written before the second adapter, by someone other than its author |

### 12.3 What the proposal does not adopt

- The dispatch job model (snapshot directory per job, prompt and output files, ladder stages with a fixed harness per stage). The runtime replaced it with live sessions and the toolkit's own later notes agree.
- The literal-only configuration grammar as the configuration format. The runtime's JSON with CUE-generated defaults and fail-closed validation is stronger; only the closure idea transfers.
- The `#Capability` and `#Plan` CUE vocabularies as runtime contracts. They remain useful reference for how gates and premises can be declared.
- The Rust kernel "open seat" (`legacy/agentscient/ops/conductor/stack.md:25-26`). Out of scope; a Python kernel behind ports keeps the door open.
- Product and host specifics: Symfony and PHP providers, vendor CLI flags and schema versions, `dev` and `master`, the original repository slug, host paths, `gh`-bound tools, the historical Owner, Lead, XO, Dispatcher, Builder and Verifier structure.

### 12.4 Recommendation for the directory

**R-12** Keep `legacy/` as read-only reference. Port, do not import: the five CI-covered suites are the specification for the state engine, the refusal wire and the transport framing, and should be rewritten against the kernel as part of Phases 1 and 2. Once an idea has been ported with its own tests, stop running the corresponding toolkit suite in CI, since it certifies the old code rather than the new. Retain the contract pages and CUE files listed in 12.2 as design records; the owner decides whether the remaining 170,000 lines stay in the repository or move to an archive.

---

## 13. Open questions for the owner

1. How long must Agentscient state formats, the `agentscient_dev` server name and the `AGENTSCIENT_*` environment variables keep working (A-1)? This sets the length of Phase 5.
2. Is a multi-host installation in scope for the next year (A-5)? If yes, Postgres and advisory locks move from Phase 4 to Phase 1 so the storage port is designed against two backends from the start.
3. Should non-software work (research programmes, operations runbooks) be a first-class target (A-3, A-6)? If yes, the `WorkTopology` configuration should ship with a second topology in Phase 3 rather than later.
4. Which of the toolkit's contracts (section 12) does the owner want preserved as specifications even if the code is retired?
5. Are human participants expected to use the same MCP tools as agents, or a dedicated operator surface (A-4)? This decides whether `mcp_pull` or an `http` agent API is the second AgentAPI adapter.
6. After the toolkit's ideas are ported with their own tests (section 12.4), should `legacy/` stay in the repository as reference, move to an archive, or keep running its five suites in CI?

---

## 14. Register

### Findings
F-1 to F-16 (mechanisms, section 3.3); F-17 to F-30 (policy fixed by code, section 3.4); F-31 to F-45 (structure, section 3.5); F-46 to F-49 (toolkit, sections 3.6 and 12); F-50 to F-52 (tests and CI, sections 3.7 and 11).

### Assumptions
A-1 compatibility is temporary; A-2 in-process Python plugins first; A-3 three-level topology as default only; A-4 humans are participants; A-5 single host first; A-6 code hosting optional.

### Recommendations
- **R-1** Keep the default work topology; generalize the scope algebra and delivery links (section 5.2).
- **R-2** Keep every mechanism in section 3.3 and relocate rather than rewrite it (sections 5.3, 5.5, 10).
- **R-3** Introduce the ports of section 5.3 with contract suites, starting with storage and transport (Phase 1).
- **R-4** Replace behaviour literals with an `OrganizationModel` whose default reproduces the current chain of command, verified by golden tables (Phase 2).
- **R-5** Express routing, workflow gates, capability conditions and runtime selection as policies with data-driven defaults (sections 5.4, Phase 2 and 3).
- **R-6** Split configuration by concern with per-section validators and one `PolicyRevision` recorded on bindings, sessions, operations and decisions (section 5.7, Phase 3).
- **R-7** Adopt the running-work rules of section 7, including stale-binding flagging instead of upgrade refusal.
- **R-8** Make the architecture rules executable (section 11.3) and run them from Phase 0.
- **R-9** Treat the toolkit as a source of specifications and test material, not as code to integrate (section 12).
- **R-10** Decide the open questions of section 13 before Phase 3, since they shape the configuration model.
- **R-11** Run the kernel's state machines through one declared-machine executor with compare-and-swap moves (section 5.2).
- **R-12** Keep `legacy/` as read-only reference; port its five certified suites against the kernel, then retire them from CI (section 12.4).
- **R-13** Distinguish `unmeasured` from negative results in gates, monitoring, evidence and readiness (section 5.4).

---

## Appendix A: Port signatures

```
Storage         unit_of_work(exclusive) → UnitOfWork · migrate(steps) · read_only()
UnitOfWork      participants · bindings · work · messages · updates · artifacts · events · decisions · operations · compile(Visibility)
Transport       attach(session, profile) → Conversation
Conversation    deliver(rendered) → submitted | uncertain · observe() · close()
Harness         prepare(launch) → LaunchPlan · permitted(tool, args, catalogue) · hook(payload, session) · run(plan, session, transports) · readiness(binary, probe, profile) · retain_login(plan, generation)
Host            start(plan) → handle · status(handle) · read(handle, lines) · stop(handle, grace)
Handoff         offer(payload) → token · accept(token) → payload
AgentAPI        serve(connection)
Integration     open_change · get_change · diff · review · comment · threads · merge · ci_status · ci_failures · watch · capabilities
Workspace       ensure · lock · changes · checkpoint · refresh · recover · submit · review_snapshot · retire
ToolProvider    available(project, binding) · tools(binding) · invoke(tool, args, ctx) · skills()
Evaluation      evaluate(questions, sources, policy) → TypedAnswers
Secrets         read(reference) · references()
Telemetry       record(frame) · context()
Clock / Ids / Process   now() · new_id() · identity(pid) · signal(handle, sig)

OrganizationModel   roles · may_report_to · authority · coordination_mode · max_per_scope · capacity_rules · interface_roles
RoutingPolicy       destinations · visibility · reply_target
WorkflowPolicy      pipeline(transition) → [Gate] ; Gate.check(transition, ctx)
CapabilityCatalog   packages · conditions · tool_surface(binding, context)
RuntimeSelection    resolve(role, scope) · validate(selection)
```

## Appendix B: Current modules to target packages

| Current | Target |
| --- | --- |
| `store.py` (schema, migrations, SQL) | `adapters/storage/sqlite/{schema,migrations,repos,visibility}.py` |
| `store.py` (session, message, work-context semantics) | `kernel/services/{sessions,messaging,work}.py` |
| `control.py` | `kernel/services/{reporting,coordination}.py`, `policies/routing/reporting_chain.py`, `kernel/scope.py` |
| `commands.py`, `planning.py`, `assignments.py`, `standby.py`, `duty.py` | `kernel/services/{coordination,sessions}.py`, `policies/organization/chain_of_command.py` |
| `delivery.py`, `orders.py`, `completion.py` | `kernel/services/delivery.py`, `policies/workflow/gates/*.py` |
| `decisions.py`, `assignment_decisions.py`, `advice.py`, `jev.py`, `jev_config.py` | `kernel/services/decisions.py`, `adapters/evaluation/jev_typesafe.py`, `policies/runtime/semantic_selection.py` |
| `api.py` (catalog, validation) | `kernel/authorization.py`, `kernel/validation.py` |
| `api.py` (dispatch), `mcp.py` | `adapters/agent_api/mcp_stdio.py` |
| `native.py`, `rpc.py`, `compat.py` | `adapters/transports/{codex_appserver,grok_acp,claude_channel}.py`, `adapters/harnesses/*` |
| `harnesses.py`, `profiles.py`, `console.py`, `console_settings.py` | `adapters/harnesses/{codex,claude,grok,external}.py` |
| `launch.py`, `bootstrap.py`, `terminals.py`, `processes.py`, `sessions.py` | `kernel/services/sessions.py`, `adapters/hosts/tmux.py`, `adapters/handoff/abstract_socket.py`, `adapters/process/linux.py` |
| `workspaces.py`, `retirement.py` | `adapters/workspaces/git_worktrees.py` |
| `github.py`, `github_http.py`, `github_tools.py`, `watcher.py`, `feedback.py`, `codex_reviews.py` | `adapters/integrations/github/*`, gate `vendor_reviews_ready` |
| `development.py`, `integration/symfony.py`, `serena_provider.py`, `serena/`, `process_observation.py`, `process_report.py` | `adapters/providers/{symfony,serena}/*` |
| `configuration.py`, `configuration.cue`, `role_packages.py`, `role-packages.cue`, `role_behaviors.py`, `role_views.py`, `runtime.py`, `adapters.py`, `consistency.py` | `composition/{config,manifest,revisions}.py`, `policies/capabilities/role_packages.py`, `policies/runtime/ordered_preference.py` |
| `telemetry.py`, `credentials.py`, `failures.py`, `diagnostics.py`, `recovery.py`, `recovery_actions.py`, `capability_gaps.py`, `onboarding.py` | `adapters/telemetry/jsonl.py`, `adapters/secrets/env_file.py`, `kernel/{failures,services/diagnostics,services/onboarding}.py` |
| `run.py`, `readiness.py`, `scripts/` | `cli/` |

## Appendix C: Coupling inventory at the PR head

Behaviour-identifier literal checks (`adapter(...) ==`, `!=`, `in`, `not in`): 122 in 21 modules. Largest: `control.py` 22, `commands.py` 22, `planning.py` 19, `workspaces.py` 8, `assignment_decisions.py` 7, `delivery.py` 5, `api.py` 5. `names_for_adapter(...)` calls: 23 in 10 modules.

Direct SQL statements (`.execute(` or `.executescript(`): 515 in 31 modules. Largest: `store.py` 107, `planning.py` 38, `commands.py` 38, `control.py` 35, `decisions.py` 32, `delivery.py` 30, `standby.py` 28, `duty.py` 23, `capability_gaps.py` 23, `work.py` 22.

Modules importing the GitHub provider: 12 (`api`, `codex_reviews`, `delivery`, `development`, `feedback`, `github`, `jev`, `operations`, `orders`, `readiness`, `watcher`, `workspaces`). Modules referencing Serena, Symfony, PHP or Composer: 14. Modules referencing Jev or TypeSafe: 10.

Function-level imports used to break cycles: 273 across the package (`commands.py` 26, `launch.py` 25, `api.py` 20, `profiles.py` 15, `decisions.py` 14, `control.py` 12).

Distinct event kinds emitted: 43, as free strings. Tables: 33.

---

Document finalized 2026-10-23 04:17 UTC. Analysis and proposal only; no repository changes were made.
