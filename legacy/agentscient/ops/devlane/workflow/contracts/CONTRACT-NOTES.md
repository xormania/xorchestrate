# Workflow contract-author notes

## Authority and counting

`PLAN.md` is the only authority for semantic values. `APPROACH.md` sections 4
and 5 supply only the comparison envelope and neutral carrier fields. The CUE
comments cite the nearest PLAN interface lines for every constraint; when a
constraint exists solely to carry the neutral shape, its comment says
`inferred` and identifies that fact explicitly.

Clause counts below count independently rejectable semantic rule groups, not
the required `facts`/`provenance` envelope, carrier field types, helper aliases,
or repeated uses of the same rule. “Illustrative” means a form adopted from the
block explicitly labeled Illustrative at PLAN.md lines 333–348. “Inferred”
means a checkable reading needed to map prose onto the neutral observation.

| contract | stated | illustrative | inferred |
|:--|--:|--:|--:|
| schema | 12 | 0 | 2 |
| db | 13 | 0 | 1 |
| gatekinds | 12 | 8 | 1 |
| meta | 2 | 0 | 1 |
| layout | 17 | 0 | 2 |
| canonical | 3 | 0 | 2 |
| exitcodes | 1 | 0 | 0 |

## Closed and open declarations

- `schema`: `#Schema`, its `facts`, `#SchemaTable`, and `#SchemaColumn` are
  closed. The exact nine-table list is closed. The column lists for
  `attempts`, `wo_state`, and `schema_migrations` remain open in length because
  PLAN.md lines 102, 107–108 do not give complete field forms. `provenance` is
  intentionally unconstrained.
- `db`: `#Db`, its `facts`, `#DbTable`, `#DbColumn`, and `#DbGateKind` are
  closed. `gate_kinds` is open in length beyond required `tdd` and `artifact`
  rows because PLAN.md lines 350–367 permit new kinds and versions.
  `schema_migrations` is open in length and string contents. `provenance` is
  unconstrained.
- `gatekinds`: `#GateKinds`, its `facts`, `#GateKindFile`, `#GateKindSpec`,
  `#GateStage`, and `#GateCheck` are closed. `#GateKindSpec` relies on CUE's
  definition closedness rather than a second `close()` wrapper. Outcome labels
  are pattern-open data through `#GateOutcomeMap`; artifact labels are narrowed
  to the six stated values. The kinds list, stage names, check classes, globs,
  and argv values remain extensible. `provenance` is unconstrained.
- `meta`: `#Meta` and its `facts` are closed. `raw` is deliberately `_` because
  PLAN.md lines 361–364 name a format version but give it no field form.
  `provenance` is unconstrained.
- `layout`: `#Layout`, its `facts`, and `#GitignoreDbLine` are closed. The
  `files`, `dirs`, and `gitignore` lists are open in length because PLAN.md
  lines 350–367 and 565–570 permit growth and sibling modules. `provenance` is
  unconstrained.
- `canonical`: `#Canonical`, its `facts`, and `#CanonicalCall` are closed.
  `keywords` is label-open because PLAN.md lines 52 and 574–575 constrain two
  keyword values but do not forbid explicit default-valued keywords.
  `provenance` is unconstrained.
- `exitcodes`: `#ExitCodes`, its `facts`, and `#ExitCodeConstant` are closed.
  The representation lists are open in length; only the documented union is
  exact. `provenance` is unconstrained.

## Illustrations and sketches

- PLAN.md lines 333–348 are explicitly illustrative. The contracts use the
  shown JSON spellings and container forms for `kind`, integer `version`,
  `stages`, `name`, optional `leave_requires`, optional `outcomes`, optional
  stage-level `actor`, `test_scope`, `harness_scope`, and `checks` with
  `name`/`class`/`argv`. The literal version `1`, TDD stage names, globs, check
  names/classes/argv, and outcome-to-effect assignments are example values and
  are not pinned.
- The `artifact` stage order and outcome vocabulary come instead from stated
  prose at PLAN.md lines 181–187. Outcome placement and most individual effect
  mappings are not supplied and are not invented.
- The `wf next --json` block at PLAN.md lines 276–283 is a sketch. It is not
  used by any of the seven requested contracts.
- The layout tree at PLAN.md lines 468–479, canonical recipe at lines 52 and
  574–575, and exit-code map at lines 271–274 are not labeled illustrative or
  sketch and are treated as stated rules. Although PLAN.md lines 560–561 call
  section 13 a build order and bar, APPROACH.md expressly assigns its layout
  and canonical clauses here. Requiring `0001_foundation.sql` in `schema` is a
  separate PLAN-only reading of the ratified build item at line 576.

## Ambiguities and readings chosen

### schema

- PLAN.md lines 98–108 list six tables with comma-separated code-form column
  names, but never say “and no other columns.” Those six lists are treated as
  exhaustive sets so a documented schema can disagree with an observation.
  Declaration order is not constrained because PLAN gives none.
- PLAN.md lines 91–108 give no SQL types or nullability. They are only checked
  as neutral carrier types. Only `claims.wo_id` is pinned as a primary key
  (lines 105 and 55); every other primary-key choice is left open.
- PLAN.md line 107 and line 56 name `record_kind`, `exhaustiveness`, and
  `agent`. “Before/after bindings” and “output tails + hashes” have no field
  spellings, so only the three code-form names are required and the rest of
  `attempts.columns` stays open.
- PLAN.md lines 102 and 108 describe `wo_state` projection data and migration
  bookkeeping only in prose. Their column names remain unconstrained.
- PLAN.md lines 361–364 say numbered forward-only SQL, lines 472 and 576 show
  `00NN_*.sql` and `0001_foundation.sql`, but no grammar is formalized. Chosen:
  four decimal digits, underscore, a possibly empty non-slash suffix, and `.sql`; the
  foundation migration is mandatory.

### db

- PLAN.md lines 453–479 describe a local rebuildable `wf.db`; APPROACH makes
  the observation optional. Chosen: when this contract is vetted,
  `present` must be true; absence is a skip outside CUE.
- PLAN.md's only concrete version form is integer `1` in the illustration at
  lines 333–348. The DB carrier deliberately permits `int|string`, and this
  contract leaves storage representation open.
- PLAN.md lines 354–360 name one `spec_sha256` but do not say whether it hashes
  raw file bytes or canonicalized JSON. Chosen reading: raw bytes are the pin,
  because the tracked file is source of truth and in-place edits are said to be
  detectable. The CUE shape cannot operationalize that choice: both digest
  carrier fields remain strings, and digest encoding is unpinned.
- PLAN.md lines 361–364 do not say whether `schema_migrations` stores `0001`,
  a stem, or a filename. Values and current cardinality remain unconstrained.
- PLAN.md gives no current work-order count. `work_orders_count` is constrained
  only as the neutral `int` carrier; SCOPE.md's snapshot count is not imported
  into the PLAN-only contract.
- APPROACH.md says DB registry equality with files should be required, but
  `db.json` and `gatekinds.json` are vetted separately and neither contains the
  other's rows. There is no common CUE value in which to express that equality.

### gatekinds

- PLAN.md lines 328–330 specify `gate-kinds/<kind>.json`, but APPROACH does not
  define whether `facts.kinds[].file` is a basename or app-relative path.
  `file` is therefore left as a string rather than choosing either spelling.
- PLAN.md lines 354–360 have the same raw-versus-canonical digest ambiguity as
  `db`. Raw is the declared reading; neither digest value nor encoding is
  pinned because PLAN supplies none.
- PLAN.md lines 130–137 require test and harness scope semantically, while the
  `harness_scope` JSON spelling appears only at lines 343–344. The spelling and
  list form are adopted as illustrative form; concrete globs stay open.
- PLAN.md lines 337–342 alternate `leave_requires` and `outcomes`, with `actor`
  on one illustrated stage. Chosen: `name` is required; the other three stage
  fields are optional; no exactly-one rule is invented. If `actor` is present,
  `owner` is the only value PLAN names (lines 188–193), but no stage is required
  to carry it.
- PLAN.md lines 181–187 give the artifact stage sequence and six outcome
  values but do not place every value on a stage or map every value to an
  effect. The contract requires the six values collectively, rejects additional
  artifact values, and leaves their placement open. Requiring every named value
  to occur is the one inferred gate-kind semantic clause.
- Effects are limited to `advance`, `stay`, `terminal`, or an actual stage name
  in that same spec, per PLAN.md lines 185–187. Check-class names, leave-token
  strings, globs, and argv contents remain open extension data.

### meta

- PLAN.md lines 361–364 promise only that `meta.json` carries a format version.
  They give no key, value, type, nesting, bytes, or enclosing JSON form. `raw`
  is left unconstrained and `bytes` has no pinned value.
- Mapping that promise to nonempty top-level keys matching `/version/i` is an
  inference forced by APPROACH's neutral `version_keys` observation. If the
  version is nested or named without “version,” the mismatch is an ambiguity,
  not evidence that a different undocumented shape should be invented here.

### layout

- PLAN.md lines 468–479 show `wf.db`, while lines 453–454 and 538–539 call it a
  gitignored, rebuildable local artifact. Chosen: its presence is optional; if
  present, at least one observed db-related ignore line is required.
- PLAN.md never names a `.gitignore` location or literal rule. The contract does
  not invent `wf.db`, `*.db`, or a path; the nonempty observation is only a weak
  proxy and the extractor's carrier already selects lines containing `db`.
- PLAN.md lines 475–477 do not say whether `chains`, `attempts`, and `objects`
  are lazy-created. Chosen: require the named directories because the block is
  titled as the tracking layout, but require no dynamic `<wo>`, `<aa>`, or
  `<digest>` file.
- Migration naming uses the four-digit reading described under `schema`.
  Gate-kind files are required to be nonempty in number and to match
  `gate-kinds/<kind>.json`; additional files, directories, modules, migrations,
  and kinds remain allowed.

### canonical

- PLAN.md lines 52 and 574–575 prescribe byte identity, while the observation
  reports syntax only. Chosen: every reported `json.dumps` call must carry
  `sort_keys: true` and `separators: [",", ":"]`; zero reported calls remains
  structurally possible and surfaces the static-observation limitation.
- Extra keywords are allowed. PLAN does not forbid them, and an explicit
  default can preserve byte identity while another value can break it; the
  AST carrier cannot decide that semantic question.
- “sha256 helpers” at PLAN.md lines 574–575 does not literally say “no other
  algorithm,” but the chain discipline uses SHA-256 throughout lines 73–80.
  Chosen: the sorted unique algorithm observation is exactly `["sha256"]`.

### exitcodes

- PLAN.md lines 271–274 give the exact five-code map but no constant names,
  files, or required static representation. `all_codes` is exact. The other two
  lists may be empty and, when populated, may contain only mapped values; no
  code-to-verb or code-to-meaning association is invented.

## Positive-control feasibility

Written controls:

- `layout.json` uses only the layout tree, named Slice 1 modules, foundation
  migration, and seeded gate-kind names at PLAN.md lines 468–479, 513–519, and
  565–576. It omits optional `wf.db`, so no undocumented ignore literal is
  needed.
- `canonical.json` uses the stated SHA-256 algorithm and an empty call list.
  PLAN gives the keyword recipe but no valid `canonical.py` source-line value;
  using PLAN line 574 as an implementation line would fabricate provenance.
  The control is therefore valid but necessarily vacuous for call keywords.
- `exitcodes.json` uses only the five values at PLAN.md lines 271–274.

Not written, because no conforming JSON observation can be built only from
PLAN.md:

- `schema.json`: APPROACH's shape requires every documented column to have
  concrete `type`, `notnull`, and `pk`, and every table to have
  `primary_key`. PLAN.md lines 91–108 supply none of those values except
  `claims.wo_id` being PK.
- `db.json`: it has the same schema gaps and additionally requires four concrete
  digest strings across the two seeded rows, stored migration values, and a current work-order count.
  PLAN.md lines 328–364 supply none.
- `gatekinds.json`: PLAN.md lines 333–348 give no digest and only an explicitly
  illustrative TDD spec. A complete required `artifact` spec—scopes, checks,
  leave requirements, outcome placement, and mappings—is absent from lines
  179–187.
- `meta.json`: PLAN.md lines 361–364 give no bytes, key, value, type, or raw
  JSON. A passing value would necessarily invent at least one of them.

This is a blocking positive-control gap, not a reason to weaken the contracts
or manufacture placeholder data.

## Structurally unpinned promises

- `schema`/`db`: SQL types and nullability as requirements; foreign keys,
  indexes, defaults, uniqueness, CHECK constraints; migration order,
  transactionality, forward-only application, and immutability; append-only
  behavior and the chain's authority.
- `db`/`gatekinds`: equality between DB registry rows and tracked files;
  digest computation, encoding, raw-versus-canonical authority, CAS content,
  work-order pin equality, old-version immutability, and refusal on mismatch.
- `gatekinds`: actual file tracking, untouched parsed JSON, sort/uniqueness,
  runtime advance preconditions, effect application, delayed owner effects,
  command execution, glob meaning, scope adequacy, marker behavior, and the
  zero-engine-change promise.
- `meta`: byte-count truth, raw/source-byte correspondence, truthful parse
  status, derivation and sorting of `version_keys`, the exact version key/value,
  and rebuild/import behavior.
- `layout`: whether an observed ignore line actually ignores `wf.db`, git
  tracked status, stdlib-only/small-module properties, rebuild behavior,
  canonical JSONL bytes, append-only files, CAS paths/content, and dynamic path
  semantics.
- `canonical`: actual byte identity for arbitrary objects, which call
  implements `dumps()`, behavior of extra kwargs, encoding to bytes, input to
  SHA-256, digest encoding/use, omission of an event's own hash field, and exact
  stored payload bytes.
- `exitcodes`: runtime reachability and meanings, the first-failed-precondition
  rule, refusal versus integrity classification, claim behavior, and which verb
  returns which code.
- Across all seven: provenance truth, deterministic sorting, and the extractor's
  faithful derivation of its neutral facts are outside these one-file CUE
  structural checks.
# Contract notes

## Authority and clause accounting

`PLAN.md` is the only semantic authority. `APPROACH.md` supplies only the
comparison containers and CUE layout. The `S`, `X`, and `I` identifiers in
the CUE comments classify semantic clauses as stated, illustrative/sketch,
or inferred. Carrier typing, closedness, and repeated applications of one
semantic rule are not counted as separate clauses.

The clause totals are:

| contract | stated | illustrative | inferred |
|:--|--:|--:|--:|
| `cli` | 10 | 1 | 2 |
| `events` | 6 | 0 | 0 |
| `commitmsg` | 7 | 0 | 0 |
| `mcp` | 3 | 0 | 0 |
| `vocab` | 12 | 0 | 0 |

For `cli`, the stated groups are the exact verb set (`CLI-S01`), documented
per-verb arguments (`CLI-S02`), effective JSON mode everywhere (`CLI-S03`),
the chain-appending agent rule (`CLI-S04`), `WF_AGENT` (`CLI-S05`),
`--no-commit` (`CLI-S06`), `--source` (`CLI-S07`), dirty-receipt opt-out
(`CLI-S08`), claim/release identity (`CLI-S09`), and marker choices
(`CLI-S10`). `CLI-X01` is the one sketch clause. `CLI-I01` and `CLI-I02` are
the two argparse encodings inferred from stated behavior.

For `events`, the groups are all nine required event type literals
(`EVT-S01`), the documented-type set used to recognize shapes (`EVT-S02`),
common event keys (`EVT-S03`), exact code-font receipt keys (`EVT-S04`), `test_manifest`
(`EVT-S05`), and `commit.landed.sha` (`EVT-S06`). `commitmsg` has five
trailer clauses, one subject-format clause, and one generated-source clause.
`mcp` has one clause for each of `status`, `next`, and `log`. `vocab` has one
clause for each of the twelve reserved terms.

PLAN's gate-kind block at lines 333-348 is expressly illustrative but is not
used by these five requested contracts. The `wf next --json` block at lines
276-283 is expressly a sketch; its seven keys are required as a minimum and
extra keys remain allowed. The commit-message block at lines 305-315 is
headed “Message format,” not “illustrative” or “sketch”; its structure and
keys are treated as stated, while its concrete WO, stage, event number/hash,
and subject tail are fixture operands rather than universal constants.

## Closed and open declarations

- `#Cli` and `#CliFacts` are closed CUE definitions, so unknown observation
  fields fail and every carrier field is required. `#GlobalArgument`,
  `#GlobalJsonArgument`, `#FlagArgument`, `#PositionalArgument`,
  `#MarkerChoiceArgument`, `#CommandArgument`, `#Unresolved`, and `#Command`
  are closed definitions because their visible fields are fixed by the
  neutral observation shape. The command list is closed and ordered because
  PLAN lines 238-259 say one line per command and the observation is sorted.
  Each command's argument list is closed to the documented grammar; aliases,
  extra arguments, and duplicate argument identifiers fail. Global arguments
  are exactly empty or the single documented `--json` argument.
  `helpers_applied`, `env_literals`, and the `next` key list are open in
  length/content where PLAN does not enumerate values; the seven `next` keys
  are required and duplicate keys fail. `provenance` is intentionally
  open/free-form.
- `#EventTypeObservation`, `#EventShapeObservation`, `#EventFacts`, and
  `#Events` are closed definitions, with all carrier fields required. The `types`,
  `shapes`, `files`, and `keys` lists remain open to additions: PLAN lines
  366-367 expressly allow new event types, and PLAN names type-specific
  fields incompletely. Shapes for documented types are conditionally closed
  as carrier objects but accept extra key strings. `provenance` is open.
- `#CommitMsgFacts` and `#CommitMsg` are closed, required-field definitions. Their
  three lists are open because PLAN requires the documented message parts
  but never prohibits additional subjects, literals, or trailer keys.
  `provenance` is open.
- `#McpFacts` and `#Mcp` are closed, required-field definitions. All four
  lists are open because PLAN lines 483-488 describe a later seam and do not
  enumerate imports, launch dependencies, or URI forms. Only the three
  served verb literals are required. `provenance` is open.
- `#VocabFacts` and `#Vocab` are closed, required-field definitions. `literals`
  is open because the checker necessarily may contain unrelated strings;
  the twelve documented substrings are required. `joined` is required to be
  the neutral observation's newline join of `literals`; this is carrier
  consistency, not an additional semantic clause. `provenance` is open.

Hidden CUE fields are validation witnesses only. They are not observation
fields and do not weaken the closed visible definitions.

## Positive-control status

- `cli.json` carries the sixteen command forms from PLAN lines 243-259, the
  common flags from lines 261-265 and 289-303, D3 from lines 542-543, and the
  seven keys in the `next` sketch. A fully PLAN-only positive control cannot
  populate every required APPROACH.md §5.6 AST carrier: PLAN does not state
  helper application, global-versus-local placement, or most `action`, `nargs`,
  `choices`, and `required` values. The file deliberately omits those unknown
  carriers rather than inventing them, so it documents the PLAN-derived
  portion but cannot vet clean. The `append` and `?` mappings remain the two
  declared contract inferences; using them still cannot populate the other
  unknown fields. The CLI positive control is blocked.
- `events.json` carries all nine event literals in PLAN's chain-resident
  table, including nondotted `outcome`. PLAN gives no implementation `file`
  or `line` for a shape and no source-file lists for the literals, so a full
  APPROACH.md §5.9 positive observation cannot be built solely from the
  document. The file deliberately omits those carriers rather than inventing
  locations or absence, so it cannot vet clean. The Events positive control
  is blocked.
- `commitmsg.json` copies every non-carrier value from PLAN lines 307-315.
- `mcp.json` controls only the documented `log`, `next`, and `status`
  literals. PLAN supplies no concrete server/launch import basename and no
  URI literal, while APPROACH.md §5.11 requires all three carrier lists.
  The file deliberately omits them: empty lists would make invented absence
  claims, and no wildcard JSON value both satisfies the shape and remains
  noncommittal. The MCP positive control is blocked.
- `vocab.json` uses exactly the twelve terms at PLAN lines 501-502, sorted as
  required by the neutral observation and joined with its documented neutral
  separator.

## Ambiguities and choices

### CLI

- PLAN line 246 spells `open <wo>`, while D6 at line 548 says IDs are
  allocated by `open`. The contract follows the explicit public grammar and
  requires the `wo` positional; allocation behavior remains unresolved.
- PLAN line 250 requires `check <wo> <check-name>`, while the later growth
  example at line 437 invokes `check --verify-only <check>` without a work
  order. The ratified toolset grammar is used; the later example is not
  used to rename or remove those positionals.
- PLAN lines 248-250 show `<cmd...>` and `<check-name>` but do not state a
  separate argparse destination spelling. The contract takes the displayed
  tokens `cmd` and `check-name` literally. An underscore-normalized or
  renamed destination is a documentation/observation disagreement, not a
  field name guessed from code.
- PLAN line 252 supplies the marker value form `open|close` but no positional
  destination name. The contract requires the two choices and the separate
  `id`, but deliberately does not require a name for the choice-bearing
  positional. The positive fixture uses the verbatim token `open|close` only
  as an unconstrained carrier value.
- PLAN lines 254-257 use brackets for optional work-order positionals, and
  line 565 fixes argparse. The contract maps that to `nargs: "?"`
  (`CLI-I02`). This is an inference; PLAN never spells the AST keyword.
- PLAN lines 299-301 say `--source` is repeatable. The contract maps that to
  argparse `action: "append"` (`CLI-I01`). Other equivalent parser
  implementations would disagree and require adjudication.
- D3 at lines 542-543 names dirty-tree receipts. With receipt definitions at
  lines 125-128 and 171-172, `--allow-dirty` is required on `red`, `green`,
  and `check`, not on every appending verb. PLAN does not specify wider scope.
- PLAN lines 289-300 explicitly enumerate appending verbs and omit `commit`;
  lines 584-585 more generally mention agent/source flow-through in `gitio`.
  The contract follows the explicit list and the line-257 command grammar,
  so it does not require those common append flags on `commit`.
- PLAN lines 264-265 require JSON mode everywhere but do not choose root
  parser versus subparser placement. The contract accepts either no global
  arguments plus `--json` on every command, or one global `--json` argument;
  local JSON may coexist with the latter. No other global argument or alias is
  documented, so it surfaces as an extra rather than changing the branch.
- PLAN lines 261-264 permit either `--agent` or `$WF_AGENT` on appending
  commands. The contract requires the observed subparser option to have
  `required: false|null`; `required: true` would make the CLI spelling
  mandatory and defeat the environment alternative. Claim/release keep
  their `required` metadata unconstrained because line 247 shows an
  unbracketed flag and does not state the same fallback.
- PLAN lines 276-283 call the next object a sketch. The seven shown keys are
  a minimum, not an exact key set. An unresolved extractor value fails rather
  than making this clause vacuous.
- PLAN lines 513-529 phase some verbs later, but lines 238-259 ratify the full
  interface and say one line per command. The full sixteen-verb set is
  contracted; a partial slice is a mismatch rather than a weakened contract.
- For non-`next` commands, `next_json_keys: "n/a"` is the neutral observation
  encoding, not a separate PLAN promise. No helper names are constrained
  because PLAN names none.

### Events

- PLAN line 115 names the nondotted event type `outcome`, but the neutral
  `types` extractor rule admits only dotted literals. PLAN is authoritative,
  so the contract requires the exact nondotted value rather than inventing a
  dotted spelling or weakening the clause. The positive control can carry
  that doc-derived value; a mechanically extracted observation cannot, so a
  clean mismatch here is an extraction-shape/comparability finding that must
  be adjudicated.
- PLAN lines 73-83 define runtime event fields, not a required Python AST
  construction form. Shape presence is not required because the neutral
  extractor's constant-dict discovery is expressly a GUESS; every shape it
  does report for a documented type is constrained conditionally. An
  unresolved extraction must fail the required list carrier rather than be
  represented as an empty list.
- PLAN line 125 names “stage” and “check name” in prose, and lines 126-128
  name short tails only in prose. Those fields have no exact form and are
  left unconstrained. Only the backticked receipt field names are required.
- PLAN lines 137 and 155-156 mention the toolchain string, bound-red sequence,
  and frozen-set hash without exact field forms. They are left unconstrained.
- PLAN lines 145-160 say the frozen hash is sealed in `stage.entered` but do
  not name its field. No key spelling is invented.
- PLAN lines 205-206 use `seq` and `hash` both for the event envelope and the
  landed chain head without naming a nested form. The contract requires the
  envelope keys and `sha`, not invented second head-binding keys.
- PLAN line 121 says artifact records carry a digest and repo path, and line
  119 describes marker records, but it gives no exact payload field forms.
  Those type-specific keys are left unconstrained.
- PLAN lines 354-356 describe a chain opening event but never name its type.
  The type and its payload association are left unconstrained.
- PLAN line 58 names optional correction field `supersedes_seq`; a static
  shapes list cannot prove that an optional field is accepted at runtime, so
  its presence is not required on every event.

### Commit messages

- PLAN does not say the five trailer keys are exhaustive. They are required
  members and extras remain allowed.
- The message block at lines 305-315 is treated as a stated format. The CUE
  regex checks the visible `wf(<wo>/<stage>): <subject>` skeleton, not the
  concrete example operands.
- PLAN lines 299-301 allow additional source trailers but do not specify a
  count in each commit. The contract requires the `Source` key and the fixed
  generated-source literal, not a multiplicity.
- PLAN lines 314 and 317-319 plus D7 at lines 549-550 constrain attribution
  values, but the observation reports only trailer keys. The specific agent,
  model name, and vendor noreply address are left structurally unpinned.

### MCP

- PLAN lines 485-488 name conceptual “query” and “append” layers, not import
  basenames. PLAN's layout at lines 565-570 names `chain.py` as a mixed
  append/verify/project module and names no `query.py`. The contract does not
  turn either concept into an invented `query` or `chain` import constraint;
  `server_imports` is therefore typed but semantically unconstrained.
- “Serves status/next/log” at lines 485-486 does not say those are the only
  string literals in the server. They are required members; extra verb
  literals remain allowed.
- PLAN supplies no MCP URI scheme, concrete resource URI, launch import, or
  transport form. `uri_literals` and `launch_imports` remain unconstrained
  beyond their neutral types.

### Vocabulary

- PLAN lines 501-507 propose a grep for the reserved terms but do not specify
  case folding, token boundaries, or regex normalization. The contract uses
  exact, case-sensitive lowercase substrings because those are the literal
  terms PLAN supplies and the neutral observation is literal-based.
- Broad substrings such as `red`, `green`, and `wf` can occur inside unrelated
  words. The contract does not invent word-boundary behavior.
- PLAN does not define the neutral `joined` carrier, but the shared
  observation shape does: it must equal the newline join of `literals`.
  Semantic term checks still require each term in an individual literal;
  `joined` is not trusted as substitute evidence.

## Structurally unpinned promises

- CLI: runtime parsing semantics beyond the two declared argparse
  inferences; actual JSON validity/content except the statically visible
  `next` keys; no prompts; small outputs; environment fallback wiring;
  command-tail `--`/remainder behavior; `--to`/`--reason` co-requirement;
  auto-commit/deferral; dirty-tree refusal and recording; idempotency;
  compare-and-swap; and exit behavior.
- Events: value types and formats, six-digit RFC3339 time, monotonicity,
  canonical bytes, SHA-256 calculations, hash links, indexed-column checks,
  projection agreement, CAS behavior, receipt execution, manifest content,
  same-argv and frozen-set bindings, outcome effects/actors, correction
  acceptance, and events assembled dynamically outside literal dicts.
- Commit messages: trailer order and blank-line placement, trailer values,
  repeated-source count, rendered f-string/template results, per-runtime-
  commit universality, head-event binding values, and attribution identity.
- MCP: actual registration/routing, that resources return the corresponding
  JSON queries, absence of direct or indirect append calls, read-only runtime
  behavior, transport/launch behavior, URI correctness, response schemas,
  authentication, and network behavior.
- Vocabulary: that the check scans exactly the the House product design tree, honors
  the allowed `.dev`/root/commit/PR locations, is registered and executed,
  and applies the intended runtime matching semantics without false
  positives or false negatives.
