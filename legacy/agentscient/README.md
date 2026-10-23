> This is the original Agentscient README. For this recovered source collection,
> read [the extraction notes](../README.md). It is not the active Xorchestrate runtime.

# Agentscient

Agentscient is a Symfony application for agentic software development. It
manages project design, context, work, sessions, and evidence so agents can
make decisions with a shared understanding across sessions and over time.

The product supports the software lifecycle, including planning,
implementation, verification, delivery, operation, and improvement. It is
under active development; design documents describe intended capabilities,
while the code and configuration establish what is currently implemented.

## Two lanes, one repository

| Lane | Purpose | Main locations |
| --- | --- | --- |
| **Prod-lane** | The Agentscient Symfony application and the capabilities it provides to managed projects. | `src/`, `config/`, `packages/`, and the application's entry points and tests. |
| **Dev-lane** | The tools, automation, and working records used to develop Agentscient. | `ops/` and `.dev/`. |

**The `dev` Git branch contains work for both lanes.** `dev-lane` names the
development tooling, not everything on that branch. `prod-lane` names the
application, including local and unfinished application work. `master` is
the release branch.

Lane names describe responsibilities, independently of the Git branch,
deployment environment, harness, model, or agent role.

Dev-lane tooling operates independently of the Symfony application, its
services, and its `vendor/` directory. Using that tooling to build the
application does not make it part of the product. Delivering a similar
product capability does not imply replacing or retiring development tools.

The development processes Agentscient provides to managed projects are
product capabilities. They do not prescribe the process used to build
Agentscient itself. See [the lane boundary](AGENTS.md#lane-boundary-and-precedence)
for working guidance.

## Repository layout

| Location | Contents |
| --- | --- |
| `src/`, `config/`, `public/`, `bin/` | Application code, configuration, and entry points. |
| `packages/` | Application packages composed through Composer. |
| `tests/`, `phpunit.dist.xml` | Application test support and suite configuration. |
| `mate/` | Application diagnostic configuration and instructions. |
| `ops/`, `.dev/` | Development tooling, configuration, and working records. |
| `docs/` | Design documents, contracts, and reference material; check each document's scope and currency. |
| `.github/workflows/` | Repository CI definitions. |

## Getting started

For application work, use a PHP runtime and Composer installation compatible
with [composer.json](composer.json), [composer.lock](composer.lock), and the
relevant tool configuration. From an owned checkout:

```sh
composer install
php bin/console about
```

Use environment-specific configuration for local settings and credentials.
Application diagnostics are described in [Mate's instructions](mate/AGENT_INSTRUCTIONS.md).
For development tooling, start with [ops/README.md](ops/README.md) and the
instructions for the component you are using.

## Contributing

Read [AGENTS.md](AGENTS.md), identify which lane the change belongs to, and
preserve other sessions' work. Base working branches on `dev` and propose
changes back into `dev`; release decisions belong to the owner.

Reuse existing Symfony, ecosystem, and project capabilities where they fit.
Keep shared behavior in the services that own it and provide interfaces as
needed. Component documentation owns implementation details, setup, and
operating procedures as they evolve.

Run checks appropriate to the change and report what they verified. The
[CI definitions](.github/workflows/) describe the automated checks;
[CONTRIB.md](CONTRIB.md) provides contribution conventions and templates,
subject to current owner direction and the working guidance in `AGENTS.md`.
