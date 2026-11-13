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
                                                "work-calibration", "state-calibration", "acceptance", "multi-harness"] + [
                                                    c.name for c in composition.plan(suite="stories", **options)]
    assert len({check.name for check in together}) == len(together)
    acceptance, multi = (next(c for c in together if c.name == name) for name in ("acceptance", "multi-harness"))
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
        independent = replace(checks["tests"], name="probe", argv=(sys.executable, "-c", "print('" + marker + "')"))
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
    assert [row["name"] for row in report["checks"]] == ["multi-harness", "probe"]
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
    # An explicit evidence root keeps the verdict independent of where the checkout lives.
    assert cli.main(["doctor", "--mode", "native", "--config", str(config), "--driver", "codex",
                     "--output", str(tmp_path / "runs")]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "passed"
    assert report["authentication"] == "verified by live callbacks only"
    readiness = [item for check in report["checks"] for item in check["readiness"]]
    assert {check["name"] for check in readiness} >= {
        "terminal_host", "codex:executable", "claude:executable", "grok:executable",
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



# Calibration inventory selection: --inventory NAME (repeatable) or --stage --------------------------------

REGISTRY = composition.ROOT / "scripts/verification/inventories/registry.json"


def _registered():
    return json.loads(REGISTRY.read_text(encoding="utf-8"))["inventories"]


def _calibration(checks):
    return [check for check in checks if check.name.endswith("-calibration")]


def test_named_inventories_are_planned_in_the_order_given_and_once_each(tmp_path):
    checks = _calibration(composition.plan(directory=tmp_path, suite="calibration", mode="protocol-fixture",
                                           inventories=("recovery", "native", "recovery")))
    assert [check.name for check in checks] == ["recovery-calibration", "native-calibration"]
    recovery = checks[0]
    assert recovery.argv == (sys.executable, "-m", "scripts.verification.calibrate_suite", "--candidate",
                             str(composition.ROOT), "--out", str(tmp_path / "recovery-calibration"),
                             "--inventory", "recovery"), recovery.argv
    assert set(recovery.required) == {"scripts/verification/calibrate_suite.py",
                                      "scripts/verification/inventories/" + _registered()["recovery"]["file"]}


@pytest.mark.parametrize("stage", ["ci", "manual"])
def test_a_stage_plans_every_inventory_registered_at_it_in_registry_order(tmp_path, stage):
    checks = _calibration(composition.plan(directory=tmp_path, suite="calibration", mode="protocol-fixture",
                                           stage=stage))
    expected = [f"{name}-calibration" for name, entry in _registered().items() if stage in entry["stages"]]
    assert expected and [check.name for check in checks] == expected
    assert all("--stage" not in check.argv for check in checks), "the stage is expanded, never passed on"


def test_without_a_selection_the_ci_stage_is_planned_as_before(tmp_path):
    options = dict(directory=tmp_path, suite="calibration", mode="protocol-fixture")
    assert composition.plan(**options) == composition.plan(stage="ci", **options)
    assert [check.name for check in composition.plan(**options)] == [
        "native-calibration", "work-calibration", "state-calibration"]


def test_a_named_inventory_replaces_the_default_calibration_checks_in_the_whole_run(tmp_path):
    checks = composition.plan(directory=tmp_path, suite="all", mode="protocol-fixture", inventories=("recovery",))
    assert [check.name for check in _calibration(checks)] == ["recovery-calibration"]
    assert [check.name for check in checks][:4] == ["ruff", "syntax", "architecture", "tests"]


@pytest.mark.parametrize("options,fragments", [
    ({"suite": "tests", "inventories": ("native",)}, ("--inventory and --stage select calibration inventories in the "
                                                      "calibration suite",)),
    ({"suite": "static", "stage": "manual"}, ("--inventory and --stage select calibration inventories in the "
                                              "calibration suite",)),
    ({"suite": "calibration", "inventories": ("native",), "stage": "ci"}, ("Select --inventory or --stage, not both",)),
    ({"suite": "calibration", "inventories": ("no-such-inventory",)}, ("Unknown calibration inventory",
                                                                       "no-such-inventory")),
    ({"suite": "calibration", "stage": "pre-draft"}, ("No calibration inventory is registered at stage pre-draft",)),
], ids=["inventory-outside-calibration", "stage-outside-calibration", "both", "unknown-name", "empty-stage"])
def test_a_wrong_selection_is_refused_before_any_command(tmp_path, monkeypatch, options, fragments):
    assert not any("pre-draft" in entry["stages"] for entry in _registered().values()), "the empty stage must be empty"

    def unexpected_launch(*args, **kwargs):
        pytest.fail("a refused selection launched a command")

    monkeypatch.setattr(RecordingRunner, "run", unexpected_launch)
    report = composition.run(output=tmp_path, **options)
    assert report["status"] == "setup_error" and report["checks"] == [], report
    assert report["error"]["type"] == "ValueError", report["error"]
    for fragment in fragments:
        assert fragment in report["error"]["diagnostic"], report["error"]


def test_the_command_line_carries_both_selections_into_the_run():
    from scripts.testkit import operator_cli, operator_composition

    named = operator_cli.parser().parse_args(["run", "--suite", "calibration", "--inventory", "recovery",
                                              "--inventory", "native"])
    assert operator_cli.overrides(named)["inventories"] == ["recovery", "native"]
    staged = operator_cli.parser().parse_args(["run", "--suite", "calibration", "--stage", "manual"])
    assert operator_cli.overrides(staged)["stage"] == "manual"
    assert "inventories" not in operator_cli.overrides(staged) and "stage" not in operator_cli.overrides(named)
    with pytest.raises(SystemExit):
        operator_cli.parser().parse_args(["run", "--suite", "calibration", "--stage", "nightly"])
    assert {"inventories", "stage"} <= set(operator_composition.RUN_FIELDS)


@pytest.mark.parametrize("document", ["docs/testing-framework.md", "docs/pr-quality.md"])
def test_the_guides_say_when_to_select_inventories_or_a_stage(document):
    text = (composition.ROOT / document).read_text(encoding="utf-8")
    assert "--suite calibration --inventory" in text and "--stage" in text, document


@pytest.mark.parametrize("selection", [{"inventories": ["recovery"]}, {"stage": "manual"}], ids=["inventory", "stage"])
def test_a_profile_run_carries_the_selection_into_its_plan(tmp_path, selection):
    """A selection given with --profile reaches the plan exactly as it does without one."""
    from scripts.testkit.adapters import profiles
    from scripts.testkit.operator_composition import execution_options

    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"schema_version": 1, "suite": "calibration"}), encoding="utf-8")
    values = profiles.load_profile(profile, root=composition.ROOT, overrides=selection)
    for name, value in selection.items():
        assert values[name] == value, values
    plain = profiles.load_profile(profile, root=composition.ROOT)
    assert (plain["inventories"], plain["stage"]) == ([], None), "a profile without a selection selects nothing"
    checks = composition.plan(directory=tmp_path / "plan", **execution_options(values))
    expected = ["recovery-calibration"] if "inventories" in selection else [
        f"{name}-calibration" for name, entry in _registered().items() if "manual" in entry["stages"]]
    assert [check.name for check in _calibration(checks)] == expected


def test_a_run_executes_exactly_the_selected_inventories(tmp_path, monkeypatch):
    """The selection reaches what the run checks for readiness and what it launches, not only its refusals."""
    launched = []

    def record_and_stop(self, argv, *args, **kwargs):
        launched.append(tuple(argv))
        raise RuntimeError("stopped by the test before the command ran")

    monkeypatch.setattr(RecordingRunner, "run", record_and_stop)
    report = composition.run(output=tmp_path / "runs", suite="calibration", inventories=("recovery",))
    assert report["selected_check_ids"] == ["recovery-calibration"], report
    assert [check["name"] for check in report["checks"]] == ["recovery-calibration"], report["checks"]
    preflight = json.loads((Path(report["directory"]) / "preflight.json").read_text(encoding="utf-8"))
    assert [check["name"] for check in preflight["checks"]] == ["recovery-calibration"], preflight
    assert len(launched) == 1 and launched[0][launched[0].index("--inventory") + 1] == "recovery", launched


def test_a_profile_plans_a_repeated_inventory_once_in_first_occurrence_order(tmp_path):
    """--profile with a repeated --inventory runs as the same flags do without a profile."""
    from scripts.testkit.adapters import profiles
    from scripts.testkit.operator_composition import execution_options

    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"schema_version": 1, "suite": "calibration"}), encoding="utf-8")
    values = profiles.load_profile(profile, root=composition.ROOT,
                                   overrides={"inventories": ["recovery", "native", "recovery"]})
    assert values["inventories"] == ["recovery", "native"], values["inventories"]
    checks = composition.plan(directory=tmp_path / "plan", **execution_options(values))
    assert [check.name for check in _calibration(checks)] == ["recovery-calibration", "native-calibration"]
