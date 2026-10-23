# Harness and CLI capability contract — first draft

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

Standing: PROPOSED. Born 2026-09-01 from the owner's inventory ask
("produce the inventory as the first contract draft - in fact -
inventory of everything is on the docket"); the triangle it anchors
is the contract-verified-mock idea in the intake. Rows are
RELIANCES, not catalogs: each declares a surface the lane actually
depends on, which is what the probe re-checks and a mock must
implement. Every row is true of one version on one day — the
isolation probe's doctrine governs the whole file. Status markers:
MEASURED (with the day's evidence), DOCTRINE (standing recorded
law), UNPROBED (relied on without a probe yet — a named gap, never
a silent one).

## Claude Code — the maximally capturable runtime

| reliance | why the lane needs it | status |
|:--|:--|:--|
| settings files and precedence (user, project, local) | law and guards installed from files we own | MEASURED 2026-09-01 (guard install; both hooks verified by jq) |
| lifecycle hooks: pre-tool, post-tool, stop | the lane's law firing natively: trailer guard, clipped-sweep guard, backtick guard, delegation refusal, context guard | MEASURED 2026-09-01 (each fired at least once today) |
| hook matchers over MCP tool names (`mcp__<server>__<tool>`) | the engine and the matrix see an MCP write only when a PreToolUse matcher names the tool; an acting MCP tool the lane adds is guarded server-side first (coordination objective §7) and by a matcher second | MEASURED 2026-09-16 at 46639344 on Claude Code 2.1.273, the settings text and the vendor page only: `.claude/settings.json` binds the engine and the matrix on PreToolUse to the matcher `Write\|Edit\|MultiEdit\|mcp__serena__(replace\|insert\|rename\|safe_delete).*`, and the same matcher on PostToolUse to ruff. The vendor rule (code.claude.com/docs/en/hooks §Matcher patterns and §Match MCP tools, read the same day): exact names separated by `\|` match exactly; any other character makes the whole value an unanchored JavaScript regular expression. This matcher carries `(`, `.` and `*`, so it is a regex: it reaches every tool whose name contains `Write`, `Edit` or `MultiEdit`, from any server, `NotebookEdit` and a `mcp__<server>__WriteFile` included, besides the four serena prefixes; `mcp__serena__find_symbol`, `mcp__memory__get` and `Bash` are not reached (a `re.search` reproduction; grok 20260916T094152Z-review-grok-e72972#1, upheld by codex 20260916T095708Z-adjudicate-codex-c5aefc). No matcher spells `mcp__.*`. MCP tools appear as regular tools in PreToolUse, PostToolUse, PostToolUseFailure, PermissionRequest and PermissionDenied and are matched by name; `mcp__<server>__.*` and `mcp__.*__write.*` are the vendor's shapes; a bare `mcp__memory` is an exact string and matches nothing. UNPROBED: the hook firing on a live MCP call (no probe row can invoke an MCP tool, docs/probes.md's allowlist; no mock; codex 20260916T094142Z-review-codex-995043#5, partial). W2's widened matcher for an acting server is anchored. The premise that hooks cannot see MCP calls from any server but serena (PR #242's body, 2026-09-16) was a reading of this wiring, not of the harness, and the wiring already reaches other servers' tools by substring. Rows P-CLS-20260916-* |
| stop-hook wire: transcript_path, stop_hook_active, last_assistant_message; block-decision JSON; platform block cap | the context guard's whole mechanism | MEASURED 2026-09-01 (adversarial review verified fields against the live version) |
| permission rules plus the auto-mode classifier | the acting surface's gate; the classifier is stochastic call-by-call and judges the command text, outside every lever | MEASURED 2026-09-01 (same command allowed then denied; one deterministic allow rule). MEASURED 2026-09-16 (issues report in record 20260916T085626Z-review-claude-30f3eb, item 1, by agentscient-c8): `hand.py declare` denied on its first call with the reason "Self-Modification", the identical command passing on retry after the terminal's "approved", one turn lost; grant 260 in handwork.db is that declare; no allow rule for the lever in either settings file that day. The edge is external (docs/verified-couplings.md §External surfaces): the mechanism that removes the class is a deterministic allow rule for the lever's argv shape in the owner's settings, the owner's decision; in session the denied command goes to the Lead as text and the work continues (owner's standing rule, relayed 2026-09-16). Rows P-CLS-20260916-* |
| project MCP registration (.mcp.json), read-only servers | the context-and-diagnostics plane ruling | DOCTRINE (workflow server precedent); probe UNPROBED; superseded 2026-09-16 (D-MCP-1): MCP acts as well as reads |
| skills and commands directories; description injection | the pipeline's entry points for two of three harnesses | DOCTRINE (AGENTS.md records the reach) |
| cross-session messaging and idle notices | the baton; teammate coordination | MEASURED 2026-09-01 (delivery works; held-then-dropped failure mode measured once) |
| background tasks with completion notifications | every watch and dispatch wait today | MEASURED 2026-09-01 (dozens of cycles) |
| the Monitor tool: one notification per stdout line, lines within 200ms batched into one, a 5-minute default deadline and a 30-minute ceiling, automatic stop when a source floods | the watch skill's live watch — a session is told each watchdog condition instead of polling | MEASURED 2026-09-16 on harness 2.1.273 (the Dispatcher probe's reading that morning; four spaced stdout lines arrived as four notifications with the exit reported, this session; the flood suppression notice read by agentscient-11 the same day). No probe re-checks it on a cadence: a named gap |
| session transcript JSONL shape (assistant rows, typed content parts) | the context guard's fallback path; future mining | MEASURED 2026-09-01 (19.8MB parsed in 0.095s) |
| headless and SDK invocation | the capture path for driving this harness as a worker | UNPROBED here (dispatch drives the other harnesses; this one unexercised today) |

## Codex CLI

| reliance | why | status |
|:--|:--|:--|
| reads AGENTS.md cwd-upward at session start | the only law it sees — repo skills are invisible to it | DOCTRINE, load-bearing (AGENTS.md records it) |
| dispatchable headless via the pinned launcher, model set at dispatch | the pipeline's implementer and reviewer seat | MEASURED 2026-09-01 (multiple runs) |
| self-stamp may diverge from the model that ran | evidence law: record what ran, quote what it stamps | MEASURED 2026-09-01 (stamped one name, ran another, then aligned) |
| envelope harvested into the record's out/ | reading verdicts without scraping stdout | MEASURED 2026-09-01 (consistent all day) |

## Grok CLI

| reliance | why | status |
|:--|:--|:--|
| claude-compat: reads CLAUDE.md and AGENTS.md, sees repo skills | law reach and test-author seat | DOCTRINE |
| dispatchable headless via the pinned launcher | test author, adversarial reviewer, hunter | MEASURED 2026-09-01 (multiple runs) |
| envelope harvest GAP: final message lands on stdout, out/ stays empty | exit 0 with empty out/ must read as a finding, never a pass | MEASURED TWICE 2026-09-01 — proposed register row demands the launcher-side fix |
| owner customizations exist and are not to be probed | owner's standing rule | DOCTRINE — this row records a deliberate non-probe |

## gh

| reliance | why | status |
|:--|:--|:--|
| pr list --json returns every requested field on every row (isCrossRepository included) | the census's row validation and the stub's fidelity | MEASURED 2026-09-01 (reviewer-driven; the stub now models it — the first partially contract-verified mock) |
| exit 1 means any failure whatsoever | absence is proved only by a silent empty run | MEASURED 2026-09-01 (drove the probes-contract fix) |
| a full page at the listing limit is an unmeasured total | the false-total law | DOCTRINE (two contracts carry it); probe UNPROBED |
| checks output, watch mode; graphql thread resolution; merge with merge commit | the review loop's plumbing | MEASURED 2026-09-01 (used end to end) |

## git

| reliance | why | status |
|:--|:--|:--|
| trailer parsing requires the blank-line-separated final block | the attribution law's machine readability | MEASURED 2026-09-01 (CI red; local hook and CI gate diverge — a two-contract state awaiting agreement) |
| update-ref with empty old-value is create-only | retire's idempotence semantics | MEASURED 2026-09-01 (the stuck-retry fix) |
| worktree add is a lock grab; one owner per worktree | multi-session safety | DOCTRINE, measured 2026-08-21 |
| merge-tree --write-tree for merged-floor checks | the merged-tree rule made cheap | MEASURED 2026-09-01 |

## The docket

The owner's words widen this beyond harnesses: "inventory of
everything is on the docket." Sibling inventories queued for the
same triangle treatment, each a registry with a probe: the levers
and their verbs; the stores and their schemas; installed hooks;
dispatch jobs; concern rows; contract-and-gate coverage (which
promises have which checkers — the coupling census). Each lands as
its own contract draft; this file is the pattern's first instance.


## Conduct adapter interface

Adoption unit 1b, contract-only. The dated inventory above remains historical
reliance evidence; it does not admit installed versions today. The
[startup descriptor](conductor-start-plan.md#trusted-conductor-launch-descriptor)
and [shared policy](lane-policy.md) supply selection and authority. Adapter
mechanics belong to the existing harness owner; configurable Claude, Codex and
Grok implementations share this interface and never duplicate policy decisions.
No installed option spelling is certified by the synthetic examples below.

`compile_launch(selection, grants, transport, forwarded_tokens, operation)`
returns exactly `{argv, environment, reservations, capability, binding}` or a
wire refusal. It is deterministic and performs no launch, write, model selection
or grant derivation. `selection` uses startup's LaunchSelection (worker calls
use their task-owned role/job declaration); `grants` is the verified policy/task result,
never scope prose; `transport` holds verified executable/configuration, adapter
manifest, canonical paths and, for resume only, the retained session binding.
`operation` is launch or resume and must agree with the binding.

- `argv`: exact nonempty string vector beginning with the admitted absolute
  executable. No shell evaluation, shell joining or reparsing by a shell.
- `environment`: closed name-to-string map compiled from trusted declarations;
  no inherited PATH/config/environment overlays. Secret authentication transport
  remains under existing custody and is never a forwarded option or logged value.
- `reservations`: existing C2 transport declarations, distinct from model output
  grants; admitted scratch alone supplies its TMPDIR, never a parent grant.
- `capability`: required tuple from
  [C2 freshness](../devlane/dispatch/CONTRACT.md#capability-evidence-and-freshness),
  extended by grammar, tool-normalizer and MCP binding digests for conduct.
- `binding`: exactly `{invariant_sha256,argv_sha256,operation}`; digests use the
  startup canonical encoding. argv_sha256 hashes the complete final vector.
  invariant_sha256 hashes `{selection,grants,adapter,forwarded_tokens,mcp}`
  from verified transport, with adapter equal to the complete startup descriptor
  adapter object, excluding only the operation-specific session segment.

On resume, compare invariant_sha256 to retained launch evidence and require a
fresh matching resume witness. Recompile the entire vector and verify exact
launcher-owned session segment against retained session identity. A resume
argv digest normally differs from launch; it must equal its independently
compiled expected resume vector. Repeated resume with identical inputs must
produce identical bytes. Record both digests and the operation, not a false
claim that launch and resume vectors are byte-identical. Relocation permitted
by C2 rebinds canonical output paths through its custody checks; it cannot widen
grants, change source anchors or bypass conductor stationary-binding checks.

## Safe argv grammar

Each measured executable/version/operation declares a closed grammar in the
existing adapter declaration, digest-bound to its configuration. A declaration
has exactly `{schema:"adapter-argv/1",options:[Option,...]}`. Each Option is
exactly `{name,forms,values,repeatable}`: name matches `--[a-z][a-z0-9-]*`;
forms is a nonempty unique subset of `flag,split,equals`; values is null for
flag-only or a nonempty unique array of literal safe values for split/equals;
repeatable is boolean. Flag cannot coexist with value forms. Values are
printable ASCII, 1..128 bytes, must not start with `-`, and must come from an
independently reviewed harmless finite vocabulary. Names are unique. Empty
options explicitly allows only an empty forwarded vector. No permissive regex,
free-text value, path, URL, arbitrary number, positional argument or opaque blob
is admitted by this initial grammar. Safe value-bearing options remain usable.

Parse the caller's already separated tokens once, left to right. Match only
exact declared names and forms. Consume exactly one following token for split
and exactly the suffix after the first equals for equals. Count repetitions by
option identity across forms; refuse a second occurrence unless repeatable.
Reject short aliases, bundles, abbreviations, missing/extra values, bare `--`,
response-file forms and any token not consumed by a rule. Maximum 64 tokens,
each at most 256 UTF-8 bytes, total at most 8192 bytes; reject NUL/control bytes.
These are argv token bytes after the invoking shell, not its source quotation.
Preserve every accepted token byte-for-byte, in original order and form, as one
contiguous segment at the adapter's measured insertion point; no equals/split
conversion, deduplication, escaping, truncation or silent dropping.

The manifest must never admit options affecting permissions/approval/sandbox,
write roots, settings/config files, environment/headers/credentials, hooks/tools,
MCP servers, executable/plugins, working directory, session/resume, role/profile,
model/effort or launcher transport/output. Their aliases, repetition, indirect
config/response-file forms and future equivalents are reserved too. They belong
only to trusted compilation. Manifest review and verified mock/live probes must
prove semantic classification against the exact installed syntax; a name-based
blacklist alone cannot establish safety. Unknown installation syntax means no
measured grammar and prevents capability admission. A token rejected by an
available closed grammar is a deterministic refusal, not UNREACHABLE.

Synthetic grammar and admissible vector (fixture executable only):

```json
{"schema":"adapter-argv/1","options":[{"name":"--display","forms":["split","equals"],"values":["compact","plain"],"repeatable":false},{"name":"--quiet","forms":["flag"],"values":null,"repeatable":false}]}
```

```json
["--display=compact","--quiet"]
```

`["--display","plain"]` also passes and keeps its two tokens. Repeated display
across forms, `--dis`, `-q`, `--display=SECRET`, `@file`, `--config=x`, or trailing
positional text refuses. These examples grant no real vendor option.

The documentary checker counts two document-present positive vectors and seven
document-described negative vectors (the repetition and positional descriptions
are materialized as concrete tokens by the checker). It additionally synthesizes
one positive empty vector and two negatives, bare `--` and missing display value.
Thus its three positive and nine negative cases include synthetic additions;
none is live vendor evidence. A successor receipt must supply the checker, label
these origins and bind the actual checked bytes and revision/tree with UTC.
The earlier unstamped receipt remains historical evidence, not a current check.

Reject before prompt assembly, record/attempt creation, subprocess invocation or
logging raw input. Never interpolate rejected option names/values, raw vendor
parse errors or a hash of rejected secret material into diagnostics. Return
`conduct-argv: expected declared safe tokens; found invalid token at index N;
needed use the measured adapter grammar or run the independent doctor`, where
N is the zero-based numeric index (0 for a vector-level violation). Record only
admitted non-secret tokens and effective-vector digests. The credential-canary
acceptance scope is this input pipeline: prompt, stderr/stdout, diagnostics,
state/records and Git objects; it does not promise censorship of arbitrary
future model prose. Controlled environment credentials are never serialized.

## Capability admission and independent cases

Shared policy calls the normalized request interface in
[permission-surface.md](permission-surface.md#normalized-conductor-tool-interface).
An adapter must expose every effective tool surface before effects and translate
the shared decision without asking the user to approve it. Missing hooks,
malformed decisions, a tool bypass or unavailable deterministic interception
makes that harness/operation unavailable for conduct. Settings, CLI help and
synthetic argv alone are insufficient. Launch and resume need separate positive,
negative and prompt-observation arms in the
[live extension](probes-lever.md#conduct-live-diagnostics).

C2's G=D and actual A=D, source isolation, transport separation, freshness,
configured age limit and verified mock requirements remain unchanged. Conductor
restriction evidence additionally binds policy evaluator, normalizer, safe
grammar, effective configuration and actual MCP connection. Changed tuple means
stale evidence; missing measurement means unresolved; an observed failed control
is failed evidence. All prevent admission with an owning capability refusal;
UNREACHABLE describes a probe's inability to measure, not permission to run.

### Conduct capability identity encoding

For independent producers, `capability` above and a non-null live report
`identity` use this exact closed object. This encodes C2's required tuple for
conduct; it does not change worker output declarations or their admission.
All keys are required. SHA256, ID, LaunchSelection and digest encoding come from
startup. Text fields are nonempty printable ASCII, at most 128 bytes. Paths
are verified absolute paths from trusted transport, not caller locators.

| field | closed shape |
|---|---|
| harness | `{name:ID,version:text,executable:{path:absolute-path,sha256:SHA256}}` |
| adapter | `{revision:SHA256,mechanism:text,config_sha256:SHA256,grammar_sha256:SHA256,normalizer_sha256:SHA256}` |
| platform | `{os:text,sandbox:{name:text,version:text},filesystem:{name:text,alias_semantics:text}}` |
| role | literal conductor for this interface; no inherited worker grants |
| operation | launch or resume, equal to the report/compile input |
| policy | `{blob_sha256:SHA256,evaluator_sha256:SHA256}`; current admitted policy and evaluator closure |
| scope | `{source_reads:[absolute-path,...],source_write_denials:[absolute-path,...],outputs:[Output,...],limits:[text,...]}` |
| contracts | `{outputs_sha256:SHA256,tools_sha256:SHA256}`; exact owning contract bytes |
| mcp | the startup descriptor's exact mcp object |
| probe | `{revision:SHA256}` |
| mock | `{revision:SHA256,verification:{path:relative-file,sha256:SHA256}}`; retained passing verification under existing evidence custody |

Output is exactly `{kind:"file"|"scratch",path:absolute-path,operations:[Op,...]}`;
Op is `create,write,delete,rename,chmod`. The operations list is nonempty,
unique and sorted, and records the exact admitted operations, never implied
parent rights. File means one leaf; scratch means that subtree. All path/text
lists are sorted unique; outputs sort by path with unique paths. Empty lists
are explicit empty grants/limits, not unavailable analysis. The witness must
prove complete source coverage and the C2 grant equality; matching a list alone
is not containment proof. Unavailable tuple members make identity null with an
unreachable reason, never an invented version such as unknown. A measured
absence of OS sandbox may be described explicitly as name/version none, but
cannot satisfy a required OS boundary.

The live report supplies checked_at, results and digest-backed evidence;
retained mock verification must independently pass. Compare the current tuple
and scope before each operation under C2 freshness. Every descriptor adapter
field must equal the following capability identity field byte-for-byte:

| descriptor field | capability identity field |
|---|---|
| adapter.revision | adapter.revision |
| adapter.mechanism | adapter.mechanism |
| adapter.grammar_sha256 | adapter.grammar_sha256 |
| adapter.normalizer_sha256 | adapter.normalizer_sha256 |
| adapter.executable_sha256 | harness.executable.sha256 |
| adapter.config_sha256 | adapter.config_sha256 |

This covers both closed adapter objects completely, including the executable
digest at its harness-owned location. The descriptor digest bound by each tool
Request and the compiled invariant digest therefore cover the pinned normalizer
and mechanism as well as grammar, executable and configuration. A changed field
requires renewed matching evidence before launch/resume. The descriptor's policy
binding must resolve to this policy. Probe/mock changes invalidate the edge;
this encoding is not an extra capability registry or an authority record.

Independent test_adapter_argv.py/test_output_capability.py cases cover every
form, byte/order preservation, duplicates across forms, reserved aliases and
indirect channels, invalid manifests, secret canary non-persistence, all selection
axes, unsupported combinations, changed bindings and legitimate operation-owned
resume tokens. Existing dispatch admission cases remain the C1/C2 owners' duty.
Live observation format and diagnostic bootstrap are owned by probes-lever.md;
this contract does not create another witness authority or implementation.
