# PR6 IV01–IV04 provenance and coverage audit

The provenance, preserved-coverage, raw-report and original-calibration checks pass for product **e565863ae72180571678c8f6118ee6e00b129e2e**, tree **474de55c5947890ab4092daeca7ff39f9bbbc400**. This is an in-session read-only audit of the repair evidence, not the requested later fresh-session independent verification. It supports handing off the repaired candidate and its exact evidence. This track made no product or remote-reference changes.

Paths below are relative to `evidence/provenance/` unless a longer evidence path is given.

## Receipt and source identity

`input-verification.json` independently verifies the published receipt `3ac2342b6655f1a30652380c85ceb55262b7ad0c`: every published Git blob matches the retrieved files, both checksum inventories pass, and the ZIP CRC and all payloads pass. The 69,357,690-byte ZIP SHA-256 is `d161f2318727b8b52aa5dd78fa91d6d5ebc1aa4c12103ec8e385b328715833dc`. All **57,328 manifest paths** were checked against both archived payload bytes and the unchanged restoration; 2,485 unique payloads back those paths. The original report, reviewer scripts and raw evidence remain preserved. Its historical “no remote branch change” statement predates its subsequently authorized evidence-only publication; that publication is not product code.

`final-integration.json` independently verifies hosted integration commit `cf424865c3e86f93b98e015621158c995d5ae187`, with ordered parents `b0a00f1aed08d4ad8e066de8eb51f94e21da9f4a` and the final product commit, and the exact final product tree. Hosted checkout identity files omit parents because the checkout is shallow; the local Git object provides the independent parent proof.

`final-measurement.json` checks identical before/after source inventories, all 1,318 measured files against the current clean product and checker checkouts, and all 17 local command records and stdout/stderr hashes. The checker is a separate clean checkout of the final product commit. Canonical protocol bindings are:

| Input | SHA-256 |
|---|---|
| candidate_sha256 | `53c2873d9311c9492958e4beeec7da801cb9cfe4a76fea8d270dbba775acfc49` |
| checker_sha256 | `372a9d24352752610868ad367912380cb5e1e83ed8b77eea92b02434a1dfc5f5` |
| configuration_sha256 | `b1cc50c307f0f46c4817073e42f4dd397f662b938c2f0e0393c91f9e89e525c7` |
| criteria_sha256 | `75d44ff3e1c04ad0a82dcdeb3ebd08c2904cc654dd4e4e8b820a9e6dbd5abbf7` |
| fixture_sha256 | `d2e6b553a9b8995ab47097a07a97867504dfa06a1df631e6a479cc8056f5702d` |
| owner_basis_sha256 | `41545d3fab334e432f58f1f1b1367c70bed29bf2c39c1280c3eacd9cbdc783ad` |

Hosted configuration SHA-256 is `619a3034290275e054219243c25c848729783c37ea2a4dc92740593b58f3d6bd`; its environment-specific configuration is retained separately. The other five canonical bindings match local execution. These manifest/content hashes are distinct from ZIP hashes, Git tree IDs and the SHA-256 of pretty-printed JSON files.

## Coverage and controls

`final-local-coverage.json` and `final-hosted-coverage.json` independently reconcile exact JUnit class/function identities, including every parameterized case. Local Python 3.12 and hosted Python 3.11 and 3.12 each report **1,127 passing primary cases, 164 passing subtest events, zero failures/errors/skips**. JUnit's reported 1,291 includes the subtest events. All **89 original selectors**, all **165 frozen reviewed selectors**, and all **46 explicitly added selectors** pass in each execution. There are **211 distinct declared selectors**; overlapping requirement memberships must not be summed.

`final-mapping-validation.json` independently compares the source AST against ef75675: exactly 46 test functions were added and none removed. `added-selector-rationale.json` explains each added requirement mapping; `final-requirement-selectors.json` is the combined mapping. Counts describe execution and are not closure targets.

| Requirement | Original | Frozen | Added | Combined | Local/hosted |
|---|---:|---:|---:|---:|---|
| H1 | 5 | 19 | 0 | 19 | pass |
| H2 | 6 | 24 | 11 | 35 | pass |
| H3 | 6 | 37 | 25 | 62 | pass |
| H4 | 8 | 24 | 3 | 27 | pass |
| H5 | 7 | 27 | 19 | 46 | pass |
| H6 | 8 | 13 | 0 | 13 | pass |
| H7 | 10 | 43 | 17 | 60 | pass |
| H8 | 5 | 39 | 27 | 66 | pass |
| B1 | 5 | 13 | 0 | 13 | pass |
| B2 | 9 | 29 | 0 | 29 | pass |
| B3 | 9 | 9 | 0 | 9 | pass |
| B4 | 5 | 73 | 17 | 90 | pass |
| B5 | 6 | 40 | 31 | 71 | pass |
| A1 | 5 | 17 | 7 | 24 | pass |
| A2 | 6 | 66 | 46 | 112 | pass |

The added primary cases total 318, explaining the preserved 809-case baseline becoming 1,127:

| Group | New selectors / cases | Sustained control groups |
|---|---:|---|
| IV01 | 19 / 78 | Current/historical nested records on both stores; denied sessions and selected records; provider, capability and descriptor bindings; invocation and measurement scopes; original executor and operation-key conflicts; opaque payload/internal-history preservation, permission restoration, no repeated effects; activated traversal/provider faults and unrelated errors. |
| IV02 | 11 / 57 | Three-peer callback order permutations, Codex/Grok native-tail permutations, completion before submission/callback return, unmatched and anonymous activity, durable pending-effect vetoes, fabricated idle and completion-order rejection; activated per-turn-idle fault, valid completion and unrelated-error controls. |
| IV03 | 14 / 163 | Requested/completed callback identities, independent native and durable bindings, malformed/missing values, nested session and lifecycle actor contradictions, duplicate/unrelated-history controls, two sustained join faults and unrelated checker failure. |
| IV04 | 2 / 20 | Rewritten/plain assertions across nine source layouts, unrelated adjacent raises, legitimate assertions and missing instruction-position rejection. Existing 18 attribution cases are additional preserved cases, not part of these 20 additions. |

`final-source-delta.json` records all 165 frozen function bodies. Exactly one changed: the existing successful-transport test adds a terminal `tool_completed` positive-control event and shifts following sequence numbers; its missing-completion and protocol-violation rejection assertions remain. `final-existing-test-support-changes.diff` separately discloses shared helper and fixture adaptations: completed callbacks plus bound native start/end evidence for positive fixtures, explicit safe retry of read-snapshot conflicts, the pending-turn representation assertion, and held native tails in the subprocess fixture. Helpers are not silently treated as unchanged merely because selectors retain their names.

The existing F02/F03 callback-authority/recovery, F04 cache-currentness, F05 assessment-applicability, F06 Claude-overlap, F09 attempt/uncertainty and F10 timing coverage remains in the full passing execution. These are preservation checks, not a wholesale new independent reconciliation. Composition and policy-selection interfaces remain available; new projection evaluates typed application records while preserving arbitrary payloads and trusted internal data. No Jev policy or mandatory vendor-acceptance prerequisite was added by this audit.

## Protocol and source-fault audit

`final-local-protocol/` and `final-hosted-protocol/` independently construct the required 46-case inventory, check all input bindings against clean final source, and recompute reports from raw observations with the explicitly selected final checker. Both give **46/46 passed**, and both saved reports equal their recomputed reports exactly. The inventory is 3 startup, 6 direct exchanges, 3 busy deliveries, 6 unfinished-work exchanges, 9 work batches, 3 completion, 15 lifecycle and 1 native-tool audit cases. Synthetic predicate controls above do not substitute for this active composed execution.

`final-local-calibrations.json` independently inspects all **12 original activated source faults**: **36 passing controls, 19 intended assertion failures, 17 unaffected mutant passes, zero errors/skips**. It checks complete measured source copies, unique mutation anchors, actual patches, control/mutant identities, trusted observer copies and command/log consistency. `final-attribution-positions.json` additionally matches all 19 captured `RAISE_VARARGS` instruction spans to their actual target AST assertions. The observer SHA-256 is `8107f8fb650517955e46d8790e3bb4ffc2020243e32d26c67f346d60a3bb1779`. The original twelve detections were valid before IV04 and remain valid; the repair closes a separate false-attribution construction.

Hosted calibration XML and mutation/observer records independently yield the same 12/36/19/17 result in `evidence/ci/40132785317/calibration-audit.json`. The workflow omits hosted measured-source snapshots and work mutation patches; local final evidence retains the full copies and patches. The hosted raw artifact scope is stated, not expanded by assuming omitted files were retained.

## Fresh hosted execution and retained history

Fresh run: https://github.com/xormania/xorchestrate/actions/runs/40132785317 . `final-hosted-artifacts.json` independently matches the four active-scope artifact archives to retained GitHub API digests, checks ZIP CRCs and every extracted member byte. Both application jobs, static checks, recovered toolkit and live tmux jobs succeeded in the captured job snapshot. The imported-runtime job is historical scope and receives no active-application credit; its final result is recorded separately in the CI evidence.

All five historical raw collections were recomputed with the final checker without changing their recorded bindings or raw observations. The actual rechecker identity is recorded separately:

| Retained raw source | Recomputed result |
|---|---|
| ef75675 local, ca3f79 hosted, prior independent-review ef75675 run | 46 passed each |
| Original b0a00f1, superseded d4bd829 | 44 passed, Codex/Grok completion failed; overall incomplete |

`historical-report-differences.json` explains non-equality with old saved reports: the three passing collections differ only in normalized evidence sequence numbers after terminal callback observations were retained; the two older collections also expose the already established missing-completion failures. These are fresh **recomputations of retained raw data**, not fresh product executions or final-candidate credit.

## Replay and limits

Each final audit has a neighboring `*-command/command.json` with argv, timestamps, exit status and stdout/stderr hashes. Re-run the scripts with restored input paths and fresh output directories; their CLI arguments explicitly select the source checkout, raw run and mapping. Keep the exact source commit clean. The top-level repair handoff supplies the execution, reconstruction and publication steps.

Preparation controls and superseded maps remain separately named. One early source-delta audit could not resolve inherited unittest selectors; its failure and original script are retained. The corrected resolver follows local base classes, and the final source audit passes. No failed preparation attempt earns final-candidate credit.

This audit finds no provenance or declared-coverage blocker to the repaired-candidate handoff. It does not claim authenticated vendor execution, which remains the owner's planned local work, and does not replace the later independent verification session. No merge or release recommendation is implied by passing counts alone.
