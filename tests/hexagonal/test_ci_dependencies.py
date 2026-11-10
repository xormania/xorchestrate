"""Native lock checks reject changed inputs without rewriting the recorded baseline."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMMAND = ROOT / "scripts/ci/dependencies.py"


def _command(root, *arguments):
    return subprocess.run([sys.executable, str(COMMAND), *arguments, "--root", str(root), "--offline"],
                          text=True, capture_output=True, check=False, timeout=30)


@pytest.fixture
def locked_project(tmp_path):
    if shutil.which("uv") is None:
        pytest.skip("native uv is unavailable; CI installs its pinned validation runtime")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "lock-witness"\nversion = "0.0.0"\nrequires-python = ">=3.12"\ndependencies = []\n'
        '[project.optional-dependencies]\ntest = []\n[dependency-groups]\nvalidation = []\n')
    created = _command(tmp_path, "update")
    assert created.returncode == 0, created.stderr
    return tmp_path


def test_native_lock_check_accepts_healthy_inputs_without_mutation(locked_project):
    before = {name: (locked_project / name).read_bytes()
              for name in ("pyproject.toml", "uv.lock", ".github/requirements-ci.txt")}
    result = _command(locked_project, "check")
    assert result.returncode == 0, result.stderr
    assert before == {name: (locked_project / name).read_bytes() for name in before}


def test_manifest_drift_is_rejected_and_the_old_lock_is_preserved(locked_project):
    lock = (locked_project / "uv.lock").read_bytes()
    source = locked_project / "pyproject.toml"
    source.write_text(source.read_text().replace('version = "0.0.0"', 'version = "0.0.1"'))
    result = _command(locked_project, "check")
    assert result.returncode != 0
    assert "lockfile" in result.stderr.lower() and "needs to be updated" in result.stderr.lower(), result.stderr
    assert (locked_project / "uv.lock").read_bytes() == lock


@pytest.mark.parametrize("fault", ["missing", "changed"])
def test_validation_export_drift_is_rejected_and_not_repaired(locked_project, fault):
    requirements = locked_project / ".github/requirements-ci.txt"
    if fault == "missing":
        requirements.unlink()
    else:
        requirements.write_text("unreviewed-package==999\n")
    result = _command(locked_project, "check")
    assert result.returncode != 0
    assert "Generated validation requirements differ" in result.stderr
    assert requirements.exists() is (fault != "missing")
    if requirements.exists():
        assert requirements.read_text() == "unreviewed-package==999\n"


def test_intentional_update_refreshes_drift_then_returns_to_a_healthy_control(locked_project):
    source = locked_project / "pyproject.toml"
    source.write_text(source.read_text().replace('version = "0.0.0"', 'version = "0.0.1"'))
    assert _command(locked_project, "update").returncode == 0
    result = _command(locked_project, "check")
    assert result.returncode == 0, result.stderr


def test_runtime_export_keeps_inputs_unchanged_and_writes_only_requested_output(locked_project, tmp_path):
    lock = (locked_project / "uv.lock").read_bytes()
    output = tmp_path / "consumer" / "runtime.txt"
    result = _command(locked_project, "export", "--profile", "runtime", "--output", str(output))
    assert result.returncode == 0, result.stderr
    assert output.is_file()
    assert (locked_project / "uv.lock").read_bytes() == lock


def test_build_requirement_drift_is_rejected_even_when_native_lock_metadata_is_unchanged(locked_project):
    lock = (locked_project / "uv.lock").read_bytes()
    source = locked_project / "pyproject.toml"
    source.write_text(source.read_text() + '\n[build-system]\nrequires = ["setuptools>=9999"]\nbuild-backend = "setuptools.build_meta"\n')
    result = _command(locked_project, "check")
    assert result.returncode != 0
    assert "Build requirements must also be declared" in result.stderr
    assert (locked_project / "uv.lock").read_bytes() == lock
