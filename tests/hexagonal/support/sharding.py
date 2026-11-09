"""Planted catalog projects and pytest/JUnit runners for shard selection tests."""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

# Two families with uneven module costs; one module has no recorded timing.
FAMILIES = {
    "alpha": {"tests/test_alpha_heavy.py": 3, "tests/test_alpha_light.py": 1, "tests/test_alpha_mid.py": 2},
    "beta": {"tests/test_beta_one.py": 1, "tests/test_beta_two.py": 2},
    "gamma": {"tests/test_gamma_new.py": 1},
}
TIMINGS = {"tests/test_alpha_heavy.py": 9.0, "tests/test_alpha_light.py": 0.5, "tests/test_alpha_mid.py": 4.0,
           "tests/test_beta_one.py": 1.5, "tests/test_beta_two.py": 3.0, "tests/test_retired.py": 50.0}
UNTIMED = "tests/test_gamma_new.py"
DEFAULT_SECONDS = 2.0


def catalog():
    return {"schema_version": 1, "id": "fixture.sharding", "roots": ["tests"], "families": {
        name: {"guarantee": name + " behavior is retained", "modules": {path: "contract" for path in modules}}
        for name, modules in FAMILIES.items()}}


def timings():
    return {"schema_version": 1, "default_seconds": DEFAULT_SECONDS, "modules": dict(TIMINGS)}


def modules():
    return sorted(path for family in FAMILIES.values() for path in family)


def create_project(directory):
    """A minimal checkout whose collection uses the shared catalog plugin."""
    (directory / "contracts").mkdir(parents=True)
    (directory / "tests").mkdir()
    (directory / "contracts/testing.json").write_text(json.dumps(catalog()))
    (directory / "contracts/test-timings.json").write_text(json.dumps(timings()))
    (directory / "pyproject.toml").write_text('[tool.pytest.ini_options]\ntestpaths = ["tests"]\n')
    (directory / "tests/conftest.py").write_text('pytest_plugins = ["scripts.testkit.adapters.pytest_plugin"]\n')
    for family in FAMILIES.values():
        for path, cases in family.items():
            (directory / path).write_text(
                f"import pytest\n@pytest.mark.parametrize('case', range({cases}))\n"
                "def test_case(case):\n    assert case >= 0\n")
    return directory


def run_pytest(project, *arguments, name="inventory"):
    inventory = project / (name + ".json")
    junit = project / (name + ".xml")
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                             "--test-inventory", str(inventory), "--junitxml=" + str(junit), *arguments],
                            cwd=project, env={**os.environ, "PYTHONPATH": str(ROOT)},
                            capture_output=True, text=True, timeout=60)
    return result, (json.loads(inventory.read_text()) if inventory.exists() else None), junit


def junit_report(path, cases):
    """Write a JUnit report with explicit ``(classname, seconds)`` cases."""
    rows = "".join(f'<testcase classname="{classname}" name="case{index}" time="{seconds}"/>'
                   for index, (classname, seconds) in enumerate(cases))
    path.write_text(f'<?xml version="1.0"?><testsuites><testsuite name="pytest">{rows}</testsuite></testsuites>')
    return path


def run_timings_tool(*arguments):
    return subprocess.run([sys.executable, "-m", "scripts.testkit.timings", *map(str, arguments)],
                          cwd=ROOT, env=dict(os.environ), capture_output=True, text=True, timeout=60)
