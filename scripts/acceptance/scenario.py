"""Public-process acceptance worker, supervised by the private tmux runner."""

import argparse
import hashlib
import json
import math
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
from scripts.testkit.stories import select_stories, validate_limits
from xorchestrate.composition import load_config
from xorchestrate.instance.operations import provision

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


class Budget:
    """Shared controller-request and harness-submission limits, before dispatch.

    These counters do not observe model tokens or autonomous native tool calls.
    A rejected request consumes no allowance; attempted dispatches do, even when
    the application rejects them or its response is lost.
    """

    def __init__(self, *, max_sessions=2, max_turns=4, max_tool_calls=5000):
        validate_limits(max_sessions=max_sessions, max_turns=max_turns, max_tool_calls=max_tool_calls)
        self.limits = {"sessions": max_sessions, "turns": max_turns, "tool_calls": max_tool_calls}
        self.consumed = dict.fromkeys(self.limits, 0)

    def consume(self, name):
        categories = ["tool_calls"]
        if name == "start_session":
            categories.append("sessions")
        if name == "send_session":
            categories.append("turns")
        for category in categories:
            require(self.consumed[category] < self.limits[category], "budget_exhausted_" + category)
        for category in categories:
            self.consumed[category] += 1


class Client:
    """Small independent MCP client; outputs are inspected, never logged."""

    def __init__(self, project, deadline, actor="owner", budget=None):
        self.deadline = deadline
        self.budget = budget
        self.identifier = 0
        self.buffer = b""
        # The application runs only inside a set-up project (D15); the
        # provisioned project's private home selects it.
        self.process = subprocess.Popen(
            [sys.executable, "-m", "xorchestrate", "--project", str(project.repository), "--actor", actor, "serve"],
            cwd=ROOT, env={**os.environ, **project.environment},
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
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
        if self.budget is not None:
            self.budget.consume(name)
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
    def __init__(self, directory, timeout, profile, *, max_sessions=2, max_turns=4, max_tool_calls=5000):
        self.directory = directory
        self.deadline = time.monotonic() + timeout
        self.profile = profile
        self.budget = Budget(max_sessions=max_sessions, max_turns=max_turns, max_tool_calls=max_tool_calls)
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
        client = Client(config, self.deadline, actor, budget=self.budget)
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


def application_project(directory, config):
    """Record the isolated configuration and provision a disposable project that runs it.

    The project, its private home and its companion live in ``directory``;
    clients select the home through the returned project's environment.
    """
    atomic_json(directory / "application.json", config)
    return provision(directory / "project", config, name="acceptance", home=directory / "home",
                     companion=directory / "companion")


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
    return isolated_config(config, base, directory, capture=True)


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


def discover(scenario, config):
    with scenario.check("mcp_initialize_and_discover"):
        client = scenario.client(config)
        names = {tool["name"] for tool in client.call("tools/list", {})["tools"]}
        require({"create_work", "invoke", "record_result", "start_session"} <= names, "application_catalog")
    return client


def coordinate(scenario, client, config):
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
    return work


def idempotent_effects(scenario, client, work):
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
    return requests, markers


def denied_effect(scenario, client, markers):
    with scenario.check("declared_guardrail_prevents_effect"):
        denied = {"capability": "local-cli/echo", "arguments": {"nonce": "guardrail-denied"}, "idempotency_key": "denied"}
        client.tool("invoke", denied, expected_error="denied")
        require(marker_records(scenario.directory / "cli-effects.jsonl") == markers["cli-effects.jsonl"], "denied_effect_count")


def bridge_lifecycle(scenario, client, work):
    with scenario.check("bridge_session_lifecycle"):
        session = client.tool("start_session", {"driver": "bridge", "specification": {}, "work_id": work["id"],
                                               "idempotency_key": "bridge-start"})["session"]
        message = {"text": uuid4().hex}
        client.tool("send_session", {"session_id": session["id"], "message": message, "idempotency_key": "bridge-send"})
        output = client.tool("poll_session", {"session_id": session["id"], "idempotency_key": "bridge-poll"})["output"]
        require(output == [{"kind": "message", "message": message}], "bridge_message")
        stopped = client.tool("stop_session", {"session_id": session["id"], "idempotency_key": "bridge-stop"})
        require(stopped["session"]["status"] == "stopped", "bridge_stop")


def uncertain_effect(scenario, client, markers):
    with scenario.check("uncertain_effect_is_not_reexecuted"):
        uncertain = {"capability": "local-mcp/uncertain", "arguments": {"nonce": uuid4().hex}, "idempotency_key": "uncertain-once"}
        failure = client.tool("invoke", uncertain, expected_error="operation_unknown")
        operation_id = failure["error"]["details"]["operation_id"]
        client.tool("invoke", uncertain, expected_error="operation_unresolved")
        markers["mcp-effects.jsonl"].append({"name": "uncertain", "nonce": uncertain["arguments"]["nonce"]})
        require(marker_records(scenario.directory / "mcp-effects.jsonl") == markers["mcp-effects.jsonl"], "uncertain_effect_count")
    return uncertain, operation_id


def run_fixture(scenario, config):
    client = discover(scenario, config)
    work = coordinate(scenario, client, config)
    requests, markers = idempotent_effects(scenario, client, work)
    denied_effect(scenario, client, markers)
    bridge_lifecycle(scenario, client, work)
    nonce = uuid4().hex
    with scenario.check("native_protocol_callback_principal_binding"):
        callback_receipt(scenario, client, "callback", work["id"], "acceptance-sender", nonce)
    uncertain, operation_id = uncertain_effect(scenario, client, markers)
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


def work_acceptance_config(directory, config):
    """Use the existing CLI provider to measure real bytes in a private fixture."""
    (directory / "product.txt").write_bytes(b"works\n")
    config["providers"]["checks"] = {"adapter": "cli", "options": {
        "command": [sys.executable, "-m", "scripts.acceptance.scenario", "_measure",
                    str(directory / "product.txt"), str(directory / "measurement-effects.jsonl")],
        "cwd": str(ROOT), "capabilities": [{"name": "measure", "input_schema": {"type": "object"}}],
    }}
    return config


def measure_product(product, marker):
    """Fixed subprocess checker: derive candidate and outcome from observed bytes."""
    binding = json.load(sys.stdin)["arguments"]["measurement"]
    data = product.read_bytes()
    actual = {key: binding[key] for key in ("work_id", "check_id", "requirements_identity", "requirement_revision")}
    actual.update(candidate={"identity": hashlib.sha256(data).hexdigest()}, bindings={}, status="completed",
                  passed=data == b"works\n", outcomes={"A": data == b"works\n"})
    with marker.open("a", encoding="utf-8") as output:
        output.write(json.dumps({"candidate": actual["candidate"], "passed": actual["passed"]}) + "\n")
    print(json.dumps({"measurement": actual}), flush=True)


def work_acceptance(scenario, client, config):
    with scenario.check("work_requirements_candidate_and_contribution"):
        work = client.tool("create_work", {"goal": "Measure a real artifact and retain current acceptance"})["work"]
        requirements = {"work_id": work["id"], "expected_revision": 0, "objective": "The product works",
                        "outcomes": {"A": "The observed artifact contains the required result"},
                        "checks": {"A": {"capability": "checks/measure", "outcomes": ["A"]}}}
        client.tool("set_requirements", requirements)
        client.tool("set_candidate", {"work_id": work["id"], "expected_revision": 0,
                                     "candidate": {"identity": hashlib.sha256((scenario.directory / "product.txt").read_bytes()).hexdigest()}})
        client.tool("create_contribution", {"work_id": work["id"], "goal": "Supply the artifact", "outcomes": ["A"]})
        require(not client.tool("accept_work", {"work_id": work["id"]})["acceptance"]["accepted"], "unmeasured_work_not_accepted")
    with scenario.check("measured_artifact_is_accepted"):
        measurement = {"work_id": work["id"], "check_id": "A", "measurement_key": "first"}
        client.tool("run_measurement", measurement)
        require(client.tool("accept_work", {"work_id": work["id"]})["acceptance"]["accepted"], "measured_work_accepted")
    with scenario.check("requirements_revision_invalidates_acceptance_and_replay"):
        client.tool("set_requirements", {**requirements, "expected_revision": 1, "objective": "Reconfirm after the owner revision"})
        require(not client.tool("get_acceptance", {"work_id": work["id"]})["accepted"], "revised_work_not_accepted")
        require(client.tool("run_measurement", measurement)["replayed"], "measurement_replay_retained")
        require(not client.tool("accept_work", {"work_id": work["id"]})["acceptance"]["accepted"], "stale_measurement_not_accepted")
        require(len(marker_records(scenario.directory / "measurement-effects.jsonl")) == 1, "measurement_not_reexecuted")
    with scenario.check("new_measurement_and_changed_artifact_survive_restart"):
        client.tool("run_measurement", {**measurement, "measurement_key": "revised"})
        require(client.tool("accept_work", {"work_id": work["id"]})["acceptance"]["accepted"], "revised_measurement_accepted")
        (scenario.directory / "product.txt").write_bytes(b"broken\n")
        client.tool("run_measurement", {**measurement, "measurement_key": "changed-artifact"})
        require(not client.tool("accept_work", {"work_id": work["id"]})["acceptance"]["accepted"], "changed_artifact_not_accepted")
        scenario.close(client)
        client = scenario.client(config)
        view = client.tool("get_acceptance", {"work_id": work["id"]})
        require(not view["accepted"] and len(view["measurements"]) == 3, "acceptance_restart_preserves_history")
        require(len(marker_records(scenario.directory / "measurement-effects.jsonl")) == 3, "measurement_effect_count")


STORY_CHECKS = {
    "coordination": ("mcp_initialize_and_discover", "coordination_and_acknowledgement", "coordination_survives_restart"),
    "effects-recovery": ("mcp_initialize_and_discover", "cli_and_mcp_effects_are_idempotent",
                         "declared_guardrail_prevents_effect", "uncertain_effect_is_not_reexecuted", "effects_survive_restart"),
    "session-lifecycle": ("mcp_initialize_and_discover", "bridge_session_lifecycle"),
    "work-acceptance": ("mcp_initialize_and_discover", "work_requirements_candidate_and_contribution", "measured_artifact_is_accepted",
                        "requirements_revision_invalidates_acceptance_and_replay", "new_measurement_and_changed_artifact_survive_restart"),
    "native-callback": ("configured_native_application_callback", "native_callback_survives_restart"),
}


def run_story(scenario, config, story, driver=None):
    if story == "native-callback":
        return run_native(scenario, config, driver if scenario.profile == "native" else "callback")
    client = discover(scenario, config)
    if story == "coordination":
        work = coordinate(scenario, client, config)
        with scenario.check("coordination_survives_restart"):
            scenario.close(client)
            client = scenario.client(config)
            retained = client.tool("get_work", {"work_id": work["id"]})
            require(retained["work"] == work and len(retained["assignments"]) == 2, "work_persistence")
            require(client.tool("inbox", {"recipient": "acceptance-recipient"})["messages"] == [], "ack_persistence")
    elif story == "effects-recovery":
        work = client.tool("create_work", {"goal": "Retain effects across application restart"})["work"]
        requests, markers = idempotent_effects(scenario, client, work)
        denied_effect(scenario, client, markers)
        uncertain, operation_id = uncertain_effect(scenario, client, markers)
        with scenario.check("effects_survive_restart"):
            scenario.close(client)
            client = scenario.client(config)
            for arguments, result in requests:
                require(client.tool("invoke", arguments) == result, "completed_operation_persistence")
            require(client.tool("get_operation", {"operation_id": operation_id})["operation"]["status"] == "unknown", "unknown_persistence")
            client.tool("invoke", uncertain, expected_error="operation_unresolved")
            for filename, records in markers.items():
                require(marker_records(scenario.directory / filename) == records, "restart_effect_identity")
    elif story == "session-lifecycle":
        work = client.tool("create_work", {"goal": "Observe a bridge session lifecycle"})["work"]
        bridge_lifecycle(scenario, client, work)
    elif story == "work-acceptance":
        work_acceptance(scenario, client, config)
    else:
        raise AcceptanceFailure("unknown_story")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "_measure":
        parser = argparse.ArgumentParser()
        parser.add_argument("product", type=Path)
        parser.add_argument("marker", type=Path)
        args = parser.parse_args(argv[1:])
        measure_product(args.product, args.marker)
        return 0
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--profile", choices=("fixture", "native"), required=True)
    parser.add_argument("--timeout", type=float, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--driver")
    parser.add_argument("--story")
    parser.add_argument("--max-sessions", type=int, default=2)
    parser.add_argument("--max-turns", type=int, default=4)
    parser.add_argument("--max-tool-calls", type=int, default=5000)
    args = parser.parse_args(argv)
    try:
        validate_limits(max_sessions=args.max_sessions, max_turns=args.max_turns, max_tool_calls=args.max_tool_calls)
        selected = select_stories((args.story,), mode=args.profile) if args.story else ()
    except ValueError as error:
        parser.error(str(error))
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be a positive finite number")
    if args.profile == "native" and (args.config is None or not args.driver):
        parser.error("native profile requires --config and --driver")
    if args.profile == "fixture" and (args.config is not None or args.driver is not None):
        parser.error("fixture profile does not accept native configuration or driver")
    old_umask = os.umask(0o077)
    directory = args.run_dir.resolve()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    scenario = Scenario(directory, args.timeout, args.profile, max_sessions=args.max_sessions,
                        max_turns=args.max_turns, max_tool_calls=args.max_tool_calls)
    receipt = {"schema_version": 1, "profile": args.profile, "status": "failed", "checks": scenario.checks,
               "evidence_scope": "local_protocol_fixture" if args.profile == "fixture" else "configured_native_callback",
               "store_override": "isolated_sqlite", "private_artifacts": ["application.json", "state.sqlite3"]}
    if selected:
        receipt["story"] = {"id": selected[0].id, "version": selected[0].version,
                            "expected_checks": ["tmux_worker_context", "isolated_application_configuration",
                                                *STORY_CHECKS[args.story], "graceful_application_shutdown"]}
    receipt["budget"] = {"limits": scenario.budget.limits, "consumed": scenario.budget.consumed,
                         "scope": "controller_requests_and_harness_submissions", "token_usage": "unknown"}

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
                if args.story == "work-acceptance":
                    config = work_acceptance_config(directory, config)
            project = application_project(directory, config)
        if args.story:
            run_story(scenario, project, args.story, args.driver)
        elif args.profile == "fixture":
            run_fixture(scenario, project)
        else:
            run_native(scenario, project, args.driver)
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
            if selected:
                receipt["story"]["executed_checks"] = [check["name"] for check in scenario.checks]
                receipt["story"]["missing_checks"] = [name for name in receipt["story"]["expected_checks"]
                                                       if name not in receipt["story"]["executed_checks"]]
                if receipt["status"] == "passed" and receipt["story"]["missing_checks"]:
                    receipt["status"] = "failed"
                    receipt["error"] = {"type": "AcceptanceFailure", "code": "story_checks_missing"}
            atomic_json(directory / "scenario.json", receipt)
        finally:
            for number, handler in previous_handlers.items():
                signal.signal(number, handler)
            os.umask(old_umask)
    print("SCENARIO " + receipt["status"], flush=True)
    return 0 if receipt["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

