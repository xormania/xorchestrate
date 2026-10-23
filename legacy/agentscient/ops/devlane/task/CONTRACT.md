# The task app — a calling convention for delegated work

This is dev tooling under
[AGENTS.md §Lane boundary and precedence](../../../AGENTS.md#lane-boundary-and-precedence).
Work profile is independent of job role, harness and conductor/worker role.
Shared output contracts do not import the prod application's Symfony
runtime into the runner or imply its replacement. A brief's older framework
recommendation cannot override that boundary. This scope clarification
changes neither the job schema nor executable acceptance behavior.

A **task** is one delegated unit of work that returns one deliverable
in one round. It is not a conversation and not a workflow stage: the
Conductor invokes it with parameters, the task does its own iteration
internally, and it returns a typed envelope.

The point is not correctness — the checks and the cross-review process
already own that. The point is a **standard, token-efficient way to
run delegated work**, so the plumbing is written once and the Conductor
never parses prose.

## Why it exists, measured

On 2026-08-22 one session launched **36 agents** and hand-wrote the
same ~40 lines of plumbing for nearly every one: snapshot a ref,
archive it, write a diff, drop a marker, discover the session stream
the harness just opened, arm the battery, wait, read a verdict by eye.
**15 of those 36 were repeat rounds of a review already run**, each
handed the entire diff again even when the live question had narrowed
to a single test case — because there was no parameter for scope.

Two costs follow from that, and both are what this app removes:

- **Plumbing retyped per launch**, with the mistakes that come with it.
- **The Conductor reading work product.** Verdicts arrived as prose, were
  read in full, and the interesting parts were retyped into the next
  prompt. That is the loop cost: a Conductor's context grows with every
  round until it cannot run another.

## The call

This is the target calling convention, not a claim that every field is
already accepted by either CLI. C1 standing and measured baseline are in
the [dispatch contract](../dispatch/CONTRACT.md#c1--execution-authority-target-not-runtime-completion).
Dev tooling remains permanent and independent of the Symfony application;
`prod` is a work profile, not a tooling implementation or retirement plan.

```
task(
    job,                                   # which pre-made task
    context = {ref, diff_base, include, prior},
    require = {schema, scope, constraints},
    runtime = {harness, model, role, profile, effort, caps},
)
```

**`context` is the token lever.** It states what the task may see:

| field | meaning |
|:--|:--|
| `ref` | the git ref to snapshot — the task works on a detached copy, never the live tree |
| `diff_base` | what the diff is taken against; omitted means no diff is written |
| `include` | explicit path allowlist. Absent means the whole snapshot, which is the expensive default and should be a deliberate choice |
| `prior` | findings from an earlier call. Turns "review this again" into "confirm these are closed", which is a much smaller job |

**`require`** states what must come back: the output `schema`, the
`scope` to aim at, and the `constraints` the task must respect (read
only, plant on copies, never edit the snapshot).

**`runtime`** states who runs it and under what ceiling: `harness`,
`model`, `role`, `profile`, `effort`, and supervision `caps`. Harness,
model, role and work profile are independent configurable dimensions,
constrained by the job and admitted capabilities; a job's declared role
is not permission to guess a model or widen source access for an output.
Profile policy is a later consume-only join owned by the parallel
`lane/lane-boundary-20260905` track, not defined by task or dispatch.
The current task vendor runner uses the telemetry battery; launch has
different inline supervision, as the dispatch baseline records.
A job never names a model — that
is the Conductor's dial, which is what makes "eight cheap ones" and "one
careful one" the same job.

## The envelope

Every task returns the same shape. The Conductor routes on it without
reading the work:

```json
{
  "job": "adversarial-review",
  "status": "ok | invalid | tripped",
  "verdict": "approve | changes | null",
  "counts": {"p1": 0, "p2": 1, "p3": 0, "opinions": 0},
  "findings": [
    {"severity": "p2", "where": "file.py §section", "claim": "...",
     "reproduce": "python3 -m unittest ..."}
  ],
  "artifacts": {"raw": "<path>", "diff": "<path>"},
  "spend": {"harness": "grok", "total": 0, "out": 0, "runs": 0},
  "stamp": {"ref": "<sha>", "started": "...Z", "ended": "...Z"},
  "note": null
}
```

Three fields carry the weight:

- **`reproduce`** — the command that re-derives the claim. A finding
  without one is an opinion, and the envelope says so rather than
  hiding it.
- **`artifacts`** — handles, not contents. The prose stays on disk;
  the Conductor reads it only if it decides to.
- **`spend`** — what the run cost, so `worth.py` can price a job
  instead of guessing. Runs happen in throwaway snapshots today, and
  usage.py's cwd filter therefore attributes **none** of them to the
  repo they served: roughly 118M tokens of review on 2026-08-22 went
  unattributed. The stamp is what closes that.

**One semantic authority:** task's [envelope.py](envelope.py) owns the
shape and acceptance rules; dispatch and all frontends must consume that
authority. Counts are derived from findings, including `opinions` for a
null reproduction; a parsed tally mismatch is refused, not repaired.
Artifacts remain non-empty, single-line handles bounded by `HANDLE_MAX`,
not embedded reports. Workers leave the declared zero spend placeholder;
the launcher resolves measured spend, declaring unavailable evidence
instead of treating that placeholder as a measurement.

`envelope.validate` applies both the JSON schema and the semantic rules.
Dispatch applies it after extracting the selected response for every harness,
including legacy raw results. Schema matching alone may identify a response;
it cannot accept one. A selected response that fails semantic validation is
invalid with a null verdict and a diagnostic. Its findings and asserted counts
remain available for inspection; an earlier clean response cannot replace it.
`run.py` uses the same validator for bare and wrapped results, including the
optional `commit` object. JSON member order is immaterial at these boundaries;
constructors continue to emit the declared key order.

**Named temporary exception — inline planning reassignment.** The owner
brief authorized the immediate read-only architecture review to return
the plan as an artifact string after plan record `976735` could not write
`out/PLAN.md` under Codex read-only containment. That specific reassignment
is temporary transport, not a standing artifact mode or general permission
for read-only jobs to embed reports. Ordinary plan, skeptic and ruling
jobs still require file handles. C1 does not universalize the workaround.

## Statuses, and the refusal rule

`status` is the routing decision, and it is not the same as `verdict`:

| status | means |
|:--|:--|
| `ok` | the task ran and produced its deliverable |
| `invalid` | the task could not do its job — harness missing, ref absent, snapshot failed, output unparseable. **Never reported as an approving verdict.** |
| `tripped` | the battery stopped a runaway; the deliverable is partial and says so |

A task that could not look must not return `approve`. This is the same
rule the registered checks follow, and the reason `status` and
`verdict` are separate fields rather than one.
`invalid` requires a null verdict and a note naming why it could not do
the job; `tripped` may carry partial findings but never `approve`.
For implementation jobs, `ok` requires the named acceptance command to
exit 0 in the final tree; otherwise `invalid` names the failing command.
Process exit zero alone is not accepted-envelope evidence.

## Jobs

A job is data, not code — a JSON entry naming the role, the prompt
template, the output schema, and the default constraints. Adding one
does not change the runner.

| job | deliverable |
|:--|:--|
| `adversarial-review` | findings that survived the task's own attempt to refute them |
| `security-audit` | security findings against the change that survived the auditor's own attempt to refute them |
| `verify` | one claim, executed: a reproduction or a refutation |
| `author-tests` | a test file that has been run and is red for the stated reason |
| `sweep` | structured findings over disjoint categories, merged |
| `plan` | a plan written to the job's `out/PLAN.md`, and an envelope naming it |
| `check-tests` | the skeptic's findings on the wire: the JSON envelope on stdout; verdict `changes` exactly when any finding exists; each finding is a quotation pair (§The skeptic's finding); `artifacts` empty; no file written |
| `implement` | commits in the snapshot that make the named test command pass, trailers per `AGENTS.md` |
| `adjudicate` | a ruling at `out/RULING.md` — UPHELD / PARTIAL / REJECTED per finding, and what the reviewers missed |

### The skeptic's finding

A skeptic finding uses the existing four fields, and may carry the
practical label (§The practical label). `severity` is `p1`, `p2` or `p3`.
`where` is the practiced location: it has no whitespace and
contains `/` or `.`. `claim` has this grammar:

    declared (<location>): <words verbatim>; practiced: <evidence verbatim>; misses: <the broken implementation the test would not catch>

The declared location is non-empty and has no parentheses. The claim is
split at the first `; practiced: ` and the last `; misses: `. `reproduce`
is a non-empty command; `null` is refused because an opinion is not a finding.

### The practical label

Findings are suggestions, and judging them needs a rating made where the
evidence is. Any finding (a skeptic's, a review's, an audit's) may carry
two more fields, both or neither: `practical` is `yes` or `no`, rated by
a concrete failure on the change's exit path (a wrong result, a crash,
or a bypassed refusal, on an invocation someone will actually make), and
`failing` is that invocation as a command when `practical` is `yes` and
`null` when it is `no`. A check-tests finding for a test that breaks a
rule of `docs/tests.md` is `practical: yes`, because the wall refuses it
at CI. The envelope (`envelope.py` `PRACTICAL_FIELDS`), the ladder's
check-tests validator and the findings ledger admit the pair; a finding
without it has the four fields alone, as every record before it does.
The label is the reviewer's, and the disposition stays the session's: a
labelled finding that is ruled rather than fixed is ruled with a
judgement beginning `declined: not practical — `, which the
`finding-disposition` row reads at `gh pr ready` (CONTRIB.md, the audit
row).

`verify` is deliberately callable on its own: it is the primitive the
others lean on, and the one whose answer is mechanical — an exit code
or a diff, not another opinion.

The last four are the dev-lane pipeline's own stages as jobs (dispatched
by the dispatch app; see `ops/devlane/dispatch/CONTRACT.md`). They are data
in `jobs.json` today. Three of them — `plan`,
`check-tests`, `adjudicate` — name `{out}` or `{inputs}`, which `run.py`
does not yet supply, so `render` refuses each by name rather than
launching a brief with a hole in it. `implement` names only `{ref}` and
`{scope}` and renders today; what it still lacks is the launcher's
whole-mode snapshot and collect, without which its commits have nowhere
to land.

## What the runner does, and does not

The target worker execution service is dispatch's `launch.py`: it
snapshots, launches, supervises, collects, parses and records. The separate
`run.py` vendor engine is manual/dormant at the measured baseline, not its
backend; direct verification and stub replay are distinct uses. It does
**not** read the work product, and
it does not decide anything a job could not have decided in
advance.

Two things stay outside it. **Intent** — which of two defensible
designs is right — belongs to the job author or escalates to the
owner; a worker task has no standing to settle it. And **the wiring's
own correctness** cannot be delegated to the tasks it wires, which is
why every job is bound to a planted fault: run it against a fixture
with a known defect and it must surface it; run it clean and it must
stay quiet. A job that cannot catch its own planted fault is a
no-op with good throughput.

### C1 task frontend construction decision

For this correction, choose C1's explicit refusal branch for task vendor
mode. Keep the existing callable
`run(job, *, context, require, runtime, jobs=None, adapters=None) -> dict`
and `main(argv=None) -> int`. A harness job with runtime.harness other than
`direct` or `stub` returns task's semantic `invalid` envelope, null verdict,
empty findings/artifacts, zero counts and
`spend={harness:null,total:0,out:0,runs:0}`, before snapshotting,
rendering, adapter lookup, process creation or store access. Its note is
`execution: expected canonical worker execution; found task vendor mode; needed ops/devlane/dispatch/launch.py <job> --harness H --model M --ref R`.
Stamp the caller's supplied string ref as an unverified request (null if absent),
never claim a resolved manifest, observed model or completed attempt.
Malformed local inputs and unknown jobs retain their own invalid diagnosis.
The CLI prints that envelope and exits 2; usage remains 64. This is the
existing task projection, not a change to dispatch's refusal wire.

Direct verification still delegates to `verify.check` and returns its
envelope unchanged, with `{harness:null,total:0,out:0,runs:1}` spend. Stub
replay still reads runtime.replay and uses task's shared envelope semantics,
without a worker launch. Their existing fileset behavior stays local.
Retain the pure render/load_jobs helpers and direct/stub adapter data.
No `run.resume`, injectable `run.canonical_launch`, executor registry or
task-to-dispatch import is required or authorized by this correction.
Worker execution and resume use the existing
[launch verbs and result transport](../dispatch/CONTRACT.md#c1-frontend-inputs-and-results).
Task's legacy context.into/include, runtime caps and adapter dictionaries
are not silently translated into launch paths, permissions or configuration.
Unsupported fileset mode continues to refuse at launch.

This settles the test-author construction gap, not runtime retirement by
documentation. The independent author must apply the
[bounded correction](#c1c2-independent-test-correction) before handing tests
to an implementation producer. Unchanged historical assertions do not
override this target or authorize rebuilding the dormant vendor engine.

## Harnesses

The job is harness-independent; an adapter absorbs the differences
in invocation, in where each harness writes its session stream, and in
how each reports usage (`ops/devlane/telemetry/usage.py` already holds
those three accountings). A `stub` adapter exists so the suite can
test the runner without spending a token or needing a model.

## The property to hold

**Iteration N should cost the Conductor about what iteration 1 cost.**
A loop whose context grows every round has a horizon; one that stays
flat can run as long as the work does. The envelope is what makes that
possible, and `worth waste`'s cache-churn signal is how it is checked.

## Declared job outputs

Target ownership: task owns output requirements in [jobs.json](jobs.json);
launch resolves declared `{out}` paths into grants and records custody.
At the C1 baseline, prompt prose requests `{out}/PLAN.md` for `plan`,
`{out}/SKEPTIC.md` for `check-tests`, and `{out}/RULING.md` for `adjudicate` (reproduce:
`rg -n 'PLAN.md|SKEPTIC.md|RULING.md' ops/devlane/task/jobs.json`). These are
job outputs outside the source snapshot, not source-write roles.

A later runtime slice adopts the C2 `outputs` declaration below in `jobs.json`.
No such machine-readable declaration exists today; prompt prose
records demand and must not be parsed to authorize filesystem grants.
Missing declarations mean no extra grant. The named temporary inline
planning reassignment above remains limited to that specific reassignment.

**Target:** grants follow declared artifact needs, not arbitrary job-name
branches. A read role retains source isolation and unchanged-source
settlement while writing its declared outputs. Neither an output handle
nor a profile can confer broad source/host permission. Each harness must
prove the required capability or explicitly refuse it; dispatch owns that
[capability evidence](../dispatch/CONTRACT.md#isolation-per-harness).
Output declarations and grants are owned here and in launch, with no wait
on or duplicate of the parallel lane-policy contract.

**Measured transport gap:** launch renders `{out}` and `{inputs}`, but
`writable_out` is selected by `job == "check-tests"` on launch and resume.
Other Codex read jobs remain read-only; there is no generic output grant.
The owner brief records `976735` invalid because its required plan write
was rejected. Task run does not automatically supply document-job output
or input paths either. Rendering a path and declaring a capability do not
prove that a worker can deliver it. Reproduce the name branch with
`rg -n 'writable_out=' ops/devlane/dispatch/launch.py`. C1 documents this
defect; independent runtime/test work follows.

### C2 standing and declaration schema

**Contract only:** this is the two-file bucket from GPT-6 plan
`20260906T024750Z-plan-codex-93f60d`, Claude review
`20260906T025522Z-review-claude-cfc890`, and the supplied
`launch-c2-contract-ruling.md`, incorporating F1–F5. Task owns declaration
vocabulary, validation and deliverable rendering;
[launch](../dispatch/CONTRACT.md#c2--output-admission-contract-only) resolves
paths, admits grants and checks filesystem custody. No executable code,
catalogue, prompts or capabilities change here. Historical evidence and the
narrow inline planning exception above retain their standing; C2 does not
retroactively reject old records or generalize that exception.

Source checked on 2026-09-06 at `19f938e339cc519d66eb65989915decfa00439df`:
`cat ops/devlane/task/jobs.json` shows no `outputs` declarations and native
prompts request their document paths without fixed artifact keys;
`rg -n 'ENVELOPE\.|\.validate\(|writable_out=|TMPDIR|COMMIT_MSG|last-message.json'
ops/devlane/dispatch/launch.py` shows the job-name grant branches and the
semantic-validator gap described above. This is source evidence, not live
containment proof. Runtime adoption remains later independent work.

An optional `outputs` member on a harness job has exactly this standalone
JSON Schema. Omission is defined as `[]`, with no additional worker output
grant. Explicit `null` is invalid. Each `file` is a required, non-empty
document; `scratch` authorizes temporary work and requires no artifact.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "array",
  "uniqueItems": true,
  "items": {
    "oneOf": [
      {
        "type": "object",
        "properties": {
          "kind": {"const": "file"},
          "path": {"$ref": "#/$defs/path"},
          "artifact": {
            "type": "string",
            "pattern": "^[a-z][a-z0-9_-]*$",
            "maxLength": 64
          }
        },
        "required": ["kind", "path", "artifact"],
        "additionalProperties": false
      },
      {
        "type": "object",
        "properties": {
          "kind": {"const": "scratch"},
          "path": {"$ref": "#/$defs/path"}
        },
        "required": ["kind", "path"],
        "additionalProperties": false
      }
    ]
  },
  "$defs": {
    "path": {
      "type": "string",
      "maxLength": 240,
      "pattern": "^[A-Za-z0-9_][A-Za-z0-9_.-]*(/[A-Za-z0-9_][A-Za-z0-9_.-]*)*$"
    }
  }
}
```

Task's semantic declaration validation additionally rejects duplicate
JSON object members, duplicate paths even across different kinds, repeated
artifact keys, ancestor/descendant overlaps and more than one scratch
entry. Paths and keys must match their grammar over the entire string
(including rejecting a trailing newline). Paths are case-sensitive POSIX
relative names beneath the job's canonical `out/`, never templates or
shell expressions: absolute paths, dot segments, backslashes, whitespace,
globs and substitutions refuse. No case folding or path normalization may
turn invalid input into a different grant. Normalized declarations mean
validated values with omission expanded to `[]`, preserving array order.

Launch supplies adapter/launcher transport reservations to validation;
equal, ancestor or descendant collisions refuse. Reserved leaves include
`last-message.json` and `COMMIT_MSG` beneath `out/`; neither is a report or
scratch declaration. The list must include any further transport paths an
adapter needs. At adoption, declared and admitted scratch owns its temporary
subtree and `TMPDIR` mapping, replacing any legacy launcher-created temporary
output directory for that purpose; it is not also reserved as transport.
Thus the declared `tmp` below is scratch, while genuine transport leaves
remain reserved and file/scratch collisions still refuse. Existing path
types, links and unsupported filesystem alias behavior are checked by
dispatch, not inferred from the schema. A regular
document must not be multiply linked; no output or ancestor may be a
symlink. Lexical and real-path checks alone do not prove race-proof
containment.

### C2 library adoption seams

Reuse `run.load_jobs(path=JOBS_PATH) -> dict` as task's catalogue reader;
dispatch may consume it through the existing dispatch-to-task edge. Input
is a string or path-like filesystem path to the held trusted catalogue,
read as strict UTF-8 JSON. Decode the entire document before selecting a
job, checking object member pairs before constructing each dictionary.
Reject duplicates at every depth, including unselected jobs, escaped-key
equivalents and repeated top-level job names; reject malformed JSON and
non-finite constants. A native dictionary or its re-encoding is not proof
of a strict original decode. Return the original mapping of nonempty job
names to job objects, retaining values and output order, without adding
defaults or interpreting prompt text. This does not introduce a closed job
schema. Preserve `RunError` for unavailable/undecodable files and malformed
catalogue shape; duplicate members raise `envelope.OutputsError` with
`outputs-schema`, naming the duplicate key. No offending value spelling is
required. Dispatch retains exact source bytes/digest and maps that exception
to its existing C2 refusal before filesystem effects. Resume uses retained
declarations; any catalogue it must read uses the same strict reader.
Task's `run()` maps `OutputsError` from default `load_jobs()` to its semantic
invalid envelope with null verdict and an `outputs-schema` note carrying
expected, found and needed; `main()` prints that envelope and exits 2 without
a traceback, while direct library calls to `load_jobs()` keep raising.

The following task-owned `envelope.py` signatures extend the existing
library, with no policy or adapter implementation import:

```text
outputs_from_job(spec, reserved=TRANSPORT_RESERVED) -> list[dict]
render_declared_outputs(outputs, out_root, reserved=TRANSPORT_RESERVED) -> str
append_declared_outputs(prompt, outputs, out_root, reserved=TRANSPORT_RESERVED) -> str
```

`spec` is a job dictionary; omission of outputs returns `[]`, explicit null
refuses. `outputs` is a C2 array; `out_root` is the canonical absolute string
bound by dispatch; `prompt` is a string. `reserved` accepts a list, tuple,
set or frozenset of C2 relative transport paths. Always union it with
`TRANSPORT_RESERVED = {"COMMIT_MSG","last-message.json"}`; even an explicit
empty collection cannot remove those leaves. Null, strings and malformed
members refuse `outputs-schema`. The same complete set reaches
`validate_outputs`, extraction, rendering and append; omission is only the
library's compatibility default, never evidence that an adapter needs no
additional transport. Each call validates before returning any text or
array, preserves declaration order and spelling, and performs no filesystem
write. Rendering returns exactly the section below; append returns
`prompt.rstrip("\r\n") + "\n\n" + section`. A failure returns neither a
partial section nor a grant. Existing `load_outputs`/`validate_outputs`
inputs, results and frozen C2 tests stay unchanged.

`OutputsError` remains a distinct subclass of `EnvelopeError`. Declaration
and reservation failures are `outputs-schema`, unrepresentable bound paths
are `outputs-path`, and `validate(env, outputs=rows, out_root=root)` raises
`OutputsError` for `outputs-handle` mismatch. Each includes expected, found
and needed. General envelope errors retain `EnvelopeError`; validation
without outputs retains historical relative handles. The optional closed
`commit` object `{subject:<string>,body:<string>}` survives recognition and
semantic validation, whether the response is top-level or under `result`.
Absence stays absence; unrelated keys still refuse. Launch extracts worker
responses and consumes task validation; task replay consumes the same rules.
Neither needs a second vendor response parser in task's refusal frontend.

### Native declarations and rendered deliverables

The following is catalogue data for later adoption, not a catalogue edit
in this bucket. Each value is the named job's `outputs` array:

```json
{
  "plan": [{"kind": "file", "path": "PLAN.md", "artifact": "plan"}],
  "check-tests": [
    {"kind": "file", "path": "SKEPTIC.md", "artifact": "skeptic"},
    {"kind": "scratch", "path": "tmp"}
  ],
  "adjudicate": [
    {"kind": "file", "path": "RULING.md", "artifact": "ruling"},
    {"kind": "scratch", "path": "tmp"}
  ]
}
```

Preserve native document demands, destinations and `role=read`. Skeptic
mutation and adjudication reproductions use disposable copies in declared
scratch. A renamed job with identical declarations gets identical output
treatment; plain reviews with omission or `[]` gain no writes. Runtime
authorization never searches prompt prose or branches on these names.

Task owns a deterministic deliverable section, consumed by launch on every
initial and resumed worker brief regardless of harness schema transport.
After removing trailing CR/LF from the rendered native prompt, before any
appended envelope schema, append two LFs and `Declared job outputs:` followed
by LF. Visit entries in declaration order. Substitute the canonical absolute path
and declared key into these exact line templates; each substitution is a
JSON string literal (double-quoted, ASCII-escaped with lowercase hex for
non-ASCII characters, no optional slash escaping or shell expansion), and
each line ends in LF:

```text
Required file: <path>. Write a non-empty document; set artifacts[<artifact>] to <path>.
Scratch subtree: <path>. Temporary work only; no artifact is required; TMPDIR is <path>.
```

For omission or `[]`, the only following line is
`None. No additional worker output write grant.` plus LF. No key is chosen
from prose or inferred from the filename. For canonical `out/` at
`/jobs/example/out`, check-tests renders exactly:

```text
Declared job outputs:
Required file: "/jobs/example/out/SKEPTIC.md". Write a non-empty document; set artifacts["skeptic"] to "/jobs/example/out/SKEPTIC.md".
Scratch subtree: "/jobs/example/out/tmp". Temporary work only; no artifact is required; TMPDIR is "/jobs/example/out/tmp".
```

Rendering communicates an admitted declaration; it cannot authorize a
grant. Later catalogue/prompt alignment must remove contradictory output
instructions while retaining native work requirements, and test the exact
key/path pair for each job and a renamed equivalent. No current prompt is
silently changed here. Permission failures cannot fall back to inline
prose. The specific inline planning reassignment persists until its
transition is independently defined and tested, without becoming a new
`outputs` kind or standing report mode.

### Output acceptance

For every `file`, `artifacts[artifact]` must equal the canonical absolute
path rendered to the worker and resolve to that declared regular file,
non-empty in bytes, under the bound job `out/`. Equality is exact string
equality of the decoded handle and the rendered absolute path value;
a different spelling that resolves to the same file does not satisfy it.
This deliberately tightens the plan's resolving-handle language for newly
declared outputs. It neither retroactively invalidates historical records
nor removes the existing narrow inline planning transport exception.
Task's [envelope.py](envelope.py)
remains the single authority for bounded, non-empty, single-line handles,
counts and status/verdict semantics. Launch consumes that validation and
adds filesystem/attempt custody checks; it must not copy those semantic
rules into another validator. If a bound path cannot be represented as a
valid handle, admission refuses `outputs-path` before child execution.

Missing files, a missing/wrong key or handle, unreadable evidence, unsafe
types/links or unverifiable custody prevent `status=ok`. Dispatch records
each accepted file's digest and current-attempt custody. Other evidence
handles remain references and confer no write authority. Scratch is not a
substitute report and is never automatically published. Prior attempt
reports remain prior evidence; current completion needs new production or
explicit verified reuse as specified by dispatch. Changed read-role HEAD
or snapshot residue still invalidates even when every output is present.

The [runtime adoption checklist](../dispatch/CONTRACT.md#c2-runtime-adoption-dependencies)
requires task semantic-validator integration into launch, including optional
write-job commit transport and the narrow inline exception, catalogue/prompt
alignment, independently authored tests and fresh capability proof. These
are later dependencies, not effects of this two-file contract.

## Unit1a declaration transport

Adoption unit 1a freezes task's side of the
[policy binding input](../../conductor/lane-policy.md#trusted-binding-input-and-declaration-transport).
This is a consumption format, not a new jobs schema, registry, CLI flag or
runtime permission. Task validates job declarations and envelope semantics;
dispatch acquires trusted sources, resolves paths and owns attempt custody.
Task imports neither dispatch/harness nor the policy implementation.

`LaunchDeclarations` is exactly
`{selection,requested_scope,job_grants,role_grants,adapter_grants,outputs,reservations,sources}`.
All keys are required. Use the policy coupling's strict JSON and canonical
encoding rules; provenance comes from held trusted inputs, never from passing
schema validation alone. Dispatch provides adapter grants/reservations from
admitted harness declarations; task validates the complete output declaration
and its collisions without loading or selecting an adapter implementation.

For a worker, `selection` is `TaskSelection`, exactly
`{harness,model,effort,role,job_role,profile}`. role is literal `worker`, job_role
is `read` or `write` equal to the trusted job role, effort is null or nonempty
printable ASCII, and model is explicit nonempty printable ASCII; model/effort
are at most 128 bytes. harness is a discovery ID; profile is null only while
resolving absence under the policy selection/default rule, otherwise a valid
policy profile id. Before binding it must be the resolved nonempty profile.
For a conductor, the same LaunchDeclarations transport accepts startup's
separate LaunchSelection with role conductor and job_role null; its canonical
[record variant](../dispatch/CONTRACT.md#canonical-conductor-record-variant-proposed)
has stage conduct, job null and brief null. This is a closed union selected by
selection.role; worker requires read/write job_role and conductor requires null.
No job declaration infers conductor authority. Requested and
observed model fields retain their distinct existing owners.

`requested_scope`, `job_grants`, `role_grants` and `adapter_grants` each have
exactly `{repository,outputs}`, sorted unique arrays of paths. Repository paths
use policy's exact/prefix rules. Output paths are names beneath canonical job
out, using C2's relative path grammar; kind/operations come only from the matching
validated C2 declarations and admitted adapter witness. A file name means one
leaf and a scratch name that exact subtree, never their parents. Scope expresses
an explicit trusted request, not permission by itself. `--scope` and scope-file
prose remain brief content and cannot populate these structured grant sets.
`context.include` is visibility, not requested write authority. Role read always
has an empty repository grant; outputs do not relax snapshot immutability.
Conductor job_grants and role_grants are exactly `{repository:[],outputs:[]}`,
and outputs is `[]`; it has no task output demand or direct source-write role.
Coordination/delegation remains separately authorized through existing levers.
Absent source grant terms normalize to empty arrays with absence recorded in
sources; explicit empty terms also grant nothing and retain their measured source.

`outputs` is precisely the validated C2 array, omission expanded to `[]` while
preserving declaration order. `reservations` is the sorted unique list of
C2-grammar relative paths reserved for launcher/adapter transport under out;
include last-message.json, COMMIT_MSG and every actual additional reservation.
Missing/unavailable reservation analysis refuses, never normalizes to an empty
successful list. Scratch is not a transport reservation. No source/role/profile
or envelope handle creates an undeclared output operation.

`sources` is a sorted unique array of DeclarationSource, exactly
`{component,kind,path,ref,sha256}`. component is one of
`selection,scope,job,role,adapter,outputs,reservations,dependencies`. kind is
`git`, `invocation` or `absent`. For git, path is an admitted repository-relative
source leaf, ref its full Git id, and sha256 hashes exact blob bytes. For
invocation, path is the component token, ref null, and sha256 hashes the exact
retained canonical structured input received through the trusted caller.
For absent, path/ref/sha256 are all null and that component supplies no grant.
All non-null digests are lowercase 64-hex. Sort uniquely by component, kind,
path, ref (null first). Duplicate source identities or conflicting sources refuse.
Every component except dependencies must have source coverage; dependencies
names its admitted analyzer when carried in policy's separate evidence object.
A component may cite multiple verified Git sources for its closure. Selection,
job, role, adapter and reservations need available verified sources before
admission; absent scope or outputs can supply only empty grants. For a conductor,
the outputs component is explicitly absent, as is job (job_grants is empty);
this is the sole exception to the available job-source requirement. Its role
source covers admitted conductor restrictions, startup template, caps and
supervision configuration (including timeout and its source), retaining exact
source bytes, ref and digest through the existing `role` component; selection,
role, adapter and reservations still require available verified sources. No
consumer indexes jobs.json with null or infers a read/write job for this case.
For a worker, outputs may cite the catalogue whose measured omission yields
`[]`, keeping that observation distinct from an unavailable catalogue.

Dispatch verifies Git sources against the old admitted declaration closure and
retains invocation bytes under caller custody; a model-created matching digest,
current mutable checkout or candidate input is insufficient. `brief.template.sha256`
still hashes the trusted template source and must equal brief.policy's
existing template_sha256. Source receipts are retained with the normalized grants
and exact original output array outside worker writes; no extra brief.policy
key is introduced. Independent producers can construct policy mocks from this
shape without task importing the consumer.

Policy derives normalized scope/job/role/adapter from requested_scope/job_grants/
role_grants/adapter_grants respectively, and normalized outputs from every C2
entry.path as `{repository:[],outputs:<sorted unique paths>}`. File leaves and
scratch subtrees keep their original relative spelling and kind semantics;
reservations never enter that set. Policy's
[exact derivation](../../conductor/lane-policy.md#trusted-binding-input-and-declaration-transport)
also owns maintenance-row decoding and hash bytes. Retain sources and the
original ordered C2 array so consumers can recompute, not invent, those values.

A synthetic declaration fragment illustrates requested scope, not authorization:

```json
{"repository":[],"outputs":["SKEPTIC.md","tmp"]}
```

For C2's existing check-tests array this requests its file leaf and scratch
subtree. If role/adapter grants and the profile allow both, admission still
requires G=D, actual A=D, verified source isolation and fresh capability evidence.
If any term is absent, there is no partial grant or silent removal of scratch.
The same values on a renamed job have identical meaning. Empty repository and
output arrays on a read job can support stdout-only envelope transport under
its existing authority; they do not authorize an out file.

### Attempt outputs and result acceptance

Task keeps C2's exact decoded artifact-key/path equality, nonempty file demand,
counts and status/verdict validation. Dispatch supplies the trusted current
attempt plus declared destination and prior-receipt identity to filesystem
acceptance; a worker-provided attempt number cannot establish custody.
The [dispatch attempt transport](../dispatch/CONTRACT.md#unit1a-attempt-and-result-transport)
retains file bytes/digests and separates fresh production from explicit reuse.
A reuse request travels through an inherited launcher-owned per-attempt input,
not a new envelope member or a guessed reference to a leftover file. It names
an exact prior receipt and is verified before being accepted; it grants nothing.

Policy results are launcher-owned result.policy, never task envelope members.
Task's semantic validator remains the single authority; the launcher additionally
checks custody and policy before accepting a semantically valid envelope.
Missing, stale or refused policy evidence prevents acceptance despite ok/approve
or exit zero. Pre-act refusals keep the existing envelope absent. After effects,
the launcher retains the worker's original response and partial evidence,
serializes the owning refusal under policy's late Abort rule, and emits invalid
with null verdict and a reason in note; an existing tripped outcome remains
tripped, never approve. No independent findings/counts are silently repaired.
This envelope rule applies when a worker response exists. With none, dispatch
retains result.envelope null and refused PolicyResult; task validates no invented
response. Conductor results always bypass the worker envelope validator and use
their declared canonical variant. Pre-effect/late timing is per invocation:
the successful directory claim is already an effect even before an attempt;
resume/collect preflight can still reject before their own first mutation.
The existing optional write-job commit transport and narrow inline planning
exception retain their current authority and later integration requirements.
No report, scratch, reuse request or policy receipt widens either exception.

Independent construction cases pair trusted versus forged sources, prose versus
structured scope, omitted/empty/unavailable grants, selection conflicts and
renamed jobs, transport collisions, fresh output versus leftover bytes, verified
reuse versus stale/foreign-attempt receipts, and invalid/tripped late outcomes.
These are documentary acceptance duties; this unit authors no executable tests
and admits no current job or adapter capability.

## Dispatch ownership

Target: [dispatch](../dispatch/CONTRACT.md) consumes task's jobs and envelope
semantics plus harness isolation/budget wiring. The declared edges are
`dispatch → task` and `dispatch → harness`; task must not depend on
dispatch, harness or lane-policy implementation. The historical
`ops/contracts/imports.json` is absent at the measured baseline, so this
is a declared boundary, not a claim of an installed import gate.

`jobs.json` stays here — it is data the dispatch app reads by path,
not an import — and so do `run.py`, `envelope.py`, `fileset.py`, and
`verify.py`.
The manual/dormant task vendor path's later explicit tested refusal is
specified by the [construction decision](#c1-task-frontend-construction-decision),
with supported behavior accounted for by the correction below.
Fileset/staging parity is not required for dispatch/go/ladder convergence;
dispatch's existing tested `mode-unavailable` refusal remains in force.

## C1/C2 independent test correction

This instruction is for the independent test producer, before runtime work;
the implementation producer neither edits nor approves tests. It applies
only to the supplied `test_run.py`, `test_execution_frontend.py` and
`test_execution_adoption.py` from `1139d52f1206688a8d648c0a1bd23900b9c000c4`.
Measured on 2026-09-06 against `3f84832108274403a4f2c58bc12410ae896da2c0`
with `sha256sum ../in/test_*.py`: run `3692dcda0cc5b0c41cdb1b67bb1003450c60525b12aed3d49c780b14a34cdf43`,
frontend `07abaf471c39bd20cc5508c1c67b3612d267511ef7df0675b6b05d43ccb0112f`,
adoption `951e48aac226d5bd6d356b47d77501244beb620cf0e778e454bd52cdd99c23ae`.
These are supplied artifacts, not a claim they are installed at this ref.

In the tables, method names omit only the literal `test_` prefix; every
named method gets the stated correction. Move a duty by rewriting that
case against canonical launch fixtures, then removing its task-vendor
assertion in the same test change. Do not skip, xfail, delete coverage or
hand an implementer both incompatible assertions. Existing dispatch tests
may discharge a duty only with an explicit case-to-case coverage citation;
otherwise retain a synthetic positive and faulty control in the moved case.
No real vendor, live store, catalogue activation or capability admission.

### Old test_run.py assertions superseded

| class / methods | bounded correction and preserved duty |
|:--|:--|
| RunBuildsTheSnapshotWithFileset: the_snapshot_is_a_fileset_snapshot_of_the_ref_not_the_worktree; the_source_repo_is_not_checked_out_or_mutated | R1: remove vendor fileset.snapshot/FILESET.md/include-set assertions. Test launch whole-ref bytes versus dirty source and its permitted record-only invoking-repo delta; retain independent fileset tests and mode-unavailable refusal. |
| StampRefComesFromTheManifest: stamp_ref_is_the_sha_the_manifest_names; an_unresolvable_ref_is_invalid_and_never_launches | R2: launch owns ref resolution/stamping and pre-effect bad-ref refusal. No task vendor manifest or frontend stamp overlay. |
| RoleSelectsTheSandbox: a_write_role_receives_the_write_sandbox_not_the_read_value; a_read_role_receives_the_read_sandbox; a_grok_write_role_is_auto_not_plan; builtin_write_values_are_not_the_read_only_ones | R3: move role separation to launch's admitted isolation fixtures; remove task ADAPTERS pins and Grok auto/plan requirement. Assert declared mechanics and unsupported-role refusal; sandbox labels alone never prove C2 capability. |
| AnUnknownHarnessIsInvalid: an_unknown_harness_is_invalid_with_a_note_naming_it | R4: launch's unknown selection refuses without substitution or child; task vendor mode uses execution refusal regardless of vendor name. |
| AMissingCliIsInvalidNotAVerdict: a_missing_cli_is_invalid_with_a_note_naming_the_gap; a_missing_cli_is_not_ok_changes_about_the_work | R5: move fake missing executable to launch; preserve named non-acceptance and no invented work findings. Task must not resolve vendor binaries. |
| TheBatteryIsArmedWithCapOut: a_cache_heavy_total_does_not_trip_when_output_is_under_cap_out | R8: launch synthetic stream records 7000000 total/50 output under cap-out 1000 without trip; remove task battery argv/process requirement. |
| ABatteryTripIsNotACompletedRun: a_battery_trip_is_status_tripped_with_no_verdict_and_a_note | R9: launch kills the over-budget group, retains partials and tripped/non-approve with reason. Null remains the no-partial-verdict fixture expectation, not a ban on permitted partial findings. |
| StreamDiscoveryNamesTheSnapshot: the_stream_that_names_the_snapshot_is_chosen_over_a_newer_sibling | R10: launch's isolated store/session/cwd identity beats newer foreign evidence; remove global task store/name heuristics. |
| AbsentSpendIsNotZeroSpend: no_stream_is_unknown_spend_not_a_measured_zero | R11: launch declares missing evidence versus measured zero and terminates an unsupervised live child after grace. Supersede the unconditional ok-with-no-stream expectation. |
| RawOutputLivesInsideTheSnapshot: raw_output_is_inside_the_snapshot_and_not_inlined | R12: launch raw.out is under the canonical job directory, outside snapshot. Preserve exact raw bytes and bounded handles, never inline raw contents. |
| RunDoesNotInterpretProse: a_structured_envelope_from_the_harness_is_parsed; prose_that_says_approve_does_not_become_a_verdict | R13: launch extracts structured responses and consumes task semantics; prose remains invalid/null with reason. Preserve replay recognition locally. |
| AnUnknownJobIsInvalid: an_absent_job_is_invalid_with_a_note_naming_it; load_jobs_reads_the_json_table; load_jobs_reads_a_path_it_is_given | R14: retain all three local checks; no snapshot/child on unknown job. Reader positives use the strict seam above. This class is not blanket-retired. |
| MainIsACli: a_changes_run_prints_json_and_returns_one; an_approving_run_prints_json_and_returns_zero | R15: replace implicit vendor success fixtures with controlled local direct verify findings/approval; preserve JSON and 1/0 mapping. Test explicit task vendor refusal separately; never default a model to rescue these calls. |
| MainIsACli: an_invalid_run_prints_json_and_returns_two; a_usage_error_returns_sixty_four | R15: retain local invalid JSON/2 and usage/64 checks. Dispatch exits are separately tested, not copied to task. |
| TheHarnessReceivesTheRenderedPrompt: the_harness_receives_the_scope_and_the_ref | Move scope/ref delivery to launch's prompt witness; task vendor mode does not render a worker brief. |
| TheHarnessReceivesTheRenderedPrompt: render_fills_the_job_template | Keep pure task rendering unchanged. |
| ATrippedRunLeavesNothingBehind: the_group_dies_even_when_the_parent_was_already_reaped; the_group_is_recorded_at_launch | Move orphan termination and launch-time pgid evidence to the canonical supervisor; remove run._terminate/_remember_group/_task_pgid implementation pins. Retain direct-operation cleanup coverage in verify's own suite. |
| TheConductorsDialReachesTheHarness: grok_receives_both_dials; claude_receives_both_dials; codex_receives_the_model_dial; codex_receives_both_dials; an_unset_dial_adds_nothing | Move dial encoding to launch's fake argv witness; never run._adapter_argv. The unset control omits optional effort but supplies required model, with a separate missing-model refusal. |
| TheRealAdaptersBindToTheirEvidence: grok_discovery_is_bound_to_the_snapshot; every_live_adapter_can_carry_a_model | Move cwd/session attribution and explicit model delivery/refusal to launch; remove task live-table structure assertions. Fake evidence does not admit a live adapter. |

Keep every method in DirectDelegatesToVerify (R6), StubReplaysWithoutLaunching
(R7), TheJobAsksForWhatTheRunnerParses and TheDocAndTheRegistryAgree unchanged.
Their duties are local verification/replay, prompt/schema agreement and exact
job-table coverage. The R1–R5/R8–R15 blanket retirement in the supplied report
is therefore too broad: R14, half of R15 and pure rendering remain.

### Supplied C1 and frontend cases

| class / methods | correction |
|:--|:--|
| C1VendorModeDelegatesToCanonicalLaunch: vendor_mode_refuses_or_transfers_and_never_owns_a_second_executor | Require the exact task execution refusal and absence of snapshot, prompt, child and store effects; remove the synthetic-transfer alternative. |
| C1VendorModeDelegatesToCanonicalLaunch: when_vendor_mode_transfers_explicit_selection_is_identical | Move to admitted dispatch/go/ladder invocation; compare exact job/ref/lineage and all six TaskSelection members, including profile, using the dispatch input/result contract. No substring search in a call blob. |
| C1VendorModeDelegatesToCanonicalLaunch: direct_verify_does_not_transfer_and_still_returns_verify_check; stub_replay_does_not_transfer_and_still_replays; vendor_mode_does_not_import_dispatch_or_harness | Keep behavior, replace invented callback instrumentation with child/effect and import-boundary observations. |
| C1VendorFrontendParsesOptionalCommit: a_commit_bearing_envelope_is_accepted_not_treated_as_absent; the_same_envelope_without_commit_is_still_found; a_commit_bearing_envelope_nested_under_result_is_accepted | Use runtime.harness=stub and runtime.replay naming each fixture for these three parser cases, preserving subject/body and absence controls; launch extraction of the same fixtures stays in adoption tests. No successful task vendor execution. |
| C1LoadJobsDecodesTheCatalogueStrictly: duplicate_members_are_refused_before_dict_collapse; reencoding_a_collapsed_dict_does_not_recover_the_duplicate | Keep raw-byte faulty/control pair; require OutputsError/outputs-schema for duplicates. Add duplicate job, unselected job and escaped-equivalent key subcases, without weakening frozen C2 array tests. |
| FrontendSupervisionIsLaunchs: a_canonical_launch_trip_is_returned_unchanged; vendor_mode_does_not_rewrite_a_launch_trip_to_ok | Run the existing launch entrypoint with synthetic over-cap evidence; compare canonical record/envelope status and partial findings, then the frontend projection. Remove callback-return oracle. |
| FrontendRefusalsAreLaunchs: a_canonical_launch_refusal_is_invalid_with_the_refusal_text; a_faulty_synthetic_executor_does_not_launch_a_harness_cli | Exercise real launch preflight with a missing model or isolation fixture. Assert exit/stderr/no-state per owning refusal; pre-act dispatch has no fabricated envelope. Task's own invalid refusal is covered above. |
| FrontendResumeDelegates: resume_is_a_public_seam_that_transfers_the_same_id; resume_does_not_invent_a_new_dispatch_id | Invoke launch.main(["resume", id]) in existing launch fixtures; verify exact record id, cwd, session, original selection, appended attempt and prior-evidence preservation on refusal. Remove run.resume and meaningless absence-of-one-random-id assertion. |
| FrontendResultsAreLaunchs: the_frontend_returns_launchs_envelope_without_rebuilding_it; requested_model_is_not_copied_onto_observed_spend | Compare decoded canonical result fields through actual frontend transport, not Python object identity. Preserve optional commit, findings, stamp, tripped/invalid and measured spend. Pin requested/observed model to record.model.requested/ran/read_from with distinct synthetic identities; spend.harness is a harness, not a model id. |
| FrontendDirectAndStubStayLocal: direct_verify_does_not_see_a_synthetic_launch_trip | Keep direct positive result and no worker effects; drop monkeypatch of the undeclared callable. |

Remove _SyntheticLaunch, bind_canonical_launch and selection_blob dependencies
after converting their callers. Use existing launch.main(argv), fake CLIs,
record readers and deterministic clocks, or a contract-verified frontend
process double that returns precisely launch's existing exit/stdout/stderr.
Relocate launcher-facing cases into the supplied dispatch adoption test home;
keep task-only controls in their supplied task homes. No new production API
is a remedy for test placement or a mock's preferred return value.

Add these four named cases in the dispatch adoption test home, alongside the
relocated frontend cases. They supplement the 83 supplied dispositions above
and below; they do not replace selection-identity coverage. Each consumes
[dispatch profile transport](../dispatch/CONTRACT.md#c1-frontend-inputs-and-results)
through trusted inputs and the policy selection rule it cites:

| class / method | required case and control |
|:--|:--|
| ProfileSelection: explicit_and_structured_profiles_agree | Equal valid explicit and structured ids resolve to that exact profile; either input alone selects that same id, without consulting a default. |
| ProfileSelection: conflicting_profiles_refuse_before_effects | Different valid ids refuse lane-profile before directory, record, home, prompt or child effects; empty, unknown or malformed supplied ids also refuse without default fallback. |
| ProfileSelection: absent_selections_use_only_configured_default | With both inputs absent/null, resolve only the admitted explicitly configured valid default; preserve its source and exact profile in TaskSelection. |
| ProfileSelection: absent_selections_without_default_refuse | With both inputs absent/null and no configured default, refuse lane-profile before effects; no inference from harness, job role, record.lane, environment or scope prose. |

### Supplied test_execution_adoption.py cases

Retain every method of OptionalCommitSurvivesValidateAndLaunch,
CompleteReservationsReachEverySeam and OutputsErrorIsTheC2RefusalFamily.
The three reserved= call sites are now declared above; they must still fire
before text is returned. Preserve unknown-key, counts-mismatch, no-extra-
reservation, native render and historical-handle controls.

In CatalogueDecodesDuplicatesBeforeDictCollapse, change only
`test_launch_refuses_duplicate_catalogue_members_before_a_child`'s required
phrases from path/B.md to the duplicate key path plus outputs-schema wire
detail; a strict decoder need not expose the overwritten value. Keep
`test_reencoding_the_collapsed_catalogue_is_a_different_input` unchanged.

In LaunchAdmissionCarriesCompleteReservations, retain
`test_a_file_named_last_message_json_is_refused_before_a_child` and
`test_a_file_named_commit_msg_is_refused_before_a_child`. Extend their
pre-effect witness to no job directory/record/home/prompt as well as no child.
For `test_native_plan_declaration_is_rendered_into_the_brief`, provide a
contract-verified synthetic capability witness with exact file authority
and a fake worker producing PLAN.md plus its exact artifact handle; pair
with missing/unavailable witness refusal. Fake Grok success alone is not
admission. Keep `test_omission_renders_the_none_grant_line` with empty extra
authority. An extra transport collision is supplied through trusted
LaunchDeclarations.reservations, never adapter.reservations or new policy
mock methods. Check the identical set at extraction/render/append and resume.

Correction acceptance requires all named old assertions either retained or
replaced as directed, no contradictory vendor-engine tests in the handed-off
suite, direct/stub positives and faulty controls, explicit profile agreement/
conflict/default/absence cases, and unchanged frozen C2 tests. Run each corrected
file against the baseline to distinguish intended behavioral reds from fixture
errors, then `ops/bin/suites.sh` for runtime landing. This contract-only change
edits no tests/runtime/catalogue/settings/state/admission and claims no such
suite pass or capability proof.
