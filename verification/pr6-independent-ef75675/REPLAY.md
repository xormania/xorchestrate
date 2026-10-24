# Replay the PR #6 independent verification

Pinned candidate/checker: `ef7567516520543970af55b6f19534cb66eda357`, tree `a0813aae760e6e5bb6245693bd92b7e7c321348b`. The review recommends holding integration for IV01–IV04. No repairs or merge are included.

The ZIP uses SHA-256 content deduplication: repeated calibration checkouts and raw runs share payload bytes, while every original path/mode/hash remains in `package-manifest.json`. Read the supplied standard-library helper before running it. It verifies all payloads, validates paths, rejects conflicting existing files and reconstructs evidence only; it does not execute recovered code or contact a service.

```sh
python restore_review.py Xorchestrate-PR6-Independent-Verification-Evidence.zip --verify-only
python restore_review.py Xorchestrate-PR6-Independent-Verification-Evidence.zip --out /fresh/review
cd /fresh/review
```

The report and this guide are also readable directly inside the ZIP. A matching standalone helper/report accompany it. The full original repair ZIP is retained unchanged as `inputs/Xorchestrate-Repair-Evidence.zip`; the original handoff README, assignment and checksum files are under `inputs/handoff/`. This review's bundle is distinct from the stale historical bundle inside the repair ZIP.

## Recover exact source and retained inputs

```sh
export XOR_REVIEW_ROOT="$PWD"
git init candidate
git -C candidate fetch "$XOR_REVIEW_ROOT/reviewer/root/verified-source.bundle" 'refs/reviewer/*:refs/reviewer/*'
git -C candidate checkout --detach ef7567516520543970af55b6f19534cb66eda357
git -C candidate show -s --format='%H%n%T%n%P' HEAD
git -C candidate status --porcelain
git -C candidate worktree add --detach "$XOR_REVIEW_ROOT/checker" ef7567516520543970af55b6f19534cb66eda357
mkdir input original
python -m zipfile -e inputs/Xorchestrate-Repair-Evidence.zip input
python input/verify_package.py
python -m zipfile -e input/original-review.zip original
tar -xJf input/measurements.tar.xz -C input
python input/restore_git_inputs.py --repo candidate --root input
python input/restore_git_inputs.py --repo candidate --root input --verify-only
python3 -m venv venv
venv/bin/python -m pip install -e 'candidate[test]' -r candidate/.github/requirements-ci.txt
mkdir fresh
```

Use Python 3.11/3.12; this review ran 3.12.14. Exact installed versions are in `reviewer/root/dependencies.txt`. Initial dependency installation may require network. Source, raw observations and complete recovery inputs are provided offline. The bundle uses custom reviewer refs: use the explicit fetch refspec above, not plain clone (which does not select those refs). Base/d4 are in its complete history. For original probe replay, create the required track's omitted `base/` checkout at `b0a00f1aed08d4ad8e066de8eb51f94e21da9f4a`.

## Minimal independent counterexamples

```sh
venv/bin/python reviewer/delivery_authority/probe_historical_work.py candidate fresh/historical-scope.json
venv/bin/python reviewer/checker/minimal_identity_witness.py --candidate candidate --baseline input/repair-evidence/final/protocol --out fresh/callback-identity
venv/bin/python reviewer/checker/challenge_attribution-v2.py --candidate candidate --out fresh/assertion-attribution
cd candidate
PYTHONPATH="$PWD" "$XOR_REVIEW_ROOT/venv/bin/python" "$XOR_REVIEW_ROOT/reviewer/completion/reverse_overlap.py" --peers codex claude grok --out "$XOR_REVIEW_ROOT/fresh/completion-orders"
```

Expected results on the pinned candidate:

- Scope probe exits 0 because both stores reproduce the denied historical-session leak and pass the controls.
- Identity probe exits 0 while its results show 46/46 checker passes on contradictory one-field mutations and failed production receipts. Exit status alone is not the product verdict.
- Attribution v2 exits 0; same-line unrelated raise is falsely `detected`, separate-line raise is `unrelated_failure`, real assertion failure is `detected`.
- Completion script exits 1 after all controls: only Codex BA completion becomes idle/passed with A held; Codex AB and Claude/Grok both orders reject until both finish.

Outputs require fresh paths. The completion fixture uses real subprocesses/hooks/gateway/SQLite and needs working local sockets. It does not use vendor credentials. No source edits are required.

## Baseline and original calibrations

From `candidate/` with this venv on PATH:

```sh
export PATH="$XOR_REVIEW_ROOT/venv/bin:$PATH"
python -m pytest tests/hexagonal -q -rs --junitxml="$XOR_REVIEW_ROOT/fresh/application.xml"
python "$XOR_REVIEW_ROOT/input/verify_required_assertions.py" --selectors "$XOR_REVIEW_ROOT/input/original-requirement-selectors.json" --junit "$XOR_REVIEW_ROOT/fresh/application.xml" --out "$XOR_REVIEW_ROOT/fresh/original-selectors.json"
python "$XOR_REVIEW_ROOT/input/verify_required_assertions.py" --selectors "$XOR_REVIEW_ROOT/input/all-requirement-selectors.json" --junit "$XOR_REVIEW_ROOT/fresh/application.xml" --out "$XOR_REVIEW_ROOT/fresh/all-selectors.json"
python -m scripts.verification.calibrate_suite --candidate "$PWD" --out "$XOR_REVIEW_ROOT/fresh/native-calibration"
python tests/hexagonal/calibrate_work_acceptance.py --output "$XOR_REVIEW_ROOT/fresh/work-calibration"
python -m ruff check xorchestrate tests devtools .github scripts
python .github/lint_checks.py
python -m unittest discover -s .github/tests -v
python -m compileall -q xorchestrate tests/hexagonal scripts/acceptance
```

The exact focused nine-module command, live tmux command, executable/hash/library paths and all environment adaptations are in `reviewer/execution/commands/`, `network-permitted/commands/` and `final-with-tmux/commands/`. Review-local tmux is in `reviewer/execution/tooling/`; choose the executable/library locations documented by `run_with_tmux.py` or install tmux normally. Confirm actual create/send/capture/cleanup, not its version string. Preserve environmental failures separately. The fresh final credited suite is `final-with-tmux/application.xml` (809 primary rows; 164 subtest events separately); initial attempts are not replacements.

## Composed protocol and saved-audit replay

```sh
cd "$XOR_REVIEW_ROOT/checker"
python -m scripts.verification criteria --brief "$XOR_REVIEW_ROOT/input/owner-brief.md" --out "$XOR_REVIEW_ROOT/fresh/criteria.json"
python -m scripts.verification.multi_harness --candidate "$XOR_REVIEW_ROOT/candidate" --mode protocol-fixture --brief "$XOR_REVIEW_ROOT/input/owner-brief.md" --criteria "$XOR_REVIEW_ROOT/fresh/criteria.json" --out "$XOR_REVIEW_ROOT/fresh/protocol" --timeout 60
python -m scripts.verification check --run "$XOR_REVIEW_ROOT/fresh/protocol/run.json" --criteria "$XOR_REVIEW_ROOT/fresh/protocol/criteria.json" --bindings "$XOR_REVIEW_ROOT/fresh/protocol/bindings.json" --fixture "$XOR_REVIEW_ROOT/fresh/protocol/fixture.json" --out "$XOR_REVIEW_ROOT/fresh/protocol-recomputed.json"
python "$XOR_REVIEW_ROOT/input/repair-evidence/final/independent-evidence-audit/audit.py" --candidate "$XOR_REVIEW_ROOT/candidate" --checker "$XOR_REVIEW_ROOT/checker" --evidence "$XOR_REVIEW_ROOT/input/repair-evidence/final" --brief "$XOR_REVIEW_ROOT/input/owner-brief.md" --out "$XOR_REVIEW_ROOT/fresh/saved-audit"
```

To recheck a retained raw run, substitute that run's unchanged criteria/bindings/fixture/run files in the check command. Locations: final local `input/repair-evidence/final/protocol`; original `original/review/baseline/protocol`; d4 `input/repair-evidence/superseded-d4bd829/protocol`. Exact hosted paths and independent inventory comparison are retained in `reviewer/provenance_coverage/protocol-reconciliation.json`. Final runs recompute46/46; original/d4 recompute44 passing plus2 failed Codex/Grok completion cases, overall incomplete. Never transplant final aggregate observations into old evidence.

Track-specific original repros, reviewer variants, clean/faulty controls and command adaptations are documented in each `reviewer/*/REPORT.md`. Commands retain their actual original absolute paths; adapt only workspace/output prefixes in copies. Several original scripts write beside themselves, so copy them first. The source bundle and original repair ZIP preserve all historical evidence needed to validate these adaptations.

`command-run-ledger.json` indexes all command records and statuses. `input-identities.json` binds product/checker/configuration/criteria/fixture/brief; `closure-verdicts.json` and `capability-assessment.json` hold the conclusions. The manifest also preserves v1 failed/confounded probes and their distinct v2 witnesses. Package verification establishes byte identity, not correctness of those conclusions.
