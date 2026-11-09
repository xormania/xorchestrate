# Xorchestrate Architecture Changes for Per-Repository Instances

| | |
| --- | --- |
| Prepared | 2026-11-09 21:51 UTC |
| Status | Recommended changes for the owner's review, collected from the installation, versioning and companion repository discussions. Nothing here is implemented. |
| Basis | `xormania/xorchestrate` master at `246206a` (version `0.2.0`). Cited paths are to that revision. The October architecture proposal in `Xorchestrate-Architecture-Proposal.md` concerned the earlier runtime and is not restated here. |
| Related documents | `Companion-Repo-Specification.md`, `Release-Specification.md` |

---

## 1. Scope

Master is already a ports-and-adapters application: the kernel owns orchestration, adapters implement ports, and `xorchestrate/composition.py` is the single trusted factory boundary that reads a JSON configuration and assembles a runtime. The changes below add an **operator layer** around that boundary: how an installation is created per repository, pinned to a release, configured from a private operator source, checked, upgraded and rolled back. They touch the application core in exactly two places, both small: a configuration format version in composition and a data version in the SQLite store. Ports, the application, session drivers, providers, guardrails, routing and evaluators are unchanged.

---

## 2. Summary of changes

| # | Change | Where | Kind |
| --- | --- | --- | --- |
| C1 | Configuration format `version` accepted and enforced; `validate_config` factored out of `build` | `xorchestrate/composition.py` | product, small |
| C2 | Data version in the SQLite store with explicit migrations | `xorchestrate/adapters/storage.py`, new `xorchestrate/adapters/storage_migrations.py` | product, small |
| C3 | `--version` printing release, commit and supported versions | `xorchestrate/cli.py` | product, trivial |
| C4 | `instance` command group: `init`, `render`, `sync`, `upgrade`, `check`, `check-source`, `migrate-config`, `doctor`, `status`, `list`, `toolchains prune` | new package `xorchestrate/instance/` | operator layer, new |
| C5 | Operator source reader and template renderer with a closed placeholder set | `xorchestrate/instance/source.py`, `render.py` | operator layer, new |
| C6 | Toolchain manager over uv: release-keyed virtual environments, interpreter pin, lock handling | `xorchestrate/instance/toolchains.py` | operator layer, new |
| C7 | Local registry and settings | `xorchestrate/instance/registry.py` | operator layer, new |
| C8 | Doctor with WSL2 and Docker Desktop checks | `xorchestrate/instance/doctor.py` | operator layer, new |
| C9 | Architecture contract: a new role for the operator layer | `contracts/hexagonal.json` | enforcement |
| C10 | Documentation: `docs/instances.md`, `docs/operator-source.md`, `docs/releases.md`, `CHANGELOG.md` | `docs/` | docs |
| C11 | Tests: renderer, registry, migrations, fixture stores per data version, source fixtures | `tests/hexagonal/` | verification |

---

## 3. Boundaries and safety rules

These rules decide the layout. They come from the product's own statements that configuration is trusted local code selection and that guardrails are not an OS sandbox (`docs/hexagonal-architecture.md:142-146`, `README.md` "Connect capabilities and agents").

1. **No executable xorchestrate code inside a harness `cwd`.** Session drivers run with `cwd` at the project repository. A writable repository must not contain the virtual environment, the wrapper's target or any launcher, because an agent with write access could change what the operator executes next. Toolchains live under the user's data directory; launchers are copied into the instance data directory.
2. **No durable state inside the repository.** State, evidence, telemetry and backups live under the user's state directory. This keeps them out of agent reads, out of repository-wide tooling and out of `git clean -xdf`.
3. **The repository holds a pointer, a rendered config and a wrapper.** All three are regenerable from the companion commit and the registry. They are excluded through `.git/info/exclude`, so nothing is committed and nothing is public.
4. **Rendered configuration is generated, never edited.** The template in the companion is the editable artifact; `sync` re-renders.
5. **Instances never follow the companion's head.** They record the commit they rendered from and move on explicit `sync` or `upgrade`.
6. **The product never migrates on load.** Configuration and data migrations are explicit operator commands with backups.

---

## 4. Layout

```
<repo>/.xorchestrate/                                   excluded via .git/info/exclude
  instance.json          pointer and provenance (schema below)
  config.json            rendered product configuration
  bin/xo                 generated wrapper, absolute toolchain path baked in, regenerated on upgrade
  run.lock               shared lock held by every running xo process

~/.local/share/xorchestrate/toolchains/<release>+<extras>/   pyproject.toml, uv.lock, .python-version, .venv/
~/.local/state/xorchestrate/<instance-id>/                   state.sqlite3, telemetry/, launchers/, backups/
~/.config/xorchestrate/settings.json                         {"schema":1,"source":"/path/to/companion"}
~/.config/xorchestrate/registry.json                         {"schema":1,"instances":{"<id>":{"repo":"/abs","project":"app"}}}
```

XDG variables override the three home-relative roots. Toolchains are keyed by release and extras, so repositories on the same pin share one environment and an upgrade of one repository never touches another.

**`instance.json` schema 1**

```json
{
  "schema": 1,
  "id": "7f3c2a9e4b1d4e0f9a7c1b2d3e4f5a6b",
  "project": "app",
  "release": "v0.3.0",
  "sha": "8a8e147c…",
  "extras": ["telemetry"],
  "python": "3.12.16",
  "toolchain": "/home/x/.local/share/xorchestrate/toolchains/v0.3.0+telemetry",
  "data": "/home/x/.local/state/xorchestrate/7f3c2a9e4b1d4e0f9a7c1b2d3e4f5a6b",
  "source": {"commit": "c0ffee…", "template": "templates/app.json", "template_sha256": "…", "policies": ["policies/guardrail-default.json"]},
  "launchers": [{"path": "launchers/app-tools", "sha256": "…"}],
  "config_version": 1,
  "data_version": 1,
  "rendered_at": "2026-11-20T10:00:00Z",
  "upgrades": []
}
```

**The wrapper**

```sh
#!/bin/sh
DIR="$(cd "$(dirname "$0")/.." && pwd)"
exec flock -s "$DIR/run.lock" /home/x/.local/share/xorchestrate/toolchains/v0.3.0+telemetry/.venv/bin/python \
  -m xorchestrate --config "$DIR/config.json" "$@"
```

The shared lock is what lets `sync` and `upgrade` refuse while anything runs: they take the same lock exclusively and non-blocking. `flock` is util-linux and present on WSL Ubuntu. The wrapper execs the venv interpreter directly; uv is used only by `init`, `upgrade` and `toolchains`.

---

## 5. Product changes

### C1. Configuration format version in composition

- Accept an optional top-level integer `version`. Declare `SUPPORTED_CONFIG_VERSIONS = {1}` in `composition.py`. Treat an absent value as `1` during the grace period defined in the release specification, with a warning returned through `describe`.
- Refuse a newer version with `configuration_version_unsupported` and an older unsupported version with `configuration_migration_required`, both `OrchestrationError` codes so CLI and MCP render them as they render every other failure.
- Factor the structural checks out of `build()` into `validate_config(config, base_dir) -> ResolvedConfig`: section names, adapter shapes, option types, path resolution and placeholder-free paths, without importing factories or constructing adapters. `build()` calls it first. The instance tooling uses it for `render`, `check` and `check-source`, which must never launch a provider or session process.

Today `build()` interleaves validation with construction (`composition.py:116-149`), so `check-source` has no safe way to validate a template at a release. This refactor is the one piece of composition work the operator layer depends on.

### C2. Data version in the SQLite store

- `SQLiteStore` sets `PRAGMA user_version = DATA_VERSION` and writes the `meta` record on first creation (`storage.py:298-306` is where the schema is created). On open: equal proceeds; newer refuses with `data_version_unsupported`; older refuses with `data_migration_required` unless constructed with `migrate=True`, which only the instance tooling passes.
- `storage_migrations.py` holds an ordered registry of steps keyed by source version, each a function over a transaction using `list`, `put` and `emit`, so record versions advance and a `data_migrated` event is retained. Version 1 is the current schema; `user_version` 0 is adopted as 1 without a step.
- `MemoryStore` reports a constant current version.

### C3. `--version`

Print `{"version", "release", "commit", "config_versions", "data_version", "source_schemas"}`. The commit comes from the installed distribution's `direct_url.json` when present, which uv writes for git installs; otherwise `null`. No build step embeds anything.

---

## 6. Operator layer

### C4. Command group

All commands print one JSON object and use the CLI's existing exit codes: `0` done, `2` refused or failed with a structured error, `1` internal error. `--source PATH` overrides the settings file; `<repo>` defaults to the nearest ancestor of the current directory containing `.xorchestrate/instance.json`.

| Command | Effect | Refuses when |
| --- | --- | --- |
| `instance init <repo> --project NAME [--release TAG]` | create data directory, build or reuse toolchain, render, write `.xorchestrate/`, add `.git/info/exclude` entry, register | `.xorchestrate/` exists with a different project; repo on a non-ext4 filesystem; source invalid |
| `instance render [<repo>]` | re-render `config.json` and launchers from the recorded companion commit | lock held |
| `instance sync [<repo>]` | re-render from the current companion commit; same release | lock held; release in project file differs (use `upgrade`) |
| `instance upgrade [<repo>] [--to TAG] [--plan-only]` | the release specification's procedure | preconditions in that procedure |
| `instance check [<repo>]` | dry-run compatibility report for the current or a named release | never; reports |
| `instance check-source [PATH]` | validate an operator source | never; exit status is the verdict |
| `instance migrate-config PATH [--write]` | rewrite a template or config across format versions, print a diff | unknown version |
| `instance doctor [<repo>]` | environment and project readiness observations | never; reports |
| `instance status [<repo>]`, `instances status` | pinned versus desired release, rendered versus template, data and config versions | never |
| `instances list` | registry contents with existence checks | never |
| `toolchains prune [--keep N]` | remove toolchains no registered instance references | a referenced toolchain |

`init` is idempotent: re-running with the same project re-renders and re-registers without touching data.

### C5. Source reader and renderer

- Reads `source.json`, `projects/*.json`, templates, fragments and launchers exactly as the companion specification defines, with the same refusals.
- Deep-merge order: fragments in listed order, then the template; later wins.
- Placeholders are a closed set: `${repo}`, `${data}`, `${launchers}`, `${home}` in path-valued options and command arrays, `${instance}` in telemetry identity fields only. Substitution happens after merge and before validation. Any other `${...}` is a refusal, as is an absolute path in a template, an `env` block with values, or `inherit_env`.
- Launchers are copied into `${data}/launchers/` with mode `0755`, hashed, and recorded.
- The rendered document is validated by `validate_config` at the target release's toolchain, by invoking that toolchain's `xorchestrate instance check` when it differs from the running one.

### C6. Toolchain manager

- Key `<release>+<extras sorted and joined by '+'>`, for example `v0.3.0+telemetry`.
- Build: `uv python install <python>`; write `pyproject.toml` with one dependency `xorchestrate[<extras>] @ <url>@<release>` and `.python-version`; copy the companion lock if `locks/<key>.lock` exists else `uv lock`; `uv sync --frozen`. Record `sha` from the lock's resolved revision.
- Build in a temporary directory and rename into place, so a failed build never leaves a half toolchain behind.
- Toolchains are never modified after creation; a new release is a new directory. `prune` removes unreferenced ones.

### C7. Registry and settings

- `settings.json` holds `source` and optional overrides for the toolchain and state roots.
- `registry.json` maps instance id to repo path and project name. It is a cache: `init` writes it, `instances list` validates it against the filesystem, and `init` rebuilds entries after a loss.
- Both files are written atomically with mode `0600`.

### C8. Doctor

Read-only observations, each with `name`, `passed`, `required`, `detail`:

- interpreter version and the collector's SQLite probe, reusing the probe already written for the testkit (`scripts/testkit/adapters/profiles.py:124-135`);
- `uv` and `git` present; git access to the source URL;
- repo and data directories on a Linux filesystem: `stat -f` type is not `9p` or `drvfs`, which is the correct WSL2 test rather than a `/mnt/` prefix;
- `docker context show` and `docker info` succeed through the Docker Desktop integration;
- `tmux` present when any configured driver or story needs it;
- each configured harness command resolves to a Linux ELF binary, not a Windows executable reached through interop;
- every `pass_env` variable named in the rendered config is set, reported by name only;
- every launcher exists, is executable and lives under `${data}/launchers`;
- `git check-ignore .xorchestrate` succeeds in the repo;
- `instance.json` consistent with `config.json`, the toolchain exists and `--version` matches the pin.

Doctor fixes nothing. Where the testkit's `setup` and `doctor` overlap with this, they can later delegate to the same module; that is not required for the first release.

---

## 7. Enforcement and documentation

### C9. Architecture contract

`contracts/hexagonal.json` version `5` assigns every module a role with allowed imports (`scripts/architecture` enforces it in the `static` suite). Add a role:

```json
"operator": {
  "modules": ["xorchestrate.instance", "xorchestrate.instance.*"],
  "allowed_roles": ["foundation", "adapters", "composition", "operator"],
  "allowed_external": ["stdlib:*"]
}
```

and add `operator` to `entrypoints.allowed_roles` so `cli.py` can dispatch to it. Composition must not import the operator layer; the operator layer may import composition's `validate_config`, `read_json` and the storage migration registry. Bump the contract version.

### C10. Documentation

- `docs/instances.md`: layout, wrapper, commands, WSL2 notes, rollback.
- `docs/operator-source.md`: the contract from the companion specification, published as product documentation so the companion repository itself stays private.
- `docs/releases.md`: the release specification's rules as they apply to maintainers.
- `CHANGELOG.md` with an Upgrade notes section per release, starting with the release that introduces these changes.

### C11. Tests

- Renderer: merge order, each placeholder in each permitted field, each refusal.
- Registry and settings: atomic writes, rebuild after loss.
- Store: pre-versioned adoption, newer refusal, older refusal, migration chain over fixture stores, `data_migrated` event retained.
- Composition: `validate_config` equals `build` on structural errors for every example in `examples/`; version refusals.
- Source: the companion specification's worked example passes `check-source`; fixtures with each forbidden construct fail for the named reason.
- Toolchain manager: tested through a fake `uv` on PATH that records invocations, consistent with the testkit's existing fake-program pattern; one optional live job builds a real toolchain from the repository under test.
- Doctor: each observation with a fake environment; the filesystem type check with a stubbed `statvfs`.

---

## 8. WSL2 and Docker Desktop notes carried into the design

- Repositories, toolchains and data must be on the WSL ext4 filesystem. `init` refuses `9p` and `drvfs` mounts; `doctor` reports them.
- Processes launched by the product receive no inherited environment unless `pass_env` or `inherit_env` says so, so templates forward `PATH` and `HOME` at minimum; `init` does not inject these, the template declares them.
- Harness clients must be the Linux installs inside WSL. Interop can place Windows executables on `PATH`; `doctor` detects them.
- Docker Desktop's WSL integration supplies the `docker` CLI and socket; bind mounts of WSL paths work. `doctor` checks the context and daemon.
- Unix sockets, PTYs, `/proc` and tmux work inside WSL2, so the bridge, native terminals and acceptance tooling are unchanged.
- Host Chrome stays a manual, attached-mode tool; managed runs default to owned headless mode. Mirrored networking needs the `.wslconfig` setting; otherwise the Windows host address comes from the nameserver entry in `/etc/resolv.conf`.

---

## 9. What does not change

- Ports, the application, the state engine, session drivers, providers, guardrails, routing, evaluators, acceptance and telemetry behaviour.
- Configuration semantics, apart from the new optional `version` key and the two refusal codes.
- The CLI's `describe`, `call`, `serve` and `run`, and the MCP surface.
- The testkit, verification and acceptance scripts. They continue to manage their own profiles and environments; they may later reuse the toolchain manager.
- The examples in `examples/`, which gain a `version` field and otherwise stay valid.

---

## 10. Sequencing

| PR | Content | Verification |
| --- | --- | --- |
| 1 | C1 configuration version and `validate_config`; C2 data version and migration registry; C3 `--version`; `CHANGELOG.md` | composition and store tests; fixture store for data version 1; examples updated |
| 2 | C4 to C8 without `upgrade`: `init`, `render`, `sync`, `check`, `check-source`, `doctor`, `status`, `list`; C9 contract role; `docs/instances.md`, `docs/operator-source.md` | renderer, registry, doctor and source tests; a live `init` against a temporary companion fixture in the tmux job |
| 3 | `upgrade`, `migrate-config`, `toolchains prune`; release CI gates from the release specification; `docs/releases.md` | migration chain tests; dry-run and rollback scenario tests with two fake releases |
| 4 | First tagged release following the release procedure; companion repository created from the specification with one project | `check-source` in the companion's CI; one real instance initialized and upgraded between two tags |

Each pull request follows the repository's PR quality standard and keeps the static, tests and acceptance suites green.

---

## 11. Tradeoffs

| Choice | Benefit | Cost | Mitigation |
| --- | --- | --- | --- |
| uv as the toolchain tool | managed interpreters solve the SQLite requirement; cheap venvs; lock files | a new external dependency for operators | uv is only needed by `init`, `upgrade` and `toolchains`; the wrapper does not use it |
| Toolchains shared by release key | one environment per release across repositories | a toolchain cannot carry per-project patches | per-project differences belong in configuration, not in the environment |
| Data outside the repository | survives `git clean`, invisible to agents, backed up as a unit | one more absolute path per machine | the registry and `instance.json` record it; `init` recreates it |
| Explicit migrations only | no surprise rewrites; backups always precede changes | an upgrade is a deliberate operator step | `instances status` shows pending upgrades; `check --plan-only` is free to run |
| Closed placeholder set | templates stay readable as configs; path-free holds | some setups may want more substitution | widen the set only when two projects need the same thing |
| Private git dependencies | no package index to run | `uv sync` needs git credentials for xorchestrate and `cce` | `doctor` checks git access to both URLs |

---

## 12. Open decisions

1. Whether the operator layer lives in the main package as `xorchestrate.instance` or in a separate small distribution that depends on xorchestrate. In-package is simpler and keeps one release cadence; a separate distribution would let the operator tooling evolve faster than the product. The recommendation is in-package until a reason appears.
2. Whether the testkit's `setup` and `doctor` should delegate to the toolchain manager and doctor in a later pull request, so there is one environment story.
3. Whether `init` should write `.git/info/exclude` by default, as recommended, or require `--exclude` and otherwise leave the operator to add a `.gitignore` line.

---

Document finalized 2026-11-09 21:51 UTC. Specification and recommendations only; no repository changes were made.
