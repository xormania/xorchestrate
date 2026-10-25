"""F04 regressions through native subprocess/MCP adapters, plus sustained faults.

The cache-bypass fault deliberately removes only replay validation after a valid
call. Its target is the currentness/authorization assertion, not an adapter crash.
"""
import json
import sys
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path

import pytest

from tests.hexagonal.test_work_acceptance import MeasuringProvider, accept, make_app, measure, setup_work
from xorchestrate.adapters.bridge import SessionBridgeHost
from xorchestrate.adapters.claude import ClaudeSessionDriver
from xorchestrate.adapters.grok import GrokSessionDriver
from xorchestrate.adapters.sessions import CodexSessionDriver
from xorchestrate.composition import build
from xorchestrate.model import Conflict, OrchestrationError

FIXTURES = Path(__file__).parent / "fixtures"


def retry(app, action, args):
    for attempt in range(8):
        try:
            return app.call(action, args)
        except Conflict as error:
            if not error.details.get("retry_safe") or attempt == 7:
                raise


@contextmanager
def native_runtime(protocol, tmp_path, bridge=False):
    wire = tmp_path / "wire.jsonl"
    options = {"command": [sys.executable, str(FIXTURES / ("claude_native.py" if protocol == "claude" else "native_progress_peer.py")),
                            *([] if protocol == "claude" else [protocol])],
               "env": {"PEER_RESULTS": str(wire), "CLAUDE_STATE_DIR": str(tmp_path / "claude")},
               "timeout": 5, "application_tools": True}
    if protocol == "claude":
        options.update(terminal="discard", startup_timeout=10)
    if protocol == "grok":
        options["resume_supported"] = True
    config = {"store": {"adapter": "sqlite", "options": {"path": str(tmp_path / "state.sqlite")}},
              "guardrail": {"adapter": "rules", "options": {"rules": [{"actions": ["get_acceptance", "create_work"],
                  "roles": ["revoked"], "effect": "deny"}]}},
              "sessions": {"native": {"adapter": protocol, "options": options}}}
    with ExitStack() as stack:
        if bridge:
            native = {"codex": CodexSessionDriver, "claude": ClaudeSessionDriver, "grok": GrokSessionDriver}[protocol](
                {key: value for key, value in options.items() if key != "application_tools"})
            host = SessionBridgeHost(native, tmp_path / "host", capabilities=make_app().describe(), options={"transport": "fifo"})
            stack.callback(host.close)
            config["sessions"]["native"] = {"adapter": "bridge", "options": {"endpoint": str(host.endpoint),
                "transport": "fifo", "timeout": 5, "application_tools": True}}
        runtime = stack.enter_context(build(config, Path.cwd()))
        app = runtime.application
        app.providers["checks"] = MeasuringProvider()
        actor = app.call("add_actor", {"actor_id": "owner", "roles": ["allowed"]})["actor"]
        work, basis = setup_work(app, {"A": "A"})
        measure(app, work)
        assert accept(app, work)["accepted"]
        session = retry(app, "start_session", {"driver": "native", "specification": {}, "work_id": work["id"], "idempotency_key": "start"})["session"]
        adapter = app.sessions["native"]
        rejected = []
        replay = getattr(adapter, "_application_replay", None)

        def checked(*args):
            try:
                return replay(*args)
            except OrchestrationError as error:
                rejected.append(error.code)
                raise
        if replay is not None:
            adapter._application_replay = checked

        def send(label, action="get_acceptance", arguments=None, fresh=False):
            arguments = {"work_id": work["id"]} if arguments is None else arguments
            body = {"replay_fixture": {"label": label, "call_id": "fresh" if fresh else "fixed",
                                      "mcp_request_id": "fresh" if fresh else "fixed"}}
            if protocol == "claude":
                body["application_call"] = {"action": action, "arguments": arguments}
                message = body
            else:
                body.update(action="tool", name=action, arguments=arguments)
                message = {"text": json.dumps(body)}
            retry(app, "send_session", {"session_id": session["id"], "message": message, "idempotency_key": label})
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                records = [json.loads(row) for row in wire.read_text().splitlines()] if wire.exists() else []
                matches = [row for row in records if row["label"] == label]
                if matches:
                    result = matches[-1]["result"]
                    if protocol == "codex":
                        return json.loads(result["contentItems"][0]["text"])
                    if "error" in result:
                        return result
                    if protocol == "grok":
                        result = result["result"]
                    return result["structuredContent"]
                time.sleep(.01)
            raise AssertionError("native replay fixture did not return a wire result")
        yield app, adapter, work, basis, actor, send, rejected


def assert_replay_rejected(result, errors, expected):
    assert "error" in result and expected in errors, "F04_CURRENT_NATIVE_REPLAY_REQUIRED"


@pytest.mark.parametrize("protocol", ["codex", "claude", "grok"])
@pytest.mark.parametrize("change", ["owner_revision", "permission_revoked"])
def test_adapter_cached_acceptance_requires_current_basis_and_authority(protocol, change, tmp_path):
    exercise_replay(protocol, change, tmp_path)


def exercise_replay(protocol, change, tmp_path, fault=False, bridge=False):
    with native_runtime(protocol, tmp_path, bridge=bridge) as (app, adapter, work, basis, actor, send, rejected):
        first = send("first")
        assert first["accepted"]
        unchanged = send("unchanged")
        assert unchanged == first, "unchanged acceptance remains safely replayable"
        if change == "owner_revision":
            retry(app, "set_requirements", {"work_id": work["id"], "expected_revision": 1,
                "objective": "Owner adds B", "outcomes": {"A": "A", "B": "B"},
                "checks": {**basis["checks"], "B": {"capability": "checks/measure", "outcomes": ["B"]}}})
            expected = "stale_native_result"
            assert not app.call("get_acceptance", {"work_id": work["id"]})["accepted"]
        else:
            app.call("update_actor", {"actor_id": "owner", "expected_version": actor["version"], "changes": {"roles": ["revoked"]}})
            expected = "denied"
            with pytest.raises(OrchestrationError, match=".") as denied:
                app.call("get_acceptance", {"work_id": work["id"]})
            assert denied.value.code == "denied"
        if fault:
            adapter._application_replay = None
            assert adapter._application_replay is None, "fault activation: completed cache bypasses application validation"
        result = send("changed")
        if fault:
            assert result == first and result["accepted"], "fault must actually expose the obsolete accepted result"
        else:
            if change == "owner_revision":
                assert send("fresh", fresh=True)["accepted"] is False
        assert_replay_rejected(result, rejected, expected)


@pytest.mark.parametrize("protocol", ["codex", "claude", "grok"])
def test_known_bad_cache_bypass_is_detected(protocol, tmp_path):
    with pytest.raises(AssertionError, match="F04_CURRENT_NATIVE_REPLAY_REQUIRED"):
        exercise_replay(protocol, "owner_revision", tmp_path, fault=True)


@pytest.mark.parametrize("protocol", ["codex", "claude", "grok"])
def test_native_cache_inside_external_bridge_revalidates(protocol, tmp_path):
    exercise_replay(protocol, "owner_revision", tmp_path, bridge=True)


@pytest.mark.parametrize("protocol", ["codex", "claude", "grok"])
def test_cached_accept_work_cannot_replay_superseded_decision(protocol, tmp_path):
    with native_runtime(protocol, tmp_path) as (app, adapter, work, basis, actor, send, rejected):
        first = send("first", "accept_work")
        assert first["acceptance"]["accepted"]
        assert send("repeat", "accept_work") == first
        retry(app, "set_requirements", {"work_id": work["id"], "expected_revision": 1,
            "objective": "Owner adds B", "outcomes": {"A": "A", "B": "B"},
            "checks": {**basis["checks"], "B": {"capability": "checks/measure", "outcomes": ["B"]}}})
        assert_replay_rejected(send("changed", "accept_work"), rejected, "acceptance_stale")
        state = app.call("get_acceptance", {"work_id": work["id"]})
        assert len(state["acceptance_history"]) == 2, "replay did not execute a new acceptance decision"


@pytest.mark.parametrize("protocol", ["codex", "claude", "grok"])
def test_adapter_replay_preserves_non_idempotent_effect_and_rejects_missing_durable_call(protocol, tmp_path):
    with native_runtime(protocol, tmp_path) as (app, adapter, work, basis, actor, send, rejected):
        first = send("first", "create_work", {"goal": "Exactly one effect"})
        assert send("repeat", "create_work", {"goal": "Exactly one effect"}) == first
        assert len(app.call("list_work", {})["work"]) == 2
        # A cache without its durable operation is not permission to run again.
        with app.store.transaction() as tx:
            native_calls = tx.list("native_call")
        original = app.invoke_native
        def missing(driver, action, args, context, **kwargs):
            context = {**context, "call_id": "missing-durable-call"}
            return original(driver, action, args, context, **kwargs)
        app.invoke_native = missing
        result = send("missing", "create_work", {"goal": "Exactly one effect"})
        assert "error" in result and "native_call_unresolved" in rejected
        assert len(app.call("list_work", {})["work"]) == 2
        with app.store.transaction() as tx:
            assert tx.list("native_call") == native_calls


def test_unrelated_failure_cannot_satisfy_currentness_assertion():
    with pytest.raises(AssertionError, match="F04_CURRENT_NATIVE_REPLAY_REQUIRED"):
        assert_replay_rejected({"error": {"code": "timeout"}}, ["timeout"], "stale_native_result")


@pytest.mark.parametrize("protocol", ["codex", "claude", "grok"])
@pytest.mark.parametrize("denied_boundary", ["source", "destination"])
def test_native_resume_replay_reauthorizes_both_boundaries_without_new_effect(protocol, denied_boundary, tmp_path):
    from xorchestrate.adapters.policies import RuleGuardrail
    with native_runtime(protocol, tmp_path) as (app, adapter, work, basis, actor, send, rejected):
        app.call("add_actor", {"actor_id": "destination"})
        source = retry(app, "start_session", {"driver": "native", "specification": {}, "work_id": work["id"], "idempotency_key": "source"})["session"]
        args = {"driver": "native", "specification": {}, "work_id": work["id"], "actor_id": "destination", "resume_session_id": source["id"]}
        first = send("first", "start_session", args)
        assert "session" in first, first
        assert send("unchanged", "start_session", args) == first
        rule = ({"actions": ["send_session"], "where": {"session.id": source["id"]}, "effect": "deny"}
                if denied_boundary == "source" else
                {"actions": ["start_session"], "where": {"session_actor": "destination"}, "effect": "deny"})
        app.guardrail = RuleGuardrail({"rules": [rule]})
        assert_replay_rejected(send("revoked", "start_session", args), rejected, "denied")
        with app.store.transaction() as tx:
            assert len(tx.list("session")) == 3
        app.guardrail = RuleGuardrail()
        assert send("restored", "start_session", args) == first
        with app.store.transaction() as tx:
            assert len(tx.list("session")) == 3


@pytest.mark.parametrize("protocol", ["codex", "claude", "grok"])
def test_native_cache_cannot_recreate_missing_idempotent_operation(protocol, tmp_path):
    with native_runtime(protocol, tmp_path) as (app, adapter, work, basis, actor, send, rejected):
        args = {"driver": "native", "specification": {}, "work_id": work["id"]}
        first = send("first", "start_session", args)
        assert "session" in first, first
        with app.store.transaction() as tx:
            row = tx.get("operation", first["session"]["start_operation_id"])
            tx.delete("operation", row.key, row.version)
        assert_replay_rejected(send("missing", "start_session", args), rejected, "native_call_unresolved")
        with app.store.transaction() as tx:
            assert len(tx.list("session")) == 2
            assert tx.get("operation", row.key) is None
