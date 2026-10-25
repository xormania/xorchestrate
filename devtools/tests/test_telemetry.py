import io
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from support import Client, initialize, until

from devtools import adapters
from devtools.api import API
from devtools.mcp import serve
from devtools.profiles import hook, prepare
from devtools.store import Store
from devtools.telemetry import Trace, Transcript, context, observed, requested


class TelemetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "state"
        workspace = self.root / "workspace"
        workspace.mkdir()
        initialize(self.state, workspace, ["owner:external", "worker:claude"])
        self.store = Store(self.state)

    def test_explicit_request_scope_is_retained_for_both_frames_during_concurrent_calls(self):
        client = Client(self.state, "owner", operator=True)
        try:
            first = client.invoke("work_order_create", title="First", objective="First", done_when="Done")["work_order"]["id"]
            second = client.invoke("work_order_create", title="Second", objective="Second", done_when="Done")["work_order"]["id"]
            job = client.invoke("job_create", work_order=second, title="Job", objective="Job", done_when="Done")["job"]["id"]
            client.invoke("work_order_select", work_order=first)
            calls = []
            for key, scope in (("job", {"job": job}), ("order", {"work_order": first})):
                ident = client.begin("tools/call", {"name": "capability_invoke", "arguments": {
                    "capability": "message_send", "arguments": {"to": "worker", "body": key, "key": key, **scope},
                }})
                calls.append((ident, (second, job) if key == "job" else (first, None)))
            for ident, _ in calls:
                self.assertFalse(client.finish(ident).get("isError"))
            with self.assertRaises(ValueError):
                client.invoke("job_get", job=9999)
            rejected = client.ident
        finally:
            client.close()
        rows = [json.loads(line) for line in (self.state / "telemetry/owner.jsonl").read_text().splitlines()]
        for ident, expected in calls:
            frames = [r for r in rows if r["data"].get("id") == ident]
            self.assertEqual({"mcp_in", "mcp_out"}, {r["kind"] for r in frames})
            for frame in frames:
                self.assertEqual(expected, (frame["context"]["work_order"], frame["context"]["job"]))
                self.assertEqual("request", frame["context"]["scope_source"])
                self.assertEqual(first, frame["context"]["selected_work_order"])
        for frame in [r for r in rows if r["data"].get("id") == rejected]:
            self.assertEqual("unresolved", frame["context"]["scope_source"])
            self.assertIsNone(frame["context"]["work_order"])

    def test_concurrent_output_trace_order_matches_the_wire(self):
        first_logged, second_attempted = threading.Event(), threading.Event()
        original_record, original_call = Trace.record, API.call
        lock_held = []

        class OutputLock:
            def __init__(self):
                self.lock = threading.Lock()

            def __enter__(self):
                if first_logged.is_set():
                    lock_held.append(self.lock.locked())
                    second_attempted.set()
                self.lock.acquire()

            def __exit__(self, *args):
                self.lock.release()

        def record(trace, kind, data, **kwargs):
            original_record(trace, kind, data, **kwargs)
            if kind == "mcp_out" and data.get("id") == 2:
                first_logged.set()
                self.assertTrue(second_attempted.wait(3), "Second response did not attempt the output lock")

        def call(api, name, arguments, **kwargs):
            result = original_call(api, name, arguments, **kwargs)
            if arguments.get("query") == "second":
                self.assertTrue(first_logged.wait(2))
            return result

        packets = [{"id": 1, "method": "initialize"}, {"method": "notifications/initialized"}]
        packets += [{"id": ident, "method": "tools/call", "params": {
            "name": "capability_search", "arguments": {"query": query}}}
            for ident, query in ((2, "first"), (3, "second"))]
        source = io.BytesIO(("\n".join(map(json.dumps, packets)) + "\n").encode())
        output = io.StringIO()
        threads = SimpleNamespace(Lock=OutputLock, Event=threading.Event, Thread=threading.Thread)
        with patch("devtools.mcp.sys.stdin", SimpleNamespace(buffer=source)), patch("devtools.mcp.sys.stdout", output), \
                patch("devtools.mcp.threading", threads), \
                patch.object(Trace, "record", record), patch.object(API, "call", call):
            serve(self.state, "owner", operator=True)
        wire = [json.loads(line)["id"] for line in output.getvalue().splitlines()]
        rows = [json.loads(line) for line in (self.state / "telemetry/owner.jsonl").read_text().splitlines()]
        sent = [r for r in rows if r["kind"] == "mcp_out"]
        self.assertEqual([1, 2, 3], wire)
        self.assertEqual([True], lock_held, "The first trace and wire write must hold the same output lock")
        self.assertEqual(wire, [r["data"]["id"] for r in sent])
        self.assertEqual([1, 2, 3], [r["context"]["wire_sequence"] for r in sent])

    def test_mcp_wire_capture_correlates_live_channel_and_acknowledgement(self):
        generation = self.store.begin_session("worker", os.getpid())
        self.store.session("worker", generation=generation, state="idle", native_id="native-1")
        client = Client(self.state, "worker", channel=True, operator=True)
        try:
            message = self.store.send("owner", "worker", "Live direction", key="live")
            until(lambda: client.notifications)
            client.invoke("message_ack", message=message["id"])
        finally:
            client.close()
        records = [json.loads(line) for line in (self.state / "telemetry/worker.jsonl").read_text().splitlines()]
        channel = next(r for r in records if r["data"].get("method") == "notifications/claude/channel")
        self.assertIn("Live direction", channel["data"]["params"]["content"])
        self.assertEqual(generation, channel["context"]["generation"])
        self.assertEqual("native-1", channel["context"]["native_id"])
        self.assertTrue(any(r["kind"] == "mcp_in" and r["data"].get("method") == "tools/call" for r in records))
        events = self.store.events(actor="worker", limit=100)["events"]
        delivering = next(r for r in events if r["kind"] == "message_delivering")
        submitted = next(r for r in events if r["kind"] == "message_submitted")
        self.assertEqual(delivering["data"]["attempt"], submitted["data"]["attempt"])
        self.assertTrue(any(r["kind"] == "message_acknowledged" for r in events))

    def test_requested_inherited_and_observed_runtime_are_distinct(self):
        home = self.root / "codex"
        home.mkdir()
        (home / "config.toml").write_text('model="inherited-model"\nmodel_reasoning_effort="high"\n')
        with patch.dict(os.environ, {"CODEX_HOME": str(home)}):
            private, _, _ = prepare(self.state, "owner", "codex")
            requested(self.store, "owner", {"harness": "codex", "model": "requested-model"}, private)
        self.assertEqual({}, observed(self.store, "owner", "empty", {"thread": {"id": "one"}}))
        observed(self.store, "owner", "thread/start", {"model": "actual-model", "reasoningEffort": "medium"})
        events = self.store.events(actor="owner")["events"]
        configured = next(r["data"] for r in events if r["kind"] == "runtime_requested")
        actual = next(r["data"] for r in events if r["kind"] == "runtime_observed")
        self.assertEqual("requested-model", configured["settings"]["model"])
        self.assertEqual("inherited", configured["sources"]["reasoning"])
        self.assertEqual("high", configured["settings"]["reasoning"])
        self.assertIsNone(configured["settings"]["vendor"])
        self.assertEqual("actual-model", actual["settings"]["model"])
        self.assertNotIn("vendor", actual["settings"])

    def test_registered_codex_alias_retains_inherited_runtime_settings(self):
        manifest = self.root / "adapters.json"
        document = json.loads(adapters.MANIFEST.read_text())
        document["adapters"]["harness"]["custom-codex"] = "devtools.harnesses:CodexHarness"
        manifest.write_text(json.dumps(document))
        private = self.root / "private"
        private.mkdir()
        (private / "config.toml").write_text('model="inherited-model"\nmodel_reasoning_effort="high"\n')
        with patch.object(adapters, "MANIFEST", manifest):
            requested(self.store, "owner", {"harness": "custom-codex"}, private)
        event = self.store.events(kind="runtime_requested")["events"][-1]["data"]
        self.assertEqual("custom-codex", event["settings"]["harness"])
        self.assertEqual("inherited-model", event["settings"]["model"])
        self.assertEqual("high", event["settings"]["reasoning"])
        self.assertEqual("inherited", event["sources"]["model"])

    def test_capture_refreshes_all_configured_credentials_and_preserves_wire_arguments(self):
        policy_directory = self.root / "policy"
        policy_directory.mkdir()
        keys = {name: policy_directory / (name + ".key") for name in ("project", "role", "job")}
        for name, path in keys.items():
            path.write_text("fixture-" + name + "-secret")
            path.chmod(0o600)
        policy = policy_directory / "configuration.json"
        policy.write_text(json.dumps({"version": 1,
            "project": {"connection": {"credential": {"file": "project.key"}}},
            "roles": {"coder": {"connection": {"credential": {"file": "role.key"}}}},
            "jobs": {"1": {"connection": {"credential": {"file": "job.key"}}}}}))
        team_path = self.state / "team.json"
        team = json.loads(team_path.read_text())
        team["project"] = {"configuration_file": str(policy)}
        team_path.write_text(json.dumps(team))
        trace = Trace(self.state, "owner")
        self.addCleanup(trace.close)
        for rotation in ("", "-rotated"):
            values = ["fixture-" + name + "-secret" + rotation for name in keys]
            for path, value in zip(keys.values(), values):
                path.write_text(value)
            payload = {"text": " ".join(values), "serialized": json.dumps({"value": values[-1]})}
            original = json.dumps(payload)
            trace.record("native_in", payload)
            self.store.event("owner", "native_usage", observation=payload)
            self.assertEqual(original, json.dumps(payload), "Filtering must never change wire/provider arguments")
            retained = trace.path.read_text() + json.dumps(self.store.events(limit=100))
            for value in values:
                self.assertNotIn(value, retained)
        self.assertIn("[REDACTED]", retained)
        # A valid key can coincide with an envelope field. Redacting dictionary
        # keys must not leave the original unsanitized payload in that field.
        for value in ("data", "context"):
            keys["job"].write_text(value)
            trace.record("native_in", {"text": "Copied " + value}, frame_context={"label": value})
            row = json.loads(trace.path.read_text().splitlines()[-1])
            self.assertEqual("Copied [REDACTED]", row["data"]["text"])
            self.assertEqual("[REDACTED]", row["context"]["label"])
        # Unsafe sources suppress capture only, with a stable reason and envelope.
        keys["job"].chmod(0o644)
        trace.record("native_in", {"text": "unchecked sensitive data"})
        self.store.event("owner", "native_usage", observation="unchecked sensitive data")
        row = json.loads(trace.path.read_text().splitlines()[-1])
        self.assertEqual("native_in", row["kind"])
        self.assertEqual("owner", row["actor"])
        self.assertEqual("credential_filter_unavailable", row["data"]["reason"])
        event = self.store.events(kind="native_usage")["events"][-1]
        self.assertEqual("credential_filter_unavailable", event["data"]["reason"])
        self.assertNotIn("unchecked sensitive data", trace.path.read_text() + json.dumps(event))
        self.store.event("worker", "native_usage", observation={"inputTokens": 1})
        self.store.event("worker", "native_transcript", path="unavailable-path")
        Transcript(self.store, "worker", trace).poll()
        operator = API(self.state, "owner", operator=True)
        usage = operator.invoke("telemetry_usage", {"actor": "worker"})
        self.assertFalse(usage["observed"])
        self.assertEqual("credential_filter_unavailable", usage["events"][0]["data"]["reason"])
        keys["job"].chmod(0o600)
        self.store.event("worker", "native_usage", observation={"inputTokens": 2})
        self.assertTrue(operator.invoke("telemetry_usage", {"actor": "worker"})["observed"])

    def test_capture_does_not_initialize_state_or_require_an_evaluation_key(self):
        empty = self.root / "capture-only"
        empty.mkdir()
        trace = Trace(empty, "owner")
        self.addCleanup(trace.close)
        with patch.dict(os.environ, {}, clear=True):
            trace.record("native_in", {"message": "ordinary traffic"})
        self.assertEqual({"telemetry"}, {path.name for path in empty.iterdir()})
        row = json.loads(trace.path.read_text())
        self.assertEqual({"message": "ordinary traffic"}, row["data"])

    def test_transcript_reader_cannot_attribute_old_entries_to_replacement_session(self):
        first = self.store.begin_session("worker", os.getpid())
        transcript = self.root / "first.jsonl"
        transcript.write_text('{"message":{"usage":{"input_tokens":1}}}\n')
        self.store.event("worker", "native_transcript", path=str(transcript), native_id="first")
        trace = Trace(self.state, "worker", context=lambda: context(self.store, "worker"))
        self.addCleanup(trace.close)
        reader = Transcript(self.store, "worker", trace)
        reader.poll()
        self.store.session("worker", generation=first, state="stopped", desired="stopped", pid=None)
        # Final entries still belong to the stopped generation until replacement.
        with transcript.open("a") as stream:
            stream.write('{"message":{"usage":{"input_tokens":2}}}\n')
        reader.poll()
        second = self.store.begin_session("worker", os.getpid())
        with transcript.open("a") as stream:
            stream.write('{"message":{"model":"obsolete-model","usage":{"input_tokens":3}}}\n')
        reader.poll()
        usage = self.store.events(kind="native_usage")["events"]
        self.assertEqual([1, 2], [row["data"]["observation"]["input_tokens"] for row in usage])
        self.assertEqual({first}, {row["data"]["generation"] for row in usage})
        self.assertFalse(self.store.events(kind="runtime_observed")["events"])
        self.assertNotIn("obsolete-model", trace.path.read_text())
        current = self.root / "second.jsonl"
        current.write_text('{"message":{"model":"current-model","usage":{"input_tokens":4}}}\n')
        self.store.event("worker", "native_transcript", path=str(current), native_id="second")
        Transcript(self.store, "worker", trace).poll()
        latest = self.store.events(kind="native_usage")["events"][-1]
        self.assertEqual(second, latest["data"]["generation"])
        self.assertEqual(4, latest["data"]["observation"]["input_tokens"])

    def test_claude_transcript_tail_handles_partial_writes_and_redacts_nested_content(self):
        self.store.begin_session("worker", os.getpid())
        native = self.root / ".claude"
        transcript = native / "projects/project/session-one.jsonl"
        transcript.parent.mkdir(parents=True)
        trace = Trace(self.state, "worker", context=lambda: context(self.store, "worker"))
        self.addCleanup(trace.close)
        reader = Transcript(self.store, "worker", trace)
        with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(native), "TEST_API_KEY": "test-secret-value"}):
            hook(
                self.state,
                "worker",
                "claude",
                {
                    "hook_event_name": "SessionStart",
                    "session_id": "session-one",
                    "model": "reported-model",
                    "transcript_path": str(transcript),
                },
            )
            entry = {
                "message": {
                    "model": "reported-model",
                    "id": "msg-1",
                    "usage": {"input_tokens": 17},
                    "content": [{"text": "Tool result: test-secret-value"}, {"text": '{"password":"must-disappear"}'}],
                }
            }
            encoded = json.dumps(entry)
            transcript.write_text(encoded[:20])
            reader.poll()
            self.assertFalse(self.store.events(kind="native_usage")["events"])
            with transcript.open("a") as stream:
                stream.write(encoded[20:] + "\n")
            reader.poll()
            reader.poll()
            self.store.event("worker", "error", authorization="must-disappear", text="test-secret-value")
        usage = self.store.events(kind="native_usage")["events"]
        self.assertEqual(1, len(usage))
        self.assertEqual(17, usage[0]["data"]["observation"]["input_tokens"])
        retained = trace.path.read_text() + json.dumps(self.store.events(limit=100))
        self.assertNotIn("test-secret-value", retained)
        self.assertNotIn("must-disappear", retained)
        self.assertIn("reported-model", retained)
        outside = self.root / "session-one.jsonl"
        outside.write_text('{"message":{"content":"unrelated-private-file"}}\n')
        hook(
            self.state,
            "worker",
            "claude",
            {"hook_event_name": "Stop", "session_id": "session-one", "transcript_path": str(outside)},
        )
        reader.poll()
        self.assertNotIn("unrelated-private-file", trace.path.read_text())
        self.assertTrue(self.store.events(kind="transcript_rejected")["events"])
