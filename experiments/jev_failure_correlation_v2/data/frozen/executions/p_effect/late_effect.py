"""An applied bridge effect must not become definitely failed on replacement."""
import json
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path('/workspace/xorchestrate-jev')
sys.path.insert(0, str(ROOT))
from xorchestrate.adapters.bridge import SessionBridgeHost
from xorchestrate.composition import build
from xorchestrate.model import OrchestrationError


class CounterNative:
    options = {}
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.count = 0

    def start(self, specification):
        return {"native_id": "counter-conversation", "incarnation": "counter-native-incarnation", "configuration_id": "counter-v1"}

    def send(self, backend, message):
        self.count += 1
        if message.get("hold_after_effect"):
            self.entered.set()
            assert self.release.wait(10)
        return {"applied_count": self.count}

    def poll(self, backend):
        return []

    def close(self):
        self.release.set()


def scenario(replace):
    with tempfile.TemporaryDirectory(prefix="review-late-effect-") as directory:
        endpoint = Path(directory) / "host"
        native = CounterNative()
        host = SessionBridgeHost(native, endpoint, options={"transport": "fifo"})
        config = {"store": {"adapter": "sqlite", "options": {"path": str(Path(directory) / "store.db")}},
            "sessions": {"external": {"adapter": "bridge", "options": {"endpoint": str(endpoint), "transport": "fifo", "timeout": 4}}}}
        runtime = build(config)
        app = runtime.application
        try:
            old = app.call("attach_session", {"driver": "external", "specification": {}, "idempotency_key": "attach"})["session"]
            with ThreadPoolExecutor() as pool:
                pending = pool.submit(app.call, "send_session", {"session_id": old["id"], "message": {"hold_after_effect": True}, "idempotency_key": "old-send"})
                assert native.entered.wait(3)
                assert native.count == 1
                new = app.call("reconnect_session", {"driver": "external", "specification": {}, "resume_session_id": old["id"], "idempotency_key": "reconnect"})["session"] if replace else old
                if replace:
                    new = app.call("reconnect_session", {"driver": "external", "specification": {}, "resume_session_id": new["id"], "idempotency_key": "reconnect-again"})["session"]
                try:
                    pending_control = {"result": app.call("send_session", {"session_id": new["id"], "message": {}, "idempotency_key": "pending-control"})}
                except OrchestrationError as exc:
                    pending_control = {"code": exc.code, "details": exc.details}
                assert pending_control.get("code") == "conflict", "control: unresolved source effect is fenced before late result"
                assert native.count == 1
                native.release.set()
                try:
                    outcome = {"result": pending.result(3)}
                except OrchestrationError as exc:
                    outcome = {"code": exc.code, "details": exc.details}
            first_count = native.count
            try:
                retry = {"result": app.call("send_session", {"session_id": new["id"], "message": {"same_intent": "counter increment"}, "idempotency_key": "new-send"})}
            except OrchestrationError as exc:
                retry = {"code": exc.code, "details": exc.details}
            backend = new["backend"]
            result = {"replacement": replace, "config": config, "pending_control": pending_control, "first_effect_count": first_count, "first_outcome": outcome,
                "next_effect_outcome": retry, "final_effect_count": native.count, "host_state": runtime.application.sessions["external"].inspect(backend),
                "new_session": new, "defect_reproduced": bool(replace and outcome.get("code") == "operation_failed" and native.count == 2)}
            return result
        finally:
            native.release.set()
            runtime.close()
            host.close()
            if "result" in locals():
                result["cleanup"] = {"host_closed": host._closed,
                    "client_sessions_remaining": len(app.sessions["external"]._sessions),
                    "open_host_peers": sum(not peer.closed for peer in host._peers), "endpoint_exists": host.endpoint.exists()}


if __name__ == "__main__":
    results = [scenario(False), scenario(True)]
    Path(__file__).with_name("late-effect-results.json").write_text(json.dumps(results, indent=2))
    print(json.dumps([{k: v for k, v in row.items() if k not in {"host_state", "config", "new_session"}} for row in results], indent=2))
