# Behavioral preservation implementation

Owner basis: [complete supplied brief](owner-brief.md), sections 2–10. The original
attachment remains the external requirement input. No requirement has been removed.
Baseline: `30254fd0f184b7e31aa7e0896347429004933ed4`, clean fresh checkout of
`integration/hexagonal`; PR #4 remained open. Work is on
`repair/behavioral-preservation`; no merge is authorized.

## Shared contracts, revision 1

Conversation identity, connection incarnation, ownership, connection state and
activity are distinct. Submission acknowledges a bounded transport effect;
native receipt, reply, completion and acceptance are separate facts. Unknown
effects remain fenced but do not block observation or control.

Native drivers retain start/send/poll/stop and optionally implement deliver,
attach/detach/reconnect, and bind_observer(context, observation). Trusted context
contains the application session ID and principal. Native observations contain
conversation ID, incarnation, call ID and request/result phase. Ordinary caller
evidence cannot supply this provenance. Durable delivery binds the routed message,
recipient, attempt, application session and incarnation before a native effect.

Owner requirements and candidate revisions are separate from contributions.
Acceptance consumes current measurements under a replaceable acceptance policy;
assessment and authorization remain separate. Requested, resolved and observed
settings are recorded separately; unknown observations remain unknown.

## Delegation and integration

| Track | Requirements | Owned files | Baseline | Status |
|---|---|---|---|---|
| Lead | H3 H6 H7 A1 A2, integration of all IDs | application, ports, composition, delivery, delivery tests, execution/handoff | baseline above; contract 1 | Implemented; final integrated checks indexed in evidence |
| Transport | H2 H7 | adapters/jsonrpc.py, mcp.py, transport progress tests | contract 1 | Implemented; final integrated checks indexed in evidence |
| Sessions | H1 H2 H4 H5 B3 | adapters/sessions.py, Grok integration and tests | contract 1 | Implemented; final integrated checks indexed in evidence |
| Claude | H1 H2 H4 H5 B3 | adapters/claude*.py, Claude tests | contract 1 | Implemented; final integrated checks indexed in evidence |
| External lifecycle | H4 H7 B3 | adapters/bridge.py, bridge host, bridge tests | contract 1 | Implemented; final integrated checks indexed in evidence |
| Acceptance | B1 B2 A1 A2 | acceptance.py, adapters/acceptance.py, work acceptance tests | contract 1 | Implemented; final integrated checks indexed in evidence |
| Verification | H8 B4 B5 | scripts/verification, verification tests/docs | contract 1 | Implemented; final integrated checks indexed in evidence |

Agents inspect source and run focused checks. Their results are component evidence;
the lead will inspect changes and rerun the integrated checks. A later fresh-session
review remains pending regardless of internal review.

## Complete requirement matrix

All rows start with implementation in progress, deterministic verification not run,
native verification pending where applicable, and independent review pending.

| ID | Required behavior | Primary assertion families |
|---|---|---|
| H1 | Persistent three-harness native conversations | Private retained markers; observed identity across exchanges |
| H2 | Delivery and callbacks during outstanding work | Later callback after ack; held-tool and unfinished-work delivery; unrelated progress |
| H3 | Durable complete exchange correlation | Message/recipient/attempt/incarnation/native-call/reply/receipt joins; negative evidence |
| H4 | Ownership, attach, detach, resume, recovery | External host survives detach; source/destination authorization; stale connection rejection |
| H5 | Independent provider, permission, ownership, scheduling choices | MCP/CLI substitution; direct native permission allow/deny |
| H6 | Routed durable delivery and replaceable policies/storage | Atomic message/attempt/events; routing snapshot; substitutions |
| H7 | Honest uncertainty, replay and stale fences | Lost response/crash/duplicates/late completion; observation/control remain available |
| H8 | Active composed deterministic and local native acceptance | Six directed, three busy, six work exchanges; nine work batches; lifecycle |
| B1 | Owner objective survives decomposition and revisions | A+B, narrowed child, cancelled B; current owner basis and staleness |
| B2 | Sufficient current bound acceptance evidence | Claim/failure/running/unknown/stale/wrong-scope/native/artifact rejections |
| B3 | Requested/resolved/observed configuration fidelity | Nondefault choices, restart/resume/config changes/fallback/unknowns |
| B4 | Assertion-specific calibrated verification | Passing controls; targeted faults; setup/unrelated/skipped/escaped classifications |
| B5 | Independently checkable exact candidate and required coverage | Input snapshot; external criteria; omitted cases/fabrication/revision mismatch |
| A1 | Composable acceptance and policy | Actual substitutions; messaging usable without organizational workflow |
| A2 | Evidence identities at applicable boundaries | Durable delivery versus work acceptance bindings and necessary links |

## Evidence and decisions

Raw development runs live under `.xorchestrate/implementation-evidence/` until the
final evidence package is captured. Generated logs are excluded from measured
source inputs. Dependency installation and setup failures are not behavioral passes.
Authenticated native acceptance is assigned to xor, per the brief.

The persistent external-session bridge is the implemented attachment path: an
operator hosts an existing managed conversation at a configured endpoint, and
application clients attach/reconnect without owning or terminating its process.
This does not assert arbitrary terminal process injection.

## Final integration decisions

Contract revision 2 adds durable semantic native-call replay, source-binding checks
at each authorized effect boundary, pre-effect-only bounded callback retries,
uncertain-source inheritance across reconnect, and currentness checks when native
replay consumes acceptance. Contract revision 3 binds assessment routing and
relevant evaluator configuration, including a ToolEvaluator's provider dependency.
These changes were propagated across adapter, checker and acceptance tracks.

Internal review reproduced and repaired fallback authorization bypasses, stale
callbacks, late-result connection revival, selection races, duplicate semantic
native calls, conflicting submission identities, post-enqueue retry duplication,
stale acceptance replay, stale evaluator assumptions, missing native IDs and
late-turn activity replacement. Each has a named regression. See final command
records for the combined candidate; component passes and earlier dirty-snapshot
passes are not presented as the final integrated result.

The source references remain intact. Active core dependency tests reject imports
of native infrastructure and the retained historical runtime. Final verification
and calibration artifacts, requirement statuses and remaining environmental/native
limitations are indexed by HANDOFF.md and evidence/final-manifest.json.
