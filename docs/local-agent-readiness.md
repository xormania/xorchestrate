# Local setup and readiness

> Historical reference: this document describes the imported Agentscient runtime in
> `devtools/`. Its roles, defaults, restrictions, and setup procedures are not
> requirements of the current Xorchestrate application. Start with the
> [current architecture](hexagonal-architecture.md) and [README](../README.md).

The development environment provides independent control-plane MCP services,
persistent native harness communication, work orders/jobs, telemetry, artifacts,
managed Git/GitHub delivery, installed Symfony development tools and a separate
Serena v2 REPL provider. The Symfony application remains a starting foundation.
This guide covers operator setup and the evidence needed before relying on a local
team. For normal agent usage, see the [roles and tools](control-plane-roles-and-tools.md).

## Stages and completion requirements

| Stage | Requirements | Done means |
| --- | --- | --- |
| Foundation | Use the committed Composer lock: PHP `^8.5.11`, Symfony `8.1.*`, Maker, Mate and MCP Bundle. Keep service attributes, autowiring and autoconfiguration. | Installed Makers generate the native implementation; generated attribute commands register and execute. CI verifies this. |
| Operator setup | Linux or WSL2, Python 3.12+, Git identity and remote access, locked PHP platform, Composer, Docker, selected authenticated harness binaries. All managed harness sessions need tmux. | Team configuration, private state, project binding and pinned Serena image exist. `doctor --online` passes its available checks. |
| Command and context | External CO plus available XO/planner/specialist slots; add an NCO per job for a multi-job plan. Normal MCP connections only. | CO relays xor's instructions; XO plans, organizes and steers; each collaborator receives its brief and role tools. Unbound members cannot act on arbitrary work. |
| Source work | Job worktree supplied automatically; Serena is separate and restricted to the role's paths. Makers/Mate run through control-plane tools. | Navigation, a persistent REPL value, an allowed edit, installed generation and validation are observed in the bound worktree. Protected paths remain protected. |
| Live coordination | Native conversations stay alive while work proceeds. Reports, evidence and guidance retain their job association. | A live clarification and response are observed with the same native conversation ID; queued messages alone do not establish receipt. |
| Delivery | Save/check/submit through outcome tools; audit the submitted snapshot when needed; inspect before acceptance. | PR/CI/evidence are associated automatically. Changed revisions or dirty work block acceptance. Normal GitHub requirements apply. |
| Turnover | XO accepts jobs, submits the combined result, inspects it and concludes the work order; CO relays the outcome to xor. | Job and final PRs are checked separately. Merged worktrees retire after shutdown; history and evidence remain. |
| Authenticated local acceptance | Run the selected installed harnesses with their real accounts through a bounded, authorized job. | The operator inspects actual native/tool evidence, artifact results, PR and CI. Record the tested revision and harness versions. This stage remains a local check. |

## One-time operator setup

### Manual operator scripts

The checked-in helpers live in [`devtools/scripts`](../devtools/scripts). Run them
with Bash as your normal Linux/WSL user. They resolve their project from the script
location, so an absolute script path works from any directory. They do not edit
your shell startup files. Explicit install/update commands may run vendor installers
whose own setup behavior still applies. Sourcing a helper prints an instruction and
returns without changing your shell options.

| Script | Check or preview | Explicit action |
| --- | --- | --- |
| `git-sync.sh` | Default: current branch and cached tracking state | `--apply` fetches and fast-forwards; add `--push` to publish ahead commits |
| `dependencies.sh` | `check`: package candidates, owned PHP/Composer integrity, Docker and pinned Serena | `update` installs/updates named prerequisites and the owned toolchain; `--build-serena` builds the provider |
| `runners.sh` | `status`: registration, user services and Docker | `start` starts stopped/failed services after validating the selected pool |
| `harnesses.sh` | `check --online`: installed versions versus public release metadata | `update --apply` installs missing clients and updates supported installation types |
| `credentials.sh` | Default: stored credentials and available expiry metadata; `--online` adds supported authentication requests | Reports renewal commands; does not log in or renew automatically |
| `acceptance.sh` | Default `preview`; `check --online` checks configuration, readiness and Jev access | `update`, `prepare`, `doctor`, `co`, `verify`, `stop` manage an isolated fixture run |

For example, from the Agentscient checkout:

```bash
bash devtools/scripts/git-sync.sh
bash devtools/scripts/dependencies.sh check
bash devtools/scripts/runners.sh status
bash devtools/scripts/harnesses.sh check --online
bash devtools/scripts/credentials.sh --online
bash devtools/scripts/acceptance.sh preview --fixture ../fixture-ag
```

Each command is independent and reports blockers with a nonzero exit status. The
helpers never enable `set -e` in your terminal. If your terminal already has
`errexit` enabled, wrap a command in `if` so a failed check cannot close it:

```bash
if bash devtools/scripts/dependencies.sh check; then
    printf 'Dependency checks passed.\n'
else
    printf 'Review the reported blockers; your terminal stays open.\n'
fi
```

Git sync refuses dirty, detached or diverged checkouts. It never stashes, resets,
switches branches or force-pushes. `--repo PATH` selects a different checkout.
Preview uses cached remote refs; only `--apply` fetches current remote state.

Dependency maintenance supports Debian/Ubuntu on Linux/X64. `update` refreshes APT
metadata and installs/updates only the named prerequisite packages. `sudo` prompts
in your terminal for those package commands; do not run the entire helper with
sudo or provide a password in an argument. PHP and Composer come from the existing
checksum-pinned Agentscient release and remain in a private, versioned directory
under `${XDG_STATE_HOME:-$HOME/.local/state}/agentscient/toolchains`. Existing
toolchains are checked and preserved, including a damaged installation that needs
inspection. Docker Desktop upgrades remain a Windows operation; the helper checks
Docker reachability and explains WSL integration. It never installs a competing
Docker daemon. Harness versions are checked separately.

Use `dependencies.sh update --build-serena` when the pinned Serena image needs
building. The build uses that helper checkout's Dockerfile. If the fixture-pinned
runtime has a different Serena revision, build its `devtools/serena` directory with
the selected image tag; acceptance doctor checks that exact source label. Repeat
`--project PATH` to also check/install each project's dependencies
from its Composer lockfile. This executes that project's configured Composer
scripts/plugins during installation; it does not run `composer update`.

Runner defaults match the shared local pool: `~/actions-runner/ag1` through `ag12`,
organization `uscient`, and user services `agentscient-runner@N.service`. Override
`--runner-root`, `--count`, or `--organization` for another existing pool. The
helper does not register runners or read their registration tokens. A running local
service does not by itself prove GitHub connectivity or successful job scheduling.

Harness maintenance covers Codex, Claude, Grok and Agy (Google Antigravity CLI).
Use repeated `--only NAME` to narrow it. Offline inventory does not establish
freshness; unsupported installation owners or conflicting binaries are reported
for manual attention. Agy remains an operator client; the managed Agentscient
adapters are Codex, Claude and Grok. Credential reports distinguish stored login,
verified API access and native login that still needs a live run. Expiration is
reported only when exposed; an opaque key or subscription login is not assumed
permanent. An access-token expiry is not the subscription's expiry.

For a renewal report, use `credentials.sh --online --warn-days 14`; add `--json`
for sanitized structured output, or repeat `--service NAME` to check selected
clients. GitHub checks default to both uscient repositories; `--repo OWNER/REPO`
selects others. The helper initiates no login or explicit refresh; native status
commands remain vendor-controlled. Exit `0` means the requested supported API
checks verified access, `1` means a failure, and `2` means verification is incomplete.
A working API key is reported separately when the native client's active login
selection cannot be established. Opaque subscription/keyring credentials may need
a real native session; API model-list access does not prove generation entitlement.

Jev is included in the default credential report. Check it independently of the
fixture, PHP, Docker or native harness installations with:

```bash
bash devtools/scripts/credentials.sh --service jev --online
```

The Jev check uses `~/.config/agentscient/configuration.json` when present, otherwise
the runtime's default credential reference (`TYPESAFE_API_KEY`). Select another
overlay with `--configuration PATH`; an explicit missing file fails rather than
falling back. It uses the runtime's project/role configuration resolver, including
relative credential-file paths and role overrides. Keys remain file/environment
references. Standalone checking has no retained jobs, so an overlay containing
job-specific settings requires the existing team's configuration workflow instead.

Without `--online`, this checks credential readability/format without network access.
With `--online`, it makes a small real evaluation for each distinct configured
connection/model/transport, which can incur provider charges. `jev: VERIFIED` means
those requests authenticated and their responses passed validation. It does not
establish a full agent run. Credential expiry remains unknown because this API does
not expose it; secrets, provider responses and exception text are never printed.

### Isolated fixture acceptance

`acceptance.sh` wraps the existing fixture operator and frozen verifier rather
than introducing another E2E engine. It discovers a sibling `fixture-ag` or
`agentscient-test-repo`; supply `--fixture` if both exist or the layout differs.
The optional private overlay defaults to `~/.config/agentscient/configuration.json`
when present, otherwise normal runtime defaults use `TYPESAFE_API_KEY`. Explicit
`--configuration` paths must exist. GitHub Actions secrets are not local credentials.

For repeated acceptance after a reviewed change, use a clean checkout of the
committed revision whose CI passed:

```bash
bash devtools/scripts/acceptance.sh run
```

`run` selects that checkout's exact `HEAD`, updates the fixture, provisions a
separate immutable runtime and creates a fresh isolated run. It performs the
existing online preparation checks and frozen doctor, then starts the native CO
session with the run's brief as its first user message. No copying or pasting is
required. After CO exits normally, it runs the frozen
online verifier and collects diagnostics. A normal console exit alone does not
establish acceptance; the verifier's result does. `--runtime-revision SHA` selects
another committed runtime explicitly. The other setup options below also apply to
`run`.

The run directory is generated beneath
`${XDG_STATE_HOME:-$HOME/.local/state}/agentscient-acceptance/runs/` and remembered
across shells. Use the same short commands afterwards:

| Command | Purpose |
| --- | --- |
| `bash devtools/scripts/acceptance.sh status` | Read preparation status and the selected run path. |
| `bash devtools/scripts/acceptance.sh co` | Open a new CO conversation for the retained work without resending the original brief. Recover current context before giving new direction. |
| `bash devtools/scripts/acceptance.sh verify --online` | Run the frozen final verifier again when work is ready. |
| `bash devtools/scripts/acceptance.sh collect` | Write a private, filtered diagnostic JSON file and print its path for sharing. |
| `bash devtools/scripts/acceptance.sh stop` | Stop the remembered run's agents while retaining evidence and branches. |

An explicit `--run-dir PATH` on an existing-run command selects an older run
without changing the remembered one. The default `run` blocks concurrent
invocations and a still-active remembered team; resume or explicitly stop that
team first. Supplying a new `--run-dir PATH` to `run` deliberately starts a separate
run. Interrupting CO returns promptly and
leaves the team and evidence available; it does not start verification or stop
workers automatically. Stage failures retain the original failure result and any
created run, with diagnostic collection when available.

Automatic briefing requires a fixture and runtime that support it. `run` checks
both before online preparation or branch publication. An older retained run still
uses its frozen launcher; reopening it with plain `co` does not require briefing
support or a brief file.

If an older frozen Codex run reports `MCP tool call requires approval, but
approval policy is never`, use its native `/permissions` command to choose a
mode that can ask for approval, then ask CO to retry the original `direct` call.
For a run with automatic briefing support, if CO has already exited and no work
was created (`ready_to_direct`, `work: null`), reopen it with
`bash devtools/scripts/acceptance.sh co --brief` to submit the retained brief
again, then select that native permission mode before retrying.
Do not modify the frozen runtime to repair a retained run. After work exists,
use plain `co` to recover its context without resending the brief.

If `stop` leaves uncertain queued or running operation records, the default guard
still blocks replacement; inspect the diagnostics or deliberately choose a new
`--run-dir` instead of deleting the old state.

`collect` reads retained state through the recorded runtime. It includes bounded
session, message, operation and failure evidence, applies the runtime's credential
filter and does not package the live database, private profiles or credential files.
Its output is diagnostic evidence, not an acceptance certificate.

The individual setup commands remain available for troubleshooting or explicitly
testing the fixture workflow's reference runtime:

```bash
bash devtools/scripts/acceptance.sh update --fixture ../fixture-ag
bash devtools/scripts/acceptance.sh check --online --fixture ../fixture-ag
bash devtools/scripts/acceptance.sh prepare --fixture ../fixture-ag \
    --run-dir "$HOME/agentscient-acceptance-001"
```

Run these steps individually and address blockers before the next step. `update`
checks GitHub access, clones or fast-forwards clean fixture `main`, and provisions
a separate checkout at the exact runtime SHA selected by the fixture workflow.
That immutable runtime path lives under
`${XDG_STATE_HOME:-$HOME/.local/state}/agentscient-acceptance/runtimes/<SHA>`.
It never changes the branch of the checkout containing these scripts or repoints
a runtime recorded by an older run. An explicit `--runtime` must match the selected
revision.
`--configuration` is accepted by `run`, `preview`, `check` and `prepare`; it is not an
`update` option. Add it only to those commands, rather than to shared update options.

To test a newer reviewed Agentscient commit before the fixture updates its reference,
use `--runtime-revision` with its full 40-character commit SHA. Select a commit whose
Agentscient CI has passed. From a checkout at that verified commit:

```bash
verified_revision=$(git rev-parse HEAD)
bash devtools/scripts/acceptance.sh preview --fixture ../fixture-ag --runtime-revision "$verified_revision"
bash devtools/scripts/acceptance.sh update --fixture ../fixture-ag --runtime-revision "$verified_revision"
bash devtools/scripts/acceptance.sh check --online --fixture ../fixture-ag --runtime-revision "$verified_revision"
bash devtools/scripts/acceptance.sh prepare --fixture ../fixture-ag --runtime-revision "$verified_revision" \
    --run-dir "$HOME/agentscient-acceptance-new-runtime-001"
```

Repeat the revision on each new-run command; omitting it selects the fixture workflow
reference again. Preview and setup print both revisions. The override provisions its
own SHA-specific checkout and does not change the fixture workflow or earlier runs.
Fixture CI continues to check its configured reference runtime and the independent
product gates; it does not establish that the newly selected runtime passed its own
CI. The new run records the actual runtime commit/tree, and its frozen verifier rejects
later runtime changes. Existing-run commands do not accept `--runtime-revision`.

`check` uses disposable private state and the existing readiness/configuration
services. `--online` adds read-only GitHub checks and a small provider-backed Jev
request for each distinct effective connection/model/transport. Native stored login
does not establish model entitlement. By default all three managed harnesses are
selected; repeat `--harness` or use `--runtime-from TEAM_STATE` to copy an approved
role/vendor policy. Nothing in this helper creates role alternatives automatically.
`--scenario scenarios/greeting-decisions.json` additionally requires the fixture's
selection/arbitration setup; failed prerequisites stop before preparation.

The helper automatically uses the matching owned PHP/Composer installation created
by `dependencies.sh` at its default location. Otherwise pass both `--php` and
`--composer` executable paths (also printed by the dependency helper). A custom
`--toolchains` location needs these explicit paths. `--serena-image` selects a
different already-built image, which still must carry the pinned source label.

`prepare` requires a new run directory outside both repositories, with an existing
parent. It repeats online preflight before invoking the fixture, installs locked
dependencies in the isolated run, and publishes a unique fixture acceptance branch.
It does not start CO. Failed preparations and all subsequent evidence are retained.

```bash
bash devtools/scripts/acceptance.sh doctor --online --run-dir "$HOME/agentscient-acceptance-001"
bash devtools/scripts/acceptance.sh co --brief --run-dir "$HOME/agentscient-acceptance-001"
# The first launch submits the brief automatically. Omit --brief when reopening CO.
bash devtools/scripts/acceptance.sh status --run-dir "$HOME/agentscient-acceptance-001"
bash devtools/scripts/acceptance.sh verify --online --run-dir "$HOME/agentscient-acceptance-001"
bash devtools/scripts/acceptance.sh stop --run-dir "$HOME/agentscient-acceptance-001"
```

Existing-run commands use the run's frozen operator/verifier and recorded runtime;
they do not rediscover the current fixture or select a newer runtime. `status` only
reads the manifest and reports preparation state, not final acceptance. `doctor`
and `verify` retain evidence, `co` launches real authenticated clients, and `stop`
stops that run's agents while preserving its files and remote branches. Only the
frozen verifier's actual product, gate and delivery evidence establishes acceptance.

Runtime fixes require a new run with the reviewed revision. Preserve the old run
and stop its agents with `stop`; editing its recorded runtime or protected test
setup invalidates the frozen evidence. A failed test run can also leave fixture
database changes behind when it boots without the test environment's transaction
support, so retrying that workspace is not a clean baseline.

### Team setup without the fixture helper

These terminal commands are for the operator. Participating agents use MCP. Start
with an up-to-date local `dev` checkout and these prerequisites:

| Prerequisite | Used by |
| --- | --- |
| Linux or WSL2; Python 3.12+ with pidfd support and readable `/proc` | Managed launch and process recovery require Linux process identities, abstract Unix sockets and peer credentials. SQLite is in the standard library. |
| Git with commit identity and authenticated remote access | Managed worktrees, checkpoints and delivery. |
| GitHub API access | PRs, CI and feedback; configure it as described below. |
| PHP matching the committed Composer platform and Composer | Installed Symfony Makers, Mate and checks. |
| Docker | Separate pinned Serena v2 REPL provider. |
| Installed, authenticated Codex, Claude or Grok binaries | The collaborators you choose; All managed harness sessions need tmux. |

Install the locked application dependencies and build the provider:

```sh
composer install --prefer-dist --no-interaction
docker build --tag agentscient-serena:v2-beta devtools/serena
```

The Serena build locks its base image, Debian snapshot, Python runtime/build
dependencies and PHPactor archive. See [image maintenance](../devtools/serena/README.md)
for updating those pins and verifying a cold build.

Authenticate the Git remote, GitHub and the native harnesses using their usual local
login. The GitHub service reuses `gh auth token`, an operator-maintained `token_file`
in `team.json`'s `github` section, or `AGENTSCIENT_GITHUB_TOKEN`. Credentials never
appear in agent tool inputs. Git push authentication is separate from API access.

Create the team with shared role policy. The service allocates instances as XO
requests roles; multiple jobs can use the same role. Initialization copies the
maintained role/model/effort lineup into private policy. Enable your logged-in vendors
and review ordered harness/model/reasoning preferences in the generated private
`roles.json` and `vendors.json` files. See [runtime policy](shared-runtime-config.md).
The dynamic team needs only the external CO member in `team.json`. Set
`roles.json`'s `max_sessions` to at least two for XO plus a planner or specialist,
plus the effective `decisions.reserved_sessions` when semantic decisions are
enabled. The maintained reserve is one, so the default minimum is three; allow
more slots for concurrent jobs and audits. See [runtime capacity](shared-runtime-config.md)
for the reserve policy.

```sh
python3 devtools/run.py init \
  --state "$HOME/.local/state/agentscient-live" \
  --workspace "$PWD" --repository uscient/agentscient --target dev \
  --serena-image agentscient-serena:v2-beta \
  --co owner --member owner:external --runtime
```

`init` enables managed workspaces, default installed Symfony tools/checks and the
default sibling `../wt-ag/` worktree directory. Work orders integrate their jobs
before final delivery into `dev`. Configure the [GitHub rules](../.github/CI.md#github-settings)
for both PR destinations before local acceptance. `init` also enables the
separate Serena image. It does not start models. It refuses to replace an existing
team. For an existing team, edit its private `team.json`; see
[team and project configuration](../devtools/OPERATIONS.md#team-and-project-configuration).
Existing fixed-member teams remain supported. Shared runtime teams set optional
`binary`, `model` and `reasoning` in role profiles. Maintained defaults select explicit
models; operator policy can change them without modifying capability packages.
For a PHP binary outside PATH, set `project.development.php` to
its absolute path; `project.development.composer` can select Composer as well.

Configure Jev through the private `project.configuration_file` entry and its JSON
overlay. Obtain a key from your [TypeSafe account](https://console.typesafe.ai/keys)
and save it in a private local file; the service loads that file directly. CI's
GitHub secret does not supply local credentials. The overlay selects the key-file
reference, endpoint/model and independent selection, advice, filtering, guidance and completion
switches. See [configuration and local Jev credentials](../devtools/OPERATIONS.md#configuration-and-local-jev-credentials)
for a complete example and environment-variable alternative. Defaults enable
assignment selection, context filtering and completion decisions, plus local skill
guidance. Configure the local Jev credential and an available Arbitrator runtime
before the authenticated acceptance run.

Inspect the effective project settings, or add `--role` and an existing `--job`
number to see their overrides:

```sh
python3 devtools/run.py config --state "$HOME/.local/state/agentscient-live"
```

Inspection never opens the key file. Policy changes apply on the next operation;
key-file replacement applies on the next credential read. Existing process
environments and native startup settings require restarting the affected service
or conversation to change.
Check the setup and launch the native CO using current role/vendor policy:

```sh
python3 devtools/run.py doctor --state "$HOME/.local/state/agentscient-live" --online
python3 devtools/run.py co --state "$HOME/.local/state/agentscient-live"
```

Use `--profile`, `--model` and `--reasoning` for operator choices on this launch;
the interactive CO also retains native commands, tools, settings, model/reasoning
selection, delegation and exit controls. See [CO startup and handover](session-handover.md).
Keep its MCP connection active while managed startup, delivery and CI observation
are needed. Agents do not
assemble a separate XO connection. For an existing external CO client, the
`connection --state ...` command still exports an absolute normal `mcpServers`
configuration without `--operator`; only MCP communications from that client are
visible to the service.

Before giving work, ask CO to **run a roll call**. Its `roll_call` tool checks
configured prerequisites and starts XO in standby for a live “reporting for duty”
acknowledgement. CO relays the retained result and blockers; `starting` means the
reply is still pending. This creates no work order. When you give the brief, XO
starts in the real work context and reports ready again. Subsequent agents report
to their commissioning parent automatically. See the
[readiness contract](control-plane-roles-and-tools.md#reporting-for-duty).
Existing frozen acceptance runs keep their frozen runtime; use a new run to test
this capability after updating the runtime.

`doctor` returns exit code 2 for a failed prerequisite. Its probes inspect the
configured checkout, remote identity, Git identity, private state, effective
configuration, required local Jev credential sources, locked real PHP platform,
Docker image source label, selected binaries and installed Codex protocol schema.
For dynamic teams it checks configured role availability, the CO/XO/planner
workflow roles and `max_sessions`; fixed teams retain their collaborator-slot
check. Harness probes use the profiles selected by the current fallback policy.
Managed teams also check the current Linux process identity, pidfd access,
abstract Unix sockets and peer credentials. Run the control plane on Linux or WSL2; native
macOS managed launch is not supported. `--online` additionally reads the remote target and GitHub access. It does
not push, merge, install dependencies, start models, contact Jev, or establish
provider authentication. A readable local key is not an authenticated request.
Claude channel registration and Grok ACP authentication are checked on native
startup. A `preflight_passed` result still requires the authenticated acceptance
stage. Image labels identify the configured build; runtime boundary checks are the
separate integration test, not an inference from a label.

## Start work through CO

Each session calls `onboard` to receive its customized responsibilities, current
tools and shared skill catalog. Use `skill_read` for relevant task guidance; see
[role onboarding](role-onboarding.md).

The CO client uses `direct` to relay xor's objective, completion criteria,
boundaries and prepared specification/context. CO does not assess, coordinate,
execute or accept work. That binds XO; XO commissions a planner with `orchestrate`
and adopts the job list with `plan_adopt`. It coordinates one job directly or
appoints one NCO per job for multiple jobs. Each collaborator receives its brief, role-scoped tools and job
workspace automatically. Native managed profiles also attach the separate Serena
provider when configured. Agents do not assemble connections or select branches.
Managed collaborators use standalone conversations; Agentscient creates and
coordinates them instead of harness-native subagents. The appointed interactive
CO uses the operator's native controls while retaining its role-bound MCP authority.

Use the [role/tool reference](control-plane-roles-and-tools.md) for the actual
catalog, source paths, retained operations, review and acceptance behavior.
Externally hosted specialist clients require the separate
[specialist connections](../devtools/OPERATIONS.md#external-specialist-connections).

## Authenticated local acceptance

Use a small owner-approved foundation improvement, or an operator-owned test
repository containing this Symfony foundation. Set the actual objective, completion
criteria and protected paths through CO's `direct`. Do not turn temporary acceptance
probes into product requirements. The local agents should not build or repair their
own dev-lane during this run.

Require the following evidence without prescribing how agents solve the job:

- XO starts a persistent collaborator; it receives the right tools and brief.
- The collaborator uses installed Maker metadata/generation, Mate diagnostics,
  Serena REPL and configured checks for the bounded change.
- A clarification arrives while work is underway, is answered, and the native
  conversation identity remains stable. Pause/resume also preserves that identity.
- The result is checkpointed and submitted with retained evidence. Audit, when
  assigned, reviews that revision; findings and responses arrive through MCP.
- XO inspects and accepts each passing contribution, then submits the combined
  result with `work_submit`. XO addresses the returned Codex review findings,
  inspects that delivery and concludes after CI and both current Codex reviews
  pass. CO relays the outcome to xor. Turnover preserves history and permits another job; merged worktrees
  retire after shutdown.
- Managed workers' native shell/edit attempts are denied. The interactive CO
  retains the operator's native tools and controls; verify model/reasoning changes
  and normal exit in that session. Observed usage is reported honestly. A
  stopped/restarted session is a new conversation, not claimed transcript recovery.

After the run, the operator can export a compact evidence index for its work-order
number (the number is an operator argument, not agent bookkeeping):

```sh
python3 devtools/run.py evidence \
  --state "$HOME/.local/state/agentscient-live" --work-order 1
```

It reads existing state without upgrading or changing it. It lists native identities,
completed tool-call counts, message/acknowledgement states, operation states,
artifacts and job/PR/acceptance records. It does not certify success: a completed call
can return a failed check or an asynchronous receipt. Inspect the retained results
and native traces alongside this index. Keep the acceptance assessment with the
work-order result.

## Verification already available

The [CI guide](../.github/CI.md) lists the Python/MCP/native-protocol fixtures,
real installed Symfony integration, pinned Serena container integration and local
verification commands. These checks use no model account. Authenticated native
delivery and model behavior require the local acceptance run above.
