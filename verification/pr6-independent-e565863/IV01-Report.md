# IV01 independent verification

Verdict: **open; original session witness repaired, residual typed assessment disclosure demonstrated** on `e565863ae72180571678c8f6118ee6e00b129e2e`, tree `474de55c5947890ab4092daeca7ff39f9bbbc400`.

The original unchanged witness reproduced denied same-work historical session disclosure on `ef7567516520543970af55b6f19534cb66eda357` in MemoryStore and SQLiteStore. It exits 1 on the repair because its original assertion requires the disclosure. `closed-iv01.py` changes only that expectation and the defect flag; `closed-expectation.diff` records both changes. Authorized public history and direct-denial controls pass on both stores. The retained provider, measurement, direct-operation, typed-result, requested-phase, conflicting-key and catalog challenges also pass freshly in `retained-cross-review-replay/review.json`. These passes do not establish complete recursive coverage.

The new minimal witness is `probe_assessment_binding_v2.py`. Actor `worker` performs a native `assess`, `list_actors` and `record_result` on a public work. A separate inspector `observer` initially reads the history. Current RuleGuardrail policy then explicitly denies inspection of the worker actor record (`inspection.kind=actor`, `record.id=worker`) for `native_observations` and `get_work`.

The historical `list_actors` result removes worker and retains observer, proving the same-action configured denial applies. Nevertheless `assess.result.assessment.input_binding.actor_record` in both native-call and completed-observation history still returns the full denied actor, including `PRIVATE_TYPED_ACTOR_METADATA_4ce011`. Current `get_work` also discloses this actor through evidence whose application-owned source is `type=assessment` and whose result contains the same input binding. The probe uses actual application methods and both configured stores; it does not inject malformed storage records.

This is a typed application record, not arbitrary opaque evaluator/provider content: `Application._assess` creates `input_binding`, and `AcceptanceMixin._assessment_input_binding` copies `context.actor_record` (acceptance.py:199–210). The evaluator returns only a public conclusion and never returns the canary. Delivery projection recognizes the outer assessment result but does not traverse its application-created input binding (delivery.py:180–202,257–280). The source discriminator on assessment-produced evidence likewise identifies application-owned content.

Fourteen checks pass for each store (eleven preservation controls and three disclosure assertions) in `assessment-binding-v2-candidate/results.json`: initial stored-record identity, same-action denied catalog, authorized public catalog, unchanged opaque user `record_result` content and assessment request context, independent inspector/executor, both history paths, current work path, raw-store immutability, exact grant-restoration view, and no evaluator reinvocation. Opaque controls deliberately use a different marker, preventing a global-canary search from conflating intended opaque data with the typed disclosure. V1 is retained; V2 strengthens it with different principals and distinct canaries. Root independently inspected the typed source construction and repeated V2 in `results/root-rechecks/iv01-assessment`; both stores reproduced all checks.

Replay from the restored review root (fresh output path required):

```sh
PYTHONDONTWRITEBYTECODE=1 "$PYTHON" results/iv01/probe_assessment_binding_v2.py \
  --candidate "$CANDIDATE" --out /absolute/fresh/iv01
```

Exit 0 means the disclosure and all controls reproduced, not that the product passed. Inspect `results.json` and each store's `raw.json`. The original baseline and closed-expectation commands are recorded in the top-level replay guide. Source remained unchanged. No blanket recursion into user/provider dictionaries is recommended: preserve opaque payload semantics and trusted raw inputs while projecting explicitly typed application-owned assessment records. This is an integration blocker under IV01/current inspection policy and H5/A2, not an optional hardening suggestion.
