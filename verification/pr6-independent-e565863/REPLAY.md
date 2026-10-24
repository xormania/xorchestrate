# Replay this independent review

Product: `e565863ae72180571678c8f6118ee6e00b129e2e`; tree `474de55c5947890ab4092daeca7ff39f9bbbc400`. The commit publishing these artifacts is **not** the product. The review recommends holding integration for residual IV01–IV03.

Use a new directory, absolute paths, Python 3.12 (this run used 3.12.14), functioning local Unix/TCP sockets and tmux. No vendor credentials are needed. Do not run expected-defect commands under blanket `set -e`: exit semantics below are intentional.

## Retrieve, verify and restore

Download this directory from the immutable publication commit linked by the review response, or clone its verification branch and record exact HEAD. Check all standalone files and the archive before restoration:

```sh
sha256sum -c SHA256SUMS
python3 restore_evidence.py Xorchestrate-PR6-Independent-e565863-Evidence.zip --verify-only
python3 restore_evidence.py Xorchestrate-PR6-Independent-e565863-Evidence.zip --out /absolute/new-review
export R=/absolute/new-review
```

The content-addressed ZIP maps original paths, byte counts, modes and SHA-256 values through `package-manifest.json`. Ordinary extraction does not recreate the evidence tree. The helper verifies bytes and paths without executing subject code. Caches and nonportable runtime symlinks/endpoints are explicitly listed as exclusions; logs, failures, scripts, raw observations, databases and copied mutant/control sources are retained. The source bundle and minimal original witness inputs are included. Complete prior archives remain directly retrievable at their immutable GitHub publications and need not be duplicated in this ZIP.

## Recover exact source

```sh
git init "$R/repository"
git -C "$R/repository" fetch "$R/inputs/source.bundle" \
  'refs/independent-e565863/*:refs/independent-e565863/*'
git -C "$R/repository" worktree add --detach "$R/candidate" e565863ae72180571678c8f6118ee6e00b129e2e
git -C "$R/repository" worktree add --detach "$R/checker" e565863ae72180571678c8f6118ee6e00b129e2e
git -C "$R/repository" worktree add --detach "$R/baseline" ef7567516520543970af55b6f19534cb66eda357
git -C "$R/candidate" show -s --format='%H%n%T%n%P'
git -C "$R/candidate" status --porcelain
python3 -m venv "$R/venv"
export PYTHON="$R/venv/bin/python"
"$PYTHON" -m pip install -r "$R/inputs/requirements-replay.txt"
"$PYTHON" -m pip install --no-deps -e "$R/candidate"
export CANDIDATE="$R/candidate" CHECKER="$R/checker" BASELINE="$R/baseline"
export PYTHONDONTWRITEBYTECODE=1
export PATH="$R/inputs/tooling/unpacked/usr/bin:$R/venv/bin:$PATH"
export FRESH="$R/fresh"
mkdir "$FRESH"
```

The verified bundled tmux 3.5a executable is Linux x86-64 tooling from the GitHub evidence input; portability is limited. Substitute a functioning locally installed tmux if necessary and record its identity. Do not substitute another product commit. Keep candidate/checker source unchanged and outputs external. A separate checker is execution isolation, not an independent correctness oracle.

## Four original defects and repaired controls

```sh
"$PYTHON" "$R/inputs/original-witnesses/iv01.py" "$BASELINE" "$FRESH/iv01-baseline.json"
"$PYTHON" "$R/inputs/original-witnesses/iv01.py" "$CANDIDATE" "$FRESH/iv01-candidate-original.json"
"$PYTHON" "$R/results/iv01/closed-iv01.py" "$CANDIDATE" "$FRESH/iv01-candidate-closed.json"
(cd "$BASELINE" && PYTHONPATH="$BASELINE" "$PYTHON" "$R/inputs/original-witnesses/iv02.py" --peers codex claude grok --out "$FRESH/iv02-baseline")
(cd "$CANDIDATE" && PYTHONPATH="$CANDIDATE" "$PYTHON" "$R/inputs/original-witnesses/iv02.py" --peers codex claude grok --out "$FRESH/iv02-candidate")
"$PYTHON" "$R/inputs/original-witnesses/iv03.py" --candidate "$BASELINE" --baseline "$R/inputs/retained-ef-protocol" --out "$FRESH/iv03-baseline"
"$PYTHON" "$R/inputs/original-witnesses/iv03.py" --candidate "$CANDIDATE" --baseline "$R/inputs/retained-ef-protocol" --out "$FRESH/iv03-candidate"
"$PYTHON" "$R/inputs/original-witnesses/iv04.py" --candidate "$BASELINE" --out "$FRESH/iv04-baseline"
"$PYTHON" "$R/inputs/original-witnesses/iv04.py" --candidate "$CANDIDATE" --out "$FRESH/iv04-candidate"
```

IV01 original exits 0 on baseline because the disclosure exists, and 1 on the repair because its old assertion requires disclosure. The two-line expectation adaptation exits 0 while checking absence and preserved authorized/direct-denial controls. IV02 baseline exits 1 for Codex BA; repaired six schedules exit 0. IV03/IV04 programs record observations and may exit 0 on both versions: inspect their JSON. Original IV03 mutations retain 46/46 on baseline and reject on repair. Original IV04 same-line unrelated raise changes from `detected` to `unrelated_failure`; genuine assertion stays `detected`.

## Decisive new counterexamples

```sh
"$PYTHON" "$R/results/iv01/probe_assessment_binding_v2.py" --candidate "$CANDIDATE" --out "$FRESH/iv01-assessment"
"$PYTHON" "$R/results/iv02/scripts/submission_gap_v2.py" --candidate "$CANDIDATE" --out "$FRESH/iv02-submission-gap"
(cd "$CHECKER" && PYTHONPATH="$CHECKER" "$PYTHON" "$R/results/root-rechecks/check_iv02.py" "$FRESH/iv02-submission-gap" "$FRESH/iv02-separate-checker.json")
"$PYTHON" "$R/results/iv03/minimal_counterexamples.py" --candidate "$CHECKER" --input "$R/results/execution/protocol" --out "$FRESH/iv03-identities"
"$PYTHON" "$R/results/iv04/challenge.py" --candidate "$CANDIDATE" --out "$FRESH/iv04-adversarial"
"$PYTHON" "$R/results/iv04/challenge_positions_v2.py" --candidate "$CANDIDATE" --out "$FRESH/iv04-positions"
```

Expected on this exact candidate:

- IV01 exits 0 because the new typed disclosure and all 14 checks (11 controls and three disclosure assertions) reproduce on each store. Read `results.json` and store `raw.json`.
- IV02 exits 0 after the complete controlled schedule: product remains active while B is held; a one-observation forged-idle append incorrectly passes Grok completion, while Codex rejects. Both finally reach legitimate idle. `forged-delta.json` proves every original raw record is unchanged. The separate-checker command reproduces those verdicts; it does not independently justify them. Accepted-submission records, adapter interjection identity and peer-side gate prove the expectation.
- IV03 exits **1**: 15 false acceptances among 30 minimal cases, no false rejections. Each contradiction changes an applicable explicit claim; absent-optional and coherent alternate-work controls pass. Use a newly executed protocol directory with `--input` if desired. The archived input is this review's complete fresh raw run, not a summary.
- IV04 primary challenge exits 0 for all 32 cases. Positions V2 exits **1** for four documented valid-encoding sensitivity misses; neither unrelated nor ambiguous exceptions receive detection credit.

The track reports describe expanded probes, duplicate controls, mixed callback/tail schedules and requirements. Script V1 versions, failed attempts and source/peer adaptations are retained. Do not treat program exit zero as automatic product closure.

## Full suite, actual composed protocol and original calibrations

The recorder was copied byte-for-byte from the checked repair evidence, inspected, and used solely for command execution/provenance. Independent auditors and new probes supply separate expectations.

```sh
"$PYTHON" "$R/results/recorder/run_final.py" \
  --candidate "$CANDIDATE" --checker "$CHECKER" \
  --sha e565863ae72180571678c8f6118ee6e00b129e2e \
  --out "$FRESH/execution" --python "$PYTHON" \
  --tmux-bin "$R/inputs/tooling/unpacked/usr/bin" \
  --brief "$CANDIDATE/docs/implementation/owner-brief.md"
```

This runs full/focused pytest with raw XML, seven native plus five work source-fault calibrations, static/optional checks, live tmux, criteria extraction, actual three-peer `protocol-fixture`, raw-report recomputation and before/after source/module inventories. Recorded result: full 1,127+164 pass, protocol 46/46 exact recomputation, all 12 faults detected. The first concurrently executed focused suite had one synchronization-sensitive calibration failure; it remains failed. An isolated selector and full focused replay passed. Inspect XML/logs rather than demanding a green wrapper status, and retain every retry separately.

For direct protocol replay/recomputation from the separate checker:

```sh
cd "$CHECKER"
export PYTHONPATH="$CHECKER"
"$PYTHON" -m scripts.verification criteria --brief "$CANDIDATE/docs/implementation/owner-brief.md" --out "$FRESH/criteria.json"
"$PYTHON" -m scripts.verification.multi_harness --candidate "$CANDIDATE" --mode protocol-fixture --brief "$CANDIDATE/docs/implementation/owner-brief.md" --criteria "$FRESH/criteria.json" --out "$FRESH/protocol" --timeout 60
"$PYTHON" -m scripts.verification check --run "$FRESH/protocol/run.json" --criteria "$FRESH/protocol/criteria.json" --bindings "$FRESH/protocol/bindings.json" --fixture "$FRESH/protocol/fixture.json" --out "$FRESH/recomputed.json"
"$PYTHON" "$R/results/iv04/audit_calibration_v2.py" --candidate "$CANDIDATE" --baseline "$BASELINE" --execution "$FRESH/execution" --out "$FRESH/calibration-audit.json"
```

Compare complete parsed reports, all content bindings and independent inventories, not totals alone. Consult `IV04-Report.md` for the source/assertion audit, `Preservation-Report.md` for independent raw checks, and `Closed-Findings-Report.md` for seven prior closures. Their scripts include precise additional commands and known path adaptations. Some copied old scripts write beside themselves: copy them into a fresh directory first.

## Complete retained-input/hosted provenance audit

The full prior publications were freshly retrieved and verified during this review. To rerun auditors expecting their restored layouts, retrieve the same immutable Git objects (not branch heads):

```sh
git -C "$R/repository" fetch https://github.com/xormania/xorchestrate.git 37547a138758fe70e6cc471b491aaab01bfc3a05
git -C "$R/repository" fetch https://github.com/xormania/xorchestrate.git 3ac2342b6655f1a30652380c85ceb55262b7ad0c
git -C "$R/repository" worktree add --detach "$R/publication" 37547a138758fe70e6cc471b491aaab01bfc3a05
git -C "$R/repository" worktree add --detach "$R/original-publication" 3ac2342b6655f1a30652380c85ceb55262b7ad0c
(cd "$R/publication/verification/pr6-iv-repairs-e565863" && sha256sum -c SHA256SUMS && python3 restore_evidence.py Xorchestrate-PR6-IV-Repair-Evidence.zip --out "$R/restored")
(cd "$R/original-publication/verification/pr6-independent-ef75675" && sha256sum -c PUBLICATION-SHA256SUMS && python3 restore_review.py Xorchestrate-PR6-Independent-Verification-Evidence.zip --out "$R/original-restored")
python3 "$R/results/preservation/identities.py" "$R" "$FRESH/identities.json"
python3 "$R/results/preservation/audit.py" --root "$R" --out "$FRESH/provenance.json" --fresh-xml "$R/results/execution/application.xml" --fresh-protocol "$R/results/execution/protocol"
python3 "$R/results/preservation/raw_audit.py" "$R" "$FRESH/independent-raw.json"
"$PYTHON" "$R/results/preservation/recompute.py" "$R" "$FRESH/retained-recomputation"
```

Hosted run 40132785317's five archives and decoded logs are durably included in the repair publication; GitHub API metadata/logs freshly retrieved by this review are retained in `results/preservation/`. Actual hosted integration ancestry was independently verified through both Git and the GitHub commit object. Python 3.11/3.12 full suites ran hosted; only 3.12 ran hosted calibration/protocol. Historical toolkit/runtime results remain historical, not fresh active-suite execution. Authenticated vendors remain the user's separate local work.
