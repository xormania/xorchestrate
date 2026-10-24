# PR #6 — fresh independent verification of e565863

**Recommendation: hold integration.** IV01–IV03 have demonstrated residual defects. IV04's false-detection defect is closed in the executed scope, with a documented optional encoding-sensitivity improvement.

The product is **`e565863ae72180571678c8f6118ee6e00b129e2e`**, tree **`474de55c5947890ab4092daeca7ff39f9bbbc400`**. This verification publication is a separate evidence-only child; do not select its commit as the product. PR #6 remained on the requested candidate. No product repair, merge, release, PR comment or Jev change was made.

- [Independent report and integration recommendation](Independent-Review.md)
- [Machine-readable closure verdicts](closure-verdicts.json)
- [Replay instructions](REPLAY.md) and [input identities](input-identities.json)
- [IV01: typed assessment disclosure](IV01-Report.md)
- [IV02: accepted Grok interjection completion veto](IV02-Report.md)
- [IV03: explicit native actor and lifecycle work contradictions](IV03-Report.md)
- [IV04: assertion attribution and sensitivity limits](IV04-Report.md)
- [Coverage, provenance and independent raw audit](Preservation-Report.md)
- [Fresh preservation of seven previously closed findings](Closed-Findings-Report.md)

The four original defects were reproduced on `ef75675`; their exact witnesses behave correctly on the repair. New controlled counterexamples prevent complete closure. Fresh full suite: **1,127 passed +164 subtests**, zero failures/errors/skips. Fresh actual composed protocol: **46/46**, exact recomputation, independently corroborated raw joins/exchanges/work answers. All twelve original source faults remain valid. The original 89/frozen 165 selectors and 46 added functions reconcile to 211 mapped selectors. A concurrent focused-run calibration failure and every material failed/superseded attempt are retained, alongside isolated and full focused passing replays.

## Download and verify

[Xorchestrate-PR6-Independent-e565863-Evidence.zip](Xorchestrate-PR6-Independent-e565863-Evidence.zip): **27,641,890 bytes**, SHA-256:

```text
8a5cfc2a8e3ac7ce50b4730d060974f1dc9c29f88430533e377178ac789d1916
```

The primary archive losslessly retains **30,893 files**, 3,208 unique payloads and 499,607,329 logical bytes: raw observations/databases/JUnit/logs, actual source-fault controls, reviewer scripts, failed attempts, original witnesses and minimal historical raw inputs, exact source bundle, command records, identities and evidence audits. [package-manifest.json](package-manifest.json) maps paths/modes/hashes to content-addressed payloads and lists exclusions. Use the restore helper rather than ordinary ZIP extraction.

```sh
sha256sum -c SHA256SUMS
python3 restore_evidence.py Xorchestrate-PR6-Independent-e565863-Evidence.zip --verify-only
python3 restore_evidence.py Xorchestrate-PR6-Independent-e565863-Evidence.zip --out /absolute/fresh-review
```

[Package-QA-Evidence.zip](Package-QA-Evidence.zip) and [package-qa-summary.json](package-qa-summary.json) separately retain verification/restoration of the exact final archive, independent hashing of every restored file, source recovery to the exact clean tree and all four minimal replays from the restored bytes. They also preserve the earlier package QA attempt before a count-label correction. The QA archive is separate to avoid checksum self-reference. [SHA256SUMS](SHA256SUMS) binds every standalone artifact, including both ZIPs.

All source inputs were retrieved directly from GitHub. Complete earlier archives remain unchanged at the immutable [repair publication](https://github.com/xormania/xorchestrate/tree/37547a138758fe70e6cc471b491aaab01bfc3a05/verification/pr6-iv-repairs-e565863) and [original independent review](https://github.com/xormania/xorchestrate/tree/3ac2342b6655f1a30652380c85ceb55262b7ad0c/verification/pr6-independent-ef75675); their checksums and complete verification are recorded. They are not redundantly embedded here. REPLAY includes their retrieval/restoration for the full retained/hosted provenance audit.

The review covers bounded deterministic subprocess behavior and evidence verification. Authenticated vendor acceptance remains xor's planned local work, not a prerequisite imposed by this review. A separate checkout of the same checker supplies isolation, not an independent correctness oracle.
