"""Exercise the actual local/CI gate, including its process exit status."""

import json
import subprocess
import sys
from pathlib import Path

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


def test_ci_retains_blocking_gate_and_independent_diagnostics():
    workflow = json.loads((ROOT / ".github/workflows/ci.yml").read_text())
    job = workflow["jobs"]["architecture"]
    gate = next(step for step in job["steps"] if step.get("id") == "deterministic")
    assert "python3 -m scripts.architecture" in gate["run"]
    assert "--diagnostics-only" not in gate["run"]
    assert not gate.get("continue-on-error") and not job.get("continue-on-error")
    diagnostic = next(step for step in job["steps"] if step.get("name") == "Jev architecture diagnostics")
    assert diagnostic["if"] == "always()"
    assert "--jev-config contracts/hexagonal-jev.json" in diagnostic["run"]
    assert "--diagnostics-only" in diagnostic["run"]
    assert "TYPESAFE_API_KEY" in diagnostic["env"]
