# Changelog

Each release has an entry. Its **Upgrade notes** state, for every category,
the change and the migration it needs, or "None". Releases are annotated tags
`vMAJOR.MINOR.PATCH` on master; see [PR quality](docs/pr-quality.md) for when
entries are written.

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
  writing.
- `xorchestrate --version` prints JSON with the package version, the installed
  commit (for VCS installs, otherwise `null`), the supported configuration
  format versions and the data version.
- `describe` output gains a `warnings` list beside `operations`.

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
- **Lifecycle definitions:** None.
- **Adapter options:** None.
- **Operator source schema:** None.
- **Telemetry collector (`cce` commit):** None.
