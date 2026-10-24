# Narrow IV03 applicability adjudication

**No material blocker demonstrated by the nested-session historical probe.** The optional field is a typed application-session claim, and the retained historical row is internally contradictory. However, the row is excluded from the current call's evidence; the passing current exchange has a complete independent native/requested/completed chain. This case does not demonstrate borrowing credit from the malformed historical row.

The identity agent's raw probes are under `identity-results/probe-output/{checker,receipt}-historical-{native,requested,callback,return}-{coherent,current,null}.json`. They begin with a passing complete current exchange and qualified historical records, then alter one historical row's `observation.session_id`. The historical outer `session_id` is `historical-application`, native identity is `codex-native`, and incarnation is `historical-incarnation`. Setting the optional nested session to `codex-application` does not alter the independently recorded historical connection or any current record.

## Code grounding

- `scripts/verification/evidence.py:38–39,107` preserves this field as `observation_session_id`, and `scripts/verification/core.py:86–93` checks equality with the observation's application session. `xorchestrate/exchange.py:36–41` checks the same optional claim. It is not opaque provider content.
- `evidence.py:9–19,123–132` assigns applicable observations using their retained application session or matching native connection/incarnation. `exchange.py:72–81` similarly qualifies a reused call label by application session or native connection/incarnation. The nested optional claim alone does not select a current call.
- `docs/repair/architecture-iv123.md` says reused call labels in coherent retired or unrelated session scopes do not become current evidence merely by sharing a label. The probe makes the optional nested field incoherent, but does not demonstrate that the independent connection qualification is wrong or that the record contributes evidence to the selected proof.
- `xorchestrate/delivery.py:481–513` retains the supplied observation without validating its nested session; `delivery.py:87–98` includes all retained observations in the policy snapshot. Thus the raw-input shape is supported. This is an observation-boundary validation/diagnostic question, not evidence of a false current exchange.

## Independent negative controls

To distinguish ignored contradictory data from borrowed proof, `nested-session-adjudication.py` removes each required current observation in turn, while retaining the corresponding historical row with the same call label and an explicit nested current-session claim. Expected rejection follows from the missing required current proof; it is not computed from either subject's verdict.

| Removed current observation | Checker result | Receipt result |
| --- | --- | --- |
| Native call | Rejected: `native_call_missing_or_misbound` | Rejected: `independent_native_observation_missing` |
| Requested callback | Rejected: `callback_request_missing_or_misbound` | Rejected: `native_request_observation_missing` |
| Completed callback | Rejected: `challenge_missing_or_misbound` | Rejected: `native_callback_missing_or_misbound` |

All six controls reject. Full raw inputs and decisions are in `nested-session-controls.json`. Reproduce with:

```sh
python nested-session-adjudication.py /path/to/pinned-candidate nested-session-controls.json
```

The assessed candidate remains `1fef55c0c0cadd5e1417c0e010f272f6447f8ea4`, tree `75ecb3ef4edbc381012fe74e416a9abd3fb5c667`. No product code was changed.

## Disposition and limit

Retain this observation as a possible narrow follow-up for diagnostic/validation policy on discarded records. Do not report it as a false-credit failure: these experiments show the contrary when current proof is removed. This assessment does not establish that every malformed historical record is harmless, and it does not weaken rejection requirements for claims on applicable current records. A blocker would need a concrete required proof accepted using contradictory or missing applicable evidence; this particular probe does not supply one.
