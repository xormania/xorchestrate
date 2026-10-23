---
name: mini-app-lessons
description: Lessons from building the dev-lane mini apps, kept so the prod-lane versions can be written better — each with the incident behind it.
covers:
  - ops/devlane/
---

# Building a mini app: what the dev lane taught

The dev-lane apps are the prototypes. The prod lane will want its own,
and this is the running record of what to do differently — kept in the
same form as [`cue-sys.md`](cue-sys.md): **no claim without the incident
or the measurement behind it.**

Running document. Add to it while the measurement is still on the
screen; a lesson written from memory a week later is a guess.

---

## 1. The test author must not be the implementer

**Measured 2026-08-24.** Two checkers were built with one author writing
the implementation and its tests. Codex reviewed them over seven rounds
and produced 14 findings. **Eleven were missing test cases, not coding
errors** — the implementation did exactly what the tests specified, and
the tests were incomplete. Two were unreachable by any test; one was a
scope question.

The scarce skill is adversarial coverage, not implementation. So put the
strongest reasoning on tests, and have a third party ask *"what input
passes these and is still wrong?"* rather than *"are these right?"*.

Corollary, learned the hard way twice in one session: **the extra
condition added to be careful is what lets the defect through.** Both of
Codex's rounds on the trailer checker landed on a defensive test that
could only ever hide a true positive. One fix was a deletion.

## 2. Precision before coverage, or nobody reads it

**Measured.** A cross-app import checker keyed on textual shape flagged
**187 of 243 commits**, 137 of them commit-subject prefixes that merely
look like trailers. A checker that fires on three quarters of history is
one nobody reads. Rewritten to key on structure — a string that is an
operand of `/`, not a string that mentions a path — it flagged exactly
the 46 real cases, 0 missed and 0 extra.

Tune precision first, then widen. Every widening re-opens the question:
when `Path`/`open` were added to the inspected calls, the inert-fixture
case had to be re-proved, not assumed.

## 3. Absence must be refused, never interpreted

Three separate instances, one shape:

- `may_import` missing from an app's entry was read as `[]` — a
  meaningful configuration invented from silence.
- A commit with no `Source:` trailer at all passed clean, because the
  check compared a raw scan against git's parse and a parse can only see
  trailers that are **there**.
- Two apps owning one module basename returned "no owner", making an
  unresolved cross-app import indistinguishable from a stdlib one — the
  check discarded the very edge it exists to find.

A value that was never stated is not a value. Refuse.

## 4. A contract can only constrain a fact somebody extracts

**The commit-trailer case.** `commitmsg.cue` asserts every commit
carries a `Source:` trailer, and the constraint is sound — tested in
isolation it accepts a document with one and rejects a document without.
Yet 63 of 230 commits on `dev` violate it.

Because its observation's `provenance.target` is `gitio.py`: the fact is
about the code that *generates* wf's auto-commit messages, not about
commits. A hand-written commit never passes through that file. **The law
was correct and aimed at the wrong subject.**

The same file taught the other half: an extractor rule that read only
plain string constants would have reported `trailer_keys == ["Source"]`,
because six of the seven message lines are f-strings — *"a confident,
wrong answer"*. It had to be adapted to read f-strings.

**So the reusable asset is the extractor, not the contract.** The
contract is cheap; choosing what to observe, and observing it correctly,
is the whole skill.

## 5. A hole in a contract hides the finding behind it

**Measured.** The `mcp` contract placed no constraint on
`facts.server_imports`. A planted fault — a fictional `gitio` import —
was **accepted**, and the *real* finding standing behind it
(`mcp_server.py` importing `chain`, the module defining `Chain.append`)
never became a disagreement packet, because the observation had nothing
to conflict with.

A contract hole does not merely fail to catch a planted fault. It
conceals the true one. This is the argument for a fault corpus: without
it, a contract's silence is unreadable.

## 6. Prove the plant, and prove withholding in both directions

A fixture whose plant silently failed answers a question about nothing,
confidently. Every planted case must assert the fault is *in the
fixture* before asserting the verdict — and the harness itself must be
fed a no-op and rejected, or its name is the only evidence it works.

Withholding is invisible: a snapshot that leaked the wrong file looks
exactly like one that did not. Prove **both** — nothing matching a
withheld pattern present, *and* something matching every given pattern
present. An empty snapshot satisfies the first perfectly.

## 7. Vocabulary is load-bearing, and ambiguity produces wrong numbers

**Two incidents.**

`harness` means two things in this repo — the vendor CLI (46 uses in one
module) and the app that gates a dispatch. A detector grepping for the
word inside the task app reported six coupling edges; every one was the
vendor sense. The app is being renamed to `dispatch`, a word its own
files already use 136 times.

`commission` was a word the owner could not retain across a session.
A term that will not stay in the reader's head costs something every
time it is read; it became `job` while that was still three lines of
JSON.

**Rename before the thing that depends on it exists.** Both renames were
cheap only because nothing was built on top yet.

## 8. Write it where the next reader will look

`cue-sys.md` recorded the full independence pattern and the
firewall rule on 2026-08-23. The following night a session read
`ops/process/cross-review.md`, found three roles, ran a truncated
pipeline, and had to be corrected twice — while the complete answer sat
two directories away, referenced by nothing but its own companion.

**A record nothing links to is a record the next session rebuilds by
hand.** Check the link, not just the file.

## 9. What the environment does, only the environment can tell you

A range derived from `origin/dev` was correct locally and broke in CI:
a push run checks out the pushed commit *and sets `origin/dev` to it*,
so the range was empty and the gate refused on every push. The fix then
introduced its own: `GITHUB_EVENT_PATH` is set for every step of a job,
including the ones running the checker inside a throwaway fixture repo
that has none of the event's commits.

Both passed on this machine. Neither could have been found here.

## 10. Role independence is bounded by how many producers you have

The dev lane runs on three harnesses. Its pipeline has six stages, so
somebody fills two roles by arithmetic — Grok writes the tests and
reviews the code, Codex checks the tests and reviews the code. Both
arrangements are legal (nobody reviews what they wrote, no pair is
consecutive) and both carry a residual that no amount of process
removes:

- **Correlated blind spots.** A fresh session denies a reviewer any
  memory of having produced earlier, so it cannot defend its own work.
  It does not make the model see what that model could not see the first
  time. Statelessness defeats motive, not limitation.
- **The producer adjudicates.** Whoever produced an artifact decides
  which findings against it to act on. Escalating to an uninvolved
  harness fixes the ruling but not the decision to escalate.

Both dissolve with enough distinct producers: give every role its own
model and no role doubles up, so there is no shared blind spot to
correlate and always somebody uninvolved to rule. **These are scarcity
artifacts, not design flaws** — do not carry the dev lane's workarounds
into a lane that can simply assign a fresh model per role.

What *does* transfer: no producer owns two consecutive artifacts,
reviewers run cold against detached snapshots, and every finding is
answered in writing rather than silently dropped.

## 11. Two workflow files that share a `name:` share `github.workflow`

**Measured 2026-08-26, PR #53, run 32917378592.** Both files were
renamed `CI` so the left half of the rendered check would read the
same. Each built its concurrency group from `${{ github.workflow }}`.
That key is repository-wide. The two runs computed
`CI-pull_request-repo/ci-names`, and the second cancelled the first:
nine app jobs reported `cancelled` with zero steps run.

The test that pinned the key checked presence, not uniqueness — both
files contained `${{ github.workflow }}`, so the suite stayed green
while the runs ate each other. The prefix is now a per-file literal
(`ci-dev-` / `ci-dev-apps-`). Presence of a shared expression is not
evidence that two files will not collide.

## 12. CUE agreement is not source-presence

**Measured in the harness contracts.** Hidden-field aliases did not
enforce equality, conditionals inside `close()` weakened closure, and
open lists let an absent key become an inferred empty list. Even direct
unification can fill in an omitted value, so `cue vet -c` cannot prove
that a key was stated in the input. Equality belongs in direct field
references; source-presence needs a raw-document check before vetting.
The controls and exact failure shapes are recorded in
[`cue-aar-equality.md`](cue-aar-equality.md).

## 13. Deterministic agreement does not prove earned evidence

The contract experiments produced reproducible PASS results while most
extractors had converted inability to inspect into empty facts. CUE can
prove that facts agree with a shape; it cannot prove an invocation ran,
a reproduction reproduced, or a receipt was earned. Pair shape vetting
with deterministic gates that establish those events, and prove each
gate both fires on a planted fault and stays quiet on its closest valid
case. The measured boundary is detailed in
[`cue-sys.md`](cue-sys.md).

## 14. Plans cannot serve as stable law

`PLAN.md` accumulated at least 1,260 positional citations across 212
files because its claims had no named, closed seams. A correctly
designed manual retirement was reverted 27 minutes later after skipping
the ratification process. Draft and ratify closed contracts first,
repoint callers to named seams, discharge rulings into the decisions
log, and only then retire the plan. The synthesis and migration evidence
is kept in [`scratch/contract-synthesis.md`](scratch/contract-synthesis.md).

## 15. Blind producers need a deterministic meeting edge

**Measured across 12 contracts and 12 observations.** Separating the
intent-derived contract author from the code-derived observation author
surfaced 38 disagreements, four confirmed real. The useful pattern is
not merely separate authors: each side must be denied the other's
source, staging must prove what was given and withheld, and `cue vet`
must be the deterministic meeting edge. A third author plants faults
without seeing the contracts.

## 16. Every seam has a data contract and a call contract

Twice, producers agreed on the exchanged data but implemented
incompatible invocation shapes—positional versus flagged arguments,
then a Markdown artifact versus a metadata sidecar. Both sides passed
their own tests. A seam needs one closed interface object, declared by
neither producer, covering both the payload and the call convention;
when variants appear, record the mismatch instead of silently adapting
it away.

## 17. Closure and sufficiency are independent

A snapshot can prove that nothing forbidden entered while containing
too little to do the job. A hand-cut section range once omitted sections
referenced by the included text. Treat required inputs as a closed
projection: the task declares each required match and its reason, and
staging refuses unless every requirement is present as well as every
withholding rule satisfied. The prose brief is part of that context and
needs the same closed treatment.

## 18. Certification aligns two guarantee systems

The design work distinguishes CUE's between-component guarantees from
Rust's inside-component type guarantees. Each is checked independently;
certification is their deterministic alignment, with behavioral tests
remaining a separate blade for properties that cannot be expressed as
shape. Diagnostics may project drift for a developer, but only CI
refusal enforces it. This is product-proper architecture and therefore
must live outside `.dev/`, even though the dev-lane experiments supplied
some of its evidence.

## 19. Negative controls need attributed failure paths

In the contract corpus, 66 planted faults yielded only 20 failures at
the predicted path; 40 failed for the wrong reason and six passed. A
mere nonzero exit therefore overstates coverage. Every negative fixture
must prove its mutation landed and name the exact constraint it must
draw, while a nearby positive control proves the contract is not simply
rejecting everything.

## 20. Lane separation is semantic and operational

The dev-lane capabilities build the product but are not the product. Their
vocabulary must not enter product design or code, and their ledgers,
dispatch records, and context stores must not become product-lane
instances. Share a capability shape only after real instances justify
the abstraction; never share the runtime data. The ratified ruling is
also recorded in [`DECISIONS.md`](DECISIONS.md).

## 21. Management controls should shrink the conductor's job

The management analysis maps segregation of duties, span of control,
jidoka, and exception handling onto agent work. Its practical test is
whether routine coordination moves into visible artifacts and
deterministic controls while the conductor retains objectives,
boundaries, exceptions, and ratification. The full framework belongs
with the process it governs at
[`../process/agentic-management.md`](../process/agentic-management.md).
