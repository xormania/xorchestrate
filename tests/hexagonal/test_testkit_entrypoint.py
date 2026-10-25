"""Reusable local/CI selection keeps modes explicit and retains command evidence."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.testkit import __main__ as cli
from scripts.testkit import composition
from scripts.testkit.adapters.evidence import RecordingRunner, digest


def native_configuration(tmp_path):
    path = tmp_path / "native.json"
    path.write_text(json.dumps({"sessions": {
        peer: {"adapter": peer, "options": {"command": [sys.executable], "cwd": str(tmp_path),
                                           "application_tools": True}}
        for peer in ("codex", "claude", "grok")
    }}))
    return path


@pytest.mark.parametrize("mode", composition.MODES)
def test_all_plan_is_the_same_named_checks_as_individual_suites(tmp_path, mode):
    config = native_configuration(tmp_path) if mode == "native" else None
    options = dict(directory=tmp_path, mode=mode, config=config,
                   driver="codex" if mode == "native" else None, timeout=7)
    together = composition.plan(suite="all", **options)
    separately = sum((composition.plan(suite=suite, **options) for suite in composition.COMPONENT_SUITES), ())
    assert together == separately
    assert [check.name for check in together] == ["tests", "architecture", "native-calibration", "work-calibration",
                                                "state-calibration", "acceptance", "multi-harness"]
    acceptance, multi = together[-2:]
    assert acceptance.argv[:3] == (sys.executable, "-m", "scripts.acceptance")
    assert multi.argv[:3] == (sys.executable, "-m", "scripts.verification.multi_harness")
    assert acceptance.argv[acceptance.argv.index("--profile") + 1] == ("native" if mode == "native" else "fixture")
    assert multi.argv[multi.argv.index("--mode") + 1] == mode
    for check in (acceptance, multi):
        assert ("--config" in check.argv) == (mode == "native")
    frozen = tmp_path / "owner-brief.md"
    criteria = json.loads((tmp_path / "criteria.json").read_text())
    assert criteria["owner_basis_sha256"] == digest(frozen.read_bytes())


def test_checks_plan_selects_declared_family_and_retains_inventory(tmp_path):
    checks = composition.plan(directory=tmp_path, suite="checks", mode="protocol-fixture",
                              families=("state",), check_timeout=37)
    assert len(checks) == 2
    assert checks[0].argv[-2:] == ("--test-family", "state")
    assert "--test-inventory" in checks[0].argv
    assert checks[0].timeout == 37
    assert checks[1].name == "architecture"


def test_unknown_family_is_rejected_before_any_command(tmp_path, monkeypatch):
    def unexpected_launch(*args, **kwargs):
        pytest.fail("unclassified family launched a command")

    monkeypatch.setattr(RecordingRunner, "run", unexpected_launch)
    report = composition.run(output=tmp_path, suite="checks", families=("missing-family",))
    assert report["status"] == "setup_error" and report["checks"] == []
    assert "Unknown test families" in report["error"]["diagnostic"]


@pytest.mark.parametrize("fault", ["no_config", "tools_disabled", "missing_command", "string_command"])
def test_invalid_native_inputs_never_launch_or_fall_back(tmp_path, monkeypatch, fault):
    config = native_configuration(tmp_path)
    if fault != "no_config":
        data = json.loads(config.read_text())
        options = data["sessions"]["codex"]["options"]
        if fault == "tools_disabled":
            options["application_tools"] = False
        elif fault == "missing_command":
            del options["command"]
        else:
            options["command"] = sys.executable
        config.write_text(json.dumps(data))

    def unexpected_launch(*args, **kwargs):
        pytest.fail("invalid native inputs launched a command")

    monkeypatch.setattr(RecordingRunner, "run", unexpected_launch)
    report = composition.run(output=tmp_path / "runs", suite="multi-harness", mode="native",
                             config=None if fault == "no_config" else config)
    assert report["status"] == "setup_error"
    assert report["mode"] == "native" and report["checks"] == []
    directory = Path(report["directory"])
    assert not (directory / "commands").exists()
    assert json.loads((directory / "report.json").read_text()) == report


def test_native_doctor_checks_inputs_without_authentication_or_process_launch(tmp_path, monkeypatch, capsys):
    config = native_configuration(tmp_path)
    monkeypatch.setattr(composition.shutil, "which", lambda name: sys.executable)

    def unexpected_launch(*args, **kwargs):
        pytest.fail("doctor launched a process or a test run")

    monkeypatch.setattr(subprocess, "Popen", unexpected_launch)
    monkeypatch.setattr(cli, "run", unexpected_launch)
    assert cli.main(["doctor", "--mode", "native", "--config", str(config), "--driver", "codex"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "passed"
    assert report["authentication"] == "verified by live callbacks only"
    assert {check["name"] for check in report["checks"]} >= {
        "tmux", "codex:executable", "claude:executable", "grok:executable",
    }


@pytest.mark.parametrize("destination", ["same", "nested", "symlink"])
def test_run_rejects_output_inside_candidate_before_allocating(tmp_path, monkeypatch, destination):
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    output = candidate if destination == "same" else candidate / "runs"
    if destination == "symlink":
        output = tmp_path / "alias"
        output.symlink_to(candidate, target_is_directory=True)

    def unexpected_allocation(*args):
        pytest.fail("output was allocated inside measured candidate")

    monkeypatch.setattr(composition, "allocate_run", unexpected_allocation)
    with pytest.raises(ValueError, match="outside the measured candidate"):
        composition.run(root=candidate, output=output, suite="multi-harness", mode="fixture")


def test_recording_runner_retains_raw_failure_and_missing_executable(tmp_path, monkeypatch):
    monkeypatch.setenv("TESTKIT_PRIVATE_SECRET", "not-for-command-records")
    runner = RecordingRunner(tmp_path / "commands")
    failed = runner.run((sys.executable, "-c", "import sys; print('partial'); print('failure detail', file=sys.stderr); sys.exit(7)"),
                        cwd=str(tmp_path), env={}, timeout=5)
    missing = runner.run((str(tmp_path / "absent-command"),), cwd=str(tmp_path), env={}, timeout=5)
    assert failed.exit == 7 and failed.stdout == "partial\n" and failed.stderr == "failure detail\n"
    assert missing.exit != 0
    for record in runner.records:
        directory = Path(record["directory"])
        stored = json.loads((directory / "command.json").read_text())
        assert stored["environment"] == {}
        assert stored["stdout_sha256"] == digest((directory / "stdout.log").read_bytes())
        assert stored["stderr_sha256"] == digest((directory / "stderr.log").read_bytes())
    assert [record["status"] for record in runner.records] == ["failed", "setup_error"]
    assert runner.records[0]["exit_status"] == 7
    assert runner.records[1]["exit_status"] is None
