# Discovery lever — contract

Standing: corrected step-1 proposal under the
[review ruling](reports/discovery-contract-review-20260905.md), awaiting
independent review. Authorization to write is not admission or self-approval.
The corrected v3 obligations require independent step-2 acceptance. The
[sequence](conductor-start-plan.md) and this contract inherit
[instrument conventions](instrument-conventions.md) and the
[wire](../../docs/wire.md).

## Audience and ownership

This is the conductor discovery surface. Role (conductor/worker), work
profile (dev/prod/additional configured profiles), and harness are independent.
Trusted launch/configuration selects the audience and its allowed sources.
This is an assumption about launch custody, not proven host isolation.
`--config FILE` is that launch-supplied trusted descriptor, like the anchors;
it is not a query argument. Discovery queries/resources accept no config,
audience, role, repository, path, command, or consumer switch. Starting a
process with the wrong FILE is a launch defect, not a query defect.
`conduct` starts conductors; the existing dispatcher starts workers for named
jobs. A worker must not inherit conductor sources through a shared harness or
profile. A separately bounded worker context/MCP follow-up belongs to the
[task contract](../devlane/task/CONTRACT.md) and
[harness context](../devlane/harness/context.cue): brief, authorized inputs,
relevant contracts, diagnostics/checks and outputs, with withheld material
excluded. Both roles reuse services with different catalogues and source grants.

These are existing dev-lane services under `ops/`, independent of the Symfony
application and never awaiting replacement by it. The latest owner scope in
the [reconciliation note](reports/discovery-reconciliation-20260906.md)
supersedes earlier framework targets in this draft and its cited reports.
Prod discovery describes application ownership; it imposes no application
dependency on discovery, dispatch, policy or worker tooling.

Two role-specific MCP server entrypoints/instances from one codebase remain
a proposed deployment boundary; writable conductor tools remain a separate
proposal requiring explicit revision of the standing read-only MCP contract.
Superseded 2026-09-16 (D-MCP-1): MCP acts as well as reads. Neither is
admitted here. The parallel worker consolidation track precedes
conduct startup, not discovery; its contract still needs admission. The
`lane/lane-boundary-20260905` enforcement track is pending, not implemented
policy that discovery can claim to provide.

Extend `tools/lane_read.py` and `tools/lane_mcp.py`, not a second backend.
[lane-mcp-lever.md](lane-mcp-lever.md) and [lane-mcp-v2.md](lane-mcp-v2.md)
remain the v1/v2 baselines, including their existing limitations and payloads.
The four new queries are the only v3 extension. MCP stays read-only:
`readOnlyHint=true`, `destructiveHint=false`. Superseded 2026-09-16
(D-MCP-1): MCP acts as well as reads generally; this v3 query surface
stays read-only. Reads require no identity.
MCP visibility is not enforcement through other harness tools. CLI/API
actions, harness enforcement, lifecycle and ratification retain their owners.

No writes, database origination, ref updates, network, credential/user-home
exploration, source-controlled executable/import/expression, or writer-module
imports. Disable bytecode writes at entry. No policy engine, job runner,
session store, copied authority, or competing records namespace is introduced.

## Trusted configuration and bounded profiles

v1 has exactly `version, dispatch_store, job_ids, monitor_state, consumer,
usage_rollout`; v2 adds `job_registry`. v3 adds exactly `profiles, discovery`
to v2. Their tool counts are 4, 7, 11 respectively. Old config validation and
all seven old queries keep their existing semantics even under v3.
Only new discovery reads use the anchors below; this does not harden or
silently broaden the old rollout/record readers.

Config remains one regular, nonblocking file, at most 65536 bytes, loaded
once per process; duplicate/extra/missing keys, invalid types or constraints
refuse `config` before sources. `check` reads config only and returns exactly
`{"valid":true,"tools":11}` for v3. Reload requires restart.
For v3 configuration/new-source JSON, reject nesting deeper than 32, non-finite
numbers and integers outside signed 64-bit range before materialization;
malformed source JSON is a reading, not a server crash. No old reader changes.

Identifiers `ID`: `^[a-z][a-z0-9-]{0,31}$`; concepts:
`^[a-z][a-z0-9-]{0,63}$`; Git object IDs: 40 lowercase hex (SHA-1 repositories
only in this slice). Paths: printable ASCII, at most 1024 bytes, no empty,
dot or dot-dot components, backslashes, control bytes, tilde expansion or
glob expressions. Relative file paths have no leading/trailing slash; roots
are relative directory prefixes ending in slash. Absolute anchor paths begin
with slash and obey the same component restrictions. Labels: printable ASCII,
1..128 bytes. Bool is never int.

`profiles`: object of 2..16 ID keys, including dev and prod; every value has
exactly `label, work_roots, homes, startup_homes, source_ids, lineage`.
Roots: 1..16 unique roots; homes: 1..64 unique declared concepts in configured
order; startup_homes: 1..3 unique members of homes, in presentation order.
source_ids: unique subset of `repo, retained, dispatch/ID` (ID here is an
existing `job_ids` identifier with v1 grammar); repo is required; retained is
allowed only with a configured retained prefix; dispatch IDs need a non-null
dispatch binding. lineage is an ID naming the trusted work lineage, not a
branch-name heuristic. At most 22 source IDs per profile: required repo leaves
room for 21 dispatch IDs when retained is absent from source_ids, or 20 when
retained is included. No profile-specific code branch or index column is allowed.

Shipped default declarations (all concepts remain reachable via homes):

| profile | label / roots | startup_homes | homes |
|---|---|---|---|
| dev | development machinery / `["ops/"]` | `["lane-mcp","dispatch","onboard"]` | all index concepts except mapping, packages, projections |
| prod | Symfony application / `["src/","packages/","config/","public/"]` | `["mapping","packages","projections"]` | all index concepts except wf-runtime, dispatch, harness, jobs, ci-store, ladder |

The homes lists above expand in index order; source_ids default to
`["repo"]`, lineage to the profile ID. A valid additional profile is
`{"label":"documentation","work_roots":["docs/"],"homes":["wire","onboard"],
"startup_homes":["wire"],"source_ids":["repo"],"lineage":"docs"}`.
Same algorithms, limits and resources apply. Config may override defaults;
acceptance uses the shipped declarations. Profiles are neither Git branches
nor deployment environments.

`discovery` has exactly these fields:

| field | exact type and meaning |
|---|---|
| audience | literal `"conductor"`; supplied by trusted launch, never MCP arguments |
| anchors | object of 1..8 ID keys to absolute directory paths; repo is required; paths are trusted capabilities, not discovered from HOME/CWD |
| repository | `{id:ID, root:"repo", gitdir:ID, common_gitdir:ID, head:OID, ref:string\|null}`; both gitdir keys name anchors; head is the trusted launch commit; ref null for detached or a validated full refs/heads name |
| retained_refs | null or exactly `"refs/heads/records/"`; no default/fallback namespace |
| dispatch | null or `{anchor:ID, path:relative-directory}`; binds the same resolved directory as v1 dispatch_store, but only profile-authorized job_ids are read |
| record_bindings | 0..256 entries, exact shape below; trusted metadata owned by handoff custody, not parsed from record prose |
| work_bindings | 0..20 metadata entries `{id:job_id,lineage:ID,work_paths:[relative-file,...]}`; unique configured IDs, 1..64 unique paths; bounds bindings, not authorized dispatch IDs or work-list length |

A record binding has exactly
`{record_id:ID, kind:"handoff"|"aar", revision:SHA256, lineage:ID,
work_paths:[relative-file,...], supersedes:[ID,...]}`.
revision is the FULL 64-hex SHA-256 of record bytes; one unique record_id and
revision pair per entry, with no revision bound to conflicting IDs or metadata.
work_paths has 1..64 unique paths; supersedes has 0..16 unique IDs.
References must exist, have the same kind and lineage, and form an acyclic
graph; multiple revisions of one record_id are allowed only with identical
metadata (a changed revision is still separate evidence).
These are approved config assertions, not authentication inferred from Git.
Config provenance/custody is a launch responsibility. Trusted launch must
prevent an untrusted branch from installing config or authorizing a record;
discovery assumes that boundary rather than proving it across host tools.

Anchors may point at existing state outside the repository; no store moves
are required. Root and gitdir/common-dir device/inode identities are pinned
by trusted launch. For linked worktrees, launch supplies and validates both
gitdirs; do not follow an arbitrary .git or commondir redirection.
All components are opened descriptor-relative beneath these anchors with
no symlink traversal, including ancestors; reject FIFO/device/socket and
unexpected directories. Never re-resolve an anchor through a mutable pathname.
If safe access cannot be established, report unavailable, not a fallback.
Configuration is granted from the launch-supplied `--config FILE` descriptor;
discovery does not authenticate its launcher or specify a host sandbox.

## Trusted discovery boot transport

For v3 discovery reads, trusted process launch supplies
`--discovery-boot-fd FD` alongside `--config FILE`. FD is an inherited,
launcher-opened read-only directory descriptor for the retained runtime
artifact, separate from the repository being inspected. This adds no v3
configuration key or query/resource argument. The descriptor grants exactly:

- `ops/conductor/discovery-lever.md`
- `ops/conductor/tools/lane_read.py`
- `ops/conductor/tools/lane_mcp.py`
- `ops/conductor/tools/lane_refs.py`
- `ops/conductor/tools/mcp_transport.py`
- `ops/conductor/tools/dispatch_observation.py`

Open only these fixed components descriptor-relative without symlinks; no
enumeration or caller path selector. Require regular files, at most 1 MiB
each and 4 MiB total, a 10-second bootstrap deadline, and before/after
identity, size and modification checks. Trusted launch must bind the five
executed modules to these same captured bytes; a separately claimed manifest
does not establish that binding. The executed-byte gate below verifies this
coupling separately from launcher authentication. Load and retain the bytes
once before the first discovery acquisition. Close owned descriptors on every failure and
normal shutdown. No adjacent-file, HOME, CWD, environment or inspected-repo
fallback is allowed. FD numbers and filesystem timestamps enter no fingerprint.
Use exactly the definition observation and sorted five-file implementation
manifest recipe below; direct-object acquisition retains `git:null`.

Config-only check/list operations and all seven old queries neither read boot
files nor open repository anchors. Missing or rejected supplied boot on a discovery
read refuses before repository acquisition with the applicable v3-only boot line in
[Refusals and exits](#refusals-and-exits). The independent conformance
counterparty must register separate hermetic witnesses with valid v3 config
for missing and rejected supplied boot, checking their precise lines and
remedies; a legacy config refusal is no proof.
This transport assumes trusted launch custody, not host admission or activation.
The parallel interface track owns the pending reciprocal boot cross-link in
[conductor-start-plan.md](conductor-start-plan.md); this correction does not
edit that counterpart or change its admission standing.

### Executed-byte binding gate

The trusted launch owner supplies the retained artifact and a load path that
binds each of the five modules' actual execution inputs to its captured bytes.
The discovery boot custodian in `ops/conductor/tools/lane_mcp.py` owns checking
that binding before calling acquisition, including its own entry module's
load evidence. Execute retained buffers directly or establish byte-for-byte
equality with immutable loader-owned execution inputs; reopening `__file__`,
hashing adjacent paths, or accepting a caller's claimed manifest is insufficient.
An already loaded module without verifiable load evidence must refuse
`binding-unverified`; differing execution inputs must refuse `binding-mismatch`.
No discovery module may subsequently reload from mutable paths. These checks
share the bootstrap deadline and precede repository anchors and fingerprints.

D-A47 includes the startup binding gate: an independent test producer plants
boot artifact A and a different execution artifact B, with a harmless distinct
execution marker in each of the five modules in turn. The fixture records
the actual loader inputs and executed markers, computes the manifest itself,
and requires A/B to refuse before any anchor access or discovery envelope.
A/A and B/B clean neighbors must execute the corresponding markers and report
their independently computed adapter fingerprints. Missing load evidence must
refuse too. A counterparty that hashes A but executes B, or omits the binding
check, must fail this gate. Receipts must bind process, artifact hashes and
observed execution, not merely compare two supplied digest strings.

The runtime producer owns boot-custodian implementation; the trusted launch
owner owns the loader input guarantee, including entry-module loading. The
independent test producer owns these fixtures, a separate reviewer checks them,
and the integrator runs them at one combined ref before step-2 acceptance.
This gate measures custody under a trusted launcher/interpreter; it does not
authenticate that launcher, defeat a malicious interpreter, admit host startup,
or prove arbitrary changed contract prose has been implemented. Hashing alone
proves none of those claims. The reciprocal startup-plan edit remains pending
with the parallel interface owner named above.

## Fixed acquisition boundary

The current read core's no-subprocess restriction remains absolute.
`lane_read` accepts immutable readings; it never runs Git or imports
`lane_refs`. Both projections use one acquisition coordinator outside the
core: `ops/conductor/tools/lane_refs.py` owns the public `acquire` function
and the sole fixed Git read adapter's closed operations below. The declared
import target is `ops.conductor.tools.lane_refs.acquire`; a fixture imports
that symbol from the trusted runtime artifact with its root on the import
path, never a query-selected module. `lane_mcp` calls this coordinator and
passes its immutable reading to `lane_read` for both CLI and MCP projection;
the v1/v2 no-subprocess boundary and entry behavior remain unchanged.
No command string or path supplied by a query reaches it.

Closed operations, all bound to the repository capability above:

| operation | input | successful data |
|---|---|---|
| anchor_head | trusted head OID and optional trusted ref | `{head:OID,ref:string\|null,ref_commit:OID\|null}`; verify commit type and ref correspondence |
| list_refs | configured retained prefix only | sorted `[{ref,commit:OID}]`; each ref must directly name a commit |
| list_tree | verified commit from anchor_head/list_refs | sorted `[{path,mode,blob:OID}]` for the two record path patterns |
| tree_entry | verified commit plus a path from list_tree or the fixed document/entrypoint set | `{path,mode,blob:OID}` or null for measured absence |
| read_blob | blob returned by list_tree/tree_entry in this acquisition | exact bytes; no revision expressions or caller OIDs |

Ref grammar here is `refs/heads/` followed by nonempty slash-separated
`[A-Za-z0-9_-][A-Za-z0-9._-]*` components, total ≤256 bytes, with no `..`,
trailing dot or `.lock` suffix in any component. Unexpected enumerated names
make that namespace malformed; they are never echoed into arguments. The
adapter verifies supplied head/ref correspondence; an existing ref pointing
elsewhere is repository-mismatch. Null ref is a declared detached snapshot.

Every operation returns exactly `{state:"ok"|"unavailable",reason:Reason|null,
data:object|array|string|null}`; internal bytes are byte buffers, not public
JSON strings. Failure has data null. Modes 100644/100755 and blob type only;
mode 120000, submodule/tree mismatches and non-commit refs are `object-type`.
Allowed record paths are `ops/conductor/handoff/HANDOFF-*.md` and
`ops/conductor/AAR-*.md`, where star is one or more ASCII letters, digits,
dot, underscore or hyphen. Enumerate paths before reading; a ref-only unknown
filename is discoverable. Other tree entries may be examined only to verify
fixed law/index/registry/entrypoint paths and composer.lock. Never recursively
explore user directories. Records are read from the pinned HEAD tree and
retained commit trees, not untracked or dirty worktree files; this scope is
declared in every startup omission. No filename or commit subject is an
instruction or evidence of relevance.

A Git subprocess implementation must use an absolute trusted Git executable,
fixed argv templates for ref enumeration, commit/type/tree/blob reads only,
no shell, revision syntax from callers, pathspec magic, user aliases or helper
commands. Set explicit gitdir/worktree/common-dir bindings; ignore ambient
CWD and all inherited GIT_* variables. Use an allowlisted environment only,
disable system/global config, replace objects, optional locks, lazy promisor
fetch, external diff/textconv, hooks and pagers; reject repository alternates,
replace/graft redirection, partial/promisor stores or external includes/helpers
that cannot be excluded. Repository-local config must not redirect object
access or execution. Object/pack/ref storage must stay beneath verified
gitdir/common-dir anchors, with no symlink components or special files.
Missing local objects are unavailable; never fetch. A direct-object
implementation must meet the same contract. This is a bounded adapter,
not a sandbox platform or general Git runner.

Bounds apply before materialization: at most 65 ref entries examined to
detect a 64-ref overflow; stable bytewise ref order. At most 257 matching
record occurrences across HEAD and retained trees to detect a 256-occurrence
overflow. Tree enumeration examines at most 4097 entries within the two
fixed directories per unique commit (4096 allowed), 1 MiB enumeration output
per operation, and 1 MiB+1 bytes per blob (1 MiB allowed). At most 16 MiB
aggregate object/file bytes, 512 operations and 10 seconds per acquisition;
each operation gets at most 2 seconds, counting process startup and decode.
Stop on the first exceeded bound; no unbounded sorting or buffering before
the cap. Ref overflow discards the entire retained result, tree overflow
discards that tree, and aggregate overflow invalidates acquisition. No partial
set can prove absence or unique selection. An empty successfully enumerated
namespace is ok empty; a disabled namespace was not searched.

Read each object once per acquisition. Re-enumerate enabled ref identities
and verify anchor/head/ref identities at the end, within the same budgets;
mutation yields `changed-during-read`, no snapshot, no selection, no retries.
File sources use the same pinned-descriptor discipline and verify identity,
size and modification metadata before/after; changed reads are unavailable.
Immutable object bytes are verified against their object IDs. No worktree,
Git config/index/ref/object, database, source file or host mutation is allowed.

### Deterministic acquisition checkpoints

The public in-process seam is
`ops.conductor.tools.lane_refs.acquire(config, boot, *, checkpoint=None)`.
The optional callback receives a closed immutable event `{phase, source_id}`
and returns no data. `source_id` is the affected `repo`, `retained`, or
authorized `dispatch/ID` source. The two phases are:

- `before-final-revalidation`: after initial identities and data have been
  acquired, immediately before the required final identity reads for that source.
- `before-file-postcheck`: after file bytes have been read, immediately before
  their post-read identity, size and modification checks.

Observable multiplicity is exact within each acquisition: one
`before-final-revalidation` event for repo and one for enabled retained when
that source reaches final identity revalidation; one `before-file-postcheck`
event for each authorized dispatch/ID whose single record file reaches its
post-read check. No other phase/source pairs occur. Empty successfully read
namespaces still reach revalidation. Disabled sources and sources that fail
before reaching the phase emit zero events, not synthetic checkpoints. A
whole-acquisition stop emits no later events; retries and duplicate events for
the same pair are forbidden.

The observable order is a partial order: for each source, initial identity
and data read precede its callback entry; callback entry precedes mutation
and acknowledgement (or stationary return); callback return precedes the
source's actual final identity reads or file postcheck; those checks precede
the acquisition result. Callback invocations are synchronous and do not
overlap. There is no relative order between different sources' events, and
no global sort or internal traversal algorithm is prescribed. Fixtures compare
the exact eligible event multiset and each source's ordered trace against
these relations, accepting any cross-source interleaving that satisfies them.
Failed-before-phase and early-stop fixtures must account for each omitted event
by its observed failure, not silently accept a missing callback.

Production supplies no callback. No environment variable, config key, query
argument, dynamic import, skip flag or caller-supplied success result selects
this seam. Verification, operation limits and deadlines remain active, including
time spent in the callback. The adapter itself never writes.

An independent D-A31 fixture imports the named coordinator, invokes its real
adapter operations and synchronously mutates
one test-owned retained ref to an already planted commit at the first phase,
or one authorized dispatch file at the second. It verifies changed metadata
and acknowledges completion before acquisition resumes. Record the checkpoint,
mutation and verification ordering and bounded completion; fail if the selected
checkpoint is not reached. Require unavailable/changed-during-read, null
snapshot/data and no retry; a stationary neighbor succeeds and a deliberately
omitted final check fails the fixture. Separately project the immutable reading
through the normal core. Injecting an unavailable packet proves projection
only; a scheduler-dependent mutation during a child invocation proves neither
checkpoint ordering nor the real comparator's detection.

## Local source composition

No importing or executing onboard, decisions, handwork, gh, launcher, monitor
or watchdog. Discovery shares the fixed five-row subset of onboard's seven
law entries below and its five state labels, not its subprocess acquisition.
Each new query acquires the same
profile dependency closure, even a named action query with a startup snapshot.

The ordered discovery path/one-liner pairs are frozen here and must equal
the corresponding first five rows of [onboard's law table](onboard-lever.md#the-brief-three-sections-in-order):

| path | one-liner |
|---|---|
| `AGENTS.md` | the operating contract |
| `CONTRIB.md` | naming, PR bodies, fix commits, the review wire |
| `ops/conductor/instrument-conventions.md` | the lane's base contract: wire, identity, exits |
| `ops/conductor/stack.md` | the stack: layers, incumbents, open seats |
| `ops/conductor/coverage-zones.md` | what gates what |

Onboard alone also lists discovery-lever.md and conductor-start-plan.md.
An independent equality gate must compare exact paths, order and one-liners
for the shared subset and onboard's two additional rows, including projection
presence/absence arithmetic. No runtime import, generator or replacement
registry is introduced. Discovery's five-row shape and 8192-byte cap stand.

| source ID | exact binding / derivation |
|---|---|
| config | canonical loaded v3 config, including all profiles, bindings and launch anchor identities |
| contract | boot-supplied card/shape/law/action definitions; revision is SHA-256 of the launch-supplied discovery-lever.md bytes, as in the definition observation below; no second definition digest |
| adapter | acquisition protocol version `discovery-acquisition/1`, adapter implementation SHA-256 and trusted Git executable digest/version (null executable fields for direct objects) |
| repo | pinned HEAD and ref identity; fixed law paths, index homes/contracts/registries/entrypoints, composer.lock, and enumerated HEAD record blobs, all through the adapter |
| retained | configured prefix plus every enumerated ref→commit, tree entry/mode/blob and read outcome; read only when this profile includes retained |
| dispatch/ID | exactly anchored dispatch/ID/record.json, authorized by both job_ids and this profile's source_ids; regular file ≤1 MiB, no directory enumeration |

The latter four are never located by caller input or ambient state. Law
cards use the fixed five-row table above: `present` when a nonempty
regular tracked blob exists, `missing` for verified absence or empty bytes,
`unavailable` on a failed read. Startup state summaries are exact:

| name | status / summary / evidence |
|---|---|
| register | unavailable / null / []; reason `unsupported-local-reading` |
| ledger | unavailable / null / []; reason `unsupported-local-reading` |
| prs | unavailable / null / []; reason `unsupported-local-reading` |
| dispatches | ok / `{configured:N,readable:N,unknown:N,unavailable:N}` / authorized dispatch source IDs; measured counts, not live running or closed totals |
| branch | ok / `{ref:string\|null,commit:OID}` / ["repo"]; unavailable/null if head/ref acquisition fails |

There is no register/ledger/PR binding in this slice and no SQLite read for
these fields. Their existing stores and onboard output are unchanged.
Dispatch counts classify each existing lane_record-compatible structural
reading using an anchored read, not the old reader's ambient path handling;
they never probe PIDs or list other jobs. N is a non-negative integer;
no authorized dispatch IDs gives four zeros, not a claim about all dispatches.

Work is authorized dispatch summaries only, ordered by configured job_ids,
filtered by work_bindings as below; title null, no prompts/transcripts/PRs.
The complete eligible/unknown work list has at most 21 cards (20 when retained
is in source_ids), bounded by authorized dispatch IDs, not work_bindings.
An authorized dispatch without a work binding remains in that list as unknown;
the 0..20 binding limit does not remove it or cap the list at 20.
Branch state is already shown separately. Profile roots classify trusted
work_paths: a record is relevant iff lineage equals profile.lineage and at
least one path has an exact root prefix (directory boundary included).
Mixed-root work is relevant to each intersecting profile only if lineage
also matches; configuration must explicitly choose a common lineage for that.
No timestamps, branch words, titles, filename stems, or body text classify work.
Missing work metadata appears as unknown relevance, never guessed. Known
cross-profile work is excluded before returning any content.
For a readable dispatch, Work.revision is the full byte digest and
recorded_status uses the existing lane_record text rule. Missing work binding
sets lineage null, relevance unknown and reason metadata-missing. A relevant
bound dispatch keeps its lineage/relevance when its file fails; status/revision
are null and reason is the source failure (which takes precedence over missing
metadata). There is no title, live status or selected-work authority implied.

## Record identity, trust and selection

Occurrence identity is `{ref:string|null,commit:OID,path,blob:OID}`.
HEAD occurrences use the trusted ref or null. Deduplicate exact byte-identical
records by FULL SHA-256, across paths/refs/HEAD; retain all sorted occurrences
in internal dependencies. Public key is `record/<64-hex-sha256>`, stable
across identical occurrences; two blobs per ref have different keys unless
their bytes are identical. Same bytes classified as both handoff and AAR are
`metadata-conflict`, never selectable. Record IDs/kinds/relevance are taken
only from matching trusted record_bindings. A filename supplies a provisional
kind for an unbound card, never authority or priority.

Current/stale is evidence-relative, not time freshness. Among completely
observed, relevant bound records, a record is stale iff a readable relevant
candidate's trusted supersedes graph reaches its record_id; otherwise current.
For handoffs, restrict reachability to readable relevant handoff IDs; an
irrelevant superseder cannot establish staleness, and missing relevant evidence
makes classification unknown. The same-kind, same-lineage acyclic rules stand.
Different revisions of one ID with no superseding record are tied current.
No tie-break by date, path, ref, HEAD location or commit timestamp. An AAR
can supersede only AARs and never becomes the selected handoff.
Non-UTF-8/oversized/unreadable bytes cannot count as readable evidence;
a valid body above the 2048-byte presentation cap is still readable up to
the 1 MiB source bound. Byte-identical deduplication compares bytes as well
as digests; a digest collision is metadata-conflict, never merged evidence.

Candidate cards have `relevance:"relevant"|"unknown"` (known irrelevant cards
are excluded), and `freshness:"current"|"stale"|"unknown"`. An observed unbound
handoff is a bindings failure: complete false, selection unknown and counts
null, even beside one readable current handoff. Missing/unreadable bound
handoff evidence, handoff metadata conflict or incomplete enabled scan also
makes selection unknown. AAR metadata gaps do not block handoff selection.
A binding for this profile whose bytes are absent from a complete scan is
visible as an unavailable candidate (`missing-bound-record`). Unbound
records remain visible as key/kind/reason only, no body or provenance paths
until relevance is established. This exposes incompleteness without leaking
cross-profile prose. Unknown records do not authorize actions.
Disabled retained refs are outside the declared scan scope: selection may be
one within repo only, with `retained source not searched` explicitly shown.

When all enabled scans are complete and relevant handoff evidence readable:
an empty relevant handoff set gives none; one current handoff gives one; two
or more current handoffs gives multiple. A complete nonempty relevant handoff
set always contains a current handoff: reachability on its finite acyclic ID
graph is a strict partial order with an unsuperseded ID, whose readable
revisions are current. A complete nonempty stale-only set is therefore
impossible. Only one gives selected_key, including when lists span pages.
A presentation page containing only stale cards retains global selection and
the terminal current key on another page; named stale detail remains readable
and cannot become selected. No fabricated preclassified stale-only acquisition,
retirement metadata, cycle or hidden relevant superseder may replace this proof.
AARs are listed
but excluded from handoff counts. Lexical key ordering is presentation only.
A per-file failure whose key cannot be computed lives in source issues, not
a fabricated candidate key, and still blocks selection.
Incomplete handoff classification gives every returned candidate freshness
unknown; otherwise readable relevant cards follow the supersedes rule, while
unbound/unreadable AAR cards retain freshness unknown. Only failures in record
enumeration/reading or handoff bindings affect Selection.complete; an absent
binding for an observed handoff explicitly counts as a bindings failure.
Unavailable law, lock or dispatch readings do not by themselves invalidate a
complete record scan. Unbound AARs remain listed with metadata-missing and
are excluded from handoff counts and completeness. Relevant bound
cards have reason null unless their own bytes/presentation fail; unbound
cards use metadata-missing. Synthetic missing cards use missing-bound-record.

Every public candidate carries `trust:"untrusted-record"`. Named detail body
is wrapped before transport by the shared projection on CLI, MCP tool text,
resource text and structuredContent; no raw alternative body is emitted.
Provenance remains in the candidate fields. For inner record bytes B, emit
the following UTF-8 byte concatenation (quoted strings here use JSON escapes):

`"BEGIN UNTRUSTED RECORD key=" + key + " revision=" + revision + "\n" + E(B) + "\nEND UNTRUSTED RECORD\n"`.

E splits B only on LF (0x0a), preserving every separator and empty segment.
For each segment exactly equal to ASCII `END UNTRUSTED RECORD`, prefix ASCII
`>` (0x3e), repeating until it no longer equals that marker. Leave every other
byte unchanged, including CR (0x0d), existing `>` prefixes, Unicode, and the
fixture's different `[END UNTRUSTED DATA]` marker. Do not normalize newlines.
The header ends with one LF; always append the one LF before the end marker
even when B already ends in LF (then there is a blank line), and one LF after
the end marker. Empty B therefore has one empty inner line. JSON-escape the
result only after wrapping, with the canonical encoder on every projection.
This is a literal boundary label, not semantic injection immunity. Claims
require corroboration against governing contracts and measured readings.
No action is authorized solely by record prose, even if binding, bytes,
provenance, delimiters and JSON syntax are valid.

## Exact public types and dependency snapshots

All shapes below are closed, all shown keys required. Arrays always exist.
Nullable means exactly the specified type or null, never an omitted key.
Strings are valid Unicode; public paths/IDs satisfy the config grammar.
Source prose is never copied into reasons. Canonical bytes use the existing
encoder (sorted keys, ASCII JSON escaping, compact separators); no call clock.

Reason is null or one of these fixed tokens:

| tokens | application |
|---|---|
| disabled, missing, unsupported-local-reading | explicit source not measured / absent |
| unreadable, unsafe-path, object-type, malformed, oversized, timeout | acquisition failure |
| ref-overflow, tree-overflow, record-overflow, budget-exceeded | incomplete bounded scan |
| changed-during-read, repository-mismatch | acquisition cannot bind stationary repository |
| metadata-missing, metadata-conflict, missing-bound-record | candidate not eligible |
| unknown-concept, unknown-key, unknown-action | successful named miss inside the profile catalogue |
| page-frame-exceeds-cap, item-exceeds-cap, response-exceeds-cap | no silent output truncation |
| body-exceeds-cap | named body omitted with measured byte count |

Missing source state is unknown; disabled/unsupported and failed sources are
unavailable; looked empty is ok. Public query state is ok or unavailable
only: partial source failures remain typed readings in an ok packet.
Acquisition-wide race/budget failure or response overflow gives unavailable.

`Source = {id:string,state:"ok"|"unknown"|"unavailable",reason:Reason,
fingerprint:SHA256}`. Source IDs are the table above, sorted bytewise.
Each source summarizes its whole acquisition: unavailable outranks unknown,
which outranks ok; when several failures occur, its public reason is the
first failure in operation/path/ref bytewise order. The fingerprint still
contains all observed failures. Config, contract and adapter are ok once
validation completes: their definitions are supplied by trusted boot, so a
repo read failure cannot make contract unavailable. Retained is included
with disabled when not granted.
Thus every closure contains these five IDs plus its authorized dispatch IDs.
fingerprint hashes canonical `{id,binding,state,reason,observations}`:
binding contains exact anchor capability identities and configured locators;
observations contain every attempted path, mode, byte revision (full SHA-256
or null), object/ref/commit identities, outcomes and limit/completeness
markers. repo also includes HEAD/ref checks, all card presence inputs and
lock bytes; retained includes the complete sorted ref map, even if every
blob is unchanged. config/contract/adapter observations are their definitions
above. Disabled inputs have explicit null observation inputs/data; no file mtime
or current clock enters a successful fingerprint. Transient OS error text is
normalized to the fixed reason. Exact duplicate observations collapse.

For an independently reproducible encoding, binding is exactly
`{configuration:loaded_v3_config,anchors:[{id,device:int,inode:int},...]}`,
anchors sorted by ID, from trusted launch (never process-local fd numbers).
An observation is exactly `{operation,input,state,reason,data}`. Operation
inputs are `anchor_head:{head,ref}`, `list_refs:{prefix}`,
`list_tree:{commit}`, `tree_entry:{commit,path}`, `read_blob:{blob}`;
successful data is the adapter table's output, except byte buffers normalize
to `{revision:SHA256,bytes:int}`. An anchored dispatch read is operation
`read_record`, input `{id:job_id}`, data `{revision:SHA256,bytes:int}` or null.
Failed operations have data null, including overflow; the reason records
the exact exceeded bound. Source classifications use their completed
observations plus the fixed failure sentinel, never an unbounded partial set.
Definition sources use one `definition` observation, input `{id:source_id}`:
config data is the loaded config; contract data is `{protocol:"discovery/3",
revision:SHA256}` with revision the full SHA-256 of the exact launch-supplied
discovery-lever.md bytes (no normalization or separate compiled-data hash);
adapter data is `{protocol:"discovery-acquisition/1",
implementation:SHA256,git:{revision:SHA256,version:string}|null}`. implementation
hashes canonical `[{path,revision},...]`, sorted by path, for lane_read.py,
lane_mcp.py, lane_refs.py, mcp_transport.py and dispatch_observation.py from trusted launch bytes. All paths here have
the `ops/conductor/tools/` prefix. The direct-object implementation has git
null. Config/contract/adapter source data is supplied by trusted boot, not
record text or a repository read. The ordinary contract source fingerprint
still hashes the full canonical source object above, including this revision.
Disabled sources use one `disabled` observation with input/data null,
state unavailable and reason disabled. Observation arrays sort by their
complete canonical bytes and deduplicate identical entries. repo and retained
contain their respective Git operations, including both before/after ref
checks; a blob shared between them appears once in each source's dependencies.
No step-2 author chooses another hash recipe or drops failed observations.

`snapshot` is FULL SHA-256 of canonical
`{protocol:"discovery/3",audience:"conductor",profile:ID,
sources:[Source,...]}`. This replaces the draft's inadequate 16-hex snapshot;
v2's 16-hex file revision is unchanged. Every detail query requires profile,
so its dependency closure matches lane_start. Source changes, failures,
adapter/config changes, ref movement to identical blobs, relevance or
supersedes changes invalidate continuation. No snapshot store is written.

Snapshot input grammar: 64 lowercase hex. Reacquire the entire bounded
profile closure for every request. If stationary acquisition is impossible,
return unavailable with snapshot null; do not validate an old cursor against
partial data. Otherwise compare any supplied snapshot before named lookup or
pagination; mismatch refuses cursor. Failure sentinels yield stable snapshots
when their observed failure states remain the same.

`Envelope = {schema:"agentscient.lane/v1",query:Query,state:"ok"|"unavailable",
reason:Reason,profile:ID,snapshot:SHA256|null,sources:[Source,...],data:Data|null}`.
For query-wide unavailable, data is null; sources retain completed bounded
source readings, except response overflow sets sources [] (dependencies still
bound by snapshot if known). Only unavailable may have null snapshot/data.
Successful named misses use reason unknown-* and a null item, not unavailable.

Types used by Data:

- `Home = {concept,home:string|null,contract:string|null,entrypoint:string|null,
  registry:string|null,ownership:"declared"|"unresolved",document_present:bool|null,
  implementation_present:bool|null,locked:bool|null,installed:bool|null,
  activated:bool|null,probed:bool|null,intended:string|null,evidence:[source_id,...]}`.
  evidence is ["contract"] for the compiled index row, with "repo" appended
  iff document_present, implementation_present or locked was measured or
  attempted from repo observations. No other source IDs. Unresolved ownership,
  failed document reads and locked false/null still include repo when consulted.
  Consultation is property-specific: actual `tree_entry`/`read_blob` observations
  for that Home's document, executable or applicable lock check establish it,
  including failed attempts. Repository membership in the profile closure,
  `anchor_head` failure, or membership in a convenience files map does not.
  If `anchor_head` fails before supplying a verified commit, downstream
  `tree_entry`/`read_blob` calls for Home properties are prohibited. Keep the
  failure in the repo source; do not synthesize per-path attempted outcomes.
  Unmeasured properties remain null (declared unbuilt/document-only rules
  still apply), ownership unresolved and entrypoint null, with ["contract"].
  Repo remains mandatory in every closure; this is no repo-disable mechanism.
- `HomeSummary = {concept,home:string|null,entrypoint:string|null,
  document_present:bool|null,implementation_present:bool|null}`.
- `Law = {path,status:"present"|"missing"|"unavailable",one_liner}`.
- `StateSummary = {name,status:"ok"|"unknown"|"unavailable",
  summary:object|null,reason:Reason,evidence:[source_id,...]}`;
  the table in Local source composition defines the only summary objects.
- `Work = {id:job_id,lineage:ID|null,relevance:"relevant"|"unknown",
  recorded_status:string|null,revision:SHA256|null,reason:Reason}`;
  recorded_status follows lane_record's 256-byte text rule; no live status.
- `Candidate = {key,record_id:ID|null,kind:"handoff"|"aar",
  relevance:"relevant"|"unknown",freshness:"current"|"stale"|"unknown",
  trust:"untrusted-record",revision:SHA256,occurrences:int,provenance:
  {ref:string|null,commit:OID,path,blob:OID}|null,body:string|null,
  body_bytes:int|null,reason:Reason}`.
  Provenance is the first sorted occurrence (ref null sorts first, then
  commit/path/blob); occurrences is the total, no loss of identity in the
  fingerprint. Missing-bound-record uses its binding revision/key, zero
  occurrences, null provenance/body/length. Unknown relevance also suppresses
  provenance/body/length. Lists/start never carry body. A non-null body is the
  literal pre-transport wrapper above; revision, key and body_bytes describe
  the original inner bytes, excluding wrapper/escape bytes.
- `Selection = {selection:"none"|"one"|"multiple"|"unknown",
  selected_key:string|null,current:int|null,stale:int|null,
  unknown:int|null,complete:bool}`. Counts cover handoffs only and are null
  when an enabled scan failure or handoff reading/binding failure prevents
  complete handoff classification; AAR metadata gaps do not affect these counts.
  With complete true all counts are measured, unknown is zero.
- `Action = {name,owner,entrypoint:string|null,implementation_present:bool|null,
  available:bool|null,installed:null,activated:null,probed:null,reason:Reason}`.
- `Page = {offset:int,count:int,total:int,more:bool,next_cursor:string|null}`.

Ownership precedence: founding docs and owning contracts govern; instrument
registries identify entrypoints and conformance only. This index is a routing
projection, not a new authority. home-map and AAR reports are evidence.
A routing disagreement with an owning contract must be corrected by review
at that governing home before regenerating the index; runtime does not parse
prose to decide authority. A missing/unreadable governing document gives
ownership unresolved and entrypoint null; otherwise this index declares
ownership declared. document_present checks the governing
contract blob (directories count only if the row explicitly names a directory).
implementation_present checks only the executable target, never a contract.
For document-only concepts it is null; for declared unbuilt instruments false.
A missing executable is false; a failed check is null.
locked is true only when an object in composer.lock's `packages[]` or
`packages-dev[]` has `name` exactly equal to the intended Composer package.
Constraint values, require/suggest keys and all other JSON locations do not
count as membership. It is false on measured absence, null for malformed/unavailable lock or
non-package intended values. installed/activated/probed are always null here:
neither a lock entry nor an executable blob proves installation/live readiness.
Only the intended value exactly equal to symfony/workflow names a Composer
package in this index, describing prod's workflow genus; prose/local
package paths do not trigger inferred package lookups.

## Queries, pages and resources

Closed arguments; every new query requires `profile:ID`, optional
`snapshot:SHA256`. Additional keys:

| query | optional arguments | exact Data on success |
|---|---|---|
| lane_start | cursor | `{law:[Law],state_sources:[StateSummary],homes:[HomeSummary],work:[Work],handoffs:Selection,actions:[Action],omissions:[string]}`; cursor pages work only |
| lane_homes | concept, cursor | `{homes:[Home],home:Home\|null,page:Page\|null}` |
| lane_handoffs | key, cursor | `{candidates:[Candidate],candidate:Candidate\|null,handoffs:Selection,page:Page\|null}` |
| lane_actions | name | `{actions:[Action],action:Action\|null}` |

Concept/key/name plus cursor is refused arguments. Key grammar is
`^record/[0-9a-f]{64}$`; name uses ID grammar. Without a name, detail lists
are in configured home order, lexical candidate key order, or action index
order. Named requests have [] list and page null, including misses. A known
concept/key outside the profile is indistinguishable from a named miss.
Named handoff lookup returns that card whether or not it is selected; it never
turns a multiple/unknown selection into one.

Cursor grammar is `<snapshot>:<offset>`, snapshot 64 lowercase hex, offset
a canonical decimal integer (no leading zeros). For homes/handoffs offset is
1..255; for lane_start it is 0..20, covering the at-most-21-card work list
(0 permits retrying work omitted for fit on the initial read).
Page one without cursor has offset 0.
cursor binds the profile snapshot and query/list implicitly through a required
matching `snapshot` argument plus query-specific offset bounds; it grants no
access and may be reused as an offset on another list within the same profile
snapshot. Offset must be below total; no cursor for empty lists.
Malformed snapshot/cursor, mismatch or out-of-range offset refuses cursor.
Pagination does not mean incomplete acquisition: enumerate the bounded set
before computing selection, then page presentation.

Homes/handoffs list pages greedily append whole cards, at most 16 and at most 16384 encoded
envelope bytes including final page metadata. Empty measured lists have
`{offset:0,count:0,total:0,more:false,next_cursor:null}`.
When items remain, at least one must fit; otherwise item-exceeds-cap, data null.
A frame that cannot fit returns page-frame-exceeds-cap. More implies next
cursor at offset+count, else null. Named cards bypass the 16-item limit but
obey the response cap. No card is silently skipped/reduced.
The assembled candidate set, including missing-bound-record cards, is also
limited to 256: detect a 257th card and return query-wide unavailable with
record-overflow and data null. Named lookup does not bypass acquisition
overflow. The 16-card limit is presentation only and is bypassed by a name.

Every lane_start response caps at 8192 encoded bytes. Without cursor it
carries all five law rows, all five state rows, all configured startup_homes
(at most three), all four actions, and Selection, plus the first eight
eligible/unknown work cards (or all if fewer). If needed drop work only (all
of it); retain the mandatory frame and the continuation omissions below.
With cursor, use the same Data shape and repeat that entire mandatory frame;
page only work from the supplied offset in the complete eligible/unknown
job_ids order. Greedily append whole cards, at most eight, including final
omission bytes in each fit calculation. Never skip a card or drop owners,
state, actions or selection. If work remains and not even one card fits on a
continuation, return unavailable/item-exceeds-cap with data null. An oversized
mandatory frame (including required omissions) is unavailable with
page-frame-exceeds-cap. No empty non-advancing continuation succeeds.
Profile/anchor limits and compact summaries permit the shipped defaults;
acceptance measures bytes. Full homes are targeted detail.
All other responses cap at 16384. Body only on named handoff, valid UTF-8,
≤2048 bytes; larger body becomes null with length and body-exceeds-cap;
invalid UTF-8 is malformed, no body. The 2048 limit and body_bytes count inner
bytes before wrapper and JSON escaping; the envelope cap includes both.

Startup omissions always contain, in this order: `no host activation`,
`no live readiness`, `no role enforcement`, `no lifecycle publication`,
`no raw documentation dump`, `HEAD tree only; dirty and untracked files excluded`,
`register ledger prs local readings unavailable`.
Append `retained source not searched` when disabled for this profile.
Let offset be zero on the initial read and the cursor offset on continuation;
count is the returned work length, total the complete eligible/unknown count.
When N = total-offset-count is positive, append `work omitted: N`, then
`work next cursor: SNAPSHOT:OFFSET`, where SNAPSHOT is the envelope snapshot
and OFFSET is offset+count in canonical decimal. These are literal ASCII
prefixes with single spaces; no extra page field is added to lane_start.
For an initial all-work drop OFFSET is 0. When N is zero append neither
string. Previous slices are not counted as omissions. Empty work thus keeps
fixture A's exact shape/bytes. No source prose in omissions.

A continuation on CLI uses TOOL `lane_start` with JSON exactly
`{"profile":"dev","snapshot":"S","cursor":"S:8"}` (S denotes the same
measured 64-hex snapshot in both strings); MCP tools/call uses name lane_start
and that same arguments object. Substitute the returned cursor/offset, never
assume eight fit. Snapshot is required with cursor, including offset zero.
Twenty fitting cards return 8, 8, 4 at offsets 0, 8, 16; only the first two
responses advertise a next cursor. The same rule applies to every profile.

CLI remains `python3 -B ops/conductor/tools/lane_mcp.py --config FILE query TOOL [JSON]`.
For v3 discovery queries add `--discovery-boot-fd FD` before `query`; stdio
launch uses the same option before `serve`, under the
[trusted boot transport](#trusted-discovery-boot-transport).
FILE is supplied at trusted process launch, outside the closed JSON query
schema; it cannot be changed by a tool call or resource URI. A job-bound launch
descriptor is distinct from the conductor descriptor; this contract does not
implement its worker catalogue or authenticate which process may be launched.
Tools and resources project the SAME envelope; _meta remains transport-only
and ignored as in v2. Resource list reads config only, adds one each of
`lane://start/PROFILE`, `lane://homes/PROFILE`,
`lane://handoffs/PROFILE`, `lane://actions/PROFILE` per profile.
Three added templates are `lane://homes/{profile}/{concept}`,
`lane://handoffs/{profile}/record/{digest}`,
`lane://actions/{profile}/{name}`; the v2 job template remains.
Resource first pages omit snapshot/cursor. Continuation is query-only through
CLI or MCP tool calls, including lane_start work; resources have no cursor URI.
URI segments have the corresponding argument grammars, no decoding into paths;
unknown/malformed resource URIs retain -32602. v1 template handling stays -32601.

## Refusals and exits

Validation order: config; argument object/required keys/types/grammars;
configured profile; cursor grammar and required matching snapshot; trusted
discovery boot validation/loading; acquisition;
snapshot equality; offset bounds; render. Unknown tool under v1/v2 remains
arguments. Refusals are one stderr line and exit 2 with empty stdout; MCP
isError=true carries the same line. Templates (all substitutions bounded,
validated values only; arbitrary malformed input rendered as `invalid`):

| class | exact line template |
|---|---|
| config | `config: expected valid versioned configuration; found invalid; needed supply the declared keys and bindings` (v3 only; v1/v2 keep their existing lines) |
| config | `config: expected trusted discovery boot descriptor; found missing; needed launch with --discovery-boot-fd FD` (v3 discovery read with no supplied descriptor) |
| config | `config: expected trusted discovery boot descriptor; found rejected SUBJECT CODE; needed REMEDY` (supplied v3 boot rejected; closed substitutions below) |
| arguments | `arguments: expected closed arguments for QUERY; found invalid; needed use the declared query schema` |
| profile | `profile: expected configured profile; found ID; needed use a profile from configuration` |
| cursor | `cursor: expected current snapshot and valid page offset; found invalid; needed restart from lane_start` (grammar/offset) |
| cursor | `cursor: expected snapshot CURRENT; found GIVEN; needed restart from lane_start` (mismatch) |

For rejected supplied boot, SUBJECT is literal `descriptor` or one of the four
fixed granted paths; no caller text, FD number or OS error text is echoed.
CODE and REMEDY are paired exactly below. An invalid/non-directory/unreadable
FD is rejected, not missing. A missing component is observed absence under a
supplied descriptor; unreadable means the component could not be examined.

| CODE | condition / SUBJECT | REMEDY |
|---|---|---|
| descriptor-unreadable | unusable supplied descriptor / descriptor | supply an inherited readable directory descriptor with --discovery-boot-fd FD |
| component-missing | fixed component does not exist / fixed path | supply all four fixed boot components in the retained artifact |
| component-unreadable | fixed component cannot be read / fixed path | make the fixed boot component readable in the retained artifact |
| unsafe-path | symlink component or ancestor / fixed path | supply fixed boot components beneath the descriptor without symlinks |
| not-regular | component is not a regular file / fixed path | supply the fixed boot component as a regular file |
| component-oversized | component exceeds 1 MiB / fixed path | supply a boot component of at most 1 MiB |
| total-oversized | captured total exceeds 4 MiB / descriptor | supply boot components totaling at most 4 MiB |
| changed-during-read | before/after identity, size or modification differs / fixed path | supply a stationary retained boot artifact and relaunch |
| timeout | bootstrap exceeds 10 seconds / descriptor | supply a readable stationary boot artifact within the 10-second bootstrap deadline |
| binding-unverified | no verifiable execution-input evidence / fixed module path | use a trusted loader that binds execution inputs to retained boot bytes and relaunch |
| binding-mismatch | execution inputs differ from captured bytes / fixed module path | load the five discovery modules from the same retained boot bytes and relaunch |

Check descriptor usability first, capture components in bytewise fixed-path
order with path/type checks before size/read checks, then verify execution
binding in that same module-path order. Stop at the first failed check;
deadline/aggregate exhaustion stops immediately. The aggregate bound remains
mandatory even though four individually bounded components cannot exceed it.
Each refusal closes owned descriptors and precedes repository acquisition.
These new boot lines supersede the earlier single boot line only; all other
legacy refusal bytes remain unchanged. Independent witnesses cover missing,
each reachable rejected condition, and a clean neighbor under D-A47.

Exit 0 includes unavailable/unknown-source readings; exit 1 unused.
Exit 3 only outer wall/kill, not source timeout (a reading).
No identity refusal. Refusal examples and exact successful envelopes are in
[discovery examples](discovery-examples.md); the document is non-executable.

## Action index

| name | owner | entrypoint | availability derivation |
|---|---|---|---|
| onboard | ops/conductor/onboard-lever.md | ops/conductor/tools/onboard.py | implementation_present and available equal the verified executable-blob presence; missing reason missing, failed check reason unreadable |
| handoff | ops/conductor/handoff/README.md | null | false; disabled |
| aar | ops/conductor/handoff/README.md | null | false; disabled |
| closeout | docs/workflow.md | null | false; disabled |

Here available means a documented implementation entrypoint exists, not an
installed callable handle or permission to act. All installation/activation/
probe fields remain null. Owner-initiated handoff/AAR/combined writes are
step 4; exit, crash and compaction create no completed records.

## Concept index

Routing metadata amended by commit against the existing governing homes.
There is no profile column: profile config chooses membership and priorities.
Paths are repo-relative. The implementation column is an executable target,
`unbuilt`, or `none` for document-only concepts. Intended names are direction.

Framework intentions describe prod application concepts only. Existing dev
tools keep their own service owners; a null intended value gives no replacement
target. `wf-runtime` denotes the unbuilt generic dev-lane runtime, not a future
Symfony implementation. The workflow-genus contract can also describe prod's
Symfony Workflow use without choosing the dev runtime's technology.

| concept | home | contract | entrypoint | registry | implementation | intended |
|---|---|---|---|---|---|---|
| `wire` | `docs/wire.md` | `docs/wire.md` | null | null | `unbuilt` | `packages/wire` |
| `workflow-genus` | `docs/workflow.md` | `docs/workflow.md` | null | null | `none` | `symfony/workflow` |
| `wf-runtime` | `docs/workflow.md` | `docs/workflow.md` | null | null | `unbuilt` | null |
| `probes` | `docs/probes.md` | `ops/conductor/probes-lever.md` | null | null | `unbuilt` | null |
| `concerns` | `docs/concerns.md` | `docs/concerns.md` | null | null | `none` | null |
| `verified-couplings` | `docs/verified-couplings.md` | `docs/verified-couplings.md` | null | null | `none` | null |
| `mapping` | `docs/mapping.md` | `docs/mapping.md` | null | null | `none` | `four projections` |
| `onboard` | `ops/conductor/onboard-lever.md` | `ops/conductor/onboard-lever.md` | `ops/conductor/tools/onboard.py` | `ops/conductor/registry/onboard.py.json` | `ops/conductor/tools/onboard.py` | null |
| `lane-mcp` | `ops/conductor/lane-mcp-lever.md` | `ops/conductor/lane-mcp-lever.md` | `ops/conductor/tools/lane_mcp.py` | `ops/conductor/registry/lane_mcp.py.json` | `ops/conductor/tools/lane_mcp.py` | null |
| `lane-read` | `ops/conductor/lane-mcp-lever.md` | `ops/conductor/lane-mcp-lever.md` | `ops/conductor/tools/lane_read.py` | null | `ops/conductor/tools/lane_read.py` | null |
| `dispatch` | `ops/devlane/dispatch/CONTRACT.md` | `ops/devlane/dispatch/CONTRACT.md` | `ops/devlane/dispatch/launch.py` | `ops/devlane/task/jobs.json` | `ops/devlane/dispatch/launch.py` | null |
| `harness` | `ops/devlane/harness/` | `ops/devlane/harness/context.cue` | null | null | `none` | null |
| `jobs` | `ops/devlane/task/jobs.json` | `ops/devlane/task/CONTRACT.md` | null | `ops/devlane/task/jobs.json` | `none` | null |
| `handoff` | `ops/conductor/handoff/README.md` | `ops/conductor/handoff/README.md` | null | null | `unbuilt` | null |
| `aar` | `ops/conductor/handoff/README.md` | `ops/conductor/handoff/README.md` | null | null | `unbuilt` | null |
| `decisions` | `ops/conductor/decisions-register.md` | `ops/conductor/decisions-register.md` | `ops/conductor/tools/decisions.py` | `ops/conductor/registry/decisions.py.json` | `ops/conductor/tools/decisions.py` | null |
| `ledger` | `ops/conductor/handwork-ledger.md` | `ops/conductor/handwork-ledger.md` | `ops/conductor/tools/hand.py` | `ops/conductor/registry/hand.py.json` | `ops/conductor/tools/hand.py` | null |
| `watch` | `ops/conductor/watch-lever.md` | `ops/conductor/watch-lever.md` | `ops/conductor/tools/watch.py` | `ops/conductor/registry/watch.py.json` | `ops/conductor/tools/watch.py` | null |
| `land` | `ops/conductor/land-lever.md` | `ops/conductor/land-lever.md` | `ops/conductor/tools/land.py` | `ops/conductor/registry/land.py.json` | `ops/conductor/tools/land.py` | null |
| `ci-store` | `ops/conductor/ci-store.md` | `ops/conductor/ci-store.md` | `ops/conductor/tools/ci.py` | `ops/conductor/registry/ci.py.json` | `ops/conductor/tools/ci.py` | null |
| `branches` | `ops/conductor/branch-census.md` | `ops/conductor/branch-census.md` | `ops/conductor/tools/branches.py` | `ops/conductor/registry/branches.py.json` | `ops/conductor/tools/branches.py` | null |
| `skills` | `.claude/skills/skills/SKILL.md` | `.claude/skills/skills/SKILL.md` | `ops/conductor/tools/skills.py` | null | `ops/conductor/tools/skills.py` | null |
| `permission` | `ops/conductor/permission-surface.md` | `ops/conductor/permission-surface.md` | `ops/conductor/tools/co_doc.py` | null | `ops/conductor/tools/co_doc.py` | null |
| `monitor` | `ops/conductor/monitor-lever.md` | `ops/conductor/monitor-lever.md` | `ops/conductor/tools/monitor.py` | `ops/conductor/registry/monitor.py.json` | `ops/conductor/tools/monitor.py` | null |
| `watchdog` | `ops/conductor/watchdog-lever.md` | `ops/conductor/watchdog-lever.md` | `ops/conductor/tools/watchdog.py` | `ops/conductor/registry/watchdog.py.json` | `ops/conductor/tools/watchdog.py` | null |
| `ladder` | `ops/conductor/ladder-lever.md` | `ops/conductor/ladder-lever.md` | `ops/conductor/tools/ladder.py` | `ops/conductor/registry/ladder.py.json` | `ops/conductor/tools/ladder.py` | null |
| `onboard-discovery` | `ops/conductor/discovery-lever.md` | `ops/conductor/discovery-lever.md` | null | null | `unbuilt` | null |
| `conduct` | `ops/conductor/conductor-start-plan.md` | `ops/conductor/conductor-start-plan.md` | null | null | `unbuilt` | null |
| `packages` | `packages/README.md` | `packages/README.md` | null | null | `none` | `bundle gestation` |
| `projections` | `AGENTS.md` | `AGENTS.md` | null | null | `none` | `CLI, TUI, MCP, API` |

## Smallest extensions and measured boundary

The [handoff custody note](handoff/README.md#discovery-metadata-extension)
owns trusted record-binding semantics. Bindings in launch configuration are
a temporary read input, not a new register: future publication must supply
them through the existing record lineage. No old record is rewritten or
assumed to carry that metadata. Work bindings belong to the existing
task/dispatch context. Extending owner schemas and independently verifying
their custody precedes automated production of these bindings.

Step 2 owns the v3 hermetic config and trusted-boot refusal witnesses in the
existing `ops/conductor/registry/lane_mcp.py.json`; they must exercise the
declared v3 lines. Preserve onboard's seven-row table and separately verify
its implementation/fixtures and discovery's shared five-row subset. This
correction changes no runtime, tests or registry. Step 3 extends
[harness capabilities](harness-capabilities.md) and
[permission surface](permission-surface.md) for measured capabilities,
selection constraints and conductor policy; it does not give discovery
permission to probe or enforce. Worker source grants are the separate
task/harness follow-up, not a role switch on these queries.

Historical draft measurement (not reissued as current): 2026-09-06T01:15:46Z against
`3682a8c58e304e7025036f1b3ec4edb63424832e`: parsed composer.json/lock,
enumerated packages, checked vendor and proposed instrument paths, and parsed
lane_read's AST. Relevant locked/resolved versions are in the
[plan](conductor-start-plan.md#symfony-mapping-preserved-historical-measurement).
vendor, lane_refs.py, wf.py and probes.py were absent; packages contained
README.md only; lane_read had zero subprocess mentions. No installed runtime,
Git namespace contents, package release freshness or host activation was
measured. [docs/mapping.md](../../docs/mapping.md) describes prod's Symfony
ownership only; its dev-tool replacement doctrine is superseded. Current
recovery evidence is in the reconciliation note, separately from these prior
readings. No package/dependency change here.

## Non-executable acceptance rows

These are the specification for independently authored step-2 tests, not
tests executed or approved by this correction's author. Plant synthetic
trees/configs/adapter readings; never use live stores, credentials or captured
AAR bodies as oracles. Compare canonical bytes across CLI/tool/resource.
A counterparty change re-demands its coupled rows.
Historical checkers, acceptance reports and producer receipts retain their
original refs and verdicts as provenance, not proof of these corrected rows.
An independent contract reviewer precedes test correction; a test producer
independent of the runtime producer owns executable coverage and a separate
reviewer checks it without implementation access. Integration must measure
discovery_v3, lane_mcp, onboard and conformance suites at one combined ref,
then `bash ops/bin/suites.sh`, with raw v1/v2 refusal parity and no unresolved
acceptance skip. This document neither executes nor approves those tests.

| id | planted case | required observation |
|---|---|---|
| D-A1 | v1 check and a discovery query | tools 4; discovery arguments refusal; all v1 bytes preserved |
| D-A2 | v2 check, tools, resources, _meta and brief | tools 7; existing bytes and limitations unchanged |
| D-A3 | valid v3 including null optional sources | tools 11, no source access during check |
| D-A4 | extra/missing keys, bool counts, invalid paths, conflicting metadata/cycle | config wire, no acquisition |
| D-A5 | missing profile, unknown field, role/path/command argument | arguments; no acquisition |
| D-A6 | valid unknown profile | profile wire; no acquisition |
| D-A7 | shipped dev, all fixed docs readable, bounded normal records; shared law equality and missing-row controls | startup ≤8192; lane-mcp, dispatch, onboard present; catalogue fully pageable; exactly five law pairs in frozen order equal onboard's first five of seven; one missing shared file yields four present/one missing; onboard arithmetic is specified by its owner |
| D-A8 | shipped prod, same fixture | startup ≤8192; mapping, packages, projections present; lock absence distinct from installation |
| D-A9 | added docs profile from config example | wire startup; wire/onboard catalogue; docs-root relevance, no new branch in code |
| D-A10 | catalogue >16 within 64 limit | bounded pages reach every home once; named card works independently |
| D-A11 | complete empty HEAD + enabled empty retained namespace | complete true, none, counts zero; not unavailable |
| D-A12 | one relevant bound handoff exists only at unknown retained filename | enumerated, one selected key; named body without caller path |
| D-A13 | two current relevant handoffs; or two revisions of one ID | multiple, no selected key; no timestamp/path tie-break |
| D-A14 | identical HEAD/ref bytes and two distinct blobs in one ref | one key per digest, all occurrences bound; no stem collisions |
| D-A15 | trusted same-lineage supersedes pair | old stale, new current; AAR never selected as handoff |
| D-A16 | complete readable relevant supersedes chain, with a stale-only presentation page and the sole terminal current handoff on another page | global selection one and terminal selected_key on every page and named stale detail; stale cards/body remain readable and never selected; nonempty complete set has at least one current handoff; no skip or impossible preclassified fixture; D-A11 empty none and D-A17 missing-evidence unknown stand |
| D-A17 | unbound old handoff, unreadable candidate or missing bound handoff beside one good handoff | unknown, null selected_key/counts, complete false; observed unbound handoff is a bindings failure; missing evidence visible |
| D-A17b | one readable bound current handoff plus unbound AARs, complete scans | one, that selected_key, complete true, current 1/stale 0/unknown 0; AAR cards visible with metadata-missing, excluded from handoff counts |
| D-A17c | pinned HEAD 38edb5de5410e9881332ae6cfdd8788991d96f02, shipped dev/prod declarations, empty record_bindings, retained disabled | unknown, complete false, selected_key/counts null; four unbound handoffs and four unbound AARs visible, no inferred bindings or body/provenance leakage |
| D-A18 | dev-only, prod-only and mixed-root bindings | root+lineage filtering; explicit common lineage required; no cross-profile body leak |
| D-A19 | missing work binding | unknown work relevance, no inferred profile from title/ref |
| D-A20 | register/ledger/PR sources unsupported | exact unavailable summaries; no subprocess, DB, gh, home/env fallback |
| D-A21 | zero authorized dispatch IDs; then malformed authorized record | zero scoped counts; then unavailable count, no live-process claim |
| D-A22 | named misses for all three detail queries | exact envelope, null item, fixed unknown-* reason |
| D-A23 | detail with profile and snapshot from start | same source closure and snapshot including actions |
| D-A24 | retained ref moves to new commit with identical blob bytes | snapshot changes; old continuation refuses cursor |
| D-A25 | content, profile roots, binding/supersedes, adapter or source failure changes | snapshot changes; old continuation refuses |
| D-A26 | malformed, wrong-profile, missing/mismatching snapshot or out-of-bounds cursor, including lane_start | cursor wire; lane_start accepts canonical 0..20 below total, homes/handoffs 1..255 below total; no leading zeros, silent restart or skip |
| D-A27 | 65 refs, 257 occurrences, 4097 scanned tree entries, byte/op/time budget | declared overflow, incomplete or unavailable; never unique partial selection |
| D-A28 | namespace unreadable vs empty vs disabled | unavailable/ok/disabled distinguished; disabled omission gives declared scope |
| D-A29 | anchor symlink/ancestor swap, FIFO, malicious .git/commondir/alternates, ambient CWD/GIT_DIR | unsafe/repository failure, no escape/fallback/read effects |
| D-A30 | missing or wrong-type objects, symlink blob, unknown caller OID/path | adapter rejects; only verified operation outputs can become inputs |
| D-A31 | real ops.conductor.tools.lane_refs.acquire(config, boot, checkpoint=...) at each declared phase; synchronous acknowledged retained-ref or authorized-file mutation, plus stationary neighbor and omitted-check fault control | exact eligible phase/source multiplicity and per-source partial order, including failed-before-phase/early-stop omissions; checkpoint reached within budgets; recorded checkpoint/mutation/postcheck ordering; unavailable changed-during-read, snapshot/data null, no retries; stationary success and omitted-check rejection; injected result proves projection only |
| D-A32 | named hostile fixture plus bodies empty, ending in LF, containing exact END UNTRUSTED RECORD lines, prefixed markers, CRLF, quotes/backslashes and Unicode | CLI/tool/resource text and structuredContent carry the exact pre-transport wrapper formula and E bytes; only LF-delimited exact end-marker segments gain >; inner digest/length unchanged, envelope counts escaped wrapper; corroboration required, no semantic immunity claim |
| D-A33 | governing document exists but implementation absent | document_present true; implementation_present false; no invented entrypoint |
| D-A34 | intended symfony/workflow in packages[].name, then packages-dev[].name, vendor absent; then only constraints/other locations or no mention; malformed/unavailable lock | true for either exact name entry only; false for measured name absence (including the pinned HEAD's constraint-only mentions); null for failed lock; installed/activated/probed null; AAR contract is custody README |
| D-A35 | onboard executable exists; lifecycle actions absent | onboard available means presence only; other actions false/disabled |
| D-A36 | 2049-byte body or Unicode encoding expansion, oversized frame/item | body null with count/reason or exact unavailable; no truncation or empty non-advancing page |
| D-A37 | 17 candidates within complete scan | page size ≤16; global selection unchanged; named lookup works |
| D-A38 | initial eight-work slice exceeds cap but one card fits; then one oversized card or mandatory frame | initial work dropped with work omitted: N and work next cursor: S:0; continuation greedily advances without losing owners; item-exceeds-cap if no next card fits, page-frame-exceeds-cap if mandatory frame fails; every lane_start ≤8192 |
| D-A38b | 20 authorized relevant work bindings in job_ids order, eight cards fit per page | initial/continuation counts 8/8/4, cursor offsets 8/16 then no cursor, omissions 12/4 then none; all 20 reachable once via CLI/MCP, same owners/law/state/actions/Selection/snapshot each page; tools remain 11 |
| D-A38c | source_ids is repo plus 21 configured dispatch IDs in job_ids order, retained absent from source_ids, dispatch non-null, work_bindings empty; all dispatches readable, stationary snapshot S; mandatory frame fits and five work cards fit but six exceed 8192 bytes including final omissions at each nonfinal continuation | config valid at 22 source IDs; closure has 26 Source entries including disabled retained; total 21 unknown cards with lineage null and reason metadata-missing; initial eight-card slice dropped, work omitted: 21 and work next cursor: S:0; CLI/MCP continuations at offsets 0/5/10/15/20 return 5/5/5/5/1, omissions 16/11/6/1 then none, next cursors S:5/S:10/S:15/S:20 then none; S:20 with matching snapshot S returns the 21st card exactly once; S:21 refuses cursor, as does S:20 when total is 20; every successful envelope ≤8192 with the same owners/law/state/actions/Selection/snapshot; tools remain 11 |
| D-A39 | stationary sources and resource first pages | byte-identical CLI/tool/resource envelopes, no per-call timestamps |
| D-A40 | read without WF_AGENT, tools/list | success; readOnlyHint true, destructiveHint false |
| D-A41 | before/after filesystem, DB, refs/index/config/object tree; import graph | unchanged; no writer imports; read core still no subprocess |
| D-A42 | two trusted launch-supplied descriptors, conductor config versus job-bound config, same harness/profile; query attempts config/audience/role/repository/path overrides | conductor queries use the conductor descriptor; the distinct job-bound descriptor/grants are a launch fixture assumption, not a worker-server test; all override arguments rejected before acquisition; wrong FILE is a launch defect, no claimed host isolation or other-tool enforcement |
| D-A43 | independently started conductor, dev/prod/extra profiles, no path hints | startup then targeted detail identifies owners, tools and relevant handoff or exact uncertainty |
| D-A44 | v3 old seven queries on unchanged v2 inputs | byte-identical; no broader cleanup |
| D-A45 | known irrelevant key requested by another profile | same unknown-key envelope as nonexistent key; no prose/provenance leakage |
| D-A46 | same failed source twice, then recovery | deterministic failure fingerprint; recovery changes snapshot |
| D-A47 | two bounded launch artifacts via --discovery-boot-fd with changed contract bytes, fixed config and failing repo; separately changed implementation bytes; missing/rejected supplied boot; external pathname mutation after load; executed-byte binding gate A/B mismatch, unverified load, A/A and B/B controls for each module | independently computed contract revision/definition/source fingerprint changes with exact bytes while contract stays ok; sorted five-file manifest changes adapter identity, direct objects git:null; retained bytes do not reload; exact condition-specific boot refusals before anchors, check/list/old queries read no boot; actual execution markers and loader-input receipts bind executed bytes to reported identity; omitted-check/hash-A-execute-B counterparty fails; fixture/contract fingerprint remains planted; no host admission claim |
| D-A48 | valid config/boot, retained disabled, stable missing HEAD object; prod mapping Home through CLI/tool/resource; contrast verified HEAD followed by failed document read and measured/attempted implementation/lock fields | missing HEAD permits no Home-property tree_entry/read_blob, ownership unresolved, document_present and entrypoint null, evidence exactly ["contract"]; actual property attempt including failure or locked false/null gives ["contract","repo"]; mandatory repo source retains upstream failure without invented per-path attempts; wf-runtime example unchanged |
| D-A49 | lane-mcp and lane-read concept rows; historical mapping heading/link | lane-mcp registry is ops/conductor/registry/lane_mcp.py.json, lane-read registry null; step 2 appends to existing file; mapping heading/link identify preserved history, no remeasurement |

No source, settings, dependencies, database/host changes, executable tests,
active config example, registry entry or PR is part of this revision.
Step 2 must prove these promises before any implementation is called usable.
