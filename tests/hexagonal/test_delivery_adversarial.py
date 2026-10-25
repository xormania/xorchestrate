"""Independent regressions for delivery/lifecycle authorization and stale effects."""
import itertools
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from xorchestrate.adapters.policies import ConfigurableRouting, RuleGuardrail
from xorchestrate.adapters.storage import MemoryStore
from xorchestrate.application import Application
from xorchestrate.composition import _wire_application_tools
from xorchestrate.model import Conflict, OrchestrationError


class Driver:
    def __init__(self):
        self.starts = []
        self.callback = None
        self.entered = threading.Event()
        self.release = threading.Event()
        self.send_error = None

    def start(self, specification):
        self.starts.append(specification)
        return {"native_id": "conversation", "incarnation": "generation-1", "handle": "connection"}

    def bind_application_tools(self, capabilities, callback):
        self.callback = callback

    def send(self, backend, message):
        self.entered.set()
        assert self.release.wait(2)
        if self.send_error:
            raise self.send_error
        return {"submitted": True}

    def stop(self, backend):
        return {"stopped": True}

    def detach(self, backend):
        return {"detached": True}

    def poll(self, backend):
        return []


def application(driver, *, guardrail=None, fallbacks=None, receipt_policy=None):
    identifiers = itertools.count(1)
    return Application(store=MemoryStore(), providers={}, sessions={"agent": driver},
        guardrail=guardrail or RuleGuardrail(), routing=ConfigurableRouting(), evaluators={},
        clock=lambda: "2026-10-23T12:00:00Z", identifiers=lambda: f"r{next(identifiers)}",
        session_fallbacks=fallbacks, receipt_policy=receipt_policy)


def start(app):
    return app.call("start_session", {"driver": "agent", "specification": {}, "idempotency_key": "start"})["session"]


def test_effective_fallback_driver_must_be_authorized_before_launch():
    driver = Driver()
    guardrail = RuleGuardrail({"rules": [{"actions": ["start_session"],
        "where": {"resolved_driver": "agent"}, "effect": "deny"}]})
    app = application(driver, guardrail=guardrail, fallbacks={"unavailable": "agent"})
    with pytest.raises(OrchestrationError) as rejected:
        app.call("start_session", {"driver": "unavailable", "specification": {}, "idempotency_key": "fallback"})
    assert rejected.value.code == "denied"
    assert driver.starts == [], "fallback_launch_authorized_as_effective_driver"


@pytest.mark.parametrize("closed_action", ["stop_session", "detach_session"])
def test_stale_native_callback_cannot_create_application_effect(closed_action):
    driver = Driver()
    app = application(driver)
    _wire_application_tools("agent", driver, app)
    session = start(app)
    app.call(closed_action, {"session_id": session["id"], "idempotency_key": "close"})
    context = {"actor": "owner", "session_id": session["id"], "native_id": "conversation",
               "incarnation": "generation-1", "call_id": "late-call"}
    with pytest.raises(OrchestrationError):
        driver.callback("create_work", {"goal": "A late stale callback"}, context, "late-call")
    assert app.call("list_work", {})["work"] == [], "stale_callback_did_not_create_work"


@pytest.mark.parametrize("closed_action,expected", [("stop_session", "stopped"), ("detach_session", "detached")])
@pytest.mark.parametrize("outcome", ["not_started", "unknown"])
def test_late_send_failure_never_reopens_closed_session(closed_action, expected, outcome):
    driver = Driver()
    driver.send_error = OrchestrationError("Late send result", "provider_unavailable", {"outcome": outcome})
    app = application(driver)
    session = start(app)
    with ThreadPoolExecutor(max_workers=1) as executor:
        sent = executor.submit(app.call, "send_session", {"session_id": session["id"],
            "message": {"text": "held"}, "idempotency_key": "send"})
        assert driver.entered.wait(1)
        app.call(closed_action, {"session_id": session["id"], "idempotency_key": "close"})
        driver.release.set()
        with pytest.raises(OrchestrationError):
            sent.result(timeout=1)
    current = app.call("list_sessions", {})["sessions"][0]
    assert current["status"] == expected, "late_send_failure_preserves_closed_lifecycle"
    with pytest.raises(OrchestrationError):
        app.call("send_session", {"session_id": session["id"], "message": {}, "idempotency_key": "after-close"})


def test_receipt_commit_rechecks_authorization_context_after_policy_evaluation():
    driver = Driver()
    app = None

    class Policy:
        def evaluate(self, snapshot, recipient, expectation):
            owner = next(item for item in app.call("list_actors", {})["actors"] if item["id"] == "owner")
            app.call("update_actor", {"actor_id": "owner", "expected_version": owner["version"],
                "changes": {"roles": ["revoked"]}})
            return {"verified": False, "diagnostics": ["deliberate test policy"]}

    guardrail = RuleGuardrail({"rules": [{"actions": ["verify_exchange"], "roles": ["revoked"], "effect": "deny"}]})
    app = application(driver, guardrail=guardrail, receipt_policy=Policy())
    app.call("add_actor", {"actor_id": "owner", "roles": ["allowed"]})
    app.call("add_actor", {"actor_id": "recipient"})
    message = app.call("send_message", {"destination": {"actor": "recipient"}, "body": {}})["message"]
    with pytest.raises(Conflict):
        app.call("verify_exchange", {"message_id": message["id"], "recipient": "recipient", "expectation": {}})
    assert app.call("get_delivery", {"message_id": message["id"]})["receipts"] == []


def test_callback_source_closed_during_authorization_cannot_commit_effect():
    driver = Driver()
    app = None
    source_id = None

    class Guardrail(RuleGuardrail):
        def evaluate(self, action, context):
            if action == "create_work" and context.get("native_source"):
                app.call("stop_session", {"session_id": source_id, "idempotency_key": "stop-during-authorization"})
            return super().evaluate(action, context)

    app = application(driver, guardrail=Guardrail())
    _wire_application_tools("agent", driver, app)
    source = start(app)
    source_id = source["id"]
    context = {"actor": "owner", "session_id": source_id, "native_id": "conversation",
               "incarnation": "generation-1", "call_id": "racing-call"}
    with pytest.raises(OrchestrationError):
        driver.callback("create_work", {"goal": "Concurrent callback"}, context, "racing-call")
    assert app.call("list_work", {})["work"] == [], "callback_binding_rechecked_at_commit"


def test_superseded_source_cannot_be_reactivated_by_late_send_result():
    driver = Driver()
    resume_entered = threading.Event()
    resume_release = threading.Event()

    def resume(backend, specification):
        resume_entered.set()
        assert resume_release.wait(2)
        return {"native_id": "conversation", "incarnation": "generation-2", "handle": "replacement"}

    driver.resume = resume
    app = application(driver)
    source = start(app)
    with ThreadPoolExecutor(max_workers=2) as executor:
        replacement = executor.submit(app.call, "start_session", {"driver": "agent", "specification": {},
            "resume_session_id": source["id"], "idempotency_key": "resume"})
        assert resume_entered.wait(1)
        sending = executor.submit(app.call, "send_session", {"session_id": source["id"],
            "message": {}, "idempotency_key": "old-send"})
        try:
            # Either source operations are fenced while replacing, or their late
            # completion must retain the superseded state after replacement.
            driver.entered.wait(.1)
            resume_release.set()
            replacement.result(timeout=1)
            driver.release.set()
            try:
                sending.result(timeout=1)
            except OrchestrationError:
                pass
        finally:
            resume_release.set()
            driver.release.set()
    sessions = {item["id"]: item for item in app.call("list_sessions", {})["sessions"]}
    assert sessions[source["id"]]["status"] == "superseded", "late_send_cannot_revive_superseded_source"


def test_delivery_selection_is_revalidated_before_native_effect():
    driver = Driver()
    driver.deliveries = []
    driver.deliver = lambda backend, envelope: driver.deliveries.append(envelope) or {"submitted": True}
    app = application(driver)
    app.call("add_actor", {"actor_id": "owner"})
    source = start(app)

    class Selection:
        def select(self, message, recipient, sessions):
            app.call("stop_session", {"session_id": source["id"], "idempotency_key": "stop-in-selection"})
            return source["id"]

    app.session_selection = Selection()
    message = app.call("send_message", {"destination": {"actor": "owner"}, "body": {"text": "after-close"}})["message"]
    app.call("dispatch_deliveries", {"message_id": message["id"]})
    assert driver.deliveries == [], "closed_selected_session_does_not_receive_native_delivery"


def test_native_call_replay_cannot_repeat_non_idempotent_application_action():
    driver = Driver()
    app = application(driver)
    _wire_application_tools("agent", driver, app)
    source = start(app)
    context = {"actor": "owner", "session_id": source["id"], "native_id": "conversation",
               "incarnation": "generation-1", "call_id": "stable-native-call"}
    arguments = {"goal": "One native request"}
    original = driver.callback("create_work", arguments, context, "stable-native-call")
    try:
        replay = driver.callback("create_work", arguments, context, "stable-native-call")
    except OrchestrationError:
        # An explicit replay rejection is safe; a duplicated effect is not.
        pass
    else:
        assert replay == original
    assert len(app.call("list_work", {})["work"]) == 1, "native_call_replay_does_not_duplicate_effect"


@pytest.mark.parametrize("field,wrong", [("native_id", "another-conversation"), ("incarnation", "replaced-generation")])
def test_conflicting_adapter_submission_identity_cannot_become_submitted(field, wrong):
    driver = Driver()
    driver.deliver = lambda backend, envelope: {"submitted": True, "native_id": backend["native_id"],
        "incarnation": backend["incarnation"], field: wrong}
    app = application(driver)
    app.call("add_actor", {"actor_id": "owner"})
    start(app)
    message = app.call("send_message", {"destination": {"actor": "owner"}, "body": {}})["message"]
    result = app.call("dispatch_deliveries", {"message_id": message["id"]})["deliveries"][0]
    assert result["attempt"]["status"] != "submitted", "conflicting_submission_identity_rejected"


def test_completed_native_call_replay_requires_current_authorization():
    driver = Driver()
    guardrail = RuleGuardrail({"rules": [{"actions": ["create_work"], "roles": ["revoked"], "effect": "deny"}]})
    app = application(driver, guardrail=guardrail)
    _wire_application_tools("agent", driver, app)
    actor = app.call("add_actor", {"actor_id": "owner", "roles": ["allowed"]})["actor"]
    source = start(app)
    context = {"actor": "owner", "session_id": source["id"], "native_id": "conversation",
               "incarnation": "generation-1", "call_id": "permission-sensitive-call"}
    arguments = {"goal": "Created while permitted"}
    driver.callback("create_work", arguments, context, "permission-sensitive-call")
    app.call("update_actor", {"actor_id": "owner", "expected_version": actor["version"],
        "changes": {"roles": ["revoked"]}})
    with pytest.raises(OrchestrationError) as rejected:
        driver.callback("create_work", arguments, context, "permission-sensitive-call")
    assert rejected.value.code == "denied", "native_replay_checks_current_authority"
    assert len(app.call("list_work", {})["work"]) == 1


def test_fallback_replay_rechecks_effective_driver_authorization():
    driver = Driver()
    app = application(driver, fallbacks={"unavailable": "agent"})
    arguments = {"driver": "unavailable", "specification": {}, "idempotency_key": "fallback"}
    app.call("start_session", arguments)
    app.guardrail = RuleGuardrail({"rules": [{"actions": ["start_session"],
        "where": {"arguments.driver": "agent"}, "effect": "deny"}]})
    with pytest.raises(OrchestrationError) as rejected:
        app.call("start_session", arguments)
    assert rejected.value.code == "denied"
    assert len(driver.starts) == 1


def test_native_call_retries_only_pre_effect_evidence_conflict_once():
    driver = Driver()
    app = None
    checked = []

    class Guardrail(RuleGuardrail):
        def evaluate(self, action, context):
            if action == "record_result" and context.get("native_source"):
                checked.append(action)
                if len(checked) == 1:
                    app.call("record_result", {"work_id": context["work_id"], "result": "concurrent background evidence"})
            return super().evaluate(action, context)

    app = application(driver, guardrail=Guardrail())
    _wire_application_tools("agent", driver, app)
    work = app.call("create_work", {"goal": "Concurrent callback"})["work"]
    source = start(app)
    context = {"actor": "owner", "session_id": source["id"], "native_id": "conversation",
               "incarnation": "generation-1", "call_id": "evidence-race", "work_id": work["id"]}
    result = driver.callback("record_result", {"result": "native result"}, context, "evidence-race")
    assert result["evidence"]["result"] == "native result"
    assert len(checked) == 2
    evidence = app.call("get_work", {"work_id": work["id"]})["evidence"]
    assert [item["result"] for item in evidence].count("native result") == 1, "safe_retry_does_not_duplicate_effect"


def test_native_retry_reauthorizes_changed_guardrail_and_stays_denied():
    driver = Driver()
    app = None
    checked = []

    class Guardrail(RuleGuardrail):
        def evaluate(self, action, context):
            decision = super().evaluate(action, context)
            if action == "record_result" and context.get("native_source"):
                checked.append(action)
                if len(checked) == 1:
                    app.call("update_actor", {"actor_id": "owner", "expected_version": context["actor_record"]["version"],
                        "changes": {"roles": ["revoked"]}})
            return decision

    app = application(driver, guardrail=Guardrail({"rules": [{"actions": ["record_result"], "roles": ["revoked"], "effect": "deny"}]}))
    _wire_application_tools("agent", driver, app)
    app.call("add_actor", {"actor_id": "owner", "roles": ["allowed"]})
    work = app.call("create_work", {"goal": "Reauthorize callback"})["work"]
    source = start(app)
    context = {"actor": "owner", "session_id": source["id"], "native_id": "conversation",
               "incarnation": "generation-1", "call_id": "permission-race", "work_id": work["id"]}
    with pytest.raises(OrchestrationError) as rejected:
        driver.callback("record_result", {"result": "must stay absent"}, context, "permission-race")
    assert rejected.value.code == "denied"
    assert len(checked) == 2
    assert app.call("get_work", {"work_id": work["id"]})["evidence"] == []


def test_post_enqueue_dispatch_conflict_never_repeats_committed_message():
    from xorchestrate.adapters.delivery import DeliverySchedule
    driver = Driver()
    app = None
    raced = []

    class Guardrail(RuleGuardrail):
        def evaluate(self, action, context):
            if action == "dispatch_deliveries" and not raced:
                raced.append(True)
                app.call("record_result", {"work_id": context["work_id"], "result": "concurrent evidence after enqueue"})
            return super().evaluate(action, context)

    app = application(driver, guardrail=Guardrail())
    app.delivery_scheduler = DeliverySchedule({"dispatch_on_enqueue": True})
    _wire_application_tools("agent", driver, app)
    app.call("add_actor", {"actor_id": "owner"})
    work = app.call("create_work", {"goal": "One enqueue"})["work"]
    source = start(app)
    context = {"actor": "owner", "session_id": source["id"], "native_id": "conversation",
               "incarnation": "generation-1", "call_id": "enqueue-race", "work_id": work["id"]}
    try:
        driver.callback("send_message", {"destination": {"actor": "owner"}, "body": "once"}, context, "enqueue-race")
    except OrchestrationError:
        pass
    assert raced == [True]
    messages = app.call("inbox", {"recipient": "owner", "include_acknowledged": True})["messages"]
    assert len(messages) == 1, "post_commit_dispatch_conflict_does_not_repeat_enqueue"


def unresolved_completed_native_call(app, driver):
    _wire_application_tools("agent", driver, app)
    source = start(app)
    context = {"actor": "owner", "session_id": source["id"], "native_id": "conversation",
               "incarnation": "generation-1", "call_id": "retained-completion"}
    observe = app.observe_native

    def fault(driver_name, callback_context, observation):
        result = observe(driver_name, callback_context, observation)
        if observation.get("phase") == "completed":
            raise OSError("Crash after retaining the adapter result")
        return result

    app.observe_native = fault
    try:
        with pytest.raises(OSError):
            driver.callback("create_work", {"goal": "Exactly one retained native effect"}, context, "retained-completion")
    finally:
        app.observe_native = observe
    call = app.call("native_observations", {"session_id": source["id"]})["calls"][0]
    assert call["status"] == "unknown"
    return call, context


def test_native_resolution_authorization_is_bound_to_target_session():
    driver = Driver()
    app = application(driver)
    call, context = unresolved_completed_native_call(app, driver)
    app.guardrail = RuleGuardrail({"rules": [{"actions": ["resolve_native_call"], "actors": ["worker"],
        "where": {"session_actor": "owner"}, "effect": "deny"}]})
    with pytest.raises(OrchestrationError) as rejected:
        app.call("resolve_native_call", {"native_call_id": call["id"], "expected_version": call["version"],
            "reason": "Read retained result"}, actor="worker")
    assert rejected.value.code == "denied"


def test_native_resolution_requires_retained_adapter_result_and_never_reexecutes():
    driver = Driver()
    app = application(driver)
    call, context = unresolved_completed_native_call(app, driver)
    resolved = app.call("resolve_native_call", {"native_call_id": call["id"], "expected_version": call["version"],
        "reason": "Verified retained adapter-origin completion"})["native_call"]
    replay = driver.callback("create_work", {"goal": "Exactly one retained native effect"}, context, "retained-completion")
    assert replay == resolved["result"]
    assert len(app.call("list_work", {})["work"]) == 1


@pytest.mark.parametrize("fault", ["caller_origin", "wrong_incarnation", "stale"])
def test_native_resolution_rejects_misbound_completion(fault):
    from copy import deepcopy
    driver = Driver()
    app = application(driver)
    call, context = unresolved_completed_native_call(app, driver)
    with app.store.transaction() as tx:
        record = next(item for item in tx.list("native_observation") if item.data["observation"].get("phase") == "completed")
        data = deepcopy(record.data)
        if fault == "caller_origin":
            data["origin"] = "caller"
        elif fault == "wrong_incarnation":
            data["observation"]["incarnation"] = "other-generation"
        else:
            data["stale"] = True
        tx.put("native_observation", record.key, data, record.version)
    with pytest.raises(OrchestrationError) as rejected:
        app.call("resolve_native_call", {"native_call_id": call["id"], "expected_version": call["version"], "reason": "Attempt invalid binding"})
    assert rejected.value.code == "unverified_native_result"
    with pytest.raises(OrchestrationError) as replay:
        driver.callback("create_work", {"goal": "Exactly one retained native effect"}, context, "retained-completion")
    assert replay.value.code == "native_call_unresolved"
    assert len(app.call("list_work", {})["work"]) == 1


def test_native_resolution_cannot_take_a_caller_supplied_result():
    driver = Driver()
    app = application(driver)
    call, _ = unresolved_completed_native_call(app, driver)
    with pytest.raises(OrchestrationError) as rejected:
        app.call("resolve_native_call", {"native_call_id": call["id"], "expected_version": call["version"],
            "reason": "Caller manufactured completion", "result": {"accepted": True}})
    assert rejected.value.code == "invalid_arguments"
