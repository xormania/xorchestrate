# Providers for prod-lane development

The dev-lane MCP owns workflow, live communication, identity and source receipts.
Serena v2 beta supplies discovery and edits of the Symfony **prod-lane**. This
provider is not intended for developing `ops/` or `.dev/`. Mate, Makers, checks,
dependencies and Git belong to their named project operations, not Python cells.
The optional Symfony integration below supplies Mate and checks. Dependency and
Git operations remain incomplete in readiness stage 4.

## Install and select

Create an isolated installation outside the project. Do not replace an operator's
existing Serena environment. From the repository root, for example:

```sh
uv venv --python 3.13 /absolute/serena-v2
uv pip install --python /absolute/serena-v2/bin/python \
  -r ops/devlane/providers/serena-v2.txt
python3 -B ops/conductor/tools/sessions.py start --member NAME --mcp-only \
  --serena-python /absolute/serena-v2/bin/python
```

Git, Node and npm are backend dependencies. The core dev-lane stays stdlib-only
and requires no Symfony kernel, Composer vendor directory or Serena package.
The provider checks Serena's distribution version and installed VCS commit
against the manifest before starting. Keep the virtualenv's `bin/python` path;
resolving its symlink would select the base interpreter without the installation.

`profiles.py prepare-interactive` accepts the same provider option. The private
profile continues to register one MCP entry, named `coordination`. Serena is a
child of that entry. A missing provider is reported during discovery without
disabling live coordination. Select the option again for a replacement member.

Intelephense 1.14.4 is the selected PHP backend. Serena installs it into its
private home on first use; allow time for installation and indexing. The provider
sets `SERENA_USAGE_REPORTING=false` and disables Intelephense's bundled
Application Insights 2.7.3 telemetry and Statsbeat through
`APPLICATIONINSIGHTS_CONFIGURATION_CONTENT`. npm audit, funding and update
notifications are disabled. These are upstream configuration switches, not
upstream code edits. Normal package downloads still use the configured registry.
Operators can supply `NPM_CONFIG_CACHE` and `NPM_CONFIG_OFFLINE=true` to use a
populated cache without registry access.

## Work through the broker

Discover `code_status`, `code_repl` and `code_restart` with `capability_search`,
then load their schemas with `capability_describe`. Invoke them using
`capability_invoke`. Initial discovery remains three tools / 1,154 compact JSON bytes.

1. Invoke `code_status` with empty arguments to obtain the trusted worktree,
   participant, optional assignment binding, source fingerprint and provider epoch.
2. Invoke `code_repl` with `code`, `expected_source_fingerprint` and
   `expected_epoch` from that status. Start with `s.info('lsp.find_symbol')` or
   other selected API documentation. Retain objects in REPL variables and return
   small selected results.
3. Inspect the outcome, changed paths and source-event identifier. Use the
   returned fingerprint and epoch for the next cell, or refresh `code_status`.
   A source change by a peer requires inspection before another edit.
4. Use `code_restart` with the current epoch when needed. It reports loss of
   variables; assignments and messages remain in the lane store.

Caller arguments cannot select a participant, workspace, executable or Serena
session. A trusted `dev_mcp.py --binding FILE` can attach an existing assignment;
an ordinary interactive profile has no assignment-specific binding and reports
`assignment: null`. Direct work requires no new permission ceremony. A changed
binding requires a new entry process so temporary state cannot cross assignments.

## Source records and failures

Every cell invalidates current-source check receipts **before** execution, even
discovery cells: arbitrary Python cannot be assumed read-only. The existing lane
SQLite store records participant, optional assignment, epoch, fingerprints,
changed paths and outcome. A short worktree lock serializes source operations
without holding the coordination database during the cell.

Fingerprints include HEAD, tracked files, nonignored untracked files, deletions,
modes and symlink targets; ignored runtime data is excluded. Historical CI records
stay historical. Named Symfony checks now use the same source receipts.

At this beta pin, Python exceptions can be ordinary MCP text. The adapter
recognizes their location format and records partial edits. Failed transport
never causes automatic replay. A failed final source scan retains the event as
`source_unknown`, leaves verification stale and returns no invented fingerprint.
Inspect and repair the worktree. Namespace eviction reports lost variables before
the next requested cell runs. Bounded replies retain the source-event identifier;
the complete changed-path list remains in `source_events`.

Private logs, configuration and indexes live outside the product worktree under
the lane's state directory. Ending the entry stops its owned provider/LSP process
group. Serena's configuration ignores dev-lane directories and disables its shell
tool. Arbitrary Python still has provider-process permissions: this is **not a
filesystem, credential or network sandbox**. Environment filtering omits harness
identity, lane configuration, publication credentials and SSH agents; it cannot
prevent the same OS user from reading other accessible files. An observed edit
outside the intended lane is reported, not silently rolled back.

## Evidence and remaining acceptance

`test_code_work.py` exercises real pipes with a synthetic provider: isolated
namespaces, bindings, stale source, partial failures, lost state, bounded output
and source accounting. `test_mcp_only.py` checks provider selection across profile
projections while keeping one entry. Neither is native-harness evidence.

The optional real-provider check uses disposable PHP service files:

```sh
python3 -B ops/devlane/providers/serena_php_smoke.py \
  --serena-python /absolute/serena-v2/bin/python
```

On 2026-09-26 it passed with Serena commit
`b79e2a55f9d4072084977dd35bd07d31146d88b9`, Python 3.13.15 and Intelephense 1.14.4.
The real dev-lane → Serena MCP route demonstrated PHP class/reference discovery,
an edit/read, persistent variables, edit-then-error accounting, stale-check
invalidation and restart with an unchanged assignment. It recorded eight source
events, using npm offline and both usage-reporting paths disabled. No PHP runtime
test was performed.

Stage 3 still requires these operations from an installed native participant.
The complete workflow also needs native restrictions/live communication acceptance,
remaining project actions and a useful prod-lane task. See the
[readiness stages](../../../docs/dev-lane-mcp-readiness.md).

## Symfony and Mate

Install the reviewed Composer lock with PHP 8.5.11 or later on the selected 8.5
runtime. Symfony FrameworkBundle is locked at 8.1.7, and Mate and its Symfony
extension at 0.14.0. MCP Bundle 0.14.0 supplies native MCP
attribute discovery. MakerBundle 1.68.0 is available in dev/test only. Stopwatch
supports the selectively enabled framework profiler; no profiler UI is installed.

Select the runtime in an operator-owned file outside the worktree:

```json
{"version": 1, "provider": "symfony", "php": "/absolute/php", "environment": "dev"}
```

Pass that file when starting a participant:

```sh
python3 -B ops/conductor/tools/sessions.py start --member NAME --mcp-only \
  --serena-python /absolute/serena-v2/bin/python \
  --project-config /absolute/project-runtime.json
```

The profile copies and pins this configuration. Only `dev` and `test` are
supported. There is still one MCP entry with three startup tools (1,154 compact
JSON bytes). Discover capabilities on demand:

| Capability | Existing mechanism |
| --- | --- |
| `project_context` | Actual PHP/Composer versions, Mate's project instruction aggregator and version-selected references |
| `mate_skills`, `mate_skill` | Mate's existing skill discovery and enabled source/override, loaded on demand with provenance |
| `project_status` | Shared worktree identity, source receipts and prerequisite checks |
| `mate_tools`, `mate_describe` | Mate's tool catalogue and parameter schemas |
| `mate_environment` | Mate `server-info` |
| `mate_services`, `mate_service` | Mate's compiled-container inspection |
| `mate_profiles`, `mate_profile`, `mate_resource` | Mate's captured-request tools and resources |
| `project_output` | Bounded pages of the last four large results in this entry session |
| `project_refresh` | Symfony `cache:clear` for the selected environment |
| `project_check` | Symfony `lint:container` and a nonempty PHPUnit suite |

Refresh and checks require the current `expected_source_fingerprint` from
`project_status` or `code_status`. They invalidate earlier checks before running;
successful checks record the actual source and runtime inputs. Partial failures
retain source accounting. Commands are fixed by the provider, bounded by time and
output size, and receive no workflow/publication credentials from the parent
environment. They execute trusted project code with the host user's permissions;
they are not a sandbox. There is no model-provided command or arbitrary argv.

Mate inspection does not boot the application to manufacture missing evidence.
Missing vendor, container or profiler data is reported while coordination remains
available. `project_refresh` explicitly boots the kernel and writes its cache.
Kernel compilation can regenerate Symfony's tracked `config/reference.php`; such
source changes are reported and invalidate prior checks. Inspect the new source
fingerprint and refresh again. Container observations report `unavailable`,
`unverified`, `stale` or `current`;
inspect that field before relying on a service result. A refresh receipt covers
source, dependency installation, environment inputs, PHP and compiled artifacts.
Captured profiles remain historical and are not claimed to describe current
source. Enable profiling for the scenario being exercised. Mate's redaction and
untrusted-data annotations are retained. Mate 0.14 service detail includes actual
constructor arguments and service references as well as tags and definition flags.

MCP initialization and `code_status` direct agents to `project_context` before
product edits. That response separates installed features from observed wiring.
`mate_skill` reads the enabled package source or the selected project override,
not a possibly stale generated harness copy. It reports both provenance and the
generated copy's status. Skill bodies retain upstream examples; in an MCP-only
session use the corresponding named operations. Fresh entries reconstruct this
information from the selected worktree and dependencies rather than a private
knowledge store. Context remains guidance; design review and native task rehearsal
must establish whether an agent uses it effectively.

For product MCP capabilities, use `#[McpTool]` directly on a suitable service
method. Existing `autowire: true` injects dependencies and `autoconfigure: true`
registers the MCP attribute. The bundle owns schemas and invocation. Serena edits
these services. A wrapper is justified only by a concrete interface difference.
There is no custom Maker or generation capability in this change: native features
cover the selected case. Add one only for a demonstrated recurring structure that
existing Makers do not cover. This follows the application's PHP/Symfony guidance
in [AGENTS.md](../../../AGENTS.md#application-guidance).

Run the real integration smoke after installing the lock:

```sh
python3 -B ops/devlane/providers/symfony_smoke.py --php /absolute/php
```

It uses a disposable project and the actual Mate/MCP packages. On 2026-09-26 it
passed with PHP 8.5.11: context and installed-skill delivery over MCP, discovery,
constructor-wiring inspection, stale configuration and refresh,
an attributed service with an autowired dependency invoked by the MCP SDK, an
actual HTTP request/profile, redaction, and a planted behavior failure. The same
service handles both MCP and HTTP. No wrapper or generated service configuration
is needed. A separate Symfony integration CI job runs this smoke when application
or integration-provider sources change. Product CI remains independent of `ops/`;
ordinary dev-lane-only changes do not require PHP or vendor. Python fixtures cover missing PHP or
vendor, bindings, partial writes, bounded output and process cleanup. These checks
do not claim installed native-harness or complete local workflow acceptance.

The prod CI selects the latest PHP 8.5 patch on hosted runners. Self-hosted
Linux/X64 jobs use a checksum-pinned PHP 8.5.11 binary in their temporary
directory, leaving the host's global PHP unchanged. Both enforce Composer platform
requirements. Its artifact records the locked baseline and available direct
dependency updates. The current-version check on 2026-09-26 used
[PHP releases](https://www.php.net/releases/) and
[Symfony releases](https://symfony.com/releases); Composer reported no newer direct
dependencies after this update. The existing Composer Dependabot job remains
paused pending its empty path-repository issue; this change does not claim an
automated future upgrade process.
