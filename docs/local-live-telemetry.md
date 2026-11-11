# Local live tests with retained telemetry

Use the existing managed proof runner to own the tmux worker/dashboard, native
sessions, cancellation and evidence. That tmux worker is the test kit's
supervisor, not the product [terminal host](terminal-host.md). It runs your installed harness through the
product adapters. It preserves your selected native configuration and ordinary
authentication; a native failure does not select a fixture peer or another
model. The [managed lifecycle](managed-proofs.md) and [story catalog](local-stories.md)
remain the execution and verdict owners. [Source: operator composition](../scripts/testkit/operator_composition.py).

## Set up one project

From the checkout, select an external profile and the actual project directory:

```bash
# Use Python 3.12+ with the collector-required SQLite WAL fix.
# If your default Python is unsuitable, prefix setup with PROOF_PYTHON=/path/to/python.
./scripts/proof.sh setup \
  --profile "$HOME/.config/xorchestrate/live-python.json" \
  --mode native --suite stories --story native-callback --driver codex \
  --project /absolute/path/to/project --language python
./scripts/proof.sh doctor --profile "$HOME/.config/xorchestrate/live-python.json"
```

The language selection controls prerequisite defaults and the project controls
the working directory for built-in native/provider adapters. It does not impose
a framework or install project dependencies. Choose Rust or PHP with the same
command and a different `--language`:

| Language | Default project prerequisites |
|---|---|
| `rust` | `rustc`, `cargo` |
| `python` | `python3` |
| `php` | `php`, `composer` |

For a custom toolchain, the profile accepts
`"project": {"path": "/absolute/project", "language": "rust", "requires": ["./tools/check-ready"]}`.
An explicit `requires` list replaces the language defaults; an empty list is
permitted. Relative executable paths resolve from that project. These are
readiness observations, not project assertions or a new executor.
[Source: profile selection and doctor](../scripts/testkit/adapters/profiles.py).

Setup creates the external Python environment, a private native configuration
template and an evidence root beside the profile. Local telemetry is enabled in
new native templates. Setup installs one dependency set, which includes the
collector, and preserves existing native configuration bytes. Explicit disabled
capture stays disabled. When local capture is selected, doctor checks the
selected interpreter's actual SQLite compatibility (`telemetry_sqlite`);
it does not log into vendors, launch a model or establish authenticated
acceptance. It does report whether the evidence root is a location the product
accepts for a private home (`run_location`). Add `--probe` to take each selected
driver through session start and stop, without a turn, before spending a model
call on a run; see [managed setup](managed-proofs.md#set-up-once). A fixed SQLite runtime such as uv Python 3.12.16 can be selected
before setup. [Source: setup/doctor](../scripts/testkit/adapters/profiles.py).

Inspect the profile's `config` path and configure the existing
[native JSON fields](../scripts/verification/native-config.example.json) before
starting. Select the installed client with `--driver codex`, `--driver claude`
or `--driver grok`; repeat that option for a deliberate multi-harness selection.
Leave model settings at the client's existing choice unless you want a specific
override. Supply only the environment forwarding that the selected adapter
requires. Setup never writes vendor authentication or global model settings.
[Source: native configuration template](../scripts/verification/native-config.example.json).

The `native-callback` story is a bounded native communication/callback test. It
is not a coding assignment or proof that a Rust, Python or PHP application is
correct. Select the existing story/check workload and its independent assertions
for the claim you intend to establish. The substantive Symfony fixture is an
optional separate workload, not a requirement of this profile.
[Sources: story scope](local-stories.md), [substantive native witness](telemetry.md#native-qualification-and-first-workload).

## Start, attend and repeat

```bash
./scripts/proof.sh start --profile "$HOME/.config/xorchestrate/live-python.json" --detach
# Use the exact RUN_DIRECTORY printed above for all remaining controls.
./scripts/proof.sh status RUN_DIRECTORY
./scripts/proof.sh watch RUN_DIRECTORY
./scripts/proof.sh attach RUN_DIRECTORY
./scripts/proof.sh wait RUN_DIRECTORY
./scripts/proof.sh verify RUN_DIRECTORY
```

The managed supervisor owns a private tmux worker/dashboard and records owned
resource identities. Watch follows saved progress; leaving a viewer does not
stop the run. Attach rechecks ownership and process incarnation. In native mode
it waits for an actual owned native PTY, rather than presenting the worker's
progress pane as an approval prompt. Claude's existing managed PTY can expose
its native prompts; detach with **Ctrl-]**. A tmux terminal uses its normal
**Ctrl-b d** binding. Codex app-server and Grok protocol pipes are not native TUI
approval windows: where a driver does not expose an attachable native PTY,
use status/watch and retain that interaction gap. Do not type terminal answers
into an MCP protocol stream.
[Sources: public attachment](../scripts/testkit/operator_cli.py), [owned targets](../scripts/testkit/adapters/terminals.py), [private tmux worker](../scripts/testkit/adapters/managed_process.py).

Native approvals remain attended where the selected client exposes them. Before
relying on an unattended workload, exercise the installed client's actual
prompt and response path. A ready doctor, live tmux pane or configured login
alone does not demonstrate that path. An installed client that cannot complete
startup remains unavailable; it does not become a native success.
[Source: managed native boundaries](managed-proofs.md#run-installed-harnesses-locally).

```bash
./scripts/proof.sh stop RUN_DIRECTORY       # request owned cancellation
./scripts/proof.sh wait RUN_DIRECTORY       # observe final outcome/finalization
./scripts/proof.sh collect RUN_DIRECTORY    # optional fresh collection attempt
./scripts/proof.sh verify RUN_DIRECTORY     # recheck latest retained telemetry
./scripts/proof.sh close RUN_DIRECTORY      # remove finished owned dashboard
./scripts/proof.sh start --from RUN_DIRECTORY --detach
```

Finalization already collects automatically. Manual collect is for another
collection attempt, including after a failed export; it never overwrites an
earlier attempt. `start --from` keeps the prior run and allocates new run/state,
native and default run-local telemetry identities. Explicit shared/custom capture
policy retains its selected storage and lineage. A repeat does not resume an
ambiguous native effect or inherit the old verdict. `wait` reports proof plus required cleanup and
collection; `verify` reports telemetry byte closure separately.
[Sources: managed controls](../scripts/testkit/operator_composition.py), [store](../scripts/testkit/adapters/managed_store.py), [telemetry supplement](../scripts/testkit/adapters/native_evidence.py).

Repeats derive generated capture from the saved operator-input snapshot and its
original configuration directory; an explicit `--config` selects a new input.
For a repeat already created with the earlier storage-reuse bug, use the original
saved run or pass `--config` with the intended operator policy. That earlier
repeat may have saved an effective configuration as its input; stored history is
not rewritten automatically.

## What is retained

The printed run directory is the single private evidence location. Newly
derived native configuration allocates its local corpus under that run, with
fresh dataset/collector identities. The reusable input configuration is not
mutated. Explicit disabled, custom, profile-backed or external/shared storage
choices remain selected. External/shared corpora are not automatically scanned
or copied into a single run's export.
[Source: configuration binding](../scripts/testkit/adapters/configuration.py).

| Evidence | Retention and boundary |
|---|---|
| Managed plan, progress, assertion/check receipts and verdicts | Existing committed generations and redacted JSON diagnostics ZIP; verdict stays with the shared test-kit. |
| Product semantic events, permitted exposed model/tool content, wire and transcript observations | Local SQLite/artifact corpus where enabled; source-specific limits, withheld credential bytes, dropped/unknown observations and usage semantics stay visible. This does not expose hidden reasoning. |
| Native terminal/scrollback diagnostics | Existing owned PTY transcript and bounded pane capture where the selected check supplies them; raw diagnostics stay private. An unexposed terminal stream is not claimed captured. |
| Run-owned local telemetry | Each collection writes `exports/<attempt>/telemetry-index.json`, verified portable bundles, a fresh-location restore and `telemetry.zip` containing the index and complete verified bundle members. |
| Disabled, missing, corrupt, foreign/shared or unavailable sources | Explicit disabled entries or gaps. No foreign history search and no fallback success. |
| Credential configuration, private input snapshots and unclassified raw logs | Excluded from the shareable JSON/telemetry archive policy; remain private at their original boundary. Review permitted content before sharing. |

Sources: [product capture policy](telemetry.md), [managed JSON archive policy](../scripts/testkit/adapters/managed_store.py),
[telemetry collection/verification](../scripts/testkit/adapters/native_evidence.py).

`verify` checks the saved SQLite/artifact bundle, its fresh restore, source
cutoff/lineage, and companion archive identity. This establishes retained byte
closure, not that every upstream source completed or that a harness/product
passed. The index keeps `source_completion: "unknown"` and
`native_acceptance: "not_established_by_telemetry"`; current managed proof and
outcome remain separate. Query/range-read the corpus through the product's
[authorized telemetry actions](telemetry.md) for analysis.
[Source: verifier](../scripts/testkit/adapters/native_evidence.py).

## Reused work

The original [multi-harness PoC](https://github.com/xormania/multi-harness-proof/tree/ad294398501ed119a436db54eca784be2fb10e76)
supplies the operator pattern: attended native approvals, owned tmux resources,
retained partial diagnostics, fresh repetition and a final private archive.
Its [operations guide](https://github.com/xormania/multi-harness-proof/blob/ad294398501ed119a436db54eca784be2fb10e76/OPERATIONS.md),
[session controls](https://github.com/xormania/multi-harness-proof/blob/ad294398501ed119a436db54eca784be2fb10e76/mhproof/sessions.py)
and [capture](https://github.com/xormania/multi-harness-proof/blob/ad294398501ed119a436db54eca784be2fb10e76/mhproof/capture.py)
are mechanism references; historical model defaults and role policies are not
new requirements.

Agentscient's current [tmux controls](https://github.com/xormania/agentscient/blob/a3693e7a707a3b332ce5da410ce7d0cfea78d6a2/docs/tmux-control.md),
[owned terminals](https://github.com/xormania/agentscient/blob/a3693e7a707a3b332ce5da410ce7d0cfea78d6a2/devtools/terminals.py),
[bounded readiness](https://github.com/xormania/agentscient/blob/a3693e7a707a3b332ce5da410ce7d0cfea78d6a2/devtools/readiness.py)
and [filtered collection](https://github.com/xormania/agentscient/blob/a3693e7a707a3b332ce5da410ce7d0cfea78d6a2/devtools/scripts/acceptance_collect.py)
support the same ownership, finite-probe and explicit-gap boundaries. This
workflow extends the existing Xorchestrate test-kit rather than adding another
harness launcher or verdict engine.
