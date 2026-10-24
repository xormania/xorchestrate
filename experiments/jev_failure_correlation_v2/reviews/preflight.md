# Methodology and source preflight

Reviewer: separate native Codex agent session `/root/evidence_review`, 2026-10-24 UTC. This reviewer owns only this file and the later `methodology-review.md`; it authored no corpus, policy, provider, probe, scoring, or economics implementation. It shares the implementation workspace and communicates findings to the implementers. This is a separate preparation review, not the final adversarial review requested in package document 05, and not organizational or model-family independence.

## Exposure and checkpoint

At the initial source checkpoint, the repository HEAD was `d2d2123bb8d4e866454c8e8e364f7749fa63a198`, tree `7bb88d5fd01a21478b720ad2d42b2b627b74bb92`. The reviewer read repository `AGENTS.md`, all six current numbered package documents, the v1 README/report/independent review/probe scope, corpus/runner/rubric, provenance and selected retained raw observations, and the original F01–F10 report and evidence. It read historical v1 model results. It has not inspected any v2 live model output. Initial v2 packets and labels were not yet available at this checkpoint. V2 label sufficiency is therefore **pending**, not passed.

The requested review order is: supplied initial packets and stage-specific epistemic key; record the first judgment; then inspect the independent outcome key and executed observations. Source/probe authors necessarily know their construction. That knowledge must stay out of evaluated initial inputs. Historical familiarity with F01–F10 prevents claiming blind discovery of those mechanisms.

## Independently checked source integrity

The original review archive at `reference/Xorchestrate-Independent-Review-Package.zip` in the supplied package has SHA-256 `67218f0e6a1e180d3368ea25ed8f766a03dc921e96b085b9ee287c1c82b40086`. An in-memory ZIP read verified all **1,831** entries in `review/package-manifest.json`: every byte count and SHA-256 matched. No historical evidence was overwritten or re-executed.

The v1 retained Actions attempts contain 43 valid responses from `jev-1.13.0`, totaling 59,623 input and 7,313 output tokens, agreeing with its report. An independent read-only call to `runner.replay_attempts` validated requests and raw typed responses and reproduced all 34 case and eight pair predictions exactly. Recomputing `score_predictions` yielded baseline/Jev mechanism agreement 11/17, probe agreement 11/19, and relationship agreement 4/2, matching the report. The retained measured source is `454f6f25cb01d0aca793e21c72a032f8428535a9`. This is replay of retained evidence, not a fresh provider call. No provider call was made by this reviewer.

The reviewer also executed the corrected historical probe wrapper with the frozen Jev selections:

```sh
/workspace/jev-venv/bin/python experiments/jev_failure_correlation/evidence/probe_replay_v1_2.py \
  --predictions experiments/jev_failure_correlation/evidence/actions-40065048378/experiment-results/evaluation/predictions.json \
  --selector jev --out /tmp/jev-v2-review-v1-probes-venv
```

It exited zero. P4's passing control passed; only q01 earned intended-target detection credit. q02/q03/q04 reproduced the three known unrelated-exception false detections, while q06/q08 remained not-exercised and q07 skipped. P7 reproduced the material-context fault on memory and SQLite with passing cosmetic-title controls. This is a new local historical-probe replay, not v2 outcome evidence and not corrected hosted evidence. An earlier attempt under system `python` failed because pytest was absent; that output is preserved separately at `/tmp/jev-v2-review-v1-probes` and receives no behavioral credit.

## Reusable components and required boundaries

| Existing capability | Reuse and boundary |
| --- | --- |
| `devtools/jev.py` | Typed Choice validation, full distributions, native confidence, requested model checks, and a replaceable adapter. Schema success remains separate from semantic correctness. |
| v1 `runner.py` | Complete request bodies, stable hashes, bounded attempts, no-alias model checking, sanitized raw responses, and offline reconstruction from typed responses. A stored prediction is insufficient without its exact request and raw response. |
| v1 `corpus.py` and provenance | Explicit field extraction, raw references, reversible neutral replacements, omissions, and family grouping. Compact human-designed packets remain selected evidence rather than unsupervised discovery. |
| v1 `probes.py` and corrected replay wrapper | Actual calibrator execution and active-application material-context controls. Preserve the original hosted attribution failure separately from corrected local replay. |
| Original independent review reproductions | Real application/adapters and established protocol harnesses, explicit gates and controls, raw argument/attempt/timing records. A protocol peer does not establish installed-vendor reachability. |

No additional general-purpose assessment framework was found necessary. The v1 lexical comparator is deliberately weak; it must not be the sole comparator for v2. Identifiers, ordering, booleans, requirement revisions, and explicit policy comparisons belong in the deterministic comparator.

## Source-specific label and probe risks

| Family | Source establishes | Limit that must survive v2 |
| --- | --- | --- |
| F01 | An authorized enclosing delivery read returns another explicitly denied work's records, on both stores; direct denied queries control authorization. | Scope leakage is supported; credential takeover and broader exploits are not established. |
| F02 | A callback observed under the denied original principal is dispatched after reconnect with a replacement principal/work. | Reconnect alone is not the defect. Preserve observed callback identity, actual executed identity, permission basis, and no-replacement control. |
| F03 | An effect applies before response hold; reconnect initially blocks a new effect; releasing the old response leads to false failure and another effect is admitted. | An error name alone proves neither no effect nor safe retry. Preserve effect counts, recovery fence, and chronology. F02/F03 share a bridge but have distinct demonstrated causes. |
| F04 | Actual Codex/Claude/Grok adapter replay returns old acceptance after revision, while fresh adapter and direct reads reject. | A direct application-only check misses the adapter cache boundary. Fresh-read controls are valid, not automatically obsolete because they mention revisions. |
| F05 | A consumed material input changes; reused favorable assessment accepts while a fresh assessment rejects. Cosmetic title changes control over-invalidation. | Version change alone is not sufficient. Preserve material dependency evidence and whether change occurred before/during/after assessment. |
| F06 | With two protocol-peer callbacks held, releasing only A yields idle while B remains pending. Both-complete idle is valid. | This establishes the adapter under the constructed protocol schedule. Installed Claude behavior remains unverified. |
| F07 | Unrelated helper exceptions receive intended-fault detection credit because traceback text contains the assertion source. | Require terminal attribution to the actual intended assertion, passing unaffected control, and separate setup/collection/timeout/helper/not-exercised outcomes. |
| F08 | Substituted native recipient conflicts with actual callback for the same joined call; independent checker passes and production receipt rejects. | Evidence-substitution checker experiment, not observed live wrong dispatch. Normalization must retain original and executed arguments. |
| F09 | Appended later unknown attempt is masked by older success. | Ordered same-message/same-recipient attempts matter; no claim that normal retry generated this synthetic state. |
| F10 | Substituted completion timestamp precedes replies; checker reports bad reply timing but passes. | Checker flaw, not demonstrated late live reply. Preserve challenge/reply/completion chronology and the explicit unfinished-work requirement. |

## Controls needed before measurements can support claims

1. Freeze stage-specific epistemic labels separately from outcomes; initially missing evidence may justify unresolved even when later execution proves a fault. Validate each later-stage packet against the exact acquired observation.
2. Keep common invariant and common implementation cause as separate judgments. V1's `shared_invariant` required differing mechanisms and its F02/F03 pair admitted four alternatives; neither should become a permissive v2 success criterion.
3. Do not treat a checker verdict as an independent oracle. Concurrent environment and checker faults can coexist. Preserve absence, wrong-subject evidence, invalid response, provider failure, unrun, unsupported, and not-exercised as distinct states.
4. Bind probe subject revision and actual assertion. V1 hosted p4 used qualified JUnit classnames while looking for `test_probe::test_target`, so all native outcomes were `not_exercised`; corrected `--rootdir` local replay cannot retroactively change the hosted outcomes.
5. Count shared probe batches once. Only executed discriminating observations can earn realized utility; probe-option agreement and same-action label correction do not automatically reduce downstream loss.
6. Group derivatives and related pairs into one partition. Fresh variants of known F01–F10 mechanisms support variant transfer, not new-mechanism discovery. Repeated calls and adapter/store reruns are not new independent incidents.
7. Freeze initial route before independent random auditing; sample every outcome with recorded nonzero inclusion probabilities. Keep random escalation, random audit, targeted risk audits, and adaptive stress attacks separate.
8. Keep reasoning-only inputs blind to Jev diagnoses; diagnosis-conditioned fallback is a distinct condition. Charge actual collection separately from modeled routine-policy deployment. Unknown native-session costs stay unknown.
9. An immutable handoff and a later independent review are still required. This working-tree preparation review cannot certify final source identity, completed comparisons, economic gains, or E01–E14 fulfillment.

## Initial v2 packet review — preliminary, superseded

The reviewer first inspected **only** `data/frozen/initial_packets.json`, SHA-256 `3b80e579cd3a6177b99554ee1c2b1fdac04a5df5c4616ef68ee9a7038e8a8725`, containing 24 packets. No initial key, outcome key, v2 raw outcome execution, v2 probe implementation, or live v2 output was inspected before the judgments below. The initial print was truncated; a second packet-only projection removed repeated candidate descriptions and preserved all case facts/observations for review. The reviewer had already read the common question contract.

These are **preliminary packet-only judgments**, interpreted against each listed local requirement. They are recorded before opening the author's key. The author then reported that this generation would become reference replay and be superseded with materially revised trajectories; it is not a label-review approval for final measurement.

| Packet | Initial local claim judgment | Supported reason / useful missing observation |
| --- | --- | --- |
| c8b932080dc3e | supports | Cosmetic title; old and fresh acceptance both true. |
| c06e2dd37f2ab | contradicts | Material context changes; old true versus fresh false. |
| c593af9fb49a0 | unresolved | Boundary result missing; collect current/fresh assessment comparison. |
| c4ac02ef39bfc | supports | Actual target comparison receives required value 1. |
| c5999881a21d8 | contradicts | Actual target comparison receives 2, violating the stated value requirement. See invariant dispute below. |
| cb482dcc4a0c1 | unresolved | Target execution result missing; run the actual assertion. |
| c5dd2167f348a | supports | Original/executed arguments agree and supplied current attempt is submitted. |
| cc191a8053cc3 | contradicts | Arguments differ; later unknown also prevents complete-delivery claim. See uncertainty-invariant dispute below. |
| c1c062765510d | unresolved | Original/executed comparison and current attempt observation missing; either a decisive argument contradiction or attempt result may resolve rejection. |
| caeff6c6c4681 | supports | Latest supplied attempt submitted; the complete receipt is positive. |
| cbc761dc81aa1 | contradicts | Later same-message sequence-2 attempt unknown; receipt rejects. See uncertainty-invariant dispute below. |
| cf22aab3d75cf | unresolved | Offered summary is from another operation and actual target attempt is absent. |
| c5af5e427a2f3 | supports | Reported revision 2 equals current revision 2. No shared violated invariant shown. |
| c756d2780a92a | contradicts | Reported revision 1 is offered current at revision 2 in both adapters; similar behavior and shared invariant supported; common implementation cause insufficient. |
| cbfa1b0cc1f0f | unresolved | Replayed current revision missing in both adapters; relationship insufficient; acquire actual replay. |
| c0fb9a45eb2da | supports | First effect acknowledged before another send; no replacement and no unresolved old outcome shown. |
| c94d0a24d43b5 | contradicts | Applied effect becomes failed after replacement and a next send adds another effect despite unresolved old outcome. |
| c9de3dd808cb8 | unresolved | Effect/response/recovery observations missing; acquire effect count and fence behavior. |
| cabe25ecf8ef4 | supports | Session active with two held callbacks. This supports local activity, not completed work. |
| c3e667a1b7532 | contradicts | Session idle with a callback still held. |
| c19acb40f8797 | unresolved | Current activity absent; observe remaining holds and aggregate activity. |
| c85b37db76aaa | supports | Executed callback retains observed actor and work. |
| cef7128badec1 | contradicts | Executed callback takes replacement actor and work. |
| c51eabfc2dae5 | unresolved | Executed callback identity missing; observe original/executed actor and work across replacement. |

First-pass count: eight supports, eight contradicts, eight unresolved. This simple balanced pattern and explicit structured comparisons make this primarily a controlled evidence-composition test. They do not establish unsupervised diagnosis, novel failure mechanisms, or production frequencies.

Findings sent before key inspection:

1. **Assertion invariant mismatch.** `c5999881a21d8` shows the verifier reaching its comparison and correctly rejecting value 2. The question contract's `assertion_execution` means reaching/checking the intended assertion and not miscrediting unrelated exceptions. Its violation is not established. The bad value condition needs its own invariant or an explicit none-of-supplied category; do not call correct fault detection a verifier defect.
2. **Completion precondition versus uncertainty preservation.** In `cbc761dc81aa1` the later unknown attempt is retained and receipt rejects. `cc191a8053cc3` similarly retains unknown alongside an argument contradiction. These contradict an unconditional completion claim, but do not independently show a system violation of the stated uncertainty-preservation invariant. Define whether the question assesses an unsatisfied acceptance requirement or a defect in uncertainty handling, and score consistently.
3. **Overbroad generic claim.** The same claim says the operation is "current and complete" even in the healthy activity case with callbacks intentionally unfinished. Restrict the claim to the explicitly listed local requirement; passing local activity does not establish work completion.
4. **Holdout novelty pending.** The parent reviewer flagged unchanged original-review fixture executions relabeled as fresh holdout. The corpus author acknowledged that this generation is reference replay and is revising adapter, bridge, and busy trajectories. Exact material novelty and grouping must be checked on the replacement candidate; renamed fields, new timestamps, UUIDs, or reruns do not create new incidents.

Final initial-key comparison and later independent outcome validation remain pending. File existence is not an approval gate.

### Replacement packet-only checkpoint

Before opening any v2 key/outcome, the reviewer read replacement initial packets with SHA-256 `3cc1194cf8cb063094bc1b1d0f108b89d7509e5a8729da481e91f29b0a5b97c3` and transformation packets with SHA-256 `fd69badecd26d6ca30241549122b69153fba53a6a558b17f276de528400202f7`. The 24 IDs retain the local judgments above (eight supports/eight contradicts/eight unresolved). The assertion pair now measures actual classifier credit against reached assertion: correct credit for a terminal target comparison versus false credit for a helper exception before comparison. Busy claims now concern aggregate activity, not finished work. Attempt/argument claims now concern supplied completion claims, explicitly separating correctly rejecting product defenses from challenged claims. These changes resolve the corresponding initial semantic disputes if the common question contract uses the same claim-obligation meaning.

The 30 transformation packets cover six parents with three invariance changes and two evidence-sensitivity changes each. The first judgment is: equivalent local wording, irrelevant presentation context, and reversed option order should preserve the parent answer; removing decisive evidence should make the claim unresolved. Common implementation remains insufficient without actual cause evidence. Reversed-order packet files alone do not prove a request-order change: the runner must apply and bind the requested criteria order.

Two further corrections were requested before opening keys: the missing assertion packet retained `right=false`, which was an actual hidden reached-assertion fact rather than a policy constant; hide both observed operands there. The wrong-subject transformation also removed decisive values, confounding subject substitution with evidence removal; retain the foreign values and change only applicability binding for that test. Final machine-readable approval is still pending these corrections and independent key/outcome checks.

### First final-packet judgment recorded before keys

The reviewer confirmed both corrections in initial-packet SHA-256 `162037ddd32be14ed6025996cdb90d68dea5b3f8e1ea1eaf29d52ac1f4ca7060` and transformation-packet SHA-256 `9e58e227127e87eb21377db92a7a089fa3dd9714718e771294670c0624499b54`. The initial judgments remain the recorded 24-case table. A wrong-subject observation must leave the actual target claim unresolved even if the foreign observation itself looks decisive; it must not become a supported violation of the actual target. For the six reversed/equivalent/irrelevant transformations, retain the parent judgment; for the six removed-evidence and six foreign-subject transformations, use unresolved for the actual target. Common implementation cause remains insufficient throughout because no actual source/call-stack cause evidence is supplied. This checkpoint precedes opening the initial key, outcome key, probe source, or any v2 live response. The next step is comparison with the author's initial key, followed separately by raw outcome verification.

### Completed premeasurement label check

**Approved with limits for the exact corpus in `reviews/label-review.json`.** This approval is label/evidence sufficiency for a bounded experiment, not approval of results, all E01–E14 requirements, production adoption, or merge. Historical pending entries above preserve the preparation sequence.

The reviewed canonical corpus digest is `ca68bf8fa36c7e51da5e2b24764e968605b36a6dc13c62b0e7088135d7b8bd4c`; `cases.json` file SHA-256 is `70e3e7c948d46f6453cb424bc96b543aa73b93a06aba50f636b514fe286cedb5`. Initial and transformation packet hashes remain as in the first final-packet checkpoint. All freeze-listed hashes verified. Initial packet/key and outcome-key exports match the actual case records exactly.

After recording packet-only judgments, the reviewer compared all 24 initial keys. The judgment/action/sufficiency labels agree with the independent first pass. The healthy replay pair supports similar valid behavior, no shared violation, and insufficient common-cause evidence; the contract now explicitly allows valid behavioral similarity. Initial applicability of a matched declared context is separate from missing decisive facts. Violations are obligations of the supplied local claim; rejecting a forged completion claim is not itself a product defect.

Only then did the reviewer open the outcome key, corpus/probe code, retained raw results, adapted reproduction sources, and freeze/provenance records. Every eventual outcome is backed by actual controlled observations rather than evaluated model agreement. Missing initial cases correctly remain unresolved until their applicable observation is acquired. All eight outcome probe boundaries were independently rerun in fresh temporary directories. Six ran inside the sandbox; replay/busy initially remained not-exercised due socket PermissionError, then succeeded in a separately approved local socket run. The direct raw checks show:

- Context: cosmetic old/fresh acceptance agrees; material consumed context produces reused true/fresh false.
- Assertion: healthy returned value passes; intended comparison fails at value 2 and earns credit; helper AssertionError before comparison wrongly earns classifier credit. Injected setup/helper-marker/timeout controls stay not-exercised.
- Arguments/attempts: unchanged durable receipt verifies, mutated receipt rejects; raw argument contradiction survives comparison; latest sequence-2 unknown cannot prove completion. No live wrong dispatch is claimed.
- Replay: all three actual adapters return cached revision 1 at owner revision 3 while fresh/direct reads use 3. Adapted source adds the intermediate replay between two owner revisions.
- Effect: the recorded counter is already 1 before held response; two reconnects preserve an intermediate resume source, then failure and another send produce counter 2.
- Busy: three callbacks are held, only B completes, two remain held and activity incorrectly becomes idle; all-complete idle is the healthy terminal control.
- Authority: two actor/work rebindings occur before the old callback executes as final-actor/final-work; no-replacement control retains the original identities.

Reviewer artifact mapping: `/tmp/jev-v2-independent-probe-review` is to be retained as `evidence/preflight/independent-probe-review/`, and `/tmp/jev-v2-independent-probe-review-sockets` as `evidence/preflight/independent-probe-review-sockets/`. These local reviewer runs are separate from corpus-generation and measured policy/audit probe executions. The parent owns publication of the copies; verify their final relative paths in the handoff.

The grouping review found that arguments and attempts derive from the same durable-exchange fixture with the same appended unknown attempt. They now share one calibration group. There are **seven scenario dependency groups**: two development groups (six rows), one calibration group (six rows), and four holdout groups (12 rows). No group crosses partitions. Three variants per probe are correlated; these counts do not prove seven statistically independent production incidents. The four held-out trajectories materially change known schedules (two owner revisions, two effect reconnects, three callback holds/middle completion, two authority rebindings). They test known-mechanism variant transfer, not unseen mechanisms. Original unchanged reruns are retained as reference replay.

Material limits for interpreting later results: invariant names and explicit operands/operators are already supplied, so this mainly tests composition of extracted evidence. Correlation evidence is three views of one adapter family, not broad cross-family correlation. All initially withheld main-case outcomes happen to contradict their local claim; that enriched set cannot estimate production prevalence or justify always investigating. Static accept/reject valuation is hypothetical unless separately tied to controlled action execution. Installed-vendor behavior, prospective learning uplift, and actual business value remain outside this label approval. The reviewer has still seen no live v2 model output at approval.

## Preparation interface findings

These are working-draft findings communicated before evaluation; their resolution must be checked against the frozen implementation.

- The initial contract asked whether the "selected candidate" discriminated while selection was another independent question in the same batch. That answer is not yet an input. The policy author agreed to ask whether any supplied candidate discriminates, keep the observation question independent, and assess the particular selection using later execution. This avoids a false dependency claim but does not prove that the chosen candidate is the discriminating one.
- The initial composition allowed `sufficiency=missing` and `judgment=contradicts` to produce rejection while guarding only positive claims. Under the written sufficiency contract this is inconsistent and should stay unresolved; sent for correction/clarification.
- The initial economic code converted an absent probe duration to zero and did not expose missing linked probe records. The economics author reports adding measured-duration subtotal, unknown-duration counts and missing IDs. Unknown execution cost must remain visible.
- Human-minute prices were initially declared but unused. The economics author reports adding review-minute sensitivity and break-even review time, plus paired incremental stage/comparator economic comparisons. This remains scenario analysis rather than measured review effort or business value.
- The scoring draft chose the complete outcome key whenever `acquired_evidence` was true. This is valid only if that flag means the exact applicable decisive facts were actually revealed. Wrong, unrelated, or partial probes need the epistemic key for their actual observed state; acquisition alone cannot entitle a policy to hidden truth. Sent to harness/scoring authors for an explicit binding/control.
