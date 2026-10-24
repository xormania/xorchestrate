# IV03 independent verification: original witness closed, residual contradictions remain

**Verdict: IV03 remains open. Recommend holding integration for two demonstrated verifier defects.** The exact original callback-native/incarnation counterexample is repaired. Fresh independently designed probes still make the repaired checker accept contradictory native actor records and contradictory lifecycle work records. These are acceptance-checker findings, not demonstrations of live unauthorized dispatch.

The product and separately selected checker are both `e565863ae72180571678c8f6118ee6e00b129e2e`, tree `474de55c5947890ab4092daeca7ff39f9bbbc400`. The repair evidence publication `37547a138758fe70e6cc471b491aaab01bfc3a05` was read as evidence only. The historical source is `ef7567516520543970af55b6f19534cb66eda357`. Source was not modified. Root's directly retrieved GitHub inputs and verified restoration supply the source and earlier evidence.

## What was executed

The handoff, Repair-Report, closure matrix, input identities, REPLAY, original independent review, IV03 component summary, prior adversarial report, checker, normalizer, product receipt policy and identity regressions were read. Expectations below come from the user's explicit IV03 requirement, the handoff's actor/work join instruction, and raw records that already contain the relevant fields. They do not assume every historical record must carry every possible identity.

| Execution | Result |
|---|---|
| Byte-identical original `iv03.py` on ef75675, retained ef raw input | Control and each of three contradictory completed callbacks pass 46/46; production receipt accepts only control. Original defect reproduced. |
| Same script on e565863, same retained ef raw input | Control passes 46/46; wrong native ID, retired incarnation and explicit null native ID each reject, 43/46. Product receipts reject all three. |
| Independent fresh raw matrix | 109 controlled variants, 75 expectations met and 34 false acceptances within the two residual families below. Complete per-case patches and reports retained. |
| Minimal all-peer fresh witnesses on separate checker checkout | 30 cases: 15 false acceptances, zero false rejections. Fifteen positive or rejecting controls behaved as intended. |
| Independent native duplicate controls | Nine cases: exact duplicate passes, contradictory work duplicate rejects, contradictory actor duplicate passes incorrectly for each of Codex, Claude and Grok. |
| Existing identity/evidence/completion modules | 265 passed, no skips, failures or errors. `focused.xml` and `focused.log`. |

The historical original-witness recheck is not fresh product execution. The new probes use the root's newly executed complete 46-case subprocess protocol, run `2758acc2c2ba4d9fa30382058cfea882`, under `results/execution/protocol`. It is deterministic protocol execution, not authenticated vendor execution. `minimal-fresh/identities.json` records the raw file hashes, exact checker module origin and source hashes. A separate checkout provides isolation; the independent oracle consists of the explicit raw consistency comparisons in the reviewer script, not another copy of `verify()`.

## IV03-A: explicit native actor contradiction is erased (P2, verifier)

**Expectation:** when an applicable native observation explicitly identifies its creator, that actor must agree with the corresponding session and executed callback. A good callback cannot supply substitute authority for a contradictory independent native record. An older observation that genuinely omits the optional creator field can remain compatible.

**Minimal change:** in the fresh Codex native `send_message` observation `2996149a39ed3cf8ce88afc9f07b597877f7b960c00145de49aef102063b0628`, change only existing `created_by` from `codex` to `different-actor` (or null). Keep its session `d9cb8cb6eb64469f8a4d0ef43d3beb13`, native conversation `native-9f5df205e59044118b748981f16bd7c2`, incarnation `47a1c6521e23447ca05bfbd7bf20c023`, call `fb0a9911b8a74f7189ce981ce2b5b130`, arguments and completed callback unchanged. The durable session's `actor_id` and callback `created_by` remain `codex`.

**Actual:** full report remains **46/46 passed**. The same one-field wrong and null mutations pass for Claude and Grok. Removing the optional native creator field passes as a compatibility control. Changing the completed callback's creator instead rejects, isolating the native-side asymmetry. Appending a second unique-ID native observation with the same call identity but a different explicit creator also passes beside the valid original; an analogous work contradiction is rejected.

**Cause:** `scripts/verification/normalize.py:28`–42 projects native identity and work but drops `created_by`; callback normalization separately retains it at line 71. `checker.py:107`–121 checks only callback and durable-session actor claims before choosing native proof. The native record's explicit conflicting creator therefore cannot affect either the native join or native audit.

The production `evaluate_exchange` policy also accepts the main native-actor mutation in the broad matrix. That agreement is not an independent correctness oracle: `xorchestrate/exchange.py:29`–40 checks session actor but does not compare observation creator. No runtime exploit or unauthorized provider invocation is claimed from these saved-record substitutions. The demonstrated defect is false verification credit for contradictory independently recorded evidence.

Evidence: `minimal-fresh/codex-native-wrong-actor-records.json`, corresponding report, all-peer results and `duplicates-fresh/`. The broad Grok witness also retains a complete changed `run.json` under `fresh-challenges/main-native-created_by-wrong/`.

## IV03-B: lifecycle callback and saved evidence may disagree on work (P2, verifier)

**Expectation:** the actual saved evidence selected for a retained lifecycle callback must agree with that callback's explicit work argument. Native arguments that explicitly supply a work must agree with application arguments; a missing native argument may be enriched. This is a relation among already present claims, not a new universal requirement that evidence work equal session work.

**Minimal change:** for fresh Codex reconnect, change only saved evidence `c41648cfd3684f32b528da7c676241d3`'s existing `work_id` from `4b28e530370f4ac297e4d1f31bede711` to `different-work` (or null). The completed callback `3b9bb3f616d46cc3a7be85043820b00cb8d16cdac6523cba740d07fd50e2937c` still explicitly has the original `arguments.work_id`, and its embedded result for the same evidence ID still has that original work. Call ID, session, conversation, incarnation, result payload, actor and evidence ID remain unchanged.

**Actual:** full report remains **46/46 passed**, including `lifecycle-codex-reconnect`. Independently changing only callback `arguments.work_id` also retains 46/46. The same three mutations pass for Claude and Grok reconnect. Expanded Codex probes show the same omission at resume, attach and replace; changing native/callback observation `work_id` or an explicit native argument likewise escapes this lifecycle predicate.

**Valid control:** a consistent explicitly selected alternate work is preserved. The reviewer changes native `arguments.work_id`, callback `arguments.work_id`, callback embedded evidence work and independently saved evidence work together, while retaining session scope. All three peers remain 46/46. Thus the expectation permits supported explicit scopes and does not hardwire work to the session. Original native/session/call/incarnation contradictions and lifecycle actor contradictions continue to reject.

**Cause:** `scripts/verification/checker.py:298`–324 validates lifecycle identities, actor claims, verification payload and evidence ID. It never compares the saved effect's work to the application argument, or checks the lifecycle native/application argument relationship. The main join performs those comparisons at lines 133–144, but the separate lifecycle predicate omits them.

Evidence: `minimal-fresh/codex-evidence-wrong-work-records.json`, corresponding report, all-peer results and alternate-work controls. A complete modified raw run is also retained in `fresh-challenges/life-reconnect-evidence-work-wrong/run.json`.

These findings directly limit the handoff's actor/work invariant and H3/H4 evidence correlation and retained lifecycle proof. B2/B5/A2 require correctly associated evidence at applicable boundaries; the findings do not establish that live work-acceptance logic has the same defect. The application remains configurable; no universal actor field, fixed work scope, provider choice, evaluator or approval hierarchy is proposed.

## Replay

From a restored review package, choose absolute paths for `REVIEW` (this review root), `CHECKER` (clean e565863 checkout) and `PYTHON` (recorded dependency environment):

```sh
"$PYTHON" "$REVIEW/results/iv03/minimal_counterexamples.py" \
  --candidate "$CHECKER" --input "$REVIEW/results/execution/protocol" \
  --out /absolute/new-output/iv03-minimal
"$PYTHON" "$REVIEW/results/iv03/duplicate_native_probe.py" \
  --candidate "$CHECKER" --input "$REVIEW/results/execution/protocol" \
  --out /absolute/new-output/iv03-duplicates
```

Both commands deliberately exit **1** when false acceptance remains. The minimal script independently asserts its raw expectation before comparing the checker's report. Every output directory must be new. The full 109-variant recorder `challenge_identities.py` uses the same CLI; it exits zero after recording all results, so its `summary.json`, not its process status, establishes the findings.

To reproduce the original defect, run the unchanged retained `inputs/original-witnesses/iv03.py` with `--candidate` selecting ef75675 and `--baseline` selecting retained ef protocol. Repeat with e565863. `command-ledger.json` records commands, script hashes, paths, exit semantics and results. No witness edits or raw input relabeling were needed. Only absolute workspace paths and interpreter selection were adapted. An initial dependency diagnostic found no system pytest; root supplied the exact pinned venv. Original witness runs require only the Python standard library and used the same Python 3.12.14 runtime.

No failed reviewer witness implementation was discarded or silently replaced. The intended baseline contradictions, all full reports, rejected controls, complete changed runs for two decisive examples and the exploratory matrix remain. The focused suite's clean success is deliberately retained alongside the counterevidence. No product code, source expectation, original evidence, Jev, merge or release was changed.

## Limits and disposition

The current candidate repairs the original IV03 witness and substantially improves native/callback/session matching. It does not close the stated invariant because the two fresh minimal counterexamples survive every positive control and the passing 265-test focused suite. Recommend fixing or explicitly disposing of these verifier defects before integration. Authenticated vendor acceptance remains the user's separate planned work and is not the reason for this hold. No universal guarantee over arbitrary evidence schemas or all protocol schedules is claimed.
