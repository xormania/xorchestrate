# Progressive MCP entry for live development

`tools/dev_mcp.py` projects existing coordination and evidence services through
three MCP tools: `capability_search`, `capability_describe`, and
`capability_invoke`. It owns no domain state and needs no Symfony kernel or
application dependencies. The initial tools/list payload is 1,154 UTF-8 bytes
with compact JSON; the transport bounds whole responses at 16,384 bytes.

Live communication between persistent harnesses is the default. The standalone
dispatch adapter remains available only for an explicit owner request. The
new entry neither advertises nor invokes its launch, wait, status or collect
operations. Reading historical evidence does not launch work.

## Start and use

The existing session launcher must supply its normal trusted participant
context. An MCP request cannot bind itself, choose a role or supply an identity.
The entry uses the same private capability verification as coordination_mcp.

```sh
python3 -B ops/conductor/tools/dev_mcp.py check
python3 -B ops/conductor/tools/dev_mcp.py serve
```

`--binding FILE` optionally narrows capabilities and names the expected
participant using the existing coordination binding shape. A binding file does
not replace the session's private capability. `--evidence-config FILE` accepts
the existing lane_mcp configuration; without it, evidence capabilities describe
their missing configuration and messaging stays usable. Options precede the verb.

`--serena-python FILE` selects the isolated Serena v2 REPL provider for Symfony
prod-lane discovery and edits. `--workspace PATH` can bind a standalone entry;
issued profiles use their trusted worktree and reject a conflicting override.
Discover `code_status`, `code_repl` and `code_restart` through the same three tools.
See [provider installation, receipts and PHP evidence](../devlane/providers/README.md).

Search returns at most ten capability cards, with `next_offset` for further
pages. Describe returns one complete input schema. Call that schema through
`capability_invoke` with `capability` and `arguments`; returning a schema as data
does not install a native host tool. Describing availability reports configured
access, not authentication, successful invocation, or native readiness.

For a peer exchange, discover `coord_participants`, then describe and invoke
`coord_send`. Use a stable `key` when retrying the same message. Recipients use
`coord_inbox`, `coord_ack`, and `coord_resolve`; the core still requires a valid
evidence reference for resolution. `coord_routes` and `coord_attempts` inspect
existing courier state and native submissions. A send queues a durable message;
it does not claim the recipient received, acknowledged or resolved it. The
existing sessions/courier services must be running for native delivery.

Caller-provided authority, unknown arguments and unknown capabilities refuse
before invocation. Oversized message responses preserve the committed message
identifier so callers can retry with the same key or inspect its durable record.
Oversized request IDs refuse before a write. Resources cannot invoke mutations.

## Select an MCP-only persistent profile

The operator can start each participating member with the existing supervisor:

```sh
python3 -B ops/conductor/tools/sessions.py start --member NAME --mcp-only
```

Add `--serena-python /absolute/serena-v2/bin/python` to enable private prod-lane
code work. This option requires `--mcp-only` and adds no agent-facing server.

Use the normal `--team`, `--run` and `--worktree` options as needed. Select
`--mcp-only` for every participating member and again when starting a replacement
generation. Existing profiles are not silently converted. Inspect, attach,
recover and stop remain the existing session operations; recover observes
processes and pending messages rather than starting a new conversation.

For a standalone persistent terminal, `profiles.py prepare-interactive` accepts
the same `--mcp-only` option. Launch or resume through its `run-interactive`
operation and retained binding. `check` validates the requested projection; it
does not certify what an installed native harness actually intercepted.

The private profile registers only this entry, under the existing `coordination`
server name, preserving courier and adapter recognition. It drops inherited MCP
servers and skips the legacy CLI onboarding skill. Model/provider choices and
credential custody retain their existing owners. Global configuration is not
edited. Protocol messages describe how to acknowledge and reply through
`capability_describe` and `capability_invoke` in the same native session.

| Harness | Requested restriction |
| --- | --- |
| Claude Code | `--tools ""` removes built-in tools; strict private MCP configuration selects this entry |
| Codex | Shell, unified exec, freeform patch, JS REPL and code-mode feature settings are disabled; CLI overrides retain those settings and the selected entry across project config layers; the profile hook rejects direct reads/edits and other native project tools |
| Grok | ACP receives the selected MCP server; the profile hook admits discovery and `use_tool` calls to this entry, and rejects other project tools |

The selected profile's PreToolUse boundary admits the three published entry
tools and harmless conversation controls. Unknown tools, other servers and
malformed broker envelopes fail closed. Changed or unavailable pinned helper
inputs also deny pre-tool use. This restriction applies only to the selected
MCP-only profile; ordinary repository diagnostics remain advisory. The MCP
services still validate their capability arguments and caller authority.

Native feature settings alone are not a complete tool restriction: in
particular, Codex can advertise a patch handler independently of the freeform
feature flag. Its native pre-tool hook must intercept that call. No claim of
filesystem or REPL sandboxing follows from these settings. Dispatch workers
are not enabled by this option and remain outside the default workflow.

Implementation references checked 2026-09-26: [Claude CLI tool selection](https://code.claude.com/docs/en/cli-reference),
[Codex configuration](https://developers.openai.com/codex/config-reference),
[Codex tool hooks](https://github.com/openai/codex/blob/main/codex-rs/core/src/tools/registry.rs),
and [Grok source](https://github.com/xai-org/grok-build). Installed versions still
require the canaries below; an accepted configuration is not live evidence.

## Validation and remaining work

`test_dev_mcp.py` drives real stdio against the existing coordination store in
isolated test worlds, including peer exchange, binding denial, retry identity,
dispatch exclusion, pagination and response bounds. It belongs to the CI profile.

`test_mcp_only.py` exercises actual helper processes, denied native calls,
permitted entry calls against the coordination store, invalid configuration,
and preserved advisory behavior outside this profile. Adapter tests exercise
the existing persistent protocol transports. These are not native-binary tests.

Stage 2 remains open until installed Codex, Claude Code and Grok each demonstrate
a permitted workflow call, failed shell/direct-write canaries, live peer
exchange during unfinished work, and reconnect/recovery with retained identity.
Record versions, requested versus observed settings, acknowledgment and
resolution separately. Claude's existing adapter currently reports a TUI and
channel capability probe; the profile does not add a live ingress channel or
claim that its busy delivery works. Do not enable native shell as a fallback
for a missing capability.

The private Serena v2 REPL route has passed its real PHP fixture check; native
participant acceptance is still open. Project actions, Mate, custom Makers and
the complete local acceptance remain stages of
[the readiness handoff](../../docs/dev-lane-mcp-readiness.md). The legacy
coordination and evidence server entry points remain compatible.
