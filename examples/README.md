# Configuration examples

These files illustrate composition choices. They do not prescribe a project stack,
agent hierarchy, model, workflow, or production policy.

## Minimal local use

After `python3 -m pip install -e .`, run these commands from the repository root:

```bash
python3 -m xorchestrate --config examples/minimal.json describe
python3 -m xorchestrate --config examples/minimal.json call create_work --json '{"goal":"Investigate a feature and retain the evidence"}'
python3 -m xorchestrate --config examples/minimal.json call list_work --json '{}'
```

This example is runnable without external tools, model accounts, or credentials.
It stores records at `examples/.xorchestrate/minimal.sqlite3`. Paths for the SQLite
adapter resolve against the config file's directory. Use a private state location
appropriate for your work when copying this config elsewhere.

The same configuration can expose the application as a stdio MCP server:

```bash
python3 -m xorchestrate --config examples/minimal.json serve
```

An MCP client launches that command, initializes the connection, and uses
`tools/list` and `tools/call`. The application tool names and schemas are the same
ones shown by CLI `describe`.

## Connecting tools, a worker, and optional assessment

`connected.json` is an executable-dependent template. Its external entries are
initially disabled. Before enabling an entry, replace the placeholder paths,
model names, and environment-variable names with the values for your installed
tools. The repository does not supply `project-tools` or `assessment-bridge`.
`codex` must be installed and authenticated if you enable that session driver.

| Entry | Expected implementation |
| --- | --- |
| Provider `project` | A stdio MCP server advertising the project tools you choose |
| Provider `judgment` | A CLI bridge reading one JSON request on stdin and writing one JSON result on stdout |
| Session `worker` | A Codex app-server process with application dynamic tools enabled |
| Evaluators `triage`, `deliberate` | Tool adapters selecting two models through the configured judgment capability |

Enable only what you use by setting its `enabled` to `true`. To use assessment,
enable `judgment` and both evaluator entries together, and replace their model
placeholders. Jev can supply triage through your bridge; the model identifier is
whatever that bridge supports. A direct TypeSafe evaluator is also available;
see its adapter contract if that transport suits your setup.

The example's `0.8` threshold and uncertain outcome names are illustrative,
editable assessment settings. The heavier evaluator is used when triage returns
low or missing confidence, or a configured uncertain outcome. An assessment
remains evidence and does not override action guardrails.

Provider and session processes receive no inherited environment by default.
`pass_env` forwards named variables if present; `env` supplies explicit values.
For example, the Codex entry forwards `PATH`, `HOME`, and `CODEX_HOME` so the
installed executable and its operator-selected configuration can be found.
Add the actual credential variable names required by your tools. Do not commit
credential values. `inherit_env: true` is available when forwarding the complete
parent environment is an intentional deployment choice.

The CLI judgment provider advertises an `assess` capability whose invocation
receives this envelope; the names below must match your bridge:

```json
{
  "name": "assess",
  "arguments": {
    "model": "the-selected-model",
    "question": "Does the supplied evidence justify the next action?",
    "context": {"evidence": []}
  },
  "context": {"purpose": "assessment"}
}
```

Its stdout must contain one JSON object in this form:

```json
{
  "outcome": "uncertain",
  "confidence": 0.55,
  "rationale": "The supplied evidence leaves an unresolved question.",
  "evidence": [],
  "details": {}
}
```

For an MCP-backed evaluator, the tool can return that object as
`structuredContent`, or as a single JSON text content block. These objects show
wire formats, not a fixture that pretends to evaluate a real question. A CLI
provider can alternatively use `discovery_command` to obtain its catalog instead
of the static `capabilities` array shown here.

## Bounded native parameter delegation

Put native execution policy in trusted driver options: Codex uses `thread` and
`turn`; ACP uses `session`, including configured MCP server commands. Caller
`specification.parameters` is empty by default. To let a Codex caller choose a
model from a configured set, add this fragment inside the driver's `options`:

```json
{
  "request_parameters": {
    "type": "object",
    "properties": {
      "model": {"enum": ["replace-with-allowed-model-a", "replace-with-allowed-model-b"]}
    },
    "additionalProperties": false
  }
}
```

Replace the example identifiers with models available to your harness. A caller
can then supply `"specification":{"parameters":{"model":"replace-with-allowed-model-a"}}`.
Unlisted fields and values fail before process creation. Configured `thread` or
`session` values override delegated values, so leave a field unset there if the
caller should choose it. Session identities and Codex dynamic tools remain owned
by the driver. This schema is an explicit delegation; a permissive schema would
delegate correspondingly broader native configuration choices.

## A persistent session conversation

After enabling and configuring the required connected entries, launch:

```bash
python3 -m xorchestrate --config examples/connected.json run
```

Keep this process open. Send the following JSON requests one line at a time,
reading each response before continuing. Replace every `<WORK_ID>` with
`result.work.id` from `create_work`, and every `<SESSION_ID>` with
`result.session.id` from `start_session`. Angle-bracket values are placeholders,
not values the application will resolve.

Register an actor and create work:

```json
{"action":"add_actor","arguments":{"actor_id":"worker","roles":["implementer"]}}
{"action":"create_work","arguments":{"goal":"Add the requested feature using the project tools to discover requirements, implementation context, and validation evidence."}}
```

Discover the configured project's capabilities:

```json
{"action":"capabilities","arguments":{"provider":"project"}}
```

Start a worker bound to the work and its actor identity:

```json
{"action":"start_session","arguments":{"driver":"worker","actor_id":"worker","work_id":"<WORK_ID>","specification":{},"idempotency_key":"example-start-1"}}
```

The Codex entry's `application_tools: true` exposes application operations such as
`capabilities`, `invoke`, and `record_result` as dynamic tools. Native calls use
the worker's bound identity and pass through application guardrails. Codex native
tools and approvals separately follow the configured native policy; this option
does not create a host sandbox or silently approve native requests.

Send a goal, then collect output:

```json
{"action":"send_session","arguments":{"session_id":"<SESSION_ID>","message":{"text":"Use the connected project tools to investigate the work goal, follow declared requirements, implement the change, and retain validation evidence."},"idempotency_key":"example-send-1"}}
{"action":"poll_session","arguments":{"session_id":"<SESSION_ID>","idempotency_key":"example-poll-1"}}
```

Sending starts a turn; it does not assert completion. For another fresh poll,
use a new key such as `example-poll-2`. Repeating the same key retrieves the
retained result of that poll. Every distinct effect needs its own key. An unknown
outcome requires inspection and reconciliation; changing the key is not a safe
retry strategy for an effect that may already have occurred.

If assessment is configured, ask about the retained work and supplied context:

```json
{"action":"assess","arguments":{"work_id":"<WORK_ID>","question":"Does the available evidence establish that the requested feature meets the declared requirements, or is further investigation needed?","context":{"open_questions":[]}}}
```

Read the assessment's evidence and unresolved status. It does not itself mark
work complete or grant permission. When finished with the conversation, stop it:

```json
{"action":"stop_session","arguments":{"session_id":"<SESSION_ID>","idempotency_key":"example-stop-1"}}
```

`run` and MCP `serve` preserve driver instances until their connection ends.
One-shot `call` closes its owned drivers at exit, so a series of independent
`call` invocations cannot share a live session handle. A stored native Codex
thread ID can be explicitly resumed by a new `start_session` with
`"specification":{"resume":"<NATIVE_THREAD_ID>"}` and a new operation key.
That is a new driver connection, not automatic recovery of a vanished process.
