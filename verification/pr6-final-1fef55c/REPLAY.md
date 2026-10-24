# Reproduce the bounded review

Use Linux, Python 3.12, tmux, working local Unix/FIFO endpoints and subprocesses.
The measured interpreter was 3.12.14, tmux 3.5a. No vendor credentials are needed.
`setup/environment.json` in the fresh archive records pinned dependencies and the
user-space Debian tmux package source/hash used when system installation failed.

First checksum-verify and restore this publication with [README.md](README.md).
Restore the existing [repair archive](https://github.com/xormania/xorchestrate/tree/09f801eab7eda6f7b07715c516f45b1a6a0a656b/verification/pr6-iv123-hexagonal-1fef55c)
and [original review archive](https://github.com/xormania/xorchestrate/tree/0f55a1a031a047b8730ef37bc6eda20e17e3cdb0/verification/pr6-independent-e565863)
using their immutable README/checksum/helper instructions. We verified all 72,508
and 30,893 payload files respectively. They are not embedded again here.

## Source and environment

Choose fresh absolute paths. `EVIDENCE` below is the restored repair `evidence`
directory; `REVIEW` is this publication's restored fresh evidence root. Keep
`FRESH` separate from both. Output directories used by probes must not exist.

```sh
REVIEW_ROOT=/absolute/new-review
EVIDENCE=/absolute/restored-repair/evidence
REVIEW=/absolute/restored-final-review
SHA=1fef55c0c0cadd5e1417c0e010f272f6447f8ea4
mkdir -p "$REVIEW_ROOT"
git clone --no-checkout https://github.com/xormania/xorchestrate.git "$REVIEW_ROOT/candidate"
git -C "$REVIEW_ROOT/candidate" fetch origin "$SHA" e565863ae72180571678c8f6118ee6e00b129e2e ef7567516520543970af55b6f19534cb66eda357
git -C "$REVIEW_ROOT/candidate" checkout --detach "$SHA"
CANDIDATE="$REVIEW_ROOT/candidate"
CHECKER="$REVIEW_ROOT/checker"
git -C "$CANDIDATE" worktree add --detach "$CHECKER" "$SHA"
git -C "$CANDIDATE" worktree add --detach "$REVIEW_ROOT/old" e565863ae72180571678c8f6118ee6e00b129e2e
git -C "$CANDIDATE" worktree add --detach "$REVIEW_ROOT/baseline" ef7567516520543970af55b6f19534cb66eda357
git -C "$CANDIDATE" show -s --format='%H%n%T'
# Tree must be 75ecb3ef4edbc381012fe74e416a9abd3fb5c667; source status must be clean.
git -C "$CANDIDATE" status --porcelain
python3.12 -m venv "$REVIEW_ROOT/venv"
PYTHON="$REVIEW_ROOT/venv/bin/python"
"$PYTHON" -m pip install -r "$EVIDENCE/replay/inputs/requirements-replay.txt"
"$PYTHON" -m pip install -e "$CANDIDATE[test]"
export PATH="$REVIEW_ROOT/venv/bin:$PATH"
export PYTHONDONTWRITEBYTECODE=1
FRESH="$REVIEW_ROOT/output"
mkdir -p "$FRESH"
```

## Original defects, closure witnesses and fresh raw protocol

```sh
"$PYTHON" "$EVIDENCE/replay/run_replays_v4.py" --candidate "$REVIEW_ROOT/old" --out "$FRESH/original-defects" --phase before --python "$PYTHON" --expected-sha e565863ae72180571678c8f6118ee6e00b129e2e
"$PYTHON" "$EVIDENCE/replay/run_replays_v4.py" --candidate "$CANDIDATE" --out "$FRESH/witnesses" --phase after --python "$PYTHON" --expected-sha "$SHA"
"$PYTHON" "$EVIDENCE/replay/run_composed_v3.py" --candidate "$CANDIDATE" --checker "$CHECKER" --expected-sha "$SHA" --python "$PYTHON" --out "$FRESH/composed" --phase after
"$PYTHON" "$EVIDENCE/replay/native_result_claims.py" --candidate "$CHECKER" --input "$FRESH/composed/protocol" --out "$FRESH/native-result-claims"
"$PYTHON" "$EVIDENCE/replay/adapted-scripts/iv03-requested-pair.py" --candidate "$CHECKER" --input "$FRESH/composed/protocol" --out "$FRESH/requested-pair"
"$PYTHON" "$EVIDENCE/root-receipt/probe-native-result-v2.py" --candidate "$CANDIDATE" --input "$FRESH/composed/protocol" --out "$FRESH/product-receipt"
"$PYTHON" "$EVIDENCE/identity-adversarial-inspection/replay_adversarial_identity.py" --candidate "$CANDIDATE" --out "$FRESH/identity-adversarial"
```

Read semantic summaries and raw records. The before wrapper exits zero for the
expected old defects: IV01 three disclosures per store, IV02 Grok forged-idle
acceptance with actual activity retained, IV03 15 minimal +3 duplicate false
accepts. After expectations reverse only the violated requirements; valid controls
remain. Expected fresh protocol: 46 passed, exact recomputation, 69 callback joins,
15 exchanges and independently computed batch answers. Claims/receipts must have
empty false-accept/false-reject lists; the receipt script's exit alone proves nothing.

## Focused contracts and one complete suite

```sh
cd "$CANDIDATE"
PYTHONPATH="$CANDIDATE" "$PYTHON" -m pytest tests/hexagonal/test_assessment_inspection_contract.py tests/hexagonal/test_completion_contracts.py tests/hexagonal/test_bridge_turn_contracts.py tests/hexagonal/test_identity_contracts.py tests/hexagonal/test_verification_decoders.py tests/hexagonal/test_boundaries.py tests/hexagonal/test_verification_boundaries.py -q -rs --basetemp="$(mktemp -d /tmp/xfs-focus.XXXXXX)" --junitxml="$FRESH/focused.xml"
PYTHONPATH="$CANDIDATE" "$PYTHON" -m pytest tests/hexagonal -q -rs --basetemp="$(mktemp -d /tmp/xfs-full.XXXXXX)" --junitxml="$FRESH/full.xml"
"$PYTHON" "$EVIDENCE/replay/inventory_audit.py" --before "$REVIEW_ROOT/old" --candidate "$CANDIDATE" --checker "$CHECKER" --protocol "$FRESH/composed/protocol" --xml "$FRESH/full.xml" --out "$FRESH/inventory.json"
```

Use short basetemps to respect AF_UNIX path limits and retain their outputs.
Measured: focused 2,942 passed; full 4,067 primary cases +164 subtests; zero skips,
errors or failures. All 211 selectors pass; 450 prior bodies remain unchanged.
`suite-results/commands.json` records the actual disposable suite worktree and
basetemps. `suite-runtime/{focused,full}` preserves their regular files.

## Calibration and IV04

```sh
mkdir -p "$FRESH/calibration"
PYTHONPATH="$CANDIDATE" "$PYTHON" -m scripts.verification.calibrate_suite --candidate "$CANDIDATE" --out "$FRESH/calibration/native-calibration"
PYTHONPATH="$CANDIDATE" "$PYTHON" tests/hexagonal/calibrate_work_acceptance.py --output "$FRESH/calibration/work-calibration"
"$PYTHON" "$EVIDENCE/iv04-preservation/run_iv04.py" --candidate "$CANDIDATE" --baseline "$REVIEW_ROOT/baseline" --calibrations "$FRESH/calibration" --out "$FRESH/iv04"
"$PYTHON" "$EVIDENCE/iv04-preservation/audit_challenges.py" --execution "$FRESH/iv04" --out "$FRESH/iv04/raw-challenge-audit.json"
"$PYTHON" "$EVIDENCE/iv04-preservation/audit_calibrations-output-adapted.py" --candidate "$CANDIDATE" --evidence "$FRESH/calibration" --out "$FRESH/iv04/mutation-audit.json"
```

Expected: 12 original faults, 36 passing controls, 19 intended assertion failures,
17 unaffected mutant passes. Inspect source/XML/event traces. IV04: 32 challenges,
zero false credit. Its positions child intentionally exits 1 for four deferred
encoding misses; the outer runner preserves this expected nonzero result.

## Additional independent probes

Use the CLI commands in the component reports and retained scripts, pointing
them at the same clean candidate and new output directories:

- [Inspection-and-Completion.md](Inspection-and-Completion.md): `inspection-completion-results/probe.py` and final `raw_audit.py`. Preserve the older auditor versions as failed attempts.
- [Architecture-and-Bridge.md](Architecture-and-Bridge.md): `architecture-results/architecture_probe.py` and `bridge_probe.py`.
- [Identity-and-Receipts.md](Identity-and-Receipts.md): `identity-results/probe.py`, `fresh-history.py`, and adaptation audit. Historical applicability rows with no declared rejection oracle are observations, not passing negative tests.
- [Historical-Claim-Adjudication.md](Historical-Claim-Adjudication.md): `ci-identity/nested-session-adjudication.py`; six missing-current-proof controls must reject.

The archive retains each actual command, raw result, imported module origin and
source identity. The root `results/root-semantic-audit.json` independently compares
the original witness bytes and summarizes raw facts without importing verdict code.
The root runner scripts preserve this session's exact absolute paths; the commands
above and component CLI arguments are the portable replay interface.

Existing identity-verified CI supplies unchanged historical coverage. Do not
relabel it as fresh local execution or authenticated vendor acceptance. No check
requires merging, changing product code, or another general audit.
