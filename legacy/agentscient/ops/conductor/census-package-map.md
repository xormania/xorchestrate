# Census-to-package map — what we have, mapped to where it lands

Current scope notice, owner 2026-09-05:
[AGENTS.md §Lane boundary and precedence](../../AGENTS.md#lane-boundary-and-precedence)
supersedes this draft's dev-lane twin/strangler retirement and app-dependent
Mate recommendations. The original census below is historical provenance,
not current operational policy. Application research belongs to
`docs/mapping.md`, scoped there to prod work; dev tooling evolves independently.

Standing: DRAFT — conductor surface per the coverage-zones precedent;
placement is the owner's call. Source of demand: owner, 2026-09-01,
in session: "i just want the what we have - but there - plus i need
to integrate AI Mate." Anchors: D-EST-1 (packages as uscient repos,
MinSpec the substrate — ratified), D-EST-2 (MinSpec approved for all
changes — ratified), I-019 (the six freights), I-066 (the migration
doctrine: preservation is defined by contracts, not implementations;
the strangler runs per conformance row; the wire admits any language).

Every figure below measured at dev `99334f7`, command inline. A
missing contract is written ABSENT — a named gap outranks an
imaginary cover, and per I-066 anything not yet gate-covered gets its
row before its rewrite.

## Instruments → Console twins (freight 3)

Conformance registry: 7 rows
(`grep -c '^    ("' ops/conductor/tests/test_instrument_conventions.py`);
13 test files (`git ls-files ops/conductor/tests | grep -c test_`).

| instrument | contract | conformance row | target shape |
|---|---|---|---|
| onboard.py | onboard-lever.md | yes | Console command + Mate resource (the brief served read-only) |
| decisions.py | decisions-register.md | yes | Console command |
| hand.py | handwork-ledger.md | yes | Console command |
| watch.py | watch-lever.md | yes | Console command |
| land.py | land-lever.md | yes | Console command |
| ci.py | ci-store.md | yes | Console command |
| branches.py | branch-census.md | yes | Console command |
| skills.py | ABSENT | no — recorded absence | contract + row BEFORE rewrite |
| co_doc.py | skill adapter only | no — recorded absence | contract + row BEFORE rewrite |

The strangler per row: Python original and PHP twin coexist behind
the same conformance row until the twin passes; the row re-aims; the
original retires (bounded coexistence, I-066).

## Apps → packages (freights 4, 5)

Test-file counts: `git ls-files "ops/devlane/<app>" | grep -c test_`.

| app | contract | test files | target shape |
|---|---|---|---|
| workflow | PLAN.md only — contract document ABSENT (pipeline.md's own named gap; 1,236 citations pin the plan) | 45 | symfony/workflow declarations generated from the genus CUE; gates as guard listeners; marking store writes the evidence chain |
| dispatch | CONTRACT.md | 19 | Process + Messenger; harness CLIs stay dispatched subprocesses on the wire — the boundary whole-hog does not touch |
| task | CONTRACT.md | 6 | Messenger messages + handlers |
| harness | ABSENT | 4 | the firewall package: manifest-staged snapshots, proved both directions (stage.py / isolation.py doctrine) |
| hooks | ABSENT | 14 | event listeners; git-native recording kept |
| telemetry | ABSENT | 12 | profiler integration + own stores |
| fixtures | ABSENT | 3 | aligns with MinSpec workbench-fixtures |

## Cross-cutting freights

- **wire** (freights 1–2 seam): contract in genesis (`docs/wire.md`);
  the standing first move — smallest surface, maximal doctrine, House
  twin-tests existing. Tiny library + conformance harness.
- **contracts**: 31 CUE schemas (`git ls-files '*.cue' | wc -l`),
  297 fault files (`git ls-files | grep -c fault`). Lands as the
  projector (CUE → generated config and Flex recipes) and the
  extractor (compiled container → comparable form) meeting in
  unification — I-066's "code graph to contract issue."
- **AI Mate integration**: symfony/ai-mate + Symfony bridge as the
  practiced-graph probe (container, profiler); the Agentscient Mate
  extension serves the dev-lane read surfaces (brief, registers,
  ledger, dispatch state) per the ruled MCP posture — context and
  diagnostics read-only, levers act. Superseded 2026-09-16 (D-MCP-1): MCP
  acts as well as reads. Pre-1.0: pinned, probed,
  consumed through the triangle.
- **capability registry**: 0 entries at `99334f7` — a genuine zero
  (`ops/capabilities/capabilities.json`). This census is its seed;
  the registry re-aims at packages as they exist.

## Named gaps (absent is not none)

1. skills.py and co_doc.py: no contract, no conformance row.
2. workflow app: no contract document — extraction from PLAN.md is
   discharge work under pipeline.md §"A plan is discharged".
3. harness, hooks, telemetry, fixtures: no CONTRACT.md.
4. The freight-6 fixture (House-names-in-framework-files) exists as
   doctrine, not yet as a check in any package skeleton.

## First moves, in order, each on the owner's word

1. The wire package (standing since I-019).
2. This map ratified or re-placed; the gap rows above become the
   census dividend's work items.
3. A per-row strangler order proposed from this map's rows.
