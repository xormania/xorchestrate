"""Security checks are selectable, candidate-complete, and safe to publish."""

import json

import pytest

from scripts.testkit import __main__ as cli
from scripts.testkit import composition
from scripts.testkit.adapters.static import bind_static_report
from tests.hexagonal.support.security_checks import (
    empty_security_subject,
    prepare_secret_scan,
    run_secret_scan,
    secret_scan_runner,
    security_subject,
)
from tests.hexagonal.support.static_checks import write_source


def _assert_complete_file_evidence(report, manifest):
    expected = {name: manifest["files"][name]["content_sha256"] for name in manifest["sources"]}
    observed = {item["path"]: item["sha256"] for item in report["files"]}
    assert len(report["files"]) == len(observed), "the report must not repeat a selected input"
    assert observed == expected, "the report must bind every selected path to its captured bytes"
    assert report["effective_input_sha256"] == manifest["content_sha256"]


def _github_token(character="A"):
    # Keep the planted token out of this repository's own source selection.
    return "gh" + "p_" + character * 36


HIGH_SIGNAL_CREDENTIALS = (
    pytest.param("-----BEGIN " + "OPENSSH PRIVATE KEY-----", id="private-key"),
    pytest.param("AK" + "IA" + "A1" * 8, id="aws-access-key"),
    pytest.param(_github_token(), id="github-token"),
    pytest.param("xo" + "xb-" + "A1" * 5, id="slack-token"),
    pytest.param("AI" + "za" + "A" * 35, id="google-api-key"),
)


def test_cli_accepts_the_security_suite(tmp_path, monkeypatch, capsys):
    assert "security" in composition.SUITES, "security suite must be exposed through the public CLI choices"
    observed = {}

    def passing_preflight(**options):
        observed.update(options)
        return {"status": "passed", "checks": []}

    monkeypatch.setattr(cli, "preflight", passing_preflight)
    assert cli.main(["doctor", "--suite", "security", "--output", str(tmp_path / "runs")]) == 0
    assert observed["suite"] == "security"
    assert json.loads(capsys.readouterr().out)["status"] == "passed"


def test_security_and_static_plans_select_the_declared_checks_once(tmp_path):
    options = {"directory": tmp_path, "mode": "protocol-fixture", "check_timeout": 37}
    assert [check.name for check in composition.plan(suite="security", **options)] == [
        "ruff", "secret-scan", "zizmor", "dependency-audit",
    ]
    assert [check.name for check in composition.plan(suite="static", **options)] == [
        "ruff", "syntax", "architecture", "secret-scan", "zizmor",
    ]

    for suite in ("checks", "all"):
        names = [check.name for check in composition.plan(suite=suite, **options)]
        assert len(names) == len(set(names)), f"{suite} must execute each planned check once"
        assert names.count("ruff") == 1
        assert names.count("secret-scan") == 1
        assert names.count("zizmor") == 1
        assert names.count("dependency-audit") == 0


def test_clean_whole_candidate_is_a_passing_complete_control(tmp_path):
    root = security_subject(tmp_path / "candidate")
    directory = tmp_path / "evidence"

    result, report, manifest, _ = run_secret_scan(root, directory, tmp_path)

    assert result.exit == 0, result.stderr + result.stdout
    assert report["schema_version"] == 1 and report["status"] == "passed"
    assert report["findings"] == []
    assert {"docs/security-control.txt", "scratch/untracked-security.bin"} <= set(manifest["sources"])
    assert manifest["scope"]["configuration"] == ["contracts/security.json"]
    assert manifest["files"]["contracts/security.json"]["content_sha256"]
    _assert_complete_file_evidence(report, manifest)
    assert bind_static_report("secret-scan", directory=directory, manifest=manifest)["status"] == "passed"


@pytest.mark.parametrize("canary", HIGH_SIGNAL_CREDENTIALS)
def test_secret_scan_detects_high_signal_credentials_without_republishing_them(tmp_path, canary):
    root = security_subject(tmp_path / "candidate")
    relative = "scratch/untracked-security.bin"
    (root / relative).write_bytes(b"unrelated prefix\n" + canary.encode() + b"\ninvalid utf8: \xff\n")
    directory = tmp_path / "evidence"

    result, report, manifest, _ = run_secret_scan(root, directory, tmp_path)

    assert result.exit != 0, "a planted high-signal credential must fail the secret scan"
    assert report["schema_version"] == 1 and report["status"] == "failed"
    assert len(report["findings"]) == 1
    finding = report["findings"][0]
    assert set(finding) == {"path", "line", "rule"}
    assert finding["path"] == relative and finding["line"] == 2
    assert isinstance(finding["rule"], str) and finding["rule"]
    _assert_complete_file_evidence(report, manifest)
    assert bind_static_report("secret-scan", directory=directory, manifest=manifest)["status"] == "failed"
    assert canary not in (directory / "secret-scan.json").read_text(encoding="utf-8")
    assert canary not in result.stdout
    assert canary not in result.stderr


def test_allow_marker_applies_only_to_its_own_line(tmp_path):
    root = security_subject(tmp_path / "candidate")
    relative = "docs/security-control.txt"
    allowed, blocked = _github_token("A"), _github_token("B")
    write_source(root, relative, f'ALLOWED = "{allowed}"  # secret-scan: allow\n')
    allowed_directory = tmp_path / "allowed-evidence"

    result, report, manifest, _ = run_secret_scan(root, allowed_directory, tmp_path)

    assert result.exit == 0, result.stderr + result.stdout
    assert report["status"] == "passed" and report["findings"] == []
    _assert_complete_file_evidence(report, manifest)
    assert allowed not in result.stdout + result.stderr + json.dumps(report)

    write_source(root, relative, f'ALLOWED = "{allowed}"  # secret-scan: allow\nBLOCKED = "{blocked}"\n')
    blocked_directory = tmp_path / "blocked-evidence"
    result, report, manifest, _ = run_secret_scan(root, blocked_directory, tmp_path)

    assert result.exit != 0
    assert report["findings"] == [{"path": relative, "line": 2, "rule": report["findings"][0]["rule"]}]
    assert allowed not in result.stdout + result.stderr + json.dumps(report)
    assert blocked not in result.stdout + result.stderr + json.dumps(report)
    _assert_complete_file_evidence(report, manifest)


def test_secret_scan_refuses_a_candidate_with_no_selected_files(tmp_path):
    root = empty_security_subject(tmp_path / "empty-candidate")
    directory = tmp_path / "evidence"
    runner = secret_scan_runner(directory)

    with pytest.raises(ValueError, match="(?i)(no files|empty)"):
        prepare_secret_scan(root, directory, tmp_path, runner=runner)
    assert runner.records == [], "empty selection must be refused before launching the scanner"


@pytest.mark.parametrize("corruption", [
    "missing", "malformed", "unselected", "incomplete", "source-hash", "manifest-hash", "file-extra",
    "finding-extra", "finding-incomplete", "finding-unselected",
])
def test_secret_scan_success_requires_complete_bound_structured_evidence(tmp_path, corruption):
    root = security_subject(tmp_path / "candidate")
    if corruption.startswith("finding-"):
        write_source(root, "docs/security-control.txt", _github_token() + "\n")
    directory = tmp_path / "evidence"
    result, report, manifest, _ = run_secret_scan(root, directory, tmp_path)
    expected_exit = 1 if corruption.startswith("finding-") else 0
    assert result.exit == expected_exit, result.stderr + result.stdout
    path = directory / "secret-scan.json"

    if corruption == "missing":
        path.unlink()
    elif corruption == "malformed":
        path.write_text('{"schema_version": 1, "status": "passed"}', encoding="utf-8")
    else:
        if corruption == "unselected":
            report["files"].append({"path": "outside-selection.txt", "sha256": "0" * 64})
        elif corruption == "incomplete":
            report["files"].pop()
        elif corruption == "source-hash":
            report["files"][0]["sha256"] = "0" * 64
        elif corruption == "manifest-hash":
            report["effective_input_sha256"] = "0" * 64
        elif corruption == "file-extra":
            report["files"][0]["preview"] = "not publishable"
        elif corruption == "finding-extra":
            report["findings"][0]["match"] = _github_token("Z")
        elif corruption == "finding-incomplete":
            report["findings"][0].pop("rule")
        else:
            report["findings"][0]["path"] = "outside-selection.txt"
        path.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises((OSError, ValueError)):
        bind_static_report("secret-scan", directory=directory, manifest=manifest)
