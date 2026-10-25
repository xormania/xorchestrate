"""Native identity transitions and their active adapter consumers."""
import sys
import threading
from dataclasses import replace
from itertools import permutations

import pytest

from xorchestrate.activity import ActivityState, NativeConnection, activity_definitions
from xorchestrate.adapters.claude import ClaudeSessionDriver, _ClaudeLive
from xorchestrate.adapters.grok import GrokSessionDriver
from xorchestrate.adapters.sessions import ACPSessionDriver, CodexSessionDriver, _LiveSession
from xorchestrate.model import Capability, OrchestrationError
from xorchestrate.outcomes import before_effect
from xorchestrate.state import StateEngine


@pytest.mark.parametrize("order", list(permutations("ABC")))
def test_identity_completion_permutations_leave_other_work_active(order):
    state = ActivityState()
    callbacks = state.set("callback", "ABC")
    for index, identity in enumerate(order):
        callbacks.discard(identity)
        callbacks.discard(identity)
        callbacks.add(identity)  # a late start is not a new incarnation
        assert set(callbacks) == set(order[index + 1:])
        assert state.activity == ("active" if index < 2 else "idle")
    assert {row["state"] for row in state.snapshot()} == {"completed"}


def test_uncertainty_is_owned_by_identity_and_cannot_erase_other_work():
    state = ActivityState()
    prompts = state.mapping("prompt")
    callbacks = state.set("callback")
    future = object()
    prompts["same"] = future
    callbacks.add("same")
    callbacks.discard("same")
    state.uncertain("callback", "same")
    assert prompts["same"] is future and state.activity == "active"
    prompts.pop("same")
    assert state.activity == "unknown"
    assert state.snapshot()[1]["kind"] == "callback"


def test_configured_guard_refuses_native_transition_without_mutation():
    definitions = activity_definitions()
    work = definitions["native_work"]
    definitions["native_work"] = replace(work, transitions=tuple(
        replace(item, guard=lambda context: context["identity"] != "refused")
        if item.event == "start" and None in item.sources else item for item in work.transitions))
    state = ActivityState(StateEngine(definitions))
    pending = state.mapping("prompt")
    pending["accepted"] = object()
    before = state.snapshot()
    with pytest.raises(OrchestrationError) as error:
        pending["refused"] = object()
    assert error.value.code == "state_guard_rejected"
    assert state.snapshot() == before and set(pending) == {"accepted"}


def test_connection_has_one_engine_controlled_lifecycle():
    connection = NativeConnection(StateEngine(activity_definitions()), "incarnation")
    assert connection.state == "starting"
    connection.transition("connect")
    connection.transition("disconnect")
    connection.transition("close")
    before = (connection.state, connection.version)
    with pytest.raises(OrchestrationError):
        connection.transition("connect")
    assert (connection.state, connection.version) == before == ("closed", 4)


def _callback_fixture(protocol, callback, engine=None):
    options = {"command": [sys.executable, "unused"]}
    capability = Capability("inspect", "Read fixture", {"type": "object"})
    if protocol == "claude":
        adapter = ClaudeSessionDriver(options)
        if engine is not None:
            adapter.bind_state_engine(engine)
        adapter.bind_application_tools([capability], callback)
        live = _ClaudeLive({}, "native", activity_model=ActivityState(engine))
        adapter._hook(live, {"session_id": "native", "hook_event_name": "PreToolUse",
            "tool_name": "mcp__xorchestrate__inspect", "tool_use_id": "call", "tool_input": {}})
        def invoke():
            return adapter._tool(live, {"name": "inspect", "arguments": {}, "mcp_request_id": 1})
    else:
        kind = {"codex": CodexSessionDriver, "acp": ACPSessionDriver, "grok": GrokSessionDriver}[protocol]
        adapter = kind(options)
        if engine is not None:
            adapter.bind_state_engine(engine)
        live = _LiveSession({}, native_id="native", activity_model=ActivityState(engine))
        if protocol == "acp":
            adapter.set_request_handler(callback)
            def invoke():
                return adapter._request(live, "fixture/request", {"sessionId": "native"})
        else:
            adapter.bind_application_tools([capability], callback)
            if protocol == "codex":
                def invoke():
                    return adapter._request(live, "item/tool/call", {
                        "threadId": "native", "callId": "call", "tool": "inspect", "arguments": {}})
            else:
                live.grok = {"active_prompts": set(), "interjections": {}, "mcp_requests": {},
                    "calls": {"call": {"action": "inspect", "arguments": {}, "used": False}},
                    "condition": threading.Condition(live.lock)}
                def invoke():
                    return adapter._gateway(live, "tools/call", {"name": "inspect", "arguments": {},
                        "mcp_connection_id": "connection", "mcp_request_id": 1})
    return adapter, live, invoke


@pytest.mark.parametrize("protocol", ["codex", "acp", "grok", "claude"])
@pytest.mark.parametrize("knowledge,expected", [("preflight", "idle"), ("unknown", "unknown")])
def test_callback_activity_uses_effect_evidence_not_exception_category(protocol, knowledge, expected):
    def refusal(*args):
        if knowledge == "preflight":
            with before_effect():
                raise OrchestrationError("Refused before the handler.", "same_code")
        raise OrchestrationError("Handler might have acted.", "same_code")

    adapter, live, invoke = _callback_fixture(protocol, refusal)
    try:
        if protocol == "codex":
            assert invoke()["success"] is False
        else:
            with pytest.raises(OrchestrationError):
                invoke()
        assert live.activity == expected
        assert live.activity_model.activity == expected
        assert not live.activity_model.active("callback")
    finally:
        adapter.close()


def test_completed_retention_is_bounded_without_losing_unresolved_work():
    state = ActivityState(max_completed=2)
    state.begin("prompt", "pending")
    state.uncertain("prompt", "uncertain")
    for identity in "ABCD":
        state.begin("prompt", identity)
        state.finish("prompt", identity)
    retained = {row["identity"]: row["state"] for row in state.snapshot()}
    assert retained == {"pending": "pending", "uncertain": "unknown", "C": "completed", "D": "completed"}
    state.begin("prompt", "D")
    assert state.active("prompt") == ("pending",)


def test_codex_claimed_call_rejected_before_application_does_not_poison_activity():
    invoked = []
    adapter = CodexSessionDriver({"command": [sys.executable, "unused"]})
    adapter.bind_application_tools([Capability("inspect", "fixture", {"type": "object"})],
        lambda *args: invoked.append(args))
    live = _LiveSession({}, native_id="native")
    try:
        response = adapter._request(live, "item/tool/call", {
            "threadId": "native", "callId": "claimed", "tool": "inspect", "arguments": ["not-an-object"]})
        assert response["success"] is False
        assert invoked == [] and live.activity == "idle"
        assert "claimed" in live.calls
        # The same claimed request remains a refusal on replay, never an effect.
        assert adapter._request(live, "item/tool/call", {
            "threadId": "native", "callId": "claimed", "tool": "inspect", "arguments": ["not-an-object"]}) == response
        assert invoked == [] and live.activity == "idle"
    finally:
        adapter.close()


@pytest.mark.parametrize("protocol", ["grok", "claude"])
@pytest.mark.parametrize("refuse_fallback", [False, True])
def test_callback_result_is_retained_when_configured_settlement_refuses(protocol, refuse_fallback):
    definitions = activity_definitions()
    effect = definitions["native_effect"]
    definitions["native_effect"] = replace(effect, transitions=tuple(
        replace(item, guard=lambda context: False) if item.event == "succeed" or (refuse_fallback and item.event == "uncertain") else item
        for item in effect.transitions))
    calls = []

    def completed(*args):
        calls.append(args)
        return {"observed": "completed-once"}

    adapter, live, invoke = _callback_fixture(protocol, completed, StateEngine(definitions))
    try:
        with pytest.raises(OrchestrationError) as error:
            invoke()
        assert error.value.details["outcome"] == "completed"
        ledger = live.mcp_calls if protocol == "claude" else live.grok["mcp_requests"]
        row = next(iter(ledger.values()))
        assert row["state"] == ("pending" if refuse_fallback else "unknown")
        assert row["effect_outcome"] == "completed"
        if refuse_fallback:
            assert row["recording_error"] == "state_guard_rejected"
        assert row["result"] == {"observed": "completed-once"}
        assert live.activity == "unknown"
        with pytest.raises(OrchestrationError) as replay:
            invoke()
        assert replay.value.details["outcome"] == "completed"
        assert len(calls) == 1 and live.activity == "unknown"
    finally:
        adapter.close()


def test_codex_unconfigured_fallback_refuses_before_native_handler():
    adapter = CodexSessionDriver({"command": [sys.executable, "unused"]})
    live = _LiveSession({}, native_id="native")
    try:
        response = adapter._request(live, "item/tool/call", {
            "threadId": "native", "callId": "claimed", "tool": "unconfigured", "arguments": {}})
        assert response["success"] is False and live.activity == "idle"
    finally:
        adapter.close()


@pytest.mark.parametrize("protocol", ["grok", "claude"])
def test_refusal_fact_survives_denied_failure_recording_and_replay(protocol):
    definitions = activity_definitions()
    effect = definitions["native_effect"]
    definitions["native_effect"] = replace(effect, transitions=tuple(
        replace(item, guard=lambda context: False) if item.event == "fail" else item
        for item in effect.transitions))
    calls = []

    def refused(*args):
        calls.append(args)
        with before_effect():
            raise OrchestrationError("Refused before application effect.", "denied")

    adapter, live, invoke = _callback_fixture(protocol, refused, StateEngine(definitions))
    try:
        for _ in range(2):
            with pytest.raises(OrchestrationError) as error:
                invoke()
            assert error.value.details["outcome"] == "not_started"
            assert live.activity == "idle"
        assert len(calls) == 1
        ledger = live.mcp_calls if protocol == "claude" else live.grok["mcp_requests"]
        row = next(iter(ledger.values()))
        assert row["state"] == "pending" and row["effect_outcome"] == "not_started"
        assert row["recording_error"] == "state_guard_rejected"
    finally:
        adapter.close()
