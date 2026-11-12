# Operational evaluation independent detection evidence

The initial **28 new cases** each have an intended assertion red across **18 deliberate faults**, paired with passing healthy controls. Mutations were applied only to disposable snapshots using the existing command/evidence services. Fresh external bytecode caches prevent stale-code witnesses.

The once-only review added **2 focused regressions**. The actual pre-repair source failed all 2 intended assertions with no setup errors. The repaired affected scope passed **30 tests** on Python 3.12.16.

| Fault | Assertion failures |
|---|---:|
| known_labels_promoted_to_confirmation | 1 |
| oracle_leaked_to_provider | 1 |
| catalog_validation_removed | 2 |
| source_pin_not_verified | 1 |
| missing_ref_replaced_by_other_ref | 1 |
| provider_population_unchecked | 4 |
| historical_usage_counted_fresh | 1 |
| changing_outputs_marked_identical | 1 |
| missing_source_marked_complete | 1 |
| live_disposition_never_confirms | 1 |
| disposition_source_unchecked | 1 |
| witness_hash_unchecked | 1 |
| disposition_packet_unchecked | 1 |
| pilot_scope_inverted | 7 |
| health_reuses_cached_answer | 1 |
| adjudication_mutates_original | 1 |
| retained_packet_unchecked | 1 |
| ci_pilot_scope_missing | 1 |

Exact patches, selectors, source/test hashes and failure messages are retained in [v3-operational.json](v3-operational.json).

```sh
python -m pytest -p no:cacheprovider tests/hexagonal/test_jev_operational.py -q
```

The review controls show that flat request/retry options reach the shared evaluator and repeated trials do not multiply unique confirmed findings. The pre-repair operational module was an uncommitted prototype; its exact working-byte hash is retained rather than attributing that file to the master commit where it did not yet exist.

Provider answers in the independent detection checks are controlled fixtures. Those checks establish detection and integration behavior, not live Jev accuracy or measured engineering savings.

## Hosted PR79 observation

[Run 43052078315](https://github.com/xormania/xorchestrate/actions/runs/43052078315) evaluated source `1d706854f152b11bdc3e4e6318b810a91d5dd418` at tested merge `778c631d2251c25b3aaafde85a248a26ef206d34`. Changed evaluation inputs selected the full pilot. The `jev-1.14.0` heldout replay completed four fresh calls across two before/after incident pairs, using **47,224 input / 2,804 output tokens**, with cache disabled. All four cases routed to **insufficient_evidence**: zero known-fault matches, confirmed findings or false alerts; all four remained unknown. The structural baseline passed four cases; two historical Codex findings remain a separate comparator.

| Heldout case | Applicability choice / confidence | Sufficiency choice / confidence | Violation choice / confidence | Routed outcome |
|---|---|---|---|---|
| Hosted capacity before | yes .61 | yes .35 | yes .80 | insufficient evidence |
| Hosted capacity after | yes .42 | yes .14 | no .31 | insufficient evidence |
| Recovery authority before | yes .44 | insufficient .07 | yes .38 | insufficient evidence |
| Recovery authority after | yes .33 | insufficient .11 | no .52 | insufficient evidence |

The values above are the adapter-reported confidence scores, not choice probabilities or calibrated defect likelihoods. The raw choices point toward the known before/after distinction, but did not pass the composed evidence requirements and do not establish added detection or clearance. All target spans were complete; that does not imply every supporting dependency was included. The separate six-case synthetic pilot used **6 calls, 14,793 input / 1,566 output tokens** and produced five unknowns plus one unsupported storage concern, with zero confirmed findings. Billing and reviewer time remain unknown; no engineering savings are claimed. Exact artifact content hashes, identities, raw facet choices and usage are retained under `hosted_pr79` in [v3-operational.json](v3-operational.json).
