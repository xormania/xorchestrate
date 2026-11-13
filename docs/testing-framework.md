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

Python 3.12 is the validation baseline; package support starts at 3.12 without an
upper bound. Hosted compatibility evidence applies to the interpreter exercised.
From a checkout, establish an external bytecode cache before starting Python,
then install the locked application/test and validation dependencies with uv 0.13.7:

```bash
export PYTHONPYCACHEPREFIX="$(mktemp -d "${TMPDIR:-/tmp}/xorchestrate-pycache.XXXXXX")"
export UV_PROJECT_ENVIRONMENT="$(mktemp -d "${TMPDIR:-/tmp}/xorchestrate-env.XXXXXX")"
python3 -m scripts.ci.dependencies check
uv sync --locked --only-group validation --no-install-project --managed-python --python 3.12.16
uv sync --locked --no-default-groups --group validation --extra test --no-install-project --managed-python --python 3.12.16
uv pip install --python "$UV_PROJECT_ENVIRONMENT/bin/python" --no-deps --no-build-isolation -e .
export PATH="$UV_PROJECT_ENVIRONMENT/bin:$PATH"

# Focused security view: Ruff, candidate-complete secret scan and workflow analysis.
python3 -m scripts.testkit run --suite security

# Security checks, current-source syntax and deterministic architecture.
python3 -m scripts.testkit run --suite static

# Cataloged behavioral tests with inventory and JUnit.
python3 -m scripts.testkit run --suite tests

# Static checks plus all behavioral tests.
python3 -m scripts.testkit run --suite checks

# Static checks plus one declared behavioral family.
python3 -m scripts.testkit run --suite checks --family state

# One balanced shard (K of N, one-based) of the cataloged tests; combines with --family.
python3 -m scripts.testkit run --suite tests --shard 2/4

# Prove that deliberate native, work and state faults trigger the intended assertions.
python3 -m scripts.testkit run --suite calibration

# Composed live-process workflows with deterministic protocol peers.
python3 -m scripts.testkit run --suite multi-harness
python3 -m scripts.testkit run --suite acceptance

# Selectable, bounded scenarios in owned terminals of the product's terminal host.
python3 -m scripts.testkit stories
python3 -m scripts.testkit run --suite stories --story work-acceptance --timeout 60

# Every component suite; security checks run once through static.
python3 -m scripts.testkit run
```

The cache prefix must resolve outside the candidate and is inherited by child
Python commands. Use a fresh prefix for each validation session. Ruff and pytest
caches are disabled or written outside the candidate by the shared suites;
syntax validation reads current bytes rather than trusting cached bytecode.
Static-only execution needs Python, the pinned Ruff and the pinned Zizmor
executable, without pytest discovery, tmux, native configuration or model
credentials. Managed setup installs both validation tools for its selected
interpreter; `doctor` checks that environment's Zizmor executable without
launching the scanner. `--family` filters behavioral tests only; it never reduces
static coverage. The default composition runs each selected check once.
`security` is a focused view rather than another component: `static`, `checks`
and the default/all composition include its Ruff, secret-scan and Zizmor checks
once through `static`.

## Jev advisory stages

The early architecture job asks atomic questions about selected current source
and declared role contracts. `source_atomic` enables separate applicability,
evidence sufficiency, claim and exact evidence-ID support questions. Questions
sharing a packet run in one bounded batch. Current and prior source remain
explicitly labelled; deletion questions evaluate the removal under the current
contract. The support-question shortlist prioritizes focal source and receipts;
all supplied evidence remains available for applicability and claim judgments.
Reports distinguish shortlisted, scored and unscored evidence IDs. Source text is evidence, never an
instruction, and this stage has no executed test evidence. It cannot establish
behavioral coverage or test quality.

After CI completes, the independent Jev advisory workflow runs optional post-test
triage, including when a required lane failed. It authenticates the current
candidate, original context, GitHub job/artifact lineage and inventory receipts.
A failed job can supply an observation; it cannot qualify as a successful lane.
Foreign, missing or contradictory receipts are excluded and reported explicitly.
Provider setup and triage neither delay required CI completion nor change its
verdict. The consumer executes trusted collector code against the tested checkout
as data; see [independent advice](jev-ci-advice.md).

Post-test checks compare declared family guarantees with complete selected test
units and their bounded static bindings. Selection prioritizes failed modules,
then changed tests and tests whose static dependencies include changed source.
`failure_regression` additionally requires an assertion failure matched to the
exact executed inventory case. Missing failure text, missing concrete bindings,
unknown dynamic relationships and unrun tests remain insufficient evidence.
Static dependency selection is not a coverage claim. Unselected modules and
omitted source remain visible in the artifact.

`contracts/hexagonal-jev.json` configures `threshold`, `max_support_evidence` (12 by default), source checks and the
`post_test` checks, `max_subjects`, `max_cases_per_subject` (four by default)
and `max_source_bytes`. Post-test subjects prioritize actual failed cases, then
distinct test units; omitted case counts and identity digests remain explicit. The default checks are
`guarantee_gap` and `failure_regression`. Raw atomic distributions, applicability,
sufficiency, supplied evidence IDs and selected supporting IDs remain in
`triage.json` in the independent advice artifact, including exact selected source
text and spans.
The threshold is routing policy, not a calibrated defect probability. A concern
is an investigation candidate for human review, never a confirmed defect or an
additional acceptance gate. Reused behavioral runs are not described as fresh
execution and do not repeat post-test model work.

## Sharding the tests suite

`--shard K/N` (1 <= K <= N) splits the `tests` suite by test module. The plugin
first applies the catalog and any `--family`/`--test-layer` selection, then
partitions exactly those modules into N shards and runs shard K. Each module
belongs to exactly one shard and the union of shards 1..N is the unsharded
selection, so running every shard is equivalent to one unsharded run. Every shard
still discovers all test modules and validates them against the complete catalog
(undeclared, missing or multiply-owned modules fail every shard); the check that
each requested module was collected and has cases covers only that shard's own
modules. Its inventory records the shard index, count, assigned modules and
estimated seconds, and its JUnit/inventory are written to its own run directory.
A shard that would select no modules (more shards than selected modules) is an
error rather than an empty pass. `--shard` is accepted only with `--suite tests`
(not `checks` or `all`, which would rerun their other suites in every shard);
direct pytest takes `--test-shard K/N`.

Assignment is deterministic: longest-processing-time first, heaviest module on the
currently lightest shard, ties broken by module path and shard index. Local weights default
to the committed [`contracts/test-timings.json`](../contracts/test-timings.json)
(module path to seconds, including setup and teardown). A module without a recorded
duration, such as a newly added one, gets the file's `default_seconds` (the median
recorded duration) and is assigned like any other; entries for removed modules are
ignored. Timings only affect balance, never coverage.

CI uses eight shards and resolves one shared timing snapshot before they start.
The latest usable successful default-branch push supplies its published model;
unavailable, expired or invalid history falls back to the committed weights.
Every shard downloads the same artifact ID. `--timings PATH` accepts an external
model with `--suite tests --shard K/N`; execution freezes its exact bytes in the
run evidence and passes them to both pytest and independent qualification.
Inventories retain `timings_sha256`, and the aggregate verifies identical frozen
models across the complete shard set. The checkout is never rewritten.

After complete fresh shard evidence qualifies, CI publishes new per-module JUnit
durations for subsequent runs. Documentation skips, reused behavioral evidence,
failures and incomplete measurements preserve the inherited model. Publication
is advisory; only successful default-branch push runs can supply future models.
This shared-snapshot and skipped-run preservation pattern adapts
[UXor PR #133](https://github.com/xormania/uxor/pull/133), using Xorchestrate's
authenticated run/attempt and bounded artifact intake. Model selection and
publication receipts live in `ci-test-timing-plan--attempt-N` and
`ci-test-timings--attempt-N` artifacts. There are no automatic source commits.

Regenerate the timings when the balance drifts, from one unsharded run or from
every shard's JUnit (for example the CI shard artifacts):

```bash
python3 -m scripts.testkit run --suite tests --output ../runs
python3 -m scripts.testkit.timings ../runs/<run-id>/tests.xml
# or, with every shard's report: python3 -m scripts.testkit.timings shard-1/tests.xml shard-2/tests.xml ...
```

Reports that leave any cataloged module untimed (for example a single shard or a
family run) are refused and the file is left unchanged; `--allow-partial` writes
them anyway, and the missing modules then take the default weight. Review and
commit the rewritten `contracts/test-timings.json`.

Ruff and syntax validation select applicable Python files beneath `xorchestrate`,
`scripts`, `tests` and `.github`. Historical source trees remain outside this
scope. Ruff uses its configured/default exclusions while Git ignore rules do not
remove files from these active roots. Syntax traversal includes ignored Python
files. Architecture uses its explicit scope and role policy in
`contracts/hexagonal.json`. The retained manifests describe each check's actual
selection; an inventory of planned roots alone does not prove file coverage.

The secret scan applies the policy in `contracts/security.json` to every existing
Git-tracked file and every nonignored untracked file in the candidate. It reads
bytes rather than assuming text, so binary inputs remain in scope. In-repository
directory-link entries are identity-bound in the manifest while their target
files are scanned through their own candidate paths; broken or escaping links
are refused. A
`secret-scan: allow` marker suppresses an intentional match only on its own line.
The structured report binds every selected path to its captured byte hash and the
complete effective-input identity. Findings retain only path, line and rule;
matched credential bytes are excluded from the report and captured command
output.

The Zizmor check selects only immediate `.yml` and `.yaml` files beneath
`.github/workflows`; an empty selection, a broken link or a workflow that resolves
outside the candidate is refused. The pinned Zizmor 1.32.0 executable runs with
offline access, strict collection and the regular persona against the explicit
`contracts/zizmor.yml` policy. That policy records exact filename-and-line
exceptions for reviewed workflow behavior, so line movement can invalidate an
exception rather than silently widening it. The adapter holds tool-native output
only long enough to normalize it, then publishes candidate-bound file hashes and
path/location/rule/severity/confidence findings; workflow snippets and fixes are
not copied into the report or outer command output.

`--output /path/outside/checkout` selects a reusable evidence parent; each invocation
allocates a private run directory. `--check-timeout` bounds each deterministic or
calibration command. `--timeout` retains the live scenario timeout. Native harness
configuration, terminal handling and authentication are described in
[live-testkit.md](live-testkit.md). Native fixtures never establish authenticated
vendor acceptance.

Direct pytest remains supported for development and narrow calibration witnesses
after the same startup environment is established. Disable its local cache:

```bash
python3 -m pytest -p no:cacheprovider
python3 -m pytest -p no:cacheprovider --test-family state --test-inventory ../state-inventory.json
python3 -m pytest -p no:cacheprovider --test-shard 1/4 --test-inventory ../shard-1-inventory.json
python3 -m pytest -p no:cacheprovider tests/hexagonal/test_storage.py -q
```

Default discovery selects only active tests. The collection plugin validates the
complete module catalog even for a selected family, then records selected cases,
deselections and outcomes. Explicit subsets are reported as subsets. An empty
selection, missing declared module, duplicate ownership or unclassified module
is an error; none receives a successful empty result.

See [selectable local stories](local-stories.md) for the story catalog, bounded
controller requests, native callback selection and managed tmux automation.

## Ownership

| Concern | Canonical owner |
|---|---|
| Behavioral family, guarantee and test layer | [`contracts/testing.json`](../contracts/testing.json) |
| Static check definitions and suite membership | Shared test-kit composition and static adapter |
| Ruff policy and validation dependency pin | `ruff.toml`, `pyproject.toml` validation group; generated `.github/requirements-ci.txt` |
| Secret detection policy and same-line allowances | `contracts/security.json`; shared static and secret-scan adapters |
| Workflow-analysis policy and validation dependency pin | `contracts/zizmor.yml`, `pyproject.toml` validation group; generated `.github/requirements-ci.txt`; shared static and Zizmor adapters |
| Catalog validation and selection | `scripts/testkit/catalog.py` |
| Shard specification, balanced module partition and timings format | `scripts/testkit/sharding.py`, `contracts/test-timings.json` |
| Timings regeneration from JUnit | `scripts/testkit/adapters/timings.py`, `python3 -m scripts.testkit.timings` |
| Shared CI timing snapshot and automatic measurement refresh | `scripts/ci/timings.py`, `scripts/testkit/adapters/shard_timings.py` |
| Pytest discovery, markers and inventory | `scripts/testkit/adapters/pytest_plugin.py` |
| Command execution, named results, check receipts and resource ownership | `scripts/testkit/{model,ports,core}.py` and execution adapters |
| Independent expected behavior and reusable scenario inventories | `tests/hexagonal/contracts/` |
| Application construction, test doubles and storage selection | `tests/hexagonal/support/application.py`, `delivery.py`, `work_acceptance.py` |
| Native driver configuration, observation waits and controlled callbacks | `tests/hexagonal/support/native.py` |
| Raw observations and independent normalized evidence fixtures | `tests/hexagonal/support/raw_evidence.py`, `verification_evidence.py` and identity adapters |
| Shared memory/SQLite fixture lifetime | `tests/hexagonal/conftest.py` |
| Fixture projects (`project`, `worktree`): a git repository with tracked `.xorchestrate/`, a private `XORCHESTRATE_HOME`, a companion with the approval, the `ProjectContext` for `build()` and a CLI runner inside the project | `tests/hexagonal/conftest.py`, `tests/hexagonal/support/projects.py` |
| Install script sandbox with fake `uv`, `curl` and `sudo` programs | `tests/hexagonal/support/install_script.py` |
| Committed store per data version, its regeneration command and pre-versioned store construction | `tests/fixtures/stores/`, `tests/hexagonal/support/store_fixtures.py` |
| Snapshot, exact mutation, trusted assertion observer and captured calibration command | Shared services in `scripts/verification/__main__.py` using the kit's evidence/command adapters |
| Fault inventories, their registry and the stages each runs at; one service judging any inventory | `scripts/verification/inventories/` (data), `scripts/verification/fault_calibration.py` (service), `inventory_files.py` (loader) and `calibrate_suite.py` (command line) |
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

Crash calibration waits for the native fixture's applied-effect acknowledgment
before killing its host. An RPC timeout alone does not establish that boundary.
Failed scenarios also kill and reap their own blocked host if graceful termination
cannot finish. The unknown-effect and no-replay assertions are unchanged; the
[boundary regression receipt](ci-evidence/native-calibration-boundary.json)
records the delayed-fixture reproduction and retained fault detection.

Fault cases for calibration are data. An inventory file in
`scripts/verification/inventories/` holds cases only: one exact source edit, the
selected tests, the target test and its declared assertion message, and the tests
that must stay green. `registry.json` names every inventory and the stages it runs
at (`ci`, `pre-draft`, `manual`); `schema.json` defines both files, and an
unregistered file or an unknown name is an error. The calibration suite composes one
check per inventory registered at `ci` (`native-calibration`, `work-calibration`,
`state-calibration`), each running that inventory; to run others on demand:

```bash
python3 -m scripts.verification.calibrate_suite --candidate . --out <new directory outside the tree> --stage manual
python3 -m scripts.verification.calibrate_suite --candidate . --out <new directory outside the tree> --inventory native
```

With neither option, the inventories registered at `ci` run. A selection that names
no inventory (for example a stage with nothing registered) exits 2 with
`no_inventory_selected` before anything runs. A case passes when it
classifies as its expectation: `detected` by default, or `unrelated_failure` for a
negative control.

A calibrated check is a plain `assert condition, "ID"`. The ID is UPPER_SNAKE and
is the inventory case's diagnostic, and it is asserted at exactly one site in the
tests (a parametrized test is one site): in the case's target test, or in the one
contract function the case declares as `"contract": "module:function"`. Only an
assertion raised in that function while the target test runs earns credit for a
case that declares it; the suite command refuses a declared function the tree does
not hold (`contract_not_found`) or that does not assert the ID
(`diagnostic_not_in_contract`) before any case runs. To state a refusal guarantee,
assert `refusal(call, code)` from `tests/hexagonal/contracts/calibrated.py`, which
answers whether the call was refused with that code and never asserts itself. Other
assertions and `pytest.raises` stay as they are; they are not calibrated checks.

## Consolidation and completion evidence

The test-framework consolidation (PR #12) established the following outcomes:

| Outcome | Verification |
|---|---|
| Historical testing is outside the active product tree and CI | Removed historical suites/runner/PHP test infrastructure; default pytest roots and CI definitions select active code |
| Test families have explicit ownership and honest collection | Catalog/plugin tests plant missing, unclassified, overlapping and empty selections; CI retains inventory alongside JUnit |
| Shared setup replaces repeated implementation | Five reusable application construction sites share one builder; nine store selection paths share one selector; native observation/retry/held-callback mechanics share support implementations |
| Identity faults use shared contract scenarios | Nine callback-specific test bodies become two contract entrypoints, retaining action/phase/type/lifecycle variants; two exact duplicate mutations removed |
| Calibration uses shared execution and complete inputs | Native, state and work calibration consume common snapshot/observer/command services; all fault witnesses must detect their intended mutation |
| Framework architecture stays enforced | Generic dependency and forbidden-literal controls reject test-to-test imports, support-to-test imports, impure shared contracts and decoder-to-verdict dependencies |
| Existing live behavior remains observable | Full active suites, 46-case multi-harness run and 11-check tmux scenario retain their original behavioral assertions and cleanup receipts |

CI binds actual results and artifacts to the tested revision. A family
inventory explains what ran; JUnit and raw receipts establish its observed result.
Passing counts alone do not establish completeness.

The shared `tests` check qualifies the pytest inventory before reporting success.
Its catalog identity, module/family ownership, selected case identities, outcomes
and aggregate counts must agree with the requested selection and native exit.
Sharded runs use the same family and weighted-module selector as pytest; their
recorded shard identity and selected modules must match the requested shard.
Missing or inconsistent evidence makes a successful process an `evidence_error`;
the original process exit and command receipt remain available. A nonzero native
outcome remains failed even when its partial inventory also needs diagnosis.
At least one selected case must pass, and no selected case may be failed or
unexecuted in a successful run. Mixed passes and explicit skips retain separate
counts; a run containing only skips cannot establish behavioral verification.
This checks that execution evidence supports the reported result. Independent
assertions and deliberate fault witnesses still establish what the tests detect.

Static evidence records effective input membership and content, including examined
ignored/untracked files and followed symlink targets. This identity is distinct
from the repository commit/tree and the existing calibration candidate hash.
Source/configuration changes invalidate the affected result. Reports retain check
IDs, readiness/preparation outcomes, tool versions, structured findings and raw
command receipts. Secret-scan findings and command output are redacted rather than
retaining the matched bytes. Zizmor's source-bearing native output is normalized
inside its adapter and never reaches the outer command receipt. A check blocked
before launch has an explicit setup outcome and does not borrow another check's
command record.

Structural plan errors reject the whole plan. Missing tools, selected
configuration or check-specific preparation block only dependent checks;
independent checks continue and the aggregate remains unsuccessful. `doctor` uses
the same readiness definitions without launching a live harness; its opt-in
`--probe` runs the `native-handshake` story through the same executor. Native terminal
inheritance applies to live checks; static and pytest output stays captured.

