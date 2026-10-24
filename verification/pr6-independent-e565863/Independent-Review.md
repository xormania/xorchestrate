# Independent verification of Xorchestrate PR #6

Prepared for xor on 2026-10-24, America/Port-au-Prince. Execution timestamps are UTC.

**Hold integration of `e565863ae72180571678c8f6118ee6e00b129e2e`. IV01, IV02 and IV03 remain open through new demonstrated counterexamples; IV04's false-detection defect is closed in the exercised scope.** The four original defects reproduce on the original candidate, and their exact witnesses behave correctly on the repair. The residual findings below survive the passing full suite and composed protocol.

No product repair, merge, release, PR comment or Jev change was performed. Authenticated vendor acceptance remains xor's planned local work and is not the reason for this hold. The review artifacts are published on a separate verification branch. This report evaluates the product commit, not either evidence publication commit.

## Identity and evidence basis

| Item | Identity |
|---|---|
| Product and separately selected checker | `e565863ae72180571678c8f6118ee6e00b129e2e` |
| Product tree | `474de55c5947890ab4092daeca7ff39f9bbbc400` |
| Original candidate/tree | `ef7567516520543970af55b6f19534cb66eda357` / `a0813aae760e6e5bb6245693bd92b7e7c321348b` |
| Repair evidence publication | [37547a1](https://github.com/xormania/xorchestrate/tree/37547a138758fe70e6cc471b491aaab01bfc3a05/verification/pr6-iv-repairs-e565863) |
| Original independent review | [3ac2342](https://github.com/xormania/xorchestrate/tree/3ac2342b6655f1a30652380c85ceb55262b7ad0c/verification/pr6-independent-ef75675) |
| PR base | `b0a00f1aed08d4ad8e066de8eb51f94e21da9f4a` |
| Hosted integration commit | `cf424865c3e86f93b98e015621158c995d5ae187`, exact product tree, parents base then product |

All inputs were retrieved directly from GitHub into a fresh clone. The PR head matched the pinned candidate at retrieval and subsequent refreshes; the final check is retained in `results/publication-prep/pr-final.json`. No later candidate was silently substituted. The repair publication is an evidence-only child of the product with 21 additions. Both complete published archives passed checksums and content restoration: 61,311 repair files and 57,328 original-review files. Their SHA-256 values are respectively `0ed0b6eccb4e15f381865e191b95e4b3e949431dd6c09989b2a38786a00200f0` and `d161f2318727b8b52aa5dd78fa91d6d5ebc1aa4c12103ec8e385b328715833dc`.

The handoff, reports, closure matrix, identities, provenance and replay instructions were read and executed. All 23 indexed hashes, four unchanged original witnesses and eight supplied bundle refs reconcile. Candidate/checker inventories and actual module origins are retained before and after execution. A separate checker checkout provides isolation; correctness expectations below come from requirements, controlled schedules, typed source construction and independent raw-record checks.

Paths below refer to the restored evidence archive. Standalone track reports are also published alongside this report.

## Verdicts and demonstrated blockers

| Finding | Original defect on `ef75675` | Exact witness on `e565863` | Independent verdict |
|---|---|---|---|
| IV01 inspection | Denied same-work session leaks on both stores | Original disclosure absent; authorized/direct-denial controls pass | **Open:** application-created assessment input actor snapshot still discloses a denied actor |
| IV02 completion | Codex BA reports idle with A pending | All six original adapter/order schedules pass | **Open, checker:** accepted Grok interjection is omitted from independent pending-submission veto |
| IV03 identity | Three completed-callback identity mutations wrongly retain 46/46 | Those mutations reject; pristine run passes | **Open, checker:** explicit native actor and lifecycle work contradictions retain acceptance |
| IV04 attribution | Same-line unrelated raise falsely earns detection | Both unrelated variants reject; real assertion detects | **Closed for false credit in executed scope**, with optional encoding sensitivity limitation |

### IV01 — typed assessment snapshots bypass current actor inspection policy

Severity P1, product. A separate inspector `observer` is explicitly denied actor record `worker` under the current `native_observations`/`get_work` policy. A historical `list_actors` result correctly removes worker and preserves observer. The same read nevertheless discloses worker's full record, including a private metadata canary, through `assess.result.assessment.input_binding.actor_record` in both native-call and completed-observation history. Current `get_work` also discloses it through application-generated assessment evidence.

This is not an opaque provider/user payload: `_assessment_input_binding` copies the actual actor record and `_assess` stores it before returning the assessment ([acceptance.py:199](https://github.com/xormania/xorchestrate/blob/e565863ae72180571678c8f6118ee6e00b129e2e/xorchestrate/acceptance.py#L199), [application.py:871](https://github.com/xormania/xorchestrate/blob/e565863ae72180571678c8f6118ee6e00b129e2e/xorchestrate/application.py#L871)). Projection recognizes the outer assessment but omits its typed input binding ([delivery.py:185](https://github.com/xormania/xorchestrate/blob/e565863ae72180571678c8f6118ee6e00b129e2e/xorchestrate/delivery.py#L185)). The evaluator supplies only a public conclusion.

`results/iv01/probe_assessment_binding_v2.py` demonstrates this through actual APIs on MemoryStore and SQLiteStore. Fourteen checks per store include three disclosure assertions and eleven controls that preserve authorized records, opaque user content, opaque request context, trusted stored inputs, exact grant-restored views and no evaluator reinvocation. Root independently inspected the typed construction and repeated the witness in `results/root-rechecks/iv01-assessment/`. The earlier version is retained; V2 strengthens distinct principals and separate opaque-data markers. The seven categories of retained provider/measurement/operation/requested-phase/collision checks and catalog controls pass freshly, but omit this typed container.

### IV02 — checker ignores an accepted Grok interjection before its first native start

Severity P2, verifier. In an actual application/SQLite/Grok subprocess schedule, A's callback returns while its native continuation tail remains held. B is accepted as an interjection and held by the deterministic peer before its first native-start/callback notification. Release A while B remains held. The product correctly remains **active**; its raw `native_submission` retains B's `interjection_id` and `result.accepted=true`, and adapter state retains B.

Copy that exact captured raw run and append only one false aggregate-idle observation. The checker then passes `completion-grok` while the peer gate still proves B pending. No original record is changed or deleted. The analogous Codex control rejects with `completion_native_turn_unresolved`; releasing B yields legitimate idle and passing completion for both adapters.

The independent veto reads Grok `prompt_id`/`result.promptId`, omitting the adapter's accepted interjection identifier ([checker.py:339](https://github.com/xormania/xorchestrate/blob/e565863ae72180571678c8f6118ee6e00b129e2e/scripts/verification/checker.py#L339)). This directly violates the handoff's requirement that forged idle cannot hide independently known pending work. It demonstrates a verifier false acceptance, **not** a remaining live adapter false-idle defect.

Decisive script: `results/iv02/scripts/submission_gap_v2.py`; raw proof: `submission-gap-v2/grok/`, including `forged-delta.json`. Root repeated the actual schedule and rechecked all stages from the separate checker in `results/root-rechecks/iv02-submission-gap-v2/` and `iv02-separate-checker.json`. Peer gate adaptations and the initial version remain intact.

### IV03 — independent identity contradictions still borrow matching records

Severity P2, verifier. Two residual families occur on this review's fresh 46-case raw protocol execution:

1. Change an existing native observation's `created_by` to another actor or explicit null. Its session actor and requested/completed callbacks remain unchanged. All 46 cases still pass. The equivalent completed-callback actor mutation rejects. Normalization drops the native-side actor claim, and native signatures omit it ([normalize.py:40](https://github.com/xormania/xorchestrate/blob/e565863ae72180571678c8f6118ee6e00b129e2e/scripts/verification/normalize.py#L40), [checker.py:66](https://github.com/xormania/xorchestrate/blob/e565863ae72180571678c8f6118ee6e00b129e2e/scripts/verification/checker.py#L66)). Adjacent contradictory native duplicates can also borrow a valid record's identity.
2. In a retained reconnect probe, change saved evidence's existing `work_id` to another work/null, or change only the completed callback's explicit `arguments.work_id`. The independent native arguments, callback/effect and saved evidence disagree, yet lifecycle verification passes. It compares the verification result payload and effect ID but omits applicable work-argument/effect agreement ([checker.py:300](https://github.com/xormania/xorchestrate/blob/e565863ae72180571678c8f6118ee6e00b129e2e/scripts/verification/checker.py#L300)).

The minimal 30-case script exercises all three peers: 15 false acceptances, zero false rejections. Pristine, genuinely absent optional native actor, and coherently supplied alternate-work controls pass; explicit contradictory callback actors reject. Thus the expectation is consistency of applicable explicit claims, not a requirement that every historical record contain every field. The broader 109-case investigation records 34 false acceptances in these families, with all attempts retained. Root repeated the minimal script against the separate checker with the same 15 results. These are evidence-verifier defects; live wrong-principal dispatch is not demonstrated.

Replay: `results/iv03/minimal_counterexamples.py`, with `results/execution/protocol/` as input. Read `results.json`, per-case raw selected records and reports; exit 1 means the contradictions were falsely accepted.

### IV04 closure and optional sensitivity limitation

The unchanged same-line witness reproduced false credit on the original candidate and rejects on the repair in both calibrators. All 32 fresh variant/mode challenges match independently defined event-trace expectations: helper/condition/message errors, adjacent raises, context-manager/finally exceptions, replacement exceptions, nested assertions and wrong-marker failures receive no intended credit; real multiline, Unicode, loop, context propagation and saved-reraise assertions remain detectable.

All twelve original source faults remain independently valid: 36 passing controls, 19 intended assertion failures and 17 unaffected mutant passes, zero errors/skips. The independent audit checks actual source mutations, observer hashes, XML and exact source spans; it does not infer validity from classifier totals. Those twelve detections were valid before the repair and remain so.

Four positive-control misses are explicitly retained: valid UTF-8 BOM and Latin-1 source each produce `unrelated_failure` in rewritten/plain modes because the observer reads text without Python encoding rules ([pytest_calibration.py:42](https://github.com/xormania/xorchestrate/blob/e565863ae72180571678c8f6118ee6e00b129e2e/scripts/verification/pytest_calibration.py#L42)). This is an optional fail-closed compatibility/sensitivity improvement, not false acceptance or a regression in the retained UTF-8 corpus. Missing source positions correctly receive no unambiguous credit. The earlier encoding-probe oracle mistake is retained and corrected in V2; its apparent passes are not credited.

## Execution, preservation and provenance

| Fresh measurement | Result |
|---|---|
| Full active suite, Python 3.12.14 | **1,127 passed +164 subtests**, zero failures/errors/skips |
| Actual three-adapter composed protocol | **46/46**, exact parsed raw-report recomputation |
| Original twelve source calibrations | **12 detected**, independently audited source/assertion/control evidence |
| Focused suite | First concurrent run **593 passed, 1 failed**; isolated selector **1 passed**; complete focused replay **594 passed** |
| Static/optional/live fixture | Ruff, security lint, compilation, 13 optional-helper tests and live tmux fixture pass |
| Seven prior closures | F02/F03/F04/F05/F06/F09/F10 preserved by fresh targeted replays |

The focused failure is a test synchronization issue: it snapshots raw observations after the completion record but before the injected faulty-idle record, then reads a later adapter status. This yields `native_completion_missing` rather than the calibration's expected `completion_callback_unresolved` and aborts before its designated product assertion. It is retained in `results/execution/`, not retroactively made green. Improving that controlled-schedule synchronization is optional test reliability work; it is not demonstrated product false completion. The original twelve fault calibrations are unaffected. An attempted copy of its pytest temporary directory found that concurrent pytest cleanup had already removed it; the failure XML/log remain, and the replay uses an explicit external base directory.

Fresh preservation executes six two-replacement authority/recovery cases, 23 cache/assessment variants, five additional valid/fault controls, the original Claude overlap schedule and eleven full-raw uncertainty/timing variants. Legitimate restoration, reconciliation, reassessment, final completion, historical uncertainty superseded by a valid newer attempt, harmless record reorder and timezone-equivalent timestamps remain usable. No regression in the seven previously closed findings was demonstrated. A retained compact callback-kind side probe remains an unresolved investigation, not an additional established blocker.

All original 89 and frozen 165 selectors remain; exactly 46 added test functions are mapped, giving 211 distinct selectors. Source beneath preserved selectors was audited: one frozen positive fixture adds completed-callback proof while retaining its negative assertions; a separate existing Codex expectation reflects the pending-set representation. Shared fixture/support changes are disclosed. H1–H8, B1–B5 and A1–A2 maps reconcile with actual passing XML. Counts are coverage records, not acceptance targets.

An independent standard-library raw audit, importing no checker or normalizer, corroborates each fresh/retained-local/hosted positive run: 69 native/requested/completed/durable joins, 15 complete exchanges including busy/unfinished timing, and nine work answers recomputed from latest fixture attempts. It has a stated scope; it does not replace adversarial lifecycle/completion checks. Retained local and hosted reports separately recompute exactly.

All five retained hosted artifact ZIPs match newly retrieved GitHub digests; six newly retrieved job logs match retained bytes. [Run 40132785317](https://github.com/xormania/xorchestrate/actions/runs/40132785317), attempt 1, passed six jobs. Both Python 3.11/3.12 full suites have 1,127 primary tests plus 164 subtest events. Hosted calibration/protocol ran on 3.12 only. The integration Git object independently proves exact tree and parents; an empty shallow parent field is not ancestry proof. The 285 toolkit and 825 historical runtime passes are retained hosted historical-source execution, not fresh active-product credit.

Configurable hexagonal boundaries remain: ports/composition, replaceable stores/providers/native drivers/transports, routing, scheduling, retry, receipt, assessment and acceptance policy. Fresh controls exercise meaningful substitutions and restore usable authorized paths. No mandatory evaluator hierarchy, universal approval or Jev coupling was introduced. IV01 limits inspection guarantees; IV02/IV03 limit independent completion/identity proof despite this preservation.

## Limitations, adaptations and recommendation

This is bounded deterministic subprocess/product execution and adversarial evidence checking. It does not establish authenticated Codex/Claude/Grok behavior or exhaust every policy and schedule. The source candidate remains unchanged. The same checker in two directories is not an independent oracle. Historical runs remain labeled historical.

All failed/superseded attempts are retained: initial sandbox socket/clone failures, original vulnerable-expectation exits, focused calibration failure and missing temporary-directory copy, reviewer schema/hash/path mistakes, encoding-oracle correction and the initial versions of strengthened probes. Replay adaptations affect reviewer orchestration, peer-side gates or explicit expectations only. The unchanged product and source/module identities are independently checked. Archive packaging records exclusions for caches and nonportable runtime aliases/endpoints, not test failures.

**Integration recommendation:** hold PR #6 for the demonstrated IV01 typed assessment disclosure, IV02 accepted-interjection veto gap, and IV03 explicit identity-consistency gaps. Repair those boundaries while retaining authorized/opaque views, legitimate completion, absent-optional/coherent-scope controls and the configurable architecture, then independently replay the minimal counterexamples and affected preservation checks. Encoding sensitivity and focused-test synchronization are optional improvements, distinct from those blockers. No additional vendor prerequisite is imposed.

Use [REPLAY.md](REPLAY.md) for source recovery, full execution, minimal witnesses and expected exit semantics. [closure-verdicts.json](closure-verdicts.json) provides machine-readable verdicts; track reports and the checksummed evidence archive retain raw observations and all controls.
