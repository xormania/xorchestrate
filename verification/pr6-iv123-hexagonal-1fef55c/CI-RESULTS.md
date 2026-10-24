# Final normal CI

[Run 40141188920](https://github.com/xormania/xorchestrate/actions/runs/40141188920) was triggered normally by the PR update. **All six jobs passed on attempt 1; no job was manually rerun.**

Product: `1fef55c0c0cadd5e1417c0e010f272f6447f8ea4`.
Product tree: `75ecb3ef4edbc381012fe74e416a9abd3fb5c667`.
Hosted integration: `2894c5eb2d210656e6d29293a85836a846672c45`.
Its independently retrieved parents are PR base `b0a00f1aed08d4ad8e066de8eb51f94e21da9f4a` and the product commit. Its tree exactly matches the product tree. The integration commit is GitHub's test merge; no merge into the base branch was performed.

| Normal job | Result and retained audit |
| --- | --- |
| Static checks | Passed: runtime/test/tool lint, security-lint coverage, optional toolchain helpers and compilation. |
| Recovered toolkit tests | Passed: 285 tests, no skipped or unexecuted cases. |
| Application and adapter tests, Python 3.11 | Passed: 4,067 primary cases +164 subtests, no failures/errors/skips. |
| Application and adapter tests, Python 3.12 | Passed: 4,067 primary cases +164 subtests. Original calibrations and composed protocol also passed. |
| Tmux live acceptance, protocol fixture | Passed: 11 checks, clean process exit and cleanup. This is deterministic protocol evidence. |
| Imported runtime regression, historical source | Passed: 825/825 tests, zero failures/errors/skips/not-run cases. The raw census is retained in `evidence/hosted-ci-final/audit/runtime-final.json`. |

Independent calibration audit: 12 unchanged original faults, 36 passing controls, 19 intended assertion failures, 17 unaffected mutant passes, zero errors/skips. It examines raw XML and source assertion locations, not just the calibrators' summary verdicts.

Hosted protocol: 46/46 cases, exactly recomputed from 1,006 raw observations using an isolated final checker. All 17 recursive checker sources and 1,339 candidate source files match the exact candidate; content bindings verify. A separate standard-library raw audit checks callback joins, exchange timing and work answers independently of verdict logic.

The archive preserves metadata, all six decoded job logs, every published artifact, GitHub archive-digest checks, extracted raw XML/protocol/census, source identities and audit scripts. Artifact URLs can expire; the archived copies remain available through this Git publication. Retrieval failures and connector adaptations are documented in the hosted-evidence README.

The superseded `42c4d8b` also passed all six normal jobs in [run 40140471563](https://github.com/xormania/xorchestrate/actions/runs/40140471563), with 2,934 tests +164 subtests. Its later typed-native-return counterexample failed despite those passes. Its CI is retained under `evidence/hosted-ci`; it is not substituted for the final candidate's results.

Final local results independently corroborate the full suite, static checks, 285 toolkit tests, tmux, 46-case protocol and 12-fault calibrations. The original local suite continued through a control-plane reconnection and passed; the separately retained precautionary repeat passed as well. All credited source-integrity comparisons remained clean and unchanged.
