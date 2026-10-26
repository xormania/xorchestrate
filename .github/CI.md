# Validation

Local and CI execution use the [testing framework](../docs/testing-framework.md).
Install the declared application/test dependencies and the single pinned linter
requirements file:

```bash
python3 -m pip install -e '.[test]' -r .github/requirements-ci.txt
python3 -m scripts.testkit run --suite checks
python3 -m scripts.testkit run --suite calibration
python3 -m scripts.testkit run --suite multi-harness
python3 -m scripts.testkit run --suite acceptance
```

`python3 -m scripts.testkit run` composes all four suites. `--family NAME` selects
one declared deterministic family. Each run gets fresh private evidence outside
the candidate. Direct pytest is available for a focused scenario; default
collection targets only active tests and validates `contracts/testing.json`.

## CI jobs

CI runs on GitHub-hosted Ubuntu. Pinned actions have read-only repository
permissions. Pull requests, pushes to master/integration and manual runs invoke:

| Job | Purpose |
|---|---|
| Static checks | Lint and compile active product/framework/test code |
| Application and adapter tests, Python 3.11 and 3.12 | Shared `checks` suite: declared behavioral families, pytest inventory/JUnit, deterministic architecture report |
| Python 3.12 follow-up steps | Shared `calibration` suite and composed three-harness protocol suite |
| Tmux live acceptance | Shared acceptance suite with tmux explicitly installed; private owned process cleanup is part of the receipt |
| Hexagonal architecture | Declared dependency enforcement and optional Jev diagnostics, with retained reports |

The former historical-runtime and recovered-toolkit jobs, PHP provisioning/tests
and historical runner have been removed. Historical test sources remain available
at their pinned source revisions and in Git history. CI does not preserve an old
implementation as a product acceptance obligation.

Security-lint calibration belongs to the catalog's architecture family and uses
the actual CI lint command/configuration. Architecture and tmux regression tests
run through the application matrices; their dedicated jobs no longer rerun the
same selected pytest modules.

## Evidence and limits

`framework-checks-python-*` artifacts retain family/case inventories, JUnit,
command records, deterministic architecture results and calibration reports for
30 days. Full calibration candidate copies are excluded from artifact uploads.
Composed protocol receipts remain available for seven days; tmux uploads selected
report/scenario receipts for seven days. Raw native configurations and terminal
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
