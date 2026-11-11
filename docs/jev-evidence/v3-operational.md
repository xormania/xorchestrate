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

Provider answers are controlled fixtures. These records establish detection and integration behavior, not live Jev accuracy or measured engineering savings.
