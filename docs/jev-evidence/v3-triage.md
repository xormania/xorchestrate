# Post-test triage independent detection evidence

The initial **18 new cases** each have an intended assertion red across **12 deliberate faults**, paired with passing healthy controls. Mutations were applied only to disposable snapshots using the existing command/evidence services. Fresh external bytecode caches prevent stale-code witnesses.

The once-only review added **8 focused regressions**. The actual pre-repair source failed all 8 intended assertions with no setup errors. The repaired affected scope passed **136 tests** on Python 3.12.16.

| Fault | Assertion failures |
|---|---:|
| advisory_flag_removed | 1 |
| candidate_identity_ignored | 2 |
| foreign_metadata_repaired | 1 |
| partial_lanes_discarded | 2 |
| failure_detail_optional | 2 |
| unrun_treated_as_failed | 1 |
| source_budget_ignored | 1 |
| inventory_validation_removed | 4 |
| source_atomic_disabled | 1 |
| summary_html_not_escaped | 1 |
| triage_became_blocking | 1 |
| all_checkout_checks_ignored | 1 |

Exact patches, selectors, source/test hashes and failure messages are retained in [v3-triage.json](v3-triage.json).

```sh
python -m pytest -p no:cacheprovider tests/hexagonal/test_jev_post_test_triage.py tests/hexagonal/test_ci_result.py -q
```

The review controls cover deleted/modified focal evidence, missing before-side input, explicit HTTP transport, coherent metadata packing, bounded multi-case receipts, and malformed nested judgments. The malformed-data tests catch product exceptions into explicit assertions; an import/setup error never counts as detection. Initial missing temporary upstream dependencies were excluded.

Provider answers are controlled fixtures. These records establish detection and integration behavior, not live Jev accuracy or measured engineering savings.

## Compacted source identity integration

After the final request-compactor repair was propagated, both real-Git deleted/modified source cases failed at `PROJECTED_SOURCE_FILE_IDENTITY_RETAINED` on `81ceeb0`. The previous atomic consumer lost the file hash, module, role, revision side, Git commit and shared source ownership because those fields had moved to the file table. No setup error supplied this red result. Switching both current and before-side collections to the shared `source_units` expansion retained the exact original identities, focal IDs and base commit. The two new cases and three existing raw/missing-before controls passed (**5 green**); Ruff passed. Exact input hashes and the targeted command are recorded in `v3-triage.json`.

## Many-unit source packet integration

The controlled 114-unit selection contains **18,012 source bytes**, three candidate owners and nine atomic subjects. The unchanged `59ab69a` atomic/request bytes failed the intended `ALL_MANY_UNIT_ATOMIC_FACETS_COMPLETE` assertion; no setup error counted. Factoring repeated role metadata and already-selected provider ownership let all nine subjects finish through three controlled provider calls under the default budgets. The test also compares every complete source body, recoverable role, retained evidence and ownership lists. The full affected module passed **27 tests**, including existing healthy controls; Ruff passed. Exact source/test hashes and commands are retained in `v3-triage.json`. This checks bounded request composition with typed fixture answers, without claiming live-model accuracy.

## Hosted launcher fixture repair and failed-run triage

The first hosted PR78 run [43052026182](https://github.com/xormania/xorchestrate/actions/runs/43052026182) failed the existing isolated launcher's `cold.returncode == 0` assertion. The copied architecture tooling now imports `scripts.ci`, but the isolated fixture omitted that dependency; Ruff correctly reported I001 under the incomplete namespace. The same local assertion reproduced the failure on `c2a6a4f`. Including `scripts/ci` in the fixture preserved the existing cold/warm cache and lint guarantees; all **17 static-check tests passed** with exit 0. No product lint rule or assertion was weakened. Exact hashes and an excluded sandbox process-hook attempt are recorded in the JSON receipt.

The failed PR78 run still completed post-test triage: **5 assessments / 3 real provider calls**, with the failing bridge and launcher cases prioritized. The initial PR79 run [43052078315](https://github.com/xormania/xorchestrate/actions/runs/43052078315) completed **3 assessments / 2 calls** after its launcher failure. Both retained deterministic status `failed`; every Jev outcome was `insufficient_evidence`, with no concern asserted. This proves failed-run integration, not successful defect diagnosis. Artifact hashes and actual GitHub merge revisions are retained in the JSON receipt.

The same hosted run exposed a scheduling race in the existing durable bridge recovery test: its 100 ms RPC timeout fired before native entry. The snapshot now waits for the native-entry event and checks the request remains in flight before copying pending state. Restart still checks identity, uncertainty, no replay and private storage; `test_lost_response_preserves_host_effect_and_control_progress` retains timeout coverage. A deliberate premature-completion fault failed the revised test at `HOST_EFFECT_PENDING_AT_NATIVE_BOUNDARY`; the healthy control and the recovery/timeout pair passed. The production bridge implementation is unchanged. The JSON receipt retains the mutation patch and input hashes.

Affected local verification passed all **15 external-bridge cases** and **2 FIFO turn-contract cases**. Two Unix-socket turn-contract cases were blocked at socket creation by this managed host (`EPERM`); these are recorded setup limits, not assertion-red proof. Hosted CI runs that boundary. The shared static suite passed Ruff, syntax and architecture after both repairs.
