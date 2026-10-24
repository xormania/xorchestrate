# Behavioral verification and native handoff

The implementation brief supplied by xor remains the requirement basis: H1–H8,
B1–B5 and A1–A2. The tools here measure a selected candidate. Their source and
criteria must be reviewed; hashes establish identity, not correctness or owner
approval. A fresh independent review remains pending after implementation tests.

## What the runnable suite measures

`scripts.verification.multi_harness` uses the active composition, application
actions, routing, SQLite store, durable delivery attempts, receipt policy and
native callbacks. It does not run the imported Agentscient runtime or standalone
PoC. Its fixed default inventory is a verification fixture, not a product role or
organization restriction.

| Requested cases | Count | Evidence required |
|---|---:|---|
| Private-memory startup | 3 | Native call and persisted ready report for each persistent conversation |
| Directed exchange | 6 | Native challenge callback, durable delivery attempt/submission, retained-memory reply callback, reverse delivery, native sender receipt |
| Outstanding-tool exchange | 3 | Submission inside a held provider callback followed by the complete exchange |
| Unfinished-work exchange | 6 | Complete exchange with submission and reply before later work batches finish |
| Work batches | 9 | Three independently scored latest-attempt build-log batches for each harness |
| Native turn completion | 3 | Matching native completion observations after submissions |
| Lifecycle | 15 | Resume, external attachment, detach, reconnect and replaced connection for each harness; native retained-memory probes and stale-binding rejection |
| Native-call audit | 1 | Every actual application callback matches native action, arguments, conversation, incarnation and call identity |

Busy submission does not assert interruption during token generation. Reports
retain acknowledgement-after-reply timing. Unfinished-work cases require the
reply before work completion; held-tool cases require submission during the hold
and permit the reply after release. Communication, work correctness, native completion and application
work acceptance are different facts. A delivery receipt never accepts a work
item; work acceptance is covered by the separate application acceptance tests.

The checker recomputes exchange and lifecycle predicates from retained raw
application records. Candidate-authored verdicts, copied totals, missing cases,
another candidate's bindings and lifecycle summary booleans cannot substitute
for those records. Requested, discovered and executed inventories remain separate.

## Reproducible deterministic checks

Run from a candidate checkout with Python 3.11 or newer and its declared test
dependencies installed:

```sh
python -m pip install -e '.[test]'
python -m pytest -q tests/hexagonal
```

Keep the original owner brief and generated criteria outside that checkout. Use
the original attached brief where available; the retained repository copy can
be compared byte-for-byte before selecting it for a measurement. Owner changes
create a revised basis and require affected measurements to be rerun.

```sh
export VERIFY_ROOT=/absolute/path/to/private-verification
mkdir -p "$VERIFY_ROOT"
cp /absolute/path/to/owner-approved-brief.md "$VERIFY_ROOT/owner-brief.md"
python -m scripts.verification criteria \
  --brief "$VERIFY_ROOT/owner-brief.md" --out "$VERIFY_ROOT/criteria.json"
python -m scripts.verification.multi_harness \
  --mode protocol-fixture \
  --brief "$VERIFY_ROOT/owner-brief.md" --criteria "$VERIFY_ROOT/criteria.json" \
  --out "$VERIFY_ROOT/protocol-run" --timeout 60
```

`protocol-fixture` launches real Codex/Claude/Grok **adapter implementations**
against deterministic subprocess protocol peers. It exercises their native wire
paths, application callbacks, durable delivery and independent external-host
lifecycle through the same suite. It records `native_acceptance=pending_local_run`.
`fixture` is a faster synthetic session-port composition check and is separately
labeled; it does not claim vendor protocol coverage. Neither mode launches a
vendor model or establishes authenticated native behavior.

Each run directory must be new. Failed attempts remain inspectable and are not
overwritten. Candidate inputs must remain unchanged during a run; changes make
the result `invalid_candidate`, even if its individual predicates passed.

## Authenticated native run, executed locally by xor

Prerequisites:

1. Install and authenticate Codex, Claude Code and Grok Build locally. The runner
   records each selected executable's actual `--version` command and output.
2. Select a private isolated working directory, retain the owner brief/criteria
   outside the candidate, and install the candidate's Python dependencies.
3. Copy `scripts/verification/native-config.example.json` outside the candidate.
   Replace all workspace paths and choose locally available model/provider/effort
   settings. No model is hardwired by the product or this suite.
4. Run from a terminal suitable for Claude's interactive channel startup. Enable
   its development channel when the native CLI asks. Channel registration and
   SessionStart/PreToolUse/Stop hooks must actually be observed; a missing hook
   remains a failure. Native binaries must be compatible with the implemented
   Codex `turn/start.toolOutput`, Claude channel notification and Grok
   `session/prompt`/`_x.ai/interject` paths.

The example's tool permissions are one explicit fixture configuration. Native
permissions, provider selection, process ownership and scheduling are independent
product choices. Configure them deliberately; the example does not impose its
allowlist on other deployments. Keep authentication in native configuration or
explicit inherited environment, not in shared JSON evidence.

For Grok, select model and effort using its executable arguments when supported,
for example `["grok", "--model", "YOUR_MODEL", "--effort", "YOUR_EFFORT",
"agent", "stdio"]`. Do not assume arbitrary `session/new` fields configure the
vendor. Codex thread parameters and Claude launch settings remain explicit in
their respective adapter options. Requested, resolved and native-observed
settings are retained separately; unavailable observations remain unknown.

```sh
python -m scripts.verification.multi_harness \
  --mode native --config "$VERIFY_ROOT/native-config.json" \
  --brief "$VERIFY_ROOT/owner-brief.md" --criteria "$VERIFY_ROOT/criteria.json" \
  --out "$VERIFY_ROOT/native-run" --timeout 180
```

This command launches fresh persistent native sessions and then separate native
hosts for lifecycle cases. The application detaches from externally owned host
processes without terminating them. The test runner owns the hosts it explicitly
launched and terminates them during final cleanup; it never terminates unrelated
existing native sessions. Retained-memory probes omit the memory from their
prompts. Reconnection must produce a new connection incarnation and preserve the
native conversation identity. A required unsupported resume/attachment interface
is reported as incomplete, not treated as an excluded harness or a pass.

The runner releases outstanding holds before closing owned drivers. Per-host
cleanup records include process status and endpoint removal. `cleanup.json`
records the overall cleanup result. Interruptions, launch failures, unknown
effects and native errors remain in the run; investigate them before rerunning
into a new directory.

## Independent checker and exact candidate

Use a separately selected checker checkout when reviewing candidate changes:

```sh
cd /absolute/path/to/reviewed-checker-checkout
python -m scripts.verification.multi_harness \
  --candidate /absolute/path/to/candidate \
  --mode protocol-fixture \
  --brief "$VERIFY_ROOT/owner-brief.md" --criteria "$VERIFY_ROOT/criteria.json" \
  --out "$VERIFY_ROOT/independent-protocol-run" --timeout 60
```

Product imports are deferred until the candidate path is selected. The manifest
records imported product module paths and hashes and rejects product modules
outside that candidate. The separately loaded checker package remains in the
reviewed checkout. The two-candidate regression proves that changing
`--candidate` changes the product loaded, rather than merely its reported hash.

The run stores `candidate.json`, `checker.json`, `bindings.json`, `criteria.json`,
`fixture.json`, `configuration.json`, `run.json`, `report.json`,
`candidate-after.json`, version command logs, SQLite state and lifecycle records.
It identifies tracked, deleted and nonignored untracked inputs. Generated run
output lives outside the candidate and is not folded into its measured hash.
Review ignored configuration paths separately if they are supplied as run inputs;
the selected configuration and fixture are explicitly hashed and copied.

For a recoverable dirty-tree snapshot:

```sh
python -m scripts.verification capture \
  --candidate /absolute/path/to/candidate --out "$VERIFY_ROOT/candidate-snapshot"
```

The snapshot includes content, modes/symlinks, deleted paths and a binary working
patch. A bare commit ID is never substituted for dirty or untracked code.

To independently recompute a retained report, select the original expected
bindings from the measured candidate/configuration inventory, not an arbitrary
claimed success summary:

```sh
python -m scripts.verification check \
  --run "$VERIFY_ROOT/native-run/run.json" \
  --criteria "$VERIFY_ROOT/criteria.json" \
  --bindings "$VERIFY_ROOT/native-run/bindings.json" \
  --fixture "$VERIFY_ROOT/native-run/fixture.json" \
  --out "$VERIFY_ROOT/independent-native-check.json"
```

Inspect the raw records and selected inputs, compare checker changes and rerun
measurements. Rechecking a saved file cannot independently establish that the
file's native observations were honestly collected. Full behavioral acceptance
requires the native run and the separate fresh-session review.

## Targeted calibration

`scripts.verification calibrate` copies the exact candidate inputs into separate
control and mutant directories, applies one exact source replacement, runs the
same selected pytest assertions in both, and retains raw output/JUnit plus the
mutation. `--target` and `--unaffected` are JUnit identities, commonly
`tests.hexagonal.test_module::test_name`; selectors are pytest paths.

```sh
python -m scripts.verification calibrate \
  --candidate /absolute/path/to/candidate --out "$VERIFY_ROOT/calibration" \
  --mutation "$VERIFY_ROOT/mutation.json" \
  --selector tests/hexagonal/test_module.py::test_target \
  --selector tests/hexagonal/test_module.py::test_control \
  --target tests.hexagonal.test_module::test_target \
  --diagnostic EXACT_ASSERTION_MARKER \
  --unaffected tests.hexagonal.test_module::test_control
```

The mutation JSON contains `path`, `old` and `new`; `old` must match exactly once.
Only a passing control followed by the intended failed assertion, matching
diagnostic, correct failure exit and passing unaffected controls earns
`detected`. Setup errors, unrelated failures, skipped assertions, missing cases,
invalid controls and escaped faults are distinct outcomes. Calibration of the
calibrator covers missing dependencies, timeouts, skips and wrong diagnostics.

## Privacy, provenance and limits

Run directories and raw JSON/logs are private. Native prompts, private markers,
paths and configuration may be sensitive. Keep raw traces private; prepare
redacted derivatives with original and derivative hashes, redaction description
and source paths before sharing. A redacted report alone cannot replace raw
native evidence needed by an authorized verifier. No fixed historical call count
is required; every actual application callback is audited.

Reused mechanisms and inspected source:

- Multi-harness proof `a944f31b7527807d000da985c546e8b5bc9ff476`:
  `mhproof/suite.py`, `mhproof/evidence.py`, `mhproof/workload.py` and
  `fixtures/build-log.json`. The exchange predicate, distinct work scoring,
  outstanding-tool timing and gated unfinished-work protocol are adapted here.
- Agentscient acceptance fixture
  `7d1abb3c4ac01451d83e1c1e12742b9fa2067561`: `acceptance/product/README.md`,
  `acceptance/tests/test_product.py` lines 85–145 and `acceptance/verify.py`
  lines 87–107 and 910–977. Reused principles: separately selected checker,
  exact measured candidate, requested coverage and assertion-specific faults
  with successful unaffected setup/control. Historical merge policy and product
  allowlists are not adopted as Xorchestrate requirements.

Implementation, deterministic verification, native verification and independent
review remain separate states in the implementation handoff matrix. A passed
deterministic suite establishes only the declared deterministic coverage.


## Focused native repair schedules

After the deterministic [F01–F10 repair](repair/EXECUTION.md), run these focused
schedules against installed vendors in addition to the normal 46 cases. Retain
the same exact candidate/checker/configuration and raw observation bindings. The
protocol-peer regressions are executable references, not installed-vendor proof.

- **Acceptance replay (Codex, Claude, Grok):** create passing acceptance, record
  the successful native call ID and request, explicitly revise owner requirements,
  then replay the same semantic call (new transport request ID for Codex). Retain
  direct and fresh-call controls. The old cached response must be rejected as stale
  and the side-effect ledger must show no reexecution. Repeat after material
  evaluator context/evidence changes.
- **Overlapping Claude work:** hold distinct A/B callbacks through the actual
  provider, release A, and retain its actual Stop hook. Inspect status while B is
  still gated; it must remain active. Release B and then establish idle. Keep
  hook correlation and native version/ordering; an installed version that cannot
  produce the schedule leaves this measurement pending, not passed.
- **Observed callback across reconnect:** gate dispatch after the native
  observation, replace the application connection with a differently authorized
  actor/work, and release the callback. Its old binding must be retained or the
  call rejected; it must never execute with replacement authority. Include the
  no-replacement denial control and inspect both works' persisted evidence.
- **Applied effect across reconnect:** use a countable provider effect, gate its
  response after the effect, reconnect, and attempt another send. The unresolved
  operation must stay fenced after releasing the old response. Poll/recovery
  must expose the retained result/uncertainty; explicitly reconcile before a new
  effect and count actual effects. Do not interpret connection error as proof of
  non-execution or as an exactly-once guarantee.

Use private stores/endpoints and event gates, with ownership-aware cleanup;
never substitute arbitrary sleeps for the required causal ordering. Native
credentials and private traces stay local. Report each unexecuted focused case
as pending separately from the original 46-case inventory.
