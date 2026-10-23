# Zones of coverage — CDD, BDD, TDD and E2E wired to surfaces

Owner, 2026-08-31: "make sure we're using contract driven dev - bdd, tdd
and e2e - we need to not over regress - and setup zones of coverage."
Drafted on the conductor surface per the cdd-draft precedent; moving it
into `ops/process/` is the owner's placement call. CDD
(`ops/conductor/cdd-draft.md`) is the umbrella: the contract is the
specification, tests are its instruments, the contract arbitrates.

## The zone contract

A **zone** is a surface with a named oracle stack. Every zone row states:

| field | meaning |
|---|---|
| contract | where the spec lives (CUE, ratification, plan §) |
| BDD | the feature shelf (Given/When/Then), or NONE — a named gap |
| TDD | the unit suites, with their run command |
| E2E | the execution oracle that sees the real shape, and who runs it |
| regression floor | what must stay green for ANY change touching the zone |

Two standing rules make "not over regress" mechanical rather than moral:

1. **Detection power never decreases.** A change may not remove or weaken
   a discriminating fixture without naming it and its reason in the stage
   report (the U10 rule, generalized to every zone). The mutation gate
   (loopstrap's `mutation-check.sh` shape — still the highest-value
   unbuilt thing) is this rule's eventual enforcement; until it exists,
   the skeptic brief asks the question per pass.
2. **The merged tree is a zone of its own.** Four merged-reds tonight
   (U0+U1, #85 vocab, wall/AGENTS.md alignment, CI-shape pins) — all
   green in isolation, red together. The regression floor for ANY push to
   a PR branch is the suite run on the MERGE with origin/dev, not the
   branch tip. Framework U4 (the cross-unit gate) is this rule's landing;
   until then it is the conductor's push discipline, and it caught two of
   the four tonight only because it was applied by hand.

Sweep-style enforcement per zone, where a shelf exists: the
`test_bdd_sweep` pattern (walk the feature shelf from disk; every
scenario mapped or explicitly deferred; no list to fall behind). A zone
with no shelf says NONE — a visible gap outranks an imaginary cover.

## The map, as measured 2026-08-31

| zone | contract | BDD | TDD | E2E | regression floor |
|---|---|---|---|---|---|
| workflow app (`ops/devlane/workflow/`) | PLAN.md + contracts/cue/ | partial (vocab features) | 864 tests, 3 CI shards | CI itself on every PR | full suite on merged tree |
| dispatch/launcher (`ops/devlane/dispatch/`, pinned) | harness-fixes ratification (D-ENV/D-DIFF/D-SIG) | 52 scenarios (harness-research) — **shelf not yet swept by a test** | dispatch+task suites (241+) | the probes (a real tiny dispatch per scenario) — the cheapest decisive records we own | suites + one probe when a scenario's surface moves |
| framework (`ops/capabilities/`) | plan.cue, capabilities.cue, capability.cue + ratifications | **NONE — named gap**; U3's plan should open with scenarios for boot refusals | 152+ tests (plan/registry/anatomy) | `cue vet` gates in CI (registry-vet, anatomy) + the U0/U1 merged-red lesson feeding U4 | all agentscient suites + wall + both registered checks |
| infra/docker (`ops/devlane/infra/`) | docker-lane plan 3 + D-IMG/D-LINT decisions | feature shelf exists (BDD sweep precedent measured here) | 365 tests | **the daemon runs — the only oracle that ever saw the real shape** (12-run history); conductor-run until CI gains a daemon | non-daemon suite always; daemon run before un-draft/merge; **second-boot-on-existing-state is now a required E2E case** (the idempotence defect's coverage gap, currently covered by no test — named) |
| CI verification (`.github/workflows/`) | `ops/devlane/workflow/checks/CONTRACT-ci.md` | planted missing-work and disabled-gate cases | `test_ci_two_jobs.py` checks actual invocation; `test_lane_checks.py` executes the shared plan; `ops/testkit/tests/test_checks.py` owns execution and discovery invariants | GitHub runs the same full gate on every PR; standard JSON parsing replaces the private YAML parser | the full `ops/bin/suites.sh` plan and both checks named in `CHECK_NAMES` green on the current head |
| gitea-ci lane (exports, grammar) | gitea-ci-decisions.md A1–A5 | NONE — U-G3.A's fault corpus plays the role | **absent on `dev`** (measured 2026-09-14 at `f8acfa2f`: no `test_ci_contexts*` under any path; the 48 named here are on `lane/workflow`, ops/README.md §Declared absences) | parse-proof on the compose Gitea, both ways before GitHub changes (ND-6) | none on `dev` until the lane crosses; a named gap, not a cover |
| conductor tools (wrapper, meters, board) | dispatch-wrapper-spec, board CONTRACT.md | spec acceptance rows are the scenarios | 14 wrapper tests; board contract tests | `--dry-run` argv equality + a real launch observed | tool suites; a conf change re-runs dry-run set |
| contracts/CUE (all packages) | cue-aar-equality doctrine + U2-A3 | faults ARE the scenarios (a contract without firing faults enforces nothing) | fault corpora + `faults` runners | `cue vet` at the pinned version where the gate runs (the 0.14.1/0.17.1 skew is a standing caveat on every claim) | every enumerated refusal demonstrated (the undemonstrated gate refuses) |

## E2E's charter

The ratified e2e plan (`e2e/dev`, e532625: behaviour E2E for all of it,
context contracts, allowed sets, the fixture repo) is the vehicle for the
E2E column growing beyond conductor-run measurements; U-D4's claim (the
docker lane as a two-cycle unit under context contracts) is its first
measurement. This document's map is the input its planner was missing.

## Project wiring

Every Work record (project-charter.md D-PROJ-1) names its zones; a Work
item touching a zone inherits that zone's regression floor into its
acceptance without anyone recalling it. The named gaps above (framework
BDD shelf, dispatch shelf-sweep test, second-boot E2E case, the mutation
gate) are the first four Idea/Gap records the Project capability ingests.
