# Live test-kit integration completion

Goal: integrate the existing Agentscient test-kit mechanisms into Xorchestrate's
active hexagonal testing architecture so local and CI runners reuse concern
implementations and retain independent behavioral evidence.

## Required completion and evidence

| Requirement | Implementation | Evidence |
|---|---|---|
| Reuse existing kit mechanisms in active code | `scripts/testkit/model.py`, `core.py`, `adapters/command.py`; pinned source links in `docs/live-testkit.md` | `test_testkit_core.py`, `test_testkit_command.py` exercise full-plan preflight, results, timeout descendants and exact bytes |
| Both runners share configuration, recording, evidence and ownership | Acceptance Scenario and ComposedSuite; compatibility imports delegate, not duplicate | Existing acceptance scenario suite, 46-case composed protocol suite, configuration/integration tests |
| Cleanup failure cannot skip remaining owned resources | ResourceScope in acceptance clients, runtime/provider and bridge lifecycle | `test_acceptance_testkit.py`, `test_testkit_integration.py` inject close/capture failures and inspect retained errors and stopped hosts |
| Repeatable local and CI entrypoint | `python -m scripts.testkit run`; same command in both CI live jobs | Entrypoint tests, hosted tmux and multi-harness runs |
| Deterministically enforce test-tooling hex dependencies | Expanded existing generic contract; pure core, decoder, adapter and composition roles | `test_testkit_boundaries.py` plants forbidden dependencies and unclassified modules; existing oracle/wire guards retained |
| Preserve existing behavior and evidence binding | Existing scenario predicates, 46-case inventory, selected candidate and checker identities | Existing application/adapter suite plus workflow runs; no authenticated-native pass inferred from fixtures |

## Verification record

The PR links the final revision's CI run, retained test reports, composed run
receipts and architecture report. Local results and host limitations are recorded
in the PR. Native vendor acceptance requires the operator's configured clients
and authentication and is reported separately; fixture evidence is never labeled
as authenticated-native completion.

This work changes testing infrastructure and fixes cleanup defects exposed by the
extraction. It does not adopt the separate kernel/services proposal or change the
application's domain state policies. Discoveries remain evidence rather than new
requirements.
