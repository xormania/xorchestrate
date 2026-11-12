# Hexagonal architecture enforcement

The deterministic architecture check in the `Static checks` CI job enforces the
current application contract; the `Hexagonal architecture` job only reports that
job's outcome under its existing required status name. Jev supplies separate diagnostics locally and in CI. A diagnostic result
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
# Review the current worktree's changes against an existing commit, with related source.
python3 -m scripts.architecture --jev-config contracts/hexagonal-jev.json --base-ref HEAD~1 --output .xorchestrate/architecture-changes.json
```

Both commands enforce the deterministic result through their process exit status.
Use `--root` and `--profile` to inspect another project with its own role mapping.
No package names are embedded in the evaluator. A profile declares source roots,
module selectors, allowed roles/external dependencies, permitted dynamic loading
and optional prohibited calls. Every source module must match exactly one role;
every required selector must match. New adapters matching the declared adapter
scope are accepted. The multiplexer adapter is its own role,
`terminal_multiplexer`: only composition may import it, and its name is a
forbidden literal in roles that never reach composition. New top-level modules need a role assignment; legitimate new
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

Three typed checks examine semantic boundary leaks, concrete coupling in ports,
and coverage/composition gaps for each selected candidate. `--base-ref` identifies
both sides of changed behavior through Git; an unavailable base is inconclusive rather
than silently reviewing unrelated source. JSON configuration may instead supply
`changed_ranges`: objects with `path`, `start_line` and `end_line`. Paths must
belong to the measured inventory. Changes outside that inventory remain explicit
exclusions. Without a change selection, inventory selection prioritizes configured
`source_paths`, deterministic findings, and constrained roles. The shipped profile
has no fixed source list and permits at most three candidates; the reusable default
is one. `max_candidates`, `max_files`, `max_source_bytes` and `checks` bound the work.
Optional `role_priorities` lists distinct roles from the architecture profile.
Selection takes one candidate from each changed role in that order before taking
a second from any role; unlisted roles follow. The shipped list puts application
roles before test scaffolding. This is selection policy, not a new architecture
constraint. Reports retain eligible and selected counts by role.

Candidates contain complete Python AST units, including decorators and complete
function/method bodies. Related imports, imported definitions, callers and test
callers come from the measured import inventory and source AST. Static reference
selection follows up to two caller/registration hops and three dependency hops,
including parametrized contract lists, fixture arguments and imported subjects.
Imports and lexical references identify possibilities; arbitrary dynamic dispatch,
fixture names selected at runtime and external dependency internals remain unknown.
They are static
relationships, not proof of execution or behavioral coverage. Candidate units get
the budget before related evidence. A modified candidate and its complete old unit
must fit together; a pair that cannot fit is omitted whole. `before_sources` holds
old units from the resolved, immutable Git base with file hashes and commit identity.
Removed functions and deleted files remain candidates when their path and role
belong to the current declared source scope. Old source is explicitly judged against
the supplied **current** contract, not an invented historical contract. Renames are
represented as deletion/addition; no semantic rename matching is claimed.
Current source stays in `sources`; `change` links each candidate's old/new unit IDs,
and each source's `support_for` records which candidates need it. The byte budget
counts both old and new source. Missing or excluded inputs are recorded explicitly;
an unreadable old unit never silently becomes a claim that the function was added.
A unit that cannot fit is omitted whole;
reports expose excluded units, oversize candidates, omitted files and the budget.
They never turn a clipped prefix into a complete function. No fitting candidate
is `inconclusive` and makes no provider call. Source bytes are revalidated against
the deterministic hashes before building the packet.

Each question names its `candidate_id`, path, complete line span, declared role
contract and check. Its predeclared claim receives `concern`, `no_concern` or
`insufficient_evidence`; omitted context should lead to abstention when necessary.
A concern is an unverified investigation candidate. Reviewers must inspect its
grounding and independently confirm it before counting a detected defect. The
existing TypeSafe protocol returns choices and distributions, not freeform
explanations. The report retains each claim/question, outcome, confidence, model,
usage when supplied, and `evidence_context`: the exact source packet with its
context hash. It does not invent a model rationale or claim the model cited lines.

No configuration is `not_configured`; absent credentials or an unreachable service
is `unavailable`; malformed responses/configuration are `error`; changed or missing
source evidence is `inconclusive`. None means Jev found no concern. A completed
diagnostic only covers the supplied scope. Live provider output and HTTP fixture
results are distinct evidence.

The independent `Jev advisory` workflow retains these assessments, their
probabilities, selected source and omission receipts after CI completes. The
consumer authenticates the tested checkout and records the separate trusted
collector revision. Currentness is checked before and after inference; a source
that advances is marked historical. Confidence and distributions remain model
outputs rather than calibrated probabilities of an actual defect.

Required validation never waits for advice. Failed tests can still supply
candidate-bound diagnostic evidence; setup/provider failure cannot turn the
required result green or red. See [independent CI advice](jev-ci-advice.md) for
trusted execution, reservations, output location and rollout behavior.

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
