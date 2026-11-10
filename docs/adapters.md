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
and calls it with a private deep copy of `options`. Only `companion` factories
get more: `factory(options, *, version_control=...)`. Project adapters with
`close()` are closed in reverse construction order when the runtime ends.

Options are rendered and checked before your factory sees them:

- Rendering a project's configuration substitutes `${data}` (the instance's
  data directory), `${home}` (the account home), `${launchers}`
  (`${data}/launchers`) and `${instance}` (the instance id) in every option
  string, and refuses any other `${...}`.
- `${repo}` is allowed only in the `command`, `discovery_command`, `cwd` and
  `env` options of `providers`, `sessions` and `evaluators`, and stays
  unresolved: the built-in process adapters resolve it per call to the
  caller's worktree, while your factory receives the literal text.
- For every provider and evaluator, composition resolves `options["cwd"]`
  (default `"."`) to identify the configuration, so a non-string `cwd` is
  refused with `configuration_error`; your factory still receives `cwd` as
  written. Only built-in aliases receive resolved paths (`cwd`, the SQLite
  `path`, the telemetry `runtime_root`).
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

`telemetry` also accepts a `module:factory`, but `TelemetrySink` is
provisional and the host passes it internals (a database path and artifact
store described by `snapshot_source`/`bundle_source`), so a third-party
collector is not supported yet and `telemetry` is not in `xcontracts.KINDS`.
`state_engine` also accepts a `module:factory`, but its interface is
Xorchestrate's lifecycle engine, not a published port, and may change with
any release. The local collector's `corpus`/`artifacts` storage and the
client transports (`ClientTransport`, `ClientConnection`, `PeerIdentity`) are
internal as well. The optional `bind_telemetry`, `bind_observer` and
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
