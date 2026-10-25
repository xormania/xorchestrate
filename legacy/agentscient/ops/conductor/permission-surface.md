# The permission surface — contract for /co-doc

Current operation (owner direction, 2026-09-24): `AGENTS.md` describes the working
guidance. Hooks warn and guide; CI owns acceptance. Hand grants are retired,
and the pipeline, dispatcher and deliverable records are optional capabilities.
Historical procedure below does not impose prerequisites on ordinary work.


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

The conductor acts through a stack of things that may refuse it: hooks,
permission rules, levers, the launcher, identity. Each layer below declares
its probe (an oracle that executes), its tier, and its remedy shape. The
doctor (`tools/co_doc.py`) is this contract's instrument: it runs every
probe read-only and reports findings with remedies. It is not the fixer.

**Lockout invariants** (owner + conductor, 2026-08-31):

1. Reads are never gated. Any layer able to refuse a read is itself a
   finding, per se.
2. Every refusal names its remedy; layers under this contract also name
   the doctor.
3. The doctor depends on nothing it diagnoses: stdlib Python, filesystem
   reads, and at most a `--help` subprocess. No launcher, no wrapper, no
   database writes. If everything else is wedged, it still runs.
4. The bypass is the owner, formalized: the doctor proposes; application
   of a remedy happens only with the owner present, one decision at a
   time. (The pre-ratified remedies table that would let it fix
   unattended is **ratification-pending** — until the owner ratifies it,
   v1 applies nothing.)

## Probes

| id | layer | asserts | tier |
|---|---|---|---|
| P-SET-1 | settings | every present settings file (`.claude/settings.json`, `.claude/settings.local.json`, `~/.claude/settings.json`) parses as a JSON object; an absent file is reported absent, never as clean-by-silence | 2 |
| P-PERM-1 | permissions | every `permissions.allow` rule naming an absolute path resolves to an existing file — an allow whose target does not exist is an **inert grant**, the lockout that looks like permission | 2 |
| P-PERM-2 | permissions | no deny rule textually shadows an allow's target (heuristic, stated as such in output) | 3 |
| P-HOOK-1 | hooks | every configured hook's command file exists and is executable; when `CONTROL_STATE` is supplied, the conductor control's pinned files and retained owner plan match their recorded hashes (otherwise the context is explicitly ABSENT) | 2 |
| P-HOOK-2 | hooks | every hook matcher names the tools it judges — a `.*` or absent matcher is a finding (the tool-guard lesson, agent-lab rule) | 2 |
| P-LEV-1 | levers | `dispatch.conf` parses; an explicit `LAUNCHER_PIN` or the sibling-default launcher, `dispatch.sh`, and `gate.sh` are each reported live or inert with the path examined | 2 |
| P-ID-1 | identity | `WF_AGENT`, when set, is `Name <address>`; unset is a note with the export line as remedy (reads need no identity) | 2 |
| P-WF-1 | record | `wf.py` exists and `--help` exits 0 | 2 |
| P-DB-1 | ledgers | `handwork.db` and `skills.db` open; open grants past TTL counted and named | 2 |

## Output contract

One line per finding: `[CLASS] probe-id: what was expected / what was found
— remedy: <one sentence>`. CLASS is the four-class taxonomy: Rejection,
Denial, Abort, Finding. A probe that cannot examine its layer reports
INVALID for that probe — an unperformed scan is never a clean result.
Exit 0: every probe ran, no findings. Exit 1: findings present. Exit 2:
one or more probes INVALID.

`--list-probes` prints every probe id above, one per line — the drift
check between this contract and the instrument.

## Remedies (proposed by the doctor; application ratification-pending)

| finding | proposed remedy |
|---|---|
| inert grant | repoint the rule at the resolving path, or complete the step that makes it resolve (e.g. the main checkout's move to dev); never widen |
| broad/absent hook matcher | scope the matcher to named tools; move general policy into `permissions` |
| missing hook file | remove the entry or restore the file — a hook that cannot run is config lying about enforcement |
| malformed settings | restore from the timestamped backup beside it, or fix the JSON; the doctor names the parse error |
| stale lever pin | repoint at the measured current target; record the measurement |
| stuck grants | settle or expire them with reason; a stuck grant is unfinished evidence |

A remedy that would **widen** any permission is never proposed for
unattended application, under any future ratification.

## Registration duty

Any new gate that can refuse the conductor registers here with a probe id
and a remedy shape before it arms, or it is an unproven gate. This table
and `co_doc.py --list-probes` must agree; their drift is itself a finding.

Source: owner doctrine 2026-08-31 (lockout fear, skill bypass, CDD) +
conductor design, this session. Ratified-by: pending owner for the
remedies-application clause; the diagnostic contract is design (conductor).


## Normalized conductor tool interface

Adoption unit 1b defines a future refusal surface; it does not arm the old
conductor guard, modify settings or add current doctor probe ids. Policy lives
once in [lane-policy.md](lane-policy.md#bootstrap-and-minimum-complete-activation).
Adapters translate requests and decisions; the independent doctor above remains
stdlib/read-only and does not depend on startup, policy loading or a vendor
session. A later gate implementation must register its diagnostic and remedy
with the existing doctor before arming; its read scan must work even when that
gate cannot start. Live checks belong to the
[separate diagnostic mode](probes-lever.md#conduct-live-diagnostics).

`normalize_tool(bound_context, native_request)` returns the closed Request
below or refuses. `evaluate_tool(bound_role, normalized_request)` returns the
closed Decision below. These are in-process interfaces, not MCP actions or
another CLI. bound_role is a verified handle to the launch descriptor, policy
binding and admitted tool declarations; the request cannot select it. The
normalizer is pinned adapter code. Never trust a model-supplied classification,
claimed effect list, command substring or tool display name as provenance.

Counterpart ownership: adoption unit 1a supplies the matching declaration of
`evaluate_tool(bound_role, normalized_request)` and its Request/Decision coupling
in [lane-policy.md](lane-policy.md). At review `20260906T110711Z-review-claude-e4968e`
that declaration was pending; the bootstrap citation alone established only
module ownership. Unit 1a closes the documentary edge by binding these exact
shapes. Executable coupling remains unresolved until independent verification;
neither the declaration nor this interface activates it.

Use the startup contract's strict JSON and canonical digest rules, with a
65536-byte request bound and no duplicate/missing/unknown fields. Request is
exactly `{schema:"conductor-tool/1",id:ID,binding_sha256:SHA256,kind,target,
arguments}`. id is allocated by the trusted intercepting adapter; binding_sha256
must equal the held conductor descriptor digest. kind is one of the following;
target and arguments are closed tagged variants, never arbitrary passthrough:

| kind | target | arguments and admitted interpretation |
|---|---|---|
| read | admitted read-operation ID | the target owner's closed read schema; anchored paths/refs only within source/custody grants |
| delegate | canonical frontend ID for dispatch/go/ladder | the frontend owner's validated structured request, transferred to the single worker service; no raw vendor execution |
| coordinate | admitted coordination lever ID | the lever owner's closed verb schema and grants; no implementation editing hidden in coordination |
| mcp-read | bound read-only tool name or resource ID | exactly its existing query schema or resource URI; same descriptor/config/session binding |
| forbidden | literal `implementation` | exactly `{reason:Reason}` |

Read/coordinate/delegate target declarations come from the trusted owner of
that existing lever, including its executable/import closure, allowed verbs,
argument schema, read scope and permitted effect contract. Missing declaration
refuses; this interface creates no registry and no wildcard shell allowance.
The `arguments` value is one strict JSON object verified by that target's schema
before evaluation; its bytes are retained only if admitted as non-secret input.
A model-supplied target label never bypasses resolving the actual executable or
native tool identity. Reads include canonical Git state/diff, contract/application
inspection within custody, board/status/record and handoff discovery. They require
no WF_AGENT, fresh permission grant or new owner prompt. A read outside custody
is not a permitted read. An unavailable read source stays an unavailable reading,
not an implementation-handwork violation. Coordination writes require the lever's
existing authorization; handoff/AAR publication remains owner-requested only.

Reason is exactly one of `direct-edit,git-mutation,shell-escape,raw-vendor,
in-session-agent,unknown-tool,malformed-request,binding-mismatch`. These are
fixed codes, never the original command, path or rejected value. Normalize
native edits/apply_patch, Git mutations such as apply/checkout/reset, direct
vendor or task vendor-launch paths and in-session agents to forbidden. Arbitrary
shell/interpreter programs cannot be represented as a trusted lever invocation.
A shell wrapper may normalize a single exact declared invocation only if the
adapter proves tokenization and the executed program/arguments/environment are
identical: no separators, pipes, redirections, command substitutions, eval,
wrappers, environment assignments or interpreter escape. Anything not provable
refuses shell-escape. The presence of dispatch.sh anywhere in text grants nothing.
Canonical dispatch may itself execute workers under its admitted effect contract;
the conductor cannot launch those vendor processes directly.

Decision is exactly `{schema:"conductor-decision/1",id:ID,
binding_sha256:SHA256,outcome:"allow"|"refuse",refusal:Refusal|null}`.
The id/binding must match Request and the held current binding. On allow,
refusal is null. On refuse it is exactly `{class:"conductor-tool",expected,
found,needed}` with nonempty strings chosen by the evaluator from fixed templates;
no raw request echo. Fixed field values must contain no semicolon, so the wire's
`; ` separators remain unambiguous. It serializes to the wire before effects. Example:

```json
{"class":"conductor-tool","expected":"declared conductor operation","found":"direct-edit","needed":"use canonical dispatch and run the independent doctor for gate diagnosis"}
```

The adapter consumes a matching decision once for that intercepted request only,
checks that native request/executable/cwd/environment and held binding have not
changed, then either executes that exact allowed invocation or blocks it. A
missing, late, malformed, duplicate or mismatched response fails closed before
execution. The adapter cannot turn refusal into permission prompts, automatic
approval, retry under weaker settings or an unrelated allowed request. Transport
failure is recorded as unavailable decision evidence, while the request is
blocked. No durable decision store is added; existing attempt transport retains
admitted non-secret evidence. Unknown/rejected native data never enters records.

### Acceptance duties and bounded refusals

Independent cases COND-HAND-P1/N1/L1 prove canonical delegation and necessary
coordination reads work, and forbidden direct-tool/Git/shell/vendor/sub-agent
attempts block before tree/index/ref effects. Include read commands containing
words such as apply without executing a mutation, and compound commands naming
an allowed lever; classify actual operations, not prose. COND-AXIS-N1 changes
harness, model, role, job role and profile separately. COND-EFFECT-N1 compares
only the declared effects of canonical coordination/delegation. Malformed hook
payloads and decisions, changed/replayed bindings, prompt attempts and missing
interception each prevent conduct admission. Repeat on resume and every supported
harness using the shared policy, with verified mocks and live observations.

These are adoption acceptance duties from the Daybreak input, not claims of an
introduced exploit in this documentary change. Existing lane-policy P1–P5,
N1–N10, R1–R5 and I1 remain binding. In particular shared inspection reads stay
permitted; the older unarmed guard's ban on hand investigation supplies no new
authority. Doctor findings propose bounded remedies; neither a diagnostic run
nor a passing observation can ratify policy or widen permissions.
