"""The CI handoff reports required execution honestly and keeps diagnostics advisory."""

import hashlib
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.ci import result as subject
from tests.hexagonal.support.ci_candidate import (
    artifact_zip,
    candidate_repository,
    github_observation,
    strict_documentation_receipts,
)

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
    repository = candidate_repository(tmp_path / "repository")
    event, env = github_observation(repository)
    context = subject.candidate_evidence.capture_context(repository["root"], event, env)
    artifacts = tmp_path / "artifacts"
    context, metadata, needs, workflow = strict_documentation_receipts(artifacts, context)
    if not static_succeeded:
        needs["changes"]["result"] = "failure"
    for name, value in (("needs", needs), ("workflow", workflow), ("catalog", {}),
                        ("context", context), ("metadata", metadata)):
        _write(tmp_path / (name + ".json"), value)
    summary = tmp_path / "summary.md"
    output = tmp_path / "result.json"
    result = subprocess.run([
        sys.executable, "-I", "-B", str(Path(subject.__file__).resolve()),
        "--needs", str(tmp_path / "needs.json"), "--workflow", str(tmp_path / "workflow.json"),
        "--catalog", str(tmp_path / "catalog.json"), "--artifacts", str(artifacts),
        "--summary", str(summary), "--output", str(output), "--event", "pull_request",
        "--source-sha", context["source_sha"], "--tested-sha", context["checkout"]["commit"],
        "--context", str(tmp_path / "context.json"), "--metadata", str(tmp_path / "metadata.json"),
        "--run-url", "https://example.invalid/run/1",
    ], cwd=tmp_path, check=False, capture_output=True, text=True, timeout=30)
    report = json.loads(output.read_text())
    assert result.returncode == (0 if static_succeeded else 1), result.stderr
    assert report["status"] == ("passed" if static_succeeded else "failed")
    assert report["source_sha"] == context["source_sha"]
    assert report["tested_sha"] == context["checkout"]["commit"]
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


def _jev_report(*, status="complete"):
    binding = {"source_sha256": "1" * 64, "contract_sha256": "2" * 64}
    diagnostic = {
        "provider": "jev", "status": status, "subject": deepcopy(binding),
        "context_sha256": "3" * 64, "model": "jev-fixture",
        "source_scope": {"selected": ["src/core.py"], "omitted": ["src/adapter.py"],
                         "truncated": ["src/core.py"], "max_source_bytes": 24000, "source_bytes": 3000},
        "assessments": [{"check_id": "evidence_coverage", "question": "Does this span violate its contract?",
                         "outcome": "concern", "confidence": 0.72, "rationale": "", "evidence": [],
                         "details": {"model": "jev-fixture", "probabilities": {
                             "concern": 0.81, "no_concern": 0.09, "insufficient_evidence": 0.1},
                             "usage": {"input_tokens": 19000, "output_tokens": 48}}}],
    }
    if status != "complete":
        diagnostic.update(reason="fixture_provider_unavailable", assessments=[])
    return {"schema_version": 1, "status": "pass", "subject": binding,
            "execution": {"git_commit": "a" * 40, "git_status": ""}, "diagnostics": diagnostic}


@pytest.mark.parametrize("required_passed", (True, False))
def test_jev_observations_are_visible_without_changing_required_qualification(tmp_path, required_passed):
    needs, workflow, catalog = _complete_receipts(tmp_path)
    if not required_passed:
        needs["changes"]["result"] = "failure"
    _write(tmp_path / "architecture-evidence/diagnostics.json", _jev_report())
    result = _evaluate_complete(tmp_path, needs, workflow, catalog)
    rendered = subject.markdown(result, "https://example.invalid/run/1")
    assert "evidence_coverage" in rendered and "concern" in rendered
    assert "0.72" in rendered and "0.81" in rendered
    assert "jev-fixture" in rendered
    assert "src/core.py" in rendered and "src/adapter.py" in rendered
    assert "truncated" in rendered.lower()
    assert "architecture-evidence" in rendered and "https://example.invalid/run/1" in rendered
    assert result.get("jev", {}).get("current") is True
    assert result["status"] == ("passed" if required_passed else "failed")
    assert bool(result["errors"]) is not required_passed


@pytest.mark.parametrize("status", ("unavailable", "error", "inconclusive", "not_configured"))
def test_current_jev_provider_gaps_remain_explicit_advice(tmp_path, status):
    needs, workflow, catalog = _complete_receipts(tmp_path)
    _write(tmp_path / "architecture-evidence/diagnostics.json", _jev_report(status=status))
    result = _evaluate_complete(tmp_path, needs, workflow, catalog)
    assert result.get("jev", {}).get("status") == status
    rendered = subject.markdown(result, "https://example.invalid/run/1")
    assert status in rendered and "fixture_provider_unavailable" in rendered
    assert result["status"] == "passed" and result["errors"] == []


@pytest.mark.parametrize("fault, expected", (
    ("missing", "missing"), ("foreign", "foreign"), ("dirty", "foreign"),
    ("unknown_worktree", "foreign"), ("different_subject", "foreign"),
    ("invalid_hash", "invalid"), ("bad_confidence", "invalid"),
    ("bad_distribution", "invalid"), ("unknown_outcome", "invalid"),
    ("malformed_json", "invalid"), ("duplicate", "invalid"),
    ("missing_schema", "invalid"), ("boolean_schema", "invalid"),
    ("unknown_schema", "invalid"), ("deep_json", "invalid"),
    ("huge_confidence", "invalid"), ("huge_probability", "invalid"),
))
def test_jev_missing_foreign_and_invalid_artifacts_cannot_look_current(tmp_path, fault, expected):
    needs, workflow, catalog = _complete_receipts(tmp_path)
    document = _jev_report()
    if fault == "foreign":
        document["execution"]["git_commit"] = "b" * 40
    elif fault == "dirty":
        document["execution"]["git_status"] = " M src/core.py"
    elif fault == "unknown_worktree":
        del document["execution"]["git_status"]
    elif fault == "different_subject":
        document["diagnostics"]["subject"]["source_sha256"] = "f" * 64
    elif fault == "invalid_hash":
        document["diagnostics"]["context_sha256"] = "not-a-digest"
    elif fault == "bad_confidence":
        document["diagnostics"]["assessments"][0]["confidence"] = 2
    elif fault == "bad_distribution":
        document["diagnostics"]["assessments"][0]["details"]["probabilities"]["concern"] = 0.01
    elif fault == "unknown_outcome":
        document["diagnostics"]["assessments"][0]["outcome"] = "all-clear"
    elif fault == "missing_schema":
        del document["schema_version"]
    elif fault == "boolean_schema":
        document["schema_version"] = True
    elif fault == "unknown_schema":
        document["schema_version"] = 2
    elif fault == "huge_confidence":
        document["diagnostics"]["assessments"][0]["confidence"] = 10 ** 400
    elif fault == "huge_probability":
        document["diagnostics"]["assessments"][0]["details"]["probabilities"]["concern"] = 10 ** 400
    if fault != "missing":
        path = tmp_path / "architecture-evidence/diagnostics.json"
        _write(path, document)
        if fault == "malformed_json":
            path.write_text("{broken JSON")
        elif fault == "deep_json":
            path.write_text("[" * 5000 + "0" + "]" * 5000)
        elif fault == "duplicate":
            _write(path.parent / "nested/diagnostics.json", document)
    result = _evaluate_complete(tmp_path, needs, workflow, catalog)
    assert result.get("jev", {}).get("status") == expected
    assert result.get("jev", {}).get("current") is False
    rendered = subject.markdown(result, "https://example.invalid/run/1")
    assert expected in rendered
    assert "evidence_coverage" not in rendered
    assert result["status"] == "passed" and result["errors"] == []


def test_ci_summary_waits_for_advisory_artifact_without_requiring_jev_success():
    workflow = json.loads((Path(__file__).parents[2] / ".github/workflows/ci.yml").read_text())
    aggregate = workflow["jobs"]["ci-result"]
    assert "architecture" in aggregate["needs"]
    assert aggregate["if"] == "${{ always() }}"
    docs, errors = subject.job_verdict(_needs(), event="pull_request")
    assert docs is False and errors == []


def test_ci_advisory_link_is_exported_from_the_actual_artifact_upload():
    workflow = json.loads((Path(__file__).parents[2] / ".github/workflows/ci.yml").read_text())
    diagnostic = workflow["jobs"]["architecture"]
    upload = next(step for step in diagnostic["steps"]
                  if step.get("with", {}).get("name", "").startswith("architecture-evidence--attempt-"))
    assert upload.get("id"), "The retained upload must expose its own artifact URL"
    assert diagnostic.get("outputs", {}).get("artifact_url") == "${{ steps." + upload["id"] + ".outputs.artifact-url }}"


@pytest.mark.parametrize("advice_fault", ("http", "archive"))
def test_optional_advisory_transfer_cannot_fail_required_evidence_download(tmp_path, monkeypatch, advice_fault):
    payload = artifact_zip([("report.json", b'{"status":"passed"}', "file")])
    digest = "sha256:" + hashlib.sha256(payload).hexdigest()
    lane = "static-checks-python-3.12"
    context = {"repository": {"full_name": "xormania/xorchestrate"}, "producer": {"run_attempt": 1}}
    metadata = {"jobs": [{"name": "Static checks", "run_attempt": 1}], "artifacts": [
        {"id": 1, "name": lane + "--attempt-1", "digest": digest},
        {"id": 2, "name": "architecture-evidence--attempt-1", "digest": digest},
    ]}

    def respond(url, token, limit):
        assert token == "inert-canary"
        if url.endswith("/2/zip"):
            if advice_fault == "http":
                raise OSError("controlled optional download failure")
            return b"invalid archive bytes"
        return payload

    monkeypatch.setattr(subject.candidate_evidence, "api_bytes", respond)
    subject.candidate_evidence.download_evidence(context, metadata, "inert-canary", tmp_path / "healthy",
                                                {lane: "Static checks"})
    assert (tmp_path / "healthy" / (lane + "--attempt-1") / "report.json").is_file()

    def unavailable(*args, **kwargs):
        raise OSError("controlled required download failure")

    monkeypatch.setattr(subject.candidate_evidence, "api_bytes", unavailable)
    with pytest.raises(OSError):
        subject.candidate_evidence.download_evidence(context, metadata, "inert-canary", tmp_path / "blocked",
                                                    {lane: "Static checks"})


@pytest.mark.parametrize("dirty", (False, True))
def test_architecture_producer_retains_actual_working_tree_identity(tmp_path, dirty):
    from scripts.architecture.__main__ import _identity

    def git(*arguments):
        return subprocess.run(["git", *arguments], cwd=tmp_path, check=True, capture_output=True, text=True).stdout.strip()

    git("init", "-q")
    git("config", "user.name", "fixture")
    git("config", "user.email", "fixture@example.invalid")
    (tmp_path / "source.py").write_text("VALUE = 1\n")
    git("add", "source.py")
    git("commit", "-qm", "fixture")
    expected_commit = git("rev-parse", "HEAD")
    if dirty:
        (tmp_path / "source.py").write_text("VALUE = 2\n")
    receipt = _identity(tmp_path)
    assert receipt.get("git_status") == (" M source.py\n" if dirty else "")
    assert receipt["git_commit"] == expected_commit


@pytest.mark.parametrize("scenario", ("single_fresh", "batch_fresh", "cache_hit", "invalid_usage"))
def test_advisory_execution_distinguishes_new_provider_usage_from_reused_history(tmp_path, scenario):
    needs, workflow, catalog = _complete_receipts(tmp_path)
    document = _jev_report()
    sample = document["diagnostics"]["assessments"][0]
    sample["details"]["usage"] = {"input_tokens": 20, "output_tokens": 3}
    document["diagnostics"]["assessments"] = [
        {**deepcopy(sample), "check_id": name}
        for name in ("evidence_coverage", "semantic_boundaries", "port_abstraction")
    ]
    hit = scenario == "cache_hit"
    metadata = {
        "request_sha256": "4" * 64, "mode": "single" if scenario == "single_fresh" else "batch",
        "cache_status": "hit" if hit else "disabled" if scenario == "single_fresh" else "miss",
        "provider_calls": 0 if hit else 3 if scenario == "single_fresh" else 1,
        "reused_assessments": 3 if hit else 0,
        "fresh_usage": {"input_tokens": 0 if hit else 60, "output_tokens": 0 if hit else 9},
        "reused_usage": {"input_tokens": 60, "output_tokens": 9} if hit else None,
    }
    if scenario == "invalid_usage":
        metadata["fresh_usage"]["input_tokens"] = -1
    document["diagnostics"]["execution"] = metadata
    _write(tmp_path / "architecture-evidence/diagnostics.json", document)
    result = _evaluate_complete(tmp_path, needs, workflow, catalog)
    assert result["status"] == "passed" and result["errors"] == []
    if scenario == "invalid_usage":
        assert result.get("jev", {}).get("status") == "invalid"
        assert result["jev"]["current"] is False
        return
    assert result.get("jev", {}).get("execution") == metadata
    rendered = subject.markdown(result, "https://example.invalid/run/1")
    assert f"Assessment execution: {metadata['mode']}; cache {metadata['cache_status']}" in rendered
    assert f"provider calls {metadata['provider_calls']}; reused assessments {metadata['reused_assessments']}" in rendered
    fresh = metadata["fresh_usage"]
    assert f"Fresh provider usage: {fresh['input_tokens']} input / {fresh['output_tokens']} output tokens" in rendered
    assert ("Historical reused usage: 60 input / 9 output tokens" if hit else "Historical reused usage: unknown") in rendered


@pytest.mark.parametrize("explicit_task_ids", (True, False))
def test_grounded_candidate_reports_keep_distinct_claims_and_source_locations(tmp_path, explicit_task_ids):
    needs, workflow, catalog = _complete_receipts(tmp_path)
    document = _jev_report()
    first = document["diagnostics"]["assessments"][0]
    first.update(candidate_id="first-source-unit", path="src/core.py", start_line=10, end_line=15,
                 claim="The first unit supports a semantic contract concern.", contract={"role": "core"})
    second = deepcopy(first)
    second.update(candidate_id="second-source-unit", start_line=20, end_line=25,
                  claim="The second unit supports a semantic contract concern.", outcome="no_concern")
    if explicit_task_ids:
        for item in (first, second):
            item["task_id"] = item["candidate_id"] + ":" + item["check_id"]
    document["diagnostics"]["assessments"] = [first, second]
    path = tmp_path / "architecture-evidence/diagnostics.json"
    _write(path, document)
    result = _evaluate_complete(tmp_path, needs, workflow, catalog)
    assert result.get("jev", {}).get("status") == "complete"
    assert len(result["jev"]["assessments"]) == 2
    rendered = subject.markdown(result, "https://example.invalid/run/1")
    assert "src/core.py:10-15" in rendered and "src/core.py:20-25" in rendered
    assert "first-source-unit" in rendered and "second-source-unit" in rendered
    assert result["status"] == "passed" and result["errors"] == []
    document["diagnostics"]["assessments"].append(deepcopy(first))
    _write(path, document)
    duplicate = _evaluate_complete(tmp_path, needs, workflow, catalog)
    assert duplicate["jev"]["status"] == "invalid" and duplicate["jev"]["current"] is False
    assert duplicate["status"] == "passed" and duplicate["errors"] == []
    _write(path, _jev_report())
    legacy = _evaluate_complete(tmp_path, needs, workflow, catalog)
    assert legacy["jev"]["status"] == "complete"


def test_ci_diagnostic_context_uses_the_actual_change_base():
    workflow = json.loads((Path(__file__).parents[2] / ".github/workflows/ci.yml").read_text())
    diagnostic = workflow["jobs"]["architecture"]
    checkout = next(step for step in diagnostic["steps"] if step.get("uses", "").startswith("actions/checkout@"))
    assert checkout.get("with", {}).get("fetch-depth") == 0
    command = next(step for step in diagnostic["steps"] if step.get("name") == "Jev architecture diagnostics")
    assert command.get("env", {}).get("JEV_BASE_REF") == "${{ github.event.pull_request.base.sha || github.event.before }}"
    assert '--base-ref "$jev_base_ref"' in command["run"]
    assert "git rev-parse" in command["run"] and "HEAD^" in command["run"]


@pytest.mark.parametrize("candidate_count", (11, 100))
def test_supported_grounded_task_populations_survive_advisory_reporting(tmp_path, candidate_count):
    from scripts.architecture.context import build_context
    from scripts.architecture.diagnostics import evaluation_tasks
    from scripts.architecture.python import check

    root = tmp_path / "measured"
    package = root / "pkg"
    package.mkdir(parents=True)
    source = "\n".join(f"def work_{index}(port):\n    return port.read({index})\n" for index in range(candidate_count))
    (package / "work.py").write_text(source)
    profile = {"schema_version": 1, "id": "task-capacity-control", "version": 1, "source_roots": ["pkg"],
               "roles": {"core": {"modules": ["pkg.*"], "allowed_roles": ["core"],
                                  "allowed_external": [], "allow_dynamic": False}}}
    report = check(root, profile)
    assert report["status"] == "pass"
    context = build_context(report, root, max_candidates=candidate_count, max_files=1)
    tasks = evaluation_tasks(context)
    assert len(tasks) == candidate_count * 3
    document = _jev_report()
    document["subject"] = deepcopy(report["subject"])
    diagnostic = document["diagnostics"]
    diagnostic.update(subject=deepcopy(report["subject"]), source_scope=context["source_scope"],
                      context_sha256=hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest(),
                      assessments=[{**task, "outcome": "no_concern", "confidence": 0.9, "details": {}}
                                   for task in tasks])
    needs, workflow, catalog = _complete_receipts(tmp_path)
    _write(tmp_path / "architecture-evidence/diagnostics.json", document)
    result = _evaluate_complete(tmp_path, needs, workflow, catalog)
    assert result.get("jev", {}).get("status") == "complete", "SUPPORTED_TASK_CAPACITY_VISIBLE"
    assert len(result["jev"]["assessments"]) == candidate_count * 3
    rendered = subject.markdown(result, "https://example.invalid/run/1")
    assert all(candidate["candidate_id"] in rendered for candidate in context["candidates"])
    assert result["status"] == "passed" and result["errors"] == []
