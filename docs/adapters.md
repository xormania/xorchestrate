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
and calls it with a private copy of `options`. Only `companion` factories get
more: `factory(options, *, version_control=...)`. Options reach a referenced
factory as written; Xorchestrate resolves relative paths (`cwd`, the SQLite
`path`, `${repo}`) only for its own built-in aliases. Project adapters with
`close()` are closed in reverse construction order when the runtime ends. The
exact rules are in the docstring of `xcontracts/factories.py`; `xcontracts.KINDS`
lists every selectable kind and its port.

Which kinds are public:

| Kind | Port | Selected in |
| --- | --- | --- |
| `store` | `StateStore` | project configuration |
| `telemetry` | `TelemetrySink` (provisional in 0.x) | project configuration |
| `providers`, `sessions`, `evaluators` | `CapabilityProvider`, `SessionDriver`, `Evaluator` | project configuration (named) |
| `guardrail`, `routing`, `acceptance` | `Guardrail`, `RoutingPolicy`, `AcceptancePolicy` | project configuration |
| `session_selection`, `delivery_scheduler`, `retry_policy`, `receipt_policy` | `SessionSelection`, `DeliveryScheduling`, `DeliveryRetry`, `ReceiptPolicy` | project configuration |
| `companion`, `version_control`, `repository_hosting` | `Companion`, `VersionControl`, `RepositoryHosting` | the user's home (`settings.json`, `instance.json`) |

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
