# Upgrading a project

A project pins the release it runs in its tracked `.xorchestrate/project.json`
(`"release": "v0.3.0"`). Each machine's instance records the release it was set
up or last upgraded for (`instance.json` `release`). `xorchestrate upgrade` moves
the instance from that release to the pinned one: it stops the project server,
snapshots telemetry, backs up the instance's data, builds the new release's
toolchain, lets the new release check and migrate the data, records the new
release and starts the server again.

Configuration files are rewritten only on request, with
[`migrate-config`](#configuration-format-rewrites).

## Changing the pinned release

The pin is part of the tracked configuration, so it changes like any other
tracked setting, and approval works the same way:

1. Edit `release` in `.xorchestrate/project.json`, commit it (usually in a pull
   request of the project), and pull it on each machine.
2. `xorchestrate approve` on each machine. An unapproved pin is never built,
   run or upgraded to (`approval_required`, naming `project.json`).
3. `xorchestrate upgrade` on each machine.

`upgrade` never edits tracked files and never approves anything for you.
Between steps 2 and 3, project commands (`describe`, `call`, `run`, `serve`,
`connect`, `up`, `telemetry`) are refused with `upgrade_required`, naming the
pinned and installed releases, so no release runs against data or a
configuration it was not upgraded for. `down` and `status` keep running with the
installed release, so its server can still be inspected and stopped. To go back
before upgrading, restore the pin: the earlier configuration is still approved.

## `xorchestrate upgrade`

```sh
xorchestrate upgrade [--to TAG] [--plan-only] [--detach] [--timeout S]
```

| Option | Effect |
| --- | --- |
| `--to TAG` | The release you expect to upgrade to; refused with `upgrade_target_mismatch` unless it is the approved pin. With `--plan-only` any release can be planned. |
| `--plan-only` | Build the target toolchain and print its dry-run plan; change nothing (the server keeps running). |
| `--detach` | Stop the server even while sessions are live (they become `disconnected`). |
| `--timeout S` | Seconds to wait for the server to drain and stop (default 60). |

The steps, each reported in the JSON output:

1. **Preconditions.** The tracked configuration, with its pin, is approved.
   `down` stops the project server; it is refused with `sessions_live`, changing
   nothing, while sessions are live, unless `--detach` is given. From here until
   the new release is recorded, `upgrade` holds the server's single-instance
   lock, so no harness can start a server in between.
2. **Snapshot.** The [`before_upgrade` telemetry trigger](telemetry.md#snapshots-at-chosen-moments)
   (selected by default) snapshots the running configuration's corpus into its
   export root as `before_upgrade-<UTC time>`, and archives it into the
   companion when `archive` is set.
3. **Backup.** `~/.xorchestrate/projects/<project>/backups/<UTC time>/` receives an
   online SQLite backup of the state store (`state.sqlite3`), `instance.json`,
   `config.rendered.json` and a `manifest.json` with their SHA-256 digests and the
   snapshot's path. Backups are never pruned.
4. **Toolchain.** The target release's toolchain is built under
   `~/.xorchestrate/toolchains/<release>/` with the
   [release handoff](projects.md#release-handoff)'s builder, or reused. When the
   installed launcher is the target release, it is used directly.
5. **Dry run.** The target release (`upgrade-step check`) reports, without
   changing anything, whether it reads the configuration (`config`: `ok`,
   `migration_required`, `unsupported` or `invalid`), the store's data version
   and pending migration steps (`data`), and the telemetry corpus (`telemetry`).
   Anything it cannot read is a blocker: the upgrade stops with
   `upgrade_blocked` before migrating. A configuration in an older format needs
   [`migrate-config`](#configuration-format-rewrites) and a new approval first.
6. **Migrate.** The target release (`upgrade-step migrate`) runs the explicit
   [data migration chain](state-management.md); then the dry run is repeated to
   confirm the store is at the target data version.
7. **Flip.** `instance.json` records `release`, `data_version`,
   `config_version` and `toolchain`, and appends an `upgrades` entry
   `{from, to, at, backup, snapshot, migrations, companion_commit}`.
8. **Verify.** `describe` runs through the new release (its failure is
   `upgrade_verification_failed`); `doctor`'s result is reported as `doctor_ok`.
9. **Up.** If a server was running before the upgrade, `up` starts it with the
   new release.

Running `upgrade` when the instance already runs the pin reports
`{"status": "current"}` and changes nothing.

## Interrupted or failed upgrades

Progress is kept in `~/.xorchestrate/projects/<project>/upgrade.json` with the
finished steps, `status` (`failed`, `blocked`, `verify_failed`) and the error.
Run `xorchestrate upgrade` again after fixing the cause: finished steps (the
snapshot and the backup) are not repeated, and migration resumes at the last
committed data version. The file is removed when the upgrade finishes.

What each failure leaves:

| Failure | State | Next |
| --- | --- | --- |
| Before the flip (snapshot, backup, toolchain, dry run, blockers) | The instance still records the old release; the data is unchanged; the server is stopped. | Fix the cause and re-run, or restore the pin. |
| During migration | Each step is one transaction, so a failed step is rolled back and the store stays at the last committed data version. With a multi-step chain, earlier steps may have committed, so the old release may no longer read the store. | Re-run to resume, or roll back (below). |
| After the flip (verification) | The new release is recorded and the data migrated. | Fix the cause and re-run (only verification and `up` repeat), or roll back. |

**Rollback.** Restore the pin in `project.json` to the old release (the earlier
configuration is still approved) and, if data was migrated, copy the backup's
`state.sqlite3` over the instance's store, deleting any `state.sqlite3-wal` and
`-shm` beside it. Then set `release` in `instance.json` back to the old release
(or copy the backup's `instance.json`). To restore telemetry as well, use
`telemetry_restore` with the `before_upgrade` bundle. Downgrading migrated data
without the backup is unsupported. A dedicated rollback command is not provided
yet.

## Configuration format rewrites

```sh
xorchestrate migrate-config [FILE] [--write]
```

Configurations declare a format version (`"version": 1`); a release reads the
versions in `xorchestrate --version` `config_versions` and never rewrites a
configuration on load. `migrate-config` rewrites one configuration file (by
default the project's tracked `.xorchestrate/config.json`) to the newest format
this release reads, applying its ordered rewrite steps, and prints the steps and
a unified diff. Without `--write` it changes nothing.

- A configuration without `version` is read as version 1 and gains
  `"version": 1`.
- A newer version is refused (`configuration_version_unsupported`); one older
  than this release's rewrites reach is `configuration_migration_unavailable`.
- With `--write`, the file is replaced atomically (two-space indented, sorted
  keys) and the original is kept under
  `~/.xorchestrate/backups/migrate-config/<UTC time>/`.
- A tracked configuration that changes needs approval again: review the diff,
  commit it and run `xorchestrate approve`. The command never approves.

It runs with the installed launcher, so it rewrites to the format the launcher
reads; upgrade the launcher first when a new release changes the format. Policy
fragments and overlays are merge patches and are not rewritten; release notes
name any change they need.

## Known limitations

- Durable `operation` records left `pending` by a crashed server are not a
  precondition; only the running server's live sessions are.
- A configuration whose format the target release no longer reads blocks the
  upgrade; it is not rewritten in memory.
- There is no rollback command; rollback is the manual procedure above.
