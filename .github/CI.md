# Validation

Local and CI execution use the [testing framework](../docs/testing-framework.md).
Python 3.12 is the required validation baseline; the telemetry tests also need a
Python whose SQLite has the WAL fix (CI uses uv-installed Python 3.12.16). Establish a fresh bytecode cache
outside the checkout before starting Python, then install the declared
application/test dependencies from the native uv lock (install uv 0.13.7 first):

```bash
export PYTHONPYCACHEPREFIX="$(mktemp -d "${TMPDIR:-/tmp}/xorchestrate-pycache.XXXXXX")"
export UV_PROJECT_ENVIRONMENT="$(mktemp -d "${TMPDIR:-/tmp}/xorchestrate-env.XXXXXX")"
python3 -m scripts.ci.dependencies check
uv sync --locked --only-group validation --no-install-project --managed-python --python 3.12.16
uv sync --locked --no-default-groups --group validation --extra test --no-install-project --managed-python --python 3.12.16
uv pip install --python "$UV_PROJECT_ENVIRONMENT/bin/python" --no-deps --no-build-isolation -e .
export PATH="$UV_PROJECT_ENVIRONMENT/bin:$PATH"
python3 -m scripts.testkit run --suite static
python3 -m scripts.testkit run --suite tests
python3 -m scripts.testkit run --suite tests --shard 1/4   # one of four shards
python3 -m scripts.testkit run --suite checks
python3 -m scripts.testkit run --suite calibration
python3 -m scripts.testkit run --suite multi-harness
python3 -m scripts.testkit run --suite acceptance
```

`checks` combines `static` and `tests`. The default `python3 -m scripts.testkit run`
composes every component suite once: static checks, tests, calibration, both live
suites and `stories`.
`--family NAME` selects behavioral families without reducing static coverage.
`--shard K/N` (only with `--suite tests`) runs one balanced, deterministic shard of the selected test modules;
shards 1..N together cover the selection exactly once (see
[sharding](../docs/testing-framework.md#sharding-the-tests-suite)).
Each run gets fresh private evidence outside
the candidate. Direct pytest is available for a focused scenario; default
collection targets only active tests and validates `contracts/testing.json`.

## CI jobs

The [installed package acceptance](../docs/package-acceptance.md) job builds and
installs the actual wheels and source distributions of `xorchestrate` and its
alias `xork` into independent runtime-only consumers. It records CLI, persistent-state, stdio MCP and absolute gateway launch
checks outside the source checkout, using locked dependency constraints.

The job also installs the launcher through each offline [install path](../docs/install.md)
(`uv tool install` from a flat index as `xorchestrate` and as `xork`, the wheel,
a local git tag and a source directory, and `uv tool run --from <wheel>`) and
checks the `install_source` each reports, through both `xo` and `xorchestrate`.

CI runs on GitHub-hosted Ubuntu. Pinned actions have read-only repository
permissions. PR open/source/reopen and all edits receive candidate validation;
eligible deterministic lanes may reuse exact qualified evidence. Static checks
and both stable required verdicts run even for title/body edits, so the newest
workflow run can satisfy branch protection. Marking a draft ready preserves its
existing candidate checks without starting a new run.
Pushes to master and manual runs execute full validation. See
[PR lifecycle validation](../docs/pr-lifecycle-ci.md) for event handling, original
evidence links and the `CI_REUSE_ENABLED=false` recovery switch. Selected jobs are:

| Job | Purpose |
|---|---|
| Classify changed paths | `scripts/ci/classify_changes.py` decides whether the change touches anything other than documentation; see below |
| Static checks | Shared `static` suite: Ruff lint/security, current-source syntax and deterministic architecture enforcement |
| Application tests shard K/N (Python 3.12) | Matrix of N jobs, each running `--suite tests --shard K/N` with its own pytest inventory/JUnit |
| Application and adapter tests (Python 3.12) | Retains the existing status name; needs every tests shard and fails unless the shard matrix succeeded. It does not rerun validation |
| Calibration and multi-harness verification | Shared `calibration` suite and composed three-harness `multi-harness` protocol suite, in parallel with the tests shards |
| Tmux live acceptance (protocol fixture) | Shared acceptance suite. The job provisions tmux for the test kit's own supervisor (installed through apt only when the runner image lacks it; the step prints `tmux -V`). That supervisor is not the product terminal host. Private owned process cleanup is part of the receipt |
| Hexagonal architecture | Retains the existing required status name and reports the shared static job's outcome; it does not rerun validation |

Each Python job configures `PYTHONPYCACHEPREFIX` and `UV_PROJECT_ENVIRONMENT` in
runner temporary storage before validation. uv-managed Python 3.12.16 and uv 0.13.7 are
selected explicitly. Native `uv lock --check` and the generated validation export
check reject dependency-input drift, then `uv sync --locked` installs the chosen
profile. Validation tools are bootstrapped from the lock before the telemetry
source build, which uses the installed backend without build isolation. Editable application installation uses `--no-deps --no-build-isolation`
with the already locked backend. Public package dependency ranges remain unchanged.

The setup-uv download/wheel cache is keyed by `uv.lock`; a cache hit does not
replace frozen installation or test execution. Ordinary jobs omit telemetry;
application shards explicitly select that optional extra. The validation dependency
group supplies Ruff and build tooling. `.github/requirements-ci.txt` is a generated
pip-compatible export of that group, not a second manually edited pin source.
See [validation dependencies](../docs/validation-dependencies.md) for intentional
updates, runtime/build exports and separately labeled fresh-resolution checks.

Static checks run once in their own job, after the initial candidate context is available. They run regardless of the documentation-only decision. The Jev diagnostic
calculation remains independent and does not add a second deterministic gate.
The `tests` suite is the longest validation, so it runs as parallel shards
balanced by the committed `contracts/test-timings.json`. The shard count N is
defined once, as the matrix value `shards` of the `application-tests` job (currently 4);
job and step names, the `--shard` argument and artifact names all reference it,
and the `shard` list must be 1..N. A framework test enforces both. Shards use
`fail-fast: false` so one failing shard does not hide the others' results.
The tests shard, calibration and protocol suite steps have explicit eligibility conditions:
successful checkout/interpreter/dependency setup and a run that has not been
cancelled. In the verification job a calibration failure does not skip the protocol suite or erase
the original job failure. Failed setup produces an unavailable receipt instead of
launching dependent validation. Available evidence uploads even on failure.

### Documentation-only changes

Validation runs can omit fresh behavioral execution through the independently
guarded documentation decision or qualified reuse. Known obsolete source runs
can also stop after proving a surviving replacement, without qualifying a pass.
Pushes (including every push
to `master`) and manual runs always run the full pipeline. Non-PR concurrency
cancels older work on the same ref; PR validation concurrency separates source
head/base candidates and metadata has a separate group. A delayed older event
cannot cancel a newer candidate's validation. A documentation-only master merge still
receives full validation of the integrated tree.

For a pull request, the `paths` job (Classify changed paths) checks out full
history and diffs `HEAD` (the merge commit) against
`github.event.pull_request.base.sha`. Two independent steps must both agree
before the heavy jobs are skipped:

1. **Guard** (inline Bash, does not use the classifier). It reports
   `force=true` unless the event is `pull_request` and
   `git diff --name-only -z` against the base succeeds with a non-empty
   result. It also reports `force=true` if any path is under `scripts/ci/`,
   `.github/`, `contracts/` or `tests/`. A pull request that changes the
   classifier, the workflow or the validation definitions therefore cannot
   decide for itself that its own tests may be skipped. A pull request can still
   edit the guard itself in `ci.yml`. Review covers that case, because
   `.github/**` always forces a full run unless the guard is removed.
2. **Classifier** (`python3 -m scripts.ci.classify_changes`). It writes
   `code=true|false` from `git diff --raw -z --no-renames`, counting both sides
   of renames. A change is documentation-only when every changed entry is a
   regular file (mode `100644`/`100755`, or absent on the added/deleted side)
   and its path is a top-level `*.md`, anything under `docs/`, or a `*.md` under
   `examples/`. Symlinks (`120000`), gitlinks (`160000`) and other modes are
   code even under `docs/`, because validation copies `docs/` and follows
   symlinks. `docs/implementation/owner-brief.md` is excluded because the
   protocol suites read it as input. Everything else is code, including
   `.github/**` (also `CI.md`), `contracts/**`, `scripts/**`, `tests/**`,
   `xorchestrate/**`, the example projects' `examples/*/.xorchestrate/*.json`, `pyproject.toml` and `ruff.toml`. A
   missing or null base, a failed or malformed diff and an empty change set all
   count as code.

The job output is
`steps.guard.outputs.force == 'false' && steps.classify.outputs.code || 'true'`.
It is `false` only when the guard ran and allowed classification and the
classifier reported `false`. The tests shards, the application aggregate, the
verification, tmux acceptance and Jev diagnostics jobs declare `needs: paths` and
the documentation condition `!cancelled() && needs.paths.outputs.code != 'false'`
(the application aggregate also needs the shard matrix). The deterministic
behavioral jobs additionally require `needs.paths.outputs.reuse != 'true'`;
the advisory Jev job remains fresh for code-bearing candidates. An unavailable
classifier or reuse planner cannot skip validation. An explicit supersession
decision can skip old static and behavioral lanes, but cannot qualify a pass.
On a documentation-only or
qualified-reuse PR the shards and application aggregate skip together. Otherwise
the application aggregate uses `always()` and fails unless the whole matrix
succeeded. The final `CI result` always authenticates the explicit applicability
or reuse decision. Static checks wait for `paths` to receive the candidate context,
use an always-running condition, and are never gated on documentation or reuse. The
`Hexagonal architecture` status always reports their result. The static suite backs
the required status. It does not lint Markdown, so no documentation check is
lost by skipping the other jobs.

Required status checks: the observed repository ruleset requires both
`CI result` and `Hexagonal architecture`, sourced from GitHub Actions integration
15368. `Hexagonal architecture` reports the shared static result; `CI result`
qualifies the complete applicable execution and retained evidence. Keep both
requirements. Documentation-only applicability remains a job-level decision;
workflow-level path filters would leave absent required checks pending.

The former historical-runtime and recovered-toolkit jobs, PHP provisioning/tests
and historical runner have been removed. Historical test sources remain available
at their pinned source revisions and in Git history. CI does not preserve an old
implementation as a product acceptance obligation.

Security-lint calibration belongs to the catalog's architecture family and uses
the shared Ruff definition with independent expected findings; it runs in the
tests shard that owns its module, where the pinned Ruff dependency is installed.
The verification job installs `.[test]`. Architecture and tmux
regression tests run through the tests shards; dedicated jobs do not rerun
the same selected pytest modules.

## Evidence and limits

`static-checks-python-3.12` retains static reports, effective-input identities,
structured findings and raw command receipts for 30 days.
`framework-tests-python-3.12-shard-K-of-N` retains each shard's family/case
inventory, JUnit and command records for 30 days. `calibration-python-3.12` retains
calibration reports and command records for 30 days. Shard JUnit reports can
regenerate the timings file with `python3 -m scripts.testkit.timings`.
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

Jev source diagnostics and candidate-bound test evidence triage run in the separate
`Jev advisory` workflow after CI completes. `CI result` has no Jev dependency,
provider credential, diagnostic installation or inference step. The consumer uses
trusted default-branch code and model policy; the tested checkout and artifacts
are read as data. It authenticates their producer identity, reserves each source
attempt before provider access and retains the source and trusted revisions
separately. Cancelled, superseded, documentation-only and behavior-reuse runs do
not start new inference. See [independent advice](../docs/jev-ci-advice.md).

Full synthetic and historical calibration is an explicit default-branch workflow
dispatch. Routine lifecycle events do not repeat calibration or a health request.
The local evaluation and live-health commands remain available. See
[operational evaluation](../docs/jev-operational.md).

CI neither requests reviews nor merges branches. xor controls those decisions.

## Release workflow

`.github/workflows/release.yml` is separate from CI. It runs on a pushed
`v*.*.*` tag (publishing to PyPI) or manually (a TestPyPI dry run), with the same
pinned actions, uv 0.13.7 and Python 3.12.16. The workflow has no default
permissions; build and check jobs read contents, only the publish job may request
an OIDC token for trusted publishing, and only the GitHub release job writes
contents. See [releases](../docs/releases.md).

## Telemetry runtime

Application CI selects the lock's `test` extra; the telemetry collector ships with the
application. It uses uv 0.13.7 / Python 3.12.16 for the SQLite corpus adapter's WAL-fix
requirement. Static checks need no SQLite runtime. The existing application test-kit
collects the declared telemetry families alongside the current behavior suites.
Collector or exporter failure does not replace the application's required
effect, receipt or acceptance recording. See [the active guide](../docs/telemetry.md).

## Aggregate result and reviewer summary

`CI result` is an always-running final job on all selected events, including
metadata edits. It requires successful path
classification, static checks, every application shard and its aggregate,
calibration/multi-harness verification, tmux acceptance, and installed-package
acceptance when that job is present, either executed now or independently
qualified from the same candidate. Jev remains advisory. Heavy jobs can skip for
an explicit successful documentation-only decision, or an authenticated reuse
plan whose original receipts are requalified. An absent decision, failed
classifier, missing proof or cancellation cannot establish a pass. Static checks
remain required to qualify every selected validation run.

The aggregate authenticates this run's original per-lane producers through
GitHub jobs, attempts, artifact metadata, check association and immutable Git
objects. It downloads exact artifact IDs and verifies their archive digests
before bounded extraction. The actual checkout context, initial captured copy,
and downstream lane manifests must agree. See
[candidate evidence](../docs/ci-candidate-evidence.md) for the versioned schema,
partial-rerun behavior, runtime inputs and qualification limits.

The expected suite reports must pass and identify a clean checkout of the actual
tested commit. Application inventories must contain exactly the workflow's shard
set, use the current catalog identity, have disjoint selected modules/cases, and
cover every catalog module. Inventory hashes remain checked by the existing
qualifier. Missing or foreign required evidence fails the aggregate even when
job outcomes say success. It does not independently enumerate dynamic tests or
assess assertion quality.

The job summary lists source head and tested merge commit separately, job outcomes,
recorded family/shard pass and skip counts, command durations and the run's artifact
links. Command durations are not workflow wall time. Full reports remain separate
artifacts, including `ci-result-evidence--attempt-N/result.json`. The summary states evidence
qualification; GitHub's final `CI result` job status also includes failures of the
checkout, artifact download and final upload steps. The same stdlib aggregation
entrypoint is available locally as `python3 -m scripts.ci.result --help`.

The aggregate finishes when deterministic evidence qualification finishes. Jev
assessments, model probabilities, source scope and execution receipts are retained
in the independent `jev-advice-...` artifact. They cannot qualify required lanes.
The consumer labels advice historical if the candidate changes during inference.

### Required checks and freshness

The observed active ruleset requires **CI result** and **Hexagonal architecture**
from GitHub Actions (integration 15368). Preserve both names and the other rules:
PRs, resolved review threads, merge commits, deletion/force-push protection and
owner review policy. Strict up-to-date checking remains disabled. Evidence proves
the tested candidate and its observed base; the target can advance afterward.
Changing the merge freshness setting remains an explicit owner decision.
