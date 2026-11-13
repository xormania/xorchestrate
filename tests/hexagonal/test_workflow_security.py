"""Pinned offline workflow scanning is candidate-bound and safe to publish."""

import json
import os
import sys
from pathlib import Path

import pytest

from scripts.testkit.adapters.evidence import RecordingRunner, digest
from scripts.testkit.adapters.static import bind_static_report, definitions, prepare_static
from tests.hexagonal.support.static_checks import ROOT, static_subject, write_source

SAFE_WORKFLOW = """name: safe
on:
  workflow_dispatch:
permissions: {}
jobs:
  audit:
    runs-on: ubuntu-latest
    steps:
      - run: echo safe
"""


def _workflow_subject(root, source=SAFE_WORKFLOW, relative=".github/workflows/control.yml"):
    root = static_subject(root)
    write_source(root, relative, source)
    return root


def _zizmor_definition(directory):
    matches = [check for check in definitions(directory=directory, timeout=30) if check.name == "zizmor"]
    assert len(matches) == 1, "the shared static adapter must declare one Zizmor check"
    return matches[0]


def _environment(tmp_path, *, programs=None, **extra):
    environment = {
        "PYTHONPATH": str(ROOT),
        "PYTHONPYCACHEPREFIX": str(tmp_path / "pycache"),
    }
    if programs is not None:
        environment["PATH"] = os.pathsep.join((str(programs), os.environ.get("PATH", "")))
    environment.update(extra)
    return environment


def _prepare(root, directory, tmp_path, *, environment=None):
    directory.mkdir(parents=True, exist_ok=True)
    runner = RecordingRunner(directory / "commands")
    environment = environment or _environment(tmp_path)
    prepared, manifest = prepare_static(
        _zizmor_definition(directory), root=root, directory=directory, runner=runner, env=environment,
    )
    return prepared, manifest, runner, environment


def _run(root, directory, tmp_path, *, environment=None):
    prepared, manifest, runner, environment = _prepare(
        root, directory, tmp_path, environment=environment,
    )
    result = runner.run(prepared.argv, cwd=str(root), env=environment, timeout=prepared.timeout)
    report = json.loads((directory / "zizmor.json").read_text(encoding="utf-8"))
    return result, report, manifest, runner


def _assert_complete_file_evidence(report, manifest):
    expected = {name: manifest["files"][name]["content_sha256"] for name in manifest["sources"]}
    observed = {item["path"]: item["sha256"] for item in report["files"]}
    assert len(report["files"]) == len(observed), "the report must not repeat a selected workflow"
    assert observed == expected, "the report must bind every selected workflow to its captured bytes"
    assert report["effective_input_sha256"] == manifest["content_sha256"]


def _fake_zizmor(tmp_path, *, version="1.32.0"):
    programs = tmp_path / "fake-zizmor-bin"
    observation = tmp_path / "fake-zizmor-observation.json"
    source = f"""#!{sys.executable}
import json
import os
import sys
from pathlib import Path

path = Path(os.environ["FAKE_ZIZMOR_OBSERVATION"])
observations = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
observations.append({{
    "argv": sys.argv[1:],
    "github_token_names": [name for name in (
        "GH_TOKEN", "GITHUB_TOKEN", "ZIZMOR_GITHUB_TOKEN"
    ) if name in os.environ],
}})
path.write_text(json.dumps(observations), encoding="utf-8")
if sys.argv[1:] == ["--version"]:
    print("zizmor {version}")
    raise SystemExit(0)
print("[]")
"""
    executable = write_source(programs, "zizmor", source)
    executable.chmod(0o755)
    return programs, observation


def test_real_pinned_zizmor_accepts_a_clean_minimal_workflow_with_complete_inputs(tmp_path):
    root = _workflow_subject(tmp_path / "candidate")
    directory = tmp_path / "evidence"

    result, report, manifest, _ = _run(root, directory, tmp_path)

    assert result.exit == 0, result.stderr + result.stdout
    assert set(report) == {"schema_version", "status", "effective_input_sha256", "files", "findings"}
    assert report["schema_version"] == 1 and report["status"] == "passed"
    assert report["findings"] == []
    assert manifest["sources"] == [".github/workflows/control.yml"]
    assert manifest["scope"]["configuration"] == [
        "contracts/zizmor.yml", ".github/requirements-ci.txt",
    ]
    for relative in manifest["scope"]["configuration"]:
        assert manifest["files"][relative]["content_sha256"] == digest((root / relative).read_bytes())
    assert manifest["tool"]["zizmor"] == "zizmor 1.32.0"
    _assert_complete_file_evidence(report, manifest)
    assert bind_static_report("zizmor", directory=directory, manifest=manifest)["status"] == "passed"


def test_real_pinned_zizmor_detects_a_deliberate_template_injection_without_republishing_it(tmp_path):
    unsafe_expression = "$" + "{{ github.event.pull_request.title }}"
    source = """name: unsafe
on:
  pull_request:
permissions: {{}}
jobs:
  audit:
    runs-on: ubuntu-latest
    steps:
      - run: echo "{}"
""".format(unsafe_expression)
    root = _workflow_subject(tmp_path / "candidate", source)
    directory = tmp_path / "evidence"

    result, report, manifest, _ = _run(root, directory, tmp_path)

    assert result.exit == 14, "the real high-severity fault must retain Zizmor's finding exit"
    assert report["status"] == "failed"
    assert report["findings"] == [{
        "path": ".github/workflows/control.yml",
        "line": 9,
        "column": 24,
        "rule": "template-injection",
        "severity": "high",
        "confidence": "high",
    }]
    assert set(report["findings"][0]) == {"path", "line", "column", "rule", "severity", "confidence"}
    _assert_complete_file_evidence(report, manifest)
    assert bind_static_report("zizmor", directory=directory, manifest=manifest)["status"] == "failed"
    assert unsafe_expression not in json.dumps(report)
    assert unsafe_expression not in result.stdout
    assert unsafe_expression not in result.stderr


def test_zizmor_selects_only_complete_top_level_workflow_and_policy_inputs(tmp_path):
    root = _workflow_subject(tmp_path / "candidate")
    write_source(root, ".github/workflows/second.yaml", SAFE_WORKFLOW.replace("name: safe", "name: second"))
    write_source(root, ".github/workflows/nested/not-a-workflow.yml", SAFE_WORKFLOW)
    write_source(root, ".github/actions/not-a-workflow.yml", SAFE_WORKFLOW)
    directory = tmp_path / "evidence"

    _, manifest, _, _ = _prepare(root, directory, tmp_path)

    assert manifest["sources"] == [
        ".github/workflows/control.yml", ".github/workflows/second.yaml",
    ]
    assert manifest["scope"]["configuration"] == [
        "contracts/zizmor.yml", ".github/requirements-ci.txt",
    ]
    assert set(manifest["sources"]).isdisjoint({
        ".github/workflows/nested/not-a-workflow.yml", ".github/actions/not-a-workflow.yml",
    })


def test_zizmor_refuses_a_candidate_with_no_selected_workflows_before_launch(tmp_path):
    root = static_subject(tmp_path / "candidate")
    directory = tmp_path / "evidence"
    directory.mkdir()
    runner = RecordingRunner(directory / "commands")

    with pytest.raises(ValueError, match="(?i)(no workflow|empty)"):
        prepare_static(
            _zizmor_definition(directory), root=root, directory=directory, runner=runner,
            env=_environment(tmp_path),
        )
    assert runner.records == [], "empty workflow selection must be refused before launching Zizmor"


def test_zizmor_refuses_a_workflow_symlink_that_escapes_the_candidate(tmp_path):
    root = static_subject(tmp_path / "candidate")
    outside = write_source(tmp_path, "outside.yml", SAFE_WORKFLOW)
    workflow = root / ".github/workflows/escape.yml"
    workflow.parent.mkdir(parents=True)
    workflow.symlink_to(outside)
    directory = tmp_path / "evidence"
    directory.mkdir()
    runner = RecordingRunner(directory / "commands")

    with pytest.raises(ValueError, match="(?i)(outside|escape)"):
        prepare_static(
            _zizmor_definition(directory), root=root, directory=directory, runner=runner,
            env=_environment(tmp_path),
        )
    assert runner.records == [], "an escaping workflow must be refused before launching Zizmor"


def test_zizmor_invocation_is_offline_strict_and_scrubs_github_tokens(tmp_path):
    root = _workflow_subject(tmp_path / "candidate")
    programs, observation = _fake_zizmor(tmp_path)
    environment = _environment(
        tmp_path, programs=programs, FAKE_ZIZMOR_OBSERVATION=str(observation),
        GH_TOKEN="first-token-canary", GITHUB_TOKEN="second-token-canary",
        ZIZMOR_GITHUB_TOKEN="third-token-canary",
    )
    directory = tmp_path / "evidence"

    result, report, manifest, _ = _run(root, directory, tmp_path, environment=environment)

    assert result.exit == 0, result.stderr + result.stdout
    observed = json.loads(observation.read_text(encoding="utf-8"))
    assert [item["argv"] for item in observed].count(["--version"]) == 1
    assert all(item["github_token_names"] == [] for item in observed)
    argv = next(item["argv"] for item in observed if item["argv"] != ["--version"])
    assert "--offline" in argv
    assert "--strict-collection" in argv
    assert "--persona=regular" in argv
    assert "--format=json-v1" in argv
    assert argv[argv.index("--config") + 1] == str(root / "contracts/zizmor.yml")
    cache = Path(argv[argv.index("--cache-dir") + 1]).resolve()
    assert not cache.is_relative_to(root)
    assert str(root / ".github/workflows/control.yml") in argv
    assert report["status"] == "passed" and report["findings"] == []
    _assert_complete_file_evidence(report, manifest)


def test_zizmor_refuses_an_installed_version_that_disagrees_with_the_exact_pin(tmp_path):
    root = _workflow_subject(tmp_path / "candidate")
    requirements = root / ".github/requirements-ci.txt"
    original = requirements.read_text(encoding="utf-8")
    changed = original.replace("zizmor==1.32.0", "zizmor==0.0.0")
    assert changed != original, "the isolated subject must carry the reviewed exact Zizmor pin"
    requirements.write_text(changed, encoding="utf-8")
    programs, observation = _fake_zizmor(tmp_path)
    environment = _environment(
        tmp_path, programs=programs, FAKE_ZIZMOR_OBSERVATION=str(observation),
    )

    with pytest.raises(ValueError, match="(?i)(pin|version|match)"):
        _prepare(root, tmp_path / "evidence", tmp_path, environment=environment)
    observed = json.loads(observation.read_text(encoding="utf-8"))
    assert [item["argv"] for item in observed] == [["--version"]], (
        "a mismatched tool must be refused before it scans workflows"
    )


def test_strict_zizmor_collection_refuses_a_malformed_workflow_without_a_normalized_verdict(tmp_path):
    root = _workflow_subject(tmp_path / "candidate", "name: malformed\non: [\n")
    directory = tmp_path / "evidence"
    prepared, _, runner, environment = _prepare(root, directory, tmp_path)

    result = runner.run(prepared.argv, cwd=str(root), env=environment, timeout=prepared.timeout)

    assert result.exit in {1, 2, 3}, "collection failure must not be mistaken for a finding verdict"
    assert not (directory / "zizmor.json").exists()


@pytest.mark.parametrize("corruption", [
    "missing", "malformed", "schema-version", "report-extra", "unselected", "duplicate", "incomplete",
    "source-hash", "manifest-hash", "file-extra", "status-contradiction", "passed-with-finding",
    "finding-extra", "finding-incomplete", "finding-unselected", "finding-line", "finding-column",
    "finding-rule", "finding-severity", "finding-confidence", "policy-drift",
])
def test_zizmor_success_requires_exact_complete_candidate_bound_evidence(tmp_path, corruption):
    root = _workflow_subject(tmp_path / "candidate")
    programs, observation = _fake_zizmor(tmp_path)
    environment = _environment(
        tmp_path, programs=programs, FAKE_ZIZMOR_OBSERVATION=str(observation),
    )
    directory = tmp_path / "evidence"
    result, report, manifest, _ = _run(root, directory, tmp_path, environment=environment)
    assert result.exit == 0, result.stderr + result.stdout
    assert bind_static_report("zizmor", directory=directory, manifest=manifest)["status"] == "passed"
    path = directory / "zizmor.json"

    if corruption == "missing":
        path.unlink()
    elif corruption == "malformed":
        path.write_text("[]", encoding="utf-8")
    else:
        relative = ".github/workflows/control.yml"
        if corruption.startswith("finding-") or corruption == "passed-with-finding":
            if corruption != "passed-with-finding":
                report["status"] = "failed"
            report["findings"] = [{
                "path": relative, "line": 1, "column": 1, "rule": "template-injection",
                "severity": "high", "confidence": "high",
            }]
        if corruption == "schema-version":
            report["schema_version"] = 2
        elif corruption == "report-extra":
            report["native_output"] = []
        elif corruption == "unselected":
            report["files"].append({"path": ".github/workflows/outside.yml", "sha256": "0" * 64})
        elif corruption == "duplicate":
            report["files"].append(dict(report["files"][0]))
        elif corruption == "incomplete":
            report["files"].pop()
        elif corruption == "source-hash":
            report["files"][0]["sha256"] = "0" * 64
        elif corruption == "manifest-hash":
            report["effective_input_sha256"] = "0" * 64
        elif corruption == "file-extra":
            report["files"][0]["preview"] = "not publishable"
        elif corruption == "status-contradiction":
            report["status"] = "failed"
        elif corruption == "finding-extra":
            report["findings"][0]["feature"] = "not publishable"
        elif corruption == "finding-incomplete":
            report["findings"][0].pop("confidence")
        elif corruption == "finding-unselected":
            report["findings"][0]["path"] = ".github/workflows/outside.yml"
        elif corruption == "finding-line":
            report["findings"][0]["line"] = 0
        elif corruption == "finding-column":
            report["findings"][0]["column"] = False
        elif corruption == "finding-rule":
            report["findings"][0]["rule"] = ""
        elif corruption == "finding-severity":
            report["findings"][0]["severity"] = "impossible"
        elif corruption == "finding-confidence":
            report["findings"][0]["confidence"] = "impossible"
        elif corruption == "policy-drift":
            policy = root / "contracts/zizmor.yml"
            policy.write_text(policy.read_text(encoding="utf-8") + "\n# changed after capture\n", encoding="utf-8")
        path.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises((OSError, ValueError)):
        bind_static_report("zizmor", directory=directory, manifest=manifest)
