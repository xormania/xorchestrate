# Preservation of the seven previously closed findings

Exact product: `e565863ae72180571678c8f6118ee6e00b129e2e`, tree `474de55c5947890ab4092daeca7ff39f9bbbc400`. Fresh execution used Python 3.12.14 and the recorded replay dependencies. Candidate files were not changed. This track found no regression in F02, F03, F04, F05, F06, F09 or F10 within the exercised scope. This result does not close the separate IV01–IV04 investigation.

| Finding | Fresh observations and positive controls | Result |
|---|---|---|
| F02 callback authority | Both stores reject a callback observed before two successive reconnects with `stale_connection`; no work receives old evidence. The newest callback succeeds exactly once as the newest actor. Missing call identity and foreign conversation reject. | Preserved |
| F03 post-effect recovery | Both stores reject pre-effect stale sends with zero executions. After an already applied held effect and two replacements, both current sends remain fenced, the original operation stays unknown, and completed host evidence persists. Wrong-version resolution rejects; valid resolution permits replay without another effect, then one new effect succeeds. | Preserved |
| F04 cached native calls | Native Codex/Claude/Grok paths through external FIFO bridges reject revoked authority and permit restored authority. Deleting the actual durable native-call row rejects replay on six direct/bridged paths without another work creation. Material assessment changes invalidate cached acceptance on all six paths while unchanged and fresh-current controls retain their intended behavior. | Preserved |
| F05 assessment applicability | Both stores reject nested integer-to-boolean changes and actor/contribution mutations during acceptance; stale decisions do not commit. Reassessment is usable. Configured projections excluding goal/metadata permit edits to those unconsumed fields. | Preserved |
| F06 original Claude overlap | Actual native fixture: both callbacks held means active; release A while B remains held stays active; releasing B reaches idle. One native process and gateway exit and no driver sessions remain. | Preserved |
| F09 latest delivery attempt | On this review's fresh complete protocol run, later unknown/failed/running/queued attempts each fail the affected exchange with `latest_delivery_attempt_not_submitted`. An older unknown attempt followed by the valid newer submission, and harmless raw record reordering, preserve 46/46. | Preserved |
| F10 unfinished-work timing | On fresh protocol observations, setting work completion equal to or one microsecond before the independently observed reply rejects with `reply_after_work_completion`; the second later exchange also correctly becomes out of window. Missing deadline produces `invalid_observation_timestamp`. Expressing the original valid instant in UTC+01:00 preserves 46/46. | Preserved |

## Executed evidence

- `bridge-observations.json`: two callback-authority cases plus four pre/post-effect cases; six asserted cases passed. Full host events, stored operations, valid/invalid resolution and resulting counters are retained.
- `cache-assessment.xml`: 23 independent variants passed, zero failures/errors/skips. Each case's raw observations and native/bridge artifacts remain in `cache-assessment-tmp/`.
- `fault-observations/assertion-witnesses.json`: five passing controls and five activated faults failing the intended assertion. Three native cache faults terminate in `assert_replay_rejected` with exact marker `F04_CURRENT_NATIVE_REPLAY_REQUIRED`; two store assessment faults terminate in `require_stale_rejected` with `F05_MATERIAL_ASSESSMENT_INPUTS_MUST_REMAIN_CURRENT`. This is additional preservation sensitivity evidence, not five new members of the original twelve source faults.
- `scripts/probe-results.json`: original Claude overlap schedule with active-before/active-partial/idle-final controls and explicit cleanup observations.
- `checker-observations/`: unchanged prior compact checker challenge, including F09 and F10 valid/rejection controls. The fixture constructor comes from candidate tests; the prior review script supplies the required expected outcomes. This is supplemental to the independently selected fresh-run changes below.
- `fresh-boundary-observations/`: 11 complete checks using this session's raw 46-case protocol execution. Each retains a full compressed raw run, exact mutation record, report and input hashes. The four legitimate controls retain all 46 passing cases. The seven rejection variants reject for the intended uncertainty/timing reasons.

All subprocess command records, cwd/environment overrides, timestamps, return codes and raw stdout/stderr hashes are in each named command directory. These are new candidate measurements, not credit copied from retained reports. Test totals overlap root suite coverage and are not added to its primary count.

## Requirements, configurability and scope

Expectations follow the owner brief: H3 distinguishes delivery attempts, replies and completion; H4 separates incarnation and ownership; H7 preserves uncertainty, replay fences and stale-connection handling; B2 requires current material acceptance evidence; H5/H6 and A1/A2 retain configurable providers, policy and storage at appropriate boundaries.

The fresh tests exercise both Memory/SQLite implementations, three native adapter protocols, direct versus externally hosted FIFO transport, mutable rule-based permission revocation/restoration, replaceable acceptance policy, and configurable assessment work-input projections. They retain usable positive paths after restoration/reconciliation/reassessment instead of requiring blanket rejection. Comparison to the independently reviewed `ef75675` shows no changes in `ports.py`, `composition.py`, acceptance-policy adapter, storage adapter, bridge adapter or Claude adapter. Candidate boundary/full-suite coverage is documented by the root and provenance tracks. No mandatory evaluator hierarchy, fixed routing order, vendor requirement, universal approval rule or Jev coupling was added by these repairs or this review. The other tracks' specific IV01/IV03 findings still limit evidence/scope guarantees despite these preservation results.

This is bounded deterministic product/fixture execution and raw-evidence substitution testing. It is not authenticated vendor execution or proof of every configured adapter/policy. No new finding in this track warranted reopening the seven prior closures or a wholesale reconciliation.

## Attempts and adaptations

All six reused scripts were copied byte-for-byte from the checksummed original review; `input-scripts.json` binds their source and destination hashes. No semantic or timing patch was needed for these fresh replays. The original Claude script was renamed to `claude_original.py` to avoid basename collision; its selected `ROOT` already equals the current candidate path. For portability, replace that path in a separately retained copy and record the diff. It writes `probe-results.json` beside itself, so always run a copy in a fresh evidence directory.

The original compact checker script is an observational reporter and exits zero even if an expected outcome is not met. Its `callback-kind-mismatch` side probe reports expected incomplete versus actual passed. The prior independent report explicitly retained that side probe as investigation rather than an established finding; changing kind alone has not established an independently decisive requirement violation here. It is neither hidden nor counted as a passing rejection test. The IV03 reviewer was notified. Every F09/F10 outcome from this script matched expectation; the separate fresh-boundary script asserts its expected outcomes and exited zero.

No fresh execution failed or required replay retries in this track. Interrupted scheduling before execution did not produce a source or evidence mutation. No product changes, merge, release or Jev work occurred.

## Replay

Recover the exact product and install the published replay dependencies. Set `CANDIDATE`, `PYTHON` and `FRESH` to absolute paths; create a new output root and copy `scripts/` there before using the Claude script. Set `PYTHONPATH` to the candidate plus copied script directory and `PYTHONDONTWRITEBYTECODE=1`. Local sockets must work for the actual native/bridge fixture paths.

```sh
"$PYTHON" "$FRESH/scripts/probe_bridge_variants.py" "$CANDIDATE" "$FRESH/bridge-observations.json"
"$PYTHON" -m pytest "$FRESH/scripts/test_independent_cache_assessment.py" -q -rs --basetemp="$FRESH/cache-tmp" --junitxml="$FRESH/cache.xml"
"$PYTHON" "$FRESH/scripts/fault_witnesses.py" "$CANDIDATE" "$FRESH/faults"
"$PYTHON" "$FRESH/scripts/claude_original.py"
"$PYTHON" "$FRESH/scripts/challenge_checker.py" --candidate "$CANDIDATE" --out "$FRESH/compact-checker"
"$PYTHON" "$FRESH/scripts/fresh_delivery_boundaries.py" --candidate "$CANDIDATE" --raw /absolute/path/to/fresh/protocol --out "$FRESH/fresh-boundaries"
```

Run from the selected candidate cwd. `--raw` must contain the complete `run.json`, `criteria.json`, `bindings.json`, `fixture.json` from the fresh protocol recorder. The original 46-case inventory remains unchanged; this script modifies only raw attempts or provider event timestamps in isolated copies and records every change. The compressed runs can be read with Python's standard `gzip` module.
