# Reusable tmux live acceptance

Use the [shared live test-kit](live-testkit.md) for repeatable local/CI runs.
The direct commands below remain supported.

The acceptance runner starts a real MCP client inside a private tmux pane. That
client launches the current `xorchestrate` application and checks observable
results through its public tools. Tmux provides an attachable terminal and an
owned process lifetime; application success comes from the scenario assertions.

The runner lives under `scripts/acceptance/`. It does not add a tmux dependency to
the application or require tmux for ordinary CLI/MCP use.

## Run the fixture

Use the repository checkout with Python 3.11 or newer and tmux installed. The
supervisor verifies owned process identities through Linux `/proc`; Linux and
WSL2 are the supported hosts for this fixture. The host must permit PTYs and
Unix-domain sockets; a restricted container can have tmux installed and still
reject those facilities.

```bash
python3 -m pip install -e '.[test]'
python3 -m scripts.acceptance --output .xorchestrate/acceptance --timeout 120
```

`--output` is a parent directory. Every invocation creates a unique private run
directory, so repeated runs retain separate evidence. The runner prints the
directory and an attach command while the session is alive. Use that command in
another terminal to observe progress. The server is removed after the run;
`pane.txt` retains the captured terminal output.

The default `fixture` profile uses deterministic protocol peers. It exercises
real tmux, public MCP calls, child processes, session callbacks, and SQLite
persistence. It needs no model subscription or service credentials. Its result
does not establish authenticated model execution or live Jev acceptance.

## Run a configured native harness

Use an explicit trusted application configuration and select its session driver:

```bash
python3 -m scripts.acceptance \
  --profile native \
  --config /absolute/path/acceptance.json \
  --driver worker \
  --timeout 180 \
  --output .xorchestrate/acceptance
```

The selected driver must provide the application's tools to the harness. The
supplied Codex adapter does this with `options.application_tools: true`; other
compatible drivers can implement the same callback behavior. The scenario asks
the harness to call `record_result` with a fresh nonce. It checks the resulting
stored evidence and delegated actor identity, then verifies persistence after an
application restart. A launched process, a thread ID, or matching model text
alone cannot pass this scenario.

Choose the executable, model, workspace, native permissions, and credential
forwarding in that configuration. Native mode inherits the operator environment
for application startup; each subprocess adapter's `pass_env`, `env`, or
`inherit_env` options still decide what reaches its harness. The runner does not
log environment values or disable configured guardrails. Unavailable binaries,
authentication failures, denied operations, missing callbacks, and timeouts fail
the run; native mode never falls back to a deterministic peer.

Use an acceptance configuration and a disposable workspace. The worker leaves
the source configuration unchanged and writes a private derived copy with an
isolated SQLite store for this run. Built-in provider/session working directories
retain their meaning relative to the source configuration. Other configured
adapters and guardrails keep their supplied behavior.

The native result establishes the observed callback behavior of the configured
driver. The operator supplies the harness and account; the receipt is not an
independent certification of vendor identity or access to a named model. Jev or
other remote capability acceptance needs a scenario that exercises that service.

## Optional xorder hosts

[xorder](https://github.com/xormania/xorder) supplies two accepted Linux/amd64
images relevant to these runs. Its generated image pages carry the measured
inventories and exact identities:

| Environment | Available tooling | Use here |
| --- | --- | --- |
| [python-matrix/trixie 1.0.1](https://github.com/xormania/xorder/blob/master/docs/images/python-matrix-trixie.md) | Python 3.12/3.14, SQLite, tmux 3.5a | Credential-free protocol fixture |
| [harness-live/trixie 1.0.0](https://github.com/xormania/xorder/blob/master/docs/images/harness-live-trixie.md) | Python 3.14, tmux 3.5a, managed vendor CLI installation | Configured native-harness run |

With a working Docker engine, registry access, and an xorder checkout, its
[public launcher](https://github.com/xormania/xorder/blob/master/scripts/run-image.sh)
mounts the chosen workspace at `/workspace`. This runs our fixture using the
accepted Python matrix digest and an interpreter compatible with Xorchestrate:

```bash
WORKSPACE=/absolute/path/to/xorchestrate CACHE_VOLUME=xorchestrate-fixture-home \
  bash /absolute/path/to/xorder/scripts/run-image.sh \
  ghcr.io/xormania/python-matrix@sha256:dc0da00ee10740bb20406e4a0544ed4672e6ed0fc82169c34a246ccd20f93662 \
  bash -eu -c '
    python3.12 -m venv /tmp/xorchestrate-acceptance
    /tmp/xorchestrate-acceptance/bin/python -m pip install -e ".[test]"
    /tmp/xorchestrate-acceptance/bin/python -m scripts.acceptance --output .xorchestrate/acceptance --timeout 120
  '
```

For a native run, start the accepted harness image with a dedicated private HOME
volume. This example selects Codex installation; the harness image's
[usage guide](https://github.com/xormania/xorder/blob/master/docs/harness-live.md)
describes other selections and operator authentication:

```bash
WORKSPACE=/absolute/path/to/xorchestrate CACHE_VOLUME=xorchestrate-private-harness-home HARNESS_NAMES=codex \
  bash /absolute/path/to/xorder/scripts/run-image.sh --interactive \
  ghcr.io/xormania/harness-live@sha256:247e7bd01adcfc0d6ee321906ada6ac182b72905a5deee3b20b952dd5af746d5 bash
```

Inside the container, prepare a project virtual environment and run the native
command above with container-visible configuration and workspace paths. Retain
the image digest and `harnesses status` output with private run evidence. Vendor
CLIs are installed on initialization, not bundled in image layers; their network
and authentication prerequisites remain explicit. The image's existing readiness
evidence covers xordination's launcher. Xorchestrate acceptance on these exact
images still needs to be run; changing images does not itself verify our fixture.

## Read the result

The command exits successfully only when the scenario passes, its worker exits
successfully, and owned-process cleanup is verified. Missing tmux is a failure,
not a successful skip. The top-level `report.json` is the run result;
`scenario.json` records the scenario's named checks. A scenario receipt alone
does not establish that teardown succeeded.

Raw pane output, the derived configuration, and SQLite state remain inside the
private run directory. Native output and configuration can contain sensitive
information. Share the summary receipts when reporting results; do not upload
the entire native evidence directory. CI uploads only the two JSON receipts from
the deterministic fixture.

Each run uses its own tmux socket and bypasses personal tmux configuration.
Cleanup first asks the worker to close its application connections, then checks
the owned process tree using PID and process-start identity. Timeout and signal
paths use bounded cleanup and do not target unrelated tmux sessions or processes.
Run the fixture through its standalone CLI supervisor. Its process tracking assumes
that supervisor launches only the fixture's children; embedding it in a process
that also launches unrelated children is not supported. Process snapshots cannot
attribute a descendant that forks and becomes orphaned entirely between observations.
The foreground tmux server is waited on so that completion does not leave an
unreaped server behind. If an otherwise successful worker leaves children that
require forced termination, acceptance fails with `owned_process_leak`; the
cleanup receipt still records whether those processes were successfully removed.

## Keep using it

The separate `Tmux live acceptance (protocol fixture)` CI job runs on pull
requests and integration/master pushes. It provisions tmux, runs lifecycle
regressions and the same command above, and retains the receipts after success
or failure. The existing fast application tests continue to cover smaller
boundaries; the live fixture checks that the composed processes work together.

Add a scenario assertion when a new capability changes behavior across these
boundaries. Keep checks tied to observable outcomes and distinguish protocol
peers from configured external services in the receipt. Models, agent roles,
and project-specific policies remain configuration choices.
