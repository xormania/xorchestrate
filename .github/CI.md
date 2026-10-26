# Validation

Local and CI execution use the [testing framework](../docs/testing-framework.md).
Python 3.12 is the required validation baseline. Establish a fresh bytecode cache
outside the checkout before starting Python, then install the declared
application/test dependencies and the single pinned linter requirements file:

```bash
export PYTHONPYCACHEPREFIX="$(mktemp -d "${TMPDIR:-/tmp}/xorchestrate-pycache.XXXXXX")"
python3 -m pip install -e '.[test]' -r .github/requirements-ci.txt
python3 -m scripts.testkit run --suite static
python3 -m scripts.testkit run --suite tests
python3 -m scripts.testkit run --suite checks
python3 -m scripts.testkit run --suite calibration
python3 -m scripts.testkit run --suite multi-harness
python3 -m scripts.testkit run --suite acceptance
```

`checks` combines `static` and `tests`. The default `python3 -m scripts.testkit run`
composes static checks, tests, calibration and both live suites once.
`--family NAME` selects behavioral families without reducing static coverage.
Each run gets fresh private evidence outside
the candidate. Direct pytest is available for a focused scenario; default
collection targets only active tests and validates `contracts/testing.json`.

## CI jobs

CI runs on GitHub-hosted Ubuntu. Pinned actions have read-only repository
permissions. Pull requests, pushes to master/integration and manual runs invoke:

| Job | Purpose |
|---|---|
| Static checks, Python 3.12 | Shared `static` suite: Ruff lint/security, current-source syntax and deterministic architecture enforcement |
| Application and adapter tests, Python 3.12 | Shared `tests` suite with declared behavioral families and pytest inventory/JUnit, then shared calibration and composed three-harness protocol suites |
| Tmux live acceptance | Shared acceptance suite with tmux explicitly installed; private owned process cleanup is part of the receipt |
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
