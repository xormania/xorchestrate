"""Shared static adapters bind real checker behavior to their measured inputs."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.testkit.adapters.evidence import RecordingRunner, digest
from scripts.testkit.adapters.identity import verify_effective_inputs
from scripts.testkit.adapters.static import bind_static_report, definitions, prepare_static, static_manifest
from tests.hexagonal.support.static_checks import ROOT, static_subject, write_source


@pytest.mark.parametrize(("name", "relative", "fault", "diagnostic"), [
    ("ruff", ".github/actions/nested/helper.py", 'eval("1")\n', "S307"),
    ("syntax", "tests/hexagonal/support/subject.py", "VALUE = (\n", "never closed"),
    ("architecture", "xorchestrate/subject.py", "import subprocess\n", "dependencies.external"),
])
def test_shared_check_rejects_its_fault_and_retains_the_diagnostic(tmp_path, name, relative, fault, diagnostic):
    root = static_subject(tmp_path / "candidate")
    directory = tmp_path / "evidence"
    directory.mkdir()
    runner = RecordingRunner(directory / "commands")
    environment = {"PYTHONPATH": str(ROOT), "PYTHONPYCACHEPREFIX": str(tmp_path / "pycache")}
    definition = next(check for check in definitions(directory=directory, timeout=30) if check.name == name)

    control, manifest = prepare_static(definition, root=root, directory=directory, runner=runner, env=environment)
    clean = runner.run(control.argv, cwd=str(root), env=environment, timeout=30)
    assert clean.exit == 0, clean.stderr + clean.stdout
    assert manifest["files"]

    write_source(root, relative, fault)
    prepared, _ = prepare_static(definition, root=root, directory=directory, runner=runner, env=environment)
    failed = runner.run(prepared.argv, cwd=str(root), env=environment, timeout=30)
    assert failed.exit != 0
    structured = directory / ("ruff.json" if name == "ruff" else name + ".json")
    assert diagnostic in failed.stderr + failed.stdout + structured.read_text()
    record = runner.records[-1]
    receipt = Path(record["directory"])
    assert record["stdout_sha256"] == digest((receipt / "stdout.log").read_bytes())
    assert record["stderr_sha256"] == digest((receipt / "stderr.log").read_bytes())


@pytest.mark.parametrize("change", ["ignored-content", "addition", "removal", "rename", "symlink-target", "configuration"])
def test_effective_input_identity_covers_content_and_membership_changes(tmp_path, change):
    root = static_subject(tmp_path / "candidate")
    ignored = write_source(root, "scripts/ignored.py", "VALUE = 1\n")
    write_source(root, ".gitignore", "scripts/ignored.py\n")
    target = write_source(root, "data/linked-source.txt", "VALUE = 1\n")
    (root / "scripts/linked.py").symlink_to(target)
    directory = tmp_path / "evidence"
    directory.mkdir()
    runner = RecordingRunner(directory / "commands")
    name = "architecture" if change in {"ignored-content", "configuration"} else "syntax"
    options = dict(root=root, directory=directory, runner=runner, env={}, timeout=30)
    before = static_manifest(name, **options)
    assert "scripts/ignored.py" in before["files"]
    assert "scripts/linked.py" in before["files"]

    if change == "ignored-content":
        ignored.write_text("VALUE = 2\n")
    elif change == "addition":
        write_source(root, "scripts/new_module.py", "VALUE = 2\n")
    elif change == "removal":
        ignored.unlink()
    elif change == "rename":
        ignored.rename(root / "scripts/renamed.py")
    elif change == "symlink-target":
        target.write_text("VALUE = 2\n")
    else:
        profile = root / "contracts/hexagonal.json"
        value = json.loads(profile.read_text())
        value["description"] = "Changed measured architecture configuration"
        profile.write_text(json.dumps(value))

    after = static_manifest(name, **options)
    assert after["content_sha256"] != before["content_sha256"]
    assert not verify_effective_inputs(root, before, actual=after)["passed"]


def test_static_input_selection_rejects_symlink_escape(tmp_path):
    root = static_subject(tmp_path / "candidate")
    target = write_source(tmp_path, "outside.py", "VALUE = 1\n")
    (root / "scripts/escaped.py").symlink_to(target)
    directory = tmp_path / "evidence"
    directory.mkdir()
    with pytest.raises(ValueError, match="outside"):
        static_manifest("syntax", root=root, directory=directory,
                        runner=RecordingRunner(directory / "commands"), env={})


@pytest.mark.parametrize(("name", "corruption"), [
    ("ruff", "missing"), ("ruff", "malformed"),
    ("syntax", "membership"), ("syntax", "identity"),
    ("architecture", "source-hash"), ("architecture", "configuration"),
])
def test_successful_process_requires_a_complete_bound_tool_report(tmp_path, name, corruption):
    root = static_subject(tmp_path / "candidate")
    directory = tmp_path / "evidence"
    directory.mkdir()
    runner = RecordingRunner(directory / "commands")
    environment = {"PYTHONPATH": str(ROOT), "PYTHONPYCACHEPREFIX": str(tmp_path / "pycache")}
    definition = next(check for check in definitions(directory=directory, timeout=30) if check.name == name)
    prepared, manifest = prepare_static(definition, root=root, directory=directory, runner=runner, env=environment)
    result = runner.run(prepared.argv, cwd=str(root), env=environment, timeout=30)
    assert result.exit == 0, result.stderr + result.stdout
    assert bind_static_report(name, directory=directory, manifest=manifest)["status"] == "passed"
    path = directory / (name + ".json")
    report = json.loads(path.read_text())

    if corruption == "missing":
        path.unlink()
    else:
        if corruption == "malformed":
            report = {"unrecognized": "not a diagnostic list"}
        elif corruption == "membership":
            report["files"].pop()
        elif corruption == "identity":
            report["effective_input_sha256"] = "0" * 64
        elif corruption == "source-hash":
            report["files"][0]["sha256"] = "0" * 64
        else:
            report["profile"]["version"] = "different-policy"
        path.write_text(json.dumps(report))

    with pytest.raises((OSError, ValueError)):
        bind_static_report(name, directory=directory, manifest=manifest)


def test_documented_launcher_keeps_cold_and_warm_cache_outputs_external(tmp_path):
    root = static_subject(tmp_path / "candidate")
    # The actual launcher and checker adapters run from this isolated candidate.
    for relative in ("scripts/testkit", "scripts/architecture"):
        shutil.copytree(ROOT / relative, root / relative, ignore=shutil.ignore_patterns("__pycache__"))
    profile = root / "contracts/hexagonal.json"
    value = json.loads(profile.read_text())
    # This scenario measures launcher/cache behavior; the separate checker matrix
    # exercises dependency violations. Its architecture subject stays isolated.
    value["source_roots"] = ["xorchestrate"]
    value["roles"]["subject"]["modules"] = ["xorchestrate.*"]
    profile.write_text(json.dumps(value))
    for argv in (("git", "init", "-q"), ("git", "add", "."),
                 ("git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                  "commit", "-qm", "isolated launcher subject")):
        subprocess.run(argv, cwd=root, capture_output=True, check=True, timeout=10)
    environment = {**os.environ, "PYTHONPATH": str(root), "PYTHONPYCACHEPREFIX": str(tmp_path / "pycache"),
                   "PYTHONDONTWRITEBYTECODE": ""}
    command = (sys.executable, "-m", "scripts.testkit", "run", "--suite", "static",
               "--output", str(tmp_path / "evidence"))
    sources = {path.relative_to(root): path.read_bytes() for path in root.rglob("*.py")}
    cold = subprocess.run(command, cwd=root, env=environment, capture_output=True, text=True, timeout=60)
    assert cold.returncode == 0, cold.stdout + cold.stderr
    assert {path.relative_to(root): path.read_bytes() for path in root.rglob("*.py")} == sources

    subject = root / "xorchestrate/subject.py"
    subprocess.run((sys.executable, "-m", "py_compile", str(subject)), cwd=root, env=environment,
                   capture_output=True, check=True, timeout=10)
    previous = subject.stat()
    subject.write_text("VALUE = (\n")
    assert subject.stat().st_size == previous.st_size
    os.utime(subject, ns=(previous.st_atime_ns, previous.st_mtime_ns))
    warm = subprocess.run(command, cwd=root, env=environment, capture_output=True, text=True, timeout=60)
    assert warm.returncode != 0
    report = json.loads(warm.stdout)
    syntax = next(check for check in report["checks"] if check["name"] == "syntax")
    assert syntax["launched"] and syntax["exit_status"] != 0
    assert not any(path.name in {"__pycache__", ".ruff_cache", ".pytest_cache"} for path in root.rglob("*"))
    assert not list(root.rglob("*.pyc"))
    assert list((tmp_path / "pycache").rglob("*.pyc"))
