# Native CO and role replacement

Initialize the [shared runtime](shared-runtime-config.md), configure the chosen
harnesses' [native login storage](../devtools/OPERATIONS.md#native-login-storage),
and enable their vendors. From Linux or WSL2, start the CO:

```sh
python3 devtools/run.py co --state "$HOME/.local/state/agentscient-live"
```

For an existing work binding, this uses its configured CO role identity. Without
a work binding, it uses the catalog's default role for CO behavior. A binding
with another behavior is rejected. The launcher uses the first available profile
in the selected role's preference list. An operator can
choose a configured CO profile and override model or reasoning for this launch:

```sh
python3 devtools/run.py co --state "$HOME/.local/state/agentscient-live" \
  --profile codex_astra_xhigh --model YOUR_MODEL --reasoning YOUR_EFFORT
```

Profile names come from `roles.json`; the maintained CO profiles are
`codex_astra_xhigh`, `codex_sol_xhigh` and `grok_high`.
An explicit profile must belong to CO's preference list and have an available
vendor and executable. A disabled explicit choice fails instead of silently
launching a different vendor. Model and effort values must be supported by the
installed harness and selected model, and must satisfy that role package's
model/reasoning allowlists. Overrides are checked before a new session is registered;
they do not edit the configured profile. Omit them to use the configured values,
package defaults or permitted native defaults. Ordinary agent MCP tools do not
accept these runtime choices.

The launcher opens the native interactive terminal in the appointed member's
workspace and attaches the common MCP server. This is the operator's own session:
native model/reasoning selection, slash commands, tools, settings, delegation and
quit controls remain available under the harness's normal configuration and
permissions. The same treatment applies to a configured role alias with CO behavior;
it does not apply to managed collaborators that happen to use the same harness.
The CO calls `onboard`, which includes its shared and role instructions. Use `skill_read` only
for relevant skills not already marked `loaded`. CO then relays xor's instructions
to XO; XO creates managed tmux sessions for the specialist roles. Claude may show
its native development-channel consent.
Console hooks observe lifecycle and tool activity without applying the managed
MCP-only tool gate. Agentscient MCP still authorizes every call against the current
role and work binding. CO remains xor's interface to XO; access to native controls
does not grant coordination, execution or acceptance authority in Agentscient.
Managed worker launches keep their existing tool restrictions, isolated settings
and login handling. Normal native authentication and consent remain in force.

Grok CO registers a temporary connection with native `grok mcp add --scope user`
and adds a personal observer-hook file. Native configuration writes preserve other
settings but may reformat TOML. The connection and hooks run only in that CO launch
environment. CO uses Grok's embedded backend (`--no-leader`) so an existing shared
backend cannot supply another session's environment or connection settings; native
tools and controls remain available. Normal exit uses native `mcp remove` and
removes the hook file if their generated contents remain unchanged. Operator edits
are preserved. A killed launcher can leave inert, generation-gated entries; inspect
them with native `grok mcp list` and remove the named entry if needed.
Codex adds its connection and observer hooks through session configuration
overrides. Native hook trust still applies; disabling or declining observers can
reduce telemetry. Loose plugins are not used as a substitute for MCP registration.

Use the harness's native model/effort controls to change the running CO session;
the launcher options above select its initial values. Native quit and interrupt
commands pass through the terminal relay. Closing its input also shuts down the
child session and restores the caller's terminal.

## Swap CO

1. Have CO retain important user decisions, pending questions and next actions
   with `work_report`. Reports, jobs, messages and evidence are already durable.
2. Exit the current native CO normally. If it is stuck, use the operator's
   `stop --state ... --member owner` command and wait for shutdown.
3. Run `co` again, optionally choosing another profile. The replacement loads
   `onboard` and `work_context` for the same logical CO and current work.

CO may switch among its configured harness adapters. The existing XO and specialists keep
their conversations. Native transcript, private reasoning and REPL variables do
not migrate. Unreported private decisions cannot be reconstructed by the service.
Codex/Grok interactive CO clients read new team reports through `work_context`
or `monitor`; their TUI is not the managed app-server/ACP message bridge. Claude
can also receive the registered MCP channel. Keep a normal team MCP connection
active for launch intents and background observation.

## Replace other roles

| Controller | Action | Preserved |
| --- | --- | --- |
| Operator | Replace XO through trusted repair tools | XO work, specialist team, reporting chain, evidence |
| XO | `steer(action="replace", work=..., role=..., direction=...)` | Named specialist's role, job, workspace, history and evidence |
| XO | `steer(action="pause" / "resume")` | A still-running native conversation |

Replacement requests stop the old process, then launch the same member. A retained
assignment runtime must still match the current work and role/vendor policy; a
changed policy or unavailable selected profile blocks startup instead of silently
choosing another model. The coordinator must refresh that assignment's selection
through `orchestrate` before retrying. XO selects a current profile at its own
launch when its retained selection is stale.
Replacement does not retry an uncertain message or replay a native conversation.
Recovery uses the latest brief and durable reports. Inspect `monitor` for observed
startup or a failure. Externally attached XO or specialists require their operator
to reconnect them.

Each launch has a new generation. Native MCP and hook commands carry that
generation in a separate private profile directory. Old connections and old
profile reconnects cannot act in a replacement session. A new generation waits
for an in-flight MCP/provider call to finish; launching while a call is active
returns a retryable error. The old native process must exit before replacement.
The separate Serena gateway enforces the same session boundary.
Live delivery claims recheck the generation and stop state in the same transaction
as dequeuing, so a retiring process cannot consume its replacement's recovery direction.
After native shutdown, a fresh external connection from the exported `connection`
configuration can resume the same external CO seat without a generation argument.
It keeps the durable work and captures the last generation as a boundary: the next
native launch invalidates it. Connections opened before shutdown and connections
that explicitly carry the stopped native generation remain blocked.

## Evidence and limits

The CO launcher records terminal output, MCP calls/results, hooks, runtime choices
and available Claude transcript entries alongside existing per-member telemetry.
It does not record raw keystrokes; native login/password input remains governed by
the harness. Output uses credential filtering and incomplete-line buffering. The
capture contains native terminal control sequences and task content. Not every
terminal redraw is a structured message, and a visible selected model is not
proof of the vendor's internal routing. Preserve private state accordingly.

CI runs real PTYs, tmux, MCP, generation fencing, cross-harness CO fixtures and
vendor fallback on XO replacement. It retains the resulting traces and database.
These are protocol/CLI fixtures: installed authenticated vendor acceptance remains
the [local check](local-agent-readiness.md#authenticated-local-acceptance).

Native CLI settings were checked against the official
[Codex reference](https://developers.openai.com/codex/cli/reference),
[Codex configuration](https://developers.openai.com/codex/config-reference),
[Claude reference](https://code.claude.com/docs/en/cli-reference),
[Grok reference](https://docs.x.ai/build/cli/reference) and
[Grok settings](https://docs.x.ai/build/settings/reference).
