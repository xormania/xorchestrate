"""Real public-interface managed success, fresh repeat and cancellation workflows."""

import json

import pytest

from scripts.testkit.adapters.managed_store import owner_alive
from tests.hexagonal.contracts.live_diagnostics import PROVIDER_REASON, surfaced_reason
from tests.hexagonal.contracts.managed_workflow import CONTROL_SCOPES, REPEAT_PROPERTIES, TERMINAL_PHASES
from tests.hexagonal.support.live_diagnostics import (
    archive_names,
    archive_text,
    native_configuration,
    peer_session,
)
from tests.hexagonal.support.managed_workflow import (
    PublicWorkflow,
    await_condition,
    foreign_terminal,
    isolated_candidate,
)
from tests.hexagonal.support.terminal_host import requires_host

pytestmark = requires_host()


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


def test_public_failure_reason_reaches_the_operator_but_never_the_saved_state_or_export(tmp_path):
    candidate = isolated_candidate(tmp_path / "candidate")
    workflow = PublicWorkflow(candidate, tmp_path / "operator")
    config = native_configuration(tmp_path / "operator" / "native.json",
                                  {"refused": peer_session("--refuse-start", root=candidate)}, capture=True)
    label = "story:native-callback:refused"
    words = ("configured_native_application_callback", "start_session", PROVIDER_REASON)
    try:
        directory, _ = workflow.start(suite="stories", mode="native", story="native-callback", config=config,
                                      driver="refused", timeout=30)
        waited, result = workflow.command("wait", directory)
        assert waited.returncode != 0
        assert result["outcome"] == "failed"
        surfaced_reason(result.get("failures"), check=label, words=words)
        shown, current = workflow.command("status", directory)
        assert shown.returncode == 0, shown.stdout + shown.stderr
        surfaced_reason(current.get("failures"), check=label, words=words)
        summary = [line.removeprefix("Failure: ") for line in (directory / "summary.txt").read_text().splitlines()
                   if line.startswith("Failure: ")]
        surfaced_reason(summary, check=label, words=words)
        collected, receipt = workflow.command("collect", directory)
        assert collected.returncode == 0, collected.stdout + collected.stderr
        # Shareable records keep codes: the provider's text stays in the private diagnostics. The export's
        # separate telemetry corpus retains original action outcomes by design and is not this claim.
        [scenario] = list((directory / "checks").rglob("scenario.json"))
        [private] = list((directory / "checks").rglob("diagnostics.json"))
        assert PROVIDER_REASON in private.read_text()
        for name, text in (("state.json", (directory / "state.json").read_text()),
                           ("scenario.json", scenario.read_text()),
                           ("diagnostics.zip", archive_text(receipt["archive"]))):
            assert PROVIDER_REASON not in text, f"{name} exposes the provider's reason"
        assert receipt["telemetry"]["status"] != "disabled", "Captured telemetry must be part of the checked export"
        assert "diagnostics.json" not in [name.rsplit("/", 1)[-1] for name in archive_names(receipt["archive"])]
    finally:
        workflow.close()


def test_kept_terminals_outlive_a_killed_supervisor_until_close_adopts_and_ends_them(tmp_path):
    """--keep-after-run: cleanup reports the kept terminals as retained, and a later close ends them."""
    import signal
    from pathlib import Path

    from scripts.testkit.adapters.ownership import Identity, send_signal
    from tests.hexagonal.support.host_processes import arguments_containing

    candidate = isolated_candidate(tmp_path / "candidate")
    workflow = PublicWorkflow(candidate, tmp_path / "operator")
    try:
        refused, _ = workflow.command("run", "--suite", "multi-harness", "--mode", "fixture", "--keep-after-run",
                                      "--output", str(workflow.outside / "runs"))
        assert refused.returncode != 0 and "managed runs" in refused.stdout + refused.stderr, "TESTKIT_KEEP_IS_REFUSED_IN_THE_FOREGROUND"
        started, launch = workflow.command("start", "--detach", "--suite", "multi-harness", "--mode", "fixture",
                                           "--timeout", "5", "--shutdown-grace", "1", "--keep-after-run",
                                           "--output", str(workflow.outside / "runs"))
        assert launch.get("status") == "launched", started.stdout + started.stderr
        directory = Path(launch["directory"])
        workflow.runs.append(directory)
        waited, result = workflow.command("wait", directory)
        assert result["phase"] == "finished", waited.stdout + waited.stderr
        assert result["cleanup"]["verified"] and not result["cleanup"]["forced"], result["cleanup"]
        assert result["cleanup"]["retained"] == {"terminals": ["dashboard", "worker"], "verified": False}
        assert result["terminal"]["keep_after_run"] is True and result["terminal"]["host"]["survives_controller"]

        def dashboards():
            return [pid for pid, command in arguments_containing(str(directory)) if " _dashboard " in f" {command} "]

        assert dashboards(), "the kept dashboard is running"
        owner = workflow.state(directory)["owner"]
        send_signal(Identity(owner["pid"], owner["started"]), signal.SIGKILL)
        await_condition(lambda: not owner_alive(workflow.state(directory)["owner"]))
        assert dashboards(), "kept terminals outlive their supervisor"
        closed, response = workflow.command("close", directory)
        assert closed.returncode == 0, closed.stdout + closed.stderr
        assert response["status"] == "closed" and response["kept_terminals"] == ["dashboard", "worker"], response
        await_condition(lambda: not dashboards())
    finally:
        workflow.close()


def test_close_after_a_supervisor_killed_mid_run_leaves_no_process_and_no_endpoint(tmp_path):
    """An owned host does not end with a supervisor killed outright; close ends what is left and says so."""
    import signal
    from pathlib import Path

    from scripts.testkit.adapters.ownership import Identity, processes_naming, send_signal

    candidate = isolated_candidate(tmp_path / "candidate")
    workflow = PublicWorkflow(candidate, tmp_path / "operator")
    try:
        directory, launch = workflow.start(timeout=60)
        assert launch["status"] == "launched"
        workflow.freeze(directory)  # hold the worker mid-run
        endpoint = Path(json.loads((directory / "plan.json").read_text())["terminal_endpoint"])
        assert endpoint.is_dir() and processes_naming(directory)
        owner = workflow.state(directory)["owner"]
        send_signal(Identity(owner["pid"], owner["started"]), signal.SIGKILL)
        await_condition(lambda: not owner_alive(workflow.state(directory)["owner"]))
        closed, response = workflow.command("close", directory)
        assert closed.returncode == 0, closed.stdout + closed.stderr
        assert response["status"] == "closed" and response["cleanup"]["verified"], response
        assert processes_naming(directory) == [] and not endpoint.exists()
        again, repeated = workflow.command("close", directory)
        assert again.returncode == 0 and repeated["status"] == "already_closed", repeated
    finally:
        workflow.frozen.clear()  # its worker is gone; nothing to resume
        workflow.close()


def test_an_endpoint_named_in_the_plan_is_removed_only_when_it_is_the_runs_own(tmp_path):
    import os
    import tempfile
    from pathlib import Path

    from scripts.testkit.operator_composition import remove_endpoint

    own = Path(tempfile.mkdtemp(prefix="xp-"))
    elsewhere = tmp_path / "xp-not-in-the-temporary-root"
    elsewhere.mkdir(mode=0o700)
    wrong_name = Path(tempfile.mkdtemp(prefix="other-"))
    link = Path(tempfile.gettempdir()) / f"xp-link-{os.getpid()}"
    link.symlink_to(elsewhere)
    try:
        for refused in (elsewhere, wrong_name, link):
            assert remove_endpoint(refused) is False and refused.exists(), "TESTKIT_FOREIGN_ENDPOINTS_ARE_NEVER_REMOVED"
        assert remove_endpoint(own) is True and not own.exists()
        assert remove_endpoint(own) is True, "an endpoint already gone is fine"
    finally:
        link.unlink(missing_ok=True)
        wrong_name.rmdir()


def test_ctrl_c_on_the_supervisor_finishes_unsuccessfully_with_verified_cleanup(tmp_path):
    """Ctrl-C (SIGINT) to a managed run's supervisor stops it like stop and the deadline: same receipt, clean exit."""
    import signal

    from scripts.testkit.adapters.ownership import Identity, processes_naming, send_signal

    candidate = isolated_candidate(tmp_path / "candidate")
    workflow = PublicWorkflow(candidate, tmp_path / "operator")
    try:
        directory, _ = workflow.start(suite="acceptance", mode="protocol-fixture", timeout=30)
        workflow.freeze(directory)
        owner = workflow.state(directory)["owner"]
        send_signal(Identity(owner["pid"], owner["started"]), signal.SIGINT)
        await_condition(lambda: workflow.state(directory).get("reason") == "supervisor_signal")
        workflow.resume()
        waited, result = workflow.command("wait", directory, timeout=45)
        assert waited.returncode != 0
        assert result["phase"] in TERMINAL_PHASES and result["outcome"] == "failed"
        assert result["reason"] == "supervisor_signal"
        assert result["cleanup"]["verified"] and not result["observed"]["worker_alive"], result["cleanup"]
        await_condition(lambda: not owner_alive(workflow.state(directory)["owner"]))
        # The host's processes end just after the supervisor; a real leak never clears.
        await_condition(lambda: processes_naming(directory) == [], timeout=20)
    finally:
        workflow.close()


def test_freeze_stops_the_runs_own_worker_and_never_a_look_alike_outside_the_run(tmp_path):
    """The worker is chosen among the run's own processes; a command line that only looks like it is never stopped."""
    import subprocess
    import sys
    import tempfile
    from pathlib import Path

    from scripts.testkit.adapters.ownership import processes
    from tests.hexagonal.support.host_processes import track

    candidate = isolated_candidate(tmp_path / "candidate")
    workflow = PublicWorkflow(candidate, tmp_path / "operator")
    outside = Path(track(tempfile.mkdtemp(prefix="xo-outside-")))
    look_alike = None
    try:
        directory, launch = workflow.start(timeout=60)
        assert launch["status"] == "launched"
        look_alike = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)", "_worker", str(directory)],
                                      cwd=outside, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL)
        impostor = processes()[look_alike.pid][0]
        frozen = workflow.freeze(directory)
        assert frozen != impostor, "freeze chose a look-alike outside the run"
        state = Path(f"/proc/{look_alike.pid}/stat").read_text().rsplit(") ", 1)[1].split()[0]
        assert state != "T", "a process outside the run was stopped"
    finally:
        workflow.close()
        if look_alike is not None:
            look_alike.kill()
            look_alike.wait(timeout=10)
        outside.rmdir()
