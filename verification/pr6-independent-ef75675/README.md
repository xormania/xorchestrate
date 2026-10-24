# PR #6 independent verification — completed review

**Recommendation: hold integration of `ef7567516520543970af55b6f19534cb66eda357`.** Four controlled counterexamples remain: historical inspection discloses an explicitly denied session; Codex reports idle with an older callback pending; the saved-run checker accepts contradictory callback identities; and both calibrators can misattribute a same-line unrelated exception as an assertion failure.

- [Independent verification report](Xorchestrate-PR6-Independent-Verification-Report.md)
- [Evidence ZIP — download](https://raw.githubusercontent.com/xormania/xorchestrate/verification/pr6-independent-ef75675/verification/pr6-independent-ef75675/Xorchestrate-PR6-Independent-Verification-Evidence.zip) (69,357,690 bytes)
- [Closure verdicts](closure-verdicts.json) and [capability assessment](capability-assessment.json)
- [Replay instructions](REPLAY.md) and [restoration helper](restore_review.py)
- [Checksums](SHA256SUMS), [input identities](input-identities.json), [command ledger](command-run-ledger.json) and [package validation](package-validation.json)

## Result and scope

F02–F06, F09 and F10 are closed within the exercised deterministic scope. F01, F07, F08 and the later aggregate-completion repair remain open. F06 refers specifically to the repaired Claude overlap defect; the new Codex sibling keeps the broader aggregate-completion repair open.

Fresh verification passed **809 primary tests +164 subtests**, **46 composed protocol cases**, all **89 original /165 final mapped selectors**, and all **12 original source-fault calibrations**. These green results do not dispose of the four additional counterexamples. The evidence retains positive and negative controls, exact source identities, raw measurements, logs/JUnit, source mutations, failed attempts and minimal reproductions.

Product/checker commit: `ef7567516520543970af55b6f19534cb66eda357`.
Product tree: `a0813aae760e6e5bb6245693bd92b7e7c321348b`.
PR: https://github.com/xormania/xorchestrate/pull/6.

Authenticated vendor acceptance remains xor's planned local work. No product repair, merge, release or Jev experiment change is included.

## Retrieve and verify

```sh
git clone --single-branch --branch verification/pr6-independent-ef75675 https://github.com/xormania/xorchestrate.git xorchestrate-pr6-verification
cd xorchestrate-pr6-verification/verification/pr6-independent-ef75675
sha256sum --check SHA256SUMS
python3 restore_review.py Xorchestrate-PR6-Independent-Verification-Evidence.zip --verify-only
python3 restore_review.py Xorchestrate-PR6-Independent-Verification-Evidence.zip --out /fresh/review
```

Read [REPLAY.md](REPLAY.md) before source recovery or executing probes. The ZIP stores repeated file contents once by SHA-256; the helper reconstructs every retained path. All **57,328 files** were independently verified and fully restored. The four decisive witnesses were rerun from restored source/evidence. The package includes the unchanged prior repair ZIP and a separate, complete `verified-source.bundle` for the exact candidate, evidence publication and CI integration identities.

Evidence ZIP SHA-256:

```text
d161f2318727b8b52aa5dd78fa91d6d5ebc1aa4c12103ec8e385b328715833dc
```

## Publication provenance

This verification-only branch is a later evidence publication authorized by xor after the review completed. It adds only this directory to the pinned product candidate; it does not advance the PR's product branch or change product files. The standalone report and ZIP remain byte-for-byte identical to the completed review deliverables.

Statements in the frozen report/evidence that no remote branch had been changed, or that publication was awaiting a destination, describe the completed review phase. This README records the subsequent publication authorization and action; historical records were not rewritten. No PR comment or additional pull request was created for this publication.
