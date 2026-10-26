# Xorchestrate

Xorchestrate coordinates agents, work, messages, capabilities, and evidence through
replaceable ports and adapters. Its application owns orchestration; configuration
selects storage, execution, routing, guardrails, and assessment implementations.

The current application is `xorchestrate/`. It has no required agent hierarchy,
project stack, model vendor, or evaluator. Project knowledge and explicitly
chosen standards arrive through configured MCP or CLI tools. Observed tool output
and model recommendations do not become policy on their own.

## Testing framework

Use Python 3.12 for validation. Before starting Python, select a fresh cache outside
the checkout:

```bash
export PYTHONPYCACHEPREFIX="$(mktemp -d "${TMPDIR:-/tmp}/xorchestrate-pycache.XXXXXX")"
python3 -m pip install -e '.[test]' -r .github/requirements-ci.txt
python3 -m scripts.testkit run --suite static
python3 -m scripts.testkit run --suite checks
```

`static` runs Ruff, current-source syntax validation and architecture enforcement;
`tests` runs the cataloged behavioral tests; `checks` combines both. Use
`python3 -m scripts.testkit run` for static checks, tests, calibration and live protocol workflows.
Local and CI runs use the same [testing framework](docs/testing-framework.md),
shared contracts, fixtures and evidence services. See [live tests](docs/live-testkit.md)
for authenticated native configuration.

## Run locally

Use Python 3.12 or newer. From the checkout:

```bash
python3 -m pip install -e .
python3 -m xorchestrate describe
python3 -m xorchestrate call create_work --json '{"goal":"Investigate a feature and retain the evidence"}'
python3 -m xorchestrate call list_work --json '{}'
```

Without a config file, state is retained in `.xorchestrate/state.sqlite3`; there
are no external providers, agent processes, or evaluators. `describe` lists the
application operations. `call` accepts JSON through `--json`, `--input FILE`, or
standard input. These same operations are exposed through the stdio MCP server:

```bash
python3 -m xorchestrate --config /absolute/path/team.json serve
```

For a persistent CLI connection, `run` accepts one JSON request per line and
returns a `result` or `error` envelope:

```bash
python3 -m xorchestrate --config /absolute/path/team.json run
```

For example, send `{"action":"list_work","arguments":{}}`. Keep `run` or the MCP
connection open while using native sessions. A one-shot `call` closes the adapters
it owns when it exits; a stored session record does not keep its process alive.

The CLI actor defaults to `owner`; `--actor` selects the identity bound to that
local invocation or MCP server process. The local process owner is trusted to
choose configuration and identity. The stdio server is not a network
authentication service.

## Connect capabilities and agents

A configuration selects adapters by name and passes their options explicitly:

```json
{
  "store": {"adapter": "sqlite", "options": {"path": "state.sqlite3"}},
  "providers": {
    "project": {
      "adapter": "mcp",
      "options": {"command": ["project-tools", "serve"]}
    }
  },
  "sessions": {
    "worker": {
      "adapter": "codex",
      "options": {
        "command": ["codex", "app-server", "--listen", "stdio://"],
        "pass_env": ["PATH", "HOME", "CODEX_HOME"],
        "application_tools": true
      }
    }
  }
}
```

`project-tools` represents an executable you supply. Provider capabilities are
discovered at runtime and addressed as `provider/name`; adding one does not require
editing the application. Session drivers communicate with configured agent
processes. MCP and CLI can supply project context, declared requirements, source
editing, checks, and other capabilities without teaching the core a particular
framework or stack.

`application_tools` exposes application operations as native dynamic tools for
this Codex driver. Calls retain the session's actor identity and pass through the
same application guardrails as CLI and MCP calls.

Configuration is trusted local code selection: adapters can launch executables or
load installed `module:factory` implementations. Use config files you control.
Runtime tool arguments cannot replace a configured executable. Filesystem,
network, and native tool access also depend on the chosen process environment and
harness; application guardrails are not an operating-system sandbox.

The native adapters include Codex, Claude Code, and Grok Build. Routed messages
have durable delivery attempts and independently observed callback evidence;
submission, receipt, reply, turn completion and work acceptance remain separate.
See [native sessions](docs/native-sessions.md), [external attachment and recovery](docs/external-session-bridge.md),
and [owner requirements and acceptance](docs/work-acceptance.md).

See [the architecture and contracts](docs/hexagonal-architecture.md),
[configuration examples](examples/), and [validation](.github/CI.md) for the
supported behavior and limits. The deterministic tests use real subprocess
boundaries with fixture servers; they do not establish authenticated vendor or
Jev endpoint acceptance.

## Live acceptance fixture

With tmux installed, run the reusable acceptance scenario from the checkout:

```bash
python3 -m scripts.acceptance --output .xorchestrate/acceptance
```

It uses a private tmux server, exercises real MCP and subprocess boundaries, and
retains a receipt plus evidence for each run. The default uses deterministic
protocol peers and needs no model account. A configured native profile verifies
a real harness's application-tool callback. See [live acceptance](docs/live-acceptance.md)
for both profiles, observation, and cleanup behavior.

The complete behavioral-preservation procedure and evidence are indexed in the
[implementation handoff](docs/implementation/HANDOFF.md). Authenticated native
acceptance is an explicit local run; protocol fixtures cannot supply that result.

## Source layout

| Location | Purpose |
| --- | --- |
| `xorchestrate/` | Current application, ports, adapters, composition, CLI, and MCP input |
| `tests/hexagonal/` | Declared behavioral families, independent contracts, support adapters and collected scenarios |
| `scripts/testkit/` | Shared test framework, collection, execution, lifecycle and evidence services |
| `scripts/acceptance/` | Reusable tmux acceptance runner, scenarios, and deterministic protocol peers |
| `devtools/` | Imported source retained for selective reuse; historical tests and CI removed |
| `reference/imported-agent-skills/` | Historical agent instructions, removed from active skill discovery |
| `legacy/agentscient/` | Earlier conductor, tooling, fixtures, and testkit source collection |
| `reference/agentscient-dev/` | Original repository guidance and CI machinery |

The imported runtime and historical documents describe their original behavior;
its roles, path ceilings, evaluator prerequisites, and delivery policies are not
requirements for the current application. There is no automatic migration of its
state or a claim of feature parity. Reuse is deliberate, with original license
notices and [extraction provenance](docs/extraction.md) retained.
