# Externally managed native sessions

The bridge keeps an operator-owned native conversation alive while Xorchestrate
clients attach, detach, restart, or replace a connection. The host selects the
native executable and permissions from trusted configuration. Tool arguments
cannot choose an endpoint, executable, PID, or native conversation ID.

This is a concrete attachment path for conversations launched by the bridge.
It does not claim attachment to an arbitrary already-running terminal process.
The host wraps the configured Codex, Claude, Grok, or another session driver;
native resume support remains the responsibility of that driver.

## Launch and attach

Launch the host in an operator-managed terminal. Use a configured native driver
name from the same native configuration used for ordinary owned sessions:

```bash
python3 -m xorchestrate.bridge --config /absolute/native.json --driver codex \
  --endpoint /absolute/private/codex-host --transport fifo \
  --state /absolute/private/codex-host-state.json --application-tools
```

The first stdout JSON record reports readiness and the observed host/native
identity. The state file contains private observations and the host's resume
secret. Keep it private; it is written atomically with mode `0600`.

Configure the attached application's session driver:

```json
{
  "sessions": {
    "codex": {
      "adapter": "bridge",
      "options": {
        "endpoint": "/absolute/private/codex-host",
        "transport": "fifo",
        "application_tools": true
      }
    }
  }
}
```

`attach_session` uses an empty `specification` and the normal actor/work binding.
`start_session` on a bridge driver also attaches. The application establishes its
trusted context; native settings remain those selected by the host. When
application tools are enabled, the attached application's catalog must match the
host's catalog. A changed catalog fails explicitly.

`detach_session` and the bridge driver's `stop` close only the client connection.
`reconnect_session` takes `resume_session_id` referring to a verified application
session. It requires the existing source-control and destination-creation
authorization. The host validates its signed receipt and preserves native
identity while issuing a new connection incarnation. A replacement fences the
old connection; late old results remain inspectable and cannot apply to the new
connection.

Stop the operator's host with its normal terminal interrupt or `SIGTERM` when
the native process should end. Application runtime cleanup never terminates an
external host. The host removes only its owned endpoint.

## Transport and recovery

`unix` is the default transport: a mode-`0600` Unix socket with a same-user peer
check where the operating system exposes peer credentials. `fifo` uses an
owner-only endpoint directory and mode-`0600` named pipes. It is an explicit
alternative for environments that prohibit Unix socket creation. Both use the
same bounded bidirectional request/callback protocol, worker limits, frame
bounds, and request correlation. Neither exposes a network listener.

Callbacks and observations progress without controller polling. Observations
retain native and client incarnations separately. Known attempt, native turn,
prompt and call identities bind observations to the corresponding connection.
Unbound or historical observations remain inspectable; they do not become proof
for a replacement connection. Retention gaps fail explicitly.

With `--state`, restarting the host resumes its saved native backend through the
configured driver's resume contract. It preserves host identity and its signed
receipt. Effects recorded as pending before the restart become `unknown` and
are not replayed. Without `--state`, host-process recovery requires a new host;
client reconnect alone still preserves the live host conversation. Unsupported
native resume, changed configuration, or changed native identity fails explicitly.

After an abrupt host crash, first confirm that the recorded host process has
exited. A stale endpoint is deliberately not removed automatically: remove only
that host's configured socket, or its private FIFO endpoint directory, before
restarting the same command with the same `--state` file. Do not remove the state
file. This preserves the saved host/native binding and unresolved effects.
Inspect any separately surviving native process using the harness's lifecycle
mechanism; a saved receipt does not establish that an orphan process terminated.

This implements uncertainty preservation and replay protection, not exactly-once
delivery. Inspect and reconcile unresolved application operations before deciding
whether another delivery is appropriate.

## Verification

```bash
python3 -m pytest tests/hexagonal/test_external_bridge.py -q
```

The tests cover external detach/cleanup, retained context, replaced connections,
late effects, callback/native-observation forwarding, signed receipts, settings,
recovery of pending effects, the Codex protocol adapter with a controlled peer,
and a host in a separate operating-system process. An additional test actually
kills its owned host subprocess after an effect has applied but before a response,
then recovers the retained marker and unknown effect without replaying it. The
native boundary for that crash test is a persistent deterministic fixture.
The synthetic three-protocol
labels are session-contract tests, not authenticated vendor measurements.

The composed verifier's lifecycle helper runs five cases for each native harness:
resume, external attach, detach, reconnect, and replaced connection. It supplies
private markers only during initialization, then checks native-observed callbacks
from retention probes that omit the marker. It retains raw application records,
host launch/cleanup records, and operation/observation/evidence identities for
the independent checker. Authenticated results remain pending until that run.
