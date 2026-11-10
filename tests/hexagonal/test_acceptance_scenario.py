"""Scenario checks cross real processes; these tests do not claim tmux acceptance."""

import io
import json
import signal
import sys
import time
from pathlib import Path

import pytest

from scripts.acceptance.scenario import (
    AcceptanceFailure,
    Scenario,
    application_project,
    atomic_json,
    fixture_config,
    main,
    native_config,
    run_fixture,
    run_native,
)
from scripts.testkit.adapters.diagnostics import describe
from tests.hexagonal.contracts.live_diagnostics import (
    LOCATIONS,
    PROVIDER_REASON,
    PROVIDER_SUMMARY,
    code_only_receipt,
    handshake_only,
    retained_reason,
)
from tests.hexagonal.support.live_diagnostics import (
    SESSION_START,
    TURN,
    failed_effect,
    native_configuration,
    observed_methods,
    peer_session,
    scripted_client,
    shared_directory,
    worker,
)


def test_fixture_scenario_crosses_processes_and_preserves_effect_counts(tmp_path):
    config = application_project(tmp_path, fixture_config(tmp_path))
    scenario = Scenario(tmp_path, 30, "fixture")
    try:
        run_fixture(scenario, config)
    finally:
        scenario.cleanup()
    assert len(scenario.checks) == 8
    assert all(check["status"] == "passed" for check in scenario.checks)
    assert len((tmp_path / "cli-effects.jsonl").read_text().splitlines()) == 1
    assert len((tmp_path / "mcp-effects.jsonl").read_text().splitlines()) == 2
    assert not (tmp_path / "scenario.json").exists()


def test_native_configuration_preserves_relative_paths_and_original_source(tmp_path):
    original = tmp_path / "operator"
    original.mkdir()
    source = original / "application.json"
    configuration = {
        "store": {"adapter": "memory"},
        "sessions": {"chosen": {"adapter": "codex", "options": {
            "command": ["operator-harness"], "cwd": "project", "application_tools": True,
            "thread": {"model": "operator-choice"}, "pass_env": ["OPERATOR_KEY"],
        }}},
        "providers": {"custom": {"adapter": "operator_package:factory", "options": {"path": "unchanged"}}},
        "guardrail": {"adapter": "rules", "options": {"default": "deny"}},
    }
    atomic_json(source, configuration)
    result = native_config(source, tmp_path / "evidence", "chosen")
    assert result["store"]["options"]["path"] == str(tmp_path / "evidence" / "state.sqlite3")
    assert result["sessions"]["chosen"]["options"]["cwd"] == str(original / "project")
    assert result["sessions"]["chosen"]["options"]["thread"] == {"model": "operator-choice"}
    assert result["providers"] == configuration["providers"]
    assert result["guardrail"] == configuration["guardrail"]
    assert json.loads(source.read_text()) == configuration


def test_native_scenario_requires_persisted_callback_using_protocol_peer(tmp_path):
    # This independently validates the native-profile criterion with a local
    # protocol peer. It is not a native vendor/model acceptance receipt.
    config = application_project(tmp_path, fixture_config(tmp_path))
    scenario = Scenario(tmp_path, 30, "native")
    try:
        run_native(scenario, config, "callback")
    finally:
        scenario.cleanup()
    assert all(check["status"] == "passed" for check in scenario.checks)


def test_native_driver_without_application_tools_is_explicit_failure(tmp_path):
    config = tmp_path / "application.json"
    atomic_json(config, {"sessions": {"operator": {"adapter": "codex", "options": {"command": ["missing"]}}}})
    with pytest.raises(AcceptanceFailure, match="native_application_tools_required"):
        native_config(config, tmp_path, "operator")


def test_native_session_completion_text_without_callback_does_not_pass(tmp_path):
    options = fixture_config(tmp_path)
    options["sessions"]["callback"]["options"]["command"].append("--text-only")
    config = application_project(tmp_path, options)
    scenario = Scenario(tmp_path, 1, "native")
    try:
        with pytest.raises(AcceptanceFailure, match="response_deadline|native_callback_missing"):
            run_native(scenario, config, "callback")
        scenario.close(scenario.clients[0])
        scenario.deadline = time.monotonic() + 3
        client = scenario.client(config)
        work = client.tool("list_work", {})["work"][0]
        evidence = client.tool("get_work", {"work_id": work["id"]})["evidence"]
        assert any(isinstance(item["result"], dict) and item["result"].get("turn", {}).get("status") == "completed" for item in evidence)
        assert not any(item.get("label") == "tmux-acceptance" for item in evidence)
    finally:
        scenario.cleanup()
    assert scenario.checks[0]["status"] == "failed"


def test_worker_without_tmux_records_failure_without_false_acceptance(tmp_path, monkeypatch):
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)
    previous_handlers = {number: signal.getsignal(number) for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}
    assert main(["--run-dir", str(tmp_path), "--profile", "fixture", "--timeout", "30"]) == 1
    assert {number: signal.getsignal(number) for number in previous_handlers} == previous_handlers
    report = json.loads((tmp_path / "scenario.json").read_text())
    assert report["status"] == "failed"
    assert report["error"] == {"type": "AcceptanceFailure", "code": "tmux_required"}
    assert not (tmp_path / "application.json").exists()
    assert Path(tmp_path / "scenario.json").stat().st_mode & 0o077 == 0


def test_refused_application_home_keeps_the_receipt_code_only_and_the_products_reason_private(tmp_path, monkeypatch):
    location = shared_directory(tmp_path, LOCATIONS["group-writable"][0])
    status, receipt, text, retained = worker(location / "run", "--profile", "fixture", monkeypatch=monkeypatch)
    assert status == 1
    private = (str(location), "writable")
    code_only_receipt(receipt, text, code="scenario_error", private=private)
    row = retained_reason(retained, check="isolated_application_configuration", words=private)
    assert row["details"]["directory"] == str(location)
    assert (location / "run" / "diagnostics.json").stat().st_mode & 0o777 == 0o600


def test_refused_native_session_start_retains_the_providers_own_explanation_privately(tmp_path, monkeypatch, capsys):
    config = native_configuration(tmp_path / "operator" / "native.json", {"refused": peer_session("--refuse-start")})
    status, receipt, text, retained = worker(tmp_path / "run", "--profile", "native", "--config", config,
                                             "--driver", "refused", "--story", "native-callback",
                                             monkeypatch=monkeypatch)
    assert status == 1
    printed = [line for line in capsys.readouterr().out.splitlines() if line.startswith("FAILED ")]
    assert len(printed) == 1, printed
    assert printed[0].startswith("FAILED configured_native_application_callback: application_tool_failed: start_session")
    assert PROVIDER_REASON in printed[0]
    code_only_receipt(receipt, text, code="application_tool_failed", private=(PROVIDER_REASON, PROVIDER_SUMMARY))
    retained_reason(retained, check="configured_native_application_callback", tool="start_session",
                    words=(PROVIDER_REASON, PROVIDER_SUMMARY))


@pytest.mark.parametrize("story,turn_observed", [("native-handshake", False), ("native-callback", True)],
                         ids=["handshake", "callback-control"])
def test_handshake_reaches_the_harness_session_start_but_never_a_turn(tmp_path, monkeypatch, story, turn_observed):
    trace = tmp_path / "methods.jsonl"
    config = native_configuration(tmp_path / "operator" / "native.json", {"observed": peer_session("--trace", trace)})
    status, receipt, _, retained = worker(tmp_path / "run", "--profile", "native", "--config", config,
                                          "--driver", "observed", "--story", story, monkeypatch=monkeypatch)
    # What the harness process itself received comes first: it is independent of the scenario's own accounting.
    methods = observed_methods(trace)
    if turn_observed:
        assert SESSION_START in methods and TURN in methods, methods
    else:
        handshake_only(methods, start=SESSION_START, turn=TURN)
    assert status == 0, retained
    assert receipt["status"] == "passed"
    if not turn_observed:
        assert receipt["budget"]["limits"]["turns"] == receipt["budget"]["consumed"]["turns"] == 0


def test_a_pane_that_cannot_encode_the_reason_does_not_replace_the_failure(tmp_path, monkeypatch):
    pane = io.TextIOWrapper(io.BytesIO(), encoding="ascii")
    monkeypatch.setattr(sys, "stdout", pane)
    scenario = Scenario(tmp_path, 30, "fixture")
    failure = AcceptanceFailure("application_tool_failed",
                                {"tool": "start_session", "error": {"code": "provider_error", "message": "refusé…"}})
    with pytest.raises(AcceptanceFailure) as raised, scenario.check("named_check"):
        raise failure
    assert raised.value is failure
    assert scenario.checks[-1]["failure"] == {"type": "AcceptanceFailure", "code": "application_tool_failed"}
    [row] = scenario.diagnostics
    assert row["check"] == "named_check" and row["details"]["error"]["message"] == "refusé…"


def test_a_failed_cleanup_met_again_while_unwinding_is_one_retained_failure(tmp_path, capsys):
    scenario = Scenario(tmp_path, 30, "fixture")

    def unclosable():
        raise OSError("client did not close")

    scenario.resources.defer("application-client", unclosable)
    # The worker's own sequence: a graceful shutdown check, then its unconditional final cleanup.
    with pytest.raises(AcceptanceFailure, match="runtime_cleanup_failed"), scenario.check("graceful_application_shutdown"):
        scenario.cleanup()
    with pytest.raises(AcceptanceFailure, match="runtime_cleanup_failed") as again:
        scenario.cleanup()
    scenario.diagnose("runtime_cleanup", again.value)
    assert [row["check"] for row in scenario.diagnostics] == ["graceful_application_shutdown"]
    assert [line for line in capsys.readouterr().out.splitlines() if line.startswith("FAILED ")] == [
        "FAILED " + describe(scenario.diagnostics[0])]


def test_an_interrupt_while_reading_the_preserved_outcome_is_not_swallowed():
    client = scripted_client(failed_effect("operation-1"), AcceptanceFailure("interrupted"))
    with pytest.raises(AcceptanceFailure, match="^interrupted$"):
        client.tool("start_session", {})
    assert [arguments["name"] for _, arguments in client.requests] == ["start_session", "get_operation"]
    # Any other failure of that best-effort read still reports the original tool failure.
    client = scripted_client(failed_effect("operation-2"), AcceptanceFailure("response_deadline"))
    with pytest.raises(AcceptanceFailure, match="^application_tool_failed$") as raised:
        client.tool("start_session", {})
    assert "operation" not in raised.value.detail
