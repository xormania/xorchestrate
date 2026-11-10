# Jev advisory reporting verification

Base: `af162ce0ed798bf06418c5bc31bf170b1d6acce6`. Runtime: CPython 3.12.16.
Implementation and tests were authored independently. The change exposes Jev
advice in the existing CI result while preserving deterministic qualification.

| Guarantee | Boundary and evidence |
| --- | --- |
| Current advice exposes outcomes, confidence, distributions and scope | Public `evaluate`/`markdown` reporting with representative architecture artifacts |
| Advice never clears a required failure or prevents an otherwise qualified pass | Healthy required receipts paired with failed jobs, provider gaps and malformed/missing/foreign advice |
| A report cannot borrow another candidate's identity | Tested commit, explicit clean working-tree status, measured subject and context validation; real clean/dirty Git producer controls |
| CI waits for advice without requiring successful advice or its transfer | Workflow dependency, mandatory artifact exclusion, separate optional download and direct artifact URL assertions |

## Detection and healthy controls

The final 28 added parameter cases were copied into a detached worktree at the
base commit and executed with the same inputs. All 28 failed their intended
assertions; there were no collection, import or setup errors. The baseline lacked
visible advisory observations, current report status, clean producer identity and
the required optional-transfer wiring. These are assertion failures against the
old behavior, not missing API failures.

```bash
python -m pytest -p no:cacheprovider tests/hexagonal/test_ci_result.py \
  -k 'jev_observations or current_jev_provider or jev_missing or ci_summary_waits or ci_advisory_link or architecture_producer or optional_advisory_transfer' -q
python -m pytest -p no:cacheprovider tests/hexagonal/test_ci_result.py -q
```

The complete updated reporting module passed 94 cases. Additional targeted
architecture CLI/HTTP diagnostic tests passed. The shared static suite passed
Ruff, syntax validation and deterministic architecture checks. Execution used an
external bytecode cache and retained the test-kit command/input receipts.

## Bounded pre-draft review

One adversarial sub-agent reviewed the implementation, independent assertions,
workflow wiring and optional failure paths. It identified two practical defects:

- Extreme JSON integers could raise `OverflowError` outside the optional parser
  boundary. The boundary now contains that error; malformed advice remains
  visible without replacing deterministic qualification. Independent huge-value
  cases exercise the repaired path.
- Downloading optional advice with required artifacts could fail the required CI
  job on an advisory transfer failure. Required downloads now exclude
  `architecture-evidence`; its separate download continues on error. An
  independent workflow assertion covers both policies.

The reviewer confirmed the targeted repairs and reported no remaining practical
blocker. Its final inspected production diff SHA-256 was
`5d4547d895fcafa7839a20104701f6b621257a480169f6345a690cb144e83511`.
Subsequent changes add verification records and the independent workflow test.

These controls establish reporting and advisory isolation. They do not establish
semantic model accuracy, authenticated live model availability or a real defect
behind an individual model classification. Hosted CI remains recorded in the
draft pull request's checks.
