# Task as the pipeline composer

> *"pipelines is basically what i want Tasks to do — dynamic workflows with
> best practices baked in"* — owner, 2026-08-30

This is the design that follows, written to be ratified or rejected. It is not
implemented; the conductor does not hand-write code.

---

## 1. Task already owns the right things

`ops/devlane/task/` is not a new home for this — it is the existing one:

| file | what it owns |
|---|---|
| `jobs.json` | the catalogue: eight job specs |
| `envelope.py` | the result contract |
| `fileset.py` | what a job may touch |
| `run.py` | execution |
| `verify.py` | the check |
| `CONTRACT.md` | the whole thing, stated |

What it owns is **one dispatch**: its prompt, its role, its snapshot mode, its
constraints, its deliverable, and the shape of what comes back. What it does not
own is **the sequence of dispatches**, or **which sequence this work needs**.
Those live nowhere, which is why they live in the conductor's memory, which is
why the conductor ran the eight-dispatch pipeline against a five-line
permission file.

Three layers, of which only the first exists:

1. **Job** — one dispatch. *Exists.*
2. **Pipeline** — an ordered composition, with gates between stages and stop
   conditions inside them. *Missing.*
3. **Selector** — the map from declared properties of the work to a pipeline.
   *Missing.*

---

## 2. Today's measurement, as the argument

The same subject, two shapes:

| | full pipeline (tool-guard) | composed shape (guard syntax) |
|---|---|---|
| dispatches | 21 | **1** |
| measured cost | $62.26 | unpriced (codex), 2m29s wall |
| verdict basis | six skeptic passes, one false green | the installed loader itself |
| findings | 31 p1 across the lineage | 14, verdict `fail` on intent |

The short shape was not a smaller version of the long one. It was a different
shape, chosen because **an authoritative oracle already existed** — Claude
Code's own settings loader — and no test this repository could author would
outrank it. Sol went and read the grammar out of the installed build.

That choice was made by a conductor who happened to have written it down an
hour earlier. Nothing recorded it, nothing checked it, and nothing will
remember it next session. That is the gap Task should close.

---

## 3. The decisions, for ratification

### D-TASK-1 — A pipeline is declared, not coded

Pipelines live in CUE beside the rest of the system's contracts, not as control
flow inside `run.py`. **Premise:** the owner's standing direction that CUE
defines the law and all contracts align to it. A pipeline expressed as Python
control flow cannot be diffed, ratified, or held to a contract; a pipeline
expressed as data can be all three.

The gate vocabulary is closed and small. This is a composition language, not a
scripting language — the moment it can express anything, it can express a
pipeline nobody reviewed.

### D-TASK-2 — Selector inputs are declared; the shape is derived; both are recorded

The conductor answers the properties explicitly. The selector maps answers to a
shape. **Both the answers and the resulting shape land on the dispatch record.**

Inference is refused. A property the composer guessed would let a conductor's
hunch enter the record wearing the clothes of a measurement — and the whole
value of the record is that it distinguishes those.

This is also what makes short pipelines an *experiment* rather than a shortcut.
The question that settles the experiment — **did short shapes miss what long
shapes caught?** — is a query over recorded shapes, and is unanswerable today
because the shape was never written down.

The properties, from `pipelines.md`:

```
P1  authoritative oracle exists outside this repository?
P2  is the subject representable in a snapshot?
P3  does it change a contract?
P4  blast radius of being wrong
P5  is the defect class already known?
P6  is the question about the work, or about the world?
```

### D-TASK-3 — Stop conditions are first-class and read from measurement

A pipeline segment may be a loop whose exit is a predicate over `meters.db`,
not a fixed count. The measured one:

> stop at the first pass returning zero p1 at above-mean cost, and never accept
> a green from a pass that cost more than the pass that last found something.

**Premise:** the tool-guard skeptic chain found its unblocking defect at pass 3
for $4.50, then spent **$16.21** on passes 4–6 to reach a false green. A fixed
pass count cannot express that; a predicate over yield can. This is the part of
"dynamic" that pays for itself.

### D-TASK-4 — Every transition names a failure class

looplaw's four, never a generic error: **Rejection** (malformed), **Denial**
(well-formed and declined — a successful execution, recorded, never routed
through error handling), **Abort** (infrastructure), **Finding** (first-class,
never skipped). A transition that cannot say which class it is handling cannot
be reasoned about, and today's launcher has places where all four look alike.

### D-TASK-5 — Invariants are properties of every shape, not of the conductor

These are compiled into every pipeline and cannot be dropped by choosing a
shorter one. They are what "best practices baked in" means:

- **the ratification gate** — any pipeline touching a contract holds until a
  ratified decision exists with reason, premise and `ratified_by`;
- **the no-seeding check** — a brief carrying conductor findings, anomaly ids,
  record ids or an expected verdict is refused before it costs anything;
- **the escape clause, appended by the composer** — *if you find something
  outside these questions that would change the verdict, report it.* Appended,
  not required, because a rule the conductor remembers is a rule kept four
  times in five;
- **observer over report** — the stage that reads an authority is not optional
  because the pipeline is short. It is the entire reason a short pipeline is
  allowed to be short.

### D-TASK-6 — Ratification is a Denial, not a stage

It costs no dispatch and consumes no harness. The pipeline **holds**, and the
hold is recorded as a Denial in the D-TASK-4 sense — a well-formed thing
correctly declined, waiting on an authority that is not a harness. It is not an
error, it is not a stage, and it is not skippable at any size.

---

## 4. Where it sits, and what it is not

**Task is not Project.** Project owns *what needs doing and what is stuck* — the
gap lifecycle, `#WorkKind: fill | split`, decisions awaiting authority, unlanded
work. Task owns *how a thing gets done*. They meet at exactly one place:
**Project declares a gap; Task composes the pipeline that fills it.** Keeping
that seam clean is what stops either from becoming a general-purpose tracker.

**Ordering.** Stop conditions read measurement, so the composer is downstream of
`records` and `observe` — it lands with or after the `project` capability, not
before it. The job catalogue and precheck tools from `pipelines.md` §4 are
usable immediately and do not wait for any of this.

**Not ratified.** Six decisions, each with its premise. D-TASK-1 and D-TASK-2
are the load-bearing pair: declared-in-CUE and declared-inputs. If either is
rejected the rest reshapes around it.
