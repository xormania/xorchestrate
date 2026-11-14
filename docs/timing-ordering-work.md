# Timing and ordering work

This work makes elapsed time, event order, readiness and harness allowances
explicit at the boundaries that use them. xor requested implementation after
the [research and scope](https://chatgpt.com/library?file_id=libfile_3388f24d2324819191e1b74ce5165488&account_id=personal).
Current owner direction governs the work; the research is background and a
proposed scope, rather than an immutable specification.

## Source inventory

[temporal-uses.json](temporal-uses.json) preserves the first reviewed hotspots at
master `e2511f6` and records the grouped deadline review against `314e979`.
The initial entries remain historical; their current dispositions distinguish
already merged ordering work, this test-kit candidate and separate runtime or
readiness candidates. The review covers shared support, verification/acceptance,
execution/cleanup, runtime transports/providers, storage/telemetry and ongoing
services. It classifies purposes and observable boundaries instead of treating
every timeout literal as a defect. Linked tests are discovery leads; executed
coverage belongs in the candidate evidence.

The important existing mechanisms are:

- The state-store event log already owns commit-order sequence numbers.
  Record lists are key-sorted and cannot stand in for that log.
- Native observation waits already share injected classified monotonic budgets.
  This candidate propagates running boundaries into nested waits and blocking
  pipe/socket observations instead of renewing a full allowance at each step.
- Collector receipts preserve persistence order across capture-clock rollback.
  The merged checker work now carries retained order witnesses as well; neither
  persistence boundary proves arbitrary remote occurrence order.
- Callback gates and fixture holds already support controlled overlap. Reuse
  them and prove the intended boundary was reached before injecting a fault.

## First delivery group

The first group was delivered through separate review boundaries. Its purposes
and evidence requirements remain useful context for the following work.

| Slice | Result | Evidence required before handoff |
|---|---|---|
| Session selection | Choose the latest eligible session using its existing store-owned creation witness; declare unwitnessed legacy behavior | Memory/SQLite, equal/regressing UTC readings, ID order, eligibility, reopen and event pagination; test-first red proof |
| Checker observation order | Retain actual store witnesses, record fixture boundaries in the same serialization domain and use them through normalization and completion/exchange checks | Wall-clock changes and raw-record permutation preserve the verdict; missing/bad witnesses and false completion are rejected; existing calibration still detects the intended faults |
| Classified waits | Shared injected monotonic polling, remaining-budget clamping, diagnostic budgets and an opt-in factor for classified harness allowances | Oversleep red witnesses; safe conflicts retry, unsafe conflicts escape; scale cannot relax product guarantees or move deliberate fault controls |
| Inventory | Keep the purposes, affected paths, compatibility choices and remaining work inspectable | JSON/path/content validation and a pre-draft adversarial review; no behavioral coverage change |

Each behavioral PR uses separate test and implementation authors. Record the
test-first source, intended assertion failure, repaired candidate and commands.
Run the repository's static checks and affected behavior, obtain a focused
adversarial review before opening the draft, then get applicable hosted CI green
for xor's review. [PR quality](pr-quality.md) defines the existing process.

## Following delivery groups

The reviewed waits/budgets group now has concrete test-kit and runtime transport
review boundaries. The test-kit changes use one running `Deadline` for nested
observation, saturated pipe writes and partial-line readiness. Execution,
cancellation and cleanup remain distinct: command cleanup shares one forced
allowance, while terminal-host cleanup explicitly remains cooperative and
retains elapsed/overrun evidence. Reviewed shared observation helpers opt into
the existing harness scale; product settings and deliberate fault controls do
not. See [bounded observation waits](testing-framework.md#bounded-observation-waits)
for exact boundaries and limitations.

The runtime transport candidate separately handles request-level remainder
through writer admission, sending and framed response reads. It cannot depend
on test-kit infrastructure. Per-command provider settings, native startup/turn
phases, database contention, service-lifetime loops and deliberate gate
watchdogs retain their distinct purposes. The inventory marks those dispositions
and the remaining cooperative port boundaries explicitly.

The broader groups remain visible below. Their exact PR boundaries depend on
the evidence; an inventory or unused interface does not complete them.

1. **Shared observation contracts and compatibility.** Carry producer/source
   identity, incarnation, operation binding and source counters through actual
   capture/export/reopen/decoding consumers where cross-source claims need them.
   Add closure/gap semantics for finite proofs and distinguish invalid evidence,
   insufficient evidence and a violated property. Preserve existing xcontracts
   ownership and coordinate any required pin/release change with consumers.
2. **Remaining waits and cancellation.** Classify the remaining adapter and
   shared-support deadlines. Propagate remaining budgets into nested blocking
   calls. Declare whether each limit bounds useful execution, cancellation
   initiation or total return. Keep cleanup allowances, per-check timeout and
   whole-run cancellation explicit. Extend scaling only to reviewed harness
   allowances.
3. **Schedule controls and native parity.** Adapt operation-specific
   arm-before-action observations and boundary gates to the existing test kit.
   Exercise required competing schedules and retain an activation witness.
   Compare controlled adapters with real process/transport/store boundaries.
   The [shared operation controls](operation-observations.md) now integrate
   source/incarnation-bound observation tickets, retained completion and fresh
   native activation receipts into the existing completion matrix. General
   telemetry identity/export compatibility remains the separate first item.
4. **Environment measurements.** Provide a selectable passive clock profile in
   the managed local proof workflow and a bounded CI sample. Bracket wall reads
   with monotonic samples; retain uncertainty, dispatch delay, conditions,
   failed attempts and measurement version. Loaded WSL and actual Windows
   suspend/resume need that environment's retained witness.

The common verification matrix covers wall rollback/forward/frozen readings,
equal readings, source restart, harmless report reordering, missing proof,
completion/timeout races, cancellation scope and memory/SQLite/reopen. Choose
combinations by the property rather than requiring a full Cartesian product.

## Interpretation and open semantics

- A store sequence establishes order at one persistence boundary. A collector
  sequence establishes acceptance at that collector. Neither automatically
  establishes the occurrence order of arbitrary delayed remote reports.
- A successful observation predicate may finish after its harness allowance;
  an allowance that gates polling attempts is not a strict product latency
  measurement or a preemption mechanism. Document the chosen helper semantics.
- Advancing a fake clock does not execute timers or complete external I/O.
  Real subprocesses and waits still need an independent real watchdog.
- Old evidence retains its original meaning. Missing order provenance cannot
  be repaired by sorting UTC, IDs or raw-list positions.
- Define “latest” as the selected policy's observable creation/activation
  boundary. Reconnect does not silently become creation. Declare mixed legacy
  preference explicitly where original creation cannot be reconstructed.
- Deadline equality, completion-versus-timeout arbitration, suspend-sensitive
  policies and persisted calendar/lease behavior need a per-contract choice.
  Preserve an established intent; bring an actual unresolved behavior choice
  to xor rather than inventing a restriction.

The reported clock reversals and intermittent failures motivated the research.
They do not establish the prevalence of clock steps on WSL or hosted runners.
Controlled injection tests the defined fault; passive measurements characterize
the environments actually observed.

## Reuse provenance

The research pins detailed source references. The implementation should adapt
mechanisms through the active architecture, preserving their proof limits:

| Source | Mechanism to reuse | Limit to preserve |
|---|---|---|
| [Agentscient](https://github.com/xormania/agentscient/tree/91506128934b70a8967c626d6ece16d2c8a0815c) | Output locking and sequence assignment; fresh readiness challenges | Trace-before-write is not recipient acceptance; separate directions have separate counters |
| [multi-harness-proof](https://github.com/xormania/multi-harness-proof/tree/ad294398501ed119a436db54eca784be2fb10e76) | Observed boundary sequences, explicit holds and gated work | Collector order is local observation order |
| [CCE](https://github.com/xormania/cce/tree/99a1470620da4f8d60a9dc6117504cd60d24696d) | Port-based fault control, overlap activation witness, retained experiment inputs | Storage simulation is not a generic external-process scheduler |
| [UXor](https://github.com/xormania/uxor/tree/89e607a4be48b2fd8f46f1b9e9465cba406fedee) | Arm before action, retained completion, operation gates and all-attempt timing reports | Bounded recorder retention needs explicit gaps in a proof system |
| [Xorgate](https://github.com/xormania/xorgate/tree/cccc8d6af9599d01fffaee24a6b1985aeacd4937) | Commit evidence with completion; await diagnostic arrival separately | Wall-time lease and nested subprocess limits have different contracts |
| [Looplaw](https://github.com/xormania/looplaw/tree/3a7b846b5d9a7472c2d9c88a3977a23313b8d633) | Injected time for reproducibility and persisted-format checks | Prefer scoped injection over shared global clocks |

Historical policy and incidental fixture restrictions do not become requirements
through reuse. Product guarantees, test controls and optional diagnostics keep
their separate responsibilities.
