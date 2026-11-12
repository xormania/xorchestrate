# Measuring Jev on engineering incidents

The operational replay compares current Jev questions with pinned source from real
Xorchestrate defects and repaired controls. It keeps three different observations
separate: the unchanged architecture check, the original Codex discovery, and the
current provider's source-bound judgment. A known-defect match is not a new
discovery, a confirmed operational benefit or an engineering time saving.

## Run the retained source cases

Run keyless workflow checks first:

```bash
python -m scripts.architecture.operational --mode fixture \
  --output .xorchestrate/operational-fixture.json
```

This reads immutable Git snapshots and the snapshot's own architecture profile,
verifies source/profile SHA256 pins, and runs the current deterministic checker.
Historical modules are parsed, never imported or executed. Scripted predictions
exercise reporting without contacting a provider. Full Git history is required;
CI already checks it out. A missing revision or changed pin fails explicitly.

For an optional live heldout replay with the normal configured credential:

```bash
python -m scripts.architecture.operational --mode live --split heldout \
  --jev-config contracts/hexagonal-jev.json --max-seconds 120 \
  --output .xorchestrate/operational-live.json
```

`JEV_ENDPOINT` and `JEV_MODEL` select the deployment as in ordinary diagnostics.
The live route reuses the same atomic question, request planner, cache and
TypeSafe transport as the diagnostic stages. Each subject supplies actual source
units and an explicit operational requirement. Applicability, evidence sufficiency,
the claim and supporting evidence are assessed separately. These behavioral
requirements are not disguised as import-boundary questions.

`--repeat 2` collects repeated trials when stability matters. Comparison requires
the same source packet, question identity and observed model; cached copies are
excluded from stability evidence. Repeated trials do not become independent
incidents. The elapsed-time budget stops admission of later cases and limits each
case's transport timeout; it is not an operating-system hard deadline.

## Frozen source population

The [catalog](../tests/fixtures/jev-operational/catalog.json) has six cases in three
paired mechanism groups. The source refs, complete target spans, source/profile
hashes, original review and named regression are retained for each case.

| Group | Split | Faulty source → repaired source | Independent historical record |
| --- | --- | --- | --- |
| Host-owned exit status | Tuning | `a4fbc482f05faf3f83b02e17a45324f100793a51` → `15e936c82961c1703f987f87a8d453877b6fb3d8` | [PR71 code finding](https://github.com/xormania/xorchestrate/pull/71#discussion_r4535898775): competing reaping could turn exit 7 into exit 0 |
| Hosted session capacity | Heldout | `19df4c79f2218486574cb9dae44ed2b6e8d17cc0` → `a7f4bb16294c36afe1c62ba5ca656f686e7f83a1` | [PR73 security finding](https://github.com/xormania/xorchestrate/pull/73#discussion_r4535926434): per-child limits did not bound the hosted wrapper |
| Recovery work authority | Heldout | `1361a588d6034baf1f3002a4d94e32e76cc94506` → `ddbe9f37f82b01d05782224ac8b8e0897db4b2f1` | [PR74 code finding](https://github.com/xormania/xorchestrate/pull/74#discussion_r4535905292): missing work binding bypassed a caller-specific deny |

Both revisions of a group have the same requirement and split. Source variants
cannot cross from tuning into heldout. Splits are fixed before this replay is
used for tuning; the incidents and repairs are already public, so this is not a
blind test of unseen mechanisms or model training exposure.

Provider subjects contain source and requirement evidence only. Case labels,
before/after names, split/group labels, original reviews, regression assertions,
oracle findings and later dispositions remain outside the model input. Related
test callers are excluded from the operational question packet. Complete target
coverage is checked before provider work; missing targets remain unknown.

The baseline is the current architecture checker with every rule in the pinned
profile. It is not a recreated historical full CI run. A structural pass does not
establish the three behavioral invariants. The original Codex finding is a
separate historical comparator: matching it again supplies no incremental
discovery credit over that record.

## Review and disposition without another provider call

Reports retain exact subjects, source pins, source refs, question-policy hash,
question/packet identities, configured and observed provider metadata where
exposed, availability, source coverage, latency, fresh/reused usage and cache
status. A provider concern initially remains unconfirmed.

To confirm a finding, independently reproduce the claimed defect against its
pinned source and retain a witness JSON record containing:

- `schema_version: 1`, `case_id`, `source_ref` and `requirement_id`;
- `verdict: "defect_reproduced"` and the complete target file's `source_sha256`;
- the actual `command` array, `test_id` and intended failing `assertion`.

Record its file path and SHA256 in a disposition entry. The ledger has
`schema_version: 1` and an `entries` list. Each entry binds `case_id`, `source_ref`,
`packet_sha256` and `subject_id`, and supplies `status` (`confirmed`, `dismissed`
or `unconfirmed`). A confirmation requires `witness: {"path": "...", "sha256":
"..."}`. Paths are relative to the ledger. Optional `reviewer_minutes` is an
actually measured nonnegative number; omission stays unknown.

Apply the ledger to a retained live report:

```bash
python -m scripts.architecture.operational \
  --adjudicate .xorchestrate/operational-live.json \
  --dispositions .xorchestrate/dispositions.json \
  --output .xorchestrate/operational-adjudicated.json
```

This reads and hashes the witness, verifies its source and requirement binding,
and recomputes the report without Git snapshot reconstruction or model calls.
A witness records independently supplied evidence; JSON alone does not attest
that an external command ran. Fixture-only predictions cannot earn confirmed
operational credit. Retained subject packets that have changed are rejected.

Known-fault matches, independently confirmed findings, unconfirmed concerns,
healthy-control false alerts, decisive misses and unknown/unavailable cases remain
distinct. An abstention is not a false clear. Fresh and reused tokens are separate.
The main known/confirmed/unconfirmed finding totals count unique case, source and
packet identities. Repeated-trial counts remain explicit under `attempt_outcomes`;
neither fresh repetition nor cache reuse creates another engineering finding.
Missing usage, invoices and price schedules remain unknown. Review times are
recorded only when supplied, with duplicate review identities counted once across
repeated trials. No time saving, billing total or monetary benefit is inferred.

## Economical CI selection

The existing advisory job selects a full evaluation when model/contract,
question, source-packet, request/evaluation code, corpus or evaluation-test inputs
change. Unknown comparison scope also selects full. The full path runs the
existing synthetic pilot and the operational heldout population. Other code PRs
run one small intentional live control through the same atomic evaluator, without
historical snapshot reconstruction or cache reuse:

```bash
python -m scripts.architecture.operational --mode live --health \
  --output .xorchestrate/jev-health.json
```

The health report identifies its explicit control and retains availability and
the answer. It makes no operational-effectiveness claim. Selecting a deployment
through repository variables without changing a tracked model configuration
requires an explicit full evaluation (for example the live command above);
changed-path classification cannot observe a variable edit.

All evaluation/health steps remain advisory and retain artifacts after failure.
The existing deterministic qualification and repository merge decision are
unchanged. Provider execution, a typed answer and useful engineering outcomes
remain separate observations.
