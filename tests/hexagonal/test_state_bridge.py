"""The host's durable effect boundary consumes replaceable state definitions."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace

import pytest

from xorchestrate.activity import activity_definitions
from xorchestrate.adapters.bridge import BridgeSessionDriver, SessionBridgeHost
from xorchestrate.model import OrchestrationError
from xorchestrate.state import StateEngine


class NativeEffects:
    """An independent native port with controlled result/outcome knowledge."""

    def __init__(self, outcome=None):
        self.outcome, self.calls, self.engine = outcome, 0, None

    def bind_state_engine(self, engine):
        self.engine = engine

    def start(self, specification):
        assert self.engine is not None, "host must bind state policy before starting the native port"
        return {"native_id": "retained-conversation", "incarnation": "native-1", "protocol": "fixture"}

    def resume(self, source, specification):
        return {**self.start(specification), "incarnation": "native-2"}

    def send(self, backend, message):
        self.calls += 1
        if self.outcome:
            details = {} if self.outcome == "unknown" else {"outcome": self.outcome}
            raise OrchestrationError("Controlled native outcome", "controlled_failure", details)
        return {"accepted": deepcopy(message)}

    def close(self):
        pass


@contextmanager
def hosted(tmp_path, native=None, engine=None):
    native = native or NativeEffects()
    host = SessionBridgeHost(native, tmp_path / "host", options={
        "transport": "fifo", "state_path": str(tmp_path / "host-state.json")}, state_engine=engine)
    peer = object()
    receipt = host._request(peer, "attach", {"catalog_identity": host.catalog_identity})
    try:
        yield native, host, peer, receipt
    finally:
        host.close()


def request(host, peer, receipt, operation="operation", message=None):
    return host._request(peer, "send", {
        "incarnation": receipt["incarnation"], "operation_id": operation,
        "message": {} if message is None else message})


def test_host_uses_bound_definition_before_reserving_native_effect(tmp_path):
    definitions = activity_definitions()
    effect = definitions["native_effect"]
    definitions["native_effect"] = replace(effect, transitions=tuple(
        replace(rule, guard=lambda context: context["identity"] != "refused")
        if rule.event == "reserve" else rule for rule in effect.transitions))
    engine = StateEngine(definitions)
    with hosted(tmp_path, engine=engine) as (native, host, peer, receipt):
        before = host.state_path.read_bytes()
        with pytest.raises(OrchestrationError, match="prerequisites") as failure:
            request(host, peer, receipt, "refused")
        assert failure.value.details["outcome"] == "not_started"
        assert native.engine is engine
        assert native.calls == 0
        assert host._request(peer, "inspect", {"incarnation": receipt["incarnation"]})["effects"] == {}
        assert host.state_path.read_bytes() == before
        assert request(host, peer, receipt, "permitted") == {"accepted": {}}
        assert native.calls == 1


def test_completed_effect_replay_preserves_result_and_transition_history(tmp_path):
    with hosted(tmp_path) as (native, host, peer, receipt):
        assert request(host, peer, receipt, message={"value": 7}) == {"accepted": {"value": 7}}
        assert request(host, peer, receipt, message={"value": 7}) == {"accepted": {"value": 7}}
        assert native.calls == 1
        row = json.loads(host.state_path.read_text())["effects"]["operation"]
        assert row["status"] == "completed"
        assert [(item["event"], item["source"], item["target"]) for item in row["state_history"]] == [
            ("reserve", None, "pending"), ("succeed", "pending", "completed")]
        assert row["state_version"] == 2


@pytest.mark.parametrize("outcome,status,event", [
    ("not_started", "failed", "fail"), ("failed", "failed", "fail"),
    ("unknown", "unknown", "uncertain"), ("completed", "unknown", "uncertain"),
])
def test_host_retains_effect_knowledge_and_never_replays_failed_or_unknown_effect(tmp_path, outcome, status, event):
    with hosted(tmp_path, NativeEffects(outcome)) as (native, host, peer, receipt):
        with pytest.raises(OrchestrationError, match="Controlled native outcome"):
            request(host, peer, receipt)
        with pytest.raises(OrchestrationError) as replay:
            request(host, peer, receipt)
        assert native.calls == 1
        assert replay.value.details["outcome"] == ("failed" if status == "failed" else outcome)
        row = json.loads(host.state_path.read_text())["effects"]["operation"]
        assert row["status"] == status
        assert row["state_history"][-1]["event"] == event


def test_invalid_message_does_not_leave_reserved_effect(tmp_path):
    with hosted(tmp_path) as (native, host, peer, receipt):
        before = host.state_path.read_bytes()
        with pytest.raises(OrchestrationError, match="must be an object"):
            request(host, peer, receipt, message=["not", "an", "object"])
        assert native.calls == 0
        assert host._effects == {}
        assert host.state_path.read_bytes() == before


def test_completed_result_survives_refused_settlement_replay_and_recovery(tmp_path):
    definitions = activity_definitions()
    effect = definitions["native_effect"]
    definitions["native_effect"] = replace(effect, transitions=tuple(
        replace(rule, guard=lambda context: False)
        if rule.event in {"succeed", "uncertain"} else rule for rule in effect.transitions))
    engine = StateEngine(definitions)
    result = {"accepted": {"value": 7}}
    with hosted(tmp_path, engine=engine) as (native, host, peer, receipt):
        with pytest.raises(OrchestrationError) as rejected:
            request(host, peer, receipt, message={"value": 7})
        assert rejected.value.code == "completed_unapplied"
        assert rejected.value.details == {"outcome": "completed", "result": result, "cause": "state_guard_rejected"}
        with pytest.raises(OrchestrationError) as replay:
            request(host, peer, receipt, message={"value": 7})
        assert replay.value.details == {"outcome": "completed", "result": result}
        assert native.calls == 1
        row = json.loads(host.state_path.read_text())["effects"]["operation"]
        assert row["status"] == "pending", "a refused terminal transition must not be forced"
        assert row["result"] == result and row["effect_outcome"] == "completed"
        assert row["recording_error"] == "state_guard_rejected"
    with hosted(tmp_path, engine=engine) as (native, host, _, _):
        row = json.loads(host.state_path.read_text())["effects"]["operation"]
        assert row["status"] == "unknown"
        assert row["result"] == result and row["effect_outcome"] == "completed"
        assert native.calls == 0


def test_returned_result_is_carried_outward_when_host_persistence_fails(tmp_path, monkeypatch):
    with hosted(tmp_path) as (native, host, peer, receipt):
        save = host._save_state

        def refuse_completed_save():
            if native.calls:
                raise OSError("controlled persistence failure")
            save()

        monkeypatch.setattr(host, "_save_state", refuse_completed_save)
        with pytest.raises(OrchestrationError) as rejected:
            request(host, peer, receipt, message={"value": 7})
        assert rejected.value.code == "completed_unapplied"
        assert rejected.value.details == {"outcome": "completed", "result": {"accepted": {"value": 7}}, "cause": "OSError"}
        with pytest.raises(OrchestrationError) as replay:
            request(host, peer, receipt, message={"value": 7})
        assert replay.value.details["outcome"] == "completed"
        assert native.calls == 1


class HeldNativeResult(NativeEffects):
    """Hold completion until the test has either preserved or replaced its lease."""

    def __init__(self, raise_result):
        super().__init__()
        self.raise_result = raise_result
        self.entered = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()
        self.result = {"native_output": "retained-native-result-canary"}

    def send(self, backend, message):
        self.calls += 1
        self.entered.set()
        if not self.release.wait(5):
            raise TimeoutError("test did not release the native completion gate")
        self.finished.set()
        if self.raise_result:
            raise OrchestrationError("Native result needs settlement", "completed_unapplied",
                                     {"outcome": "completed", "result": deepcopy(self.result)})
        return deepcopy(self.result)

    def close(self):
        self.release.set()


def bridge_effect_response(client, receipt):
    try:
        return {"result": client.send(receipt, {})}
    except OrchestrationError as error:
        return {"error": {"code": error.code, "message": str(error), "details": error.details}}


@pytest.mark.parametrize("replace_attachment", [False, True], ids=["current", "replaced"])
@pytest.mark.parametrize("settlement", ["normal", "guard", "persistence"])
@pytest.mark.parametrize("raise_result", [False, True], ids=["returned-result", "native-error-result"])
def test_post_effect_results_are_retained_but_only_exposed_to_current_lease(
        tmp_path, monkeypatch, replace_attachment, settlement, raise_result):
    definitions = activity_definitions()
    if settlement == "guard":
        definition = definitions["native_effect"]
        definitions["native_effect"] = replace(definition, transitions=tuple(
            replace(rule, guard=lambda context: False)
            if rule.event in {"succeed", "uncertain"} else rule for rule in definition.transitions))
    native = HeldNativeResult(raise_result)
    host = SessionBridgeHost(native, tmp_path / "lease-host", state_engine=StateEngine(definitions),
                             options={"transport": "fifo", "state_path": str(tmp_path / "lease-state.json")})
    options = {"endpoint": str(host.endpoint), "transport": "fifo", "timeout": 3}
    original, replacement = BridgeSessionDriver(options), BridgeSessionDriver(options)
    save = host._save_state
    if settlement == "persistence":
        def fail_post_effect_save():
            if native.finished.is_set():
                raise OSError("controlled post-effect storage failure")
            save()

        monkeypatch.setattr(host, "_save_state", fail_post_effect_save)
    try:
        old = original.attach({"context": {"actor": "old-actor", "work_id": "old-work"}})
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(bridge_effect_response, original, old)
            try:
                assert native.entered.wait(3), "control: native effect must be in flight before replacement"
                current_client, current = original, old
                if replace_attachment:
                    current_client = replacement
                    current = replacement.resume(old, {"context": {"actor": "new-actor", "work_id": "new-work"}})
                    assert current["incarnation"] != old["incarnation"]
            finally:
                native.release.set()
            response = pending.result(3)
        # Inspect through the current client's wire API, not internal host state.
        retained = current_client.inspect(current)["effects"]
        assert len(retained) == 1
        effect = next(iter(retained.values()))
        assert effect["result"] == native.result
        assert effect["effect_outcome"] == "completed"
        assert effect["incarnation"] == old["incarnation"]
        assert native.calls == 1
        if replace_attachment:
            assert response.get("error", {}).get("code") == "stale_connection", response
            assert response["error"]["details"] == {"outcome": "unknown"}
            assert "retained-native-result-canary" not in json.dumps(response)
        elif not raise_result and settlement == "normal":
            assert response == {"result": native.result}
        else:
            error = response["error"]
            assert error["code"] == ("effect_unapplied" if raise_result and settlement != "normal"
                                      else "completed_unapplied")
            assert error["details"]["outcome"] == "completed"
            if error["code"] == "completed_unapplied":
                assert error["details"]["result"] == native.result
        if settlement != "persistence":
            persisted = json.loads(host.state_path.read_text())["effects"]
            assert next(iter(persisted.values()))["result"] == native.result
    finally:
        native.release.set()
        original.close()
        replacement.close()
        host.close()


def test_host_recovery_uses_definition_and_retains_pending_effect_history(tmp_path):
    with hosted(tmp_path) as (_, host, _, receipt):
        # This is the exact durable boundary before invoking the native port.
        host._transition_effect("interrupted", "reserve", {"identity": "effect", "incarnation": receipt["incarnation"]})
        host._save_state()
    with hosted(tmp_path) as (native, host, _, _):
        row = json.loads(host.state_path.read_text())["effects"]["interrupted"]
        assert row["status"] == "unknown"
        assert row["recovery_reason"] == "host_restarted_before_effect_outcome"
        assert [item["event"] for item in row["state_history"]] == ["reserve", "recover"]
        assert native.calls == 0
