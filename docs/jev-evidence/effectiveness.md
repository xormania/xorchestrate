# Jev effectiveness verification

Independent tests cover accounting, population validation, source grounding, unavailable/unknown outcomes, usage retention, and the bounded operator command. Fixture results establish these mechanics; they do not establish live Jev quality.

## Red and green evidence

- Production source for deliberate faults: `574381e`; independent initial tests: `5afc308`.
- Ten deliberate faults each failed their intended assertions with a paired passing healthy control. All 20 initial test cases have a red witness. No import or setup error was counted.
- The once-only adversarial review found that string check IDs could match by substring and receive false confirmation. Eight independent regressions failed against `574381e` with `DID NOT RAISE`; fix `2aa3416` validates named distinct check lists and rule-ID lists.
- Final complete module: **29 passed**, Python 3.12.16.

| Deliberate fault | Intended assertion failures |
|---|---:|
| population_validation_removed | 7 |
| corpus_validation_removed | 4 |
| false_alarms_erased | 1 |
| baseline_detection_erased | 1 |
| grounding_bypassed | 2 |
| partial_packet_accepted | 1 |
| unavailable_erased | 1 |
| fixture_mislabeled_live | 1 |
| deadline_ignored | 1 |
| provider_usage_discarded | 1 |

The exact selectors, source hashes, transformations and assertion messages are retained in [effectiveness.json](effectiveness.json). Healthy and faulty commands used the existing test-kit RecordingRunner and JUnit parser in a disposable worktree, with a fresh external bytecode cache for each invocation.

```sh
python -m pytest -p no:cacheprovider tests/hexagonal/test_architecture_effectiveness.py -q
```

For a fault replay, use a disposable worktree at the recorded source, apply the named transformation only to `scripts/architecture/effectiveness.py`, copy the recorded tests and catalog declarations, and run its recorded selector with a fresh external `PYTHONPYCACHEPREFIX`. Restore the healthy source before running the next control.

Two superseded calibration attempts are excluded: the first reused timestamp bytecode for an equal-size edit; the second verifier rejected a normal pytest-rewritten assertion message. The final counted round used fresh caches and explicit test/failure records.

The existing deterministic checker remains the baseline. Scripted false alarms and abstention stay visible. Reviewer minutes and monetary costs are unmeasured.

The optional CI pilot has a separate red witness: the workflow test failed against `574381e` because no live-effectiveness step existed (`assert 0 == 1`). It passes with one `--mode live --max-seconds 120` step, `continue-on-error: true`, the existing provider environment, and retained `effectiveness.json`. Credential absence stays an unavailable provider result rather than fixture success. The final full module passes 29 cases.

The existing CI architecture-command guard now matches the exact `scripts.architecture` module token, so `scripts.architecture.effectiveness` is a distinct optional command. The healthy current workflow passes. Removing `--diagnostics-only` from the actual architecture invocation in a disposable copy still fails the original guard assertion; the intended detection is preserved.
