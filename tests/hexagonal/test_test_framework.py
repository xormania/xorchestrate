"""Behavioral controls for test ownership, selection and collection evidence."""

import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

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
