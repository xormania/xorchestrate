# Companion Repository Specification

| | |
| --- | --- |
| Prepared | 2026-11-09 21:51 UTC |
| Status | Draft specification for the owner's review. Describes a private operator repository and the contract xorchestrate publishes for reading it. Nothing here is implemented. |
| Basis | `xormania/xorchestrate` master at `246206a` (version `0.2.0`). Cited paths are to that revision. |
| Decisions incorporated | One companion repository for all projects. No machine or checkout paths inside it. Briefs and private documents live in it. |
| Related documents | `Release-Specification.md` (versions, compatibility, upgrade procedure), `Instance-Architecture-Changes.md` (the xorchestrate changes that consume this contract) |

---

## 1. Purpose

The companion repository is the single private source of truth for how xorchestrate is deployed across your projects: which release each project runs, how each instance is configured, which launchers wrap your tools, which policies apply, and the private briefs and records that go with them. Xorchestrate stays publishable because it never names this repository. It publishes an **operator source contract**: a directory layout and a handful of JSON shapes. The companion repository is one directory that satisfies the contract.

Five rules follow from that purpose:

1. **Xorchestrate is agnostic of the repository.** The product reads an operator source through one local setting that points at a directory. No code in xorchestrate knows the repository's name, URL or existence.
2. **Path-free.** The repository never contains a machine name, a checkout path or a home directory. Everything location-specific lives in a local registry on each machine and in each repo's instance file.
3. **Secret-free.** Templates name environment variables through `pass_env`; values live in the operator's environment or in each harness's own login store. Nothing in the repository is a credential.
4. **Code only in launchers.** The only executable content is the `launchers/` directory. Everything else is data.
5. **Pinned consumption.** An instance records the companion commit it was rendered from and moves only on an explicit `sync` or `upgrade`. Pulling the companion does not change any running instance.

---

## 2. Scope

| Belongs in the companion | Does not belong |
| --- | --- |
| Desired release per project | Actual pinned release of any instance (recorded in the repo's `.xorchestrate/instance.json`) |
| Configuration templates: store, providers, session drivers, guardrail rules, routing, evaluators, assessment, telemetry identities | Rendered `config.json` files, state databases, evidence, telemetry corpora, virtual environments |
| Launchers wrapping public tools | Copies of xorchestrate or of any vendor tool |
| Shared policy fragments | Secrets, tokens, login caches, `env` blocks with values |
| Private briefs, runbooks, decision records, the architecture proposal | Checkout paths, host names, user names |
| The companion's own CI that validates all of the above against the pinned release | Anything a project repository must commit |

A project repository commits nothing. Its `.xorchestrate/` directory is excluded through `.git/info/exclude`, so the public footprint of using xorchestrate is zero.

---

## 3. Layout

```
xorch-private/
  source.json                 # the operator source: xorchestrate location, default release, interpreter pin
  projects/
    app.json                  # one file per project; file name is the project name
    docs-site.json
  templates/
    app.json                  # product configuration templates with placeholders
    docs-site.json
  policies/
    guardrail-default.json    # shared fragments merged into templates by name
    routing-single-worker.json
  launchers/
    app-tools                 # executables wrapping public tools; the only code in the repository
    assessment-bridge
  locks/                      # optional: uv lock files per toolchain key, for identical resolution across machines
    v0.3.0+telemetry.lock
  briefs/                     # private operator material; not read by xorchestrate
  docs/
  .github/workflows/check.yml # validation against the pinned release (section 8)
  CODEOWNERS, README.md
```

---

## 4. File formats

All JSON files must parse with unique keys and finite numbers, the same rule the product applies to configuration (`xorchestrate/composition.py:47-53`).

### 4.1 `source.json`

```json
{
  "schema": 1,
  "xorchestrate": {
    "url": "git+ssh://git@github.com/xormania/xorchestrate.git",
    "default_release": "v0.3.0"
  },
  "python": "3.12.16",
  "default_extras": ["telemetry"]
}
```

| Field | Meaning |
| --- | --- |
| `schema` | Operator source schema version. Each xorchestrate release declares the schema versions it reads. |
| `xorchestrate.url` | Where releases are fetched from. A git URL with tag pins; a package index is also acceptable later. |
| `xorchestrate.default_release` | Used by `instance init` when a project file omits `release`. |
| `python` | Interpreter version uv installs for every toolchain. `3.12.16` satisfies the telemetry collector's SQLite requirement (`docs/telemetry.md:15-18`). |
| `default_extras` | Installation extras applied when a project file omits `extras`. |

### 4.2 `projects/<name>.json`

```json
{
  "release": "v0.3.0",
  "template": "templates/app.json",
  "extras": ["telemetry"],
  "policies": ["policies/guardrail-default.json", "policies/routing-single-worker.json"],
  "launchers": ["launchers/app-tools"],
  "requires": ["docker", "tmux", "codex"],
  "briefs": ["briefs/app/owner-brief.md"],
  "notes": "Python service; single Codex worker; telemetry on."
}
```

| Field | Meaning |
| --- | --- |
| `release` | Desired xorchestrate release tag for this project. Omit to inherit `default_release`. |
| `template` | Path inside the companion to the configuration template. Required. |
| `extras` | Installation extras for the toolchain. Omit to inherit `default_extras`. |
| `policies` | Ordered fragments merged beneath the template (section 5.3). |
| `launchers` | Launchers this project's template references. Each is copied into the instance at render time (section 5.4). |
| `requires` | Executable names or launcher-relative paths that `doctor` must find on the machine. Readiness observations, not installs. |
| `briefs` | Documents for humans and for tools you connect; xorchestrate does not read them. |
| `notes` | Free text. |

The project name is the file name without extension and must match `^[a-z][a-z0-9-]{0,63}$`.

### 4.3 Templates

A template is an ordinary product configuration (`docs/hexagonal-architecture.md:98-131`) with two additions: a `version` field for the configuration format, which the release specification introduces, and a closed set of placeholders. Everything else is literal and must be valid as written.

```json
{
  "version": 1,
  "store": {"adapter": "sqlite", "options": {"path": "${data}/state.sqlite3"}},
  "telemetry": {"adapter": "local", "options": {
    "runtime_root": "${data}/telemetry",
    "project_id": "app", "dataset_id": "agent-work", "collector_id": "${instance}"}},
  "providers": {
    "project": {"adapter": "mcp", "options": {
      "command": ["${launchers}/app-tools", "serve"], "cwd": "${repo}",
      "pass_env": ["PATH", "HOME", "PROJECT_TOOL_TOKEN"], "timeout": 30}}
  },
  "sessions": {
    "worker": {"adapter": "codex", "options": {
      "command": ["codex", "app-server", "--listen", "stdio://"], "cwd": "${repo}",
      "pass_env": ["PATH", "HOME", "CODEX_HOME"], "application_tools": true, "timeout": 120}}
  }
}
```

Template rules, all checked by `instance check-source`:

- No absolute paths. Relative paths are permitted and resolve against the rendered config's directory, which is the product's existing behaviour (`composition.py:110-140`), but `${repo}` and `${data}` are preferred for clarity.
- No `env` blocks carrying values and no `inherit_env: true`. Only `pass_env` names.
- Launchers are referenced only through `${launchers}`. A template must not point at the live companion checkout, so that an instance stays pinned to what it rendered.
- `enabled: false` entries are allowed and remain disabled after rendering.
- Unknown placeholders and placeholders outside the fields listed in section 5.2 are refusals.

### 4.4 Policy fragments

A fragment is a partial product configuration holding one or more top-level sections, typically `guardrail`, `routing`, `acceptance`, `session_selection`, `delivery_scheduler`, `retry_policy`, `receipt_policy` or `state_engine` options. Fragments carry no placeholders.

### 4.5 Launchers

Executables with a shebang or compiled binaries, mode `0755`, no secrets, reading credentials only from environment variables forwarded by `pass_env`. A launcher may be a thin wrapper around a public MCP server or CLI. Launchers are the trusted code of the deployment and are reviewed like code.

### 4.6 `locks/`

Optional. When present, `locks/<release>+<extras>.lock` is the `uv.lock` used for that toolchain key on every machine, giving identical transitive resolution. When absent, each machine resolves at `init` and keeps its own lock inside the toolchain directory.

---

## 5. How xorchestrate consumes it

### 5.1 Location

One local settings file selects the operator source:

```json
// ~/.config/xorchestrate/settings.json
{"schema": 1, "source": "/home/x/src/xorch-private"}
```

`--source PATH` overrides it per command. The companion is an ordinary clone; the operator updates it with git. Xorchestrate reads the working tree and records the commit it read.

### 5.2 Rendering

`instance init` and `instance sync` render a project's template into `<repo>/.xorchestrate/config.json`:

1. Load the project's fragments in order, then the template; deep-merge with later documents overriding earlier ones, so the template wins over shared policy.
2. Substitute placeholders. The set is closed:

   | Placeholder | Value | Allowed in |
   | --- | --- | --- |
   | `${repo}` | Absolute path of the project repository root | path-valued options and command arrays |
   | `${data}` | The instance data directory, outside the repository | path-valued options |
   | `${launchers}` | `${data}/launchers` | `command` arrays and path-valued options |
   | `${home}` | The operator's home directory | path-valued options |
   | `${instance}` | The instance id | telemetry identity fields (`collector_id`, `producer_id`) only |

3. Copy each listed launcher into `${data}/launchers/`, mode `0755`, and record its SHA-256 in `instance.json`.
4. Validate the result with the product's configuration validation at the target release, without constructing adapters or launching processes.
5. Write `config.json` and update `instance.json` with the companion commit, template path, template hash and launcher hashes.

Rendered output is generated and never hand-edited; `instance status` reports when the rendered config no longer matches its template and commit.

### 5.3 Commands that read the source

| Command | Reads | Writes |
| --- | --- | --- |
| `instance init <repo> --project NAME` | `source.json`, `projects/NAME.json`, template, fragments, launchers | toolchain if missing, data directory, `.xorchestrate/`, local registry entry |
| `instance sync [<repo>]` | same, at the current companion commit | re-rendered `config.json`, refreshed launchers; refuses while the instance is in use |
| `instance upgrade [<repo>] [--to TAG]` | `projects/NAME.json` for the desired release | new toolchain, migrated data, flipped pin; see the release specification |
| `instances status` | every project file plus the local registry and each `instance.json` | nothing |
| `instance doctor [<repo>]` | `projects/NAME.json` `requires` and launcher list | nothing |
| `instance check-source [PATH]` | the whole operator source | nothing; exit status is the verdict |

Instances never follow the companion's head implicitly. The only path from a companion change to a running instance is `sync` for configuration and `upgrade` for releases, both explicit and both recorded in `instance.json`.

### 5.4 Provenance recorded per instance

`instance.json` holds `source.commit`, `source.template`, `source.template_sha256` and `launchers[].sha256` alongside the release pin and resolved SHA. Reproducing an instance on another machine is `instance init` with the same project at the same companion commit; the only inputs that differ are the local paths the registry supplies.

---

## 6. Secrets and trust

- Templates and fragments carry variable **names** only. `doctor` reports whether each named variable is set, never its value.
- Harness authentication stays in each harness's own store (`CODEX_HOME`, Claude's and Grok's login state). The product never writes vendor authentication, and neither does the companion.
- The companion is trusted executable input because of `launchers/`. Keep the repository private, protect its default branch, require review, and keep the local clone at mode `0700` outside any directory a harness session uses as `cwd`.
- Rendered configuration is trusted local code selection, as the product documents (`docs/hexagonal-architecture.md:142-146`). The companion is where that trust is exercised deliberately and reviewed.

---

## 7. Change management

- Every change is a pull request, even with one author. The repository history is the audit trail for each organizational, policy and infrastructure decision.
- A release rollout is one pull request editing `release` in the chosen `projects/*.json` files, then `instance upgrade` per repository at the operator's pace. `instances status` shows the gap between desired and pinned while the rollout is in progress.
- A policy change is one pull request editing a fragment or template, then `instance sync` per repository. Both commands refuse while an instance is in use and record what they changed.
- `CODEOWNERS` names the owner for `launchers/` and `templates/` so a review is required where code or trust changes.

---

## 8. Validation in the companion's own CI

The companion runs xorchestrate as a tool, at the release each project pins:

```yaml
- run: uvx --python 3.12.16 --from "git+ssh://git@github.com/xormania/xorchestrate.git@v0.3.0" \
       xorchestrate instance check-source .
```

`check-source` validates: `source.json` schema and release tag format; every project file, including that its template, fragments, launchers and briefs exist; every template against the configuration format version the pinned release accepts, with placeholders substituted by sentinel values; placeholder use only in permitted fields; no absolute paths, `env` values or `inherit_env`; launcher executability and shebangs; and a secrets scan over the whole tree. When projects pin different releases, the job runs once per distinct release. A template that will not render under the release it pins fails here, before it reaches any repository.

---

## 9. Multiple machines

Because the companion is path-free, a second machine needs the companion clone, the settings file, and `instance init` per repository. The local registry at `~/.config/xorchestrate/registry.json` maps instance ids to repository paths on that machine only; it is a cache and can be rebuilt by re-running `init`. Telemetry collector identities come from `${instance}`, so each machine's instance has its own lineage, which the telemetry guide requires (`docs/telemetry.md:21-24`).

---

## 10. Worked example

Project file `projects/app.json` as in section 4.2, template as in section 4.3. On a machine where the repo is at `/home/x/src/app`, `instance init /home/x/src/app --project app` produces:

```
/home/x/src/app/.xorchestrate/
  instance.json      {"schema":1,"id":"7f3c…","project":"app","release":"v0.3.0","sha":"8a8e…",
                      "toolchain":"/home/x/.local/share/xorchestrate/toolchains/v0.3.0+telemetry",
                      "data":"/home/x/.local/state/xorchestrate/7f3c…",
                      "source":{"commit":"c0ffee…","template":"templates/app.json","template_sha256":"…"},
                      "launchers":[{"path":"launchers/app-tools","sha256":"…"}],
                      "config_version":1,"data_version":1,"rendered_at":"…","upgrades":[]}
  config.json        the template with ${repo} = /home/x/src/app, ${data} = /home/x/.local/state/xorchestrate/7f3c…,
                     ${launchers} = that data directory's launchers/, ${instance} = 7f3c…
  bin/xo             exec flock -s "$DIR/run.lock" "<toolchain>/.venv/bin/python" -m xorchestrate --config "$DIR/config.json" "$@"

/home/x/.local/state/xorchestrate/7f3c…/   state.sqlite3, telemetry/, launchers/app-tools, backups/
/home/x/.config/xorchestrate/registry.json  {"schema":1,"instances":{"7f3c…":{"repo":"/home/x/src/app","project":"app"}}}
```

Nothing in `xorch-private` changed. Nothing under `/home/x/src/app` is tracked by git.

---

## 11. Open items

1. **Briefs.** You have existing projects that produce briefs. The contract only reserves a place for them and a `briefs` list in the project file; how a brief reaches a session, through a provider the template connects or through a message, is for those projects to decide. Nothing in sections 4 to 8 depends on the answer.
2. **Telemetry identities.** `project_id` and `dataset_id` are literal in templates and `collector_id` comes from `${instance}`. If you want a shared corpus across machines instead of one lineage per instance, that is a template choice, not a contract change.
3. **Lock files.** Whether to commit `locks/` for identical resolution across machines, or let each machine resolve at `init`. Committing them is the safer default once more than one machine exists.
4. **Operator source schema evolution.** Each xorchestrate release declares the `source.json` schema versions it reads; the release specification covers how that is announced.

---

Document finalized 2026-11-09 21:51 UTC. Specification and recommendations only; no repository changes were made.
