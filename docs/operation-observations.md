# Operation observations and schedule controls

The shared native completion scenarios arm their observation before submitting
work. A completion must belong to the requested operation and the same native
source incarnation. Completion that arrives between the action and the first
wait stays available; another operation's terminal event cannot release the
wait. The adapter lives in `tests/hexagonal/support/completion_runtime.py` and
uses the existing application builder, store adapters, native peers, public
session poll transport and classified `native.wait_for`.

The arm-before-action and retained-completion pattern adapts
[UXor's transition observer](https://github.com/xormania/uxor/blob/89e607a4be48b2fd8f46f1b9e9465cba406fedee/tests/e2e/transitions.ts)
through the existing callback and process gates. Unlike silent recorder
truncation, an unread retained-window gap is an explicit failed observation.

`OperationObserver` reads a retained `ObservationSnapshot` from one source.
Arming captures its current counter. A ticket consumes the subsequent complete
window and matches source, incarnation, operation and boundary. A pre-arm record
cannot qualify, even when its other fields match. Missing counters, unread
eviction, counter regression and source replacement raise `ObservationError`;
these are insufficient observation, not proof that the product violated a
property. A matched record is retained by its ticket even if the recorder later
evicts it. An already consumed prefix may be evicted harmlessly.

The thread-safe `OperationLog` gives concurrent fixture callbacks this retained
window. The native completion adapter imports actual `native_observation.created`
records using the store's event witnesses, then retains a local observation
counter. This counter means order at the fixture's capture boundary. It does not
claim remote occurrence order or define a new telemetry/export format. The
fixture journal is unbounded for its short scenario lifetime; optional bounded
retention is explicit and is exercised by the shared observer contract.

## Proving a controlled boundary

Each process operation gets a fresh challenge containing its label, native
source, incarnation and token. The existing protocol peer acknowledges the
challenge with its actual turn/prompt identity before starting work. A held
start or tail writes an atomic activation receipt at that precise boundary.
Release requires a matching receipt with a nonempty native operation identity.
Paths and tokens are unique, so a prior invocation's files cannot supply either
activation or release. A fixture label identifies one submission; reusing it is
refused before delivery.

Callback holds reuse `ControlledCallbacks`: entry and return are recorded before
their corresponding event is published. Each prepared callback has an armed
entry ticket. Its activation identifies the actual provider invocation and the
provider incarnation. A prepared operation can be invoked once; simultaneous
duplicates are refused before either records a second activation or effect.
The claim lock is released before the callback waits, preserving overlap between
distinct operations. This is a provider witness; it does not pretend to identify
a native prompt by itself. The independent process receipt supplies that link
for native completion. Snapshot artifacts retain both sets of controls alongside
the existing completion evidence.

Controls hold each invocation independently, permitting two callbacks and tails
to overlap and finish in either order. Releasing a stage observes the next
requested stage or its exact terminal boundary. Cleanup deliberately releases all
owned gates even when no activation arrived, so a failed setup does not strand
a peer. The peers' existing 30-second watchdog remains a real, unscaled fault
control; it is separate from the harness allowance for observing a receipt.
Older fixture callers without an operation challenge keep their existing simple
gate protocol.

## Behavioral evidence

The reusable contract in `tests/hexagonal/contracts/operation_observation.py`
runs through Codex, Claude and Grok deterministic process peers using memory
and SQLite stores and direct and FIFO transports. It proves callback entry and
distinct native activation before release, rejects a competing completion while
the target remains held, and correlates each terminal observation to its actual
native operation. Existing aggregate-completion and independent-checker
contracts continue to exercise false-idle and held-work vetoes. These native
fixtures exercise process/transport/store boundaries, not vendor authentication.

Run the observer and control tests with the existing test kit's `framework`
family, and the composed scenarios with `work-acceptance`. The registered manual
calibration inventory uses isolated copies, exact source faults, independent
assertion sites and healthy controls:

```bash
python3 -m scripts.testkit run --suite calibration --inventory operation-observation
python3 -m scripts.testkit run --suite calibration --inventory native
```

The operation inventory plants an omitted operation comparison, a reused pre-arm
cursor, an unwitnessed gate release and a real native terminal attributed to all
operations. The last fault crosses the SQLite/process boundary and must fail
the contract while a single-operation completion remains healthy. This is a
finite scenario observation mechanism; general cross-source telemetry closure,
clock profiling and persisted evidence compatibility remain separate work.
