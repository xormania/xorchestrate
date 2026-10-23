# Runtime telemetry

> Historical reference: this document describes the imported Agentscient runtime in
> `devtools/`. Its roles, defaults, restrictions, and setup procedures are not
> requirements of the current Xorchestrate application. Start with the
> [current architecture](hexagonal-architecture.md) and [README](../README.md).

The private state directory retains `live.sqlite3` events and
`telemetry/<member>.jsonl` communications. Captures are append-only across restarts;
each frame includes the component, connection, process, member, generation, native
conversation, role and work binding when known. Multiple writers serialize appends.
MCP tool frames retain the effective per-request `work_order` and `job` separately
from the member's persistent selection, including explicit operator overrides.
Rejected requests with no resolved scope are marked `unresolved`. Directional
`wire_sequence` values preserve transport order across concurrent request handling;
outbound recording and transmission share one lock.
Files are owner-readable. They contain task content; keep the state directory private.
Credential keys, known secret environment values, configured provider credential
values and bearer credentials are redacted in events and traces, including JSON
nested in MCP text results. Provider references from every project, role and job
override are read at capture time, so file rotation and configuration changes take
effect without restarting; filtering does not alter transmitted arguments. An
invalid or unreadable credential source suppresses the captured payload with
`credential_filter_unavailable` while preserving the event envelope and normal
operation outcome. Evidence summaries count these captures under `suppressed_events`;
they do not count them as observed runtime settings, usage or completed calls. A
missing credential remains an ordinary absence. This is a credential
filter, not a promise to identify every secret someone types into prose.

Codex and Grok record incoming and outgoing native protocol frames, MCP traffic,
tool arguments/results, messages, streamed responses and exposed usage updates.
Claude records MCP channel traffic and hooks, and tails the native session's JSONL
transcript while it runs. SessionStart supplies its transcript path; only a matching
session file under the native Claude projects directory is accepted. A missing or
rejected transcript does not count as captured conversation content. Partial JSONL
writes wait for the complete line. An external CO connected through MCP has MCP
traffic captured; its private native conversation is not visible to this server.
The common `co` launcher additionally captures live native terminal output with
the same filtering, plus hooks and available Claude transcript entries. It does
not capture raw keystrokes. The separate Serena MCP gateway also records incoming
requests and outgoing results. See [session handover](session-handover.md).

`runtime_requested` records explicit and inherited harness/model/reasoning/provider
choices. `runtime_observed` records only fields actually reported by the native
protocol, hooks or transcript. These observations do not certify a vendor's internal
routing. Missing fields stay unknown. MCP client names and versions are retained
separately and are not used to guess a model. Session, usage, failure and delivery
events link to the same generation. Queued, delivering, submitted and acknowledged
messages remain distinct; delivery events retain their attempt ID and safe content.
Claude transcript readers stop at generation replacement. `monitor` projects
reports only from the current work binding and usage only from the current session
generation; earlier records remain available as history. Usage projections retain
their observed generation and native conversation identity.

Enabled Jev evaluations emit `jev_evaluation` events in the same work scope. These
record the purpose, input fingerprint, model, elapsed time, cache reuse and
retained evidence. Provider usage appears on fresh successful evaluations;
cache hits have `usage: null` and should not be counted as fresh consumption.
The referenced artifact includes the exact sanitized TypeSafe request, response,
binding and operator policy. `jev_feedback` connects an evaluation to a decision
update, and an outcome update to that decision. Feedback labels and report text
record the officer's assessment separately from Jev's probabilities. Recording a
feedback report does not allocate, revise or accept work; normal command tools do that.
The response retains the original evaluation's usage. Advice is recorded
separately from subsequent agent decisions and measured outcomes; a recommendation
does not establish that the agent followed it or that it improved the result.

Service decisions additionally retain `decision_selected`, `decision_escalated`,
`decision_resolved` and `decision_applied` events. These distinguish a decisive
evaluation from an unresolved question, its assigned Arbitrator's evidenced
resolution, and the observed continuation outcome. Original answers and policy
remain separate from an Arbitrator's resolution. Resolution does not
by itself prove that the resumed operation succeeded or that the choice improved
the final product. Current work, evidence and policy are rechecked before use.

Known failures also carry service-owned `failure.category`, `reason`, and trusted
`details` through MCP, retained operations, and events. Provider outcomes distinguish
unavailability, invalid responses, invalid requests, and unclassified failures.
They must not be counted as substantive judgments that work failed its objective.

`skill_guidance_delivered` retains the triggering call, failure, concrete suggested
actions, originating operation, context identity and configuration revision.
`recovery_action_observed` correlates a later exact call under that same context and
policy using `guidance_event`, `originating_operation`, and `subsequent_operation`.
`matches` distinguishes suggested actions from original-call retries. An original
retry with an observed successful outcome sets `condition_resolved: true`; a
failed retry sets false, and pending or unknown outcomes remain null. A suggested
check instead records its named measurement, evidence, observed and expected
revision, and workspace cleanliness. A worker's `completed` state does not
override a nested failed check. The correlation captures sequence and outcomes;
it does not establish that guidance caused a recovery. Old context/configuration
suggestions are not correlated with replacement work, and telemetry faults cannot
replace the tool outcome.

Structured `capability_gap` fields on `work_report` and `guidance_request` retain
agent-reported capability problems alongside the originating update and normal
routed message. Reports distinguish missing tools, discovery problems, permission
restrictions, tool failures and unknown causes. These are agent assessments;
failure telemetry remains candidate evidence rather than an automatic confirmed
gap. A successful workaround can still be reported as a gap.

The operator's capability-gap backlog groups exact normalized capability names
within the recorded or configured project repository; otherwise work is grouped
within its work order. Late repository metadata preserves a gap's ID and review
history. A work-order group joins repository grouping when that identity is free;
existing independently reviewed groups remain separate when it is occupied.
Occurrence keys distinguish fresh encounters from retries.
Operator reviews retain their classification, status, note and optional owner.
Resolving a gap requires evidence supplied by the operator; this is a recorded
judgment, not automated verification. A fresh occurrence reopens a resolved gap,
while duplicate delivery does not. History remains available through bounded,
paginated reads. See [operator review](../devtools/OPERATIONS.md#capability-gap-review).

Coverage starts with structured reports: old prose is not retrospectively parsed,
and silent workarounds are not inferred. An empty backlog does not establish that
the tools cover every need. Unresolved entries describe outstanding reports;
they are not a complete inventory of every current capability limitation.

Inspect operator `telemetry_events` / `telemetry_usage`, the JSONL traces, or run:

```sh
python3 devtools/run.py evidence --state /private/team --work-order 1
```

The evidence summary includes runtime records and trace paths alongside work,
delivery and validation observations. Protocol fixture tests exercise capture and
redaction, not authenticated model acceptance. Local acceptance still requires the
installed, logged-in harnesses and retained evidence from that run.

## Serena process observations

The Serena image installs a CPython audit hook in the interpreter executing the
REPL. The hook observes supported process-launch attempts, including subprocesses
started directly or through a shell. The gateway captures bounded records in the
existing `telemetry/<member>.jsonl` files with component `serena-mcp` and kinds
`process_audit` and `process_audit_status`. Records use the normal credential
filter and retain the provider, member, work binding and call context when known.
`gateway_connection` and `gateway_request_id` link a correlated observation to
the original MCP call; `request_id` identifies the separate backend request.
They do not include a child process's environment or output.

This is observe-only: recording an attempt neither blocks it nor changes the
agent's permitted MCP tools. Makers, checks and Git still use the existing
control-plane operations. A process audit event means that Python attempted the
operation; it does not prove successful execution, a completed command, policy
violation or host privilege escalation. Submitted REPL code remains separate
evidence from runtime observations.

Attribution distinguishes recognized backend activity, activity associated with
REPL execution, and unknown origins. Executable names alone cannot establish
authorization: a PHPactor-looking command launched by agent code is not evidence
that Serena's backend authorized it. Unknown activity requires inspection and is
not silently treated as expected. The operator report hides recognized expected
activity by default; `--include-expected` includes it for comparison.

Run the local report from the repository root, using the state directory supplied
to your CO/runtime launch:

```sh
python3 devtools/scripts/serena-audit.py --state /absolute/path/to/state
python3 devtools/scripts/serena-audit.py --state /absolute/path/to/state --include-expected
python3 devtools/scripts/serena-audit.py --state /absolute/path/to/state --json
```

Use `--actor MEMBER`, `--job ID` and `--since 2026-10-04T18:00:00-04:00` to narrow
the report. `--since` requires an ISO timestamp with a timezone. The report only
reads retained telemetry; it does not launch a test command, contact a harness or
change a running provider. Exit status is `0` for a completed report, `1` for
findings only when `--fail-on-findings` is supplied, and `2` when the selected
telemetry is missing, unreadable or incomplete. Inspect coverage diagnostics
alongside findings before interpreting an empty result. `--limit` bounds displayed
event details (default 100); totals still include the scanned records. If the scan
itself reaches its input limit, the report marks that gap and returns `2`.

Existing images must be rebuilt and providers restarted to install the hook; see
the [operator procedure](../devtools/OPERATIONS.md#review-serena-process-activity).
Startup status establishes that the hook reported installation for that provider,
not that every later operation was observed. Per-call completion records carry
the gateway's observer status, so filtering to a recent period does not require
the original startup record to fall inside that period. The report labels coverage
`best_effort`, `unavailable` or `incomplete`; none is a claim of exhaustive capture.
Missing monitor health, malformed or partial records, unfinished calls and
reported capture failures produce coverage gaps. Bounded command/stack details
are counted separately as `truncated_details`; this does not itself mean an event
was lost. Dropped events are reported only with the next delivered observer
record, so a final drop can remain undetected if no later record arrives.
Historical traces are not retroactively audited.

The hook is not a sandbox, an independent OS process observer or a tamper-proof
audit trail. Unrestricted Python can bypass Python-level observation, and child
processes do not automatically inherit this hook. Attribution and runtime metadata
come from the container and are not independent proof of origin. No findings
means no findings in the available records, not proof that process execution was
impossible or never occurred.
