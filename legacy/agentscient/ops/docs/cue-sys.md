---
name: cue-sys
description: What CUE turned out to be for in agent development — bounding and proving an agent's context, not validating its data. Lessons and inventions from one session, each with the incident behind it.
---

# CUE in agent development

Written 2026-08-23, from one session that built a contract-vetting
pipeline for the `workflow` mini-app and, in the process, broke in about
a dozen instructive ways. Every claim below has a measurement or an
incident behind it; where something is opinion it says so.

`cue` v0.17.1 throughout.

---

## 1. The thing CUE is actually for here

The obvious use is validating data. That is not where the value turned
out to be.

**CUE's value in agent work is that closure and unification give
guarantees about an agent's CONTEXT that prose and procedures cannot.**

Three properties do the work:

| property | what it buys |
|:--|:--|
| a closed struct rejects undeclared fields | extra context **cannot be added** |
| a required field must be present | missing context **cannot be omitted** |
| unification makes a conflict an error | a contradiction **fails to evaluate** rather than being resolved by judgement |

Every guard built by hand this session was a *denylist* — enumerate the
known bad, block it, hope the list is current. A closed definition is an
allowlist with a proof. That difference is the whole argument.

---

## 2. The independence pattern, and why CUE is the meeting point

The pipeline that produced real findings looked like this:

- **contract author** reads the specification, and *nothing else*. No
  modules, no SQL, no JSON, no database.
- **extractor author** reads the code, with the specification *removed
  from its snapshot*. Writes mechanical extractors that report what is
  actually there.
- both implement against a **neutral shape spec** written before either
  starts, which neither owns.
- `cue vet` compares.

Neither author is in a position to reconcile a disagreement quietly.
That is the point, and it is not achievable with one author however
careful: a person or model holding both halves will resolve a
discrepancy in passing and never mention it.

**Measured result.** Twelve contracts against twelve observations
produced **38 disagreements**. Four were confirmed against the tree by
hand:

- `wf.py` declares **17** CLI verbs where the spec documents 16 (`agents`)
- exit codes are `[0,1,2,3,64,70]` where the spec states `{0,1,2,3,64}`
- `outcome` is an event type with no dot, so the documented
  "dotted literal" rule structurally cannot see it — two facts inside one
  observation disagree
- a gate-kind registry row had drifted from its tracked file, putting
  BDD features outside the frozen test set

**The firewall must be proved, not intended.** Withholding is invisible:
a snapshot that leaked the wrong file looks exactly like one that did
not. Staging four roles by hand and checking afterwards with `find`
produced one role firewalled on one side where the plan specified both.
Build the snapshot from a manifest and refuse to dispatch one you cannot
prove.

**Prove it in both directions.** Nothing matching a withheld pattern
present, *and* something matching every given pattern present. An empty
snapshot satisfies the first perfectly; a role staged from a mistyped
path leaks nothing, contains nothing, and then answers questions about
an empty directory.

---

## 3. Closure as a context guarantee

The largest surface is the one you are standing on. Three mechanisms
guarded a role's context — harness configuration, staged files, the
inherited instruction chain — and the **brief** was prose typed fresh
each time, validated by nothing.

Both of the session's real harness defects came from the brief.

A closed `#Brief` changes the failure mode:

```cue
#Brief: close({
    role:          #NonEmpty
    deliverables:  [#Deliverable, ...#Deliverable]
    report_fields: [#NonEmpty, ...#NonEmpty]
    rules:         [...#NonEmpty] | #DeclaredAbsent
    sha256:        #Digest
    bytes:         int & >0
})
```

An extra instruction is not filtered. It produces
`brief.extra_guidance: field not allowed` and the dispatch does not
happen. **"Too much opinion corrupts the planner's attention" stops
being a discipline someone remembers and becomes a shape with no slot
for it.**

The same applies to a projection of a law document: a role's brief
should be *derived* from the law and stamped with its digest, so a brief
written against a superseded version is detectable rather than quietly
wrong.

---

## 4. Absence has to be said

The single most expensive class of error in agentic work, recurring in a
new costume every time:

- a proxy's `404` read as "no such package", when the registry never
  answered
- an HTTP `200` missing a field read as a zero
- an empty snapshot read as "the firewall worked"
- a token cap summing cache re-reads and read as spend

**Distinguish "we looked and found nothing" from "we could not look."**
In CUE:

```cue
#DeclaredAbsent: close({ declared_absent: #NonEmpty })

// anywhere a collection may legitimately be empty
withheld: [...#NonEmpty] | #DeclaredAbsent
```

An omitted key is refused. `[]` means "we looked and there were none".
`{declared_absent: "<reason>"}` means "there is nothing here, and here is
why". A schema that accepts a missing field silently converts the second
into the first.

**This discipline saved the pipeline once, concretely.** Seven
extractors, invoked with the wrong calling convention, received the
literal string `--root` as their root directory. Every one answered
`{"unresolved": "<target> is absent"}` rather than an empty list. A
harness bug then accepted that output, ten of twelve observations came
back all-unresolved, and the run printed **PASS** — with the determinism
check confirming the wrong answer was byte-reproducible. Only the file
sizes gave it away: 192 bytes against 39,659.

Had those extractors defaulted to `[]`, twelve contracts would have
vetted clean against nothing and the result would have been believed.

**Corollary: determinism is not correctness.** Reproducing a wrong
answer is exactly as easy as reproducing a right one.

---

## 5. A claim cannot assert its own truth

Borrowed in spirit from a contract-set validator where a judgment
claiming binding force *cannot be written down* — `advisory: true` is
the only admissible value.

The analogue here: **a context cannot declare itself clean.**

```cue
#Isolation: close({
    mechanism: "flags" | "home"
    flags: [...#NonEmpty] | #DeclaredAbsent   // what was APPLIED
    observed: #IsolationObserved              // what was FOUND
})

#IsolationObserved: close({
    operator_config_present: bool
    evidence:                #NonEmpty   // HOW that was determined
    checked_at:              #NonEmpty
    harness_version:         #NonEmpty
})
```

`applied` is an intention. Only `observed` is evidence, and `evidence`
must say how. `"isolated"` is not admissible where a probe result
belongs; `"unisolated arm answered YES, isolated arm answered NO"` is.

The same shape keeps adjudication honest. A disagreement between contract
and observation is **not a finding** until someone with the packet rules
on it. In the report those entries read `"why": "no verdict file"` —
which is the honest state, not a failure.

---

## 6. Closure is only half. Sufficiency is the other half

A closed context guarantees nothing extra reached the agent. It says
nothing about whether what did reach it was **enough**. Different
failure, and only one was guarded.

The unguarded one: an extractor author was handed sections 4–6 of a plan
as its specification, and section 6 refers to sections 9 and 10, which
were not in the excerpt. The agent reported it and emitted explicit
unresolved markers rather than inventing the missing rules. **That was
the agent catching it. Nothing in the machinery would have.**

> A hand-cut slice of a document is not a projection. A projection is
> closed under its own references; a slice is whatever somebody's `sed`
> range happened to cover.

The fix puts the requirement on the **task**, declared once, so two
dispatches of one task cannot disagree about what it takes:

```cue
#Requirement: close({
    what:         #NonEmpty   // in words, so a human can check the glob
    satisfied_by: #NonEmpty   // must match something staged
    why:          #NonEmpty   // what the role cannot do without it
})
```

`why` is not decoration. A requirement nobody can justify is the one
dropped by whoever next tries to make a snapshot smaller.

Then the law adds two emptiness conditions beside the firewall's:

```cue
unmet_requirements:  []   // a requirement matched nothing staged
dangling_references: []   // a given document cites material not given
```

---

## 7. The seam is where it breaks, and the seam is the interface

**Twice in one session**, two authors of the same seam were handed
different interfaces by the same conductor:

| seam | author A | author B | consequence |
|:--|:--|:--|:--|
| extractor call | `--root DIR --out PATH`, writes a file | positional root, prints to stdout | 10 of 12 observations empty, run printed PASS |
| fault sidecar | reads `<fault>.md` | writes `<fault>.meta.json` | all 66 faults reported a missing sidecar; none proven to fire |

In both cases **the shared specification fixed the shape of the DATA and
never the shape of the CALL.** The authors met at the shape and missed at
the door.

This is the most transferable lesson in the document. A neutral shape
spec is necessary and not sufficient. What is exchanged *and how it is
exchanged* are two contracts, and only one of them was being written.

```cue
#Interface: close({
    name:   #NonEmpty
    argv?:  [...#NonEmpty]
    output?: "stdout" | "file" | "both"
    fields?: [...#NonEmpty]
    declared_at: #NonEmpty   // an arbiter that is neither author
})
```

One object, referenced by every role that meets there. An interface
written twice in prose *will* differ; written once and referenced twice,
it cannot. `declared_at` is required so a disagreement has somewhere to
be settled that is not one of the two authors.

**When a mismatch is found, support both spellings and record which was
used.** Rewriting one author to match the other erases the evidence that
they disagreed — and a harness that quietly adapts to whatever an author
produced cannot tell you the authors disagreed, which is the only thing
it is for.

---

## 8. Every contract must fire, and must stay quiet

A checker that only passes the clean case converts *"nobody looked"* into
*"we checked and it was fine"*, which is worse than no checker.

So each contract gets faults planted by a **third** author — given the
specification and the clean observations, denied the contracts, so it
cannot aim at what it knows is caught.

**Measured, 66 faults across 12 contracts:**

| outcome | count |
|:--|--:|
| fired and attributed to the fault | 20 |
| rejected, but at a path the sidecar did not predict | 40 |
| **accepted — a real hole** | **6** |

**60 of 66 caused a rejection, and all 12 contracts reject something.**

Keeping the three apart matters: a bare "20/66 fired" would have read as
catastrophe. Only the six are contracts that are too permissive. The
forty are an attribution mismatch — the planter's `reject_path` regex or
`cue`'s choice of where to report the error — and settling which is
exactly what an adjudicator is for.

**The positive control is the other half.** Each contract gets an example
built only from the specification's own tables. Seven failed: four had no
example at all (*absent, not passed*) and three were **rejected by their
own contract** — which is a contract too strict for the document it was
written from, a finding about the contract rather than the code.

Contracts simultaneously too strict in some places and too loose in
others is what a first round should look like.

---

## 9. What CUE structurally cannot check

Worth stating plainly, because a contract suite invites the belief that
green means correct. From the authors' own reports:

- that an invocation happens, returns once, or emits anything
- that a status is truthful, that a partial deliverable is honestly
  labelled, that a reproduction actually reproduces
- that spend figures are accurate or a timestamp is real
- runner behaviour, idempotency, refusal-versus-integrity, anything
  requiring execution evidence
- process properties: whether a role actually read only what it was
  given

These need receipts, not schemas. A contract can pin the *shape* of a
receipt; it cannot pin that the receipt was earned.

---

## 10. Practical notes

**`cue vet -d '#Name' contract.cue observed.json`** is the whole
comparison. Errors name the field path, which is what makes an
adjudication packet possible:

```
facts.commands: incompatible list lengths (16 and 17)
```

That one line is the CLI-verb divergence: 16 because the document says
16, 17 because the code declares 17, found mechanically.

**Closed vs open must be declared per definition**, and the author should
say why. Recursive closure (`close()` at each level) is not the same as
closing the outer struct only; an author asked to "close it" will pick
one, and which one is a real decision.

**`provenance: _`** — keep facts and provenance separate, and leave
provenance unconstrained. Then a fault can mutate a fact while
`provenance` stays byte-identical, so a diff against the clean
observation shows the mutation and nothing else.

**Disjunction errors are legible but verbose.** `2 errors in empty
disjunction` tells a reader something failed every branch, not which
branch was intended. Where a disjunction encodes a real either/or —
`[...T] | #DeclaredAbsent` — that is acceptable; where it encodes
laziness it is not.

**Regex-pin identities.** `#Digest: =~"^[0-9a-f]{64}$"` catches a
truncated hash, which was one of the planted faults.

**An extractor must never import or execute the module it inspects.**
`ast` for Python, `sqlite3.executescript` into `:memory:` for SQL, a
read-only copy for a live database. Determinism is then testable: vary
`PYTHONHASHSEED`, cwd and umask, and byte-compare. Varying the hash seed
is the good one — it turns accidental set-iteration order into a visible
diff.

---

## 11. What it costs

Twenty dispatches across three harnesses, from their own trace files:

| | tokens | share |
|:--|--:|--:|
| cache re-reads | 58,760,434 | **93.4%** |
| fresh input | 2,598,287 | 4.1% |
| output — the actual work | 1,528,991 | 2.4% |

780 turns, averaging 1,960 output tokens each. **Cache re-reads run 14.2×
all non-cached traffic combined.**

The operational consequence: a supervisor cap on "total tokens" is
measuring how many turns an agent took, not what it produced. It rises
with legitimate depth, so no threshold separates a runaway from a
thorough job. One extractor was killed at 3,083,403 "total" while its
real output was 108,241.

**Two such misfires cost 198,317 output tokens — 13% of everything
twenty dispatches produced — and neither was an agent's fault.**

Volume is a budget backstop. Runaways have a *shape* — repetition, error
density, silence — and shape is what detects them.

And the number matters far less than whether tripping it destroys the
work. A killed extractor's completed modules survived because they were
on disk; its report died because the harness writes stdout only at the
end. Make a trip cost one turn instead of a run and the threshold stops
being interesting.

---

## 12. The shape all of it shares

Nearly every failure here reduces to one sentence:

> **A thing that did not happen was recorded as a thing that happened
> and was fine.**

Nobody looked, and it printed like we checked. A guard that was absent
looked identical to a guard that passed. An observation of the wrong
tree was well-formed and honestly labelled and therefore believed.

CUE helps because it converts a class of these from *invisible* to *does
not evaluate*. It does not help with the rest, and pretending otherwise
would be the same mistake in a new costume.
