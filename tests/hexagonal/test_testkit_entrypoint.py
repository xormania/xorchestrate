"""Reusable local/CI selection keeps modes explicit and retains command evidence."""

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.testkit import __main__ as cli
from scripts.testkit import composition
from scripts.testkit.adapters.evidence import RecordingRunner, digest
from tests.hexagonal.support.static_checks import static_subject, write_source


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
    assert [check.name for check in together] == ["ruff", "syntax", "architecture", "tests", "native-calibration",
                                                "work-calibration", "state-calibration", "acceptance", "multi-harness"]
    assert len({check.name for check in together}) == len(together)
    acceptance, multi = together[-2:]
    assert acceptance.argv[:3] == (sys.executable, "-m", "scripts.acceptance")
    assert multi.argv[:3] == (sys.executable, "-m", "scripts.verification.multi_harness")
    assert acceptance.argv[acceptance.argv.index("--profile") + 1] == ("native" if mode == "native" else "fixture")
    assert multi.argv[multi.argv.index("--mode") + 1] == mode
    for check in (acceptance, multi):
        assert ("--config" in check.argv) == (mode == "native")
    # Declaring a plan does not read the brief or prepare a selected live check.
    assert not (tmp_path / "owner-brief.md").exists()
    assert not (tmp_path / "criteria.json").exists()


def test_checks_plan_selects_declared_family_and_retains_inventory(tmp_path):
    checks = composition.plan(directory=tmp_path, suite="checks", mode="protocol-fixture",
                              families=("state",), check_timeout=37)
    assert [check.name for check in checks] == ["ruff", "syntax", "architecture", "tests"]
    tests = next(check for check in checks if check.name == "tests")
    assert tests.argv[-2:] == ("--test-family", "state")
    assert "--test-inventory" in tests.argv
    assert all(check.timeout == 37 for check in checks)
    assert checks[:3] == composition.plan(directory=tmp_path, suite="static", mode="protocol-fixture",
                                          check_timeout=37)
    assert (tests,) == composition.plan(directory=tmp_path, suite="tests", mode="protocol-fixture",
                                        families=("state",), check_timeout=37)


def test_unknown_family_is_rejected_before_any_command(tmp_path, monkeypatch):
    def unexpected_launch(*args, **kwargs):
        pytest.fail("unclassified family launched a command")

    monkeypatch.setattr(RecordingRunner, "run", unexpected_launch)
    report = composition.run(output=tmp_path, suite="checks", families=("missing-family",))
    assert report["status"] == "setup_error" and report["checks"] == []
    assert "Unknown test families" in report["error"]["diagnostic"]


@pytest.mark.parametrize("fault", ["missing", "malformed", "unreadable"])
def test_unavailable_catalog_with_family_blocks_tests_without_losing_static_outcomes(tmp_path, monkeypatch, fault):
    root = static_subject(tmp_path / "candidate")
    write_source(root, "tests/conftest.py", "")
    write_source(root, "tests/hexagonal/test_subject.py", "VALUE = 1\n")
    catalog = root / "contracts/testing.json"
    if fault == "malformed":
        catalog.write_text("{")
    elif fault == "unreadable":
        catalog.write_text(json.dumps({
            "schema_version": 1, "id": "isolated-test-catalog", "roots": ["tests/hexagonal"],
            "families": {"framework": {"guarantee": "Isolated prerequisite subject",
                                       "modules": {"tests/hexagonal/test_subject.py": "contract"}}},
        }))
        original_read = Path.read_text

        def unreadable_catalog(path, *args, **kwargs):
            if path == catalog:
                raise PermissionError("catalog-read-failure")
            return original_read(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", unreadable_catalog)
    original_plan = composition.plan

    def selected_plan(**options):
        checks = {check.name: check for check in original_plan(**options)}
        return (checks["tests"], checks["syntax"])

    monkeypatch.setattr(composition, "plan", selected_plan)
    options = dict(root=root, suite="checks", mode="protocol-fixture", families=("framework",))
    doctor = composition.preflight(**options)
    assert doctor["status"] == "setup_error"
    assert [(row["name"], row["status"]) for row in doctor["checks"]] == [
        ("tests", "setup_error"), ("syntax", "passed"),
    ]
    report = composition.run(output=tmp_path / "evidence", **options)
    assert report["status"] == "failed"
    assert report["preflight"] == doctor
    blocked, independent = report["checks"]
    assert blocked["name"] == "tests" and blocked["status"] == "setup_error"
    assert blocked["launched"] is False and blocked["exit_status"] is None
    assert blocked["command_record"] is None and blocked["command_records"] == []
    assert independent["name"] == "syntax" and independent["status"] == "passed"
    assert independent["launched"] and independent["exit_status"] == 0
    receipt = Path(independent["command_record"])
    assert json.loads((receipt.parent / "stdout.log").read_text())["status"] == "passed"


def test_static_doctor_needs_no_test_discovery_or_native_setup(monkeypatch):
    from scripts.testkit.adapters import pytest_plugin

    def unrelated_dependency(*args, **kwargs):
        pytest.fail("static doctor attempted unrelated discovery or a process launch")

    monkeypatch.setattr(pytest_plugin, "load_catalog", unrelated_dependency)
    monkeypatch.setattr(subprocess, "Popen", unrelated_dependency)
    report = composition.preflight(suite="static", mode="native")
    assert report["status"] == "passed"
    assert [row["name"] for row in report["checks"]] == ["ruff", "syntax", "architecture"]


@pytest.mark.parametrize("fault", ["missing-native", "missing-brief", "preparation"])
def test_blocked_live_check_preserves_independent_captured_result_and_own_receipt(tmp_path, monkeypatch, fault):
    original_plan = composition.plan
    marker = "independent-captured-result"

    def selected_plan(**options):
        checks = {check.name: check for check in original_plan(**options)}
        independent = replace(checks["tests"], argv=(sys.executable, "-c", "print('" + marker + "')"))
        return (checks["multi-harness"], independent)

    monkeypatch.setattr(composition, "plan", selected_plan)
    brief = tmp_path / "owner-brief.md"
    if fault == "preparation":
        brief.write_text("Independent owner intent\n")
        original_read = Path.read_bytes

        def unreadable_preparation(path):
            if path == brief:
                raise OSError("brief-read-failure")
            return original_read(path)

        monkeypatch.setattr(Path, "read_bytes", unreadable_preparation)
    report = composition.run(output=tmp_path / "runs", suite="all",
                             mode="native" if fault == "missing-native" else "protocol-fixture",
                             brief=None if fault == "missing-native" else brief)
    assert report["status"] == "failed"
    assert [row["name"] for row in report["checks"]] == ["multi-harness", "tests"]
    blocked, independent = report["checks"]
    assert blocked["status"] == "setup_error" and blocked["launched"] is False
    assert blocked["exit_status"] is None and blocked["command_record"] is None
    assert blocked["command_records"] == []
    if fault == "preparation":
        assert "brief-read-failure" in blocked["reason"]
    assert independent["status"] == "passed" and independent["launched"] is True
    receipt = Path(independent["command_record"])
    command = json.loads(receipt.read_text())
    assert marker in command["command"][-1]
    assert command["terminal"] == "capture"
    assert (receipt.parent / "stdout.log").read_text() == marker + "\n"


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
    assert report["status"] in {"setup_error", "failed"}
    assert report["mode"] == "native"
    assert len(report["checks"]) == 1
    blocked = report["checks"][0]
    assert blocked["name"] == "multi-harness" and blocked["status"] == "setup_error"
    assert blocked["launched"] is False and blocked["exit_status"] is None
    assert blocked["command_record"] is None and blocked["command_records"] == []
    directory = Path(report["directory"])
    assert not list(directory.rglob("command.json"))
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
    readiness = [item for check in report["checks"] for item in check["readiness"]]
    assert {check["name"] for check in readiness} >= {
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
