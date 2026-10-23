---
name: cue-aar-equality
description: After-action report — four CUE constructs that looked like they enforced a rule and did not, measured while answering five review findings on context.cue.
covers:
  - ops/devlane/harness/context.cue
  - ops/devlane/harness/controls/
  - ops/devlane/harness/vet_context.py
  - ops/devlane/harness/probe.py
---

# After-action: the day CUE agreed with everything

**Four constructs that read as constraints turned out to enforce
nothing, and each one passed its negative test while doing so.** Every
one of them was written to close a real finding, looked right, and left
the hole open. Three were caught only because a positive control existed
beside the negative one; the fourth was caught by a second reader after
the patch was already written.

The companion document [`cue-sys.md`](cue-sys.md) is the running record
of what CUE is good for here. This one is narrower: it is the list of
ways it silently is not.

Measured 2026-08-23 with `cue version v0.17.1`, answering five findings
raised on `ops/devlane/harness/context.cue`. The experiment script is
reproduced at the end; every table below is its output.

---

## The one-line version

| construct | reads as | actually |
|:--|:--|:--|
| `_v: T` then `a: _v` and `b: _v` | *a equals b* | **nothing.** `_v` stays `T` |
| `parent: child: outer` | *child equals outer* | resolves to `child` itself |
| `a: b` where `b` is inherited | *a equals b* | `reference "b" not found` |
| `if x == v { … }` inside `close({…})` | *conditional field* | un-closes the struct |
| `a: b` as an equality | *both stated, compared* | also a **default**: fills `a` in |

---

## 1. A hidden field is not a variable

The obvious way to say "these two fields must agree" is to route both
through a hidden field:

```cue
#D: #C & {
	_v: string
	h: version:      _v
	h: iso: obs: v:  _v
}
```

It enforces nothing. `a: _v` means *a's value includes _v's value*, and
`_v`'s value is `string`. Data flowing into `h.version` does not flow
back up into `_v`, so the two fields never meet. A document naming
version `3.0.0` beside an observation taken at `2.1.4` validated.

**The reference is one-directional.** Only a direct reference between
the two fields propagates:

```cue
h: iso: obs: v: h.version
```

## 2. …and a direct reference resolves in the wrong scope

Written the other way round, it silently self-references:

```cue
brief: role: role     // `role` here is brief.role — constrains nothing
```

CUE resolves a bare name from the innermost scope outward, and inside
`brief: {role: …}` the innermost `role` is brief's own. The mismatching
document was still accepted.

Reversing it produces a *different* failure:

```cue
role: brief.role      // reference "brief" not found
```

because a struct literal's scope holds only the fields **it declares**,
not the ones unification brings in from the definition it extends.
`#Dispatchable: #Context & { … }` does not put `#Context`'s fields in
lexical scope. Naming the field in the literal fixes it:

```cue
brief: #Brief
role:  brief.role
```

**Two adjacent spellings, three different behaviours, one of them
silent.** Only the third is a constraint.

## 3. A comprehension inside `close()` un-closes the struct

Requiring a field conditionally is the natural reading of:

```cue
#Isolation: close({
	mechanism: "flags" | "home"
	home?:     #NonEmpty
	if mechanism == "home" { home: #NonEmpty }
})
```

Every negative control passed. The **positive** control broke, with
`field not allowed`, the moment `#Dispatchable` named
`harness: isolation:` on a second line — closure had stopped applying
to the struct that carries the comprehension.

The fix was not a better comprehension. It was moving the requirement
to where the other refusals already live:

```cue
#Dispatchable: #Context & {
	if harness.isolation.mechanism == "home" {
		harness: isolation: home:       #NonEmpty
		harness: isolation: auth_files: [_, ...]
	}
}
```

`#Isolation` describes what was done; `#Dispatchable` judges it. The
constraint was in the wrong file, and CUE's error was pointing at the
right thing in a language that made it look like a bug.

The `auth_files` line is `[_, ...]` and not `[...#NonEmpty]`, which is
what this document first recorded as the fix. `[...#NonEmpty]` is
satisfied by an **absent** field — CUE infers the empty list and the
result is concrete, so the omitting document validated. The spelling
that reads as "a list of non-empty strings" asserts nothing about the
field being there at all. `context.cue:402-410` carries the same
finding beside the line.

## 4. Unification is a comparison **and** a default

The subtlest one, and it survived the patch. Once

```cue
harness: isolation: observed: harness_version: harness.version
```

is in force, a document that **omits** `harness_version` entirely still
validates — CUE fills it from `harness.version`. `cue vet -c` does not
help, because after inference the field *is* concrete.

So the constraint cannot distinguish:

- the document stated the version and it matched, from
- the document said nothing and we wrote the answer in for it.

The second is exactly "missing evidence wearing the shape of a complete
record" — the thing `context.cue` exists to refuse — reintroduced by
the line meant to enforce it.

**An equality expressed as unification is not checkable inside CUE.**
Whether a key was present in the source document is a fact about the
document, not about its value, and the schema never sees it. It has to
be checked before `cue vet` runs, against the raw JSON.

---

## Evidence

Output of `ops/docs/cue-aar-lessons.sh`, `cue v0.17.1`:

```
A. does the constraint reject the mismatching document?
  hidden field + disjunction                   #D.h.iso.obs: field not allowed:
  hidden field, no disjunction                 ACCEPTED
  direct reference + disjunction               #D.h.iso.obs: field not allowed:
  direct reference, no disjunction             h.iso.obs.v: conflicting values "3.0.0" and "2.1.4":

B. and does the matching document still validate?
  direct reference + disjunction               #D.h.iso.obs: field not allowed:
  direct reference, no disjunction             ACCEPTED

C. a required field the constraint can now fill in for you
  obs.v omitted entirely                       ACCEPTED

D. a comprehension inside close(), with the struct named twice
  if m == "home" inside close()                #D.h.iso: field not allowed:
```

Row A2 is lesson 1. Rows A3/B1 are the disjunction interaction. Row C
is lesson 4, since closed. Row D is lesson 3.

---

## What actually caught these

Not review, and not reading. Three mechanisms, in the order they fired:

**A positive control beside every negative one.** Lessons 1 and 2 were
caught because four fixtures had to be rejected *and* one had to be
accepted. A schema that rejects everything passes every negative test
perfectly, and both broken constructs did.

**Pinning the field a rejection lands on.** The stale-version fixture
was rejected the entire time — by `mechanism: conflicting values "home"
and "flags"`, because the constraint had collapsed a disjunction rather
than fired. It scored as a pass. A fixture rejected for the wrong reason
reports a guard that is not there, so each control now names the field
path its rejection must mention, and `vet_context.py` fails one that
lands anywhere else.

**A second reader with no stake in the patch.** Lesson 4 was found by
dispatching grok against the finished diff and the fixtures, asking
three questions per finding — does it close it, what legitimate case
does it now reject, and what neighbouring hole does it leave. It also
found that one fixture is not a unique pin: `home-without-home.json`
omits both `home` and `auth_files`, so it would still be rejected if
only one of the two guards existed.

The general shape, which is not about CUE at all: **every one of these
failed in the direction of agreement.** A wrong constraint does not
error — it accepts. So the only thing that finds it is a document that
must be refused and a document that must not, and knowing which line is
supposed to do the refusing.

---

## Closed since

All five items this document listed as open were closed in the tree it
covers. Verified at `c80c2ad`, each against the blob named beside it —
a citation that can be re-checked with `git rev-parse <sha>:<path>`
rather than re-read.

| item | closed by | blob |
|:--|:--|:--|
| `harness_version` can be omitted and inferred (lesson 4) | `vet_context.py:70` — `harness.isolation.observed.harness_version` is in `MUST_BE_STATED`, the raw-JSON pre-vet check this document said was needed | `cb10bac7b023` |
| `count` is not `len(files)`, and nothing checks it | `context.cue:368` — `staged: count: len(staged.files)`, with the reasoning at 356-366 | `f9539313279f` |
| `home-without-home` is not a unique pin; nothing sets `home` and omits `auth_files` | `controls/build.py:191` — a `home-without-auth-files` mutant, and `rejects/home-without-home.json` now carries `auth_files`, so the two fixtures pin different things | `c382cd9bb672` |
| `behavioural_claude` reads a nonzero exit as an answer | `probe.py:141-143` — `_run` returns `rc=None` for NO ANSWER on any nonzero exit, and **both** arms go through it (`:168` grok, `:241` claude). Pinned by `test_probe.py:91` | `812365484a3c`, `9fcc0de64170` |
| `auth_files: []` is the same missing evidence with the field present | `context.cue:410` — `[_, ...]`, with the trap written out at 402-409 | `f9539313279f` |

The fourth was worth naming twice — **the fix had been applied to the
instance and not the class**, in a repository whose first standing rule
is to start from the class. It was closed the way the rule asks: one
helper both arms call, rather than a second copy of the same fix.

---

## The script

`ops/docs/cue-aar-lessons.sh` builds five tiny schemas and three
documents and reports
what `cue vet` does with each. It is deliberately not a test: it is a
notebook that can be re-run against a new `cue` release, which is the
only way to find out whether any of this still holds.

```sh
cue version                     # v0.17.1 when this was measured
ops/docs/cue-aar-lessons.sh
```
