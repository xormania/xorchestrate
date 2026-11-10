"""Private failure reasons: bounded retention and the one-line views built from it."""

import json
import os
import threading

from scripts.testkit.adapters import diagnostics
from scripts.testkit.adapters.evidence import write_json
from tests.hexagonal.contracts.live_diagnostics import PROVIDER_REASON, surfaced_reason


class Coded(Exception):
    def __init__(self, message, code, details):
        super().__init__(message)
        self.code, self.details = code, details


def scenario_failure(directory, check, rows):
    """A scenario's private diagnostics where the composed run places them."""
    target = directory / check / "20260101T000000Z-000000000000"
    target.mkdir(parents=True)
    return diagnostics.write_failures(target, rows)


def test_failure_row_keeps_the_error_code_message_and_details_of_any_exception():
    row = diagnostics.failure("named_check", Coded("The home is unsafe.", "invalid_configuration", {"directory": "/shared"}))
    assert row == {"check": "named_check", "type": "Coded", "message": "The home is unsafe.",
                   "code": "invalid_configuration", "details": {"directory": "/shared"}}
    plain = diagnostics.failure(None, ValueError("no structure"))
    assert plain == {"check": None, "type": "ValueError", "message": "no structure"}


def test_retained_values_are_json_safe_and_bounded_in_text_breadth_and_depth():
    nested = current = {}
    for _ in range(40):
        current["next"] = {}
        current = current["next"]
    value = diagnostics.bounded({"text": "x" * 100_000, "wide": list(range(10_000)), "deep": nested,
                                 "other": object(), "number": float("nan")})
    encoded = json.dumps(value, allow_nan=False)
    assert len(encoded) < 20_000
    assert len(value["text"]) <= diagnostics.MAX_TEXT + 1
    assert len(value["wide"]) == diagnostics.MAX_ITEMS
    assert isinstance(value["other"], str) and isinstance(value["number"], str)
    depth, current = 0, value
    while isinstance(current, dict):
        current, depth = current.get("next", current.get("deep")), depth + 1
    assert depth <= diagnostics.MAX_DEPTH + 1, f"Retained nesting is unbounded: {depth} levels"


def test_written_diagnostics_are_private_and_declare_their_scope(tmp_path):
    path = diagnostics.write_failures(tmp_path, [diagnostics.failure("check", ValueError("reason"))])
    assert path == tmp_path / diagnostics.NAME
    assert path.stat().st_mode & 0o077 == 0
    document = json.loads(path.read_text())
    assert document["scope"] == "private" and document["schema_version"] == 1
    assert document["failures"][0]["message"] == "reason"


def test_description_prefers_the_preserved_provider_outcome_over_the_response_summary():
    row = {"check": "configured_native_session_handshake", "type": "AcceptanceFailure", "code": "application_tool_failed",
           "message": "application_tool_failed", "details": {
               "tool": "start_session",
               "error": {"code": "operation_unknown", "message": "Subprocess returned a JSON-RPC error.",
                         "details": {"operation_id": "abc"}},
               "operation": {"status": "unknown", "error": {
                   "code": "provider_error", "message": "Subprocess returned a JSON-RPC error.",
                   "details": {"remote_error": {"message": "Invalid params", "data": PROVIDER_REASON}}}}}}
    line = diagnostics.describe(row)
    assert line.startswith("configured_native_session_handshake: application_tool_failed: start_session -> provider_error:")
    assert PROVIDER_REASON in line
    del row["details"]["operation"]
    summary = diagnostics.describe(row)
    assert "start_session -> operation_unknown: Subprocess returned a JSON-RPC error." in summary
    assert PROVIDER_REASON not in summary


def test_description_names_the_request_met_by_a_transport_or_budget_failure_and_stays_one_bounded_line():
    assert diagnostics.describe({"check": "callback", "code": "response_deadline", "message": "response_deadline",
                                 "details": {"tool": "start_session"}}) == "callback: response_deadline during start_session"
    assert diagnostics.describe({"check": None, "type": "OSError", "message": "disk\nfull"}) == "scenario: OSError: disk full"
    assert diagnostics.describe({"check": "bare", "code": "tmux_required", "message": "tmux_required"}) == "bare: tmux_required"
    long = diagnostics.describe({"check": "long", "code": "error", "message": "y" * 10_000})
    assert "\n" not in long and len(long) <= diagnostics.MAX_LINE + 1


def test_lines_cover_checks_blocked_before_launch_and_scenarios_that_retained_a_reason(tmp_path):
    write_json(tmp_path / "report.json", {"checks": [
        {"name": "story:coordination", "status": "setup_error", "launched": False, "reason": "Run location /shared is unsafe"},
        {"name": "tests", "status": "failed", "launched": True, "reason": "captured stderr of a launched command"},
        {"name": "ruff", "status": "passed", "launched": True, "reason": ""}]})
    scenario_failure(tmp_path, "story%3Anative-callback%3Aworker", [
        {"check": "configured_native_application_callback", "code": "application_tool_failed",
         "message": "application_tool_failed", "details": {"tool": "start_session", "error": {
             "code": "configuration_error", "message": "Configured authentication method is unavailable."}}}])
    lines = diagnostics.failure_lines(tmp_path)
    assert len(lines) == 2
    surfaced_reason(lines, check="story:coordination", words=("setup_error", "Run location /shared is unsafe"))
    surfaced_reason(lines, check="story:native-callback:worker", words=(
        "failed at configured_native_application_callback", "start_session -> configuration_error",
        "Configured authentication method is unavailable."))
    # A launched command's captured stderr is not a retained reason; it stays in its command record.
    assert not any("captured stderr" in line for line in lines)


def test_lines_ignore_absent_malformed_and_oversized_sources_and_are_bounded_in_number(tmp_path):
    assert diagnostics.failure_lines(tmp_path / "absent") == []
    (tmp_path / "report.json").write_text("not json")
    broken = tmp_path / "story" / "run"
    broken.mkdir(parents=True)
    (broken / diagnostics.NAME).write_text("[1, 2, 3]")
    huge = tmp_path / "huge" / "run"
    huge.mkdir(parents=True)
    (huge / diagnostics.NAME).write_text(json.dumps({"failures": [{"check": "c", "message": "z" * (diagnostics.MAX_FILE_BYTES + 1)}]}))
    assert diagnostics.failure_lines(tmp_path) == []
    scenario_failure(tmp_path, "many", [{"check": f"check-{index}", "code": "failed"} for index in range(100)])
    assert len(diagnostics.failure_lines(tmp_path)) == diagnostics.MAX_LINES


def test_written_diagnostics_always_stay_readable_and_keep_naming_the_request(tmp_path):
    wide = {f"field-{index}": ["é" * diagnostics.MAX_TEXT] * 32 for index in range(8)}
    rows = [{"check": f"check-{index}", "code": "application_tool_failed",
             "details": {"tool": "start_session", **wide}} for index in range(100)]
    target = tmp_path / "story" / "run"
    target.mkdir(parents=True)
    path = diagnostics.write_failures(target, rows)
    assert path.stat().st_size < diagnostics.MAX_FILE_BYTES
    lines = diagnostics.failure_lines(tmp_path)
    assert lines, "An oversized write must not make the retained reasons unreadable"
    assert all("start_session" in line for line in lines)


def test_lines_never_follow_symlinks_or_open_special_files(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    scenario_failure(elsewhere, "planted", [{"check": "foreign", "code": "planted_reason"}])
    checks = tmp_path / "checks"
    checks.mkdir()
    (checks / "linked-check").symlink_to(elsewhere / "planted", target_is_directory=True)
    run = checks / "story" / "run"
    run.mkdir(parents=True)
    (run / diagnostics.NAME).symlink_to(elsewhere / "planted" / "20260101T000000Z-000000000000" / diagnostics.NAME)
    fifo = checks / "fifo-story" / "run"
    fifo.mkdir(parents=True)
    os.mkfifo(fifo / diagnostics.NAME)
    result = []
    reader = threading.Thread(target=lambda: result.append(diagnostics.failure_lines(checks)), daemon=True)
    reader.start()
    reader.join(10)
    assert not reader.is_alive(), "Reading a FIFO in place of the diagnostics blocked the reader"
    assert result == [[]], result
