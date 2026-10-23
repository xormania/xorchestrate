# Constructing a pipeline

What three days of dispatching taught about choosing a pipeline shape, and the
tools that would let the choice be made deliberately instead of by habit.

The full pipeline — plan → ratify → tests → check-tests → code → two adversarial
reviews → adjudicate — is right for some work and ruinous for the rest. It costs
seven or eight dispatches and, measured, **$40–80 a lineage**. This session
applied it to a five-line permission file until the owner said the obvious thing:
experiment with shorter pipelines.

A short pipeline is not a skipped pipeline. The difference is whether the
stages you dropped were doing work.

---

## 1. What actually determines the shape

Six properties of the work, each answerable before dispatching anything.

### P1 — Does an authoritative oracle already exist?

If something outside this repository already decides the question — the
settings loader, `cue vet`, `ruff`, the test runner, CI, the container
runtime — then **authoring tests is authoring a second, weaker oracle.** No
test I write outranks the loader that will actually parse the file.

Where an authority exists, the `tests` and `check-tests` stages have nothing to
add, and the pipeline collapses to *change → run the authority → have someone
independent read its verdict.*

Where no authority exists — a behaviour contract, a refusal path, an envelope
shape — the tests **are** the oracle, and the skeptic that judges them is the
most valuable dispatch in the system (measured: **$2.99 per p1**, cheapest stage
in the pipeline).

### P2 — Is the subject representable in a snapshot?

The launcher builds a snapshot with a fresh `git init`, a fetch, and a detached
checkout. **Git-ignored files are not in it.** Neither is host state, container
state, or anything outside the tree.

This is not a small detail; it decides which jobs are usable:

| subject | usable jobs |
|---|---|
| in the tree, with a diff | `implement`, `adversarial-review`, `check-tests` |
| in the tree, no diff yet | `plan`, `author-tests`, `sweep` |
| not in the tree | `sweep` only, material supplied by hand |

Material outside the tree reaches a harness two ways: `--scope`, which is
interpolated into the prompt, or `--input`, which copies files into `in/` with
their sha recorded. Everything else is invisible no matter how carefully the
brief describes it.

### P3 — Does it change a contract?

Ratification is not a pipeline stage; it is a gate on the **decision**, it costs
no dispatch, and it is not optional at any size. A one-line change to an
envelope schema, a job catalogue, `law`, or anything under the workflow ledger
needs a ratified decision with reason, premise and `ratified_by` — even when the
pipeline around it is a single sweep.

Conversely, a change that touches no contract does not need `plan` and
`adjudicate` merely because they exist.

### P4 — What is the blast radius of being wrong?

Match spend to consequence, and measure both.

A wrong permission line costs a re-run. A wrong nested `additionalProperties`
cost **three envelopes and five findings in one day** and took two dispatches to
diagnose. The second deserved the full pipeline; the first did not.

### P5 — Is the defect class already known?

Depth is a function of **marginal** yield, not a constant. The tool-guard
skeptic chain:

```
pass 1  $3.88 → 4 p1        pass 4     ?  → 1 p1
pass 2  $3.02 → 3 p1        pass 5  $8.90 → 0 p1
pass 3  $4.50 → 2 p1        pass 6  $4.29 → 2 p1
```

Pass 3 found the defect that unblocked the unit. Passes 4–6 cost **$16.21** and
reached a false green. **Stop at the first pass returning zero p1 at
above-mean cost, and never accept a green from a pass that cost more than the
pass that last found something.**

### P6 — Is the question about the work, or about the world?

"Is this correct?" is a pipeline. "What is actually true right now?" is a
single unseeded read, and it is the best-value dispatch we buy. grok refuted a
launcher bug I had filed for **$3.47** and saved a change that was never needed.
Two of the session's best findings cost nothing at all — they were refusals.

---

## 2. The shapes

| work shape | pipeline | dispatches |
|---|---|---|
| contract change | plan → **ratify** → tests → check-tests → code → 2 reviews → adjudicate | 7–8 |
| code, no contract change | tests → check-tests → code → 1 review | 4 |
| in-tree change with an authoritative oracle | change → 1 sweep against the oracle | 1 |
| out-of-tree change with an authoritative oracle | change → 1 sweep, material by `--scope` | 1 |
| a question about the world | 1 unseeded sweep; 2 harnesses independently if it matters | 1–2 |
| refuting a conductor claim | 1 read on the cheapest capable harness | 1 |

Triage first, always. A $3 read that says "your premise is wrong" retires a $60
pipeline, and this session it did exactly that twice.

---

## 3. The mechanics, each learned by paying for it

- **Dispatchable jobs are exactly those carrying both `role` and `snapshot`.**
  `verify` carries neither and is refused. `jobs.json` lists both kinds and
  marks neither — this is anomaly A-5, and it cost three misdirected dispatches
  and two refusals.
- **`--stage` is a separate axis from the job**, with its own enum:
  `plan|tests|check-tests|code|review|adjudicate`. There is no `sweep` stage.
- **A review with `base_sha == ref_sha` is refused** (D-DIFF-1). The base is
  `merge-base(ref, origin/dev)`, not the lineage tip.
- **Run from a worktree of the lineage**, or the launcher refuses `record-target`.
- **Shell state does not persist between calls.** `WF_AGENT` and
  `DISPATCH_TIMEOUT` go *inside* every launch script.
- **A launch prints its record id at mint.** This closes the earlier A-1
  finding: a detached caller can identify the run immediately, then use
  `launch.py wait ID` for its terminal result.
- **Effort is per harness and job.** `EFFORT_<HARNESS>_<JOB>` in
  `ops/lane.conf` beats `EFFORT_<HARNESS>` (PR #20); the policy since
  2026-09-12 is plan at xhigh (or max), every other job at high (owner:
  "xhigh or max is ok for the initial planing - but that's it. Everything
  else should be high"). codex takes `-c model_reasoning_effort="..."`,
  not `--reasoning-effort`; the launcher forwards the value through each
  harness adapter. Turns are capped per job too: `caps.turns` in
  `jobs.json` becomes `--max-turns` on claude and grok; codex has no such
  flag and the record says so.
- **Landing is** `git fetch -q <dispatch>/snapshot HEAD && git cherry-pick -x
  FETCH_HEAD`.
- **Paired dispatches take a byte-identical scope.** Take the sha when you write
  it and do not touch the file again — a rule the conductor broke within an
  hour of writing it, which is why §4 proposes a check rather than a reminder.

---

## 4. The tools this asks for

Ordered by mistakes prevented per unit of build. Every one traces to a real
cost paid this session, and none of them writes.

**1. `job_catalogue`** — every job with `dispatchable` *computed* from the
presence of `role` and `snapshot`, its snapshot mode, the inputs it requires,
and the legal stages. A-5 exists only because this is a JSON file a conductor
has to read correctly, and I did not.

**2. `dispatch_precheck`** — a dry run, before spending anything, that answers:
does the scope path exist; would the diff be empty; **is the material I am
relying on actually inside the snapshot, or is it git-ignored**; does the brief
contradict its own acceptance command. Each of those four cost a real dispatch
this session. The gitignore trap cost one today.

**3. `pipeline_propose`** — given P1–P6 declared as answers, return a shape from
§2 **with its cost estimated from `meters.db`**, which now holds $/p1 by stage
and by harness. Not a rule of thumb: a number with its basis attached, so it can
be argued with.

**4. `marginal_yield(lineage, stage)`** — running p1-per-pass and cost-per-pass
for a chain in progress, so "run another skeptic?" is a query rather than a
feeling. The stop rule in P5 is one `select` away and I ran it three times too
late.

**5. Record the shape on the dispatch.** The pipeline chosen and the P1–P6
answers that chose it belong on the record. Without that, a short pipeline is a
shortcut; with it, it is an experiment we can settle later by asking the only
question that matters — **did the short pipelines miss things the long ones
caught?** That question is unanswerable today because the shape was never
written down.

---

## 5. The standing rules that do not vary with shape

Short pipelines shorten stages. They never shorten these.

- **Ratification for anything touching a contract**, at any size.
- **No seeding.** A brief never carries the conductor's findings, anomaly ids,
  or expected verdict. Today's guard sweep supplies the file under test and the
  questions, and not one word of the diagnosis.
- **The escape clause, always:** *if you find something outside these questions
  that would change the verdict, report it.* A bounded brief without it buys a
  bounded answer to a question that may be the wrong one.
- **Prefer the observer's record to the actor's report.** The stage that reads
  an authority is not optional just because the pipeline is short — it is the
  entire reason a short pipeline is allowed to be short.
