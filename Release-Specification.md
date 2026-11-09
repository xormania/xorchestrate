# Release Specification

| | |
| --- | --- |
| Prepared | 2026-11-09 21:51 UTC |
| Status | Draft specification for the owner's review. Defines release identity, the version surfaces a release must declare, compatibility rules and the per-instance upgrade procedure. Nothing here is implemented. |
| Basis | `xormania/xorchestrate` master at `246206a`. Cited paths are to that revision. |
| Related documents | `Companion-Repo-Specification.md` (where desired releases are declared), `Instance-Architecture-Changes.md` (the code changes this specification needs) |

---

## 1. Where things stand

| Fact | Where |
| --- | --- |
| The package version is `0.2.0`, declared twice | `xorchestrate/__init__.py:3`, `pyproject.toml` `[project] version` |
| The MCP server reports that version in `serverInfo` | `xorchestrate/mcp.py:44` |
| No release tags exist; the only tags are `archive/...` evidence tags | `git ls-remote --tags origin` |
| Configuration has no format version, and composition rejects unknown top-level keys | `xorchestrate/composition.py:107-109` |
| The SQLite store creates two generic tables and has no schema version or migration step | `xorchestrate/adapters/storage.py:298-306` |
| Record shapes are owned by the application; nineteen record kinds are written today, led by `operation`, `session`, `actor` and `evidence` | `xorchestrate/application.py` and siblings |
| Lifecycle definitions carry a version, and persisted transition history retains it; the documentation says replacing a definition implies no automatic record migration | `xorchestrate/lifecycles.py:25`, `docs/state-management.md` |
| The telemetry collector is an external dependency pinned to a commit of `xormania/cce`, with its own `schema_version` and a fixed SQLite requirement | `pyproject.toml` `telemetry` extra, `xorchestrate/telemetry.py:249`, `docs/telemetry.md:15-18` |
| The PR standard asks for concise release notes per user-visible change and notes that no changelog is maintained yet | `docs/pr-quality.md:117-119` |

A release today is therefore a commit, and an installed instance cannot tell whether its configuration or its data will be understood by a newer commit. This specification adds the version surfaces that make a release a contract.

---

## 2. Version surfaces

A release must declare, and an instance must record, the following:

| Surface | Form | Declared by a release in | Recorded per instance in |
| --- | --- | --- | --- |
| Release version | `vMAJOR.MINOR.PATCH`, optional `-rc.N` | git tag, `__version__`, `pyproject.toml` | `instance.json` `release`, `sha` |
| Configuration format version | positive integer | `SUPPORTED_CONFIG_VERSIONS` in composition | `config.json` `version`, `instance.json` `config_version` |
| Data version | positive integer | `DATA_VERSION` in the SQLite store adapter; migration steps registry | `PRAGMA user_version` plus a `meta` record; `instance.json` `data_version` |
| Lifecycle definition versions | integer per machine name | `lifecycles.py`, `activity.py` | retained in transition history already |
| Telemetry collector schema | the collector's `schema_version` | the `cce` commit pinned in `pyproject.toml` | reported by `telemetry_status`; snapshot before upgrade |
| Operator source schema | positive integer | `SUPPORTED_SOURCE_SCHEMAS` in the instance tooling | `source.json` `schema` |

Only the first three are new mechanisms. The others exist and are folded into the upgrade check.

---

## 3. Release identity

1. A release is an annotated tag `vMAJOR.MINOR.PATCH` on master. Pre-releases use `vMAJOR.MINOR.PATCH-rc.N`.
2. The tagged commit sets `__version__` and the `pyproject.toml` version to the same string without the `v`. A CI job on tag push fails if the three disagree.
3. `xorchestrate --version` prints JSON: the version, the resolved commit when the package was installed from git (read from the installed distribution's `direct_url.json`, which uv and pip write for VCS installs), the supported configuration format versions, the current data version and the supported operator source schemas. `instance.json` copies the first two.
4. Releases are consumed from git by tag through uv. Publishing wheels to an index is a later option and changes nothing in this specification except `source.json` `xorchestrate.url`.
5. Each release has a `CHANGELOG.md` entry with a mandatory **Upgrade notes** section (section 8). The companion's `default_release` is updated in a separate pull request in the companion, never by the release itself.

---

## 4. Compatibility policy

While the major version is 0:

| Bump | Configuration format | Data | Adapter options and tool schemas | Operator source schema |
| --- | --- | --- | --- | --- |
| Patch | unchanged | unchanged | additive only | unchanged |
| Minor | may bump, with a migration | may bump, with a migration | may change, listed in upgrade notes | may bump, with `check-source` guidance |
| Major | reserved for 1.0 | | | |

From 1.0, standard semantic versioning applies: a configuration or data version bump is a major release.

What counts as a configuration format bump: removing or renaming a section or option, changing an option's meaning or default, tightening validation that rejects previously valid files. Adding an optional option or a new adapter alias does not bump the format.

What counts as a data version bump: any change to the shape of a persisted record kind that a reader must handle differently, a new required field, or a change to the meaning of a status value. Adding a new record kind does not bump the data version.

---

## 5. Configuration format versioning

**Mechanism.** Composition accepts an optional top-level integer `version`. Each release declares `SUPPORTED_CONFIG_VERSIONS`, a set. Rules:

- `version` absent: treated as `1` for the first two minor releases after introduction, with a warning in `describe`, `instance check` and `doctor`. After that window it is required.
- `version` in the supported set: read as is. A release may support several versions and read older ones directly when the differences are additive.
- `version` newer than any supported: refuse with code `configuration_version_unsupported`, naming the version and the release that would read it.
- `version` older than any supported: refuse with code `configuration_migration_required`, naming the `instance migrate-config` command. Configuration is never rewritten on load.

**Migrations.** `instance migrate-config` applies ordered rewrite steps `N` to `N+1` to a template or a rendered config, writes the result beside the original with the original preserved, and prints a unified diff. For companion templates the rewrite is applied in the companion by a pull request; `check-source` at the new release reports which templates need it.

**Why not auto-migrate on load.** A rendered config is generated from a template in the companion; rewriting it locally would diverge from its source of truth. The companion template is the thing to migrate, and `sync` re-renders.

---

## 6. Data versioning

**Mechanism.** The SQLite store sets `PRAGMA user_version` and writes a `meta` record `{"data_version": N, "written_by": "vX.Y.Z", "migrated_at": "..."}` in the same transaction as its schema creation. A store with `user_version` 0 is a pre-versioned store and is treated as version 1 on first open by a versioned release, since the current schema is version 1 and that step changes no data.

On open, the adapter compares the stored version with its `DATA_VERSION`:

- equal: proceed.
- stored newer: refuse with `data_version_unsupported`, naming the release in `meta.written_by`. A newer release wrote this data; the operator downgraded or pointed an old toolchain at new data.
- stored older: refuse with `data_migration_required` unless opened by `instance upgrade`, which runs the migration chain. Normal `serve`, `run` and `call` never migrate.

The memory store is always current; its data version is a constant.

**Migration steps.** A registry in the storage adapter package maps `N` to a step function that transforms records of named kinds inside one transaction, using the store's own `list`, `put` and `emit` operations so that record versions advance and an event `data_migrated` with `from`, `to`, `release` and counts is retained. Steps are composable: upgrading from 1 to 4 runs 1 to 2, 2 to 3 and 3 to 4 in order. Each step is covered by a test that loads a fixture store written by the release that introduced the previous version and asserts the application reads the migrated records.

**Telemetry corpus.** The corpus belongs to the collector, not to xorchestrate. The upgrade check opens it through the collector pinned by the target release, reports its `schema_version` and whether it is readable, and refuses the upgrade when it is not. A telemetry snapshot taken before the upgrade is the rollback path, using the bundle snapshot and restore operations the telemetry guide already provides.

**Lifecycle definitions.** A definition version bump is listed in the upgrade notes with the events and guards that changed. Because persisted transition history retains the definition version, no history rewrite is needed; a data migration is required only when a record's state field values change meaning, which is a data version bump by section 4.

---

## 7. Upgrade procedure per instance

`xo instance upgrade [--to TAG]`, taking the target from the companion's project file when `--to` is omitted. Every step prints JSON and the command is safe to re-run after an interruption.

1. **Preconditions.** Refuse unless all hold: an exclusive lock on the instance's `run.lock` is obtained, which fails while any `xo` process runs because the wrapper holds a shared lock; no `session` record is in a live status (`starting`, `active`); no `operation` record is in `pending`, `unknown` or `completed_unapplied`; `doctor` passes for the current release. Each failed precondition is named with the operation or session ids involved.
2. **Backup.** SQLite online backup of `state.sqlite3` and a telemetry snapshot into `${data}/backups/<timestamp>/`, with a manifest of hashes. The backup is never pruned by the upgrade.
3. **Toolchain.** Build `toolchains/<target>+<extras>` if absent: `uv python install <python>`, write the small `pyproject.toml` depending on `xorchestrate[<extras>] @ <url>@<target>`, use the companion's lock if present else `uv lock`, then `uv sync --frozen`. Nothing in the repository or the data directory changes yet.
4. **Dry run.** Invoke the new toolchain's `xorchestrate instance check --config <rendered> --data <dir>`: configuration format accepted, rewritten or unsupported; data migration chain required, with counts per step from a read-only scan; lifecycle definition changes; telemetry corpus readability; operator source schema compatibility. The command prints the plan and exits without changes. `--plan-only` stops here.
5. **Re-render.** If the target release changed the configuration format, re-render from the companion template at the recorded commit, applying `migrate-config` to the template in memory and reporting that the companion template itself needs the same rewrite.
6. **Migrate.** Run the data migration chain in the new toolchain against the live store, inside transactions, then re-run the read-only scan to confirm the stored version.
7. **Flip.** Rewrite `bin/xo` to the new toolchain, update `instance.json` (`release`, `sha`, `config_version`, `data_version`, `toolchain`) and append an `upgrades` entry `{from, to, at, backup, migrations, companion_commit}`.
8. **Verify.** Run `doctor` and `describe` through the new wrapper. Print the summary.

**Rollback.** Point `bin/xo` and `instance.json` back at the previous toolchain, which is kept until the operator removes it with `toolchains prune`. If a data migration ran, restore `state.sqlite3` and the telemetry snapshot from the backup first. Downgrading migrated data without the backup is unsupported and the command says so.

**Skipping releases.** The chain runs every step between the stored and target data versions, so skipping releases is supported as long as each step's test exists (section 9). Configuration rewrites compose the same way.

---

## 8. Release procedure

1. Confirm the changelog entry for the release has an **Upgrade notes** section listing: configuration format changes and the rewrite applied; data version changes and the migration applied; lifecycle definition changes; adapter option changes; operator source schema changes; the `cce` collector commit if it moved. "None" is a valid entry and must be stated.
2. Bump `__version__` and `pyproject.toml` in one commit titled with the version.
3. Open the release pull request; CI runs the normal suites plus the release gates in section 9.
4. Merge, tag `vX.Y.Z` annotated with the upgrade notes, push the tag. The tag job re-checks identity agreement.
5. Create the GitHub release from the tag with the changelog entry as the body.
6. In the companion, open a pull request changing `default_release` or specific projects' `release`. Merge when ready to roll out, then upgrade instances one by one.

---

## 9. CI gates in xorchestrate

| Gate | Runs on | Checks |
| --- | --- | --- |
| Version identity | tag push | tag, `__version__` and `pyproject.toml` agree; the tag's changelog entry exists and has Upgrade notes |
| Configuration compatibility | every PR | fixture configs for every supported format version load; a fixture one version ahead is refused with `configuration_version_unsupported`; `migrate-config` from each older supported version yields the current fixture byte for byte |
| Data migration chain | every PR | one fixture `state.sqlite3` per past data version, written by the release that introduced it, is migrated to the current version and read by the application tests; a fixture one version ahead is refused |
| Pre-versioned store | every PR | a store with `user_version` 0 opens as version 1 |
| Operator source | every PR | the companion specification's examples pass `instance check-source`; a fixture with each forbidden construct fails for the named reason |
| Toolchain build | tag push, optional | `uvx --from .@<tag> xorchestrate --version` in a clean container prints the expected identity |

Fixture stores are produced by a documented command at each release that bumps the data version and committed under `tests/fixtures/stores/`. They are the only way the chain stays honest over time.

---

## 10. Support window and deprecation

- Migrations are kept for the whole chain; nothing is dropped while the major version is 0. If the chain grows long, a release may collapse early steps into one, with a test that the collapsed step equals the sequence it replaces.
- A configuration option or section slated for removal is first deprecated for one minor release: `instance check`, `doctor` and `describe` report it with the replacement; the format version bumps on removal.
- The `version`-absent grace period for configuration is two minor releases after introduction, as in section 5.

---

## 11. Open decisions

1. Whether to keep the full migration chain forever or adopt a window once there is a 1.0. The chain is cheaper now; a window needs a documented export and import path for very old instances.
2. Whether releases stay git-only or also publish wheels. Git by tag is sufficient for a private deployment and keeps `source.json` simple.
3. Whether pre-release tags are used at all, or every tagged commit is a release. For a single operator, every tag as a release is simpler; the `-rc.N` form is reserved either way.

---

Document finalized 2026-11-09 21:51 UTC. Specification and recommendations only; no repository changes were made.
