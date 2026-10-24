# Xorchestrate PR #6 — independent verification

Prepared for xor on 2026-10-24 (America/Port-au-Prince). Raw execution timestamps use UTC.

**Recommend holding integration of `ef7567516520543970af55b6f19534cb66eda357` for four demonstrated defects.** The original repairs substantially improve the product, and their reported deterministic results reproduce. Further controlled challenges found a remaining inspection disclosure, false Codex completion, and two verifier weaknesses. These counterexamples survive the passing full suite and protocol inventory.

The review executed the assignment. No product repair, merge, release, remote branch change, PR comment, or Jev experiment work was performed. Authenticated vendor acceptance remains xor's planned local work; its absence is not the reason for this hold.

## Subject, integrity and evidence selection

| Item | Independently verified identity |
|---|---|
| PR | https://github.com/xormania/xorchestrate/pull/6; open, draft, unmerged; head unchanged at initial and final refresh |
| Handoff | `ad05291f8e59fbc2bde23c50375b79b1cb1e559b`; used only to retrieve assignment/evidence |
| Product/checker | `ef7567516520543970af55b6f19534cb66eda357` |
| Product tree | `a0813aae760e6e5bb6245693bd92b7e7c321348b`; clean tracked working state |
| Original base | `b0a00f1aed08d4ad8e066de8eb51f94e21da9f4a` |
| First repair | `d4bd82946bfb6e78f4ba533d7ed2e01c37ea601e`; superseded |
| CI integration | `ca3f79ee6d04c55223fa0c907eeb26f9340d6439`; exact candidate tree, parents base then candidate |
| Evidence publication | `663f0634f59c5d6f6c54910c8f995a6af9e6a490`; sole parent candidate; exactly seven documented evidence additions, byte-identical to package copies |
| Reconstructed repair ZIP | SHA-256 `27db9e669dc136c866a37b2f4164471f8ce2bd581532225217711ac062df69fe` |
| Nested original-review ZIP | SHA-256 `67218f0e6a1e180d3368ea25ed8f766a03dc921e96b085b9ee287c1c82b40086` |

The four parts were concatenated in README order after validating `SHA256SUMS`; ZIP integrity passed. Independent hashing verified all 29 outer entries and 1,831 original-review entries. After inspecting the helpers, extraction/restoration created 135,075 exact Git-backed inputs and verified 3,367 included measurement files; verification mode then rechecked all of them. Package identity is not behavioral closure.

The package's historical `source.bundle` advertises d4 and lacks the final candidate/publication, despite its report/replay text saying no bundle is included. The handoff warning is accurate. This is a packaging/documentation issue, not an additional product defect; the review used freshly fetched exact Git objects. The new review evidence includes a separately named `verified-source.bundle` containing the candidate, publication and CI integration with complete history. The historical bundle is preserved unchanged inside the original repair ZIP.

Only `input/repair-evidence/final` receives retained final-run credit. Historical `exact-ef75675` paths map there; old d4 `final` paths map to `superseded-d4bd829`. The collided-output attempt receives no final credit. Fresh reviewer results live separately under `reviewer/`.

## Closure verdicts

“Closed” below means the original counterexample and the stated deterministic variants were exercised without a residual defect. It does not assert universal behavior or installed-vendor acceptance. Original vulnerability probes sometimes exit zero when a defect is demonstrated; their observations, controls and actual assertions determine these verdicts.

| Finding | Verdict | Evidence and limits |
|---|---|---|
| F01 inspection scope | **open** | Original cross-work leakage is repaired and 18 scope cases pass. IV01 still discloses a same-work session explicitly denied inspection through historical `get_work`. Both stores and direct/current/historical projection controls distinguish the defect. |
| F02 callback authority | **closed** | Base callback gains replacement authority; candidate rejects it. Two successive replacements, missing/foreign identities, and a valid newest-lease callback preserve authority correctly on both stores. See delivery track raw probes and 48-test focused run. |
| F03 post-effect recovery | **closed** | Base wrongly fails an applied effect and admits another; candidate retains unknown and fences. Multiple replacements preserve persisted host completion; invalid reconciliation rejects, valid reconciliation permits replay without repeating the effect. Pre-effect stale calls execute zero effects. |
| F04 cached native calls | **closed** | Three adapters and external bridges revisit current authority/basis. Owner revision, revocation/restoration, deleted native-call/operation rows and material assessment changes reject stale replay without duplicate effects. Actual native/vendor protocols remain pending. |
| F05 assessment applicability | **closed** | Material context/evidence/actor/contribution and JSON type changes invalidate stale assessments, including evaluation/acceptance races. Title and configured unconsumed-field controls remain valid. Actual CLI provider/evaluator composition was exercised. |
| F06 original Claude overlap | **closed** | Original base reports idle with B held; candidate remains active. Fresh AB/BA orders, duplicate/unmatched/anonymous stops and uncertain callbacks are covered. This scoped Claude closure does not close IV02's Codex sibling. |
| F07 calibration attribution | **open** | Original helper/import/timeout/source-marker defects reject correctly; the 12 actual original source faults retain valid credit. IV04 shows a same-line explicit raise still receives false assertion credit in both calibrators. |
| F08 normalization and joins | **open** | Original argument mismatch and many null/type/duplicate/effect faults reject. IV03 changes one completed callback identity field and retains 46/46, while production receipt evaluation rejects the same records. |
| F09 attempts/uncertainty | **closed** | Original later-unknown false pass rejects on candidate. Latest failed/running/queued/unknown, invalid order/recipient, preserved historical uncertainty and harmless record-reordering controls pass. |
| F10 unfinished-work timing | **closed** | Original late-reply case rejects; equal/missing/late timestamp variants reject and timezone-equivalent valid timing passes. Busy-tool coverage remains submission during hold followed by full exchange. |
| Later aggregate-completion repair | **open** | Original partial-Claude normalization defect is repaired; missing/later active/unknown observations reject. IV02 produces real Codex idle/completion credit while an older callback and durable operation remain pending. |

Machine-readable closure and capability assessments are in `closure-verdicts.json` and `capability-assessment.json`. Each track report identifies the original repro, repair, regression assertion, deliberately faulty subject, passing controls, fresh results and limitations.

## Material findings, ordered by consequence

### IV01 — Historical work inspection leaks an explicitly denied session (P1, product; F01)

**Expected:** current inspection policy applies to nested session records in historical results, while trusted internal evidence remains intact.

**Reproduction:** create public and private actor sessions on the same work; put a synthetic canary in the private session specification. Through the public native session, record successful `get_delivery` then `get_work` reads while access is unrestricted. Restrict the caller to inspection of that work and explicitly deny `native_observations`/`get_delivery` where `session_actor == "private"`. Query history through the public session.

**Actual:** the historical `get_work` result still contains the private session/canary in native-call and completed-observation results. Both MemoryStore and SQLiteStore reproduce it. Direct private-session inspection returns `denied`; current delivery's session projection excludes it; historical `get_delivery` excludes it, retains public data and marks redaction. Those controls establish an actual same-action actor denial, not an assumed cross-action policy.

`xorchestrate/delivery.py:30` omits `get_work` from recursive aggregate projection; lines 139–156 therefore leave that nested result untouched. `application.py:373` returns the nested work sessions. The existing historical-work regression denies an entirely different work, so it does not cover this same-work nested scope.

Replay: `python reviewer/delivery_authority/probe_historical_work.py candidate /fresh/historical.json`. Exit zero means the witness and controls reproduced the defect. Primary raw evidence: `reviewer/delivery_authority/historical_work.json`; independently repeated by the lead in `reviewer/root/historical-scope.json` with command/log hashes. The separate routing-catalog canary investigation is retained but is not counted as another acceptance defect because its cross-action policy interpretation is less decisive.

### IV02 — Newer Codex turn completion hides an older pending callback (P2, product and checker; later completion repair)

**Expected:** one completed turn cannot establish global idle while another actual callback remains pending.

**Reproduction:** through the actual application, SQLite, provider port and Codex subprocess fixture, start and hold callback A, then start and hold B. Release B first. Capture product status, durable operations, raw observations, normalization and the completion checker before release, after B, and after both.

**Actual after B:** callback A remains blocked; its native application request lacks a completion and its durable `invoke` operation is `pending`. Nevertheless adapter `pending` is empty, activity is `idle`, and `completion-codex` passes. Releasing A completes its operation and produces legitimate final idle. This is an executed composed schedule, not fabricated pending metadata.

`xorchestrate/adapters/sessions.py:564` overwrites the single `pending['turn']` slot with B. Lines 569–574 clear it and emit idle when B completes, without accounting for A. `scripts/verification/checker.py:317` trusts the latest idle after requested callbacks without reconciling their terminal outcomes. The normalizer preserves the faulty emitter's explicit state: producer and checker share an unsupported aggregate assumption.

Codex AB is a valid control; both Claude and Grok AB/BA schedules remain active/incomplete at the partial stage. All six schedules start active/incomplete and finish idle/passed. The original Claude probe and saved partial-Claude challenge reject correctly on the repaired candidate. All 24 existing overlap/completion tests pass and omit this reverse Codex ordering.

Replay from candidate with `PYTHONPATH` set to that checkout: `python /path/to/reviewer/completion/reverse_overlap.py --peers codex --out /fresh/codex`. Exit **1 is expected** because the script completes the controls and reports the defect. Raw witness: `reviewer/completion/reverse-run-2/codex-BA/after-first-stop.json`; lead repeated all six schedules in `reviewer/root/completion-recheck-network/`. Installed-vendor reachability remains unmeasured; the shipped peer demonstrably exercises this adapter schedule.

### IV03 — Saved-run checker borrows a valid native identity for a contradictory callback (P2, verifier; F08)

**Expected:** independently observed native call, executed callback and persisted effect agree on qualified identity. Explicit contradictions reject.

**Reproduction:** use the final retained passing raw run. Change only completed `send_message` observation `2c8e1b9ca2161776f6ea6e04b17f850917c4a03b9f70b3489a6d46e9c8588110`'s `native_id` to `unrelated-native`, retaining call ID, arguments, requested callback, independent native observation and persisted message. Separate variants change only incarnation to a retired epoch or native ID to null.

**Actual:** all 46 cases pass, including `busy-grok-claude` and native-call audit. The production `evaluate_exchange` rejects the same raw records with `native_callback_missing_or_misbound`; the unmodified control gives 46/46 and a verified production receipt. A wrong independent native identity is correctly rejected by the checker, isolating its asymmetry.

The normalizer preserves the contradictory callback fields. `scripts/verification/checker.py:104` joins on peer/tool/arguments/call ID and validates the independent native event, but omits the callback's own qualified identity comparison. This is an evidence-substitution counterexample; no live wrong-principal dispatch is claimed.

Replay: `python reviewer/checker/minimal_identity_witness.py --candidate candidate --baseline input/repair-evidence/final/protocol --out /fresh/identity`. Raw inputs, one-field mutations, checker reports and production receipts are in `reviewer/checker/minimal-identity/`; lead independently repeated them in `reviewer/root/minimal-identity/`.

### IV04 — Same-line explicit raise earns intended-assertion credit (P2, narrow verifier; F07)

**Expected:** the intended assertion must execute and fail. An unrelated `AssertionError` carrying its marker is insufficient.

**Reproduction:** a reviewer fixture keeps `compute()` returning 1 in both control and faulty subject. Mutate only an unrelated helper from `return None` to `return AssertionError('EXPECTED_VIOLATION')`. The test passes `assert observed == 1, 'EXPECTED_VIOLATION'; raise trouble` on one source line. All executed intended assertions are true; the explicit raise supplies the failure.

**Actual:** real pytest subprocesses emit `target_assertion:true`; both unmodified calibrators classify the fault as `detected`. Control exits 0 and mutant 1. Moving the raise to the next line yields `unrelated_failure`; changing compute to return 2 produces a genuine assertion failure and correctly yields `detected`. No XML or observer attribution was forged.

`scripts/verification/pytest_calibration.py:34` matches an AST Assert's line range to the terminal traceback line. Code-object matching excludes helper frames but cannot distinguish statements sharing the line. Both classifiers trust that attribution.

Replay: `python reviewer/checker/challenge_attribution-v2.py --candidate candidate --out /fresh/attribution`. Raw XML, observer copies, applied mutations, source snapshots and controls are in `reviewer/checker/attribution-challenge-v2/`; lead repetition is `reviewer/root/attribution-recheck-v2/`. V1 is retained separately and is not the decisive witness because its wrong-compute guard was a semantic confound. No existing candidate regression with this layout was identified. The twelve original fault detections were individually valid; this narrow finding limits the reusable verifier's general guarantee.

## Executed verification and coverage

| Check | Newly executed result | Evidence |
|---|---|---|
| Full active application, Python 3.12.14 | **809 passed +164 subtests**, zero skips/failures/errors | `reviewer/execution/final-with-tmux/application.xml`, command/stdout |
| Nine focused repair modules | **211 passed**, zero skips/failures | `reviewer/execution/network-permitted/repairs.xml` |
| Exact original/final selector reconciliation | **89/89 original; 165/165 final**, all resolve to actual passing identities | `reviewer/provenance_coverage/selector-reconciliation.json` |
| Fresh composed protocol and raw recomputation | **46/46**, report exactly equal, same declared inventory | `reviewer/execution/network-permitted/protocol/` and recomputed report |
| Seven native/delivery + five work source faults | **12 detected**, 36 passing control cases, 19 intended assertion failures, 17 unaffected mutant passes; no errors/skips | `reviewer/execution/calibration-independent-audit.json`, raw XML/observer/source changes |
| Static checks | Ruff, security-lint coverage, compile and 13 optional-toolchain checks pass; boundary assertions pass in full suite | execution command ledger |
| Live tmux protocol fixture | passed; cleanup verified, no forced termination, zero remaining processes | `reviewer/execution/final-with-tmux/tmux-live-fixture/` |

The XML's testsuite `tests=973` includes 164 subtest events; it contains 809 primary testcase elements. It is not a 973-primary-test run. Original 598→809 primary inventory is exactly 211 new cases across nine repair modules. Original selectors are a subset of the final mapping after normalizing the documented path prefix; every exact matching parameterized case/status is retained. Focused/component totals overlap and are not added together. The cache/assessment track additionally executed 23 reviewer variants and five explicitly observed known-bad witnesses; other tracks retain their additional probes separately.

The independent 46-case inventory comprises 3 startup/context markers, 6 directed exchanges, 3 held-tool exchanges, 6 unfinished-work exchanges, 9 work batches, 3 completion cases, 15 lifecycle cases and 1 all-native-calls audit. It was constructed independently of the candidate inventory generator and reconciled against requested/executed IDs and raw observations.

Rechecked retained local and hosted final raw reports are exactly equal to their saved 46/46 reports. Rechecking original b0 and superseded d4 raw with the corrected checker gives **44 passed and two failed completion cases, overall incomplete**: Codex/Grok lack aggregate activity. Claude has a genuine separate final activity record. Missing old observations were not manufactured or given final-run credit. Saved START-HERE audit also reproduced the exact source/control/observer and 12-fault assertions.

Fresh GitHub reads confirm all six jobs of run `40066679349` succeeded. Retrieved Python 3.11/3.12 and tmux logs match retained bytes; each Python suite reports 809+164, while hosted tmux reports 22 tests plus its fixture. Python 3.11 calibration/protocol workflow steps were intentionally skipped; those ran in 3.12. Historical 285-toolkit/825-runtime passes remain historical source evidence, not new active-application proof; those entire historical suites were not rerun in this review.

## Preserved capabilities and architecture

All mapped selectors pass; the assessments below account for the new counterevidence. Counts are original/final distinct mapped selectors per requirement, with overlap between requirements.

| ID | Selectors | Independent preservation assessment |
|---|---:|---|
| H1 | 5/19 | Persistent contexts, private markers and native identities survive active deterministic exchanges/resume. Installed vendors pending. |
| H2 | 6/24 | Busy and unfinished-work delivery/concurrent progress survive; global activity is incomplete under IV02. |
| H3 | 6/37 | Durable message/attempt/incarnation/reply/receipt correlation survives product controls. IV02/IV03 limit state and independent-proof claims. |
| H4 | 8/24 | Ownership, detach/resume/reconnect and immutable old authority survive; F02/F03 multi-replacement probes pass. Native endpoint compatibility pending. |
| H5 | 7/27 | Provider/native-tool/ownership choices remain independent. Explicit policy confinement is incomplete under IV01. |
| H6 | 8/13 | Durable routing, atomic enqueue/attempt/event behavior and replaceable selection/scheduling/retry/receipt/storage remain exercised. |
| H7 | 10/43 | Uncertainty, stale connection, replay and recovery fences pass the expanded probes; IV02 still misstates current activity. |
| H8 | 5/39 | Actual composed 46-case fixture is preserved. IV02–IV04 limit its oracle; authenticated native acceptance remains separate. |
| B1 | 5/13 | Owner outcomes survive narrowing/cancellation; explicit revisions govern replay and acceptance. |
| B2 | 9/29 | Current mechanical/assessment evidence and material consumed-input applicability survive expanded F04/F05 challenges. |
| B3 | 9/9 | Requested/resolved/observed settings and configured fallback remain distinct; effective vendor settings need local measurement. |
| B4 | 5/73 | Original source faults and new retained regressions have real assertion credit; general attribution remains incomplete under IV04. |
| B5 | 6/40 | Exact source/criteria/fixture and requested/executed coverage remain independently recoverable. IV02–IV04 prevent full sufficiency claims. |
| A1 | 5/17 | Acceptance, evaluation, authorization and coordination remain configurable responsibilities; no new mandatory roles, fixed evaluator threshold or universal approval found. |
| A2 | 6/66 | Delivery/work acceptance bind evidence at applicable boundaries; IV01/IV03 expose remaining scope/join holes. |

Ports and composition retain replaceable providers, native protocols, transports, Memory/SQLite storage, routing, scheduling, retry, ownership, receipt, evaluation and acceptance policies. Trusted module factories remain available; simple messaging does not require an assessment hierarchy. Currentness projections deliberately allow unconsumed changes, and mechanical-only acceptance remains available. No token interruption, exactly-once, fixed routing policy or universal approval requirement was added by this review.

## Evidence limits, failed attempts and replay

This environment initially denied AF_INET in default-sandbox subprocesses, yielding retained application/protocol/calibration setup failures. Network-permitted commands exercised the same code successfully. AF_UNIX works here, unlike the old review host. Isolated tmux 3.5a was downloaded, hash-checked and extracted into reviewer tooling; its live fixture and the final full suite eliminate the eight initial tmux skips. All failed attempts remain retained and receive no successful-run credit.

Other retained probe failures include unchanged original scripts asserting vulnerable outcomes, evolved fixture text substitutions, missing Claude error-envelope capture, an overly early Grok final-state measurement, and a work-bound cache probe whose own send evidence invalidated its positive-control assessment. The corrected fixtures alter reviewer schedules/observations only; candidate files remain unchanged. Track reports explain each adaptation and identify the authoritative completed run. V1/V2 scripts remain separately preserved. Root command records identify when exit 1 means the expected demonstrated defect rather than an environment error.

Material remaining uncertainty is installed-vendor reachability, configuration fidelity and lifecycle/timing behavior. Resolve it with xor's planned authenticated local procedure, including three persistent contexts, six directed exchanges, three busy holds, six unfinished-work exchanges, nine work batches, three native completion cases, fifteen lifecycle cases, every native-call join, and the newly identified reversed Codex schedule. Diagnostic native collection may proceed, but a green current checker cannot dispose of these counterexamples. No new vendor prerequisite is imposed merely by pending local acceptance.

The evidence ZIP includes this report, closure/capability JSON, the unchanged repair ZIP, handoff files, exact source bundle, reviewer scripts, fixtures, raw observations/databases, full logs/JUnit, applied mutations, controls, manifests and failed attempts. Repeated file bytes are stored once by SHA-256 and reconstructed with the included standard-library `restore_review.py`. The manifest records every retained path/hash/mode and all exclusions; discarded items are caches, Git administration, special runtime endpoints and explicitly recoverable clean base checkouts. `REPLAY.md` gives complete restoration, exact checkout, full-suite/calibration/protocol commands and all four minimal witnesses. Package self-checks are recorded separately in `package-validation.json`.

**Integration recommendation:** hold this candidate until the explicitly denied historical session is no longer disclosed, reverse-order Codex completion retains outstanding work, callback identity contradictions reject, and assertion attribution distinguishes the actual failing statement. Repairs should retain the stated positive/negative controls and rerun affected checks plus the exact inventory. No repair was implemented here; choosing its design and deciding disposition remains xor's responsibility.
