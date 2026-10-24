# Fresh independent replay

Use Linux, Git, Python 3.12 and tmux. Local Unix/FIFO sockets and subprocesses must work. No vendor credentials are needed for these deterministic checks. The measured interpreter was Python 3.12.14; exact dependencies are in the restored `evidence/replay/inputs/requirements-replay.txt`.

First restore the evidence using README.md. In the commands below, `EVIDENCE` is the absolute restored `evidence` directory. Choose a new workspace and output directory; replay wrappers deliberately refuse to overwrite prior output.

```sh
EVIDENCE=/absolute/path/to/restored/evidence
REVIEW_ROOT=/absolute/path/to/fresh-review
mkdir -p "$REVIEW_ROOT"
git clone --filter=blob:none --no-checkout https://github.com/xormania/xorchestrate.git "$REVIEW_ROOT/candidate"
SHA=1fef55c0c0cadd5e1417c0e010f272f6447f8ea4
git -C "$REVIEW_ROOT/candidate" fetch origin "$SHA"
git -C "$REVIEW_ROOT/candidate" checkout --detach "$SHA"
git -C "$REVIEW_ROOT/candidate" worktree add --detach "$REVIEW_ROOT/checker" "$SHA"
git -C "$REVIEW_ROOT/candidate" worktree add --detach "$REVIEW_ROOT/e565863" e565863ae72180571678c8f6118ee6e00b129e2e
git -C "$REVIEW_ROOT/candidate" worktree add --detach "$REVIEW_ROOT/ef75675" ef7567516520543970af55b6f19534cb66eda357
git -C "$REVIEW_ROOT/candidate" show -s --format='%H%n%T%n%P'
# Expected tree: 75ecb3ef4edbc381012fe74e416a9abd3fb5c667
git -C "$REVIEW_ROOT/candidate" status --porcelain

python3.12 -m venv "$REVIEW_ROOT/venv"
PYTHON="$REVIEW_ROOT/venv/bin/python"
"$PYTHON" -m pip install -r "$EVIDENCE/replay/inputs/requirements-replay.txt"
"$PYTHON" -m pip install -e "$REVIEW_ROOT/candidate[test]"
export PYTHONDONTWRITEBYTECODE=1
export PATH="$REVIEW_ROOT/venv/bin:$PATH"
CANDIDATE="$REVIEW_ROOT/candidate"
CHECKER="$REVIEW_ROOT/checker"
FRESH="$REVIEW_ROOT/output"
mkdir -p "$FRESH"
```

Verify the candidate and checker are clean before measuring. The second checkout isolates execution; it does not establish correctness. Expectations below come from controlled raw facts and independently specified contracts.

## Published witnesses and new raw counterexamples

```sh
"$PYTHON" "$EVIDENCE/replay/run_replays_v4.py" \
  --candidate "$CANDIDATE" --out "$FRESH/witnesses" --phase after \
  --python "$PYTHON" --expected-sha "$SHA"

"$PYTHON" "$EVIDENCE/replay/run_composed_v3.py" \
  --candidate "$CANDIDATE" --checker "$CHECKER" --expected-sha "$SHA" \
  --python "$PYTHON" --out "$FRESH/composed" --phase after

"$PYTHON" "$EVIDENCE/replay/native_result_claims.py" \
  --candidate "$CHECKER" --input "$FRESH/composed/protocol" \
  --out "$FRESH/native-result-claims"
"$PYTHON" "$EVIDENCE/replay/adapted-scripts/iv03-requested-pair.py" \
  --candidate "$CHECKER" --input "$FRESH/composed/protocol" \
  --out "$FRESH/requested-pair"
"$PYTHON" "$EVIDENCE/root-receipt/probe-native-result-v2.py" \
  --candidate "$CANDIDATE" --input "$FRESH/composed/protocol" \
  --out "$FRESH/product-receipt"
"$PYTHON" "$EVIDENCE/identity-adversarial-inspection/replay_adversarial_identity.py" \
  --candidate "$CANDIDATE" --out "$FRESH/identity-adversarial"
```

Expected: all wrappers exit 0; 46 protocol cases; coherent 30-case identity replay and 9 duplicate cases have no mismatches; native returns 8/8; requested-phase pairs 6/6; product receipt 47/47 with empty false-accept/false-reject lists. The product probe is observational and its exit alone is insufficient: inspect `summary.json`.

Reproduce the original defects without changing product source:

```sh
"$PYTHON" "$EVIDENCE/replay/run_replays_v4.py" \
  --candidate "$REVIEW_ROOT/e565863" --out "$FRESH/original-defects" \
  --phase before --python "$PYTHON" \
  --expected-sha e565863ae72180571678c8f6118ee6e00b129e2e
```

The wrapper should exit 0 only when the expected defects and controls reproduce. Its original IV01 script exits 0 when disclosure exists; IV02's original exit 0 only means the experiment finished. Original IV03 scripts return 1 on the defective candidate. Read the wrapper's semantic assertions and raw observations.

`evidence/replay/ADAPTATIONS.md` explains every difference from the immutable original scripts. IV01 changes only the three disclosure expectations. IV02 retains the published peer bytes before injecting its original independent gate. IV03's old alternate-work control omitted contradictory requested/native-return claims; the stronger oracle and fully coherent control are separately named, paired with negative controls, and retain the failed original replay.

## Persistent shared contracts and complete suite

```sh
cd "$CANDIDATE"
PYTHONPATH="$CANDIDATE" "$PYTHON" -m pytest \
  tests/hexagonal/test_assessment_inspection_contract.py \
  tests/hexagonal/test_completion_contracts.py \
  tests/hexagonal/test_bridge_turn_contracts.py \
  tests/hexagonal/test_identity_contracts.py \
  tests/hexagonal/test_verification_decoders.py \
  tests/hexagonal/test_boundaries.py \
  tests/hexagonal/test_verification_boundaries.py -q -rs

TEST_TMP=$(mktemp -d /tmp/xor-iv123.XXXXXX)
PYTHONPATH="$CANDIDATE" "$PYTHON" -m pytest tests/hexagonal -q -rs \
  --basetemp="$TEST_TMP" --junitxml="$FRESH/full-suite.xml"
"$PYTHON" "$EVIDENCE/replay/inventory_audit.py" \
  --before "$REVIEW_ROOT/e565863" --candidate "$CANDIDATE" --checker "$CHECKER" \
  --protocol "$FRESH/composed/protocol" --xml "$FRESH/full-suite.xml" \
  --out "$FRESH/inventory.json"
```

Expected full result: 4,067 passed +164 passing subtests, no skips/errors/failures; 211 published selectors pass and existing test bodies remain unchanged. A short basetemp avoids AF_UNIX path-length failures. Retain the temporary directory as evidence; do not use an old output directory for another attempt.

## Original calibration faults and IV04 preservation

```sh
mkdir -p "$FRESH/calibration"
PYTHONPATH="$CANDIDATE" "$PYTHON" -m scripts.verification.calibrate_suite \
  --candidate "$CANDIDATE" --out "$FRESH/calibration/native-calibration"
PYTHONPATH="$CANDIDATE" "$PYTHON" tests/hexagonal/calibrate_work_acceptance.py \
  --output "$FRESH/calibration/work-calibration"
"$PYTHON" "$EVIDENCE/iv04-preservation/run_iv04.py" \
  --candidate "$CANDIDATE" --baseline "$REVIEW_ROOT/ef75675" \
  --calibrations "$FRESH/calibration" --out "$FRESH/iv04"
"$PYTHON" "$EVIDENCE/iv04-preservation/audit_challenges.py" \
  --execution "$FRESH/iv04" --out "$FRESH/iv04/raw-challenge-audit.json"
"$PYTHON" "$EVIDENCE/iv04-preservation/audit_calibrations-output-adapted.py" \
  --candidate "$CANDIDATE" --evidence "$FRESH/calibration" \
  --out "$FRESH/iv04/mutation-audit.json"
```

Expected: 12 original faults detected, 36 controls, 19 intended assertion failures, 17 unaffected mutant passes. IV04: 32 challenge cases with zero false detection credit. The unchanged positions child intentionally exits 1 for four encoding-sensitivity misses; its wrapper records that expected outcome. An unrelated exception must never earn detection credit. These encoding misses remain follow-ups, not passing genuine-detection controls.

## Normal preservation checks

```sh
"$PYTHON" -m ruff check xorchestrate tests devtools .github scripts
"$PYTHON" .github/lint_checks.py
"$PYTHON" -m unittest discover -s .github/tests -v
"$PYTHON" -m compileall -q xorchestrate tests/hexagonal scripts/acceptance
"$PYTHON" scripts/check_legacy.py
"$PYTHON" -m scripts.acceptance --output "$FRESH/tmux" --timeout 120
git status --porcelain
```

The unchanged CI workflow supplies both Python versions and the historical runtime toolchain (including CUE and tmux). Hosted raw artifacts are archived under `evidence/hosted-ci-final`, so replay does not depend on GitHub's artifact expiry. Authenticated vendor acceptance is separate; see the candidate's `docs/native-sessions.md`, `docs/behavioral-verification.md` and `docs/live-acceptance.md` for its configured workflows.
