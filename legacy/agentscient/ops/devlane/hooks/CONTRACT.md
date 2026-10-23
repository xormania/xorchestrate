# Repository diagnostics

Owner direction, 2026-09-24: freedom to work; diagnostics warn and guide.
CI's `lane` result is the routine acceptance gate. Hooks do not deny tool calls,
block session completion, require grants, or automatically classify findings
as skill misses. A diagnostic failure produces a warning and work continues.
Native harness permissions and worker isolation remain owned by their runtimes.
A hook diagnostic cannot supply owner approval or override a harness permission.

The shared entry and the standalone Claude entries emit additional context
(or a Stop system message). They preserve useful condition detection without
issuing permission decisions. Grant checks, mandatory dispatcher identity, and
mandatory pipeline routing have been removed. The historical hand ledger is
read-only and is neither an onboarding prerequisite nor recovery authority.

The owner's recovery path is GitHub administration/bypass access, outside this
repository machinery. There is no agent-callable bypass flag or grant service.
Its availability and account separation need verification in GitHub settings.

Existing issued workers keep their pinned runtime. Newly prepared sessions/jobs
load these diagnostics. Runtime pin checks and credential custody still validate
the artifacts they load; they do not prescribe an interactive work sequence.

## Native translation

Claude, Codex and Grok hook envelopes normalize into tool_name/tool_input/cwd,
with unambiguous alias handling. Codex already supplies Bash/command for shell;
apply_patch carries patch text in command. A patch must have complete delimiters
and every add/update/delete/move target must be enumerated; malformed or unknown
patch forms refuse. Direct and multi-file edit tools normalize paths and content
without dropping any target. Agy supplies toolCall.name/args, conversationId,
workspacePaths and stepIdx; hook-process cwd is not workspace authority.

Native delegation routes remain available and need no repository permission.
Known MCP operations use the existing server operation declarations; domain
mutation authority remains enforced inside those operations. Unknown MCP mutation
shapes are unresolved, never assumed reads. Continuation input is not a fresh
shell command: a route without verified interception is disabled/refused at
launch. Installed Codex 0.155.0 ignores unified_exec=false. The guarded profile sets
unified_exec_tty=false: native TTY creation refuses and non-TTY write_stdin
refuses input because stdin is closed. Polling remains available; there is no
fictional second hook. This behavior requires its pinned native witness. Agy manage_task send_input has a native hook but
requires a declared safe continuation binding; polling/kill remain separate.

Post-tool effects cannot be undone by a hook. Missing output/final-message data
is explicitly unavailable; it is not an empty successful reading. Unsupported
lifecycle coverage prevents a claim of full lifecycle enforcement. Native event
coverage and policy conformance are separate measurements.

## Profiles and startup

`profiles.py` derives native configuration from this source for all four harnesses.
Generated configuration and digest bindings are replaceable projections, never
another role/grant/state store. The source closure, registry, profile and helper
are pinned. A missing or changed binding refuses before launching; an unavailable
hook emits a diagnostic without importing the changed helper. The guarded-launch route protects its
policy/profile files from the child; permissions/sandbox/agent/config overrides
cannot silently remove hooks. Diagnostics inspect bindings without launching.

Interactive preparation generates a profile and initializes/registers the native
terminal through the existing coordination tools. It neither creates nor requires
a dispatch job. Dispatched installation extends the existing minimal-home seam,
preserves credential/session custody and uses its existing assignment identity.
Both call the same evaluator. Resume revalidates source/configuration/identity;
no fallback to operator home, old profiles or an unguarded launch is permitted.
No owner home/project setting is activated by source installation or testing.

Participant authentication is independent of the public native session id.
Each generated profile owns a random 256-bit `coordination_capability`, encoded
as 64 lowercase hex characters in its private, mode-0600 binding. The existing
coordination core stores only its SHA256 digest on the participant. Missing or
malformed capability projections refuse; no legacy profile gains a replacement
secret merely by resuming. Resume and relocation retain the original capability.
Native launch discards inherited `COORDINATION_CAPABILITY` and carries only its
own binding value; interactive hook initialization likewise sets that value
before entering the core. Preserved MCP registrations receive the validated
profile path, never an inherited raw capability. The binding retains the same
normalized settings used for native configuration, so its child-readable copy
also excludes inherited credentials and session aliases without changing the
caller's settings. Helpers and public configs,
records and responses contain no raw capability. Interactive registration and
dispatcher-issued worker initialization remain separate callers of the core.

Codex configuration arguments have one admitted grammar across split `-c` /
`--config`, joined `-cKEY=VALUE`, and equals forms. The key is an unquoted
dotted identifier and the value is one TOML value. Missing values, quoted or
ambiguous keys, extra assignments and unknown keys refuse before launch.
Only the existing launcher projections for reasoning effort, output custody
and MCP registration may use this transport; hook and feature overrides refuse.

Interactive native arguments use a finite per-client grammar measured from the
installed help. Model, effort, output, prompt and same-session resume options
are admitted; unknown options, option clusters and missing values refuse.
Configuration, workspace, sandbox, permission, agent, plugin and remote-client
selectors are not interactive overlays. Supply a positional prompt as one
argument after native `--`; Codex's only admitted subcommand is `resume`.
This admission runs before profile flags are inserted. Worker profiles instead
consume the existing trusted dispatcher's argv, including its sandbox, issued
identity, output paths and bound MCP registration. An interactive binding
cannot select that worker route.

Credential custody is part of the same source closure: the current isolation
owner (`ops/devlane/harness/isolation.py`) has only standard-library runtime
imports on the build-home/carry-back path. Its source is pinned and protected
alongside the evaluator. Load that owner from validated source bytes before the
child starts, without trusting a mutable bytecode cache. After child exit,
revalidate the complete binding before any credential carry-back effect; a
changed or missing source/configuration refuses and leaves renewed credentials
in the native home. Error reporting never imports an unvalidated custody owner.
The worker attempt carries its already-admitted profile and binding into the
same post-child check. Missing, invalid, changed or mismatched custody context
produces the existing `credential: [{"unresolved": ...}]` reading with no host
credential effect; it does not replace the child's exit status. Resume and
relocation use the current validated worker binding and its native home.
Both callers capture directory identities in parent memory before each child:
the native home and parents of the isolation owner's declared credential files
must retain their device/inode identity and remain directories before carry-back.
Copying the pinned config bytes into a replacement home does not restore custody.
Credential files may still be atomically renewed. This capture is per attempt,
not durable profile state: a relocated resume validates its projection and then
captures its current directories before the resumed child. A failed check keeps
the original and substituted artifacts intact and makes no host credential write.

## Verification and activation

The existing hooks, harness, dispatch and conductor conformance families own
planted-fault/clean-neighbor cases, including a malformed/missing guard, hard-row
grant-free writes, every patch target, delegation, MCP, continuation and profile
tampering. Fixtures use ops.testkit and synthetic stores only. Native local
protocol fixtures must observe both tool results and filesystem effects: harness
exit 0 is not evidence that a tool ran or that a denied call was suppressed.
Binary/version/config digests stamp the witness; documentation or tool discovery
alone is not interception proof. Terminal and worker startup/resume need distinct
witnesses. Unmeasured paths stay unadmitted. Root independently verifies the
concrete candidate before any owner settings or terminals are activated.

Historical implementation provenance: the original change used WALL grant 310;
a separate test producer is absent by the owner's explicit instruction.

## Current measured transport and limits

Native deterministic fixtures on 2026-09-18 used Claude 2.1.276, Codex 0.155.0,
Grok 1.0.30 and agy 1.2.5 with synthetic local protocol responses in a network
namespace. Both worker and interactive profile modes preserved reads and own
hand work and denied shell code writes, direct edits/patches and trunk commits.
These headless fixtures exercise interactive identity registration, not an
attended TUI or a provider/model call. Operational readings are in the existing
coordination SQLite messages, including M-245; fixture apparatus is retained in
scratch for independent reproduction. A binary digest in a generated profile
is an integrity pin, not proof of support for an arbitrary new binary.

Grok publishes equivalent event aliases in different spellings (PreToolUse and
pre_tool_use); these normalize before conflict checking. Its final response is
lastAssistantMessage. AGY Stop reads the final completed PLANNER_RESPONSE from
its native transcript, inside its bound session home. Its immediate PostToolUse
has no completed tool output. The current registry contains no engine post-tool
row: a future post-tool requirement needs a verified transport reading and may
not silently inherit coverage from configuration alone. Post-tool observations
cannot undo effects; Stop has the existing engine's declared three-block cap.

The launcher adds read-only mounts for the trusted policy closure and generated
hook files while retaining the existing harness sandbox/isolation. It is not a
new filesystem-permission policy or a separate user/SQLite security boundary.
The existing shell predicates remain shape-based checks; this change does not
claim complete semantic analysis of arbitrary programs. Unknown native tools,
MCP bindings and continuation input refuse. Relevant MCP catalogues and core
authority are reused; native interception of each enabled client/server/tool
combination requires its own witness before activation. Never infer this from
tool discovery alone.

Interactive usage is prepare-interactive with harness, destination, cwd,
executable and agent, then run-interactive --binding PATH -- NATIVE_ARGS.
Preparation writes only a new requested projection directory. Launch validates
that projection, pins and reserved native settings before exec. Worker profile
installation is the existing isolation seam; AGY receives a separate issued
binding UUID while its native conversation ID remains unknown until observed.
Relocation reprojects only after verifying the retained helper/config bytes and
unchanged policy closure; it preserves authority stores and issued identity.

The Codex adapter fixes shell command origin to the verified profile cwd with
native updatedInput, grouping the original command after a successful quoted
cd. It evaluates the original command at that verified cwd before emitting the
transport prefix, preserving environment assignments and explicit directory
changes for the existing checks. Native hidden workdir cannot select another checkout.

Interactive hook registration associates the native session with its derived
profile through the participant's private capability digest. An authenticated
resume preserves an existing native messaging endpoint; initialization fills
only an absent endpoint with the profile reference. The shared instrument
resolves that association for subsequent CLI/MCP callers, including AGY which
does not export a native session variable. A profile admits one native terminal
session (including resume); another session requires another projection. Missing,
ambiguous or conflicting associations refuse. The validated projection supplies
the original authority HOME/state root to the instrument; a minimal harness
home is never a new authority store. Worker bindings retain their issued session.
Native inherited aliases are cleared at launch. Invalid explicit profiles refuse
instead of falling back to those aliases. Ordinary unprofiled session reads keep
their existing absent-session behavior. Guard failure at Stop terminates clients
with a verified continue:false channel; it never requests an unbounded retry.
AGY 1.2.5 has a different native channel: unavailable-guard Stop returns continue,
which the installed client caps at ten continuations (root M-257: eleven final
responses, exit 0, no timeout or additional tool effect in the deterministic
fixture). This is bounded retry, not immediate termination or successful policy
evaluation. PreToolUse remains denied throughout an unavailable guard.

Interactive preparation reuses isolation.build_home's declared credential leaves;
the foreground launcher carries renewed credentials back with isolation.carry_back.
Existing native MCP registrations are projected from Codex/Grok config.toml,
Claude account/project MCP definitions, and AGY's installed documented global
`.gemini/config/mcp_config.json`. Their server definitions remain intact, with
explicit shared-profile environment propagation and stale session aliases removed.
Codex model/provider preferences are retained; old hooks/features are not copied.
The original settings are read only. Unknown MCP operations still produce diagnostics at the
shared entry; projection alone does not prove native tool interception.

Example (from the reviewed stable installation, after root permits activation):

```sh
python3 ops/devlane/hooks/profiles.py prepare-interactive --harness codex --destination /absolute/new-profile --cwd /absolute/session-worktree --executable /absolute/installed/codex --agent 'Actual Model <noreply@vendor>'
python3 ops/devlane/hooks/profiles.py check --binding /absolute/new-profile/profile/binding.json
python3 ops/devlane/hooks/profiles.py run-interactive --binding /absolute/new-profile/profile/binding.json --
```

Use that same binding with the harness's native resume arguments to resume its
registered session. A new session needs a fresh projection. Worker startup/resume
continues through dispatch and isolation, without this interactive command.
