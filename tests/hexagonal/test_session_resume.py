"""Resume authorization across real native-protocol processes and durable state."""

from unittest.mock import patch

import pytest

from tests.hexagonal.support.native import peer_options, wait_for
from xorchestrate.composition import build
from xorchestrate.model import OrchestrationError


def configuration(tmp_path, protocol, storage="memory", guardrail=None):
    options = peer_options(protocol, fixture="session")
    return {
        "store": {"adapter": storage, "options": {"path": str(tmp_path / "state.sqlite")} if storage == "sqlite" else {}},
        "sessions": {"agent": {"adapter": protocol, "options": options}},
        "guardrail": {"adapter": "rules", "options": guardrail or {}},
    }


@pytest.mark.parametrize("protocol", ["codex", "acp"])
@pytest.mark.parametrize("storage", ["memory", "sqlite"])
def test_native_resume_cannot_bypass_denied_source_control(tmp_path, protocol, storage):
    rules = {"rules": [{"actions": ["send_session"], "actors": ["worker"],
                        "where": {"session_actor": "owner"}, "effect": "deny"}]}
    with build(configuration(tmp_path, protocol, storage, rules)) as runtime:
        app = runtime.application
        source = app.call("start_session", {
            "driver": "agent", "specification": {}, "idempotency_key": "owner-start",
        })["session"]
        listed = app.call("list_sessions", {}, actor="worker")["sessions"]
        assert listed[0]["backend"]["native_id"] == source["backend"]["native_id"]
        with pytest.raises(OrchestrationError) as denied:
            app.call("send_session", {"session_id": source["id"], "message": {"text": "blocked"},
                                      "idempotency_key": "denied-send"}, actor="worker")
        assert denied.value.code == "denied"

        driver = app.sessions["agent"]
        with patch.object(driver, "resume", wraps=driver.resume) as resume:
            with pytest.raises(OrchestrationError) as denied:
                app.call("start_session", {"driver": "agent", "specification": {},
                    "resume_session_id": source["id"], "idempotency_key": "denied-resume"}, actor="worker")
            assert denied.value.code == "denied"
            resume.assert_not_called()
        with patch("xorchestrate.adapters.sessions.JsonLineProcess") as process:
            with pytest.raises(OrchestrationError):
                app.call("start_session", {"driver": "agent",
                    "specification": {"resume": source["backend"]["native_id"]},
                    "idempotency_key": "raw-native-id"}, actor="worker")
            process.assert_not_called()
        assert [session["id"] for session in app.call("list_sessions", {})["sessions"]] == [source["id"]]
        assert app.call("send_session", {"session_id": source["id"], "message": {"text": "owner continues"},
                                         "idempotency_key": "owner-send"})["output"]["native_id"] == source["backend"]["native_id"]


@pytest.mark.parametrize("protocol", ["codex", "acp"])
def test_authorized_resume_after_restart_binds_callbacks_to_destination(tmp_path, protocol):
    config = configuration(tmp_path, protocol, "sqlite")
    if protocol == "codex":
        config["sessions"]["agent"]["options"]["application_tools"] = True
    with build(config) as runtime:
        app = runtime.application
        app.call("add_actor", {"actor_id": "delegate"})
        original_work = app.call("create_work", {"goal": "Original conversation"})["work"]
        target_work = app.call("create_work", {"goal": "Explicitly delegated continuation"})["work"]
        source = app.call("start_session", {"driver": "agent", "specification": {},
            "work_id": original_work["id"], "idempotency_key": "original"})["session"]

    with build(config) as runtime:
        app = runtime.application
        assert app.call("list_sessions", {})["sessions"][0]["status"] == "disconnected"
        resumed = app.call("start_session", {"driver": "agent", "resume_session_id": source["id"],
            "specification": {"context": {"actor": "forged"}}, "actor_id": "delegate",
            "work_id": target_work["id"], "idempotency_key": "authorized-resume"}, actor="controller")["session"]
        assert resumed["backend"]["native_id"] == source["backend"]["native_id"]
        assert resumed["backend"]["instance"] != source["backend"]["instance"]
        assert resumed["actor_id"] == "delegate"
        assert resumed["work_id"] == target_work["id"]
        output = app.call("send_session", {"session_id": resumed["id"], "message": {"text": "record"},
                                           "idempotency_key": "callback"}, actor="delegate")["output"]
        if protocol == "acp":
            # ACP exposes no application tools; the untranslated native request is refused.
            def completion():
                rows = app.call("native_observations", {"session_id": resumed["id"]})["observations"]
                return [row for row in rows if row["observation"]["kind"] == "native_completion"]
            completed = wait_for(completion, timeout=2)
            assert completed[0]["created_by"] == "delegate" and completed[0]["work_id"] == target_work["id"]
            assert completed[0]["observation"]["result"]["tool_response"]["error"]["code"] == -32601
            evidence = app.call("get_work", {"work_id": target_work["id"]})["evidence"]
            assert all(item["result"] != "from-native" for item in evidence)
            return
        assert output["tool_response"]["result"]["success"] is True
        evidence = app.call("get_work", {"work_id": target_work["id"]})["evidence"]
        assert ("from-native", "delegate") in [(item["result"], item["created_by"]) for item in evidence]
        assert all(item["created_by"] == "delegate" for item in evidence)
        assert not app.call("get_work", {"work_id": original_work["id"]})["evidence"]
