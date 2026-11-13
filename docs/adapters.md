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

## Companion records

A companion kind is used either project-scoped (`records(project)`) or
project-independent (`records(None)`), never both. The `git-directory`
adapter keeps project records at `<kind>/<project>/<key>` and
project-independent ones at `<kind>/<key>`, so it records each kind's scope in
`<kind>/.scope` on its first write and refuses the kind in the other scope.
Changing that layout (for example a reserved segment for project-independent
records, with a migration) is a possible later decision.
