# Operator reference

> Historical operator procedures for the imported `devtools` runtime. These are
> not setup instructions or policy requirements for the current Xorchestrate
> application. See the [current README](../README.md).

Use this guide to configure, inspect or repair the local control plane. For a new
installation, start with [local setup](../docs/local-agent-readiness.md). For normal
agent work, use the [role/tool reference](../docs/control-plane-roles-and-tools.md).

The operator connection exposes backing services directly. It is not part of a
managed agent profile; CO/XO tools normally create work, bind roles and launch the
team without operator calls.

## Team and project configuration

`init` writes `team.json` in the private state directory. Edit that file to configure
an existing team. A complete project setup has this shape (replace local paths):

```json
{
  "co": "owner",
  "members": [
    {"name": "owner", "harness": "external", "workspace": "/home/work/projects/agentscient"},
    {"name": "chief", "harness": "codex", "binary": "codex", "workspace": "/home/work/projects/agentscient"},
    {"name": "worker", "harness": "claude", "binary": "claude", "workspace": "/home/work/projects/agentscient"},
    {"name": "reviewer", "harness": "grok", "binary": "grok", "workspace": "/home/work/projects/agentscient"}
  ],
  "project": {
    "checkout": "/home/work/projects/agentscient",
    "repository": "uscient/agentscient",
    "target": "dev",
    "remote": "origin",
    "development": {},
    "serena": {"image": "agentscient-serena:v2-beta"}
  }
}
```

Member names identify reusable slots. CO is the configured external user-facing
interface: it relays xor's instructions/context to XO and system messages back to
xor. XO owns planning, execution, coordination and acceptance. For fixed-member
teams, at least two other slots support XO and a producer. A third allows concurrent
audit. Shared runtime teams allocate seats from `roles.json` capacity. Multiple slots
can use the same installed harness. External slots connect their own MCP clients.
Each managed collaborator is a standalone native session. Agentscient owns its
creation and coordination; managed collaborators do not use harness-native
subagents. The appointed interactive CO is the operator's own session and retains
native commands, model/reasoning selection, tools, settings, delegation and exit
controls. This also applies to configured role aliases with CO behavior. Its
Agentscient MCP authority and interface responsibility remain role-bound.

Each managed slot accepts optional `model` and `reasoning` strings and a `binary`
path. Model selection is not hardcoded. Active sessions keep their startup settings;
configuration changes apply to later starts. Existing stored members cannot change
harness or starting workspace in place; use a new member name for that change.

| Project setting | Meaning |
| --- | --- |
| `checkout`, `repository`, `target`, `remote` | Existing Git root, GitHub identity, integration branch and remote. |
| `workspaces` | Managed worktree parent; defaults to `../wt-ag/` beside `project.checkout`, outside source and private state. Explicit configuration is preserved. |
| `fetch` | Defaults to true; set false explicitly for an offline local target. |
| `identity` | Optional commit identity with `name` and `email`; otherwise use Git's configured identity. |
| `paths` | Optional map of editing roles to allowed repository-relative paths. Defaults are defined in [role-packages.cue](role-packages.cue). |
| `assignment_policy` | Operator-owned JSON file, absolute or relative to team state; defaults to [assignment-policy.json](assignment-policy.json). Configures protected source, default read context, denied Makers and allowed Console introspection commands. |
| `development` | `{}` enables installed Makers/Mate and default validation. |
| `development.php`, `development.composer` | Optional installed binaries. An absolute PHP path adds its directory to PATH for CLI tools. |
| `development.checks` | Optional map of check names to fixed argv arrays; agents supply a name, never argv. Defaults are container/YAML lint and PHPUnit. |
| `development.check_timeout` | Timeout in seconds for each check; default 600. |
| `serena.image` | Locally built separate Serena provider image. |
| `serena.docker`, `serena.memory` | Optional Docker binary and container memory limit; default memory is `2g`. |
| `configuration_file` | One private JSON overlay for provider connection, independent feature switches, assessment rubrics, filtering, guidance and completion. Absolute or relative to team state; omitted means maintained defaults. |

Each work order and job owns its worktree and Composer install. Managed startup
selects it automatically. Dependency installation refreshes when Composer metadata
changes. Do not share a vendor-directory symlink across worktrees.

### Role capability packages

[Role packages](../docs/role-capability-packages.md) define each role's MCP ceiling,
skill references, private hooks, workspace eligibility and execution options.
Operator `roles.json`/`vendors.json` and fixed-team settings retain their current
format. Configured profiles must fit the package; models and reasoning levels are
not hardcoded. Maintain the CUE definition and checked-in JSON export together.

### Native login storage

Managed worker profiles isolate harness settings from the operator's configuration.
Codex and Grok reuse file-based `auth.json` login data and retain private refreshes across
generations for the same source home; changing that home or replacing the source
login takes precedence. Codex also preserves its
native login-method and workspace constraints. Native authentication and provider
consent still apply; a copied file does not prove that the account is authenticated.

For managed Codex workers, `keyring` and `auto` storage cannot be copied into a
different `CODEX_HOME`: the native keyring identity depends on that path, and an
existing `auth.json` may belong to a stale account. Worker readiness and profile
creation report this explicitly.
Keep a global keyring login if desired and create a separate file-backed login home
for Agentscient using Codex's native login command. For a new directory:

```sh
codex_login_home="$HOME/.local/state/agentscient-codex-login"
mkdir -m 700 "$codex_login_home"
printf '%s\n' 'cli_auth_credentials_store = "file"' > "$codex_login_home/config.toml"
chmod 600 "$codex_login_home/config.toml"
CODEX_HOME="$codex_login_home" codex login
CODEX_HOME="$codex_login_home" python3 devtools/run.py doctor --state "$HOME/.local/state/agentscient-live"
```

Use that source home in the environment of the service launching managed Codex
sessions. The worker launcher does not modify the original login or configuration.
Codex `ephemeral` mode stays ephemeral: file credentials are neither imported nor
retained. The operator must supply the native session's authentication in that mode.
Independent model providers use their configured native credentials instead of an
OpenAI account. Their profiles skip account-file reuse; the effective runtime
provider override is honored during both readiness and profile creation.

The interactive CO preserves native operator settings rather than inheriting the
managed worker profile restrictions. Codex CO uses the operator's normal
`CODEX_HOME`, so it can use the existing keyring or auto credential storage. A team
that also selects managed Codex workers still needs a supported worker login source.
See [CO startup and handover](../docs/session-handover.md) for native controls.

### Planning and NCO setup

Runtime policy names must match the maintained role catalog. Configure ordered
profile preferences for the planner, command and specialist roles in `roles.json`,
and vendor availability in `vendors.json`. Profile names select the installed
harness, model and reasoning settings. Fixed teams reuse their configured idle
slots. Role definitions and the catalog determine available roles; there is no
separate accepted legacy role set.

XO commissions the work-order planner and adopts its job list. One job is
coordinated directly by XO; multiple jobs each have a job-bound NCO. Each active
NCO needs a live slot in addition to XO and its specialists. Planner and specialist
can reuse a slot after plan adoption completes the planning assignment and its
session stops. NCO assigns specialists within its job and reports readiness to XO;
only XO commissions planning, changes job scope and accepts jobs. Runtime allocation
and session lifecycle remain in the existing command services.

A job is an outcome, with several retained role assignments if needed. One active
editor uses its workspace at a time; readers and separate jobs can overlap.
Completing a role assignment releases its session without accepting the job or
discarding results. CO can attach prepared specification/scope documents through
the work-order's explicit shared context references. Agents never choose worktree
paths, branch names or PR targets; MCP services retain those responsibilities.

Copy `devtools/assignment-policy.json` to customize paths and inspection policy;
set `project.assignment_policy` to that file. Add project-specific Maker source
and template locations to `protected_paths`. Role path configuration remains the
upper bound for job `editable_paths`; assignment fields cannot widen it. The
default blocks `make:maker` and protects `src/Maker`, `templates/maker` and dev-lane
source. Agents still discover and invoke other installed Makers through MCP.
Existing directories are recognized by their filesystem type. For missing edit
paths, a trailing slash declares a directory (`src/NewFeature/`); other explicit
paths declare files, including extensionless files (`src/NOTICE`). Serena prepares
only those assigned files, without widening the assignment to their parent.
Use the trailing slash for missing directories in operator role paths as well.

Directories containing protected descendants remain read-only; their allowed
existing children receive writable mounts. This also protects absent Maker paths
without inventing checkout files. To add a new sibling directly in such a
directory, list its exact path in the assignment or generate it through the
maintained Maker. Serena refreshes its mounts after generation or Git replacement.

`context_paths` selects the source presented to Serena when supplied in an
assignment; explicit edit paths use the policy's read-context defaults unless
overridden. The full backing worktree remains available to native Maker, Mate,
Console and validation services. This is a curated editing view and a path-level
write boundary. It does not enforce individual generated method bodies or freeze
PHP signatures within an editable file. Plans and review carry those obligations.

The adopted plan records its exact work-order objective, jobs and role assignments.
Changing it preserves plan history and requires a deliberate handoff for affected
sessions. Reports do not become approved plans merely because they exist. See the
[handoff example](../docs/control-plane-roles-and-tools.md#planning-and-bounded-handoffs).

### Configuration and local Jev credentials

The maintained defaults have one editable source, `configuration.cue`, and one
checked-in runtime export, `configuration.json`. Runtime needs only Python and the
export; CUE is a maintainer generation and CI check dependency. The private
`team.json` selects one operator overlay through `project.configuration_file`:

```json
{"configuration_file": "configuration.json"}
```

Create that file in the team state directory. This example selects a local key
file, keeps the maintained decision features enabled, and enables direct assessment tools:

```json
{
  "version": 1,
  "project": {
    "features": {
      "selection": true,
      "advice": true,
      "filtering": true,
      "guidance": true,
      "completion": true
    },
    "connection": {
      "credential": {"file": "~/.config/agentscient/secrets/typesafe.key"}
    }
  },
  "roles": {
    "research": {"decisions": {"rules": {"context_relevance": {"noul": {"reject_below": 0.1}}}}}
  },
  "jobs": {}
}
```

Obtain a key from your [TypeSafe account](https://console.typesafe.ai/keys). Use a
separate local key if you want independent rotation from CI. Save only the key,
optionally followed by a newline, in the referenced file outside source worktrees.
Give the directory mode `0700` and the key file `0600` or `0400`.
The key never belongs in the overlay, `team.json`, agent arguments or repository.
The service reads it directly when needed, without exporting it to child processes.
File replacement takes effect on the next credential read; it needs no service
restart. Symlink key files, nonregular files and group/world-readable files are
rejected. `.env` files are not loaded implicitly.

Alternatively select `{"env":"TYPESAFE_API_KEY"}` as the entire credential
reference. Supply that variable to the process launching the services and restart
existing processes after changing it. Exactly one `env` or `file` selector is
accepted. GitHub Actions secrets supply CI only; local services require a local
source. A relative key path resolves from the overlay's directory; `~` expands to
the service user's home. The shipped provider adapter is `typesafe`; its HTTPS
endpoint and pinned model version are configurable. Another wire protocol needs
an installed adapter implementation.

The overlay uses **defaults → project → role → job** precedence. Role selectors
must exist in the role catalog; job selectors are string forms of actual retained
job IDs, such as `"12"`. Services select them from the authoritative binding.
Objects merge recursively; lists, the scope question map and credential selectors
replace as complete values. Every declared override is validated, including inactive combinations;
unknown fields, unknown role/job/skill references, conflicting bounds and stale
exports are rejected. Empty `roles` and `jobs` sections may be omitted.

| Section | Configurable behavior |
| --- | --- |
| `features` | Independent `selection`, `advice`, `filtering`, `guidance`, `completion` booleans. Defaults enable selection, filtering, guidance and completion; `advice` controls optional direct assessment tools and remains disabled. |
| `readiness` | `timeout_seconds` (integer 1–300, default 30) for duty acknowledgements. Initial standby shares this deadline with preflight; preflight receives at most 10 seconds of the remaining budget. Project, role and job overlays use the normal precedence. |
| `decisions` | Configured Arbitrator role, reserved sessions, provider failure handling, unresolved Choice labels, Noul/Choice/Score thresholds and per-purpose overrides. |
| `connection`, `model`, `transport` | Installed adapter, HTTPS endpoint, credential reference, model, timeouts, attempts, retry statuses/delays and payload limits. |
| `scope`, `assessment` | Mission/scope rubric, source/question counts, source excerpt limits and assessment rules. |
| `assignment` | Role/runtime selection rubrics, eligible role names, candidate limits and configured performance evidence. |
| `filtering` | Eligible report kinds, protected text pattern, excluded role/adapter sets, fact/state budgets, relevance question and context-preservation behavior. Decision thresholds live under `decisions.rules.context_relevance`. |
| `guidance` | Excluded role/adapter sets, diagnostic limits, ordered regex/role/adapter/action routes, action instructions, recovery templates and count limit, fallback skill/action, delivery excerpt size and failure mode. |
| `completion` | Three boundary rules, independent proof questions and evidence budgets. Decision thresholds live under `decisions.rules.completion`. |

`assignment.profile_evidence` maps configured runtime profile names (fixed teams:
member names) to dated evidence text. Include task family, harness/model/reasoning,
result and source references. Missing evidence remains unknown; benchmarks are not
invented. Selection may choose only an eligible available profile from the current
runtime policy; it does not edit that policy.

Inspect resolved settings without reading a key or contacting TypeSafe:

```sh
python3 devtools/run.py config --state "$HOME/.local/state/agentscient-live"
python3 devtools/run.py config --state "$HOME/.local/state/agentscient-live" --role research
# Add --job with an existing job number to inspect its final override.
python3 devtools/run.py doctor --state "$HOME/.local/state/agentscient-live"
```

The config output includes the selection and effective revision, with credential
contents excluded and the source reference visible. `doctor` validates configuration
and checks required credential sources locally. It does not send a Jev request or
establish provider authentication. The operator-only `config_status` MCP capability
provides the same effective-settings inspection; agents do not select policies.

CO's `roll_call` reuses `doctor --startup` in a bounded child process before the
initial XO launch. This mode checks configured project, Symfony and Serena
capabilities; teams without them do not acquire those prerequisites. It performs
no online GitHub/Jev authentication request. Timeout kills the probe process
group and gives the worker at most one second to exit. Detached descendants
cannot hold the response open; their termination is not certified. The failure
is retained. Initial standby reserves ordinary
collaborator capacity and grants only readiness tools; it is separate from work
bindings. The existing MCP lifecycle watcher launches it and expires missing
acknowledgements. Keep CO's MCP connection active during the check.

Readiness requests and receipts persist in team state and emit `readiness_*`
telemetry. Each receipt is tied to its member, session generation, role/context
and onboarding revision. Restarted or reassigned sessions need fresh evidence.
See [reporting for duty](../docs/control-plane-roles-and-tools.md#reporting-for-duty)
for the normal MCP sequence and the exact limits of a ready result.

Policy files are resolved at each operation. Effective revisions identify cached
evaluations and existing telemetry; a policy change during an assessment prevents
using its obsolete result for filtering or completion. Native session model and
harness settings still apply at session startup. To edit maintained defaults:

```sh
python3 -m devtools.configuration --write
python3 -m devtools.configuration --check
python3 -m devtools.consistency
```

Run the consistency check with `--state /private/team` to include operator files;
it validates references and installed adapter interfaces without reading credentials
or contacting providers. CI runs the repository check alongside both CUE exports.

Edit the CUE source and regenerate its JSON; never manually synchronize two copies
of the same policy. Private JSON overlays need no CUE binary. Migration is explicit:
move `project.jev.model` to the overlay's `project.model`, `profile_evidence` to
`project.assignment.profile_evidence`, and old policy overrides into the matching
sections. Set the feature switches deliberately, then remove `project.jev` and its
retired policy-file selectors. The old `jev.json` and `completion-policy.cue/json`
formats are no longer runtime inputs.

Enabling Jev selection, advice, filtering or completion permits the service to send the scoped
context and selected evidence to the configured TypeSafe endpoint. The common
credential filter runs before transmission. Filtering runs on routine brief reads;
completion runs at its configured boundaries; selection runs before assignment;
direct assessment runs on explicit `work_assess` calls.
There is no background repository scan or Jev request from native hooks.

Requests default to a 28,000-byte serialized budget, an eight-second socket timeout
and at most one short retry for HTTP 429/529. Redirects are disallowed. Oversized
explicit assessments fail instead of silently dropping requested sources. Responses
are checked for the pinned model, answer shapes, distributions and usage. These
transport settings are policy fields, not agent arguments.

Existing artifacts retain each successful evaluation's sanitized request, response,
binding and effective policy. Source ranges remain replayable after edits or
worktree retirement. Cache keys cover work binding, inputs, source identities,
questions and effective configuration. Existing service locks share identical
concurrent requests. `jev_evaluation` events retain elapsed time, model, fingerprint,
evidence and usage; cache-hit events have `usage: null` because no provider request
occurred. Do not count the original evaluation's usage again on a cache hit.
Failed evaluations retain the original redacted diagnostic and a structured
`failure` with `category`, `reason`, and trusted `details`. Event outcomes distinguish
`unavailable`, `invalid_response`, `invalid_request`, and unclassified `failed`.
Missing credentials require operator correction; transport/HTTP failure details
identify retryability separately. A malformed response supplies no judgment.
The decision policy can assign unresolved provider failures to Arbitrator or block
them; neither result converts an outage or invalid response into approval.

`work_assess(proposal=..., sources=...)` uses the configured mission rubric without
creating work or changing authority. `work_report.evaluation` links decisions and
later outcomes to retained assessments through `jev_feedback`. Historical results
remain available, but a changed binding cannot reuse an old assessment as a current
decision. Fixture tests establish integration behavior; authenticated evaluations
are needed to measure judgment quality and savings.

### Service decisions and arbitration

The service retains each semantic decision with its authoritative work binding,
input identity, evaluation evidence and effective policy. Noul answers become true
at or above `accept_at`, false below `reject_below`, and otherwise remain unresolved.
Choice requires its selected option to meet the configured probability/confidence
and runner-up margin; configured unknown labels remain unresolved. Score requires
the configured confidence and distribution margin before its measured value is used.

The default Noul thresholds are 0.35/0.65. Completion overrides them to 0.65/0.85;
context relevance uses 0.15/0.35. Choice requires 0.65 probability/confidence and a
0.15 margin; Score requires 0.65 confidence and a 0.15 margin. These are configurable
decision rules, not agent interpretation of prose or a shared meaning of model effort.

Unresolved decisions are assigned to the configured `decisions.arbitrator_role`.
The default `decisions.failure_mode: "arbitrate"` also assigns provider-unavailable
and invalid-response cases; `"block"` preserves those failures without assignment.
The service creates the Arbitrator's work binding and supplies `work_context.decision`;
coordinators do not appoint this role through `orchestrate`. Arbitrator investigates
within its read-only scope and uses `decision_resolve(decisions, reason, evidence)`
for exactly the supplied unresolved questions. It cannot replace prior decisive
answers, rewrite requirements or bypass mechanical checks.

`decisions.reserved_sessions` defaults to one (range 1–10). Ordinary allocations
leave that capacity available for Arbitrator, including eligible members of a fixed
team. Shared-runtime `max_sessions` counts the reserve, so 12 permits 11 ordinary
collaborators plus one Arbitrator. Readiness checks require enough capacity for XO,
a producer and the reserve. Existing saturated teams must release capacity or raise
the limit when enabling decisions; the service does not evict existing assignments.
Pending investigations retry when eligible capacity becomes available.

A resolution is bound to current context and policy. Changed source/evidence,
assignment, requirements or configuration requires a fresh applicable decision.
The service releases the resolved arbitration assignment and resumes a retained
initiating operation through its normal checks. It never accepts replacement tool
arguments from the Arbitrator. A blocked continuation retains its actual failure;
another unresolved question may need a separate investigation.

### Scoped guidance and proof boundaries

With guidance enabled, failed control-plane calls, failed retained checks and Serena
errors carry a permitted skill excerpt and a configured next action. Routes are
ordered, match diagnostic text by case-insensitive search and action names by full
regex match, and can select role identities or behavior adapters. Empty selector
lists match every role/adapter. Built-in policy selects adapter behavior so renaming
a role does not change its routing semantics.
A route cannot deliver a skill outside the current role's available skill catalog;
an unavailable configured fallback skill resolves to that role's own skill. A
truncated excerpt is marked. Successful calls remain quiet. `failure_mode: "omit"`
omits unavailable guidance; `"fallback"` returns the configured fallback action
when the skill cannot be loaded. Neither changes the original operation result.

Known service failures expose `failure.category` and `failure.reason` independently
of the guidance switch. Categories are `not_permitted`, `not_ready`,
`invalid_request`, `evaluation_unavailable`, `evaluation_invalid`, and
`operation_failed`. Unknown exceptions remain `operation_failed/unclassified`;
diagnostic wording is not used to invent a category. MCP errors retain their
original text plus structured metadata; background operation reads and compact
notices retain the same cause. A completed worker can still contain a failed check.

`guidance.recovery_actions` is an ordered list of templates, each with `categories`
and `reasons` selectors (empty lists match any), an existing managed `tool`,
`arguments`, and a `when` instruction. Exact argument values `$reference` and
`$check` bind readable retained references and actual configured check names;
unknown placeholders or tools and invalid argument schemas fail validation.
Tool schemas use the effective policy, including role/job assessment limits.
`max_recovery_actions` bounds delivery (default six). Templates can reference only
the current tool surface at delivery. Check suggestions also require the same job
and, for assignment proof, the same assignment. An officer reading a specialist's
proof is not offered a check which cannot supply that specialist's measurement.
Missing facts omit an action rather than fabricating an argument.

Delivered guidance includes concrete `actions`, its telemetry `reference`
(`event:<id>`, an opaque correlation reference rather than a `work_context` resource),
`context_identity`, and `configuration_revision`. Its `when` text describes the
precondition for a suggested call. The agent chooses its next step; normal service
checks reauthorize every call against current work. A suggestion neither grants
permission nor changes completion policy. Producing it makes no additional Jev call.

With filtering enabled, the service omits background reports only after their
relevance decision is false. Thresholds, eligible kinds, protected text and role/job overrides are
configurable. Core context, incoming messages and evidence-bearing reports remain
outside filtering; raw reports and authorized reference reads remain intact.
Facts beyond the candidate budget are preserved. Oversized, failed or pending
relevance decisions preserve context; `filtering.failure_mode: "notice"` additionally
returns the configured message, while `"preserve"` remains silent. Arbitrator's
own context is excluded from filtering to avoid recursive investigation.

With completion enabled, proof is checked before planner adoption, specialist
completion, job acceptance and work-order conclusion. Cancellation releases a
contribution; it does not prove the owner's goal. The configured objective,
completion-condition and constraint questions each must resolve true;
scores are never averaged. Failed measurements, missing proof and stale revisions
remain mechanical barriers. Jev supplies the semantic evaluation within the
configured boundary and Arbitrator resolves ambiguous questions. Neither can
override CI, permissions, review or workspace requirements. Pending arbitration
keeps completion waiting; a false decision reports the unmet requirement.

Existing events distinguish guidance delivery, skill reads, context projection,
completion assessment and refusal, and retain effective configuration revisions.
`recovery_action_observed` links a guidance event and its originating operation to
a later exact call under the same context and configuration. It distinguishes a
suggested action from a retry of the original blocked call. Only an observed
successful original retry marks that condition resolved; pending, uncertain,
empty and failed outcomes do not. Suggested check observations retain the named
measurement, source revision, cleanliness and evidence separately. These links
establish sequence and observed outcomes, not that guidance caused recovery.
Evaluation feedback and observed outcomes retain their existing report links;
service decision selection, arbitration and application have separate events. Tests use isolated
temporary state. Result reports capture an existing managed workspace revision
internally; this identifies capture context rather than proving a measurement.
Configured check artifacts retain the command definition with observed output.
Agents supply proof through ordinary report/check tools without entering hashes.

References: [TypeSafe API](https://docs.typesafe.ai/api),
[model versions and limits](https://docs.typesafe.ai/models).
Wire fixtures cover the supported response format; no fixture result is an
authenticated judgment-quality measurement.

### Branches and worktree lifetime

Work-order source is prepared on first use, from the fetched `project.target`
(normally `dev`). The services generate these related branch names:

| Work | Branch | PR target |
| --- | --- | --- |
| Work order | `work-order/<wo>` | `project.target` (normally `dev`) |
| Job | `work-order/<wo>-job-<job>` | `work-order/<wo>` |

`<wo>` is the team identifier, work-order number and creation generation;
`<job>` is the job number and creation generation. These are generated internally.
The hyphen before `job` avoids Git's ref conflict between a branch and a child
path of that branch. The work-order ruleset includes `work-order/*` and excludes
`work-order/*-job-*`; see [GitHub settings](../.github/CI.md#github-settings).
The initial remote publication contains the development revision. Each job starts
from the latest remote work-order branch and submits its PR into that branch.
XO acceptance merges the contribution; XO's `work_submit` validates the combined
workspace and opens its PR into `project.target`. XO's `monitor(section="review")`
and `conclude` inspect, accept and deliver that final result under normal GitHub
rules. CO remains xor's interface and relays the system outcome.
The service never directly pushes new source commits into the integration branch.

Review fixes use the existing job workspace and `work_submit` again. The service
pushes without force to the same job branch and reuses its PR. GitHub reruns CI;
XO must inspect the new revision before acceptance. Both job acceptance and final
work-order delivery wait for current CI and GitHub's merge eligibility. New fixes
after a job's PR has already merged belong to a new job.

Job `work_refresh` incorporates both the parent work-order tip and current
development changes. XO `work_refresh` fast-forwards the integration workspace
to accepted contributions. If development has changed incompatibly, use a bounded
job to resolve integration before final delivery. See [GitHub settings](../.github/CI.md#github-settings).
Refresh preserves a checkpoint and leaves conflicts available to the job's editing
tools. Resolve them within the role's permitted paths, then use `work_save` and
`work_refresh` again. Clean upstream changes outside those paths are preserved;
editing them or resolving an out-of-scope conflict requires appropriate guidance.

Local directories default to sibling `wt-ag/`, with distinct team/work identifiers
for work orders, jobs and audit snapshots. An explicit `project.workspaces` wins;
already-recorded paths and targets are preserved. Upgrading does not move, retarget
or remove older local worktrees or unrelated branches. Start new work to use the
new routing; an existing issued job retains its recorded target.

After a retained merge result, cleanup is queued durably. An MCP background worker
removes only the recorded worktree with Git, without force, and deletes its local
branch only at the accepted revision. It waits for bound collaborators, managed
sessions, workspace leases, development operations and labelled Serena containers
to stop. All audit snapshots created for that job are included. Paused Serena
providers close when their binding is released, even with an external MCP client.

Dirty, untracked, changed-commit, replaced, locked or otherwise unexpected
workspaces are preserved. Git-ignored build caches leave with a clean worktree.
Cancellation and report-only completion preserve their worktrees. A cleanup error
is retained and retried without blocking coordination or unrelated jobs. While a
work order is active, `monitor` shows cleanup status; afterward the read-only
`evidence` index and telemetry retain it. Pending cleanup resumes when an MCP
connection is running again. Force-killed or unreachable Docker providers require
operator repair before their source can retire. History, artifacts and acceptance
records stay in private state. Remote branch deletion is a GitHub setting.

Use one execution host and its state directory at a time. These worktrees and
processes belong to that host; GitHub branches do not synchronize live SQLite,
absolute paths or process identities between the Dell and Chromebook.

## Operator CLI

All commands run from the repository root and take `--state`:

| Command | Purpose |
| --- | --- |
| `init` | Create team configuration and state; refuses to replace an existing team. |
| `connection` | Print the normal CO MCP connection with absolute local paths. |
| `config [--role ROLE] [--job N]` | Print redacted effective settings and their revision without opening the credential source. |
| `co [--profile PROFILE] [--brief-file PATH]` | Launch the configured CO role through its selected native harness adapter, optionally with an initial user brief. |
| `watch [--action start\|stop\|status]` | Manage the existing GitHub observation process. |
| `doctor` | Inspect local prerequisites; `--online` also reads Git/GitHub access. `--startup` limits prerequisite checks to configured capabilities for the initial roll call. |
| `evidence --work-order N` | Summarize existing run observations without certifying success. |
| `status` | Read configured members and observed session state. |
| `start --member NAME` | Start an already-bound managed member. |
| `agent --member NAME` | Run an already-bound member in the current terminal. |
| `stop --member NAME` | Stop a managed session with bounded escalation and terminal cleanup. |

`mcp` and `serena` are stdio provider entry points. `hook` and `operation` are
internal launch targets. The compatibility `send` command is an operator aid;
normal reports and guidance route themselves through the work binding.

## External specialist connections

Managed native profiles attach their providers automatically. For a configured
`external` specialist slot, the operator must attach both providers to that
client. Replace the paths and `worker` with its configured member name:

```json
{
  "mcpServers": {
    "agentscient_dev": {
      "command": "/absolute/path/to/python3",
      "args": ["/absolute/path/to/agentscient/devtools/run.py", "mcp",
               "--state", "/absolute/path/to/state", "--member", "worker"]
    },
    "serena": {
      "command": "/absolute/path/to/python3",
      "args": ["/absolute/path/to/agentscient/devtools/run.py", "serena",
               "--state", "/absolute/path/to/state", "--member", "worker"]
    }
  }
}
```

The specialist must have an active job/role binding, and the project must configure
the Serena image. Its workspace and REPL session come from that binding. The normal
CO connection is exported by `connection` and does not need Serena. External
clients are operator-managed: these MCP connections do not disable other tools in
that client. Use its native configuration to expose only the intended tools.

## Trusted setup and diagnostics

For trusted setup and diagnostics, add this temporary operator connection,
replacing the paths with absolute paths on your machine. The command must be your
Python executable. Managed agent profiles never include `--operator`; remove this
connection from any client used as an agent role after setup.

```json
{
  "mcpServers": {
    "agentscient_operator": {
      "command": "/absolute/path/to/python3",
      "args": ["/absolute/path/to/agentscient/devtools/run.py", "mcp",
               "--state", "/absolute/path/to/state", "--member", "owner", "--operator"]
    }
  }
}
```

On this operator connection, `tools/list` exposes `capability_search`, `capability_describe`, and
`capability_invoke`. Search returns five capabilities per page; describe supplies
the input schema. Invoke takes `{"capability":"name","arguments":{...}}`.

For importing existing work or repairing setup, the operator can create work and
bind roles from the top down. For example, with work order 1 and job 2:

```json
{"capability":"member_bind","arguments":{"member":"owner","role":"co","work_order":1,"brief":"Represent the user's intent."}}
{"capability":"member_bind","arguments":{"member":"chief","role":"xo","work_order":1,"reports_to":"owner","brief":"Coordinate the work within the stated boundaries."}}
{"capability":"member_bind","arguments":{"member":"worker","role":"coder","job":2,"reports_to":"chief","brief":"Implement the job using existing framework capabilities."}}
```

The reporting member must cover the child's work scope. XO reports to CO; CO
reports to the user. Cycles and mismatched work-order/job bindings are rejected.
Rebinding identical values is a no-op. `member_unbind` with `member` removes the
active binding; existing connections immediately lose report/guidance access.
MCP clients receive a tool-list-change notification when the advertised list changes.
Changing a supervisor's scope can require updating its collaborators' reporting
bindings; a stale route reports an error instead of delivering into unrelated work.

Normal external clients use the same server configuration **without `--operator`**,
under the name `agentscient_dev`. Managed sessions are launched only after their
role/work binding exists. The CO/XO tools perform this setup behind their outcomes.

## Operator and backing-service capabilities

These backing-service capabilities are available only on the operator connection.
Managed agents use the outcome tools in the [role/tool reference](../docs/control-plane-roles-and-tools.md).

| Capability | Purpose |
| --- | --- |
| `config_status` | Inspect validated effective settings and their revision without reading credentials |
| `member_bind`, `member_unbind` | Configure or remove a trusted role/work/reporting binding |
| `peers` | Configured members, observed state, and actual native conversation IDs |
| `session_start`, `session_stop` | Start a configured member or stop it with bounded escalation and owned-terminal recovery |
| `message_send` | Send a durable message with an idempotency key |
| `message_inbox`, `message_wait` | Read or briefly wait for incoming messages |
| `message_ack`, `message_retry` | Acknowledge reception or explicitly retry uncertain delivery |
| `telemetry_events`, `telemetry_usage` | Read paginated measured events and available native usage |
| `work_order_create`, `work_order_select` | Open a numbered work order or select existing work |
| `work_order_get`, `work_order_list`, `work_order_update` | Recover purpose, boundaries, progress, jobs and evidence |
| `job_create`, `job_select` | Open or select a bounded piece of a work order |
| `job_get`, `job_list`, `job_update` | Track a job's progress, branch and resulting PR |
| `work_link`, `work_unlink` | Associate references or existing artifacts with the work |
| `artifact_save`, `artifact_list`, `artifact_read` | Save a managed snapshot, find evidence, or read its contents |
| `pr_list`, `pr_get`, `pr_diff` | Find a PR, understand its current state, and inspect changes |
| `pr_open`, `pr_update` | Open/adopt the job's PR and maintain its description or draft/ready state |
| `pr_comments`, `pr_comment`, `pr_review`, `pr_threads` | Read feedback, post progress, review a revision, and handle discussions |
| `pr_merge` | Merge the inspected revision using the repository's allowed methods and rules |
| `ci_status`, `ci_failures` | Inspect observed CI and get failed steps, annotations and log excerpts |
| `ci_workflows`, `ci_run`, `ci_rerun`, `ci_cancel` | Discover workflows, explicitly start one, retry or cancel a run |
| `ci_watch`, `ci_watch_stop` | Receive CI changes through durable live messages |
| `ci_artifacts`, `ci_artifact_save` | Find CI outputs and retain an archive or report as managed evidence |

For example, invoke `session_start` with `{"member":"chief"}`, then `message_send`
with `{"to":"chief","body":"Your message","key":"unique-key-for-this-message"}`.
Starting a session reports observed startup state, not proof that the model has
received or processed anything. A recipient's explicit acknowledgement is separate.

## Work orders, jobs, and evidence

The **work-order number** is the enduring binding for an overall effort. It holds
the objective, completion criteria, boundaries, progress, and shared evidence.
**Jobs** are bounded pieces beneath it, normally delivered through individual PRs.
Each job belongs to one work order and records its repository, branch and resulting
PR. Several jobs and live members can contribute to the same work order.

Create a work order with `work_order_create`:

```json
{"title":"Artifact support","objective":"Keep evidence with the work",
 "done_when":"Agents can save and recover evidence through MCP",
 "constraints":"Do not change Symfony application code","key":"artifact-support"}
```

The result includes its stable numeric `work_order.id`, used as the `work_order`
argument throughout the tools. Creation selects it for this member. Then create a
job with `job_create`, supplying its own `title`, `objective`, and `done_when`, plus
optional `repository` and `branch`. That selects the job and its parent work order.
`pr_open` records the PR automatically. For a PR opened elsewhere, it can adopt the
existing PR by number; alternatively record its URL with `job_update`:

```json
{"pull_request":"https://github.com/owner/repository/pull/123",
 "status":"in_progress","next_action":"Review the storage checks"}
```

`job_get` returns the job together with its parent's purpose and boundaries.
`work_order_get` returns overall progress, a small page of job/PR summaries, and
linked evidence. Use `job_list` with the work-order number and `after` set to
`next_job` to continue the job list; use `after` set to `next_after` to continue
references. Discovery and listings stay bounded, and artifact contents are read
only when requested.

Messages, artifacts, tool events, and native observations inherit the member's
selection. A job ID implies its work-order number; mismatched pairs are rejected.
Supplying a work-order number alone associates activity at the overall level.
Replies inherit the original message's binding unless explicitly overridden.
Acknowledgement and delivery events retain that binding even when another member
has selected different work. Native observations carry the selection at observation
time; aggregate vendor usage is not a precise allocation of cost to individual jobs.
An in-flight MCP call keeps its original context if another call changes selection.

`job_select` without an ID clears only the job; `work_order_select` without a number
clears both. Selection persists across MCP process restarts. Records remain after
branches disappear and PRs merge. Work-tracking tools store references; GitHub
tools retrieve current provider state. Managed Git outcomes are described in the [role/tool reference](../docs/control-plane-roles-and-tools.md#workspaces-and-git).
The operator API can edit status (`open`, `in_progress`, `blocked`, `done`, `cancelled`) directly. Normal agent completion uses XO job acceptance and XO work-order conclusion.
Closing jobs does not automatically close their work order. Optional creation
`key` values let a caller retry without creating duplicate records.

Save a small report with `artifact_save`:

```json
{"name":"results.md","content":"# Verification\nThe storage checks passed."}
```

Or save an existing deliverable with `{"name":"report.pdf","source":"var/report.pdf"}`.
The tool attaches it to the selected work order and job and returns an artifact ID.
`source` is relative to that member's configured workspace and is streamed into a
managed snapshot. Changes or deletion of the original file do not change the saved
artifact. Absolute paths, escapes outside the workspace, and non-regular files are
rejected. The tool never writes into the workspace.

Inline content is limited to 8,000 characters to fit the MCP request frame. Use
`encoding:"base64"` for inline binary content, or `source` for larger existing files.
`media_type` is optional; the service infers it from the name. Repeating the same
name, content, media type and member reuses the artifact; changed content produces
a new ID and retains the earlier snapshot. `work_link` with `kind:"artifact"` and
the artifact ID as `reference` can reuse evidence in another work order or job.
`work_unlink` removes only that association.

`artifact_read` accepts the artifact ID and returns bounded UTF-8 or base64 content.
For more content, pass its `next_offset` back as `offset`; reads default to 16 KiB
and are limited to 32 KiB. `artifact_list`, `message_inbox`, and telemetry tools can
filter by `work_order` and `job`; without filters their existing whole-team/member
listing behavior is preserved. Work selection is convenient context, not a gate
required before communication or artifact storage.

## GitHub work through MCP

On the operator connection, GitHub capabilities are discovered and invoked through
`capability_search`, `capability_describe` and `capability_invoke`. There is no raw API, arbitrary GraphQL, credential input, or command tool.
Repository/PR inputs normally come from the selected job. An explicit `job` uses
that job for the call without changing selection. Without a job, supply
`repository:"owner/name"` and `pr` where needed, or configure a default repository.

A typical sequence of capability arguments is shown below. Replace the sample
40-character revision with the head SHA returned by `pr_get`, and use the returned
run/artifact IDs for subsequent calls:

```json
{"capability":"pr_open","arguments":{"base":"dev","summary":"What changed and why"}}
{"capability":"pr_get","arguments":{}}
{"capability":"pr_diff","arguments":{"path":"src/Example.php","revision":"0123456789abcdef0123456789abcdef01234567"}}
{"capability":"ci_failures","arguments":{"run":123456}}
{"capability":"ci_watch","arguments":{}}
{"capability":"ci_artifacts","arguments":{"run":123456}}
{"capability":"ci_artifact_save","arguments":{"artifact":789,"entry":"test-results.xml"}}
```

These are separate `capability_invoke` calls. The branch must already exist on
GitHub. `pr_open` defaults to a draft, includes the job's purpose/completion criteria
and work-order number, reuses an existing PR for that branch/base, and binds its URL
to the job. Use `pr_update` with `draft:false` when ready. Merging does not mark the
job or work order done; their completion criteria can include further work.

`pr_get` returns compact reviews, CI and observed blockers; `description_offset`
continues a long PR description using `next_description_offset`. `pr_diff` lists files
first; supplying a path returns a bounded patch and `next_offset`. GitHub may omit
or truncate large/binary patches. `pr_comments` lists conversation comments,
`kind:"review"` reviews, or `kind:"inline"` inline findings. Previews include IDs;
use `comment` and `offset` for full text. `pr_threads` pages review threads, supports
replies and resolution, and checks that a selected thread belongs to this PR.

`pr_review` and `pr_merge` require the inspected 40-character `revision`. Reviews
are attached to that commit; merge passes it as GitHub's expected head. Refresh
after a revision-change error. GitHub enforces repository rules and the caller's
permissions. The tools do not bypass reviews or enable administrative merges.
Writes are not automatically retried after uncertain network outcomes: inspect
PR comments/reviews or CI status before repeating them.

`ci_status` distinguishes head and test-merge revisions, inspects all check pages
before summarizing, and reports observed results rather than claiming merge
eligibility. `page` continues checks and runs using `next_page`/`next_run_page`.
Select an explicit `run` for CI diagnosis/control. In CI output, `check` denotes
an Actions job inside a run; the MCP `job` remains the work-order child.
`ci_failures` defaults to the latest attempt and returns five failed checks per
page. Supply `attempt` for an earlier attempt, `check` plus `log_offset:0` for
consecutive log pages, or `annotation_page` to continue annotations. Missing or
expired logs remain an explicit `log_error` alongside the available step evidence.

`ci_rerun` retries failed checks and their dependents by default, at the run's
original revision. `failed_only:false` reruns the whole workflow. `ci_run` explicitly
requests an existing manually runnable workflow, with a published `ref` (default:
job branch) and optional string `inputs`. It reports acceptance, not a fabricated
run ID or completion. Find the actual run in `ci_status` after it appears.

`ci_watch` records a fixed PR head or explicit run and sends changes through the
existing message service. It retains the original work binding across selection
changes and MCP restarts. A changed PR head produces `superseded`; completion or
timeout ends the watch. Repeating the call renews the watch. `ci_watch_stop` stops
this member's watch without cancelling CI. One background observer per state
directory polls independently of agent/MCP sessions; stored notifications remain
readable in `message_inbox`. Registering a watch starts the observer automatically.
Opening a new MCP connection resumes pending watches after an observer restart.

Integrated work-order submission also requests `@codex review` and
`@codex security review`, once per review kind and submitted commit. Job PRs retain
their existing CI observation. The combined watch remains active after CI or
reviews finish, so reopened findings are reported, until the PR closes, its head
changes, or the 24-hour watch expires. XO submits again after integrating fixes
to validate the new head and request fresh reviews. `conclude` reads GitHub again
and requires both reviews on the exact current commit with no unresolved threads.

Completion is read from the authenticated Codex bot's review-summary comment.
Abbreviated commit IDs are resolved through GitHub. Missing, changed or ambiguous
summary formats remain pending; silence and reactions do not establish success.
The repository must have both Codex review features enabled for the identity
posting the requests. A lost request response is reconciled from its retained
comment marker before another request is considered. If no remote receipt is
visible, the uncertain request remains explicit for operator inspection.

The observer reuses `github_watches`, the GitHub transport and durable messages.
It polls at `github.poll_seconds` (default 30, minimum 15) without model calls,
honors rate-limit retry times, and atomically records changed observations with
their notifications. CO/XO receive work-order notices through the existing work
context and live-message delivery paths. Agents use `work_submit`, `monitor` and
`review_respond`; they do not manage processes or GitHub API calls.

For operator diagnostics only:

```sh
python3 devtools/run.py watch --state /absolute/private/state --action status
python3 devtools/run.py watch --state /absolute/private/state --action start
python3 devtools/run.py watch --state /absolute/private/state --action stop
```

`stop` preserves watches and messages. A subsequent `start`, watch registration,
or MCP connection resumes pending work. `--action run` runs in the foreground for
an operator's service supervisor. The detached process survives agent shutdown;
its singleton lock and existing Linux process identity checks prevent duplicate
observers and signalling a reused PID. Operator diagnostics are in `watcher.log`
and the existing event store. After updating dev-lane code, restart the observer
to load the new implementation. No webhook endpoint or additional service framework
is required.

`ci_artifact_save` retains the downloaded ZIP by default or a named `entry` as a
report. It verifies GitHub's digest when available and uses the existing managed
store, with repository, PR, run, revision, source ID, and archive digest provenance.
`observed_attempt` is the run's current attempt: GitHub's artifact metadata does
not identify the producing attempt. ZIP entries are streamed, never extracted into
workspace paths. The result lists 50 entry names; `entry_offset` continues the list
and reuses the saved archive. `artifact_read` subsequently returns contents and
source provenance. Default download and expanded-entry limits are 256 MiB; CI log
downloads are limited to 16 MiB, with bounded text returned to the agent.

### GitHub connection for operators

Reuse an authenticated GitHub CLI OAuth session, or provide a refreshed GitHub App
installation/OAuth credential through a private file. Add this optional object to
the state directory's existing `team.json`:

```json
{"github":{"repository":"uscient/agentscient","base":"dev",
           "poll_seconds":30,"max_download_bytes":268435456}}
```

For a managed credential file, add `token_file:"/absolute/path/to/refreshed-token"`
inside `github`; it is read on each request. Otherwise the service uses
`AGENTSCIENT_GITHUB_TOKEN` if present, then the fixed internal
`gh auth token --hostname github.com` credential lookup. Authentication must permit
the requested repository operations. Credential refresh and initial OAuth login
belong to the operator; credentials are never MCP arguments or tool results.
Private Grok profiles preserve the operator's `GH_CONFIG_DIR` for this lookup.

No GitHub server installation or new Python dependency is required. The small
transport calls GitHub's REST/GraphQL APIs behind the task service. Redirected
artifact/log downloads do not forward credentials. Rate-limit retry times are
honored by CI watches; other rejected requests report GitHub's error to the caller.
Optional `merge_method` chooses `merge`, `squash`, or `rebase` if the repository
allows it; otherwise the service selects an allowed method from repository settings.

## Storage for operators

Role/work bindings, routed updates, work orders, jobs, associations, GitHub watches,
operations, reviews and artifact metadata share `live.sqlite3`. Payloads live under `artifacts/` in the private state directory.
Services manage paths, hashes, publication and integrity checks; agents receive
references, useful summaries, and requested content. Schema upgrades preserve
existing messages, work records and telemetry.

`store.State` owns SQLite connection lifetime and read-only inspection;
`store.Store` adds writable runtime state and schema upgrades. Configuration,
consistency and evidence inspection neither create nor upgrade a database.
Each member's binding revision survives unbinding and process restarts, so an
old report cannot become current when that member receives the same assignment
again. Upgrades seed these revisions from retained bindings and proof history
without changing active binding snapshots.

Saving publishes complete bytes before committing the catalogue and association
in one database transaction. A retry can reuse a complete blob left by an
interrupted commit. Failed calls clean up temporary files; a killed process can
leave temporary or unreferenced files. The store retains these files for operator inspection and does not delete evidence automatically. Back up the database and artifact directory
together while the team is stopped. Missing or damaged payloads produce explicit
read errors.

## Native conversations

The installed harness adapters are selected through `adapters.json`. The following
behaviors describe the shipped adapters; another installed adapter owns its native
protocol, readiness checks and session transport.

Managed Codex keeps one app-server thread and receives peer messages through
`turn/start.toolOutput`. Its dynamic tools proxy the actual stdio MCP server.
Startup checks that the installed binary advertises the required tool-output API.
Managed Grok keeps one ACP session, using `session/prompt` when idle and
`_x.ai/interject` while busy. Managed Claude keeps its native interactive session
and receives MCP channel notifications. Delivery waits for the native channel registration log entry;
initial MCP handshake alone does not establish a ready channel.

Claude may need its native development-channel approval. Attach to the tmux
terminal returned by `session_start`. The approval remains in the native UI;
there is no replacement repository permission process.

Managed private profiles expose this team's MCP surface and deny native
shell/filesystem tools. Codex and Grok reuse only selected local login/configuration information;
Claude retains its native account login. These settings do not form an OS sandbox.
Global harness settings are not rewritten. Vendor protocol changes can require an
adapter update; startup failures and delivery errors remain visible in telemetry.

The `co` command uses each harness's interactive terminal for the appointed
external CO seat. It retains the operator's native controls and uses observer
hooks without the managed tool gate. Ordinary Agentscient MCP checks still apply,
including generation fencing and the current role/work binding. Native commands
and quit controls pass through the relay; closing its input terminates the child
session and restores the terminal. This console behavior does not alter Claude,
Codex or Grok profiles used by managed collaborators.

The Codex CO connection sets
`mcp_servers.agentscient_dev.default_tools_approval_mode = "approve"` for its
role-bound tools. CO requires Codex CLI 0.122.0 or newer, which introduced this
setting. Both doctor and CO profile preparation check the configured binary's
`--version`; unsupported or unrecognized versions fail with an upgrade instruction
before the setting is injected. Managed workers retain their existing protocol
requirements. This avoids a native MCP approval deadlock when the operator's
global `approval_policy` is `never`. It does not change the global approval or
sandbox policy, unrelated MCP servers, or server-side authority. Explicit native
`mcp_servers.agentscient_dev.tools.<tool>.approval_mode` settings still take
precedence; choose an approval-capable mode through `/permissions` when using
`prompt` for one of these tools. This launch default overrides a native
server-wide default for `agentscient_dev`. See the
[Codex configuration reference](https://developers.openai.com/codex/config-reference).

`co --brief-file PATH` submits a UTF-8 file as initial user input while keeping
the interactive session and native controls. The file must be nonblank, contain
no NUL characters and be at most 64 KiB. Omit the option when reopening retained
work to avoid resending its original brief. The brief is not a retained runtime
profile setting. Custom adapters opt in with `supports_initial_prompt = True`
and consume command-only `config["initial_prompt"]` as user input; unsupported
adapters reject it before a session is allocated.

Operator equivalents are available for bootstrap and troubleshooting:

```sh
python3 devtools/run.py start --state /absolute/path/to/state --member chief
python3 devtools/run.py agent --state /absolute/path/to/state --member worker
python3 devtools/run.py status --state /absolute/path/to/state
python3 devtools/run.py stop --state /absolute/path/to/state --member chief
```

`agent` runs in the current terminal. MCP participants use the named capabilities,
not these CLI commands. A stopped session's records survive; restarting creates
a new native conversation. This does not claim transcript/context recovery.

## Telemetry and delivery

The state directory contains `live.sqlite3`, private native profiles, process
logs, and redacted JSONL transport traces under `telemetry/`. Events record MCP
operation timing/results, native identity/activity, message submission and
acknowledgement, and usage observations when the native protocol supplies them.
Missing usage is unknown, not zero; no estimated cost is fabricated.
Monitoring selects reports from the current binding and usage from the current
session generation. Earlier observations remain in history. Configured provider
credentials are filtered at capture time, including file rotation and role/job
overrides; an unsafe credential source marks the capture unavailable instead of
retaining unchecked content. See the [telemetry reference](../docs/runtime-telemetry.md).

Queued, submitted, and acknowledged are distinct. A crash during delivery becomes
`uncertain` on restart, not an automatic duplicate. Only the sender can explicitly
retry that message; the ID stays the same. Traces and message history stay local.

### Review Serena process activity

Serena process monitoring observes supported Python process-launch attempts from
the REPL interpreter and records them in the existing private telemetry. It does
not block execution or add an approval step. The report identifies activity for
review and coverage gaps; an attempt is not proof of execution or privilege
escalation. See the [observation limits](../docs/runtime-telemetry.md#serena-process-observations).

After updating this checkout, install the monitor in the local image:

```sh
devtools/scripts/dependencies.sh update --build-serena
```

Stop/restart the relevant provider or start a new specialist session using the
rebuilt image. Rebuilding does not change already-running containers. Restarting
the provider loses its Python variables, so first finish the current operation
and retain any needed source changes through the normal work tools. Existing
readiness checks validate Serena's source revision label; that label alone does
not distinguish an older image without the monitor.

From the repository root, supply the same private state directory used to launch
your CO/runtime:

```sh
python3 devtools/scripts/serena-audit.py --state /absolute/path/to/state
python3 devtools/scripts/serena-audit.py --state /absolute/path/to/state --include-expected
python3 devtools/scripts/serena-audit.py --state /absolute/path/to/state --actor worker --job 1 --json
```

Recognized backend activity is omitted from findings by default. REPL-associated
and unknown activity remain visible for review; a familiar executable name alone
does not make an action expected. `--include-expected` exposes backend records as
well. Use `--since` with a timezone-qualified ISO timestamp to select a period.
The script reads local traces without starting any agent or command in Serena.

The default exit status is `0` even when the report contains findings. Add
`--fail-on-findings` to return `1` when findings are present. Missing, unreadable
or incomplete telemetry returns `2`; inspect the report's coverage diagnostics
before drawing conclusions from an empty list. A provider without installation
status may be running an older image. Rebuild and restart it before collecting
new observations; old sessions cannot be audited retroactively. Python audit
hooks are best-effort observation, so a covered provider with no findings still
does not certify that no process execution occurred.

## Capability-gap review

Agents attach structured capability problems to existing `work_report` and
`guidance_request` calls. No additional managed-agent tool or cross-work backlog
access is introduced. The operator inspects and reviews the retained backlog:

```sh
python3 devtools/scripts/capability-gaps.py --state /private/team list
python3 devtools/scripts/capability-gaps.py --state /private/team --json list --status all --limit 50
python3 devtools/scripts/capability-gaps.py --state /private/team show 1
python3 devtools/scripts/capability-gaps.py --state /private/team review 1 \
  --expected-revision 1 --status reviewing --classification discovery \
  --note "Checking whether the existing tool exposes configured check names." --owner xor
```

Use the actual gap ID and current revision from `list` or `show`. Reviews reject
stale revisions, preventing one operator from silently overwriting another
review or a newly reported occurrence. Supported states are `open`, `reviewing`,
`deferred` and `resolved`. The default list includes the first three; use
`--status all` or a specific state for other views. Operator classifications are
`missing_tool`, `discovery`, `permission`, `tool_failure` and `unknown`, separate
from the category each agent originally reported. A permission restriction may
be working as intended. Use the review note to record that judgment.

Every review requires a note and expected revision; `--owner` can record who
will follow up. To resolve an item, supply `--evidence` describing or linking the
observed repair, verification, or decision that settles it:

```sh
python3 devtools/scripts/capability-gaps.py --state /private/team review 1 \
  --expected-revision 2 --status resolved --classification discovery \
  --note "The capability is discoverable through the updated tool description." \
  --evidence "Validated in the subsequent work run; retained operation:42."
```

Evidence is the operator's recorded judgment; the script does not verify a URL,
test result or resolution claim. Reports and reviews do not grant permission or
change assignments. A new occurrence reopens a resolved item. Repeated delivery
of the same encounter does not increase its count or reopen it. Capability names
are grouped by exact normalized name and repository, without semantic merging.
The configured project repository is available before workspace preparation;
work without either a recorded or configured repository is grouped within its
work order. When a repository becomes known, an existing work-order gap keeps
its ID and review history and joins repository grouping if that identity is free.
If another repository gap already exists, the historical work-order gap stays
separate and keeps receiving that order's recurrences; independently reviewed
assessments are retained. Keep the capability name stable when reporting
recurrences, and use a new `occurrence_key` for each new encounter. See
[agent reporting](../docs/control-plane-roles-and-tools.md#reporting-capability-gaps).

Lists and histories are bounded to 50 records by default (maximum 100). Continue
a list with `--after` using the returned `next_after`. A `show` returns `next_page`
with both `after` and `review_after`; pass both as `--after` and `--review-after`
on the next call, retaining an exhausted history's position while paging the
other. `next_page` is null when both histories are exhausted. Human output prints
the combined continuation flags. `--json` includes the complete structured
output and individual `next_after`/`next_review_after` exhaustion indicators. `list` and
`show` open state read-only and never initialize or upgrade it; a schema without
gap tracking reports that it is unavailable. `review` uses the runtime's normal
schema migration after validating its arguments. Exit status is 0 for a completed
operation and 2 for invalid arguments, unavailable state or a rejected review.
If credential filtering is unavailable, reads report suppressed capture with
exit status 2. A committed review still exits 0, explicitly reporting suppressed
output. Text suppressed during storage cannot be recovered by restoring the
filter: history retains the suppression marker. Inspect the current revision
after restoring filtering and record another explicit review to supply any
replacement note or evidence.

The backlog starts with structured reports and retains the original work context.
Failed or denied calls are candidate evidence, not automatically confirmed gaps.
Old prose is not retroactively parsed, and unreported successful workarounds are
not detected by this feature. An empty backlog cannot establish the absence of
capability gaps; review it alongside [runtime telemetry](../docs/runtime-telemetry.md)
and agent reports. Keep state and exported reports private: they contain task
content and operator notes.


## Source attribution

Native transport adapters use code from the
[multi-harness proof](https://github.com/xormania/multi-harness-proof/tree/a944f31b7527807d000da985c546e8b5bc9ff476).
Its MIT notice is retained in [PROVENANCE-LICENSE](PROVENANCE-LICENSE). The pinned
Serena image retains its upstream source and GPL license as described in the
[Serena reference](../docs/control-plane-roles-and-tools.md#serena-v2-repl).
