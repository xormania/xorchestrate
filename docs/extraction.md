# Agentscient extraction

This repository starts with reusable implementation and tests from two immutable
Agentscient snapshots. The source repository is unchanged.

| Source | Revision | Destination |
| --- | --- | --- |
| `uscient/agentscient`, `dev` | `91506128934b70a8967c626d6ece16d2c8a0815c` | Active `devtools/`, required skills, runtime docs, optional PHP helper; original guidance/CI under `reference/agentscient-dev/` |
| `uscient/agentscient`, `shit` | `456457b9a0a9eb1ff285ee2d3e40535b6f722cdb` | Selected toolkit under `legacy/agentscient/` |

`extraction-manifest.json` maps every imported file to its source path, Git blob
hash, mode, and destination. Files are verified against their source blob during
extraction. Adapted files are identified; new repository documentation and CI are
listed separately. Existing notices, including `devtools/PROVENANCE-LICENSE`, are
retained. The manifest records provenance, not a requirement to leave code frozen.

## Reuse map

| Capability | Active implementation | Earlier material retained for reuse |
| --- | --- | --- |
| MCP and capability discovery | `devtools/api.py`, `mcp.py` | `ops/conductor/tools/dev_mcp.py`, `coordination_mcp.py`, `lane_mcp.py` |
| Native session lifecycle | `launch.py`, `native.py`, `profiles.py`, `harnesses.py` | `ops/conductor/tools/sessions.py`, `session_adapters/` |
| Message delivery and routing | `control.py`, `native.py`, work bindings | `ops/conductor/tools/coordination.py`, `courier.py`, `session_adapters/routing.py` |
| Roles and organization | `role-packages.cue/json`, `role_packages.py`, `role_behaviors.py` | Coordination roles, team definitions, placement and registry data |
| Configuration and adapters | `adapters.py/json`, `configuration.py/cue/json`, `runtime.py` | `ops/conductor/tools/laneconf.py`, CUE contracts and tracked/local/environment precedence |
| Work and state | `orders.py`, `work.py`, `store.py`, `artifacts.py` | `ops/conductor/tools/machine.py`, `instrument.py`, task and workflow machinery |
| Development providers | Workspace/Git/GitHub services, Serena, Symfony integration | Provider interfaces, code/project facades, harness isolation and dispatch |
| Evidence and validation | Runtime fixtures, telemetry, diagnostics, capability gaps | `ops/testkit/`, conductor/devlane tests, synthetic app fixtures and telemetry |

Active paths in the table are relative to `devtools/`; earlier paths are relative
to `legacy/agentscient/`. These are separate implementations, not interchangeable
modules. Their protocol envelopes, state schemas, and policies need comparison
before integrating an earlier component into the active runtime.

## Changes made for this repository

- Establish Xorchestrate's entry documentation and contribution guidance.
- Retain runtime namespaces, protocol/state identifiers, role catalogs, and tests
  to avoid a simultaneous behavioral migration.
- Keep earlier code in its original relative layout beneath a separate directory;
  its skills and hooks are not installed at the new repository root.
- Replace product/private-provider CI with hosted, credential-free runtime checks.
  Preserve original CI and its tests for reuse outside the active workflow.
- Keep PHP toolchain files required by existing optional setup helpers. Core use
  does not require them or the Symfony product.
- Add a temporary-repository runner for selected historical suites, since some
  tests archive HEAD and expect the old toolkit at the repository root.

## Retained assumptions

The active runtime still uses SQLite and direct SQL. Harness/evaluation/role
registries are extension points; a general database plugin contract is not yet
implemented. Current delivery combines native transport and work/role binding;
a general routing policy interface remains to be designed. The default CO/XO/NCO
structure remains behavior in the imported runtime, not a required organization
model for Xorchestrate's future design.

Provisioning and acceptance helpers retain original environment assumptions.
In particular, `devtools/scripts/acceptance.py` targets the separate original
acceptance fixture repository; the PHP toolchain manifest names original retained
release assets; runner setup assumes owner-managed infrastructure. These helpers
are preserved for adaptation and are not the new project's quickstart or CI gate.
Use explicit target branches when configuring a project; imported examples often
assume `dev`. Runtime profiles and vendor/model choices are configurable and must
match the operator's actual installed capabilities.

Inherited operator documentation describes the original system. Start from the
root README for a self-contained core example. Native sessions require Linux/WSL2
process and socket facilities plus tmux and authenticated harnesses. Docker/Serena,
PHP/Symfony, GitHub mutation, and Jev acceptance need separately configured hosts.

## Deliberate exclusions

The source Symfony product (`src/`, root `config/`, `public/`, `mate/`, Composer
manifests, and product tests) is not part of this extraction. The earlier toolkit's
synthetic applications remain because they are test fixtures.

Live `.dev/` state and records, conductor handoffs, findings, reports, ideas,
decision seed history, after-action records, host caches, credentials, and
historical operational archives are excluded. Reusable implementation, contracts,
tests, and selected reference material are included. Neither Agentscient branch
nor Xordination is modified.

See [CI and validation](../.github/CI.md) and the
[historical collection notes](../legacy/README.md) for the checks actually run.
