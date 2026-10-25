# Contributing

Work from `dev`, use an owned branch, and submit a PR into `dev`. The owner controls
releases to `master`. Preserve unrelated work and explain the problem, changed
behavior, verification and material limits in the PR.

## Choose the component

| Change | Location |
| --- | --- |
| Symfony product behavior | `src/`, `config/`, `packages/`, application tests |
| Local agent tools, live coordination or operator support | `devtools/` and its tests |
| Usage and setup documentation | `README.md`, `docs/`, `devtools/README.md`, `devtools/OPERATIONS.md` |
| CI documentation | `.github/CI.md` |

The dev-lane core must remain usable without PHP or the application's vendor
packages. Its framework tools intentionally call the installed Symfony application.
Managed local agents use MCP; maintainers developing these services use the tools
authorized for their task.

Use existing PHP/Symfony capabilities where they fit: attributes, autowiring,
autoconfiguration, installed basic Makers and Mate diagnostics. Inspect the
installed implementation before creating a wrapper or custom generator. The
[agent guidance](AGENTS.md) and [Mate instructions](mate/INSTRUCTIONS.md) describe
the existing integration.

## Verify and document

Run checks relevant to the change; [CI](.github/CI.md) documents the service,
Symfony and Serena jobs. A documentation-only change needs checked links, accurate
examples and consistency with the implemented tools. Check that GitHub displays
the root project README on the repository homepage.

Put setup in the [readiness guide](docs/local-agent-readiness.md), normal agent
usage in the [role/tool reference](docs/control-plane-roles-and-tools.md), and
backing-service details in the [operator reference](devtools/OPERATIONS.md).
Keep these guides focused on the current code and its supported operations.

Preserve source/license attribution for reused code. Use `Source:` and
`Co-Authored-By:` trailers where applicable. The Mate block in `AGENTS.md` is
generated; update its source/integration when needed rather than hand-editing it.
