"""Read Serena process-attempt telemetry without starting or changing a workload."""

import argparse
import json
import math
import os
import stat
from collections import Counter
from datetime import datetime
from pathlib import Path

MAX_LINE_BYTES = 1024 * 1024
MAX_SCAN_BYTES = 256 * 1024 * 1024
MAX_DETAILS = 100
STATUS_NAMES = frozenset({"starting", "ready", "unavailable", "capture_error", "dropped", "closed", "call_started", "call_finished"})
LIMITATION = (
    "Python audit events record attempts, not successful execution or confirmed privilege escalation. "
    "Audit hooks can be bypassed; missing findings do not establish that no processes ran. "
    "Loss counts arrive with later events; final dropped events may remain unreported."
)


def since_timestamp(value):
    """Require an explicit timezone, independent of the operator machine's locale."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("Missing timezone")
        return parsed.timestamp()
    except (ValueError, OverflowError) as error:
        raise argparse.ArgumentTypeError("Use an ISO-8601 timestamp with a timezone, such as 2026-10-04T18:00:00-04:00.") from error


def _scalar(value):
    if isinstance(value, str):
        return value[:1000]
    return value if value is None or isinstance(value, (int, float, bool)) else "[invalid value]"


def _detail(row, path, line):
    data, context = row["data"], row["context"]
    detail = {
        "time": row["time"], "actor": _scalar(row.get("actor")),
        "role": _scalar(context.get("role")), "job": _scalar(context.get("job")),
        "generation": _scalar(context.get("generation")), "connection": _scalar(row.get("connection")),
        "source": path.name, "line": line,
    }
    for key in ("event", "evidence", "origin", "executable", "cwd", "container", "request_id", "gateway_connection", "gateway_request_id", "correlation", "capture_truncated"):
        if key in data:
            detail[key] = _scalar(data[key])
    if "args" in data:
        args = data["args"]
        detail["args"] = [_scalar(value) for value in args[:24]] if isinstance(args, list) else _scalar(args)
    return detail


def _gateway_key(actor, connection, ident):
    if not isinstance(actor, str) or not isinstance(connection, str) or type(ident) not in (int, str):
        return None
    return actor, connection, type(ident).__name__, ident


class ObservationCoverage:
    """Correlate ordered host records without equating backend and gateway IDs."""

    def __init__(self, issue):
        self.issue = issue
        self.pending = {}
        self.providers = set()
        self.healthy = set()
        self.repl_calls = 0

    def accept(self, row, selected):
        data, kind = row["data"], row.get("kind")
        key = _gateway_key(row.get("actor"), row.get("connection"), data.get("id"))
        if kind == "mcp_in" and key is not None:
            params = data.get("params", {})
            is_repl = (data.get("method") == "tools/call" and isinstance(params, dict)
                       and params.get("name") == "serena_repl")
            previous = self.pending.get(key)
            if previous is not None and previous["repl"] and previous["selected"]:
                self.issue("gateway_request_id_reused_before_response")
            self.pending[key] = {"repl": is_repl, "selected": selected, "started": 0, "finished": 0,
                                 "healthy_finished": 0, "providers": set()}
        elif kind == "mcp_out" and key is not None:
            call = self.pending.pop(key, None)
            if not selected:
                return
            if call is None:
                self.issue("gateway_response_without_request")
            elif call["repl"]:
                self.repl_calls += 1
                self.providers.update(call["providers"])
                # A tool error may follow execution, a disconnect or a timeout;
                # it does not establish that dispatch was denied. Every response
                # needs matching lifecycle evidence or an explicit uncertainty gap.
                if not call["started"] or call["finished"] != call["started"]:
                    self.issue("calls_without_observation")
                elif call["healthy_finished"] != call["finished"]:
                    self.issue("calls_without_healthy_observation")
        elif kind in {"process_audit", "process_audit_status"}:
            identity = (row.get("actor"), row.get("connection"), data.get("container"))
            if not all(isinstance(value, str) and value for value in identity):
                if selected:
                    self.issue("audit_provider_identity_missing")
                return
            if selected:
                self.providers.add(identity)
            status = data.get("status")
            if not isinstance(status, str):
                return
            if status == "ready" or status in {"call_finished", "closed"} and data.get("observer_ready") is True:
                self.healthy.add(identity)
            if status in {"call_started", "call_finished"}:
                gateway = _gateway_key(row.get("actor"), data.get("gateway_connection"), data.get("gateway_request_id"))
                call = self.pending.get(gateway)
                if call is not None:
                    call["providers"].add(identity)
                    call["started" if status == "call_started" else "finished"] += 1
                    if (status == "call_finished" and data.get("observer_ready") is True
                            and data.get("capture_errors") == 0):
                        call["healthy_finished"] += 1

    def finish(self):
        for call in self.pending.values():
            if call["repl"] and call["selected"]:
                self.issue("unfinished_gateway_calls")
        for _ in self.providers - self.healthy:
            self.issue("providers_without_monitor_health")
        if not self.providers & self.healthy:
            self.issue("no_monitor_health_records_in_selection")
        return bool(self.providers & self.healthy)


def scan(state, *, actor=None, job=None, since=None, include_expected=False, limit=MAX_DETAILS,
         max_bytes=MAX_SCAN_BYTES):
    """Stream bounded regular JSONL files; retain counters and a bounded detail sample.

    Monitoring health describes evidence in the selected records. It is never a
    guarantee that all process execution was observed, even with enabled hooks.
    """
    directory = Path(state) / "telemetry"
    report = {
        "version": 1, "state": str(Path(state)),
        "filters": {"actor": actor, "job": job, "since": since},
        "coverage": "unavailable", "files": 0, "bytes": 0, "records": 0,
        "serena_records": 0, "attempts": 0, "findings": 0, "backend_attempts": 0,
        "truncated_details": 0,
        "repl_calls": 0, "providers": 0, "providers_with_health": 0,
        "statuses": {}, "issues": {}, "events": [], "details_omitted": 0,
        "limitation": LIMITATION,
    }
    issues, statuses = Counter(), Counter()
    pending_calls = set()

    def issue(name):
        issues[name] += 1

    coverage = ObservationCoverage(issue)

    if not directory.is_dir() or directory.is_symlink():
        issue("telemetry_directory_missing_or_not_regular")
        report["issues"] = dict(issues)
        return report
    try:
        paths = sorted(directory.glob("*.jsonl"))
    except OSError:
        issue("telemetry_directory_unreadable")
        report["issues"] = dict(issues)
        return report
    if actor is not None:
        # Actor is a filter, never a path component supplied to open().
        paths = [path for path in paths if path.stem == actor]
    if not paths:
        issue("no_matching_telemetry_files")
    stop = False
    for path in paths:
        if stop:
            break
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    issue("nonregular_telemetry_file")
                    continue
                with os.fdopen(descriptor, "rb", closefd=False) as stream:
                    report["files"] += 1
                    line_number = 0
                    while True:
                        remaining = max_bytes - report["bytes"]
                        raw = stream.readline(min(MAX_LINE_BYTES + 1, remaining + 1))
                        if not raw:
                            break
                        line_number += 1
                        report["bytes"] += len(raw)
                        if report["bytes"] > max_bytes:
                            issue("scan_byte_limit_reached")
                            stop = True
                            break
                        if len(raw) > MAX_LINE_BYTES:
                            issue("oversized_telemetry_record")
                            # Do not parse the remainder as an independent record.
                            while raw and not raw.endswith(b"\n"):
                                raw = stream.readline(min(MAX_LINE_BYTES, max_bytes - report["bytes"] + 1))
                                report["bytes"] += len(raw)
                                if report["bytes"] > max_bytes:
                                    issue("scan_byte_limit_reached")
                                    stop = True
                                    break
                            if stop:
                                break
                            continue
                        if not raw.endswith(b"\n"):
                            issue("partial_telemetry_record")
                            break
                        try:
                            row = json.loads(raw)
                        except (ValueError, UnicodeError, RecursionError):
                            issue("malformed_telemetry_record")
                            continue
                        if not isinstance(row, dict):
                            issue("malformed_telemetry_record")
                            continue
                        report["records"] += 1
                        if row.get("component") != "serena-mcp":
                            continue
                        if actor is not None and row.get("actor") != actor:
                            continue
                        stamp = row.get("time")
                        if (isinstance(stamp, bool) or not isinstance(stamp, (int, float))
                                or isinstance(stamp, float) and not math.isfinite(stamp)):
                            issue("invalid_serena_timestamp")
                            continue
                        selected_time = since is None or stamp >= since
                        context, data = row.get("context"), row.get("data")
                        if not isinstance(context, dict) or not isinstance(data, dict):
                            if selected_time:
                                issue("invalid_serena_record")
                            continue
                        if data.get("capture") == "suppressed":
                            # Context is removed with suppressed captures, so job
                            # attribution is impossible. Do not silently filter it.
                            if selected_time:
                                issue("suppressed_serena_capture")
                            continue
                        selected = selected_time and (job is None or str(context.get("job")) == str(job))
                        # Keep earlier input/lifecycle metadata so a response
                        # selected by --since can refer to a call begun earlier.
                        coverage.accept(row, selected)
                        if not selected:
                            continue
                        report["serena_records"] += 1
                        kind = row.get("kind")
                        if kind == "process_audit_status":
                            status = data.get("status")
                            if not isinstance(status, str) or status not in STATUS_NAMES:
                                issue("invalid_audit_status")
                                continue
                            statuses[status] += 1
                            if status in {"unavailable", "capture_error", "dropped"}:
                                issue("monitor_" + status)
                            elif status == "closed":
                                if data.get("capture_errors") != 0:
                                    issue("closed_capture_errors_or_unknown")
                                if data.get("observer_ready") is not True:
                                    issue("closed_without_ready_observer")
                            elif status in {"call_started", "call_finished"}:
                                request_key = _gateway_key(row.get("actor"), row.get("connection"), data.get("request_id"))
                                key = (data.get("container"), request_key)
                                if request_key is None or not isinstance(data.get("container"), str):
                                    issue("uncorrelated_audit_call")
                                elif status == "call_started":
                                    pending_calls.add(key)
                                else:
                                    pending_calls.discard(key)
                                    if data.get("observer_ready") is not True:
                                        issue("call_without_ready_observer")
                                    if data.get("capture_errors") != 0:
                                        issue("call_capture_errors_or_unknown")
                        elif kind == "process_audit":
                            # Unknown attribution must remain visible. Nothing
                            # here infers expected activity from an executable.
                            origin = data.get("origin")
                            report["attempts"] += 1
                            if origin == "backend":
                                report["backend_attempts"] += 1
                            else:
                                report["findings"] += 1
                            if data.get("evidence") != "attempt":
                                issue("invalid_audit_evidence")
                            if data.get("dropped_before"):
                                issue("dropped_audit_events")
                            if data.get("capture_truncated"):
                                report["truncated_details"] += 1
                            if origin != "backend" or include_expected:
                                if len(report["events"]) < limit:
                                    report["events"].append(_detail(row, path, line_number))
                                else:
                                    report["details_omitted"] += 1
            finally:
                os.close(descriptor)
        except OSError:
            issue("unreadable_telemetry_file")
    if pending_calls:
        issues["unfinished_audit_calls"] = len(pending_calls)
    health_observed = coverage.finish()
    report["repl_calls"] = coverage.repl_calls
    report["providers"] = len(coverage.providers)
    report["providers_with_health"] = len(coverage.providers & coverage.healthy)
    report["statuses"], report["issues"] = dict(statuses), dict(issues)
    report["coverage"] = "incomplete" if issues else "best_effort"
    if not health_observed and not report["attempts"]:
        report["coverage"] = "unavailable"
    return report


def render(report):
    lines = [
        "Serena process audit (observe only)",
        f"Monitoring evidence: {report['coverage']}",
        f"Process attempts: {report['attempts']}; needs review: {report['findings']}; attributed backend: {report['backend_attempts']}",
        f"Read {report['files']} telemetry files, {report['records']} records ({report['bytes']} bytes).",
        "Monitor status counts: " + json.dumps(report["statuses"], sort_keys=True),
    ]
    if report["issues"]:
        lines.append("Coverage gaps: " + json.dumps(report["issues"], sort_keys=True))
    if not report["findings"]:
        lines.append("No review findings in the records read. This is not proof that no process launches occurred.")
    for event in report["events"]:
        # JSON quoting makes terminal control characters inert and keeps the
        # report safe to paste; no raw REPL source or environment is displayed.
        lines.append(json.dumps(event, sort_keys=True, ensure_ascii=True))
    if report["details_omitted"]:
        lines.append(f"{report['details_omitted']} additional event details omitted; totals include them.")
    if report["truncated_details"]:
        lines.append(f"{report['truncated_details']} process events contain bounded/truncated source details.")
    lines.append(report["limitation"])
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", required=True, type=Path, help="Existing dev-lane state directory containing telemetry/.")
    parser.add_argument("--actor", help="Only this actor's telemetry.")
    parser.add_argument("--job", help="Only this job's telemetry.")
    parser.add_argument("--since", type=since_timestamp, help="ISO-8601 timestamp with timezone (inclusive).")
    parser.add_argument("--json", action="store_true", help="Return a JSON report.")
    parser.add_argument("--include-expected", action="store_true", help="Also show backend-attributed event details.")
    parser.add_argument("--limit", type=int, default=MAX_DETAILS, help="Maximum event details; totals still cover the scan (default: 100).")
    parser.add_argument("--fail-on-findings", action="store_true", help="Exit 1 if review findings exist; coverage gaps always exit 2.")
    args = parser.parse_args(argv)
    if not 0 <= args.limit <= 10000:
        parser.error("--limit must be between 0 and 10000")
    report = scan(args.state, actor=args.actor, job=args.job, since=args.since,
                  include_expected=args.include_expected, limit=args.limit)
    print(json.dumps(report, sort_keys=True, ensure_ascii=True) if args.json else render(report))
    if report["coverage"] != "best_effort":
        return 2
    return 1 if args.fail_on_findings and report["findings"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
