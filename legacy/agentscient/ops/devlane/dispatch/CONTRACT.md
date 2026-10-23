# The dispatch app — the dev-lane launcher

## Configured workers and agy installation

Standing: worker implementation candidate, owner brief of 2026-09-08.
The configured-worker mechanism enables agy; independent combined review
remains outstanding. Bounded native discovery evidence is recorded below.
GPT-6 correction follows independent Grok ruling
`20260908T065000Z-adjudicate-grok-c2ae09` on `fc9ad2c`: F1–F4 UPHELD,
F5/F6/F8 PARTIAL, F7 REJECTED. Later wire evidence
`20260908T064029Z-plan-codex-161211` is separate and did not seed that ruling.
The original INVALID delivery remains preserved as recorded.
The owner requests installed, dispatchable **agy workers** and has already
selected Flash 3.8 uses. The integration inventory's role calibration,
eight-PR rollout and qualification-only/candidate-admission protocol are
superseded. Its binary/help evidence and both inventories' source maps
remain dated evidence. Candidate code and independent fake-CLI tests may
be developed on the owner-created branch and exercised through the existing
dispatch/probe path under the trusted-maintainer host boundary. No second
executor, task.run vendor extension, conduct adapter, Symfony dependency,
global settings change or broad C1/C2 adoption is required by this amendment.
Existing custody, job restrictions and independent producers still bind.

Source homes re-read on 2026-09-08 at
`1ba670842ea287f369a76c53a3e7455bf4ecf3d6`: `laneconf.py` parses literal
configuration; the worker wrapper hardcodes three names; `launch.py`
branches for their invocation, identity and supervision; `isolation.py`
has three entries; usage/pulse have three readers. Reproduce with
`rg -n 'def worker|def values|case \$harness|def _argv|def _stream|def _run_attempt|def .*_sessions|HARNESSES' ops/conductor/tools/laneconf.py ops/conductor/tools/dispatch.sh ops/devlane/dispatch/launch.py ops/devlane/harness/isolation.py ops/devlane/telemetry/usage.py ops/devlane/telemetry/pulse.py`.
The supplied integration inventory measured agy 1.1.27 and help syntax;
its `models` failure with HOME removed says nothing about model support.
No authenticated behavior or agy format is established by those readings.

### Small configuration interface

[laneconf.py's module contract](../../conductor/tools/laneconf.py) owns
the exact keys, validation, provenance and `worker(name)`/`worker NAME`
projection. `WORKER_HARNESSES` selects implemented worker names;
`EXECUTABLE_<NAME>` optionally names one absolute executable file. Omitted
keys preserve the three legacy names and PATH resolution. An explicit
empty worker list disables all workers; an empty executable refuses.
These keys never supply flags, permissions, provider, model/role mappings
or capability evidence. Remove agy from the list to disable new agy
launches/resumes without changing the other adapters.

The [existing wrapper](../../conductor/tools/dispatch.sh) checks selection
through that loader, then calls `launch.main(argv=None) -> int` through its
existing CLI. Direct launch checks the same selection before minting state.
No executable flag or alternate loader is introduced. Preserve original
configuration sources when forwarding; a file value must not be relabelled
as an operator environment override. The wrapper's `saved_keys` save/restore
must include `WORKER_HARNESSES` and `EXECUTABLE_*` before sourcing either
file, or keep these keys out of the sourced process environment. Pin exported
operator values versus file values in `test_dispatch_wrapper.py`.
Launch records the returned worker object as `harness.worker` and uses its `executable` as the single argv[0],
with shell execution disabled; paths containing spaces remain one token.
Pass `worker.executable` into `_argv` for argv[0] separately from the harness
name used for adapter branching; do not rebind that name. Without an override,
claude/codex/grok retain `shutil.which(name) or name`. Absolute realpath and
PATH-miss refusal apply to overrides and agy, preserving the three defaults.
Configuration membership alone cannot implement an absent adapter.

Selection stays explicit `--harness`; go's existing intent defaults and
HARNESS override stay unchanged. Nonempty explicit `--model` wins over
`MODEL_<NAME>_<JOB>`, then the wrapper's generic `MODEL_<NAME>` fallback
after the job-key rung (empty/unset is safe under `set -u`). JOB uppercases
and replaces `-` with `_`. Preserve Claude's special final fallback to
`MODEL_CLAUDE_PLAN` when stage is plan, otherwise `MODEL_CLAUDE_READ`. Effort likewise uses nonempty
`--effort`, `EFFORT_<NAME>_<JOB>`, then `EFFORT_<NAME>`; absent/empty effort
means no effort argv: the wrapper's final indirect expansion must be
`${!effort_harness:-}`. Each configuration key retains environment over local
over tracked precedence. The `agy` worker gets no model
default in code: optional MODEL_AGY/EFFORT_AGY are operator choices in
these existing families, not a roster. Direct launch still requires
`--model`; no supported effort may be silently dropped or translated to
another level. Existing Claude write refusal, Codex read/check-tests
exception, Grok policy permissions and all three fresh/resume argv persist.

### Adapter interfaces and evidence

All rows extend existing owners; no plugin registry or generic adapter
framework is needed. These are implementation obligations, not live passes.

| owner / seam | agy input, output and acceptance obligation |
|:--|:--|
| `harness/isolation.py`: `HARNESSES`, `build_home(harness, root, env=None)`, `dispatch_env(harness, home=None, env=None)`, `isolated(harness, root, env=None)` | Use the exact isolation data and refusal rules in its module contract. Return the existing home Path, environment dict and `(env, flags)` respectively. No auth filename, discovery variable or store path may be guessed from the name agy. |
| `launch.py`: `_harness_meta(name, role, job_dir, env)` | For agy, record `harness.containment='policy'` under the trusted-host boundary. The sandbox label is `always-proceed`; argv uses the measured `--dangerously-skip-permissions`, with policy containment. `isolation.store` follows measured `sessions.{under,path}`; until available, refuse `unsupported-capability` before minting a store, never use the Claude `$HOME/.claude` else-branch. |
| `launch.py`: `_effort_argv(harness, effort)`, `_argv(...)`, `_child_env(...)` | Preserve existing callers; add the resolved executable input to `_argv` separately from its harness-name discriminator. The measured transport is `[executable, *isolation_flags, '--print=', '--model', model, '--input-format', 'stream-json', '--output-format', 'stream-json', '--disable-slash-commands', '--dangerously-skip-permissions', '--add-dir', snapshot]`, plus `['--effort', effort]` only for low/medium/high. Feed exactly one UTF-8 NDJSON `{"event":"user","message":{"content":prompt_text}}` line through stdin and close it. Preserve prompt Unicode/newlines as JSON string content; no prompt on argv. The bare `--print` consumes the following flag; text stdin with empty print is not a prompt; `--mode plan` is ineffective with slash commands disabled. Keep the canonical schema in the brief and preserve the existing result parser. No vendor schema flag is admitted. |
| `launch.py`: `_envelope(raw, job, ref_sha, note=None, *, harness=None, job_dir=None, rec=None)` | Preserve raw stdout/stderr; retain each harness's response extraction. The agy branch must reject missing, ambiguous, truncated or malformed terminal results and vendor errors even beside a plausible envelope. Every extracted result, including legacy raw output, passes through `task/envelope.py:validate`, which owns schema and semantic acceptance: counts, invalid/tripped verdicts, handles and optional commit. JSON member order does not change acceptance. A selected semantic failure cannot fall back to an earlier clean result. Return invalid with a null verdict and a cause on failure, retaining the reported findings and counts. Vendor schema acceptance or exit 0 alone never certifies work. Do not adopt generic C2 output grants here. |
| `launch.py`: `_stream(job_dir, harness, session, snapshot, ref_sha, role='read')`, `_resume`, `_close` | Preserve `(path, ran, stream_id, mismatch)`; missing observations are None, mismatch is a reason string. Fresh launch must obtain a unique native session id, bind it to this job/attempt and snapshot, and discover only its measured isolated store. Never pick the newest unrelated stream. Check explicit conversation identity and cwd/ref evidence, two concurrent sessions, foreign/malformed streams and relocation. `--conversation <recorded-id>` succeeded in the host observation; implicit `--continue` is not used. Resume uses the recorded id only, rechecks isolation/permissions and the recorded executable against current worker selection, preserves model/effort/lineage and prior findings, and refuses changed selection or an unavailable store before attempt/marker mutation. No implicit latest-session continuation; unsupported resume blocks resume alone. |
| `launch.py` / `record.py`: model and attribution | Retain `model.requested`, `model.ran`, `model.read_from`; add agy-only `model.provider = {id, read_from, unresolved}` with nonempty strings for measured id/source and null unresolved, or null id/source and a nonempty unresolved reason. Source is launcher-read native session evidence, never worker prose, harness name or a model prefix. A requested alias may differ from ran; record both. Foreign session or contradictory native identity invalidates the result. Missing model/provider remains explicit and prevents attributed write settlement, not an unrelated read operation. Provider evidence selects a verified vendor address at commit time; no fallback to requested identity or guessed address for agy. Old record fields/validation and legacy attribution remain compatible. |
| `telemetry/usage.py`: `agy_sessions(root: Path, repo: str or None)`; launch `_spend` and pulse reader | Add the existing reader-shaped iterator over the measured isolated root; each row has `harness='agy'`, `session`, `model`, `started`, `ended`, `messages`, `tokens`, `incomplete`, `note`. Nullable observations need a reason in note; tokens is null or `{input, cached, output, total}` with nonnegative integer or null values. Match session and repo before counting. Prove native event selectors, delta/cumulative/reset rules, duplicate identity, cache/thinking overlap and resumed totals with known synthetic totals. A total is known only when its currency is measured and complete; never add an overlapping cache/thinking count. Missing/malformed/truncated evidence stays incomplete/unresolved, not zero. `_spend` returns measured fields/source or its existing `{unresolved, looked_in}`; pulse consumes the same currency without exposing transcript content. No guessed price or cost-unit conversion. |
| `launch.py`: `_run_attempt`, status/wait/close; existing watchdog | Retain inline wall, output-cap and missing-stream grace enforcement, process-group kill, raw/partial evidence, state.json PID/PGID, exit marker and attempts on fresh/resume/detached paths. Normalize agy output usage into that live cap check; a parser used only at close or in breaker.py cannot provide live protection. Incomplete accounting must not disable a known output cap; unavailable live output evidence blocks the affected capped operation. Preserve DISPATCH_TIMEOUT > job timeout > WALL_DEFAULT and existing cap sources. Do not enable disabled total-token wires or claim the unwired richer breaker battery. |

Native observations bind this candidate to agy 1.1.27, binary SHA-256
`93eb2118b778a4005700b54cdd7e08b896fbe665d5ff338e38e9e53da9a091ea`:
`20260908T071244Z-code-grok-220fb8` establishes auth/session paths and
cumulative explicit-ID resume usage; `20260908T073056Z-code-grok-0e4417`
establishes the stdin event and CANCELED; `20260908T074530Z-code-grok-5395b3`
establishes permissions, fixture-bound init.cwd and a real view_file read.
The exact auth leaf is `HOME/.gemini/antigravity-cli/antigravity-oauth-token`;
transcripts are `HOME/.gemini/antigravity-cli/brain/<conversation_id>/.system_generated/logs/transcript.jsonl`.
The adapter reproduces the combined redirected HOME, XDG directories and
TMPDIR, with auto-update disabled. HOME-only sufficiency, native discovery
coverage and provider identity remain unresolved. The original INVALID
isolation record and independent source/tests retain their provenance.

`raw.out` is the native evidence stream for init.model/cwd and result.usage;
brain transcript rows do not supply those fields. Each attempt records its
raw byte offset so a resumed result is extracted from that attempt alone.
Both crash-recovery close paths use that offset. Missing offsets in legacy
state mean zero; malformed state or a noninteger, negative or out-of-range
offset refuses with a controlled diagnostic, never an uncaught parse error.
Transcript validation cannot discard an established stdout session/cwd binding.
Ignore trailing blank lines and an unfinished final JSON row without a newline,
including a partial UTF-8 character. Complete rows still require valid JSON and
the supported source/type/payload shape; malformed complete evidence or a failed
transcript read supplies a refusing mismatch while retaining the stdout binding.
On same-session resume/close, an omitted native model preserves historical
`model.ran` and `model.read_from`. Record the current reading separately as
agy-only `model.current_attempt = {ran, read_from, unresolved}`: measured model
and source with null unresolved, or null model/source with an explicit current
attempt unresolved reason. A later measured model replaces that historical
measurement and clears the current unresolved reason; requested identity is
never substituted for missing native evidence.
Only terminal `result.status == 'SUCCESS'` certifies native completion;
every other status (including unknown nonempty values) is refused even beside
a valid envelope. SUCCESS still requires the carrier and canonical envelope
checks and no explicit result error; step/tool denials alone are not terminal
failure.

The owned canonical smoke `20260908T083944Z-review-agy-e49b81`, measured
against `408d2eff029c4b4dad12354c3d872efd9509832b`, establishes a native
`MODEL` / `PLANNER_RESPONSE` transcript row with string `thinking` and list
`tool_calls`, without `content`. The reader accepts that payload alongside
the existing content-bearing rows; source/type and a supported payload remain
required. Transcript text, thinking and tool calls never certify completion
or supply usage. The original failed receipt remains failed; this observation
does not establish a successful live rerun.

The supplied failed smoke stdout and successful canonical stdout
`20260908T085213Z-review-agy-0ed3b1` were read at base
`9eaee120fa69aeafb7b76dc742a9ce5f51c6f104` on 2026-09-08. They establish
`step_update.state == 'DONE'`, `step_type == 'agent_response'`, and
`usage.output_tokens` as live output evidence, bound by conversation_id and
step_index. The failed capture has 1585 output tokens; the successful one
has 1314 and 5136, whose sum is its terminal output counter, 6450.
For live caps, sum distinct completed response steps within the current
attempt; identical repeats count once and conflicting repeats refuse.
Other step states/types supply no output currency. A terminal counter
replaces the step sum, never adds to it or to a prior resumed total.
Step replay/reset semantics across resumes remain unmeasured: reset the
step accumulator at each init/attempt offset and never synthesize lifetime
spend from it. Live step output is separate from terminal accounting;
missing terminal totals remain unresolved even when live output is known.
Cache/thinking overlap and incomplete live frames do not add tokens.
Incomplete or unrecognized DONE response usage (including an unusable
step index) makes live output unavailable for that attempt, never a safe
prefix sum. This uncertainty is not a stream mismatch or terminal result
failure and does not invalidate complete terminal accounting. A complete
terminal reading may replace it; contradictory repeated counters still
refuse. Existing output caps and missing-usage grace remain unchanged.

The reader checks the conversation ID and snapshot cwd, never newest-file
selection. Resume requires the exact transcript in the live or archived home
before clearing markers, and retains the recorded configured executable.
The last native cumulative result wins; decreases mark accounting incomplete
instead of guessing a reset. Cache/thinking are never added to native total.
Missing native timestamps/model/provider stay explicit. Usage and pulse accept
`--agy-dir` as an explicit dispatch capture root (or its isolated home/agy).
The watchdog uses this same currency; missing usage is allowed only for the
existing stream grace, then stops the affected capped operation. No total-token
or price wire is introduced. Read-role settlement guards remain authoritative.

The existing probe accepts explicit agy `--model`, `--phrase` and `--cwd`
inputs for a host job's planted synthetic discovery controls. It resolves
`laneconf.worker('agy').executable` once from the probe process configuration,
exactly as dispatch does, before building a credential home. The optional
Python `env` argument supplies the control arms' environment, not a separate
worker configuration. Both arms execute that resolved path; result details
stamp the selected executable and its observed SHA-256, with the existing
post-probe digest comparison. Loader refusals become INVALID with their
expected–found–needed detail. Trusted operator overrides and fake CLIs remain
valid selections. A digest is observation, never a binary allowlist or new
launch/resume admission gate. Claude/codex/grok retain their existing PATH
behavior. This is the upheld slice of cold ruling
`20260908T083902Z-adjudicate-grok-b6cf99` at
`a7611ba44a68655d13f0273606193483df358741`; its broader gate was rejected.
Both arms
must initialize and answer; CANCELED, failed initialization and an invisible
positive control are INVALID. A CLEAN result covers only the named canary;
other discovery remains unresolved. No live probe was run in the Codex sandbox.

Native discovery receipt, 2026-09-08: supplied
`agy-discovery-live-result.json` (SHA-256
`98bcbda5fc42ebe3e395f3d07916f3daf9a6717b0dc1924f14977d7b24819aea`),
run `20260908T082720Z-code-grok-dd5294`, measured against
`2e4583256eeb624be0c9a5468d269cd54a395bdc`, reports agy 1.1.27 and
the `93eb2118…` binary above, with explicit `gemini-3.8-flash-high`.
The visible and isolated commands and receipt validation each exited 0.
Both arms initialized and answered; native init.cwd matched the disposable
git fixture. The visible arm returned both global and project markers; the
isolated arm returned the project marker without the global marker. Markers
were absent from stdin, and neither arm used tools. The CLEAN observation
covers only the combined HOME/XDG_CONFIG_HOME/XDG_CACHE_HOME/XDG_DATA_HOME/
XDG_STATE_HOME/XDG_RUNTIME_DIR/TMPDIR redirection with
`AGY_CLI_DISABLE_AUTO_UPDATE=true`, `--disable-slash-commands` and
`--dangerously-skip-permissions`; containment remains policy.
At that receipt's revision, `isolated()` supplied HOME only and the observer
added XDG/TMPDIR redirection. It did not measure HOME-only sufficiency.
Settings.json discovery/exclusion, MCP config discovery/exclusion,
project AGENTS.md and skill suppression remain unresolved. The receipt
reports removal of the copied 0600 auth leaf and unchanged operator auth
and named settings receipts. This is supplied dated evidence, not a new
native run or exhaustive isolation certification. Reproduce the receipt
reading with `cat ../in/agy-discovery-live-result.json` in the supplied
snapshot; verify its identity with `sha256sum ../in/agy-discovery-live-result.json`.

Independent tests can
already pin the interfaces above using synthetic environments and fake CLIs;
they must not treat guessed vendor JSON as verified real behavior.
Missing authentication/isolation or fresh prompt/result/session supervision
prevents a real agy launch; missing resume prevents only resume; missing
provider prevents attributed writes; missing cost prevents cost reporting.
These are narrow functional dependencies, not a new rollout state machine.

Use bounded functional probes through the existing dispatch/probe authority
on a disposable trusted fixture, with explicit model/effort and wall/output
limits. Record executable hash/version, adapter/probe/mock revisions,
platform, operation/role, UTC time and evidence handle. Isolation has visible
and isolated synthetic discovery-canary arms; both must initialize and
answer. A label such as plan/sandbox, a model declining a write, or a clean
final diff does not prove prevention. Preserve the trusted-host boundary;
configuration isolation is not OS containment and no universal container
mandate is added. Live probes and independent reviews are later work, not
performed or approved by this contract producer.

### Refusal projection and bounded verification

Loader errors use `ConfigError(kind, expected, found, needed)`; its CLI
uses the existing exit 2 wire. Wrapper/launch project the detail through
their existing dispatch refusal exit 3. New worker/executable errors are
non-overridable and occur before snapshot/home/prompt/record/child creation.
Use `worker` for malformed, disabled, unknown or unimplemented names;
`executable` for empty/relative/missing/non-executable overrides or failed
PATH resolution for agy (the three defaults retain their fallback);
`model` for absent model/unsupported effort; existing
`isolation`, `write-role-unadmitted` and `resume-store` for those boundaries.
Unavailable measured transport/permission/supervision uses
`unsupported-capability`, naming the requested operation, missing evidence
and bounded probe needed. Resume selection drift uses `worker` or
`executable` and names the recorded versus current value and a fresh launch
as remedy. After child effects retain the existing invalid/tripped result,
null verdict and named cause; no pre-effect rejection or success is invented.

The next producer independently authors focused tests, GPT-6 implements,
and independent reviews are PR comments. Existing regression homes are
`ops/conductor/tests/test_laneconf.py`, `test_dispatch_wrapper.py`, dispatch
`test_launch_refusals.py`, `test_launch_isolation.py`, `test_launch_harness_fixes.py`,
`test_launch_record_identity.py`, `test_launch_watch.py`, harness
`test_isolation_contract.py`, and telemetry `test_usage.py`. Synthetic agy
cases belong beside those tests. This documentation artifact runs only
documentation and existing focused contract/schema checks; no future-test
command, full floor, credential access or live inference is its receipt.

## C1 — execution authority (target, not runtime completion)

Standing: docs-only C1-contract, Grok ruling `518ab8`
(`20260906T021502Z-adjudicate-grok-518ab8`), owner brief
`worker-launch-consolidation-owner.md`, inventory `464dfc`. Dev tooling is
permanent and independent of the Symfony application. A `prod` work
profile describes the application being developed, not these tools'
implementation or a replacement for them. C1 changes only this contract
and the [task contract](../task/CONTRACT.md), for independent tests and
review; runtime convergence and generic output grants have not landed.

**Target:** `launch.py` is the sole worker execution service: admission,
snapshot and input custody, invocation and resume, supervision, envelope
acceptance, settlement and recovery. `record.py` owns dispatch record
construction and validation. Dispatch/go/ladder consume that service;
task run is the refusing vendor projection with local direct/stub behavior:

| frontend | owns |
|:--|:--|
| [dispatch.sh](../../bin/dispatch.sh) / [wrapper](../../conductor/tools/dispatch.sh) | worktree/ref resolution, selection and inputs, foreground/detached invocation |
| [go.sh](../../bin/go.sh) | intent shorthand and configurable selection, forwarded to dispatch |
| [ladder](../../conductor/tools/ladder.py) | stage order, custody between jobs, retries and ladder records; worker execution and session resume stay in launch |
| [task run](../task/CONTRACT.md#c1-task-frontend-construction-decision) | explicit pre-effect vendor-mode refusal naming launch; direct verification and stub replay remain local |

Consuming frontends preserve the same effective selection, refusals and
accepted result without alternate worker execution or envelope engines.
Task owns its vendor-mode invalid envelope and local direct/stub behavior;
its vendor refusal returns no launch result. Harness
(CLI mechanics), model (explicit identity and effort), role (source
authority), and work profile (policy context) are independent configurable
dimensions, subject to job constraints and capability admission. No fixed
job-to-model table or vendor hierarchy confers authority. The target records
resolved selection and its configuration source. Actual-model provenance
remains stream-backed `model.ran`, never copied from `model.requested`.

Launch/task own job output requirements and grants; dispatch owns harness
capability declarations below; harness owns isolation/probes; task owns
envelope semantics. **Lane policy is a later consume-only coupling** owned
by the parallel `lane/lane-boundary-20260905` track. Its accepted interface
will supply profile policy. This track defines no duplicate policy and
does not make that owner depend on output grants. C1 adds no lane-policy
implementation, discovery or fileset dependency; its profile transport below
consumes the policy owner's selection rule. Workers receive job-authorized context,
not conductor-wide discovery; both MCP surfaces remain read-only.
Superseded 2026-09-16 (D-MCP-1): MCP acts as well as reads.

### C1 frontend inputs and results

The construction seam is the existing `launch.main(argv=None) -> int`
and its CLI, not a new callable returning an envelope. `argv` is a list
of strings excluding the executable, or null for process argv. Fresh worker
input is exactly the existing worker verb/options under [Verbs](#verbs),
with invoking repository, WF_AGENT identity and admitted configuration in
their existing channels. Resume input is `["resume", ID]`, optionally the
existing prompt-file option; ID addresses the same canonical record. An
accepted resume preserves original selection, sources and lineage and uses
the same admission, supervisor, extraction and settlement as fresh launch.
No task resume method, alternate execution service or new store is added.

Frontends retain launch's exit and stderr refusal detail, stdout record
acknowledgement `record: <id>`, and canonical record as their result source.
Detached acknowledgement proves launch only; completion is read with the
existing status/wait/record transport. For accepted completion consume
`result.envelope` by value, preserving commit, findings/counts, status/verdict,
stamp, handles and launcher-resolved spend; Python object identity is not
a cross-process promise. Before effects a dispatch refusal produces no task
envelope; after effects consume the retained partial result, including null
envelope when declared by unit1a. A process code or absent envelope never
manufactures ok/approve. Existing dispatch/C2 refusal exit 3, task CLI
0/1/2/64, wait 0/1/2 and policy pre-effect 2/late 3 mappings retain their
respective ownership; this correction does not reconcile those wires.

**Profile transport:** the worker verb list intentionally has no `--profile`.
Use the already declared [unit1a trusted transport](#unit1a-pre-effect-admission):
the admitted invoking consumer holds job, stage, snapshot ref, frozen lineage,
explicit selection/provenance and complete task LaunchDeclarations, supplied
by direct in-process argument or inherited read-only descriptor under that
consumer's custody. The profile inputs are the optional explicit profile
in trusted_context and `LaunchDeclarations.selection.profile`; both are
nonempty policy ids when supplied. Resolve agreement/one/default/absence
exactly under [policy selection](../../conductor/lane-policy.md#selection-and-maintenance-grants).
Null denotes absence while resolving; empty, unknown or conflicting values
refuse lane-profile before effects. The result is the same complete
TaskSelection `{harness,model,effort,role,job_role,profile}` in retained
declarations/binding, with resolved nonempty profile, role worker, job_role
equal to the admitted job, and model/effort matching explicit launch input.
Compare job/ref/lineage separately, since they are not selection members.

Dispatch/go/ladder must preserve these exact values and source identities;
an unbound caller needing profile policy refuses until trusted transport is
available. No environment, scope prose, caller-selected input file, lane.conf
key, worker JSON or new public flag substitutes for that channel. Task's
runtime.profile is declaration vocabulary, not authority or a reason for its
refusal frontend to execute. Resume cannot change profile through prompt
text or a newer catalogue. Model observations remain record.model.ran and
read_from, separate from requested model and from spend.harness. This is a
contract completion, not a claim of installed profile support.

The independent test producer follows the task contract's
[per-test correction](../task/CONTRACT.md#c1c2-independent-test-correction)
before runtime implementation; mocks conform to these inputs/results.

### Measured baseline and limits

Source re-read on 2026-09-06 at `2b0491203cb9b37459fb2af6ac703940051374d2`:
`git diff e59a52bce37dbd1fed52d358b655bae00267f71f 2b0491203cb9b37459fb2af6ac703940051374d2 --name-only --
ops/devlane ops/bin ops/conductor/tools ops/process` returned no paths.
Inventory `464dfc` remains source evidence for these estates, not proof
of current operator use. `rg -n 'run\.run\(' ops --glob '*.py'` found only
`task/tests/test_run.py`; `git ls-files '*lane*policy*'
'ops/contracts/imports.json'` found neither tracked path. C1 makes no live
vendor, host, session-store or network measurement.

| path / defect | current source behavior; boundary consequence |
|:--|:--|
| `launch.py` and `task/run.py` | Both call `subprocess.Popen`; launch does not call run. Task's vendor path is manual/dormant with no production caller found in the inspected tree, not a proven backend. Direct verification and stub replay remain non-worker uses. |
| dispatch/go/ladder | Wired to launch; wrapper permits `LAUNCHER_PIN`. Go defaults Claude for tests/implementation while launch refuses Claude writes; ladder selects Grok/Codex. Wiring is not selection parity. |
| shell levers and [cross-review recipe](../../process/cross-review.md) | Manual paths with distinct snapshots, records and supervision; live use unmeasured. Preserve supported unique behavior before later explicit tested refusal or owner-admitted retirement, including recipe consumers. |
| probes and fixtures | Diagnostic and synthetic paths, not production frontends. Fileset/staging and task vendor parity are not prerequisites to frontend convergence. |
| supervision | Launch checks individual `output_tokens`, wall time and stream grace inline; task run arms the telemetry battery. Aggregate/repeat/error/stall/size parity is not established. Resume re-enters launch's inline supervisor. |
| transport and acceptance | Launch's schema matcher differs from task's semantic validator. Declared document outputs do not yet yield generic grants; see [job outputs](../task/CONTRACT.md#declared-job-outputs). Process exit zero alone does not certify a deliverable. |

Reproduce implementation distinctions with `rg -n
'Popen|writable_out=|def _schema_valid|output_tokens|mode-unavailable'
ops/devlane/dispatch/launch.py ops/devlane/task/run.py`, and selection with
`rg -n 'LAUNCHER_PIN|def_harness|author-tests|implement'
ops/conductor/tools/dispatch.sh ops/bin/go.sh ops/conductor/tools/ladder.py`.
The sections below retain existing dispatch obligations, with proposals
and defects identified explicitly; C1 is not a broad roadmap.

Dispatch is a *composition* app. It reads the task app's job registry
(`ops/devlane/task/jobs.json`) and drives the harness app's isolation and
budget wiring (`ops/devlane/harness/isolation.py`,
`ops/devlane/harness/wires.py`). It was split out of the task app so it may
depend on both without the `task → harness` edge the cross-app import
boundary forbids: a launcher that executes `isolation.isolated` and
reads `wires.budget` genuinely depends on `harness`, and the task app
must not. The allowed edges `dispatch → task` and `dispatch → harness`
are declared here. The historical `ops/contracts/imports.json` is absent
in this tree, so no executable import gate is claimed. Task must not depend
on dispatch or harness; the reverse would create a cycle.

| file | job |
|:--|:--|
| `ops/devlane/dispatch/launch.py` | worker execution: mint a snapshot, launch, supervise, collect, record |
| `ops/devlane/dispatch/record.py` | build and validate one dispatch record, one implementation per rule |

The rest of this file is the launcher's contract, written before the
launcher existed so its tests could be authored from it. No guide page
is carried here; this contract is the whole specification.

## C2 — output admission (contract only)

Standing: GPT-6 plan `20260906T024750Z-plan-codex-93f60d`, Claude review
`20260906T025522Z-review-claude-cfc890`, with every disposition in the
supplied `launch-c2-contract-ruling.md`. Task owns the exact
[outputs schema, rendering and acceptance](../task/CONTRACT.md#declared-job-outputs).
This two-file contract changes no executable code, catalogue, prompts,
settings or capabilities. The C1 runtime below remains unchanged, including
its check-tests mechanism and narrow inline planning transport exception.
Historical records retain their standing; C2 is not retroactive validation.

**Broader directory grants refuse. No currently unresolved adapter is
admitted by these docs.** A future enforceable mechanism is a runtime
adoption dependency, not a prerequisite for writing this contract.
Dispatch's `launch.py` alone resolves declarations, computes grants,
invokes adapters, re-admits attempts and settles output custody;
`record.py` owns the record shape and its validation. Dispatch/go/ladder
consume that path. Adapters translate admitted grants without expanding
them or choosing policy. The dev lane stays independent of Symfony; C2
adds no lane-policy implementation, discovery, fileset or alternate runner.

### Grant intersection and transport

Let `D` be the write operations requested by task-validated file leaves
and scratch subtrees; `R` the role and job-custody ceiling; `P` existing
dispatch restrictions intersected with any supplied accepted profile-policy
restriction; and `H` the operations enforceable by fresh evidence for the
selected harness, adapter, platform and launch/resume operation. The extra
worker grant requested after intersection is `G = D ∩ R ∩ P ∩ H`.
Admission requires both `G = D` and `A = D`, where `A` is the actual extra
worker write authority of the concrete admitted mechanism. The witness
must show that undeclared sibling/ancestor writes are unavailable; a
generic capability set containing `D` does not prove `A = D`. The file-leaf
restriction and exact scratch-subtree exception below define that equality.
Omission or `[]` requires empty `D`, `G` and `A`, regardless of prompt prose,
artifact handles or job name. A declaration demands capability; it does not confer
it. No partial grant, dropped scratch, downgraded report, inline fallback,
automatic harness/model substitution, approval prompt or sandbox bypass
may turn a refusal into admission.

For read roles, `R` excludes writes to the snapshot and its Git directory,
`in/`, `context/`, control files, other jobs and the host. A `file` permits
writing only its declared regular leaf, with no parent-directory rights;
launch may prepare parents and safely open an empty leaf if the admitted
mechanism requires pre-creation. The worker must still deliver non-empty
content. An adapter requiring directory rights for creation, atomic replace
or an editor's sibling temporary file must refuse, even if that directory
is this job's `out/`. A `scratch` entry permits temporary files and
subdirectories only within its declared subtree, with no link escape or
rights over its parent/siblings. Directory granularity is acceptable for
that exact scratch subtree, never as an over-approximation of a file leaf.

Launcher/harness bookkeeping is a separate transport authority, not an
output grant to model tools. This includes the response file
`out/last-message.json`, records, session state and supervisor markers.
Existing write-job `out/COMMIT_MSG` and envelope `commit` transport remain
compatibility obligations under their existing authority; neither becomes
a required report or a generic read-job write grant. An adapter must
declare its transport reservations so declarations cannot overlap them.
It cannot hide broad worker rights as bookkeeping. At adoption, declared
and admitted scratch owns its temporary subtree and `TMPDIR` mapping,
replacing any legacy launcher-created temporary output directory for that
purpose. That admitted scratch subtree is not also a transport reservation;
genuine transport leaves remain reserved and file/scratch collisions still
refuse. `TMPDIR` is set only from admitted declared scratch; without scratch
no inherited temporary path may add worker write authority. A harness
needing writable transport or temporary storage must prove that separation
or refuse.

The parallel boundary track owns profile policy. C2 consumes an accepted
restriction when available and requested; it defines no profile names or
fallback allow policy. A request needing an unavailable policy decision
refuses. This consume-only join cannot widen `D`, create a cycle, or block
the contract-only bucket. Harness, model, role and profile stay independently
selected. The caller/configuration must explicitly name the model; record
requested versus observed identity and configuration source without
substitution, as C1 requires.

### Attempt admission and custody

For C2 adoption, launch reads the entire trusted catalogue through task's
[strict reader](../task/CONTRACT.md#c2-library-adoption-seams), before job
selection can discard a duplicate. It holds the exact bytes/digest used;
ordinary json.loads followed by re-encoding cannot satisfy that duty.
Compute the complete sorted unique relative transport reservation list from
launcher-owned leaves and the admitted adapter declarations, then carry it
unchanged as LaunchDeclarations.reservations and as reserved= to task's
outputs_from_job, render_declared_outputs and append_declared_outputs.
Include COMMIT_MSG and last-message.json for every harness, not just the
one currently using a response file. Explicitly measured no-extra paths is
valid; unavailable reservation analysis refuses output-capability before
effects. No adapter.reservations method or new adapter/policy API is needed:
dispatch acquires the already declared data and source evidence, task checks
it without selecting an adapter. Synthetic tests supply that held transport.

Check the complete declaration/reservation collision set and bound handle
representability before rendering any grant text or creating snapshot,
prompt, record, home, output leaf, attempt or child. Equal paths and either
ancestor direction refuse outputs-schema. Declared scratch (including tmp)
is excluded from transport reservations and keeps its own exact authority.
Resume rechecks retained declarations against complete current verified
transport needs before marker/store/attempt mutation; a new collision
refuses, never deletes a declaration or silently widens a reservation grant.
Library compatibility defaults alone cannot certify complete launch analysis.
Use task's OutputsError family and semantic envelope validation, including
optional commit and counts/handle checks; never reconstruct a second
validator in the launcher. Preserve all C2 custody/capability obligations
below and the historical inline exception's narrow scope.

Before minting filesystem state, task validates declarations from the
trusted launch-side catalogue, including transport collisions. Launch binds
the normalized declaration and catalogue SHA-256 digest into the record
through `record.py`, together with the canonical job/output root, effective
grant, selection and capability evidence identity. Authorization records
and grant descriptions stay outside worker-writable storage. Never reload
authority from a worker-edited snapshot or trust its paths as grants.

Initial launch and resume use the same admission operation before starting
a child. Recheck path types and aliases beneath the verified canonical job
directory, role/policy intersection and capability freshness. All ancestors
must be directories without symlinks; existing file entries must be regular
and singly linked, and scratch must be a directory. Missing paths may be
safely prepared by launch under the admitted mechanism. Unsupported naming,
alias behavior or unavailable safe checks refuse; do not follow or repair
an unsafe path. The capability witness must cover races and every worker
write surface; lexical/real-path checks alone are insufficient.

Resume uses the recorded declaration, rebinding its relative names under
the verified current job root after relocation. A newer catalogue cannot
add grants. A legacy record without declarations supplies no extra grant;
if its saved session would retain an unrecorded grant, refuse resume and
require a fresh dispatch. Re-admission precedes clearing exit/trip markers,
restoring writable session state or invoking a child. Failed admission
preserves prior evidence and attempt history.

Normal settlement, close and recovery consume the same task acceptance
and output custody check. Once worker writes have stopped, verify output
type, containment and links without following an escape; bind accepted
bytes by SHA-256 digest, artifact key, relative path and attempt number in
the record. Preserve previous reports with their prior digests and attempt
identity outside worker-writable storage before making the next attempt's
destinations available. A leftover report alone cannot satisfy completion.
Reuse must be explicitly named by the current attempt, checked against the
trusted prior artifact/digest, rechecked for present type/containment/links,
and recorded as reuse from that prior attempt; otherwise require fresh
production. The exact record/reuse transport is declared in
[unit1a](#unit1a-attempt-and-result-transport); its implementation remains later
work owned by record.py and launch, with task envelope semantics retained.

Keep read-role HEAD/residual settlement in addition to output acceptance.
The current Git/cache exclusions below do not establish byte-for-byte
immutability, all metadata integrity, or absence of writes later restored.
The prevention witness must protect source throughout execution; chmod and
a clean final Git diff are not that witness. Stronger settlement coverage
requires its own independently specified evidence, not a new claim here.

### Capability evidence and freshness

Apply the version/date and counterparty-movement law in
[verified couplings](../../../docs/verified-couplings.md#the-six-obligations-of-every-edge).
Each capability witness must carry a valid UTC `checked_at` in
`YYYY-MM-DDTHH:MM:SSZ` form, observed harness name/version and executable
identity, adapter revision, mechanism
and effective permission-configuration digest, platform identity (OS,
sandbox implementation/version and filesystem alias semantics), role,
operation (`launch` or `resume`), and exact supported output/source access
and limits. Bind the output contract revision, probe revision/result and
digest-backed evidence handle, plus a contract-verified mock revision and
its passing verification evidence. Record that identity at admission;
labels such as `os`, CLI help or mocked argv are not live proof.

Freshness is a predicate evaluated before each attempt at a recorded UTC
admission time. Every required stamp/identity/evidence item must be present,
readable and verified; `checked_at` must not be in the future. The observed
counterparty identity tuple (harness name/version/executable, adapter,
mechanism, permission digest, platform, role, operation, output contract,
probe and mock revisions) must equal the currently measured selected tuple,
and its scope must cover every requested operation and retained source
isolation. Any change to harness executable/version, adapter, mechanism,
effective permissions, platform, contract, probe or verified mock voids
the prior verification and demands a new passing witness. Launch evidence
does not admit resume unless resume has its own passing coverage.

There is no implicit age limit just because a date exists. If an accepted
configuration sets `max_age_seconds`, record its value and source; require
a non-negative integer and `0 <= admission_time - checked_at <= max_age_seconds`.
Without that configured limit, elapsed age alone does not expire an
otherwise matching witness. Missing/malformed stamps or inaccessible
evidence are unresolved; changed-counterparty or expired evidence is
stale; failed or unattempted controls never pass. All fail admission as
`output-capability`, without guessing a value or prompting for permission.

Independent cases can therefore hold time and tuple fixed for a positive
case, change one tuple member for stale evidence, remove `checked_at` for
unresolved evidence, or cross an explicitly configured age bound for expiry
(equality at the bound passes). Old but matching evidence with no age
policy does not fail solely for age. Synthetic counterparties prove the
admission logic; only passing live witnesses and verified mocks admit a
real harness.

The future witness must demonstrate reading snapshot/inputs, writing the
declared non-empty leaf and scratch, and finishing without permission
prompts, while blocking source/Git writes, undeclared siblings, control and
transport files, other jobs and host paths. Exercise direct tools and
shell/subprocess writes, rename/delete, chmod-then-write, source-linked
paths, inherited temporary paths and path races on both launch and resume.
A write-then-restore negative control distinguishes prevention from final
acceptance detection. Negative arms must actually attempt the forbidden
operation; a model declining to try or a tool failing to run is unresolved,
not proof of denial. Probe apparatus and mock must fire on planted faults
and stay quiet on clean neighbors. Skeptics copy mutable fixtures into
declared scratch; unavailable source/Git/network/outer-workspace authority
must be reported, never improvised.

At the supplied `19f938e339cc519d66eb65989915decfa00439df` source baseline,
the mechanisms recorded under [isolation](#isolation-per-harness) do not
establish this witness for Codex, Claude or Grok. Their C2 standing remains
**unresolved, unadmitted**. No live capability probe is run or authorized by
this bucket. Existing runtime continues as documented below; future output
admission must refuse each unsupported capability until independently
proved. A witness for one harness cannot admit another.

### C2 refusals

These are target refusals for later executable adoption, all non-overridable.
Retain dispatch's documented exit 3 and stderr form in this bounded change:
`<id>: expected <expected>; found <found>; needed <needed>`. Found names the
actual offending member/operation or unavailable evidence, never just
"invalid". Founding-wire exit reconciliation stays separate.

| id | expected | found | needed / timing |
|:--|:--|:--|:--|
| `outputs-schema` | valid unique, non-overlapping declarations | invalid member, duplicate, overlap or transport collision | correct trusted catalogue; before minting filesystem state |
| `outputs-path` | safe canonical, correctly typed unaliased targets and representable handles | offending path/type/link or unavailable check | corrected declaration or fresh safe job directory; before child execution |
| `outputs-policy` | every requested operation survives role/policy | excluded operation or unavailable requested decision | compatible scope or accepted policy evidence; before child execution |
| `output-capability` | fresh passing witness and verified mock for exact outputs plus source isolation | unsupported, stale, failed or unresolved evidence, including broader directory rights | passing witness or explicitly selected admitted harness; before child execution |
| `outputs-resume` | trustworthy recorded declaration and reproducible grant | absent custody, inherited unrecorded grant or incompatible rebinding | fresh dispatch or restored verifiable custody; before markers/session state change |

Settlement failures use the same expected/found/needed detail in the note:
`outputs-missing` expects a required non-empty document, finds absence or
empty content, needs current-attempt production; `outputs-handle` expects
the declared key and bound path, finds the missing/wrong handle, needs the
correct envelope reference; `outputs-custody` expects safe readable bytes
with verifiable attempt provenance, finds unsafe/unreadable/stale evidence,
needs safe production or verified reuse. They make an otherwise completed
envelope `invalid` with null verdict, never `ok`. Existing `tripped`
outcomes retain their class and partial evidence and never approve.

### C2 runtime adoption dependencies

Contract review may proceed independently. Executable adoption requires:

1. Task-owned declaration validation and deterministic deliverable rendering,
   with catalogue/prompt alignment for native jobs and renamed equivalents.
   Declare required keys to workers before enforcing them; retain historical
   evidence without retroactive rejection.
2. Launch integration with task's semantic validator as the single authority,
   reconciling optional write-job commit transport and independently defining
   and testing the transition of the narrow inline planning exception. Do
   not duplicate counts/status/handle validation or generalize that exception.
3. Record/envelope integration for immutable declarations, catalogue digest,
   grant/evidence binding and per-attempt production/reuse, shared by launch,
   resume, close and recovery. Preserve response extraction and actual-model
   provenance.
4. An enforceable leaf/scratch/transport mechanism with a fresh admitted live
   witness and contract-verified mock for each selected harness/operation.
   No broader directory substitute satisfies the leaf contract.
5. Independently authored behavioral tests, independent skeptic and final
   review. The implementation producer neither writes nor approves its tests.
   Run `ops/bin/suites.sh` for later runtime landing; this docs bucket claims
   only its named documentation acceptance checks, not that suite or proof.

Prospective test homes (not files created by C2):

- `ops/devlane/task/tests/test_job_outputs.py`: native/renamed declarations,
  omission/empty, exact rendering/key alignment, malformed/unknown members,
  null, duplicate members/keys/paths, overlaps, traversal and reservations.
- `ops/devlane/dispatch/tests/test_launch_outputs.py`: synthetic capability
  intersection, contradictory prose granting nothing, partial/unsupported/
  stale/unresolved evidence preventing child creation, unchanged source,
  missing/forged/linked outputs, attempt/reuse custody and catalogue changes
  unable to widen resume, plus unchanged explicit model selection.
- `ops/devlane/harness/tests/test_output_capability.py`: independently
  specified positive/negative controls and freshness cases above, stamped
  per harness/version/platform/operation. Live execution requires its own
  authorized environment; missing-symbol failures and argv-only assertions
  are not adequate behavioral proof.

Preserve existing regressions in
[test_launch_skeptic_write.py](tests/test_launch_skeptic_write.py),
[test_launch_snapshot.py](tests/test_launch_snapshot.py),
[test_launch_refusals.py](tests/test_launch_refusals.py),
[test_record_paths.py](tests/test_record_paths.py), and
[test_launch_verbs.py](tests/test_launch_verbs.py). Sandbox-label assertions
need independent reconciliation with capability evidence; a particular flag
is not the security oracle. No subsequent implementation buckets are
scheduled or capability activation authorized here.

## Dispatch — the dev-lane launcher

`launch.py` mints a snapshot from a named ref, keeps every
artifact outside the tree, applies the preconditions below as refusals,
supervises inline, returns the envelope, and writes a dispatch record
into a tracked directory on the branch. This section is its contract.
Its target sole-authority boundary is declared in C1 above; the measured
multiple paths have not been removed by this contract change.

Vocabulary: the **invoking repository** is the checkout the launcher
is run from; the **lineage branch** is the branch the work unit lives
on; the **job directory** is where one dispatch's snapshot and
artifacts live, outside every repository.

### Verbs

```text
launch.py <job> --harness H --model M [--effort E] --ref R
          [--lineage B] [--unit U] [--stage S] [--scope TEXT]
          [--input PATH]... [--follows ID]... [--answers KEY]...
          [--override ID:REASON]...
launch.py status [ID] [--json]
launch.py resume ID [--prompt-file PATH]
launch.py collect ID --verify CMD [--reason TEXT] [--timeout SECONDS]
launch.py wait ID [--timeout S] [--interval S]
launch.py close ID
launch.py brief --check ID
```

- `<job>` names a `jobs.json` entry. A job never names a model; `--model`
  is the owner's choice at dispatch and is required.
- `--ref` is resolved to a commit in the invoking repository. `--lineage`
  defaults to the invoking checkout's current branch; `--unit` defaults
  to the lineage branch and groups records when one branch carries
  several work units; `--stage` is one of `plan`, `tests`,
  `check-tests`, `code`, `review`, `adjudicate` for worker invocation.
  The proposed canonical record union additionally has `conduct`, supplied
  only by trusted conductor startup, not this worker CLI or a jobs entry;
  see [the complete variant](#canonical-conductor-record-variant-proposed).
- `--scope` is the **one free field** the dispatcher may add, capped at
  **1024 bytes**, reproduced verbatim in the record. A job whose
  template has no `{scope}` refuses it.
- `--input PATH` copies a file into the job directory's `in/` and names
  it to the template as `{inputs}`; its digest enters the record.
  Every shipped harness job must actually include those paths in
  its rendered brief. Copying inputs without telling the worker their
  paths does not discharge this boundary. List the copied paths in the
  caller's order, including for implementation, planning and review jobs;
  retain the existing instruction to read the supplied files before work.
  With no inputs the list is empty, never a guessed file or directory scan.
  Independent acceptance launches the shipped jobs through synthetic
  harnesses and checks that the exact copied paths reach the worker's
  prompt and agree with `brief.inputs`; it also checks an empty-input
  neighbor. No input contents are inlined or promoted to extra authority.
  The dispatch boundary supplies the list even when a shared job template
  omits `{inputs}`; adding a new required field to another runner's shared
  templates is not necessary to meet this dispatch obligation.
- `--follows ID` names the records this dispatch was given and follows
  (plan → tests → check-tests → code → review → adjudicate). It is a
  dispatch-graph edge; it is **not** `context.prior`, which stays
  "findings from an earlier call".
- `--answers KEY` is repeatable and names each findings-store key answered
  by a correction. A key has the shape `<record>#<n>` and is accepted only
  when it matches
  `^\d{8}T\d{6}Z-[a-z-]+-[a-z]+-[0-9a-f]{6}#[1-9][0-9]*$`; an invalid key
  is refused `arguments` before it can enter a printed remedy.
- The citation grammar lives in `findings.py` as
  `contract item (?P<item>[1-9][0-9]*) of (?P<path>[^\s;,:]+)`.
  The gate and `findings.py` read the same definition.
- `--override ID:REASON` is accepted only for a refusal marked
  overridable below, and only with a non-empty reason.
- Identity: `WF_AGENT` in the `Name <address>` form, as for `wf`.
  Recording always needs one.
- Jobs root: `$DISPATCH_JOBS`, default
  `${XDG_STATE_HOME:-$HOME/.local/state}/agentscient/dispatch`. There is
  no flag for it; the record carries the path it used.

#### Correction shape

A correction is a write-role job (`implement` or `author-tests`) whose
dispatch arguments either use `--follows` to name a record whose `job` is
one of `check-tests`, `security-audit`, `adversarial-review`, `adjudicate`,
or `sweep`, or include an `--input` path beneath such a record's `out/`.
The shape is read only from the dispatch arguments and the referenced
record, never from prose. Each followed id must match
`^\d{8}T\d{6}Z-[a-z-]+-[a-z]+-[0-9a-f]{6}$`; its
`<records>/<id>.json` path must resolve beneath the records directory, and
the record's own `id` must equal the requested id and its `job` must name a
registry job. A failure of any of those checks is a `follows` refusal.

### Snapshot modes

A job declares one of two modes. They are mutually exclusive by
construction, and a job that needs both is refused.

**`whole`** — a fresh repository minted from the invoking one:
`git init`, then `git -c core.logAllRefUpdates=false fetch <invoking
repo> refs/heads/<lineage>:refs/heads/<lineage>`, then `FETCH_HEAD`
removed, then `checkout --detach <ref_sha>`. Guaranteed, and pinned by
test: `HEAD` equals `ref_sha`; the ref's full history is present, so
`merge-base` questions are answerable; `git remote` is empty;
`objects/info/alternates` does not exist; `logs/` does not exist; the
invoking repository's path occurs in **no byte** under `.git`; `git
status --porcelain` is empty before launch. It is not `git worktree
add` (that registers in the live repository's worktree list, which is
what the `live-target` refusal exists to catch) and not `git clone
--shared` (that leaves the source path in `alternates` and the reflogs
after `origin` is removed, and makes the source object store writable
by absolute path).

**`fileset` (unimplemented proposal)** — `fileset.snapshot` with `include` and `withheld`,
proved by `stage.prove` in both directions, and **no `.git`**: a
withheld file is one `git show HEAD:path` away in any snapshot with
history. Its manifest and diff live in the job directory, not at the
snapshot root. The existing tested `mode-unavailable` refusal remains
(`tests/test_launch_refusals.py`, `HistoryVersusWithheldAndModeUnavailable`).
There is no commitment to implement filesets in C1; dormant staging is off
the required frontend convergence path. Manual staging is not equivalent
dispatch.

`direct` jobs (`verify`) stay in task, take the caller's fileset and declare
no mode; launch admits only harness jobs.

### The job directory

```text
<jobs-root>/<id>/
  snapshot/      the tree, and nothing that is not the tree
  prompt.txt     the rendered brief, exactly the bytes handed over
  in/            copies of every --input, by basename
  out/           where a job writes a document deliverable ({out})
  raw.out        the harness's stdout, byte for byte
  stderr         the harness's stderr
  launcher.log    detached wrapper output, when launched with --detach
  breaker.log    proposed battery stderr; launch currently supervises inline
  TRIPPED.md     written by launch on an inline supervisor stop
  state.json     pid, pgid, session id, stream path, attempt
  exit           the harness's exit status, written last
  home/codex     minimal home for codex (auth.json only)
  home/grok      minimal home for grok (auth.json only)
```

Nothing the launcher, the battery or the harness writes for its own
bookkeeping goes under `snapshot/`. Two incidents sit behind that
line: report files at a snapshot root tripped the vocabulary wall
twice, and bookkeeping files counted as repository changes.

`id` is `<UTC stamp>-<stage>-<harness>-<6 hex>`. The stamp is real UTC
`YYYYMMDDTHHMMSSZ`; stage and harness equal the record fields. The proposed
`conduct` stage uses this same namespace, directory and record owner.

### Template values

`render` receives, beyond `context` and `require`: `ref` and `base`
(shas), `diff` (path, or absent), `into` (the snapshot root), `out`
(the absolute path of the job's `out/`), `inputs` (the absolute paths
of the copied inputs, space-separated, in the order given), and
`scope`. A template naming a value the launcher did not supply is a
refusal at render, never a brief with a hole in it.
For harnesses whose argv carries no schema (codex and grok), the
rendered brief ends with the envelope JSON Schema. Claude continues to
receive the same schema through `--json-schema` instead.

### The child's environment

Applied to the harness process only, never exported by the launcher
for itself: unset `CLICOLOR_FORCE` and `FORCE_COLOR` (they beat
`NO_COLOR`; measured, 525 escape bytes in `gh` JSON); set `NO_COLOR=1
CLICOLOR=0 TERM=dumb PAGER=cat GH_PAGER=cat GIT_PAGER=cat LESS=FRX
CI=true GIT_TERMINAL_PROMPT=0 GIT_EDITOR=true EDITOR=true
PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8 LC_ALL=C.UTF-8`, plus
`WF_LANE=dev` and `DISPATCH_JOB=<id>`, plus whatever
`isolation.dispatch_env` returns. The working directory is `snapshot/`.

### Isolation, per harness

**Capability authority and evidence.** Dispatch declares the capabilities
it consumes; adapter mechanics implement argv, hooks, parsing, resume and
MCP integration without becoming policy owners. The table below is the
current source-selected behavior at the C1 baseline, reproduced by
`sed -n '360,555p' ops/devlane/dispatch/launch.py` and
`sed -n '70,150p' ops/devlane/harness/isolation.py`. It is not a claim that
live containment probes ran. A capability declaration names the supported
operation and role, output/source access, mechanism and limits, harness
version, dated probe result and evidence handle, and contract-verified
mock. Missing observation is `unresolved`; an arm that could not run is
INVALID, never a pass. Evidence expires when the counterparty changes.

Target output grants need per-harness witnesses for both the declared
output write and retained source isolation, or an explicit
unsupported-capability refusal with expected, found and needed. A Codex
witness cannot admit Claude or Grok. Configuration isolation is not host
containment, and a recorded `os` label is not a universal security proof.
No new runtime refusal or probe is implemented in C1.

`isolation.isolated()` runs on every new launch; there is no argument that
turns it off, and a harness with no entry is refused. A relocated record
re-derives its home environment, flags, and stream store from that same
entry at the canonical current job path; a live recorded home or store at
a foreign path is refused. The stream store
is read from the same entry's `sessions` spec, because a relocated
`CODEX_HOME` or `GROK_HOME` relocates the stream and a launcher looking
in `~/.codex/sessions` would report "no stream" and run unsupervised.

| harness | isolation | read sandbox | write sandbox | containment | model that ran |
|:--|:--|:--|:--|:--|:--|
| claude | flags `--setting-sources project,local --strict-mcp-config --disable-slash-commands`; HOME untouched | `acceptEdits`; `--add-dir out` is not OS containment | **not admitted** — no write row until the containment probe below has passed | policy | `~/.claude/projects/<slug>/<session-id>.jsonl`, `assistant` records, `message.model` |
| codex | `CODEX_HOME=<job>/home/codex` holding exactly `auth.json` | `workspace-write` under OS containment for every read-role job (LD-14, below) | `workspace-write` | os | `$CODEX_HOME/sessions/Y/M/D/rollout-*.jsonl`, `turn_context.payload.model` (id and cwd from `session_meta`) |
| grok | `GROK_HOME` **and** `HOME` at `<job>/home/grok`, holding exactly `auth.json`; prompt via `--prompt-file` from the job directory | `always-approve` | `always-approve` | policy; no OS sandbox claimed or invented | `$GROK_HOME/sessions/<url-encoded cwd>/<session-id>/summary.json`, `current_model_id`; read-role `head_commit` equals `snapshot.ref_sha`; write-role `head_commit` equals either that immutable ref or the actual local `HEAD`, which is always verified as its descendant; every other hash is rejected |

**Measured Codex read-role containment — not a live containment
proof.** Every Codex job of role `read` (check-tests, security-audit,
adversarial-review, plan, adjudicate, sweep) keeps its role, but its
Codex process uses the `workspace-write` sandbox label so it can write
scratch files under the job `out/`, which is the only writable root;
`TMPDIR` is a real, non-symlinked `out/tmp`. Before the attempt the
launcher strips Unix write bits from every snapshot file as hardening,
but mode bits alone are not a hostile-process security boundary. After
the attempt, normal read-role settlement invalidates a changed HEAD or
any residual snapshot path. LD-14 (2026-09-11) widened this from
check-tests alone: under the `read-only` label Codex ignores
writable_roots and blocks `TMPDIR`, so three of three security audits
that day could not execute a suite and judged statically. Live vendor
containment proof for this path has not run.

For Claude, the snapshot's `.claude/settings.json`
`permissions.allow` and `permissions.deny` strings are carried on argv
and recorded under `harness.permissions`. Allow rules beginning
`Bash(gh `, `Bash(composer `, `Bash(git `, `Bash(php `, or
`Bash(bash ops/bin/` are dropped because they admit network access, an
interpreter, or worker-editable scripts; malformed or absent project
settings are declared `unresolved`.
Current Claude argv also denies `WebFetch`, `WebSearch`, `Agent` and
`Task`; Grok receives `--always-approve --disable-web-search`. These are
adapter policy settings, not evidence of an OS boundary.

`containment` says what stops a write role reaching outside its
snapshot: `os` when the harness's own sandbox enforces it, `policy` when
only the harness's permission mode does. It is recorded, not judged.

The session handle is minted at launch — `claude --session-id <uuid>`,
`grok -s <uuid>` — and codex's is read from `session_meta` once the
stream is found. A harness that ignores the minted id is recorded as a
mismatch, never silently accepted.

**Containment probe (admits a claude write row).** Two arms, both
dispatched, both must answer on their own terms: arm A runs the
candidate write mode with a brief that runs the snapshot's test suite
and edits a file, and must succeed at both; arm B runs the same mode
with a brief that writes a file at an absolute path outside the
snapshot, and must fail to. An arm that did not run is INVALID, not a
pass. The result is recorded verbatim in the adapter row's
`containment` entry and in every record that row produces.

**Behavioural observation, per harness**, recorded verbatim under
`harness.isolation.observed`: claude asks the model whether the
operator's probe phrase is in its instructions (unisolated YES,
isolated NO); grok reads `grok inspect` (unisolated ≥1 instruction
file, isolated 0); codex asks for the names of the skills available to
it, one per line, or NONE (unisolated arm lists ≥1 or the run is
INVALID because the machine cannot demonstrate the leak; isolated arm
must answer NONE). Each is true of one harness version on one day and
carries `checked_at` and `harness_version`. A record for a harness
whose observation has not run carries `observed: {"unresolved":
<why>}` — never a manufactured `false`.

### Refusals

Current dispatch refusal exit: 3, before anything is minted unless the
table says otherwise. This differs from the founding [wire](../../../docs/wire.md)
(Rejection 2, Abort 3); shell levers and task CLI also have distinct exit
mappings. Reconciliation and wire-gate expansion are outside C1. The
text of each names what was expected, what was found, and what would
satisfy it. `override` marks the only overridable one.

| id | refuses when | expected / found / satisfy |
|:--|:--|:--|
| `identity` | `WF_AGENT` unset or not `Name <address>` | expected an identity in the `Name <address>` form; found `<value or unset>`; export `WF_AGENT='Your Model Name <noreply@vendor>'` |
| `live-target` | the destination is inside any entry of the invoking repository's `git worktree list --porcelain` or its common dir | expected a job directory outside every worktree of `<repo>`; found `<path>` inside `<worktree>`; set `DISPATCH_JOBS` to a directory outside the repository |
| `ref` | `--ref` does not resolve to a commit | expected a ref naming a commit in `<repo>`; found `<ref>` (`<git's message>`); commit the work, then name the commit — uncommitted work is never dispatched |
| `follows` | a record used to determine the [correction shape](#correction-shape) has a malformed id, escapes the records directory, cannot be read, has a different `id`, or names a job outside the registry | expected a readable record for `<id>` under `.dev/records/dispatches`, with its own `id` equal to `<id>` and a registry `job`; found the failed shape, path, read, identity, or job check; needed name a matching record in `.dev/records/dispatches` |
| `correction` (not overridable; exit 2, founding wire) | the [correction shape](#correction-shape) does not name every answered finding, or a named finding is absent, open, uncited, or disposed too late | No key — expected every finding answered by this correction disposed with a contract citation before `<stamp>`; found no --answers key named for a correction following `<id>` (`<job>`); needed pass --answers '<record>#<n>' for each finding answered, each disposed by python3 ops/conductor/tools/findings.py disposition '<record>#<n>' <disposition> 'contract item N of <scope path>: <why>'<br>Absent — expected every finding answered by this correction disposed with a contract citation before `<stamp>`; found key `<key>` absent from the findings store `<path>` or found key `<key>` absent from the findings store `<path>` (no store); needed python3 ops/conductor/tools/findings.py import, then python3 ops/conductor/tools/findings.py disposition '<key>' <disposition> 'contract item N of <scope path>: <why>'<br>Open — expected every finding answered by this correction disposed with a contract citation before `<stamp>`; found key <key> still open; needed python3 ops/conductor/tools/findings.py disposition '<key>' <disposition> 'contract item N of <scope path>: <why>'<br>Uncited — expected every finding answered by this correction disposed with a contract citation before `<stamp>`; found key `<key>` disposed `<what>` without a contract citation (`<reason>`); needed python3 ops/conductor/tools/findings.py disposition '<key>' <disposition> 'contract item N of <scope path>: <why>'<br>Late — expected every finding answered by this correction disposed with a contract citation before `<stamp>`; found key `<key>` disposed at `<disposed_at>`, after the mint attempt at `<stamp>`, or, for the same second, found key `<key>` disposed at `<stamp>`, not before the mint attempt at `<stamp>`; needed wait one second and retry |
| `arguments` (exit 2, before the gate) | `--answers` is given on a write job outside the [correction shape](#correction-shape) | expected no unverified answers on a non-correction write job; found `--answers` on a write job that follows a plan, tests or implement record, or nothing; needed remove `--answers`; argument parsing refuses class `arguments` before the gate runs because a claim no gate would verify must not enter a record, and the gate does not inspect the job. |
| `role` (exit 2, founding wire) | a read-role prompt contains a positive write instruction, recognized by instruction shape (a write verb and a path-shaped token, negated sentences excluded), not by a list of forbidden words; "Please write /tmp/result.md" is a positive instruction to write a path; "Copy the report to /tmp/result.md" is a positive instruction to write a path | expected a read-role prompt with no instruction to write a path; found job check-tests: "Write the report to {out}/SKEPTIC.md and name that path in artifacts"; needed drop the sentence from the prompt or declare the job a write role; one stderr line `role: expected …; found …; needed …`, stdout empty |
| `record-target` | the invoking checkout is detached, on `dev` or `main`, or on a branch other than `lineage.branch`; re-checked at close | expected the checkout on `<lineage>`; found `<branch or detached>`; run from a worktree of `<lineage>` (at close: the record file is left in place, nothing is committed, and `close ID` is re-run from the right branch) |
| `stale-base` (**override**) | `ref_sha` is not an ancestor of `lineage.branch` | expected `<ref_sha>` reachable from `<lineage>`; found it is not (`<lineage>` is at `<tip>`); name a commit on the branch, or `--override stale-base:<reason>` |
| `model` | no `--model`; or `--effort` on a harness whose adapter cannot pass it | expected a model — a job never names one and the launcher never defaults one; found none; pass `--model` (effort is forwarded through each harness adapter) |
| `worker` | a worker name is malformed, disabled, unknown or unimplemented, or resume selection changed | expected an enabled implemented worker matching the recorded selection on resume; found `<name/list or recorded/current values>`; correct worker configuration or launch afresh for changed selection |
| `executable` | an override is empty, relative, missing or not executable; agy PATH lookup fails; or the recorded resume executable changed | expected a regular executable at the configured absolute path or resolved agy PATH location, matching the resume record; found `<value/failure or recorded/current paths>`; correct the executable configuration/install the binary, or launch afresh for changed selection; legacy defaults retain their PATH fallback |
| `unsupported-capability` | measured transport, permission, store or supervision evidence for the requested operation is unavailable | expected measured support for `<operation>`; found `<missing evidence>`; supply the named bounded probe evidence before that operation |
| `isolation` | no `HARNESSES` entry, a credential missing, or a minimal home that is not empty | `isolation.NotIsolated`'s own text |
| `history-vs-withheld` | the job declares `withheld` and mode `whole` | expected one of history or withholding; found both on `<job>`; a withheld file is recoverable from `.git`, so declare `fileset` |
| `mode-unavailable` | the job declares a mode the launcher does not yet mint (`fileset`) | expected `whole`; found `fileset` on `<job>`; stage it by hand with `stage.py` and name the dispatch in the PR body until fileset mode lands |
| `write-role-unadmitted` | a write role on any harness whose adapter row has no write sandbox (claude; agy until measured) | expected a write row with a containment entry; found none for `<harness>`; run the containment probe and add the row, or dispatch the role on codex or grok |
| `scope-cap` | `--scope` over 1024 bytes, or given to a job with no `{scope}` | expected ≤ 1024 bytes on a job that takes a scope; found `<n>` bytes / a scope on `<job>`, which takes none; shorten it — a scope that needs more is a brief, and briefs are derived — or drop it |
| `caps` (**proposed for launch**) | an unknown wire, or `cap` and `total` (or `cap_out` and `out`) both given | launch has no caps preflight; existing validation text is `RunError` from `_breaker_argv` in the dormant [task runner](../task/run.py), not a current dispatch refusal |
| `off-lineage-head` (at close or collect, write roles) | the snapshot's `HEAD` is not a descendant of `ref_sha` | expected `HEAD` to descend from `<ref_sha>`; found `<head>`; nothing is fetched, and the close record says so or collect refuses |
| `resume-store` | a home-isolated resume (including agy) has no session store in either its live or archived home | expected the store for `<session id>` under both named paths; found neither; resume a job whose store survived, or launch afresh |
| `collect-role` | `collect` names a read role | expected a write role; found read; collect only a failed write job |
| `collect-state` (committed recovery extension prescribed below) | `collect` names a running/unclosed job, an already collected result, neither eligible recovery arm, or an unavailable, mismatched or empty candidate | expected a closed uncollected write job with the recorded recoverable delta; found the measured state, failure evidence and candidate mismatch; name the exact preserved eligible candidate or use the normal result |
| `verify` | the collection verification exits non-zero or reaches its wall | expected the named command to exit 0 within the recorded job wall or a larger `--timeout`; found its exit or timeout and stderr tail; correct the candidate through the existing work path and rerun explicit verification |
| `wait-target` | `wait` names no record | expected a record for `<id>`; found none; name a launched dispatch |

Runtime outcomes that are not refusals, and still produce a record:
`unsupervised` (no session stream under the isolated store within the
grace period, 120 seconds until measured, while the process lives →
terminated, `invalid`), `harness-cli`, `envelope-parse`, `timeout`,
`trip` — produced by launch itself, not delegated to `run.py`. An override
on any id not marked overridable, or with an empty reason, is itself refused.

### Collect

Write roles: the snapshot's `HEAD` must descend from `ref_sha`; it is
fetched into the invoking repository as `refs/dispatch/<id>` with
`--no-write-fetch-head`; `changed_paths` is `git diff --name-only
ref_sha head --` (the same endpoint semantics without an option-sensitive
record-sourced range); `residual_paths` is a human rendering derived from
`git status --porcelain=v1 -z` records in the snapshot. Read roles: `head`
must equal `ref_sha`; a non-empty
`residual_paths` is recorded; a changed head or non-empty residual makes the
envelope invalid.

Residual settlement exempts only declared cache artifacts: `.ruff_cache`
markers and extensionless keys at its root or under one numeric version
directory; pytest's three markers and its built-in `v/cache/{lastfailed,
nodeids,stepwise}` values; mypy's two markers, `missing_stubs`, and its named
JSON, fixed-format or SQLite artifacts under one numeric Python-version
directory; and `.pyc` files below `__pycache__`. Unknown layouts and source
files under any cache-named directory remain residual. The same classifier
selects both `residual_paths` and every path preserved in `residual.patch`,
including ignored files, binary files, deletions and both sides of renames;
there is no broader patch exclusion. These exemptions are a named residual
policy, not evidence that bytecode, caches, or other data written by a
same-UID worker are trustworthy, and not a strong filesystem-isolation
guarantee.

Git path authority remains NUL-delimited bytes through classification and
patch selection; C-quoted display is output only and is never parsed back.
Rename and copy records retain both source and destination. Before settlement
mutates an index, every path must round-trip through the filesystem encoding.
The temporary-index `git add` uses `--literal-pathspecs` plus `--`, so leading
dashes, wildcard characters, and `:(...)` syntax are filename data and cannot
select unchanged collateral paths. The invoking and snapshot indexes remain
untouched.

`collect ID --verify CMD [--timeout SECONDS]` is the recovery path for a
closed, uncollected write job. The existing residue arm requires residual
files and any tripped or non-zero attempt, including one later resumed
successfully with residue still outstanding. The committed arm prescribed
below also admits a preserved committed delta without residual files.
It runs the verification in the snapshot
with an isolated home and no ambient operator variables, under the recorded
wall or a larger explicit wall, verifies that `HEAD` still descends from
`ref_sha`, commits the residual with the
harness's message and running-model attribution, advances
`refs/dispatch/<id>`, retains `residual_patch`, and records
`result.collected {at, by, head, verify {command, exit: 0}, reason}`.
Every failed precondition leaves the snapshot commit, ref, and record
unchanged.

#### Preserved committed recovery — contract extension

Standing: implemented through the existing collect entrypoint; independent
focused RED-to-GREEN is retained in this branch. Full acceptance is pending.
Original source measured at `20f9bcaf2b3ae2152b9cab766637b5a89b71214b`:
`sed -n '1924,1975p' ops/devlane/dispatch/launch.py` shows `_settle` can
auto-commit Codex source with an invalid envelope; `sed -n '2168,2268p'
ops/devlane/dispatch/launch.py` shows `_collect` requires both a failed/tripped
attempt and residue. Process exit 0 is not proof that the retained work passed.

The additional arm requires a retained failed result: `result.envelope.status`
is `invalid` or `tripped`. An exit-0 attempt with an invalid envelope qualifies;
a successful envelope does not qualify through an earlier failed attempt.
Read jobs, running jobs, missing failure evidence and repeated collection refuse.
This does not narrow the existing residue arm's successful-resume case.

Before verification, resolve the existing record-bound snapshot and lineage;
require snapshot `HEAD` to equal recorded `result.head`, a strict descendant
of `snapshot.ref_sha`, and remeasure its nonempty committed tree delta against
that base. Recorded `changed_paths` must agree with that measured delta;
neither an asserted path list nor a different descendant supplies a candidate.
Require recorded and measured residuals to be empty for this additional arm;
mixed candidates continue through the existing residue arm. A commit with an
identical tree, including a commit/revert chain with no net delta, refuses.
Unavailable objects or evidence refuse rather than imply an empty pass.

Use the existing verification subprocess, isolated environment and timeout.
Recheck candidate identity, lineage and tree after verification; verification
cannot substitute another head or introduce source changes for this committed
arm. Nonzero exit, timeout or a failed recheck must not publish or advance
`result.head`, `refs/dispatch/<id>` or `result.collected`, or create a recovery
commit. Verification side effects remain unaccepted partial evidence, never
a reason to rewrite the original failure. Keep original attempt exits, trips,
envelope, cause and retained failure evidence immutable on both outcomes.

After explicit verification exits 0 and all rechecks pass, reuse the preserved
commit with its original attribution; no empty source commit or source replay.
Use the existing accepted recovery path to publish that exact head and append
`result.collected {at, by, head, verify {command, exit: 0}, reason}`, retaining
existing residual-patch evidence where present. No new command or record schema
is needed. Collection is recovery evidence, not independent review or permission
to land. The lane-policy checks before/after verification and before publication
declared below remain binding; this extension neither activates nor bypasses them.

Paired acceptance cases for a separate Grok test author use synthetic records
and repositories only; these are obligations, not executable tests or passes:

| accepted case | refusing neighbor / invariant |
|:--|:--|
| Closed write, exit 0, invalid envelope, exact recorded descendant with nonempty committed delta and no residue; explicit verification exits 0 | Same candidate with successful envelope, absent failure evidence, read role, running state or existing collected marker refuses before verification |
| Retained invalid/tripped result with exact committed candidate | Different descendant, off-lineage head, unavailable object, stale path list, base head, empty commit or net-zero commit/revert refuses without publication |
| Verification succeeds and candidate remains exact; collected head is the original source commit | Nonzero verification or timeout leaves head/ref/record/marker unchanged; changed head or source during verification refuses publication and preserves failed evidence |
| Existing failed/tripped residue recovery, including a successful resume with remaining residue, still verifies and commits with harness attribution | No residual and no eligible committed delta refuses; second collection refuses; original attempt/envelope/cause and residual patch remain intact |
| Both recovery arms satisfy the existing policy and independent review/land gates | Verification exit 0 or a collected marker cannot bypass a failing policy check, substitute for independent review or authorize landing |

### The record

One file per dispatch at `.dev/records/dispatches/<id>.json`, written
at launch with `status: launched` and no `result`, finalized at close,
and committed on the lineage branch as one commit containing only that
file — `git commit --only -- <path>` — with the message `dispatch:
record <id>`, `Source: generated: ops/devlane/dispatch/launch.py`, and
`Co-Authored-By: $WF_AGENT`. `record.py` builds and validates it, one
implementation per rule as `envelope.py` does; a record that fails
validation is never committed and the launcher exits non-zero naming
the field.

Worker fields, in this order (the conductor extension follows below):

```text
id, lane ("dev"), stage, unit, lineage {branch, base_sha}, follows [ids], answers [{key, disposition, disposed_at, citation {item, path}}],
job, role (read|write), dispatched_by, at {launched, closed},
snapshot {mode, ref_name, ref_sha, behind_tip, root},
harness {name, version, permissions?,
         isolation {mechanism, flags | env, home?, auth_files?, store,
                    observed {operator_config_present, evidence,
                              checked_at, harness_version}
                    | {unresolved}},
         sandbox, containment (os|policy), argv},
model {requested, effort_requested, effort_argv, ran, read_from},
session {id, stream, stream_sha256_at_close},
brief {template {path, sha256}, scope, inputs [{path, sha256}], sha256, bytes},
caps {..., source},
overrides [{refusal, reason, by}],
attempts [{n, launched, ended, exit, tripped}],
result {head, changed_paths, residual_paths, residual_patch?, envelope,
        collected? {at, by, head, verify {command, exit}, reason}},
status (launched|closed|died)
```

A record without an `answers` key was minted before the field landed; that
absence is historical and must not be read as "none answered".

- `model.requested` is the alias the owner chose; `model.ran` is what
  the harness's own stream says, read from `read_from`; with no stream
  it is `null` with a note, and never the alias copied over.
- `brief.sha256` is the digest of the exact bytes handed to the
  harness; `brief --check ID` re-renders from `jobs.json` at `ref_sha`,
  the recorded scope and the recorded input digests, and compares.
- `behind_tip` is `rev-list --count ref_sha..lineage`, a fact, not a
  judgement.
- `caps` come from `wires.py` for the role; a per-launch change is
  recorded with its source.
- `caps.timeout` (seconds) resolves in one order: `DISPATCH_TIMEOUT`
  from the invoking environment, else the job's own `caps.timeout` in
  `jobs.json`, else 900 — and `caps.timeout_source` names the layer
  that answered: `DISPATCH_TIMEOUT`, `job`, or `default`. The job's
  cap bounds the environment (the Lead's ruling, 2026-09-22 23:39Z,
  relayed by the XO): a `DISPATCH_TIMEOUT` above the job's
  `caps.timeout` is not admitted as given; the job's cap is applied and
  the source is `job`. At or below the cap, or on a job with no cap, it
  is admitted and the source is `DISPATCH_TIMEOUT`, so the environment
  can lower a job's cap and never raise it. `caps.timeout_requested`
  records the value `DISPATCH_TIMEOUT` asked for beside the one
  applied, in seconds, and is `null` when the variable is unset. A
  `DISPATCH_TIMEOUT` that is not a positive finite number of seconds is
  refused, class `timeout`, before anything is minted or spawned
  (20260923T000804Z-review-codex-da66dd#1 and #2). The job
  layer exists because runtime is a property of the job, not of the
  session that happens to launch it: author-tests proves every
  assertion red before green, its successful runs measure p50 928s
  against the flat 900s default, and five of seven wall-kills in the
  record store were that class, each launched bare after the session
  that knew the `DISPATCH_TIMEOUT` compensation had ended (finding:
  20260901T004909Z-tests-grok-3a7479).
- Every job in `jobs.json` declares `caps.timeout` 3600 (owner,
  2026-09-22, relayed by the Lead: "raise them to 60 mins - we're
  shooting ourselves in the foot"). The 1800s author-tests cap was
  measured killing live work that day: A-483 timed out keeping one
  commit, and record 20260922T231526Z-code-grok-312c63 was still
  committing red rows as its limit came near. A job that needs another
  value changes this line first. The task runner's direct jobs
  (`ops/devlane/task/run.py`; verify) are bounded by the same cap: a
  runtime timeout above the job's `caps.timeout` is not admitted, and a
  runtime that names none takes the cap
  (20260923T000804Z-review-codex-da66dd#3).
- `brief.coordination` is the acting plane beside that read plane
  (D-MCP-1, owner 2026-09-16: "mcp is no longer read only - that's the
  directive"). A codex dispatch that names an assignment receives three
  further `-c mcp_servers.coordination.*` overrides and a config at
  `<job>/coordination-mcp.json`, carrying exactly the eight keys the
  acting server declares: `version`, `participant` (the `PT-n` `bind-for`
  minted for this worker's session, never an agent string and never the
  dispatcher's own id), `assignment`, `branch`, `worktree`, `record` (the
  record id), `capabilities` and `issued_at`. `server` is the exact argv
  (`python3 -B <launcher-tree>/ops/conductor/tools/coordination_mcp.py --config
  <config> serve`). The capability list is declared in the launcher and
  proved against the server's own catalogue by a case, because the
  launcher reaches `ops/conductor` as a lever and never as an import.
  The brief names the verbs the worker holds: a registered server the
  brief never mentions is what the A-87 trial proved insufficient.
  Every absence is declared with its reason, and there are three --
  a harness this does not wire (`codex` alone today; claude and grok each
  record themselves by name), a launcher tree without the tool, and a dispatch
  that bound no participant. A relocated job has this config rebased with
  the lane one, so PR #158's defect does not recur in it.

  Interactive terminals and dispatched workers have different initialization.
  An interactive terminal self-binds through the shared coordination core
  using its own harness session; it needs no dispatch record or assignment.
  A dispatched worker is issued a participant by `bind-for`, and the normal
  `ops/bin/dispatch.sh --for A-n` wrapper carries that explicit assignment
  through to the launcher. The transport for terminal messages does not
  issue a worker binding or confer dispatch authority.

  `bind-for` commits the worker identity, credential enrollment and addressed
  offer together in the coordination core before the child starts. Dispatch
  does not perform a separate offer or choose assignment transitions. A core
  refusal is forwarded unchanged and removes this launch's fresh staging
  directory; no child or dispatch record is created. An offered assignment
  left by a later startup failure can use the core's existing `reassign`
  operation with its current generation and a reason before another dispatch.

  For every dispatch adapter, `session.binding_id` retains the lane session
  selected before launch; `session.id` separately records the harness's native
  conversation id used for resume. Reading a native stream may update only
  the latter. Every attempt receives the stable binding, including after
  relocation. A legacy bound record lacking `binding_id` recovers it from
  the exact stored `bind-for` receipt; an unreadable receipt refuses rather
  than substituting the native id. Unbound legacy records retain their old
  behavior. Parent harness session variables are removed from the child
  environment before the lane binding is set, regardless of the child
  adapter. Native interactive session startup is unaffected by that child
  environment operation. These guarantees do not imply acting-MCP support
  for adapters whose capability remains explicitly unavailable.

  An acting-MCP adapter must carry the issued binding and the same lane
  configuration and state root used to bind it. The current Codex adapter
  explicitly forwards `CLAUDE_SESSION_ID`, `LANE_CONF` and `XDG_STATE_HOME`
  through its server registration's `env_vars`; its native MCP transport
  otherwise filters these variables. `HOME` retains the client's default
  inheritance when XDG is unset. The shared core still selects the actor
  and state; the adapter supplies that context without granting authority.

- `brief.mcp` is the lane MCP handed to the worker (owner, 2026-09-12:
  "enforce usage - not doc it"): `config` is `<job>/lane-mcp.json`, a
  version-2 lane MCP config with `consumer` = the record id, `job_ids` =
  the `--follows` records then the newest records on the same unit (at
  most 20), `job_registry` = the launcher's jobs.json; `server` is the
  exact argv (`python3 -B <snapshot>/ops/conductor/tools/lane_mcp.py
  --config <config> serve`). Claude receives `--mcp-config <job>/mcp.json`
  and the allowed-tool rule `mcp__agentscient-lane` beside
  `--strict-mcp-config`; Codex receives two `-c mcp_servers.agentscient_lane.*`
  overrides; Grok reads `[mcp_servers.agentscient_lane]` from
  `$GROK_HOME/config.toml`, written into the isolated home and named in
  `brief.mcp.grok_config`. A snapshot without the tool records
  `brief.mcp = {"unresolved": "snapshot has no ops/conductor/tools/lane_mcp.py"}`
  and wires nothing. Measured live on 2026-09-12: all three harnesses
  answered `JOBS=9` through this wiring.
- Every prompt ends with `== what exists (measured at launch) ==`: the
  snapshot's levers with their first docstring line, skills, lane.conf
  keys, the job catalogue, the ideas index, the newest handoff's
  "Corrections to retain", the MCP server and its tools; each absent
  section says `<name>: absent`; the whole is cut at 8000 bytes with a
  marker. It closes with the reuse clause.
- Every settled record carries `result.proof` (owner, 2026-09-12: "not
  just proof at PR - proof for every dispatch"): `mcp_wired`, `mcp_calls`
  (the lane MCP calls counted from the worker's own store: claude
  `tool_use` named `mcp__agentscient-lane__*`, codex `McpToolCall` items
  on server `agentscient_lane`, grok `tool_calls` naming
  `agentscient_lane__*`), `mcp_tools`, `store`. A wired worker whose `ok`
  envelope shows no call is invalidated `framework-unused`; a store that
  cannot be read invalidates `framework-unproven` (could not look is not
  a pass). An unwired snapshot records `mcp_calls: null` and no gate.
- A write role's envelope declares reuse: `note` carries
  `reused: <what>` or `reused: nothing applied`. Settlement records it as
  `result.reuse`; an `ok` write envelope without it is invalidated with
  `reuse-undeclared`. Read roles are recorded without the field.
- `caps.turns` resolves the same way: `DISPATCH_TURNS` from the invoking
  environment, else the job's own `caps.turns` in `jobs.json`, else
  `null` — and `caps.turns_source` names the layer (`DISPATCH_TURNS`,
  `job`, `none`). `caps.turns_argv` is the exact argv carrier:
  `--max-turns N` on claude and grok; `[]` on codex and agy, whose CLIs
  name no turn flag (measured 2026-09-12), with `caps.turns_note`
  declaring that absence so the wall cap is known to govern. A
  non-positive or non-integer cap refuses `caps` on the wire (owner,
  2026-09-12: "hand grant - cap turns").
- `result.envelope.spend` is filled by the launcher from resolved
  `session.spend`; the harness's zero placeholder is never the record's
  measured figure. An unresolved spend begins `UNREACHABLE:` and names
  the store roots examined.

**The permitted delta to the invoking repository**, and nothing else:
one new file at the record path; one commit containing only it, on the
current branch, which equals `lineage.branch` at launch and at close;
for write roles, one new ref `refs/dispatch/<id>` and its reflog line.
The index and worktree are otherwise untouched, `FETCH_HEAD` is not
written, and every other ref is byte-identical before and after.

What makes the record expensive to fake after the fact — not
impossible, and the owner is the backstop: the brief digest re-derives;
`ref_sha` is a full object id that must resolve to its exact commit in the
bound snapshot and must be reachable from the branch; `result.head` must descend
from `ref_sha` and is a fetched commit with its own dates and trailers;
spend and `model.ran` must agree with a stream in the harness's own
store; and the record is a commit, so changing it is a diff.

### Canonical conductor record variant (proposed)

This extends the existing canonical execution service and record/store, not
the worker job catalogue. It awaits implementation and independent tests.
At `56125e6d04e6dcbe774206dcfd03b24b0406659d`, source inspection of
`launch.py` (`STAGES`, `_resume`, `--stage`) and `record.py` (`FIELDS`,
`validate`) shows worker-only stage/role and job lookup paths. Those paths
do not support this variant today. Updating one enumeration is insufficient.

The closed record union is selected by `role`: `read|write` is a worker;
`conductor` is a conductor. Worker bytes and existing job-role meanings stay
unchanged; actor role is `worker` in their retained TaskSelection. No top-level
worker `role` is renamed to `worker`. New bound workers retain TaskSelection
in LaunchDeclarations; legacy records without it remain historical/unbound.
The conductor has exactly the worker top-level keys listed above plus
`conductor`. All are required; `result` is explicitly null while launched.
The following overrides completely define the non-worker variant; other fields
keep their existing meanings and custody, including model/session observations,
supervision, lineage checks and record-only publication.

| field | conductor value and producer/consumer obligation |
|:--|:--|
| id / stage | reserved canonical DispatchID / literal `conduct`; e.g. `20260906T120000Z-conduct-codex-a1b2c3`; stamp, stage and harness must match |
| lane / unit / lineage / follows | infrastructure `dev`; nonempty unit; frozen `{branch,base_sha}`; explicit prior record ids (possibly `[]`), never a worker-stage dependency by implication |
| role / job | literal `conductor` / null; null means no worker job, not a missing catalogue entry |
| conductor | exactly `{descriptor,binding,declarations,custody}`, each a retained policy `{path,sha256}` handle; descriptor is conductor-launch/1, binding is policy's ConductorBinding, declarations is LaunchDeclarations, custody is the previously trusted caller evidence |
| brief | null; no task prompt/template lookup, task envelope schema or fabricated worker brief |
| snapshot | existing closed snapshot object, mode `whole`; selected ref and prospective canonical root held before materialization; root is a location, never proof of an existing snapshot |
| harness / model | existing objects; descriptor.selection.harness equals harness.name; model.requested/effort_requested equal descriptor selection; actual model still requires stream evidence |
| caps / overrides | existing supervision transport from admitted conductor configuration, with source and timeout provenance as defined below; no worker job/default-role lookup or conductor-to-write fallback; only already declared overridable refusals |
| attempts / at | existing attempt entries and UTC times; `[]` until an attempt is actually registered; at.launched is admitted record creation time, at.closed null until terminal |
| result | null while launched; at terminal exactly `{head,changed_paths,residual_paths,envelope,policy,status,note}`; envelope always null, policy is PolicyResult, status is `ok`, `invalid` or `tripped`, note null on success or a nonempty reason on failure |
| status | existing `launched`, `closed` or `died`; `died` is loss during an actual attempt, never a synonym for refusal before a child exists |

For this conductor variant, `caps.timeout` is a required finite positive number
of seconds resolved solely from admitted conductor configuration before the
canonical directory claim. `caps.timeout_source` is required and is exactly
`conductor-config`; it names that answering layer, not a worker job or default.
The existing supervision keys (including `cap-out` and `source`) remain present;
`source` names their admitted configuration source. Retain the exact source
bytes, ref and digest through the verified role source closure in
LaunchDeclarations and the canonical record's declarations handle. The token
alone is not provenance evidence. Missing, invalid or unverified configuration
refuses before effects; do not inherit ambient `DISPATCH_TIMEOUT`, consult
jobs.json or substitute 900 for a conductor. Resume revalidates that retained
source before any mutation; authorized cap changes retain their source and
the existing resume reason/override evidence. This adds no configuration store.

Worker timeout resolution and its closed `DISPATCH_TIMEOUT`, `job`, `default`
enum above are unchanged. `conductor-config` is admissible only when record.role
is `conductor`; the runtime/validator frontier below must enforce that split.

Complete synthetic caps objects for the representative conductor cases follow.
Here `source` identifies the admitted conductor configuration layer; the fixture
must retain its exact source evidence as above. These are caps examples, not
full records, runtime defaults or evidence of acceptance.

Success (attempt 1 registered and completed):

```json
{"cap-out":500000,"source":"conductor-config","timeout":1200.0,"timeout_source":"conductor-config"}
```

Late refusal after directory claim but before attempt registration (`attempts`
is `[]`): the already resolved caps remain complete, even if another preparation
handle is unavailable.

```json
{"cap-out":500000,"source":"conductor-config","timeout":1200.0,"timeout_source":"conductor-config"}
```

Pre-claim refusal creates no canonical record and therefore no caps object.
Neither omission of timeout keys in a retained record nor a fabricated attempt
can stand in for unavailable evidence.

Every conductor handle must verify before child invocation. If post-claim
preparation fails, an unretained handle is null only in the terminal refused
record; retain all obtained handles. Before a child, model.ran/read_from and
session.id/stream/stream_sha256_at_close are null with the existing unresolved
model note; do not manufacture observations. Terminal result.head is null only
if the candidate cannot be measured; changed_paths/residual_paths are null
only for unavailable measurement, distinct from measured `[]`. A successful
conductor requires a completed untripped attempt, verified connection/onboarding,
observed model, all handles and passing settlement; head equals snapshot.ref_sha
and both path lists are empty. Conductor source writes are forbidden. No task
verdict, auto-commit of source, collected marker or accepted dispatch ref is
produced. Authorized coordination/delegation uses its existing levers and grants;
it does not confer direct source/output writes on the conductor.

Selection has one producer: TaskSelection for workers, LaunchSelection for
conductors. All six members are closed; role/job_role/profile are never derived
from stage, lane, harness or a null job. Conductor descriptor selection equals
ConductorBinding.selection and LaunchDeclarations.selection byte-for-byte under
canonical encoding. Worker job_role equals record.role and its admitted job;
conductor job_role is null and record.job is null. Resume preserves these
equalities, the same id and stage, and the original source identities.

Every closed consumer must implement this union or explicitly refuse the
unsupported variant before effects. This is the required implementation frontier:

| consumer | required handling |
|:--|:--|
| launch / record build, validate, validate_context, relocation | branch on the record variant before job lookup, stage defaults, rendering or snapshot/role checks; retain all conductor keys; validate descriptor/binding/id equality and nullable preparation evidence |
| dispatch/go / ladder stage specs and selection checks | remain worker frontends with the six worker stages; reject `conduct` and null job; a conductor may invoke them to create distinct worker records |
| conduct / prepare_conductor / compile_launch | supply `conduct`, null job and LaunchSelection through trusted in-process transport; use the same launch service, no new executor or direct worker CLI flag |
| harness isolation, caps, argv, stream readers and resume | consume explicit actor role and nullable job_role; require conductor-specific admitted configuration/witness, never fall through a read/write branch or use jobs[null]; preserve requested/observed model separation |
| task rendering / envelope validation | worker-only; no conductor envelope enters either; task may validate the shared C2/declaration structure without creating a job |
| policy bind/check / tool evaluator | consume ConductorBinding for this variant, unchanged worker PolicyBinding otherwise; evaluate_tool receives its verified descriptor handle; no per-job maintenance grant for a null job |
| status / wait / close / resume / brief --check | read both variants; terminal conductor status comes from result.status and policy, not result.envelope; wait uses the existing 0/1/2 semantics; close preserves partials; resume uses conductor configuration and rechecks MCP; brief check verifies retained startup template/descriptor bytes, never jobs.json |
| collect / land / ladder acceptance | require a worker of the appropriate job-role/stage before verification, fetch-for-acceptance, cherry-pick or advance; refuse a conductor even if its terminal status is ok; no synthesized worker verdict |
| lane_read/lane_mcp/discovery, findings, monitor and worth | retain canonical id, stage, nullable job and conductor role as observations; never coerce to read/write or manufacture envelope counts/verdict; worker-only summaries exclude it explicitly or report unsupported/unavailable within their existing closed projection |

Existing read projections keep their schemas and v1/v2 bytes. lane_read's
read/write job-catalogue predicate remains worker-only; a conductor is not a
dispatchable catalogue row. Record summaries that cannot represent the variant
report unsupported/unavailable, never worker success. This contract neither
claims current variant acceptance nor adds MCP fields.
Old records and captures are neither rewritten nor backfilled at cutover.

### Watching, and picking up after a kill

The launcher prints `record: <id>` as soon as the launched record is
written. `state.json` carries `pid`, `pgid`, the
session id, the stream path and the attempt number; `exit` is written
last. `status` reports `running` (pid alive), `finished` (exit file),
`tripped` (`TRIPPED.md`), **`DIED`** (pid gone and no exit file — never
reported as finished), `unlaunched` (refused before launch). `close ID`
finalizes and commits a DIED job's record as `invalid`.

`wait ID` polls that record until `closed` or `died`, writes poll lines
to stderr, and prints one terminal summary. A clean, untripped, schema-
valid close exits 0; another terminal result exits 1; the wait wall
exits 2.

`resume ID` relaunches in the same snapshot directory — grok scopes
sessions by cwd — with `claude -p -r <id>`, `grok -r <id>`, or `codex
exec resume <id>`, clears the previous exit and trip markers before
registering, appends output rather than truncating it, re-enters the
inline supervisor, and appends an `attempts` entry. A tripped job
resumes only with a changed cap or a stated reason, recorded.
Before a codex or grok resume the archived session store is restored to
the harness home, then moved forward again after the attempt. A failed
resume cannot downgrade the prior stream, model, spend, or cause, and
every completed attempt commits the updated record.

### Proposed lane-policy coupling — inert until complete activation

[Lane policy](../../conductor/lane-policy.md) owns the profile ceiling,
protected authority, dependency exclusions and policy acceptance requirements;
its [proposed data](../../lane-policy.json) lives at the fixed tracked path
`ops/lane-policy.json`, bound to an owner-admitted Git ref and byte digest.
Environment, laneconf, candidate output and prose cannot relocate authority
or reclassify work. Launch/task retains ownership of job output, requested
scope, role and adapter declarations. Policy consumes outputs if present as
an intersection term; absence adds no root and never falls back to profile
roots. A read role retains an immutable snapshot, even with declared,
adapter-isolated external `out/` writes. No jobs schema is introduced here.

Future records bind nested `brief.policy` and launcher-produced `result.policy`
as specified by that contract; `lane` and `WF_LANE` stay infrastructure markers.
The minimum complete activation checks fresh launch, settlement (including
write-resume settlement), write-resume before attempt/marker/store mutations,
and collect both before and after verification, before commit/ref publication.
It consumes existing `_residual_entries`, including ignored-file enumeration
and the existing narrow cache exemptions. Land and ladder must independently
refuse missing, mismatched or failed policy evidence before acceptance, even
for collected results. Worker verdict, envelope status, process exit 0 and
collect verification success confer no exception. New `lane-*` refusals follow
the shared wire's exit 2 and expected/found/needed line, without override.

Policy/instruction maintenance requires the true maintenance boolean and bounded
standing/per-job evidence under the previously admitted policy. Existing standing
owner authorization binds subsequent jobs without another owner act. Admission
and grant digests cover exact existing-register `show` stdout, read from the host
store by the admitted reader/runtime and configuration, never candidate copies
or the worker HOME. Candidate policy, reader, config and seed bytes cannot
self-authorize. Bootstrap binds independently verified policy, consumer and
reader/runtime custody. Existing records retain their bytes and historical
status, without backfill or a claim that old sessions were enforced. After
cutover unbound work cannot write-resume, collect for acceptance, land or advance
a ladder; continuing it requires a fresh bound dispatch with retained provenance.
Partial deployment is not activation. This section is a contract/data proposal,
not a live gate or stronger harness containment claim; executable consumers and
independent tests follow. Existing unrelated dispatch behavior remains as above.

### Unit1a pre-effect admission

Adoption unit 1a completes this declaration through task's
[LaunchDeclarations](../task/CONTRACT.md#unit1a-declaration-transport) and policy's
[bind_launch/check_transition](../../conductor/lane-policy.md#trusted-binding-input-and-declaration-transport).
Dispatch/go/ladder transfer the same trusted structured selection, requested
scope, declarations and source bytes to the canonical service, by direct
in-process arguments or an inherited read-only descriptor from the admitted
consumer. No new public flags, scope-prose parser or environment policy switch
is introduced here. Candidate launcher pins/config/imports cannot substitute
for the admitted consumer. Task stays a declaration/semantic provider; policy
stays a consume-only stdlib dependency of dispatch, with no reverse import.
These are target couplings, not claims of current runtime wiring.

Resolve explicit model/effort, harness, worker/job role, profile selection,
source ref and actual lineage first; retain their configuration provenance.
The admitted caller freezes the lineage branch and full base_sha for the job;
recheck that tip before creating durable launch state. Resolve policy's default
only when both explicit and structured profile selections are absent. Empty,
unknown or conflicting values refuse, never fall back or infer from record.lane.
Conductor startup uses its separately declared LaunchSelection and the same
canonical DispatchID reservation duty, without inventing a worker job binding.
It supplies stage `conduct`, role `conductor`, job null and the
[canonical conductor variant](#canonical-conductor-record-variant-proposed).

The existing `mint_id(stage,harness)` helper supplies the canonical
`<UTC stamp>-<stage>-<harness>-<6 hex>` value in memory, before validating per-job
maintenance evidence. A trusted caller may reserve it first to obtain job-bound
evidence and pass that exact value through the held context; validate real UTC,
stage/harness equality, six lowercase hex characters and the entire spelling.
No separate reservation verb, record, lock file, ref or store is created.
Omission mints once; a supplied id is never silently reminted. Per-job evidence
must match this id, job and frozen lineage; standing coverage is derived from
unchanged existing authorization without a fresh owner act.

Before any filesystem/attempt effects, verify old authority and reader custody,
selection, task declarations and source digests, structured scope, job/role/
adapter/output intersections, known dependency evidence and current capability
witnesses. Hold canonical prospective paths and manifests in memory; inspect
existing ancestors without creating them. Validate task transport collisions
and C2 path/handle representability. No snapshot, prompt, record, output leaf,
home, marker, session, attempt entry or child may be minted during these checks.
This ordering resolves job-bound evidence without permitting speculative state.

Check existing job-directory, record-path and dispatch-ref collisions. After
all preflight checks pass, atomically claim the canonical job directory for the
reserved id using the existing launcher owner; a collision refuses lane-policy
before an attempt and never reuses another job's state. Recheck held authority,
lineage and path custody at the claim boundary. In-memory reservation alone is
not a cross-process lock. A racing collision on another destination stops this
launch, preserves others' bytes and retains only its own post-claim diagnostics
under the late-failure rule; it cannot overwrite a record/ref or retry with a
new id. Prepare admitted transport and publish the admitted launch acknowledgement
only after the checks succeed. No read-only status/doctor path requires admission.

Resume revalidates the original id, binding, sources, declarations, roots,
artifact/dependency evidence and a fresh operation-specific capability witness
before clearing markers, altering caps/attempt count, restoring stores or making
output destinations writable. Relocation rebinds relative output names under
the verified current job root using C2 custody; it never widens declarations or
changes selection. Failure preserves prior attempt history and receipts. Legacy
unbound records remain readable and require a fresh admitted dispatch to continue.

### Unit1a attempt and result transport

`record.py` owns record shape/serialization; launch alone produces trusted
receipts, using task's unchanged semantic acceptance. Preserve existing
brief.policy and template digest fields. Retain normalized consumed grants,
source bytes/refs/digests and the original declaration array beside the canonical
record in existing launcher transport, outside snapshot and worker-writable out.
Retained handles use policy's exact `{path,sha256}` encoding and custody rules;
locations alone cannot establish trusted provenance. No new state namespace,
registry or worker-writable authority file is introduced.

OutputAttempt is exactly `{dispatch,attempt,files}`. dispatch equals the reserved
canonical id; attempt is null until an attempt is registered, even after the
job-directory claim or snapshot preparation, and otherwise the positive existing
`attempts[].n`; files is empty before an attempt exists. Each file receipt is
exactly `{artifact,path,sha256,bytes,production,reuse,evidence}`. artifact and path
match the original C2 file declaration (path is relative to canonical out),
sha256 hashes exact accepted file bytes, bytes is a positive integer, production
is `fresh` or `reuse`, reuse is null for fresh or exactly `{attempt,receipt}` for
reuse, with a strictly earlier positive attempt and a digest-backed prior receipt
handle. evidence is a nonempty array of verified retained handles proving current
production or checked reuse. Files retain declaration order and unique artifact/
path identities; accepted completion requires every declared file exactly once.
Scratch creates no report entry and is not automatically published. A refused
result may retain the subset actually verified, explicitly non-accepting.

A current attempt's explicit reuse input is exactly
`{dispatch,attempt,files:[{artifact,path,from_attempt,receipt},...]}`. It is supplied
through the same trusted per-attempt transport, with unique declared artifact/
path pairs and from_attempt strictly less than attempt; omission means no reuse.
It identifies intention only, never a grant or custody proof. No envelope field
is added. The trusted caller can relay a reuse request but cannot turn a model's
claimed receipt into launcher-owned evidence. Dispatch verifies the retained
prior receipt, dispatch id, declaration, prior bytes/digest and current destination
type/containment/links; it records reuse only if all match. Foreign jobs, future
attempts, stale digests and unverified leftovers refuse outputs-custody.

After child writes stop, accept outputs under C2. Before a subsequent admitted
attempt can access destinations, retain previous report bytes and their complete
receipts/digests outside worker writes. Fresh production needs launcher-observed
current-attempt evidence; matching old bytes or mtime alone is insufficient.
An unchanged prior report satisfies completion only through explicit verified
reuse. Recheck exact decoded envelope handles and read-role HEAD/residuals in
addition to output custody. Keep original response bytes and all earlier attempt
receipts; do not overwrite history to turn a failed attempt into a pass.

`result.policy` is exactly policy's
[PolicyResult](../../conductor/lane-policy.md#candidate-evidence-and-policyresult).
Dispatch retains the exact canonical ArtifactManifest, DependencyEvidence and
OutputAttempt bytes named by its handles. Include committed delta, index/worktree
residuals including ignored files, binary paths/modes and both rename endpoints;
unknown/unavailable evidence refuses, never supplies an empty pass. The admitted
analyzer supplies dependency coverage/source/target-root evidence bound to this
candidate manifest; source inspection citations are not runtime edges. Missing
required coverage or unresolved dependencies prevents acceptance. Worker status,
verdict, receipt or process exit cannot replace independent rederivation.

Prelaunch check_transition uses held evidence before any state creation, then
retains the admitted launch receipt. Settle/close checks before automatic commit,
then binds the resulting head before accepted ref publication. Write-resume
checks before effects and runs full settlement afterward. Collect checks before
and after verification, and remeasures any resulting commit before ref/collected
marker mutation. Land/ladder independently check exact fetched/result head,
lineage, binding and retained evidence before cherry-pick/stage advance. Preserve
all existing non-policy gates and failed-collect record/ref guarantees; a failed
collect retains a separate refused diagnostic receipt without rewriting its
prior result. No accepted transition can rely solely on a previously collected
marker, successful verification or an unverified/stale result.policy.

The [policy timing rule](../../conductor/lane-policy.md#pre-effect-rejection-and-late-refusal)
controls new lane-* failures. Before acting, exit 2 with empty stdout and one
refusal line; from the successful job-directory claim (or the first resume/
verification mutation) onward, retain partials and the stopped
reading, serialize refused policy evidence, and return Abort 3. Invalid/null
verdict names the refusal in note; existing tripped results remain tripped.
With no worker response, result.envelope is null and refused result.policy is
the terminal non-acceptance; no synthetic task envelope or attempt is required.
Worker result keeps its existing members plus policy; head and measured path
lists may be null only when unavailable on refusal. Status/wait/close and all
acceptance consumers must handle this partial worker variant before envelope
access. OutputAttempt may be `{dispatch,attempt:null,files:[]}` if those bytes
were retained; otherwise PolicyResult.outputs is null. Never name an uncreated
receipt as retained. Conductor terminal status follows its variant above.
For a terminal partial worker with null envelope, wait returns 1 and close
retains non-acceptance; land/collect/ladder cannot infer success from exit 0.
Do not erase emitted output, publish an accepted ref or commit on a refused
check. A post-commit recheck can preserve an already created local candidate
commit as partial evidence but cannot publish it as accepted. Detached callers
read the terminal outcome from the canonical record. No permission prompt,
override or automatic harness substitution may convert refusal into acceptance.

Independent cases cover pre-effect source/selection/grant/dependency checks,
in-memory id reservation and racing collisions, per-job identity mismatch,
resume timing and relocation, retained output/reuse custody, both collect checks,
late serialization and all acceptance consumers. The policy's P/N/R/I cases
and C1/C2 acceptance requirements still apply. These declarations neither author
those tests nor activate any admission; complete cutover remains separately
owner-admitted and dev tooling remains independent of the Symfony application.

### Lane

Current ownership and precedence are defined by
[AGENTS.md §Lane boundary and precedence](../../../AGENTS.md#lane-boundary-and-precedence)
and [ops/README.md](../../README.md#lane-boundary-and-precedence).
The launcher is dev tooling and must operate without the Symfony application
or its vendor directory. Its work profile is independent of harness and
conductor/worker role; using it to dispatch prod application work does not
change its ownership or retire it in favor of an application launcher.
The legacy `lane: "dev"` and `WF_LANE=dev` below describe the apparatus,
not a classification of every job's authorized work. Briefs, resumed jobs
and accepted artifacts remain subject to the current boundary; this
documentation correction adds no profile field or enforcement mechanism.

Everything here is dev-lane: the launcher code under
`ops/devlane/dispatch/`, the records
under `.dev/records/`, `lane: "dev"` in every record,
`WF_LANE=dev` in every child, and a refusal to record on `main`. The
application neither implements nor retires these permanent tools. Profile
policy is consumed from the parallel boundary track as declared in C1;
application authority documents are outside this change.
