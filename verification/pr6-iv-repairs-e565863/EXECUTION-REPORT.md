All requested local execution is complete on frozen candidate `e565863ae72180571678c8f6118ee6e00b129e2e`, tree `474de55c5947890ab4092daeca7ff39f9bbbc400`. Candidate and separate checker stayed clean and byte-identical throughout measurement. This is repair-session verification, separate from the next independent session and xor's authenticated vendor acceptance.

| Fresh execution | Result | Raw evidence |
|---|---|---|
| Full active application suite | **1,127 primary tests +164 subtests passed**, no skips, failures or errors | `application.xml`, `commands/application/` |
| Focused repair/preservation modules | **594 passed**, no skips, failures or errors | `focused-inventory.json`, `repairs.xml`, `commands/repairs/` |
| Original and previous final mapped selectors | **89/89 and 165/165 passed** against actual final XML cases | `coverage/original.json`, `coverage/previous-final.json` |
| Fresh actual composed protocol fixture | **46/46 passed**; recomputed raw report exactly equals saved report | `protocol/`, `protocol-recomputed.json` |
| Original source calibrations | **12 detected**; independent audit verifies 36 passing control cases, 19 intended assertion failures, 17 unaffected mutant passes; no errors/skips | `native-calibration/`, `work-calibration/`, `calibration-independent-audit.json` |
| Static checks | Ruff, security-lint coverage, 13 optional-toolchain checks and compile passed | Corresponding `commands/` directories |
| Live tmux protocol fixture | Passed; cleanup verified, not forced, zero remaining processes, seven observed | `tmux-live-fixture/` |

Totals overlap and must not be added. The full XML contains 1,127 `testcase` elements; its `testsuite tests="1291"` includes 164 subtests. Focused 594 is a subset of the full inventory, selected as the previous nine repair modules plus every changed/new test module since `ef75675`. The previous primary inventory was 809; the final count is 318 higher, but no count was used as the acceptance predicate.

All four decisive witnesses were replayed from unchanged script copies with explicit result checks:

- **IV01:** the immutable original exits 1 at its old assertion requiring the historical leak, after preceding controls have passed. The separately preserved closed-behavior adaptation passes on MemoryStore and SQLiteStore: direct private-session inspection denies, current and historical projections remove the private canary, and authorized public data survives. The original's expected exit 1 is not a product failure.
- **IV02:** all six Codex/Claude/Grok AB/BA schedules start active/incomplete, remain active/incomplete with one callback held, and finish idle/passed only after both finish. Four additional Codex/Grok ABC/ACB schedules remain active while native tails remain held even after their application callbacks returned; all end idle only after every native tail ends.
- **IV03:** the untouched retained raw control still passes 46/46 and the production receipt verifies. All three one-field completed-callback identity contradictions reject in both the checker and production receipt evaluation.
- **IV04:** both classifiers reject the same-line and separate-line unrelated raises as `unrelated_failure`, while the genuine assertion remains `detected`. Controls exit 0 and faulty subjects exit 1. The original 12 real source-fault detections retain valid credit.

The extended IV01 cross-review also passes on this final commit: provider, capability and metadata permissions; bound measurements; direct/historical operation projections; typed nested siblings; requested invocation/measurement collisions; grant-restoration and opaque-payload controls. Its seven script groups supply 26 result rows across stores/inspectors/scenarios, plus six catalog regression cases. See `witnesses-iv01-complete/results/review.json` and each retained raw JSON.

One **recorder setup failure** remains preserved. The first witness batch copied five extended IV01 scripts, while its latest wrapper also required two newly added requested-binding probes. It stopped with `FileNotFoundError`; the original batch's overall `passed:false` verdict was not changed. Every other individual semantic check in that batch passed, including all four original witnesses and the native-tail controls. Only the extended IV01 group was repeated in fresh `witnesses-iv01-complete/` with the complete unchanged script set; all checks passed. The omitted paths, original recorder snapshot, two-line copy-list patch, script hashes and narrow retry are documented in `witness-recorder-copy-adaptation.json` and `.patch`. No product or expected behavior changed to address this packaging mistake.

The raw calibration audit independently checks actual control/mutant source changes, exact mutation activation, XML identity/exit/status, every passing control, expected assertion marker, trusted observer hash and terminal assertion source. For the new observer it additionally reconciles the recorded `RAISE_VARARGS` instruction span and asserted AST span against the actual source. The audit does not import the candidate's classifier. Its 12 cases exercise the original declared faults; the separate IV04 adversarial controls challenge attribution beyond those cases.

Final protocol bindings:

| Boundary | SHA-256 |
|---|---|
| Product contents | `53c2873d9311c9492958e4beeec7da801cb9cfe4a76fea8d270dbba775acfc49` |
| Checker files | `372a9d24352752610868ad367912380cb5e1e83ed8b77eea92b02434a1dfc5f5` |
| Configuration | `b1cc50c307f0f46c4817073e42f4dd397f662b938c2f0e0393c91f9e89e525c7` |
| Criteria | `75d44ff3e1c04ad0a82dcdeb3ebd08c2904cc654dd4e4e8b820a9e6dbd5abbf7` |
| Fixture | `d2e6b553a9b8995ab47097a07a97867504dfa06a1df631e6a479cc8056f5702d` |
| Owner basis | `41545d3fab334e432f58f1f1b1367c70bed29bf2c39c1280c3eacd9cbdc783ad` |

Python 3.12.14 and declared dependencies were selected through the repair venv. Every command sets `PYTHONPATH` to its chosen checkout; separate module-origin checks prevent an older editable installation from selecting stale source. The explicitly enabled local socket capability and isolated hash-verified tmux executable provide the process/transport prerequisites. No authenticated vendor or Jev call was made.

`execution-summary.json` aggregates all outcomes without changing the failed attempt. `command-ledger.json` indexes 17 primary commands; each command directory retains argv, cwd, selected environment, UTC times, exit status and output hashes. Witness and coverage directories have separate ledgers. Raw candidate/checker inventories before and after bind the source files. `protocol/run.json` retains its run ID, raw observations and durable records. Replay uses `execution-prep/run_final.py`, then `run_witnesses.py` with a fresh output directory; exact arguments are retained in recorder and command records. The corrected copy list makes a fresh witness replay complete; the historical incomplete invocation remains independently reviewable.
