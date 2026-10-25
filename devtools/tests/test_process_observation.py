import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from devtools.process_observation import MAX_LINE, PREFIX, ProcessObservation, origin
from devtools.telemetry import clean


class ProcessObservationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.secret = "example-private-value-for-redaction"
        cleaner = patch("devtools.telemetry.clean_for_state", side_effect=lambda _, value: clean(value, secrets=[self.secret]))
        cleaner.start()
        self.addCleanup(cleaner.stop)
        self.observer = ProcessObservation(self.directory, "coder", "container-1", {"role": "coder", "job": 7})
        self.addCleanup(self.observer.close)

    def rows(self):
        return [json.loads(line) for line in (self.directory / "telemetry/coder.jsonl").read_text().splitlines()]

    def packet(self, **values):
        return PREFIX + json.dumps({"version": 1, "kind": "process_attempt", "time_ns": time.time_ns(), "pid": 10,
                                    "event": "subprocess.Popen", "args": ["php", "--version"], "stack": [],
                                    **values}).encode() + b"\n"

    def test_observed_attempt_is_redacted_and_host_identity_cannot_be_forged(self):
        self.observer.begin(3, {"role": "coder", "job": 7})
        self.observer.consume(self.packet(args=["php", self.secret], request_id=3, actor="owner", job=999,
                                          origin="backend", evidence="success"))
        self.observer.finish(3)
        row = next(row for row in self.rows() if row["kind"] == "process_audit")
        self.assertEqual("coder", row["actor"])
        self.assertEqual(7, row["context"]["job"])
        self.assertEqual("attempt", row["data"]["evidence"])
        self.assertEqual("unknown", row["data"]["origin"])
        self.assertEqual("mcp_request", row["data"]["correlation"])
        self.assertEqual(["php", "[REDACTED]"], row["data"]["args"])
        self.assertNotIn(self.secret, (self.directory / "telemetry/coder.jsonl").read_text())

    def test_delayed_delivery_uses_event_time_not_current_request(self):
        with patch("devtools.process_observation.time.time_ns", side_effect=[100, 200, 300]):
            self.observer.begin(1, {"job": 7})
            self.observer.finish(1)
            self.observer.begin(2, {"job": 8})
        self.observer.consume(self.packet(time_ns=150))
        row = next(row for row in self.rows() if row["kind"] == "process_audit")
        self.assertEqual(1, row["data"]["request_id"])
        self.assertEqual(7, row["context"]["job"])
        self.assertEqual("time_window", row["data"]["correlation"])

    def test_stderr_audit_is_not_duplicated_to_unredacted_provider_log(self):
        source = io.BytesIO(b"ordinary provider message\n" + self.packet(args=[self.secret]))
        log = io.BytesIO()
        self.observer.read(source, log)
        self.assertEqual(b"ordinary provider message\n", log.getvalue())
        self.assertNotIn(self.secret, json.dumps(self.rows()))

    def test_malformed_oversized_and_dropped_records_are_visible_without_raw_payload(self):
        source = io.BytesIO(PREFIX + b"invalid " + self.secret.encode() + b"\n" + PREFIX + b"x" * MAX_LINE + b"\n"
                            + self.packet(dropped_before=4))
        log = io.BytesIO()
        self.observer.read(source, log)
        statuses = [row["data"]["status"] for row in self.rows() if row["kind"] == "process_audit_status"]
        self.assertIn("capture_error", statuses)
        self.assertIn("dropped", statuses)
        self.assertEqual(b"", log.getvalue())
        self.assertEqual(3, self.observer.capture_errors)

    def test_missing_hook_and_ready_hook_have_distinct_status(self):
        self.observer.check_ready()
        self.observer.consume(self.packet(kind="observer_ready"))
        self.observer.begin(1, {"job": 7})
        self.observer.finish(1)
        rows = self.rows()
        self.assertTrue(any(row["data"].get("status") == "unavailable" for row in rows))
        finished = next(row["data"] for row in rows if row["data"].get("status") == "call_finished")
        self.assertTrue(finished["observer_ready"])

    def test_telemetry_failure_does_not_raise_into_provider(self):
        with patch.object(self.observer.trace, "record", side_effect=OSError("disk full")):
            self.observer.consume(self.packet())
            self.observer.begin(3, {})
            self.observer.finish(3)
        self.assertGreater(self.observer.capture_errors, 0)

    def test_backend_classification_requires_launch_context_not_executable(self):
        self.assertEqual("unknown", origin([]))
        direct = [{"file": "<serena_repl>", "function": "<module>"}]
        self.assertEqual("repl", origin(direct))
        backend = [{"file": "/opt/serena-env/lib/python3.11/site-packages/solidlsp/util/subprocess_util.py",
                    "function": "do_popen"}]
        self.assertEqual("backend", origin(backend))
        self.assertEqual("repl", origin([*backend, *direct]))
        self.assertEqual("repl", origin([{"file": "/workspace/processes.py", "function": "do_popen"}, *direct]))
