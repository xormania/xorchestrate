# Xorchestrate: implementation scope, behavioral preservation, and verification handoff

Prepared for **xor** on **2026-10-23**. Revised for **Astra Ultra** implementation.

This document combines the reconciliation and behavioral-preservation addendum into an implementation brief. It includes an implementation prompt and a separate verification prompt. Creating this document has not changed Xorchestrate. Invoking the implementation prompt below authorizes the described implementation work.

The reports in Appendices A and B are historical findings at their recorded revisions. Their original read-only task boundaries describe those inspections; they do not restrict the implementation task authorized by section 2. xor's subsequent instructions can revise this brief.

## 1. Model choice and prompt structure

**Recommendation: use GPT-6 Astra in Ultra mode for the main implementation.** Ultra uses subagents to work on meaningful parts of a complex task in parallel. This repair has suitable adapter, test, and verification tracks, while concurrency, lifecycle, delivery, and evidence contracts need coordinated ownership. Use the coordination instructions in section 7.

Official OpenAI documentation describes Astra as the strongest option for demanding work across steps and tools, and GPT-6.1 Sol as a capable lower-cost option for complex, sustained work. It describes Ultra as using subagents for parallel work. Selecting Ultra does not guarantee faithful integration; the lead remains accountable for the complete scope and measured results. The recommendation is task-specific judgment, not a measured Astra-versus-Sol result on Xorchestrate. [Models](https://learn.chatgpt.com/docs/models)

| Use | Recommendation |
|---|---|
| Own and coordinate the complete repair | GPT-6 Astra, Ultra |
| Run without parallel subagents if Ultra is unavailable | GPT-6 Astra, Extra High; preserve the same scope and checks |
| Cost-sensitive implementation after contracts and acceptance cases are clear | GPT-6.1 Sol, High or Extra High |
| Independent review in a fresh session | GPT-6 Astra, Extra High is a reasonable default; keep the same verification requirements |

The fresh review must inspect and rerun evidence. Choosing a different model alone does not make a review independent. Model choice does not change the requirements.

The prompt uses a clear outcome, relevant source references, explicit constraints, and observable verification, following the official [prompting guidance](https://learn.chatgpt.com/docs/prompting). It keeps a short launch instruction separate from the detailed requirements.

The five phases have acceptance checks throughout and a durable evidence handoff. This follows the demonstrated pattern of milestones, verification, and inspectable progress in [long-horizon Codex work](https://developers.openai.com/blog/run-long-horizon-tasks-with-codex). The document adapts that pattern to this task.

Existing repository conventions should hold the plan and evidence; there is no requirement to create a new hierarchy of process files. OpenAI's [workflow guidance](https://developers.openai.com/cookbook/examples/codex/iterating-development-workflows-with-codex) treats those file conventions as optional and recommends fitting them to the repository. Its example approval gates and source-of-truth claims are not adopted here.

Research checked on 2026-10-23. These recommendations concern the implementation agent, not the models that Xorchestrate must support.

## 2. Implementation prompt — attach this document and paste this block

```text
Implement the Xorchestrate repair defined in the attached
“Xorchestrate: implementation scope, behavioral preservation, and verification handoff.”

This is implementation authorization. Complete the source, tests, configuration,
and documentation changes needed for the goal and requirements in sections 3–7,
then produce the reproducible evidence handoff in section 8. Prepare the native
acceptance workflow in section 9 for me to run locally. Sections 10–11 define
completion and the subsequent independent verification.

Use Astra Ultra's parallel agents under one lead responsible for the complete
goal, shared contracts, requirement matrix, and final integration. Follow the
coordination instructions in section 7. Parallel source investigation and test
design may begin immediately; establish the shared interface semantics before
agents implement against them concurrently. Delegate meaningful tracks with
clear requirement IDs, contract baselines, file ownership, and evidence duties.
Coordinate shared edits and propagate contract changes to affected agents.
Subagent completion reports are inputs to verification. Inspect their changes
and verify the integrated candidate before claiming the combined work is done.

My current instructions govern. The appended reports provide source-derived
findings and reuse leads at recorded revisions. Their original read-only
instructions do not apply to this implementation task. Check the current
repository and preserve relevant work already present.

The goal is to carry the established Agentscient and multi-harness capabilities
into Xorchestrate's active hexagonal application with their intended behavioral
guarantees. Preserve all H1–H8, B1–B5, and A1–A2 requirements. Decomposing the work
does not narrow that goal. Keep every requirement visible until verified or
explicitly changed by me.

Keep acceptance and policy composable. Bind evidence at the boundaries where it
applies. Configuration may choose among implemented capabilities; it must not
conceal missing behavior. Historical roles, thresholds, policies, and fixture
restrictions are reusable source material and do not automatically become
requirements or mandatory configuration options.

Begin by recording the current revision and working changes, locating the
existing implementation and reusable mechanisms, and mapping the requirement
IDs to a concise execution plan. Then proceed with the repairs and relevant
checks. Do not stop after another audit or plan. Resolve routine implementation
choices autonomously and state material assumptions. Ask only for a genuinely
blocking owner decision; continue independent work while it is unresolved.

Reuse and adapt the existing mechanisms. Develop meaningful behavioral assertions
alongside each repair, exercise the active application and composed adapters,
and calibrate critical checks with targeted faults. Keep architecture boundaries,
authorization, recovery, and evidence integrity intact.

I will perform the authenticated native acceptance locally. Complete the work
and deterministic verification available in your environment, provide the exact
local procedure, and keep native coverage explicitly pending until measured.
Investigate environmental or protocol limitations with evidence; do not turn
them into architectural exclusions or silently reduced scope.

Leave reviewable changes and an exact, recoverable candidate for another session.
Follow the existing authorized branch/PR workflow; do not merge on my behalf.
Do not reset unrelated work. Keep source repositories used as references intact.

Return a requirement-by-requirement result, exact check commands and outcomes,
retained logs/results, fault-calibration evidence, candidate/checker identities,
remaining gaps, and the handoff location. A completion claim or a green test total
alone is insufficient. Use the separate implementation, deterministic, native,
and independent-review states defined in this document.
```

## 3. Goal and authority

**Goal:** make Xorchestrate's active hexagonal application support the intended persistent multi-harness orchestration, verified delivery, lifecycle flexibility, configuration fidelity, and behavioral acceptance capabilities already established in the source projects, with reusable verification and evidence another session can inspect and reproduce.

The core must express the necessary behavior through appropriate ports and application responsibilities. Native protocols, transports, storage implementations, and deployment choices belong at replaceable boundaries. Simple configurations must remain practical.

The scope is the combined eight capability areas and five behavioral areas below, plus the two approved architectural interpretations. A requirement's ID is a tracking aid, not an immutable requirement: xor may revise its meaning or applicability explicitly. Record such changes and reassess affected evidence.

Plans, subordinate tasks, cancelled contributions, current implementation limitations, and historical documents cannot silently narrow the owner goal. Missing facts should trigger investigation. A demonstrated limitation remains an explicit unresolved item with evidence and possible approaches until xor decides its effect on scope.

## 4. Source map and recorded baseline

The following revisions anchor the supplied reports; they are not claims about the latest repository state. Establish the actual starting candidate before implementation and reconcile relevant changes without restarting the entire audit.

| Source | Recorded revision and useful material |
|---|---|
| [Xorchestrate](https://github.com/xormania/xorchestrate), [PR #4](https://github.com/xormania/xorchestrate/pull/4) | `integration/hexagonal`, `30254fd0f184b7e31aa7e0896347429004933ed4`; active application, ports, composition, adapters, tests, acceptance scenario, and retained source |
| [Agentscient dev](https://github.com/uscient/agentscient/tree/dev) | `91506128934b70a8967c626d6ece16d2c8a0815c`; completion, runtime selection, fault detection, native lifecycle, RPC, durable attempts |
| [Agentscient older branch](https://github.com/uscient/agentscient/tree/shit) | `456457b9a0a9eb1ff285ee2d3e40535b6f722cdb`; `ops/testkit` isolation, calibration, and checker comparison |
| [Separate acceptance fixture](https://github.com/uscient/agentscient-test-repo) | `7d1abb3c4ac01451d83e1c1e12742b9fa2067561`; independent product checker, candidate/evidence binding, requested coverage, adversarial tests |
| [Multi-harness PoC](https://github.com/xormania/multi-harness-proof) | Confirming runtime/verifier `a944f31b7527807d000da985c546e8b5bc9ff476`; published documentation/current head in the reports `ad294398501ed119a436db54eca784be2fb10e76`; adapters, native observations, verifier, behavior scenarios, live results |

The reports identified useful dependency boundaries but incomplete behavior transfer. In particular, native busy delivery, concurrent callback progress, correlated receipt evidence, lifecycle ownership, and the full completion/acceptance semantics were incomplete in the active application. Some relevant tests existed unchanged under the imported historical runtime.

The original inspections did not execute a successful behavioral verification. The addendum's attempted memory-only probe stopped at a missing `jsonschema` import. Working-tree observations in the appendices describe the reporting session's checkout, not this document's environment.

## 5. Approved architectural interpretations

### A1. Acceptance and policy remain composable

Implement and verify the intended acceptance capability as a replaceable responsibility. Deployments may select their acceptance, evaluation, authorization, and coordination policies. Preserve the distinction among measurement, assessment, authorization, and acceptance.

No fixed job hierarchy, mandatory Jev threshold, or universal approval workflow follows from the source tests. A simple messaging deployment should not need to adopt an unrelated organizational model. Composability does not justify omitting the required acceptance capability.

### A2. Evidence is bound where applicable

A delivery receipt needs the relevant message, recipient, attempt, session incarnation, and native observations. A work-acceptance decision needs the applicable requirements, measured candidate, criteria, and supporting evidence. Link these records when the decision depends on their relationship.

Do not require every record to carry every possible identity. Keep the information needed for each assertion at its proper boundary and make the required associations durable and inspectable.

Historical policies remain reference material. Implement a historical policy as a configuration option when the intended capability requires it or xor requests it. Discovery alone does not make every old restriction a mandatory option.

## 6. Combined requirements

### H1–H8: native orchestration, delivery, and lifecycle

| ID | Required outcome | Verification obligation |
|---|---|---|
| H1 | Persistent Codex, Claude Code, and Grok Build conversations retain context and native identity across exchanges. | Private retained markers, repeated exchanges, and independently observed native identities through the active composition. |
| H2 | Native delivery works during an outstanding tool call and unfinished work. Submission, ongoing activity, and control are distinct; callbacks and unrelated sessions continue to progress. | Acknowledge submission, issue a later native callback, hold work outstanding while delivering, and check concurrent progress. Exercise each proven native path. |
| H3 | Message and recipient are durably correlated with delivery attempts, session incarnations, native observations, replies, and receipts. Submission, receipt, response, turn completion, and task acceptance remain distinct facts. | Apply the complete exchange predicate; reject queue-only or RPC-only success, wrong/missing markers, wrong identity, and missing receipt. Preserve adapter-origin observations separately from caller statements. |
| H4 | Creation, ownership, externally managed attachment, detachment, resumption, and recovery have explicit semantics. Conversation identity, connection liveness, incarnation, and process ownership are distinct. | Detach without terminating externally owned processes; reject stale connections; verify reconnect/resume context; implement and test attachment through suitable endpoints or bridges. Investigate each harness's actual interface. |
| H5 | MCP/CLI provider selection, native-tool permissions, lifecycle ownership, and delivery scheduling remain independently configurable. | Swap providers while preserving application behavior; exercise allowed/denied native actions separately from application-mediated invocations. Complete the missing integrations. |
| H6 | Routing leads to durable native delivery with replaceable routing, session selection, scheduling, retry, receipt/acceptance policy, and storage boundaries. | Check atomic message/attempt/event behavior and recovery. Exercise meaningful substitutions through the same contracts. Define routing-snapshot handling or validate changed routing inputs before commit. |
| H7 | Retry and recovery preserve unknown outcomes, replay protection, duplicate handling, attempt/incarnation fencing, and stale-observation rejection. Observation and control remain available while an effect is unresolved. | Inject lost responses, crashes, duplicates, late completions, and replaced connections. An uncertain effect must not silently become an unqualified fresh delivery. |
| H8 | Deterministic and local native acceptance exercise Xorchestrate's actual application, routing, delivery, storage, adapters, and evidence. | Reuse the PoC verifier and fixtures through the new composition. A standalone legacy run or imported-runtime test pass cannot satisfy active-application coverage. |

Proven native mechanisms include Codex `turn/start.toolOutput`, Claude MCP `notifications/claude/channel`, and Grok idle `session/prompt` with active `_x.ai/interject`. Reuse their implementation and lessons while checking compatibility with the installed versions. If a mechanism must change, demonstrate equivalent behavior and record why.

Preserve the existing resume protections: adapter-issued receipt, authorization over source control and destination creation, and rejection of caller-manufactured native bindings. Keep transport bounds and uncertain-effect records during concurrency changes.

### B1–B5: owner outcomes and acceptance

| ID | Required outcome | Verification obligation |
|---|---|---|
| B1 | Full owner objectives and required outcomes survive decomposition, narrower plans, and cancelled contributions. Owner revisions govern current applicability. | Require A+B, narrow a child to A, cancel B's contribution, and keep B visibly unresolved. Explicitly revise the owner criteria and verify the new basis applies. Reject stale applicability after changes during or after assessment. |
| B2 | Configured acceptance consumes sufficient, current, correctly bound evidence. | Reject claim-only proof, missing outcomes, required mechanical failures, running/uncertain measurements, stale candidate evidence, unrelated passing checks, and wrong scope/identity/artifact bindings. A favorable assessment cannot erase a failed required condition. |
| B3 | Requested, resolved, and independently observed settings are distinct. Nondefault choices survive applicable lifecycle changes. Substitution follows explicit configuration and is observable. | Test persistence, restart/resume, same-name driver configuration changes, unavailable choices, configured fallback, and disallowed fallback. Keep native-unobserved settings explicitly unknown. Preserve and expose intentional trusted-config precedence. |
| B4 | Critical verification detects the intended deliberate violation. | Establish a passing baseline, plant a targeted fault, and require the expected assertion or diagnostic. Record setup errors, unrelated failures, skips, escaped faults, and correct detection separately. |
| B5 | Acceptance is independently checkable against the owner-approved requirements and exact candidate actually measured. | Keep requested coverage separate from discovered/executed coverage. Review changed expectations; reject missing required cases, fabricated summaries, and another revision's evidence. Preserve xor's ability to revise criteria explicitly. |

Useful source entry points include [completion tests](https://github.com/uscient/agentscient/blob/91506128934b70a8967c626d6ece16d2c8a0815c/devtools/tests/test_completion.py), [runtime selection](https://github.com/uscient/agentscient/blob/91506128934b70a8967c626d6ece16d2c8a0815c/devtools/tests/test_runtime_selection.py), [fault calibration](https://github.com/uscient/agentscient/blob/91506128934b70a8967c626d6ece16d2c8a0815c/devtools/tests/test_fault_detection.py), and the [independent product contract](https://github.com/uscient/agentscient-test-repo/blob/7d1abb3c4ac01451d83e1c1e12742b9fa2067561/acceptance/product/README.md).

### Evidence limits that must remain visible

- The owner-goal test establishes preservation of input to assessment. Its mocked evaluator does not prove model recognition of an incomplete objective. Add deterministic outcome-coverage checks where appropriate.
- Older testkit calibration counts any nonzero exit as detection and archives `HEAD`. Adapt its reusable isolation/comparison mechanisms with assertion-specific detection and exact measured-candidate identity.
- The PoC's predefined fault cases inspect specific evidence. Custom faults need sufficient attribution to distinguish the intended failure from an unrelated failure.
- The live communication proof remains confirmed despite requested-versus-observed Grok setting differences; Claude's effective effort was not independently established. That run does not establish complete configuration fidelity.
- The PoC launched fresh sessions. Attachment of unrelated open sessions needs its own implementation and verification.
- Busy delivery means submission during the outstanding tool hold followed by a verified exchange. It does not establish interruption during token generation.
- The recorded unfinished-work replies preceded work completion. Preserve and report that timing separately.
- Duplicate evidence checks do not establish exactly-once delivery. Retain honest uncertainty and measure the implemented delivery guarantees.

## 7. Implementation sequence and work discipline

### Ultra coordination

Use one lead agent to maintain the complete owner goal, requirement matrix, shared contracts, execution dependencies, and final candidate. This is coordination for implementing Xorchestrate; it does not impose a role hierarchy on the product.

1. **Establish a shared baseline.** Record repository/working state and agree the minimum session, delivery, lifecycle, and evidence semantics needed by the next implementation tracks. Source investigation, fixture discovery, and test design can proceed in parallel immediately. Begin concurrent implementation against shared interfaces once those interfaces are sufficiently defined. Contracts may evolve when evidence warrants; record the change and update affected tracks.
2. **Delegate bounded, meaningful work.** Each assignment carries the relevant H/B/A IDs, owner outcome, candidate/contract baseline, dependencies, file ownership, expected behavior, and required checks/evidence. Suitable tracks include individual native adapters, delivery/acceptance work after its dependencies are ready, and independent test or architecture review. Choose the number of agents from actual parallel work.
3. **Coordinate shared changes.** Give shared files or contracts a clear editing owner for each concurrent interval. Use disjoint file ownership or isolated worktrees when useful. An agent needing a shared contract change coordinates with the lead; the lead checks its effect on all consumers and tests. Preserve other agents' and xor's work. Run fault planting and mutable test fixtures in isolated code/runtime state with distinct outputs, so one track cannot contaminate another track's measurements; clean up owned test resources.
4. **Require evidence with each handoff.** Each agent returns changed files or commits, requirement IDs addressed, actual commands/results and evidence paths, assumptions, unresolved items, and any proposed change to expectations. Retain the measured baseline for those results. A child's narrower assignment or cancellation cannot erase a parent requirement.
5. **Integrate and verify continuously.** The lead inspects contributions, resolves conflicts, and runs affected cross-adapter, architecture, behavioral, and calibration checks against the integrated candidate. Results from separate worktrees or earlier contract revisions do not alone establish that the final composition passes. Recheck affected behavior after integration changes.
6. **Preserve independent review.** An internal reviewer can catch defects during the run. The separate fresh-session verification in section 11 still inspects the final candidate and reproducible evidence. Keep implementation, deterministic, native, and independent-review states distinct.

If parallel tools are unavailable, continue the same authorized work sequentially and record that execution choice. Parallelism changes scheduling, not scope or completion criteria.

### Repair phases

| Phase | Implementation focus | Evidence developed in that phase |
|---|---|---|
| 1 | Correct session execution semantics, concurrent callback/event progress, and distinct activity/control states. Establish current requirement and assertion inventory. | Submission versus completion, later callbacks, responsive unrelated sessions, owner outcome representation, and initial targeted failure cases. |
| 2 | Integrate all three native adapters and their configured permissions/settings. | Shared adapter contracts, native identity, context retention, busy/work delivery, and requested/resolved/observed selection. |
| 3 | Connect routing, durable delivery, evidence associations, and composable acceptance. | Complete exchange verification, missing outcomes, stale/wrong proof, duplicates, uncertainty, and transactional/routing consistency. |
| 4 | Complete lifecycle ownership, attachment/detachment, recovery, and acceptance applicability across changes. | Authorized resume, stale-incarnation rejection, context continuity, changed requirements/configuration, and explicit fallback. This may overlap phases 2–3 after phase 1. |
| 5 | Complete independent composed acceptance and the local native handoff. | Aggregate earlier assertions and calibrations; verify exact candidate/criteria/coverage binding; prepare and later incorporate the local native results. |

Inspect the current repository once, establish a baseline, then maintain a concise requirement matrix and execution record in existing suitable locations. Add necessary rows or subcases without losing the original IDs. The matrix should survive session handoffs or context compaction.

Reuse the PoC, extracted mechanisms, and existing fixtures where suitable. Keep core dependency direction enforceable and show that replaceable responsibilities actually substitute through contracts. Do not import historical runtime dependencies into the new core merely to make historical tests pass.

Develop focused positive and negative checks alongside each behavior repair; run relevant broader regression and architecture checks after integration. Calibrate critical assertions with deliberate violations. Fix applicable failures and preserve failed-attempt evidence. A missing dependency or environmental failure is not a behavioral test result.

Record material implementation choices and their rationale. Routine choices do not need another scope decision. Genuine owner-direction conflicts require a precise question and evidence; independent work continues. A temporary limitation remains visible and does not redefine completion.

## 8. Evidence handoff for another session

Produce one clearly located, retrievable evidence package using existing repository conventions. A small set of Markdown, JSON/CSV, and logs is sufficient; no new evidence platform is required. Keep raw sensitive native traces private and provide usable redacted derivatives with their provenance.

| Artifact | Required content |
|---|---|
| Handoff README | Exact candidate location, base revision, current status, setup prerequisites, reproduction commands, evidence index, known gaps, and local acceptance instructions |
| Requirement matrix | Every H1–H8, B1–B5, A1–A2 ID; source behavior; changed implementation; assertions; implementation/deterministic/native/review status; evidence paths; residual limitations |
| Candidate and run manifest | Repository, branch, base and candidate commit/tree; measured working changes if any; criteria/checker/fixture/configuration identities; tool/dependency versions; timestamps; evidence cutoff |
| Executed checks | Actual command, working directory, material environment/configuration, start/end, exit status, retained machine-readable results and relevant raw output |
| Calibration record | Passing control, targeted mutation, expected assertion/diagnostic, observed result, unaffected controls, and escaped faults or environmental failures |
| Delegation and integration record | Assigned requirement IDs and contract baselines, contribution identities, checks measured before integration, shared-contract changes, and checks rerun against the integrated candidate; a compact table in the existing execution record is sufficient |
| Change and expectation review | Material code/contract decisions; tests/checker/criteria changed and why; any changes in expected behavior; migration or compatibility implications |
| Local native acceptance package | Runnable procedure/configuration, expected case inventory, evidence outputs, interpretation instructions, cleanup, and pending cases |

For uncommitted or untracked implementation inputs, provide a recoverable patch/content snapshot and hashes. A bare `HEAD` value is insufficient when the measured working tree differs. Identify generated evidence separately so subsequent log writes do not pretend the measured code changed. If relevant code, configuration, fixtures, or checker inputs change after a run, rerun affected verification.

Use hashes to identify artifacts and detect mismatch. Hashes do not establish that a checker is correct or independent. Keep the requirement basis available outside candidate-controlled expectations, expose checker changes for review, and allow the fresh session to inspect and rerun the verification.

The final response must state the candidate and evidence locations, summarize outcomes per requirement, and distinguish executed checks from inspected source or historical results. Make the package accessible to the next session through the established repository/artifact workflow; a transient path alone is not a sufficient handoff.

## 9. Local native acceptance owned by xor

Complete implementation, deterministic verification, and the local procedure before handing off. xor will run authenticated native acceptance locally. Environment-specific setup should be configurable; its absence in a cloud session does not remove any requirement.

The native procedure must cover:

1. Fresh persistent Codex, Claude Code, and Grok Build sessions with private retained markers.
2. Six directed exchanges, three outstanding-tool submission cases, six unfinished-work exchanges, and nine work batches through Xorchestrate's active composition.
3. Correlated challenge, submission, retained-context reply, sender receipt, and independently matching native observations.
4. Separate communication, work correctness, turn-completion, and acceptance results.
5. Installed versions and requested/resolved/native-observed settings, with mismatches and unknowns retained.
6. Separate resume, reconnect, externally managed attachment, detachment, and replaced-connection scenarios with ownership-aware cleanup.
7. An inspectable report and retained evidence that the fresh verifier can correlate with the exact measured candidate.

Audit every actual required native call. The historical 69 matched calls are evidence from that run, not a fixed acceptance count for every composition.

The deterministic suite must include applicable reusable cases such as queue-only delivery, wrong memory, stale nonce, missing native observations/hooks, session drift, conflicting call-ID reuse, discovery without execution, wrong dispatch, unsupported interjection, missing native completion, and incorrect work despite successful transport.

Keep the busy and unfinished-work timing predicates precise as described in section 6. Report native acceptance as pending until the corresponding run actually exists.

## 10. Completion and handoff states

Track these dimensions separately for each applicable requirement:

| Dimension | Example states |
|---|---|
| Implementation | Not started / In progress / Implemented / Blocked |
| Deterministic verification | Not run / Passed / Failed / Blocked |
| Native verification | Pending local run / Passed / Failed / Not applicable with explanation |
| Independent review | Pending / Verified / Findings |

An implementation-ready handoff requires all implementable scope completed, relevant deterministic and architecture checks passed, calibrated critical assertions, a recoverable candidate, and the complete evidence/native-run package. A genuine unresolved implementation constraint must remain explicitly blocked; it cannot be disguised as native-only pending work.

Full behavioral acceptance requires all applicable required coverage, including the local native scenarios, plus resolution or explicit owner disposition of independent-review findings. An unavailable required check stays incomplete. Existing standalone PoC results, copied tests, test totals, and candidate-authored success summaries cannot fill a missing cell.

A later owner change creates a revised requirement basis and triggers reassessment of affected evidence. It does not erase the history of what was actually measured.

## 11. Fresh-session verification prompt

Attach this brief and provide access to the implementation candidate and evidence package. Paste the following into the independent session:

```text
Independently verify the Xorchestrate implementation against the attached
“Xorchestrate: implementation scope, behavioral preservation, and verification handoff.”

Use its current requirements H1–H8, B1–B5, A1–A2 and any subsequent explicit
instructions from xor. The implementation agent's summary is a claim to inspect.

Work in an isolated checkout or equivalent review environment. Do not repair the
candidate, merge changes, rewrite expected requirements, or conceal failures.
Running verification and writing your review artifacts is authorized.

1. Identify the exact candidate, base revision, relevant working-change snapshot,
   checker/criteria/fixtures/configuration, and evidence package. Confirm that
   the submitted results correspond to those inputs and remain reproducible.
   Inspect the delegation/integration record: component results from different
   worktrees or contract revisions must not be presented as proof that the final
   integrated candidate passed. Identify the checks actually run after integration.

2. Trace each requirement through the active application, ports, adapters, and
   composition. Identify historical-runtime-only coverage. Inspect dependency
   direction and actual substitutability, composable acceptance/policy, and
   evidence binding at the appropriate boundaries.

3. Review changes to tests, checkers, coverage discovery, and expected outcomes.
   Compare them with this brief and the source behaviors. Hashes identify files;
   they do not establish that the oracle remained faithful.

4. Set up and rerun the meaningful deterministic, architecture, composed
   acceptance, and targeted calibration checks. Confirm passing controls and
   assertion-specific detection. Classify setup errors, skipped cases,
   unrelated failures, and escaped faults honestly.

5. Inspect the supplied native run when available: candidate/configuration
   identity, all required directed/busy/work cases, retained context, native
   call/receipt correlation, timing, lifecycle coverage, and setting mismatches.
   Do not launch authenticated sessions on xor's behalf without authorization.
   If no native evidence is available, keep that coverage pending.

6. Produce a per-requirement verdict with evidence and reproducible commands.
   Distinguish checks you reran, source/evidence you inspected, historical claims,
   unverified requirements, and contradictions. Prioritize findings by their
   effect on the intended behavior and give a minimal reproduction where possible.

Report separately whether implementation/deterministic work is verified,
whether it is ready for local native acceptance, and whether full behavioral
acceptance is supported. Missing native evidence cannot produce a full pass.
Do not let a green test total override a missing requirement.

Return the candidate identity, review report location, requirement matrix,
commands/results, findings, and the remaining work or owner decisions.
```

## 12. Preserved report provenance

The two supplied reports are included below without content edits, apart from line-ending normalization. Their dated findings and uncertainty qualifications are retained for traceability. The active goal and execution instructions are in sections 2–10; section 11 is invoked separately for verification.

| Input | Original byte SHA-256 |
|---|---|
| `Pasted text.txt` — original reconciliation | `31dba40d8ba21eca9cad9a762472dbf4393c61379780f99886433ae2daa799cb` |
| `Pasted text(1).txt` — behavioral-preservation addendum | `96cefd0f14bc74a758c7b4fc5dc014da6c5533de058b6732980308be531c8320` |



## Appendix A. Original reconciliation report — historical source

| Capability or requirement | Existing source/evidence | Current Xorchestrate implementation | Assessment and exact gap | Required repair | Verification |
|---|---|---|---|---|---|
| **1. Persistent native sessions and retained context—all three harnesses** | The confirmed proof records persistent Codex, Claude Code and Grok Build conversations with retained context across exchanges. [Recorded results](https://github.com/xormania/multi-harness-proof/blob/ad294398501ed119a436db54eca784be2fb10e76/LIVE_RESULTS.md#L20-L45). | Codex thread creation/resumption and generic ACP session creation/loading exist. The supplied driver composition has no Claude channel implementation or Grok-specific integration. [Drivers](https://github.com/xormania/xorchestrate/blob/30254fd0f184b7e31aa7e0896347429004933ed4/xorchestrate/adapters/sessions.py#L350-L439), [composition](https://github.com/xormania/xorchestrate/blob/30254fd0f184b7e31aa7e0896347429004933ed4/xorchestrate/composition.py#L17-L28). | **Partially retained; missing implementation and verification.** Persistent handles exist, but the new composition does not establish the three-harness context-retention capability. | Integrate all three native paths, preserving conversation identity across deliveries. | Startup private markers, repeated exchanges and independently observed native identities; verify retained markers across all six directed harness pairs. |
| **2. Native delivery during outstanding tools and unfinished work** | Proven mechanisms: [Codex `toolOutput`](https://github.com/xormania/multi-harness-proof/blob/a944f31b7527807d000da985c546e8b5bc9ff476/mhproof/adapters.py#L214-L230), [Claude channel](https://github.com/xormania/multi-harness-proof/blob/a944f31b7527807d000da985c546e8b5bc9ff476/mhproof/mcp.py#L28-L73), [Grok prompt/interjection](https://github.com/xormania/multi-harness-proof/blob/a944f31b7527807d000da985c546e8b5bc9ff476/mhproof/adapters.py#L377-L406). | Codex sends ordinary text input and returns after `turn/start`; ACP waits for `session/prompt` completion. Another session operation is blocked while one remains pending. [Send implementations](https://github.com/xormania/xorchestrate/blob/30254fd0f184b7e31aa7e0896347429004933ed4/xorchestrate/adapters/sessions.py#L368-L439), [reservation](https://github.com/xormania/xorchestrate/blob/30254fd0f184b7e31aa7e0896347429004933ed4/xorchestrate/application.py#L544-L553). | **Architectural and implementation gaps.** Submission and turn lifetime are coupled differently by adapter. The established busy-delivery mechanisms were omitted from the active composition. | Separate submission, ongoing activity and control; provide concurrent callback/event handling; integrate the proven native delivery mechanisms. | Hold a native proof-tool call outstanding, submit a challenge, then verify its complete exchange. Separately exercise delivery during unfinished work. |
| **3. Correlated evidence; receipt versus completion** | The verifier joins challenge, retained marker, reply, sender receipt, both submissions and matching native calls. [Verifier](https://github.com/xormania/multi-harness-proof/blob/a944f31b7527807d000da985c546e8b5bc9ff476/mhproof/suite.py#L20-L81). | Inbox acknowledgment records a recipient-issued statement. Native sending and polling produce separate operation/evidence records. There is no durable join from message/recipient to native delivery and verified response. [Messaging](https://github.com/xormania/xorchestrate/blob/30254fd0f184b7e31aa7e0896347429004933ed4/xorchestrate/application.py#L391-L427), [session effects](https://github.com/xormania/xorchestrate/blob/30254fd0f184b7e31aa7e0896347429004933ed4/xorchestrate/application.py#L617-L647). | **Missing behavioral contract and implementation.** An acknowledgment is not independently observed native receipt. Operation completion means the adapter call returned; it does not establish task completion. | Correlate message, recipient, delivery attempt, session incarnation and native observations. Preserve submission, receipt, response, turn completion and task acceptance as distinct facts. | Reuse the proof predicate and negative cases: queue-only, RPC-only, missing/wrong marker, wrong native identity and missing receipt must not pass. |
| **4. Creation, ownership, attachment, detachment and resumption** | The proof launched fresh persistent sessions; unrelated-session attachment was outside its implementation. [Measured scope](https://github.com/xormania/multi-harness-proof/blob/ad294398501ed119a436db54eca784be2fb10e76/REFERENCE.md#L27-L35). | Built-ins own subprocesses. Codex resume and capability-gated ACP loading exist; application-authorized resume has receipt checks. No explicit attach/detach behavior exists. Stored sessions can remain `active` after runtime shutdown. [Ownership](https://github.com/xormania/xorchestrate/blob/30254fd0f184b7e31aa7e0896347429004933ed4/xorchestrate/adapters/sessions.py#L89-L169), [resume regression](https://github.com/xormania/xorchestrate/blob/30254fd0f184b7e31aa7e0896347429004933ed4/tests/hexagonal/test_session_resume.py#L62-L86). | **Creation/resume partially implemented; ownership contract incomplete.** Connection liveness, conversation identity and process ownership are conflated. Attachment is additional required investigation, not disproven feasibility. | Separate those identities/states; support explicit owned and externally managed lifecycle behavior, including detach. Investigate and implement attachment through suitable native endpoints or bridges. | Preserve resume authorization tests; add detach-without-termination, stale connection rejection, reconnect/resume context checks and controlled attachment tests. |
| **5. Configurable MCP/CLI providers and native-tool permissions** | MCP/CLI selection and native permissions are independent requirements. The proof’s restricted tool configuration was experimental setup. [Proof configuration](https://github.com/xormania/multi-harness-proof/blob/ad294398501ed119a436db54eca784be2fb10e76/REFERENCE.md#L174-L207). | MCP and CLI capability providers are replaceable. Codex native settings and ACP client capabilities/handlers are separate from application-tool binding. Neutral custom callback binding has tests. [Composition](https://github.com/xormania/xorchestrate/blob/30254fd0f184b7e31aa7e0896347429004933ed4/xorchestrate/composition.py#L17-L28), [native settings](https://github.com/xormania/xorchestrate/blob/30254fd0f184b7e31aa7e0896347429004933ed4/xorchestrate/adapters/sessions.py#L263-L273), [callback tests](https://github.com/xormania/xorchestrate/blob/30254fd0f184b7e31aa7e0896347429004933ed4/tests/hexagonal/test_composition.py#L259-L309). | **Useful separation retained.** Supplied native permission/tool integrations are not complete or verified across all three harnesses. Configuration cannot supply the missing implementations. | Complete adapter-specific tool access and permission handling while keeping provider choice, native permissions, ownership and scheduling independently configurable. | Swap MCP/CLI providers without changing application behavior; test allowed/denied native actions separately from application-mediated invocations. |
| **6. Routing, persistence and delivery policy boundaries** | Replaceable routing and transactional storage exist; extracted code supplies durable delivery-attempt mechanisms. [Contracts](https://github.com/xormania/xorchestrate/blob/30254fd0f184b7e31aa7e0896347429004933ed4/xorchestrate/ports.py#L15-L48), [extracted attempts](https://github.com/xormania/xorchestrate/blob/30254fd0f184b7e31aa7e0896347429004933ed4/devtools/store.py#L627-L692). | Recipient routing and storage are separated, but routing ends at a stored inbox message. Session selection, native delivery scheduling and receipt requirements are not composed into that flow. | **Sound existing boundaries; missing delivery responsibility.** Generic JSON and configurable factories do not define the missing semantics. | Connect durable delivery to messaging; keep recipient routing, session selection, scheduling, retry and acceptance requirements replaceable, with simple defaults. | Exercise the same delivery behavior against interchangeable stores/policies; verify atomic message/attempt/event updates and recovery. Existing [storage tests](https://github.com/xormania/xorchestrate/blob/30254fd0f184b7e31aa7e0896347429004933ed4/tests/hexagonal/test_storage.py#L82-L118) provide a base. |
| **7. Retry, duplicates, reconnect and stale sessions** | Proof duplicate checks protect evidence integrity, not exactly-once delivery. Extracted code has explicit retry, attempt fencing and restart uncertainty. [Proof audit](https://github.com/xormania/multi-harness-proof/blob/a944f31b7527807d000da985c546e8b5bc9ff476/mhproof/evidence.py#L83-L134), [crash/retry test](https://github.com/xormania/xorchestrate/blob/30254fd0f184b7e31aa7e0896347429004933ed4/devtools/tests/test_live.py#L158-L169). | Operation idempotency, unknown outcomes and stale driver-handle rejection exist. Unknown session effects also block observation/control until reconciliation. [Unknown-effect test](https://github.com/xormania/xorchestrate/blob/30254fd0f184b7e31aa7e0896347429004933ed4/tests/hexagonal/test_application.py#L405-L418). | **Safeguards retained; end-to-end recovery incomplete.** Native reconnect durability and exactly-once message delivery were not established by the proof. | Retain uncertainty and replay protection; add delivery-attempt/incarnation fencing, correlated recovery and configurable retry. Observation/control must remain possible without pretending the uncertain effect is resolved. | Inject lost responses, crashes, duplicate observations, stale completions and replaced connections. Verify that uncertainty never silently becomes a fresh delivery. |
| **8. Deterministic verification and local native acceptance** | Confirmed run: 15 message checks, nine work batches and 69 matched native tool calls. [Run record](https://github.com/xormania/multi-harness-proof/blob/ad294398501ed119a436db54eca784be2fb10e76/LIVE_RESULTS.md#L20-L45). | TMUX acceptance checks application messaging/providers plus one configured native callback and persisted evidence. Its deterministic “native” test explicitly uses a protocol peer. [Scenario](https://github.com/xormania/xorchestrate/blob/30254fd0f184b7e31aa7e0896347429004933ed4/scripts/acceptance/scenario.py#L203-L224), [tests](https://github.com/xormania/xorchestrate/blob/30254fd0f184b7e31aa7e0896347429004933ed4/tests/hexagonal/test_acceptance_scenario.py#L60-L98). | **Useful fixture; insufficient acceptance coverage.** It does not establish preservation of the multi-harness proof. Application evidence surviving restart is not native conversation continuity. | Extend the existing fixture through the actual composed application and adapters, incorporating the proof’s verifier and native observations. | Deterministic fault coverage first; then your local three-harness run with the full exchange, context, busy/work and native-audit checks. |

The inspected working branch is **`integration/hexagonal` at `30254fd0f184b7e31aa7e0896347429004933ed4`**, matching the published [PR #4](https://github.com/xormania/xorchestrate/pull/4) head, with no unpushed commits. The working checkout has six unstaged deletions of legacy skill symlinks; there are no active implementation changes addressing these gaps. The separate CI checkout is clean at the same revision.

For the proof, I inspected code at **`a944f31b7527807d000da985c546e8b5bc9ff476`**. The confirming `LIVE_RESULTS.md` and `REFERENCE.md` were published later; the cited documentation is pinned to **`ad294398501ed119a436db54eca784be2fb10e76`**. The confirmed capabilities remain the baseline. No files or repository state were changed, tests executed, or authenticated vendor sessions launched.

**The hexagonal dependency direction is substantially right, but the behavior carried through its boundaries is incomplete.** Storage, capability providers, recipient routing, guardrails and neutral application callbacks are useful separations. Assessment also remains evidence rather than automatically becoming authority or completing work—an important protection against the earlier problem. [Assessment regressions](https://github.com/xormania/xorchestrate/blob/30254fd0f184b7e31aa7e0896347429004933ed4/tests/hexagonal/test_application.py#L870-L890).

All five earlier review leads remain present. Two additional details explain their practical impact:

- **Native progress depends on controller activity.** The JSON-RPC reader queues incoming frames; callbacks execute while a request or poll drains them. A Codex tool call arriving after turn acceptance therefore waits for another request/poll. The serial MCP service loop also waits for each application call, so a long ACP call blocks other requests on that connection. These are internal implementation limitations. [Dispatcher](https://github.com/xormania/xorchestrate/blob/30254fd0f184b7e31aa7e0896347429004933ed4/xorchestrate/adapters/jsonrpc.py#L319-L430), [MCP loop](https://github.com/xormania/xorchestrate/blob/30254fd0f184b7e31aa7e0896347429004933ed4/xorchestrate/mcp.py#L98-L104).
- **Trusted callback identity is not retained as verification evidence.** Codex constructs a native-session/call identity, but composition uses it for selected idempotency keys; ordinary result recording retains caller provenance rather than the independently observed native call chain. [Native callback](https://github.com/xormania/xorchestrate/blob/30254fd0f184b7e31aa7e0896347429004933ed4/xorchestrate/adapters/sessions.py#L322-L344), [binding](https://github.com/xormania/xorchestrate/blob/30254fd0f184b7e31aa7e0896347429004933ed4/xorchestrate/composition.py#L186-L205).

Existing fixes should remain: resume requires an adapter-issued receipt and authorization over both source control and destination creation; callers cannot manufacture a session binding through reconciliation. Those protections address real problems without imposing historical organizational rules. [Resume authorization](https://github.com/xormania/xorchestrate/blob/30254fd0f184b7e31aa7e0896347429004933ed4/xorchestrate/application.py#L240-L267), [reconciliation protection](https://github.com/xormania/xorchestrate/blob/30254fd0f184b7e31aa7e0896347429004933ed4/xorchestrate/application.py#L676-L695).

The smallest coherent repair sequence is:

1. **Correct session execution semantics and concurrent progress.** Define observable submission, activity, native completion and connection state; allow incoming callbacks, delivery and control while work remains outstanding. Preserve bounded transport handling and unresolved-effect records. The proof’s asynchronous dispatcher and extracted [`devtools/rpc.py`](https://github.com/xormania/xorchestrate/blob/30254fd0f184b7e31aa7e0896347429004933ed4/devtools/rpc.py#L39-L104) provide mechanisms, not a wholesale replacement for current transport hardening.

2. **Integrate all three native adapters against those semantics.** Reuse the exact proof delivery paths, Claude’s native identity hooks and Grok’s native tool/completion observations. The extracted [`devtools/native.py`](https://github.com/xormania/xorchestrate/blob/30254fd0f184b7e31aa7e0896347429004933ed4/devtools/native.py#L298-L410) adds useful prompt/epoch handling. Its [Grok lifecycle regressions](https://github.com/xormania/xorchestrate/blob/30254fd0f184b7e31aa7e0896347429004933ed4/devtools/tests/test_grok_lifecycle.py#L72-L188) cover late completions and outstanding interjections.

3. **Connect durable messaging to native delivery and evidence.** Build on the existing operation/store machinery and extracted attempt tokens. Persist the associations needed by the verifier, and retain adapter-origin observations separately from caller statements. Keep scheduling, retry and acceptance criteria configurable. A smaller routing issue belongs here too: routing currently resolves a snapshot outside the write transaction without checking whether routing inputs changed; preserve the snapshot explicitly or validate it before committing. This is a source-derived race possibility, not an executed reproduction. [Routing path](https://github.com/xormania/xorchestrate/blob/30254fd0f184b7e31aa7e0896347429004933ed4/xorchestrate/application.py#L391-L405).

4. **Complete lifecycle ownership and recovery.** After step 1, this can proceed alongside steps 2–3. Separate native conversation identity from connection incarnation and ownership; implement attachment/detachment and recovery through the appropriate adapter or bridge. Preserve authorized resume and stale-handle rejection. Investigate actual native attachment constraints individually rather than deriving exclusions from today’s process-owning implementation.

5. **Extend the existing acceptance fixture to verify the composition.** Reuse the proof’s checks through Xorchestrate’s messaging, routing, delivery and evidence paths. An independently running legacy proof would demonstrate the old composition, not completion of this repair.

Selective reuse matters. The extracted [provenance manifest](https://github.com/xormania/xorchestrate/blob/30254fd0f184b7e31aa7e0896347429004933ed4/legacy/agentscient/ops/conductor/tools/session_adapters/SOURCES.md#L3-L58) traces useful mechanisms back to the pinned proof. Fixed roles, restricted tool catalogs, forced sandbox choices and historical retry rules do not acquire authority through that provenance.

The verification plan should have three layers:

- **Deterministic composition checks:** exercise all three adapter protocols; acknowledge turn start before issuing a later tool callback; hold work outstanding while delivering another message; keep unrelated sessions responsive. Reuse the extracted [three-harness unfinished-work fixture](https://github.com/xormania/xorchestrate/blob/30254fd0f184b7e31aa7e0896347429004933ed4/devtools/tests/test_native.py#L253-L286), including Grok’s prompt → interject → prompt sequence. Add negative evidence, duplicate, late-response, crash and stale-incarnation cases.
- **Your local native acceptance:** fresh persistent Codex, Claude and Grok sessions; six directed exchanges, three outstanding-tool cases, six unfinished-work exchanges and nine work batches. Every required tool execution must have matching native identity/call evidence. Preserve timestamps and requested versus observed settings. The prior run’s 69 matched calls are recorded evidence; a changed composition should audit every actual call rather than hardwire that number.
- **Additional lifecycle checks:** independently test resume, reconnect, externally managed attachment and detachment. Report these separately from the established fresh-session baseline, retaining the same context and identity standards.

The timing claims must remain precise: the proof’s busy predicate requires submission during the outstanding tool call followed by a verified exchange; it does **not** require interruption during token generation. The confirmed run additionally recorded every unfinished-work reply before work completion. Keep those observations visible without silently strengthening or weakening the predicate. [Timing definitions](https://github.com/xormania/multi-harness-proof/blob/ad294398501ed119a436db54eca784be2fb10e76/REFERENCE.md#L111-L134), [recorded results](https://github.com/xormania/multi-harness-proof/blob/ad294398501ed119a436db54eca784be2fb10e76/LIVE_RESULTS.md#L29-L45).

**No owner decision currently blocks this repair.** Provider choice, native permissions, lifecycle ownership and delivery scheduling can remain independent configuration choices. The remaining attachment/interface questions require implementation investigation, not another scope decision from you.

## Appendix B. Behavioral-preservation addendum — historical source

**Behavioral-preservation addendum:** the existing repair sequence remains appropriate, but it must preserve owner outcomes, configuration choices, and evidence-based acceptance alongside the session and delivery repairs. These guarantees cannot be deferred to a final test pass.

The historical sources contain substantial reusable machinery. Several guarantees remain only in that historical runtime; the active hexagonal application currently provides useful foundations without their complete semantics.

**Inspected revisions and execution scope**

| Source | Revision inspected |
|---|---|
| Xorchestrate working branch and published PR #4 head | `integration/hexagonal` — `30254fd0f184b7e31aa7e0896347429004933ed4`, unchanged since the reconciliation |
| Agentscient `dev` | `91506128934b70a8967c626d6ece16d2c8a0815c`, unchanged |
| Agentscient `shit` | `456457b9a0a9eb1ff285ee2d3e40535b6f722cdb` |
| Separate acceptance fixture, `main` | `7d1abb3c4ac01451d83e1c1e12742b9fa2067561` |
| Multi-harness proof | Runtime/verifier: `a944f31b7527807d000da985c546e8b5bc9ff476`; published documentation/current head: `ad294398501ed119a436db54eca784be2fb10e76` |

Xorchestrate has no unpushed commits or active implementation changes addressing these gaps. The working changes remain six deleted legacy skill links; the separate CI checkout is clean at the same revision.

The findings below are **source-derived**. Revision, diff, and source comparisons were executed. An attempted memory-only application probe stopped during import because `jsonschema` was unavailable; it supplied **no behavioral verification**. No files or repository state were changed, and no authenticated sessions were launched. “Active/test-covered” below identifies matching existing regression assertions, not tests rerun during this inspection.

| Behavior | Source test/verifier and what it establishes | Active Xorchestrate implementation | Gap or limitation | Required adaptation | Verification through the active application |
|---|---|---|---|---|---|
| **1. Owner intent survives decomposition, cancellation, and revised plans** | [`test_original_owner_goal_survives_narrowed_job_and_cancelled_contributions`](https://github.com/uscient/agentscient/blob/91506128934b70a8967c626d6ece16d2c8a0815c/devtools/tests/test_completion.py#L134-L143) verifies that evaluation receives the full owner objective despite a narrower, cancelled job. Other tests reject acceptance after owner criteria change, including changes during evaluation. [Staleness tests](https://github.com/uscient/agentscient/blob/91506128934b70a8967c626d6ece16d2c8a0815c/devtools/tests/test_completion.py#L345-L427). | **Active/test-covered:** work goals, assignments, and optimistic record updates. **Active, incomplete verification:** assessments record the evaluated `work_version`. [Work operations](https://github.com/xormania/xorchestrate/blob/30254fd0f184b7e31aa7e0896347429004933ed4/xorchestrate/application.py#L304-L336), [assessment snapshot](https://github.com/xormania/xorchestrate/blob/30254fd0f184b7e31aa7e0896347429004933ed4/xorchestrate/application.py#L729-L737). | **Preservation semantics remain historical-only; current acceptance invalidation is missing.** Generic work metadata does not establish required outcomes, their relationship to contributions, or whether current evidence covers them. Record-version checking prevents stale writes, not acceptance of an incomplete objective. | Represent the current owner-approved objective and required outcomes separately from subordinate plans and contributions, with revision history. Bind acceptance to the applicable requirement revision. Preserve earlier assessments as historical evidence; later owner instructions determine current applicability. | Owner requires A+B → subordinate task covers A → B contribution is cancelled → plan changes → B remains visibly uncovered. Then explicitly revise the owner requirement to remove or replace B and verify the new criteria govern. Change requirements during assessment and before acceptance; old acceptance must not apply. |
| **2. Completion requires sufficient, current, correctly bound evidence** | Completion tests reject claim-only proof, an unmet required condition, mechanical failure despite a high evaluator score, running/uncertain measurements, stale revisions, unrelated passing checks, and incorrect report/CI bindings. [Completion tests](https://github.com/uscient/agentscient/blob/91506128934b70a8967c626d6ece16d2c8a0815c/devtools/tests/test_completion.py#L118-L307). The independent verifier also rejects wrong native generation/session, scope, artifact hash, and candidate revision. [Verifier tests](https://github.com/uscient/agentscient-test-repo/blob/7d1abb3c4ac01451d83e1c1e12742b9fa2067561/acceptance/tests/test_verify.py#L72-L123). | **Active/test-covered:** evidence provenance categories, assessment remaining evidence, uncertainty preservation, and authorization-context checks. [Assessment regressions](https://github.com/xormania/xorchestrate/blob/30254fd0f184b7e31aa7e0896347429004933ed4/tests/hexagonal/test_application.py#L870-L890). | **Complete acceptance behavior is missing.** `update_work` permits a status change under configured authorization; there is no supplied acceptance decision that establishes outcome coverage and consumes current, bound evidence. An adapter operation completing is also not task acceptance. | Add a replaceable acceptance responsibility that evaluates evidence against current configured criteria. Keep measurements, evaluator assessments, authorization, and acceptance decisions distinct. Bind evidence to its outcome, measured candidate, operation, relevant configuration, and native identity where applicable. | Through active providers, evaluators, storage, and work operations, attempt acceptance with each missing, failing, stale, or misbound proof condition. Require the relevant rejection. A positive assessment must not erase a failed required check; an explicit owner revision can change what is required. |
| **3. Configuration and runtime selection remain faithful** | Tests retain a nonfirst Claude selection across stored begin/stop cycles, reject unavailable selections, detect material work/configuration changes, and preserve choice across ordinary coordination changes. These are deterministic configuration/persistence tests, not authenticated native restart evidence. [Runtime-selection tests](https://github.com/uscient/agentscient/blob/91506128934b70a8967c626d6ece16d2c8a0815c/devtools/tests/test_runtime_selection.py#L21-L86). | **Active/test-covered in parts:** isolated configuration snapshots, bounded parameter delegation, and resume with unchanged configuration. Missing driver names are explicitly rejected. [Driver selection](https://github.com/xormania/xorchestrate/blob/30254fd0f184b7e31aa7e0896347429004933ed4/xorchestrate/application.py#L471-L490), [resume regression](https://github.com/xormania/xorchestrate/blob/30254fd0f184b7e31aa7e0896347429004933ed4/tests/hexagonal/test_session_resume.py#L62-L86). | **Lifecycle fidelity is incomplete.** A stored driver name and requested specification do not identify the effective adapter/configuration revision. Rebuilding the same name with changed settings is not equivalent to retaining the same resolved selection. Requested settings also do not establish native effective settings. | Retain requested, resolved, and independently observed settings, material configuration identity, and any authorized change or fallback. Preserve configurable selection policies. Unavailable explicit choices must produce an observable failure or an explicitly configured fallback with its reason. | Select nondefault harness/model/provider settings through composition; verify launch observations, restart, and resume. Change configuration under the same driver name; verify explicit handling. Test unavailable choices, permitted fallback, prohibited fallback, and harmless changes that should preserve selection. |
| **4. Verification detects deliberate violations for the right reason** | [`test_fault_detection.py`](https://github.com/uscient/agentscient/blob/91506128934b70a8967c626d6ece16d2c8a0815c/devtools/tests/test_fault_detection.py#L13-L80) requires a passing baseline, no errors/skips, and failure of the named assertion in the same test. It calibrates stale-acceptance checking, retained selection, and evidence access. PoC tests remove or corrupt required exchange evidence and require rejection. [Proof evidence negatives](https://github.com/xormania/multi-harness-proof/blob/a944f31b7527807d000da985c546e8b5bc9ff476/tests/test_proof.py#L236-L331). | **Active/test-covered for a limited case:** completed prose without the required application callback cannot pass acceptance. [Callback negative](https://github.com/xormania/xorchestrate/blob/30254fd0f184b7e31aa7e0896347429004933ed4/tests/hexagonal/test_acceptance_scenario.py#L80-L98). | **Broad behavioral calibration remains historical-only.** Imported-runtime passes do not establish that active assertions detect violations of owner intent, configuration, delivery, or evidence integrity. | Apply the calibration method to each repaired active behavior. Reuse isolated fault planting and checker comparison, with assertion-specific attribution. Keep detection, escaped fault, setup failure, unrelated failure, and skipped coverage distinct. | Establish a passing composed baseline; introduce one targeted violation; require its named assertion to detect it while setup and relevant unaffected controls remain successful. Also test the calibrator with missing dependencies, unrelated timeouts, and skipped assertions so none earns detection credit. |
| **5. Candidate changes remain independently acceptable against current requirements** | The separate fixture preserves its checker outside the editable candidate, verifies protected baseline objects, and checks the actual resulting product through its real kernel/MCP interface. [Product contract](https://github.com/uscient/agentscient-test-repo/blob/7d1abb3c4ac01451d83e1c1e12742b9fa2067561/acceptance/product/README.md#L3-L55), [candidate verification](https://github.com/uscient/agentscient-test-repo/blob/7d1abb3c4ac01451d83e1c1e12742b9fa2067561/acceptance/verify.py#L910-L977). Requested coverage must pass; absent coverage remains `not_exercised`. [Coverage decision](https://github.com/uscient/agentscient-test-repo/blob/7d1abb3c4ac01451d83e1c1e12742b9fa2067561/acceptance/verify.py#L87-L107). | **Active/test-covered:** public-process checks and honest fixture/native-callback scope labels. **Missing:** independent criteria/checker binding and exact candidate binding. The current scenario runs from the candidate repository and its receipt lacks those identities. [Current receipt](https://github.com/xormania/xorchestrate/blob/30254fd0f184b7e31aa7e0896347429004933ed4/scripts/acceptance/scenario.py#L320-L334). | Candidate changes can alter both behavior and the local definition of a passing scenario. Configured harnesses or passing discovered tests do not establish the requested behavioral coverage. | Run an owner-selected, versioned checker against an identified candidate snapshot. Declare required coverage separately from observed results. Requirements remain explicitly revisable by you; freezing criteria protects one measurement from candidate alteration, not from subsequent owner direction. | Change candidate tests/scenarios, omit required cases, fabricate summary success, or substitute another revision’s evidence. Independent acceptance must still detect the unmet criterion. An explicit owner criteria revision creates a new applicable measurement basis. |

**Important limits in the source evidence**

The named owner-goal test is an **input-preservation test**. Its fixture mocks Jev to return `0.97`; it does not demonstrate that an evaluator recognizes missing recovery behavior. The useful capability is retaining the full objective in the assessment boundary. Carry that test forward, then add deterministic checks that missing required outcomes remain unresolved. [Mocked completion fixture](https://github.com/uscient/agentscient/blob/91506128934b70a8967c626d6ece16d2c8a0815c/devtools/tests/fixtures/completion.py#L13-L39).

Two calibration mechanisms need strengthening during reuse:

- The older `ops/testkit` supports isolated mutations and comparison of old versus replacement checkers, but classifies **any nonzero exit** as detection. Combine its isolation/comparison machinery with the newer assertion-specific standard. It also archives `HEAD`, so it does not measure uncommitted changes merely because they exist in the working directory. [Older calibration implementation](https://github.com/uscient/agentscient/blob/456457b9a0a9eb1ff285ee2d3e40535b6f722cdb/ops/testkit/cases.py#L147-L240).
- The PoC’s preset scenarios inspect specific failure evidence, but its custom-fault branch primarily checks the expected verdict and whether the fault fired. That alone can confuse the intended detection with an unrelated failure. [Custom-fault assessment](https://github.com/xormania/multi-harness-proof/blob/a944f31b7527807d000da985c546e8b5bc9ff476/tests/behavior.py#L205-L228).

The separate product fixture supplies another strong pattern: negative product cases must retain successful boot, MCP initialization, and cleanup while failing the particular product assertion. That tests whether the checker distinguishes a broken behavior from a broken test environment. [Product calibration](https://github.com/uscient/agentscient-test-repo/blob/7d1abb3c4ac01451d83e1c1e12742b9fa2067561/acceptance/tests/test_product.py#L90-L144).

Configuration needs two distinctions:

- Xorchestrate deliberately lets trusted configuration override delegated request parameters. This precedence is explicitly tested; it should remain bounded and become observable rather than being blindly reversed. [Precedence regression](https://github.com/xormania/xorchestrate/blob/30254fd0f184b7e31aa7e0896347429004933ed4/tests/hexagonal/test_sessions.py#L218-L227).
- The confirmed PoC requested Grok `grok-4.5`/`low` but observed `grok-4.6`/`xhigh`; Claude’s effective effort was not independently established. **The communication proof remains confirmed. Configuration fidelity was not established by that run.** The cause and present behavior require adapter/native investigation, not an inferred harness limitation. [Recorded settings](https://github.com/xormania/multi-harness-proof/blob/ad294398501ed119a436db54eca784be2fb10e76/LIVE_RESULTS.md#L49-L61).

**Adjustments to the existing repair sequence**

Keep the five repair steps, with behavioral assertions developed and exercised inside each step:

| Existing step | Required addition and verification |
|---|---|
| **1. Correct session execution semantics and concurrent progress** | Establish the current requirement/criteria revision and independently expected assertion inventory. Test submission, receipt, ongoing activity, native completion, and task acceptance as different facts. Calibrate blocked callbacks, false completion, and stale-turn observations immediately. |
| **2. Integrate Codex, Claude, and Grok adapters** | Apply the shared behavioral assertions to each adapter’s native mechanisms. Verify selection-to-launch identity, callback identity, retained context, and busy/work delivery. Preserve requested/resolved/observed configuration distinctions. Deterministic peers verify adapter behavior; local native runs supply vendor evidence. |
| **3. Connect durable messaging and evidence** | Add the associations needed for complete delivery verification and current acceptance: requirement/outcome, candidate, configuration, message/attempt, session incarnation, operation, native observation, and artifact identity. Exercise incomplete objectives, unsupported claims, wrong bindings, duplicates, and stale evidence through the active application. |
| **4. Complete lifecycle ownership and recovery** | Verify selection and evidence applicability across restart, resume, attachment/detachment, and replaced connections. Recheck acceptance dependencies when consumed. Test owner changes during and after assessment, changed configuration, explicit fallback, and late observations from an obsolete incarnation. |
| **5. Complete composed acceptance** | Run the independent checker against the captured active candidate with the full expected coverage inventory. Aggregate the assertions and calibrations already exercised during repairs, then complete your authenticated three-harness run. Candidate-local test success remains supporting evidence. |

Steps 1–3 therefore carry the owner-outcome and acceptance semantics as part of the repair. Step 5 verifies their composition; it must not be their first meaningful test.

**Required local authenticated scenarios and evidence**

Your local run should exercise Xorchestrate’s active routing, delivery, storage, adapters, and evidence paths:

- Fresh persistent Codex, Claude, and Grok sessions with retained private markers.
- Six directed exchanges, three submissions during outstanding proof-tool calls, six unfinished-work exchanges, and nine work batches.
- Complete challenge → native submission → retained-context reply → sender receipt chains, with independently matched native session/tool observations.
- Separate work-accuracy, communication, and completion results. Correct delivery must remain visible when a task answer is wrong.
- Installed versions and requested/resolved/native-observed settings, including explicit unknowns or mismatches.
- Additional lifecycle scenarios from repair step 4, reported separately from the established fresh-session baseline.

Preserve the original timing predicates: busy submission occurs during the outstanding hold and is followed by a verified exchange; this does not assert token-generation interruption. Record whether unfinished-work replies precede work completion—the confirmed run did. Audit every actual native call rather than requiring the historical count of 69. [Verifier predicates](https://github.com/xormania/multi-harness-proof/blob/a944f31b7527807d000da985c546e8b5bc9ff476/mhproof/suite.py#L20-L81), [confirmed results](https://github.com/xormania/multi-harness-proof/blob/ad294398501ed119a436db54eca784be2fb10e76/LIVE_RESULTS.md#L20-L45).

The deterministic suite should additionally cover queue-only delivery, wrong memory, stale nonce, missing native observations/hooks, session drift, conflicting call-ID reuse, discovery without execution, wrong dispatch, unsupported interjection, missing native completion, and incorrect work alongside successful transport. These already have reusable PoC scenarios. [Behavior scenarios](https://github.com/xormania/multi-harness-proof/blob/a944f31b7527807d000da985c546e8b5bc9ff476/tests/behavior.py#L47-L144).

**Explicit completion criteria**

The repair is complete when:

1. **Owner outcomes remain intact and revisable.** Narrowed tasks, cancelled contributions, and plan changes cannot silently remove requirements; subsequent owner changes supersede the applicable earlier criteria.
2. **Acceptance consumes sufficient current evidence.** Required outcomes/checks, candidate identity, evidence bindings, and material configuration remain valid at acceptance time. Historical assessments remain available without being mistaken for current acceptance.
3. **Selections are faithful and observable.** Nondefault choices survive applicable lifecycle transitions; substitutions occur only under explicit configuration and are recorded. Unobserved native settings are not reported as verified.
4. **The checker is calibrated.** Each relevant deliberate violation is detected by its intended assertion; setup errors, unrelated failures, and skips receive no detection credit.
5. **Acceptance is independently bound to the measured candidate.** Receipts identify the exact commit/tree or content snapshot—including any measured working changes—plus criteria, checker, configuration, fixtures, and evidence cutoff. Candidate-authored summaries alone cannot establish those bindings.
6. **Required coverage is actually observed.** The active composition passes the deterministic assertions and your local native scenarios. Missing required coverage stays incomplete; imported tests or the standalone PoC cannot fill those cells.

**No owner decision currently blocks this work.** Historical role hierarchies, mandatory Jev thresholds, tool allowlists, fixed runtime order, merge requirements, and fixture-specific restrictions must remain configurable source material. Treating an earlier “original goal” or frozen checker as authority over your later instructions would directly conflict with your current direction.
