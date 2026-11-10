"""The launcher runs a project's commands with the release its project.json pins."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.hexagonal.support.projects import ROOT, provision
from xorchestrate.composition import RELEASE
from xorchestrate.instance.home import Home

FAKE_UV = Path(__file__).resolve().parent / "fixtures" / "fake_uv.py"


@pytest.fixture
def machine(tmp_path):
    """One home and companion with a fake ``uv`` first on PATH."""
    programs = tmp_path / "bin"
    programs.mkdir()
    uv = programs / "uv"
    uv.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_UV}" "$@"\n')
    uv.chmod(0o755)
    home = tmp_path / "home"
    environment = {**os.environ, "XORCHESTRATE_HOME": str(home), "FAKE_UV_LOG": str(tmp_path / "uv.log"),
                   "PATH": os.pathsep.join([str(programs), os.environ.get("PATH", "")]),
                   "PYTHONPATH": os.pathsep.join([str(ROOT), os.environ.get("PYTHONPATH", "")])}
    environment.pop("XORCHESTRATE_HANDOFF", None)

    class Machine:
        root = tmp_path

        def project(self, name, release):
            created = provision(tmp_path / name, {"version": 1}, name=name, home=home,
                                companion=tmp_path / "companion", release=release)
            Home(home).update_settings(toolchain_source="fake-release={release}")
            return created.repository

        def run(self, repository, *arguments, cwd=None, **extra):
            # -P: the launcher under test is never itself shadowed by files in ``cwd``.
            return subprocess.run([sys.executable, "-P", "-m", "xorchestrate", "--project", str(repository),
                                   *arguments], capture_output=True, cwd=cwd or tmp_path,
                                  env={**environment, **extra}, timeout=60)

        def uv_calls(self):
            log = tmp_path / "uv.log"
            return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

        @property
        def toolchains(self):
            return home / "toolchains"

    return Machine()


def test_commands_run_through_each_pinned_releases_toolchain(machine):
    pinned = {"alpha": "v9.9.9", "beta": "v8.0.0"}
    repositories = {name: machine.project(name, release) for name, release in pinned.items()}
    for _attempt in range(2):
        for name, release in pinned.items():
            result = machine.run(repositories[name], "describe")
            assert result.returncode == 0, result
            reply = json.loads(result.stdout)
            assert reply["toolchain"] == release and reply["handoff"] == release
            assert reply["argv"] == ["-P", "-m", "xorchestrate", "--project", str(repositories[name]), "describe"]
    syncs = [call for call in machine.uv_calls() if call[0] == "sync"]
    assert len(syncs) == 2, "each toolchain is built once and then reused"
    assert [call[:2] for call in machine.uv_calls() if call[0] == "python"] == [["python", "install"]] * 2
    assert sorted(path.name for path in machine.toolchains.iterdir()) == ["v8.0.0", "v9.9.9"], \
        "toolchains are renamed into place with no staging directory left behind"
    for release in pinned.values():
        assert (machine.toolchains / release / "pyproject.toml").exists()


def test_the_current_release_runs_in_place(machine):
    repository = machine.project("current", RELEASE)
    result = machine.run(repository, "describe")
    assert result.returncode == 0, result
    assert "operations" in json.loads(result.stdout)
    assert machine.uv_calls() == [] and not machine.toolchains.exists()


def test_a_failed_toolchain_build_leaves_nothing_behind(machine):
    repository = machine.project("broken", "v7.0.0")
    result = machine.run(repository, "describe", FAKE_UV_FAIL="sync")
    assert result.returncode == 2, result
    error = json.loads(result.stderr)["error"]
    assert error["code"] == "toolchain_failed" and error["details"]["release"] == "v7.0.0"
    assert "simulated sync failure" in error["details"]["stderr"]
    assert list(machine.toolchains.iterdir()) == []


def test_a_toolchain_that_runs_another_release_is_refused_instead_of_looping(machine):
    repository = machine.project("looping", "v9.9.9")
    # The built "toolchain" actually runs this release, not v9.9.9.
    result = machine.run(repository, "describe", FAKE_UV_REAL="1")
    assert result.returncode == 2, result
    error = json.loads(result.stderr)["error"]
    assert error["code"] == "toolchain_mismatch" and error["details"] == {"release": "v9.9.9", "running": RELEASE}
    assert len([call for call in machine.uv_calls() if call[0] == "sync"]) == 1


def test_repository_files_cannot_shadow_the_handed_off_release(machine):
    repository = machine.project("shadowed", "v9.9.9")
    marker = machine.root / "shadow-ran"
    planted = repository / "xorchestrate"
    planted.mkdir()
    (planted / "__init__.py").write_text("")
    (planted / "__main__.py").write_text(f"open({str(marker)!r}, 'w').write('ran')\n")
    result = machine.run(repository, "describe", cwd=repository, FAKE_UV_REAL="1")
    assert not marker.exists(), "a module in the worktree ran in place of the release"
    assert json.loads(result.stderr)["error"]["code"] == "toolchain_mismatch", result


def test_an_unapproved_release_pin_is_refused_before_any_toolchain_is_built(machine):
    repository = machine.project("pinned", "v9.9.9")
    project_file = repository / ".xorchestrate" / "project.json"
    project_file.write_text(json.dumps({**json.loads(project_file.read_text()), "release": "v6.6.6"}))
    result = machine.run(repository, "describe")
    assert result.returncode == 2, result
    error = json.loads(result.stderr)["error"]
    assert error["code"] == "approval_required" and error["details"]["files"] == ["project.json"]
    assert machine.uv_calls() == [] and not machine.toolchains.exists()


def test_launched_processes_never_inherit_the_handoff_marker(monkeypatch):
    from xorchestrate.adapters.process import command_options

    monkeypatch.setenv("XORCHESTRATE_HANDOFF", "v9.9.9")
    options = command_options({"command": ["tool"], "inherit_env": True})
    assert "XORCHESTRATE_HANDOFF" not in options["env"]
