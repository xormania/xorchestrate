# Shared state management

Xorchestrate's active application and native adapters use the same declared state engine. The engine owns transition selection and validation. Lifecycle definitions supply states, events and guards. Existing stores, bridge persistence, adapter locks and external-effect boundaries keep their responsibilities.

The completion goal is in [state-management-goal.md](state-management-goal.md). This is a state mechanism for the current live-agent architecture; it adds no conductor, dispatch workflow, role hierarchy or work-completion policy.

## Reused lessons and current consumers

| Prior evidence | Mechanism applied here | Current proof |
| --- | --- | --- |
| Recovered [atomic movement and history](../legacy/agentscient/ops/conductor/tools/machine.py), with [rollback/contention tests](../legacy/agentscient/ops/conductor/tests/test_machine.py) | Join the existing transaction, check the current version, retain state and required history together. Persistence remains behind Xorchestrate's store port. | [Shared Memory/SQLite contracts](../tests/hexagonal/contracts/state.py), including contested writers and caught append failure |
| Recovered [stale-generation and ABA regressions](../legacy/agentscient/ops/conductor/tests/test_coordination.py) | Every transition, including a same-state update, advances its subject's version. | Shared stale-version and ABA scenarios |
| [Proof-at-boundaries AAR](https://github.com/uscient/agentscient/blob/456457b9a0a9eb1ff285ee2d3e40535b6f722cdb/ops/conductor/AAR-2026-09-12-proof-at-boundaries.md#L41-L65): declared capabilities had no actual consumer | Changed guards must affect assembled application, adapter and operator-host paths. | [Application composition](../tests/hexagonal/test_state_composition.py), [native activity](../tests/hexagonal/test_state_activity.py), [separate bridge CLI process](../tests/hexagonal/test_state_bridge_cli.py) |
| [Test-census AAR](https://github.com/uscient/agentscient/blob/456457b9a0a9eb1ff285ee2d3e40535b6f722cdb/ops/conductor/AAR-2026-09-13-test-census.md#L61-L71): incidental textual counts changed without behavior changing | Shared state and boundary scenarios define expectations; assertion-specific faults prove enforcement. | [Application integration contracts](../tests/hexagonal/test_state_integration_contracts.py), [state fault calibration](../scripts/verification/calibrate_state.py) |

These citations justify specific mechanisms and witnesses. They do not make the historical organization, roles, dispatch rules or review gates current requirements.

## Composition and replacement

Existing configuration uses the default engine automatically. Its explicit form is:

```json
{"state_engine": {"adapter": "declared", "options": {}}}
```

`state_engine` follows the existing adapter factory convention. A trusted `module:factory` can replace the implementation. Composition passes the same instance to the application and drivers exposing `bind_state_engine(engine)`, before their sessions launch. Direct application construction accepts `state_engine=...`. Direct native drivers and bridge hosts create equivalent default definitions when no engine is supplied.

The replacement contract is `decide(machine_name, current_state, event, context=None)` and `apply(tx, kind, key, event, data, expected_version=None, context=None, *, machine_name=None)`. Native work and bridge effects use `decide` inside their own existing serialization/persistence boundaries. Durable application records use `apply` inside their caller's store transaction. An engine must preserve the refusal, version and atomic-history guarantees described below.

The default definitions are in [`lifecycles.py`](../xorchestrate/lifecycles.py) and [`activity.py`](../xorchestrate/activity.py). Each has a name and positive integer version. Persisted transition history retains the definition version; in-memory native activity retains its subject state and version. Configurations are trusted deployment input, not tool-call arguments. Replacing an existing definition requires compatibility with the events and data its consumer supplies; no automatic persisted-record migration is implied.

## Declaring a machine

`options.definitions` adds or replaces definitions. State fields are configurable; creation has a `null` source. For example:

```json
{
  "state_engine": {
    "adapter": "declared",
    "options": {
      "definitions": {
        "parcel": {
          "version": 3,
          "state_field": "phase",
          "states": ["queued", "delivered"],
          "transitions": [
            {"event": "register", "sources": [null], "target": "queued"},
            {"event": "handover", "sources": ["queued"], "target": "delivered",
             "where": {"proposed.owner": "xor", "approved": true}}
          ]
        }
      }
    }
  }
}
```

Python callers can supply immutable `MachineDefinition` and `Transition` values, including pure callable guards, to `StateEngine`. JSON `where` conditions use the existing `RuleGuardrail` matching rules. Guards receive detached JSON context. For stored transitions, `current` and `proposed` are authoritative record values and replace any caller-supplied values of those names. Exactly one eligible transition is required; absent, rejected and ambiguous transitions are errors.

`options.guards` adds a policy to every transition of a named machine, in conjunction with its existing guard. Actions are named `machine.event`. For example, `{"guards": {"operation": {"default": "deny"}}}` blocks operation reservation before an external effect. This capability is exercised through a real composed invocation in [`test_state_composition.py`](../tests/hexagonal/test_state_composition.py), including the legitimate remedy and successful idempotent replay.

## Transactions and required history

`apply` verifies the current record version, checks the declared source/event/target, writes the new record and appends `state.transition` to the same transaction. A same-state update still advances the record version and retains a transition. An old version cannot become valid again merely because a record returns to the same state.

History retains machine name, definition version, subject kind/key, event, source/target, previous/new record versions, and optional actor/time provenance. It does not copy arbitrary guard context or native payloads. Existing application event authorization remains applicable.

**Storage extension contract:** transactions now implement `abort()`, marking the whole transaction rollback-only. After a write or required-history failure, the engine aborts even when its caller catches the exception. The transaction must reject further use and must not commit on normal context-manager exit. Both Memory and SQLite implementations satisfy this contract. Custom stores must implement it before they can apply transitions. External calls stay outside transactions; this does not turn external effects into database transactions.

## Effect knowledge and recovery

An exception's name does not establish whether an external effect happened. `before_effect()` marks a failure only while execution cannot yet have crossed the relevant effect boundary. Structured `not_started` or `failed` evidence is retained as a definite failure; unclassified post-submission loss stays unknown. Existing completed-but-unapplied results remain available for reconciliation without re-execution.

When an effect is known completed but local retention or settlement fails, callers receive structured `result_unapplied` evidence with `outcome: completed` and the actual result. This deliberately replaces a bare downstream storage exception where needed, so transport serialization cannot erase effect knowledge. Consumers must inspect the retained evidence and reconcile; the same call identity does not execute again. A stale bridge attachment still receives the established lease-fence refusal, while the host retains the completed result for the current owner.

Native activity tracks identities of outstanding work. Aggregate activity is derived from active work and unresolved uncertainty; completing an old identity cannot clear a newer one. Raw process handles and protocol replay evidence remain adapter infrastructure. Bridge effects use their existing persisted intent/result snapshot and become unknown when a host restarts with an unresolved pending effect.

Work/contribution status labels remain caller-defined. Requirements and candidate selection remain versioned, acceptance currentness remains computed from evidence, and acknowledgements remain per recipient. The finite lifecycle census in the goal document records this boundary explicitly.

## Evidence

The shared contracts in [`contracts/state.py`](../tests/hexagonal/contracts/state.py) run against independent Memory and SQLite fixtures. The application contracts exercise actual creation, all seven durable lifecycle families and restart/reconciliation. Native held-work, callback, bridge and completion contracts remain independent of the production verdict. Deliberate source-fault calibrations require their specific assertion witness and an unaffected positive control; collection/setup errors and skips cannot count as detection.

The PR evidence report records the exact candidate and executed commands. Hosted CI additionally runs both supported Python versions, Unix socket paths, tmux acceptance, existing calibrations and the composed three-harness protocol fixture. Scripted protocol acceptance does not claim authenticated vendor execution.
