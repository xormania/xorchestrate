# Executed checks

Measured candidate: `b0a00f1aed08d4ad8e066de8eb51f94e21da9f4a`. Exact retained commands use the original absolute environment paths; replace those paths when reproducing in a new checkout. Logs and machine-readable results are inside `verification-evidence.tar.xz` under `evidence-data/`.

| Check | Local outcome | Evidence |
|---|---|---|
| application | passed, exit 0 | `commands/application/` |
| compile | passed, exit 0 | `commands/compile/` |
| lint-coverage | passed, exit 0 | `commands/lint-coverage/` |
| native-calibration | passed, exit 0 | `commands/native-calibration/` |
| optional-toolchain | passed, exit 0 | `commands/optional-toolchain/` |
| protocol | passed, exit 0 | `commands/protocol/` |
| ruff | passed, exit 0 | `commands/ruff/` |
| work-calibration | passed, exit 0 | `commands/work-calibration/` |

Application: 588 passed, 10 environmental skips, 164 subtests passed. Protocol: 46/46; all12 source faults detected with valid controls. Static/lint/compile/toolchain checks passed. Hosted CI and its exact merge-tree equivalence are in `hosted-ci.json`; raw job logs are under `development/ci/`.

## Actual commands

**application**

```json
["/opt/codex/runtimes/codex-primary-runtime/dependencies/python/bin/python", "-m", "pytest", "tests/hexagonal", "-q", "-rs", "--junitxml=/workspace/scratch/13fc354c99cb/final-verification-3/application.xml"]
```

Working directory: `/workspace/scratch/13fc354c99cb/xorchestrate`; environment overrides: `{}`; started `2026-10-24T01:45:52.012291+00:00`; finished `2026-10-24T01:46:30.105530+00:00`.

**compile**

```json
["/opt/codex/runtimes/codex-primary-runtime/dependencies/python/bin/python", "-m", "compileall", "-q", "xorchestrate", "tests/hexagonal", "scripts/acceptance", "scripts/verification"]
```

Working directory: `/workspace/scratch/13fc354c99cb/xorchestrate`; environment overrides: `{}`; started `2026-10-24T01:45:52.012491+00:00`; finished `2026-10-24T01:45:52.054349+00:00`.

**lint-coverage**

```json
["/opt/codex/runtimes/codex-primary-runtime/dependencies/python/bin/python", ".github/lint_checks.py"]
```

Working directory: `/workspace/scratch/13fc354c99cb/xorchestrate`; environment overrides: `{}`; started `2026-10-24T01:45:52.013557+00:00`; finished `2026-10-24T01:45:52.128429+00:00`.

**native-calibration**

```json
["/opt/codex/runtimes/codex-primary-runtime/dependencies/python/bin/python", "-m", "scripts.verification.calibrate_suite", "--candidate", "/workspace/scratch/13fc354c99cb/xorchestrate", "--out", "/workspace/scratch/13fc354c99cb/final-verification-3/native-calibration"]
```

Working directory: `/workspace/scratch/13fc354c99cb/xorchestrate`; environment overrides: `{}`; started `2026-10-24T01:45:52.013981+00:00`; finished `2026-10-24T01:46:04.820771+00:00`.

**optional-toolchain**

```json
["/opt/codex/runtimes/codex-primary-runtime/dependencies/python/bin/python", "-m", "unittest", "discover", "-s", ".github/tests", "-v"]
```

Working directory: `/workspace/scratch/13fc354c99cb/xorchestrate`; environment overrides: `{}`; started `2026-10-24T01:45:52.014148+00:00`; finished `2026-10-24T01:45:52.269428+00:00`.

**protocol**

```json
["/opt/codex/runtimes/codex-primary-runtime/dependencies/python/bin/python", "-m", "scripts.verification.multi_harness", "--mode", "protocol-fixture", "--criteria", "/workspace/scratch/13fc354c99cb/final-verification-3/criteria.json", "--brief", "/workspace/scratch/13fc354c99cb/upload/Xorchestrate-Implementation-Brief (2).md", "--out", "/workspace/scratch/13fc354c99cb/final-verification-3/protocol", "--timeout", "60"]
```

Working directory: `/workspace/scratch/13fc354c99cb/xorchestrate`; environment overrides: `{}`; started `2026-10-24T01:45:52.055043+00:00`; finished `2026-10-24T01:46:05.621117+00:00`.

**ruff**

```json
["/opt/codex/runtimes/codex-primary-runtime/dependencies/python/bin/python", "-m", "ruff", "check", "xorchestrate", "tests", "devtools", ".github", "scripts"]
```

Working directory: `/workspace/scratch/13fc354c99cb/xorchestrate`; environment overrides: `{}`; started `2026-10-24T01:45:52.129037+00:00`; finished `2026-10-24T01:45:52.162306+00:00`.

**work-calibration**

```json
["/opt/codex/runtimes/codex-primary-runtime/dependencies/python/bin/python", "tests/hexagonal/calibrate_work_acceptance.py", "--output", "/workspace/scratch/13fc354c99cb/final-verification-3/work-calibration"]
```

Working directory: `/workspace/scratch/13fc354c99cb/xorchestrate`; environment overrides: `{}`; started `2026-10-24T01:45:52.162855+00:00`; finished `2026-10-24T01:45:56.790339+00:00`.
