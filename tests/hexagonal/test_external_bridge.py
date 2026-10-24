"""External host lifecycle tests; deterministic peers, no vendor acceptance."""
import copy
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from xorchestrate.adapters.bridge import BridgeSessionDriver, SessionBridgeHost
from xorchestrate.adapters.sessions import CodexSessionDriver
from xorchestrate.model import Capability, OrchestrationError


class MemoryNative:
    """Controlled native boundary preserving conversation state across clients."""

    def __init__(self, protocol="codex"):
        self.protocol = protocol
        self.options = {"model": "nondefault-model"}
        self.closed = False
        self.stops = 0
        self.memory = None
        self.callback = None
        self.observer = None
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def bind_application_tools(self, capabilities, callback):
        self.callback = callback

    def bind_observer(self, observer):
        self.observer = observer

    def start(self, specification):
        self.context = specification.get("context", {})
        self.memory = specification.get("private_marker", "private-marker")
        self.backend = {"native_id": "controlled-conversation", "protocol": self.protocol,
            "incarnation": "native-incarnation", "configuration_id": "native-config-v1",
            "private_marker": self.memory,
            "settings": {"requested": {"model": "nondefault-model"}, "resolved": {"model": "nondefault-model"}, "observed": {"model": None}}}
        return copy.deepcopy(self.backend)

    def resume(self, backend, specification):
        result = self.start({**specification, "private_marker": backend["private_marker"]})
        result["incarnation"] = "recovered-native-incarnation"
        self.backend = copy.deepcopy(result)
        return result

    def send(self, backend, message):
        self.calls += 1
        assert not self.closed
        assert backend["native_id"] == self.backend["native_id"]
        if message.get("hold"):
            self.entered.set()
            assert self.release.wait(5)
        if message.get("callback"):
            context = {**self.context, "native_id": backend["native_id"], "incarnation": backend["incarnation"], "call_id": "native-call"}
            self.observer(context, {"kind": "native_tool", "native_id": backend["native_id"], "incarnation": backend["incarnation"],
                "call_id": "native-call", "action": "echo", "arguments": {"value": self.memory}, "source": "adapter"})
            return {"tool": self.callback("echo", {"value": self.memory, "actor": "forged"}, context, "native-call")}
        return {"submitted": True, "memory": self.memory, "native_id": self.backend["native_id"], "message": copy.deepcopy(message)}

    def deliver(self, backend, message):
        return self.send(backend, message)

    def poll(self, backend):
        return []

    def stop(self, backend):
        self.stops += 1
        self.closed = True
        return {"stopped": True}

    def close(self):
        self.closed = True
        self.release.set()


@pytest.fixture
def bridges(tmp_path):
    hosts, clients = [], []

    def make(protocol="codex", capabilities=(), **options):
        native = MemoryNative(protocol)
        host = SessionBridgeHost(native, tmp_path / f"host-{len(hosts)}.sock",
            {"private_marker": "unrepeatable-private-marker", "context": {"actor": "host-launcher"}}, capabilities, {"transport": "fifo"})
        hosts.append(host)
        client = BridgeSessionDriver({"endpoint": str(host.endpoint), "timeout": 2, "transport": "fifo", **options})
        clients.append(client)
        return native, host, client

    yield make
    for client in clients:
        client.close()
    for host in hosts:
        host.close()


@pytest.mark.parametrize("protocol", ["codex", "claude", "grok"])
def test_external_detach_does_not_terminate_host_and_reconnect_retains_context(bridges, protocol):
    native, host, first = bridges(protocol)
    backend = first.attach({"context": {"actor": "worker", "session_id": "first"}})
    assert backend["ownership"] == "external"
    assert first.send(backend, {})["memory"] == "unrepeatable-private-marker"
    assert first.detach(backend)["terminated"] is False
    assert not native.closed, "external_detach_must_not_terminate_native_process"
    assert native.stops == 0
    assert host.thread.is_alive()
    with BridgeClient(host) as second:
        resumed = second.reconnect(backend, {"context": {"actor": "worker", "session_id": "second"}})
        assert resumed["native_id"] == backend["native_id"]
        assert resumed["native_incarnation"] == backend["native_incarnation"]
        assert resumed["incarnation"] != backend["incarnation"]
        assert resumed["settings"] == backend["settings"]
        assert resumed["settings"]["observed"]["model"] is None
        assert second.send(resumed, {})["memory"] == "unrepeatable-private-marker"


class BridgeClient:
    def __init__(self, host, **options):
        self.driver = BridgeSessionDriver({"endpoint": str(host.endpoint), "timeout": 2, "transport": "fifo", **options})

    def __enter__(self):
        return self.driver

    def __exit__(self, *args):
        self.driver.close()


def test_replaced_connection_cannot_send_or_apply_late_completion(bridges):
    native, host, first = bridges()
    backend = first.attach({})
    with ThreadPoolExecutor() as pool:
        pending = pool.submit(first.send, backend, {"hold": True})
        assert native.entered.wait(2)
        with BridgeClient(host) as second:
            replacement = second.reconnect(backend, {})
            assert second.deliver(replacement, {"body": "still responsive"})["submitted"]
            with pytest.raises(OrchestrationError, match="replaced") as error:
                first.send(backend, {})
            assert error.value.code == "stale_connection"
            native.release.set()
            with pytest.raises(OrchestrationError) as late:
                pending.result(2)
            assert late.value.code == "stale_connection"
            effects = second.inspect(replacement)["effects"]
            assert any(item["incarnation"] == backend["incarnation"] and item["status"] == "completed" for item in effects.values())


def test_lost_response_preserves_host_effect_and_control_progress(bridges):
    native, host, client = bridges(timeout=0.1)
    backend = client.attach({})
    with pytest.raises(OrchestrationError) as unknown:
        client.send(backend, {"hold": True})
    assert unknown.value.code == "bridge_timeout"
    assert unknown.value.details["outcome"] == "unknown"
    assert client.poll(backend) == []
    inspection = client.inspect(backend)
    assert any(item["status"] == "pending" for item in inspection["effects"].values())
    native.release.set()
    assert native.calls == 1


def test_native_callbacks_and_observations_use_attached_context_and_progress_without_poll(bridges):
    capability = Capability("echo", "Echo", {"type": "object"})
    native, host, client = bridges(capabilities=[capability])
    calls, observations = [], []
    observed = threading.Event()
    client.bind_application_tools([capability], lambda action, arguments, context, key:
        calls.append((action, arguments, context, key)) or {"ok": True})
    client.bind_observer(lambda context, event: (observations.append((context, event)), observed.set()))
    backend = client.attach({"context": {"actor": "app-worker", "session_id": "app-session", "work_id": "work"}})
    assert client.send(backend, {"callback": True}) == {"tool": {"ok": True}}
    assert observed.wait(2), "bridge_observation_must_progress_without_poll"
    context = calls[0][2]
    assert context["actor"] == "app-worker"
    assert context["session_id"] == "app-session"
    assert context["native_id"] == backend["native_id"]
    assert context["incarnation"] == backend["incarnation"]
    assert context["native_incarnation"] == backend["native_incarnation"]
    assert observations[0][1]["source"] == "adapter"
    client.poll(backend)
    assert len(observations) == 1


def test_bridge_rejects_manufactured_receipts_and_changed_configuration(bridges):
    _, host, first = bridges()
    backend = first.attach({})
    with BridgeClient(host) as second:
        for forged in ({**backend, "native_id": "other"}, {**backend, "resume_token": "invented"},
                       {**backend, "configuration_id": "invented"}):
            with pytest.raises(OrchestrationError) as rejected:
                second.reconnect(forged, {})
            assert rejected.value.code == "session_identity"
        host.configuration_id = "native-config-v2"
        with pytest.raises(OrchestrationError) as changed:
            second.reconnect(backend, {})
        assert changed.value.code == "configuration_changed"


def test_bridge_config_and_catalog_cannot_be_overridden_by_attachment(bridges):
    _, host, client = bridges()
    for spec in ({"endpoint": "/other"}, {"native_id": "other"}, {"parameters": {"model": "override"}}, {"command": ["other"]}):
        with pytest.raises(OrchestrationError) as error:
            client.attach(spec)
        assert error.value.code == "invalid_arguments"
    client.bind_application_tools([Capability("different", "Different")], lambda *args: {})
    with pytest.raises(OrchestrationError) as mismatch:
        client.attach({})
    assert mismatch.value.code == "configuration_changed"
    assert os.stat(host.endpoint).st_mode & 0o777 == 0o700


def test_late_old_lease_observations_remain_historical(bridges):
    _, host, first = bridges()
    backend = first.attach({})
    first.deliver(backend, {"attempt_id": "old-attempt"})
    with BridgeClient(host) as second:
        observed = []
        second.bind_observer(lambda *args: observed.append(args))
        replacement = second.reconnect(backend, {})
        host._observe({}, {"kind": "completion", "native_id": backend["native_id"],
            "incarnation": "native-incarnation", "attempt_id": "old-attempt"})
        events = second.poll(replacement)
        assert events[0]["observation"]["incarnation"] == backend["incarnation"]
        assert observed == [], "stale_observation_must_not_bind_to_replacement"


def test_cli_host_is_an_independent_process_and_survives_client_close(tmp_path):
    root = Path(__file__).resolve().parents[2]
    endpoint = tmp_path / "external.sock"
    config = tmp_path / "host.json"
    fixture = Path(__file__).parent / "fixtures" / "session_server.py"
    config.write_text(json.dumps({"sessions": {"native": {"adapter": "jsonrpc", "options": {
        "command": [sys.executable, str(fixture), "jsonrpc"], "timeout": 2}}}}))
    process = subprocess.Popen([sys.executable, "-m", "xorchestrate.bridge", "--config", str(config),
        "--driver", "native", "--endpoint", str(endpoint), "--transport", "fifo", "--specification", '{"goal":"retained host goal"}'],
        cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        ready = json.loads(process.stdout.readline())
        assert ready["ready"]
        first = BridgeSessionDriver({"endpoint": str(endpoint), "timeout": 2, "transport": "fifo"})
        backend = first.attach({})
        assert first.send(backend, {"text": "first"})["start"]["goal"] == "retained host goal"
        first.close()
        assert process.poll() is None, "client_close_must_not_terminate_external_host"
        second = BridgeSessionDriver({"endpoint": str(endpoint), "timeout": 2, "transport": "fifo"})
        try:
            resumed = second.reconnect(backend, {})
            assert resumed["native_id"] == backend["native_id"]
            assert second.send(resumed, {"text": "later"})["start"]["goal"] == "retained host goal"
            second.stop(resumed)
            assert process.poll() is None
        finally:
            second.close()
    finally:
        process.terminate()
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, (stdout, stderr)
    assert not endpoint.exists()


def test_durable_host_recovery_preserves_uncertain_effect_and_native_identity(tmp_path):
    endpoint, state = tmp_path / "recover", tmp_path / "state.json"
    native = MemoryNative()
    host = SessionBridgeHost(native, endpoint, {"private_marker": "retained-before-crash"},
        options={"transport": "fifo", "state_path": str(state)})
    client = BridgeSessionDriver({"endpoint": str(endpoint), "transport": "fifo", "timeout": 0.1})
    backend = client.attach({})
    snapshot = tmp_path / "at-crash.json"
    try:
        with pytest.raises(OrchestrationError) as error:
            client.send(backend, {"hold": True})
        assert error.value.code == "bridge_timeout"
        assert native.entered.is_set()
        # Capture the persisted state at the actual outstanding-effect boundary;
        # clean up the fixture separately, then recover exactly that snapshot.
        shutil.copy2(state, snapshot)
        saved = json.loads(snapshot.read_text())
        assert any(item["status"] == "pending" for item in saved["effects"].values())
    finally:
        native.release.set()
        client.close()
        host.close()
    replacement_native = MemoryNative()
    replacement = SessionBridgeHost(replacement_native, endpoint, options={"transport": "fifo", "state_path": str(snapshot)})
    try:
        with BridgeClient(replacement) as resumed_client:
            resumed = resumed_client.reconnect(backend, {})
            assert resumed["host_id"] == backend["host_id"]
            assert resumed["native_id"] == backend["native_id"]
            assert resumed["native_incarnation"] != backend["native_incarnation"]
            effects = resumed_client.inspect(resumed)["effects"]
            assert any(item["status"] == "unknown" for item in effects.values()), "restart_must_preserve_uncertain_effect"
            assert replacement_native.calls == 0, "restart_must_not_replay_pending_effect"
            assert resumed_client.send(resumed, {"read_memory": True})["memory"] == "retained-before-crash"
    finally:
        replacement.close()
    assert os.stat(snapshot).st_mode & 0o777 == 0o600


def test_real_codex_adapter_callback_crosses_external_bridge(tmp_path):
    fixture = Path(__file__).parent / "fixtures" / "session_server.py"
    native = CodexSessionDriver({"command": [sys.executable, str(fixture), "codex"], "timeout": 2})
    capability = Capability("echo", "Application callback", {"type": "object"})
    host = SessionBridgeHost(native, tmp_path / "codex-host", capabilities=[capability], options={"transport": "fifo"})
    try:
        with BridgeClient(host) as client:
            contexts, observations = [], []
            client.bind_application_tools([capability], lambda action, args, context, key:
                contexts.append(context) or {"value": args["value"]})
            client.bind_observer(lambda context, observation: observations.append(observation))
            backend = client.attach({"context": {"actor": "controlled-codex", "session_id": "application-session"}})
            result = client.send(backend, {"text": "tool"})
            assert result["submitted"]
            assert result["incarnation"] == backend["incarnation"]
            assert result["native_incarnation"] == backend["native_incarnation"]
            assert contexts[0]["actor"] == "controlled-codex"
            assert contexts[0]["session_id"] == "application-session"
            client.poll(backend)
            assert any(event["kind"] == "native_submission" and event["incarnation"] == backend["incarnation"] for event in observations)
            assert any(event["kind"] == "native_tool_call" and event["native_id"] == backend["native_id"] for event in observations)
    finally:
        host.close()


def test_actual_host_crash_preserves_unknown_applied_effect_without_replay(tmp_path):
    root = Path(__file__).resolve().parents[2]
    endpoint, state = tmp_path / "host", tmp_path / "host-state.json"
    native_state, blocked = tmp_path / "native-state.json", tmp_path / "blocked"
    config = tmp_path / "host-config.json"
    config.write_text(json.dumps({"sessions": {"native": {"adapter": "tests.hexagonal.fixtures.bridge_native:PersistentNativeFixture",
        "options": {"state_file": str(native_state), "blocked_file": str(blocked)}}}}))
    command = [sys.executable, "-m", "xorchestrate.bridge", "--config", str(config), "--driver", "native",
        "--endpoint", str(endpoint), "--transport", "fifo", "--state", str(state)]

    def launch():
        process = subprocess.Popen(command, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        ready = process.stdout.readline()
        if not ready:
            _, errors = process.communicate(timeout=5)
            raise AssertionError(errors)
        assert json.loads(ready)["ready"]
        return process

    process = launch()
    client = BridgeSessionDriver({"endpoint": str(endpoint), "transport": "fifo", "timeout": .1})
    backend = client.attach({})
    try:
        with pytest.raises(OrchestrationError) as error:
            client.send(backend, {"remember": "private-before-kill", "block_after": True})
        assert error.value.code == "bridge_timeout"
        assert blocked.exists()
        assert json.loads(native_state.read_text())["effects"] == 1
        assert any(effect["status"] == "pending" for effect in json.loads(state.read_text())["effects"].values())
        # This is the test's own subprocess, already identified by Popen. Wait
        # for confirmed exit before removing its private temporary endpoint.
        process.kill()
        process.communicate(timeout=5)
        assert process.returncode == -signal.SIGKILL
        client.close()
        shutil.rmtree(endpoint)
        process = launch()
        client = BridgeSessionDriver({"endpoint": str(endpoint), "transport": "fifo", "timeout": 2})
        resumed = client.reconnect(backend, {})
        assert resumed["native_id"] == backend["native_id"]
        assert resumed["host_id"] == backend["host_id"]
        assert resumed["incarnation"] != backend["incarnation"]
        assert resumed["native_incarnation"] != backend["native_incarnation"]
        pending = client.inspect(resumed)["effects"]
        assert any(effect["status"] == "unknown" for effect in pending.values()), "killed_host_must_preserve_unknown_effect"
        assert json.loads(native_state.read_text())["effects"] == 1, "killed_host_must_not_replay_applied_effect"
        assert client.send(resumed, {}) == {"memory": "private-before-kill", "effects": 1}
    finally:
        client.close()
        if process.poll() is None:
            process.terminate()
        process.communicate(timeout=5)
    assert process.returncode == 0
    deadline = time.monotonic() + 1
    while endpoint.exists() and time.monotonic() < deadline:
        time.sleep(.01)
    assert not endpoint.exists()
