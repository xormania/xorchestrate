# Xorchestrate PR6 — IV01–IV04 repair evidence

This verification branch publishes repair evidence. **The product candidate is
`e565863ae72180571678c8f6118ee6e00b129e2e`**, tree
`474de55c5947890ab4092daeca7ff39f9bbbc400`, on `repair/independent-findings`.
Do not select this evidence publication commit as the product candidate.

Read the [repair report](Repair-Report.md), [closure matrix](closure-matrix.json),
[exact identities](input-identities.json), [replay guide](REPLAY.md) and
[next independent verification assignment](Independent-Verification-Handoff.md).
The report distinguishes component attempts, failed attempts, frozen execution,
historical rechecks and same-session adversarial review.

All four findings are repaired in the examined deterministic scope. Final local
and hosted Python 3.11/3.12 suites each pass **1,127 tests +164 subtests**, with no
skips. Composed protocol is **46/46**, with exact raw-report recomputation.
All **12 original source faults** remain detected. The original 89-selector and
frozen 165-selector maps are preserved; 46 additions are explicitly mapped
(211 total).
[Hosted CI](https://github.com/xormania/xorchestrate/actions/runs/40132785317)
and its raw artifacts are included in the archive.

No merge or release was performed. Jev remains separate. This is implementation
verification and an adversarial review within the repair session; the subsequent
fresh-session independent review remains pending. Authenticated vendor acceptance
remains the user's planned local work.

## Retrieve and checksum

Download the files from this directory at the immutable commit shown in the
GitHub URL. Alternatively, clone the verification branch and record its exact
HEAD before proceeding:

```sh
git clone --single-branch --branch verification/pr6-iv-repairs-e565863 \
  https://github.com/xormania/xorchestrate.git pr6-repair-evidence
git -C pr6-repair-evidence rev-parse HEAD
cd pr6-repair-evidence/verification/pr6-iv-repairs-e565863
sha256sum -c SHA256SUMS
sha256sum -c ARCHIVE.sha256
python3 restore_evidence.py Xorchestrate-PR6-IV-Repair-Evidence.zip --verify-only
python3 restore_evidence.py Xorchestrate-PR6-IV-Repair-Evidence.zip --out restored
```

Primary ZIP: `Xorchestrate-PR6-IV-Repair-Evidence.zip`  
Bytes: **60,250,515**  
SHA-256: `0ed0b6eccb4e15f381865e191b95e4b3e949431dd6c09989b2a38786a00200f0`

The primary ZIP contains **61,311 files** in a lossless content-addressed
layout, including raw results, source/observer snapshots, exact source bundle,
commands, minimal witnesses, adaptations and failed attempts. `package-manifest.json`
maps original paths and modes to payloads. Use the helper to restore it; ordinary
ZIP extraction does not recreate the original tree. The archive keeps output
outside the product. Its exclusions identify caches, Git administration and
runtime endpoints/aliases.

`Package-QA-Evidence.zip` and `package-qa-summary.json` separately verify this
exact primary ZIP: all payloads and restored files, source recovery and fresh
minimal witness replays. They are outside the primary ZIP to avoid a checksum
self-reference. Both are covered by `SHA256SUMS`.

The unchanged prior independent review remains available at
[`3ac2342b6655f1a30652380c85ceb55262b7ad0c`](https://github.com/xormania/xorchestrate/tree/3ac2342b6655f1a30652380c85ceb55262b7ad0c/verification/pr6-independent-ef75675).
Its 69,357,690-byte ZIP has SHA-256
`d161f2318727b8b52aa5dd78fa91d6d5ebc1aa4c12103ec8e385b328715833dc`.
The new package includes byte-identical original scripts and the minimal prior
raw inputs needed for replay; it does not duplicate the entire older archive.
The complete older archive is independently revalidated and remains directly
retrievable at that immutable publication.

## Continue

Follow `REPLAY.md` to fetch the bundle's explicit `refs/repair-evidence/*`, recover
the product and separate checker, install recorded dependencies, replay witnesses,
run the full suite/calibrations/protocol and audit raw CI artifacts. Start the
next review with `Independent-Verification-Handoff.md` and the exact product
identity above. Passing summaries and this session's closure claims should be
challenged independently.
