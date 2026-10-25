# Xorchestrate independent adversarial verification

Prepared for **xor**, 2026-10-23 (America/Port-au-Prince). Execution timestamps in the evidence use UTC, 2026-10-24. This report reviews the submitted implementation; it does not execute the brief's implementation prompt.

## Executive verdict

**The reported baseline results are reproducible, but the claim that all agreed guarantees are implemented does not survive adversarial verification.** Ten distinct defects were reproduced: six in application/adapter behavior and four in verification. Three product findings have high practical impact under their stated configurations: cross-work disclosure, stale callback reassignment to a replacement principal, and false failure after an already-applied bridge effect.

| Dimension | Verdict |
|---|---|
| Implementation | Substantial active hexagonal implementation exists. Complete H/B/A fulfillment is falsified by the findings below. |
| Deterministic verification | Submitted checks reproduce; additional controlled cases fail the intended guarantees. A green existing suite is not sufficient acceptance. |
| Readiness for local native acceptance | The workflow is runnable in structure and useful for diagnosis. It is **not ready to establish acceptance** with the current implementation and checker defects unresolved. |
| Full behavioral acceptance | **Unsupported.** Authenticated native coverage remains pending, and independent review now has confirmed findings. |

No candidate repair, merge, release, or authenticated vendor session was performed. The submitted checkout remains clean at its original commit/tree. All mutations were confined to disposable fixtures, copies, or evidence inputs. An exit-zero reproduction script means it successfully demonstrated its expected control and defect; it does not mean the product passed.

## Measured candidate and evidence integrity

| Input | Independently established identity |
|---|---|
| Repository / PR | [xormania/xorchestrate, draft PR #5](https://github.com/xormania/xorchestrate/pull/5), open and unmerged |
| Base | `30254fd0f184b7e31aa7e0896347429004933ed4`, `integration/hexagonal` |
| Candidate | `b0a00f1aed08d4ad8e066de8eb51f94e21da9f4a`, `repair/behavioral-preservation` |
| Candidate tree | `0f4034870f40fb5a1d022c9f35ad0b866236455f` |
| Candidate content hash | `ccba046c2b8caa0a4b94bcaa9be9e4229ce7c3e837db911720cf1ecf2e44aa09` |
| Submitted evidence publication | `57f01ab3c18ecfa0e062ab3e2a1fc567068dff88`, direct child adding only 11 evidence files |
| Owner brief SHA-256 | `41545d3fab334e432f58f1f1b1367c70bed29bf2c39c1280c3eacd9cbdc783ad` |
| Checker SHA-256 | `f626732773942f157da097119d424c7af18a7d7d54370fad246a5d0bb0396172` |
| Criteria SHA-256 | `75d44ff3e1c04ad0a82dcdeb3ebd08c2904cc654dd4e4e8b820a9e6dbd5abbf7` |
| Fixture SHA-256 | `d2e6b553a9b8995ab47097a07a97867504dfa06a1df631e6a479cc8056f5702d` |
| Lead rerun configuration SHA-256 | `8d482e1870e9e682da94bd1845afd604f2c8855c30d0a024bdc5e616a1211cb8` |
| Submitted configuration SHA-256 | `afae41b5b849ead3601aeb86662ec8cd78113f809eab172c624e7f64d697ece1` |

The configuration hashes differ because the rerun uses its own executable, candidate and output paths; both exact configurations are retained. This is a new measurement, not substituted evidence for the original run. The external attachment matches the retained owner brief byte-for-byte. No subsequent explicit owner instruction removed or weakened any H/B/A requirement. The PR discussion returned no comments; the user's supplied completion summary was treated as claims.

Provenance checks verified all **1,297 final candidate inputs**, all **1,079 indexed evidence files**, and an empty measured working patch. The final source is recoverable from the pinned commit; there are no missing measured untracked inputs. Both retained raw protocol derivatives recompute to their identical 46-case reports. All 89 mapped assertion selectors match passing retained JUnit cases. Seventeen earlier component snapshots reconstruct; the disclosed twelve-hash discrepancy in one superseded transport snapshot is excluded from final acceptance evidence.

Fresh GitHub queries confirmed all six jobs of [CI run 40051248630](https://github.com/xormania/xorchestrate/actions/runs/40051248630) succeeded. Freshly retrieved Python 3.11/3.12 logs each report 598 tests and 164 subtests passed. The independently fetched synthetic merge `cdd84694c50b1c639caafae82067e9d0688f8b80` has the candidate's exact tree and expected parents. This verifies integration identity rather than relying on separate branch passes.

These checks support recoverability and reported-run identity. They do not establish that an oracle is correct or that arbitrary fabricated raw traces would be honest. See [provenance report](../review/tracks/evidence_provenance/REPORT.md) and the package's `review/identity/` and `review/baseline/protocol/` records.

## Requirement matrix

“Bounded pass” means the stated deterministic cases survived; it is not proof of all possible behavior. Findings are numbered below. Native testing was not performed by this review.

| ID | Active implementation and rerun evidence | Independent result | Remaining gap |
|---|---|---|---|
| H1 | Codex, Claude and Grok adapters; startup/private-marker exchanges, resume and identity tests; composed protocol run | Bounded deterministic pass | Installed vendor context retention and native identities across lifecycle changes remain pending. |
| H2 | Concurrent JSON-RPC/MCP, delayed callbacks after acknowledgement, held-tool delivery, unrelated sessions, unfinished-work exchanges | Partial; **F06**, **F10** | Claude activity can become idle with work outstanding; saved-run checker accepts late work replies. Native timing remains pending. |
| H3 | Durable messages/attempts/native observations; production complete-exchange receipt rejects queue/RPC-only and misbound evidence | Partial; **F02**, **F06**, **F08** | Bridge callback attribution and independent audit are incomplete; current conversation activity is not always accurate. |
| H4 | Owned processes, external bridge, detach/reconnect/resume, receipt/source/destination protections | Partial; **F02**, **F03** | Replacement during observed callback or applied effect breaks attribution/recovery. Native external-host lifecycle pending. |
| H5 | Replaceable MCP/CLI providers, separate native permission policies, allowed/denied controls and fallback authorization | Partial; **F01**, **F02** | Configured cross-work denial is bypassed; replacement can change callback principal. Real native permission enforcement pending. |
| H6 | Routing snapshot at enqueue; atomic message/delivery records; both stores; substitutable selection/scheduling/retry/receipt policies; eight-way dispatch control | Bounded pass for routing/substitution; linked recovery finding **F03** | Full routing-to-native recovery inherits bridge defect. No exhaustive crash-state proof. |
| H7 | Unknown-outcome records, replay ledgers, incarnation fencing; lost-response, duplicate, stale-frame and recovery controls | Falsified in additional cases: **F02–F04**, **F06**, **F09** | Old effects/callbacks and currentness-sensitive replay are not fenced throughout the composition. |
| H8 | Actual active application/SQLite/native protocol adapters; 46 composed cases, including 15 lifecycle cases | Meaningful coverage, insufficient oracle: **F07–F10** | Corrected/independently reviewed predicates and authenticated run required before acceptance. |
| B1 | Owner requirements separate from contributions; A+B survives narrowing/cancellation and spoofed result prose; explicit revision and remeasurement work | Core preservation passes; replay path fails **F04** | Adapter replay can still present superseded owner-basis acceptance as current. |
| B2 | Required mechanical measurements, candidate/provider/checker bindings, assessment/policy separation | Partial; **F04**, **F05** | Material evaluator-input changes and adapter caches permit obsolete acceptance claims. |
| B3 | Requested/resolved/observed settings; trusted precedence; explicit driver fallback; same-name configuration/resume tests | Bounded deterministic pass; full fidelity unverified | Observed native mismatches are retained but not automatically adjudicated. Local effective settings and allowed substitution need verification. |
| B4 | Both calibration suites; original seven delivery/native and five work faults | Original 12 detections survive; general guarantee fails **F07** | Runtime setup failures and unrelated exceptions can receive intended-detection credit. |
| B5 | External brief/criteria, exact candidate capture, separate checker selection, requested/executed coverage and raw recomputation | Identity/coverage controls pass; evidence sufficiency fails **F07–F10** | Independent checker false passes remain despite correct input hashes. |
| A1 | Acceptance/evaluation/authorization/routing responsibilities have replaceable boundaries; real composition substitutions exercised | Bounded pass | No unintended fixed role hierarchy, Jev threshold or universal approval workflow found. This does not excuse behavioral defects. |
| A2 | Delivery and work-acceptance records bind relevant identities at distinct boundaries | Partial; **F01–F05**, **F08–F09** | Access scope, callback lease, assessment input and replay/evidence associations are incomplete. |

Full assertion mappings remain in the submitted matrix; this table adds the independent counterevidence. The machine-readable review matrix and per-track reports accompany this report.

## Confirmed findings

All findings apply to the exact candidate above. **P1/high** means a practical authorization or recovery defect under the stated configuration; **P2/medium** means a material correctness or verification defect without a demonstrated broader exploit. Severity reflects demonstrated impact, not hypothetical maximum damage.

### F01 — Authorized delivery inspection discloses another denied work's data — P1

**Requirements/policy:** H5, A2; selected default-deny RuleGuardrail. **Precondition:** one runtime stores work with distinct access policies, and the caller may inspect one public message.

Create public/private work and place a synthetic canary in the private message and evidence. Permit the limited actor only public `get_work`/`get_delivery`. Direct private queries correctly return `denied`, and public `get_work` contains no canary. Public `get_delivery` nevertheless returns the private message and evidence. Reproduced on MemoryStore and SQLiteStore; the caller needs no private ID.

The selected message supplies the authorization context, but [`delivery.py:53`](https://github.com/xormania/xorchestrate/blob/b0a00f1aed08d4ad8e066de8eb51f94e21da9f4a/xorchestrate/delivery.py#L53) assembles global evidence/messages/sessions/observations and returns that internal snapshot publicly. Expected: scope or independently authorize returned records. No disclosure of real credentials is claimed.

**Evidence/replay:** `review/tracks/delivery_configuration/reproduce_cross_work_leak.py`, `cross-work-leak.json`; lead rerun `review/lead-reproductions/disclosure-command/` and `disclosure-results.json`. Track D1 supplies full policy, response and both denied controls.

### F02 — An already observed callback gains a replacement actor/work — P1

**Requirements/policy:** H4, H7, A2; explicit denial of old actor's `record_result`. **Precondition:** native observation precedes callback dispatch, with an authorized reconnect between them.

Observe the old native call and pause before dispatch. Reconnect to a new actor/work, then release it. The bridge dispatches the old call as the replacement actor, and composition injects the replacement work ID. The previously denied call now stores `OLD_WORK_CALLBACK` in the new work. Without replacement, the same call is denied and writes no evidence.

[`bridge.py:598`](https://github.com/xormania/xorchestrate/blob/b0a00f1aed08d4ad8e066de8eb51f94e21da9f4a/xorchestrate/adapters/bridge.py#L598) chooses current client context and overwrites the call's previously observed lease binding. Expected: preserve the original principal/work/incarnation or reject/retain uncertainty. This is an active application + bridge + SQLite reproduction using a controlled native port, not a forged receipt or unauthenticated remote attack.

**Evidence/replay:** `review/tracks/concurrency_lifecycle/composed_bridge_probe.py`, `composed-bridge-results.json`; lead `bridge_recheck.py`, `bridge-command/`, `bridge-results.json`. Track LC-01 includes raw observation and resulting evidence bindings.

### F03 — An applied bridge effect becomes definitely failed after reconnect — P1

**Requirements:** H7, H4. **Precondition:** native effect executes, but its response is held while reconnect replaces the client.

The native port increments a counter and pauses. Reconnect inherits the pending effect and correctly blocks a new send. Release the old response: the host retains its completed result, but the post-effect stale-connection check reports `outcome=not_started`. The application marks the operation failed, removes the effective recovery fence and admits a fresh send, incrementing the counter again.

The cause is [`bridge.py:591`](https://github.com/xormania/xorchestrate/blob/b0a00f1aed08d4ad8e066de8eb51f94e21da9f4a/xorchestrate/adapters/bridge.py#L591), its `_check` error classification, and the application's handling of that certainty. Expected: completed-but-unapplied or unresolved evidence remains recoverable; connection replacement cannot prove the issued effect never started. The no-replacement control returns completion correctly. This finding concerns false certainty and automatic fence removal, not exactly-once delivery.

**Evidence/replay:** `late_effect_probe.py`, `late-effect-results.json` in the lifecycle track; lead `lifecycle_recheck.py`, `lifecycle-command/`, `lifecycle-results.json`. The independent counter scenario separates this root cause from F02's callback reassignment.

### F04 — Built-in adapter caches replay superseded acceptance as current — P2

**Requirements:** B1, B2, H7, A2. **Precondition:** native `get_acceptance` succeeded, then the owner explicitly changed requirements and the same semantic native/MCP call is replayed.

All three real adapter paths reproduce the same result: revision-1 acceptance initially passes; owner adds B at revision 2; direct and fresh native reads correctly return false; duplicate native replay returns cached `accepted:true`, revision 1 and `current:true`. Direct `Application.invoke_native` replay rejects with `stale_native_result`.

Completed-cache returns in [`sessions.py:481`](https://github.com/xormania/xorchestrate/blob/b0a00f1aed08d4ad8e066de8eb51f94e21da9f4a/xorchestrate/adapters/sessions.py#L481), [`claude.py:205`](https://github.com/xormania/xorchestrate/blob/b0a00f1aed08d4ad8e066de8eb51f94e21da9f4a/xorchestrate/adapters/claude.py#L205) and [`grok.py:170`](https://github.com/xormania/xorchestrate/blob/b0a00f1aed08d4ad8e066de8eb51f94e21da9f4a/xorchestrate/adapters/grok.py#L170) bypass the application's currentness check. Codex replay uses a new transport request ID, distinguishing semantic replay from rereading one transport response.

**Evidence/replay:** `review/tracks/architecture_acceptance/protocol_replay/` includes instrumented peer patches, actual wire replies, SQLite, configuration, `probe.py`, controls and report. Lead rerun: `review/lead-reproductions/native-replay/` and `native-replay-command/`. Permission-revocation impact of these caches remains a source-derived hypothesis; this experiment establishes stale acceptance only.

### F05 — Material evaluator-input changes do not invalidate assessment-based acceptance — P2

**Requirements:** B2, A2. **Precondition:** configured acceptance requires a favorable assessment, alongside passing mechanical measurements.

A context-sensitive evaluator passes. Change material work context during assessment, after assessment, or after acceptance: the old favorable assessment still accepts, with no stale issue; an existing acceptance still appears current. A fresh assessment rejects. Reproduced on both stores and through actual CLIProvider/ToolEvaluator/SQLite composition. Adding adapter-origin diagnostic evidence produces the same failure. A title-only change is a benign control: both old and fresh assessments pass.

[`acceptance.py:202`](https://github.com/xormania/xorchestrate/blob/b0a00f1aed08d4ad8e066de8eb51f94e21da9f4a/xorchestrate/acceptance.py#L202) binds requirements/candidate/measurements/configuration but omits material work/evidence inputs consumed by the evaluator. The assessment retains `work_version`, yet consumption does not use an adequate material dependency identity. Expected: invalidate material consumed inputs; this does not require invalidating every unrelated record change.

**Evidence/replay:** `assessment_currentness.py` and `composed_assessment_currentness.py` in architecture track, with full JSON outputs; lead `assessment-command/`, `composed-assessment-command/` and composed state. Six store/schedule cases plus two actual composition material cases reproduce it.

### F06 — Claude can report idle while another callback remains held — P2

**Requirements:** H2, H7; related H3/H8 state evidence. **Precondition:** overlapping channel work with distinct prompt IDs in the supplied native protocol fixture.

Hold callbacks A and B; activity is active. Release A only and observe its Stop hook. B remains blocked, but `status.activity` becomes idle and an unqualified idle observation is emitted. After releasing B, idle is legitimate. [`claude.py:183`](https://github.com/xormania/xorchestrate/blob/b0a00f1aed08d4ad8e066de8eb51f94e21da9f4a/xorchestrate/adapters/claude.py#L183) treats any Stop as global idle without pending-turn fencing.

**Evidence/replay:** lifecycle `probes.py` / `probe-results.json`, plus lead `lifecycle-results.json`. Actual subprocesses/hooks/MCP are exercised; no direct `_hook` injection. This establishes adapter behavior under that schedule. Whether the installed Claude version produces that ordering remains a local native question; no native downstream acceptance bypass is claimed.

### F07 — Both calibrators credit unrelated exceptions as intended detection — P2

**Requirement:** B4. **Precondition:** a target assertion calls a helper, e.g. `assert compute() == 1, "EXPECTED_VIOLATION"`.

Passing control returns 1. Mutate the helper to raise an unrelated TimeoutError or import a missing dependency at runtime. The assertion never detects its intended condition, but both calibrators classify it as `detected` because the traceback prints the assertion source line containing the marker. The unaffected control passes. Collection errors, skips and escaped mutations remain separately rejected in controls.

Root causes: substring matching over complete JUnit traceback text in [`calibration.py:54`](https://github.com/xormania/xorchestrate/blob/b0a00f1aed08d4ad8e066de8eb51f94e21da9f4a/scripts/verification/calibration.py#L54) and [`calibrate_work_acceptance.py:89`](https://github.com/xormania/xorchestrate/blob/b0a00f1aed08d4ad8e066de8eb51f94e21da9f4a/tests/hexagonal/calibrate_work_acceptance.py#L89). Expected: attribution to the intended executed assertion, not marker presence in source text.

**Evidence/replay:** checker track `calibrator_adversary.py`, `calibrator-adversary/`, work-calibrator-specific short-traceback probe; lead `calibrator-command/`, `calibrator-faults/`. All original twelve mutation failures were separately inspected and were genuine intended AssertionErrors; their specific detection claim survives.

### F08 — Independent normalization erases native/application argument mismatch — P2

**Requirements:** B5, H8, H3, A2. Starting from the passing raw run, change one native `send_message.destination.actor` to a canary while retaining its actual application callback and persisted message. Expected: reject inconsistent request/effect evidence. Observed: all 46 cases, including the native audit, still pass.

[`normalize.py:41`](https://github.com/xormania/xorchestrate/blob/b0a00f1aed08d4ad8e066de8eb51f94e21da9f4a/scripts/verification/normalize.py#L41) overwrites callback arguments with native arguments before comparison. The product receipt policy rejects the same records with `independent_native_arguments_mismatch`, isolating the checker weakness. No actual live wrong dispatch is demonstrated.

### F09 — Independent checker masks a later unknown attempt with old success — P2

**Requirements:** B5, H8, H7, A2. Append a later-sequence unknown attempt for the same message/recipient to passing raw evidence. Expected: retain uncertainty or reject invalid evidence. Observed: 46/46 pass; the production receipt rejects the same snapshot.

Normalization discards attempt sequence/status in general attempt events; [`checker.py:119`](https://github.com/xormania/xorchestrate/blob/b0a00f1aed08d4ad8e066de8eb51f94e21da9f4a/scripts/verification/checker.py#L119) finds an older submitted event. This is an evidence-substitution experiment; it does not claim the normal retry policy produced that state.

### F10 — Saved-run pass does not enforce preserved unfinished-work reply timing — P2

**Requirements:** H2/H8 and B5's faithful expectations. Move the retained work-completion timestamp into the gap after challenge submission but before both Claude replies. The checker reports `reply_before_work_completed:false` for the affected cases, yet returns 46/46 passed.

[`checker.py:141`](https://github.com/xormania/xorchestrate/blob/b0a00f1aed08d4ad8e066de8eb51f94e21da9f4a/scripts/verification/checker.py#L141) requires submission before completion but only reports reply timing. The unmodified runner's gated schedule does satisfy the stronger timing in the control. The defect is the unqualified saved-run acceptance, not evidence that this candidate actually replied late. Busy-tool semantics remain unchanged: submission during hold followed by a complete exchange; token-generation interruption is not required.

**F08–F10 evidence/replay:** checker track `adversarial_checks.py` and `evidence-faults/`; lead independently repeated all three against its own raw passing run in `review/lead-reproductions/checker-faults/`. Raw mutations, reports, original values and expected diagnostics are retained. Omitted required case, wrong native identity and wrong memory controls reject correctly.

## Execution, coordination and limits

One lead owned candidate identity, the full matrix, severity, deduplication and integration conclusions. Six isolated tracks covered architecture/acceptance, concurrency/lifecycle, authorization inspection, delivery/configuration, checker/calibration and evidence provenance. All received the same candidate and external brief. Worktrees, stores, endpoints, event gates and output paths were isolated. Authorization source concerns were reconciled with executed delivery and adapter-replay evidence; they are not double-counted. F02/F03 share a bridge but have independent reproductions and distinct root causes. F04/F05 are distinct stale-acceptance mechanisms. Track severities were normalized to demonstrated impact by the lead.

| Independently executed check | Result | Evidence |
|---|---|---|
| Entire active `tests/hexagonal` suite, Python 3.12.14 | 588 passed, 10 skipped, 164 subtests passed | `review/baseline/application/`, `application.xml` |
| Separate checker checkout against submitted candidate | 46/46 protocol cases passed, exact candidate/checker hashes matched | `review/baseline/protocol-command/`, `protocol/` |
| Supplied source calibrations | 7/7 native/delivery + 5/5 work detected with passing controls; actual assertion failures inspected | checker track calibration records and `calibration-failure-audit.json` |
| Ruff, security lint coverage, optional-toolchain checks | Passed | `review/baseline/{ruff,lint-coverage,optional-toolchain}/` |
| Architecture/acceptance focused reruns | 145 tests + 40 subtests; later five replay controls also passed | architecture track recorded manifests/JUnit |
| Transport/session/lifecycle focused rerun | 74 passed | lifecycle `recorded-run/` |
| Delivery/configuration focused rerun | 147 passed + 21 subtests; 32 independent probes passed | delivery baseline/probe records |
| Checker/calibrator focused rerun | 49 passed, no skips | checker JUnit/log |
| All ten finding controls/reproductions | Reproduced by track and independently rerun by lead | `review/lead-reproductions/` |

Focused suite totals overlap the aggregate suite and are **not added together**. Bridge schedules were additionally repeated five times each with fresh state; retained results show the explicit gates, controls and cleanup. No broad randomized fuzzing claim is made. Nonces/UUIDs are retained in raw records; deterministic fault definitions and event schedules are the replay inputs, so a fuzz seed is not applicable.

Runtime: Linux x86-64/glibc 2.39, Python 3.12.14, pytest 9.1.1, jsonschema 4.26.0, Ruff 0.16.8. Each command record identifies cwd, timestamps, timeout, exit, output hashes and material overrides. Review-only peer instrumentation has retained patches. CLI/provider subprocesses, SQLite, FIFO bridges and TCP-loopback gateways are real; vendor inference is synthetic.

The original ten local skips were eight missing-Tmux cases and two host `/proc` namespace cases. A local Tmux 3.4 binary was obtained without changing the candidate, then those fixtures were attempted: 12 passed, 8 failed, 2 skipped. An independent socket/Tmux control established `AF_UNIX` creation is forbidden (`Operation not permitted`), explaining those eight environmental failures. They receive neither behavioral-pass nor product-defect credit. Setup logs, package hashes and controls are retained. Hosted coverage remains inspected CI evidence, not a local rerun. Python 3.11 and historical imported suites were inspected in hosted evidence; they were not independently rerun here or credited as active implementation proof.

## Expectations, source concerns and coverage still missing

The reviewed changes separating ACP submission/completion, enabling control during uncertain effects, retaining trusted callback identity, and rejecting stale connections agree with the brief. Bounded retries apply only to explicitly retry-safe pre-effect conflicts. No original required assertion was found intentionally removed to hide these defects. The gaps are predominantly composition boundaries and new oracle behavior: existing replay tests bypass adapter caches; bridge tests check stale error name but not post-effect certainty; assessment tests bind evaluator configuration but omit material input changes; normalized-event checker tests do not exercise raw normalization losing contradictions.

Source-derived or unresolved items, **not additional confirmed findings**:

- The same adapter caches appear to bypass current permission checks. The reproduced impact is stale acceptance; permission revocation/new disclosure through those caches was not demonstrated.
- `get_delivery` also includes global sessions/backend metadata. Actual message/evidence disclosure is F01; usable resume-credential disclosure or takeover is not established.
- B3 telemetry distinguishes selected and observed models, including mismatches. No configured native mismatch-rejection policy was established; a mismatch is not promoted into an unauthorized-substitution defect merely by discovery. Full configuration fidelity remains unverified.
- Claude's overlapping Stop ordering is reproduced by the shipped peer. Installed-vendor reachability needs native measurement.
- The independent runner imports candidate Python into its own process. Strong isolation against actively malicious arbitrary Python is a separate threat-model enhancement, not an added requirement or reported exploit.

No authenticated native run was supplied or executed. Historical PoC results remain historical evidence, not acceptance of this composition. No universal shell prohibition, mandatory roles, fixed Jev score, historical 69-call requirement, exactly-once guarantee or token-generation interruption requirement was invented.

## Remaining local native work and owner decisions

Use the supplied [native procedure](https://github.com/xormania/xorchestrate/blob/b0a00f1aed08d4ad8e066de8eb51f94e21da9f4a/docs/behavioral-verification.md), retaining the external brief, exact candidate, checker, configuration, executable versions and raw observations. Before relying on its pass verdict, address or explicitly dispose of the confirmed findings and rerun affected deterministic checks. Diagnostic native collection can still inform compatibility; it does not cure these defects.

The required local inventory remains: three fresh persistent conversations/private markers; six directed exchanges; three submissions during outstanding tool holds; six unfinished-work exchanges with reply/completion ordering; nine scored work batches; all actual native call/receipt joins; distinct communication/work/completion/acceptance results; and fifteen separate resume/attach/detach/reconnect/replacement cases. Preserve requested/resolved/observed settings and unknowns/mismatches.

Add focused native cases for owner revision followed by identical adapter replay; overlapping Claude turns with one held callback; reconnect between old-call observation and execution; and reconnect after effect application but before response. Confirm the original principal/work/incarnation, retained uncertainty, permission allow/deny behavior and ownership-aware cleanup. Raw audit must compare actual native arguments with executed callbacks and consider the latest attempt, not merely reproduce current summary booleans.

No owner scope decision blocks this report. xor remains the authority to revise requirements or dispose of findings. Product repair is a separate next action; none was undertaken in this review.

## Completion claims: survived, falsified, unsupported

**Survived:** exact recoverable candidate/evidence identity; six successful hosted jobs and their test totals; the independent 588-test/164-subtest local baseline with declared environment limitations; 46 composed protocol cases; twelve genuine supplied fault detections; useful hexagonal boundaries and substitutions; deterministic retained context; core owner-outcome preservation/revision; many production delivery/authorization/uncertainty controls.

**Falsified:** all requirements fully implemented; complete policy confinement for delivery inspection; original callback scope across reconnect; honest post-effect recovery certainty; end-to-end acceptance currentness on adapter replay and material evaluator-input changes; correct global Claude activity under the demonstrated overlap; and general assertion-specific calibration/independent raw-evidence sufficiency.

**Unsupported:** full native configuration fidelity, authenticated installed-vendor timing/lifecycle behavior, readiness to use the present checker as a complete native acceptance oracle, and full behavioral acceptance. Passing controls and negative attempts that found no defect remain bounded evidence.

The accompanying ZIP contains this report, machine-readable matrix/findings, original requirement basis, command records, logs/JUnit, controls, raw adversarial inputs, peer/mutation definitions, provenance audits and replay instructions. Duplicate clean git-backed source trees are omitted; their commit identities, input manifests and mutations remain sufficient to reconstruct them.
