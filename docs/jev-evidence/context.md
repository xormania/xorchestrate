# Targeted architecture diagnostics: verification record

The previous selector divided its byte budget between fixed file prefixes.
Changed functions near the end of a module could be absent while three broad
questions still completed. The replacement selects complete AST units from
changed ranges, supplies related measured definitions/callers/tests where they
fit, and asks typed questions bound to each candidate's source span and declared
role contract. A concern remains an unverified investigation candidate.

Implementation: `91243ee`; independently authored tests: `54827d2`. Source and
question changes are in `scripts/architecture/{context,diagnostics}.py`, with
CLI/configuration and operator documentation. New behavioral coverage belongs to
the existing `architecture` family. No live model or provider credentials were
used for these results.

## Healthy verification

Python 3.12.16, using an external bytecode cache:

```bash
python -m scripts.testkit run --suite tests --family architecture --output ../evidence-context
python -m scripts.testkit run --suite static --output ../evidence-context
```

The architecture family passed **106/106 tests**, with no skips, in local run
`20261110T054511Z-96935c35f998`. Static Ruff, syntax and deterministic architecture
checks passed in `20261110T054605Z-808ff9330df9`. Both receipts identify content
`e44cda2b371047c059c9acab1d1dc32ffffe1250ef49bc79928d8cf6027f4a73`.
This identifies the checked inputs; later stack integration and path-parser
repairs require their own affected checks and hosted candidate evidence.

## Detection calibration

The separate test author used the existing snapshot, exact-mutation, command and
assertion-observer services in `scripts/verification`. Eleven isolated production
faults were applied to disposable copies. Every healthy control passed; every
fault was observed and detected by its intended assertion. The snapshots retain
the actual source bytes, mutation, command, JUnit and assertion attribution.
Calibration input content was
`cb215a43511de647ea13fe916df8200446e3851db72217c2d81c55fd52f29f45`:
an explicitly dirty worktree based on `af162ce`, rather than a claimed clean
commit. The subsequent wording/option-validation edits were checked separately.

| Fault | Deliberate change | Intended detection |
| --- | --- | --- |
| `whole-unit` | Clip a selected function to 16 characters while claiming completeness | `CHANGED_FUNCTION_COMPLETE` |
| `related-evidence` | Omit all related units | `RELATED_EVIDENCE_INCLUDED` |
| `source-budget` | Disable the remaining-byte guard | `UNIT_BUDGET_ENFORCED` |
| `file-limit` | Disable the unique-file guard | `FILE_LIMIT_ENFORCED` |
| `hash-binding` | Disable measured-source digest comparison | `STALE_SOURCE_REJECTED` |
| `git-diff` | Ignore ranges obtained from Git | `GIT_CHANGED_FUNCTION_SELECTED` |
| `unmeasured-visible` | Suppress an outside-inventory exclusion | `UNMEASURED_SOURCE_VISIBLE` |
| `typed-grounding` | Replace the task path with `unrelated.py` | `TASK_SOURCE_BOUND` |
| `abstention` | Rewrite provider abstention as `no_concern` | `PROVIDER_ABSTENTION_RETAINED` |
| `no-candidate` | Report a missing complete candidate as completed | `MISSING_CANDIDATE_INCONCLUSIVE` |
| `gate-preservation` | Rewrite deterministic failure as pass in diagnostics | `DETERMINISTIC_FAILURE_PRESERVED` |

The retained local evidence is under `evidence-context/<fault>/`: `mutation.json`,
`calibration.json`, control/mutant JUnit and command records. The aggregate
`evidence-context/summary.json` records all 11 detections. The committed tests in
`tests/hexagonal/test_architecture_context.py` and its contract/support modules
provide the healthy replay boundary; exact mutations above identify the changed
property rather than relying on test counts alone.

Two additional real-Git regression witnesses exercise a space-containing path
and a tab-containing filename. The separate test author restored the actual
`changed_lines` parser from `91243ee` in disposable copies: both tests failed at
`SPECIAL_GIT_PATH_TARGETED`, rather than during setup. Both repaired controls
passed. The additional evidence is in `evidence-context/special-path-summary.json`.
Together these are **13 detected witnesses**: 11 deliberate faults and two
previous-parser failures. The affected context/HTTP test selection then passed
**41/41** tests. The new independent path tests are in `df3975c` and their formatting
follow-up `68b3624`; they do not reuse the implementation to derive expected paths.

## Bounded adversarial review and disposition

An independent pre-draft source/test review examined `91243ee` once, with targeted
repair verification for material findings:

| Finding | Disposition |
| --- | --- |
| Repeated `check_id` values are legitimate across candidates, while the earlier summary consumer requires global uniqueness | Repaired in the stacked consumer using task or candidate/check identity and a located candidate column. Independent tests recorded three baseline assertion failures for the two identity variants and actual CI base wiring, then the full reporting module passed 97 cases. The initial 106-test run predates these integration checks. |
| Git patch display headers quote unusual paths and append separators to some names, so parsing `+++` can lose an in-inventory changed candidate | Repair uses NUL-delimited changed names and per-path hunk parsing, with external diff execution disabled. Independent real-Git space/tab path regressions compare the previous parser with the repair. |
| Class initialization annotations and dynamic call relationships can need richer context than static imports/call names expose | Accepted static-analysis limit. Relationships are selection hints; missing evidence permits abstention. No runtime coverage or complete type resolution is claimed. |

Source packets expose omitted and oversized units and never authorize execution,
clear deterministic failures, or count a model concern as a confirmed defect.

CI now fetches the comparison base and supplies the PR base SHA (or push-before SHA) through an environment value to `--base-ref`. Independent workflow assertions cover this wiring.
