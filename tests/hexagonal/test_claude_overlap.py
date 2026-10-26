"""F06: one native Stop cannot complete unrelated outstanding work."""
import json
import sys
import threading
from functools import partial

import pytest

from tests.hexagonal.support.native import native_driver, wait_for
from xorchestrate.adapters.claude import ClaudeSessionDriver
from xorchestrate.model import Capability

wait = partial(wait_for, message="native hook schedule did not complete")


def assert_outstanding_is_active(status, outstanding):
    assert outstanding and status["activity"] == "active", "F06_OUTSTANDING_CALLBACK_IS_ACTIVE"


def overlap(tmp_path, fault=False):
    driver = native_driver("claude", state=tmp_path)
    entered = {name: threading.Event() for name in ("A", "B")}
    release = {name: threading.Event() for name in ("A", "B")}
    observations, finished = [], []
    def callback(action, args, context, key):
        name = args["nonce"]
        entered[name].set()
        assert release[name].wait(8), "test must release its own callback"
        finished.append(name)
        return {"ok": True}
    driver.bind_application_tools([Capability("hold", "hold", {"type": "object"})], callback)
    driver.bind_observer(lambda context, event: observations.append(event))
    original_hook = driver._hook
    completed_hooks = []
    def measured_hook(live, packet):
        result = original_hook(live, packet)
        if packet.get("hook_event_name") == "Stop":
            if fault:
                with live.lock:
                    live.activity = "idle"
            completed_hooks.append(packet.get("prompt_id"))
        return result
    driver._hook = measured_hook
    try:
        session = driver.start({"context": {"actor": "worker", "session_id": "overlap"}})
        driver.send(session, {"hold": "A"})
        assert entered["A"].wait(3)
        driver.send(session, {"hold": "B"})
        assert entered["B"].wait(3)
        assert driver.status(session)["activity"] == "active"
        release["A"].set()
        wait(lambda: len(completed_hooks) == 1)
        after_a = driver.status(session)
        outstanding = "B" not in finished and not release["B"].is_set()
        if fault:
            assert after_a["activity"] == "idle" and outstanding, "fault activated while B stayed held"
        # Always retain the legitimate completed/cleanup control before assertion.
        release["B"].set()
        wait(lambda: len(completed_hooks) == 2)
        assert driver.status(session)["activity"] == "idle"
        assert sorted(finished) == ["A", "B"]
        (tmp_path / "observations.json").write_text(json.dumps({"fault": fault, "after_first_stop": after_a,
            "b_outstanding": outstanding, "after_both_stopped": driver.status(session),
            "completed_callbacks": finished, "completed_prompt_ids": completed_hooks,
            "observations": observations}, indent=2))
        assert_outstanding_is_active(after_a, outstanding)
    finally:
        for event in release.values():
            event.set()
        owned = list(driver._sessions.values())
        driver.close()
        assert not driver._sessions
        assert all(live.process.poll() is not None and not live.gateway.thread.is_alive() for live in owned)
        (tmp_path / "cleanup.json").write_text(json.dumps({"sessions_remaining": len(driver._sessions),
            "owned_processes_exited": sum(live.process.poll() is not None for live in owned),
            "gateway_threads_exited": sum(not live.gateway.thread.is_alive() for live in owned)}, indent=2))


def test_real_claude_hooks_keep_second_callback_active(tmp_path):
    overlap(tmp_path)


def test_known_bad_global_idle_stop_is_detected(tmp_path):
    with pytest.raises(AssertionError, match="F06_OUTSTANDING_CALLBACK_IS_ACTIVE"):
        overlap(tmp_path, fault=True)


def test_unknown_state_or_no_outstanding_work_is_not_target_detection():
    with pytest.raises(AssertionError, match="F06_OUTSTANDING_CALLBACK_IS_ACTIVE"):
        assert_outstanding_is_active({"activity": "unknown"}, True)
    with pytest.raises(AssertionError, match="F06_OUTSTANDING_CALLBACK_IS_ACTIVE"):
        assert_outstanding_is_active({"activity": "active"}, False)


def test_duplicate_or_unmatched_stop_and_sessionstart_cannot_clear_known_turns():
    from xorchestrate.adapters.claude import _ClaudeLive
    driver = ClaudeSessionDriver({"command": [sys.executable, "unused"]})
    live = _ClaudeLive({}, "native")
    def hook(name, prompt=None):
        return driver._hook(live, {"session_id": "native", "hook_event_name": name, "prompt_id": prompt})
    hook("UserPromptSubmit", "A")
    hook("UserPromptSubmit", "B")
    hook("Stop", "A")
    hook("Stop", "A")
    assert live.active_turns == {"B"} and live.activity == "active"
    hook("SessionStart")
    assert live.active_turns == {"B"} and live.activity == "active"
    hook("Stop", "unobserved")
    hook("Stop")
    assert live.active_turns == {"B"} and live.activity == "active"
    hook("Stop", "B")
    assert live.activity == "unknown", "unmatched completion must retain uncertainty"
    driver.close()


def test_anonymous_overlap_never_claims_unqualified_idle_from_duplicate_stop():
    from xorchestrate.adapters.claude import _ClaudeLive
    driver = ClaudeSessionDriver({"command": [sys.executable, "unused"]})
    live = _ClaudeLive({}, "native")
    for name in ("UserPromptSubmit", "UserPromptSubmit", "Stop", "Stop"):
        driver._hook(live, {"session_id": "native", "hook_event_name": name})
    assert live.activity == "unknown", "unidentified overlapping completions cannot be deduplicated"
    driver.close()


def test_failed_callback_retires_running_tool_and_retains_uncertainty():
    from xorchestrate.adapters.claude import _ClaudeLive
    driver = ClaudeSessionDriver({"command": [sys.executable, "unused"]})
    def failed(*args):
        raise TimeoutError("unrelated callback timeout")
    driver.bind_application_tools([Capability("fail", "fail")], failed)
    live = _ClaudeLive({}, "native")
    driver._hook(live, {"session_id": "native", "hook_event_name": "PreToolUse", "tool_name": "mcp__xorchestrate__fail",
        "tool_use_id": "call", "tool_input": {}, "prompt_id": "A"})
    with pytest.raises(TimeoutError):
        driver._tool(live, {"name": "fail", "arguments": {}, "mcp_request_id": "one"})
    assert not live.active_tools
    driver._hook(live, {"session_id": "native", "hook_event_name": "StopFailure", "prompt_id": "A"})
    assert live.activity == "unknown"
    driver.close()
