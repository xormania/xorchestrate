# Changelog

Each release has an entry headed `## X.Y.Z`. Its **Upgrade notes** state, for
every category, the change and the migration it needs, or "None". Releases are
annotated tags `vMAJOR.MINOR.PATCH` on master; see [PR quality](docs/pr-quality.md)
for when entries are written and [releases](docs/releases.md) for the procedure.

## Unreleased

### Fixed

- Effect-journal responses and new retained failures omit arbitrary exception
  attributes and class names. Legacy diagnostics are projected conservatively
  without deleting stored history; malformed completion metadata preserves the
  known completion and its reconciliation requirement.

### Added

- The public ports, the values and errors they exchange and the adapter factory
  convention now come from the new Apache-2.0
  [xcontracts](https://github.com/xormania/xcontracts) package (`xcontracts>=0.1,<0.2`),
  with a conformance kit that third-party adapters can run. `xorchestrate.ports`
  and `xorchestrate.model` re-export the same objects, so existing imports keep
  working. The built-in stores and operator adapters run the kit. Telemetry
  collectors are not yet a supported third-party kind. See
  [writing an adapter](docs/adapters.md).
- Operator adapter factories (companion, version control, repository hosting)
  receive a deep copy of their options, so an adapter cannot change the
  stored selection. Companion record names with a trailing newline are now
  refused like any other invalid name.
- The `git-directory` companion keeps each record kind in one scope: the
  first write records it in `<kind>/.scope`, and project-scoped and
  project-independent use of one kind no longer see or block each other's
  records (it is refused instead). Writes go through a unique temporary file,
  so an interrupted write leaves no partial record and concurrent writers no
  longer collide.
- `doctor` no longer fails on a malformed GitHub response; the companion
  remote's visibility is reported as unknown. A non-string `api_url` and an
  unimportable operator adapter reference are reported as configuration
  errors.
- The `git` version-control adapter answers for a file or new entry inside a
  repository through its directory, as it already did for worktrees.
- The release identity gate refuses a release while a runtime dependency comes
  from a git, path or URL source (until xcontracts 0.1.0 is on PyPI,
  development uses a pinned git source).
- Checkout installs use `uv pip install --python python3 -e .` until
  xcontracts is on PyPI, because plain pip cannot read the pinned git source;
  a test runs that bootstrap in a clean environment.
- Live test-kit failures keep their reason. A failed scenario check still
  records only its assertion code in the shareable `scenario.json`; the request
  involved, the application's error and the preserved outcome of a failed effect
  are retained in a private `diagnostics.json` beside it, shown in the pane, and
  listed as `failures` by `proof.sh status`/`wait` and in the run's private
  `summary.txt`. A scenario's reason is not added to saved state or collected
  archives; readiness reasons for a check blocked before launch remain part of
  both, as before. Captured telemetry keeps its existing content policy.
- `proof.sh doctor` reports `run_location`: whether the evidence root can hold
  the scenario's private application home under the product's own rule. An
  unsafe location now blocks acceptance and story checks before launch, naming
  the directory at fault.
- A `native-handshake` story starts and stops a configured harness session and
  admits no turn. `proof.sh doctor --probe` runs it for each selected native
  driver and reports each outcome with its reason. Omitting `--story` in native
  mode now selects this story as well as `native-callback`.

- Exact-request Jev diagnostic reuse and shared-state question batches reduce repeated calls; CI reports fresh versus historical usage separately.

- A paired architecture-effectiveness pilot measures Jev against unchanged deterministic checks, retaining misses, false alarms, abstentions, usage and scope limits separately from fixture verification.

- Jev diagnostics select complete changed Python units with related contracts, callers and test sources; each advisory claim retains its candidate, source span and role contract.

- The CI result summary shows current Jev advisory outcomes, distributions,
  confidence, bounded source scope and artifact identities. Missing, foreign or
  invalid advice and optional artifact transfer failures remain separate from
  deterministic acceptance.
- Configuration format versions. A configuration may declare a top-level
  integer `version`; this release reads version `1`. A newer version is refused
  with `configuration_version_unsupported`, an older unsupported one with
  `configuration_migration_required`. Configuration is never rewritten on load.
- `validate_config(config, base_dir)` checks a configuration's structure and
  resolves its paths without importing factories, constructing adapters or
  launching processes. `build` runs it first, so structural errors are reported
  before any adapter is constructed.
- Data versions for durable state. The SQLite store records data version `1`
  in `PRAGMA user_version` and a `meta` table record. A store written by a newer
  release is refused with `data_version_unsupported`; an older one with
  `data_migration_required`. Migrations run only when explicitly requested
  through the new migration registry, never on a normal open.
- A read-only SQLite store mode that reports the stored data version without
  writing to the database. It needs write permission on the store's directory
  for SQLite's `-wal`/`-shm` files (`storage_read_only_unavailable` otherwise).
- `xorchestrate --version` prints JSON with the package version, the installed
  commit (for VCS installs, otherwise `null`), the supported configuration
  format versions and the data version.
- `describe` output gains a `warnings` list beside `operations`.
- `xorchestrate.bridge` hosts enforce the configuration format version of the
  configuration they are started with.
- The telemetry collector ships with xorchestrate
  (`xorchestrate.adapters.telemetry_collector`); corpus and artifact storage
  are selected adapters (`corpus`, `artifacts`). `composition.build()` accepts
  `telemetry_defaults` to construct the local collector when a configuration
  has no telemetry section.
- `telemetry_status` reports a bounded `cause` when telemetry is unavailable.
- A shared project server, so several agent harnesses use one application,
  state store and set of live sessions (see
  [Project server](docs/project-server.md)):
  - `serve --transport unix [--socket PATH] [--max-connections N]` runs the
    configuration's server in the foreground.
  - `connect [--socket PATH] [--actor NAME] [--start-timeout S]` is the
    harness's MCP command: it relays stdio to the server and starts a detached
    server if none is running. Each connection is its own MCP session with its
    own actor; disconnecting does not end the sessions it started.
  - `up`, `down [--detach] [--timeout S]` and `status` start, drain and stop,
    and inspect the server. `down` is refused while server-owned sessions are
    live unless `--detach` is given.
- `ClientTransport`, `ClientConnection` and `PeerIdentity` ports, with `stdio`
  and `unix` adapters.
- Projects (see [Projects](docs/projects.md)). A project tracks its
  configuration in `<repo>/.xorchestrate/` (`project.json`, `config.json`,
  optional `policies/`); each machine keeps one instance per project under
  `~/.xorchestrate/projects/<project>/` (or `$XORCHESTRATE_HOME`), shared by
  every git worktree and identified by the git common directory. A separate
  clone of a project already set up on the machine is refused
  (`project_clone_refused`).
- Operator commands: `setup`, `companion init|use`, `init [--name] [--approve]`,
  `approve` and `doctor`. The user's private companion repository holds
  per-project overlays (merged last: an explicit
  override wins, guardrails included) and approvals of tracked configurations
  (several hashes may be approved).
- Rendering: the layers are merged as JSON merge patches, substituted with the
  closed placeholder set `${repo}`, `${data}`, `${launchers}`, `${home}`,
  `${instance}`, validated and written to `config.rendered.json`. `${repo}`
  resolves per call to the caller's worktree. `status` and `doctor` list the
  settings the companion overrides.
- Worktree identity: `connect` reports its worktree (root, name, branch,
  configuration hash); every call carries it. Created records gain
  `created_worktree`, events `worktree`, telemetry observations a `worktree`
  source field, and the guardrail context a `worktree` entry. The server refuses
  a worktree whose tracked configuration differs from the one it runs
  (`configuration_mismatch`, naming the files) and an MCP client without one
  (`worktree_required`).
- Release handoff: when a project's `release` is not the installed release,
  project commands run through `~/.xorchestrate/toolchains/<release>/`, built
  with uv on first use, after the project's approval is checked.
- `init --relocate` re-points an instance whose repository was moved or
  renamed. Another repository with an instance's name is refused as a
  separate clone (`project_clone_refused`), an unrelated project
  (`project_name_conflict`) or a moved repository (`project_moved`).
- The approval summary and `doctor` list code an approved configuration runs
  from outside `.xorchestrate/` (`repository_code`: commands naming `${repo}`
  and adapters outside the `xorchestrate` package). `setup` and
  `companion init`/`use` refuse a companion inside a project worktree
  (`companion_exposed`).
- Operator ports with adapters selected by name and options from the user's
  home: `Companion` (project-scoped, extensible records; `git-directory`
  adapter, which validates its own location), `VersionControl` (`git`
  command-line adapter) and `RepositoryHosting` (`github` REST adapter).
  `doctor` reports the companion's remote and warns when it is public or its
  visibility is unknown. `companion init|use PATH --project` selects a
  companion for one project, stored in its `instance.json`.
- `composition.ProjectContext` and `build(config, base_dir, project=...)`:
  a project context supplies the telemetry defaults, the base directory and
  the default worktree.
- `xorchestrate --version` gains `install_source` (`index`, `git`, `local`,
  `archive` or `uvx`, or `unknown` when the distribution is not installed), read
  from the installed distribution's `direct_url.json` and the installer's
  environment, and `upgrade_command`, the command that upgrades the launcher the
  way it was installed (`null` when unknown). A pinned `uv tool` or pipx install
  is reinstalled without its pin, since their upgrade commands keep it.
- [Install documentation](docs/install.md) for every supported path (package
  index, git, install script, `uvx`) and `scripts/install.sh`, which installs uv
  when missing and then the launcher with `uv tool install`, without `sudo`.
- A release workflow: pushing a `vX.Y.Z` tag checks that the tag,
  `__version__`, `pyproject.toml` and the changelog agree, runs installed-package
  acceptance on the built wheel and sdist, publishes them to PyPI with trusted
  publishing and attaches them to the GitHub release. A manual run is a TestPyPI
  dry run. See [releases](docs/releases.md).
- Installed-package acceptance also installs the launcher with `uv tool install`
  from a flat index, the wheel, a git tag and a source directory, and runs it
  through `uvx`, checking the reported `install_source` for each.
- The package installs a short command, `xo`, beside `xorchestrate`; both run
  the same launcher. Documentation uses `xo`. Entries the launcher writes for
  other programs (the `.mcp.json` entry from `init`) keep `xorchestrate`.
- `xork`, a short install name (`uv tool install xork`): a package without
  code that depends only on `xorchestrate` of exactly its own version and
  provides the same commands. Every release builds, checks and publishes it with
  `xorchestrate`; the identity gate refuses a `xork` version or pin that differs
  from the package version or declares other console scripts. When `xork` is
  the package the user installed (the installer records it as requested, or,
  without such a record, `xorchestrate` came from an index as its dependency),
  `--version` reports its install source and upgrade command (for example
  `uv tool upgrade xork`).
  `scripts/install.sh` installs it with `XORCHESTRATE_PACKAGE=xork`, refuses to
  install one name while uv has the other installed, and stops when another
  program's `xo` is in uv's executable directory.
- `xorchestrate upgrade [--to TAG] [--plan-only] [--detach]` moves this
  machine's instance to the approved release `project.json` pins: it stops the
  server (refused while sessions are live unless `--detach`), takes the
  `before_upgrade` telemetry snapshot, backs up the state store, builds the
  target toolchain, lets the target release check and migrate the data, records
  the new release in `instance.json` (with an `upgrades` entry), verifies it and
  starts the server again. An interrupted or failed upgrade resumes when run
  again. See [upgrading](docs/upgrade.md).
- `xorchestrate migrate-config [FILE] [--write]` rewrites a configuration (by
  default the tracked `config.json`) to the current format version, showing the
  steps and a unified diff; it writes only with `--write`, keeps the original
  under `~/.xorchestrate/backups/`, refuses newer versions and never approves.
  `composition.ConfigMigrations` holds the release's ordered rewrite steps.
- Telemetry snapshots at chosen moments: the `snapshots` telemetry option
  (`{"triggers": [...], "archive": bool}`) selects `before_upgrade` (the
  default), `before_sync` and `before_approve`; `xorchestrate telemetry snapshot`
  takes a manual one. Unknown triggers are refused.
- `xorchestrate telemetry archive BUNDLE` copies a complete, verified bundle into
  the companion (`telemetry/<project>/<instance>/<bundle_id>/`) with an
  `archive` manifest object (`companion_commit`, `config_sha256`,
  `overlay_sha256`, `release`) that is not part of the bundle identity. It never
  commits. Companions track archived `*.sqlite3` with Git LFS.
- `doctor` reports a `release` check: an error while the instance does not run
  the pinned release.
- The global `--skip-snapshots` option proceeds without a selected telemetry
  snapshot and records it as skipped; a failing snapshot reports
  `snapshot_failed` with its cause and bundle path.
- Release gates: committed configuration fixtures per format version
  (`tests/fixtures/configs/`) must load and rewrite to the current fixture byte
  for byte; every committed data-version fixture store must migrate and be read;
  upgrade tests run between two fake release toolchains; installed package
  acceptance runs `upgrade --plan-only`, `telemetry snapshot`/`archive` and
  `migrate-config` on the installed launcher.

### Upgrade notes

- **Journal diagnostics:** existing operation diagnostics remain stored. Public
  projection hides unversioned diagnostic content; this is not a historical-data
  purge and needs no data-version migration.
- **Adapter options and custom adapters:** None changed. Third-party adapters
  that import from `xorchestrate.ports` or `xorchestrate.model` keep working;
  new adapters should depend on `xcontracts>=0.1,<0.2` instead. `xcontracts`
  is a new runtime dependency.
- **Companion layout:** a `git-directory` companion gains a `<kind>/.scope`
  file in each record kind's directory on its next write (`project` or
  `shared`); commit it with the records. Existing records stay where they are
  and need no migration.
- **Configuration format:** the optional top-level `version` key is introduced
  at format version `1`. A configuration without it is read as version `1` and
  `describe` reports a `configuration_version_missing` warning; add
  `"version": 1` now, because a later release will require it. No rewrite is
  needed.
- **Data:** data version `1` is the existing store schema. Stores written by
  earlier releases (`user_version` 0) are read as version `1` without any write.
  New stores are stamped at creation; existing stores are stamped only by an
  explicit migration (`SQLiteStore(..., migrate=True)`). No records change.
  A file with a nonzero `user_version` but no store schema is now refused
  unchanged (`data_version_unsupported` or `storage_unrecognized`) instead of
  having a store schema added to it.
- **Configuration errors:** structural errors are now reported before any
  adapter factory runs. A configuration with both a structural error and a
  factory option error now reports the structural `configuration_error`
  first. Built-in path errors have specific messages (`<kind> adapter <name>
  paths must be strings.`, `Invalid <kind> adapter <name> path.`) instead of
  `Cannot construct <kind> adapter <name>.`; the code is still
  `configuration_error`. Callers matching on messages should match on codes.
- **Projects (no project, no run):** the global `--config` option is removed.
  `describe`, `call`, `run`, `serve`, `connect`, `up`, `down` and `status`
  refuse to run outside a set-up project with `project_required`, naming
  `xorchestrate init`. To migrate a configuration file: run
  `xorchestrate setup <companion>` once, move the file to
  `<repo>/.xorchestrate/config.json` (replace absolute paths with `${repo}`,
  `${data}`, `${launchers}` or `${home}`, and keep machine-specific settings
  such as `env` in your overlay), then run `xorchestrate init` and
  `xorchestrate approve` in the repository. Scripts pass `--project PATH` and set
  `XORCHESTRATE_HOME`.
- **Legacy state inside `.xorchestrate/`:** a project whose tracked
  `.xorchestrate/` contains SQLite state from an earlier release
  (`state.sqlite3` and its `-wal`, `-shm`, `-journal` files) is refused with
  `project_invalid` until they are moved out, because they would change the
  approval hash on every write. Move them to
  `~/.xorchestrate/projects/<project>/state.sqlite3` (or delete them) and
  remove them from git.
- **Rendered configuration and state:** the configuration a project runs is
  rendered to `~/.xorchestrate/projects/<project>/config.rendered.json`. The
  SQLite state store defaults to `${data}/state.sqlite3`
  (`~/.xorchestrate/projects/<project>/state.sqlite3`) instead of
  `.xorchestrate/state.sqlite3` beside the configuration, and relative paths
  resolve against the instance directory. Existing state in
  `<repo>/.xorchestrate/state.sqlite3` is not moved; copy it to the instance
  directory or set the store `path` explicitly in your overlay.
- **Telemetry always on:** a project without a `telemetry` section now collects
  telemetry under `~/.xorchestrate/projects/<project>/telemetry` (`project_id`
  the project name, `collector_id` the instance id, `dataset_id` `default`).
  Set `{"telemetry": {"enabled": false}}` to turn it off.
- **Harness entries:** `init` writes Claude Code's tracked `.mcp.json` with
  `{"mcpServers": {"xorchestrate": {"command": "xorchestrate", "args": ["connect"]}}}`
  when it creates a starter configuration, and prints the snippet for harnesses
  with user-level configuration. Replace entries that pass `--config`.
- **Project server:** `serve` without `--transport` (or with
  `--transport stdio`) serves one client over stdio, inside a project; switch
  harness entries to `connect` to share one server. A project has at most
  one server, keyed by its instance id, shared by all its worktrees. Its lock, start lock, log and discovery record live in
  `$XORCHESTRATE_HOME/servers/` (absolute; default `~/.xorchestrate/servers/`
  in the account's home from the user database). Socket path rule: the server
  listens on `--socket` if given; else in `$XORCHESTRATE_RUNTIME_DIR` (must be
  absolute); else `<home>/servers/<key>.sock` when it fits the Unix socket
  limit (107 bytes on Linux, 103 elsewhere); else `$XDG_RUNTIME_DIR/xorchestrate`;
  else `<tmp>/xorchestrate-<uid>`. Clients find it through the discovery record,
  not their own environment. Socket paths over the limit are refused, and
  server directories and files must be private and owned by the user.
  An explicit `--socket` is refused with
  `socket_path_unsafe` unless its directory is owned by the user and not
  group/other-writable, and every enclosing directory is owned by the user or
  root and not group/other-writable unless sticky. A socket directly in
  `/tmp` is therefore refused; use a private subdirectory. Clients also refuse
  a listener run by another user (`peer_untrusted`) before sending anything.
  Where the platform has no `SO_PEERCRED`, neither side can check the peer's
  user and both proceed; keep an explicit `--socket` in a directory only you
  can traverse there (see [project server](docs/project-server.md#known-limitations)).
- **Commands:** `xorchestrate` keeps working and existing harness entries stay
  valid. Before installing or upgrading with uv, check
  `ls -l "$(uv tool dir --bin)/xo"`: if another program's `xo` is there, a fresh
  `uv tool install` fails, a pinned reinstall (`uv tool install
  xorchestrate@latest`) fails and removes the installed launcher, and
  `uv tool upgrade` silently replaces the other `xo`. Move the other `xo` away
  first; the install script stops when it finds one. Install only one of
  `xorchestrate` and `xork`: they own the same commands (see
  [install](docs/install.md#package-index)).
- **`down` and `status`:** they now always run in the installed launcher and are
  never handed off to the pinned release's toolchain. A process handed a command
  for one release refuses to hand it on to another (`handoff_loop`).
- **Changing a project's release:** a changed `release` in `project.json` is a
  tracked change that must be approved, and it now takes effect only through
  `xorchestrate upgrade`. Until then project commands are refused with
  `upgrade_required` (`down` and `status` keep running with the installed
  release). `instance.json` `release` now records the installed release, which
  only `init` and `upgrade` set. Run `xorchestrate upgrade` after approving a
  new pin.
- **Rendering:** a render no longer records `release` in `instance.json`. A
  telemetry snapshot selected by `before_sync` or `before_approve` (by both the
  running and the new configuration) that fails refuses the render with
  `snapshot_failed`, leaving the previous rendered configuration in place;
  `--skip-snapshots` proceeds without it. `instance.json` records the overlay
  digest of each render (`overlay_sha256`).
- **Lifecycle definitions:** None.
- **Adapter options:** the local telemetry adapter's `profile` option is
  removed; a configuration that still sets it leaves telemetry unavailable with
  `cause` `{"code": "unknown_option", "detail": "profile"}`. Move the profile's
  settings to inline options (see [telemetry](docs/telemetry.md#migrating-from-the-profile-option)).
  Profiles defaulted `database` to `live.sqlite3` while inline options default
  to `events.sqlite3`, so set `"database": "live.sqlite3"` explicitly to keep
  an existing corpus. New `corpus` and `artifacts` options select the corpus and
  artifact storage adapters (`sqlite` and `filesystem` by default); the
  `database`, `capture_lock_timeout_ms` and `artifact_root` shorthands remain
  and cannot be combined with them. `telemetry_corpus` and
  `telemetry_artifacts` are refused as top-level configuration sections.
- **Operator source schema:** None.
- **Operator ports:** `Companion` gains `revision()` and `VersionControl` gains
  `revision(path)` (the checked-out commit); companion record keys may now be
  `/`-separated paths of names, and `CompanionRecords.keys(kind, *,
  recursive=False)` lists nested keys only with `recursive=True`, so overlays,
  approvals, policies and launchers stay flat (files in their subdirectories
  are ignored). A custom companion or version-control adapter must add these. The git-directory companion's README mentions archived
  telemetry.
- **Telemetry options:** the local adapter accepts `snapshots`; it is excluded
  from the configuration identity, so the identity of existing configurations is
  unchanged.
- **Telemetry bundles:** a bundle manifest may carry an `archive` object, which
  verification excludes from the bundle identity. Earlier releases do not
  exclude it, so they cannot verify a bundle archived by this release; bundles in
  the export root are unchanged.
- **Telemetry collector:** the collector is now part of xorchestrate and the
  `telemetry` installation extra is removed; install `.[test]` (or the package
  alone) instead. The corpus and bundle formats are unchanged, so existing
  corpora and bundles open unchanged. When telemetry is unavailable,
  `telemetry_status` gains a `cause` field (`{"code": ..., "detail": ...}`);
  the detail names options or adapters, never option values.
- **Command line:** `--version` output gains the `install_source` and
  `upgrade_command` keys; existing keys are unchanged. Tools that compare the
  whole object must accept the new keys.
