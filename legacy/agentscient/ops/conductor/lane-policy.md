# Lane policy — first contract and inert data

## Enforcement authority correction — A-218, owner 2026-09-18

The existing [hooks matrix contract](../devlane/hooks/CONTRACT.md) owns current
harness enforcement: boundaries.json, matrix.py and engine.py compose through
one common entry. Claude, Codex, Grok and agy adapters translate native events;
they do not evaluate separate permissions. References below proposing activation
of lane-policy.json, lane_policy.py, conductor-enforce.py or a frozen-action
control plan as the shared enforcement authority are superseded by this ruling.
Those proposals retain historical context, not runtime authority. No undefined
A-120 hook is created by this correction.

Interactive terminal registration/profile generation and dispatch isolation
installation are separate startup routes into that same entry. Interactive
registration uses the native session and existing coordination SQLite lever;
it requires no dispatch job. Workers keep their issued assignment identity and
minimal home. Generated native settings are disposable projections, never a new
state or grant store. Identity, roles, assignments and hand grants remain with
the existing instrument/coordination/hand owners. co_doc diagnoses projected
wiring; only versioned native interception witnesses establish coverage.
Installing this source does not activate owner settings or restart terminals.
Root independently verifies the concrete candidate before activation.

Standing: proposed, not activated. This contract and
[`ops/lane-policy.json`](../lane-policy.json) prescribe future behavior; they
install no evaluator, sandbox, settings, hook or acceptance gate. The owner
authorized this contract/data slice after planning, with independent review,
independently authored tests, and implementation still to follow.

Authority: [the owning lane boundary](../../AGENTS.md#lane-boundary-and-precedence),
the [owner history](../../.dev/ladder-inputs/lane-boundary-owner.md), and the
[contract assignment](../../.dev/ladder-inputs/lane-policy-contract-owner.md).
This adopts Grok ruling `20260906T021124Z-adjudicate-grok-8ce9f8`, including
its seven dispositions, as clarified by the contract assignment's P2.
The supplied ruling is evidence, not an executable authority source.
The bounded correction adopts Grok ruling `20260906T023832Z-adjudicate-grok-8cdf11`
and the [correction assignment](../../.dev/ladder-inputs/lane-policy-correction-owner.md).
The [final ruling](../../.dev/ladder-inputs/lane-policy-final-ruling.md) accepts
the three remaining findings from Claude `20260906T025705Z-review-claude-104942`.
It records `D-LANES-1` as the permanent lane boundary's semantic owner evidence
in the conductor decisions register, with a scope-reference note on `D-SYM-1`;
neither is admission of this policy blob, a maintenance grant or runtime activation.

## Ownership and positive scope

Dev tooling is permanent, independent machinery. It must start and operate
without the application's kernel, container, services, or `vendor/`; product
delivery neither replaces nor retires it. Product work retains the application's
Symfony rules. Harness, model, job role, conductor/worker role and work profile
are separate dimensions. `record.lane` and `WF_LANE` remain infrastructure
markers and cannot select work permissions.

The example's mapping keys are configuration, never consumer branches:

| profile | permitted repository writes, before other restrictions |
|:--|:--|
| `dev` | `ops/`, excluding every protected path even with a maintenance record (`authority_maintenance: false`) |
| `prod` | `src/`, `packages/`, `config/`, `public/`, `templates/`, `assets/`, `migrations/`, `tests/`, `bin/`, `translations/`, `composer.json`, `composer.lock`, `symfony.lock`, `phpunit.dist.xml`, `.env`, `.env.dev`, `.env.test` |
| `authority-maintenance` | `ops/`, `docs/`, root/scoped instructions and the explicitly listed shared configuration paths; protected edits additionally require a trusted maintenance authorization |

Thus tooling changes pass in `dev`; application and Composer changes pass in
`prod` and refuse in `dev`; `prod` cannot write `ops/`. Shared-policy maintenance
is an explicit third case, not a union of product and tooling privileges.
Ordinary unprotected tooling maintenance needs no special authority decision.
Profile membership grants no host, network, database or dependency-installation
permission: those capabilities still require launch/task authorization.

Both lanes may read shared contracts and inspect or describe application source,
including Symfony names. Reads remain subject to custody/withholding; this policy
does not reveal withheld paths or credentials. Inspection reads are not runtime
dependencies. Unavailable application observations are reported as unavailable,
while the dev tool itself remains operable. No word ban or prose classifier can
prove architectural independence. Already corrected authority documents and
historical migration, stack, census, idea, AAR, handoff and planning records keep
their bytes and provenance; superseded replacement recommendations never regain
authority through an input, prompt or resumed job.

## Data shape and path rules

The UTF-8 JSON object has exactly `schema`, `standing`, `shared_read_roots`,
`protected_authority_paths`, `profiles`, and optional `default_profile`.
`schema` is `agentscient.lane-policy/v1`; the only allowed `standing` value is
`proposal` (descriptive, never an activation switch). Any other value refuses
`lane-policy`. `profiles` is a nonempty mapping of nonempty ids
matching `[a-z][a-z0-9-]*`. Each value has exactly `write_roots`,
`forbidden_dependency_roots`, `authority_maintenance` (boolean), and
`allow_declared_outputs` (boolean). Root lists contain unique strings; empty
lists explicitly grant nothing or forbid nothing, respectively. Duplicate JSON
keys, unknown keys, wrong types or malformed paths refuse `lane-policy`.
An optional default must be a nonempty existing profile id; this example omits it.

`agentscient.lane-policy/v1` is a fixed policy protocol identity. Following
[lane-mcp-lever.md's exact declaration precedent](lane-mcp-lever.md#coupling-proof-and-installation)
for `agentscient.lane/v1` and `agentscient-lane`, the lane literal guard may
permit, for the `agentscient` token only, the exact stripped line
`POLICY_SCHEMA = "agentscient.lane-policy/v1"` at `ops/devlane/dispatch/record.py`.
Existing outer-whitespace normalization remains. Every other line, path and
token retains detection, including another configured token matching inside
that declaration; the two existing MCP declarations remain allowed. Changed
constant names or values and appended comments or statements are not allowed.
No directory-level exemption or protocol encoding is permitted. Record
validation and its exact schema error text remain unchanged when comparison
and diagnostic use that named constant. Structural binding semantics, policy
data and proposed standing remain unchanged; this declaration neither admits
nor activates policy. Independent guard tests and review follow.

`shared_read_roots` adds shared-document reads to each profile's permitted reads;
it never narrows other profile or inspection reads. In particular the application
inspection grant above permits `src/` and `packages/` even though this list omits
them. All reads still intersect custody/withholding restrictions.

The protected set includes every exclusive dispatch lever and suite/conformance
gate entrypoint named here as exact paths: `ops/bin/dispatch.sh`, its body
`ops/conductor/tools/dispatch.sh`, `ops/bin/go.sh`, `ops/bin/ladder.sh`,
`ops/bin/suites.sh` and `ops/conductor/tools/laneconf.py`. The ladder shim's
`ops/conductor/tools/ladder.py` body remains protected too. Do not replace these
with `ops/bin/` or `ops/devlane/hooks/` prefixes that swallow ordinary tooling.
The register reader and seed are not gate entrypoints; their trusted use is
bound separately below. Unprotected tooling writes require no maintenance grant.

Paths are case-sensitive repository-relative POSIX strings. A trailing `/`
means that directory and descendants; otherwise the match is exact. There are
no globs, absolute paths, empty components, `.`/`..`, backslashes or NULs.
Reject path escapes and symlink traversal beyond the admitted root; check both
lexical and resolved targets, including symlink changes and both rename/copy
endpoints. An unresolvable or unrepresentable path refuses, never disappears.
The Git administrative directory and host paths are never repository grants.
The job's separate `out/` is a launch-owned namespace, not repository `out/` or
a policy path; its isolation and canonical location are launch/task obligations.

## Trusted authority, selection and maintenance

Only the fixed tracked path `ops/lane-policy.json` in the trusted invoking
repository is admissible. The owner admits a full Git commit id and SHA-256 of
that blob's exact bytes through the existing [decisions register](decisions-register.md).
Its host store is `Path.home()/.local/state/<LANE_NAME>/decisions.db`, with the
owner's host HOME, never the worker's isolated HOME. `handwork.db` is a different
grant; `decisions.seed.json` is a birth export, not the live admission store.
Naming this register does not ratify its currently proposed process standing.
The trusted caller supplies the admission decision id from owner custody;
reachability, a worker-created commit, or a matching digest alone is insufficient.
Resolve the canonical repository from trusted invocation, not the worker's cwd
or an environment variable. Read the blob at the admitted commit and verify its
digest; do not read the candidate worktree as authority. Missing/unreadable
admission, mismatched bytes, wrong path or unsupported shape refuses `lane-policy`.

### Reader custody and exact register evidence

Read using the existing `python3 ops/conductor/tools/decisions.py show <id>`
interface. The admitted consumer invokes an independently admitted, immutable
reader checkout, never the current mutable worktree script. Its full Git revision
pins `decisions.py`, its imported `laneconf.py`, `ops/lane.conf` and all repository
imports. Verify these against Git blobs before executing; no candidate files,
bytecode caches, local config or fallback imports may enter this closure.
Use the admitted absolute Python executable and runtime closure, no worker PATH,
PYTHONPATH, startup/site customization or environment overlays. The reader runs
with host HOME supplied by trusted custody; `LANE_CONF`, `LANE_NAME` and all
laneconf environment overrides are absent, and `ops/lane.local.conf` must be
absent in that checkout. Derive the store lane only from its pinned `ops/lane.conf`.
These are caller obligations, not new decisions flags or host settings.

The bootstrap custody anchor binds the owner principal, canonical host HOME and
repository, current admission id, consumer revision and reader tuple
`{"ref": <full Git id>, "runtime_sha256": <digest>}`. The runtime digest hashes
a retained canonical JSON object mapping absolute resolved runtime file paths
to SHA-256 byte digests, sorted keys, UTF-8, no insignificant whitespace. Include
the Python executable, imported standard-library files and loaded native
libraries; independent closure verification must account for all execution
inputs. Verify the retained manifest and files before each read; unavailable
closure evidence refuses `lane-policy`. The reader tuple in admission evidence
must equal this previously trusted tuple; it cannot establish trust in itself.
An owner-admitted replacement is checked by the old consumer/reader before
cutover. Candidate reader, configuration or seed edits may be proposed under
old grants but cannot replace any trusted input or promote themselves at acceptance.
Host custody of the anchor, runtime and live store is an explicit assumption,
not proof of same-UID OS isolation.

`show` returns JSON with exactly `decision` (the existing full row) and `events`
(its ordered history). Require exit 0, empty stderr and strict JSON. A bound
record's digest is SHA-256 of the **exact stdout bytes, including the final
newline and events**, not reserialized JSON or just the evidence field. Re-read
live at every acceptance transition; an event note changes this digest too.
An unchanged authorization can bind a fresh dispatch without a new owner act;
an old dispatch's changed digest cannot be silently refreshed.

For admission and maintenance rows require the requested `id` (register pattern
`^D-[A-Z0-9]+-[0-9]+$`), `home: "ops/lane-policy.json"`, `standing: "ratified"`,
`superseded_by: null`, and `decided_by` equal to the custody anchor's owner
principal. `recorded_by` names the recorder, who may be a conductor; it is not
the deciding principal. Require a nonempty `decided_at` and a matching `ratified`
event (decision id, timestamp, agent = recorded_by, detail = evidence); conflicting
deciding events refuse. A `supersedes` predecessor is allowed only if its live
row is superseded by this id. No candidate or seed row supplies this evidence.

The existing text `evidence` has the exact form `<principal>: <JSON object>`:
the prefix equals `decided_by`, contains no colon or newline, and is followed
by one colon and one space. This respects the existing tool's principal parser.
The remaining bytes parse as one strict JSON object; no prose/hash searching.
All payloads have exactly the common keys `kind`, `policy`, `owner_words` plus
the kind-specific keys below. `policy` has exactly `ref` (full lowercase Git id
for the repository's object format) and `sha256` (64 lowercase hex characters).
They must equal the current admitted policy. `owner_words` is a nonempty verbatim
owner instruction; its authenticity and meaning require trusted recording and
independent review, not a machine claim that arbitrary words grant authority.

| kind | additional evidence keys and checks |
|:--|:--|
| `lane-policy-admission/v1` | `reader` is the tuple above; `standing_grants` is a sorted unique array of existing decision ids, explicitly empty if none. This is the complete standing-grant set for this admission. |
| `lane-policy-standing/v1` | `paths` is a nonempty sorted unique array using the policy's exact/prefix path rules; `purpose` is nonempty text. No job or lineage keys. |
| `lane-policy-job/v1` | `paths` is a nonempty sorted unique array of exact paths (no trailing `/`); `purpose` is nonempty text; `job` equals the dispatch's job name; `dispatch` equals its unique record id; `lineage` has exactly `branch` and `base_sha`, equal to the dispatch lineage. |

These are payload encodings in the existing evidence string, not new register
columns, schema, verbs or flags. Unknown/missing keys, duplicate JSON keys,
non-finite numbers, wrong types, mismatched ref/digest/principal/reader, missing
rows, unreadable output or conflicting evidence refuse `lane-policy`.
Revocation uses the existing supersede transition; there is no invented revoke
verb or status. Proposed, rejected or superseded rows cannot authorize. Notes
neither add nor remove a grant, but change the exact `show` output digest:
existing bindings refuse at the next acceptance transition (`lane-policy`, or
`lane-resume` on write-resume). A fresh dispatch may bind the same unchanged
authority's new digest without a new owner decision; never silently rewrite an
existing bound record. Do not chase a successor as an automatic grant;
the trusted admission must explicitly name the current standing-grant set.
Read and validate every listed standing row before calculating coverage; a
missing or invalid row is not an empty set that licenses a per-job fallback.

Changing the complete standing-grant set requires a fresh ratified admission
listing the replacement set and an update to the custody anchor's current
admission id. Verify both the replacement admission and anchor update using the
previously trusted consumer, reader and authority before cutover; candidate
evidence cannot validate its own replacement. A superseded listed grant refuses
`lane-policy` for protected maintenance until a valid replacement admission and
anchor are in place. Rotation applies to fresh dispatches and never rewrites old
bindings. Dispatching another job already covered by unchanged standing grants
does not rotate the set or require a new owner act.

### Selection and maintenance grants

No `laneconf.get`, `LANE_CONF`, `LANE_POLICY_PATH`, `lane.local.conf`, environment
overlay, scope text or input file can move that authority or select a profile.
Do not add a lane.conf key. The trusted caller's explicit profile selection and
any structured launch/task profile declaration must agree exactly if both are
present. With one, use that one; with neither, use only an explicitly configured
default. Empty, unknown, malformed or conflicting selection refuses `lane-profile`
before a child starts. Never fall back from an invalid explicit value to a default.
Unstructured contradictory prose has no selection effect and requires independent
contract review; a machine must not claim to classify intent from that prose.

Protected writes require both `authority_maintenance: true` and coverage by
verified maintenance evidence. False refuses every protected write (`lane-write`)
even with a valid record. True alone grants nothing. Grant paths must still fit
the admitted profile and every other effective grant term.

For each protected path, first use the union of standing grants named by the
admission. One ratified standing decision records an existing owner instruction
once and applies across jobs while that policy ref/digest and grant remain
current. Its job-specific receipt is derived by the conductor; recording and
applying it is not a fresh owner act. Do not require job/lineage in that standing
decision or ask the owner again for already covered work. The conductor may
record on behalf of the owner only with the owner's actual words attached;
neither this contract nor a recorder can fabricate an owner decision.

Only protected paths outside every standing grant need a per-job decision supplied
by the trusted caller, in the job form above. Its exact paths must be outside the
standing union and bound to that dispatch, job and lineage. Mixed work may use
both forms; an uncovered path with no per-job grant refuses `lane-write`.
Supplied but invalid evidence refuses `lane-policy`, never widens rights. Neither
the worker's scope nor its `Source:` trailer is authorization. Unprotected tooling
requires neither form. Future consumers derive receipts for existing grants;
this assignment creates no live rows or authorization.

Evaluate policy, instruction, job/adapter declaration and gate-source changes
under the *old admitted* policy, declarations and trusted evaluator. Candidate
bytes cannot authorize their own paths, select a new profile, enlarge grants or
replace the evaluator of their result. Independent review/tests must verify the
new boundary and active instruction composition before owner admission of a new
policy ref/digest. The admission applies to fresh launches; it never edits old
bindings. The maintenance profile keeps authorized policy repair possible without
giving ordinary product or tooling jobs all writes. A newly introduced active
instruction source must first be protected by an admitted policy; creating a
file under an ordinary write root cannot promote its instructions to authority.

## Effective grants and dependency evidence

Lane policy owns profile ceilings, dependency exclusions, authority protection,
binding and acceptance requirements. [Launch/task](../devlane/task/CONTRACT.md)
owns job outputs, requested write scope, role limits and adapter capabilities.
Policy consumes those declarations, including outputs **if present**, without a
second job registry, adapter table or jobs schema. Implement the policy side
against contract-verified mock declarations; launch/task can develop its side
independently. Consumer readiness is required only at activation.

For repository paths, effective writes are the intersection of the admitted
profile roots, trusted requested scope, launch/task job grants, role grants and
adapter grants, minus protected paths unless the profile's maintenance boolean
is true and verified standing/per-job evidence covers the path.
For external job outputs, effective writes are declared output paths intersected
with requested scope, role grants and adapter grants, and are empty when the
profile's `allow_declared_outputs` is false. This boolean supplies no path itself.
Every absent grant term contributes the empty set; none means profile roots,
snapshot-wide writes or arbitrary scratch space. In particular, absent output
declarations add no `out/` permission. A job requiring an unavailable grant
refuses before launch (`lane-write`); a read job with no file output may still
return its envelope through stdout. Read roles have no repository writes and
must retain identical snapshot HEAD and no non-exempt residual paths; a declared
and isolatable external output does not relax snapshot immutability.

The caller supplies dependency evidence for runtime imports, autoloaders,
subprocess entrypoints and required services, with source path and resolved
target root, bound to the candidate tree. Check every declared/resolved edge
against `forbidden_dependency_roots`; an edge into a forbidden root refuses
`lane-dependency`. Declared app services resolve to their application source
roots, rather than becoming unclassified permission through a service name.
Evidence must explicitly distinguish measured empty edges from unavailable
analysis; unresolved edges or missing required evidence refuse acceptance.
Static path/entrypoint checks and tests with application/vendor unavailable can
verify concrete couplings. They cannot exhaust dynamic imports, shell-generated
commands or semantic dependencies. Independent review must check closure and
instruction composition under [verified couplings](../../docs/verified-couplings.md).
Literal citations of `vendor/autoload.php` are allowed; requiring that file or
executing application `bin/console` under `dev` is forbidden.

## Binding and acceptance

The proposed `brief.policy` value contains `schema` (as above), `path`
(`ops/lane-policy.json`), `ref` (full admitted Git id), `sha256` (blob bytes),
`admission` (`record`, `sha256`), `profile`, `template_sha256`,
`grants_sha256` and `maintenance` (null or an object with exactly `standing` and
`per_job`, each a list of `{"record": <decision id>, "sha256": <show digest>}`
sorted by unique record id). Null means no protected writes requested; otherwise
bind all admission-listed standing rows (even when none covers this job) and
the per-job rows used for uncovered paths. Empty lists grant nothing.
`admission.record` is the current admission decision id; `admission.sha256` and
every maintenance digest cover the exact trusted `show` stdout defined above.
Digests are 64 lowercase hex characters. The admission digest transitively binds
the reader tuple; retain its runtime manifest with the custody evidence.
`template_sha256` must equal the existing `brief.template.sha256`.
`grants_sha256` hashes the normalized consumed scope, job, role, adapter and output
grants plus maintenance paths: UTF-8 JSON, sorted object keys, sorted unique path
lists, no insignificant whitespace. It is a derived receipt, not a new source of
job declarations. Capture the declaration sources and their digests with the
receipt so the trusted caller can reproduce it. Bind it alongside the existing
job, role, harness, snapshot ref and lineage identity; do not repurpose `lane`.
Render the selected policy summary into the brief whose exact bytes are hashed.

The trusted consumer recomputes this binding from admitted sources before each
transition. Current admitted policy ref/digest, selected profile, template,
declaration sources and effective grants must remain stationary. A worker-supplied
binding or receipt cannot substitute for that comparison.

| transition | obligation before accepting or advancing |
|:--|:--|
| fresh launch | validate admission, selection, required grants and known dependency edges before minting an attempt or starting a child |
| settle/close, including write-resume settlement | validate binding and all committed delta plus residual artifacts before automatic commit or dispatch-ref publication; dependency evidence must match the candidate |
| write-resume | revalidate stationary binding and existing tree before clearing exit/trip markers, restoring stores, changing attempt count or starting a child; re-run full settlement afterward |
| collect | reject unbound/mismatched work before verification; recheck after verification (it may write), before commit/ref/collected-marker mutation; successful verification alone grants nothing |
| land and ladder stage acceptance | independently revalidate admitted policy, binding and artifact evidence against the exact fetched/result head and lineage before cherry-pick or stage advance; missing evidence is non-acceptance, including collected results |

Settlement consumes `launch.py`'s existing `_residual_entries`, including its
`git ls-files --others --ignored --exclude-standard -z` measurement. `git status
-uall` alone does not see ignored files. Preserve NUL-delimited path authority,
binary changes, deletions and both rename/copy endpoints. Check committed changes
from snapshot ref to result head as well as residuals. Retain only the already
enumerated cache-shape exemptions in the [dispatch contract](../devlane/dispatch/CONTRACT.md#collect);
unknown cache layouts and source files in cache-named directories remain checked.
These exemptions do not bless cache contents as trusted execution inputs.

Acceptance evidence is launcher-produced `result.policy`, exactly the nine-member
[PolicyResult](#candidate-evidence-and-policyresult) below. Its artifacts,
dependencies and outputs are `{path,sha256}` handles, not bare digests; only
refusals permit unavailable evidence as null. Its binding is the unchanged worker
brief.policy or ConductorBinding below. The manifest covers paths, statuses, modes,
content digests and rename endpoints; the dependency evidence and manifest are
retained beside the record with byte digests, so consumers can rederive them.
An absent or stale receipt never means pass. Consumers verify provenance and
recompute from the bound tree; they do not trust a worker's manifest or verdict.
Failure preserves diagnostic artifacts and marks policy non-acceptance without
publishing an accepted ref. A worker's `status: ok`, `verdict: approve`, exit 0,
or `collect --verify` success cannot override this gate or the existing gates.

## Startup coupling declarations — adoption unit 1a

This completes only the pending documentary edges from adoption review
`20260906T102712Z-plan-codex-bc6012`. The evaluator remains the future stdlib
library named below. No policy admission, runtime, test, registry, host grant
or state change follows from these declarations. C1/C2 and the accepted
worker brief.policy binding above retain their schemas and owners. PolicyResult
below is the sole result.policy shape. In particular the
existing optional `brief.policy` structural validator is not replaced.

### Trusted binding input and declaration transport

`bind_launch(trusted_context, declarations)` returns a verified bound context
or an owning refusal. `check_transition(binding, candidate_evidence, transition)`
returns the PolicyResult below. These are in-process library calls, no CLI.
Dispatch owns acquisition, retention and record serialization; task supplies
[validated declarations](../devlane/task/CONTRACT.md#unit1a-declaration-transport).
Policy consumes values, never imports task/harness code, invokes a worker,
loads candidate files as authority or creates a second declaration registry.

`trusted_context` holds the previously admitted custody anchor, reader/runtime
closure and exact register evidence defined above; canonical repository and
job-root handles; reserved DispatchID, job, stage, snapshot ref and frozen
lineage; explicit selection and its provenance; and retained parent binding
on resume. It comes from the admitted invoking consumer through a direct
argument or inherited read-only descriptor, never a worker/MCP parameter,
caller-chosen file, environment variable or scope prose. A digest checks held
bytes for equality; it cannot prove their origin. Read authority using the old
admitted consumer/reader before considering candidate changes. No source or
self-consistent payload can replace that custody anchor.

The declaration transport is the task-owned closed `LaunchDeclarations` object.
Its worker selection is `TaskSelection`; conductor startup retains the distinct
[LaunchSelection](conductor-start-plan.md#trusted-conductor-launch-descriptor),
with conductor role and null job_role, never a fabricated worker job. Its record
obeys dispatch's [complete variant](../devlane/dispatch/CONTRACT.md#canonical-conductor-record-variant-proposed):
stage conduct, job null, brief null. LaunchDeclarations.selection accepts that
non-worker branch without a catalogue job. Profile
resolution follows the existing explicit/declaration/default rule above; before
binding, resolve one profile and require every supplied selection to agree.
Missing required model, unsupported effort, conflicting role/job_role or harness
refuses under its owning selection contract. Neither infrastructure lane nor
harness implies profile or model. Required profile ids must be representable by
all participating interfaces; incompatible configuration refuses, never truncates.

Task/dispatch retain exact source bytes, source refs and digests for selection,
requested scope, job/role/adapter/output grants and transport reservations.
Candidate catalogue edits, brief inputs, output handles and model prose grant
nothing. Sources are verified against the old admitted declaration closure,
not merely a path in a trusted-looking directory. Hold the consumed values and
bytes in memory until admission, then retain them outside worker writes beside
the existing record. Resume reuses the original declaration and sources; a new
catalogue never widens its grant. All subsequent transitions rederive the binding
and demand stationary source identities and live register digests.

For `grants_sha256`, derive exactly
`{scope,job,role,adapter,outputs,maintenance}`. The first five values are closed
`{repository:[path,...],outputs:[relative-output-path,...]}` sets. Repository
paths use this policy's exact/prefix grammar; output paths use C2 leaf/subtree
semantics from the validated declarations. Each list is sorted and unique;
`outputs.repository` is empty. The complete producer mapping is normative:

| normalized key | producing value and exact derivation |
|:--|:--|
| scope | LaunchDeclarations.requested_scope |
| job | LaunchDeclarations.job_grants |
| role | LaunchDeclarations.role_grants |
| adapter | LaunchDeclarations.adapter_grants |
| outputs | `{repository:[],outputs:sorted(unique(entry.path for entry in LaunchDeclarations.outputs))}`; include every validated C2 file and scratch path |
| maintenance | sorted unique union of payload.paths from every verified standing/per_job row named in binding.maintenance; null binding.maintenance yields `[]`; derive payloads from retained exact show bytes using the register recipe |

Copy the first four sets by value after validation. Sorting/uniqueness specifies
encoding, not repair of invalid duplicate declarations. DeclarationSource
component scope covers requested_scope, job covers job_grants, role covers
role_grants, adapter covers adapter_grants and outputs covers the original C2
array. Neither prompt prose nor C2 demand supplies the four independent ceilings.
The maintenance union includes complete paths of every bound row, not only
changed paths or a path expansion; coverage and intersection still limit writes.
Here binding.maintenance means worker PolicyBinding.maintenance or
ConductorBinding.policy.maintenance, respectively; it adds no new binding key.

Output tokens retain exact C2 spelling: file means one leaf, scratch means its
subtree, without appending a slash, wildcard, parent or absolute root. Kind and
operations come from the retained original C2 array and admitted witness; the
path digest alone cannot distinguish kinds. Every transition verifies stationary
source identities and original declarations even if normalized paths agree.
Reservations and sources are retained/validated, not extra keys in this hash;
transport reservations never confer worker writes. Missing source grant terms
supply empty required sets, and source output omission supplies `outputs:[]`.
A missing LaunchDeclarations key refuses; no term defaults to profile ceilings.
This object is consumed grant input, not a union of effective permissions:
apply both existing intersection formulas and C2's G=D and A=D requirements.
External job demand is the C2 array; job_grants remains an independent repository
term, not an added external-output term absent from the accepted formula.
Hash UTF-8 JSON with sorted keys, ASCII escaping, compact separators and no
newline. This fixes the byte encoding of the existing grants receipt without
adding keys to `brief.policy`; retain the declarations and sources alongside
it for rederivation. The selected profile and full binding are held separately.

`bind_launch` returns a trusted handle containing these normalized grants,
verified sources, effective grants and the unchanged `brief.policy` for a
worker. It also binds selection, dispatch/job/lineage and canonical roots.
Conductor binding covers those duties with closed ConductorBinding, all keys
required:
`{policy,selection,dispatch,stage,job,lineage,snapshot_ref,template,declarations_sha256,custody_sha256}`.
policy has exactly the unchanged nine worker PolicyBinding keys above. template
is `{path,sha256}` naming the admitted repository-relative startup template leaf
and exact byte digest; policy.template_sha256 equals template.sha256, not a
nonexistent brief.template. The template's Git source is covered by the role
DeclarationSource closure. selection is LaunchSelection; dispatch is the reserved
id; stage is conduct; job is null; lineage and snapshot_ref equal the record.
declarations_sha256 hashes canonical LaunchDeclarations; custody_sha256 hashes
retained trusted caller evidence and equals the descriptor's custody digest.
policy.grants_sha256 uses the mapping above. Conductor job/role/output grants
are empty and policy.maintenance is null: no worker job or per-job maintenance
row is fabricated. Direct source/output writes are forbidden; coordination acts
use separate lever authority. The descriptor's policy_binding_sha256 hashes
these exact canonical ConductorBinding bytes, without runtime handles, secrets
or circular descriptor hashes. The record's conductor.binding handle retains
those same bytes. Verify custody; matching a digest alone is insufficient.
Neither tool nor worker may construct the handle from a claimed receipt.

### Candidate evidence and PolicyResult

All newly serialized objects here are closed strict UTF-8 JSON with all listed
keys required, including nullable keys. Reject duplicate/unknown/missing keys,
non-finite numbers and wrong types (bool is not integer). Digests are lowercase
64-hex; Git ids use the repository object format; time is UTC
`YYYY-MM-DDTHH:MM:SSZ`. Canonical JSON follows the grants encoding above; file
and register digests still hash exact bytes. No raw secrets enter receipts.
Evidence handles are exactly `{path,sha256}`, with a verified job-relative leaf
beneath existing launcher-owned transport, outside worker writes and declared
outputs; hash exact retained bytes. A handle conveys no authority to read a
caller-selected path. Verify custody, type, containment and digest on consumption.

`candidate_evidence` is exactly `{snapshot_ref,head,artifacts,dependencies,outputs}`:
snapshot_ref is the known selected Git id; head is the measured candidate Git id,
null only when unavailable on refusal. artifacts/dependencies/outputs are held
ArtifactManifest/DependencyEvidence/OutputAttempt values in this call; dispatch
resolves retained handles to verified values before calling. Each evidence value
may be null only for unavailable evidence on refusal, never for a pass;
outputs is the dispatch-owned [attempt receipt](../devlane/dispatch/CONTRACT.md#unit1a-attempt-and-result-transport).
Until an attempt is registered its attempt is null and files is empty, even
after preparation effects. This is measured absence, not an invented attempt.
Missing or unavailable evidence is explicit, never a successful empty manifest.

ArtifactManifest is exactly `{snapshot_ref,head,entries}`. An entry is exactly
`{layer,status,before,after}`; layer is `committed,index,worktree,output` and
status is `A,M,D,T,R,C,?,!`. Each endpoint is null or exactly
`{path_b64,mode,sha256}`. path_b64 is canonical padded base64 of raw relative
path bytes in that layer's namespace (repository or canonical output root).
Modes are Git mode strings `100644,100755,120000`; hash file content or symlink
target bytes without following it. Unsupported types/modes or paths that cannot
round-trip refuse `lane-artifact`, never disappear. A/?/! have only after, D
only before, M/T both with equal paths, R/C both with distinct paths. Sort entries
uniquely by layer, decoded before path, decoded after path, status (null first).
Measure snapshot-to-HEAD, HEAD-to-index, index-to-worktree and all declared
external outputs, including ignored/untracked residuals, binary content, modes,
deletions and both rename/copy endpoints. Keep only the existing cache exemptions.
Check lexical and resolved paths under the existing policy/custody rules;
output symlinks refuse C2 even when their bytes can be represented in a manifest.
At launch inspect the selected Git tree before snapshot creation; an empty delta
has entries empty explicitly, not unavailable inspection. At every later check
include the actual candidate and residual layers; a changed layer voids evidence.

DependencyEvidence is exactly
`{snapshot_ref,head,artifacts_sha256,producer,coverage,edges,unresolved,unavailable}`.
Refs equal ArtifactManifest's refs; artifacts_sha256 hashes its canonical bytes.
producer is a verified Git DeclarationSource naming the admitted analyzer,
using the task source shape with component `dependencies`. coverage is a sorted
unique array of inspected repository paths under policy path rules. Edge is
exactly `{source_b64,kind,target_space,target_root}`; source_b64 uses the manifest
path encoding, kind is `import,autoload,subprocess,service`, target_space is
`repository` or `host`. A repository target_root follows policy path rules;
a host target_root is a verified canonical absolute root from an existing
external coupling, not a new host grant. Resolve application services to their
application source roots. An unresolved row is exactly `{source_b64,kind,reason}`,
with a nonempty reason. Sort edge/unresolved arrays uniquely by canonical JSON.
unavailable is null or a nonempty explanation of unavailable analysis.

Dispatch acquires evidence from the admitted analyzer, not worker assertions.
Required coverage comes from the held job/runtime declarations, requested source
scope and resolved transitive targets; it cannot be reduced by the candidate's
coverage claim. Measured empty edges requires explicit complete coverage,
edges/unresolved empty and unavailable null. Unresolved edges, unavailable
analysis or missing required coverage refuse `lane-dependency`; any forbidden
resolved edge also refuses. Analyze before launch using the selected source,
and remeasure the exact candidate plus residual artifacts before each acceptance.
Static evidence still cannot prove arbitrary dynamic closure: independent
coupling and active-instruction review remain required, with no Symfony runtime
dependency for dev tooling. Evidence availability is never inferred from silence.

PolicyResult is exactly
`{binding,snapshot_ref,head,artifacts,dependencies,outputs,checked_at,outcome,refusal}`.
binding equals unchanged brief.policy for a worker or ConductorBinding for a
conductor, selected by trusted record role, never by the receipt's claim.
snapshot_ref equals the known selected ref; head equals the measured candidate,
null only on refusal when it cannot be measured. artifacts/dependencies/outputs
are verified retained handles, or null only on refusal when evidence could not
be obtained and retained. Held-but-unpersisted bytes justify no handle. outcome
is `pass` or `refused`. refusal is null on pass, otherwise the full single-line
owning expected/found/needed refusal. A pass requires all handles, non-null matching
refs/digests, complete evidence and the independently recomputed binding.
The launcher serializes this value as `result.policy`; the worker envelope has
no policy field and cannot supply it. Keep prior transition/attempt receipts
outside worker writes; updating the current result pointer never overwrites
past evidence. An unavailable receipt is non-acceptance, not an empty success.

`transition` is one of `launch,settle,resume,collect-before,collect-after,land,ladder`.
The existing transition table supplies each timing obligation, including full
settlement after write-resume. At launch return held evidence and the prospective
receipt in memory: its handles designate held exact bytes and reserved relative
names, not existing files. Persist and verify those bytes only after successful
preflight, before the child. Until then this prospective value is not a retained
acceptance receipt and no consumer may treat its handles as readable evidence.
At settlement, check before automatic commit and remeasure the resulting head
before ref publication; a changed head cannot inherit the pre-commit receipt.
Collect checks both sides of verification and again after any commit before
ref/marker publication. Land/ladder independently recompute against the exact
fetched head and lineage; they do not rewrite the dispatch's old result receipt.
A refused collect preserves its existing record/ref/precondition guarantees and
retains a separate refused diagnostic receipt in existing launcher transport.

### Pre-effect rejection and late refusal

Fresh launch reserves the canonical id in memory before job-bound authority
checks, as [dispatch specifies](../devlane/dispatch/CONTRACT.md#unit1a-pre-effect-admission).
No attempt, snapshot, prompt, record, output leaf, home or session is created
before successful admission. Resume refusal likewise precedes marker/store/cap/
attempt mutations. A pre-act lane rejection returns exit 2, empty stdout and
one refusal line on stderr. Suppress launch acknowledgements until admission.

For each invocation, pre-effect admission ends at its first successful durable
mutation. Fresh launch crosses at the atomic job-directory claim, even without
a snapshot, attempt, child or artifact. A failed claim creating nothing stays
pre-effect. Resume crosses at the first marker/cap/attempt/destination/store
mutation; collect crosses when verification starts (it may write), or at any
earlier mutation. Prior attempts alone do not make a new resume/collect preflight
failure late. Read-only checks and in-memory reservation are not effects.
After this boundary, failure of a required policy check is a late refusal:
record the same refusal line in a refused PolicyResult,
retain partial evidence and the last reading, and stop acceptance with Abort
exit 3. Do not promise to erase already emitted stdout or report a prelaunch
rejection. A completed worker result becomes invalid with null verdict and a
note naming the refusal; an existing tripped result stays tripped and cannot
approve. With no worker response, result.envelope is null in dispatch's partial
record variant, not a fabricated deliverable. A conductor uses result.status
and a null envelope. Without an attempt, keep attempts empty and
OutputAttempt.attempt null. Retain an empty OutputAttempt only if its bytes were
actually produced and retained; otherwise PolicyResult.outputs is null. Missing
artifact/dependency evidence is null, never a fabricated empty manifest. Known
binding and snapshot_ref survive from admitted inputs; unavailable head is null.
On a record-path collision, retain diagnostics only in this launch's claimed
directory, never overwrite another record. If diagnostic storage itself fails,
report unavailable durable evidence on stderr with the last reading and Abort 3;
do not claim a retained receipt or accepted result.
No automatic commit of work, accepted ref, collected marker or ladder advance
follows a refused check. Durable diagnostics do not make the result accepted.
Detached acknowledgement certifies only admitted launch; its later outcome is
read from the canonical record. Existing unrelated dispatch/C2 exits are outside
this clarification. No override or permission prompt repairs a lane refusal.

### Closed-shape walkthroughs

These are synthetic construction duties, not runtime observations or admission.
Every success below includes trusted source equality and full required dependency
coverage; an empty dependency edge array alone is not that evidence.

| path | producer to consumer, including terminal shape |
|:--|:--|
| worker success | Task validates C2 check-tests file SKEPTIC.md and scratch tmp, retains that ordered array in LaunchDeclarations; policy copies requested_scope/job_grants/role_grants/adapter_grants to scope/job/role/adapter and derives outputs `{repository:[],outputs:["SKEPTIC.md","tmp"]}`. With no maintenance, maintenance is `[]`; canonical bytes produce grants_sha256 in unchanged brief.policy. Dispatch reserves a check-tests id, checks held manifests and OutputAttempt `{dispatch:<id>,attempt:null,files:[]}`, claims the directory, retains and verifies the prospective launch handles, then registers attempt 1. Settlement retains the nonempty file's fresh-production evidence and one file receipt (no scratch receipt); PolicyResult has all three handles, exact binding/refs, outcome pass and refusal null. Task validates the worker envelope and exact artifact handle; read-role settlement enforces unchanged source. Read jobs do not gain collect/land authority. |
| conductor success | Startup supplies LaunchSelection, stage conduct, null job/brief, empty job/role/C2 grants and admitted sources. Policy derives the same six normalized keys and ConductorBinding; its canonical digest is descriptor.policy_binding_sha256. Dispatch retains descriptor/binding/declarations/custody handles in the conductor record. Before an attempt, OutputAttempt is the measured null/empty value; after client launch and verified MCP/onboarding, it has attempt 1 and files `[]`. Settlement consumes complete ArtifactManifest/DependencyEvidence and OutputAttempt, with unchanged head and no source residue. Terminal record status closed, result.status ok, result.envelope null, result.policy pass; status/wait consume this branch. Resume retains identity and rechecks connection; collect/land/ladder refuse it as worker-stage evidence. |
| pre-effect rejection | A profile conflict, foreign job-bound grant or invalid declaration returns its owning refusal from bind_launch before any claim. No canonical record, attempt or retained PolicyResult is invented; exit 2, empty stdout, one expected/found/needed stderr line. A refused resume/collect preflight likewise preserves prior record/receipts unchanged. |
| late preparation refusal | Claim succeeds, then required custody/retention fails before an attempt. attempts remains `[]`, result.envelope null, terminal record closed and PolicyResult refused; conductor result.status is invalid. Binding and snapshot_ref are held, head is null if not measured; each evidence handle is retained or null individually. OutputAttempt may still have null attempt/empty files, but a handle is emitted only if those exact bytes were retained. Return Abort 3, preserve own partials; a colliding record is never overwritten. |
| late attempt / collect refusal | A real attempt or collect verification writes outside grants, or actual-client MCP verification fails. Preserve measured manifests, partial OutputAttempt and refused PolicyResult. An existing worker envelope becomes invalid/null-verdict with note, tripped stays tripped; absent envelope stays null. Conductor uses result.status invalid/tripped. A refused collect keeps its prior record/ref and a separate diagnostic receipt. No accepted commit/ref/marker/advance follows. |

A launch pass with null OutputAttempt.attempt certifies preflight only, not a
completed deliverable. Settlement success requires a real completed attempt and
all declared files, so a launch receipt cannot stand in for terminal evidence.

### Conductor tool counterpart

`evaluate_tool(bound_role, normalized_request)` is owned by the same future
`ops/conductor/tools/lane_policy.py` library. It consumes exactly Request and
returns exactly Decision from
[permission-surface.md](permission-surface.md#normalized-conductor-tool-interface),
including conductor-tool/1, conductor-decision/1, all tagged target/arguments
schemas, fixed Reason codes, nullable refusal and exact id/binding equality.
Those shapes have one owner; do not fork them or add a second evaluator.
bound_role is the verified descriptor/policy/tool-declaration handle supplied
by trusted startup, never a model-selected role. Validate current binding,
source custody and the target's closed schema/effect contract before deciding.

Permit admitted custody-scoped reads, read-only MCP, authorized coordination and
canonical dispatch/go/ladder delegation. Superseded 2026-09-16 (D-MCP-1): MCP
acts as well as reads. Reads require no WF_AGENT or fresh
owner grant; an unavailable source is an unavailable reading. Coordination
writes retain the existing lever authorization and owner-requested handoff/AAR
boundary. Refuse implementation edits, Git mutations, shell/interpreter escapes,
raw vendor execution and in-session agents before effects. A matching command
substring or claimed classification grants nothing. The pinned normalizer must
resolve actual native/executable identity; adapters enforce only an exact,
current, once-consumed decision, blocking on missing/malformed/late/replayed
responses without prompts or weaker retries. This is the policy counterpart of
the existing tool contract, not MCP action authority. Superseded 2026-09-16
(D-MCP-1): MCP acts as well as reads. The independent read-only
doctor remains usable without this evaluator, startup or a vendor session.

Unit1a's independent construction cases cover transport/source substitution,
selection axes/defaults, absent versus explicit empty grants, in-memory id and
collision timing, job-bound evidence mismatch, complete-empty versus unavailable
dependencies, ignored/binary/rename artifacts, output production/reuse across
attempts, forged/stale result.policy, every transition and late refusal. Preserve
P1–P5, N1–N10, R1–R5 and I1, plus permission-surface's tool cases. Synthetic
schema/examples and link checks only establish documentary consistency; separate
producers still own executable tests, verified mocks and their approval. Complete
activation and per-harness live evidence remain the existing requirements below.

## Refusals and containment limits

Before effects, the new classes use [the wire](../../docs/wire.md#the-refusal-wire):
exit 2, empty stdout, one stderr line with nonempty expected/found/needed.
These classes are not overridable. Existing unrelated dispatch refusal exits
are outside this change. After effects, the
[late refusal rule](#pre-effect-rejection-and-late-refusal) returns Abort 3 and
records invalid/non-acceptance on the durable result, preserving prior partials.

| class | condition |
|:--|:--|
| `lane-profile` | missing, empty, unknown or conflicting profile selection |
| `lane-policy` | invalid/untrusted/missing authority, binding or declarations |
| `lane-write` | requested or measured write outside effective grants, including unapproved protected edits |
| `lane-dependency` | forbidden runtime edge or unresolved required dependency evidence |
| `lane-artifact` | incomplete, unreadable or mismatched artifact/acceptance evidence |
| `lane-resume` | missing or changed binding on resume; refuse before attempt mutations |

Example: `lane-write: expected writes within prod grants; found ops/bin/example.sh; needed a fresh tooling dispatch with admitted scope`.
Within one check use authority, selection, grant, dependency, artifact order;
resume maps a missing/changed binding to `lane-resume` first. Name actual paths
and digests in diagnostics, not secrets. A forbidden operation is refused, not
converted to an owner-permission prompt.

Profile path limits are acceptance checks unless a measured adapter enforces
those exact roots before writes. The existing Codex sandbox label alone does
not prove profile-specific prevention. Claude/Grok policy containment must be
reported as detection at acceptance, not OS prevention. Same-UID host access,
transient writes erased before settlement and undeclared dynamic behavior are
not proven contained by an artifact diff. Trusted invocation/custody is an
assumption; hostile work requires the separate isolation boundary.

## Bootstrap and minimum complete activation

This proposal lands using the existing owner authorization and existing process;
it does not require a policy binding that no consumer yet creates. Its record
remains honestly unbound. Contract review, independent tests, implementation and
review of the activation candidate occur under that same explicit bootstrap
custody, without describing their old sessions/worktrees as enforced.

Before switching acceptance on, the owner admits the independently verified
policy blob, trusted consumer revision and reader/runtime custody anchor in a
durable decision identifying the bootstrap candidate, evidence and cutover.
Use the exact admission and standing evidence forms above; the initial reader
is verified under bootstrap custody before it reads that admission. Existing
owner instructions may be recorded once by the conductor with their actual words,
without a new decision on already authorized work. This is the initial trust anchor,
not an environment toggle, a worker bypass or an automatic admission of every
old result. Gate-source changes thereafter are evaluated by the previously
admitted consumer; candidate code cannot validate itself for landing.

The minimum complete activation is one coherent operational boundary: trusted
policy loading and selection, grant/dependency evaluation, `brief.policy` and
`result.policy` validation, pre-launch checks, settle/close, pre-write-resume and
post-resume settlement, collect before/after verification, and land/ladder
refuse-if-unbound. Needed launch/task grants must be available and verified for
each enabled job/adapter combination; absent declarations cannot be synthesized
from profile ceilings. Unsupported combinations refuse while supported ordinary
tooling maintenance remains usable. The evaluator is a stdlib library at future
`ops/conductor/tools/lane_policy.py`, no CLI or profile-id literals, and no
`instrument-conventions.md` docstring marker that would falsely register a CLI.

At cutover, old records keep their bytes and historical status. Reading them
remains legal. They cannot be resumed for writing, collected for acceptance,
landed or used to advance a ladder without policy evidence; do not backfill it.
To continue old work, create a fresh admitted dispatch and independent evaluation
of its proposed delta, retaining the old id as provenance. Old commits already
landed remain history, not retroactive proof of enforcement. Cutover must block
old callers from the acceptance route; partial consumer deployment cannot be
advertised as active. No Symfony runtime is a prerequisite for this process.

Instruction-digest pinning, hooks, doctor, MCP and richer jobs capability rows
may follow. Active instruction composition must already honor current owner
precedence; deferring digest machinery cannot restore replacement doctrine.
Existing suite/conformance gates remain mandatory for executable delivery.
There is no new executor, configuration registry or MCP action here.

## Independent acceptance cases

Future test ownership (none of these tests is authored in this slice): new
`ops/conductor/tests/test_lane_policy.py` for data/selection/grants/dependencies;
new `ops/devlane/dispatch/tests/test_launch_lane_policy.py` for launch/settle/resume;
extend `ops/devlane/dispatch/tests/test_record.py` and `test_launch_collect.py`
for records and recovery, `ops/conductor/tests/test_land_lever.py` and
`test_ladder_lever.py` for acceptance, and `test_onboard_lever.py` for instruction
composition. Use synthetic repositories and mock launch declarations, never
vendor sessions. Run `ops/bin/suites.sh`, including existing laneconf closure,
literal and instrument-conformance gates. Register any new CLI refusal surface
in the same change that introduces it; do not change laneconf's wire here.

| case | prescribed result (all unmentioned grant terms explicitly present) |
|:--|:--|
| P1 | `dev` writes `ops/bin/example.sh` and ordinary hook tooling without maintenance evidence, reads `docs/wire.md` and `src/Example.php` to describe Symfony: pass; repeat tooling with application/vendor absent and tool still operable |
| P2 | `prod` writes `src/Example.php` and `composer.json`: pass; same writes under `dev`: `lane-write`; `prod` writing `ops/bin/example.sh`: `lane-write` |
| P3 | add synthetic profile `fixture-only` with `write_roots: ["unittest/"]`, `forbidden_dependency_roots: ["vendor/"]`, both booleans false, under an admitted fixture policy: `unittest/example.txt` passes; `src/Example.php` and a valid present declared external `out/` write both refuse `lane-write`, consumers unchanged |
| P4 | synthetic host register `show` admission names a ratified standing grant for `ops/lane-policy.json` and `docs/`; `authority-maintenance` changes `ops/lane-policy.json`/`docs/wire.md`: pass under old grants on two distinct jobs with the same standing record and no fresh owner act; `ops/README.md` outside that grant refuses without a per-job row, passes with an exact matching job row; mixed covered/uncovered changes pass with both forms; identical edits with no grants refuse `lane-write` |
| P5 | candidate `decisions.py`/seed edits under ordinary `dev` grants may pass as artifacts; trusted reads still use the admitted reader and host store, never those candidate bytes |
| N1 | absent selection with this default-free policy, unknown/empty id, or flag/spec conflict: `lane-profile` before child/attempt creation; a valid configured default works only when selection is absent |
| N2 | environment, laneconf overrides, scope/input text and resume prompts cannot change policy location, digest or profile; candidate policy widening cannot permit its own product write |
| N3 | `dev` runtime dependency on `vendor/autoload.php`, app service or `bin/console`: `lane-dependency`; textual inspection alone passes; unresolved required edges refuse |
| N4 | committed, untracked and ignored product writes under `dev`, including rename endpoint and symlink escapes, refuse; unknown cache/source layouts are not exemptions |
| N5 | `ok`, `approve`, exit 0 or collected verification cannot override missing/mismatched/refused policy evidence in land or ladder |
| N6 | missing output declaration gives no output grant; read snapshot stays immutable even with isolated declared `out/`; missing job/adapter scope grants never widen rights |
| N7 | `dev` writes any exact gate path `ops/bin/dispatch.sh`, `ops/conductor/tools/dispatch.sh` (actual lever body), `ops/bin/go.sh`, `ops/bin/ladder.sh`, `ops/bin/suites.sh`, `ops/conductor/tools/laneconf.py`, or the already protected `ops/conductor/tools/ladder.py`: `lane-write`; `dev` plus a valid maintenance record writes `ops/README.md`: `lane-write`; true-valued maintenance without coverage also refuses; P1 ordinary tooling still passes |
| N8 | `standing: "active"`, any other non-`proposal` value, duplicate keys, wrong boolean types or malformed shared-read paths: `lane-policy` |
| N9 | forged candidate reader/config/seed output or worker HOME store cannot supply admission; mismatched reader/runtime, output byte digest (including changed events/newline), owner principal, policy ref/digest, malformed evidence, missing/conflicting/rejected/superseded rows: `lane-policy`; no successor chasing or per-job fallback for an invalid standing row |
| N10 | per-job evidence reused for another dispatch/job/lineage, or covering a path inside the standing union: `lane-policy`; absent per-job coverage outside the union: `lane-write`; read permission cannot bypass custody |
| R1 | stationary binding and exact artifact/dependency evidence revalidate on settle/resume/collect/land/ladder |
| R2 | changed admitted policy/profile/template/declarations, or legacy unbound write-resume, refuses before marker/store/attempt mutation; collect verification that introduces a forbidden write cannot commit/publish |
| R3 | unchanged legacy records remain readable but cannot land/advance; fresh dispatch preserves provenance; bootstrap is explicit and candidate evaluator cannot accept itself |
| R4 | supersede a listed standing grant: protected maintenance refuses `lane-policy`, even with a valid per-job row; a replacement admission without the verified anchor update still refuses; ratify a fresh admission listing the complete valid replacement set and verify it and the anchor update using the previously trusted consumer/reader/authority: fresh dispatch with covered paths passes, and a second covered job passes without another owner act; old bindings stay unchanged |
| R5 | add a note to a bound admission or grant: authority is unchanged but exact `show` bytes differ; the existing dispatch refuses `lane-policy` at its next settle/collect/land/ladder transition or `lane-resume` before write-resume mutations; a fresh dispatch binds the unchanged authority's new digest and passes without a new owner decision; the old record and digest remain unchanged |
| I1 | active instructions reintroducing mandatory app twins/retirement or app runtime dependencies fail independent review; superseded historical quotations and legitimate product contracts remain allowed |

Docs/data acceptance here is only `git diff --check`, JSON parsing, changed-link
verification and example consistency. Those checks are not runtime acceptance.

## Measured seams and blast radius

Correction inspection at `16aa2d26df2d3ddb5ece9bc9d067b019a9b147c2`:
`cat ops/conductor/tools/decisions.py ops/conductor/tools/laneconf.py
ops/conductor/decisions-register.md ops/lane.conf` measures the existing row,
principal parser, `show` serialization, host-store path and configuration/import
precedence. No host store was read and no register standing was changed.

Inspection at `b9cec26be704c1ef0f993bf96331c7eeef68785e`: `sed -n
'1614,1695p;1840,1865p;1920,1950p' ops/devlane/dispatch/launch.py` identifies
residual enumeration, settlement, write-resume and collect seams; `sed -n
'195,232p' ops/conductor/tools/land.py` and `sed -n '705,730p'
ops/conductor/tools/ladder.py` show the existing acceptance decisions.
`sed -n '1,90p' ops/devlane/dispatch/record.py` confirms infrastructure identity
and record validation. `sed -n '135,145p;170,198p;310,339p;363,414p'
ops/conductor/tools/laneconf.py` measures environment precedence and conformance
constraints; `sed -n '1,65p' ops/bin/suites.sh` measures the suite entrypoint.
These are source observations, not live enforcement or vendor containment probes.
The verification frontier is those consumers, launch/task declaration adapters,
instruction composition and policy data; a changed endpoint re-demands its
contract-verified mocks and independent tests under the shared coupling law.
