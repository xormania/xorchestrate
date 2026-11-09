"""Real native/application callback composition for disclosure review controls."""

import base64
import json
import sys
from pathlib import Path

from tests.hexagonal.support.application import Provider
from tests.hexagonal.support.native import application_message, peer_options, wait_for
from tests.hexagonal.support.producer_capture import Recorder
from tests.hexagonal.support.telemetry import Fixture
from xorchestrate.adapters.jsonrpc import JsonLineProcess
from xorchestrate.adapters.policies import RuleGuardrail
from xorchestrate.model import OrchestrationError


def expanded(value):
    """Expose retained reversible encodings to an independent disclosure oracle."""
    if isinstance(value, dict):
        result = {key: expanded(child) for key, child in value.items()}
        if value.get("encoding") == "base64" and isinstance(value.get("content"), str):
            result["decoded_wire"] = expanded(json.loads(base64.b64decode(value["content"])))
        return result
    if isinstance(value, list):
        return [expanded(child) for child in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (ValueError, RecursionError):
            return value
        return expanded(parsed) if isinstance(parsed, (dict, list)) else value
    return value


class CallbackFixture:
    expand = staticmethod(expanded)

    def __init__(self, root: Path, *, artifact=False):
        self.root = root
        self.wire = root / "actual-callback-replies.jsonl"
        options = peer_options("codex", application_tools=True,
            env={"PEER_RESULTS": str(self.wire)}, callback_timeout=5)
        self.subject = Fixture(root, local_options={"artifact_threshold_bytes": 64 if artifact else 65536},
            sessions={"native": {"adapter": "codex", "options": options}})
        self.app = self.subject.app
        self.public = self.subject.create_work("outer native work remains visible")
        self.secret = "NESTED-CALLBACK-PRIVATE-CANARY-" + "rich-λ" * 80
        self.private = self.subject.create_work(self.secret)
        self.session = self.app.call("start_session", {"driver": "native",
            "work_id": self.public, "specification": {}, "idempotency_key": "callback-start"})["session"]
        self.reply = None
        self.callback_action = None
        self.callback_arguments = None

    def close(self):
        self.subject.close()

    def call(self, action, arguments, *, label="selected"):
        self.callback_action, self.callback_arguments = action, arguments
        self.app.call("send_session", {"session_id": self.session["id"],
            "message": application_message("codex", action, arguments,
                replay_fixture={"label": label, "call_id": label}),
            "idempotency_key": "send-" + label})
        def reply():
            rows = [json.loads(line) for line in self.wire.read_text().splitlines()] if self.wire.exists() else []
            return next((row for row in rows if row["label"] == label), None)
        retained = wait_for(reply, timeout=5, message="actual native callback reply did not arrive")
        actual_packet = {"id": retained["rpc_id"], "result": retained["result"]}
        self.reply_wire_size = len((json.dumps(actual_packet, ensure_ascii=True,
            separators=(",", ":")) + "\n").encode("utf-8"))
        wait_for(lambda: any(row["event_type"] == "native.native_tool_result"
            and row["payload"].get("action") == action for row in self.rows()),
            timeout=5, message="typed native callback observation did not reach corpus")
        self.app.sessions["native"].drain_telemetry(timeout=2)
        response = retained["result"]
        assert response["success"] is True, "native callback action must complete successfully"
        self.reply = json.loads(response["contentItems"][0]["text"])
        return self.reply

    def get_private_work(self):
        return self.call("get_work", {"work_id": self.private})

    def invoke_provider(self, *, opaque=False):
        self.secret = "PROVIDER-ONLY-" + self.secret
        self.provider_name = "allowed-provider" if opaque else "protected-provider"
        payload = {"body": self.secret}
        if opaque:
            payload = {"action": "get_work", "arguments": {"work_id": self.private},
                "result": {"work": {"id": self.private, "goal": self.secret}},
                "sessions": [{"id": "opaque-provider-session", "work_id": self.private}]}
        self.provider_payload = payload
        self.app.providers[self.provider_name] = Provider(callback=lambda *args: payload)
        return self.call("invoke", {"capability": self.provider_name + "/change",
            "arguments": {"value": 3}, "idempotency_key": "provider-callback"})

    def restrict(self, *, provider=False):
        selector = {"providers": [self.provider_name]} if provider else {"where": {"work.id": self.private}}
        self.app.guardrail = RuleGuardrail({"rules": [{"actions": ["telemetry*", "get_work", "get_operation"],
            "actors": ["observer"], **selector, "effect": "deny"}]})

    def rows(self):
        result = []
        for envelope in self.app.telemetry.scan(limit=1000)["events"]:
            row = self.app.telemetry.get_event(envelope["event_id"])
            if envelope.get("payload_storage"):
                row["payload_storage"] = envelope["payload_storage"]
            result.append(row)
        return result

    def public_events(self):
        return self.subject.events(actor="observer")

    def readable_payloads(self, *, original=False):
        result = []
        for row in self.rows():
            arguments = {}
            storage = row.get("payload_storage")
            if original and storage:
                arguments["digest"] = storage["digest"]
            try:
                part = self.subject.payload(row["event_id"], actor="observer", limit=65536, **arguments)
            except OrchestrationError as error:
                assert error.code in {"denied", "not_found"}, error
                continue
            result.append(json.loads(self.subject.payload_bytes(part)))
        return result

    def owner_typed_results(self):
        return [row for row in self.rows() if row["event_type"] == "native.native_tool_result"
            and row["payload"].get("action") == self.callback_action]


def generic_callback(root):
    """Reuse the transport failure peer's successful arbitrary callback path."""
    recorder = Recorder()
    expected = {"opaque": {"action": "get_work", "result": {"work": {"id": "caller-owned-data"}}}}
    options = {"command": [sys.executable, str(Path(__file__).parents[1] / "fixtures" /
        "transport_failure_server.py"), "request_callback", "--marker", str(root / "generic-peer.jsonl")],
        "timeout": 3}
    calls = []
    def handler(method, params):
        calls.append((method, params))
        return expected
    transport = JsonLineProcess(options, handler, telemetry_observer=recorder)
    try:
        result = transport.request("fixture/outer")
        transport.drain_telemetry(timeout=2)
    finally:
        transport.close()
    packets = [json.loads(base64.b64decode(row["payload"]["content"])) for row in recorder.snapshot()
        if row["event_type"] == "process.request" and row["payload"].get("encoding") == "base64"]
    return packets, calls, result, expected

