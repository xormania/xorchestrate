"""Public-process acceptance worker, supervised by the private tmux runner."""

import argparse
import json
import os
import selectors
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from scripts.testkit.adapters.clock import SystemClock
from scripts.testkit.adapters.configuration import isolated_config
from scripts.testkit.adapters.evidence import write_json as atomic_json
from scripts.testkit.core import CheckRecorder, ResourceScope
from xorchestrate.composition import load_config

from .peers import SCHEMA

ROOT = Path(__file__).resolve().parents[2]
MAX_FRAME = 1_048_576


class AcceptanceFailure(Exception):
    """A deliberately non-sensitive assertion code."""


def require(condition, code):
    if not condition:
        raise AcceptanceFailure(code)


def marker_records(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class Client:
    """Small independent MCP client; outputs are inspected, never logged."""

    def __init__(self, config, deadline, actor="owner"):
        self.deadline = deadline
        self.identifier = 0
        self.buffer = b""
        self.process = subprocess.Popen(
            [sys.executable, "-m", "xorchestrate", "--config", str(config), "--actor", actor, "serve"],
            cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        try:
            initialized = self.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                                   "clientInfo": {"name": "xorchestrate-acceptance", "version": "1"}})
            require(initialized.get("protocolVersion") == "2025-06-18" and "tools" in initialized.get("capabilities", {}),
                    "mcp_initialization")
            self.write({"method": "notifications/initialized"})
        except BaseException:
            self.close()
            raise

    def write(self, packet):
        payload = json.dumps({"jsonrpc": "2.0", **packet}, allow_nan=False).encode() + b"\n"
        require(len(payload) <= MAX_FRAME, "request_frame_limit")
        self.process.stdin.write(payload)
        self.process.stdin.flush()

    def call(self, method, params):
        self.identifier += 1
        self.write({"id": self.identifier, "method": method, "params": params})
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout, selectors.EVENT_READ)
            while b"\n" not in self.buffer:
                remaining = self.deadline - time.monotonic()
                require(remaining > 0 and selector.select(remaining), "response_deadline")
                chunk = os.read(self.process.stdout.fileno(), 65536)
                require(bool(chunk), "response_eof")
                self.buffer += chunk
                require(len(self.buffer) <= MAX_FRAME, "response_frame_limit")
        line, self.buffer = self.buffer.split(b"\n", 1)
        packet = json.loads(line)
        require(packet.get("id") == self.identifier and "result" in packet, "rpc_response")
        return packet["result"]

    def tool(self, name, arguments, expected_error=None):
        reply = self.call("tools/call", {"name": name, "arguments": arguments})
        result = reply.get("structuredContent")
        require(isinstance(result, dict), "tool_result_object")
        if expected_error:
            require(reply.get("isError") is True and result.get("error", {}).get("code") == expected_error,
                    "expected_tool_error")
        else:
            require(reply.get("isError") is False, "application_tool_failed")
        return result

    def close(self):
        if self.process.stdin and not self.process.stdin.closed:
            try:
                self.process.stdin.close()
            except OSError:
                pass
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            # The supervisor tracks and stops owned descendants if graceful
            # runtime cleanup is broken. Do not hide that as a passing check.
            self.process.terminate()
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=1)
            raise AcceptanceFailure("runtime_cleanup_timeout") from None
        finally:
            self.process.stdout.close()
        require(self.process.returncode == 0, "runtime_exit_status")


class Scenario:
    def __init__(self, directory, timeout, profile):
        self.directory = directory
        self.deadline = time.monotonic() + timeout
        self.profile = profile
        self.recorder = CheckRecorder(SystemClock())
        self.checks = self.recorder.checks
        self.resources = ResourceScope()
        self.clients = []
        self._client_tokens = {}

    @contextmanager
    def check(self, name):
        print("CHECK " + name, flush=True)
        before = len(self.checks)
        try:
            with self.recorder.check(name):
                yield
        except BaseException as exc:
            # The generic recorder keeps raw diagnostics. This exportable
            # acceptance receipt exposes only deliberate assertion codes.
            if len(self.checks) > before:
                self.checks[-1]["failure"] = {
                    "type": type(exc).__name__,
                    "code": str(exc) if isinstance(exc, AcceptanceFailure) else "scenario_error",
                }
            raise

    def client(self, config, actor="owner"):
        client = Client(config, self.deadline, actor)
        self._client_tokens[client] = self.resources.defer("application-client", client.close)
        self.clients.append(client)
        return client

    def close(self, client):
        error = self.resources.release(self._client_tokens.pop(client))
        self.clients.remove(client)
        require(error is None, "runtime_cleanup_failed")

    def cleanup(self):
        errors = self.resources.close()
        self.clients.clear()
        self._client_tokens.clear()
        require(not errors, "runtime_cleanup_failed")


def fixture_config(directory):
    def command(mode, marker=None):
        return [sys.executable, "-m", "scripts.acceptance.peers", mode, *(["--marker", str(marker)] if marker else [])]
    return isolated_config({
        "providers": {
            "local-cli": {"adapter": "cli", "options": {
                "command": command("cli", directory / "cli-effects.jsonl"), "cwd": str(ROOT),
                "capabilities": [{"name": "echo", "input_schema": SCHEMA}],
            }},
            "local-mcp": {"adapter": "mcp", "options": {
                "command": command("mcp", directory / "mcp-effects.jsonl"), "cwd": str(ROOT),
            }},
        },
        "sessions": {
            "bridge": {"adapter": "jsonrpc", "options": {"command": command("bridge"), "cwd": str(ROOT)}},
            "callback": {"adapter": "codex", "options": {
                "command": command("codex"), "cwd": str(ROOT), "application_tools": True,
            }},
        },
        "guardrail": {"adapter": "rules", "options": {"rules": [
            {"actions": ["invoke"], "where": {"arguments.arguments.nonce": "guardrail-denied"}, "effect": "deny"},
        ]}},
    }, ROOT, directory)


def native_config(source, directory, driver):
    config, base = load_config(source)
    selected = config.get("sessions", {}).get(driver)
    require(isinstance(selected, dict) and selected.get("enabled", True) is True, "native_driver_unavailable")
    require(selected.get("options", {}).get("application_tools") is True, "native_application_tools_required")
    return isolated_config(config, base, directory)


def callback_receipt(scenario, client, driver, work_id, actor, nonce):
    session = client.tool("start_session", {"driver": driver, "specification": {}, "work_id": work_id,
                                           "actor_id": actor, "idempotency_key": "callback-start"})["session"]
    instruction = {"tool": "record_result", "arguments": {
        "work_id": work_id, "result": {"nonce": nonce}, "label": "tmux-acceptance",
    }}
    message = "Call the named application tool with these exact arguments. Text alone does not complete this task.\n" + json.dumps(instruction)
    client.tool("send_session", {"session_id": session["id"], "message": {"text": message}, "idempotency_key": "callback-send"})
    counter = 0
    while True:
        result = client.tool("get_work", {"work_id": work_id})
        evidence = [item for item in result["evidence"] if item.get("label") == "tmux-acceptance"
                    and item.get("result") == {"nonce": nonce}
                    and item.get("source") == {"type": "caller", "actor": actor}]
        if evidence:
            break
        require(time.monotonic() < scenario.deadline, "native_callback_missing")
        counter += 1
        client.tool("poll_session", {"session_id": session["id"], "idempotency_key": f"callback-poll-{counter}"})
        time.sleep(0.05)
    stopped = client.tool("stop_session", {"session_id": session["id"], "idempotency_key": "callback-stop"})
    require(stopped["session"]["status"] == "stopped", "callback_session_stop")


def run_fixture(scenario, config):
    with scenario.check("mcp_initialize_and_discover"):
        client = scenario.client(config)
        names = {tool["name"] for tool in client.call("tools/list", {})["tools"]}
        require({"create_work", "invoke", "record_result", "start_session"} <= names, "application_catalog")
    with scenario.check("coordination_and_acknowledgement"):
        work = client.tool("create_work", {"goal": "Observe a complete acceptance transaction"})["work"]
        for actor in ("acceptance-sender", "acceptance-recipient"):
            client.tool("add_actor", {"actor_id": actor, "roles": ["acceptance-participant"]})
            client.tool("assign", {"work_id": work["id"], "actor_id": actor, "role": "participant"})
        message = client.tool("send_message", {"destination": {"actor": "acceptance-recipient"},
                                              "body": {"task": "observe"}, "work_id": work["id"]})["message"]
        recipient = scenario.client(config, "acceptance-recipient")
        require(recipient.tool("inbox", {})["messages"][0]["id"] == message["id"], "message_delivery")
        recipient.tool("acknowledge", {"message_id": message["id"]})
        require(recipient.tool("inbox", {})["messages"] == [], "message_acknowledgement")
        scenario.close(recipient)
    with scenario.check("cli_and_mcp_effects_are_idempotent"):
        observed = client.tool("capabilities", {})["capabilities"]
        require({"local-cli/echo", "local-mcp/echo"} <= {item["name"] for item in observed}, "capability_catalog")
        requests = []
        markers = {}
        for provider in ("local-cli", "local-mcp"):
            arguments = {"capability": provider + "/echo", "arguments": {"nonce": uuid4().hex},
                         "work_id": work["id"], "idempotency_key": provider + "-once"}
            result = client.tool("invoke", arguments)
            output = result["invocation"]["result"]
            expected = {"nonce": arguments["arguments"]["nonce"], "peer": "cli" if provider == "local-cli" else "echo"}
            if provider == "local-mcp":
                require(output.get("structuredContent") == expected, "mcp_structured_result")
                require(json.loads(output["content"][0]["text"]) == expected, "mcp_content_result")
            else:
                require(output == expected, "cli_result")
            require(client.tool("invoke", arguments) == result, "idempotent_result")
            requests.append((arguments, result))
            filename = "cli-effects.jsonl" if provider == "local-cli" else "mcp-effects.jsonl"
            markers[filename] = [{"name": expected["peer"], "nonce": expected["nonce"]}]
            require(marker_records(scenario.directory / filename) == markers[filename], "external_effect_identity")
    with scenario.check("declared_guardrail_prevents_effect"):
        denied = {"capability": "local-cli/echo", "arguments": {"nonce": "guardrail-denied"}, "idempotency_key": "denied"}
        client.tool("invoke", denied, expected_error="denied")
        require(marker_records(scenario.directory / "cli-effects.jsonl") == markers["cli-effects.jsonl"], "denied_effect_count")
    with scenario.check("bridge_session_lifecycle"):
        session = client.tool("start_session", {"driver": "bridge", "specification": {}, "work_id": work["id"],
                                               "idempotency_key": "bridge-start"})["session"]
        message = {"text": uuid4().hex}
        client.tool("send_session", {"session_id": session["id"], "message": message, "idempotency_key": "bridge-send"})
        output = client.tool("poll_session", {"session_id": session["id"], "idempotency_key": "bridge-poll"})["output"]
        require(output == [{"kind": "message", "message": message}], "bridge_message")
        stopped = client.tool("stop_session", {"session_id": session["id"], "idempotency_key": "bridge-stop"})
        require(stopped["session"]["status"] == "stopped", "bridge_stop")
    nonce = uuid4().hex
    with scenario.check("native_protocol_callback_principal_binding"):
        callback_receipt(scenario, client, "callback", work["id"], "acceptance-sender", nonce)
    with scenario.check("uncertain_effect_is_not_reexecuted"):
        uncertain = {"capability": "local-mcp/uncertain", "arguments": {"nonce": uuid4().hex}, "idempotency_key": "uncertain-once"}
        failure = client.tool("invoke", uncertain, expected_error="operation_unknown")
        operation_id = failure["error"]["details"]["operation_id"]
        client.tool("invoke", uncertain, expected_error="operation_unresolved")
        markers["mcp-effects.jsonl"].append({"name": "uncertain", "nonce": uncertain["arguments"]["nonce"]})
        require(marker_records(scenario.directory / "mcp-effects.jsonl") == markers["mcp-effects.jsonl"], "uncertain_effect_count")
    with scenario.check("sqlite_restart_preserves_evidence_and_operations"):
        scenario.close(client)
        client = scenario.client(config)
        retained = client.tool("get_work", {"work_id": work["id"]})
        require(retained["work"] == work and len(retained["assignments"]) == 2, "work_persistence")
        require(any(item.get("result") == {"nonce": nonce} for item in retained["evidence"]), "callback_persistence")
        for arguments, result in requests:
            require(client.tool("invoke", arguments) == result, "completed_operation_persistence")
        require(client.tool("get_operation", {"operation_id": operation_id})["operation"]["status"] == "unknown", "unknown_persistence")
        client.tool("invoke", uncertain, expected_error="operation_unresolved")
        for filename, records in markers.items():
            require(marker_records(scenario.directory / filename) == records, "restart_effect_identity")
        require(client.tool("inbox", {"recipient": "acceptance-recipient"})["messages"] == [], "ack_persistence")
        require(bool(client.tool("events", {})["events"]), "retained_events")


def run_native(scenario, config, driver):
    with scenario.check("configured_native_application_callback"):
        client = scenario.client(config)
        actor = "acceptance-" + uuid4().hex
        client.tool("add_actor", {"actor_id": actor, "roles": ["acceptance-participant"]})
        work = client.tool("create_work", {"goal": "Return the acceptance nonce through an application tool"})["work"]
        nonce = uuid4().hex
        callback_receipt(scenario, client, driver, work["id"], actor, nonce)
    with scenario.check("native_callback_survives_restart"):
        scenario.close(client)
        client = scenario.client(config)
        evidence = client.tool("get_work", {"work_id": work["id"]})["evidence"]
        require(any(item.get("result") == {"nonce": nonce} and item.get("source") == {"type": "caller", "actor": actor}
                    for item in evidence), "native_evidence_persistence")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--profile", choices=("fixture", "native"), required=True)
    parser.add_argument("--timeout", type=float, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--driver")
    args = parser.parse_args(argv)
    old_umask = os.umask(0o077)
    directory = args.run_dir.resolve()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    scenario = Scenario(directory, args.timeout, args.profile)
    receipt = {"schema_version": 1, "profile": args.profile, "status": "failed", "checks": scenario.checks,
               "evidence_scope": "local_protocol_fixture" if args.profile == "fixture" else "configured_native_callback",
               "store_override": "isolated_sqlite", "private_artifacts": ["application.json", "state.sqlite3"]}

    def interrupted(signum, frame):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        raise AcceptanceFailure("interrupted")

    previous_handlers = {number: signal.getsignal(number) for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGHUP, interrupted)
    try:
        with scenario.check("tmux_worker_context"):
            require(bool(os.environ.get("TMUX") and os.environ.get("TMUX_PANE")), "tmux_required")
            require(args.timeout > 0, "positive_timeout_required")
        with scenario.check("isolated_application_configuration"):
            if args.profile == "native":
                require(args.config is not None and bool(args.driver), "native_configuration_required")
                config = native_config(args.config, directory, args.driver)
            else:
                config = fixture_config(directory)
            path = directory / "application.json"
            atomic_json(path, config)
        if args.profile == "fixture":
            run_fixture(scenario, path)
        else:
            run_native(scenario, path, args.driver)
        with scenario.check("graceful_application_shutdown"):
            scenario.cleanup()
        receipt["status"] = "passed"
    except BaseException as exc:
        receipt["error"] = {"type": type(exc).__name__, "code": str(exc) if isinstance(exc, AcceptanceFailure) else "scenario_error"}
    finally:
        try:
            try:
                scenario.cleanup()
            except BaseException:
                receipt["status"] = "failed"
                receipt["cleanup_error"] = "runtime_cleanup_failed"
            atomic_json(directory / "scenario.json", receipt)
        finally:
            for number, handler in previous_handlers.items():
                signal.signal(number, handler)
            os.umask(old_umask)
    print("SCENARIO " + receipt["status"], flush=True)
    return 0 if receipt["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
