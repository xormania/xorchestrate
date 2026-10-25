# Agentscient

Agentscient combines a **Symfony application foundation** with an **MCP-based
development environment** for teams of agents.

**Development targets [`dev`](https://github.com/uscient/agentscient/tree/dev);
`master` is the release branch.** Use `dev` when contributing or setting up a team
to work on the product.

## What is here

| Part | Purpose | Current state |
| --- | --- | --- |
| **Prod-lane — Symfony application** | The Agentscient product under development. | Symfony application skeleton with MCP Bundle, basic Maker Bundle, AI Mate, and PHPUnit. |
| **Dev-lane — local development support** | Give persistent agent sessions the tools and context to develop the product. | Role-scoped MCP, live coordination, work orders/jobs, artifacts, telemetry, managed Git/GitHub delivery, framework tools and separate Serena v2 REPL. |

The control plane in `devtools/` runs independently of PHP. Its Symfony development
capabilities use the installed application packages when requested.

## Start here

Follow the [local setup and readiness guide](docs/local-agent-readiness.md) to
configure the project, select your installed harnesses, build Serena, and connect
the user-facing CO session.

From a new checkout:

```sh
git clone --branch dev https://github.com/uscient/agentscient.git
cd agentscient
```

You work with **CO**, which carries your intent to **XO**. XO organizes, monitors
and steers live collaborators. Each collaborator receives its role, relevant
brief and tools. Services handle work records, worktrees, branches, PRs and evidence.

Managed local agents use MCP without a general shell tool. The control-plane MCP
handles coordination and development operations; Serena is a separate MCP provider
for source navigation and editing. Symfony's installed Makers generate code, and
Mate supplies application diagnostics.

## Documentation

| I want to… | Read |
| --- | --- |
| Set up a local team and know what “ready” means | [Local setup and readiness](docs/local-agent-readiness.md) |
| Understand roles, context and the available tools | [Control-plane roles and tools](docs/control-plane-roles-and-tools.md) |
| Understand the development services | [Dev-lane overview](devtools/README.md) |
| Configure, inspect or repair a local installation | [Operator reference](devtools/OPERATIONS.md) |
| Contribute changes or verify them | [Contributing](CONTRIB.md) and [CI](.github/CI.md) |

## Verification

CI exercises the control plane and native protocol adapters with fixtures, real
local Git repositories, and HTTP provider fixtures. It also runs the installed
Symfony Makers/Mate and the pinned Serena container. See the [CI guide](.github/CI.md)
for the checks and their scope.

An authenticated run with the chosen local harnesses is a separate acceptance
step. The [readiness guide](docs/local-agent-readiness.md#authenticated-local-acceptance)
sets out the evidence to collect; passing CI or preflight alone does not establish it.

## Repository layout

| Path | Contents |
| --- | --- |
| `src/`, `config/`, `public/`, `bin/` | Symfony application and entry points |
| `packages/` | Space for reusable application packages |
| `tests/`, `phpunit.dist.xml` | Application tests |
| `mate/` | Installed Mate integration and diagnostic instructions |
| `devtools/` | Independent local development services and their tests |
| `docs/` | Setup, roles and tool usage |
| `.github/` | CI and repository automation |

PHP/Symfony requirements and exact dependencies are recorded in
[composer.json](composer.json) and [composer.lock](composer.lock).
