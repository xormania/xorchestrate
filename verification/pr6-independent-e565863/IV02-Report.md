# Independent IV02 review — partial closure; residual verifier blocker

The original product defect is reproduced on `ef7567516520543970af55b6f19534cb66eda357` and absent in the exercised schedules on exact candidate `e565863ae72180571678c8f6118ee6e00b129e2e`, tree `474de55c5947890ab4092daeca7ff39f9bbbc400`. **IV02 remains open in the checker:** an accepted Grok interjection waiting to begin native execution is omitted from the checker's pending-submission ledger. A false aggregate idle then earns completion credit while that independently observed submission remains held.

This is a demonstrated verifier blocker, not a claim that the repaired adapter itself falsely reports idle in this new schedule. The adapter correctly reports active. No product files were changed, and no authenticated vendor session, Jev work, merge or release was performed.

## Decisive new counterexample

`submission-gap-v2/` contains a real application/SQLite/provider/native-subprocess run, with a peer-side gate outside product source:

1. Submit A, execute its application callback successfully, and hold its native continuation tail.
2. Submit B while A is active. Grok uses its supported `_x.ai/interject` path. The peer returns `accepted: true` with B's identity, then waits at a gate **before** sending B's first native start or tool notification.
3. Complete A's tail while B remains held. Raw application observations retain B's `native_submission`, `transport: grok/_x.ai/interject`, `interjection_id`, and accepted response. The live adapter retains the same ID in `interjections` and reports active. B's entered gate exists and its release gate is absent. There is no start/end observation for B yet.
4. Deep-copy that exact raw snapshot and append one native idle observation. All original records remain byte-equivalent as parsed JSON; no submission, identity, operation, callback, or native transition is deleted or rewritten. The checker returns `completion-grok: passed`.
5. Release B. Its callback and native turn execute; the real product reaches idle and the checker legitimately passes.

The same accepted-before-start schedule for Codex is the rejection control: its recorded submission contains the turn identity the checker actually reads, so forged idle rejects with `completion_native_turn_unresolved`. Both peers have valid final completion controls.

Decisive artifacts:

- `submission-gap-v2/grok/B-accepted-held.json`: original raw evidence, active product, held B gate, B ID in adapter interjections.
- `submission-gap-v2/grok/B-accepted-held-forged-idle.json`: identical original records plus one false idle; passing completion report.
- `submission-gap-v2/grok/forged-delta.json`: explicit one-append mutation and equality assertion.
- `submission-gap-v2/grok/all-completed.json`: legitimate final control.
- `submission-gap-v2/codex/`: same schedule with a correctly enforced independent veto.
- `scripts/submission_gap_v2.py`: executable schedule, independent gate assertions and exact-delta validation.

The raw-observation expectation follows H2/H3's distinction between submission and completion, H7's unresolved-work preservation, and the handoff's explicit requirement to reconcile pending submissions independently of aggregate idle. It does not depend on comparing two copies of the same checker.

At [checker.py lines 340–344](https://github.com/xormania/xorchestrate/blob/e565863ae72180571678c8f6118ee6e00b129e2e/scripts/verification/checker.py#L340), Grok's submitted-turn identity is read only from `prompt_id` or `result.promptId`. Its actual [interjection producer](https://github.com/xormania/xorchestrate/blob/e565863ae72180571678c8f6118ee6e00b129e2e/xorchestrate/adapters/grok.py#L337) records `interjection_id`. Consequently B is never added to `submitted`; with no start yet, the raw ledger supplies no veto. The problem is observable with fully identified, valid accepted submissions; no malformed protocol packet is necessary.

Severity: **P2 verifier / IV02 residual**, affecting independent completion evidence and H8/B5 sufficiency. Recommend holding integration until this accepted-but-not-started interjection is represented and rejected while outstanding, while preserving Codex/Grok final completion controls. Do not satisfy the requirement merely by trusting adapter aggregate state.

## Original witness and valid behavior

The unchanged original witness is copied as `scripts/original_iv02.py` (byte-identical to the checksummed repair archive input).

| Execution | Result | Independent meaning |
|---|---|---|
| `original-baseline/`, exit 1 | Six schedules completed; Codex BA alone exhibits defect | A callback remains held, product is idle and old checker passes after B; all final controls pass |
| `original-candidate/`, exit 0 | All six schedules valid | All partial stages active/incomplete; all final stages idle/passed |
| `original-raw-new-checker.json` | 18/18 stage expectations | Separately selected repaired checker rejects original partial raw, including Codex BA, and accepts legitimate final controls |
| `mixed-holds/`, exit 0 | 36 stages across Codex, Claude, Grok | Four overlapping turns, mixed callback/tail completion, and a new turn after idle preserve correct activity |
| `raw-veto-matrix/`, exit 0 | 30/30 explicit expectations on fresh full-protocol raw | Current unfinished/unknown calls and operations and missing/duplicate terminals reject; unrelated/retired controls pass |

The independent mixed schedule starts A/B/C/D, releases callbacks C/A/B/D and native tails A/C/D/B, then starts E after legitimate idle. The oracle uses reviewer callback Events and peer-side tail gates, not `pending` internals or the production checker. All 30 held stages remained active and incomplete; a false native idle appended to each copied held snapshot was independently rejected. All six all-finished/post-idle final controls passed. Claude's tail gate is a documented reviewer fixture extension; it preserves real hook/MCP dispatch.

The raw veto matrix uses the root's fresh actual 46-case protocol run, not retained repair-session raw. Its unchanged control passes 46/46. For each peer, current native-call states pending/unknown/failed, applicable operation states pending/unknown/failed, a missing callback terminal, and a duplicate terminal all reject completion. A retired unknown native call and an unrelated pending operation remain valid controls. The matrix imports checker code from the separate `checker` checkout. The expectations themselves come from the intentionally unresolved record and its scope; isolation is not claimed as an independent correctness oracle.

The full active suite and focused regressions executed by the parent additionally cover every three-callback/tail permutation, early completion, duplicates, unmatched/missing completion identities, uncertain callback failures, and positive completion controls. Those existing tests are preservation evidence, separately identified from this track's independent schedules.

## Calibration synchronization observation — optional improvement

The parent's concurrent focused 594-case run retained one failure in `test_known_bad_per_turn_idle_is_detected_by_product_and_independent_checker`: expected `IV02_PENDING_WORK_IS_ACTIVE`, but an earlier assertion compared `native_completion_missing` with expected `completion_callback_unresolved`. Its source waits only for a native-completion record. The deliberately faulty wrapper appends false idle after the original callback records native completion and active activity; `measured()` snapshots raw before reading product status. A concurrent snapshot can therefore see old active raw and later idle adapter status, reaching the wrong assertion before the intended product marker. This is a test synchronization sensitivity, not evidence that a live product failure escaped that run. Preserve the failed attempt and any isolated rerun separately. The original twelve source calibrations are not disproven by this observation.

## Preservation, provenance and limitations

Application and adapter configuration remains unchanged. The probes instantiate the normal composition with SQLite and replaceable provider/session ports; no hardwired policy, organizational hierarchy or acceptance threshold is introduced. Callback work and native tails are tested separately, and simple valid completion remains achievable. The previously closed Claude-overlap behavior remains valid in the fresh six-schedule and mixed four-turn observations; other closed findings and full H/B/A provenance are addressed by the parent preservation review.

Sources were fetched by the parent directly from GitHub into clean exact worktrees; the evidence publication `37547a138758fe70e6cc471b491aaab01bfc3a05` was not selected as product. Source checkout status remains clean. `provenance.json` binds scripts, original witness bytes, source identities and imported modules. Each invocation's stdout/stderr and exit status is retained. Scripts write only outside source.

The subprocess peers are deterministic protocol fixtures, not authenticated Codex/Claude/Grok vendors. Gate placement models allowed asynchronous scheduling and changes no product code. No universal proof over all schedules is claimed. Vendor acceptance remains the user's planned local work and is not the reason for the integration hold.

## Attempts and adaptations retained

- Original baseline exit 1 is expected defect reproduction, not a setup failure; candidate exit 0 includes its positive controls.
- Initial eight small raw explorations found permissive handling of missing/null submission identities and unknown completion events. `exploratory-not-decisive.json` retains those outputs and the command logic. They are not counted as additional blockers: the stronger accepted-interjection schedule uses valid records and independently held work.
- `submission-gap/` and `scripts/submission_gap.py` preserve the initial successful counterexample. V2 strengthens it by copying one captured snapshot, asserting the exact one-record delta, and retaining the live interjection ID. The result is unchanged.
- `submission-gap-v2/peer-adaptation.patch` inserts only a pre-start gate after peer submission acknowledgment. `mixed-holds/claude_native.py.patch` inserts only a post-callback/pre-Stop tail gate and imports `time`; the Codex/Grok peer copy remains byte-identical there.
- The parent's focused calibration timing failure remains in the parent command/raw evidence. It receives no passing credit in this track.

## Replay

Use Python 3.12 with the root's recorded dependencies, actual local sockets and fresh output paths. `CANDIDATE` must be exact e565863 and `CHECKER` a separately selected clean checkout of that commit. `TRACK` is this restored directory.

```sh
cd "$CANDIDATE"
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$CANDIDATE" "$PYTHON" "$TRACK/scripts/original_iv02.py" --peers codex claude grok --out /fresh/original-candidate
PYTHONDONTWRITEBYTECODE=1 "$PYTHON" "$TRACK/scripts/submission_gap_v2.py" --candidate "$CANDIDATE" --out /fresh/submission-gap
PYTHONDONTWRITEBYTECODE=1 "$PYTHON" "$TRACK/scripts/mixed_holds.py" --candidate "$CANDIDATE" --out /fresh/mixed-holds
PYTHONDONTWRITEBYTECODE=1 "$PYTHON" "$TRACK/scripts/raw_veto_matrix.py" --checker "$CHECKER" --protocol /fresh/full-protocol --out /fresh/raw-veto
```

Repeat the original witness from the baseline checkout with `PYTHONPATH` also selecting baseline. Expected baseline exit is 1 with exactly Codex BA defect, candidate exit 0. The submission-gap script exits 0 when its schedule/control assertions complete; inspect `summary.json`: Codex `raw_checker_veto=true`, Grok `raw_checker_veto=false`. That false field is the demonstrated blocker, not closure. Mixed-holds and raw-veto scripts require every declared expectation to hold.
