# Shared role runtimes

> Historical reference: this document describes the imported Agentscient runtime in
> `devtools/`. Its roles, defaults, restrictions, and setup procedures are not
> requirements of the current Xorchestrate application. Start with the
> [current architecture](hexagonal-architecture.md) and [README](../README.md).

Initialize an operator-owned policy in the private state directory:

```sh
python3 devtools/run.py init --state /private/team --workspace /repo/agentscient \
  --co owner --member owner:external --runtime
```

Add the existing project/repository options when this team will manage product
workspaces. Existing fixed-member teams continue to work. To migrate one, create
`roles.json` and `vendors.json` using the schema below, then set `"runtime": true`
in `team.json`. Existing running conversations are not reconfigured.

`roles.json` has `version: 1`, `max_sessions` (1–100 concurrent bound collaborators),
`profiles`, and a nonempty `roles` mapping. Keys are identities from the configured
[capability catalog](role-capability-packages.md); each has an ordered list of
profile names. The catalog defines the role names and count, and maps each role's
`behavior` to an installed adapter in `devtools/adapters.json`. Its `defaults`
mapping selects the role when a workflow needs a behavior such as planning.
Several role identities may share one behavior; ambiguous automatic selection
requires an explicit default.

Runtime preferences may configure a subset of the catalog. Unknown role identities
are rejected, and requesting an omitted role fails before its allocation; profiles
are never chosen implicitly. Different roles may use different lists or share one.
For example, a profile is:

```json
{"harness":"claude","vendor":"anthropic","model":null,"reasoning":null}
```

Set `model` and `reasoning` to values accepted by your installed harness. Null
leaves the native default in control and telemetry labels it accordingly. Optional
`binary` is an executable path/name, never a shell command. A harness adapter may
support a `provider` key for a configured native model provider; the supplied
Codex adapter does. Vendor identity is explicit, not inferred from the model name.
Fresh shared teams copy [runtime-defaults.json](../devtools/runtime-defaults.json)
into their private `roles.json`. The source uses the same schema and validator as
operator policy; `python3 -m devtools.consistency` checks it against the current
role catalog and harness adapters. Vendor entries are derived from those profiles
and stay disabled until you enable your logins. Model choices live in this policy,
not in Python role switches or capability restrictions.

The maintained initial choices are:

| Role | Preferred profile | Model | Effort |
| --- | --- | --- | --- |
| CO, XO | `codex_astra_xhigh` | `gpt-6-astra` | `xhigh` |
| NCO | `codex_astra_high` | `gpt-6-astra` | `high` |
| Planner, Test Writer | `codex_sol_xhigh` | `gpt-6.1-sol` | `xhigh` |
| Architect, Coder | `claude_opus_xhigh` | `claude-opus-5-5` | `xhigh` |
| Research | `codex_astra_max` | `gpt-6-astra` | `max` |
| Arbitrator | `codex_astra_xhigh` | `gpt-6-astra` | `xhigh` |
| Audit | `grok_high` | `grok-4.7` | `high` |
| Security Auditor | `codex_daybreak_blue_xhigh` | `gpt-daybreak-blue-latest` | `xhigh` |
| Adversarial Review | `codex_sol6_high` | `gpt-6-sol` | `high` |
| Docs | `claude_sonnet_high` | `claude-sonnet-5-5` | `high` |

CO keeps ordered alternatives `codex_sol_xhigh` and `grok_high` after its preferred
Astra profile. All three profiles remain selectable through the same launcher,
for example `co --profile codex_sol_xhigh`. Specialists initially have
one selected profile each; add explicit alternatives to private policy when needed.
With selection enabled, Jev selects among eligible alternatives for the actual
assignment. XO receives the same selection before launch once its work order is
bound. CO remains the operator-selected interface because it starts before a work
binding exists. Selected profiles are retained for retries; changed work or policy
requires a current selection.
Shared profiles intentionally couple settings for roles that reference them. Copy
a profile under a new name and change the role's preference to tune it independently.

`max_sessions` includes Arbitrator capacity. When semantic decisions are enabled,
`decisions.reserved_sessions` defaults to one: a limit of 12 permits 11 ordinary
collaborators and leaves one session for investigation. Readiness requires at least
three sessions for XO, a producer and the default reserve. Fixed teams must also
have a free eligible Arbitrator member. Enabling decisions or increasing the reserve
on an already saturated team requires releasing capacity or increasing the limit;
the setting does not evict existing bindings.

Codex receives `model` and `model_reasoning_effort` through its CLI or app-server;
maintained Codex profiles also set `provider: "openai"` so an inherited alternate
provider cannot redirect the selected OpenAI model. Claude Code and Grok Build
receive `--model` and `--effort`. Use Claude Code
v2.1.284 or newer for this lineup. Daybreak Blue is a moving alias requiring
approved access on the selected account/surface. Verify its available effort and
resolved model locally. A successful policy load or `doctor` check is not proof of
authenticated model access. CLI/protocol fixtures verify setting propagation;
the authenticated acceptance run verifies actual availability and records observed
settings when the native harness exposes them.

Sources for the native settings: [Codex configuration](https://learn.chatgpt.com/docs/config-file/config-reference),
[Claude model configuration](https://code.claude.com/docs/en/model-config),
[Grok CLI](https://docs.x.ai/build/cli/reference), and
[Daybreak access](https://learn.chatgpt.com/docs/cyber-safety).

Initialization refuses existing team or runtime-policy files. Repository updates
do not overwrite a team's private choices or vendor availability. To adopt new
defaults in an existing team, merge the desired profiles and role preferences from
the maintained file into its private `roles.json`, preserving local binary/provider
overrides. Validate with `python3 -m devtools.consistency --state /private/team`,
then stop and relaunch affected sessions. The runtime and role catalog must come
from the same revision; an older runtime cannot load role identities absent from
its catalog. An acceptance fixture that pins an older toolchain must update that pin
before importing this policy.

`vendors.json` has `version: 1` and a `vendors` mapping. For example:

```json
{"version":1,"vendors":{"anthropic":{"available":false,"reason":"Usage exhausted"},"openai":{"available":true},"xai":{"available":true}}}
```

Availability controls new launches only. Resolution without a retained assignment
selection tries a role's preferences in order, skipping disabled vendors and
missing executables. Assignment selection considers the eligible profiles as
described above. A retained choice is revalidated against its exact profile and
policy revision; an unavailable or stale choice fails instead of falling through
to another profile. The service never enables another vendor or replays an
interrupted task itself.
An optional `reset_at` string is an operator reminder, not proof that quota reset.
Save edits by replacing complete JSON files atomically. Malformed policy fails
closed. Both files, the role catalog and the adapter manifest contribute to the
recorded configuration revision. Catalog CUE and generated JSON are repository
configuration shared by services; they are not selected independently per team.

CO asks for an objective; XO asks for named work and roles. The service allocates
instances dynamically, supports several instances of one role, and snapshots the
resolved runtime for each launch. Native settings and fallback reasons go to
telemetry. CO/XO tools expose role capacity and work state without model selectors.
Operator `runtime_status` exposes policy, availability and resolution diagnostics
through MCP. Model settings do not grant tools or authority.

If a vendor runs out mid-session, mark it unavailable and stop the affected
session. CO resolves current policy when relaunched; XO refreshes its stale
selection at launch. A specialist with a retained assignment selection needs its
coordinator to refresh the selection through `orchestrate` before resume or
replacement can launch another configured profile. Inspect the resulting startup
in `monitor`. The work, evidence and message history survive; a new native
conversation is a new session, not a cross-vendor transcript migration.
