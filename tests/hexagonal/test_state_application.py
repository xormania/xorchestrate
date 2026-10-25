"""Application outcome contracts use controlled effect boundaries, not error names."""

from dataclasses import replace

import pytest

from tests.hexagonal.test_application import Provider, application, store  # noqa: F401
from tests.hexagonal.test_delivery import Native
from tests.hexagonal.test_delivery_adversarial import Driver
from xorchestrate.adapters.policies import RuleGuardrail
from xorchestrate.lifecycles import default_definitions
from xorchestrate.model import OrchestrationError
from xorchestrate.outcomes import effect_outcome
from xorchestrate.state import StateEngine


def deny_events(machine, events):
    definitions = default_definitions()
    definition = definitions[machine]
    definitions[machine] = replace(definition, transitions=tuple(
        replace(rule, guard=lambda _: False) if rule.event in events else rule
        for rule in definition.transitions))
    return StateEngine(definitions)


@pytest.mark.parametrize("boundary,expected_status,expected_outcome,effects", [
    ("authorization", "failed", "not_started", 0),
    ("handler_after_commit", "unknown", "unknown", 1),
    ("completion_retention", "unknown", "completed", 1),
])
def test_native_outcome_knowledge_follows_effect_boundary_and_never_reexecutes(
        store, boundary, expected_status, expected_outcome, effects):  # noqa: F811
    app = application(store, driver=Driver())
    session = app.call("start_session", {
        "driver": "agent", "specification": {}, "idempotency_key": "source",
    })["session"]
    context = {"actor": "owner", "session_id": session["id"], "native_id": "conversation",
        "incarnation": "generation-1", "call_id": "one-logical-call"}
    arguments = {"goal": "Exactly one controlled effect"}
    if boundary == "authorization":
        app.guardrail = RuleGuardrail({"rules": [{"actions": ["create_work"], "effect": "deny"}]})
    elif boundary == "handler_after_commit":
        create = app._create_work

        def committed_then_error(*args):
            create(*args)
            # The same code as authorization is insufficient provenance.
            raise OrchestrationError("Downstream refusal after commit", "denied")

        app._create_work = committed_then_error
    else:
        observe = app.observe_native

        def retained_then_refused(driver, source, observation):
            observed = observe(driver, source, observation)
            if observation["phase"] == "completed":
                # This local storage step may refuse before its own work, but
                # the application effect already completed. Preserve that fact.
                raise OrchestrationError("Observation response lost", "denied", {"outcome": "not_started"})
            return observed

        app.observe_native = retained_then_refused

    with pytest.raises(OrchestrationError) as first:
        app.invoke_native("agent", "create_work", arguments, context)
    assert first.value.code == ("result_unapplied" if boundary == "completion_retention" else "denied")
    assert effect_outcome(first.value) == expected_outcome
    with store.transaction() as tx:
        row, = tx.list("native_call")
        assert row.data["status"] == expected_status
        assert row.data["effect_outcome"] == expected_outcome
        assert len(tx.list("work")) == effects
        history = [item for item in tx.events() if item.get("type") == "state.transition"
            and item["subject"] == {"kind": "native_call", "key": row.key}]
        assert [(item["source"], item["event"], item["target"]) for item in history] == [
            (None, "create", "pending"),
            ("pending", "fail" if boundary == "authorization" else "uncertain", expected_status),
        ]

    # A changed policy is not permission to execute this call identity again.
    app.guardrail = RuleGuardrail()
    with pytest.raises(OrchestrationError) as replay:
        app.invoke_native("agent", "create_work", arguments, context)
    assert replay.value.code == ("native_call_failed" if expected_status == "failed" else "native_call_unresolved")
    with store.transaction() as tx:
        assert len(tx.list("work")) == effects

    if boundary == "completion_retention":
        resolved = app.call("resolve_native_call", {"native_call_id": row.key,
            "expected_version": row.version, "reason": "Retained adapter completion"})["native_call"]
        assert resolved["status"] == "completed"
        assert app.invoke_native("agent", "create_work", arguments, context) == resolved["result"]
        with store.transaction() as tx:
            assert len(tx.list("work")) == 1


@pytest.mark.parametrize("machine", ["delivery", "delivery_attempt"])
def test_submission_guard_cannot_erase_completed_adapter_result_or_resubmit(store, machine):  # noqa: F811
    driver = Native()
    app = application(store, driver=driver)
    app.state_engine = deny_events(machine, {"submit", "retain_late"})
    app.call("add_actor", {"actor_id": "owner"})
    app.call("start_session", {"driver": "agent", "specification": {}, "idempotency_key": "start"})
    message = app.call("send_message", {"destination": {"actor": "owner"}, "body": {"text": "once"}})["message"]
    failed, = app.call("dispatch_deliveries", {"message_id": message["id"]})["deliveries"]
    assert failed["error"]["code"] == "result_unapplied"
    assert failed["error"]["details"]["outcome"] == "completed"
    assert failed["error"]["details"]["external_result"] == {"submitted": True}
    snapshot = app.call("get_delivery", {"message_id": message["id"]})
    delivery, = snapshot["deliveries"]
    attempt, = snapshot["attempts"]
    assert delivery["status"] == attempt["status"] == "pending"
    retained, = [item for item in snapshot["observations"]
        if item["observation"].get("phase") == "settlement_unapplied"]
    assert retained["origin"] == "adapter" and retained["stale"] is False
    assert retained["observation"]["attempt_id"] == attempt["id"]
    assert retained["observation"]["result"] == {"submitted": True}
    assert retained["observation"]["outcome"] == "completed"
    assert snapshot["receipts"] == [], "submission retention does not manufacture exchange completion"
    with store.transaction() as tx:
        assert not [event for event in tx.events() if event.get("type") == "state.transition"
            and event.get("machine") in {"delivery", "delivery_attempt"} and event["event"] == "submit"]
    restarted = application(store, driver=driver)
    assert restarted.call("dispatch_deliveries", {}) == {"deliveries": []}
    resolved = restarted.call("resolve_delivery", {"delivery_id": delivery["id"],
        "expected_version": delivery["version"], "outcome": "submitted",
        "reason": "Retained adapter observation " + retained["id"]})["delivery"]
    assert resolved["status"] == "submitted"
    assert len(driver.calls) == 1


def test_native_terminal_and_uncertainty_guard_refusal_preserves_completion_evidence(store):  # noqa: F811
    app = application(store, driver=Driver())
    app.state_engine = deny_events("native_call", {"complete", "uncertain"})
    session = app.call("start_session", {"driver": "agent", "specification": {},
        "idempotency_key": "source"})["session"]
    context = {"actor": "owner", "session_id": session["id"], "native_id": "conversation",
        "incarnation": "generation-1", "call_id": "guarded-completion"}
    arguments = {"goal": "One effect despite rejected terminal writes"}
    with pytest.raises(OrchestrationError) as refused:
        app.invoke_native("agent", "create_work", arguments, context)
    assert effect_outcome(refused.value) == "completed"
    with store.transaction() as tx:
        row, = tx.list("native_call")
        assert row.data["status"] == "pending"
        assert len(tx.list("work")) == 1
        retained, = [item for item in tx.list("native_observation")
            if item.data["observation"]["phase"] == "completed"]
        assert retained.data["origin"] == "adapter"
        assert retained.data["observation"]["result"] == refused.value.details["external_result"]
    resolved = app.call("resolve_native_call", {"native_call_id": row.key,
        "expected_version": row.version, "reason": "Retained adapter completion"})["native_call"]
    assert resolved["status"] == "completed"
    assert app.invoke_native("agent", "create_work", arguments, context) == resolved["result"]
    with store.transaction() as tx:
        assert len(tx.list("work")) == 1


@pytest.mark.parametrize("result_field", ["result", "external_result"])
def test_completed_adapter_error_survives_operation_boundary_and_reconciles_without_replay(
        store, result_field):  # noqa: F811
    raw = {"value": 42}

    def completed_then_refused(*_):
        raise OrchestrationError("Adapter settlement refused", "completed_unapplied",
            {"outcome": "completed", result_field: raw})

    provider = Provider(completed_then_refused)
    app = application(store, provider=provider)
    arguments = {"capability": "tools/change", "arguments": {"value": 42}, "idempotency_key": "once"}
    with pytest.raises(OrchestrationError) as unapplied:
        app.call("invoke", arguments)
    assert unapplied.value.code == "result_unapplied"
    assert unapplied.value.details["outcome"] == "completed"
    assert unapplied.value.details["external_result"] == raw
    with store.transaction() as tx:
        operation, = tx.list("operation")
        assert operation.data["status"] == "completed_unapplied"
        assert operation.data["external_result_origin"] == "adapter"
        assert operation.data["external_result"] == raw
    with pytest.raises(OrchestrationError) as replay:
        app.call("invoke", arguments)
    assert effect_outcome(replay.value) == "completed"
    resolved = app.call("resolve_operation", {"operation_id": operation.key,
        "expected_version": operation.version, "outcome": "completed", "reason": "Retained adapter result"})["operation"]
    assert resolved["status"] == "completed"
    assert app.call("invoke", arguments)["invocation"]["result"] == raw
    assert len(provider.calls) == 1


def test_completed_delivery_error_retains_response_without_claiming_verified_submission(store):  # noqa: F811
    driver = Native()
    raw = {"submitted": True, "incarnation": "unverified-incarnation"}
    driver.failure = OrchestrationError("Adapter completed before local settlement failed", "completed_unapplied",
        {"outcome": "completed", "result": raw})
    app = application(store, driver=driver)
    app.call("add_actor", {"actor_id": "owner"})
    app.call("start_session", {"driver": "agent", "specification": {}, "idempotency_key": "start"})
    message = app.call("send_message", {"destination": {"actor": "owner"}, "body": {}})["message"]
    returned, = app.call("dispatch_deliveries", {"message_id": message["id"]})["deliveries"]
    assert returned["delivery"]["status"] == returned["attempt"]["status"] == "unknown"
    assert returned["attempt"]["effect_outcome"] == "completed"
    assert returned["attempt"]["rejected_result"] == raw
    assert returned["attempt"]["submission"] is None
    assert app.call("dispatch_deliveries", {}) == {"deliveries": []}
    assert len(driver.calls) == 1

