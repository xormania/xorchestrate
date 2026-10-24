# Research and source notes
Researched 2026-10-24. Sources below informed the design; they do not prescribe Xorchestrate policy. Summaries distinguish reported evidence from our recommendations. No third-party experiments were independently rerun during package preparation.

## Findings to carry forward
- TypeSafe recommends narrow typed questions, relevant state, and deterministic composition. Batch independent questions; serial stages are useful when evidence or dependencies change.
- Jev confidence is a statistic of its answer distribution, not a guarantee that our end-to-end decision is correct. Keep probabilities and native confidence separate.
- A decision tree can discard the correct branch early. Preserve concurrent explanations or multiple plausible paths when appropriate, and measure final outcomes.
- The structured-extraction cookbook uses local field checks for escalation. Its quality/cost demonstration does not establish downstream business value.
- Published cascade studies mostly optimize benchmark accuracy under a fee or accuracy constraint. They do not establish that each correction is economically important.
- Stronger fallback models can repair some errors and repeat others. Measure conditional corrections and damage on the cases actually routed.
- Developer reports expose confident mistakes from wrong-subject evidence and missing observations. Threshold changes alone cannot resolve those failures.
- Additional checks should earn their operational value. Audits may earn separate learning value. Neither category is automatically valuable or worthless.

## Primary TypeSafe sources
- How to build: https://docs.typesafe.ai/concepts/how-to-build-with-system-one
- Confidence definition and formulas: https://docs.typesafe.ai/confidence
- Noul semantics and threshold guidance: https://docs.typesafe.ai/primitives/noul
- Speculative fan-out: https://docs.typesafe.ai/patterns/fan-out
- Hierarchical classification: https://docs.typesafe.ai/cookbooks/hierarchical_classification
- Structured extraction cascade: https://docs.typesafe.ai/cookbooks/sde_cascade
- Known 1.13 weaknesses: https://docs.typesafe.ai/model-jaggedness/jev-1.13
- Models/versioning: https://docs.typesafe.ai/models
- 1.14 release blog, 2026-10-14: https://typesafe.ai/blog/jev-1-14
- Launch/workflow-evaluation rationale: https://typesafe.ai/blog/introducing-system-one-models-and-jev

The current models page identifies jev-1.14.0 as stable and lists jev-1.13.0. Live access must still be tested. The release blog recommends local use-case evaluation and acknowledges limitations of internal benchmarks. Default text context and experimental features are distinct; images/long-context flags are not needed merely because they exist.

## Independent empirical research
1. JEV-as-a-Judge, v5, 2026-10-09: https://arxiv.org/html/2609.26550v5
   Reports favorable frozen and prospective cascades. Its routing statistic is maximum label probability. Threshold selection minimizes fees subject to a chosen accuracy-loss tolerance; individual error costs are not priced. It does not test downstream agent trajectories. This supports local routing evaluation, not a universal threshold.
2. Decision Models vs. LLMs as Rubric Judges, v3, 2026-10-13: https://arxiv.org/html/2609.29769v3
   Reports correlated errors and limited accuracy improvement from additional judges, including higher-tier fallbacks. Labels/rubrics and task mix have limitations. It does not prove that escalation cannot help Xorchestrate.
3. Evaluating and Benchmarking the System One Model Jev, v1: https://arxiv.org/html/2609.37647v1
   Broad evaluation of 1.13 with released raw outputs. Calibration and threshold behavior vary by task; one frozen template/run and possible public-data contamination limit generalization.
4. JevOut, v2, 2026-10-08: https://arxiv.org/abs/2609.30243v2
   Plausible context additions can cause confident errors. Its large headline flip rate follows adaptive search; it is not the prevalence of errors on ordinary traffic. Keep stress testing separate from routine performance.
5. Same Attack, Different Judge, v1: https://arxiv.org/html/2610.17820v1
   Adversarial robustness varies by task and policy. Resistance to some injected inputs does not establish that a second Jev acting as an adversary improves a first Jev.

These are early empirical studies, not settled production guarantees. Retain exact versions when citing them.

## Original developer evidence
- Wrong-subject contradictions in agent verification:
  https://github.com/jkudish/jev-mcp/issues/53
  and https://github.com/jkudish/jev-mcp/pull/54
  The contributor reported 19 false contradictions among 23 selected blocks, judged by two model-family labelers. A separate same-subject assessment reduced false blocks in replay. Private, small, selected evidence; do not treat the rate as general accuracy. The merged change introduced a separate subject assessment and composed it with the relation judgment in code.

- Escalation comparisons:
  https://github.com/mizchi/jev-playground/blob/79fc189ee6edd8caf96a170227676d7c5c143848/docs/07-escalation.md
  Shell-policy escalation worsened the author's measured result; chess search helped at some budgets. The shell fallback saw less structured context, and chess used a shallow search's own quality proxy. Useful lesson: compare conditional fallback value and equal-budget random routing.

- Missing-observation experiment:
  https://github.com/mizchi/jev-playground/blob/79fc189ee6edd8caf96a170227676d7c5c143848/docs/57-confidence-fallback.md
  A geometry probe exposed blocked clicks that often had high model confidence. Later notes correct the claim that pruning, rather than supplying geometry, caused the accuracy gain. Preserve observation, interpretation, and later correction separately.

## Xorchestrate evidence
- Repository: https://github.com/xormania/xorchestrate
- Frozen v1:
  https://github.com/xormania/xorchestrate/tree/d2d2123bb8d4e866454c8e8e364f7749fa63a198/experiments/jev_failure_correlation
- V1 measured source: 454f6f25cb01d0aca793e21c72a032f8428535a9.
- V1 product subject: b0a00f1aed08d4ad8e066de8eb51f94e21da9f4a; tree 0f4034870f40fb5a1d022c9f35ad0b866236455f.
- V1 hosted run: https://github.com/xormania/xorchestrate/actions/runs/40065048378
- V1 evidence commit: 9dd1f434b27d41aa0297ccf120a38de1b5f90fb1.
- Repair PR (status must be refreshed): https://github.com/xormania/xorchestrate/pull/6
- Agentscient: https://github.com/uscient/agentscient/tree/dev and https://github.com/uscient/agentscient/tree/shit
- Multi-harness PoC: https://github.com/xormania/multi-harness-proof

V1 measured 34 cases and 8 relationship pairs. Jev scored 17/34 mechanism choices, 19/34 probe choices and 2/8 relationship choices against its frozen key. The lexical comparator scored 11/34, 11/34 and 4/8. Only p4 and p7 probes were executable; corrected p4 replays were local, not a new hosted measurement. V1's same-implementation versus shared-invariant rubric and evidence sufficiency constrain interpretation.

A post-hoc package-preparation diagnostic found 4 incorrect mechanism choices among 13 with native confidence at least 0.8. Valid control c10772a98b7 was called obsolete_result with confidence 1.0 despite a fresh revision-2 rejection and stale replay rejection. This diagnostic uses the existing frozen key and has no new model calls. It is not a calibrated production error estimate. Reproduction data is included under reference/v1 and the result under reference/v1-confidence-diagnostic.json.

## Cloud and secrets
- Current Codex Cloud environment documentation:
  https://learn.chatgpt.com/docs/environments/cloud-environments
- Codex Cloud legacy environment documentation:
  https://learn.chatgpt.com/docs/environments/cloud-environment
- GitHub Actions secrets:
  https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets

OpenAI documents distinct Cloud environment surfaces. The newer surface supports environment-owned Network secrets with placeholders substituted for allowed HTTPS destinations during tasks. The legacy surface documents ordinary environment variables lasting through the agent phase and Secrets fields available only during setup. The owner reports no Network secrets control and has added an environment variable: the handoff therefore uses that existing route and requires runtime verification. Do not infer access from either documentation or configuration alone. GitHub Actions repository secrets remain separately configured and must be explicitly injected into the workflow step. These documentation facts were checked on the preparation date; validate the actual task surface.

## How to use these sources
Use exact repository evidence to establish behavior, documentation to understand interfaces, and studies/developer reports to design tests. Do not translate every discovered limitation into a restriction on Xorchestrate. Changes to scope, model access, or owner values must remain explicit.

