"""Exercise the actual local/CI gate, including its process exit status."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.testkit.composition import plan

ROOT = Path(__file__).resolve().parents[2]


def test_local_command_rejects_violation_and_retains_evidence(tmp_path):
    package = tmp_path / "sample"
    package.mkdir()
    source = package / "__init__.py"
    source.write_text("VALUE = 1\n")
    profile = {"schema_version": 1, "id": "sample", "version": "1", "source_roots": ["sample"],
               "roles": {"core": {"modules": ["sample"], "allowed_roles": ["core"],
                                  "allowed_external": [], "allow_dynamic": False}}}
    (tmp_path / "architecture.json").write_text(json.dumps(profile))
    output = tmp_path / "evidence.json"
    command = [sys.executable, "-m", "scripts.architecture", "--root", str(tmp_path),
               "--profile", "architecture.json", "--output", str(output)]
    healthy = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False, timeout=15)
    assert healthy.returncode == 0, healthy.stderr
    original = json.loads(output.read_text())
    assert original["status"] == "pass"
    source.write_text("import sqlite3\n")
    faulty = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False, timeout=15)
    assert faulty.returncode == 1, faulty.stderr
    rejected = json.loads(output.read_text())
    assert rejected["status"] == "violation"
    assert any(f["rule_id"] == "dependencies.external" and f["target"] == "sqlite3"
               for f in rejected["findings"])
    assert rejected["subject"]["source_sha256"] != original["subject"]["source_sha256"]
    assert rejected["diagnostics"]["status"] == "not_configured"


def test_missing_profile_fails_as_configuration_error(tmp_path):
    output = tmp_path / "result.json"
    result = subprocess.run([sys.executable, "-m", "scripts.architecture", "--root", str(tmp_path),
                             "--output", str(output)], cwd=ROOT, capture_output=True, text=True,
                            check=False, timeout=15)
    assert result.returncode == 1
    assert json.loads(output.read_text())["status"] == "error"


def test_ci_retains_blocking_gate_and_independent_diagnostics(tmp_path):
    workflow = json.loads((ROOT / ".github/workflows/ci.yml").read_text())
    static_job = workflow["jobs"]["changes"]
    gate = next(step for step in static_job["steps"] if "--suite static" in step.get("run", ""))
    assert "python3 -m scripts.testkit run" in gate["run"]
    assert not gate.get("continue-on-error") and not static_job.get("continue-on-error")
    checks = plan(directory=tmp_path, suite="static", mode="protocol-fixture")
    architecture = [check for check in checks if check.name == "architecture"]
    assert len(architecture) == 1 and architecture[0].argv[1:3] == ("-m", "scripts.architecture")
    assert not any("-m scripts.architecture" in step.get("run", "")
                   and "--diagnostics-only" not in step["run"]
                   for job in workflow["jobs"].values() for step in job["steps"])
    job = workflow["jobs"]["architecture"]
    assert not job.get("needs")
    diagnostic = next(step for step in job["steps"] if step.get("name") == "Jev architecture diagnostics")
    assert "!cancelled()" in diagnostic["if"]
    assert "steps.dependencies.outcome == 'success'" in diagnostic["if"]
    assert "--jev-config contracts/hexagonal-jev.json" in diagnostic["run"]
    assert "--diagnostics-only" in diagnostic["run"]
    assert "TYPESAFE_API_KEY" in diagnostic["env"]


@pytest.mark.parametrize("static_result", ["success", "failure", "cancelled", "skipped"])
def test_required_architecture_status_reflects_the_shared_static_result(tmp_path, static_result):
    workflow = json.loads((ROOT / ".github/workflows/ci.yml").read_text())
    status = workflow["jobs"]["architecture-status"]
    assert status["name"] == "Hexagonal architecture"
    assert status["needs"] == ["changes"] and status["if"] == "always()"
    assert not status.get("continue-on-error")
    step, = status["steps"]
    assert step["env"]["STATIC_RESULT"] == "${{ needs.changes.result }}"
    result = subprocess.run(["/bin/bash", "-e", "-c", step["run"]], cwd=tmp_path,
                            env={"STATIC_RESULT": static_result}, capture_output=True, text=True,
                            check=False, timeout=5)
    assert (result.returncode == 0) == (static_result == "success"), result.stdout + result.stderr
