"""Declaration and pure decision checks independent of persistent fixtures."""

from dataclasses import FrozenInstanceError

import pytest

from xorchestrate.model import OrchestrationError
from xorchestrate.state import MachineDefinition, StateEngine, Transition


def definition(*transitions):
    return MachineDefinition("light", 1, {"off", "on"}, transitions)


def test_definition_detaches_mutable_collections_and_registry():
    sources = {"off"}
    states = {"off", "on"}
    rules = [Transition("switch", sources, "on")]
    machine = MachineDefinition("light", 1, states, rules)
    registry = {"light": machine}
    engine = StateEngine(registry)
    sources.clear()
    states.clear()
    rules.clear()
    registry.clear()
    assert engine.decide("light", "off", "switch").target == "on"
    with pytest.raises(TypeError):
        engine.definitions["other"] = machine
    with pytest.raises(FrozenInstanceError):
        machine.version = 2


@pytest.mark.parametrize("version", [True, False, 0, -1, "1", 1.0])
def test_definition_revision_is_a_positive_integer(version):
    with pytest.raises(OrchestrationError, match="positive integer") as error:
        MachineDefinition("light", version, {"off", "on"}, (Transition("switch", {"off"}, "on"),))
    assert error.value.code == "invalid_state_definition"


def test_undeclared_endpoint_and_unguarded_ambiguity_reject_at_construction():
    for rules in (
        (Transition("switch", {"off"}, "missing"),),
        (Transition("switch", {"missing"}, "on"),),
        (Transition("switch", {"off"}, "on"), Transition("switch", {"off"}, "off")),
    ):
        with pytest.raises(OrchestrationError) as error:
            definition(*rules)
        assert error.value.code == "invalid_state_definition"


def test_guarded_alternatives_require_exactly_one_eligible_transition():
    machine = definition(
        Transition("switch", {"off"}, "on", lambda context: context["on"]),
        Transition("switch", {"off"}, "off", lambda context: context["off"]),
    )
    engine = StateEngine({"light": machine})
    assert engine.decide("light", "off", "switch", {"on": True, "off": False}).target == "on"
    assert engine.decide("light", "off", "switch", {"on": False, "off": True}).target == "off"
    for context, code in (({"on": False, "off": False}, "state_guard_rejected"),
                          ({"on": True, "off": True}, "ambiguous_transition")):
        with pytest.raises(OrchestrationError) as error:
            engine.decide("light", "off", "switch", context)
        assert error.value.code == code


def test_each_guard_receives_an_independent_context_copy():
    def first(context):
        context["facts"]["allowed"] = False
        return False

    engine = StateEngine({"light": definition(
        Transition("switch", {"off"}, "off", first),
        Transition("switch", {"off"}, "on", lambda context: context["facts"]["allowed"]),
    )})
    context = {"facts": {"allowed": True}}
    assert engine.decide("light", "off", "switch", context).target == "on"
    assert context == {"facts": {"allowed": True}}


@pytest.mark.parametrize("result", [1, "yes", [], {}, None])
def test_truthy_or_missing_guard_answers_are_not_boolean_authorization(result):
    engine = StateEngine({"light": definition(Transition("switch", {"off"}, "on", lambda _: result))})
    with pytest.raises(OrchestrationError) as error:
        engine.decide("light", "off", "switch")
    assert error.value.code == "invalid_guard_result"


@pytest.mark.parametrize("context", [{"number": float("nan")}, {1: "key"}, {"tuple": (1, 2)}])
def test_invalid_json_context_is_rejected_before_guard_runs(context):
    calls = []
    engine = StateEngine({"light": definition(Transition("switch", {"off"}, "on", lambda _: calls.append(1)))})
    with pytest.raises(OrchestrationError) as error:
        engine.decide("light", "off", "switch", context)
    assert error.value.code == "invalid_state_input"
    assert calls == []


def test_unknown_machine_state_and_event_have_distinct_failures():
    engine = StateEngine({"light": definition(Transition("switch", {"off"}, "on"))})
    for args, code in (
        (("missing", "off", "switch"), "unknown_state_machine"),
        (("light", "missing", "switch"), "invalid_state"),
        (("light", "on", "switch"), "invalid_transition"),
    ):
        with pytest.raises(OrchestrationError) as error:
            engine.decide(*args)
        assert error.value.code == code


@pytest.mark.parametrize("storage", ["memory", "sqlite"])
def test_caught_interrupt_cannot_commit_partial_transition(storage, tmp_path):
    from tests.hexagonal.support.state import StoredStateFixture

    fixture = StoredStateFixture(storage, tmp_path / "interrupt.sqlite3")

    class InterruptedHistory:
        def __init__(self, transaction):
            self.transaction = transaction

        def __getattr__(self, name):
            return getattr(self.transaction, name)

        def emit(self, event):
            self.transaction.emit(event)
            raise KeyboardInterrupt("fixture interrupted after history append")

    try:
        assert fixture.change("register", None).accepted
        before = fixture.observe()
        with pytest.raises(OrchestrationError) as error:
            with fixture.store.transaction() as tx:
                try:
                    fixture.engine.apply(InterruptedHistory(tx), "parcel", "parcel", "claim",
                                         {"status": "held"}, before.version, machine_name="parcel-flow")
                except KeyboardInterrupt:
                    pass
        assert error.value.code == "transaction_aborted"
        assert fixture.observe() == before, "INTERRUPTED_TRANSITION_CANNOT_PARTIALLY_COMMIT"
    finally:
        fixture.close()


# These bounded witnesses expose the shared contract's key guarantees directly
# in the collected test frame, where the existing calibration observer can
# distinguish the intended assertion from fixture/setup failures.
def test_state_calibration_positive_control():
    from tests.hexagonal.support.state import parcel_definition

    engine = StateEngine({"parcel-flow": parcel_definition()})
    decision = engine.decide("parcel-flow", "draft", "claim")
    assert (decision.source, decision.target, decision.definition_version) == ("draft", "held", 7)


def test_state_calibration_guard_refusal(tmp_path):
    from tests.hexagonal.support.state import StoredStateFixture

    fixture = StoredStateFixture("memory", tmp_path / "guard.sqlite3")
    try:
        assert fixture.change("register", None).accepted
        assert fixture.change("claim", 1).accepted
        before = fixture.observe()
        outcome = fixture.change("release", before.version)
        assert (not outcome.accepted and outcome.code == "state_guard_rejected"
                and fixture.observe() == before), "STATE_CALIBRATION_GUARD_REFUSAL"
    finally:
        fixture.close()


def test_state_calibration_stale_version(tmp_path):
    from tests.hexagonal.support.state import StoredStateFixture

    fixture = StoredStateFixture("memory", tmp_path / "version.sqlite3")
    try:
        assert fixture.change("register", None).accepted
        assert fixture.change("touch", 1).accepted
        before = fixture.observe()
        outcome = fixture.change("claim", 1)
        assert (not outcome.accepted and outcome.code == "conflict"
                and fixture.observe() == before), "STATE_CALIBRATION_STALE_VERSION"
    finally:
        fixture.close()


def test_state_calibration_required_history(tmp_path):
    from tests.hexagonal.support.state import StoredStateFixture

    fixture = StoredStateFixture("memory", tmp_path / "history.sqlite3")
    try:
        assert fixture.change("register", None).accepted
        observed = fixture.observe()
        expected = {"type": "state.transition", "machine": "parcel-flow", "definition_version": 7,
                    "subject": {"kind": "parcel", "key": "parcel"}, "event": "register", "source": None,
                    "target": "draft", "previous_version": None, "record_version": 1,
                    "actor": "fixture", "at": "fixture-time", "seq": 1}
        assert (observed.version == 1 and observed.history == (expected,)), "STATE_CALIBRATION_REQUIRED_HISTORY"
    finally:
        fixture.close()


def test_state_calibration_declared_target(tmp_path):
    from tests.hexagonal.support.state import StoredStateFixture

    fixture = StoredStateFixture("memory", tmp_path / "target.sqlite3")
    try:
        assert fixture.change("register", None).accepted
        before = fixture.observe()
        outcome = fixture.change("claim", before.version, wrong_target=True)
        assert (not outcome.accepted and outcome.code == "invalid_state_target"
                and fixture.observe() == before), "STATE_CALIBRATION_DECLARED_TARGET"
    finally:
        fixture.close()


def test_state_calibration_application_consumes_engine(tmp_path):
    from tests.hexagonal.support.state import LiveStateFixture

    fixture = LiveStateFixture("memory", tmp_path / "application.sqlite3")
    try:
        fixture.set_creation_guard("operation", False)
        before = fixture.snapshot()
        outcome = fixture.start()
        assert (not outcome.accepted and outcome.code == "state_guard_rejected"
                and fixture.driver.start_calls == 0
                and fixture.snapshot() == before), "STATE_CALIBRATION_APPLICATION_CONSUMES_ENGINE"
    finally:
        fixture.close()
