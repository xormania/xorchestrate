# Xorchestrate testing framework

The testing system is organized by current behavioral guarantees. Shared contracts
define expected behavior, support adapters supply subjects and controlled effects,
pytest collects and executes scenarios, and the test-kit owns command execution,
resource cleanup, snapshots and evidence. Local runs and CI use the same services.

Historical test suites and their CI jobs have been removed. The 443 removed files
include imported tests, the historical runner and unused PHP CI helper/tests.
Their original evidence remains in Git history and pinned source citations;
passing an old implementation is not a prerequisite for changing Xorchestrate.

## Run the framework

From a checkout with `python3 -m pip install -e '.[test]' -r .github/requirements-ci.txt`:

```bash
# Deterministic contracts, adapters, composition and framework checks.
python3 -m scripts.testkit run --suite checks

# A declared behavioral family, with the same collection and evidence rules.
python3 -m scripts.testkit run --suite checks --family state

# Prove that deliberate native, work and state faults trigger the intended assertions.
python3 -m scripts.testkit run --suite calibration

# Composed live-process workflows with deterministic protocol peers.
python3 -m scripts.testkit run --suite multi-harness
python3 -m scripts.testkit run --suite acceptance

# Everything above, using the same composed checks.
python3 -m scripts.testkit run
```

`--output /path/outside/checkout` selects a reusable evidence parent; each invocation
allocates a private run directory. `--check-timeout` bounds each deterministic or
calibration command. `--timeout` retains the live scenario timeout. Native harness
configuration, terminal handling and authentication are described in
[live-testkit.md](live-testkit.md). Native fixtures never establish authenticated
vendor acceptance.

Direct pytest remains supported for development and narrow calibration witnesses:

```bash
python3 -m pytest
python3 -m pytest --test-family state --test-inventory ../state-inventory.json
python3 -m pytest tests/hexagonal/test_storage.py -q
```

Default discovery selects only active tests. The collection plugin validates the
complete module catalog even for a selected family, then records selected cases,
deselections and outcomes. Explicit subsets are reported as subsets. An empty
selection, missing declared module, duplicate ownership or unclassified module
is an error; none receives a successful empty result.

## Ownership

| Concern | Canonical owner |
|---|---|
| Behavioral family, guarantee and test layer | [`contracts/testing.json`](../contracts/testing.json) |
| Catalog validation and selection | `scripts/testkit/catalog.py` |
| Pytest discovery, markers and inventory | `scripts/testkit/adapters/pytest_plugin.py` |
| Command execution, named results, check receipts and resource ownership | `scripts/testkit/{model,ports,core}.py` and execution adapters |
| Independent expected behavior and reusable scenario inventories | `tests/hexagonal/contracts/` |
| Application construction, test doubles and storage selection | `tests/hexagonal/support/application.py`, `delivery.py`, `work_acceptance.py` |
| Native driver configuration, observation waits and controlled callbacks | `tests/hexagonal/support/native.py` |
| Raw observations and independent normalized evidence fixtures | `tests/hexagonal/support/raw_evidence.py`, `verification_evidence.py` and identity adapters |
| Shared memory/SQLite fixture lifetime | `tests/hexagonal/conftest.py` |
| Snapshot, exact mutation, trusted assertion observer and captured calibration command | Shared services in `scripts/verification/__main__.py` using the kit's evidence/command adapters |
| Hex dependency and forbidden protocol-literal checks | One generic `scripts/architecture` engine and `contracts/hexagonal.json` |

Tests do not import other collected test modules as fixture libraries. Shared
scenario contracts cannot depend on product implementations, concrete fixtures or
the verdict being checked. Infrastructure belongs in support adapters. Both
restrictions are enforced by the same architecture checker used locally and in CI.
The former separate verification AST dependency checker has been removed; its
unique protocol-literal restriction is now a generic declarative role constraint.

## Adding or changing coverage

Start with the existing family, contract and support adapter. Express a new
variant as scenario data when its behavior is the same contract applied to a
different adapter, lifecycle operation, identity field or failure. A distinct
boundary may need a separate test: pure state decisions, process transport,
durable observations and composed callbacks are different evidence.

Add a new shared helper at its concern's owner. Do not create another application
builder, store selector, native driver launcher, polling loop or command recorder
inside a test module. Add a new family only when there is a distinct current
guarantee and record that guarantee in the catalog. Catalog wording does not make
historical behavior or a newly discovered issue an owner requirement.

Maintain calibration witnesses that attribute a real failure to the intended
assertion. Do not replace their independent oracle with a call to the production
implementation. Counts are descriptive evidence, not an optimization target or a
frozen acceptance threshold.

## Consolidation and completion evidence

This change has the following required outcomes:

| Outcome | Verification |
|---|---|
| Historical testing is outside the active product tree and CI | Removed historical suites/runner/PHP test infrastructure; default pytest roots and CI definitions select active code |
| Test families have explicit ownership and honest collection | Catalog/plugin tests plant missing, unclassified, overlapping and empty selections; CI retains inventory alongside JUnit |
| Shared setup replaces repeated implementation | Five reusable application construction sites share one builder; nine store selection paths share one selector; native observation/retry/held-callback mechanics share support implementations |
| Identity faults use shared contract scenarios | Nine callback-specific test bodies become two contract entrypoints, retaining action/phase/type/lifecycle variants; two exact duplicate mutations removed |
| Calibration uses shared execution and complete inputs | Native, state and work calibration consume common snapshot/observer/command services; all fault witnesses must detect their intended mutation |
| Framework architecture stays enforced | Generic dependency and forbidden-literal controls reject test-to-test imports, support-to-test imports, impure shared contracts and decoder-to-verdict dependencies |
| Existing live behavior remains observable | Full active suites, 46-case multi-harness run and 11-check tmux scenario retain their original behavioral assertions and cleanup receipts |

The PR binds actual results and CI artifacts to the tested revision. A family
inventory explains what ran; JUnit and raw receipts establish its observed result.
Passing counts alone do not establish completeness.
