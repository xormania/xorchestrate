# Hosted CI audit

Run [40132785317](https://github.com/xormania/xorchestrate/actions/runs/40132785317) completed successfully on its first attempt. All six jobs passed. No CI rerun was requested. This is repair-team evidence review, not a later independent verification session.

The workflow head is `e565863ae72180571678c8f6118ee6e00b129e2e`, tree `474de55c5947890ab4092daeca7ff39f9bbbc400`. Jobs executed GitHub's PR integration commit `cf424865c3e86f93b98e015621158c995d5ae187`. Its independently fetched commit object names parents `b0a00f1aed08d4ad8e066de8eb51f94e21da9f4a` and `e565863ae72180571678c8f6118ee6e00b129e2e` and exactly the candidate tree. `git-object-audit.json` records independently recomputed Git object hashes for both commits and the tree; raw object bytes are retained. Both Python artifacts record that integration commit and tree. Their parent line is empty because checkout is shallow; no parent identity was inferred from that omission. The first auditor attempt expected the parent line and failed; `artifact-audit-attempt-1.json` and the v1 script preserve that auditor-only limitation, corrected using the independently fetched object proof.

| Job | Conclusion | Job ID |
| --- | --- | --- |
| Application and adapter tests (Python 3.12) | success | 120987877941 |
| Imported runtime regression (historical source) | success | 120987878090 |
| Tmux live acceptance (protocol fixture) | success | 120987878096 |
| Recovered toolkit tests | success | 120987878150 |
| Application and adapter tests (Python 3.11) | success | 120987878157 |
| Static checks | success | 120987878221 |

Both Python 3.11 and 3.12 application XML files contain 1,127 passing testcases, no failures, errors or skips. Their raw logs additionally report 164 passing subtests each. All 20 new IV04 source-position attribution cases passed on both Python versions, including ordinary/rewrite modes and missing-position controls. `artifact-audit.json` retains exact testcase identities.

Recovered toolkit logs reconcile six module censuses to 285 executed, zero skipped or unexecuted. Historical runtime `test-census.json` reports 825 executed, no skipped/unexecuted/failing/error cases. Static optional-toolchain helpers executed 13 tests. Tmux acceptance regressions executed 22 tests; the separate local protocol fixture passed 11 checks and verified cleanup of 9 observed processes, with zero remaining, no forced cleanup and no cleanup error.

The hosted composed protocol raw run recomputes to all 46 criteria passed using the frozen candidate checker. The independently recomputed report exactly equals the hosted report. This is protocol-fixture evidence; real native-vendor acceptance remains `pending_local_run`.

All 12 original hosted fault calibrations independently reconcile their XML/observer evidence: 36 passing control testcase executions, 19 intended assertion failures, 17 unaffected mutant passes, zero errors or skips. `calibration-audit.json` verifies target identity, marker, exception phase/type, exact raising instruction/source position, observer hash and mutation anchor. The hosted workflow omits measured source copies and work mutation.patch; native mutation.json and fixed work CASES are reconciled with frozen candidate source. Local final calibration evidence retains actual source copies. Omitted hosted source bytes are not claimed as downloaded.

All five artifact archives match GitHub's SHA-256 digests and pass ZIP CRC/path validation. Every extracted member has a retained byte count and SHA-256 in `artifact-audit.json`.

| Archive | Bytes | SHA-256 |
| --- | ---: | --- |
| composed-protocol-evidence | 1149765 | `57423c2f92f3f64bb464f9f10b4b8d675350236f0f6a9843a011eeb764747f93` |
| repair-checks-python-3.11 | 18793 | `5ef6bb0ccc9109d35ee51b2fd89c62875c3ab3a83f7c1e3609cba2d4be2082a9` |
| repair-checks-python-3.12 | 162441 | `fcbc4d10d807f967809f7b3dc59c2e06ff9eb9b9b561e2f0c7cae321e8d0c923` |
| runtime-evidence | 416020 | `f3b004280d00fc61d70d9b99393b5ef761ba8b779ce0167d163df32c64afa1ed` |
| tmux-acceptance-evidence | 1017 | `23a033b11e2cffd02e649a802fb2de85c27c576e6b5e86d2c66ff735a41a09e9` |

## Reproduction

Use a checkout at the recorded candidate commit with its test environment installed. Set `ci_evidence` to this directory and `product_checkout` to that checkout. The scripts live in this directory's parent.

```bash
python "$ci_evidence/../inspect_artifacts.py" --root "$ci_evidence" --metadata "$ci_evidence/api/artifacts-final.json" --candidate e565863ae72180571678c8f6118ee6e00b129e2e --tree 474de55c5947890ab4092daeca7ff39f9bbbc400
python "$ci_evidence/../audit_calibration.py" --root "$ci_evidence" --candidate "$product_checkout"
PYTHONPATH="$product_checkout" python -m scripts.verification check --run "$ci_evidence/extracted/composed-protocol-evidence/run.json" --criteria "$ci_evidence/extracted/composed-protocol-evidence/criteria.json" --bindings "$ci_evidence/extracted/composed-protocol-evidence/bindings.json" --fixture "$ci_evidence/extracted/composed-protocol-evidence/fixture.json" --out "$ci_evidence/protocol-recomputed.json"
python "$ci_evidence/../summarize_ci.py" --root "$ci_evidence"
```

`api/` retains GitHub run/job/artifact snapshots, `logs/` retains all six job logs, and `archives/` retains the five exact downloads. `acquisition-ledger.json` records tool/API sources and local verification commands. Signed download URLs are not retained.
