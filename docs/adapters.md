# Writing an adapter

Adapters implement the ports in [xcontracts](https://github.com/xormania/xcontracts),
a separate Apache-2.0 package with no dependencies beyond the standard library.
Depend on `xcontracts` (for now `xcontracts>=0.1,<0.2`), not on `xorchestrate`:
the ports, the values and errors they exchange (`Record`, `Capability`,
`Verdict`, `OrchestrationError`, `Conflict`, ...) and the factory convention
are all defined there. `xorchestrate.ports` and `xorchestrate.model` re-export
the same objects, so code inside Xorchestrate is unaffected.

## The convention

An adapter is selected in configuration by a trusted, installed reference:

```json
{"guardrail": {"adapter": "my_package.policies:DenyActions", "options": {"actions": ["start_session"]}}}
```

Xorchestrate imports `my_package.policies`, reads its top-level `DenyActions`
and calls it with a private deep copy of `options` as its only argument. The
one public exception is `companion`: `factory(options, *, version_control=...)`.
Some built-in aliases get host internals (the built-in `tool` evaluator gets
the constructed providers, the built-in `pr` provider gets the store and the
state engine, the built-in `local` telemetry collector gets `resolve=`); a
`module:Name` reference never does. Project adapters with
`close()` are closed in reverse construction order when the runtime ends.

A factory refuses invalid options by raising `OrchestrationError` with a
public message; its code is passed on as is (the built-in adapters mostly use
`invalid_configuration`). Any other exception, and a reference whose module or
attribute cannot be imported, is reported as `configuration_error` without
its text.

Options are rendered and checked before your factory sees them:

- Rendering a project's configuration substitutes `${data}` (the instance's
  data directory), `${home}` (the account home), `${launchers}`
  (`${data}/launchers`) and `${instance}` (the instance id) in every option
  string, and refuses any other `${...}`.
- `${repo}` is allowed only in the `command`, `discovery_command`, `cwd` and
  `env` options of `providers`, `sessions` and `evaluators`, and stays
  unresolved: the built-in process adapters resolve it per call to the
  caller's worktree, while your factory receives the literal text.
- For every provider and evaluator, composition also resolves
  `options["cwd"]` (default `"."`), only to identify the configuration, so a
  non-string `cwd` is refused with `configuration_error`; your factory still
  receives `cwd` as written.
- Only built-in aliases receive resolved paths: built-in providers (except
  `pr`) and sessions an absolute `cwd`, the SQLite store its `path`, the local telemetry
  its `runtime_root`. Built-in evaluators receive `cwd` as written.
- Operator settings in the user's home are not rendered.

So accept absolute paths (`${data}` gives one) or document what yours are
relative to. The exact rules are in the docstring of `xcontracts/factories.py`;
`xcontracts.KINDS` lists every selectable kind and its port.

Which kinds are public:

| Kind | Port | Selected in |
| --- | --- | --- |
| `store` | `StateStore` | project configuration |
| `providers`, `sessions`, `evaluators` | `CapabilityProvider`, `SessionDriver`, `Evaluator` | project configuration (named) |
| `guardrail`, `routing`, `acceptance` | `Guardrail`, `RoutingPolicy`, `AcceptancePolicy` | project configuration |
| `session_selection`, `delivery_scheduler`, `retry_policy`, `receipt_policy` | `SessionSelection`, `DeliveryScheduling`, `DeliveryRetry`, `ReceiptPolicy` | project configuration |
| `companion`, `version_control`, `repository_hosting` | `Companion`, `VersionControl`, `RepositoryHosting` | the user's home (`settings.json`, `instance.json`) |

`terminal_host` (`TerminalHost`) is selected in the user's home, not in a
project configuration. It is defined locally in `xorchestrate.ports` while it
is still changing, so a third-party terminal-host adapter is not supported
through xcontracts. `recovery_policy` (`RecoveryPolicy`) is a project section
(`reconnect` by default, or `manual`) and is local for the same reason. The
`hosted` session adapter is a built-in wrapper around another session driver,
not a separate port. See [terminal host](terminal-host.md).

`telemetry` also accepts a `module:factory`, but `TelemetrySink` is
provisional and the host passes it internals (a database path and artifact
store described by `snapshot_source`/`bundle_source`), so a third-party
collector is not supported yet and `telemetry` is not in `xcontracts.KINDS`.
`state_engine` also accepts a `module:factory`, but its interface is
Xorchestrate's lifecycle engine, not a published port, and may change with
any release. The local collector's `corpus`/`artifacts` storage and operational
signal sinks are selected through its injected resolver, with factories called
as `factory(options, CollectorScope)`; see [telemetry options](telemetry.md#configuration-options).
Those nested kinds and the
client transports (`ClientTransport`, `ClientConnection`, `PeerIdentity`) are
internal as well, and so are the pull request delivery ports
(`PullRequestHost`, `PullRequestWorkspace`), `TerminalHost` and
`RecoveryPolicy`, which are new and still changing. The optional `bind_telemetry`, `bind_observer` and
`bind_state_engine` hooks pass host internals and are not covered by
xcontracts' compatibility policy.

## Checking it

Run xcontracts' conformance kit in your adapter's tests; it does not import
pytest:

```python
import pytest
from xcontracts.testing import factory, state_store


@pytest.mark.parametrize("check", state_store.CHECKS, ids=str)
def test_store_conforms(check, tmp_path):
    check(lambda: MyStore({"path": str(tmp_path / "state.db")}))


def test_selectable_by_reference():
    factory.construct("guardrail", {"adapter": "my_package.policies:DenyActions", "options": {}})
```

Behavioral checks cover `StateStore`, `Companion`, `VersionControl` and
`RepositoryHosting`; Xorchestrate's built-in adapters run the same checks
(`tests/hexagonal/test_storage.py`, `tests/hexagonal/test_operator_ports.py`,
`tests/hexagonal/test_xcontracts.py`). For the other ports the kit checks
structure, and the behavior they must support is described in each port's
docstring and in [the application architecture](hexagonal-architecture.md).

## Capability execution and retention

Ordinary providers keep the existing journaled invocation behavior. Trusted
configuration can select a different execution policy for an exact
`provider/capability` address:

```json
{
  "capability_execution": {
    "reports/inspect": {"mode": "query", "observation": "none"},
    "reports/export": {"mode": "receipt", "observation": "none"}
  }
}
```

| Mode | Execution | Retention and repeated calls |
| --- | --- | --- |
| `journaled` (default) | Requires an idempotency key; reserves the operation before invoking | Retains arguments, result and work evidence; replays the retained result |
| `query` | Invokes freshly after discovery and invocation authorization; an idempotency key is optional | Creates no operation, invocation or work evidence; returns `{"result": ...}` and rechecks the authorization context and authority revision before releasing it |
| `receipt` | Requires a key and uses the same durable effect reservation and uncertain-outcome fence | First response is `{"result": ..., "operation_id": ...}`; retains request fingerprint, scope, outcome and descriptor, but no argument payload, provider result or work evidence. A completed repeat returns only `{"operation_id": ..., "status": "completed", "result_retained": false}` |

Selecting `query` asserts that this installed operation is a read: Xorchestrate
cannot prove that an arbitrary provider has no side effects. Select `receipt`
for mutations whose payloads must not be copied into the orchestration store.
Failed and uncertain receipt effects remain fenced. Completed-but-unapplied
outcomes retain their completion fact without the discarded result, and can be
explicitly reconciled to a status receipt. Reconciliation never manufactures
retained measurement evidence for these modes. A previously retained effect
keeps its fence even if current configuration selects query execution.

Native calls retain their original source identity and request fingerprint.
They do not retain private query/receipt payloads in native-call records or
application-tool observations. A completed native query repeats a fresh,
authorized read; a receipt repeat returns only its operation receipt. Changed
arguments or execution mode on the same native call identity are refused.
Native failures retain their verified operation identity so the caller can
inspect and reconcile the fenced effect. Native reconciliation itself repeats
only its safe completion receipt after current authorization.

`observation` independently selects `full` (the default) or `none` for optional
application and typed native-tool telemetry. Required durable native-call and
native-observation evidence still follows `mode`: journaled calls retain it;
queries and receipt calls omit payloads. `none` suppresses typed driver
observations emitted before or after the callback, including queued telemetry,
and nested `Application.record_observation` calls on the executing thread. A
nested call cannot turn capture back on. This is not control over an external
process's transcript or unrelated asynchronous adapter telemetry. Configure
those sources separately. Receipt retries and reconciliation retain the
originating observation choice using the actual actor and retained source binding.

An installed provider may expose the provisional `execution_policies()` hook,
returning this same map with local capability names. Composition qualifies the
names with the configured provider name, then applies explicit configuration
overrides. This hook is trusted installed code, not remote discovery. Descriptor
annotations, including `readOnlyHint`, remain descriptive data and never select
execution policy. Explicit selections are validated before providers are
constructed; installed hook results are validated during composition. The hook
and top-level section are Xorchestrate extensions; the
four-field `xcontracts.Capability` and `CapabilityProvider` port are unchanged.

## Companion records

A companion kind is used either project-scoped (`records(project)`) or
project-independent (`records(None)`), never both. The `git-directory`
adapter keeps project records at `<kind>/<project>/<key>` and
project-independent ones at `<kind>/<key>`, so it records each kind's scope in
`<kind>/.scope` on its first write and refuses the kind in the other scope.
Changing that layout (for example a reserved segment for project-independent
records, with a migration) is a possible later decision.
