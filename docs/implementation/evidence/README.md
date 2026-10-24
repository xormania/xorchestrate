# Measured implementation evidence

The source candidate is `b0a00f1aed08d4ad8e066de8eb51f94e21da9f4a`, based on `30254fd0f184b7e31aa7e0896347429004933ed4`. This later publication contains generated evidence, not a new measured source candidate. No merge or release was performed.

- [Requirement-by-requirement result](requirement-matrix.md)
- [Exact candidate, input identities, timestamps and outcomes](final-manifest.json)
- [Executed commands and evidence paths](executed-checks.md)
- [46-case composed report](protocol-report.json)
- [12 calibrated source faults](calibration-results.json)
- [Hosted CI and checkout identity](hosted-ci.json)
- [Complete evidence archive](verification-evidence.tar.xz) and [artifact hashes](artifact-index.json)
- [Implementation handoff](../HANDOFF.md), [decisions and expectation review](../CHANGE-REVIEW.md), [delegation/contracts](../EXECUTION.md), and [complete owner brief](../owner-brief.md)
- [Runnable local authenticated procedure](../../behavioral-verification.md)

Extract the archive outside the candidate checkout. It creates `evidence-data/` containing actual commands and logs, JUnit, candidate input hashes, criteria/checker/configuration/fixture identities, raw protocol/lifecycle record derivatives, complete control/fault mutation records, component handoffs, earlier failed attempts and redaction provenance. `artifact-index.json` hashes each extracted file.

The measured working tree was clean; check out the candidate commit to recover all source bytes. Isolated calibration source copies can be reconstructed from that commit plus each retained one-match mutation/patch. Work-calibration input hashes were independently compared with the final candidate manifest. No duplicate source archives are required for the clean git-backed candidate.

`component-source-recovery/` retains overlays and a reconstruction tool for 17 pre-freeze component snapshots. One older transport manifest has 12 superseded hashes that differ from its overwritten snapshot; that mismatch is recorded explicitly. These development records do not replace the fully recoverable final candidate or its repeated final checks.

Bridge resume credentials are replaced consistently in published JSON derivatives; private host-state files and SQLite/peer-state copies are omitted. Original and derivative hashes and changed JSON paths are retained. All checker-required raw associations remain. Recomputing the report from the derivative produced the identical 46-case pass. These are deterministic synthetic peers; no authenticated native traces were collected.

Local final results: 588 passed, 10 environmental skips, 164 subtests passed; 46/46 composed protocol cases; 12/12 assertion-specific source faults; static/architecture checks passed. Hosted CI is recorded separately, including environment-dependent coverage. Failed runs receive no final pass credit and remain retrievable.

Authenticated native acceptance by xor and the section 11 fresh-session independent review remain pending. The implementation author's checker run is not independent approval. Full behavioral acceptance is not claimed.
