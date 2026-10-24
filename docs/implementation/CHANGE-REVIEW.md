# Change and expectation review

The owner brief's H1–H8, B1–B5 and A1–A2 remain the scope. No owner requirement
was removed. Historical organizational roles, mandatory evaluator thresholds,
forced native tool restrictions and fixture-specific policies were not imported.

## Material contracts

- JSON-RPC dispatch and MCP request execution are bounded and concurrent. A
  callback after submission acknowledgement progresses without a controller poll.
  Native completion is a separate observation. CLI `run` remains ordered;
  concurrent operation is available through MCP.
- Codex uses `turn/start.toolOutput`; Claude uses native MCP channel notifications
  with independent hooks; Grok uses idle prompts and active interjections. Native
  permissions remain separate from application capability providers and guardrails.
- Messages retain the routing snapshot used at enqueue. Per-recipient delivery
  reservations, attempts and events are atomic. Session selection, scheduling,
  retry and receipt policy are supplied at composition. The default is explicit
  dispatch; immediate dispatch is configurable. A committed enqueue is never
  repeated because subsequent dispatch fails.
- Adapter-origin observations and semantic native-call reservations are durable.
  Caller reports remain caller reports. Native call-ID conflicts reject before
  effects, uncertain calls cannot repeat, and completed replay reauthorizes.
  Stale acceptance/read replay rejects obsolete currentness claims. Recovery of
  a native call requires its retained adapter-origin application result.
- Conversation identity, process ownership and connection incarnation are
  distinct. Shutdown marks connections disconnected. Resume/reconnect supersede
  the source connection. Explicit reconnect can restore observation/control
  while preserving unresolved source-effect fences. A late result cannot reopen
  a stopped, detached, disconnected or superseded connection.
- External native hosts have signed receipts, ownership-aware detach, replaceable
  client connections and private persisted recovery state. The implemented
  attachment path is an operator-hosted bridge. It does not inject into arbitrary
  terminal processes. FIFO and Unix endpoints are explicit transport choices.
- Requested, resolved and observed settings remain distinct. Material selection
  hashes include adapter protocol/contract and exclude operational timeout/capacity
  tuning; full configuration hashes still retain those inputs. Trusted settings
  precedence and explicit fallback are visible. Unknown native settings stay unknown.
- Owner objectives/outcomes, contributions, measurements, assessments and
  acceptance are separate records. Current candidate, requirement, checker/provider
  bindings and applicable evaluator configuration are rechecked at consumption.
  Mechanical measurements need not rerun solely because an unrelated evaluator
  changed; an assessment-consuming decision does invalidate when its actual
  evaluator or its underlying tool provider changes.

## Changes to existing expectations

- The trusted callback context now includes an application session ID and native
  identity fields where available. Tests assert their binding rather than an old
  smaller exact key set.
- ACP send returns submission promptly; its `stopReason` is collected as a later
  completion observation. Tests wait for the observation, not a synchronous turn.
- An uncertain effect still fences new sends, but poll and stop are available.
  The old test that required stop to fail was replaced by explicit uncertainty
  preservation and control-progress assertions.
- Runtime shutdown produces `disconnected`; stale subsequent CLI sends fail at
  the application state boundary before trying the dead driver. Resume retains
  its adapter receipt and source/destination authorization checks.
- Identical native call replay no longer invokes the application twice; conflicting
  call-ID reuse rejects. Missing native identity cannot fall back into an unobserved
  native application call.
- The existing early-stdout-close subprocess test now reports exit details and
  fixture markers on failure. An earlier unexplained process exit was retained;
  focused reproduction and 40 diagnostic repetitions passed. No unproven runtime
  change was made to conceal that failure.

## Verification interpretation

The full composed communication inventory is 46 cases, including 15 lifecycle
cases. That report is not B1/B2 acceptance coverage: those requirements map to the
work-acceptance tests and their targeted source mutations. The independent checker
recomputes predicates from raw records, retains required versus executed coverage,
and selects a separately identified candidate. Its hashes establish identity,
not correctness or owner approval. Section 11 of the owner brief remains the
fresh-session review procedure.

All initial failures, setup errors and invalid-candidate runs remain development
evidence. Only frozen-candidate final runs are authoritative for the delivered
code. Authenticated native behavior remains pending xor's local run.
