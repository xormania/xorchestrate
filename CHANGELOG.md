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
- **Project server:** `serve` without `--transport` (or with
  `--transport stdio`) is unchanged, so existing harness entries keep working;
  switch them to `connect` to share one server. A configuration has at most
  one server, keyed by its resolved `--config` path (or the working directory
  when there is none). Its lock, start lock, log and discovery record live in
  `$XORCHESTRATE_HOME/servers/` (absolute; default `~/.xorchestrate/servers/`
  in the account's home from the user database). Socket path rule: the server
  listens on `--socket` if given; else in `$XORCHESTRATE_RUNTIME_DIR` (must be
  absolute); else `<home>/servers/<key>.sock` when it fits the Unix socket
  limit (107 bytes on Linux, 103 elsewhere); else `$XDG_RUNTIME_DIR/xorchestrate`;
  else `<tmp>/xorchestrate-<uid>`. Clients find it through the discovery record,
  not their own environment. Socket paths over the limit are refused, and
  server directories and files must be private and owned by the user.
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
