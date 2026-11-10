# Selectable local stories

The existing proof runner executes a selected story battery in owned **tmux**
terminals. Every story gets fresh application state, independent named assertions,
private evidence and ownership-aware process cleanup. Linux/WSL2, tmux, PTYs,
Unix sockets and readable `/proc` are required. Follow [managed setup](managed-proofs.md)
once to install dependencies into an external environment.

```bash
./scripts/proof.sh stories
./scripts/proof.sh plan --suite stories --story coordination --story effects-recovery
./scripts/proof.sh doctor --profile "$HOME/.config/xorchestrate/proof.json" --suite stories
./scripts/proof.sh start --profile "$HOME/.config/xorchestrate/proof.json" \
  --suite stories --story coordination --story effects-recovery \
  --timeout 60 --run-timeout 180 --max-sessions 2 --max-turns 4 --max-tool-calls 100 --detach
```

`stories` lists IDs, versions and supported modes. `plan` shows selected commands,
deadlines and story limits without creating a run or starting a harness. `doctor`
checks prerequisites; authenticated behavior is established by a native run.
Repeat `--story` to select an ordered battery; omitting it selects all supported
stories for the chosen mode. Unknown, duplicate and unsupported IDs are errors.
`--suite all` includes the story battery alongside existing checks and live suites;
`--story` limits only that battery. It does not narrow other suites in `all`.

| ID | Observed behavior | Modes |
|---|---|---|
| `coordination` | Real MCP discovery, actor assignment, message delivery/acknowledgment and retained state after restart | Protocol peers |
| `effects-recovery` | Real CLI/MCP effects, idempotent replay, denied effects and uncertain effects fenced across restart | Protocol peers |
| `session-lifecycle` | Start, send, poll and stop a bridge session | Protocol peers |
| `work-acceptance` | Measure actual private artifact bytes through a subprocess; accept, revise requirements, reject stale measurement and changed artifact; retain history after restart | Protocol peers |
| `native-handshake` | Start the configured harness session and stop it; no turn is admitted | Protocol peers or configured native harness |
| `native-callback` | Actor-bound harness tool callback, exact nonce, session shutdown and retained result after restart | Protocol peers or configured native harness |

Protocol mode uses real application processes and controlled peers without vendor
models. The artifact story supplies a small fixed fixture; it is not an autonomous
coding assignment. Native callback runs use the configured installed harness and
normal local authentication, with no protocol fallback:

```bash
./scripts/proof.sh plan --profile "$HOME/.config/xorchestrate/proof.json" \
  --suite stories --mode native --story native-callback --driver codex --driver claude
./scripts/proof.sh start --profile "$HOME/.config/xorchestrate/proof.json" \
  --suite stories --mode native --story native-callback --driver codex \
  --max-sessions 1 --max-turns 1 --max-tool-calls 5000 \
  --timeout 120 --run-timeout 180 --detach
```

Each selected driver gets a separate attempt. `native-handshake` is the cheaper
of the two native stories: it reaches session start, where launch and session options are exercised (and
authentication, for adapters that authenticate at session start), and its turn
limit is zero, so
a turn is refused before dispatch whatever `--max-turns` allows. Its receipt
records that effective limit. [`doctor --probe`](managed-proofs.md#set-up-once)
runs it for the selected drivers. Omitting `--story` in native mode selects both
native stories for each driver. The other stories explicitly report
unsupported native selection; their controlled failure schedules have not been
qualified against installed vendors. The existing full multi-harness suite
remains available separately.

## Bounds and retained results

Limits apply per story/driver attempt and remain shared across its application
clients and restarts. Defaults are two session-start requests, four harness turn
submissions and 5,000 public controller tool requests. All must be positive
integers. Admission checks every affected counter before dispatch and consumes no
allowance for a refused request. Attempted dispatches count even if the application
rejects the request or the response is lost. Exceeding a limit fails that story;
owned-resource cleanup still runs. Independent selected stories can continue.

These counts bound controller activity, not autonomous native tool callbacks or
provider tokens. A harness may perform internal work within a submitted turn.
`--timeout` bounds each tmux scenario, including native work, and
`--run-timeout` optionally bounds the whole battery. Cleanup has a separate bounded
grace period. There is no claim of an enforceable token or billing ceiling; usage
is unknown here. Usage normalization and longitudinal comparisons belong to the
separate telemetry integration.

The reusable profile accepts `stories`, `max_sessions`, `max_turns` and
`max_tool_calls` in addition to existing fields. Command-line overrides and
`start --from` retain them. Keep the directory printed by `start`:

```bash
./scripts/proof.sh status RUN_DIRECTORY
./scripts/proof.sh watch RUN_DIRECTORY
./scripts/proof.sh attach RUN_DIRECTORY
./scripts/proof.sh wait RUN_DIRECTORY
./scripts/proof.sh collect RUN_DIRECTORY
./scripts/proof.sh start --from RUN_DIRECTORY --detach
```

Attachment reaches a current owned terminal. Native prompt access uses the existing
native PTY bridge; MCP pipes remain separate. Detaching a viewer does not cancel
the battery. `wait` returns the qualified result, including required cleanup and
collection. Repetition uses current source/tools and fresh state, retaining the
first attempt and its configuration/source differences. No automatic retry hides
a failure.

The normal run report retains each `story:ID[:DRIVER]` check and its command.
Nested `scenario.json` records story/version, expected/executed/missing assertions,
budget limits and consumption. Nested `report.json` records tmux lifecycle and
verified cleanup. Missing required assertions fail the story. Preserve these
original receipts when telemetry imports their references; telemetry export
success is not a behavioral verdict.

CI runs the six protocol stories in the existing tmux job, and tests exercise
selection, pre-dispatch budget rejection, actual effects, managed repetition and
failure cleanup. Installed vendor qualification remains a local measurement.
