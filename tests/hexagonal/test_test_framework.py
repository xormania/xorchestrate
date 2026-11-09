"""Behavioral controls for test ownership, selection and collection evidence."""

import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.testkit import composition
from scripts.testkit.adapters.evidence import RecordingRunner
from scripts.testkit.catalog import select_families, validate_catalog

ROOT = Path(__file__).resolve().parents[2]


def catalog():
    return {"schema_version": 1, "id": "fixture.testing", "roots": ["tests"], "families": {
        "identity": {"guarantee": "Identity is retained", "modules": {"tests/test_identity.py": "contract"}},
        "transport": {"guarantee": "Transport handles failures", "modules": {"tests/test_transport.py": "adapter"}},
    }}


def test_classification_keeps_one_explicit_owner_and_selection_does_not_change_catalog():
    declaration = catalog()
    before = deepcopy(declaration)
    assigned = validate_catalog(declaration, ["tests/test_transport.py", "tests/test_identity.py"])
    assert assigned["tests/test_identity.py"] == {
        "family": "identity", "layer": "contract", "guarantee": "Identity is retained"}
    assert list(select_families(assigned, ["identity"])) == ["tests/test_identity.py"]
    assert list(select_families(assigned, layers=["adapter"])) == ["tests/test_transport.py"]
    assert declaration == before


@pytest.mark.parametrize("fault", ["unknown", "missing", "duplicate", "empty_family", "unknown_layer", "overlapping_roots"])
def test_catalog_rejects_undeclared_missing_or_ambiguous_coverage(fault):
    declaration = catalog()
    found = ["tests/test_identity.py", "tests/test_transport.py"]
    if fault == "unknown":
        found.append("tests/test_unowned.py")
    elif fault == "missing":
        found.remove("tests/test_identity.py")
    elif fault == "duplicate":
        declaration["families"]["transport"]["modules"]["tests/test_identity.py"] = "adapter"
    elif fault == "empty_family":
        declaration["families"]["identity"]["modules"] = {}
    elif fault == "unknown_layer":
        declaration["families"]["identity"]["modules"]["tests/test_identity.py"] = "unclassified"
    else:
        declaration["roots"].append("tests/nested")
    with pytest.raises(ValueError):
        validate_catalog(declaration, found)


@pytest.mark.parametrize("families,layers", [(["missing"], []), (["identity"], ["adapter"]), ([], ["missing"])])
def test_selection_cannot_manufacture_a_successful_empty_plan(families, layers):
    assigned = validate_catalog(catalog(), ["tests/test_transport.py", "tests/test_identity.py"])
    with pytest.raises(ValueError):
        select_families(assigned, families, layers)


@pytest.fixture
def project(tmp_path):
    (tmp_path / "contracts").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "contracts/testing.json").write_text(json.dumps(catalog()))
    (tmp_path / "pyproject.toml").write_text('[tool.pytest.ini_options]\ntestpaths = ["tests"]\n')
    (tmp_path / "tests/conftest.py").write_text('pytest_plugins = ["scripts.testkit.adapters.pytest_plugin"]\n')
    (tmp_path / "tests/test_identity.py").write_text('import pytest\n@pytest.mark.parametrize("identity", [1, 2])\ndef test_identity(identity):\n    assert identity > 0\n')
    (tmp_path / "tests/test_transport.py").write_text('def test_transport():\n    assert True\n')
    return tmp_path


def run_pytest(project, *arguments):
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "--test-inventory", str(project / "inventory.json"), *arguments],
                          cwd=project, env={**os.environ, "PYTHONPATH": str(ROOT)},
                          capture_output=True, text=True, timeout=20)


def test_pytest_family_selection_retains_deselection_and_case_outcomes(project):
    result = run_pytest(project, "--test-family", "identity")
    assert result.returncode == 0, result.stdout + result.stderr
    inventory = json.loads((project / "inventory.json").read_text())
    assert inventory["status"] == "passed"
    assert (inventory["collected"], inventory["selected"]) == (3, 2)
    assert inventory["families"]["identity"]["outcomes"] == {"passed": 2}
    assert inventory["families"]["transport"]["outcomes"] == {"deselected": 1}


def test_pytest_explicit_case_selection_keeps_calibration_runs_possible(project):
    result = run_pytest(project, "tests/test_identity.py::test_identity[1]")
    assert result.returncode == 0, result.stdout + result.stderr
    inventory = json.loads((project / "inventory.json").read_text())
    assert inventory["selected"] == inventory["collected"] == 1
    assert inventory["cases"][0]["id"] == "tests/test_identity.py::test_identity[1]"


@pytest.mark.parametrize("fault", ["unknown", "missing", "empty", "empty_class", "ignored", "empty_selection"])
def test_pytest_rejects_collection_gaps_instead_of_reporting_a_pass(project, fault):
    arguments = []
    if fault == "unknown":
        (project / "tests/test_undeclared.py").write_text("def test_undeclared():\n    assert True\n")
    elif fault == "missing":
        (project / "tests/test_identity.py").unlink()
    elif fault == "empty":
        (project / "tests/test_identity.py").write_text("VALUE = 1\n")
    elif fault == "empty_class":
        (project / "tests/test_identity.py").write_text("class TestEmpty:\n    pass\n")
    elif fault == "ignored":
        arguments = ["--ignore", "tests/test_identity.py"]
    else:
        arguments = ["-k", "does_not_exist"]
    result = run_pytest(project, *arguments)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "ERROR" in result.stderr


def test_failed_and_skipped_cases_stay_visible_in_inventory(project):
    (project / "tests/test_identity.py").write_text('import pytest\ndef test_identity():\n    pytest.skip("fixture unavailable")\n')
    (project / "tests/test_transport.py").write_text("def test_transport():\n    assert False, 'transport fault'\n")
    result = run_pytest(project)
    assert result.returncode == 1, result.stdout + result.stderr
    inventory = json.loads((project / "inventory.json").read_text())
    assert inventory["status"] == "failed"
    assert inventory["families"]["identity"]["outcomes"] == {"skipped": 1}
    assert inventory["families"]["transport"]["outcomes"] == {"failed": 1}


@pytest.fixture
def testkit_project(project):
    directory = project / "tests/hexagonal"
    directory.mkdir()
    for path in (project / "tests").glob("test_*.py"):
        path.rename(directory / path.name)
    declaration = catalog()
    declaration["roots"] = ["tests/hexagonal"]
    for family in declaration["families"].values():
        family["modules"] = {path.replace("tests/", "tests/hexagonal/"): layer
                             for path, layer in family["modules"].items()}
    (project / "contracts/testing.json").write_text(json.dumps(declaration))
    (project / "contracts/test-timings.json").write_text(json.dumps({
        "schema_version": 1, "default_seconds": 1, "modules": {}}))
    return project


@pytest.mark.parametrize("fault", [
    "missing", "malformed", "catalog", "duplicate_case", "foreign_case", "missing_outcome",
    "counts", "family_counts", "ownership", "selection", "collect_only", "exit_status", "missing_module",
])
def test_successful_pytest_command_requires_consistent_execution_evidence(testkit_project, monkeypatch, fault):
    project = testkit_project
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
    original = RecordingRunner.run

    def run_then_damage_inventory(runner, argv, **options):
        result = original(runner, argv, **options)
        assert result.exit == 0, result.stdout + result.stderr
        inventory_path = Path(argv[argv.index("--test-inventory") + 1])
        if fault == "missing":
            inventory_path.unlink()
        elif fault == "malformed":
            inventory_path.write_text("{")
        else:
            inventory = json.loads(inventory_path.read_text())
            if fault == "catalog":
                inventory["catalog_sha256"] = "0" * 64
            elif fault == "duplicate_case":
                inventory["cases"][1] = deepcopy(inventory["cases"][0])
            elif fault == "foreign_case":
                inventory["cases"][0]["id"] = "tests/test_foreign.py::test_identity"
            elif fault == "missing_outcome":
                inventory["cases"][0]["outcome"] = "not_run"
            elif fault == "counts":
                inventory["selected"] += 1
            elif fault == "family_counts":
                inventory["families"]["identity"]["outcomes"] = {"passed": 500}
            elif fault == "ownership":
                inventory["cases"][0]["family"] = "transport"
            elif fault == "selection":
                inventory["cases"][0]["selected"] = False
            elif fault == "missing_module":
                inventory["cases"] = [case for case in inventory["cases"] if case["family"] != "transport"]
                inventory["collected"] = inventory["selected"] = len(inventory["cases"])
                inventory["collected_modules"].remove("tests/hexagonal/test_transport.py")
                inventory["families"]["transport"].update(collected=0, selected=0, outcomes={})
            elif fault == "collect_only":
                inventory["status"] = "collected"
            else:
                inventory["exit_status"] = 1
            inventory_path.write_text(json.dumps(inventory))
        return result

    monkeypatch.setattr(RecordingRunner, "run", run_then_damage_inventory)
    report = composition.run(root=project, output=project.parent / (project.name + "-runs"), suite="tests")
    check, = report["checks"]
    assert check["launched"] and check["exit_status"] == 0
    assert check["status"] == "evidence_error", report
    assert report["status"] == "failed"
    assert Path(check["command_record"]).is_file()


@pytest.mark.parametrize("behavior,expected_exit,expected_status", [
    ("mixed", 0, "passed"), ("all_skipped", 0, "evidence_error"), ("failure", 1, "failed"),
])
def test_pytest_verdict_distinguishes_executed_behavior_from_skips(testkit_project, monkeypatch, behavior,
                                                                 expected_exit, expected_status):
    project = testkit_project
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
    (project / "tests/hexagonal/test_identity.py").write_text(
        'import pytest\ndef test_identity():\n    pytest.skip("fixture unavailable")\n')
    if behavior == "all_skipped":
        (project / "tests/hexagonal/test_transport.py").write_text(
            'import pytest\ndef test_transport():\n    pytest.skip("transport unavailable")\n')
    elif behavior == "failure":
        (project / "tests/hexagonal/test_transport.py").write_text('def test_transport():\n    assert False, "transport fault"\n')
    report = composition.run(root=project, output=project.parent / (project.name + "-runs"), suite="tests")
    check, = report["checks"]
    assert check["launched"] and check["exit_status"] == expected_exit, report
    assert check["status"] == expected_status, report
    assert report["status"] == ("passed" if behavior == "mixed" else "failed")
    inventory = json.loads((Path(report["directory"]) / "inventory.json").read_text())
    assert inventory["families"]["identity"]["outcomes"] == {"skipped": 1}
    assert inventory["families"]["transport"]["outcomes"] == {
        {"mixed": "passed", "all_skipped": "skipped", "failure": "failed"}[behavior]: 1}
    command = json.loads(Path(check["command_record"]).read_text())
    assert command["exit_status"] == expected_exit


@pytest.mark.parametrize("families,shards", [((), ("1/2", "2/2")), (("identity",), ("1/1",))])
def test_pytest_evidence_qualifies_real_shard_and_family_selections(testkit_project, monkeypatch, families, shards):
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
    observed = set()
    for shard in shards:
        report = composition.run(root=testkit_project, output=testkit_project.parent / (testkit_project.name + "-runs"),
                                 suite="tests", families=families, shard=shard)
        assert report["status"] == "passed", report
        check, = report["checks"]
        assert check["status"] == "passed" and check["exit_status"] == 0
        inventory = json.loads((Path(report["directory"]) / "inventory.json").read_text())
        selected = {case["id"] for case in inventory["cases"] if case["selected"]}
        assert selected and not observed.intersection(selected)
        observed.update(selected)
        assert all(case["outcome"] == ("passed" if case["selected"] else "deselected") for case in inventory["cases"])
    expected = {"tests/hexagonal/test_identity.py::test_identity[1]", "tests/hexagonal/test_identity.py::test_identity[2]"}
    if not families:
        expected.add("tests/hexagonal/test_transport.py::test_transport")
    assert observed == expected


@pytest.mark.parametrize("fault", ["missing_descriptor", "wrong_descriptor", "missing_selected_module"])
def test_pytest_evidence_rejects_inconsistent_shard_execution(testkit_project, monkeypatch, fault):
    monkeypatch.setenv("PYTHONPATH", str(ROOT))
    original = RecordingRunner.run

    def run_then_damage_shard(runner, argv, **options):
        result = original(runner, argv, **options)
        assert result.exit == 0, result.stdout + result.stderr
        path = Path(argv[argv.index("--test-inventory") + 1])
        inventory = json.loads(path.read_text())
        if fault == "missing_descriptor":
            del inventory["shard"]
        elif fault == "wrong_descriptor":
            inventory["shard"]["count"] = 2
        else:
            inventory["cases"] = [case for case in inventory["cases"] if case["family"] != "transport"]
            inventory["collected"] = inventory["selected"] = len(inventory["cases"])
            inventory["collected_modules"].remove("tests/hexagonal/test_transport.py")
            inventory["families"]["transport"].update(collected=0, selected=0, outcomes={})
        path.write_text(json.dumps(inventory))
        return result

    monkeypatch.setattr(RecordingRunner, "run", run_then_damage_shard)
    report = composition.run(root=testkit_project, output=testkit_project.parent / (testkit_project.name + "-runs"),
                             suite="tests", shard="1/1")
    check, = report["checks"]
    assert check["launched"] and check["exit_status"] == 0
    assert check["status"] == "evidence_error", report
    assert report["status"] == "failed"
