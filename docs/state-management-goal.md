# Shared state management: goal and required completion

## Goal

Integrate reusable state management into Xorchestrate's active live-agent architecture. Components supply their states, events and guards; the shared engine evaluates and applies those rules consistently. Existing storage ports remain authoritative, native conversations remain persistent, and external effects stay outside database transactions.

This work does not reinstate Agentscient's retired conductor, dispatch, hand-grant or assignment workflow. Historical code supplies reusable mechanisms and failure witnesses. Current owner direction and the live-communication architecture govern their use.

Base: `a2ebb03f35402309500456b37099f978c51a94a9` on `master`. Delivery: a draft PR to `master`, with an exact-candidate evidence report. The owner controls merge and any deferral. No item below is declared deferred.

## Required completion

| ID | Required outcome | Decisive evidence |
| --- | --- | --- |
| S1 | Reusable declared states, events, guards and definition identity; configurable engine and definitions through composition. | Two unrelated machines; invalid/ambiguous definitions and unavailable transitions reject; a changed definition/guard changes a real application path. |
| S2 | Pure decisions do not mutate supplied state or context. Guard inputs cannot spoof authoritative current/proposed state. | Detached input witnesses, read-only decisions, guard refusal and malformed input controls. |
| S3 | Application transitions join the existing transaction and atomically retain state plus required transition history with current-version checks. | Shared Memory/SQLite contracts; rejected transition changes nothing; emission failure rolls back even when caught; stale same-state/ABA and contested-writer cases. |
| S4 | Recovery preserves durable identity, history and known outcomes across restart and reconciliation. | SQLite reopen; lost-response/reconcile traces; retained completed-but-unapplied result; old attempt/incarnation cannot settle replacement work. |
| S5 | All active finite durable lifecycle families and native activity use the shared mechanism on their applicable mutation paths. | Explicit integration census and executable paths for operation, session, invocation, delivery, delivery attempt, native call, measurement, native activity and bridge effects, including late results and runtime shutdown. |
| S6 | Known pre-effect refusal, ambiguous effects and completed-but-unapplied effects remain distinct through adapters, durable calls and activity. | Controlled pre-effect refusal does not leave unresolved activity; post-submission loss stays uncertain with no automatic replay; completed effect can reconcile without re-execution. |
| S7 | Current live communication and acceptance behavior survives integration. | Existing independent held-work completion, identity, callback, bridge, resume, delivery and acceptance scenarios; supported store/adapter compositions; controlled event order, legitimate progress and interruption/recovery. |
| S8 | The integrated candidate is validated and reviewable with honest evidence. | Static/boundary checks; full application tests; existing assertion-specific calibrations; composed three-harness protocol acceptance; required hosted CI; candidate/source identities, actual commands/results, omissions and unresolved failures stated. |

Shared scenario expectations come from controlled facts, not the new engine's answer. A standalone engine test, imported legacy-suite pass, a matching hash, or a count of passing tests is insufficient to establish S5/S7.

## Integration census

| State family | Intended integration |
| --- | --- |
| operation | Reservation, completion, failure, uncertainty, retained external result, reconciliation, late-result retention |
| session | Start/attach/reconnect, reservation, active/unknown, stop/detach, replacement, closed-session late results and runtime disconnect |
| invocation | Reservation and terminal/reconciled outcome |
| delivery | Queue, reserve, submit/fail/unknown, policy-authorized retry and explicit reconciliation |
| delivery_attempt | Reservation, settlement, reconciliation, late evidence without settling a newer attempt |
| native_call | Reservation, retained completion/refusal/uncertainty, current replay and evidence-based reconciliation |
| measurement | Reservation, completion/failure/uncertainty and recovery from retained adapter results |
| native activity | Identity-correlated accepted work, turns, callbacks, interjections and terminal observations; aggregate activity derived from outstanding work/uncertainty |
| native connection | Starting, connected, disconnected and closed connection lifecycle under the adapter's existing lock |
| bridge effects | Persisted intent/outcome and restart-to-unknown using the bridge host's existing persistence boundary |

The following existing semantics are preserved and explicitly accounted for: free-form work/contribution status, versioned owner requirements/candidate selection, append-only acceptance/receipt decisions with computed currentness, per-recipient acknowledgements, assignment/relation bookkeeping, and retained native observations. They do not acquire a fixed historical workflow merely to use a state engine.

## Reuse and compatibility

Inspect and adapt existing atomic movement, version/identity and shared-conformance mechanisms. The active implementation must not import legacy runtime infrastructure or create a competing state database. Default state definitions preserve the existing record vocabulary and public action contracts; any deliberate public change must be documented and exercised with its actual consumers.

State transition history identifies the definition, subject, event, source/target and record versions. Existing authorization, ownership, routing and evidence-currentness checks remain applicable. Required state history and optional telemetry have separate responsibilities.

The transaction port gains an explicit rollback-only operation so a failed required history append cannot be caught and followed by a partial commit. Both shipped stores and the shared contracts must implement this guarantee; extension authors must receive the updated port contract.

## Evidence and stopping condition

Keep the baseline and final candidate separate. Record test collection/execution, skips, errors and intended assertion failures honestly. This host's unavailable capabilities are verified in hosted CI rather than relabelled as passing local evidence. Authenticated vendor execution and scripted protocol evidence are different claims.

Completion requires every S1–S8 outcome to be evidenced against the integrated candidate and the PR to remain draft. If a requirement is unmet, it remains open with the actual reason. No automatic additional general review cycle or unilateral deferral is part of this goal.

