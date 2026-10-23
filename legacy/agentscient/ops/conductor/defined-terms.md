# Defined terms — how mature systems scope vocabulary

> *"well - lexicon is a loaded word here - because the law system is based
> on it - and this is a good lesson. We need to think this through - read up
> on docs about lexicon and sys design contracts"* — owner, 2026-08-31

The provocation (I-003): the idea about avoiding vocabulary collisions
(I-002) itself grabbed a colliding term. "Lexicon" is not neutral here —
the law system this corpus is patterned on *runs* on controlled
definitions; borrowing its foundation-word for our meta-machinery collides
with the analogy source itself. The lesson generalizes and three mature
traditions were read for it.

## What the traditions actually build

**None of them builds a global lexicon.** Each scopes vocabulary and
manages the seams:

- **Legislative drafting** scopes definitions to the enactment: a
  Definitions section applies "in this Act" — or narrower, per Part or per
  section, with separate interpretation sections for self-contained Parts.
  A jurisdiction-wide Interpretation Act supplies *defaults* that any
  statute may override. **Referential definitions** import a term from
  another enactment without restating it. The mechanism's own name is
  "Definitions", and it is scoped by construction.
- **Domain-driven design** scopes language to bounded contexts: inside
  one, the ubiquitous language is single-meaning and coherent; across
  contexts, the *same word deliberately means different things* (sales'
  customer is not shipping's customer), and that is managed at the seams
  with context maps — not fixed by a global dictionary.
- **ISO 704 / ISO 1087 terminology work** splits **concept** from
  **designation**: concepts are the stable things; terms are labels bound
  to concepts *within one special language/domain*. "A terminology" is the
  designation-set of one domain — plural by definition.

Convergence: vocabulary is governed **per scope**, collisions are judged
**per surface**, and cross-scope reuse is an explicit **import**, never an
ambient global.

## Prior art: looplaw already built this

`xormania/looplaw` (read 2026-08-31: `dev/lexicon-product.cue`,
`dev/lexicon-workshop.cue`, `dev/README.md`) implements the traditions'
convergence, and further — which is why "lexicon" is loaded: the word
already names *this mechanism, in that repo, with this anatomy*:

- **Two lexicons, not one** — product's words and workshop's words are
  separate scoped files (bounded contexts, in CUE), with
  `TestVocabulariesDoNotIntersect` enforcing disjointness as a *test* —
  the collision gate I-002 asked for, already running.
- **Per-entry `collision` field** — every entry names "the strongest
  outside prior, as the misreading to defuse". writ's entry would carry
  `collision: "write"` — the exact concern, as schema.
- **`not: [{misreading, write_instead}]`** — each misreading pairs with an
  executable redirect, ordered by danger; the refusal-with-remedy doctrine
  applied to vocabulary. Plus a `violation`/`rewrite` pair: one banned
  sentence shown only to teach its repair.
- **`inherited_dev`** — terms taken from an outside method are listed
  separately because "an inherited term is not one this project may
  redefine". Imports are declared and no-redefine, law's referential
  definition as data.
- **`reserved_future`** — terms referenced but deliberately not coined:
  "Coining any of these is a ratification, not a drafting act." No-coin
  zones exist before the term does.
- **`do_not_borrow`** — the reverse direction, learned at cost ("actor"
  was coined in the workshop and reached the ledger schema before
  anything caught it): product words the workshop must not take for its
  own concepts. Two properties tested; the discussed-vs-borrowed judgment
  honestly left to a reader.
- **Proportionality** — the workshop lexicon is deliberately short: "This
  vocabulary has one reader who self-corrects; the full
  collision-resistant anatomy would cost more than it prevents." Full
  anatomy where readers are many and stochastic; short form where one
  self-correcting reader.

Consequence for naming: "lexicon" is not merely rejected as our coinage —
it is **looplaw's term for looplaw's mechanism**, and under looplaw's own
`inherited_dev` rule the only honest uses are inherit-in-its-sense or
don't-use. Re-coining it locally would be the `do_not_borrow` failure,
one repo up.

## Prior art in this repo's own law (surveyed 2026-08-31)

The law systems already built here practice the same discipline, which
means D-LEX-1 extends ratified in-repo law rather than importing a novelty:

- **`#Refusal.name` is namespaced by ratified schema** — agentscient's
  `outcome.cue` requires a dotted name of two-plus segments
  (`^[a-z][a-z0-9-]*(\.[a-z][a-z0-9-]*)+$`). Name-namespacing is already
  law for refusals; Definitions extend the pattern to terms.
- **The four outcome classes flowed looplaw → in-repo law**: `#OutcomeClass`
  in `outcome.cue`, the doctor's `[Rejection|Denial|Abort|Finding]` output
  contract in `permission-surface.md`, and `on_fail` in the writ schema all
  speak the same ratified vocabulary — an adoption chain, not three
  coinages.
- **Contracts carry fault corpora** (agentscient's 11 anatomy faults, the
  board contract's 4) — the proving-red discipline a Definitions block
  would inherit: a defined term with no demonstrated misreading-refusal is
  an unproven definition.
- **The board's `decisions` source** tracks open questions awaiting the
  owner — the home where D-WO-1..3 and D-LEX-1 surface for ratification.
- `.dev/xor/` holds owner-side law work (law-mini-app-design,
  contract-synthesis, learnings-since-loopstrap) that is permission-walled
  from the conductor; this survey does not speak for it.

## What this lane already has

The **vocabulary wall** (`ops/devlane/workflow/checks/vocabulary_wall.py`,
AGENTS.md §dev-lane surface) is a live bounded-context boundary: dev-lane
terms (work order, gate, stage, …) exist only on dev-lane surfaces and
never in the House's design artifacts. One direction, word-boundary matched,
with the allowlist drift-pinned to the AGENTS.md sentence naming the same
surfaces. The generalization asked for by I-002 extends *this*, it does
not add a second system.

## The design that falls out

1. **Definitions blocks, per contract** (law's mechanism, law's name):
   a contract that coins or narrows a term carries a `Definitions`
   section; the term is defined *for that contract's scope*. A writ citing
   a contract imports its definitions (the referential definition). No
   contract-wide term exists without a home contract.
2. **Concept/term split** (ISO 704): if a registry exists, it registers
   concepts with stable ids; terms are per-scope bindings to concepts.
   Renames rebind a label; the concept id and its records survive.
3. **Collision detection at binding, per surface** (the I-002 gate,
   corrected): a new term within one edit of an existing term is a finding
   *only where both are bindable on the same surface*. writ/write collide
   on prose surfaces; `law.#Writ` collides with nothing in CUE — which is
   why derived surfaces already need no machinery (D-TASK-1's packages).
4. **Anti-corruption at analogy imports** (DDD): the law analogy is a
   context we import *from*. A term crossing from the source domain into
   our meta-machinery gets deliberately renamed or deliberately adopted —
   never absorbed ambiently. "Lexicon" failed this rule; "writ" passed it
   (adopted knowingly, flagged, ratified).

## Naming, run through its own test

| candidate     | collision analysis                                        | verdict |
|---------------|-----------------------------------------------------------|---------|
| lexicon       | looplaw's term for its own two-context mechanism          | inherit-only, never re-coin |
| glossary      | documentation term of art; names a listing, not machinery | weak    |
| terminology   | ISO's name for the *discipline*, not the artifact         | weak    |
| **Definitions** | law's name for exactly this mechanism, scoped by design | **adopt** for per-contract blocks |
| **vocabulary**  | already in-lane (vocabulary wall); extending, not coining | **keep** for the check layer |

Proposed vocabulary (eating the dogfood): contracts carry **Definitions**;
the **vocabulary wall** generalizes to read them; there is **no global
lexicon artifact at all** — which the three traditions each arrived at
independently.

## The ratification-pending home: the `law` mini-app

`.dev/xor/out/law-mini-app-design.md` (read 2026-08-31 with the owner's
permission; standing: PROPOSAL, nothing built or ratified) already
assigns all of this a home. Its trinity's **Lexicon leg** holds every
dev-lane mini-app's reserved terms, tiered CANON | REVIEW | QUALIFY |
BANNED, and — the move this doc's sketch lacked — **absorbs and
generates the vocabulary wall's allowlist from one source**, closing the
wall's own self-referential gap (allowlist and AGENTS.md sentence
maintained by hand in two places). Its CI gate already enumerates
"banned-tier lexicon term used outside its allowed surface" and the
wall extended over `law`'s own vocabulary as refusal conditions.

Consequences for this doc:

- **D-LEX-1 reframes from invention to reaffirmation.** Definitions
  blocks + a generalized wall = the law design's Lexicon leg, staged.
  If the owner ratifies law §5 step 0, D-LEX-1 collapses into it; until
  then it is the bridge.
- **The writ citation form needs one bridge note**: writs cite authority
  as `{path, ref}`; law's rule is *cite by contract id, never by file
  path or position* (the PLAN.md category-error lesson — ~1,340
  line-number citations across 239 files as the measured disease).
  Pre-law writs cite paths at pinned refs; once contracts carry
  `C-[A-Z0-9-]+` ids, `authority.contracts` cites ids.
- **The seven §6 questions remain the owner's**, unratified — name and
  wall status, scope boundary, stack, remainder placement, producer
  segregation bar, authority timing, receipt store. The D-TASK/D-WO/D-LEX
  batch and law's step 0 should be ratified together or in stated order,
  not separately by drift.

## For the ratification batch

- D-LEX-1 (name pending its own rules): contracts may carry Definitions
  blocks; writs import definitions referentially from the contracts they
  cite; the vocabulary wall learns to read Definitions and judge
  edit-distance collisions per surface.
- The Definitions entry anatomy **inherits looplaw's `#Entry`** — per the
  looplaw-findings verdict (mine doctrine, don't depend): `collision`
  naming the strongest outside prior, `not: [{misreading,
  write_instead}]`, exhaustive aliases, `reserved_future` no-coin zones,
  `inherited` no-redefine imports, tested disjointness between scopes,
  and the proportionality rule — full anatomy only where readers are many
  and stochastic. Expressible in our CUE; no Go dependency.
- Amends I-002's mechanism sketch (registry-first) to definitions-first;
  I-002's collision-gate intent survives unchanged.

## Sources

- martinfowler.com/bliki/BoundedContext.html — bounded contexts; same word,
  different meaning across contexts, on purpose
- justice.gc.ca Legistics "Definitions"; Commonwealth drafting manual ch. 9
  — definition scope ("in this Act", per-Part, per-section), referential
  definitions, Interpretation Acts as defaults
- ISO 704 / ISO 1087 — concept vs designation; a terminology is one
  domain's designation-set
