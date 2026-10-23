"""Scenario checks cross real processes; these tests do not claim tmux acceptance."""

import json
import signal
import time
from pathlib import Path

import pytest

from scripts.acceptance.scenario import (
    AcceptanceFailure,
    Scenario,
    atomic_json,
    fixture_config,
    main,
    native_config,
    run_fixture,
    run_native,
)


def test_fixture_scenario_crosses_processes_and_preserves_effect_counts(tmp_path):
    config = tmp_path / "application.json"
    atomic_json(config, fixture_config(tmp_path))
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
    config = tmp_path / "application.json"
    atomic_json(config, fixture_config(tmp_path))
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
    config = tmp_path / "application.json"
    options = fixture_config(tmp_path)
    options["sessions"]["callback"]["options"]["command"].append("--text-only")
    atomic_json(config, options)
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
