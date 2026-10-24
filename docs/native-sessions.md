# Native session adapters

The active application composes `codex`, `grok`, `claude`, and generic `acp`
session drivers. Commands and native permissions come from trusted configuration.
They are independent of the `mcp` or `cli` providers used by application tools.
No historical organizational roles or fixed evaluator policy are required.

## Submission and observations

`send_session` starts ordinary conversation work. `send_message` plus
`dispatch_deliveries` routes a retained message and calls the driver's native
`deliver` method with its message, recipient, attempt and incarnation envelope.
The native paths are:

| Adapter | Ordinary work | Routed delivery while active | Completion evidence |
| --- | --- | --- | --- |
| Codex | `turn/start` text input | `turn/start.toolOutput`, with empty input | `turn/completed` notification |
| Grok Build | idle `session/prompt` | `_x.ai/interject` | correlated ACP response and native prompt completion observations |
| Claude Code | MCP channel notification | `notifications/claude/channel` | native `Stop` hook with matching identity |
| Generic ACP | `session/prompt` | rejects a concurrent prompt without an interjection contract | asynchronous prompt response carrying `stopReason` |

A successful send or delivery result means submission. It does not mean receipt,
reply, completed work, or acceptance. ACP send returns after writing the request;
its later completion is retained separately. Callbacks and notifications progress
without a controller polling, while other sessions and native control remain usable.

Adapter observations enter the application through a trusted callback. They
retain the native conversation ID, connection incarnation, native call ID, action,
arguments, and source. A model's `record_result` arguments cannot manufacture
that provenance. Grok MCP execution additionally requires a matching independently
observed ACP tool execution. Catalog discovery is never execution. Conflicting
native call-ID reuse is rejected; repeated Codex native calls replay their prior
response without invoking the application again. MCP request replay is fenced
within each gateway connection. Grok associates MCP executions with exact native
action/argument observations one-to-one in observed order. When concurrent inputs
are identical, the association records all eligible native call IDs and its
ordered-match basis; it does not claim the MCP request itself supplied a native
call ID.

## Configuration and native permissions

A typical native driver selection is:

```json
{
  "sessions": {
    "codex-worker": {
      "adapter": "codex",
      "options": {
        "command": ["codex", "app-server", "--listen", "stdio://"],
        "pass_env": ["PATH", "HOME", "CODEX_HOME"],
        "application_tools": true,
        "thread": {"model": "OWNER_SELECTED_MODEL", "approvalPolicy": "on-request"},
        "timeout": 30,
        "callback_timeout": 300
      }
    },
    "grok-worker": {
      "adapter": "grok",
      "options": {
        "command": ["grok", "agent", "stdio"],
        "pass_env": ["PATH", "HOME", "GROK_HOME", "XAI_API_KEY"],
        "application_tools": true,
        "session": {"cwd": "/absolute/project/path", "mcpServers": []},
        "native_permissions": {"default": "deny", "allow": ["search_tool", "use_tool"]},
        "timeout": 30,
        "turn_timeout": 3600,
        "observation_timeout": 10
      }
    }
  }
}
```

Replace the model and workspace values. Grok model and effort selection belongs
in its configured launch argv, for example `grok --model MODEL --effort EFFORT
agent stdio`, subject to the installed CLI's actual options. Arbitrary delegated
ACP parameters are not a claim that the native provider implements them. The
resolved Grok receipt retains `launch_command`; native configuration updates and
startup `configOptions`/`models` remain independently observed evidence.

These examples are configuration shapes,
not a claim that an installed vendor version accepts every option or has been
authenticated. Local native acceptance must record installed versions and actual
observed settings.

Codex native approval requests may be handled by a separately bound trusted
handler, or by explicit `native_permissions` responses, for example
`{"item/commandExecution/requestApproval":{"decision":"decline"}}`. The adapter
does not implicitly approve native requests. Codex's own sandbox and approval
configuration still apply.

Grok `native_permissions` has `allow` and `deny` lists of native tool names, with
`default` set to `allow`, `deny`, or `delegate`. `delegate` uses a separately bound
trusted handler for unlisted tools. These choices govern native permission
requests; application callbacks separately pass the application guardrail. Native
MCP machinery may itself request permission, so deployments must configure its
names as appropriate for the installed version. Application gateway tool
catalogs do not advertise filesystem or terminal client capabilities implicitly.

The Grok adapter supplies a run-local MCP gateway when `application_tools` is
true. Its descriptor is a local capability protected by a private directory and
random token; the default gateway listens only on loopback TCP, and an explicit
Unix socket transport is available where the environment supports it. Its frame,
worker, and deadline bounds remain configurable. Claude uses the same gateway
for channel delivery and native hooks.

Native request parameters require a `request_parameters` JSON Schema in trusted
configuration. The default allows no additional parameters. Trusted `thread`
(Codex) or `session` (ACP/Grok) settings override delegated parameters; this
precedence is recorded. The material selection hash excludes transport deadlines
and capacity limits so operational tuning does not silently discard a selection;
the full `configuration_snapshot_id` still identifies those inputs for evidence.
Backend receipts retain separate `settings.requested`,
`settings.resolved`, and `settings.observed` values. Missing native observations
remain `null` or absent; requested values never become observed values merely
because launch succeeded.

## Lifecycle and recovery

The native conversation ID, driver instance, connection handle, incarnation, and
process ownership are separate. A persisted session receipt does not mean its
connection is alive after an application restart.

Use `resume_session_id` to resume a stored application session, or
`reconnect_session` with that source. The application checks the adapter-issued
source receipt and source-control/destination-creation authorization. Passing a
raw native ID in a start request is rejected. Native resumption is enabled for
Codex; ACP/Grok additionally require `resume_supported: true` and the actual
provider's `loadSession` capability.

Resumption with omitted parameters retains the source's requested selection.
A changed configured driver identity fails with `configuration_changed` unless
trusted configuration explicitly sets `allow_configuration_change_on_resume`
to `true`. An authorized change records its old and new identity. For a verified
same-instance replacement, the adapter then closes the old owned process and
invalidates its handle. Late events from the old incarnation do not become
observations of the replacement.

For an owned stdio driver, detach closes its owned process connection; a
resumable native conversation may then be resumed. For an externally managed
conversation, use the persistent session bridge. Detaching that client leaves
the externally owned native process alive; reconnect establishes a new bridge
incarnation. It does not attach by trusting an arbitrary PID supplied by a
caller. See the persistent bridge documentation and local acceptance procedure
for launching and attaching external Codex, Grok, and Claude conversations.

Grok retains unresolved interjections until matching handling or completion
evidence arrives. A late completion for an old prompt cannot clear newer native
activity. Lost responses and transport timeouts remain uncertain effects; they
are not permission for a fresh unqualified delivery.

## Deterministic verification

```bash
python -m pytest tests/hexagonal/test_sessions.py tests/hexagonal/test_session_resume.py tests/hexagonal/test_native_sessions.py -q
```

These tests run independent deterministic peers across actual subprocess and MCP
boundaries. They include private marker retention and resumed native identity,
post-ack callbacks, held-tool delivery, unrelated session progress, native
permission choices, callback/native evidence correlation, configuration changes,
and old-connection rejection. They do not constitute authenticated vendor runs.
