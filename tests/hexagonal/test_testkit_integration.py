"""Faults at composed runner boundaries must not strand owned resources."""

import io
import json
import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.testkit import composition as testkit
from scripts.testkit.adapters.command import SubprocessRunner
from scripts.testkit.model import Check, Timeout
from scripts.verification import lifecycle, multi_harness
from scripts.verification.capture import digest
from xorchestrate import composition


@pytest.mark.parametrize("outer_grace", [0.5, 10.0], ids=["small-budget", "default-budget"])
@pytest.mark.skipif(shutil.which("tmux") is None, reason="nested cleanup witness requires real tmux")
def test_nested_acceptance_finalizer_finishes_before_outer_command_escalation(tmp_path, outer_grace):
    planned = testkit.plan(directory=tmp_path, suite="acceptance", mode="protocol-fixture",
                           shutdown_grace=outer_grace)[0]
    inner_grace = float(planned.argv[planned.argv.index("--shutdown-grace") + 1])
    cleanup_budget = float(planned.argv[planned.argv.index("--cleanup-budget") + 1])
    assert 0 < inner_grace < cleanup_budget < outer_grace
    directory = tmp_path / "inner"
    scenario = directory / "scenario.json"
    program = (
        "import signal,time,sys; from pathlib import Path\n"
        f"def finish(*_): time.sleep(.05); Path({str(scenario)!r}).write_text('{{\"status\":\"failed\"}}'); sys.exit(0)\n"
        "signal.signal(signal.SIGTERM,finish)\n"
        "while True: time.sleep(.01)\n"
    )
    wrapper = (
        "import sys\n"
        "from scripts.testkit.adapters.tmux import supervise\n"
        f"supervise((sys.executable,'-c',{program!r}),{str(directory)!r},timeout=20,"
        f"grace={inner_grace!r},cleanup_budget={cleanup_budget!r})\n"
    )
    with pytest.raises(Timeout) as caught:
        SubprocessRunner(shutdown_target="owner", shutdown_grace=outer_grace).run(
            (sys.executable, "-c", wrapper), cwd=Path(__file__).resolve().parents[2],
            env=dict(os.environ), timeout=0.5)
    report = json.loads((directory / "report.json").read_text())
    assert scenario.exists(), "outer command killed the inner scenario's finalizer"
    assert caught.value.shutdown["forced"] is False
    assert report["cleanup"]["verified"] is True
    assert report["cleanup"]["budget_seconds"] == cleanup_budget


@pytest.mark.parametrize("flag,value", [("--shutdown-grace", "0"), ("--shutdown-grace", "nan"),
                                        ("--cleanup-budget", "-1"), ("--cleanup-budget", "inf")])
def test_acceptance_rejects_invalid_nested_budget_before_allocating_run(tmp_path, flag, value):
    from scripts.acceptance import __main__ as acceptance_cli

    output = tmp_path / "must-not-exist"
    with pytest.raises(SystemExit) as caught:
        acceptance_cli.main(["--output", str(output), flag, value])
    assert caught.value.code == 2
    assert not output.exists()


def test_multiple_acceptance_drivers_expand_without_repeating_static_checks(tmp_path):
    checks = testkit.plan(directory=tmp_path, suite="all", mode="native", config=tmp_path / "native.json",
                          drivers=("first", "second/name"))
    names = [check.name for check in checks]
    assert len(names) == len(set(names))
    assert names.count("ruff") == names.count("syntax") == names.count("architecture") == 1
    acceptance = [check for check in checks if check.name.startswith("acceptance:")]
    assert [check.argv[check.argv.index("--driver") + 1] for check in acceptance] == ["first", "second/name"]
    assert all("/" not in check.name for check in acceptance)
    assert len({check.argv[check.argv.index("--output") + 1] for check in acceptance}) == 2
    assert names.count("multi-harness") == 1


@pytest.mark.parametrize("mode,check_name,forwarded", [
    ("protocol-fixture", "tests", False), ("native", "tests", False),
    ("native", "native-calibration", False), ("protocol-fixture", "multi-harness", False),
    ("protocol-fixture", "acceptance", False), ("native", "multi-harness", True),
    ("native", "acceptance:peer", True),
])
def test_managed_native_presentation_context_is_scoped_to_native_live_wrappers(
        tmp_path, monkeypatch, mode, check_name, forwarded):
    root = tmp_path / "candidate"
    root.mkdir()
    managed = tmp_path / "actual-managed-run"
    managed.mkdir()
    monkeypatch.setenv("XORCHESTRATE_PROOF_DIRECTORY", str(managed))
    output = managed / "native-inputs" if forwarded else tmp_path / "independent-test-inputs"
    observed = tmp_path / "observed.json"
    program = (
        "import json,os,sys; from pathlib import Path; "
        f"sys.path.insert(0,{str(Path(__file__).resolve().parents[2])!r}); "
        "from scripts.testkit.adapters.configuration import isolated_config; "
        f"config=isolated_config({{'sessions':{{'peer':{{'adapter':'claude','options':{{}}}}}}}},{str(root)!r},{str(output)!r}); "
        f"Path({str(observed)!r}).write_text(json.dumps({{'marker':os.getenv('XORCHESTRATE_PROOF_DIRECTORY'),"
        "'terminal':config['sessions']['peer']['options'].get('terminal','inherit')}))"
    )
    selected = Check(check_name, (sys.executable, "-c", program), 3)
    monkeypatch.setattr(testkit, "plan", lambda **kwargs: (selected,))
    monkeypatch.setattr(testkit, "preflight", lambda **kwargs: {"status": "passed", "checks": []})
    monkeypatch.setattr(testkit, "readiness", lambda *args, **kwargs: {"status": "passed", "reason": ""})
    brief = tmp_path / "owner.md"
    brief.write_text("Independent fixture intent\n")
    report = testkit.run(output=tmp_path / "reports", root=root, mode=mode, brief=brief)
    assert report["status"] == "passed", report
    actual = json.loads(observed.read_text())
    assert actual["marker"] == (str(managed) if forwarded else "")
    assert isinstance(actual["terminal"], dict) is forwarded
    assert os.environ["XORCHESTRATE_PROOF_DIRECTORY"] == str(managed)
    receipt = json.loads(Path(report["checks"][0]["command_record"]).read_text())
    if forwarded:
        assert "XORCHESTRATE_PROOF_DIRECTORY" not in receipt["environment"]
    else:
        assert receipt["environment"]["XORCHESTRATE_PROOF_DIRECTORY"] == ""


@pytest.mark.parametrize("cancel_during_timeout", [False, True])
def test_real_composed_timeout_and_run_stop_have_different_scheduling_effects(tmp_path, monkeypatch,
                                                                          cancel_during_timeout):
    root = tmp_path / "candidate"
    root.mkdir()
    stopped = tmp_path / "stop-request"
    finalizer = (
        "import signal,time,sys; from pathlib import Path\n"
        f"def finish(*_): Path({str(stopped)!r}).touch(); time.sleep(.05); sys.exit(0)\n"
        "signal.signal(signal.SIGTERM,finish)\n"
        "print('active',flush=True)\n"
        "while True: time.sleep(.01)\n"
    )
    checks = (Check("before", (sys.executable, "-c", "print('before')"), 2),
              Check("slow", (sys.executable, "-c", finalizer), 0.3),
              Check("after", (sys.executable, "-c", "print('after')"), 2))
    monkeypatch.setattr(testkit, "plan", lambda **kwargs: checks)
    monkeypatch.setattr(testkit, "preflight", lambda **kwargs: {"status": "passed", "checks": []})
    events = []
    directory = tmp_path / "run" / "checks"
    report = testkit.run(output=tmp_path / "output", directory=directory, root=root, shutdown_grace=1,
                         cancellation=lambda: "operator_stop" if cancel_during_timeout and stopped.exists() else None,
                         result_sink=events.append)
    rows = {row["name"]: row for row in report["checks"]}
    assert report["status"] == "failed"
    assert rows["before"]["status"] == "passed"
    assert rows["slow"]["status"] == "timeout"
    assert rows["slow"]["shutdown"]["scope"] == "check"
    assert rows["after"]["status"] == ("not_run" if cancel_during_timeout else "passed")
    assert (directory / "commands" / "after").exists() is not cancel_during_timeout
    assert json.loads((directory / "report.json").read_text()) == report
    assert [event["check"] for event in events if event["event"] == "completed"] == ["before", "slow", "after"]
    assert [event["revision"] for event in events] == list(range(1, len(events) + 1))


def test_late_publication_failure_preserves_earlier_completed_evidence(tmp_path, monkeypatch):
    root = tmp_path / "candidate"
    root.mkdir()
    checks = tuple(Check(name, (sys.executable, "-c", "print('finished')"), 2) for name in ("before", "later"))
    monkeypatch.setattr(testkit, "plan", lambda **kwargs: checks)
    monkeypatch.setattr(testkit, "preflight", lambda **kwargs: {"status": "passed", "checks": []})

    def failed_sink(event):
        if event["event"] == "started" and event["check"] == "later":
            raise OSError("external sink unavailable")

    report = testkit.run(output=tmp_path / "output", root=root, result_sink=failed_sink)
    saved = json.loads((Path(report["directory"]) / "report.json").read_text())
    assert saved["status"] == "failed"
    assert saved["checks"][0]["name"] == "before" and saved["checks"][0]["status"] == "passed"
    assert saved["checks"][0]["validation"] == "pending"
    assert saved["error"]["diagnostic"] == "external sink unavailable"


def test_suite_setup_and_provider_failure_still_close_runtime(tmp_path, monkeypatch):
    calls = []

    def provider_close():
        calls.append("provider")
        raise RuntimeError("provider-close-fault")

    runtime = SimpleNamespace(
        application=SimpleNamespace(providers={"verification": SimpleNamespace(close=provider_close)}),
        close=lambda: calls.append("runtime"),
    )

    def fail_setup(*args, **kwargs):
        raise RuntimeError("suite-setup-fault")

    monkeypatch.setattr(composition, "build", lambda *args: runtime)
    monkeypatch.setattr(multi_harness, "ComposedSuite", fail_setup)
    monkeypatch.setattr(multi_harness, "candidate_identity", lambda root: {"content_sha256": "candidate"})
    monkeypatch.setattr(multi_harness, "verify_candidate", lambda *args: {"passed": True})
    brief = tmp_path / "brief.md"
    brief.write_text("Exercise runner cleanup, including partial setup.\n")
    criteria = tmp_path / "criteria.json"
    criteria.write_text(json.dumps({"owner_basis_sha256": digest(brief.read_bytes())}))
    output = tmp_path / "run"

    assert multi_harness.main([
        "--mode", "fixture", "--brief", str(brief), "--criteria", str(criteria), "--out", str(output),
    ]) == 1

    assert calls == ["provider", "runtime"]
    cleanup = json.loads((output / "cleanup.json").read_text())
    assert cleanup["status"] == "failed"
    assert [error["name"] for error in cleanup["errors"]] == ["verification_holds"]
    assert cleanup["errors"][0]["diagnostic"] == "provider-close-fault"
    assert json.loads((output / "setup-failure.json").read_text())["diagnostic"] == "suite-setup-fault"


@pytest.mark.parametrize("faults", [{"runtime"}, {"records"}, {"runtime", "records"}])
def test_lifecycle_cleanup_drains_host_and_streams_after_capture_or_runtime_failure(tmp_path, monkeypatch, faults):
    calls = []
    outer_application, inner_application = object(), object()
    session = {"id": "session", "backend": {"native_id": "native", "incarnation": "connection"}}
    suite = SimpleNamespace(application=outer_application, sessions={"codex": {"session_id": "session"}},
                            work_id="work", directory=tmp_path, timeout=1, lifecycle_events=[], executed=[])

    class Process:
        pid = 123
        returncode = None

        def __init__(self):
            self.stdout = io.StringIO('{"ready":true}\n')

        def poll(self):
            return self.returncode

        def terminate(self):
            calls.append("host-terminate")
            self.returncode = 0

        def wait(self, timeout):
            calls.append("host-wait")
            return self.returncode

    process = Process()
    streams = []

    def launch(*args, **kwargs):
        streams.append(kwargs["stderr"])
        return process

    def records(application):
        if application is inner_application:
            calls.append("capture")
            if "records" in faults:
                raise RuntimeError("capture-fault")
        return {"session": [session]}

    def call(application, action, **arguments):
        if application is inner_application:
            raise RuntimeError("scenario-fault")
        return {"session": session, "operation_id": "operation"}

    def close_runtime():
        calls.append("runtime-close")
        if "runtime" in faults:
            raise RuntimeError("runtime-close-fault")

    monkeypatch.setattr(lifecycle, "PEERS", ("codex",))
    monkeypatch.setattr(lifecycle, "_records", records)
    monkeypatch.setattr(lifecycle, "_call", call)
    monkeypatch.setattr(lifecycle, "_probe", lambda *args, **kwargs: {})
    monkeypatch.setattr(lifecycle, "_stale", lambda *args: {})
    monkeypatch.setattr(lifecycle, "build", lambda *args: SimpleNamespace(application=inner_application, close=close_runtime))
    monkeypatch.setattr(lifecycle.subprocess, "Popen", launch)
    monkeypatch.setattr(lifecycle.select, "select", lambda readers, *args: (readers, [], []))

    with pytest.raises(RuntimeError, match="scenario-fault") as caught:
        lifecycle.run_lifecycle(suite, {}, tmp_path)

    assert caught.value.args == ("scenario-fault",)
    assert calls == ["capture", "runtime-close", "host-terminate", "host-wait"]
    assert process.stdout.closed and streams[0].closed
    cleanup = json.loads((tmp_path / "lifecycle/codex/cleanup.json").read_text())
    assert cleanup["status"] == "failed"
    assert cleanup["host_returncode"] == 0 and cleanup["endpoint_removed"]
    expected = {"lifecycle_" + ("records" if fault == "records" else "runtime") for fault in faults}
    assert {error["name"] for error in cleanup["errors"]} == expected
    assert any("external_host_cleanup_failed" in note for note in caught.value.__notes__)
