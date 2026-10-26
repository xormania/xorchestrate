# Hexagonal architecture enforcement

The deterministic `Hexagonal architecture` CI job enforces the current application
contract. Jev supplies separate diagnostics locally and in CI. A diagnostic result
cannot clear a deterministic violation. Discoveries do not automatically create
requirements or modify the contract.

## Goal and completion evidence

| Required outcome | Evidence |
| --- | --- |
| Reusable deterministic engine with repository policy supplied as data | `scripts/architecture/core.py`, Python extraction adapter, and `contracts/hexagonal.json`; calibration uses two unrelated package namespaces |
| Enforce the active application's inward dependency direction | Exhaustive module classification, required selectors, direct/transitive dependency paths, package initializers, declared external dependencies and constrained dynamic operations |
| Preserve legitimate extension and port substitution | Valid adapter-extension controls; existing composition, Memory/SQLite storage and shared state contracts in the CI job |
| Prove violations are detected for the intended reason | Healthy/fault controls identify the actual rule and path; real CLI returns a failing process status; syntax/configuration errors remain errors |
| Add usable Jev diagnostics | Existing TypeSafe adapter, real HTTP fixture tests, local command and CI invocation with explicit unavailable/error/incomplete evidence outcomes |
| Retain reviewable evidence | Source, contract and checker hashes, commit hint, coverage, concrete findings, separate diagnostic results and JUnit in `architecture-evidence` |

## Local use

```bash
python3 -m scripts.architecture --output .xorchestrate/architecture.json
python3 -m scripts.architecture --jev-config contracts/hexagonal-jev.json --output .xorchestrate/architecture-with-diagnostics.json
```

Both commands enforce the deterministic result through their process exit status.
Use `--root` and `--profile` to inspect another project with its own role mapping.
No package names are embedded in the evaluator. A profile declares source roots,
module selectors, allowed roles/external dependencies, permitted dynamic loading
and optional prohibited calls. Every source module must match exactly one role;
every required selector must match. New adapters matching the declared adapter
scope are accepted. New top-level modules need a role assignment; legitimate new
dependencies can be added to the applicable contract.

The Xorchestrate profile permits adapters to depend on other adapters where the
current design composes them. Composition may load trusted configured factories.
The core and application-owned ports cannot depend on those concrete layers.
Runtime calls through injected ports remain legitimate dependency inversion.

The extractor adapts the existing verification boundary traversal. It reads source
without importing it, includes package initializer edges and checks transitive
paths under the originating role's policy. The former application blacklist now
uses this shared declared contract. Existing verification-specific semantic guards
remain in their own tests; they have not been discarded.

## Jev diagnostics

`contracts/hexagonal-jev.json` supplies the endpoint, pinned model and source budget.
It uses the existing `TypeSafeEvaluator`; this work adds no new transport or model
dependency to the application core. Set `TYPESAFE_API_KEY` locally. CI reads the
repository secret `TYPESAFE_API_KEY` (or `JEV_API_KEY` as fallback). Optional
`JEV_ENDPOINT` and `JEV_MODEL` environment variables/repository variables override
the profile's deployment values.

Three typed assessments examine semantic boundary leaks, concrete coupling in
ports, and coverage/composition gaps. Inputs include the declared contract,
deterministic findings and bounded source excerpts. Selected source hashes must
still match the deterministic inventory. Omitted and truncated files remain
explicit. The existing TypeSafe protocol returns choices and distributions, not
freeform explanations: reports retain each question, outcome, confidence, model,
usage when supplied, and the exact source scope/context hash.

No configuration is `not_configured`; absent credentials or an unreachable service
is `unavailable`; malformed responses/configuration are `error`; changed or missing
source evidence is `inconclusive`. None means Jev found no concern. A completed
diagnostic only covers the supplied scope. Live provider output and HTTP fixture
results are distinct evidence.

CI runs diagnostics even after a deterministic failure. Its explicit
`--diagnostics-only` invocation does not determine the separate blocking gate's
exit status. The deterministic and behavioral steps have no continue-on-error.
Reports are uploaded after success or failure. GitHub's repository ruleset controls
whether this job is also mandatory for merging; a workflow cannot grant itself
that administration setting.

## Limits and extension

These are static Python dependency and named-operation checks, not a hostile-code
sandbox or proof of all Python semantics. External package internals are not
traversed. Behavioral contract tests establish their specified scenarios;
composition tests exercise actual configured consumers. Runtime observations and
Jev diagnostics do not establish behavior for unobserved paths.

Additional profiles can reuse the engine and result format. Additional languages
need extraction adapters that preserve their own relationship semantics. A new
finding is evaluated against the owner's current requirements before changing
policy; there is no automatic historical baseline or newly mandatory rule.
