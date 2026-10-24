"""Independent deterministic probes; no candidate files are modified."""
import importlib.util
import json
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path('/workspace/xorchestrate-jev')
sys.path.insert(0, str(ROOT))
from xorchestrate.adapters.claude import ClaudeSessionDriver
from xorchestrate.adapters.bridge import SessionBridgeHost, BridgeSessionDriver
from xorchestrate.model import Capability, OrchestrationError

spec = importlib.util.spec_from_file_location("bridge_fixture", ROOT / "tests/hexagonal/test_external_bridge.py")
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def wait(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError("probe scheduling predicate timed out")


def claude_overlapping_completion():
    with tempfile.TemporaryDirectory(prefix="review-claude-") as directory:
        driver = ClaudeSessionDriver({"command": [sys.executable, str(ROOT / "tests/hexagonal/fixtures/claude_native.py")],
            "env": {"CLAUDE_STATE_DIR": directory}, "terminal": "discard", "startup_timeout": 5, "timeout": 3})
        entered = {name: threading.Event() for name in ("A", "B", "C")}
        release = {name: threading.Event() for name in ("A", "B", "C")}
        observations = []
        finished = []

        def callback(action, args, context, key):
            name = args["nonce"]
            entered[name].set()
            if not release[name].wait(10):
                raise AssertionError("independent probe hold timed out")
            finished.append(name)
            return {"ok": True}

        driver.bind_application_tools([Capability("hold", "Hold", {"type": "object"})], callback)
        driver.bind_observer(lambda context, event: observations.append(event))
        try:
            session = driver.start({"context": {"actor": "worker", "session_id": "probe-session"}})
            driver.send(session, {"hold": "A"})
            assert entered["A"].wait(3)
            driver.send(session, {"hold": "B"})
            assert entered["B"].wait(3)
            driver.send(session, {"hold": "C"})
            assert entered["C"].wait(3)
            before = driver.status(session)
            assert before["activity"] == "active", "passing control: two native tools held"
            release["B"].set()
            wait(lambda: any(event["kind"] == "turn_completed" for event in observations))
            after_a = driver.status(session)
            assert not release["C"].is_set() and "C" not in finished
            result = {"case": "claude_overlapping_completion", "before": before, "after_a": after_a,
                "b_still_held": True, "completed_callbacks": list(finished),
                "observations": observations.copy(), "defect_reproduced": after_a["activity"] == "idle"}
            release["A"].set()
            release["C"].set()
            wait(lambda: len([e for e in observations if e["kind"] == "turn_completed"]) == 3)
            result["after_b_control"] = driver.status(session)
            return result
        finally:
            for event in release.values():
                event.set()
            owned = list(driver._sessions.values())
            driver.close()
            if "result" in locals():
                result["cleanup"] = {"owned_native_processes": len(owned),
                    "native_processes_exited": sum(live.process.poll() is not None for live in owned),
                    "gateway_threads_exited": sum(not live.gateway.thread.is_alive() for live in owned),
                    "driver_sessions_remaining": len(driver._sessions)}


def bridge_callback_after_replacement(replace):
    class DelayedNative(fixture.MemoryNative):
        def send(self, backend, message):
            context = {"native_id": backend["native_id"], "incarnation": backend["incarnation"], "call_id": "observed-old-call"}
            self.observer(context, {"kind": "native_tool_call", **context, "action": "echo", "arguments": {"value": "old-work"}})
            self.entered.set()
            assert self.release.wait(5)
            return self.callback("echo", {"value": "old-work"}, context, "observed-old-call")

    with tempfile.TemporaryDirectory(prefix="review-bridge-") as directory:
        cap = Capability("echo", "Echo", {"type": "object"})
        native = DelayedNative()
        host = SessionBridgeHost(native, Path(directory) / "host", capabilities=[cap], options={"transport": "fifo"})
        first = BridgeSessionDriver({"endpoint": str(host.endpoint), "transport": "fifo", "timeout": 3})
        second = BridgeSessionDriver({"endpoint": str(host.endpoint), "transport": "fifo", "timeout": 3})
        callbacks = []
        first.bind_application_tools([cap], lambda a, p, c, k: callbacks.append({"client": "first", "context": c, "arguments": p}) or {"effect": "first"})
        second.bind_application_tools([cap], lambda a, p, c, k: callbacks.append({"client": "second", "context": c, "arguments": p}) or {"effect": "second"})
        try:
            old = first.attach({"context": {"actor": "old-actor", "session_id": "old-session", "work_id": "old-work"}})
            with ThreadPoolExecutor() as pool:
                pending = pool.submit(first.send, old, {})
                assert native.entered.wait(3)
                old_observation = json.loads(json.dumps(host._events[-1]))
                assert old_observation["incarnation"] == old["incarnation"]
                new = second.reconnect(old, {"context": {"actor": "new-actor", "session_id": "new-session", "work_id": "new-work"}}) if replace else None
                if replace:
                    new = second.reconnect(new, {"context": {"actor": "final-actor", "session_id": "final-session", "work_id": "final-work"}})
                native.release.set()
                try:
                    outcome = {"result": pending.result(3)}
                except OrchestrationError as exc:
                    outcome = {"error": exc.code, "details": exc.details}
            result = {"case": "bridge_callback_after_replacement" if replace else "bridge_callback_control", "old_observation": old_observation,
                "old_incarnation": old["incarnation"], "new_incarnation": new["incarnation"] if new else None,
                "callbacks": callbacks, "send_outcome": outcome,
                "defect_reproduced": bool(replace and callbacks and callbacks[0]["client"] == "second")}
            return result
        finally:
            native.release.set()
            first.close()
            second.close()
            host.close()
            if "result" in locals():
                result["cleanup"] = {"host_closed": host._closed, "native_closed": native.closed,
                    "client_sessions_remaining": len(first._sessions) + len(second._sessions),
                    "open_host_peers": sum(not peer.closed for peer in host._peers), "endpoint_exists": host.endpoint.exists()}


if __name__ == "__main__":
    results = [bridge_callback_after_replacement(False), bridge_callback_after_replacement(True), claude_overlapping_completion()]
    Path(__file__).with_name("probe-results.json").write_text(json.dumps(results, indent=2))
    print(json.dumps([{k: v for k, v in item.items() if k not in {"observations", "old_observation"}} for item in results], indent=2))
