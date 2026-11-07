"""Public CLI/MCP episode and composed policy fixtures for telemetry contracts."""

import base64
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from agent_telemetry.artifacts import FileArtifactStore

from tests.hexagonal.support.producer_capture import HttpFixture, response_body
from xorchestrate.adapters.policies import RuleGuardrail
from xorchestrate.composition import build
from xorchestrate.model import OrchestrationError

ROOT = Path(__file__).resolve().parents[3]


class Episode:
    tool_value = "independent provider episode " + "z" * 768

    def __init__(self, root, *, direct=False):
        self.root = root
        self.path = root / "profile.json"
        self.marker = root / "effects.txt"
        self.corpus = root / "corpus"
        self.runtime = None
        self.direct = direct
        catalog = [{"name": "project.inspect", "description": "Return exactly the supplied project observation",
                    "input_schema": {"type": "object", "properties": {"value": {"type": "string"}},
                                     "required": ["value"], "additionalProperties": False}}]
        definitions = root / "capabilities.json"
        definitions.write_text(json.dumps(catalog))
        self.config = {"store": {"adapter": "sqlite", "options": {"path": str(root / "state.sqlite3")}},
                       "telemetry": {"adapter": "local", "options": {
                           "runtime_root": str(self.corpus), "project_id": "workflow-project",
                           "dataset_id": "workflow-dataset", "collector_id": "workflow-collector",
                           "artifact_threshold_bytes": 512}},
                       "providers": {"tools": {"adapter": "cli", "options": {
                           "command": [sys.executable, str(ROOT / "tests/hexagonal/fixtures/capability_server.py"),
                                       "cli", "--definitions", str(definitions), "--marker", str(self.marker)],
                           "capabilities": catalog, "timeout": 10, "telemetry_drain_timeout": 1.0}}}}
        self.path.write_text(json.dumps(self.config))
        self.environment = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT), os.environ.get("PYTHONPATH", "")])}
        self.arguments = None
        if direct:
            self.runtime = build(self.config, root)

    def close(self):
        if self.runtime is not None:
            self.runtime.close()

    def command(self, *arguments, actor="owner"):
        return [sys.executable, "-m", "xorchestrate", "--config", str(self.path), "--actor", actor, *arguments]

    def cli(self, *arguments, input=None, actor="owner"):
        return subprocess.run(self.command(*arguments, actor=actor), input=input, capture_output=True,
                              cwd=self.root, env=self.environment, timeout=20)

    def call(self, action, arguments, actor="owner"):
        if self.runtime is not None:
            return self.runtime.application.call(action, arguments, actor=actor)
        response = self.cli("call", action, "--json", json.dumps(arguments), actor=actor)
        assert response.returncode == 0, response.stderr.decode()
        assert response.stderr == b"", response.stderr.decode()
        return json.loads(response.stdout)

    def create_work(self):
        return self.call("create_work", {"goal": "independent workflow private canary " + "g" * 800})["work"]["id"]

    def invoke_mcp(self, work):
        self.arguments = {"capability": "tools/project.inspect", "arguments": {"value": self.tool_value},
                          "work_id": work, "idempotency_key": "public-episode-effect"}
        frames = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "independent-workflow", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "invoke", "arguments": self.arguments}}]
        response = self.cli("serve", input=b"".join(json.dumps(frame).encode() + b"\n" for frame in frames))
        assert response.returncode == 0, response.stderr.decode()
        assert response.stderr == b"", response.stderr.decode()
        replies = [json.loads(line) for line in response.stdout.splitlines()]
        assert {item["id"] for item in replies} == {1, 2}, replies
        result = next(item for item in replies if item["id"] == 2)["result"]
        assert not result.get("isError"), result
        return result["structuredContent"]

    def replay_cli(self, work):
        assert self.arguments["work_id"] == work
        return self.call("invoke", self.arguments)

    def effect_names(self):
        return self.marker.read_text().splitlines()

    def events(self, work):
        return self.call("telemetry_events", {"work_id": work, "limit": 1000, "max_bytes": 262144})["events"]

    def payload(self, event):
        pieces, offset = [], 0
        while True:
            part = self.call("telemetry_payload", {"event_id": event["event_id"], "offset": offset, "limit": 16384})
            pieces.append(base64.b64decode(part["data_base64"], validate=True))
            if part["next_offset"] is None:
                return json.loads(b"".join(pieces))
            offset = part["next_offset"]

    def require_actions(self, *actions):
        if self.runtime is not None:
            available = {item.name for item in self.runtime.application.describe()}
        else:
            reply = self.cli("describe")
            assert reply.returncode == 0, reply.stderr.decode()
            available = {item["name"] for item in json.loads(reply.stdout)["operations"]}
        assert set(actions) <= available, "The public telemetry workflow must expose verified bundle operations"

    def bundle(self, name):
        return self.corpus / "exports" / name

    def bundle_rows(self, name):
        with sqlite3.connect((self.bundle(name) / "telemetry.sqlite3").as_uri() + "?mode=ro", uri=True) as connection:
            return [{"event_id": row[0], "digest": row[1], "observation": json.loads(row[2])}
                    for row in connection.execute("SELECT event_id,digest,observation FROM events ORDER BY sequence")]

    def bundle_artifacts_verified(self, name):
        bundle = self.bundle(name)
        for row in self.bundle_rows(name):
            for reference in row["observation"]["artifacts"]:
                if reference["availability"] != "captured":
                    continue
                digest = reference["digest"].removeprefix("sha256:")
                data = (bundle / "artifacts" / "sha256" / digest[:2] / digest).read_bytes()
                assert len(data) == reference["size_bytes"]
                assert hashlib.sha256(data).hexdigest() == digest
        return True

    def has_bundle_sidecars(self, name):
        database = self.bundle(name) / "telemetry.sqlite3"
        return any(Path(str(database) + suffix).exists() for suffix in ("-wal", "-shm", "-journal"))

    def bundle_database_exists(self, name):
        return (self.bundle(name) / "telemetry.sqlite3").exists()

    def deny_work(self, work):
        self.runtime.application.guardrail = RuleGuardrail({"rules": [{"actors": ["observer"],
            "actions": ["telemetry*", "get_work"], "where": {"work.id": work}, "effect": "deny"}]})

    def failed_call(self, action, arguments, actor):
        try:
            self.call(action, arguments, actor=actor)
        except OrchestrationError as error:
            return error.code
        return None

    def assess_actual_provider(self):
        if self.runtime is not None:
            self.runtime.close()
        with HttpFixture(response_body()) as source:
            self.config["evaluators"] = {"triage": {"adapter": "typesafe", "options": source.options()}}
            self.config["assessment"] = {"triage": "triage"}
            self.runtime = build(self.config, self.root)
            work = self.create_work()
            assessment = self.call("assess", {"question": "Is the supplied evidence ready?", "work_id": work})
            adapter = self.runtime.application.evaluators["triage"]
            drain = getattr(adapter, "drain_telemetry", None)
            if callable(drain):
                drain(timeout=3)
            return work, assessment, len(source.requests)

    def report_trial(self, work, identity, outcome):
        result = self.runtime.application.record_observation("trial.result", {"outcome": outcome,
            "conditions": {"fixture": "independent-reported-cohort"}},
            {"actor": "owner", "work_id": work}, event_id=identity, basis="reported")
        assert result["status"] == "recorded"

    def report_usage(self, work, identity, tokens):
        result = self.runtime.application.record_observation("evaluator.usage", {
            "usage": {"input_tokens": tokens}, "counter_semantics": "delta"},
            {"actor": "owner", "work_id": work}, event_id=identity, basis="reported")
        assert result["status"] == "recorded"

    def mixed_read_arguments(self, operation, baseline, candidate):
        if operation == "telemetry_status":
            return {}
        if operation != "telemetry_payload":
            return {"event_type": "evaluator.usage", "limit": 1000}
        actual_result = self.call("list_work", {})
        assert {row["id"] for row in actual_result["work"]} == {baseline, candidate}
        actual_result["work"].sort(key=lambda row: row["id"] == candidate)
        recorded = self.runtime.application.record_observation("application.completed", {
            "action": "list_work", "result": actual_result}, {"actor": "owner", "work_id": candidate},
            event_id="mixed-private-payload", basis="reported")
        assert recorded["status"] == "recorded"
        return {"event_id": "mixed-private-payload", "limit": 65536}

    @contextmanager
    def revoke_earlier_work_during_public_read(self, baseline, candidate, operation):
        application = self.runtime.application
        original_authorize = application._authorize
        target = application.inspection if operation == "telemetry_payload" else application
        state = {"baseline_authorized": False, "candidate_checks": 0, "revoked": False}

        def authorize(action, context):
            result = original_authorize(action, context)
            relevant = (action == operation and context.get("inspection", {}).get("kind") == "work"
                        if operation == "telemetry_payload" else action == "get_work")
            if relevant:
                work = (context.get("work") or {}).get("id")
                if work == baseline:
                    state["baseline_authorized"] = True
                if work == candidate:
                    state["candidate_checks"] += 1
                    trigger = 2 if operation == "telemetry_usage" else 1
                    if state["candidate_checks"] == trigger and not state["revoked"]:
                        self.deny_work(baseline)
                        state["revoked"] = True
            return result

        with patch.object(target, "_authorize", side_effect=authorize):
            yield state

    @contextmanager
    def revoke_baseline_after_candidate_query(self, baseline, candidate):
        telemetry = self.runtime.application.telemetry
        original = telemetry.query
        state = {"revoked": False}

        def query(**arguments):
            result = original(**arguments)
            if arguments.get("work_id") == candidate and not state["revoked"]:
                self.deny_work(baseline)
                state["revoked"] = True
            return result

        with patch.object(telemetry, "query", side_effect=query):
            yield state

    @contextmanager
    def revoke_earlier_work_during_final_bundle_pass(self, baseline, candidate, operation, destination):
        application = self.runtime.application
        original_authorize = application._authorize
        original_verify = FileArtifactStore.verify
        artifact_root = self.bundle(destination) / "artifacts"
        state = {"verified": False, "baseline_authorized": False, "revoked": False}

        def verify(store, reference):
            result = original_verify(store, reference)
            if store.root == artifact_root:
                state["verified"] = True
            return result

        def authorize(action, context):
            result = original_authorize(action, context)
            if action == operation and state["verified"]:
                work = (context.get("work") or {}).get("id")
                if work == baseline:
                    state["baseline_authorized"] = True
                if work == candidate and not state["revoked"]:
                    self.deny_work(baseline)
                    state["revoked"] = True
            return result

        with patch.object(FileArtifactStore, "verify", verify), \
                patch.object(application, "_authorize", side_effect=authorize):
            yield state

    def work_while_export_blocked(self):
        entered, release = threading.Event(), threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                entered.set()
                release.wait(6)
                self.send_response(503)
                self.end_headers()

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server_thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        server_thread.start()
        self.config["telemetry"]["options"]["operational"] = {"adapter": "otlp",
            "endpoint": f"http://127.0.0.1:{server.server_port}", "timeout": 5,
            "max_pending": 32, "max_bytes": 1048576, "drain_timeout": .05}
        if self.runtime is not None:
            self.runtime.close()
        self.runtime = build(self.config, self.root)
        finished = False
        result = None
        reached = False
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(self.call, "create_work", {"goal": "complete despite blocked optional transport"})
                try:
                    reached = entered.wait(2)
                    result = future.result(timeout=1)
                    finished = True
                except TimeoutError:
                    pass
                finally:
                    release.set()
                if result is None:
                    result = future.result(timeout=6)
        finally:
            release.set()
            server.shutdown()
            server.server_close()
            server_thread.join(1)
        return result, reached, finished

    @contextmanager
    def revoke_after_authorized_artifact_read(self, work):
        state = {"allowed": False, "revoked": False}
        original = FileArtifactStore.read

        class Guardrail:
            def evaluate(_self, action, context):
                if context.get("actor") == "observer" and action.startswith("telemetry") and context.get("work_id") == work:
                    if state["revoked"]:
                        return RuleGuardrail({"default": "deny"}).evaluate(action, context)
                    state["allowed"] = True
                return RuleGuardrail().evaluate(action, context)

        self.runtime.application.guardrail = Guardrail()

        def read(store, reference):
            data = original(store, reference)
            if state["allowed"]:
                state["revoked"] = True
            return data

        with patch.object(FileArtifactStore, "read", read):
            yield state
