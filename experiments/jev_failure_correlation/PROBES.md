# Deterministic follow-up probes

The v1 execution budget is two distinct supported probe options per selector, with zero retries. The same implementation and budget apply to baseline and Jev selections. `p4` runs one passing control and eight fixture variants (nine pytest subprocesses, each capped at 30 seconds). `p7` runs four active-application cases: material-context versus cosmetic-title changes on memory and SQLite stores. Multiple cases choosing the same option share its execution; they are not independent probe discoveries.

Only p4 and p7 are implemented for this bounded experiment. A selected p1/p2/p3/p5/p6 remains explicitly unexecuted, with no substitution and no utility credit. `none` and `insufficient` incur no execution. This limited execution coverage must be included when interpreting selection usefulness.

```sh
python experiments/jev_failure_correlation/probes.py --predictions OUTPUT/baseline.json --selector baseline --out OUTPUT/baseline-probes
python experiments/jev_failure_correlation/probes.py --predictions OUTPUT/predictions.json --selector jev --out OUTPUT/jev-probes
```

Outputs require a new directory to preserve historical results. The CLI returns nonzero for a probe execution error. A deliberately faulty candidate is not an experiment execution error: the retained outcome records distinguish intended target assertion detection, a reproduced known classifier/product defect, passing controls, skipped coverage, absent target coverage and infrastructure failure. An unrelated helper exception does not receive intended-detection credit, even when the candidate classifiers incorrectly report `detected`.

The independent-generation run preceded model evaluation and generated four F07 development-transfer cases: nested dependency `ValueError`, a dependency's own `AssertionError`, a passing dependency, and a collection-time missing dependency. These are meaningful changed failure/entry paths in a known family, not unseen-family performance. Fixtures and immutable raw outcomes are retained in `probe_fixtures/calibration.json` and `evidence/independent_generation/`. `probe_fixtures/export_additional.py` documents the packet extraction; expected labels, detection-credit flags, assertion markers and diagnostic paths are excluded from the model-visible packet. Retained `.py.txt` files preserve exact executed fixture bytes while preventing accidental collection by ordinary pytest discovery.

The probe tests independently execute their own disposable states to validate the experiment. Those validation executions are not measured baseline/Jev selection outcomes. Neither these fixtures nor protocol-peer evidence establish authenticated vendor harness acceptance.
