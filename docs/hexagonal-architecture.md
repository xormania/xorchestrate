# Xorchestrate application architecture

The application coordinates work, actors, messages, sessions, tool invocations,
assessments, and retained evidence. It depends on purpose-oriented ports. Adapters
implement those ports, and the composition layer selects implementations from
trusted local configuration.

The purpose of this boundary is practical: changing a transport, database, agent
harness, or policy should not require changing the orchestration use cases. A
role name, a stack preference, or an agent's earlier recommendation must not
become an application invariant merely because it appeared in the first project.

Dependencies point toward the application contracts; composition selects the
adapters that fulfill them:

```mermaid
flowchart TD
    CLI["CLI / JSON lines"] --> App["Orchestration application"]
    MCP["MCP input"] --> App
    App --> Ports["Application-owned ports"]
    Store["Storage adapters"] -. implement .-> Ports
    Tools["MCP / CLI providers"] -. implement .-> Ports
    Sessions["Native / bridge sessions"] -. implement .-> Ports
    Policy["Routing, guardrails, evaluation"] -. implement .-> Ports
```

## Responsibilities

| Boundary | Owns | Does not decide |
| --- | --- | --- |
| Application | Work and coordination records, action sequencing, effect tracking, retained results | Database, transport, executable, vendor, project stack |
| State store | Transactions, record versions, event sequence, persistence | Agent hierarchy or work acceptance |
| Capability provider | Discovery and invocation of external tools | Authority inferred from tool output |
| Session driver | Starting and communicating with an agent session | Who must plan, delegate, or approve work |
| Routing policy | Recipients for a destination in the current actor/relationship context | A compulsory organization chart |
| Guardrail | Whether a requested action is allowed under explicit policy | Model reasoning or arbitrary host isolation |
| Evaluator | An assessment with rationale, confidence, and evidence | Permission to override policy |
| Acceptance policy | Decisions over current owner requirements and bound measurements | Replacing missing or failed required evidence with confidence |
| Session selection / delivery scheduling / retry / receipt | Replaceable routing-to-native-delivery responsibilities (`SessionSelection`, `DeliveryScheduling`, `DeliveryRetry` and `ReceiptPolicy` ports) | Conflating submission, receipt, response, completion and acceptance |
| Composition and input adapters | Trusted configuration, adapter lifetime, CLI/MCP encoding and identity | Domain-specific project practices |

The application module keeps action dispatch, authorization context and its
verification, guardrail authorization, record creation and update, work, actor
and message records, capability discovery and invocation (with the invocation
effect hooks) and telemetry reads. The remaining use cases live in focused
modules that it assembles:

| Module | Responsibility |
| --- | --- |
| `actions.py` | The public action catalogue and its closed input schemas |
| `effects.py` | The effect journal: intent before I/O, outcome retention, replay and reconciliation through reservation, application and release hooks supplied by the host |
| `session_lifecycle.py` | Session start, attach, reconnect, resume, send, poll, stop and detach, including the session effect hooks |
| `delivery.py` | Routing-to-native delivery, native observations and exchange verification |
| `assessment.py` | `WorkAssessment`: triage and escalation through configured evaluators; like acceptance, it depends only on contracts and host callbacks |
| `acceptance.py` | `WorkAcceptance` (below) |
| `inspection.py` | `InspectionProjector` (below) |

Shared canonical JSON digests, public record views and action descriptors are
foundation helpers in `model.py`.

The public ports, the values and errors they exchange and the adapter factory
convention are defined in [xcontracts](https://github.com/xormania/xcontracts),
a separate stdlib-only Apache-2.0 package, so adapters can be written and
checked without depending on Xorchestrate. `ports.py` and `model.py` re-export
them unchanged; the application and adapters keep importing from there. The
driving-side client transport ports (`ClientTransport`, `ClientConnection`,
`PeerIdentity`) stay in `ports.py`: entrypoints choose them, not
configuration, and they evolve with the project server. The pull request
delivery ports (`PullRequestHost`, `PullRequestWorkspace`) also stay there
while they are new and still changing. In the architecture
profile (`contracts/hexagonal.json`), the foundation and every role that
reaches it may import only `xcontracts.ports` and `xcontracts.model`; the
conformance kit (`xcontracts.testing`) and the factory types are allowed only
in test support (`tests.hexagonal.support.*`) and collected tests. xcontracts
never imports Xorchestrate: its own boundary test and
`tests/hexagonal/test_xcontracts.py` check that. See [writing an adapter](adapters.md).

Public inspection is shared by work, acceptance, delivery and native-history
reads. `InspectionProjector` owns typed record projection, nested historical
results and freshness revalidation through the store and explicit context,
authorization and validation callbacks. It receives no application facade or
delivery/acceptance workflow; authorization uses the application's current
guardrail. Projection copies leave trusted stored records and receipt-policy
inputs unchanged. Caller/provider payloads remain opaque outside declared
application-owned envelopes.

`WorkAcceptance` owns requirements, candidates, contributions, measurements and
acceptance decisions. The application delegates those use cases to the component
and supplies storage, the existing transaction-aware record mutations, authority
revalidation and an invocation callback through `Application.call("invoke", ...)`.
Live suppliers expose the current acceptance policy and material configuration
identities, including changes during evaluation. Shared assessment input-binding
calculations are pure functions used by both assessment and acceptance. Public
acceptance reads compose the component's transactional view with the inspection
projector in the application; acceptance imports neither its host nor projection,
delivery or infrastructure. Measurement replay and result reconciliation retain
their existing operation evidence and never launch another effect implicitly.

The core is usable with supplied ports without importing SQLite, subprocesses,
MCP machinery, or the historical runtime. Memory and SQLite stores pass the
same xcontracts `StateStore` conformance kit. External capabilities can be added through discovery, without
editing a central list of project-specific commands.

CLI and MCP are input adapters for the same application operations. MCP providers
and CLI providers are also output adapters for project context and tools. A port
expresses an application need; it is not a wrapper for a particular wire protocol.

The CLI `call` command performs one operation and closes its owned adapters.
`run` keeps a runtime open and processes JSON lines shaped as `{"action": "...",
"arguments": {}}`, returning a `result` or `error` envelope per line. MCP `serve`
also keeps the runtime open. Use a persistent input connection for session work;
one-shot commands are appropriate for durable work records and discrete tool
operations.

MCP input reaches the application through the driving-side `ClientTransport`
port: the `stdio` adapter serves the launching process, and the `unix` adapter
serves the long-running [project server](project-server.md) that agent
harnesses reach through `connect`. Each connection is its own MCP session with
its own actor; all sessions share one application and one set of bounded
worker pools.

## Composition and configuration

The CLI runs a set-up [project](projects.md): the operator layer
(`xorchestrate/instance/`) finds it from the working directory or
`--project PATH`, checks the approval of its tracked configuration, merges the
project's and the user's layers and renders them to `config.rendered.json`.
It then calls `build(config, project=ProjectContext(...))` with the explicit
project context: name, instance id, data directory and default worktree.
Composition never imports the operator layer and looks nothing up itself.
Built-in SQLite paths and provider/session working directories resolve
against the base directory (a project's instance directory); paths starting
with `${repo}` resolve per call to the caller's worktree. Custom adapter
options follow that adapter's contract. The rendered defaults use SQLite at
`${data}/state.sqlite3`, configurable routing, and an allow policy; no
provider, session driver, or evaluator is required for basic work tracking.
`build()` without a project still works for composition-level callers, with
no telemetry unless `telemetry_defaults` is supplied.

The operator layer has its own driven ports:
`Companion` (project-scoped, extensible records in the user's companion),
`VersionControl` (worktree, history and remote queries),
`RepositoryHosting` (whether a hosted repository is private) and
`TerminalHost` (where long-lived local work runs, see
[terminal host](terminal-host.md)). The first three are defined in xcontracts
and re-exported by `xorchestrate/ports.py`. `TerminalHost`, its values and its
`terminal_host` kind are defined locally in `xorchestrate/ports.py` while they
are still changing, as the pull request delivery ports are. `RecoveryPolicy`
and its values are local for the same reason. Both move to
xcontracts once they are stable; until then, third-party terminal-host
adapters are not supported through the public contracts. Their adapters (`git-directory`, `git`,
`github`, `tmux`, `detached-process`) are registered in composition's `BUILTINS`
like every other adapter, but as operator kinds: they are selected from the
user's home (`settings.json`, a project's `instance.json`), never by a project
configuration section. `reconnect` and `manual` are the built-in
`recovery_policy` adapters; that section is project configuration, not an
operator kind. See [projects](projects.md#companions-version-control-and-hosting).
The multiplexer adapter has its own architectural role, `terminal_multiplexer`.
Only composition may import it, and its name is a forbidden literal in the
roles that never reach composition, so nothing else can hardwire it.

The top-level sections are:

| Section | Selection |
| --- | --- |
| `version` | Configuration format version, a positive integer; see below |
| `store` | One state-store adapter and its `options` |
| `state_engine` | Lifecycle state engine; built-in `declared` (default) accepts `definitions` and `guards` options |
| `telemetry` | Observation collector; built-in `local` with selected `corpus` (`sqlite`) and `artifacts` (`filesystem`) storage; always on for a project (the `ProjectContext` supplies `TelemetryDefaults`) unless `enabled: false` |
| `providers` | Named capability providers, each with `adapter` and `options` |
| `sessions` | Named session drivers, each with `adapter` and `options`. `hosted` wraps another driver and runs it in the [terminal host](terminal-host.md#hosted-sessions) |
| `guardrail` | An enforcement adapter and its options |
| `routing` | A routing adapter and its options |
| `evaluators` | Named assessment adapters and their options |
| `assessment` | Which evaluator supplies triage, when to escalate, and the heavier evaluator |
| `acceptance` | Current required-check acceptance policy |
| `session_selection` | Eligible native session for each recipient |
| `delivery_scheduler` | Manual/default or immediate/configured delivery scheduling |
| `retry_policy` | Retry permission after a definite failed attempt |
| `receipt_policy` | Verification of complete exchange evidence |
| `recovery_policy` | Which interrupted sessions to recover automatically after a hard restart; built-in `reconnect` (default) or `manual`. See [recovery](terminal-host.md#recovering-after-a-container-or-machine-failure) |
| `session_fallbacks` | Explicit unavailable-name to configured-driver substitutions |

An adapter selection may use a supplied alias or a trusted installed
`module:factory` implementation. Factories receive options, with dependency-aware
built-in adapters assembled by composition. The exact convention, including
the companion's `version_control` argument and which options are resolved,
is part of xcontracts (`xcontracts.factories`); see [writing an adapter](adapters.md). Disabled named entries (`enabled:
false`) are not activated. Configuration is read for the assembled application;
unrelated historical source edits do not invalidate ongoing work or retained
results. Restart with the intended configuration to activate a change.

### Format version and structural validation

Each release declares the configuration format versions it reads in
`SUPPORTED_CONFIG_VERSIONS` (`xorchestrate/composition.py`; currently `{1}`) and
prints them with `xorchestrate --version`. A configuration's optional top-level
`version` selects one:

| `version` | Result |
| --- | --- |
| absent | Read as `1`; `describe` reports a `configuration_version_missing` warning. A later release will require the key |
| a supported version | Read as is |
| newer than every supported version | Refused with `configuration_version_unsupported`, naming the version and this release |
| older than every supported version | Refused with `configuration_migration_required`, asking for the configuration to be migrated to the oldest supported version (`target` in the details). The planned `instance migrate-config` operator command will perform that rewrite; it does not exist yet |
| not a positive integer | Refused with `configuration_error` |

Configuration is never rewritten on load. Both refusals are ordinary
`OrchestrationError` codes, rendered by CLI and MCP like every other failure.

`validate_config(config, base_dir)` is the structural half of `build`: format
version, section names, adapter shapes, option containers and path resolution.
It returns a `ResolvedConfig` and imports no factory module, constructs no adapter,
launches no process and writes nothing, so tooling can check a configuration
safely. `build` calls it first and constructs only after it succeeds, so a
structural error never leaves partially constructed adapters or new state files.
Adapter option contents remain each factory's contract and are checked at
construction; `assessment` and `session_fallbacks` remain application-validated.

This changes which error `build` reports for some invalid configurations.
Structural errors are reported before any factory runs, so a configuration with
both a structural error and a factory option error (for example `routing: 5`
and a negative SQLite `busy_timeout_ms`) now reports the structural
`configuration_error` rather than the factory's `invalid_request`; the factory
error appears once the structure is valid. Path errors in built-in options have
specific messages instead of `Cannot construct <kind> adapter <name>.`: a
non-string path or working directory reports `<kind> adapter <name> paths must
be strings.` and an unresolvable path (such as one containing a NUL byte)
reports `Invalid <kind> adapter <name> path.`; the code is still
`configuration_error`. Within the structural checks, sections are checked in
construction order.
An invalid `telemetry` section is not an error in either path: it is reported as a
`telemetry_configuration_invalid` warning and telemetry is unavailable, as before.
`describe` prints these warnings beside the operations as `{"operations": [...],
"warnings": [...]}`.

Telemetry is an optional output port (`TelemetrySink`: capture, bounded scans,
scope resolution, payload reads and bundle snapshot/verify/restore). When the `telemetry` section is absent or
`enabled: false`, no collector is constructed; a caller of `build()` can supply
`TelemetryDefaults` to construct the local collector for an absent section. The
`local` adapter bridges application, native, capability, evaluator and process
observations to the in-tree collector (`xorchestrate/adapters/telemetry_collector`),
whose corpus and artifact storage are selected adapters (`sqlite` and `filesystem`
by default). A failed
construction yields a visible unavailable state rather than preventing unrelated
work, and capture failure does not change the observed action's result. See
[active telemetry](telemetry.md) for options and the `telemetry_*` operations.

Configuration is trusted execution input. It selects commands and installed code;
it is not accepted from provider output. Tool arguments carry operation data,
not replacement executable paths or shell fragments. Provider invocation uses
configured argument arrays and JSON data, not shell interpolation.

The default actor is `owner`. A local CLI invocation or stdio MCP server binds its
chosen actor when started; the project server binds the actor each connection
asserts. The caller's worktree identity is bound the same way and reaches the
guardrail context as `worktree` (see [projects](projects.md#room-for-per-worktree-access)). This is a local trust boundary, not remote login or
multi-tenant authentication. A future network adapter must establish its caller's
identity before calling the same application boundary.

## Work, organization, and judgment

Work can be created and updated without a planner, a manager chain, or a model
account. Actors can have free-form roles and relationships. Routing and declared
guardrails can use those records; none of the historical CO/XO/NCO roles is built
into the application.

Project goals, source observations, UX context, explicit opinions, and checks come
from the caller and configured MCP/CLI capabilities. The application stores the
provided context and evidence. It does not impose Symfony paths or treat observed
source structure as a declared standard. An assessment result remains an
assessment; it does not rewrite guardrails or configuration.

External capability calls receive the caller, work, and operation identifiers and
the selected capability descriptor as context. The descriptor binds each call to
its authorized discovery snapshot, including during concurrent discovery.
Project data is supplied through explicit tool arguments, rather than
implicitly copying the work's entire evidence history to every provider. Policies
can inspect the richer internal context; assessment requests deliberately include
the context being evaluated.

Assessment is optional. A selected triage evaluator can be Jev or another adapter.
The assessment configuration names the heavier evaluator and the conditions for
escalation, including a confidence threshold and uncertain outcomes. Missing
confidence or unresolved output must remain visible. An unresolved assessment is
not silently converted into permission, and the default work-tracking path does
not require evaluator credentials.

Policy enforcement and inference are separate responsibilities. A policy may
allow an action, deny it, or require review. Evaluation may inform the caller's
next decision, but a provider cannot turn its own recommendation into authority.
Choose guardrails for the deployment's real boundaries and connect any desired
approval flow explicitly.

For example, the rules adapter can require review for a deployment provider while
leaving unrelated actions available:

```json
{
  "guardrail": {
    "adapter": "rules",
    "options": {
      "default": "allow",
      "rules": [{
        "id": "review-release",
        "effect": "review",
        "actions": ["invoke"],
        "capabilities": ["deploy/*"],
        "reason": "Owner reviews releases"
      }]
    }
  }
}
```

Rules can match configured actors, roles, providers, capabilities, and exact
context values. Numeric values compare by value (`1` matches `1.0`); booleans
remain distinct from numbers, including inside objects and arrays.
A matching denial wins over review, and review wins over allow.
Review is a blocked action with an explicit reason; this adapter does not invent
an approval authority or persist a blanket bypass.

Discovery is authorized as `capabilities` for the selected provider. Invocation
is then authorized as `invoke` against the discovered descriptor. With a
default-deny policy, allow the required discovery and invocation separately.
Descriptors stay bound to that invocation; a reported catalog change requires
fresh discovery and authorization. Tool annotations remain supplied metadata,
not proof that an executable will honor them.

The configurable routing adapter accepts direct actors, role selections, and
named relationships, for example `{"actors":["reviewer"]}` or
`{"relationship":{"name":"consults","direction":"outgoing"}}`. Multiple
selections form a union. Broadcast and inclusion of the sender are options,
not consequences of a built-in management role.

## External effects and evidence

External effects use explicit idempotency keys. The stored operation binds the
key to its request. A repeated completed request can return the retained result;
a different request cannot reuse the same identity. The application records
intent before external I/O, performs I/O outside the database transaction, and
then retains the outcome and associated events.

This is not a claim of exactly-once execution across arbitrary tools. A process
may disappear after an external effect but before its result is retained. Such
an outcome stays unknown until reconciled; replaying the same request does not
blindly execute it again. Inspect the operation and external evidence before
resolving it. A failed or unknown operation is distinct from successful work.

`invoke` accepts `capability`, `arguments`, and `idempotency_key`, with optional
`work_id`. Session start, send, poll, and stop also require effect keys. Keys are
bound to the caller as well as the operation and payload. Use `get_operation` to
inspect a retained effect. `resolve_operation` requires its current version, a
verified outcome, and a reason; it records that reconciliation without repeating
the external operation. A result retained before a local application failure can
be reconciled from the retained data.

Journal failures use a shared public diagnostic envelope. Typed
`OrchestrationError` messages, codes and JSON-valid details are public; arbitrary
exception messages, class names, codes and detail attributes are replaced by a
fixed `external_error` diagnostic. Invalid diagnostic metadata cannot prevent a
known completion from being recorded as `completed_unapplied`. Only typed,
JSON-valid completion metadata supplies an exception-carried external result;
normal successful provider results keep their existing contract.

Retained records, responses and telemetry that name a failure take its code from
`public_error_code`: a typed failure's code, otherwise the site's documented
default (`external_error`, or a specific one such as `evaluation_unavailable`).
They do not record an untyped exception's class name, text or attributes.
Terminal adapters that report a local operating-system failure use
`terminal_support.os_error_code` (an adapter helper, since errno is not a
foundation dependency), which gives its errno name (for example `EACCES`) and otherwise
behaves like `public_error_code`. Native delivery reads an exception-carried
result only through the typed completion metadata above. A source check
(`test_only_public_error_helpers_read_exception_code_or_details`) rejects
`getattr(..., "code")` and `getattr(..., "details")` calls outside `model.py`, and
`type(name).__name__` on the exception an `except ... as name` handler caught; it
does not cover other spellings.

New journal diagnostic fields are versioned. Inspection, replay and reconciliation
project unversioned historical diagnostics conservatively, reporting
`legacy_error_retained` when a diagnostic was replaced. This projection does not
purge historical storage or rewrite provider results. Existing databases need no
migration; removing historical sensitive bytes requires a separate explicit
storage-retention action.

Successful session-start reconciliation uses only the adapter result already
retained as `external_result`. A caller cannot supply or replace its backend
identifiers. An unknown start without that receipt cannot be reconciled into an
imported native connection. After investigating the external outcome, the caller
can record the attempt as failed and make a separate authorized start request;
this does not automatically retry the original operation.

Records use compare-and-replace versions, and each storage transaction commits
its records and events together. Evidence and returned data remain inspectable;
the application does not infer successful delivery from a transport merely
starting or a model claiming completion.

Session drivers retain backend identifiers in application records. Durable
application state does not itself make a vendor process durable. Process lifetime,
resume support, and reconnect behavior belong to the driver and must be reported
accurately. An unavailable session handle is not a license to target a reused PID
or silently create a replacement conversation.

To resume, `start_session` accepts a top-level `resume_session_id` identifying a
stored application session. The requested driver must match that session's
driver. The application authorizes `send_session` against the source session's
actor, work, and evidence context, and `start_session` against the requested
destination; it verifies both authorization snapshots before the effect.
Configured policy can permit delegation across actors or work items. A source
with an unresolved pending or unknown effect must be reconciled before ordinary
resumption. Explicit `reconnect_session` can restore observation/control while
retaining the source uncertainty fence; it cannot silently authorize another
send. Runtime shutdown marks owned connection records disconnected. A crash can
leave a stale durable state, so adapters also validate their live handles.

Resumption creates a new connection record with source lineage as provenance.
That lineage does not automatically propagate later policy revocation to the new
record; subsequent actions evaluate current policy in the new session's context.
Drivers opt into the `ResumableSessionDriver.resume(backend,
specification)` port and map the stored opaque backend to their native protocol.
Caller-supplied `specification.resume` native identifiers are rejected.
Resumable records link through `start_operation_id` to an adapter-issued receipt.
Pre-fix stored sessions lacking that provenance require a fresh start; caller
reconciliation results cannot import their native identities.

The supplied Codex adapter accepts complete command arguments, such as
`["codex","app-server","--listen","stdio://"]`. Its configured `thread` and
`turn` options carry native settings. Authorized resumption opens the stored
native thread in a new process. Sending acknowledges submission; continuously dispatched native callbacks and
notifications establish progress and completion separately. Polling reads retained
observations. Native tools are governed by the chosen native policies, while
application-provided dynamic tools cross the application's guardrails.

Drivers that expose application tools opt into
`ApplicationToolSessionDriver.bind_application_tools(capabilities, handler)`.
The handler receives an action, JSON arguments, trusted session context, and an
optional adapter invocation key. Drivers that answer repeated native calls from
their own call ledger may also define `bind_application_replay(handler)`, through
which composition revalidates a completed cache hit without a new effect. Composition binds the caller and work context,
and scopes effect keys to the configured driver. Each adapter translates its
own protocol, tool descriptors, and request identities; the shared wiring does
not require Codex request fields. Native requests that an adapter does not
translate are rejected as `unsupported_method`. Conflicting application/native tool names
must be resolved in configuration before sessions start.

Native execution policy belongs in trusted adapter options: Codex `thread` and
`turn`, or ACP `session` settings, including any MCP server commands. By default,
Codex and ACP accept only empty caller-supplied `specification.parameters`.
An operator can delegate bounded choices through an `options.request_parameters`
JSON Schema; the driver validates those choices before spawning a process.
Configured native settings override delegated values. Native session identities
and Codex dynamic tools remain driver-owned even when a schema permits extra
properties. See the [bounded delegation example](../examples/README.md#bounded-native-parameter-delegation).

The generic JSON-RPC driver uses configurable session start/send/poll/stop methods
for an installed bridge. ACP, Grok Build and Claude Code are also supplied
protocol adapters. The external-session host exposes ownership-aware attachment
without terminating its native conversation when a client disconnects. The transport
uses bounded frames, timeouts, explicit environment forwarding, and process
cleanup. These mechanisms do not make an arbitrary executable safe; choose the
process environment and capabilities for the intended work.

## Reuse and extension

Earlier Agentscient implementations live upstream at the pinned revisions in
[extraction provenance](extraction.md); no in-repo copies are kept. Useful protocol
handling, integrity mechanisms, and fixtures can be adapted behind the new ports.
The active package does not depend on the historical runtime and thereby
inherit its hierarchy, mandatory evaluator setup, source-hash coupling, or
historical delivery policy. The active application now owns durable delivery
attempts and composes explicit session selection, scheduling, retry and receipt
policies; message routing retains the exact actor/relationship snapshot used at
enqueue. Ordinary caller acknowledgements remain separate from native receipts.

Historical source regression tests are not part of the application suite. Their
passing behavior is evidence about that source, not a preservation requirement for
the new architecture.

There is no automatic migration of imported state and no assertion that every
historical capability has been ported. The current adapter tests use real local
process boundaries and fixtures. Live vendor/model availability, a real Jev
endpoint, and project-specific acceptance require the actual tools, credentials,
and workspace. See [validation](../.github/CI.md) for the checks and limits.

When extending the system, implement or replace the boundary that owns the
variation, verify the relevant contract, and keep policy choices inspectable.
A new abstraction earns its place by separating a real responsibility; the port
list is allowed to evolve with the system.

## Behavioral preservation and verification

See [native sessions](native-sessions.md), [terminal host](terminal-host.md),
[external session lifecycle](external-session-bridge.md),
[work acceptance](work-acceptance.md), and the [implementation handoff](implementation/HANDOFF.md).
Owner outcomes and candidate revisions remain separate from subordinate work.
Native call replay is durably fenced, including actions without application effect
keys. Observation and control remain available during uncertain effects. Only
pre-effect optimistic authorization conflicts are eligible for bounded callback
retry; every retry reevaluates current authorization. A committed message enqueue
is never repeated because immediate dispatch later conflicts.

The deterministic full suite and local native procedure use the active
composition and retain requested coverage separately from measured coverage.
Protocol fixtures do not establish authenticated vendor behavior. Native
acceptance and independent review have their own explicit states.

