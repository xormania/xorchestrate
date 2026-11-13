"""Dependency auditing is a bound local observation and advisory hosted signal."""

import ast
import json
import os
import re
import shutil
import sys
import tomllib
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.testkit import composition
from scripts.testkit.adapters.evidence import RecordingRunner
from scripts.testkit.adapters.static import bind_static_report, definitions, prepare_static
from tests.hexagonal.support.dependency_audit import (
    ALIAS,
    DETAIL_CANARY,
    SUMMARY_CANARY,
    URL_CANARY,
    VULNERABILITY_ID,
    OsvService,
    audit_subject,
)
from tests.hexagonal.support.static_checks import ROOT

REPORT_FIELDS = {
    "schema_version", "status", "enforcement", "effective_input_sha256", "files",
    "scanner", "service", "selection", "packages", "findings", "limitations", "execution",
}
PACKAGE_FIELDS = {"name", "version", "source_kind", "source_identity", "match_basis"}
FINDING_FIELDS = {
    "kind", "id", "aliases", "package", "version", "fixed_versions", "published", "modified", "status",
}
LIMITATIONS = [
    "git-source-name-version-match",
    "mutable-osv-data",
    "short-response-completeness-unverified",
]


def _environment(tmp_path):
    return {
        "PATH": os.environ["PATH"],
        "PYTHONPATH": str(ROOT),
        "PYTHONPYCACHEPREFIX": str(tmp_path / "pycache"),
    }


def _audit_definition(directory, service_url):
    matches = [check for check in definitions(
        directory=directory, timeout=45, suites=("dependency-audit",),
    ) if check.name == "dependency-audit"]
    assert len(matches) == 1, "the shared static adapter must declare one dependency audit"
    check = matches[0]
    argv = list(check.argv)
    index = argv.index("--service-url")
    argv[index + 1] = service_url
    return replace(check, argv=tuple(argv))


def _run_audit(root, directory, tmp_path, service):
    directory.mkdir(parents=True, exist_ok=True)
    environment = _environment(tmp_path)
    runner = RecordingRunner(directory / "commands")
    prepared, manifest = prepare_static(
        _audit_definition(directory, service.url), root=root, directory=directory,
        runner=runner, env=environment,
    )
    result = runner.run(prepared.argv, cwd=str(root), env=environment, timeout=prepared.timeout)
    report = json.loads((directory / "dependency-audit.json").read_text(encoding="utf-8"))
    return result, report, manifest, runner


def _assert_safe_common_report(report, manifest, service):
    assert set(report) == REPORT_FIELDS
    assert report["schema_version"] == 1
    assert report["enforcement"] == "advisory"
    assert report["effective_input_sha256"] == manifest["content_sha256"]
    expected_files = [{
        "path": name, "sha256": manifest["files"][name]["content_sha256"],
    } for name in ("pyproject.toml", "uv.lock")]
    assert report["files"] == expected_files
    assert report["scanner"] == {"name": "uv", "version": "0.13.7", "output_schema": "preview"}
    assert report["service"] == {"format": "osv", "url": service.url}
    assert report["limitations"] == LIMITATIONS
    assert all(set(package) == PACKAGE_FIELDS for package in report["packages"])
    assert report["packages"] == sorted(report["packages"], key=lambda item: (item["name"], item["version"]))
    assert len(report["packages"]) == len({(item["name"], item["version"]) for item in report["packages"]})
    assert set(report["execution"]) == {"native_exit", "argv", "stdout", "stderr"}
    for stream in ("stdout", "stderr"):
        evidence = report["execution"][stream]
        assert set(evidence) == {"bytes", "sha256"}
        assert isinstance(evidence["bytes"], int) and evidence["bytes"] >= 0
        assert re.fullmatch(r"[0-9a-f]{64}", evidence["sha256"])


def _locked_auditable_packages(root):
    lock = tomllib.loads((Path(root) / "uv.lock").read_text(encoding="utf-8"))
    return sorted(
        (package["name"], package["version"])
        for package in lock["package"]
        if "editable" not in package["source"]
    )


def _locked_git_commit(root, name):
    lock = tomllib.loads((Path(root) / "uv.lock").read_text(encoding="utf-8"))
    package = next(item for item in lock["package"] if item["name"] == name)
    return package["source"]["git"].rsplit("#", 1)[1]


def test_dependency_audit_is_an_explicit_advisory_view_without_default_duplication(tmp_path):
    options = {"directory": tmp_path, "mode": "protocol-fixture", "check_timeout": 37}
    assert "dependency-audit" in composition.SUITES
    assert "dependency-audit" not in composition.COMPONENT_SUITES
    assert [check.name for check in composition.plan(suite="dependency-audit", **options)] == [
        "dependency-audit",
    ]
    assert [check.name for check in composition.plan(suite="security", **options)][-1] == "dependency-audit"
    for suite in ("static", "checks", "all"):
        names = [check.name for check in composition.plan(suite=suite, **options)]
        assert names.count("dependency-audit") == 0, f"{suite} must stay deterministic and offline"


def test_real_uv_clean_control_observes_every_locked_profile_once_without_claiming_completeness(
        tmp_path, monkeypatch):
    # Ambient uv policy must not silently narrow this declared universal observation.
    monkeypatch.setenv("UV_NO_DEV", "1")
    monkeypatch.setenv("UV_NO_DEFAULT_GROUPS", "1")
    root = audit_subject(tmp_path / "candidate")
    with OsvService("clean") as service:
        result, report, manifest, _ = _run_audit(root, tmp_path / "evidence", tmp_path, service)

    assert result.exit == 0, result.stderr + result.stdout
    assert report["status"] == "observed" and report["findings"] == []
    _assert_safe_common_report(report, manifest, service)
    assert manifest["sources"] == ["uv.lock"]
    assert manifest["scope"]["configuration"] == ["pyproject.toml"]
    assert manifest["tool"]["uv"] == "uv 0.13.7"
    queries = [(item["package"]["name"], item["version"]) for item in service.queries]
    packages = [(item["name"], item["version"]) for item in report["packages"]]
    expected = _locked_auditable_packages(root)
    assert queries == packages == expected, (
        "one universal invocation must audit every registry/Git lock package exactly once"
    )
    assert all(
        set(query) == {"package", "version"}
        and query["package"] == {"ecosystem": "PyPI", "name": query["package"]["name"]}
        for query in service.queries
    )
    assert service.paths == [("POST", "/v1/querybatch")]
    user_agent = next(value for name, value in service.headers[0].items()
                      if name.lower() == "user-agent")
    assert user_agent.startswith("uv/0.13.7 ")
    names = {name for name, _ in queries}
    assert {"jsonschema", "xcontracts", "pytest", "ruff", "zizmor"} <= names
    assert report["selection"] == {
        "groups": "all", "extras": "all", "target": "universal", "audited_packages": len(packages),
    }
    git = next(item for item in report["packages"] if item["name"] == "xcontracts")
    assert git == {
        "name": "xcontracts", "version": "0.1.0", "source_kind": "git",
        "source_identity": _locked_git_commit(root, "xcontracts"),
        "match_basis": "pypi-name-version",
    }
    executable = Path(shutil.which("uv", path=os.environ["PATH"])).resolve()
    argv = report["execution"]["argv"]
    assert Path(argv[0]).resolve() == executable
    assert argv[1:] == [
        "--preview-features", "audit-command,json-output", "audit", "--locked",
        "--output-format", "json", "--no-progress", "--no-python-downloads",
        "--service-format", "osv", "--service-url", service.url,
    ]
    assert report["execution"]["native_exit"] == 0
    assert bind_static_report(
        "dependency-audit", directory=tmp_path / "evidence", manifest=manifest,
    )["status"] == "passed"


def test_real_uv_detects_a_planted_locked_vulnerability_without_republishing_remote_text(
        tmp_path, monkeypatch):
    token_canary = "outer-github-token-canary"
    monkeypatch.setenv("GITHUB_TOKEN", token_canary)
    root = audit_subject(tmp_path / "candidate")
    with OsvService("vulnerable") as service:
        result, report, manifest, _ = _run_audit(root, tmp_path / "evidence", tmp_path, service)

    assert result.exit == 1, "the planted lock-package vulnerability must remain a concern outcome"
    assert report["status"] == "concern"
    assert report["execution"]["native_exit"] == 1
    _assert_safe_common_report(report, manifest, service)
    package = next(item for item in report["packages"] if item["name"] == "attrs")
    assert report["findings"] == [{
        "kind": "vulnerability",
        "id": VULNERABILITY_ID,
        "aliases": [ALIAS],
        "package": "attrs",
        "version": package["version"],
        "fixed_versions": ["99.0.0"],
        "published": "2026-01-01T00:00:00Z",
        "modified": "2026-01-02T00:00:00Z",
        "status": "affected",
    }]
    assert set(report["findings"][0]) == FINDING_FIELDS
    assert service.paths == [
        ("POST", "/v1/querybatch"), ("GET", "/v1/vulns/" + VULNERABILITY_ID),
    ]
    published = b"\n".join(
        path.read_bytes()
        for path in sorted((tmp_path / "evidence").rglob("*"))
        if path.is_file()
    )
    for canary in (SUMMARY_CANARY, DETAIL_CANARY, URL_CANARY, token_canary):
        assert canary.encode() not in published
        assert canary not in result.stdout
        assert canary not in result.stderr
        assert canary not in json.dumps(service.headers)
    assert bind_static_report(
        "dependency-audit", directory=tmp_path / "evidence", manifest=manifest,
    )["status"] == "failed"


@pytest.mark.parametrize(("mode", "expected_status"), [
    ("unavailable", "unavailable"),
    ("malformed", "error"),
])
def test_service_or_preview_schema_failure_is_never_a_clean_observation(
        tmp_path, mode, expected_status):
    root = audit_subject(tmp_path / "candidate")
    with OsvService(mode) as service:
        result, report, manifest, _ = _run_audit(root, tmp_path / "evidence", tmp_path, service)

    assert result.exit == 2
    assert report["status"] == expected_status
    assert report["execution"]["native_exit"] == 2
    assert report["status"] != "observed" and report["findings"] == []
    _assert_safe_common_report(report, manifest, service)
    assert bind_static_report(
        "dependency-audit", directory=tmp_path / "evidence", manifest=manifest,
    )["status"] == "failed"


@pytest.mark.parametrize("corruption", [
    "malformed", "extra", "manifest-hash", "source-hash", "incomplete-files",
    "scanner-version", "package-extra", "package-version", "finding-extra", "execution-extra",
    "native-exit", "status-contradiction", "lock-drift", "project-drift",
])
def test_dependency_observation_requires_exact_candidate_bound_evidence(tmp_path, corruption):
    root = audit_subject(tmp_path / "candidate")
    with OsvService("clean") as service:
        result, report, manifest, _ = _run_audit(root, tmp_path / "evidence", tmp_path, service)
    assert result.exit == 0
    assert bind_static_report(
        "dependency-audit", directory=tmp_path / "evidence", manifest=manifest,
    )["status"] == "passed"
    path = tmp_path / "evidence/dependency-audit.json"

    if corruption == "malformed":
        path.write_text("[]", encoding="utf-8")
    elif corruption == "lock-drift":
        (root / "uv.lock").write_text((root / "uv.lock").read_text() + "\n# drift\n", encoding="utf-8")
    elif corruption == "project-drift":
        path = root / "pyproject.toml"
        path.write_text(path.read_text() + "\n# drift\n", encoding="utf-8")
    else:
        if corruption == "extra":
            report["raw"] = {}
        elif corruption == "manifest-hash":
            report["effective_input_sha256"] = "0" * 64
        elif corruption == "source-hash":
            report["files"][0]["sha256"] = "0" * 64
        elif corruption == "incomplete-files":
            report["files"].pop()
        elif corruption == "scanner-version":
            report["scanner"]["version"] = "0.0.0"
        elif corruption == "package-extra":
            report["packages"][0]["summary"] = "unsafe"
        elif corruption == "package-version":
            report["packages"][0]["version"] = "0.0.0"
        elif corruption == "finding-extra":
            report["status"] = "concern"
            report["findings"] = [{
                "kind": "vulnerability", "id": VULNERABILITY_ID, "aliases": [],
                "package": report["packages"][0]["name"], "version": report["packages"][0]["version"],
                "fixed_versions": [], "published": None, "modified": None, "status": "affected",
                "description": DETAIL_CANARY,
            }]
        elif corruption == "execution-extra":
            report["execution"]["stdout"]["preview"] = "unsafe"
        elif corruption == "native-exit":
            report["execution"]["native_exit"] = 1
        else:
            report["status"] = "concern"
        path.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises((OSError, ValueError)):
        bind_static_report("dependency-audit", directory=tmp_path / "evidence", manifest=manifest)


def test_dependency_audit_refuses_an_installed_uv_version_that_disagrees_with_the_pin(tmp_path):
    root = audit_subject(tmp_path / "candidate")
    programs = tmp_path / "programs"
    programs.mkdir()
    executable = programs / "uv"
    executable.write_text(
        f"""#!{sys.executable}
import sys

if sys.argv[1:] == ["--version"]:
    print("uv 0.13.6 (fake-target)")
    raise SystemExit(0)
raise SystemExit("unexpected fake uv invocation")
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    directory = tmp_path / "evidence"
    directory.mkdir()
    runner = RecordingRunner(directory / "commands")
    environment = {**_environment(tmp_path), "PATH": str(programs)}

    with pytest.raises(ValueError, match="(?i)(pin|version|match)"):
        prepare_static(
            _audit_definition(directory, "http://127.0.0.1:9/"), root=root,
            directory=directory, runner=runner, env=environment,
        )
    assert len(runner.records) == 1, "version mismatch must stop before an OSV request"


def test_ci_runs_one_bounded_advisory_audit_and_uploads_separate_evidence():
    workflow = json.loads((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    assert list(workflow["jobs"]) == [
        "paths", "changes", "application-tests", "application", "verification",
        "tmux-acceptance", "architecture-status", "package", "ci-result",
    ]
    matches = []
    for job_name, job in workflow["jobs"].items():
        for step in job.get("steps", []):
            if "--suite dependency-audit" in step.get("run", ""):
                matches.append((job_name, step))
    assert len(matches) == 1
    job_name, step = matches[0]
    assert job_name == "changes", "the advisory observation must reuse the installed static environment"
    assert step.get("continue-on-error") is True
    assert step.get("timeout-minutes") == 2
    assert "--check-timeout 90" in step["run"]
    assert '--output "$RUNNER_TEMP/xor-dependency-audit"' in step["run"]
    static_step = next(item for item in workflow["jobs"][job_name]["steps"]
                       if "--suite static" in item.get("run", ""))
    assert step.get("if") == static_step.get("if")
    assert [item for item in workflow["jobs"][job_name]["steps"]
            if item.get("continue-on-error") is True] == [step]
    uploads = [item for item in workflow["jobs"][job_name]["steps"]
               if item.get("uses", "").startswith("actions/upload-artifact@")
               and "dependency-audit" in item.get("with", {}).get("name", "")]
    assert len(uploads) == 1
    assert uploads[0].get("if") == "always()"
    assert uploads[0]["with"].get("if-no-files-found") == "warn"
    assert "xor-dependency-audit" in uploads[0]["with"]["path"]
    static_upload = next(item for item in workflow["jobs"][job_name]["steps"]
                         if item.get("with", {}).get("name", "").startswith("static-checks-"))
    assert uploads[0]["with"]["name"] != static_upload["with"]["name"]
    assert "xor-static" not in uploads[0]["with"]["path"]
    assert all("--suite dependency-audit" not in json.dumps(job)
               for name, job in workflow["jobs"].items() if name != "changes")
    rendered = json.dumps(workflow)
    assert rendered.count("candidate.py lane --lane") == 6
    for lane in (
        "static-checks-python-3.12", "framework-tests-python-3.12-shard-${{ matrix.shard }}-of-${{ matrix.shards }}",
        "calibration-python-3.12", "composed-protocol-evidence", "tmux-acceptance-evidence",
        "package-acceptance-evidence",
    ):
        assert rendered.count("--lane " + lane) + rendered.count('--lane \\"' + lane + '\\"') == 1

    result_source = (ROOT / "scripts/ci/result.py").read_text(encoding="utf-8")
    assert "dependency-audit" not in result_source
    assignments = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in ast.parse(result_source).body
        if isinstance(node, ast.Assign) and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in {"ALWAYS", "HEAVY", "ARTIFACTS"}
    }
    assert assignments == {
        "ALWAYS": ("paths", "changes", "architecture-status"),
        "HEAVY": ("application-tests", "application", "verification", "tmux-acceptance"),
        "ARTIFACTS": {
            "static-checks-python-3.12": ("static",),
            "calibration-python-3.12": ("calibration",),
            "composed-protocol-evidence": ("multi-harness",),
            "tmux-acceptance-evidence": ("acceptance", "stories"),
        },
    }
    assert "dependency-audit" not in (ROOT / "scripts/ci/candidate.py").read_text(encoding="utf-8")
