"""Real public-interface managed success, fresh repeat and cancellation workflows."""

import json
import shutil

import pytest

from scripts.testkit.adapters.managed_store import owner_alive
from tests.hexagonal.contracts.managed_workflow import CONTROL_SCOPES, REPEAT_PROPERTIES, TERMINAL_PHASES
from tests.hexagonal.support.managed_workflow import (
    PublicWorkflow,
    await_condition,
    foreign_terminal,
    isolated_candidate,
)

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="Real tmux is unavailable")


def test_public_detached_success_repeat_close_and_ownerless_recollection(tmp_path):
    candidate = isolated_candidate(tmp_path / "candidate with spaces")
    workflow = PublicWorkflow(candidate, tmp_path / "operator with spaces")
    try:
        with foreign_terminal() as foreign_alive:
            first, launch = workflow.start()
            assert launch["status"] == "launched"
            # The start CLI has already exited; the finalization owner remains.
            assert owner_alive(workflow.state(first)["owner"])
            waited, first_result = workflow.command("wait", first)
            assert waited.returncode == 0, waited.stdout + waited.stderr
            assert first_result["phase"] == "finished"
            assert first_result["outcome"] == first_result["proof"] == "passed"
            assert first_result["cleanup"]["verified"] and not first_result["cleanup"]["forced"]
            assert first_result["observed"]["supervisor_alive"]
            assert not first_result["observed"]["worker_alive"]
            saved_plan = (first / "plan.json").read_bytes()
            saved_report = (first / "checks" / "report.json").read_bytes()
            first_input = json.loads(saved_plan)["brief"]

            second, _ = workflow.start(source=first)
            waited, second_result = workflow.command("wait", second)
            assert waited.returncode == 0, waited.stdout + waited.stderr
            assert second_result["outcome"] == "passed"
            assert first != second
            assert (first / "plan.json").read_bytes() == saved_plan
            assert (first / "checks" / "report.json").read_bytes() == saved_report
            second_plan = json.loads((second / "plan.json").read_text())
            first_plan = json.loads(saved_plan)
            assert second_plan["parent_run_id"] == first.name
            assert all(first_plan[name] == second_plan[name] for name in REPEAT_PROPERTIES)
            assert first_input != second_plan["brief"]
            first_native = json.loads((first / "checks" / "multi-harness" / "run.json").read_text())
            second_native = json.loads((second / "checks" / "multi-harness" / "run.json").read_text())
            assert first_native["run_id"] != second_native["run_id"]
            assert first_native["sessions"] != second_native["sessions"]

            collected, active_export = workflow.command("collect", first)
            assert collected.returncode == 0, collected.stdout + collected.stderr
            closed, _ = workflow.command("close", first)
            assert closed.returncode == 0
            await_condition(lambda: not owner_alive(workflow.state(first)["owner"]))
            stopped, unchanged = workflow.command("stop", first)
            assert stopped.returncode == 0 and unchanged["status"] == "already_finished"
            assert foreign_alive()
            collected, ownerless_export = workflow.command("collect", first)
            assert collected.returncode == 0, collected.stdout + collected.stderr
            assert ownerless_export["archive"] != active_export["archive"]
            assert (first / "checks" / "report.json").read_bytes() == saved_report
            assert foreign_alive()
    finally:
        workflow.close()


@pytest.mark.parametrize("scope", CONTROL_SCOPES)
def test_public_active_close_refuses_and_run_stop_or_deadline_finishes_unsuccessfully(tmp_path, scope):
    candidate = isolated_candidate(tmp_path / "candidate")
    workflow = PublicWorkflow(candidate, tmp_path / "operator")
    try:
        overrides = {"run_timeout": 1} if scope == "run-deadline" else {}
        directory, _ = workflow.start(suite="acceptance", mode="protocol-fixture", timeout=30, **overrides)
        workflow.freeze(directory)
        refused, response = workflow.command("close", directory)
        assert refused.returncode != 0
        assert response["status"] == "setup_error"
        assert "finalize" in response["diagnostic"]
        if scope == "stop":
            first, requested = workflow.command("stop", directory)
            second, repeated = workflow.command("stop", directory)
            assert first.returncode == second.returncode == 0
            assert requested["status"] == repeated["status"] == "requested"
            assert requested["request_id"] != repeated["request_id"]
            await_condition(lambda: workflow.state(directory).get("reason") == "operator_stop")
        else:
            await_condition(lambda: workflow.state(directory).get("reason") == "run_deadline")
        workflow.resume()
        waited, result = workflow.command("wait", directory, timeout=45)
        assert waited.returncode != 0
        assert result["phase"] in TERMINAL_PHASES
        assert result["outcome"] == "failed"
        assert result["reason"] == ("operator_stop" if scope == "stop" else "run_deadline")
        assert (directory / "checks" / "progress.json").is_file()
        assert (directory / "checks" / "report.json").is_file()
        assert result["cleanup"]["verified"]
        assert not result["observed"]["worker_alive"]
        collected, receipt = workflow.command("collect", directory)
        assert collected.returncode == 0, collected.stdout + collected.stderr
        assert receipt["archive"]
    finally:
        workflow.close()
