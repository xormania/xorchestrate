# Telemetry verification — 7 November 2026

This receipt records independently authored tests, observed RED failures, targeted GREEN runs, and adversarial review for the three telemetry PRs. Counts are checkpoints with different test inventories; they are not additive. The companion [machine-readable receipt](verification-2026-11-07.json) retains exact log SHA-256 values, short failure excerpts, source identities, commands and reviewer records.

## Authorship and source identity

| Area | Implementation author | Independent test author | Adversarial reviewer |
|---|---|---|---|
| Foundation | `collector_impl` | `collector_tests` | `preserve_tests` |
| Producer capture | `capture_impl` | `capture_tests` | `collector_tests` |
| Preservation and observability | `preserve_impl` | `preserve_tests`; composed workflow by `qualification`; compact receipt regressions by `collector_tests` | `capture_tests` |

The lead agent assembled the stack, CI/configuration and documentation. The cited test authors edited test/contracts/support/fixture files rather than their subject's production implementation. Agent task names identify separate sessions, not separate human accounts.

The upstream baseline is `9a38bae1005a23ef5420b6bbcff849a2959b9a08`; its exact active-source local reconstruction is `df4d736bdd006fc19ad2b9a571708fac81bbc8fc`. The collector dependency is CCE `84229bb7497547d2db91d02a4633ccb3867f740b`. Local source-tree hashes and published GitHub tree hashes differ because publication preserves the remote repository's archives; the publisher checked changed-file bytes and merge content.

| Published increment | Draft and exact published source | Hosted result |
|---|---|---|
| Foundation | [PR #18](https://github.com/xormania/xorchestrate/pull/18), `523c256a45a79c1ea2a01ff26601ee8f5bec147e` | [Run 42406093650](https://github.com/xormania/xorchestrate/actions/runs/42406093650), green |
| Producer capture | [PR #19](https://github.com/xormania/xorchestrate/pull/19), `1cf8a7a423e2991ebc8ace3e4049552ad1dfe1d2` | [Run 42406898147](https://github.com/xormania/xorchestrate/actions/runs/42406898147), green |
| Preservation/observability | This PR; final publication identity and CI run belong in its PR description | See the exact source checkpoint below |

## RED → GREEN evidence

| Area | Meaningful RED control | Targeted GREEN checkpoint | Receipt keys |
|---|---|---|---|
| Foundation | Baseline: **30 failed, 3 passed**; review regressions: 2, 3 and 2 failed | **40 passed** | `foundation-baseline`, `foundation-green`; `foundation_author` |
| Producer capture | Pristine baseline: **37 assertion failures, 1 control pass** in 38 tests | **46 passed**; later transcript follow-up: 11 passed | `producer-baseline`, `producer-green`; `producer_review` |
| Preservation semantics | **3 tests, 7 failed assertions** for cumulative streams, outcome populations and metric values | **44 deep tests passed** at review checkpoint | `preserve-semantics-red`, `preserve-review-green` |
| Native-message reingestion | **1 failed**: identical 10-token message charged 20 across app-session incarnations | **15 usage/view tests passed**; raw bindings retained | `native-reingestion-red`, `native-reingestion-green` |
| Snapshot final-readback mutation | **1 failed** when only the final authorization callback was removed in memory | Original implementation: **1 passed** | `snapshot-race-mutant-red`, `snapshot-race-green` |
| Composed public workflow | Original baseline: **10 failed, 0 errors**; exact review baseline: **4 closure failures** plus **4 mixed-read failures**, 0 errors | Final: **18 passed**, 10.97 s | `workflow-baseline`, `public-auth-races-red`, `public-read-revision-red-final`, `public_workflow_final_green` |
| Compact public bundle receipts | Exact review baseline: **4 failed, 0 errors** | **4 passed** using normal shared catalog | `bundle_receipts` |

Every receipt key above is retained in the [JSON](verification-2026-11-07.json), with exact log hashes and short assertion excerpts. The final mixed-read RED and GREEN inventories have identical test-file hashes.

The failures were behavioral assertions. Examples retained in the JSON receipt include:

```text
TELEMETRY_PRECREATION_REQUEST_MUST_INHERIT_CURRENT_CREATED_WORK_AUTHORIZATION
TELEMETRY_OPAQUE_MARKER_MUST_NOT_DEREFERENCE_ANOTHER_EVENTS_PROTECTED_ARTIFACT
missing observed evaluator.request at the producer boundary
AssertionError: 25 != 15
AssertionError: 20 != 10
Comparison must reauthorize the complete cohort union before returning private data
Public bundle completion must require current authority for its entire mixed closure
```

The final-readback snapshot race also has an isolated negative control: remove the final verification's authorization callback in an in-memory copy of the module, leaving candidate files untouched. The test fails with `PermissionError not raised`; the original source passes. This is identified as a mutation control, not mislabelled as a fresh baseline execution.

## Adversarial review and repairs

| Review | Actionable findings and repair | Closure receipt |
|---|---|---|
| Foundation | Precreation attempt scope leak; private source status metadata; oversized rehydrated event page; forged opaque payload marker dereferencing a protected artifact. Repaired with trusted scope resolution, authorized metadata/projection and artifact closure checks. | `foundation_review`: four findings closed; no remaining actionable findings in reviewed scope |
| Producer capture | Rejected request omitted; timeout context lost; configured evaluator credential leaked through semantic result; slow observer held protocol deadline; same-inode transcript rewrite reused old offset. Repairs include producer-boundary capture, context retention, semantic protection, bounded dispatch and prefix continuity detection. | `producer_review`, `producer_exact_review`: repaired; no pending findings in bounded review |
| Preservation and views | Cumulative stream double charge, fabricated metric values, non-result comparison population, final snapshot authorization race and native-message reingestion accounting. Independent RED controls preceded repairs, except the explicitly labelled snapshot mutation control. | Deep controls and logs above; `controls` |
| Public integration | Comparison cohort union revocation; final mixed-work bundle closure revocation; oversized manifest responses; current-policy mixed-read guards. Final targeted reproductions deny revoked access and retain the complete manifest behind compact receipts. | `final_public_review`, `public_workflow_final_green`, `bundle_receipts` |

Foundation review was frozen at local `e217e976af6f55a3c1440e39e1f817b671ca81a7`, tree `6976ecc7478ed63e30180a813ffdbb8088bb0a44`. Producer review covered `699f559cb112e929094e6c3672bd9e9202a240bc` and was mapped to final local candidate `910b798f717b7b70b4f1bcb3a7029406d843689e`: the retained mapping checks identical import bindings/non-import AST and unchanged independent test bytes. These source-matched reviewer receipts were supplied before opening their corresponding drafts. The final PR3 reviewer receipt and checkpoint are retained in the JSON.

The final production freeze is `eda5bc3d0aa6427794c2fbc8b9ce9a1f9497e3a3`. The independent 18-case public workflow run used `70d779d4fa4bea4ce7b4821aaed5b11f1dc3e0ac`, with no production delta; its small fixture-order correction preserves actual membership and the failure oracle. The reviewer inspected that test-only correction and recorded no remaining actionable findings in the targeted integration scope. The retained seal covers **170 source/test files**, aggregate SHA-256 `930857d36bc051ea142f74ccb60b58ac80b4fe655578c1d75627f49f3936b291`.

The final reviewer reproduction retained **1,200 valid links** in a **1,290,102-byte** saved manifest. Snapshot and verify application responses were **550 bytes**, restore **738 bytes**, and the actual MCP verify response **1,265 bytes**. These exact fixture measurements support the bounded-receipt repair; they are not general performance guarantees. See `final_public_review` and `final_public_review_reproductions` in the JSON.

## Commands and limits

Local qualification used Python 3.12.14 linked to SQLite 3.53.1, the pinned collector source, and caches outside the candidate. Representative commands (with their exact run identities in the JSON) are:

```sh
python -m pytest -p no:cacheprovider tests/hexagonal/test_telemetry.py -q --test-inventory /outside/candidate/foundation.json
python -m pytest -p no:cacheprovider tests/hexagonal/test_producer_capture.py tests/hexagonal/test_native_producer_capture.py tests/hexagonal/test_transcript_producer_capture.py -q
python -m unittest tests.hexagonal.test_telemetry_preservation tests.hexagonal.test_telemetry_views -v
python -m pytest -p no:cacheprovider tests/hexagonal/test_telemetry_workflow.py -q --test-inventory /outside/candidate/workflow.json
python -m scripts.testkit run --suite checks
```

Baseline RED reconstruction uses the identified baseline production source plus the independently authored test revision. The exact public-race control was run in an isolated `b0be6f9907f0b5fb5df459be8d3e845af327e336` checkout, tree `e92484b315264869a76724154b1eebd73ef811bd`, with no production worktree diff; its inventory retains the three changed test-file hashes.

These runs exercise actual local CLI, MCP, subprocess, HTTP, SQLite and artifact boundaries with deterministic fixtures. They do not establish authenticated installed vendor/model behavior, a live commercial evaluator response, power-loss durability, or a completed Symfony greeting workload. Native qualification remains the documented local operator run.

The earlier 175-test/63-subtest producer affected-family run predates the final transcript-prefix repair. The later 46-case producer run and 11-case transcript follow-up are recorded separately; they are not backdated to that earlier run. [Calibration](../telemetry-measurements.md) covers 21 bounded local trials at its own source checkpoint; it supplies measurements, not a performance SLO or universal latency guarantee.

