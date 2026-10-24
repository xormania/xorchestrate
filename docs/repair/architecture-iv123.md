# IV01–IV03 repair architecture and remaining migration

This document describes the focused architecture boundary added with the repairs
following the independent review of product commit
`e565863ae72180571678c8f6118ee6e00b129e2e`. The review publication at
`0f55a1a031a047b8730ef37bc6eda20e17e3cdb0` is evidence, not the product candidate.
The consolidated repair handoff records the resulting candidate commit and tree,
commands, results, failed attempts and immutable evidence links.

## Repaired boundaries

| Layer | Responsibilities | Dependency direction |
| --- | --- | --- |
| `xorchestrate/delivery.py` inspection projection | Project application-owned typed assessment bindings using current visibility, without altering internal assessment inputs or opaque payloads. | Application model and ports; the existing application dependency guard applies. |
| `xorchestrate/exchange.py` verified receipt policy | Reconcile independent native/request/completion identities and typed returned message/evidence claims with their durable effects. | Application evidence values and model helpers; independent from verification verdict logic and concrete protocol decoding. |
| `xorchestrate/adapters/bridge.py` native observation adapter | Preserve a current callback lease's turn/prompt relation when its first asynchronous native tool observation supplies that relation. | Concrete bridge/native adapter boundary; ownership policy remains explicit and does not enter shared scenarios. |
| `scripts/verification/model.py` | Shared evidence primitives and verification errors. | Standard library only. |
| `scripts/verification/ports.py` | Observation decoder contract. | Shared types and standard library; no vendor or verdict selection. |
| `scripts/verification/evidence.py` | Normalize retained application evidence with injected decoders; keep explicit claims, provenance and raw record references. | Model and ports; concrete decoders supplied from outside. |
| `scripts/verification/core.py` | Reconcile claims and apply shared completion, exchange and lifecycle invariants. | Pure verification types and helpers; no production verdict, transport, database or native protocol decoding. |
| `scripts/verification/adapters/native.py` | Decode concrete native observations into vendor-neutral events, including accepted submissions and their eventual native turns. | Model and ports; cannot depend on the verdict it supplies. |
| `scripts/verification/checker.py`, `normalize.py` | Compatibility and composition facades selecting default concrete decoders and supplying them to pure logic. | Inward to invariants and outward to concrete adapters at the composition boundary. |
| `tests/hexagonal/contracts/` | Shared behavioral scenarios, fixture/subject protocols and independent expectations for inspection, completion and identity. | Standard library and contract modules; no concrete fixture, product or verification implementation imports. |
| `tests/hexagonal/support/` | Concrete application/evidence fixture adapters and test composition. Translate semantic fixture operations to storage and native protocol forms. | Contracts, application composition and verifier subject interfaces. No imports from collected test modules. |
| `tests/hexagonal/test_*_contract*.py` | Select implementations and collect shared scenarios. | Contracts and concrete support implementations. |

The facades preserve established caller interfaces. A separate checker checkout
is useful isolation, but its verdict is not an independent expectation. The
shared contracts instead specify expectations from fixture-controlled facts:
current grants and denied actor identifiers, explicitly held or released work,
and deliberate coherent or contradictory identity claims.

### Verification evidence and interchangeable implementations

An observation decoder translates wire records into facts. It does not grant a
case passing credit. Normalization retains applicable explicit actor, application
session, native session, incarnation and work claims with their record provenance
so the invariant layer can reject disagreements across records. An absent
optional claim stays absent; a contradictory explicit claim cannot be replaced
by a valid claim from another record. Opaque arguments and payloads remain raw
values rather than an invitation to recursively infer identity.

The same independent identity scenarios also exercise the application's verified
receipt policy through a separate subject adapter. That comparison exposed
product-side joins which could borrow a valid observation's identity and accept
contradictory typed returned message/evidence claims. The focused expansion checks
applicable actor, session, incarnation and work claims, including duplicates and
explicit nulls. Returned effect identifiers remain bound to durable effects;
additional explicit sender/source/actor/work claims must agree. Historical
callbacks containing only an identifier and genuinely absent optional claims
remain valid. Reused call labels in coherent retired or unrelated session scopes
do not become contradictory evidence for a current call merely by sharing a
label. This applies the repair to demonstrated receipt behavior without making
the production receipt verdict an oracle for the verifier.

Completion facts distinguish an accepted submission, a started turn and a
finished turn. The shared invariant accounts for accepted work before a native
start notification. Product activity is observed separately from the verifier
subject. Held callback, deferred-start and continuation-tail controls provide
expectations independent of either implementation's activity ledger. Releasing
all held work exercises valid eventual completion.

Those controls also exposed a bridge preservation defect: an asynchronous native
tool observation could be the first observation carrying its turn or prompt
identity, leaving a later legitimate completion without a recorded ownership
relation. The bridge adapter now records that relation only for a call already
bound to the current lease and with coherent native identity. It does not
transfer an already owned turn or let a stale call claim a new turn. The shared
`preserve_turn_ownership` contract uses an injected native port across FIFO and
Unix socket bridge transports, covering valid initial/replacement completion,
historical ownership, stale calls and contradictory native identity. Concrete
turn/prompt field selection lives in `support/bridge_turns.py`.

Inspection scenarios are shared across memory and SQLite composition. Completion
scenarios are shared across the applicable native fixture implementations and
configured direct/bridge compositions. Identity scenarios use multiple native
encodings, plus an unlisted peer with an injected custom decoder. Applicability
is explicit: a native implementation need not support an operation that its
public capability contract does not promise. Concrete protocol-specific decoder
tests remain appropriate in the adapter layer.

The evidence-driven receipt and bridge expansions remain part of the affected
scope. They do not imply that the broader receipt, lifecycle or transport fixture
architecture listed below has already been migrated.

## Automated boundary enforcement

Run the existing application guard and the new scoped verification/test guard:

```sh
python -m pytest tests/hexagonal/test_boundaries.py \
  tests/hexagonal/test_verification_boundaries.py -q
```

`test_verification_boundaries.py` parses imports without importing the subjects.
It follows local dependency edges transitively, including relative imports,
`from package import child` and executable package initializers. It protects the
pure verification roots, every shared contract module, concrete decoder direction
and affected fixture interfaces. Pure roots use a small explicit standard-library
allowlist; transport, database, process and filesystem facilities do not become
available through a helper imported by a protected module. Dynamic module loading
and source execution are rejected in protected layers, including aliased loading
imports. A supplemental AST check catches known native wire discriminators copied
inline into a shared layer; this supplements the dependency graph rather than
replacing it with a list of vendor names.

Persistent deliberate violations demonstrate that the guard rejects transitive
transport/store/adapter imports, dynamic loading, an invariant importing a decoder,
and a scenario deriving its expected answer from production verdict logic.
Positive controls permit dependency injection and concrete decoding in the
adapter layer. These checks are architecture regression tests, not a security
sandbox capable of proving arbitrary Python code harmless or of discovering every
possible future wire encoding. Review is still required when a new dependency,
protocol or contract interface is added.

## Follow-up migration after merge

The affected repaired paths have explicit shared contracts and adapter boundaries.
The rest of the existing test and verification architecture has **not** been
migrated. Existing tests remain as preservation coverage; this repair does not
rename or delete their public selectors. Follow-up work should preserve their
capability evidence while separating shared behavior from incidental fixture
construction restrictions.

| Remaining area | Concrete starting points | Follow-up completion criterion |
| --- | --- | --- |
| Collected tests used as fixture libraries | `test_assessment_inputs.py` imports helpers from `test_work_acceptance.py`; `test_inspection_history.py` and `test_delivery_scope.py` import `test_application.py`/`test_delivery.py`; `test_delivery.py` imports `test_application.py`; `test_callback_identity.py` imports `test_raw_evidence_calibration.py`. | Move reusable composition to support modules with explicit fixture interfaces, while retaining independent scenario expectations and all current selectors. Extend the no-collected-test-import guard to each migrated area. |
| Completion/currentness tests with native private state and shared helper coupling | `test_aggregate_completion.py` and `test_native_completion_state.py` import `test_native_cache_currentness.py`; that file also imports `test_work_acceptance.py`; `test_bridge_repair_calibration.py`, `test_claude_overlap.py`, `test_native_sessions.py`, `test_claude_sessions.py` contain concrete native protocol or driver state. | Separate genuine adapter protocol tests from shared completion/currentness contracts. Use observable controlled work for shared scenarios; preserve native-only assertions at the adapter boundary. |
| Assessment and inspection fixtures outside the repaired typed binding cases | `test_assessment_inputs.py`, `test_work_acceptance.py`, `test_inspection_history.py`, `test_delivery_scope.py`. | Share relevant behavior through fixture ports across applicable policy/store/provider implementations; keep expected visibility and assessment outcomes independent of production helper logic. |
| Older raw evidence/calibration fixtures | `test_callback_identity.py`, `test_completion_evidence.py`, `test_raw_evidence_calibration.py`, `test_verification.py`. | Extract wire encoders and evidence builders into explicit adapters and reuse semantic contracts where applicable. Preserve malformed-input and calibration controls rather than substituting normalized happy paths. |
| Full multi-agent verification orchestration | `scripts/verification/multi_harness.py` runtime/protocol configuration and suite orchestration; `scripts/verification/lifecycle.py` runtime setup; `fixture.py`, `capture.py` and CLI composition. | Separate orchestration policies from concrete runtime, transport, storage and file capture factories behind injected interfaces, retaining full-suite commands and raw evidence references. Concrete runtime selection stays in a composition boundary. The focused invariant/decoder separation does not claim this entire orchestration migration. |
| Repository-wide boundary coverage | Tests outside `tests/hexagonal/contracts/` and existing concrete verification entrypoints. | Expand protected roots only after each area's dependencies and fixtures have been migrated. Avoid allowing forbidden dependencies solely to make a broad graph check pass. |

The IV04 encoding-sensitivity finding remains a separate follow-up: BOM or legacy
encoding handling can produce missed detections even though unrelated exceptions
cannot earn assertion credit in the tested scope. The pre-existing focused test
`test_known_bad_per_turn_idle_is_detected_by_product_and_independent_checker`
also has a snapshot/status synchronization issue to address independently. Neither
is silently counted as completed by this architecture migration.

Authenticated vendor acceptance remains the owner's planned local work. The
shared contract, fixture protocol, composed-protocol, calibration and dependency
checks are deterministic evidence; they do not imply authenticated vendor
acceptance or a merge decision.
