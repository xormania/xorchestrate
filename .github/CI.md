# Validation

Local and CI execution use the [testing framework](../docs/testing-framework.md).
Python 3.12 is the required validation baseline; the telemetry tests also need a
Python whose SQLite has the WAL fix (CI uses uv-installed Python 3.12.16). Establish a fresh bytecode cache
outside the checkout before starting Python, then install the declared
application/test dependencies and the single pinned linter requirements file:

```bash
export PYTHONPYCACHEPREFIX="$(mktemp -d "${TMPDIR:-/tmp}/xorchestrate-pycache.XXXXXX")"
python3 -m pip install -e '.[test,telemetry]' -r .github/requirements-ci.txt
python3 -m scripts.testkit run --suite static
python3 -m scripts.testkit run --suite tests
python3 -m scripts.testkit run --suite checks
python3 -m scripts.testkit run --suite calibration
python3 -m scripts.testkit run --suite multi-harness
python3 -m scripts.testkit run --suite acceptance
```

`checks` combines `static` and `tests`. The default `python3 -m scripts.testkit run`
composes every component suite once: static checks, tests, calibration, both live
suites and `stories`.
`--family NAME` selects behavioral families without reducing static coverage.
Each run gets fresh private evidence outside
the candidate. Direct pytest is available for a focused scenario; default
collection targets only active tests and validates `contracts/testing.json`.

## CI jobs

CI runs on GitHub-hosted Ubuntu. Pinned actions have read-only repository
permissions. Pull requests, pushes to master and manual runs invoke:

| Job | Purpose |
|---|---|
| Classify changed paths | `scripts/ci/classify_changes.py` decides whether the change touches anything other than documentation; see below |
| Static checks | Shared `static` suite: Ruff lint/security, current-source syntax and deterministic architecture enforcement |
| Application and adapter tests (Python 3.12) | Shared `tests` suite with declared behavioral families and pytest inventory/JUnit, then shared calibration and composed three-harness protocol suites |
| Tmux live acceptance (protocol fixture) | Shared acceptance suite with tmux explicitly installed; private owned process cleanup is part of the receipt |
| Jev architecture diagnostics | Independent diagnostics-only invocation with retained advisory output |
| Hexagonal architecture | Retains the existing required status name and reports the shared static job's outcome; it does not rerun validation |

Each Python job configures `PYTHONPYCACHEPREFIX` in the runner's temporary directory before
invoking Python. Static checks run once in their own job. The Jev diagnostic
calculation remains independent and does not add a second deterministic gate.
Application, calibration and protocol steps have explicit eligibility conditions:
successful checkout/interpreter/dependency setup and a run that has not been
cancelled. A suite failure does not skip the later independent suites or erase
the original job failure. Failed setup produces an unavailable receipt instead of
launching dependent validation. Available evidence uploads even on failure.

### Documentation-only changes

The `paths` job (Classify changed paths) checks out full history and runs
`python3 -m scripts.ci.classify_changes`, which writes `code=true|false` to the
job output. It diffs `HEAD` against `github.event.pull_request.base.sha` for
pull requests and against `github.event.before` for pushes, counting both sides
of renames. A change is documentation-only when every changed path is a
top-level `*.md`, anything under `docs/`, or a `*.md` under `examples/`.
`docs/implementation/owner-brief.md` is excluded because the protocol suites read
it as input. Everything else is code, including `.github/**` (also `CI.md`),
`contracts/**`, `scripts/**`, `tests/**`, `xorchestrate/**`, `examples/*.json`,
`pyproject.toml` and `ruff.toml`. Manual runs and other events, a missing or null
base (new branch, `before=000…`), a diff that fails (for example a force-push
whose old head is gone) and an empty change set all count as code.

The application, tmux acceptance and Jev diagnostics jobs declare `needs: paths`
and the job-level condition
`!cancelled() && needs.paths.outputs.code != 'false'`. They are skipped only
when the classifier explicitly reports `false`. If the classifier fails or emits
nothing, they still run. Static checks and the `Hexagonal architecture` status
job do not depend on `paths` and always run. The static suite is cheap and backs
the required status. It does not lint Markdown, so no documentation check is
lost by skipping the other jobs.

Required status checks: the repository ruleset currently requires only
`Hexagonal architecture`. It needs only `changes` (Static checks), so it reports
a real pass or fail for every change. GitHub reports a job skipped by its
job-level `if` as `skipped`, and a skipped job satisfies a required status
check. So any heavy job made required later will still pass on documentation-only
changes instead of staying pending. Do not move this decision to workflow-level
`paths`/`paths-ignore` filters: a workflow that never starts leaves required
checks pending. Do not gate `changes` on `paths` either: a skipped static job
would make `Hexagonal architecture` fail, because it requires
`needs.changes.result == 'success'`. On code changes the heavy jobs start after
the short classifier job finishes, roughly 10–20 seconds later.

The former historical-runtime and recovered-toolkit jobs, PHP provisioning/tests
and historical runner have been removed. Historical test sources remain available
at their pinned source revisions and in Git history. CI does not preserve an old
implementation as a product acceptance obligation.

Security-lint calibration belongs to the catalog's architecture family and uses
the shared Ruff definition with independent expected findings. Its pinned Ruff
dependency remains installed in the application job. Architecture and tmux
regression tests run through the application suite; dedicated jobs do not rerun
the same selected pytest modules.

## Evidence and limits

`static-checks-python-3.12` retains static reports, effective-input identities,
structured findings and raw command receipts for 30 days.
`framework-checks-python-3.12` retains family/case inventories, JUnit,
command records and calibration reports for 30 days.
Full calibration candidate copies are excluded from artifact uploads.
Composed protocol receipts remain available for seven days; tmux uploads selected
report/scenario and captured outer-command receipts for seven days. Raw native configurations and terminal
logs are private local-run artifacts, not automatically published by CI.

Deterministic fixtures require no model accounts. Authenticated native harnesses,
remote MCP services and actual model behavior need their configured environment;
a fixture pass is not native authentication evidence. Native mode never falls back
to a fixture. See [live test-kit](../docs/live-testkit.md).

Jev diagnostics use the configured repository secret when available, retain their
selected/omitted source scope, and do not override deterministic violations or
create requirements from findings. See
[architecture enforcement](../docs/architecture-enforcement.md).

CI neither requests reviews nor merges branches. xor controls those decisions.

## Telemetry runtime

Application CI installs the optional pinned CCE collector through `.[test,telemetry]`.
It selects uv 0.13.7 / Python 3.12.16 for the collector's fixed SQLite WAL requirement.
Static checks remain independent of this extra. The existing application test-kit
collects the declared telemetry families alongside the current behavior suites.
Optional collector/exporter failure does not replace the application's required
effect, receipt or acceptance recording. See [the active guide](../docs/telemetry.md).
