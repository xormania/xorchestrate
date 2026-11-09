"""Balanced, complete and deterministic sharding of the cataloged tests suite."""

import json
import random
import subprocess

import pytest

from scripts.testkit import __main__ as cli
from scripts.testkit import composition
from scripts.testkit.adapters import profiles
from scripts.testkit.adapters.pytest_plugin import load_catalog, load_timings, shard_selection
from scripts.testkit.adapters.timings import regenerate
from scripts.testkit.catalog import select_families
from scripts.testkit.sharding import parse_shard, partition, select_shard
from tests.hexagonal.contracts.sharding import (
    INVALID_SPECS,
    VALID_SPECS,
    balanced,
    complete_partition,
    sharded_inventories,
)
from tests.hexagonal.support import sharding as fixture


def shards_of(selection, count, root):
    return [shard_selection(root, selection, f"{index}/{count}")[0] for index in range(1, count + 1)]


@pytest.mark.parametrize("spec", sorted(VALID_SPECS))
def test_shard_specs_are_one_based_and_bounded(spec):
    assert parse_shard(spec) == VALID_SPECS[spec]
    assert parse_shard(None) is None


@pytest.mark.parametrize("spec", INVALID_SPECS)
def test_invalid_shard_specs_are_rejected_before_any_check_is_planned(spec, tmp_path):
    with pytest.raises(ValueError):
        parse_shard(spec)
    with pytest.raises(ValueError):
        composition.plan(directory=tmp_path, suite="tests", mode="protocol-fixture", shard=spec)
    assert list(tmp_path.iterdir()) == []


def test_assignment_is_deterministic_and_independent_of_input_order():
    weights = fixture.TIMINGS
    paths = fixture.modules()
    expected = partition(paths, 3, weights=weights, default=fixture.DEFAULT_SECONDS)
    for seed in range(5):
        shuffled = list(paths)
        random.Random(seed).shuffle(shuffled)
        assert partition(shuffled, 3, weights=dict(reversed(weights.items())),
                         default=fixture.DEFAULT_SECONDS) == expected


def test_committed_timings_shard_the_real_catalog_completely_for_every_count():
    _, assignments = load_catalog(composition.ROOT)
    default, weights = load_timings(composition.ROOT)
    for count in range(1, 9):
        shards = shards_of(assignments, count, composition.ROOT)
        complete_partition(assignments, shards)
        balanced(shards, weights, default)
        assert shards == shards_of(assignments, count, composition.ROOT)


def test_family_selection_is_partitioned_not_widened_or_narrowed():
    _, assignments = load_catalog(composition.ROOT)
    for families in (["framework"], ["state", "telemetry"], ["architecture", "sessions", "transport"]):
        selection = select_families(assignments, families)
        for count in range(1, min(len(selection), 5) + 1):
            shards = shards_of(selection, count, composition.ROOT)
            complete_partition(selection, shards)
            assert all(entry["family"] in families for shard in shards for entry in shard.values())


def test_module_without_recorded_timing_receives_default_weight_and_is_assigned():
    paths = fixture.modules()
    assert fixture.UNTIMED in paths and fixture.UNTIMED not in fixture.TIMINGS
    for count in range(1, len(paths) + 1):
        shards = [shard["modules"] for shard in partition(paths, count, weights=fixture.TIMINGS,
                                                          default=fixture.DEFAULT_SECONDS)]
        complete_partition(paths, shards)
        balanced(shards, fixture.TIMINGS, fixture.DEFAULT_SECONDS)
    plan = partition(paths, len(paths), weights=fixture.TIMINGS, default=fixture.DEFAULT_SECONDS)
    owner = next(shard for shard in plan if fixture.UNTIMED in shard["modules"])
    assert owner["estimated_seconds"] == fixture.DEFAULT_SECONDS


def test_more_shards_than_selected_modules_is_an_error_not_an_empty_pass():
    with pytest.raises(ValueError, match="selects no modules"):
        select_shard(fixture.modules(), (len(fixture.modules()) + 1, len(fixture.modules()) + 1),
                     weights=fixture.TIMINGS, default=fixture.DEFAULT_SECONDS)


def test_tests_check_carries_the_shard_and_requires_the_timings_input(tmp_path):
    (check,) = composition.plan(directory=tmp_path, suite="tests", mode="protocol-fixture",
                                families=("state",), shard="2/4")
    assert check.argv[check.argv.index("--test-shard") + 1] == "2/4"
    assert check.argv[check.argv.index("--test-family") + 1] == "state"
    assert "contracts/test-timings.json" in check.required
    (unsharded,) = composition.plan(directory=tmp_path, suite="tests", mode="protocol-fixture")
    assert "--test-shard" not in unsharded.argv
    row = composition.readiness(check, root=composition.ROOT, mode="protocol-fixture",
                                families=("state",), shard="2/4")
    assert row["status"] == "passed"
    assert next(item for item in row["readiness"] if item["name"] == "test_shard")["modules"] > 0


@pytest.mark.parametrize("arguments", [["--suite", "static", "--shard", "1/2"], ["--suite", "tests", "--shard", "3/2"]])
def test_command_line_rejects_shards_without_tests_or_out_of_range(arguments, tmp_path, capsys):
    assert cli.main(["run", *arguments, "--output", str(tmp_path / "runs")]) == 1
    report = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert report["status"] == "setup_error" and report["checks"] == []
    assert "shard" in report["error"]["diagnostic"].lower()


def test_profiles_retain_a_shard_only_for_suites_with_tests(tmp_path):
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"schema_version": 1, "suite": "tests", "shard": "1/3", "output": "runs"}))
    assert profiles.load_profile(profile, root=composition.ROOT)["shard"] == "1/3"
    profile.write_text(json.dumps({"schema_version": 1, "suite": "static", "shard": "1/3", "output": "runs"}))
    with pytest.raises(ValueError, match="shard"):
        profiles.load_profile(profile, root=composition.ROOT)


def test_pytest_shards_partition_cases_with_their_own_inventory_and_junit(tmp_path):
    project = fixture.create_project(tmp_path / "project")
    result, unsharded, _ = fixture.run_pytest(project, name="all")
    assert result.returncode == 0, result.stdout + result.stderr
    inventories, reports = [], set()
    for index in range(1, 4):
        result, inventory, junit = fixture.run_pytest(project, "--test-shard", f"{index}/3", name=f"shard-{index}")
        assert result.returncode == 0, result.stdout + result.stderr
        assert junit.is_file()
        reports.add(junit)
        inventories.append(inventory)
    assert len(reports) == 3
    sharded_inventories(unsharded, inventories, 3)
    assert any(fixture.UNTIMED in inventory["shard"]["modules"] for inventory in inventories)


def test_pytest_shards_compose_with_family_selection(tmp_path):
    project = fixture.create_project(tmp_path / "project")
    _, unsharded, _ = fixture.run_pytest(project, "--test-family", "alpha", name="alpha")
    inventories = [fixture.run_pytest(project, "--test-family", "alpha", "--test-shard", f"{index}/2",
                                      name=f"alpha-{index}")[1] for index in (1, 2)]
    sharded_inventories(unsharded, inventories, 2)
    assert {case["family"] for inventory in inventories for case in inventory["cases"] if case["selected"]} == {"alpha"}


@pytest.mark.parametrize("arguments", [["--test-shard", "4/3"], ["--test-shard", "7/7"],
                                       ["--test-family", "gamma", "--test-shard", "2/2"]])
def test_pytest_rejects_invalid_or_empty_shards(arguments, tmp_path):
    project = fixture.create_project(tmp_path / "project")
    result, _, _ = fixture.run_pytest(project, *arguments)
    assert result.returncode == 4, result.stdout + result.stderr


def test_timings_regenerate_from_shard_junit_reports(tmp_path):
    project = fixture.create_project(tmp_path / "project")
    first = fixture.junit_report(tmp_path / "one.xml", [("tests.test_alpha_heavy", 1.25), ("tests.test_alpha_heavy", 2.0),
                                                        ("tests.test_beta_two", 0.5)])
    second = fixture.junit_report(tmp_path / "two.xml", [("tests.test_beta_one.TestGroup", 4.0),
                                                         ("tests.unrelated", 9.0)])
    output = tmp_path / "timings.json"
    result = regenerate(project, [first, second], output)
    document = json.loads(output.read_text())
    assert document == {"schema_version": 1, "default_seconds": 3.25, "modules": {
        "tests/test_alpha_heavy.py": 3.25, "tests/test_beta_one.py": 4.0, "tests/test_beta_two.py": 0.5}}
    assert result["untimed_modules"] == ["tests/test_alpha_light.py", "tests/test_alpha_mid.py", "tests/test_gamma_new.py"]
    load_timings(project, output)


def test_timings_command_writes_a_loadable_file_for_the_real_catalog(tmp_path):
    report = fixture.junit_report(tmp_path / "tests.xml", [("tests.hexagonal.test_testkit_sharding", 0.5)])
    output = tmp_path / "timings.json"
    completed = fixture.run_timings_tool(report, "--output", output)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["modules"] == 1
    default, weights = load_timings(composition.ROOT, output)
    assert weights == {"tests/hexagonal/test_testkit_sharding.py": 0.5} and default == 0.5


def test_ci_runs_every_shard_and_keeps_the_required_application_check():
    workflow = json.loads((composition.ROOT / ".github/workflows/ci.yml").read_text())
    shards = workflow["jobs"]["application-tests"]
    assert shards["strategy"]["fail-fast"] is False
    values = shards["strategy"]["matrix"]["shard"]
    run = next(step["run"] for step in shards["steps"] if "--suite tests" in step.get("run", ""))
    count = int(run.split('--shard "${{ matrix.shard }}/', 1)[1].split('"', 1)[0])
    assert values == list(range(1, count + 1)), "every shard index of N runs exactly once"
    upload = next(step for step in shards["steps"] if step.get("uses", "").startswith("actions/upload-artifact"))
    assert "${{ matrix.shard }}" in upload["with"]["name"] and upload["if"] == "always()"
    assert not any("--suite tests" in step.get("run", "") for name, job in workflow["jobs"].items()
                   if name != "application-tests" for step in job.get("steps", []))
    gate = workflow["jobs"]["application"]
    assert gate["name"] == "Application and adapter tests (Python 3.12)"
    assert gate["if"] == "always()" and "application-tests" in gate["needs"] and not gate.get("continue-on-error")
    needed = [workflow["jobs"][name] for name in gate["needs"] if name != "application-tests"]
    assert any("--suite calibration" in step.get("run", "") for job in needed for step in job["steps"])
    assert any("--suite multi-harness" in step.get("run", "") for job in needed for step in job["steps"])


@pytest.mark.parametrize("tests_result,calibration_result", [
    ("success", "success"), ("failure", "success"), ("cancelled", "success"), ("skipped", "success"),
    ("success", "failure"), ("success", "skipped")])
def test_required_application_check_passes_only_when_every_needed_job_passed(tmp_path, tests_result,
                                                                             calibration_result):
    gate = json.loads((composition.ROOT / ".github/workflows/ci.yml").read_text())["jobs"]["application"]
    (step,) = gate["steps"]
    assert step["env"] == {"TESTS_RESULT": "${{ needs.application-tests.result }}",
                           "CALIBRATION_RESULT": "${{ needs.calibration.result }}"}
    result = subprocess.run(["/bin/bash", "-e", "-c", step["run"]], cwd=tmp_path, capture_output=True, text=True,
                            env={"TESTS_RESULT": tests_result, "CALIBRATION_RESULT": calibration_result}, timeout=10)
    assert (result.returncode == 0) == (tests_result == calibration_result == "success"), result.stdout
