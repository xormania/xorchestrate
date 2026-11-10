# Changelog

Each release has an entry headed `## X.Y.Z`. Its **Upgrade notes** state, for
every category, the change and the migration it needs, or "None". Releases are
annotated tags `vMAJOR.MINOR.PATCH` on master; see [PR quality](docs/pr-quality.md)
for when entries are written and [releases](docs/releases.md) for the procedure.

## Unreleased

### Added

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

### Upgrade notes

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
- **Telemetry collector:** the collector is now part of xorchestrate and the
  `telemetry` installation extra is removed; install `.[test]` (or the package
  alone) instead. The corpus and bundle formats are unchanged, so existing
  corpora and bundles open unchanged. When telemetry is unavailable,
  `telemetry_status` gains a `cause` field (`{"code": ..., "detail": ...}`);
  the detail names options or adapters, never option values.
- **Command line:** `--version` output gains the `install_source` and
  `upgrade_command` keys; existing keys are unchanged. Tools that compare the
  whole object must accept the new keys.
