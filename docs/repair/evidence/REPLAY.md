# Fresh-session replay

Exact measured product: `ef7567516520543970af55b6f19534cb66eda357`, tree `a0813aae760e6e5bb6245693bd92b7e7c321348b`. Draft PR #6 remains unmerged.
The original reviewed product and superseded first repair are historical inputs;
only `repair-evidence/final/` supplies the final measurement. Read
`final-manifest.json`, `closure-matrix.json`, `requirement-matrix.json` and
`publication.json` before assigning acceptance credit.

## Recover files and source

Run from the directory produced by extracting this package ZIP. These commands
recover the published commits and require no vendor credentials:

```sh
export XOR_VERIFY_ROOT="$(pwd)"
git clone https://github.com/xormania/xorchestrate.git "$XOR_VERIFY_ROOT/candidate"
git -C "$XOR_VERIFY_ROOT/candidate" checkout --detach ef7567516520543970af55b6f19534cb66eda357
tar -xJf "$XOR_VERIFY_ROOT/measurements.tar.xz" -C "$XOR_VERIFY_ROOT"
unzip -q "$XOR_VERIFY_ROOT/original-review.zip" -d "$XOR_VERIFY_ROOT/original-review"
python "$XOR_VERIFY_ROOT/restore_git_inputs.py" --repo "$XOR_VERIFY_ROOT/candidate" --root "$XOR_VERIFY_ROOT"
python "$XOR_VERIFY_ROOT/restore_git_inputs.py" --repo "$XOR_VERIFY_ROOT/candidate" --root "$XOR_VERIFY_ROOT" --verify-only
mkdir -p "$XOR_VERIFY_ROOT/rerun"
cd "$XOR_VERIFY_ROOT/candidate"
python -m pip install -e '.[test]' -r .github/requirements-ci.txt
git show -s --format='%H%n%T%n%P' HEAD
git status --porcelain=v1
```

Git-backed source is recovered from the published exact commits and the package
deduplication map; no Git bundle is included. Python 3.11/3.12 and declared test
dependencies are required. Initial Git cloning and dependency installation can
require network access. After the clone contains all indexed commits, restoration
of omitted measurement source inputs uses local Git objects. The restore helper
rebuilds only exact Git-backed duplicate inputs, refuses conflicting existing
files and verifies retained evidence hashes. It does not change repository refs.
The unchanged original review ZIP has its own manifest and owner brief.

Historical command records, SQLite contents and JSON observations retain their
original absolute workspace paths as provenance. When replaying a retained
command or copied probe, replace only the old workspace prefix with this extracted
workspace; do not edit the original records. Apply output-directory mappings
according to the candidate identity in each record:

- Superseded d4 records name their former `repair-evidence/final/` directory;
  those files now live under `repair-evidence/superseded-d4bd829/`.
- Completed final-candidate records name `repair-evidence/exact-ef75675/`;
  that completed measurement was moved to `repair-evidence/final/` after earlier
  background sessions closed. Use `repair-evidence/final/` to locate its files.

The retained `repair-evidence/collided-output-attempt-ef75675/` was an earlier
setup/output-collision attempt containing stale and current outputs. Its command
failure and setup records are preserved for provenance only; they provide no
behavioral pass, product-defect or final-candidate credit. Final acceptance uses
the completed, separately captured exact-candidate run and its measured bindings.
Always choose fresh output directories for replay; do not write into retained
measurement directories.

## Full coverage and focused repair assertions

```sh
python -m pytest tests/hexagonal -q -rs --junitxml="$XOR_VERIFY_ROOT/rerun/application.xml"
python "$XOR_VERIFY_ROOT/verify_required_assertions.py" --selectors "$XOR_VERIFY_ROOT/original-requirement-selectors.json" --junit "$XOR_VERIFY_ROOT/rerun/application.xml" --out "$XOR_VERIFY_ROOT/rerun/original-selector-coverage.json"
python -m pytest tests/hexagonal/test_delivery_scope.py tests/hexagonal/test_bridge_repair_calibration.py tests/hexagonal/test_native_cache_currentness.py tests/hexagonal/test_assessment_inputs.py tests/hexagonal/test_claude_overlap.py tests/hexagonal/test_calibration_attribution.py tests/hexagonal/test_raw_evidence_calibration.py tests/hexagonal/test_completion_evidence.py tests/hexagonal/test_native_completion_state.py -q --junitxml="$XOR_VERIFY_ROOT/rerun/repairs.xml"
python -m ruff check xorchestrate tests devtools .github scripts
python .github/lint_checks.py
python -m unittest discover -s .github/tests -v
python -m compileall -q xorchestrate tests/hexagonal scripts/acceptance
```

The focused files retain deliberately faulty product subjects and corrupt raw
observations. Calibration passes only when the activated fault fails its intended
assertion/rejection and controls remain valid. Import/setup failures, unrelated
exceptions, skipped coverage and unapplied faults cannot earn detection credit.

```sh
python -m scripts.verification.calibrate_suite --candidate "$XOR_VERIFY_ROOT/candidate" --out "$XOR_VERIFY_ROOT/rerun/native-calibration"
python tests/hexagonal/calibrate_work_acceptance.py --output "$XOR_VERIFY_ROOT/rerun/work-calibration"
```

These two commands retain the original seven native/delivery and five work
source faults. Inspect their actual JUnit and trusted pytest assertion-observer
records. New output directories are mandatory; preserve failed runs.

## Independent checker and new composed run

```sh
git clone "$XOR_VERIFY_ROOT/candidate" "$XOR_VERIFY_ROOT/checker"
git -C "$XOR_VERIFY_ROOT/checker" checkout --detach ef7567516520543970af55b6f19534cb66eda357
cd "$XOR_VERIFY_ROOT/checker"
python -m scripts.verification criteria --brief "$XOR_VERIFY_ROOT/owner-brief.md" --out "$XOR_VERIFY_ROOT/rerun/criteria.json"
python -m scripts.verification.multi_harness --candidate "$XOR_VERIFY_ROOT/candidate" --mode protocol-fixture --brief "$XOR_VERIFY_ROOT/owner-brief.md" --criteria "$XOR_VERIFY_ROOT/rerun/criteria.json" --out "$XOR_VERIFY_ROOT/rerun/protocol" --timeout 60
python -m scripts.verification check --run "$XOR_VERIFY_ROOT/rerun/protocol/run.json" --criteria "$XOR_VERIFY_ROOT/rerun/protocol/criteria.json" --bindings "$XOR_VERIFY_ROOT/rerun/protocol/bindings.json" --fixture "$XOR_VERIFY_ROOT/rerun/protocol/fixture.json" --out "$XOR_VERIFY_ROOT/rerun/protocol-recomputed.json"
```

Separate checkouts make product/checker selection explicit; they do not by
themselves prove reviewer independence. Inspect both implementations. The same
46-case inventory remains required. Protocol fixtures exercise actual adapters,
subprocesses, SQLite, hooks, bridge and application callbacks, not vendor models.

Recompute the retained final report without launching peers:

```sh
python -m scripts.verification check --run "$XOR_VERIFY_ROOT/repair-evidence/final/protocol/run.json" --criteria "$XOR_VERIFY_ROOT/repair-evidence/final/protocol/criteria.json" --bindings "$XOR_VERIFY_ROOT/repair-evidence/final/protocol/bindings.json" --fixture "$XOR_VERIFY_ROOT/repair-evidence/final/protocol/fixture.json" --out "$XOR_VERIFY_ROOT/rerun/saved-final-recomputed.json"
```

Do not relabel the original or superseded d4 raw runs as final passes. The corrected
completion checker yields 44 passed and two incomplete Codex/Grok completion cases
for those older records because aggregate state was not recorded. Their original
checker/CI results remain historically valid observations of a weaker oracle.
`claude-completion-normalization-challenge/` preserves the false-pass demonstration;
`completion-checker-repair/` preserves corrected recomputations and targeted tests.

## CI, native scope and the next review

Inspect `final-manifest.json` for actual CI run/artifact URLs and exact candidate
or verified integration-tree bindings. Tmux requires successful private-socket
create/send/capture/cleanup; a binary/version string is insufficient. Work host
socket failures are environmental limits. Hosted results are labeled hosted;
historical runtime tests preserve source behavior but cannot replace active tests.

Authenticated vendor acceptance remains xor's local work. Follow
`candidate/docs/behavioral-verification.md` for the complete inventory plus
identical-call replay after owner revision, overlapping Claude work, and reconnect
before callback dispatch/after native effect. Native records must retain aggregate
post-transition activity; one turn completion cannot establish global idle.
Unfinished-work replies must precede completion. Busy-tool coverage requires
submission during the hold followed by the complete exchange; no token-generation
interruption or exactly-once claim is made.

The fresh session must inspect source, corrected predicates, controls, source
identities, case inventories and raw evidence. In-session challenges do not
replace that review. No merge is authorized.
