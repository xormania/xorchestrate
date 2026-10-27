"""Managed lifecycle preserves proof truth across operational boundaries."""

import pytest

from scripts.testkit.operator import decide, project
from scripts.testkit.operator_cli import watch
from scripts.testkit.operator_composition import prepare_run, read_json
from tests.hexagonal.contracts.managed_operator import FINALIZATION_CASES
from tests.hexagonal.support.managed_operator import frozen_plan, native_configuration


@pytest.mark.parametrize("proof,verified,forced,collection,reason,expected", FINALIZATION_CASES)
def test_required_operational_failures_cannot_borrow_a_successful_proof(proof, verified, forced, collection, reason, expected):
    state = {"phase": "finalizing", "proof": proof, "cleanup": {"verified": verified, "forced": forced}, "reason": reason}
    change = decide(state, "finalized", collection={"status": collection, "archive": "private.zip"})
    assert change["outcome"] == expected
    assert change["phase"] == ("incomplete" if proof == "incomplete" else "finished")
    assert "proof" not in change


@pytest.mark.parametrize("report,exit_status,proof", [
    (None, 0, "incomplete"),
    ({"status": "passed", "selected_check_ids": [], "checks": []}, 0, "incomplete"),
    ({"status": "passed", "selected_check_ids": ["selected"], "checks": [{"name": "other"}]}, 0, "incomplete"),
    ({"status": "passed", "selected_check_ids": ["selected"], "checks": [{"name": "selected"}]}, 1, "incomplete"),
    ({"status": "passed", "selected_check_ids": ["selected"], "checks": [{"name": "selected"}]}, 0, "passed"),
])
def test_worker_exit_requires_matching_authoritative_evidence(report, exit_status, proof):
    change = decide({"phase": "running"}, "worker_finished", report=report, exit_status=exit_status,
                    cleanup={"verified": True, "forced": False})
    assert change["proof"] == proof


def test_viewer_reconciles_stale_liveness_without_rewriting_saved_result():
    state = {"phase": "running", "proof": "pending"}
    observed = project(state, owner_alive=False, worker_alive=True)
    assert observed["phase"] == "incomplete" and observed["outcome"] == "failed"
    assert observed["saved_phase"] == "running" and observed["observed"]["worker_alive"]
    assert state == {"phase": "running", "proof": "pending"}
    final = project({"phase": "finished", "proof": "passed", "outcome": "passed"}, owner_alive=False, worker_alive=False)
    assert final["outcome"] == "passed"


def test_stop_is_idempotent_and_cannot_relabel_a_finished_proof():
    state = {"phase": "running"}
    state.update(decide(state, "stop", reason="operator_stop"))
    assert decide(state, "stop", reason="run_deadline")["reason"] == "operator_stop"
    assert decide({"phase": "finished"}, "stop", reason="operator_stop") == {}


def test_invalid_native_input_snapshot_does_not_suppress_independent_static_plan(tmp_path):
    source, directory, plan = frozen_plan(tmp_path, "{", prepare=prepare_run)
    assert plan["selected_check_ids"] == ["ruff", "syntax", "architecture"]
    assert plan["input_snapshot_errors"]["config"] == "JSONDecodeError"
    assert (directory / "inputs" / "config").read_bytes() == source.read_bytes()
    assert plan["config"] == str(directory / "inputs" / "config")


def test_relocated_native_snapshot_keeps_original_base_and_does_not_follow_later_edits(tmp_path):
    source, directory, plan = frozen_plan(tmp_path, native_configuration(), prepare=prepare_run)
    source.write_text("changed after the run was prepared")
    config = read_json(plan["config"])
    assert config["sessions"]["codex"]["options"]["cwd"] == str(source.parent / "workspace")
    assert config["store"]["options"]["path"] == str(directory / "derived" / "state.sqlite3")
    assert (directory / "inputs" / "config").read_text() == native_configuration()


def test_watch_shows_check_transitions_while_the_run_phase_stays_running(monkeypatch, capsys):
    from scripts.testkit import operator_cli

    states = iter([
        {"run_id": "observed", "phase": "running", "proof": "pending", "progress": {
            "last_event": {"event": "started", "check": "syntax"}}},
        {"run_id": "observed", "phase": "running", "proof": "pending", "progress": {
            "last_event": {"event": "completed", "check": "syntax", "status": "failed"}}},
        {"run_id": "observed", "phase": "running", "proof": "pending", "progress": {
            "last_event": {"event": "qualified", "check": "tests", "result": {"status": "passed"}}}},
        {"run_id": "observed", "phase": "finished", "proof": "failed"},
    ])
    monkeypatch.setattr(operator_cli.managed, "status", lambda directory: next(states))
    monkeypatch.setattr(operator_cli.time, "sleep", lambda seconds: None)
    assert watch("explicit-run-directory")["phase"] == "finished"
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 4
    assert "syntax started" in lines[0]
    assert "syntax completed failed" in lines[1]
    assert "tests qualified passed" in lines[2]
