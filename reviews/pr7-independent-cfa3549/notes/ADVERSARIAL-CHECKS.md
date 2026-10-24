# Independent behavioral and tamper controls

Review subject: `cfa3549490ee9db1c3e6cad918523cd854902e51`; product behavior remains the frozen `d2d2123bb8d4e866454c8e8e364f7749fa63a198`. No implementation, historical outcome, or paid model call was changed. These controls were reviewer-created after seeing the existing handoff and assignment. They are post-hoc adversarial checks, not new holdout samples.

The executable boundaries reproduced as claimed: **16/16 ordinary controls** (healthy and intended defect across all eight probe families), **24/24 unrelated setup/helper/timeout controls**, and **four actual acceptance-action controls** passed in a socket-capable local execution. The initially restricted sandbox left the healthy/fault variants of `p_replay` and `p_busy` **not_exercised**; its 36/40 result is retained separately and is not credited as fault detection. Socket-capable reproduction then resolved that local restriction. The 24 setup/helper/timeout injections occur before `run_operation`; they calibrate wrapper fail-closed behavior, not every possible failure position inside an adapter. The actual assertion-family helper error separately occurs inside a real pytest subprocess. These reproductions call the committed probe adapter and inspect its retained outputs; they are fresh executions, not an independent implementation of the adapter. Controlled protocol peers do not establish installed vendor behavior.

The current product calibration classifier remains intentionally defective: a returned wrong value reaches the comparison and is classified `detected`; an unrelated helper `AssertionError('TARGET_REQUIREMENT')` never reaches the comparison but is also classified `detected`. The experiment distinguishes them using the separately recorded reached phase and values. The healthy return passes, the wrong-return control reaches `{phase: comparison, actual: 2, expected: 1}`, and the helper error retains `{phase: entered}`. Thus the experiment does not credit the helper exception as valid intended detection, even though the frozen product classifier does. This is a sustained known-bad control, not a product repair.

The action controls execute real Application/SQLite state transitions: accepting stale material-context evidence creates a consequential false acceptance; rejecting withholds it; healthy acceptance succeeds; investigating the healthy case unnecessarily withholds acceptance. This proves the fixture can detect an action effect. It does not establish observed business losses, downstream repair, or broad operational usefulness.

**Priority: qualify the breadth of semantic replay validation.** The original archive and untouched replay control pass. The committed scorer rejects six tested alterations and accepts six others listed below. The unchanged `ARTIFACTS.sha256.json` seal rejects every mutation. This is a semantic-validation coverage gap, **not evidence that the published original bytes or conclusions were altered**. The original review's broader “raw replay” wording should identify which joins are checked by `runner.score`, which require the full-file seal, and which were independently recomputed.

| Disposable-copy mutation | `runner.score` result | Full unchanged seal |
| --- | --- | --- |
| Derived P1 action changed | Reject: deterministic composition | Reject |
| Wire request model changed | Reject: wire body differs | Reject |
| Raw provider model changed, duplicate ledgers made consistent | Reject: unexpected model version | Reject |
| Probe raw boundary changed | Reject: raw hash | Reject |
| Raw action acceptance flag changed | Reject: raw hash | Reject |
| Audit inclusion probability changed to zero | Reject: sampling design | Reject |
| End-to-end `by_policy.P4.correct_actual_acceptance` changed to 999 | **Accept; 999 copied into evaluation** | Reject |
| Action `decision_sha256` changed | **Accept** | Reject |
| Native per-invocation raw response changed | **Accept** | Reject |
| Native per-invocation identity changed | **Accept** | Reject |
| Selected audit `independent_miss` flipped | **Accept; weighted estimate becomes 2/24** | Reject |
| P4 route changed from `none` to `reasoning` | **Accept** | Reject |

The reason is concrete: [native normalization](https://github.com/xormania/xorchestrate/blob/cfa3549490ee9db1c3e6cad918523cd854902e51/experiments/jev_failure_correlation_v2/runner.py#L488) reuses `reasoning-responses.json` without joining each raw invocation; [audit verification](https://github.com/xormania/xorchestrate/blob/cfa3549490ee9db1c3e6cad918523cd854902e51/experiments/jev_failure_correlation_v2/runner.py#L544) rebuilds the selection design but not `independent_miss`; [action scoring](https://github.com/xormania/xorchestrate/blob/cfa3549490ee9db1c3e6cad918523cd854902e51/experiments/jev_failure_correlation_v2/runner.py#L606) checks raw hashes/copied fields but copies aggregate summaries and does not join `decision_sha256`; decision verification composes the answer without independently replaying the router. The independent stdlib recomputation written by the parallel review track validates all six omitted joins on the original and rejects all six corresponding mutations, while accepting the untouched control (`independent-tamper-crosscheck.json`). Thus the gap does not change the independently recomputed original results.

**Routing qualification.** Both P4 false clears are authority-family variants: `cef7128badec1` at judgment confidence 0.95 and `c51eabfc2dae5` at 0.85. The first already has explicit contradictory actor/work operands. The second initially lacks observations, then obtains the applicable decisive P2 probe. Both have decisive contradictory evidence by P3/P4; structured interpretation of their available final evidence rejects both. P4's categorical `route=none`, `uncertainty=none`, `probe_discrimination=no` causes no further action. Numeric confidence is not a routing threshold in [policies.py](https://github.com/xormania/xorchestrate/blob/cfa3549490ee9db1c3e6cad918523cd854902e51/experiments/jev_failure_correlation_v2/policies.py#L173). These are persistent interpretation errors and a failure to seek correction, not evidence that the only useful probe was suppressed before running. Budget exhaustion, provider failure, pending and unresolved status all compose to `defer` in the controlled checks; none becomes acceptance.

For E06, executable controlled-boundary attribution is demonstrated, including unrelated-error rejection; installed vendor validity remains unverified. For E02/E09/E12, original results remain supported after independent joins, but the broad claim that the built-in score command alone revalidates all raw/native/audit/action bindings is contradicted by the counterexamples above. Minimum additional evidence is exact semantic join validation, exercised against these controls; a new paid run is unnecessary for that question. No implementation repair was made.

Replay from any pinned repository checkout, using the isolated environment prepared by the root replay guide:

```sh
python3 -m zipfile -e experiments/jev_failure_correlation_v2/evidence/cloud-20261024-01.zip /tmp/pr7-raw
python3 path/to/reproduce_controls.py \
  --repo "$PWD" --original /tmp/pr7-raw/cloud-20261024-01 --out /tmp/pr7-controls-new
python3 path/to/tamper_replay.py \
  --repo "$PWD" --original /tmp/pr7-raw/cloud-20261024-01 --out /tmp/pr7-tamper-new
```

Use a host permitting local protocol sockets for the first command. Neither script invokes a paid provider. Every output directory must be fresh. To aggregate retained `behavior-controls.zip` results without executing behavior, extract it and pass `--out <extraction>/controls-final --summarize-existing` to `reproduce_controls.py`. This only checks already-recorded outcomes and is marked `aggregation_mode: retained_records`.

A reviewer-script aggregation attempt failed after the behavior had completed with a JSON decoding exception while reading the retained main `probes.json`; the original JSON remained valid and sealed when subsequently checked. That failed attempt is retained as `controls-final.log`. The reviewer script was corrected to load inputs before behavior and provide explicit existing-record aggregation. `controls-final-aggregation.log` records the successful aggregation of all 40 saved probe records and four action records; it is not presented as another fresh behavior run. An earlier complete socket-capable run also produced 40/40 (`controls-permitted.log`). No failed attempt was silently counted as a passing check.

`behavior-controls.zip` retains the successful final raw executions, SQLite fixtures, actual subprocess outputs/JUnit, and the initial sandbox-limited records. Absolute paths in these raw files describe the historical execution only. Scripts and published JSON outputs are portable through explicit arguments. The reviewer had the parent's task context, source, handoff, existing claims, and other review tracks' messages; this is an independent computation/behavioral review, not a blinded outcome assessment. xor remains final authority.
