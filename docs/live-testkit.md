# Shared live test-kit

The [testing framework](testing-framework.md) owns deterministic tests, calibration
and live workflows. This page describes its live execution adapters.

The active acceptance and multi-harness runners use `scripts.testkit` for command
execution, resource ownership, check recording, configuration isolation and
private evidence. These are shared implementations used inside the runners, as
well as by the common entrypoint. The deterministic hex contract covers this
testing system alongside the product.

## Run

From the checkout, install Python 3.11+ dependencies with
`python3 -m pip install -e '.[test]' -r .github/requirements-ci.txt`. The tmux suite also needs tmux, Linux/WSL2,
PTYs, Unix sockets and a matching `/proc` process namespace.

```bash
python3 -m scripts.testkit doctor
python3 -m scripts.testkit run
```

The default runs deterministic checks and calibration followed by both live suites
with real application processes and deterministic protocol peers. It does not contact vendor models. Each invocation creates a
fresh private run under `../xorchestrate-runs/`, prints its directory and writes
`report.json`. `--output /path/outside/checkout` changes that reusable parent.
No manual copying of the owner brief, creation of criteria or selection of a new
run directory is needed. The brief is copied and hash-bound for every run.

Keep one private native application config, starting from
[`native-config.example.json`](../scripts/verification/native-config.example.json).
Set actual executable commands, isolated working directories, model options and
explicit environment forwarding. The three-harness suite uses session names
`codex`, `claude`, `grok`; `--driver` selects the smaller callback suite's driver.

```bash
python3 -m scripts.testkit doctor --mode native --config /path/native.json --driver codex
python3 -m scripts.testkit run --mode native --config /path/native.json --driver codex
```

`doctor` validates selected local inputs without launching or authenticating a
harness. Only a successful live run establishes authenticated callback behavior.
Native launch/authentication/timeout failures remain failures; there is no fixture
fallback. Native suite commands inherit the operator's terminal; outer command
records mark `terminal: inherit` and do not claim captured terminal output.
Claude still requires the terminal/channel prerequisites described in
[behavioral verification](behavioral-verification.md).

Use `--suite acceptance` for tmux/MCP callbacks and persistence, or
`--suite multi-harness` for the existing 46-case communication, controlled work
and lifecycle suite. `--timeout` controls each scenario's existing timeout; the
outer command bound allows the complete suite to finish. `--brief` selects an
alternative owner brief. The older direct module commands remain supported.

CI invokes this same entrypoint with explicit suite selection. Its deterministic
protocol runs establish application/adapter behavior, not vendor authentication.

## Concern ownership and hex boundaries

| Concern | One active owner | Consumers |
|---|---|---|
| Check plan, whole-plan preflight, named results | `testkit.core.execute_checks`, model/ports | Common local/CI entrypoint |
| Scenario receipt bookkeeping | `testkit.core.CheckRecorder` with injected clock | Acceptance and multi-harness scenarios |
| Cleanup order and retained failures | `testkit.core.ResourceScope` | Acceptance clients, multi-harness runtime/provider, external bridge hosts |
| Bounded command processes | `testkit.adapters.command.SubprocessRunner` | Check execution, captured checks and version probes |
| Atomic private JSON, exact command bytes, candidate identities | `testkit.adapters.evidence` | Both runners and verification/calibration capture |
| Derived config and isolated SQLite | `testkit.adapters.configuration` | Both runners, fixture and native modes |
| Owned tmux/PID lifecycle | `testkit.adapters.tmux` | Acceptance transport; old import delegates to this module |
| Product assertions and independent verdicts | Existing acceptance scenarios and `verification` checker | Shared mechanics supply observations; product verdicts are not the oracle |

The kit core imports only its model/ports and declared pure standard-library
modules. It cannot import the filesystem, subprocess, Xorchestrate or concrete
kit adapters. Product configuration belongs to an explicit integration adapter;
product construction stays in composition/runner adapters. Native decoding cannot
depend on the checker verdict. New modules must receive an explicit role in
[`contracts/hexagonal.json`](../contracts/hexagonal.json).

```bash
python3 -m scripts.architecture --output ../architecture.json
python3 -m pytest tests/hexagonal/test_testkit_*.py tests/hexagonal/test_acceptance_testkit.py -q
```

The existing Jev diagnostic command uses the expanded deterministic report too;
diagnostics do not override deterministic failures. See
[architecture enforcement](architecture-enforcement.md).

## Reused work and adaptations

The imported source is pinned in [`extraction-manifest.json`](../extraction-manifest.json).
This integration adapts the existing Agentscient mechanisms into active ports and
adapters:

- [Command/result and bounded process-session cleanup](https://github.com/uscient/agentscient/blob/456457b9a0a9eb1ff285ee2d3e40535b6f722cdb/ops/testkit/command.py)
  supply the command adapter and result model. Exact raw byte evidence is retained
  in addition to convenient decoded text.
- [Check definitions and full-plan validation before spawn](https://github.com/uscient/agentscient/blob/456457b9a0a9eb1ff285ee2d3e40535b6f722cdb/ops/testkit/checks.py)
  supply the injected executor. Filesystem checks and command effects moved behind
  ports; the default live plan runs serially to keep terminal ownership clear.
- [World resource registration and reverse cleanup](https://github.com/uscient/agentscient/blob/456457b9a0a9eb1ff285ee2d3e40535b6f722cdb/ops/testkit/world.py)
  supply the shared ownership model. Cleanup drains every registered resource and
  retains failures, including explicit releases that failed earlier.
- [Existing Xorchestrate ownership supervisor](https://github.com/xormania/xorchestrate/blob/b9a42685b229c88ff9e23c7f708cf7b49a1dc096/scripts/acceptance/lifecycle.py)
  retains PID/start identity checks, pidfd signaling and orphan adoption. It moved
  intact behind the kit adapter instead of reverting to older process scans.
- [Existing candidate/evidence capture](https://github.com/xormania/xorchestrate/blob/b9a42685b229c88ff9e23c7f708cf7b49a1dc096/scripts/verification/capture.py)
  retains dirty/untracked input identity and exact command output. The old module
  reexports the shared implementation. Checker identity now includes kit sources.

Historical fixture-only HOME isolation cannot be imposed on authenticated vendor
clients. Fixture and native environment policies remain explicit. No conductor,
dispatch authority, role hierarchy or historical refusal wording is introduced.

## Evidence scope

Each run has fresh application state, IDs, nonces and evidence. The configured
harness working directory is intentionally the operator's selected workspace;
the kit does not claim to create a fresh source worktree. The multi-harness workload
remains controlled build-log analysis with concurrent communication, not a coding
assignment. Existing focused native schedules in the behavioral verification
document remain distinct from the 46-case suite; this extraction does not claim
new vendor coverage or replace those assertions.

Raw command output, derived configuration and SQLite files are private artifacts.
Share selected receipts after review, rather than publishing raw native logs.
Run directories and evidence files use private modes; original config files are
never rewritten.
