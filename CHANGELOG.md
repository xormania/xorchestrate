# Changelog

Each release has an entry headed `## X.Y.Z`. Its **Upgrade notes** state, for
every category, the change and the migration it needs, or "None". Releases are
annotated tags `vMAJOR.MINOR.PATCH` on master; see [PR quality](docs/pr-quality.md)
for when entries are written and [releases](docs/releases.md) for the procedure.

## Unreleased

### Changed

- Acceptance and tool evaluation share semantic provider-result decoding while
  preserving the exact raw evidence. Direct measurements can include business
  `content`; MCP assessment JSON text with duplicate fields is now refused.
- CI runs eight application-test shards with refreshed fallback weights and a shared artifact-based timing model that updates from complete qualified runs.
- Small advisory Jev probes select PR review attention, examine specific findings
  and classify failed CI evidence in trusted event workflows. Their bounded,
  cached results do not delay required CI. See [event advice](docs/jev-events.md).
- Review probes validate original source before redaction and retain separate
  inference-text identities. Review-triggered inference requires a configured
  GitHub reviewer ID verified against the review and relay actor.
- Jev source and post-test advice runs after CI in a separate trusted consumer; required verification no longer waits for inference. Full live calibration is explicitly dispatched.
- PR title/body edits use a small metadata observation and marking a draft ready
  preserves existing checks. Source and base changes still validate; obsolete
  source work can stop when a surviving current-head run is verified. See
  [PR lifecycle validation](docs/pr-lifecycle-ci.md).
- Jev change packets retain moved assignment before-state using stable target identities and distinguish repeated targets.
- CI summaries retain valid mixed-cache Jev assessments and separate fresh from reused usage.
- Post-test Jev observation IDs distinguish modules that share a test family.
- Jev operational replay and live health honor the configured support-evidence question limit.
- Jev operational evaluation replays pinned engineering incidents and healthy
  controls with separate tuning and heldout groups. Reports distinguish known
  fault matches, independently supported findings, unknown costs and reviewer
  time. Live calibration is explicitly dispatched after evaluation inputs change.
  See [operational evaluation](docs/jev-operational.md).
- Jev diagnostics budget the complete serialized request, preserve complete
  evidence while splitting oversized candidate batches, and reuse successful
  partitions. Bounded transient retries share the request deadline; recognized
  provider errors retain safe codes and actual HTTP attempt counts. See
  [Jev execution](docs/jev-execution.md).
- Jev diagnostics select changed behavior across configured architecture roles,
  retain complete before/after source and deletions, and connect contract wrappers
  to statically declared fixtures and subjects. Packets identify missing inputs
  and preserve whole-change source budgets; static relationships do not claim
  behavioral coverage. See [architecture diagnostics](docs/architecture-enforcement.md#jev-diagnostics).
- The project server now runs in a terminal host, selected in `settings.json`
  as `terminal_host`. It defaults to a private multiplexer server (`tmux`),
  so the server's terminal survives its client and an operator can attach to
  it. Detached processes (`detached-process`) are the configured fallback
  where the multiplexer is missing and run the server exactly as before.
  `doctor` and `status` report which host is active, why, and whether its
  terminals survive and are interactive. `down` closes the stopped server's
  terminal. See [terminal host](docs/terminal-host.md).
- The test kit runs acceptance and story workers, and managed runs, in the
  product's terminal host instead of its own private copy of the multiplexer
  code, always in owned mode. Choose a host with the profile's `terminal_host`
  or `--terminal-host` (JSON, as in `settings.json`). The default is the
  product's default host *without* its fallback: an unready host is a setup
  error (`terminal_host_unavailable`) before anything launches, so a run never
  moves to another host unless one is selected explicitly.
  Renamed or changed in reports and receipts:
  - readiness item `tmux` → `terminal_host`; its text is the host's own reason;
  - acceptance failure `tmux_missing` → `terminal_host_unavailable`;
    `tmux_start_failed` and `tmux_control_failed` → the host's own error code;
  - acceptance cleanup error `tmux_shutdown` → `terminal_shutdown`,
    `terminal_host_shutdown` or `terminal_cleanup_unverified`;
  - `report.json`: `tmux_status_refreshes` removed, `terminal_host` added
    (selected and active host, fallback reason);
  - `session.json` is no longer written; the run keeps `terminal.log` (all the
    worker printed) beside `pane.txt`. On timeout the worker is asked alone to
    stop and given the grace, and `pane.txt` is the tail of that log, so its
    interruption lines are kept;
  - scenario check `tmux_worker_context` → `terminal_host_worker_context`, code
    `tmux_required` → `terminal_host_required`; evidence label
    `tmux-acceptance` → `terminal-acceptance`. The worker receives
    `XORCHESTRATE_TERMINAL_HOST`; the multiplexer's own markers no longer reach it;
  - `terminal.json` schema 1 → 2: the host's selection, its placement and an
    opaque receipt. Attach rebuilds the host at the run's own private
    placement and asks it for the attach command; nothing in the file is
    executed. Schema-1 multiplexer descriptors are refused with a reason.
    Native PTY descriptors are unchanged;
  - managed state: `worker` and `dashboard` process identities and the
    `terminal` server identity are replaced by `terminal` (host report and
    receipts); `status` derives `observed.worker_alive` from the live
    supervisor and the run's lifecycle; stalled workers get a keyboard
    interrupt instead of SIGTERM.
- `proof.sh close` on a run whose supervisor is gone (finished or killed
  mid-run) shuts the run's host down at its recorded placement, stops anything
  still naming the run, removes the run's own endpoint directory only, and
  reports `cleanup` from what is actually left (`closed`, or `already_closed`
  when nothing was).
- `proof.sh start --keep-after-run` keeps a managed run's terminals after it
  finishes, even if its supervisor ends; `close` adopts and ends them. Cleanup
  then lists them as `retained` (never verified) beside the work's own
  verified cleanup. Foreground runs refuse the option.

### Fixed

- A failed bridge frame write closes the connection before another writer can
  append to the unfinished frame. Awaiting requests retain unknown outcomes;
  response timeouts after complete writes still allow observation and control.
- Native crash calibration waits for the applied-effect boundary before crash
  injection and reaps its blocked host when a scenario fails.

- The built-in PR provider limits authenticated inspection and handoff reads to
  configured workspace repositories plus explicit `read_repositories` entries.
  Out-of-scope reads are rejected before hosting or credential lookup.
- Effect-journal responses and new retained failures omit arbitrary exception
  attributes and class names. Legacy diagnostics are projected conservatively
  without deleting stored history; malformed completion metadata preserves the
  known completion and its reconciliation requirement.
- Delivery attempts, native-call settlement, acceptance measurements,
  assessment escalation, bridge host evidence, adapter settlement causes and
  telemetry no longer copy an arbitrary exception's `code` or `details`
  attribute (or its class name, where that was the fallback). They record a
  typed failure's code, or `external_error` / the site's existing default.
- Session recovery reports, adapter cleanup errors, native-call and delivery
  recording errors, ACP native observations and telemetry construction status
  no longer record an untyped exception's class name; they record its public
  code (`external_error` for untyped failures). `application.failed` telemetry
  drops its `type` field. Terminal hosts report local OS failures by errno name
  (for example `EACCES`) instead of the exception class.

### Added

- Optional developer settings for contributor tools: one declared table of
  `XORCHESTRATE_DEV_*` keys read from flags, the environment, a git-ignored
  `.devtools.env` and a user file, with `python3 -m scripts.devconfig show`
  naming each value's source. The product does not read them. See
  [developer settings](docs/agent-workflow.md#developer-settings).
- Calibration fault cases are registered data: one service judges any inventory
  under `scripts/verification/inventories/`, selected by name or by stage (`ci`,
  `pre-draft`, `manual`). The native faults moved there unchanged. See
  [the testing framework](docs/testing-framework.md#adding-or-changing-coverage).
  `python -m scripts.verification.calibrate_suite --inventory` now takes a
  registered inventory name instead of a file path, and
  `scripts/verification/faults.json` is gone: register a new inventory file in
  `scripts/verification/inventories/registry.json` and pass its name.
- The state and work faults are registered inventories too, run at `ci` by the
  same service; each work fault now runs once per storage adapter. Their
  separate entry points are gone: run
  `python -m scripts.verification.calibrate_suite --inventory state` (or `work`).
- A calibration case may declare the contract function that holds its check
  (`"contract": "module:function"`); an assertion there is credited only for that
  case. Calibrated checks follow one standard: an UPPER_SNAKE ID asserted at one
  site, with `refusal(call, code)` for refusal guarantees. Three native
  diagnostics are renamed to UPPER_SNAKE.
- Jev now asks evidence-addressed atomic questions at the source stage and adds
  optional post-test guarantee support and failure triage. Current candidate and
  lane identity, executed inventories and exact failure cases bind the advice;
  missing evidence abstains, source spans remain reviewable, and deterministic
  CI qualification stays unchanged when a lane or the provider fails.

- Shared development skills for agents working on this repository (run-checks,
  red-first, pr-ready, crash-recovery, team-roles, serena) in `.agents/skills`,
  mirrored for Claude Code through relative symlinks in `.claude/skills`. They
  point at the existing guides and at the new
  [agent workflow](docs/agent-workflow.md), which covers team roles, crash
  recovery for contributors and using Serena. A Serena project configuration
  (`.serena/project.yml`, python language server, tools interface) is
  included; connecting Serena stays a per-machine step.
- `hosted` session adapter: a native session runs in a bridge host that
  Xorchestrate opens in the project's terminal host. It survives the server,
  can be attached to, and is reconnected (or resumed in a fresh terminal from
  its saved state) through the existing bridge. The `connected` example's
  `worker` is now hosted. `stop` closes the terminal even when the bridge host
  does not answer, and `max_sessions` bounds running hosted terminals,
  detached ones included. See [hosted sessions](docs/terminal-host.md#hosted-sessions).
- Recovery after a hard restart: the server detects an unclean stop or
  interrupted sessions from durable state and recovers them in the background
  as the project's `recovery_policy` selects (`reconnect`, the default, or
  `manual`). `xorchestrate recover` recovers the rest, acting only where its
  caller may reconnect or stop each session (its actor and work binding
  weighed). `status` and `doctor` report it; doctor reads only an approved,
  built-in store. Shutdown waits for the session in hand. See
  [recovering after a container or machine failure](docs/terminal-host.md#recovering-after-a-container-or-machine-failure).
- PR edit/readiness observations preserve useful running validation and reuse
  authenticated exact-candidate behavior evidence, retaining original lane
  identities and flat references across repeated metadata activity.
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
- A configurable PR capability provider observes candidate-bound checks and
  discussions, checkpoints and refreshes owned branches, publishes reviewed
  source trees as drafts, and retains target custody and progress for uncertain
  writes. See [PR preparation](docs/pr-delivery.md) for configuration and recovery.
- CI candidate contexts bind the tested merge/base/tree to GitHub checks and
  immutable per-lane producer artifacts, including successful partial reruns.
- `TerminalHost` port with `tmux` and `detached-process` adapters: open,
  list, input, interrupt, bounded output, attach, verified close and
  identity-checked adoption after a controller restart, plus an `owned` mode
  in which everything stops with the controller.
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
- Operational telemetry accepts a nested `sink` adapter selection, including
  trusted installed factories, through the existing composition resolver.
  Current flat local/OTLP options keep working; sampling, bounded dispatch and
  local corpus capture remain shared. Approval summaries and `doctor` disclose
  external sink factory references without importing their code. See
  [telemetry options](docs/telemetry.md#configuration-options).
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

- **CI policy:** edited/readiness PR events now run current observations.
  `CI_REUSE_ENABLED=false` disables behavioral reuse without weakening ordinary
  validation. Existing required status names and merge freshness settings remain.

- **Journal diagnostics:** existing operation diagnostics remain stored. Public
  projection hides unversioned diagnostic content; this is not a historical-data
  purge and needs no data-version migration.
- **Failure codes in retained records:** for failures that are not typed
  `OrchestrationError`s, new delivery-attempt, native-call, measurement and
  bridge host records store `external_error` where they previously stored the
  exception's `code` attribute or class name. Existing records are unchanged;
  no migration.
- **Failure names in reports and telemetry:** `recover` reports, `cleanup_failed`
  details, `result_unapplied` `recording_error`, ACP `native_completion_unknown`
  observations and telemetry construction details now carry `external_error`
  (or a typed code) instead of an exception class name; terminal host errors
  and readiness reasons carry an errno name. `application.failed` telemetry no
  longer has `error.type`. Consumers matching class names should match codes.
- **Adapter options and custom adapters:** local telemetry adds optional
  `operational.sink` selection through the existing trusted resolver. Current
  flat local/OTLP settings keep working; no configuration migration is needed.
  Nested sink factories receive `(options, CollectorScope)` and expose `emit`;
  this internal kind is not a new public xcontracts kind. Third-party adapters
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
- **Project server and terminal host:** No settings change is required. The
  server runs as terminal `server` in a terminal host. The default is `tmux`,
  falling back to `detached-process` (the previous launch) when tmux is
  missing; `doctor` and `status` report the switch. `down` still stops the
  server, then closes its terminal. `serve` without `--transport` (or with
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
- **Hosted sessions:** Opt-in. Existing owned session drivers are unchanged.
  Set a session's adapter to `hosted`, wrapping the previous selection, only
  when that conversation should survive the server.
- **Recovery:** No configuration change. `recovery_policy` defaults to
  `reconnect`. Set `{"recovery_policy": {"adapter": "manual"}}` to detect a
  hard restart and leave reconnects to `xorchestrate recover`. Unlike `down`
  and `status`, `recover` is handed off to the pinned release, as `up` is.
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
- **Lifecycle definitions:** operation definition version `2` adds a
  `progress` transition from `pending` to `pending` for PR target custody and
  confirmed substeps. Existing stored states need no migration. Custom operation
  definitions used with the PR provider must admit and govern this transition.
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
  A new optional `pr` capability provider accepts `github` and `git` adapter
  options; its owned workspace paths are explicit absolute paths. Its optional
  `read_repositories` list grants inspection outside those workspace repositories;
  configure this list for a provider used only for reads. An empty read scope
  denies all hosted inspection. Other provider configuration is unchanged.
  Uncertain PR writes retain custody until explicit journal reconciliation;
  see [PR preparation](docs/pr-delivery.md).
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
