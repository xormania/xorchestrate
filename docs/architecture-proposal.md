# Xorchestrate architecture proposal

Revision 2. Prepared 2026-10-23 01:44 UTC; revised 2026-10-23 03:34 UTC. Appendix C lists the substantive changes in this revision.
Basis: [xormania/xorchestrate#1](https://github.com/xormania/xorchestrate/pull/1) at head `af2d52f` (branch `feat/extract-agentscient`, 1,212 files, +281,511 lines), read in full for the active runtime and by survey for the recovered toolkit.
Status: proposal for the owner's evaluation. Nothing here is implemented.

Every statement is tagged by kind:

- **Finding** — observed in the PR's code, tests, configuration or documentation, with a path and line range from the PR head.
- **Assumption** — something I could not confirm from the PR and have taken as given; each one is revisable.
- **Recommendation** — a design choice I propose; the owner decides.

File references are relative to the PR head. Line ranges were checked against that revision.

---

## 1. Summary

**Finding.** PR1 lands a working, well-tested coordination runtime (`devtools/`, about 42k lines of Python including tests, standard library only) plus an unused earlier toolkit (`legacy/agentscient/`, 921 files) and the original CI (`reference/`). The runtime already has several qualities the goal asks for: a trusted adapter manifest with factory loading, a validated role catalog expressed as data, layered fail-closed configuration, durable idempotent messaging, generation-fenced sessions, credential-redacting telemetry, and a test runner that refuses silent skips. The PR's own documentation is candid that storage, routing and organization are not yet extension points (`docs/extraction.md`, "Retained assumptions").

**Finding.** The runtime's *values* are configurable; its *composition and behavior* are not. The organization model (CO → XO → NCO → specialists), the workflow gates, message routing and visibility, storage (SQLite with SQL in about 30 modules), the delivery provider (GitHub), the terminal (tmux), the evaluator (Jev/TypeSafe), the source-editing provider (Serena/Docker) and the product framework (Symfony/Maker/Mate) are fixed in code and reach into the core. There are 128 branches on role behavior across 21 modules, the schema migration enforces an organizational rule, and configuration files are read live on every request so an edit takes effect, or breaks requests, at the moment of the write. This is the accumulation pattern the goal warns against, already in progress.

**Recommendation.** Keep the runtime's correctness guarantees and restructure around a small **kernel** (durable model, request pipeline, capability registry), a set of **ports** each justified by a concrete need, **extensions** that own their state, migrations and declared assumptions, and **presets** that compose them. The current Agentscient behavior becomes one preset, `chain_of_command`, offered as a starting point rather than a compatibility promise; a `basic` preset (SQLite, stdio MCP, polling inbox, one lead plus workers, no tmux, no GitHub, no evaluator) is the simple default. Configuration becomes something the owner *applies*, with the effect on running work computed and shown before it happens. The migration is a strangler sequence in which kernel guarantees stay tested throughout while inherited policies are triaged, moved into the preset's own suite, and changed deliberately with a record of the decision.

---

## 2. What PR1 contains

**Finding.** Layout and size at the PR head:

| Area | Files | Role in PR1 |
| --- | --- | --- |
| `devtools/` | 206 | Active runtime: MCP server, native session drivers, messaging, work records, role packages, Git/GitHub services, telemetry, tests |
| `.agents/skills/` | 20 | Role instruction files the runtime delivers at onboarding; six are Symfony/Mate framework skills |
| `legacy/agentscient/` | 921 | Earlier conductor/courier/dispatch toolkit, about 218k lines; not imported by the runtime |
| `reference/agentscient-dev/` | 23 | Original CI and guidance |
| `docs/`, root docs | 13 | Extraction map, operator and role documentation |
| `extraction-manifest.json` | 1 | Provenance for 1,208 files: 1,196 copied, 12 adapted, 4 new |

**Finding.** CI (`.github/workflows/ci.yml`) has three jobs: static checks (ruff, security-lint calibration, CUE export drift, adapter consistency), the full runtime suite under tmux with strict census, and selected legacy suites run from a temporary Git repository. The PR body reports 825 runtime tests and 285 legacy tests executed with zero skips.

**Finding (verified locally, 2026-10-23).** On the PR tree with Python 3.13: `python3 -m devtools.consistency` passes and reports 25 managed tools, 3 operator meta-tools and 51 operator capabilities; a five-module subset (`test_state`, `test_role_packages`, `test_configuration`, `test_runtime`, `test_work`) runs 62 tests green with 2 skips for optional tooling. I did not run the tmux or legacy suites.

**Assumption.** The PR's CI run on the final revision passed as described in its body. I did not re-run it.

---

## 3. Evaluation of the current design

### 3.1 Capabilities worth retaining

**Finding.** These mechanisms are sound and tested. Each row names where the behavior lives; §4 classifies which of them are guarantees the kernel must keep and which are policies a preset may change.

| Capability | Where | Why it matters |
| --- | --- | --- |
| Trusted adapter manifest with `module:attribute` factories, validated names, duplicate-key rejection | `devtools/adapters.py:20-71`, `adapters.json` | Already the right shape for a plugin registry; today used for harness, role and evaluation kinds only |
| Role packages as validated data, exported from CUE, with tool ceilings, path ceilings, skill references and live projections | `role_packages.py:58-128`, `role-packages.cue`, `role_views.py` | Authority as data, not code; `RoleSet`/`RolePaths` avoid duplicated role lists |
| Harness adapter contract (prepare, permitted, hook, run, command, channel, retain_login, readiness) | `harnesses.py:1-35` | A real port, documented in the module docstring |
| Layered configuration: CUE defaults → project → role → job, fail-closed, duplicate keys rejected, credential references never values | `configuration.py:304-366, 425-516` | The precedence model and "reference, never value" rule are worth generalizing |
| Durable idempotent messaging with explicit delivery states (`queued`, `delivering`, `submitted`, `uncertain`, `superseded`) and sender-only retry | `store.py:546-692` | Honest about transport uncertainty; the claim-in-transaction pattern in `take_delivery` prevents a retiring process consuming a replacement's messages |
| Generation fencing of sessions: per-member lease, launch handoff over a one-use abstract socket, no credential-bearing launch files | `sessions.py`, `launch.py:179-237`, `bootstrap.py` | Correct answer to the replace-a-stuck-agent problem |
| Request-key idempotency on work orders, jobs, links, reports, reviews | `work.py:52-112`, `control.py:618-727`, `delivery.py:218-288` | Lets agents retry safely |
| Append-only event log with work/job scoping and credential filtering | `store.py:436-453`, `telemetry.py:18-57` | Evidence trail; redaction applied uniformly |
| Full-duplex JSON-RPC helper with tracing | `rpc.py` | Small and reusable |
| Stdio MCP server with concurrent calls, size-bounded frames, structured failure metadata | `mcp.py:17-238`, `failures.py` | Protocol edge is thin and testable |
| Test census runner that fails on skips and unexecuted tests | `devtools/tests/run.py` | Keeps "all passed" honest |
| Real-protocol test client over stdio MCP | `devtools/tests/support.py:129-260` | Contract-level testing without mocking the transport |
| Workspace isolation: one worktree per job, serialized Git under `fcntl` locks, immutable audit snapshots | `workspaces.py:98-258`, `delivery.py:40-82` | Worth keeping as an extension |
| Capability-gap reporting as a first-class record | `capability_gaps.py`, `control.py:20-26` | A feedback channel from agents to the owner about missing tooling |

### 3.2 Where policy is hardwired

**Finding.** The following concerns are fixed in code. Changing any one of them today means editing core modules, not configuration.

| Concern | Hardwired where | Evidence | Consequence |
| --- | --- | --- | --- |
| Organization structure | `control.py:179-235` (`bind_in`), `commands.py:299-350, 385-536, 615-667`, `role_packages.py:167-179` | CO must have no superior; XO reports to CO; NCO reports to XO with a job; Planner reports to XO without a job; one NCO per job; `assignable_roles` excludes `co`, `xo`, `arbitrator`, and for NCO also `planner`, `nco` | A different team shape (flat lead + workers, two reviewers, no XO) is not expressible |
| Binding shape | `store.py:152-156` (`member_bindings`: one row per member, one `reports_to`), `store.py:157-162` (`standby_bindings.reports_to UNIQUE`), `commands.py:155-165` (recursive descent over `reports_to`), `control.py:208-214` (cycle check) | Every member has at most one binding and at most one superior; the team is a tree by construction | Matrix guidance, peer groups and shared specialists cannot be represented |
| Role behaviors | `role_behaviors.py` (11 fixed ids), 128 `adapter(...) ==`/`in` branches across 21 modules (`control.py` 22, `commands.py` 22, `planning.py` 19, `workspaces.py` 8, …) | Role *names* can be re-mapped in the catalog; role *semantics* cannot be added without touching these modules | A new behavior such as "release manager" requires edits in a dozen files |
| Tool availability rules | `api.py:435-470` (`_tools`) | Which package groups unlock depends on behavior id, project configuration, development configuration and job presence, in code | Adding a new tool group means editing the gating function |
| Message routing and visibility | `control.py:108-153, 502-567`; also `github.py:921-932`, `operations.py:275-289`, `delivery.py:270-286`, `duty.py`, `standby.py` | Recipient resolution branches on behavior in at least six modules; message category and supersession are raw SQL updates in five (`commands`, `control`, `delivery`, `duty`, `standby`); visibility is SQL text composed in the control module and consumed by `store.take_delivery` (`store.py:636-645`) | Routing policy and storage are mutually dependent; `store` imports `control` |
| Transport | `native.py:38` (only `codex`, `grok` protocols), `native.py:374-410`, `mcp.py:52-76` (Claude channel) | Per-protocol delivery is a branch inside one class; Claude delivery lives in the MCP server | A fourth transport means editing `Native` and `serve` |
| Harness support | `profiles.py:159-186, 234-346` (`Supported harnesses: codex, claude, grok`), `compat.py`, `native.py:96-197`, `console.py:88-107` | `BuiltinHarness` delegates to `_prepare_builtin`, which branches on harness name; a third-party harness cannot reuse it. The manifest path is a module constant; the plugin test extends it by patching `adapters.MANIFEST` and `role_packages._catalog` (`tests/test_harness_plugins.py:31-39`), so there is no per-team manifest | The adapter contract exists but the shipped implementations are not self-contained |
| Storage | `store.py` (SCHEMA_VERSION 18, one `upgrade()` of 250 lines, about 40 tables); SQL executed in about 30 modules (`planning.py` 38 calls, `commands.py` 38, `control.py` 35, `decisions.py` 32, `delivery.py` 30, …) | No repository boundary; callers pass raw `sqlite3` connections | Replacing SQLite is a rewrite of most modules |
| Workflow gates | `delivery.py:295-426` (`_finish_job`), `commands.py:626-667` (`_conclude`) | CI must pass, workspace must be clean, NCO must have reported, completion evaluation must pass, then merge; inline | No way to drop or add a gate per project |
| Delivery provider | `delivery.py:11` imports `GitHub` directly; `jobs.pull_request` column (`store.py:64-70`); `github.py` 970 lines | GitHub is the only way to review, merge, or watch CI | GitLab, Gerrit, or "local branch only" require schema and module changes |
| Evaluator | `configuration.cue` requires `connection`; `validate_connection` (`configuration.py:129-153`) requires an installed evaluation adapter and an HTTPS endpoint even when all evaluator features are off | Every team must declare a TypeSafe endpoint | The optional sophistication is not optional at the configuration level |
| Terminal | `terminals.py`, `launch.py:208`, docs `tmux-control.md` | tmux is the only session container | Basic use on a host without tmux is impossible for managed sessions |
| Source-editing provider | `native.py:54-56, 119-136`, `profiles.py:298-302`, `control.py:413-421` | Serena/Docker is referenced from native transport, profile preparation and the work-context brief | The core knows a specific provider's server name and tool |
| Product framework | `role-packages.cue:78, 171-185`, `assignment-policy.json`, `development.py`, `integration/symfony.py` | Framework skills and editable paths are Symfony-specific (`src/`, `composer.json`, `phpunit.dist.xml`) in the shipped catalog | A non-PHP project ships with the wrong defaults |
| Platform | `fcntl` in 8 modules, abstract Unix sockets, `/proc` process identity, `pidfd` | Linux/WSL2 only | Not a problem for the owner's environment, but it is an undeclared assumption of the kernel |
| Naming | `api.py:31` (`SERVER = "agentscient_dev"`), `compat.py:40-43` (Claude readiness regex matches that literal), `AGENTSCIENT_*` env vars in 4 modules, `agentscient` in 14 runtime modules | The product name is part of the protocol | Renaming is a cross-cutting change; the PR retains it deliberately |

### 3.3 The accumulation pattern, concretely

**Finding.** Several local decisions have already become enforced requirements:

1. **Schema migration enforces organization.** `store.upgrade()` refuses to migrate a team if an NCO-behavior member is bound without a job or two NCOs share a job (`store.py:332-346`). A storage upgrade now depends on the organization model.
2. **Hook policy is an invariant, not a setting.** A role package is rejected unless its hooks are exactly `[PreToolUse, PostToolUse, SessionStart, Stop]`, policy `mcp_only`, `native_delegation: false` (`role_packages.py:97-99`); the CUE schema fixes the same values (`role-packages.cue:20-24`). The field exists but cannot vary.
3. **The CO package must be an interface.** Its command tools must be exactly `[direct, monitor, roll_call]` and nothing else may be declared (`role_packages.py:105-107`). The catalog format suggests freedom the validator removes.
4. **Capacity reservation logic is duplicated.** Arbitrator reservation is computed in `runtime.allocate` (`runtime.py:246-254`) and again in `commands._slot` (`commands.py:177-204`), with different data sources (runtime policy vs fixed team members).
5. **Five overlapping authorization controls.** A tool call is checked by the package ceiling (`require_capability`), the gating function (`api._tools`), the per-call schema validator, the native hook (`profiles._hook_builtin`), and per-protocol native denial (`native.request`). Each was added for a reason; together they make "why was this denied?" a five-place question.
6. **Evaluator configuration is mandatory.** See the Evaluator row above. The `features` switches turn behavior off, but validation still demands the connection.
7. **Exact key-set validators.** `runtime.validate`, `configuration._validate` and `role_packages.validate` all require exact field sets. Fail-closed is right, but every new field requires coordinated edits to CUE, validator and docs, which discourages adding configuration.
8. **Fixture defaults diverge from production defaults.** Tests disable selection, filtering and completion by default (`tests/support.py:44-62`) because the production defaults require an evaluator. The test suite therefore exercises a configuration the product does not ship.
9. **Dynamic seat naming is a string convention.** Runtime-allocated members are named `runtime-%` and found by `LIKE` (`runtime.py:258-271`).
10. **A second permission matrix.** `operations.py:29-39` decides which behaviors may run which long operations in code, alongside the package ceiling it then also checks.
11. **The evaluator knows the command set.** The decision engine lists the tools it may replay after arbitration (`decisions.py:35`), coupling an optional extension to the core verbs.
12. **SQLite dialect everywhere.** `BEGIN IMMEDIATE` appears in 18 modules and `json_each`/`json_extract` in 11, so the storage engine's dialect is part of most modules' code.
13. **Any policy edit invalidates every retained runtime selection.** The selection revision is a digest over the whole policy, availability, catalog and adapter registry (`runtime.py:105, 168-179`), so adding a profile for one role blocks relaunch of every assignment until each is re-selected.

None of these is wrong in isolation. Together they show controls accreting where the architecture offers no designated place for them.

### 3.4 Dependency structure

**Finding.** Import analysis of the 60 non-test modules:

- Highest fan-in: `store` (42 importers), `role_packages` (32), `control` (23), `workspaces` (20), `telemetry` (19), `configuration` (15), `work` (14), `api` (14).
- Highest fan-out: `api` (27 internal imports), `commands` (18), `decisions` (17), `advice` (15), `workspaces` (15), `launch` (14), `control` (14).
- Cycles are resolved with function-local imports rather than structure: `store` imports `control`, `duty`, `standby`, `sessions`, `telemetry` inside functions; `api` is imported by `native`, `profiles`, `decisions`, `advice`, `serena_provider`, `diagnostics`, `configuration`, all of which `api` itself reaches. Sixty-plus function-local imports exist for this reason.
- There is no composition root. `API.__init__` (`api.py:414-428`) constructs eight services directly from a directory path; module-level constants locate `adapters.json`, `role-packages.json`, `configuration.json` relative to `__file__`; `api.invoke` (`api.py:618-749`) is a 130-line name-dispatch chain.

**Finding.** The runtime uses only the standard library (no third-party imports in `devtools/`). That is an asset worth keeping for the kernel.

### 3.5 Product and environment coupling

**Finding.** Token counts in `devtools/*.py` (non-test): `github`/`GitHub` in 15 files, `serena`/`Serena` in 12, `codex` in 10, `jev`/`Jev` in 8, `grok` in 5, `claude` in 7, `Symfony`/`Maker`/`Mate` in 4, `tmux` in 3, `agentscient` in 14. The extraction kept these on purpose to avoid a simultaneous behavioral migration (`docs/extraction.md`, "Changes made for this repository"), which was the right call for an extraction PR.

### 3.6 Tests as evidence

**Finding.** 84 test files with about 850 test methods. Strengths: the census runner; a real stdio MCP client fixture used by 14 files; 46 files exercise the `API` contract directly; synthetic harness fixtures for Codex, Claude and Grok protocols; fixture environments that never inherit a developer's credentials.

**Finding.** What the tests pin, by file count: 55 execute SQL directly and 53 open `store.connect()` to arrange state; 18 reference the SQLite file or `PRAGMA user_version`; 15 contain `PRAGMA` or `CREATE TABLE`; 10 import `SCHEMA_VERSION`; 27 assert on event kind strings (`session_starting` in 11, `native_usage` in 7, `skill_loaded`, `jev_evaluation`, `onboarding_delivered`, …); 20 reference `reports_to`; 7 reference tmux; 5 the `agentscient_dev` server name; 2 assert Serena's exact Docker mount strings (`test_coordination.py:29-31`); `patch.object` is used 293 times, often on module internals such as `role_packages._catalog` and `adapters.MANIFEST`. The main coordination fixtures (`tests/fixtures/coordination.py`, `support.adopt_plan`) encode the CO/XO/NCO protocol.

**Finding.** The suite is therefore three suites mixed together: tests of guarantees the system must keep, tests of the current organization and workflow, and tests of implementation details. §4 separates them.

### 3.7 The recovered toolkit

**Finding (survey, spot-checked).** `legacy/agentscient/` is an earlier, differently shaped generation of the same system: about 218k lines in total (Python 176k, roughly two thirds of it tests), organized as many single-purpose CLI "instruments" sharing one refusal wire, plus a generic SQLite state engine, with roles `lead`, `coordinator`, `dispatcher`, `worker`, `verifier` (`legacy/agentscient/ops/conductor/tools/coordination.py:104`). It shares SQLite, tmux, Serena and per-harness adapters with `devtools/` but no code; `docs/extraction.md` is right that the two are not interchangeable. The hardwiring pattern this proposal targets is visible there too: the harness list is repeated in five places, and `ops/devlane/dispatch/CONTRACT.md` records it.

Items with design value for this proposal. Each is a pattern to carry forward, not code to import; paths are under `legacy/agentscient/ops/`.

- **Core/transport separation stated as a rule**: "The core never sends. Transports are adapters outside it; the core records intent and receipt" (`conductor/tools/courier.py` docstring), with a reserve → submit → settle delivery model and outcomes `written`, `accepted`, `unsupported`, `failed`, `uncertain` (`courier.py:71, 181, 217`). This is the `Transport` port of §5.7 in an earlier form; the runtime's delivery states are its better-tested descendant.
- **Declared state machines as data**: `Machine` and `Transition` named tuples with actor, evidence and hold rules, moved in one transaction with an append-only marking log (`conductor/tools/machine.py:97-115, 330-353`). A good model for making assignment lifecycles and workflow gates declarative.
- **Domain verbs behind thin projections**: `core_<verb>(conn, part, ...)` functions (`coordination.py:809-1895`) with authority checks inside the domain, and an MCP facade that forbids a caller naming its own identity (`coordination_mcp.py`, `FORBIDDEN_ARGUMENTS`). Same principle as the single authorization step in §5.5.
- **Instance-isolation refusals** when two members share a profile, home or capability (`conductor/tools/session_adapters/routing.py:89-119`).
- **An 85-line stdio JSON-RPC transport** supporting three MCP protocol versions (`conductor/tools/mcp_transport.py`).
- **Configuration closure checks**: every declared key has a reader and every read key is declared (`conductor/tools/laneconf.py:422, 537`). Adopted for the manifest in §5.8.
- **Monitor as adapters → conditions → routes in data** (`conductor/tools/monitor.py:197-226`).
- **A domain-free testkit** (`testkit/`, about 3.3k lines): hermetic `World`, JSON-spec fake CLIs, table and mutation testing, and a test that forbids the kit importing anything from the domain (`testkit/tests/test_domain_free.py:15-16`). The runtime's `tests/support.py` could be rebuilt on this shape.
- **Typed result envelope** with derived counts and bounded artifact handles (`devlane/task/envelope.py:39-90`).
- **Harness isolation as data with measured evidence** (`devlane/harness/isolation.py`), the same idea as the assumptions register in §5.8.

Items tied to the original product or environment: Symfony providers and fixture apps, the `uscient/agentscient` slug and `dev`/`master` branches in `lane.conf`, pinned harness versions, Gitea and `act_runner` infrastructure, the governance doctrine (audit ladder, term walls, dated rulings), and several declared-but-absent pieces (`wf.py`, `board.py`, an inert `lane-policy.json`). CI runs only the testkit and five conductor suites (`scripts/check_legacy.py`).

**Recommendation.** Do not integrate legacy code into the runtime. Mine the items above as design input, then archive the tree (§8, phase 8). Keeping 921 files and a third CI job in the main tree costs attention on every change for material the runtime does not use.

### 3.8 Configuration lifecycle today

**Finding.** Configuration is read from source files on the request path and takes effect at the moment of the write:

- The adapter manifest is re-read on every registry call (`adapters.py:32-33`); the role catalog re-reads `role-packages.cue` and `.json` and re-validates by content hash on every call (`role_packages.py:131-144`); configuration defaults do the same (`configuration.py:329-347`); runtime policy re-reads `roles.json` and `vendors.json` on every resolution (`runtime.py:52-56`).
- The MCP server recomputes the advertised tool list every second and sends `tools/list_changed` when it differs (`mcp.py:179-185`), so a catalog edit changes a live Claude session's tool list within a second, while service-side authorization uses the new catalog on the next call (`api.py:491`).
- A launched session keeps its launch-time native allowlist and settings (`native.py:444`, `profiles.py:50-77`, `profiles.py:264`), so the same edit is deferred for the native surface. The documentation says to "restart control services and managed sessions to load package changes consistently" (`docs/role-capability-packages.md`, "Maintainer changes").
- An edit to the CUE source without regenerating the export fails every request with "export is stale" (`role_packages.py:62`, `configuration.py:335`), and any policy edit invalidates every retained runtime selection (§3.3, item 13).

So one edit is partly immediate, partly deferred and partly breaking, and nothing records which revision a running session is operating under beyond the launch descriptor. There is no step at which the owner decides that a change should take effect, and no view of what the change will do to running work.

---

## 4. What deserves preservation

This section answers the first concern: a migration that promises exact reproduction of today's semantics and a permanently green suite would carry forward the restrictions the restructuring is meant to question. The resolution is to classify what the current implementation embodies, commit to preserving only one class, and give the owner a defined way to change the rest.

### 4.1 Three classes

**Recommendation.** Every behavior the runtime exhibits today falls into one of three classes. The class decides whether it is preserved, where its tests live, and how it may change.

**Class K: correctness guarantees.** Properties the kernel must hold for *any* preset, because without them coordination is unsafe or evidence is untrustworthy. Preserved; tested by the kernel contract suite; changed only by a kernel design decision.

| Guarantee | Evidence today |
| --- | --- |
| A message is claimed by at most one live incarnation of its recipient, and an uncertain delivery is never replayed automatically; only the sender can retry | `store.py:627-692`, `native.py:322-326` |
| A superseded session generation cannot act, consume messages or register a launch | `sessions.py:24-49`, `store.py:627-633`, `launch.py:232-234` |
| Identical retries are idempotent through request keys; a reused key with a different payload is rejected | `work.py:73-78`, `store.py:555-562`, `control.py:633-647` |
| Authorization is fail-closed: a call outside the caller's current ceiling is denied before any effect, and a failing hook denies the native tool | `api.py:491-499`, `run.py:167-180` |
| A managed session's scope only narrows: an assignment may narrow the package's paths and tools, never widen them | `docs/role-capability-packages.md`, `workspaces.py:260-286`, `role_packages.py:221-238` |
| No agent-supplied shell, Git or Docker commands; Git runs with hooks disabled and credentials redacted | `workspaces.py:36-56`, `api.py:40` |
| Credentials are references, never values, and never appear in events or traces | `configuration.py:129-153`, `telemetry.py:18-57` |
| Evidence is bound to the exact revision and binding under which it was produced, and the binding is rechecked inside the recording transaction | `control.py:598-616, 661-664` |
| Private state lives outside any managed source tree | `store.py:376-378`, `workspaces.py:146-157` |
| The test census is honest: no silent skips | `tests/run.py` |

**Class P: inherited policies.** Choices that made sense for Agentscient's team and product. Preserved as the `chain_of_command` preset's *initial* content; tested by that preset's suite; changed deliberately through the behavior change record in §4.3.

| Policy | Where it lives today |
| --- | --- |
| CO is an interface only; XO is the sole acceptor; one NCO per job; planner reports to XO at work-order scope; arbitrator is service-assigned; assignable-role exclusions | `control.py:190-207`, `role_packages.py:105-107, 167-179`, `planning.py`, `commands.py` |
| Standby XO, roll call, duty readiness challenge before work | `standby.py`, `duty.py`, `commands.py:312-321` |
| Reserved arbitrator capacity; at most four work items per `orchestrate`; capacity counted per policy | `runtime.py:246-254`, `commands.py:39-43, 177-204` |
| Acceptance requires CI passed, a clean workspace, an NCO result report and completion evaluation; then merge | `delivery.py:295-426` |
| Codex bot review requests on the work-order PR; draft PRs; worktree per job; branch naming | `codex_reviews.py`, `orders.py`, `workspaces.py:205-212` |
| Managed sessions are MCP-only with native delegation disabled | `role_packages.py:97-99`, `profiles.py:22-32, 65-73` |
| Symfony checks, Maker/Mate tools, Symfony editable paths and framework skills | `development.py`, `role-packages.cue:78, 171-185`, `assignment-policy.json` |
| Vendor, model and reasoning defaults per role | `runtime-defaults.json` |

The MCP-only hook policy deserves a note. It is safety-relevant, so it is the default and is reported as a guard in `doctor`, but it is still a policy: a preset that runs only external sessions (humans, or agents with their own sandbox) has nothing for it to govern. The kernel guarantee is narrower and unconditional: *if* a session is declared managed, it cannot exceed its declared ceiling.

**Class I: implementation assumptions.** Details of how the current code happens to work. Not preserved; tests that pin them are retired when the implementation is replaced.

SQLite schema and table names, `SCHEMA_VERSION`, the SQL dialect; integer ids as the meaning of references (the reference *format* `kind:id` stays agent-facing, the id stays opaque); tmux, `/proc` process identity, abstract sockets, `fcntl` lock files; the `agentscient_dev` server name and `AGENTSCIENT_*` environment names; the `.agents/skills` path; `runtime-%` seat names; the 128 KiB frame; CUE as a runtime dependency; `role-packages.json` beside the code; internal event kind strings; per-harness hook JSON shapes; Serena mount strings.

**Assumption.** The owner has live team state directories created by the current runtime and wants their records carried forward. If not, compatibility reduces to the kernel capability vocabulary and the transition gets simpler.

### 4.2 Purpose and scope of compatibility

**Recommendation.** Compatibility has three purposes and nothing else:

1. **State carries forward.** An existing team's durable records (members, work orders, jobs, messages, artifacts, events, bindings, reviews, decisions) can be imported one way into the new runtime by a migration that runs once. Telemetry files are kept as-is for reading.
2. **The kernel vocabulary is stable within a major version.** Kernel capability names and schemas (`work_context`, `work_report`, `guidance_request`, `message_*`, `work_*`, `artifact_*`, `onboard`, `skill_read`) and the reference format keep their meaning, so skills and instructions written for one deployment can move to another.
3. **The current behavior is available as a preset.** `chain_of_command` reproduces today's organization, gates and defaults closely enough that an existing team can continue, and it is the suite of record for those policies. It is a starting point for the owner, not a commitment that any of its rules survive.

Out of scope, explicitly: byte-identical events or telemetry, schema and table names, native command lines, hook payload shapes, the server name, tmux, the CUE toolchain. Where a Class P rule's rationale is not recorded anywhere, the triage in §8 marks it "inherited, rationale unknown"; it stays in the preset until the owner decides, and is never promoted to Class K by default.

### 4.3 How tests evolve

**Recommendation.** Tests are evidence to evaluate, not a constraint to satisfy. They are triaged once (phase 0) into K, P and I, recorded in a checked-in table, and then governed by three rules:

- **K tests** are rewritten against ports during the phase that introduces the port and then protected. The intent of the existing test is kept; its mechanics change. A K test may only be deleted by a kernel design decision recorded in an ADR.
- **P tests** move into the preset's own suite. When the owner changes a policy, the tests change in the same change set, and the change set carries a **behavior change record**: the rule as it was, the rule as it is now, why, which tests were altered or removed, and what the owner decided about teams already running the old rule. Records accumulate in the preset's `CHANGES.md`. This replaces "all tests green throughout" with "K green always; P green except for tests named in a record in the same change".
- **I tests** are deleted when the implementation they pin is replaced. If an I test also carried an intent (for example, "a replacement session must not see the old session's messages" expressed through SQL), that intent is re-expressed as a K or P test first.

The triage counts in §3.6 suggest the split: roughly a fifth of files are K in intent, most of the coordination fixtures are P, and the SQL, schema, tmux and mount-string assertions are I. The exact assignment is work for phase 0 and is itself reviewable evidence.

---

## 5. Proposed architecture

### 5.1 Principles and the rules that follow from them

This section also answers the second concern: a restructuring can replace one set of accidental restrictions with a deliberate set that is just as obstructive. Each rule below states its scope and the practical benefit it buys; anything without both is a diagnostic, not a rule.

**Recommendation.**

1. **The kernel owns mechanisms; presets own policy.** The kernel knows members, sessions, messages, work items, bindings, relations, artifacts and events, and holds the Class K guarantees. It does not know what a CO is. *Benefit:* the organization can change without touching the code that keeps coordination safe.
2. **A port is justified by a concrete need, not by symmetry.** A port exists when at least one holds: (a) the `basic` preset must run without the thing (an environment dependency such as network, Docker, tmux or a vendor); (b) tests need a double for isolation or speed; (c) the owner has a concrete second implementation in mind. §5.4 states the justification per port. Where none holds, a module boundary with a documented interface suffices and no port is created. *Benefit:* avoids speculative abstraction while making every environment dependency replaceable. This replaces the earlier "two implementations per port" rule, which would have forced a second implementation where no one needs it.
3. **Dependency direction is a rule; import placement is not.** Kernel code never depends on adapters or presets; adapters never depend on other adapters; presets depend on anything. Function-local imports are acceptable for optional extensions, heavy modules and load-order convenience as long as the direction holds. A cycle *between layers* is an error; a cycle *within* a layer is reported as a diagnostic. *Benefit:* substitution is possible because nothing below a boundary reaches above it; the earlier blanket ban on local imports would have made optional extensions harder to keep optional.
4. **Extensions own their state, migrations, assumptions and invariants.** No extension writes another's records; no kernel migration checks an extension's rules. *Benefit:* an extension can be enabled, disabled or replaced without a kernel change.
5. **Configuration is applied, not watched.** The runtime reads an applied snapshot, never source files, on the request path (§5.8). *Benefit:* unrelated edits cannot disturb running work, and the owner sees the effect of a change before it happens.
6. **Authorization has one decision point.** The pipeline decides; hooks and native denials enforce that decision at the transport. *Benefit:* "why was this denied" has one answer.
7. **Structural counts are diagnostics; a few direction checks are gates.** §9 names the gates. *Benefit:* the design is verified by behavior under substitution, not by numbers that can be gamed or that penalize a legitimately rule-heavy adapter.

### 5.2 Layers and packages

**Recommendation.** Target layout (Python package `xorchestrate`, standard library only in the kernel):

```
xorchestrate/
  core/            model + ports + request pipeline + capability registry   (no I/O policy)
  runtime/         composition root, manifest loading and apply, doctor, CLI
  adapters/
    storage/       sqlite/, memory/
    transport/     inbox_poll/, codex_app_server/, grok_acp/, claude_channel/
    harness/       codex/, claude/, grok/, external/
    terminal/      tmux/, subprocess/
    routing/       chain_of_command/, direct/, fanout/
    organization/  chain_of_command/, matrix/, swarm/
    workflow/      gates: ci_passed/, workspace_clean/, result_reported/, completion_eval/, quorum/
    evaluator/     none/, jev_typesafe/
    delivery/      local_git/, github/
    workspace/     shared/, git_worktrees/
    providers/     serena/, symfony/
  presets/
    basic.json
    chain_of_command.json       (today's Agentscient behavior, Symfony catalog included)
```

Each `adapters/*/<name>/` directory holds its implementation, its schema contribution and migrations, its `extension.json` (requirements, guards, capabilities, tool groups, supported storage backends), its skills if any, and its tests. `legacy/` and `reference/` are not part of the package (§8, phase 8).

### 5.3 Core model

**Recommendation.** Keep the current entities and generalize three of them.

| Entity | Today | Proposed |
| --- | --- | --- |
| Member | `members` row with harness, workspace, native_id, generation, state | Unchanged; `harness` references a harness adapter name; members may be allocated at runtime through a kernel operation instead of the `runtime-%` convention |
| Session | Fields on member plus `generation` | Unchanged mechanism; `Lease` stays in core, its lock becomes a storage advisory lock |
| Message | `messages` with delivery states, `category` (`peer`, `direction`, `readiness`) | Unchanged states; `category` is an open namespace registered by extensions; recipient is always one member (fan-out happens in routing) |
| Work item | Two fixed tables, `work_orders` and `jobs`, with GitHub fields | One `work_items` aggregate with `kind` and `parent`; `work_order`/`job` are the kinds the chain-of-command preset uses; `repository`/`branch`/`pull_request` move to the delivery extension's records keyed by work item |
| Binding | `member_bindings` (role, work scope, `reports_to`, brief, paused, revision) | `bindings` keeps role, scope, brief, paused, revision and one row per member; `reports_to` is removed from the binding |
| Relation | Not present | `relations(subject, kind, object, scope, revision, created_by)`: plural, typed links between bindings (§5.6) |
| Artifact, Event, Work update, Link | As today | Unchanged |
| Configuration | Files read live | `configurations`: applied snapshots with revision, current pointer (§5.8) |
| Role package | Catalog as today | Unchanged format; preset-owned; behavior ids are registered by the organization adapter, not fixed in `role_behaviors.py` |

### 5.4 Ports and their contracts

**Recommendation.** Each port is listed with its justification under rule 2 and the contract concrete enough to assess. Contracts are sketched as Python protocols; the real contract is the port's test suite.

| Port | Justification | Adapters in tree |
| --- | --- | --- |
| Storage | (b) tests need a fast in-memory store; (c) a shared backend later | `sqlite`, `memory` |
| Transport | (a) basic use must not need a native protocol | `inbox_poll`, `codex_app_server`, `grok_acp`, `claude_channel` |
| SessionDriver (harness) | (a) vendor CLIs; (b) synthetic harness fixtures exist today | `codex`, `claude`, `grok`, `external` |
| Terminal | (a) tmux | `tmux`, `subprocess` |
| RoutingPolicy | (c) owner intent: different organizations | `chain_of_command`, `direct`, `fanout` |
| OrganizationModel | (c) owner intent | `chain_of_command`, `matrix`, `swarm` |
| Gate | (a) CI and evaluator gates need network | one adapter per check |
| Evaluator | (a) network and a paid service | `none`, `jev_typesafe` |
| DeliveryProvider | (a) GitHub | `local_git`, `github` |
| WorkspaceProvider | (b) tests; shared-checkout basic use | `shared`, `git_worktrees` |

Not ports: telemetry sink (one JSONL implementation; a module interface is enough), `rpc.py`, the MCP server edge, the skill loader. Each gets a documented module boundary and can become a port if a need under rule 2 appears.

#### Storage

The guarantees the current code relies on, as findings: atomic multi-record transactions (`BEGIN IMMEDIATE` in 18 modules); a single writer so check-then-write is safe for delivery claims, binding revisions and capacity counts (`store.py:627-655, 391-400`, `runtime.py:239-254`); unique constraints as idempotency keys (`messages UNIQUE(sender,request_key)`, `work_orders UNIQUE(created_by,request_key)`, `work_updates UNIQUE(actor,request_key)`, `reviews.request_key UNIQUE`, the partial unique index on `decisions`); monotonic integer ids used as cursors (`after=`) and as references (`message:12`); foreign-key integrity (`PRAGMA foreign_keys=ON`); a nested savepoint for the selection preview (`assignment_decisions.py`, `SAVEPOINT assignment_selection_preview`); cross-process advisory locks held as `fcntl` files in three directories (`sessions.py:11-21`, `launch.py:139-142`, `workspaces.py:98-114`).

The contract makes those explicit and portable:

```python
class Storage(Protocol):
    def open(self, team_state: Path) -> None            # one backend per team state; kernel and extension records share it
    def unit_of_work(self) -> UnitOfWork                 # serializable with respect to other units on this team state
    def advisory_lock(self, name: str, *, exclusive: bool) -> ContextManager   # cross-process; replaces the lock directories
    def migrate(self, contributions: list[SchemaContribution]) -> AppliedSchema  # kernel first, then extensions in manifest order, under the migration lock

class UnitOfWork(Protocol):
    members: Members; sessions: Sessions; messages: Messages; work: WorkItems
    bindings: Bindings; relations: Relations; artifacts: Artifacts; events: Events; configurations: Configurations
    def records(self, kind: str) -> RecordSet            # extension-declared kinds
    def savepoint(self) -> ContextManager                # nested rollback
    def commit(self) -> None; def rollback(self) -> None

class RecordSet(Protocol):                               # generic, backend-translated
    def insert(self, **fields) -> int                    # raises Duplicate on a declared unique key
    def get(self, ident: int) -> Row | None
    def update(self, ident: int, *, expect: dict | None = None, **fields) -> bool   # compare-and-set on expected field values
    def find(self, *, where: dict, order: Literal["id", "-id"] = "id", after: int = 0, limit: int) -> list[Row]  # `where` keys must be declared indexed fields
```

Guaranteed by every adapter: serializable isolation per team state (SQLite by its single writer and `BEGIN IMMEDIATE`; a server database by `SERIALIZABLE` with retry inside the adapter, or by a per-team advisory lock on the simple path; memory by one lock); monotonic per-kind integer ids; declared unique keys; savepoints; advisory locks; durable append order for events. Not guaranteed: transactions across team states or across backends. These are the consistency guarantees the system relies on, and every kernel algorithm is written against them and nothing more.

**Extension-owned state.** An extension declares its records rather than writing DDL:

```json
{"owner": "delivery.github", "version": 3,
 "kinds": {
   "watches": {"fields": {"member": "ref:members", "work_item": "ref:work", "repository": "str", "pr": "int?", "revision": "str",
                          "state": "str", "next_poll": "float", "sequence": "int"},
               "indexes": ["member", "work_item", "state"], "unique": [["member", "repository", "pr", "revision"]]}},
 "migrations": [{"from": 2, "to": 3, "steps": "delivery.github.migrations:v3"}],
 "supports": ["sqlite", "memory"]}
```

Backends translate declarations into tables, dictionaries or collections. Kernel repositories and the generic `RecordSet` cover the kernel's own queries and most extension queries. An extension that needs more than `find` can ship backend-specific query modules for the backends it names in `supports`; `doctor` refuses to apply a composition that enables an extension on a backend it does not support. This is the honest statement of what "replace storage" costs: the backend adapter, plus each enabled extension's backend-specific queries if it has any. The kernel is portable by construction; extensions are portable by declaration.

**Migrations across backends.** Migration steps are written against `UnitOfWork` and `RecordSet`, so they are portable unless an extension marks a step backend-specific. Ordering is kernel first, then extensions in manifest order, each under the migration advisory lock, each recorded as a kernel `schema_versions` row per owner. A step may refuse and name its owner and reason; the whole apply then rolls back. This is where the current NCO check (`store.py:332-346`) goes: the chain-of-command organization adapter's migration refuses to upgrade a team with an invalid binding, and the refusal names that adapter rather than "the schema".

**Cross-record consistency.** Kernel and extension records share one unit of work, so a kernel write and an extension write commit or fail together. Extension records reference kernel ids; kernel records never reference extension records. Kernel deletions are rare (unbind, unlink) and extensions react to kernel events rather than relying on cascades.

#### Transport

```python
class Transport(Protocol):
    def deliver(self, session: Session, envelope: Envelope) -> Receipt   # Receipt.outcome in {"submitted", "uncertain", "unsupported"}
    def capabilities(self) -> set[str]                                    # e.g. {"interject", "initial_prompt"}
```

`submitted` and `uncertain` map onto the existing delivery states; `unsupported` lets the kernel fall back to `inbox_poll`. Claiming the envelope stays in storage (`take_delivery`); the transport only moves one claimed envelope into one live session.

#### SessionDriver, Terminal

The harness contract is today's `harnesses.py` contract with `prepare`, `run`, `permitted`, `hook` and `readiness`, plus a launch context object instead of `(directory, member, config)` tuples; each shipped driver owns its own profile preparation, which today lives in `profiles._prepare_builtin`. `Terminal` is `open`, `status`, `read`, `close` with `tmux` and a plain `subprocess` implementation.

#### RoutingPolicy, OrganizationModel, Gate

```python
class RoutingPolicy(Protocol):
    def recipients(self, uow, sender: Binding, message: Outgoing) -> list[Recipient]   # fan-out happens here; each Recipient becomes one message
    def visibility(self, uow, member: Binding) -> Scope                                # value object; storage translates it

class OrganizationModel(Protocol):
    behaviors: Mapping[str, Behavior]                                   # replaces role_behaviors.py
    relation_kinds: Mapping[str, RelationKind]                          # e.g. reports_to(commands=True), guided_by(commands=False)
    def check_binding(self, uow, member, role, scope, relations: list[Relation]) -> None   # raises with a reason
    def assignable_roles(self, uow, by: Binding | None) -> tuple[str, ...]
    def may(self, uow, actor: Binding, action: str, target: Binding | WorkItem) -> bool  # steer, assign, accept, conclude, ...
    def tool_groups(self, uow, binding: Binding | None, context: ToolContext) -> set[str]

class Gate(Protocol):
    name: str
    def check(self, uow, subject: WorkItem, evidence: Evidence) -> GateResult   # passed | blocked(reason) | unavailable(reason)
```

`Scope` replaces the SQL fragments in `control.py:108-153`: a small value with work item, optional child, allowed categories and recipient constraints. The storage adapter translates it. This breaks the `store ↔ control` cycle and preserves the current visibility semantics, which become the SQLite adapter's translation.

### 5.5 Capability registry and request pipeline

**Recommendation.** Replace the three hand-maintained lists (`CAPABILITIES`, `TOOLS`, `OPERATOR_TOOLS` in `api.py`) and the name-dispatch in `api.invoke` with registrations:

```python
registry.capability(
    name="work_report", schema=REPORT_SCHEMA, handler=reports.report,
    groups={"bound"}, readonly=False, audience="agent")
```

Extensions register capabilities at composition time; role packages keep referencing names by group (today's `validate_tools` already checks that every referenced name exists, `role_packages.py:213-218`). Audience (`agent`, `operator`) replaces the separate operator surface; the three operator meta-tools stay as a projection.

The request path in `API.call`/`API.run` (`api.py:479-616`) is a fixed nesting of lease check, scope resolution, tool gating, schema validation, decision context, recovery capture, diagnostics guidance and event recording. It becomes an explicit middleware list configured by the preset:

```
basic:             lease → scope → authorize → validate → events
chain_of_command:  lease → scope → authorize → validate → decisions → recovery → diagnostics → events
```

The `scope` step captures the caller's binding revision *and* the current applied configuration revision once per request, the way `REQUEST_BINDING` captures scope today (`api.py:482-483`). Everything downstream reads those captured values. Optional sophistication stays optional because it is a list entry, not a branch.

### 5.6 Organization: bindings, relations and authority

This section answers the third concern's organizational half. The single `reports_to` field is the current shape; the question is whether keeping it supports the flexibility the goal asks for.

**Finding.** Today a binding is one row per member with one `reports_to` (`store.py:152-156`), standby bindings make `reports_to` unique (`store.py:157-162`), the team is enumerated by recursive descent over `reports_to` (`commands.py:155-165`), reports route to `reports_to` or branch on behavior (`control.py:502-567`), and `bind_in` forbids cycles (`control.py:208-214`). Coordination data for an NCO is JSON packed into the `brief` text (`commands.py:108-113`). The model is a tree, and the kernel's routing, authority and visibility all read the tree.

**Recommendation.** Keep one binding per member. Move the relationship out of the binding into plural, typed `relations`. The organization adapter declares which relation kinds exist and what each grants:

```json
{"relation_kinds": {
   "reports_to": {"commands": true,  "routes": ["report", "guidance", "readiness"], "max_per_subject": 1, "acyclic": true},
   "guided_by":  {"commands": false, "routes": ["guidance"]},
   "reviews":    {"commands": false, "routes": ["finding"]},
   "peer_of":    {"commands": false, "routes": ["report", "finding"], "symmetric": true}}}
```

- **Authority** (steer, pause, stop, replace, rebind, accept) is granted only by relation kinds marked `commands`. The kernel enforces that a command verb is permitted by `OrganizationModel.may`, which reads relations; it does not interpret kinds itself.
- **Routing** reads relations by message purpose. A `guidance_request` goes to `guided_by` targets if any, else `reports_to`; a `finding` goes to `reviews` targets and the chain; a `report` goes to `reports_to` and `peer_of`. Fan-out produces one message per recipient with a per-recipient request-key suffix, the idiom `delivery._publish` already uses (`delivery.py:270-286`).
- **Visibility** is a `Scope` derived from the member's bindings and relations by the routing adapter.
- Coordination data (an NCO's delegated mission, a swarm's claim) lives in the organization adapter's own records, not in `brief`.

Three organizations under this model:

| Organization | Relations | Authority | Routing | Acceptance |
| --- | --- | --- | --- | --- |
| **Chain of command** (today) | exactly one `reports_to` per non-interface binding; acyclic; scope-covering; one NCO per job | `reports_to` only; XO accepts jobs, concludes orders | reports and guidance up the chain | gates `ci_passed`, `workspace_clean`, `result_reported`, `completion_eval` |
| **Matrix guidance** (an architect guides coders across jobs while NCOs run the jobs) | `reports_to` as above plus `guided_by` from each coder to the architect | `reports_to` only; the architect cannot pause or steer | guidance to the architect first, reports up the chain; architect's replies are advisory messages, not direction | as above |
| **Peer swarm** (no coordinator; workers claim items; two approvals accept) | `peer_of` among members bound to the same work item; no `reports_to` | none; a member may pause itself; the owner's operator connection steers | reports and findings fan out to peers; guidance to the owner | gate `quorum(reviews >= 2, not_the_producer)` |

The matrix case shows why a single `reports_to` could not do this: two superiors would have had to share one field, and routing would have had to guess which one a guidance request meant. The swarm case shows that "no superior" must be representable without a dummy member. Both work with no kernel change because the kernel reads relations through the adapter and never assumes a tree.

**Unresolved assumptions, stated.**

- *One binding per member.* A specialist shared across two work orders (one architect advising two teams) needs either two members with the same identity or multiple bindings with a per-request focus. The proposal keeps one binding and defers multi-binding; the cost of adding it later is a `focus` on sessions and a change to the `scope` step, and the kernel model is laid out so that nothing else assumes uniqueness.
- *Conflicting direction under multiple commanding relations.* The kernel does not arbitrate. A preset that marks two kinds `commands` must say in its adapter which wins; the shipped presets mark only `reports_to`.
- *Runtime membership.* A swarm needs members to join and leave; the kernel operation that today allocates `runtime-%` seats (`runtime.py:256-276`) becomes `Members.allocate(prefix)` and the swarm adapter uses it. Session identity for an ephemeral member is unchanged.
- *External participants.* Humans or foreign agents are `external` members today; their acknowledgement semantics differ from managed sessions. The relation model does not change that; the swarm's `quorum` gate must count approvals from external members the same way, which the gate adapter decides.

### 5.7 Routing, transport and storage of messages

**Recommendation.** Three responsibilities, one shared envelope:

1. **Storage** keeps the durable envelope and the delivery state machine (`store.py:546-692`, unchanged).
2. **Routing** decides recipients and visibility (§5.6).
3. **Transport** moves one claimed envelope into one live session and reports a receipt. `Native.deliver` (`native.py:374-410`) and the Claude channel loop (`mcp.py:52-76`) become transport adapters; the existing `message_wait` polling becomes `inbox_poll`, which works with any MCP client.

Rendering a message for a native prompt (`control.render`, `control.py:755-786`) is a transport concern parameterized by the preset's instruction text.

### 5.8 Configuration lifecycle

This section answers the fourth concern. Findings about today's lifecycle are in §3.8.

**Recommendation.** Configuration has three sources and one point of effect.

*Sources.* The installed package (presets, adapters and their schemas; changes with a deploy). The team's files (the manifest, role and vendor policy, catalog overrides, project overlays; edited by the operator). The environment (credential references only). CUE remains an optional authoring tool whose export is just another team file.

*Apply.* `xorchestrate config apply --state DIR` (also an operator capability) does, in order: resolve the composition from the sources; validate it end to end (adapters importable and contract-complete, catalog against the registry, policies, extension support for the chosen storage backend, closure of declared versus read keys); compute the resolved snapshot and its revision (a digest of the snapshot); compute the **impact** on running work (below); write the snapshot as a `configurations` record and move the `current` pointer, in one unit of work. Nothing on the request path reads source files. Editing a file does nothing until apply.

*What running work observes.* Every request captures the current applied revision at its `scope` step and uses only that revision for the rest of the request; every launch records the revision it was prepared under; every retained runtime selection records the revision of the *parts it depends on* (the role's profiles and that role's package), not a digest of the whole policy, so an unrelated edit does not invalidate it (fixing §3.3 item 13). Apply classifies each running binding and session by diffing the resolved snapshot per role, profile and extension:

| Class | Example | Effect |
| --- | --- | --- |
| Unaffected | a skill text for another role changed; a vendor was enabled | nothing observable |
| Compatible | a role's tool ceiling widened; a skill it uses changed; a gate added to a work-item kind it is not in | service-side checks use the new revision from the next request; the session keeps its launch-time native surface and is flagged `launch_stale` in `monitor` until relaunched |
| Incompatible | a role removed or its ceiling narrowed; its harness no longer permitted; its selected profile removed; the pipeline or organization adapter changed | apply **refuses** by default and lists the affected bindings and sessions; the operator reruns with `--pause-affected` or `--stop-affected`, which uses the existing pause and stop mechanics, records `configuration_applied` with the affected list, and leaves the work records intact for relaunch under the new revision |

Narrowing is fail-closed on the service side the moment the new revision is current, whether or not the session has been relaunched: its cached native allowlist is a superset the service rejects, which is already the design (hooks and the service check both run today). The operator is told which sessions this affects before it happens.

*Failure and recovery.* A validation failure applies nothing. Apply is atomic: the snapshot and the pointer move together, so a crash leaves the previous revision current. The last N snapshots are retained (default 10); `config apply --revision R` re-applies an earlier snapshot and goes through the same impact analysis, so rollback is not a special path. A code deploy changes the installed package; `doctor` compares each running process's package revision with the applied snapshot and reports `restart_required` per process. There is no hot code reload, no file watcher and no additional lock: the only control is the current applied revision, and the only observation is the revision captured per request and per launch.

*Changing direction.* `apply --stop-affected` is the intentional, recorded way to change the organization or a role's authority while work is running. Work items, messages, evidence and bindings survive; affected sessions are stopped and relaunched under the new revision through the existing replace semantics; the behavior change record (§4.3) says why.

*Development convenience.* An `--auto-apply` watcher is a legitimate wish for a one-person team editing presets. It belongs in a development extension that calls `apply` on file change with impact set to refuse; it is not part of the kernel and is off by default.

*The manifest.* One JSON document per team; presets are manifests with defaults; precedence stays defaults → preset → team → role → job with objects merged and lists replaced (`configuration.py:350-366`). Validation reuses the JSON-schema validator already in `api.py:371-411` against schemas each extension ships, replacing the hand-written validators in `configuration.py` and `runtime.py`. Unknown fields fail closed; new fields no longer need three coordinated edits. Each `extension.json` declares requirements and guards:

```json
{"requires": {"platform": ["linux"], "executables": ["tmux"], "network": [], "services": []},
 "guards": {"mcp_only_hooks": {"value": true, "reason": "managed sessions must not reach native shell", "owner": "preset"}},
 "supports": {"storage": ["sqlite", "memory"]}}
```

`doctor --explain` prints the applied composition, every requirement and guard it carries, and the revision each running session was launched under.

### 5.9 Dependency rules

**Recommendation.** Three rules, each checked by a small `ast` test (§9), each stated with its scope:

- `core` imports only the standard library. Scope: the kernel package. Benefit: the kernel runs anywhere and depends on no adapter.
- An adapter imports `core`, the standard library and its own package; never another adapter. Scope: `adapters/*`. Benefit: adapters are independently replaceable. Cross-adapter needs go through a port or an event.
- `sqlite3`, `fcntl` and process-identity calls appear only in the storage, terminal and harness adapters. Scope: everything outside those adapters, after the phase that introduces the port. Benefit: the kernel is portable and the Linux assumption is declared where it belongs.

Not rules: no function-local imports (direction is what matters); no behavior-id literals anywhere (the organization adapter is *supposed* to be full of them); decreasing counts (see §9).

---

## 6. Representative changes

Each scenario names what changes and what does not under the proposed structure, with the equivalent cost today. The target for a change is: the adapter directory, its tests and docs, plus the manifest or preset; a change to a *port* touches the port, its contract suite and every adapter of that port; never another adapter and never another preset's behavior. Independence is demonstrated by the substitution drills in §9, not by counting files.

**A. Replace storage** (SQLite → in-memory for tests; later a server database). Implement `Storage` and the repositories in `adapters/storage/<name>`; run the storage contract suite; confirm every enabled extension lists the backend in `supports` or ships queries for it. Routing, organization, commands and transports are untouched. Today: SQL in about 30 modules, visibility as SQL text, an organization check inside the migration.

**B. Add a harness** (a fourth CLI agent). One `adapters/harness/<name>` implementing `SessionDriver`, optionally a `Transport`; one manifest entry. Today: `adapters.json` plus edits to `profiles.py:159-186, 234-346`, `native.py:38, 96-197`, `compat.py`, `telemetry.requested`.

**C. Change the organization** to matrix guidance. Derive a preset from `chain_of_command` that enables the `matrix` organization adapter (adds the `guided_by` relation kind) and a routing adapter that prefers `guided_by` for guidance. Commands, storage, transports untouched; the chain-of-command preset's suite is unaffected because it does not load the matrix adapter. Today: a second superior cannot be represented at all (§5.6).

**D. Run a peer swarm** with no coordinator. Enable the `swarm` organization adapter, the `fanout` routing adapter and the `quorum` gate; `assign` becomes a self-service claim. No kernel change. Today: `bind_in` requires a covering superior for every non-CO role (`control.py:192-195`).

**E. Replace GitHub** with GitLab, or run with local branches only. A `DeliveryProvider` adapter; delivery fields live in the extension's records; the `ci_passed` gate reads the provider. Today: `delivery.py` imports `GitHub`; `jobs.pull_request`, `github_watches`, `codex_requests` are core schema; `github.py` is reached from `workspaces`, `orders`, `delivery`, `feedback`, `watcher`.

**F. Add a transport** (HTTP push to a remote agent runtime). A `Transport` adapter; the envelope and delivery states are unchanged; `inbox_poll` remains the fallback. Today: a branch in `Native.deliver` and a loop in `mcp.serve`.

**G. Add a capability** (for example `calendar_check`) from an extension. Register it; reference it from a role package group. Today: append to `TOOLS`, add a branch to `api.invoke` and possibly to `api._tools`.

**H. Narrow a role's editable paths while two coders are running.** Edit the team's catalog override; run `config apply`. The impact analysis reports both coder bindings as incompatible and refuses. The operator reruns with `--pause-affected`; both coders are paused with a direction message; service-side path checks use the new ceiling from the next request; the operator or coordinator resumes them, which relaunches under the new revision. Today: the edit takes effect on the next MCP call for the service check and never for the native allowlist, with no record of the change and nothing telling the operator who was affected (§3.8).

**I. Deliberately drop "one NCO per job".** Edit the chain-of-command organization adapter's rule table; change the preset tests that asserted the rule; add a behavior change record naming the rule, the reason, the altered tests and what happens to teams already running with it. The kernel suite is untouched. Today: the rule lives in `control.py:202-207`, `planning.py`, `store.py:332-346` and the coordination fixtures, and nothing records that changing it was intended.

**J. Basic use.** `xorchestrate init --preset basic --state DIR --workspace .` yields SQLite, stdio MCP, polling inbox, subprocess terminal, one lead and N workers, no GitHub, no evaluator, no tmux, no Docker, and a first `config apply` performed by `init`. The same kernel with fewer adapters loaded. Today: the quickstart avoids vendors and Docker, but managed sessions need tmux and Linux, the evaluator connection must be declared, and the organization is CO/XO/NCO even for one person and one worker.

---

## 7. Tradeoffs

**Recommendation**, with the costs stated plainly.

| Choice | Benefit | Cost | Mitigation |
| --- | --- | --- | --- |
| Ports only where rule 2 applies | Environment dependencies and test isolation become replaceable without speculative abstraction | A later need for a port means retrofitting a module boundary | Boundaries are documented now; retrofitting a port behind a documented boundary is local |
| Generic `RecordSet` plus declared schema contributions | Kernel portable by construction; extensions portable by declaration | Less expressive than SQL; the two or three intricate queries (`evidence_sql`, `control.py:145-153`) need adapter-specific modules | `supports` declarations and `doctor` refusal keep the cost visible; no silent partial support |
| Relations instead of `reports_to` | Matrix, peer and committee organizations become representable; authority and routing become data | One more record kind and a join on the hot path; the chain-of-command preset gains nothing it did not have | The relation kinds for that preset are one row per binding; the join is indexed |
| One binding per member kept | Session identity and scope capture stay simple | A shared specialist across work orders is not representable | Deferred with its cost stated (§5.6) |
| Applied configuration with impact analysis | Unrelated edits cannot disturb running work; the owner sees effects before they happen; rollback is an apply | One more operator step than edit-and-go; impact classification must be maintained as new configuration kinds appear | `init` applies once; the development extension can auto-apply with refusal; classification lives next to each extension's schema |
| Class K/P/I triage instead of a permanently green suite | Inherited rules can be changed on purpose with a record | Triage is judgment work; some tests will be misclassified at first | The triage table is reviewable evidence; reclassification is a one-line change with a reason |
| Direction gates only; counts as diagnostics | Verification cannot be gamed and does not penalize rule-heavy adapters | Backsliding in the kernel shows up as a trend, not a failed build | The three direction gates are the backsliding that matters; the kernel is small enough to review |
| JSON-schema validation from the existing mini-validator | One validator, extension-shipped schemas | Less precise than CUE constraints such as `max_questions <= 32 / max_sources` | Keep CUE as an optional authoring tool; express cross-field rules in adapter code |
| Archiving `legacy/` out of the main tree | Removes 921 files and a CI job | Loses in-tree browsing | Tag the PR1 revision; the extraction manifest already records provenance |
| Kernel portable, adapters Linux-specific | Basic preset runs on macOS or WSL without tmux | Leases and process identity need a portable path | Leases through the storage advisory lock; process identity stays in the tmux adapter |

**Assumption.** The owner intends Xorchestrate to be used on projects that are not Symfony and with agents beyond the three shipped harnesses. If that is wrong, scenarios B and E lose weight and the Symfony catalog can stay the default preset.

---

## 8. Transition from the current implementation

**Recommendation.** A strangler sequence, one phase per change series. The exit criterion for every phase is the same: the Class K suite is green; the chain-of-command preset suite is green except for tests named in a behavior change record in the same change; Class I tests that pinned the replaced implementation are deleted in that change; the substitution drills that exist at that phase pass. Disposition per module is in Appendix B.

| Phase | Work | Exit criterion |
| --- | --- | --- |
| 0 Baseline and triage | Merge PR1. Add `docs/adr/` with ADR-0001 (this architecture) and ADR-0002 (naming: package `xorchestrate`, server name configurable, `agentscient_dev` as a compatibility default). Triage the 84 test files into K, P and I in a checked-in table with a one-line reason each. Write the compatibility scope (§4.2) into `docs/`. Add the three direction tests in report-only mode. | Triage table reviewed; direction tests report current violations |
| 1 Composition root and registry | `Runtime.build(manifest)`; `API` and `mcp.serve` take a `Runtime`; replace `api.invoke` dispatch with registrations; keep every tool name and schema identical. | No behavior change; `api.invoke` deleted |
| 2 Storage port | Repositories, `RecordSet`, advisory locks over SQLite; move SQL out of the 30 modules; memory adapter; storage contract suite; K tests that used SQL rewritten against ports; I tests pinning SQL deleted. | `sqlite3` and `fcntl` only in the storage adapter; contract suite passes on both adapters |
| 3 Schema ownership and applied configuration | Split `store.upgrade()` into kernel migrations plus extension contributions with refusal; move the NCO check into the chain-of-command adapter's migration; add `configurations` records, `config apply` with impact analysis, per-request and per-launch revision capture; remove live source reads. | Kernel migration has no role knowledge; no request-path file reads; scenario H passes |
| 4 Routing, transport and terminal | `Scope` value object; `RoutingPolicy` with `chain_of_command` and `direct`; transports `codex_app_server`, `grok_acp`, `claude_channel`, `inbox_poll`; `Terminal` with `tmux` and `subprocess`. | `basic` preset starts a managed session without tmux; `store` no longer imports `control` |
| 5 Organization and gates | Relations; `OrganizationModel` and `Gate` ports; lift rules from `control.bind_in`, `commands`, `planning`, `delivery._finish_job`; `role_behaviors.py` becomes the chain-of-command adapter's behavior table; `matrix` and `swarm` adapters; P tests move into the preset suite; the first deliberate behavior changes are allowed with records. | Scenarios C, D and I pass; chain-of-command preset suite green or recorded |
| 6 Providers and evaluator | GitHub, git worktrees, Serena, Symfony/Maker/Mate and Jev become extensions with `extension.json`; `connection` moves into the evaluator extension and becomes optional; Symfony paths and framework skills move into the chain-of-command preset's catalog; `doctor --explain` prints requirements, guards and session revisions. | `basic` manifest applies with no evaluator and no GitHub |
| 7 State import | One-way import of an existing team state directory into the new schema, with the extension migrations. | An existing team's records are readable and its open work can be resumed under `chain_of_command` |
| 8 Legacy disposition | Extract the specific reusable pieces identified in §3.7 into extensions or tests; tag the PR1 revision; move `legacy/` and `reference/` out of the main tree. | Main tree contains only the package, presets, docs and tests |

What to **retain** unchanged in spirit: the Class K guarantees and their mechanisms, the adapter manifest, the role catalog format, telemetry redaction, the census runner, the stdio client fixture, worktree isolation, capability-gap reporting.

What to **restructure**: `api.py` (registry and pipeline), `control.py` (reports, routing adapter, scope), `commands.py` and `planning.py` (generic verbs over the organization port), `store.py` (storage adapter and extension contributions), `profiles.py`, `native.py`, `harnesses.py`, `compat.py` (self-contained harness adapters), `delivery.py` and `orders.py` (gates and delivery provider), `configuration.py` and `runtime.py` (manifest, apply, schema validation).

What to **replace**: `role_behaviors.py` (fixed ids → adapter-registered behaviors); the single `reports_to` (→ relations); the SQL-fragment visibility helpers (→ `Scope`); the hand-written validators (→ schema validation); live source reads (→ applied snapshots).

What to **remove** from the main tree: `legacy/` (after mining), `reference/agentscient-dev/` (after CI is self-contained), `.github/actions/php-runtime/` and `devtools/scripts/acceptance*.py` (owner-infrastructure helpers that `docs/extraction.md` already excludes from the gate), `devtools/serena/` Dockerfile (moves to the Serena extension).

---

## 9. Verifying the proposed qualities

**Recommendation.** Verification is sized to what it proves. Gates prove behavior, independence or the absence of a specific regression; everything else is reported.

**Gates.**

1. **Class K contract suite.** The kernel's guarantees (§4.1), written against ports and run against every storage and transport adapter. This is the suite that must always be green.
2. **Port contract suites.** One abstract test class per port, executed against each adapter: storage (SQLite, memory), harness drivers against their synthetic fixtures, transports against recorded sessions, organization adapters against the rules they declare, gates against fixed evidence. These define the ports.
3. **Substitution drills.** Two CI compositions run the same behavior scenarios end to end: `basic` (memory storage, `inbox_poll`, `subprocess` terminal, `swarm` or flat organization) without tmux, Docker or network; `chain_of_command` with the current fixtures under tmux. A change to one adapter that breaks the other drill violated a dependency rule. This is the evidence that the representative changes in §6 are local.
4. **Direction tests.** Three `ast` checks for the rules in §5.9. Cheap, unambiguous, and they catch the one kind of backsliding that undermines substitution.
5. **Configuration impact tests.** For each impact class in §5.8, a scenario that applies a change against running fixtures and asserts what each session observes and what the operator is told.
6. **Preset suites.** The chain-of-command preset's Class P tests, green or named in a behavior change record in the same change.
7. **`doctor --explain` snapshots** per preset, checked in; a new requirement or guard appears as a reviewable diff.

**Diagnostics, reported in CI output but not gated:** behavior-id literal counts per module (useful to watch the kernel, meaningless in an organization adapter), function-local import counts, fan-in and fan-out, lines per module. These show trends and prompt review. Gating them would either push rules out of the adapter that should hold them into data tables that cannot express them, or reward moving code around without changing structure. The earlier revision's ratcheting counts and file-count probes are withdrawn for that reason.

**Documentation as contract:** each extension's README lists its requirements, guards and supported backends; the preset docs list the pipeline, gates and relation kinds; the `basic` quickstart runs in CI as a smoke test.

---

## 10. Open decisions for the owner

1. Keep CUE for preset authoring, or standardize on JSON with extension-shipped schemas only.
2. Confirm the package and server naming (`xorchestrate`, with `agentscient_dev` as a compatibility default).
3. Whether `chain_of_command` or `basic` is the documented default preset. This proposal makes `basic` the default.
4. Whether the Jev/TypeSafe evaluator remains in-tree as an extension or moves to a separate repository.
5. Platform scope: kernel portable with Linux-only adapters (proposed), or Linux-only throughout.
6. Disposition of `legacy/` and `reference/` (archive branch versus separate repository).
7. Whether `reports_to` stays a kernel-known optional relation kind with a default routing meaning, or is purely preset-defined. Proposed: kernel-known and optional.
8. Whether narrowing takes effect on the service side immediately on apply (proposed, fail-closed) or only after the affected sessions relaunch.
9. How many applied configuration snapshots to retain (proposed: 10).
10. Whether multi-binding per member is wanted soon enough to design for now rather than defer.
11. Which Class P rules, if any, the owner wants promoted to Class K after the triage (the MCP-only guard is the likely candidate).

---

## Appendix A. Evidence counts

Collected on the PR head with `grep` and an `ast` import walk over `devtools/*.py` excluding tests.

| Measure | Value |
| --- | --- |
| Runtime modules (non-test) | 60 |
| Runtime lines of Python including tests | 41,823 |
| Behavior branches `adapter(...) ==`/`in` | 128 in 21 modules |
| Literal behavior ids (`"co"`, `"xo"`, `"nco"`, `"planner"`, `"arbitrator"`, `"audit"`, `"coder"`) | 29 modules; `commands.py` 38, `planning.py` 32, `control.py` 32 |
| Modules executing SQL outside `store.py` | about 30 |
| Modules using `BEGIN IMMEDIATE` / `json_each`,`json_extract` | 18 / 11 |
| Modules writing message category or supersession SQL outside `store.py` | 5 |
| Highest fan-in | `store` 42, `role_packages` 32, `control` 23 |
| Highest fan-out | `api` 27, `commands` 18, `decisions` 17 |
| Agent-facing tools / operator capabilities / roles / behaviors | 25 / 51 / 13 / 11 |
| Test files / test methods | 84 / about 850 |
| Test files executing SQL directly / opening `store.connect()` | 55 / 53 |
| Test files importing `SCHEMA_VERSION` / referencing the SQLite file or `user_version` / containing `PRAGMA` or `CREATE TABLE` | 10 / 18 / 15 |
| Test files asserting event kind strings / referencing `reports_to` / tmux / `agentscient_dev` | 27 / 20 / 7 / 5 |
| `patch.object` uses in tests | 293 |
| Third-party imports in the runtime | 0 |

## Appendix B. Module disposition

| Module(s) | Disposition | Target |
| --- | --- | --- |
| `store.py` | Restructure | `adapters/storage/sqlite` + kernel repositories; `upgrade()` split by owner |
| `sessions.py`, `launch.py`, `bootstrap.py`, `processes.py` | Retain, relocate | `core/sessions` with lease over the storage advisory lock; launch handoff kept; process identity to the tmux adapter |
| `terminals.py` | Restructure | `adapters/terminal/tmux`; add `subprocess` |
| `harnesses.py`, `profiles.py`, `native.py`, `compat.py`, `console.py`, `console_settings.py` | Restructure | Three self-contained harness adapters plus transports; operator console stays with the Claude/Codex/Grok adapters |
| `rpc.py` | Retain | `core/protocol` |
| `mcp.py` | Retain, thin | `adapters/transport/stdio_mcp` server edge; channel delivery moves to `claude_channel`; tool-list watch reads the applied revision |
| `api.py` | Restructure | `core/pipeline` + `core/registry`; schemas stay; the mini JSON-schema validator becomes the configuration validator |
| `control.py` | Restructure | `core/reports` (work_report, guidance_request, context), `Scope`, routing adapter; `_destination` becomes the chain-of-command routing adapter |
| `commands.py`, `planning.py`, `assignments.py`, `assignment_decisions.py`, `standby.py`, `duty.py`, `orders.py` | Restructure | Generic verbs in `core/commands`; rules, standby and duty in `adapters/organization/chain_of_command`; coordination briefs become adapter records |
| `role_packages.py`, `role_views.py`, `role-packages.cue/json` | Retain | Catalog format kept; invariants become preset guards; read from the applied snapshot |
| `role_behaviors.py` | Replace | Behavior table inside the organization adapter |
| `adapters.py`, `adapters.json` | Retain, extend | Registry for all port kinds; manifest resolved at apply |
| `configuration.py`, `configuration.cue/json`, `runtime.py`, `runtime-defaults.json`, `jev_config.py` | Restructure | Manifest loading and `config apply` in `runtime/`; evaluator settings move to the evaluator extension; selection revisions scoped per role and profile |
| `work.py`, `artifacts.py`, `capability_gaps.py` | Retain, generalize | `work_items` aggregate; delivery fields move out |
| `delivery.py`, `feedback.py`, `codex_reviews.py`, `github.py`, `github_http.py`, `github_tools.py`, `watcher.py` | Restructure | `adapters/delivery/github` + gates; `local_git` added |
| `workspaces.py`, `retirement.py` | Retain as extension | `adapters/workspace/git_worktrees`; `shared` added |
| `serena_provider.py`, `devtools/serena/`, `integration/serena.py` | Retain as extension | `adapters/providers/serena` |
| `development.py`, `integration/symfony.py`, `integration/php-runtime.php`, `assignment-policy.json`, Mate skills | Retain as extension | `adapters/providers/symfony`; defaults move into the `chain_of_command` preset |
| `advice.py`, `decisions.py`, `completion.py`, `jev.py`, `credentials.py` | Retain as extension | `adapters/evaluator/jev_typesafe` + `completion_eval` gate; `none` added |
| `diagnostics.py`, `recovery.py`, `recovery_actions.py`, `onboarding.py` | Retain as middlewares | Pipeline entries in `chain_of_command` |
| `telemetry.py`, `failures.py`, `process_observation.py`, `process_report.py` | Retain | `core/telemetry` with a module boundary, not a port |
| `readiness.py`, `consistency.py`, `run.py` | Restructure | `runtime/cli` with `config apply`, `config validate` and `doctor --explain` |
| `operations.py` | Retain | Long-operation worker, kernel; its permission matrix moves to the organization adapter |
| `scripts/acceptance*.py`, `scripts/harnesses.*`, `scripts/credentials.*`, `scripts/dependencies.*`, `scripts/git-sync.sh`, `scripts/runners.sh` | Remove from main tree | Owner-infrastructure helpers; archive with `reference/` |
| `tests/run.py`, `tests/support.py`, synthetic harness fixtures | Retain | Census runner and stdio client become the contract-suite base; fixtures split by K/P/I |

## Appendix C. Revision history

**Revision 2** (this document). Substantive changes from revision 1:

- Replaced the promise of exact reproduction and a permanently green suite with a three-class model (§4): kernel guarantees that are preserved, inherited policies that live in the preset and change through behavior change records, and implementation assumptions that are not preserved. Added a compatibility scope and a test-evolution rule, and a test triage phase. Added §3.8 and the test-pinning counts in §3.6 as evidence.
- Withdrew four rules from revision 1: two implementations per port (now a justification rule with the justification stated per port), the blanket ban on function-local imports (now a direction rule), monotonically decreasing structural counts (now diagnostics), and the one-directory-plus-manifest target (now a described scope verified by substitution drills). Reduced verification to gates that prove behavior or independence and diagnostics that are reported.
- Made the storage boundary concrete: the consistency guarantees the current code relies on, the port contract that provides them, declared extension state with portability by declaration, migration ordering with owner-named refusal, and the honest cost of replacing a backend.
- Replaced the single `reports_to` with typed relations declared by the organization adapter, with authority and routing derived from relation kinds; showed chain-of-command, matrix-guidance and peer-swarm organizations under the model; stated the unresolved assumptions (one binding per member, conflicting command relations, runtime membership, external participants).
- Added the configuration lifecycle: applied snapshots, per-request and per-launch revision capture, impact classes with refusal by default, atomic apply and rollback, and per-dependency selection revisions. Reconciled the migration phases, scenarios, tradeoffs, verification, open decisions and module disposition accordingly.

**Revision 1** (2026-10-23 01:44 UTC). Initial evaluation of PR1 and the kernel/ports/extensions/presets proposal.
