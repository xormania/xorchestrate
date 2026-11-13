"""Concrete candidates and command setup for security-check contracts."""

import json
import subprocess
from pathlib import Path

from scripts.testkit.adapters.evidence import RecordingRunner
from scripts.testkit.adapters.static import definitions, prepare_static
from tests.hexagonal.support.static_checks import ROOT, static_subject, write_source


def _git(root, *args):
    return subprocess.run(("git", *args), cwd=root, capture_output=True, check=True, text=True)


def security_subject(root):
    """Build a real candidate with clean tracked and nonignored untracked inputs."""
    root = static_subject(root)
    write_source(root, "docs/security-control.txt", "tracked control\n")
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm",
         "isolated security subject")
    write_source(root, "scratch/untracked-security.bin", "untracked control\n")
    return root


def empty_security_subject(root):
    """Build an initialized Git candidate with no selected files."""
    root = Path(root)
    root.mkdir()
    _git(root, "init", "-q")
    return root


def secret_scan_definition(directory):
    matches = [check for check in definitions(directory=directory, timeout=30) if check.name == "secret-scan"]
    if len(matches) != 1:
        raise ValueError("the shared static adapter must declare one secret-scan check")
    return matches[0]


def secret_scan_runner(directory):
    return RecordingRunner(Path(directory) / "commands")


def secret_scan_environment(tmp_path):
    return {"PYTHONPATH": str(ROOT), "PYTHONPYCACHEPREFIX": str(Path(tmp_path) / "pycache")}


def prepare_secret_scan(root, directory, tmp_path, *, runner=None):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    runner = runner or secret_scan_runner(directory)
    environment = secret_scan_environment(tmp_path)
    prepared, manifest = prepare_static(
        secret_scan_definition(directory), root=root, directory=directory, runner=runner, env=environment,
    )
    return prepared, manifest, runner, environment


def run_secret_scan(root, directory, tmp_path):
    prepared, manifest, runner, environment = prepare_secret_scan(root, directory, tmp_path)
    result = runner.run(prepared.argv, cwd=str(root), env=environment, timeout=prepared.timeout)
    report = json.loads((Path(directory) / "secret-scan.json").read_text(encoding="utf-8"))
    return result, report, manifest, runner
