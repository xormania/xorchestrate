import argparse
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from devtools.process_report import main, render, scan, since_timestamp

ROOT = Path(__file__).resolve().parents[2]


class ProcessReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)
        self.telemetry = self.state / "telemetry"
        self.telemetry.mkdir()

    def row(self, kind="process_audit", *, actor="worker", job=1, stamp=100, **data):
        if kind in {"process_audit", "process_audit_status"}:
            data = {"container": "serena-1", **data}
        if kind == "process_audit":
            data = {"event": "subprocess.Popen", "evidence": "attempt", "origin": "repl", **data}
        return {"time": stamp, "actor": actor, "component": "serena-mcp", "connection": "conn",
                "context": {"job": job, "generation": 1, "role": "coder"}, "kind": kind, "data": data}

    def write(self, *rows, actor="worker"):
        with (self.telemetry / f"{actor}.jsonl").open("a") as stream:
            for row in rows:
                stream.write(json.dumps(row) + "\n")

    def ready(self, **kwargs):
        return self.row("process_audit_status", status="ready", **kwargs)

    def call(self, status, ident=1, **kwargs):
        data = {"observer_ready": True, "capture_errors": 0} if status == "call_finished" else {}
        return self.row("process_audit_status", status=status, request_id=ident, **data, **kwargs)

    def request(self, ident, **kwargs):
        return self.row("mcp_in", id=ident, method="tools/call", params={"name": "serena_repl"}, **kwargs)

    def response(self, ident, *, error=False, **kwargs):
        return self.row("mcp_out", id=ident, result={"isError": error, "content": []}, **kwargs)

    def observed(self, status, *, gateway_id, ident=99, **kwargs):
        row = self.call(status, ident=ident, gateway_connection="conn", gateway_request_id=gateway_id, **kwargs)
        row["connection"] = "observer-connection"
        return row

    def run_cli(self, *args):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["--state", str(self.state), "--json", *args])
        return code, json.loads(output.getvalue())

    def test_review_attempts_are_distinct_from_backend_and_success_claims(self):
        self.write(self.ready(), self.row(origin="backend", executable="/usr/bin/php"),
                   self.row(executable="/bin/sh"), self.row(origin="unknown", executable="/bin/false"))
        report = scan(self.state)
        self.assertEqual((3, 2, 1), (report["attempts"], report["findings"], report["backend_attempts"]))
        self.assertEqual(["repl", "unknown"], [event["origin"] for event in report["events"]])
        self.assertEqual("best_effort", report["coverage"])
        self.assertIn("not successful execution", report["limitation"])
        self.assertEqual(3, len(scan(self.state, include_expected=True)["events"]))
        self.assertEqual(0, self.run_cli()[0])
        self.assertEqual(1, self.run_cli("--fail-on-findings")[0])

    def test_status_complete_call_supports_since_filter_after_startup(self):
        self.write(self.ready(stamp=5), self.call("call_started", stamp=10), self.row(stamp=11),
                   self.call("call_finished", stamp=12))
        report = scan(self.state, since=10, job="1", actor="worker")
        self.assertEqual("best_effort", report["coverage"])
        self.assertEqual({"call_started": 1, "call_finished": 1}, report["statuses"])

    def test_missing_and_old_logs_do_not_report_clean(self):
        empty = scan(self.state)
        self.assertEqual("unavailable", empty["coverage"])
        self.assertIn("no_matching_telemetry_files", empty["issues"])
        self.write(self.row("mcp_in", method="tools/call", params={"name": "serena_repl"}))
        report = scan(self.state)
        self.assertEqual("unavailable", report["coverage"])
        self.assertIn("no_monitor_health_records_in_selection", report["issues"])
        self.assertEqual(2, self.run_cli()[0])
        self.assertIn("not proof", render(report))

    def test_failures_dropped_records_and_unfinished_calls_remain_visible(self):
        self.write(self.ready(), self.call("call_started"),
                   self.row("process_audit_status", status="capture_error"), self.row(dropped_before=3))
        report = scan(self.state)
        self.assertEqual("incomplete", report["coverage"])
        self.assertEqual(1, report["issues"]["unfinished_audit_calls"])
        self.assertIn("monitor_capture_error", report["issues"])
        self.assertIn("dropped_audit_events", report["issues"])
        self.assertEqual(2, self.run_cli("--fail-on-findings")[0])

    def test_job_actor_and_time_filters_select_only_matching_events(self):
        self.write(self.ready(), self.row(job=1, stamp=110), self.row(job=2, stamp=120))
        self.write(self.ready(actor="other"), self.row(actor="other"), actor="other")
        report = scan(self.state, actor="worker", job="2", since=115)
        self.assertEqual(1, report["attempts"])
        self.assertEqual(2, report["events"][0]["job"])
        self.assertEqual(1, report["files"])
        self.assertEqual(0, scan(self.state, actor="../worker")["files"])

    def test_suppressed_context_is_explicit_even_when_job_filter_cannot_attribute_it(self):
        self.write(self.ready(), {**self.row("process_audit"), "context": {},
                                 "data": {"capture": "suppressed", "reason": "credential_filter_unavailable"}})
        report = scan(self.state, job=1)
        self.assertEqual("incomplete", report["coverage"])
        self.assertEqual(1, report["issues"]["suppressed_serena_capture"])

    def test_malformed_partial_and_oversized_lines_are_not_treated_as_clean(self):
        self.write(self.ready())
        with (self.telemetry / "worker.jsonl").open("ab") as stream:
            stream.write(b"garbage\n" + b"x" * 600 + b"\n" + b'{"partial":')
        with patch("devtools.process_report.MAX_LINE_BYTES", 500):
            report = scan(self.state)
        self.assertEqual(1, report["issues"]["malformed_telemetry_record"])
        self.assertEqual(1, report["issues"]["oversized_telemetry_record"])
        self.assertEqual(1, report["issues"]["partial_telemetry_record"])

    def test_scan_and_output_bounds_preserve_counts_and_disclose_incomplete_input(self):
        self.write(self.ready(), *(self.row() for _ in range(5)))
        report = scan(self.state, limit=2)
        self.assertEqual((5, 2, 3), (report["findings"], len(report["events"]), report["details_omitted"]))
        self.assertEqual("best_effort", report["coverage"])
        bounded = scan(self.state, max_bytes=500)
        self.assertIn("scan_byte_limit_reached", bounded["issues"])
        self.assertLessEqual(bounded["bytes"], 501)

    def test_report_does_not_follow_symlinks_or_echo_terminal_control_characters(self):
        external = self.state / "external"
        external.write_text("private-value")
        (self.telemetry / "linked.jsonl").symlink_to(external)
        self.write(self.ready(), self.row(executable="\x1b[31mexample", args=["\x00argument"]))
        report = scan(self.state)
        self.assertIn("unreadable_telemetry_file", report["issues"])
        text = render(report)
        self.assertNotIn("private-value", text)
        self.assertNotIn("\x1b", text)
        self.assertNotIn("\x00", text)

    def test_timestamp_requires_timezone(self):
        self.assertEqual(0, since_timestamp("1970-01-01T00:00:00Z"))
        self.assertEqual(0, since_timestamp("1969-12-31T19:00:00-05:00"))
        with self.assertRaises(argparse.ArgumentTypeError):
            since_timestamp("2026-10-04T18:00:00")

    def test_healthy_actor_cannot_hide_other_actors_legacy_successful_call(self):
        self.write(self.ready())
        self.write(self.request(1, actor="other"), self.response(1, actor="other"), actor="other")
        report = scan(self.state)
        self.assertEqual("incomplete", report["coverage"])
        self.assertEqual(1, report["issues"]["calls_without_observation"])
        self.assertEqual(2, self.run_cli()[0])

    def test_gateway_and_backend_ids_are_distinct_and_since_keeps_input_metadata(self):
        self.write(self.request("outer-request", stamp=10),
                   self.observed("call_started", gateway_id="outer-request", stamp=11),
                   self.observed("call_finished", gateway_id="outer-request", stamp=12),
                   self.response("outer-request", stamp=13))
        report = scan(self.state, since=13)
        self.assertEqual("best_effort", report["coverage"])
        self.assertEqual((1, 1, 1), (report["repl_calls"], report["providers"], report["providers_with_health"]))

    def test_matching_gateway_id_from_different_connection_cannot_cover_call(self):
        started = self.observed("call_started", gateway_id=1)
        finished = self.observed("call_finished", gateway_id=1)
        started["data"]["gateway_connection"] = "different-connection"
        finished["data"]["gateway_connection"] = "different-connection"
        self.write(self.request(1), started, finished, self.response(1))
        report = scan(self.state)
        self.assertEqual(1, report["issues"]["calls_without_observation"])

    def test_error_response_does_not_establish_denial_before_dispatch(self):
        self.write(self.ready(), self.request(1), self.response(1, error=True))
        report = scan(self.state)
        self.assertEqual("incomplete", report["coverage"])
        self.assertEqual(1, report["issues"]["calls_without_observation"])

    def test_healthy_actor_cannot_hide_other_actors_legacy_error_response(self):
        self.write(self.ready())
        self.write(self.request(1, actor="other"), self.response(1, actor="other", error=True), actor="other")
        report = scan(self.state)
        self.assertEqual("incomplete", report["coverage"])
        self.assertEqual(1, report["issues"]["calls_without_observation"])

    def test_gateway_numeric_and_string_ids_do_not_alias(self):
        self.write(self.request(1), self.observed("call_started", gateway_id=1),
                   self.observed("call_finished", gateway_id=1), self.response(1),
                   self.request("1"), self.response("1"))
        report = scan(self.state)
        self.assertEqual(2, report["repl_calls"])
        self.assertEqual(1, report["issues"]["calls_without_observation"])

    def test_process_event_requires_health_from_its_own_provider(self):
        self.write(self.ready(), self.row(container="different-container"))
        report = scan(self.state)
        self.assertEqual("incomplete", report["coverage"])
        self.assertEqual(1, report["issues"]["providers_without_monitor_health"])

    def test_wrapper_runs_from_other_directory_without_initializing_state(self):
        self.write(self.ready())
        before = sorted(path.relative_to(self.state) for path in self.state.rglob("*"))
        result = subprocess.run([sys.executable, str(ROOT / "devtools/scripts/serena-audit.py"),
                                 "--state", str(self.state), "--json"], cwd="/", text=True,
                                capture_output=True, timeout=10, check=False)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("best_effort", json.loads(result.stdout)["coverage"])
        self.assertEqual(before, sorted(path.relative_to(self.state) for path in self.state.rglob("*")))


if __name__ == "__main__":
    unittest.main()
