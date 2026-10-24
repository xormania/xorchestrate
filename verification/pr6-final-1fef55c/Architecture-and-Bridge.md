# Architecture and bridge lease review

**No material blocker found in this bounded track.** Measured product:
`1fef55c0c0cadd5e1417c0e010f272f6447f8ea4`, tree
`75ecb3ef4edbc381012fe74e416a9abd3fb5c667`. The disposable worktree remained clean
and at this identity before and after both probes. No product source was edited.

## Hypotheses and results

| Hypothesis | Independent exercise | Result |
| --- | --- | --- |
| Changed generic verification and shared contracts have inward dependency direction | Actual candidate source graph; seven deliberate source-map violations covering transitive HTTP, executable package initialization, aliased filesystem, relative concrete decoder, product-derived expected result, decoder-to-verdict and aliased dynamic loader dependencies | Every deliberate violation rejected; clean pure, contract and adapter graphs accepted |
| Boundaries still permit legitimate substitution | Two positive dependency controls plus an independently written positional-packet decoder for two unlisted peers; same packet input without the decoder | Injection and adapter-local wire interpretation accepted; missing decoder receives no proof credit |
| Normalization retains provenance and contradictions | Nine independently expected decoder cases; raw snapshots compared byte-for-value before/after a decoder that deliberately mutates its supplied record copy | Raw input unchanged, raw references and explicit/null/absent actor claims preserved; contradictions rejected |
| Bridge associates a coherent asynchronous call with its previously unclaimed turn/prompt | Newly written native port through real Unix and FIFO hosts/clients; callback arrives before native observation | Both late turn and prompt completion observed for the correct current lease |
| Lease association cannot transfer historical ownership or allow stale calls to seed a new turn | Pre-attachment calls, mixed new/old turn and prompt labels, explicit-null native identity, two owner replacements, stale callback/new-prompt attempts | Invalid completion/callback credit rejected; coherent callback and final completion remain usable |

The boundary probe has 12 declared cases: three clean graphs, seven violations,
and two positive controls. The substitution probe has nine cases. The bridge
probe has 20 declared checks per transport, 40 total. Counts summarize the finite
matrix; the retained expected values and raw observations are the evidence.

## Semantic observations

`scripts/verification/model.py`, `ports.py`, `evidence.py` and `core.py` remain
free of concrete native, transport, store and production-verdict dependencies.
`checker.py` and `normalize.py` choose decoders at composition boundaries;
`adapters/native.py` translates wire observations. The application envelope
projection preserves independently supplied identities and keeps each untouched
source record and raw reference. Shared scenario expectations live in contracts;
concrete fixture construction lives in support modules. Concrete adapter tests
appropriately depend on their adapters.

The positional-packet decoder is new reviewer code rather than a renamed bundled
decoder. The candidate fixture adapter supplies raw exchange construction only;
the expected acceptance is explicitly declared from controlled claims in
`architecture_probe.py`. The subject's verifier does not define the oracle.
Pristine packets, absent optional native actor, fully coherent alternate work and
qualified unrelated historical reuse pass. Explicit-null native actor, a typed
returned work contradiction, a requested-only work contradiction and a duplicate
with null actor fail. Removing decoder injection from identical pristine packets
also fails. Every produced fact retains the original raw envelope/ref, and the
raw records survive a decoder's destructive `pop` on its input copy unchanged.

The alternate-work control was inspected independently from its verdict:
18 selected-work claims across native packets, requested/completed callbacks and
typed returns all name `explicit-alternate-work`; session/source work remains
`session-work`. The paired requested-only mutation fails. This supports the
repair's distinction between selected work and source/session work, rather than
manufacturing success by omitting contradictory records.

The bridge probe uses public native and bridge ports and inspects public poll
observations. It does not use private binding tables as its oracle. A native call
observed before any attachment remains stale after attachment; its turn is not
forwarded as current. Callback-first calls acquire both labels when the later
coherent observation arrives. A new turn coupled to an old prompt allows the new
turn to finish without transferring the old prompt. Explicit-null native IDs or
incarnations cannot seed completion; the same textual call ID with a coherent
qualified native identity remains usable. Calls belonging to either of two
previous owners remain stale after the third attachment and cannot seed new
prompt ownership. The third owner's fresh callback and completion still pass.

## Replay and raw evidence

Use the exact pinned clean candidate and the dependency environment described by
the root handoff. Output directories must not exist:

```sh
PYTHONDONTWRITEBYTECODE=1 "$PYTHON" architecture_probe.py \
  --candidate "$CANDIDATE" --out "$FRESH/architecture-execution"
PYTHONDONTWRITEBYTECODE=1 "$PYTHON" bridge_probe.py \
  --candidate "$CANDIDATE" --out "$FRESH/bridge-execution"
```

- `architecture-execution/summary.json` records source identities and the complete
  declared matrix; `boundary-results.json` retains source-map mutations and exact
  dependency chains. `decoder-0.json` through `decoder-8.json` retain each raw
  input, normalized facts, expected verdict and actual report.
- `bridge-execution/unix.json` and `fifo.json` retain every declared expectation,
  result, current lease ID and raw public poll observation. `summary.json` records
  both matrices and source identities. No bridge signing secret or resume token
  is published.
- `architecture.log` and `bridge.log` retain process summaries. Both processes
  exited zero with all their independently declared expectations satisfied.
- `inputs/` holds pinned connector-read source snippets used while Git fetched;
  the measured runs imported the disposable worktree, not these snippets.

No targeted product probe failed. During read-only preparation, the guessed
`contracts/bridge_turns.py` path returned 404 (the actual shared contract is in
`contracts/completion.py`), and the system interpreter lacked pytest. Neither was
credited as product evidence; the installed review venv supplied pytest 9.1.1.

## Limits and stopping point

The deliberate dependency violations alter in-memory copies of source read from
the disposable worktree; they do not edit production files or execute forbidden
imports. The guard is an architecture regression check, not a sandbox or proof
against arbitrary Python obfuscation. The documented broader test/orchestration
migration remains deferred. The bridge probe is a deterministic native port and
does not claim authenticated vendor acceptance. Root owns the required shared
contracts/full suite and the other review tracks. This finite track is complete.
