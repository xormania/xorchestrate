"""State policy and engine factories exercised through actual composition."""
import sys
from types import ModuleType

import pytest

from xorchestrate.adapters.state import ConfiguredStateEngine
from xorchestrate.composition import build
from xorchestrate.model import Capability, OrchestrationError


@pytest.fixture
def extension(monkeypatch):
    module = ModuleType("xor_state_composition_fixture")
    module.calls = []
    module.engines = []

    class Provider:
        def __init__(self, options):
            pass

        def discover(self):
            return [Capability("echo", "Return an observed result")]

        def invoke(self, name, arguments, context):
            module.calls.append((name, arguments, context["actor"]))
            return {"observed": arguments}

        def close(self):
            pass

    class Session:
        def __init__(self, options):
            self.engine = None

        def bind_state_engine(self, engine):
            self.engine = engine

        def close(self):
            pass

    def engine(options):
        instance = ConfiguredStateEngine(options)
        module.engines.append(instance)
        return instance

    module.Provider, module.Session, module.engine = Provider, Session, engine
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return module


def configuration(extension, state_engine=None):
    config = {
        "store": {"adapter": "memory"},
        "providers": {"peer": {"adapter": extension.__name__ + ":Provider"}},
        "sessions": {"peer": {"adapter": extension.__name__ + ":Session"}},
    }
    if state_engine is not None:
        config["state_engine"] = state_engine
    return config


def invoke(runtime):
    return runtime.application.call("invoke", {
        "capability": "peer/echo", "arguments": {"message": "live state"}, "idempotency_key": "one",
    })


def test_configured_guard_controls_actual_effect_and_atomic_reservation(extension):
    guarded = configuration(extension, {"adapter": "declared", "options": {
        "guards": {"operation": {"default": "deny"}},
    }})
    with build(guarded) as runtime:
        # Simple use remains available; the policy governs only its declared machine.
        assert runtime.application.call("create_work", {"goal": "independent"})["work"]["goal"] == "independent"
        with pytest.raises(OrchestrationError) as error:
            invoke(runtime)
        assert error.value.code == "state_guard_rejected"
        assert extension.calls == [], "STATE_CONFIGURED_GUARD_PREVENTS_EXTERNAL_EFFECT"
        with runtime.application.store.transaction() as tx:
            assert tx.list("operation") == [] and tx.list("invocation") == []
            assert not [event for event in tx.events() if event["type"] == "state.transition"]
    # Removing exactly the blocking policy is a real, reachable remedy.
    with build(configuration(extension)) as runtime:
        result = invoke(runtime)
        assert result["invocation"]["status"] == "completed"
        assert result["invocation"]["result"] == {"observed": {"message": "live state"}}
        assert len(extension.calls) == 1, "STATE_CONFIGURED_REMEDY_EXECUTES_ONCE"
        assert invoke(runtime) == result and len(extension.calls) == 1


def test_custom_engine_factory_is_shared_by_application_and_driver(extension):
    with build(configuration(extension, {"adapter": extension.__name__ + ":engine"})) as runtime:
        assert len(extension.engines) == 1
        assert runtime.application.state_engine is extension.engines[0]
        assert runtime.application.sessions["peer"].engine is extension.engines[0]
        assert invoke(runtime)["invocation"]["status"] == "completed"
        with runtime.application.store.transaction() as tx:
            transitions = [event for event in tx.events() if event["type"] == "state.transition"]
        assert {event["machine"] for event in transitions} >= {"operation", "invocation"}


def test_json_definition_uses_arbitrary_state_field_and_supplied_guard():
    config = {"store": {"adapter": "memory"}, "state_engine": {"adapter": "declared", "options": {
        "definitions": {"parcel": {"version": 3, "state_field": "phase",
            "states": ["queued", "delivered"], "transitions": [
                {"event": "register", "sources": [None], "target": "queued"},
                {"event": "handover", "sources": ["queued"], "target": "delivered",
                 "where": {"proposed.owner": "xor", "approved": True}},
            ]}},
    }}}
    with build(config) as runtime:
        engine = runtime.application.state_engine
        with runtime.application.store.transaction() as tx:
            first = engine.apply(tx, "parcel", "p", "register", {"phase": "queued", "owner": "xor"})
        with runtime.application.store.transaction() as tx:
            before = tx.events()
            with pytest.raises(OrchestrationError, match="prerequisites"):
                engine.apply(tx, "parcel", "p", "handover", {"phase": "delivered", "owner": "xor"}, first.version)
            assert tx.get("parcel", "p") == first and tx.events() == before
        with runtime.application.store.transaction() as tx:
            final = engine.apply(tx, "parcel", "p", "handover", {"phase": "delivered", "owner": "xor"},
                                 first.version, {"approved": True})
            assert final.data["phase"] == "delivered" and final.version == 2
            assert tx.events()[-1]["definition_version"] == 3


@pytest.mark.parametrize("options", [
    {"unexpected": True}, {"definitions": []}, {"guards": []},
    {"guards": {"operation": None}},
    {"guards": {"missing": {"default": "allow"}}},
    {"definitions": {"parcel": {"states": "queued", "transitions": []}}},
    {"definitions": {"parcel": {"states": ["queued"], "transitions": [
        {"event": "register", "sources": [None], "target": "queued", "where": []},
    ]}}},
])
def test_invalid_state_configuration_is_rejected(options):
    with pytest.raises(OrchestrationError):
        build({"store": {"adapter": "memory"}, "state_engine": {"adapter": "declared", "options": options}})

