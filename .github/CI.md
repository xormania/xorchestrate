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

CI runs on GitHub-hosted Ubuntu. Pinned actions have read-only repository
permissions. Pull requests, pushes to master and manual runs invoke:

| Job | Purpose |
|---|---|
| Classify changed paths | `scripts/ci/classify_changes.py` decides whether the change touches anything other than documentation; see below |
| Static checks | Shared `static` suite: Ruff lint/security, current-source syntax and deterministic architecture enforcement |
| Application tests shard K/N (Python 3.12) | Matrix of N jobs, each running `--suite tests --shard K/N` with its own pytest inventory/JUnit |
| Application and adapter tests (Python 3.12) | Retains the existing status name; needs every tests shard and fails unless the shard matrix succeeded. It does not rerun validation |
| Calibration and multi-harness verification | Shared `calibration` suite and composed three-harness `multi-harness` protocol suite, in parallel with the tests shards |
| Tmux live acceptance (protocol fixture) | Shared acceptance suite with tmux required (installed through apt only when the runner image lacks it; the step prints `tmux -V`); private owned process cleanup is part of the receipt |
| Jev architecture diagnostics | Independent diagnostics-only invocation with retained advisory output |
| Hexagonal architecture | Retains the existing required status name and reports the shared static job's outcome; it does not rerun validation |

Each Python job configures `PYTHONPYCACHEPREFIX` in the runner's temporary directory before
invoking Python. Each pinned `actions/setup-python` step enables its `cache: pip`
download/wheel cache (`~/.cache/pip`), keyed on the files that declare that job's
installs: static on `.github/requirements-ci.txt`, tests shards on `pyproject.toml`
and `.github/requirements-ci.txt`, verification, tmux and Jev on `pyproject.toml`. Each tests shard
job's uv-created 3.12.16 venv pip uses the same cache directory. The cache only
avoids downloads/wheel builds; pip still resolves the declared pins and ranges on
every run. A cache entry is written once per key and refreshed only when those
files change, so packages newly released within a declared range are downloaded
on each run until then. uv's own cache stays disabled because uv only provisions
the interpreter.
Static checks run once in their own job. The Jev diagnostic
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

Only pull requests can skip validation. Pushes (including every push to
`master`), manual runs and every other event always run the full pipeline. The
concurrency group cancels an in-progress run when a newer push arrives on the
same ref. If a docs-only push could skip, a docs-only merge could cancel the
full run of a code merge just before it. The integrated `master` tree would then
never be fully validated, because the ruleset does not require branches to be up
to date.

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
   `xorchestrate/**`, `examples/*.json`, `pyproject.toml` and `ruff.toml`. A
   missing or null base, a failed or malformed diff and an empty change set all
   count as code.

The job output is
`steps.guard.outputs.force == 'false' && steps.classify.outputs.code || 'true'`.
It is `false` only when the guard ran and allowed classification and the
classifier reported `false`. The tests shards, the application aggregate, the
verification, tmux acceptance and Jev diagnostics jobs declare `needs: paths` and
the job-level condition `!cancelled() && needs.paths.outputs.code != 'false'`
(the application aggregate also needs the shard matrix). If either step fails or
emits nothing, they still run. On a documentation-only pull request the shards
and the aggregate are skipped together; otherwise the aggregate runs even after a
shard failure and fails unless the whole matrix succeeded. Static checks and the `Hexagonal architecture` status
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
the shared Ruff definition with independent expected findings; it runs in the
tests shard that owns its module, where the pinned Ruff dependency is installed.
The verification job installs `.[test]` and does not need the telemetry extra. Architecture and tmux
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

CI neither requests reviews nor merges branches. xor controls those decisions.

## Telemetry runtime

Application CI installs the optional pinned CCE collector through `.[test,telemetry]`.
It selects uv 0.13.7 / Python 3.12.16 for the collector's fixed SQLite WAL requirement.
Static checks remain independent of this extra. The existing application test-kit
collects the declared telemetry families alongside the current behavior suites.
Optional collector/exporter failure does not replace the application's required
effect, receipt or acceptance recording. See [the active guide](../docs/telemetry.md).
