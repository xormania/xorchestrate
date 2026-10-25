import threading
import time
from pathlib import Path

import pytest

from tests.hexagonal.support.native import native_drivers, peer_options, wait_for
from xorchestrate.adapters.native_gateway import request
from xorchestrate.model import Capability, Conflict, OrchestrationError


def test_wait_for_retries_safe_observation_conflict():
    attempts = []

    def observation():
        attempts.append(True)
        if len(attempts) == 1:
            raise Conflict("Evidence changed", retry_safe=True)
        return True

    wait_for(observation)
    assert len(attempts) == 2


@pytest.mark.parametrize("error", [Conflict("Unresolved effect"),
    OrchestrationError("Unknown effect", "operation_unknown", {"retry_safe": True})])
def test_wait_for_propagates_unsafe_errors(error):
    attempts = []

    def observation():
        attempts.append(True)
        raise error

    with pytest.raises(type(error)) as raised:
        wait_for(observation)
    assert raised.value is error and len(attempts) == 1


def test_wait_for_safe_conflict_keeps_original_deadline(monkeypatch):
    ticks = iter([0, 0, 1])
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(time, "sleep", lambda delay: None)

    def observation():
        raise Conflict("Evidence keeps changing", retry_safe=True)

    with pytest.raises(AssertionError, match="Expected native observation did not arrive"):
        wait_for(observation, timeout=.5)


@pytest.fixture
def make_driver(tmp_path):
    with native_drivers() as create:
        def make(**options):
            driver = create("claude", state=tmp_path, **options)
            observations, calls = [], []
            driver.bind_observer(lambda context, event: observations.append((context, event)))
            caps = [Capability("echo", "Echo", {"type": "object", "properties": {"marker": {}, "nonce": {}}, "required": ["marker", "nonce"]}),
                    Capability("hold", "Held callback", {"type": "object", "properties": {"nonce": {}}, "required": ["nonce"]})]
            driver.bind_application_tools(caps, lambda action, args, context, key: calls.append((action, args, context, key)) or {"accepted": True})
            return driver, observations, calls

        yield make


def test_claude_retained_context_callbacks_progress_without_poll_and_native_identity(make_driver):
    driver, observations, calls = make_driver()
    session = driver.start({"context": {"actor": "worker", "session_id": "app-session"}})
    first = driver.send(session, {"remember": "private-marker"})
    assert first["submitted"] and "completed" not in first
    wait_for(lambda: any(e[1]["kind"] == "turn_completed" for e in observations))
    driver.deliver(session, {"message_id": "message", "recipient": "worker", "attempt_id": "attempt", "incarnation": session["incarnation"],
                            "body": {"recall": "nonce-1"}})
    wait_for(lambda: len(calls) == 1)
    driver.send(session, {"recall": "nonce-2"})
    wait_for(lambda: len(calls) == 2)
    assert [call[1] for call in calls] == [{"marker": "private-marker", "nonce": "nonce-1"}, {"marker": "private-marker", "nonce": "nonce-2"}]
    assert all(call[2]["native_id"] == session["native_id"] and call[2]["session_id"] == "app-session" for call in calls)
    assert all(call[2]["incarnation"] == session["incarnation"] and call[2]["call_id"] for call in calls)
    assert any(e[1]["kind"] == "native_tool" and e[1]["source"] == "claude/PreToolUse" for e in observations)
    assert any(e[1]["kind"] == "submitted" and e[1]["message"].get("attempt_id") == "attempt" for e in observations)


def test_claude_channel_delivery_during_outstanding_callback_and_unrelated_session(make_driver):
    driver, observations, calls = make_driver()
    entered, release = threading.Event(), threading.Event()

    def callback(action, args, context, key):
        calls.append((action, args, context, key))
        if action == "hold":
            entered.set()
            assert release.wait(5)
        return {"accepted": True}

    driver.bind_application_tools(list(driver._capabilities.values()), callback)
    first = driver.start({})
    second = driver.start({})
    try:
        driver.send(first, {"hold": "busy"})
        assert entered.wait(3)
        driver.deliver(first, {"recall": "during-hold"})
        driver.send(second, {"recall": "unrelated"})
        wait_for(lambda: len(calls) == 3)
        assert not release.is_set()
        assert {call[1].get("nonce") for call in calls} == {"busy", "during-hold", "unrelated"}
        # Observation and control do not wait for the tool response.
        assert driver.poll(first)
        assert driver.stop(second)["stopped"]
    finally:
        release.set()


def test_claude_requested_resolved_and_observed_settings_and_resume(make_driver):
    driver, _, calls = make_driver(launch={"model": "trusted-model", "effort": "high"}, request_parameters={"type": "object", "properties": {"model": {"type": "string"}}, "additionalProperties": False})
    spec = {"parameters": {"model": "requested-model"}}
    session = driver.start(spec)
    assert session["settings"]["requested"]["model"] == "requested-model"
    assert session["settings"]["resolved"]["model"] == "trusted-model"
    assert session["settings"]["observed"] == {"model": "fixture-model", "effort": None}
    driver.send(session, {"remember": "retained"})
    wait_for(lambda: any(e["kind"] == "turn_completed" for e in driver._get(session).events))
    driver.stop(session)
    resumed = driver.resume(session, {})
    assert resumed["settings"]["requested"] == session["settings"]["requested"]
    assert resumed["native_id"] == session["native_id"]
    assert resumed["incarnation"] != session["incarnation"]
    driver.send(resumed, {"recall": "after-resume"})
    wait_for(lambda: calls)
    assert calls[-1][1]["marker"] == "retained"
    with pytest.raises(OrchestrationError, match="stale"):
        driver.send(session, {"recall": "stale"})


def test_claude_changed_configuration_requires_explicit_policy(make_driver):
    original, _, _ = make_driver(launch={"model": "one"})
    source = original.start({})
    original.stop(source)
    changed, _, _ = make_driver(launch={"model": "two"})
    with pytest.raises(OrchestrationError) as error:
        changed.resume(source, {})
    assert error.value.code == "configuration_changed"
    allowed, _, _ = make_driver(launch={"model": "two"}, allow_configuration_change=True)
    resumed = allowed.resume(source, {})
    assert resumed["settings"]["configuration_change"]["from"] == source["configuration_id"]


def test_claude_native_permissions_independent_of_application_tools(make_driver):
    driver, observations, calls = make_driver(permissions={"Bash": "deny", "Read": "allow", "application_tools": "allow"})
    session = driver.start({})
    driver.send(session, {"native_tool": "Bash"})
    driver.send(session, {"native_tool": "Read"})
    wait_for(lambda: len(calls) == 2)
    assert {call[1]["nonce"] for call in calls} == {"deny", "allow"}
    assert {e[1]["decision"] for e in observations if e[1]["kind"] == "native_permission"} == {"deny", "allow"}


def test_claude_callback_requires_matching_native_hook(make_driver, tmp_path):
    driver, observations, calls = make_driver(env={"CLAUDE_STATE_DIR": str(tmp_path), "CLAUDE_FAULT": "missing_tool_hook"})
    session = driver.start({})
    driver.send(session, {"recall": "unproven"})
    wait_for(lambda: any(e[1]["kind"] in {"turn_completed", "turn_failed"} for e in observations))
    assert calls == [], "CLAUDE_NATIVE_HOOK_REQUIRED"
    assert not any(e[1]["kind"] == "tool_result" for e in observations)


@pytest.mark.parametrize("fault,code", [("wrong_identity", "session_identity"), ("missing_registration", "session_start_timeout"), ("forged_registration", "session_start_timeout")])
def test_claude_start_requires_observed_identity_and_channel_registration(make_driver, tmp_path, fault, code):
    driver, _, _ = make_driver(env={"CLAUDE_STATE_DIR": str(tmp_path), "CLAUDE_FAULT": fault}, startup_timeout=0.7)
    with pytest.raises(OrchestrationError) as error:
        driver.start({})
    assert error.value.code == code
    assert driver._sessions == {}


def test_claude_conflicting_native_call_id_and_missing_hook_rejected(make_driver):
    driver, _, calls = make_driver()
    session = driver.start({})
    live = driver._get(session)
    descriptor = live.gateway.descriptor_path
    with pytest.raises(RuntimeError, match="native_evidence_missing"):
        request(descriptor, "tools/call", {"name": "echo", "arguments": {"marker": "x", "nonce": "n"}, "mcp_request_id": "1"})
    packet = {"session_id": session["native_id"], "hook_event_name": "PreToolUse", "tool_name": "mcp__xorchestrate__echo",
              "tool_use_id": "native-call", "tool_input": {"marker": "x", "nonce": "n"}}
    request(descriptor, "hook", packet)
    request(descriptor, "tools/call", {"name": "echo", "arguments": packet["tool_input"], "mcp_request_id": "2"})
    request(descriptor, "tools/call", {"name": "echo", "arguments": packet["tool_input"], "mcp_request_id": "2"})
    assert len(calls) == 1
    with pytest.raises(RuntimeError, match="native_call_conflict"):
        request(descriptor, "hook", {**packet, "tool_input": {"marker": "different", "nonce": "n"}})


def test_claude_denied_application_tool_cannot_execute_via_gateway(make_driver):
    driver, _, calls = make_driver(permissions={"application_tools": "deny"})
    session = driver.start({})
    descriptor = driver._get(session).gateway.descriptor_path
    args = {"marker": "not-executed", "nonce": "denied"}
    decision = request(descriptor, "hook", {"session_id": session["native_id"], "hook_event_name": "PreToolUse",
        "tool_name": "mcp__xorchestrate__echo", "tool_use_id": "denied-call", "tool_input": args})
    assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"
    with pytest.raises(RuntimeError, match="denied"):
        request(descriptor, "tools/call", {"name": "echo", "arguments": args, "mcp_request_id": "denied-request"})
    assert calls == []


def test_claude_raw_native_debug_retention_is_explicit_and_private(make_driver, tmp_path):
    driver, _, _ = make_driver(evidence_directory=str(tmp_path / "private-native"))
    session = driver.start({})
    directory = Path(session["diagnostics"]["directory"])
    assert session["diagnostics"]["retained"]
    assert directory.stat().st_mode & 0o077 == 0
    driver.stop(session)
    assert "Channel notifications registered" in Path(session["diagnostics"]["debug_file"]).read_text()


def test_claude_resume_replaces_same_instance_connection_after_verified_launch(make_driver):
    driver, _, _ = make_driver()
    source = driver.start({})
    old_process = driver._get(source).process
    resumed = driver.resume(source, {})
    assert resumed["native_id"] == source["native_id"]
    assert resumed["incarnation"] != source["incarnation"]
    assert old_process.poll() is not None
    with pytest.raises(OrchestrationError) as error:
        driver.send(source, {"recall": "obsolete"})
    assert error.value.code == "session_unavailable"
    assert driver.send(resumed, {"remember": "replacement"})["submitted"]


def test_claude_application_callback_through_active_composition_and_sqlite(tmp_path):
    from xorchestrate.composition import build
    config = {"store": {"adapter": "sqlite", "options": {"path": str(tmp_path / "state.sqlite3")}},
              "sessions": {"worker": {"adapter": "claude", "options": peer_options("claude", state=tmp_path / "native", application_tools=True)}}}
    with build(config) as runtime:
        app = runtime.application
        work = app.call("create_work", {"goal": "Retain native callback provenance"})["work"]
        session = app.call("start_session", {"driver": "worker", "specification": {}, "work_id": work["id"], "idempotency_key": "launch"})["session"]
        app.call("send_session", {"session_id": session["id"], "message": {"application_call": {
            "action": "record_result", "arguments": {"result": {"marker": "from-native"}}}}, "idempotency_key": "send"})
        wait_for(lambda: any(row.get("result") == {"marker": "from-native"} for row in app.call("get_work", {"work_id": work["id"]})["evidence"]))
        observations = app.call("native_observations", {"session_id": session["id"]})["observations"]
        assert any(row["observation"].get("action") == "record_result" and row["origin"] == "adapter" for row in observations)
        assert all(row["observation"]["native_id"] == session["backend"]["native_id"] for row in observations)
