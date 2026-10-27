# Managed local proofs

`scripts/proof.sh` provides setup, foreground execution and managed run controls
around the [shared test-kit](testing-framework.md). It uses the same selected
checks, command receipts and independent verifiers as the direct Python entry
point. Native processes are still launched by the product's existing adapters.

The default protocol mode exercises real application processes with deterministic
peers. Native mode uses your installed, authenticated harnesses and never falls
back to protocol peers. A protocol result does not establish vendor acceptance.
Authenticated native measurements are performed locally by the operator; keep
their results pending until that run produces the required observations.

## Set up once

Use Python 3.12 or newer. Managed terminal and acceptance workflows also require
Linux or WSL2, tmux, PTYs, Unix sockets and a readable `/proc` in the same process
namespace. Native mode additionally needs the configured Codex, Claude Code and
Grok Build installations and your normal local authentication.

From the checkout:

```bash
./scripts/proof.sh setup --profile "$HOME/.config/xorchestrate/proof.json"
./scripts/proof.sh doctor --profile "$HOME/.config/xorchestrate/proof.json"
./scripts/proof.sh run --profile "$HOME/.config/xorchestrate/proof.json"
```

Setup installs the repository's declared application/test dependencies into the
configured external environment. It creates a private `native.json` template and
workspace beside the profile once, and preserves existing profiles and reusable
native configuration. The launcher establishes the external Python bytecode
cache before imports and propagates it to child checks. Keep the environment,
cache and evidence root outside the measured checkout. Installation is setup
work; complete it before measuring the candidate.

The editable profile selects mode, suite, acceptance drivers, optional test
families, native application configuration, owner brief, deadlines and evidence
root. Relative profile paths resolve against the profile's directory. Invalid
fields, incompatible options and empty selections are errors. Command-line
selection overrides are retained in the saved effective plan.

The generated profile uses these reusable defaults; setup also records the
current checkout's owner-brief path:

```json
{
  "schema_version": 1,
  "mode": "protocol-fixture",
  "suite": "all",
  "drivers": ["codex"],
  "families": [],
  "config": "native.json",
  "output": "runs",
  "environment": "environment",
  "timeout": 180,
  "check_timeout": 600,
  "run_timeout": null,
  "shutdown_grace": 10
}
```

`--timeout` controls existing live scenario bounds; `--check-timeout` controls
deterministic/calibration command bounds. `--shutdown-grace` gives interrupted
work time to finalize before escalation. An optional `--run-timeout` bounds the
whole run; the default has no overall deadline. Foreground `run` also supports
cooperative interruption and this overall deadline without a dashboard.

`doctor` reports readiness for that selection without authenticating or launching
a model. Missing prerequisites are reported against their dependent checks;
independent selected checks can still run. A malformed plan is rejected before
execution. Use `--suite static` for checks that need no native client or tmux.

## Run and inspect

Use the foreground command for deterministic development or CI:

```bash
./scripts/proof.sh run --profile "$HOME/.config/xorchestrate/proof.json" --suite checks
```

For a managed run:

```bash
./scripts/proof.sh start --profile "$HOME/.config/xorchestrate/proof.json" --detach
```

Keep the run-directory path printed by `start`. It is the argument to subsequent
controls; there is no global run-ID registry to search. In an interactive
terminal, `start` attaches by default unless `--detach` is selected. Detached
success means the run was accepted and launched, not that its proof passed.

```bash
./scripts/proof.sh list --profile "$HOME/.config/xorchestrate/proof.json"
./scripts/proof.sh status RUN_DIRECTORY
./scripts/proof.sh status RUN_DIRECTORY --json
./scripts/proof.sh watch RUN_DIRECTORY
./scripts/proof.sh attach RUN_DIRECTORY
./scripts/proof.sh wait RUN_DIRECTORY
```

`status` distinguishes the saved lifecycle, observed process liveness, selected
checks, provisional and validated results, cleanup and collection. Retrieving
status successfully is not a successful proof. A stale saved `running` record
after a crash is incomplete or unknown, not evidence of a live worker.

`watch` follows retained progress. Leaving a viewer or detaching a terminal does
not cancel the run. `attach` selects a currently live, owned interactive terminal,
including terminals inside acceptance checks. It does not resume a native
conversation or create another harness. In native mode it waits with progress
status until an actual native PTY is available; it does not attach to a worker
pane as a substitute for native interaction. Terminal identity is checked again when attaching;
saved shell attachment text is not executed.

Detach from a native PTY with **Ctrl-]** to return to the control interface. A tmux
terminal uses its normal detach binding, **Ctrl-b d** by default. At the control
dashboard, enter a terminal number to select an identified owned target, `w` to
follow current targets or `q` to leave the viewer. An initial target can also be
selected with `attach RUN_DIRECTORY --target 1`. Selection is checked against
the current owned-target list.

`wait` is the command to use when a caller needs the final result. Its exit status
reflects authoritative proof evidence and required operational finalization.
Missing final evidence, failed required cleanup or failed required collection
cannot produce success. An attachment's exit status is only a viewer result.

## Run installed harnesses locally

Edit the generated private `native.json` referenced by the profile.
Use the existing [example](../scripts/verification/native-config.example.json)
as the field reference. Set the actual executable argv, a deliberate workspace,
model settings and explicit environment forwarding for each configured driver.
Keep your normal vendor authentication in place. Setup and ordinary proof startup
do not update vendors or rewrite authentication.

The initial protocol profile retains this native-config reference without using
it. Selecting `--mode native` activates it. Set the profile's acceptance driver
selection before starting native acceptance.

The standard three-harness selection uses session names `codex`, `claude` and
`grok`. Those names belong to the existing suite fixture. Select the acceptance
driver or drivers explicitly; a Codex-only acceptance check is not individual
acceptance coverage for all three vendors. Shared static checks still run once.

```bash
./scripts/proof.sh doctor --profile "$HOME/.config/xorchestrate/proof.json" --mode native \
  --driver codex --driver claude --driver grok
./scripts/proof.sh start --profile "$HOME/.config/xorchestrate/proof.json" --mode native \
  --driver codex --driver claude --driver grok --detach
./scripts/proof.sh attach RUN_DIRECTORY
./scripts/proof.sh wait RUN_DIRECTORY
./scripts/proof.sh collect RUN_DIRECTORY
```

Before relying on unattended native runs, verify actual prompt visibility and
human input through public attachment in both acceptance and multi-harness
paths. Check switching must reach the current terminal rather than a stale pane.
Keep native terminal traffic separate from MCP protocol input/output. Readiness
checks and a visible pane alone do not prove this interaction works with the
installed vendor versions.

Review the retained requested, resolved and independently observed settings.
An unobserved setting remains unknown. A configured working directory is not a
fresh worktree or an operating-system isolation boundary. The normal native
workload proves controlled work and communication; it does not prove arbitrary
tool interruption, simultaneous model inference or hostile-agent isolation.

## Stop, close, collect and repeat

```bash
./scripts/proof.sh stop RUN_DIRECTORY
./scripts/proof.sh close RUN_DIRECTORY
./scripts/proof.sh collect RUN_DIRECTORY
./scripts/proof.sh start --from RUN_DIRECTORY
```

`stop` requests run cancellation. The executor stops scheduling new checks,
preserves completed outcomes, marks unstarted checks as not run and gives active
work a bounded opportunity to save and clean up. Escalation targets only owned
resources and remains visible in the result. Repeated or simultaneous requests
are safe; an accepted request and completed shutdown are separate facts.

A check timeout affects that check's owned work and records a failed result;
independent checks continue. It does not set the run-wide cancellation flag.
Explicit cancellation or an overall run deadline stops further scheduling,
including cancellation received during a check's timeout cleanup. A required
check's timeout keeps the aggregate unsuccessful even if later checks pass.

`close` removes an owned finished dashboard and retains evidence. It refuses to
skip finalization of an active run. It is not a cancellation command.
The supervisor remains available after proof finalization to own the retained
presentation until `close`. Cleanup evidence lists that presentation separately;
it does not treat surviving native work as an expected dashboard resource.

`collect` creates a fresh private, non-overwriting archive and readable summary.
It exports redacted JSON receipts/events from a committed evidence generation and
records the inventory and exclusions. Raw logs, private input/configuration
snapshots and mutable native diagnostics remain private and are excluded by the
export policy; the bundle is not an atomic snapshot of every process. Native PTY
output is retained privately with an 8 MiB cap. Collection does not rewrite
canonical proof results or upload anything. A failed export remains visible and
can be retried without overwriting an earlier archive. Review exports before
sharing: bounded redaction cannot guarantee removal of sensitive prose.

`start --from` starts a new run from saved selection and input snapshots, with
explicit overrides if requested. It uses current source and tools and records
differences. It allocates fresh application/native identities, nonces, state and
evidence. It does not replay an ambiguous native effect, continue an old session
or reuse a previous pass. Retain failed attempts alongside later results.

Worker exit without authoritative final evidence leaves an incomplete run.
Supervisor termination, SIGKILL or host loss can prevent orderly finalization;
retained state supports inspection, not automatic message/effect replay.

## Concern ownership and reuse

| Mechanism/source | Current owner | Managed adaptation and evidence boundary |
|---|---|---|
| Shared suite definitions and catalog | `scripts.testkit.composition`, `contracts/testing.json` | Expand profile selection through the same definitions; record selected and executed IDs; static checks remain unique. |
| Agentscient check execution | `scripts.testkit.core.execute_checks` | Extend the existing loop with scoped cancellation and incremental results; do not add another executor. |
| Agentscient resource scope and command bounds | `ResourceScope`, `adapters.command` | Preserve cleanup ownership, bounded grace/escalation and interrupted command receipts. |
| Existing Xorchestrate tmux supervisor | `scripts.testkit.adapters.tmux` | Reuse private-socket targets and PID/start identity fencing; real tmux checks cover parsing and foreign-resource preservation. |
| Existing native launch and configuration | Product native adapters, `adapters.configuration` | Route terminals through the existing launcher and derive private per-run configuration/SQLite; never launch duplicate peers. |
| Candidate/effective-input capture | `adapters.evidence`, `adapters.identity`, static manifests | Bind actual bytes, ignored inputs, symlink targets, commands and validation status; check integrity again before delivery. |
| PoC managed operator workflow | Operator services and storage/process/terminal adapters | Adapt fresh-run controls, retained partial diagnostics and distinct exports; preserve the shared verifier as the verdict owner. |
| PoC native evidence/capture lessons | Existing native observations and independent verification checker | Keep submission, ingestion where observed, response, receipt and completion distinct; exact-session capture is optional diagnostics. |

The revision-3 handoff package contains the 41-item historical transfer ledger and
the unchanged source guide. Its guide SHA-256 is
`0827c7d4bccb812103a44174d99358c0df53a1f6b0b012c9d6efeb8de88004a1`
(66,708 bytes). That identifies historical text, not newly authenticated native
results. The [PoC source](https://github.com/xormania/multi-harness-proof/tree/ad294398501ed119a436db54eca784be2fb10e76)
is a mechanism reference; historical roles and policies do not become operator
requirements automatically.

## Focused native schedule disposition

These schedules come from [behavioral verification](behavioral-verification.md#focused-native-repair-schedules)
and the current [owner brief](implementation/owner-brief.md). They remain separate
from the 46-case multi-harness inventory. The deterministic references below
establish executable support to inspect; they are not installed-vendor evidence.
Each local native execution or inability to produce its required ordering needs
its own result in the external completion record. Discovery of a schedule does
not add a new wrapper requirement, and a 46/46 result does not close these rows.

| Focused schedule | Existing executable reference / owner guarantee | Native disposition |
|---|---|---|
| Acceptance replay after revised requirements or material evaluator context | `test_native_cache_currentness.py`, `test_work_acceptance.py`; B1/B2/B5 current acceptance and replay binding | Local measurement pending; retain stale-replay rejection, fresh/direct controls and no-reexecution evidence for each selected vendor. |
| Overlapping Claude callbacks with an actual Stop hook | `test_claude_overlap.py`, `test_native_completion_state.py`; H2/H3 activity and completion correlation | Local measurement pending; retain the real hook while B remains gated, then valid idle after B completes. |
| Overlapping Codex work and callback/completion ordering | `test_native_completion_state.py`, `test_native_sessions.py`; H2/H3/H7 outstanding-work evidence | Local measurement pending; retain both release orders and completion-before-callback control. |
| Current and historical inspection after denial | `test_inspection_history.py`, `test_delivery_scope.py`; H3/H5 authorization and retained trusted evidence | Local measurement pending; preserve authorized rows while excluding denied nested records from public views. |
| Contradictory or null native/callback identity | `test_identity_contracts.py`, `test_callback_identity.py`; H3/H7 independent identity and effect binding | Local measurement pending; recheck copied native evidence with targeted contradictions/nulls and an unchanged valid control. |
| Observed callback across reconnect with changed authority | `test_external_bridge.py`, lifecycle identity contracts; H4/H7 connection and incarnation fencing | Local measurement pending; gate callback dispatch and retain its original authority or rejection after replacement. |
| Applied effect across reconnect with explicit reconciliation | `test_external_bridge.py`, `test_state_bridge.py`, native-resolution cases in `test_delivery_adversarial.py`; H4/H7 unknown effects and replay protection | Local measurement pending; count effects, retain unresolved state across reconnect, and reconcile before a new effect. |

## Completion record template

Copy this template to the external run/evidence package or PR description and
fill it there. The table in the measured checkout is a template, not a result.
Finish measured code, documentation and catalog edits before qualification.
Record the candidate revision/tree, full content identity and effective checker
input identities. Before delivery, compare them again; later measured edits need
renewed qualification rather than attaching a new hash to older evidence.

| ID | Outcome | Evidence to record outside the candidate |
|---|---|---|
| C1 | One supported entry point and Python baseline | Cold/warm shell launches, spaces/argv forwarding, interpreter diagnostic, direct/managed plan parity. |
| C2 | Reusable conservative setup/profile | Initial/repeated setup receipts, preserved operator config/auth, declared dependencies and exact binaries. |
| C3 | All operator controls | Foreground/managed/control scenarios, current-terminal attachment, detach/close/wait semantics and simultaneous controls. |
| C4 | One owner per concern | Sole native launcher, persistent supervisor readiness and finalization after CLI exit, explicit artifact writers. |
| C5 | Process/terminal lifecycle | Real tmux boundaries, foreign-resource survival, current prompt routing, scoped timeout/cancellation and bounded escalation. |
| C6 | Honest readiness and independent checks | Malformed-plan rejection, check-scoped setup/timeout failure with later independent execution, aggregate failure. |
| C7 | Fresh repetition | Two complete run directories, new IDs/nonces/state, saved input reuse and preserved prior evidence. |
| C8 | Evidence and delivered-candidate binding | Exact selection/receipts, incremental validation state, effective inputs/configuration, settings, post-run/pre-delivery integrity. |
| C9 | Unmodified candidate | Pre-import external cache/child propagation, no generated candidate outputs, current-source cold/warm invalid-source control. |
| C10 | Failure/overlap diagnostics | Partial launch/crash/timeout/cancellation, retained completed rows, concurrent exports, stale/interrupted publication and collection failure. |
| C11 | Hex and test architecture | Classified new roles, deterministic architecture pass and isolated planted violation, complete catalog and separate advisory diagnostics. |
| C12 | Existing guarantees | Shared inventory/JUnit, assertion-specific calibration and declared protocol/tmux results with cleanup. |
| C13 | Actual local native use | Installed/authenticated versions, selected acceptance drivers, full declared native cases, independent identities, prompt interaction and cleanup. |
| C14 | Public native repetition | A second local native `--from` run, fresh identities, retained first attempt and explicit differences. |
| C15 | Reviewable delivery | Matching final identities, exact reproduction commands, external results table, seven-schedule dispositions and checksummed historical source. |

Keep implementation, deterministic verification, local native measurement and
review states separate. For this delivery, request one Codex review and one Codex
Security review per PR after its checks are green, resolve their findings, and
leave merging to xor.
