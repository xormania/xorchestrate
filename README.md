# Xorchestrate

A foundation for configurable agent coordination, extracted from Agentscient.
The current runtime, its tests, and the useful earlier tooling are preserved here
so development can build on working code.

| Location | What is here |
| --- | --- |
| `devtools/` | Working Python runtime: MCP, native sessions, message delivery, role packages, work tracking, Git workspaces, telemetry, and provider adapters |
| `.agents/skills/` | Role and framework assets required by the runtime |
| `legacy/agentscient/` | Earlier conductor, routing/courier, session adapters, dispatch, workflow, telemetry, contracts, fixtures, and testkit; an independent source collection |
| `reference/agentscient-dev/` | Original repository guidance and CI machinery, retained for reuse |

The active runtime currently uses the `devtools` Python namespace and
`agentscient_dev` MCP server name. These identifiers, state formats, and policy
defaults are retained for compatibility. The earlier toolkit is not imported by
the active runtime or installed as root-level hooks.

## Try the core locally

Use Python 3.12 on Linux or WSL2 for the tested development environment. The core
uses Python's standard library. From the checkout:

```bash
python3 -m devtools.run --help
python3 -m devtools.consistency

# Private, disposable state outside the source tree; no vendor session is started.
xor_state=$(mktemp -d /tmp/xorchestrate.XXXXXX)
python3 -m devtools.run init --state "$xor_state" --workspace "$PWD" \
  --member operator:external --member collaborator:external --co operator
python3 -m devtools.run status --state "$xor_state"
python3 -m devtools.run connection --state "$xor_state"
```

`connection` describes the MCP connections. Attach a named member using
`python3 -m devtools.run mcp --state /absolute/state/path --member MEMBER`.
The `--operator` connection provides setup authority; use a normal member
connection for managed work. State contains private runtime information and
belongs outside Git.

Managed native sessions additionally require tmux, Linux process/socket support,
and an installed, authenticated harness. Codex, Claude, and Grok adapters are
included. Serena source editing uses Docker; Symfony tools require a separately
configured PHP application. Jev evaluation is optional. None is needed for the
core example above.

## Develop and extend

- [Extraction map and retained assumptions](docs/extraction.md)
- [Validation and CI](.github/CI.md)
- [Runtime code map](devtools/README.md)
- [Role packages](docs/role-capability-packages.md) and [runtime configuration](docs/shared-runtime-config.md)
- [Operator reference](devtools/OPERATIONS.md) — inherited Agentscient procedures; see the extraction map before using its provisioning helpers

Harness and role adapters already have registries. Storage remains SQLite, and
the current role hierarchy and delivery decisions still contain policy in code.
General storage, routing, and organization extension points are future work;
this extraction does not claim those abstractions already exist.

The source Symfony product, live working records, credentials, and host state are
not included. Source revisions and original file hashes are recorded in
`extraction-manifest.json`; existing license notices are retained in place.
