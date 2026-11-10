"""The CI handoff reports required execution honestly and keeps diagnostics advisory."""

import hashlib
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.ci import result as subject

MANDATORY = (
    "paths", "changes", "application-tests", "application", "verification",
    "tmux-acceptance", "architecture-status",
)
HEAVY = ("application-tests", "application", "verification", "tmux-acceptance")


def _needs(*, docs=False):
    needs = {name: {"result": "success", "outputs": {}} for name in MANDATORY}
    needs["paths"]["outputs"] = {"code": "false" if docs else "true"}
    needs["architecture"] = {"result": "failure", "outputs": {}}
    if docs:
        for name in HEAVY:
            needs[name]["result"] = "skipped"
    return needs


def test_code_handoff_requires_successful_execution_but_not_jev_advice():
    docs, errors = subject.job_verdict(_needs(), event="pull_request")
    assert docs is False
    assert errors == []


@pytest.mark.parametrize("job", MANDATORY)
@pytest.mark.parametrize("outcome", ("failure", "cancelled", "skipped"))
def test_any_required_job_without_success_prevents_a_code_handoff(job, outcome):
    needs = _needs()
    needs[job]["result"] = outcome
    _, errors = subject.job_verdict(needs, event="pull_request")
    assert errors, (job, outcome)


@pytest.mark.parametrize("job", MANDATORY)
def test_missing_required_job_is_incomplete_execution(job):
    needs = _needs()
    del needs[job]
    _, errors = subject.job_verdict(needs, event="pull_request")
    assert errors, job


def test_only_an_explicitly_classified_documentation_pull_request_allows_heavy_skips():
    docs, errors = subject.job_verdict(_needs(docs=True), event="pull_request")
    assert docs is True
    assert errors == []


@pytest.mark.parametrize("event", ("push", "workflow_dispatch"))
def test_integrated_or_manual_validation_cannot_borrow_documentation_skips(event):
    _, errors = subject.job_verdict(_needs(docs=True), event=event)
    assert errors


@pytest.mark.parametrize("code", (None, "", "unknown", False, True))
def test_missing_or_noncanonical_classification_cannot_waive_validation(code):
    needs = _needs(docs=True)
    needs["paths"]["outputs"]["code"] = code
    _, errors = subject.job_verdict(needs, event="pull_request")
    assert errors


@pytest.mark.parametrize("classifier_status", ("failure", "cancelled", "skipped"))
def test_unsuccessful_classifier_cannot_supply_a_documentation_waiver(classifier_status):
    needs = _needs(docs=True)
    needs["paths"]["result"] = classifier_status
    _, errors = subject.job_verdict(needs, event="pull_request")
    assert errors


@pytest.mark.parametrize("job", ("changes", "architecture-status"))
def test_documentation_handoff_still_needs_its_actual_static_result(job):
    needs = _needs(docs=True)
    needs[job]["result"] = "skipped"
    _, errors = subject.job_verdict(needs, event="pull_request")
    assert errors


def test_packaged_consumer_becomes_required_when_the_workflow_includes_it():
    needs = _needs()
    for state in (None, "failure", "cancelled", "skipped", "success"):
        current = deepcopy(needs)
        if state is not None:
            current["package"] = {"result": state, "outputs": {}}
        _, errors = subject.job_verdict(current, event="pull_request", package=True)
        assert bool(errors) is (state != "success"), state


def test_packaged_consumer_has_the_same_explicit_documentation_exemption():
    needs = _needs(docs=True)
    needs["package"] = {"result": "skipped", "outputs": {}}
    docs, errors = subject.job_verdict(needs, event="pull_request", package=True)
    assert docs is True
    assert errors == []


def _write(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document) + "\n")


def _receipt_set(root):
    catalog = {"id": "ci-control", "families": {"behavior": {
        "guarantee": "Two independent behavioral modules execute once.",
        "modules": {"tests/a.py": "contract", "tests/b.py": "adapter"},
    }}}
    catalog_hash = hashlib.sha256(json.dumps(catalog, sort_keys=True, separators=(",", ":"),
                                            allow_nan=False).encode()).hexdigest()
    workflow = {"jobs": {"application-tests": {"strategy": {"matrix": {"shards": [2], "shard": [1, 2]}}}}}
    for index, module in enumerate(("tests/a.py", "tests/b.py"), 1):
        directory = root / f"framework-tests-python-3.12-shard-{index}-of-2" / "run"
        inventory = {"schema_version": 1, "status": "passed", "exit_status": 0,
                     "catalog_id": catalog["id"], "catalog_sha256": catalog_hash,
                     "shard": {"index": index, "count": 2, "modules": [module]}, "selected": 1,
                     "cases": [{"id": module + "::test_observable_behavior", "path": module,
                                "family": "behavior", "selected": True, "outcome": "passed"}]}
        _write(directory / "inventory.json", inventory)
        _write(directory / "report.json", {
            "schema_version": 2, "suite": "tests", "shard": f"{index}/2", "status": "passed",
            "checks": [{"name": "tests", "status": "passed", "duration_seconds": 1.25,
                        "tool_report": {"sha256": hashlib.sha256((directory / "inventory.json").read_bytes()).hexdigest(),
                                        "selected": 1}}],
        })
        _write(directory / "candidate.json", {"commit": "a" * 40, "status": ""})
    return workflow, catalog


def test_complete_distinct_shards_reconcile_to_the_catalog_and_report_families(tmp_path):
    workflow, catalog = _receipt_set(tmp_path)
    rows, families = subject.shard_evidence(tmp_path, workflow, tested_sha="a" * 40, catalog=catalog)
    assert [row["shard"] for row in rows] == ["1/2", "2/2"]
    assert [row["selected"] for row in rows] == [1, 1]
    assert families == {"behavior": {"passed": 2}}


@pytest.mark.parametrize("defect", ("missing-inventory", "foreign-source", "foreign-catalog",
                                  "duplicate-shard", "unqualified-bytes", "wrong-selected-count", "failed-case"))
def test_shard_receipts_must_be_complete_qualified_and_from_the_tested_candidate(tmp_path, defect):
    workflow, catalog = _receipt_set(tmp_path)
    directory = tmp_path / "framework-tests-python-3.12-shard-2-of-2" / "run"
    if defect == "missing-inventory":
        (directory / "inventory.json").unlink()
    elif defect == "foreign-source":
        _write(directory / "candidate.json", {"commit": "b" * 40, "status": ""})
    else:
        inventory = json.loads((directory / "inventory.json").read_text())
        if defect == "foreign-catalog":
            inventory["catalog_sha256"] = "b" * 64
        elif defect == "duplicate-shard":
            inventory["shard"]["modules"] = ["tests/a.py"]
            inventory["cases"][0].update(id="tests/a.py::test_observable_behavior", path="tests/a.py")
        elif defect == "wrong-selected-count":
            inventory["selected"] = 2
        elif defect == "failed-case":
            inventory["cases"][0]["outcome"] = "failed"
        _write(directory / "inventory.json", inventory)
        if defect == "unqualified-bytes":
            with (directory / "inventory.json").open("a") as stream:
                stream.write(" ")
        else:
            report = json.loads((directory / "report.json").read_text())
            report["checks"][0]["tool_report"]["sha256"] = hashlib.sha256((directory / "inventory.json").read_bytes()).hexdigest()
            _write(directory / "report.json", report)
    with pytest.raises(ValueError):
        subject.shard_evidence(tmp_path, workflow, tested_sha="a" * 40, catalog=catalog)


@pytest.mark.parametrize("foreign", (False, True))
def test_documentation_static_receipt_belongs_to_the_tested_candidate(tmp_path, foreign):
    directory = tmp_path / "static-checks-python-3.12" / "run"
    _write(directory / "report.json", {"schema_version": 2, "suite": "static", "status": "passed"})
    _write(directory / "candidate.json", {"commit": ("b" if foreign else "a") * 40, "status": ""})
    result = subject.evaluate(_needs(docs=True), event="pull_request", source_sha="c" * 40,
                              tested_sha="a" * 40, workflow={"jobs": {}}, artifacts=tmp_path, catalog={})
    assert result["status"] == ("failed" if foreign else "passed")


@pytest.mark.parametrize("static_succeeded", (True, False))
def test_actual_cli_preserves_a_required_job_failure_with_healthy_receipts(tmp_path, static_succeeded):
    artifacts = tmp_path / "artifacts"
    directory = artifacts / "static-checks-python-3.12" / "run"
    _write(directory / "report.json", {"schema_version": 2, "suite": "static", "status": "passed"})
    _write(directory / "candidate.json", {"commit": "a" * 40, "status": ""})
    needs = _needs(docs=True)
    if not static_succeeded:
        needs["changes"]["result"] = "failure"
    for name, value in (("needs", needs), ("workflow", {"jobs": {}}), ("catalog", {})):
        _write(tmp_path / (name + ".json"), value)
    summary = tmp_path / "summary.md"
    output = tmp_path / "result.json"
    result = subprocess.run([
        sys.executable, "-I", "-B", str(Path(subject.__file__).resolve()),
        "--needs", str(tmp_path / "needs.json"), "--workflow", str(tmp_path / "workflow.json"),
        "--catalog", str(tmp_path / "catalog.json"), "--artifacts", str(artifacts),
        "--summary", str(summary), "--output", str(output), "--event", "pull_request",
        "--source-sha", "c" * 40, "--tested-sha", "a" * 40,
        "--run-url", "https://example.invalid/run/1",
    ], cwd=tmp_path, check=False, capture_output=True, text=True, timeout=30)
    report = json.loads(output.read_text())
    assert result.returncode == (0 if static_succeeded else 1), result.stderr
    assert report["status"] == ("passed" if static_succeeded else "failed")
    assert report["source_sha"] == "c" * 40 and report["tested_sha"] == "a" * 40
    if not static_succeeded:
        assert any("Required job changes did not pass: failure" in error for error in report["errors"])
        assert "Required job changes did not pass: failure" in summary.read_text()


def _complete_receipts(root):
    workflow, catalog = _receipt_set(root)
    workflow["jobs"]["package"] = {}
    needs = _needs()
    needs["package"] = {"result": "success", "outputs": {}}
    artifacts = (
        ("static-checks-python-3.12", ("static",)),
        ("calibration-python-3.12", ("calibration",)),
        ("composed-protocol-evidence", ("multi-harness",)),
        ("tmux-acceptance-evidence", ("acceptance", "stories")),
    )
    for name, suites in artifacts:
        for suite in suites:
            directory = root / name / suite
            _write(directory / "report.json", {"schema_version": 2, "suite": suite, "status": "passed"})
            _write(directory / "candidate.json", {"commit": "a" * 40, "status": ""})
    _write(root / "package-acceptance-evidence/run/report.json", {
        "schema_version": 1, "status": "passed", "candidate": {"commit": "a" * 40, "status": ""},
    })
    return needs, workflow, catalog


def _evaluate_complete(root, needs, workflow, catalog):
    return subject.evaluate(needs, event="pull_request", source_sha="c" * 40, tested_sha="a" * 40,
                            workflow=workflow, artifacts=root, catalog=catalog)


def test_full_producer_shapes_with_clean_candidates_remain_a_valid_handoff(tmp_path):
    needs, workflow, catalog = _complete_receipts(tmp_path)
    result = _evaluate_complete(tmp_path, needs, workflow, catalog)
    assert result["status"] == "passed", result["errors"]
    assert {row["suite"] for row in result["suites"]} == {
        "static", "calibration", "multi-harness", "acceptance", "stories", "installed-package",
    }


@pytest.mark.parametrize("evidence", ("suite", "shard", "package"))
@pytest.mark.parametrize("dirty_status", (None, " M xorchestrate/application.py"))
def test_matching_commit_cannot_launder_dirty_or_unspecified_working_inputs(tmp_path, evidence, dirty_status):
    needs, workflow, catalog = _complete_receipts(tmp_path)
    if evidence == "package":
        path = tmp_path / "package-acceptance-evidence/run/report.json"
        document = json.loads(path.read_text())
        candidate = document["candidate"]
    else:
        path = tmp_path / ("static-checks-python-3.12/static/candidate.json" if evidence == "suite" else
                           "framework-tests-python-3.12-shard-2-of-2/run/candidate.json")
        document = json.loads(path.read_text())
        candidate = document
    if dirty_status is None:
        del candidate["status"]
    else:
        candidate["status"] = dirty_status
    _write(path, document)
    result = _evaluate_complete(tmp_path, needs, workflow, catalog)
    assert result["status"] == "failed", (evidence, dirty_status)
    assert any("clean" in error or "identity" in error for error in result["errors"]), result["errors"]


@pytest.mark.parametrize("schema", (None, 2, True))
def test_package_evidence_uses_its_known_native_schema(tmp_path, schema):
    needs, workflow, catalog = _complete_receipts(tmp_path)
    path = tmp_path / "package-acceptance-evidence/run/report.json"
    report = json.loads(path.read_text())
    if schema is None:
        del report["schema_version"]
    else:
        report["schema_version"] = schema
    _write(path, report)
    result = _evaluate_complete(tmp_path, needs, workflow, catalog)
    assert result["status"] == "failed", schema
    assert any("Installed-package" in error for error in result["errors"]), result["errors"]
