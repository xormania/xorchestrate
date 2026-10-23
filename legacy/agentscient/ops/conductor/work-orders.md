# Writs — the seam artifact, drafted for ratification

> *"my project idea seems like a good home — because there's always a bigger
> workflow at play. That way I can work with ideas to convert them into specs
> and workorders. Similar to law"* — owner, 2026-08-31

**Naming — ratified.** The artifact is the **writ** (*"writ it is - note it
in the batch"* — owner, 2026-08-31). The law term is adopted as our term:
"work order" remains `wf`'s execution object (`wf open`, with
claim/release/red/green/advance/outcome as its verbs), and the two relate
by reference — executing a writ opens wf work orders — never by shared
vocabulary. That resolves the reconciliation requirement recorded in
I-001. This file keeps its drafted name so I-001's pointer holds; it takes
the writ name when it lands.

**Flagged for review at ratification:** `writ` sits one letter from
`write`, and a stochastic system can slip between them (owner,
2026-08-31). Scope the risk by layer: on derived surfaces it is covered —
the composer emits the token from a schema constant and CUE's closed
structs refuse a `write:` key as an unknown field, a refusal, not a
drift (owner, same date: derivation makes the token arbitrary there).
The residual risk lives where freehand survives (§3): prose, briefs,
prompts, and readers — no validator sits between a harness's
pattern-completion and the word "write". Defenses on that layer: ids
carry the `W-` prefix, and the dev-lane vocabulary check can learn the
pair on prose surfaces — generalized in I-002 (namespaced lexicon). The same layer is where the name earns its keep —
"writ" carries issued-under-authority into every fresh read. If the
prose-layer defenses are judged thin at ratification, renaming goes to
the same batch.

`task-composer.md` already draws the seam: **Project declares a gap; Task
composes the pipeline that fills it.** The writ is the artifact that
crosses that seam — the only one. This doc gives it a format. It extends the
six D-TASK decisions; it does not reopen them.

---

## 1. The law analogy, taken literally

The owner's "similar to law" is not decoration — it assigns each artifact a
distinct legal character, and the format falls out of the characters:

| law            | here                    | character                                    |
|----------------|-------------------------|----------------------------------------------|
| petition       | **idea / gap**          | freeform; Project's intake; no format demands |
| statute        | **spec / contract**     | ratified, versioned; the authority            |
| writ           | **writ**                | issued *under* a statute, citing it; directs one execution |
| docket, case law | **records, meters.db** | receipts; precedent that tunes future shapes  |

Two consequences, both structural:

- **A writ without a statute is void.** A writ must cite the contract(s)
  that authorize it, at a pinned ref, and a validator refuses a writ whose
  cited scope does not exist at that ref. CDD stops being discipline and
  becomes construction.
- **Statutes are amended, never edited.** An issued writ is immutable; a
  change is a superseding writ citing the one it replaces. Records then
  attach to something that cannot drift under them.

## 2. Where it lives, what language it speaks

**Home: the project capability.** Project owns the bigger workflow — ideas,
gaps, decisions awaiting authority — so it owns the conversion chain
*idea → spec → writ*, and Task consumes the writ. Proposed:
`ops/devlane/project/writs/<writ-id>/writ.cue` plus that unit's briefs,
committed **before the first launch** — the writ file is declare-before-do
at unit level.

**Language: CUE, per D-TASK-1.** The schema is the law; each writ is an
instance that `cue vet`s against it; the launcher consumes `cue export`'s
deterministic JSON. (This supersedes the earlier JSON-first strawman — same
fields, one level of law above them.) The closed gate vocabulary from
D-TASK-1 applies: composition, not scripting.

## 3. Construction is derivation, not drafting

The standardized way to construct a writ is that nobody writes one freehand:

1. Project holds a gap (the idea, in whatever words it arrived in).
2. The spec exists and is ratified — else the writ **holds as a Denial**
   (D-TASK-6), costing nothing.
3. The conductor answers **P1–P6 explicitly** (D-TASK-2; inference refused).
4. The selector maps answers → shape; the composer emits the writ:
   stages from the shape, defaults from `jobs.json`, overrides recorded with
   their source, invariants compiled in (D-TASK-5) and undroppable.
5. `writ --check` re-derives every brief and compares digests — same writ,
   same catalogue, same contract ref ⇒ byte-identical briefs, provable by
   anyone, any session.

Freehand survives in exactly two places: the idea's prose and the contract's
content. Everything downstream is derived, diffable, and refusable.

## 4. The fields

```cue
writ: {
  id:      "U13-…"                 // citable, immutable once issued
  gap:     "project/gap/…"         // the petition this fills
  authority: {
    contracts: [{path: "ops/devlane/…/CONTRACT.md", ref: "…"}]  // the statutes
    // Bridge form. law-mini-app-design §2.3: cite by contract id, never
    // by path or position — once contracts carry C-* ids, this field
    // cites ids (defined-terms.md, "The ratification-pending home").
    decisions: ["D-TASK-2", …]     // ratifications relied on
    supersedes?: "U12-…"           // amendment chain
  }
  selector: {                      // D-TASK-2: answers AND derived shape
    answers: {P1: …, P2: …, P3: …, P4: …, P5: …, P6: …}
    shape:   "full" | "short-oracle" | …
  }
  stages: [{
    id: "tests", job: "author-tests"          // job from the catalogue
    harness: "grok", model: "…"               // routing default, overridable
    reads: ["contract"]                        // the firewall, as structure
    follows: [ … ]                             // the DAG
    caps: {timeout: 1800, source: "writ"}      // no session-resident params
    on_fail: "Finding" | "Abort" | …           // D-TASK-4: named classes
  }]
  acceptance: [{run: "python3 -m unittest …", exit: 0}]  // executable, measured
  land: {mode: "cherry-pick", trailers: ["Dispatch"]}
}
```

States, as law: `gap → drafted → ratified | held(Denial) → issued →
executing → settled → landed`, each transition naming its failure class.

## 5. Not ratified

Three decisions this doc adds to the D-TASK batch, smallest first:

- **D-WO-1** — the writ is Project's artifact, consumed by Task (the seam
  stays one artifact wide). Its name is settled above: writ, with "work
  order" left to `wf`'s execution object, related by reference.
- **D-WO-2** — writs are immutable once issued; amendment is supersession.
- **D-WO-3** — writ construction is derivation (§3); a hand-written stage
  list is refused the way an inferred selector answer is.

If D-TASK-1 or D-TASK-2 is rejected, this reshapes with them — the format is
those decisions serialized, plus the law lifecycle the owner named.
