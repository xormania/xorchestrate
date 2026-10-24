# Work requirements and acceptance

Work status, measurement, assessment, authorization, and acceptance are separate
facts. `update_work` can change a plan or status; it cannot create acceptance.
`record_result` retains a caller statement. An evaluator's favorable assessment
cannot replace a failed or missing required check.

The optional workflow uses the same application and configured capability
providers as ordinary work. A simple messaging deployment need not declare
requirements, create measurements, or use an evaluator.

## Declare the owner basis

Create a work item, then call `set_requirements` with `expected_revision: 0` for
the first basis. Subsequent owner changes supply the current revision. Each
revision is retained. Authorization for this operation belongs to the configured
guardrail, as it does for other application operations; configure the actors
permitted to revise owner requirements at the deployment boundary.

```json
{
  "work_id": "WORK_ID",
  "expected_revision": 0,
  "objective": "Deliver the happy path and recovery",
  "outcomes": {"A": "Happy path works", "B": "Recovery works"},
  "checks": {
    "happy": {"capability": "product/measure", "outcomes": ["A"]},
    "recovery": {
      "capability": "product/measure",
      "outcomes": ["B"],
      "bindings": {"scope": "recovery", "artifact": {"sha256": "EXPECTED_HASH"}}
    }
  }
}
```

Every declared check must pass, and every required outcome must be covered by a
passing declared check. Declaring an outcome with no check keeps it visibly
uncovered. A later explicit revision can add, replace, or remove outcomes or
checks. A revised basis requires new measurements; old results remain history.

`create_contribution` and `update_contribution` represent subordinate goals,
outcome references, and statuses. Narrowing or cancelling a contribution does
not remove the owner's outcome. The full objective and current coverage are
included in evaluator context under `acceptance_basis`.

## Identify and measure the candidate

`set_candidate` records the exact candidate selection:

```json
{
  "work_id": "WORK_ID",
  "expected_revision": 0,
  "candidate": {
    "identity": "tree-or-content-snapshot-identity",
    "configuration": "material-configuration-identity"
  }
}
```

The candidate object is deployment-defined JSON with a required nonempty
`identity`. It must identify all relevant measured inputs, including working
changes where applicable. This selection is an expectation, not proof that a
provider inspected those inputs. The configured measurement provider must
independently inspect its actual input and return the candidate it measured.
The application compares that observation with the selected candidate. Changing
files outside the application requires capturing/selecting their new identity;
the infrastructure-independent core does not inspect a filesystem itself.

Call `run_measurement` with a required `check_id`, a unique `measurement_key`,
and optional provider arguments. The application durably reserves a measurement
before invoking the criterion's configured capability. Retrying the same key
does not issue a new effect; a changed request with that key is rejected.

The provider receives this envelope in its ordinary invocation arguments:

```json
{
  "measurement": {
    "work_id": "WORK_ID",
    "check_id": "recovery",
    "requirements_identity": "APPLICATION_REQUIREMENTS_HASH",
    "requirement_revision": 1,
    "candidate": {"identity": "EXPECTED_CANDIDATE"},
    "bindings": {"scope": "recovery"},
    "outcomes": ["B"]
  },
  "arguments": {}
}
```

Its result must contain a measurement observation:

```json
{
  "measurement": {
    "work_id": "WORK_ID",
    "check_id": "recovery",
    "requirements_identity": "APPLICATION_REQUIREMENTS_HASH",
    "requirement_revision": 1,
    "candidate": {"identity": "ACTUALLY_MEASURED_CANDIDATE"},
    "bindings": {"scope": "recovery"},
    "status": "completed",
    "passed": true,
    "outcomes": {"B": true}
  }
}
```

The result may be a direct CLI object, MCP `structuredContent`, or one JSON text
block. The application preserves the raw provider response, its digest, the
adapter-origin operation, requested binding, requirement revision, candidate
revision, and resolved provider configuration identity. Requested values are
never filled into missing observation fields. Use `bindings` only for applicable
scope, artifact, checker, native-session, or incarnation associations. Every
required association must match using type-sensitive JSON equality.

The configured provider is a measurement trust boundary. Merely echoing the
request does not establish that the product or artifact was measured. The
composed CLI regression demonstrates a provider reading the actual product and
computing its digest independently of the requested digest. The independent
verification handoff must also identify and review the selected checker.

Missing, failed, running, uncertain, stale, or incorrectly bound required
measurements prevent acceptance. An unrelated passing check cannot replace a
required failed check. A later completed measurement can supersede a definite
failure of the same check on the same basis. Pending or uncertain effects remain
unresolved; a fresh pass does not erase them. A retained adapter-origin result
can be consumed after operation reconciliation by replaying its measurement
key. Caller-manufactured reconciliation output cannot become measured truth.

## Decide and consume acceptance

`accept_work` evaluates the current basis using the configured acceptance policy.
It may optionally reference an `assessment_id`. Assessment references must match
the current requirement, candidate, measurement, operation, policy, and provider
configuration binding. Changes during policy evaluation are fenced before the
decision is committed. Rejected decisions are retained with specific diagnostics.

An assessment also retains the routing, threshold, uncertainty options, and
resolved configuration identities of its selected evaluators as observed before
inference. Changing those inputs invalidates decisions that consume the old
assessment, including after a persisted-state rebuild. Mechanical measurements
remain applicable; changing an unrelated evaluator does not invalidate them or
the selected assessment. A replayed native acceptance call cannot revive a
superseded decision, and a replayed native read cannot present a changed view as
current.

The built-in configuration is:

```json
{
  "acceptance": {
    "adapter": "required-checks",
    "options": {"require_assessment": false}
  }
}
```

An optional `assessment_outcomes` list selects favorable assessment names. No
particular model, confidence threshold, role hierarchy, or approval organization
is mandatory. A custom `module:factory` policy exposes a stable material
configuration `identity` and `evaluate(snapshot) -> {accepted: bool, reasons:
list}`. It can add deployment-specific decisions; it cannot turn missing or
invalid required evidence into acceptance. The authorization guardrail remains
separate and may deny the acceptance operation even when evidence is sufficient.

`get_acceptance` and `get_work` return current coverage, issues, measurements,
contributions, and decision history. The `accepted` field uses the latest
applicable decision. Changed requirements, candidate selection, relevant provider
configuration, policy configuration, or measurement/operation state invalidate
earlier acceptance. Superseded decisions are retained with `current: false`.
Checks for unrelated providers do not alter the binding.

These states concern the selected candidate and declared check inventory. They
do not claim that native vendor acceptance or independent review has run. Keep
those verification states explicit in the evidence handoff.

## Deterministic verification

```bash
python -m pytest tests/hexagonal/test_work_acceptance.py tests/hexagonal/test_work_calibration.py tests/hexagonal/test_boundaries.py -q
python tests/hexagonal/calibrate_work_acceptance.py --output .xorchestrate/calibration/UNIQUE_RUN
```

The suite exercises both storage adapters, configured policy substitution,
criteria changes during and after assessment, preserved owner outcomes,
measurement uncertainty, negative binding cases, and the actual CLI provider
boundary. Its assertions are tied to B1, B2, A1, and A2 in the implementation
brief. The historical completion tests supplied behavior and failure-case
patterns; the active application does not import the historical runtime.

The calibration command copies the measured application and tests to isolated
snapshots, retains their hashes and archive, and plants five targeted mutations.
Each run requires a clean passing baseline, its exact expected assertion failure,
and passing unaffected controls. Raw output, pytest XML, commands, timestamps,
and mutation patches are retained. Setup errors, unrelated failures, skipped or
missing cases, and escaped faults never receive detection credit. The classifier
tests exercise real failing, skipped, and missing-dependency pytest subprocesses.
