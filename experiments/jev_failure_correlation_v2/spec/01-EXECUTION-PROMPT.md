# Execution prompt — implement and measure Jev v2

You are executing xor's Xorchestrate Jev v2 experiment in Codex Cloud. Read the complete package before editing. Current explicit owner instructions govern. Ask only for genuinely missing owner decisions or access after completing independent work; do not turn uncertainty into reduced scope.

## Outcome
Deliver an implemented, executed, independently reviewable experiment answering:
- Can Jev recognize related failure behavior and invariants across implementations?
- Can it select probes that expose or distinguish real faults?
- Do decomposition, adversarial checking, and selective reasoning escalation improve consequential decisions?
- Which investigations are economical under explicit assumptions, and which are worthwhile primarily for learning?
- Can Jev help allocate investigation without hiding confident errors or missed opportunities?

Treat each as a hypothesis. A finding of no benefit is valid. Unsupported success claims, missing controls, or unexplained omissions are not.

## Establish the workspace
Inspect current repository guidance, branches, working changes, and execution capabilities. Record the actual starting commit and tree. Preserve unrelated work.

Use a new branch, normally experiment/jev-failure-correlation-v2, and a new experiment directory, normally experiments/jev_failure_correlation_v2. The frozen v1 publication is d2d2123bb8d4e866454c8e8e364f7749fa63a198. For a straightforward isolated starting point, branch from that commit; an already accepted integration base may be used if it preserves and identifies the v1 reference. Document the choice and choose a PR base that makes the v2 delta reviewable. Do not merge another task's unmerged repair branch to make this experiment run. Product subjects may be checked out separately at explicit revisions.

The repair task was draft PR #6 at ef7567516520543970af55b6f19534cb66eda357 when this package was prepared. Refresh its status; that observation is not a claim of current repair acceptance. Keep repair execution and Jev v2 separate.

Read v1 README, REPORT, INDEPENDENT_REVIEW, PROBES, runner, rubric, corpus, provenance, and retained raw results. Locate useful imported assessment code before reimplementing it. Use the original independent-review ZIP for reproductions and source evidence. Review Agentscient and multi-harness-proof only where needed to resolve the intended behavior or provenance; do not expand this assignment into another general reconciliation.

## Implement one experiment
Implement the requirements E01–E14 in 02-EXPERIMENT-SPEC.md and the trace/economic requirements in 03-TELEMETRY-AND-ECONOMICS.md. Reuse existing project conventions. Keep model/provider selection, question sets, routing, audit policy, loss assumptions, and evidence persistence replaceable behind appropriate boundaries. Reuse ports or small interfaces; a new orchestration framework is not needed to run this experiment.

Build one harness with incremental policies and shared evaluation machinery. Finish deterministic preparation/replay and controls first, then the live policies. Independent questions over one state may be batched; dependent questions must receive the relevant prior result or new observation. Preserve competing explanations where the evidence supports more than one.

Supply a stronger deterministic comparator using explicit structured checks where possible. The old lexical baseline remains a historical reference, not the only comparator. Keep numerical comparisons, identifiers, ordering, and known policy checks in code. Assess semantics with Jev where needed.

Define a bounded execution plan before live batches: case counts by split/family, requested models, candidate probes, thresholds or threshold-selection procedure, utility scenarios, audit design, maximum requests/retries/tokens or output limits, maximum elapsed time, and cost-estimation basis. Use existing owner-configured provider limits. Choose and disclose conservative experiment defaults when ordinary implementation choices are needed. Unknown monetary values remain unknown or scenario ranges; do not invent a production budget or claim unrestricted spend. Enforce the declared finite run limits.

## Preserve evidence and honest measurement
Freeze development, calibration, and holdout membership before holdout evaluation. V1 cases are development/reference material for v2; do not relabel them as fresh heldout data. Keep meaningful derivatives grouped. Independently check that each stage-specific epistemic judgment follows from evidence available to the evaluated policy at that stage. Independently established outcome labels may use later probe or execution evidence; they must not leak into earlier inputs. Keep answer keys, split/family identifiers, fault-injection names, repair diffs, and expected outcomes out of model inputs unless a field is deliberately part of the tested deployment contract. Record and disclose any unavoidable evaluator exposure.

Maintain raw observations and the exact bounded view sent to each model. Mark omissions, transforms, and provenance. Do not collapse unknown, unrun, provider failure, invalid response, not exercised, or unsupported into a correct result or a negative finding.

Keep two reference layers: what can be concluded from the initial evidence, and independently established outcome truth that may be revealed only by a probe or later execution. Hide outcome truth from the evaluated policy until the corresponding observation is obtained. An initial unresolved answer can be correct even when a later investigation discovers a fault; score the investigation's value separately. Do not remove these cases merely because the decisive fact is initially missing.

Make selected probes executable against the applicable application/adapters or established protocol harness. Bind each probe to the subject revision and the actual assertion. Include a healthy control, the intended fault, and an unrelated failure. An exception containing an assertion string does not earn detection credit. A protocol peer does not prove installed-vendor behavior.

Capture decision-and-outcome telemetry, full distributions, actual costs, and independent outcome evidence. Log the initial route before audit selection. Jev may propose targeted audits but cannot control the independent random sample.

## Execute through available routes
Use a single pinned Jev version for the main comparison, normally jev-1.14.0 if a live smoke confirms it. Retain jev-1.13.0 as the v1 reference. Never silently use a moving alias or swap versions. A version comparison is a separate, bounded comparison on common requests.

The owner selected direct Jev API access from Codex Cloud as the primary route and reports adding an environment variable; this Cloud UI has no Network secrets control. Read TYPESAFE_API_KEY from the task environment without printing it and send the ordinary Authorization bearer header to https://api.typesafe.ai/v1/systemone through the configured HTTPS proxy. Verify runtime availability, allowed POST connectivity, authentication, schema and pinned model identity with a bounded smoke call before measured requests. Never dump headers or environment variables or write the credential into files. Do not require a different settings surface. If a future environment supplies a network-secret placeholder, use it normally without trying to recover the raw key. Do not bypass setup-only secret lifecycles by persisting credentials.

Retain the existing GitHub Actions TYPESAFE_API_KEY route as a fallback. Reuse and adapt v1's scoped workflow/evidence publication approach without modifying its retained results or rerunning v1 inadvertently. Use GitHub-hosted runners for this xormania repository. Configure explicit branch/path triggers so a new workflow can run before merge; do not rely exclusively on default-branch workflow_dispatch availability. Do not use pull_request_target or untrusted fork execution to obtain secrets. Keep Cloud and hosted measurements separately identified.

Run an authenticated transport/schema/model-identity smoke before measured calls. Set finite timeouts and retries, record every attempt, and upload evidence even on incomplete runs. Use fresh run directories. Inspect workflow jobs and artifacts, not only summary files.

Discover reasoning-provider access independently. Pin the requested provider/model/effort and record what is actually observed. A supported native Codex delegation route may be used if each evaluation is a real separate invocation with a fixed evidence packet, retained prompt/result, declared context exposure and observed metadata. Do not call the implementing agent's retrospective opinion a blinded evaluation. Do not call unreported subscription costs zero, and do not compare unlike execution routes as though their costs were identical.

If a required provider, model, network route, or native harness is inaccessible, complete the other authorized work and retain prepared, hash-bound requests plus a resume command. Identify the exact blocked operation and minimum missing access. Keep the required arm pending; do not remove it or claim the whole experiment complete. Do not fabricate responses to fill a results table.

## Verify and publish
Run appropriate experiment tests and relevant existing checks. Tests must catch meaningful oracle, routing, identity, cost-accounting, and artifact failures. Use sustained known-bad controls whose required detection remains enforced. Re-run affected checks after material fixes.

Obtain a separate review of methodology and source/result consistency. The implementing agent must not self-certify independence. Record reviewer exposure, verified claims, disagreements, and unresolved findings.

Prepare a draft PR with source/tests/workflow, a readable results report, and exact-candidate evidence. Preserve raw outcomes, request/response bodies, executable replay commands, corpus/label provenance, cost scenarios, and the requirement-to-evidence table. Commit as xormania according to repository guidance; do not add AI co-author trailers. Do not merge or release.

The final response must name the branch, PR, source commit/tree, evidence commit/run/artifacts, replay commands, measured configurations, what helped or harmed under which economic assumptions, and concrete pending work. Every E01–E14 requirement must be demonstrated, contradicted, or unverified with a reason. Distinguish implementation completion, measurement completion, and support for a scientific claim.

Do not conclude the project needs a production Jev gate simply because the experiment ran. Recommend adoption, revision, targeted further evidence, or no additional stage according to observed results and the owner's objectives.

