# Projects

Xorchestrate runs only inside a set-up project. A project tracks its
configuration in its own git repository; each machine keeps one instance of
it, shared by every worktree; the user's private companion repository holds
their own overrides and their approvals of the project's configuration.

Two directories share a name and hold different things:

- `<repo>/.xorchestrate/` is the project's **tracked configuration**, committed
  with the project. Nothing in it is machine-specific or secret.
- `~/.xorchestrate/` is this user's **machine-local state**: settings, the
  instance registry, release toolchains and each project's live data.

## Layout

```
<repo>/.xorchestrate/                tracked; path-free and secret-free
  project.json                       {"schema": 1, "name": "app", "release": "v0.2.0", "requires": ["codex"]}
  config.json                        product configuration with placeholders
  policies/*.json                    optional fragments merged beneath config.json
<repo>/.mcp.json                     tracked; {"mcpServers": {"xorchestrate": {"command": "xorchestrate", "args": ["connect"]}}}

~/.xorchestrate/                     or $XORCHESTRATE_HOME, which must be absolute
  settings.json                      {"schema": 1, "companion": {"adapter": "git-directory", "options": {"path": "/abs"}}}
  registry.json                      instance id -> project name, repository, git common directory
  toolchains/<release>/              release-keyed environments (see release handoff)
  projects/<project>/                one instance per project per machine, shared by all worktrees
    instance.json                    instance id, git common directory, installed release, approval hash,
                                     versions, upgrades, and optionally this project's own companion selection
    config.rendered.json             the merged, substituted configuration the server runs
    state.sqlite3                    application state (the default store)
    telemetry/                       the live telemetry corpus
    launchers/                       launchers copied from the companion (mode 0755)
    backups/                         upgrade backups (see upgrade.md)
    hosted/                          private directories of hosted sessions (host configuration, bridge state, terminal log)
    upgrade.json                     progress of an unfinished upgrade
  servers/                           project servers' locks, sockets, discovery records, terminal placement and receipts

<companion>/                         the user's private repository (git-directory adapter)
  overlay/<project>/overlay.json     the user's overrides for one project
  approvals/<project>/approvals.json approved hashes of the project's tracked configuration
  policies/*.json                    fragments merged above every project's config.json
  launchers/                         executables configurations name as ${launchers}/...
  telemetry/<project>/<instance>/<bundle_id>/  archived telemetry snapshot bundles
  .gitattributes                     tracks telemetry/**/*.sqlite3 with Git LFS
```

`project.json` fields: `schema` (1), `name` (lowercase letters, digits, `.`,
`_`, `-`; it names the instance directory, the overlay and the approvals),
`release` (the release tag the project runs, such as `v0.2.0`) and `requires`
(programs `doctor` checks for on `PATH`).

## Commands

| Command | Effect |
| --- | --- |
| `xorchestrate setup [PATH]` | Once per user: create a companion at a new or empty `PATH`, or link an existing companion clone, write `settings.json`, and run the machine checks of `doctor`. |
| `xorchestrate companion init PATH [--project [REPO]]` | Create a companion at `PATH` (the git-directory layout, a README and an LFS rule for archived telemetry) and use it: as this machine's default, or with `--project` for that one project only. |
| `xorchestrate companion use PATH [--project [REPO]]` | Use an existing companion clone, as the machine default or for one project. |
| `xorchestrate init [PATH] [--name NAME] [--approve]` | Once per project per machine; see below. |
| `xorchestrate approve [PATH]` | Approve the tracked `.xorchestrate/` of the worktree at `PATH` (default: the working directory). |
| `xorchestrate doctor` | Check Python and its SQLite WAL fix, `git`, `uv`, the project's approval, the overlay's overrides, that configured harness commands and `requires` resolve on `PATH`, and that `pass_env` variables are set. Also reports the terminal host (`terminal_host`), what the project's host holds (`project_terminals`), and, while no server is running, whether the last stop was clean and which sessions are persisted as live (`recovery`). That recovery read opens the store only for an approved configuration with a built-in store adapter. Exit status 1 when any check is an error. |
| `describe`, `call`, `run`, `serve`, `connect`, `up`, `down`, `status`, `recover` | Run the application for the project found from the working directory, or the one named by `--project PATH`. `recover [--session ID]` asks the running server to recover interrupted sessions; see [terminal host](terminal-host.md#recovering-after-a-container-or-machine-failure). |
| `xorchestrate telemetry snapshot [--destination NAME] [--archive]` | Take a manual snapshot bundle of the project's telemetry corpus (see [telemetry](telemetry.md#snapshots-at-chosen-moments)). |
| `xorchestrate telemetry archive BUNDLE` | Copy a complete, verified bundle into the companion. |
| `xorchestrate upgrade [--to TAG] [--plan-only] [--detach]` | Move this machine's instance to the approved pinned release (see [upgrading](upgrade.md)). |
| `xorchestrate migrate-config [FILE] [--write]` | Rewrite a configuration to the current format version, showing the diff (see [upgrading](upgrade.md#configuration-format-rewrites)). |

All commands print JSON. Errors are `{"error": {"code", "message", "details"}}`
on standard error with exit status 2.

### `init`

`init` in a repository:

1. requires a registered companion (`setup` first);
2. creates a starter `.xorchestrate/` (`project.json` named after the
   repository, `config.json` with `{"version": 1}`) when the repository has
   none, and asks you to review and commit it. A repository that already
   tracks `.xorchestrate/` is never edited;
3. creates your empty overlay in the companion if absent;
4. registers this machine's instance (`projects/<name>/`), or reuses it from
   another worktree of the same repository. Instances are keyed by project
   name, so another repository with the same name is refused, naming the
   existing instance:
   - a separate clone of the same project (they share a root commit):
     `project_clone_refused`; use `git worktree add` instead;
   - an unrelated repository: `project_name_conflict`; rename one of the
     projects in its `project.json`;
   - the instance's repository no longer exists where it was recorded (it was
     moved or renamed): `project_moved`. Run `xorchestrate init --relocate` in
     the repository's new location to re-point the instance; it refuses while
     the recorded repository still exists (`project_not_moved`);
5. writes Claude Code's tracked `.mcp.json` when it creates the starter (and
   the file is absent), and prints the same entry for harnesses that keep MCP
   servers in user-level configuration, which `init` never edits;
6. renders the configuration if it is approved; otherwise it shows a summary
   (the tracked files, their hash and every command the configuration would
   launch) and asks for `xorchestrate approve`;
7. runs `doctor`.

The first approval of a project is always a separate `approve`. Afterwards,
`init --approve` approves the current tracked configuration and renders in one step.

## No project, no run

`describe`, `call`, `run`, `serve`, `connect`, `up`, `down`, `status` and `recover`
refuse to run outside a set-up project with `project_required`, naming
`xorchestrate init`. The project is the git worktree containing the working
directory (or `--project PATH`), which must have `.xorchestrate/project.json`
and a registered instance under the current home. Scripts and tests pass
`--project` and set `XORCHESTRATE_HOME` so they never touch the user's home.

There is no `--config` option. Composition still accepts any configuration:
`build(config, base_dir, project=ProjectContext(...))` takes the explicit project
context the entrypoints construct, and composition looks nothing up itself.

## Worktrees

A project is identified on a machine by its git common directory
(`git rev-parse --git-common-dir`), which every worktree of a repository
shares. All worktrees map to the same instance, the same server, the same
state and the same telemetry corpus.

`${repo}` is not one path per instance. `connect` reports the worktree it runs
in (its root, git worktree name, branch and tracked configuration hash) in its
connection hello, and the server binds it to that connection like the actor.
Every call over the connection carries it, and processes launched for that call
resolve `${repo}` in their command, `cwd` and `env` values to that worktree's
root. CLI commands bind their own worktree the same way. A launch outside any
worktree leaves `${repo}` unresolved and fails as an unavailable command.
Providers that keep a process between calls (the `mcp` provider) keep one per
resolved launch, so calls from different worktrees never share a process
started for another worktree.

A session keeps the worktree it was started from (its `workspace`): later
`send_session`, `poll_session`, `stop_session` and `detach_session` calls, a
resume or reconnect (the new session inherits its source's workspace), and
the native application-tool calls its process makes all run with that
worktree, whichever connection drives them. Native tool calls are attributed
to that worktree (`created_worktree`, `worktree`, guardrail context).

The server runs one configuration: the approved one of the worktree it was
started from. A connection from a worktree whose tracked configuration hash
differs is refused with `configuration_mismatch`, naming the differing files,
until that configuration is approved and the server restarts with it
(`xorchestrate down`, then the next `connect`).

### Room for per-worktree access

Per-worktree access rules are future work. What later rules need is already in
place:

- the connection's worktree identity reaches every application call;
- created records carry `created_worktree` and events carry `worktree`;
- telemetry capture records `worktree` in each observation's source context;
- the guardrail context passed to `Guardrail.evaluate(action, context)` has a
  `worktree` entry, so the rules adapter can already match it, for example
  `{"effect": "deny", "actions": ["create_work"], "where": {"worktree.name": "review"}}`.

## Configuration layers and rendering

The configuration a project runs is merged from four layers, later winning
(D12):

1. the project's tracked `policies/*.json`, in file-name order;
2. the project's tracked `config.json`;
3. the companion's `policies/*.json`, in file-name order;
4. your overlay, the companion's `overlay` record of the project.

Each layer is a JSON merge patch (RFC 7386): objects merge key by key, other
values replace, and `null` removes a setting. An explicit overlay setting
therefore wins over the project for everything, guardrails included. The
overlay is yours and needs no approval. `status` and `doctor` list every
setting whose effective value differs from the project's own layers, as
`{"path": "/guardrail/options/default", "source": "overlay", "project": "allow",
"value": "deny"}` (`"removed": true` for a removal; no `project` key when the
project did not set it; `source` is the last companion layer that touched the
setting). A patch that changes nothing, or a change a later layer reverts, is
not listed.

The merged document is substituted with a closed placeholder set:

| Placeholder | Value |
| --- | --- |
| `${repo}` | left unresolved; each call's worktree root (see worktrees) |
| `${data}` | `~/.xorchestrate/projects/<project>` |
| `${launchers}` | `~/.xorchestrate/projects/<project>/launchers` |
| `${home}` | the account's home directory |
| `${instance}` | the instance id |

Any other `${...}` is refused with `configuration_error`, and so is
`${repo}` anywhere other than the `command`, `discovery_command`, `cwd` and
`env` options of providers, sessions and evaluators: elsewhere (a store path,
the telemetry root) it could not be resolved per call. A SQLite store
without a `path` (and an absent `store` section) uses `${data}/state.sqlite3`,
so no durable state lands in the repository. Relative paths resolve against
the instance directory. A `telemetry` section without an `adapter` changes
options of the project's telemetry defaults, for example
`{"telemetry": {"options": {"dataset_id": "experiment-routing"}}}` in an
overlay keeps a trial's runs apart. The result is validated with
`validate_config` and written to `config.rendered.json`; the server and the
CLI build from it.

## Telemetry

Telemetry is always on for a project (D3). With no `telemetry` section, the
local collector is built with the project defaults: `runtime_root`
`~/.xorchestrate/projects/<project>/telemetry`, `project_id` the project name,
`collector_id` the instance id and `dataset_id` `default`.
`{"telemetry": {"enabled": false}}` turns it off. A Python whose SQLite lacks
the WAL fix leaves telemetry unavailable without blocking other work; `doctor`
reports it.

## Trust and approval

Configuration selects what xorchestrate launches, and a tracked configuration
can be changed by any agent with write access to the repository and by anyone
whose commits you pull. So:

- `xorchestrate approve` records the hash and the file digests in the
  project's companion (its `approvals` record, D13) and reports a summary of
  what it approved: the files and their hash, every command the configuration
  launches, the overrides and the code outside the approval (see below).
  Several hashes may be approved, for example one per branch.
- Every tracked file is read once: the approval check and the rendered
  configuration use the same bytes, so a file changed after it was checked is
  never rendered as approved. A symbolic link anywhere in `.xorchestrate/`,
  including the directory itself, is refused, and so are SQLite state files
  (`*.sqlite3`, `-wal`, `-shm`, `-journal`) that earlier releases kept there:
  move them to `~/.xorchestrate/projects/<project>/` or delete them.
- Every render checks the approval first and refuses with `approval_required`,
  naming the files that differ from the last approved configuration. The CLI
  and the server render before they build, so neither runs an unapproved
  configuration, and a refused render leaves the previous rendered file in place.
- The project server renders once, at start. It refuses connections from
  worktrees whose tracked configuration differs from the one it runs.

- A project command checks the approval before it hands off to the release
  `project.json` pins (see below), so an unapproved pin never builds or runs
  a toolchain.
- The companion must be private and outside every project. Each companion
  adapter validates its own location. The git-directory adapter refuses a
  companion directory not owned by you or writable by group or others, any
  enclosing directory owned by another non-root user or writable by others
  without the sticky bit (the rule the server applies to its socket
  directories), any location below a directory that holds a project
  (`.xorchestrate/project.json`, however many repositories are nested in
  between), and a git worktree with `.xorchestrate/`. New companions are
  created with mode `0700`. `setup` and `companion init`/`use` refuse an
  unsafe companion, every command that opens the companion refuses it again
  (`companion_exposed`), and `doctor` reports it as an error.
- `doctor` reports the companion's remote (its git `origin`) and whether the
  hosted repository is private (`companion_remote`): a public repository, or
  one whose visibility cannot be determined (not GitHub, no token, offline),
  is a warning.

**What approval does not cover.** Approval covers only the files under
`.xorchestrate/`. An approved configuration can still run code from elsewhere:
a command that names `${repo}` runs a file from the worktree, and a
`module:factory` adapter outside the `xorchestrate` package is imported from
the Python path, which can include the repository. Those files can change
without changing the hash. The approval summary lists them as
`repository_code` and `doctor` warns about each one; review them, and the
repository changes you pull, as you would any code you run. `.mcp.json` is
outside `.xorchestrate/` and is not part of the hash either.

Processes xorchestrate starts for itself never take code from the worktree:
the project server is started with `python -P` in the instance directory, and
the release handoff runs the toolchain with `-P`, so a file or directory named
`xorchestrate` in the repository cannot run in place of the package.

## Companions, version control and hosting

The operator layer reaches the companion, git and the companion's host only
through ports (`xorchestrate/ports.py`), with adapters selected by name and
options like every other adapter (a trusted installed `module:factory` works
too). Selections live only in the user's home, never in a project's tracked
`.xorchestrate/`:

| Port | Setting | Built-in adapter |
| --- | --- | --- |
| `Companion` | `settings.json` `companion` (machine default), or a project's `instance.json` `companion`, which takes precedence | `git-directory`, options `{"path": "/abs"}` |
| `VersionControl` | `settings.json` `version_control` | `git` (the git command line), options `executable`, `timeout` |
| `RepositoryHosting` | `settings.json` `repository_hosting` | `github` (the REST API), options `api_url`, `host`, `token_variables`, `token_command`, `timeout` |
| `TerminalHost` | `settings.json` `terminal_host`, with an optional `fallback` selection | `tmux` (default, falling back to `detached-process`) and `detached-process`; see [terminal host](terminal-host.md) |

`companion init|use PATH` writes the machine default; with `--project` it
writes that project's `instance.json`, so one project can keep its records in
a different companion. A `companion` setting that is a plain path (written by
earlier builds) is read as `git-directory` at that path and rewritten in the
adapter shape.

The `Companion` port stores records of one project, or project-independent
ones, as opaque bytes addressed by kind and key. Each operator capability owns
a kind and its format (`overlay`, `approvals` and `telemetry` per project,
`policies` and `launchers` independent of projects); a later capability adds a
kind without changing the port or its adapters. A key is one or more names
separated by `/`, so a capability can keep a tree of records: archived bundles
are `telemetry` records keyed `<instance>/<bundle_id>/<file>`. `keys(kind)`
lists only single-name keys, so the flat kinds ignore subdirectories (a launcher
library in `launchers/lib/`, a parked policy in `policies/disabled/`);
`keys(kind, recursive=True)` lists every key. `revision()`
reports the companion's committed revision (its git `HEAD`), which archived
bundles record; xorchestrate writes companion records but never commits them.

The `github` adapter reads a token from `GH_TOKEN` or `GITHUB_TOKEN`, else
from `gh auth token`, and asks `GET /repos/{owner}/{name}` whether the
repository is private. Without a token, offline, or when GitHub does not show
the repository, it answers `unknown` instead of failing. Its `api_url` must be
`https` (plain `http` only to a loopback host), and it never follows a
redirect, so the token is not sent anywhere else.

The home itself (`~/.xorchestrate` or `$XORCHESTRATE_HOME`) chooses programs
xorchestrate runs, so every command refuses a home that another user could
replace: it must be owned by you and not writable by group or others, and
every enclosing directory owned by you or root and not writable by others
unless sticky. `settings.json`, `registry.json` and `instance.json` must be
regular files (not symbolic links) owned by you with no group or other
permissions.

## Release handoff

The installed `xorchestrate` reads `release` from `project.json`. When it is a
different release, the project commands (`describe`, `call`, `run`, `serve`,
`connect`, `up`, `telemetry`) execute the same command line through
`~/.xorchestrate/toolchains/<release>/`. `down` and `status` only talk to the
server's socket and always run in the installed launcher. The pin must also be
the release this machine's instance was set up or upgraded for
(`instance.json` `release`): a newly pinned release takes effect only through
[`xorchestrate upgrade`](upgrade.md), and until then the other project commands
are refused with `upgrade_required`. A process that was handed a command for one
release never hands it on to another (`handoff_loop`), so releases that
disagree about which one runs a command cannot loop. A missing toolchain is built with uv
first:

1. `uv python install <python>`;
2. a small `pyproject.toml` depending on the release;
3. `uv sync` into `.venv` beside it.

The toolchain is built in a temporary directory under `toolchains/` and renamed
into place, so a partial build is never used; a failed build leaves nothing
behind (`toolchain_failed`, with uv's error output). The command then runs as
`<toolchain>/.venv/bin/python -P -m xorchestrate ...` with
`XORCHESTRATE_HANDOFF=<release>` set; a toolchain that turns out to run another
release is refused with `toolchain_mismatch` instead of handing off again. The
handed-off process removes the marker from its environment, so the tools it
launches never see it. A project that is not set up, or whose `project.json`
is not approved, is refused before any handoff.

`settings.json` may set `uv` (an absolute path; default: `uv` on `PATH`),
`toolchain_python` (default `3.12`) and `toolchain_source`, a PEP 508
requirement with `{release}` (default
`xorchestrate @ git+https://github.com/xormania/xorchestrate@{release}`).
The operator commands (`setup`, `companion`, `init`, `approve`, `doctor`,
`upgrade`, `migrate-config`) run with the installed release.

## Known limitations

- `init` does not build the pinned release's toolchain ahead of time; the first
  project command does.
- The approval hash covers files on disk, including untracked files under
  `.xorchestrate/`.
- The server does not re-render while it runs: an approved change, or an
  overlay change, takes effect when the server restarts.
- `${repo}` is resolved in launched process commands, `cwd` and `env`, not in
  other adapter options; rendering refuses it elsewhere.
- The worktree identity a connection reports is asserted by `connect`, a
  process run by the same user, like the actor.
- If a home directory is so long that the server socket does not fit, the
  socket falls back to a short runtime directory recorded in the server's
  discovery record under `servers/`, not in `instance.json`.
