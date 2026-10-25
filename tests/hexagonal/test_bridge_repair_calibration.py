"""F02/F03 composed regressions and retained behavioral fault calibration.

The faulty hosts deliberately remain faulty. Each calibration proves activation,
then requires the same behavioral assertion used by the product regression.
No installed vendor behavior is asserted by these controlled native boundaries.
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest

from xorchestrate.adapters.bridge import SessionBridgeHost
from xorchestrate.composition import build
from xorchestrate.model import OrchestrationError


class CallbackBoundary:
    options = {}

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()

    def bind_application_tools(self, capabilities, callback):
        self.callback = callback

    def bind_observer(self, observer):
        self.observer = observer

    def start(self, specification):
        return {"native_id": "callback-conversation", "incarnation": "native-incarnation",
            "configuration_id": "callback-v1"}

    def send(self, backend, message):
        context = {"native_id": backend["native_id"], "incarnation": backend["incarnation"], "call_id": "old-call"}
        arguments = {"result": {"canary": "OLD_WORK_CALLBACK"}}
        self.observer(context, {"kind": "native_tool_call", **context, "action": "record_result", "arguments": arguments})
        self.entered.set()
        if not self.release.wait(5):
            raise TimeoutError("callback scheduling gate did not release")
        return self.callback("record_result", arguments, context, "old-call")

    def poll(self, backend):
        return []

    def close(self):
        self.release.set()


class CounterBoundary:
    options = {}

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.count = 0
        self.closed = False

    def start(self, specification):
        return {"native_id": "counter-conversation", "incarnation": "native-incarnation",
            "configuration_id": "counter-v1"}

    def send(self, backend, message):
        self.count += 1
        if message.get("hold_after_effect"):
            self.entered.set()
            if not self.release.wait(5):
                raise TimeoutError("effect scheduling gate did not release")
        return {"applied_count": self.count}

    def poll(self, backend):
        return []

    def close(self):
        self.closed = True
        self.release.set()


class ReassignedCallbackHost(SessionBridgeHost):
    """Known bad F02: overwrite observed authority at callback dispatch."""
    fault_activated = False

    def _callback(self, action, arguments, native_context, idempotency_key=None):
        with self._lock:
            key = next(key for key in self._bindings if key[0] in {"call_id", "native_call"})
            if self._bindings[key] != self._lease:
                self.fault_activated = True
                self._bindings[key] = self._lease
        return super()._callback(action, arguments, native_context, idempotency_key)


class FalseNotStartedHost(SessionBridgeHost):
    """Known bad F03: classify a completed, replaced effect as not started."""
    fault_activated = False

    def _check(self, peer, params, *, outcome="not_started"):
        if outcome == "unknown" and (peer is not self._active or params.get("incarnation") != self._lease):
            assert any(item["status"] == "completed" for item in self._effects.values())
            self.fault_activated = True
            outcome = "not_started"
        return super()._check(peer, params, outcome=outcome)


def outcome(function, *args):
    try:
        return {"result": function(*args)}
    except OrchestrationError as error:
        return {"code": error.code, "details": error.details}


def configuration(tmp_path, store, callbacks=False):
    return {"store": {"adapter": store, "options": {"path": str(tmp_path / "store.db")} if store == "sqlite" else {}},
        "sessions": {"external": {"adapter": "bridge", "options": {
            "endpoint": str(tmp_path / "host"), "transport": "fifo", "application_tools": callbacks, "timeout": 3}}}}


def callback_scenario(tmp_path, store, replace, host_type=SessionBridgeHost):
    config = configuration(tmp_path, store, callbacks=True)
    config["guardrail"] = {"adapter": "rules", "options": {"rules": [{"id": "old-cannot-record", "actors": ["old-actor"],
        "actions": ["record_result"], "effect": "deny"}]}}
    runtime = build(config)
    app, native = runtime.application, CallbackBoundary()
    host = host_type(native, tmp_path / "host", capabilities=app.describe(), options={"transport": "fifo"})
    try:
        for actor in ("old-actor", "new-actor"):
            app.call("add_actor", {"actor_id": actor})
        works = {name: app.call("create_work", {"goal": name})["work"]["id"] for name in ("old", "new")}
        old = app.call("attach_session", {"driver": "external", "specification": {}, "actor_id": "old-actor",
            "work_id": works["old"], "idempotency_key": "attach-old"})["session"]
        with ThreadPoolExecutor() as pool:
            future = pool.submit(outcome, app.call, "send_session", {
                "session_id": old["id"], "message": {}, "idempotency_key": "old-send"})
            assert native.entered.wait(3), "setup: native call must reach observation gate"
            deadline = time.monotonic() + 3
            while not app.call("native_observations", {"session_id": old["id"]})["observations"]:
                assert time.monotonic() < deadline, "setup: original observation must reach store"
                time.sleep(.005)
            if replace:
                app.call("reconnect_session", {"driver": "external", "specification": {}, "actor_id": "new-actor",
                    "work_id": works["new"], "resume_session_id": old["id"], "idempotency_key": "reconnect"})
            native.release.set()
            result = future.result(3)
        return {"outcome": result, "old": app.call("get_work", {"work_id": works["old"]}),
            "new": app.call("get_work", {"work_id": works["new"]}), "events": deepcopy(host._events),
            "old_incarnation": old["backend"]["incarnation"], "call_binding": next(key for key in host._bindings if key[0] in {"call_id", "native_call"}), "bindings": deepcopy(host._bindings),
            "fault_activated": getattr(host, "fault_activated", False)}
    finally:
        native.release.set()
        runtime.close()
        host.close()
        assert not host.endpoint.exists(), "setup: owned endpoint cleanup"


def assert_original_callback_scope(result):
    assert not result["new"]["evidence"], "F02_CALLBACK_MUST_NOT_ACQUIRE_REPLACEMENT_AUTHORITY"
    assert not result["old"]["evidence"], "F02_OLD_ACTOR_DENIAL_MUST_HOLD"
    assert result["bindings"][result["call_binding"]] == result["old_incarnation"], "F02_OBSERVED_BINDING_MUST_REMAIN_IMMUTABLE"
    assert all(event["incarnation"] == result["old_incarnation"] for event in result["events"])


@pytest.mark.parametrize("store", ["memory", "sqlite"])
@pytest.mark.parametrize("replace", [False, True])
def test_observed_callback_cannot_acquire_replacement_authority(tmp_path, store, replace):
    result = callback_scenario(tmp_path, store, replace)
    assert_original_callback_scope(result)
    assert result["outcome"]["details"]["cause"] == ("stale_connection" if replace else "denied")


@pytest.mark.parametrize("store", ["memory", "sqlite"])
def test_f02_known_bad_callback_reassignment_is_detected(tmp_path, store):
    control_dir = tmp_path / "control"
    control_dir.mkdir()
    control = callback_scenario(control_dir, store, False, ReassignedCallbackHost)
    assert_original_callback_scope(control)
    assert not control["fault_activated"]
    fault_dir = tmp_path / "fault"
    fault_dir.mkdir()
    faulty = callback_scenario(fault_dir, store, True, ReassignedCallbackHost)
    assert faulty["fault_activated"], "calibration: callback reassignment must activate"
    assert faulty["new"]["evidence"][0]["created_by"] == "new-actor"
    with pytest.raises(AssertionError, match="F02_CALLBACK_MUST_NOT_ACQUIRE_REPLACEMENT_AUTHORITY"):
        assert_original_callback_scope(faulty)


def effect_scenario(tmp_path, store, replace, host_type=SessionBridgeHost, recover=False):
    native = CounterBoundary()
    host = host_type(native, tmp_path / "host", options={"transport": "fifo", "state_path": str(tmp_path / "host-state.json")})
    runtime = build(configuration(tmp_path, store))
    app = runtime.application
    try:
        old = app.call("attach_session", {"driver": "external", "specification": {}, "idempotency_key": "attach"})["session"]
        with ThreadPoolExecutor() as pool:
            future = pool.submit(outcome, app.call, "send_session", {"session_id": old["id"],
                "message": {"hold_after_effect": True}, "idempotency_key": "old-send"})
            assert native.entered.wait(3), "setup: first effect must reach post-effect gate"
            assert native.count == 1
            current = app.call("reconnect_session", {"driver": "external", "specification": {},
                "resume_session_id": old["id"], "idempotency_key": "reconnect"})["session"] if replace else old
            blocked_before = outcome(app.call, "send_session", {"session_id": current["id"], "message": {}, "idempotency_key": "before"})
            assert blocked_before["code"] == "conflict", "control: pending effect must fence new send"
            assert native.count == 1
            native.release.set()
            first = future.result(3)
        following = outcome(app.call, "send_session", {"session_id": current["id"], "message": {}, "idempotency_key": "after"})
        operation_id = first.get("details", first.get("result", {}))["operation_id"]
        operation = app.call("get_operation", {"operation_id": operation_id})["operation"]
        # The current client can inspect and poll while the old effect is unresolved.
        inspection = app.sessions["external"].inspect(current["backend"])
        polling = app.call("poll_session", {"session_id": current["id"], "idempotency_key": "poll"})
        result = {"first": first, "following": following, "count": native.count, "operation": operation,
            "inspection": inspection, "poll": polling, "fault_activated": getattr(host, "fault_activated", False)}
        if recover:
            assert_effect_uncertainty_and_fence(result)
            completed = [item for item in inspection["effects"].values()
                if item["incarnation"] == old["backend"]["incarnation"] and item["status"] == "completed"]
            assert len(completed) == 1
            result["resolved"] = app.call("resolve_operation", {"operation_id": operation_id,
                "expected_version": operation["version"], "outcome": "completed", "reason": "inspected retained host result",
                "result": completed[0]["result"]})
            result["after_resolution"] = app.call("send_session", {
                "session_id": current["id"], "message": {}, "idempotency_key": "after-resolution"})
            result["resolved_count"] = native.count
        app.call("detach_session", {"session_id": current["id"], "idempotency_key": "detach"})
        assert not native.closed, "control: detaching must preserve externally owned process"
        return result
    finally:
        native.release.set()
        runtime.close()
        host.close()
        assert not host.endpoint.exists(), "setup: owned endpoint cleanup"


def assert_effect_uncertainty_and_fence(result):
    assert result["first"].get("code") == "operation_unknown", "F03_APPLIED_EFFECT_MUST_NOT_BECOME_NOT_STARTED"
    assert result["operation"]["status"] == "unknown", "F03_APPLIED_EFFECT_MUST_REMAIN_RECOVERABLE"
    assert result["following"].get("code") == "conflict", "F03_REPLACEMENT_MUST_RETAIN_RECOVERY_FENCE"
    assert result["count"] == 1, "F03_UNRESOLVED_EFFECT_MUST_NOT_ADMIT_FRESH_SEND"
    assert any(item["status"] == "completed" and item["result"]["applied_count"] == 1
        for item in result["inspection"]["effects"].values()), "F03_HOST_MUST_RETAIN_COMPLETED_RESULT"


@pytest.mark.parametrize("store", ["memory", "sqlite"])
def test_post_effect_reconnect_preserves_fence_observation_and_recovery(tmp_path, store):
    result = effect_scenario(tmp_path, store, True, recover=True)
    assert_effect_uncertainty_and_fence(result)
    assert result["resolved"]["operation"]["status"] == "completed"
    assert result["resolved_count"] == 2
    assert result["after_resolution"]["output"]["applied_count"] == 2


@pytest.mark.parametrize("store", ["memory", "sqlite"])
def test_f03_no_replacement_control_completes_and_allows_later_effect(tmp_path, store):
    result = effect_scenario(tmp_path, store, False)
    assert result["first"]["result"]["output"]["applied_count"] == 1
    assert result["operation"]["status"] == "completed"
    assert result["count"] == 2


@pytest.mark.parametrize("store", ["memory", "sqlite"])
def test_f03_known_bad_not_started_classification_is_detected(tmp_path, store):
    control_dir = tmp_path / "control"
    control_dir.mkdir()
    control = effect_scenario(control_dir, store, False, FalseNotStartedHost)
    assert control["operation"]["status"] == "completed"
    assert not control["fault_activated"]
    fault_dir = tmp_path / "fault"
    fault_dir.mkdir()
    faulty = effect_scenario(fault_dir, store, True, FalseNotStartedHost)
    assert faulty["fault_activated"], "calibration: post-effect false certainty must activate"
    assert faulty["operation"]["status"] == "failed"
    assert faulty["count"] == 2
    with pytest.raises(AssertionError, match="F03_APPLIED_EFFECT_MUST_NOT_BECOME_NOT_STARTED"):
        assert_effect_uncertainty_and_fence(faulty)


@pytest.mark.parametrize("error", [TimeoutError, ModuleNotFoundError])
def test_setup_exceptions_cannot_be_mistaken_for_bridge_fault_detection(error):
    # The scenario executes outside the target-assertion exception handler in
    # each calibration. Non-product failures must propagate as their own type.
    def broken_setup():
        raise error("unrelated setup failure")

    with pytest.raises(error, match="unrelated setup failure"):
        outcome(broken_setup)


def test_unbound_missing_and_stale_native_calls_cannot_gain_new_authority(tmp_path):
    from xorchestrate.adapters.bridge import BridgeSessionDriver
    from xorchestrate.model import Capability

    capability = Capability("echo", "Echo", {"type": "object"})
    native = CallbackBoundary()
    host = SessionBridgeHost(native, tmp_path / "identity-host", capabilities=[capability], options={"transport": "fifo"})
    client = BridgeSessionDriver({"endpoint": str(host.endpoint), "transport": "fifo", "timeout": 3})
    calls = []
    client.bind_application_tools([capability], lambda *args: calls.append(args) or {"ok": True})
    native_context = {"native_id": host.backend["native_id"], "incarnation": host.backend["incarnation"], "call_id": "before-attach"}
    try:
        # The call existed before any application principal. Later attachment
        # must not retroactively grant it authority.
        native.observer(native_context, {"kind": "native_tool_call", **native_context, "action": "echo", "arguments": {}})
        backend = client.attach({"context": {"actor": "replacement"}})
        with pytest.raises(OrchestrationError) as unbound:
            native.callback("echo", {}, native_context, "unbound")
        assert unbound.value.code == "stale_connection"
        with pytest.raises(OrchestrationError) as missing:
            native.callback("echo", {}, {**native_context, "call_id": None}, "missing")
        assert missing.value.code == "missing_call_identity"
        with pytest.raises(OrchestrationError) as stale:
            native.callback("echo", {}, {**native_context, "incarnation": "obsolete", "call_id": "new"}, "stale")
        assert stale.value.code == "session_identity"
        assert calls == []
        # A new native incarnation may reuse a vendor-local call ID without
        # inheriting an obsolete binding. Old-incarnation frames still fail.
        host.backend["incarnation"] = "resumed-native-incarnation"
        current_context = {**native_context, "incarnation": host.backend["incarnation"]}
        native.observer(current_context, {"kind": "native_tool_call", **current_context, "action": "echo", "arguments": {}})
        assert native.callback("echo", {}, current_context, "current") == {"ok": True}
        assert calls[0][2]["incarnation"] == backend["incarnation"]
        assert calls[0][2]["actor"] == "replacement"
        assert calls[0][2]["native_incarnation"] == "resumed-native-incarnation"
    finally:
        client.close()
        host.close()


@pytest.mark.parametrize("callback_failure", ["detached", "not_started"])
def test_callback_failure_does_not_classify_enclosing_native_effect_as_not_started(tmp_path, callback_failure):
    from xorchestrate.adapters.bridge import BridgeSessionDriver
    from xorchestrate.model import Capability

    class AppliedThenCallback(CallbackBoundary):
        count = 0

        def send(self, backend, message):
            self.count += 1
            return super().send(backend, message)

    native = AppliedThenCallback()
    capability = Capability("record_result", "Record", {"type": "object"})
    host = SessionBridgeHost(native, tmp_path / "callback-failure", capabilities=[capability], options={"transport": "fifo"})
    client = BridgeSessionDriver({"endpoint": str(host.endpoint), "transport": "fifo", "timeout": 3})

    def rejected(*args):
        raise OrchestrationError("controlled callback preflight rejection", "callback_preflight", {"outcome": "not_started"})

    client.bind_application_tools([capability], rejected)
    try:
        backend = client.attach({})
        with ThreadPoolExecutor() as pool:
            future = pool.submit(outcome, client.send, backend, {})
            assert native.entered.wait(3)
            assert native.count == 1
            if callback_failure == "detached":
                client.detach(backend)
            native.release.set()
            result = future.result(3)
        assert result["details"]["outcome"] == "unknown", "F03_CALLBACK_REJECTION_CANNOT_PROVE_NATIVE_NON_EXECUTION"
        deadline = time.monotonic() + 3
        while any(item["status"] == "pending" for item in host._effects.values()):
            assert time.monotonic() < deadline
            time.sleep(.005)
        assert all(item["status"] == "unknown" for item in host._effects.values())
        assert native.count == 1
    finally:
        native.release.set()
        client.close()
        host.close()
