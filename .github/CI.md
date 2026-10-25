# Validation

CI runs on GitHub-hosted Ubuntu; application tests use Python 3.11 and 3.12. It
needs no repository secrets or authenticated model accounts for deterministic checks.
Jev architecture diagnostics use a configured repository secret when available and
report unavailable credentials explicitly. Pinned action revisions use read-only repository
permissions. Pull requests and pushes to `master` or `integration/hexagonal` run
the checks.

## Current application

```bash
python3 -m pip install -e '.[test]' -r .github/requirements-ci.txt
python3 -m ruff check xorchestrate tests devtools .github scripts
python3 .github/lint_checks.py
python3 -m compileall -q xorchestrate tests/hexagonal scripts/acceptance
python3 -m pytest tests/hexagonal -q
```

Application tests cover orchestration through injected ports. Adapter tests
exercise persistence, process protocols, discovery, validation, and failures with
local fixtures. The declared architecture profile exhaustively classifies active modules and checks
direct/transitive dependencies, external imports and constrained dynamic loading. They do not
freeze a role hierarchy, a model choice, or exact documentation language.

These checks establish the behavior they exercise. Authenticated native harnesses,
remote MCP providers, and a live Jev endpoint require separate acceptance runs
with the intended configuration. Fixture success is not evidence that an account
has access to a particular model or service.

## Tmux live acceptance

The `Tmux live acceptance (protocol fixture)` job installs tmux and runs the
reusable acceptance scenario in an owned terminal:

```bash
python3 -m scripts.acceptance --output .xorchestrate/acceptance --timeout 120
```

This job exercises the public MCP interface across actual process boundaries,
SQLite persistence, configured capabilities, and session callbacks. It also
checks termination of the fixture's owned processes and tmux server. Missing
tmux, scenario failures, timeouts, or failed cleanup make the run fail. There is
no successful skip for an unavailable dependency.

Each run retains a unique evidence directory. CI uploads its `report.json` and
`scenario.json` receipts as `tmux-acceptance-evidence` after success or failure and
retains them for seven days. The job also runs the acceptance scenario, lifecycle,
and cleanup regressions with tmux installed. Raw configuration, state, and pane
capture stay outside the upload. The native profile is an explicit local/operator run and is
not automatically uploaded by this job.
See [live acceptance](../docs/live-acceptance.md) for observation, receipt
interpretation, and the configured native profile.

## Imported source regression

A separately named job checks the historical `devtools` implementation. That job
stages `reference/imported-agent-skills` back into `.agents/skills` in its own
checkout because the imported source expects those paths. The new application
does not load these assets, and they are not installed at the repository root.

The imported job installs CUE v0.17.1 and tmux, validates its original catalogs,
and runs its test census in strict mode. Strict mode fails on skips, unexecuted
tests, expected failures, or failures. These checks preserve useful source evidence;
they do not make historical behavior a requirement for the new application.

The earlier toolkit has a separate `scripts/check_legacy.py` job. Only its selected
suites run; the entire historical tree is not an active acceptance gate. Synthetic
application fixtures may deliberately contain faults.

## Optional infrastructure

The retained PHP helper tests run separately from the application tests. The helper
manifest and provisioning procedures retain assumptions about the original
product. Serena/Docker, Symfony/Maker/Mate, model subscriptions, GitHub mutation,
and original host acceptance need separately provisioned environments. Original CI
is kept under `reference/agentscient-dev/.github/` as source material.

CI does not request model reviews or merge branches. Review and merge decisions
follow the authorization for the current effort; the workflow does not impose
that delivery policy on projects coordinated by Xorchestrate.

## Complete composed communication and lifecycle fixture

The Python 3.12 application job additionally runs `scripts.verification.multi_harness`
in `protocol-fixture` mode. It exercises all three native protocol adapters,
routed durable delivery, independent callback observations, six directed exchanges,
three held-tool submissions, six unfinished-work exchanges, nine work batches and
separate lifecycle cases. Its 46-case requested inventory is copied outside the
candidate for each CI measurement. A fresh independent review must still select
and inspect its own owner basis/checker; CI copying a file is not independent
approval of its content. Deterministic evidence is uploaded for seven days.
Authenticated native mode is never launched by CI.

## Hexagonal architecture

The `Hexagonal architecture` job runs the generic deterministic checker, calibrated
healthy/fault controls, and existing port/composition contracts. It fails on
violations or incomplete/invalid structural inputs. Jev runs afterward for
diagnostics, including when deterministic enforcement fails. The
`architecture-evidence` artifact retains both reports and contract-test JUnit.
See [architecture enforcement](../docs/architecture-enforcement.md) for local
commands, profile extension, Jev configuration and evidence limits.
