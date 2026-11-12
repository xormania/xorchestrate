"""Independent real-HTTP batching and exact-request reuse contracts."""
from __future__ import annotations

import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.architecture.cache import run_tasks
from scripts.architecture.diagnostics import diagnose
from tests.hexagonal.support.architecture_context import measured_sources, source_report
from tests.hexagonal.support.jev_efficiency import CRITERIA, CountingEvaluator, http_choices, tasks
from xorchestrate.adapters.evaluation import TypeSafeEvaluator
from xorchestrate.model import OrchestrationError


def test_batch_http_preserves_question_identity_and_counts_request_usage_once():
    questions = {"semantic": "Inspect candidate A", "ports": "Inspect candidate B"}
    context = {"evidence": [{"path": "app/work.py", "source": "def work(port): return port.store()"}]}
    with http_choices() as provider:
        provider["choices"] = {"semantic": "concern", "ports": "insufficient_evidence"}
        answers = TypeSafeEvaluator(provider["options"]).assess_many(questions, context)
        assert {key: answer.outcome for key, answer in answers.items()} == provider["choices"], "BATCH_ANSWERS_BOUND"
        assert len(provider["requests"]) == 1, "BATCH_SINGLE_REQUEST"
        request = provider["requests"][0]
        assert request["state"] == context
        assert {key: row["instructions"] for key, row in request["questions"].items()} == questions
        assert all(row["criteria"] == CRITERIA for row in request["questions"].values())
        assert sum(answer.details.get("usage", {}).get("input_tokens", 0) for answer in answers.values()) == 13, "BATCH_USAGE_ONCE"
        assert all(answer.details["usage_scope"] == "batch" for answer in answers.values())
        assert all(answer.details["batch_question_ids"] == list(questions) for answer in answers.values())


@pytest.mark.parametrize("fault", ["missing", "extra", "model", "distribution"])
def test_invalid_batch_cannot_return_partial_or_foreign_answers(fault):
    questions = {"semantic": "Inspect candidate A", "ports": "Inspect candidate B"}
    answer = {"type": "choice", "choice": "concern", "confidence": .8,
              "probabilities": {"concern": 1, "no_concern": 0, "insufficient_evidence": 0}}
    response = {"model": "fixture-choice", "answers": {key: deepcopy(answer) for key in questions}}
    if fault == "missing":
        response["answers"].pop("ports")
    elif fault == "extra":
        response["answers"]["unrequested"] = deepcopy(answer)
    elif fault == "model":
        response["model"] = "foreign-model"
    else:
        response["answers"]["ports"]["probabilities"]["concern"] = .1
    with http_choices() as provider:
        provider["response"] = response
        code = None
        try:
            TypeSafeEvaluator(provider["options"]).assess_many(questions, {})
        except OrchestrationError as error:
            code = error.code
        assert code == "evaluation_invalid", "INVALID_BATCH_REJECTED"


@pytest.mark.parametrize("batch", [False, True])
def test_exact_cache_reuse_keeps_historical_usage_out_of_fresh_usage(tmp_path, batch):
    evaluator = CountingEvaluator()
    first, fresh = run_tasks(evaluator, tasks(), {"source": "measured"}, cache_dir=tmp_path, batch=batch)
    second, reused = run_tasks(evaluator, tasks(), {"source": "measured"}, cache_dir=tmp_path, batch=batch)
    expected_calls = 1 if batch else 2
    assert len(evaluator.calls) == expected_calls, "EXACT_REQUEST_REUSED"
    assert fresh["provider_calls"] == expected_calls
    assert fresh["fresh_usage"] == {"input_tokens": 13 * expected_calls, "output_tokens": 5 * expected_calls}, "FRESH_USAGE_ACTUAL_CALLS"
    assert reused["cache_status"] == "hit" and reused["provider_calls"] == 0
    assert reused["fresh_usage"] == {"input_tokens": 0, "output_tokens": 0}, "REUSE_HAS_NO_FRESH_USAGE"
    assert reused["reused_usage"] == fresh["fresh_usage"]
    assert [(item.outcome, item.confidence) for item in first] == [(item.outcome, item.confidence) for item in second]
    assert all("usage" not in item.details and item.details["cache_reused"] for item in second), "REUSED_USAGE_NOT_DOUBLE_COUNTED"


@pytest.mark.parametrize("dimension", ["context", "question", "task_id", "criteria", "model", "endpoint", "mode"])
def test_cache_identity_includes_every_semantic_request_dimension(tmp_path, dimension):
    evaluator, selected, context = CountingEvaluator(), tasks(), {"source": "original"}
    _, first = run_tasks(evaluator, selected, context, cache_dir=tmp_path)
    if dimension == "context":
        context["source"] = "changed"
    elif dimension == "question":
        selected[0]["question"] = "Different question about same source"
    elif dimension == "task_id":
        selected[0]["task_id"] = "different-candidate:semantic"
    elif dimension == "criteria":
        evaluator.criteria["concern"] = "Different declared criterion"
    elif dimension == "model":
        evaluator.model = "different-model"
    elif dimension == "endpoint":
        evaluator.endpoint = "https://other-fixture.invalid/choice"
    _, second = run_tasks(evaluator, selected, context, cache_dir=tmp_path, batch=dimension == "mode")
    assert second["request_sha256"] != first["request_sha256"], "CHANGED_REQUEST_IDENTITY"
    assert second["cache_status"] != "hit", "CHANGED_REQUEST_NOT_REUSED"
    assert len(evaluator.calls) == (3 if dimension == "mode" else 4)


@pytest.mark.parametrize("fault", ["corrupt", "resealed-invalid-choice", "expired"])
def test_unusable_cache_is_recomputed_without_trusting_stale_choices(tmp_path, monkeypatch, fault):
    evaluator = CountingEvaluator()
    monkeypatch.setattr("scripts.architecture.cache.time.time", lambda: 1000)
    run_tasks(evaluator, tasks(), {}, cache_dir=tmp_path, cache_ttl_seconds=1)
    path = next(tmp_path.glob("*.json"))
    if fault == "corrupt":
        path.write_text("invalid json")
    elif fault == "expired":
        monkeypatch.setattr("scripts.architecture.cache.time.time", lambda: 1002)
    else:
        entry = json.loads(path.read_text())
        entry["assessments"][0]["outcome"] = "invented"
        payload = json.dumps(entry["assessments"], sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        entry["results_sha256"] = hashlib.sha256(payload).hexdigest()
        path.write_text(json.dumps(entry))
    answers, execution = run_tasks(evaluator, tasks(), {}, cache_dir=tmp_path, cache_ttl_seconds=1)
    assert len(evaluator.calls) == 4 and execution["provider_calls"] == 2, "UNUSABLE_CACHE_RECOMPUTED"
    assert execution["cache_status"] == "unusable"
    assert all(answer.outcome == "concern" for answer in answers)


@pytest.mark.parametrize("fault", ["overflow-timestamp", "deep-json"])
def test_exceptional_cache_corruption_is_contained_and_recomputed(tmp_path, fault):
    evaluator = CountingEvaluator()
    run_tasks(evaluator, tasks(), {}, cache_dir=tmp_path)
    path = next(tmp_path.glob("*.json"))
    if fault == "overflow-timestamp":
        entry = json.loads(path.read_text())
        entry["created_at"] = 10 ** 400
        path.write_text(json.dumps(entry))
    else:
        depth = max(10000, sys.getrecursionlimit() + 1000)
        path.write_text("[" * depth + "0" + "]" * depth)
    escaped = None
    answers, execution = [], {}
    try:
        answers, execution = run_tasks(evaluator, tasks(), {}, cache_dir=tmp_path)
    except Exception as error:
        escaped = type(error).__name__
    assert escaped is None, "CACHE_CORRUPTION_CONTAINED"
    assert len(evaluator.calls) == 4 and execution["provider_calls"] == 2
    assert execution["cache_status"] == "unusable"
    assert all(answer.outcome == "concern" for answer in answers)


def test_failed_provider_attempt_is_never_cached(tmp_path):
    evaluator = CountingEvaluator()
    evaluator.error = True
    code = None
    try:
        run_tasks(evaluator, tasks(), {}, cache_dir=tmp_path)
    except OrchestrationError as error:
        code = error.code
    assert code == "evaluation_unavailable"
    assert list(tmp_path.glob("*.json")) == [], "FAILED_ATTEMPT_NOT_CACHED"
    evaluator.error = False
    _, execution = run_tasks(evaluator, tasks(), {}, cache_dir=tmp_path)
    assert execution["cache_status"] == "miss" and execution["provider_calls"] == 2
    assert len(evaluator.calls) == 3


def test_diagnose_cold_and_warm_http_requests_report_real_reuse(tmp_path):
    source_root, cache_root = tmp_path / "source", tmp_path / "cache"
    sources, line, _ = measured_sources(source_root)
    report = source_report(sources)
    report["status"] = "violation"
    with http_choices() as provider:
        options = {key: value for key, value in provider["options"].items() if key != "criteria"}
        options.update(cache_dir=str(cache_root), changed_ranges=[{"path": "pkg/work.py", "start_line": line + 2, "end_line": line + 2}])
        cold = diagnose(report, source_root, options)
        warm = diagnose(report, source_root, options)
        assert cold["status"] == warm["status"] == "complete"
        assert len(provider["requests"]) == 3, "DIAGNOSE_CACHE_PREVENTS_REPEAT_REQUESTS"
        assert cold["execution"]["provider_calls"] == 3 and warm["execution"]["provider_calls"] == 0
        assert warm["execution"]["cache_status"] == "hit"
        assert warm["execution"]["fresh_usage"] == {"input_tokens": 0, "output_tokens": 0}, "DIAGNOSE_REUSE_USAGE_ZERO"
        assert warm["execution"]["reused_usage"] == {"input_tokens": 39, "output_tokens": 15}
        assert cold["deterministic_status"] == warm["deterministic_status"] == "violation"
        assert len(warm["assessments"]) == 3
        assert all(item["details"]["cache_reused"] and "usage" not in item["details"] for item in warm["assessments"])


def test_diagnose_batch_profile_uses_one_http_request_with_grounded_answers(tmp_path):
    sources, line, _ = measured_sources(tmp_path)
    with http_choices() as provider:
        options = {key: value for key, value in provider["options"].items() if key != "criteria"}
        options.update(batch=True, changed_ranges=[{"path": "pkg/work.py", "start_line": line + 2, "end_line": line + 2}])
        result = diagnose(source_report(sources), tmp_path, options)
        assert result["status"] == "complete"
        assert len(provider["requests"]) == 1, "DIAGNOSE_BATCH_SINGLE_WIRE_REQUEST"
        assert result["execution"]["provider_calls"] == 1
        assert result["execution"]["fresh_usage"] == {"input_tokens": 13, "output_tokens": 5}
        assessments = result["assessments"]
        assert len(assessments) == 3 and len({item["task_id"] for item in assessments}) == 3
        assert {item["check_id"] for item in assessments} == {"semantic_boundaries", "port_abstraction", "evidence_coverage"}
        assert all(item["path"] == "pkg/work.py" and item["start_line"] == line and item["end_line"] == line + 3
                   for item in assessments), "BATCH_CANDIDATE_BINDING_PRESERVED"
        request = provider["requests"][0]
        assert list(request["questions"]) == ["q0", "q1", "q2"]
        assert [item["instructions"] for item in request["questions"].values()] == [item["question"] for item in assessments]


@pytest.mark.parametrize("http_status", [400, 529])
def test_diagnose_retains_bounded_http_status_without_error_body_or_failed_cache(tmp_path, monkeypatch, http_status):
    source_root, cache_root = tmp_path / "source", tmp_path / "cache"
    sources, line, _ = measured_sources(source_root)
    marker = "private-fixture-provider-diagnostic-body"
    monkeypatch.setenv("JEV_PROVIDER_STATUS_FIXTURE_KEY", marker)
    with http_choices() as provider:
        provider["status"], provider["response"] = http_status, {"error": marker}
        options = {key: value for key, value in provider["options"].items() if key != "criteria"}
        options.update(cache_dir=str(cache_root), batch=True, credential_env="JEV_PROVIDER_STATUS_FIXTURE_KEY",
                       changed_ranges=[{"path": "pkg/work.py", "start_line": line + 2, "end_line": line + 2}])
        result = diagnose(source_report(sources), source_root, options)
        assert result.get("provider_status") == http_status, "PROVIDER_HTTP_STATUS_RETAINED"
        assert result["status"] == "unavailable" and result["reason"] == "evaluation_unavailable"
        attempts = 3 if http_status == 529 else 1
        assert result["assessments"] == [] and len(provider["requests"]) == attempts
        assert result["execution"]["provider_calls"] == attempts
        assert marker not in json.dumps(result), "RAW_PROVIDER_BODY_NOT_RETAINED"
        assert list(cache_root.glob("*.json")) == [], "FAILED_HTTP_ATTEMPT_NOT_CACHED"


def test_diagnose_projects_relevant_evidence_and_retains_full_report_scope(tmp_path):
    sources, line, changed = measured_sources(tmp_path)
    report = source_report(sources)
    with http_choices() as provider:
        options = {key: value for key, value in provider["options"].items() if key != "criteria"}
        options.update(batch=True, max_files=1,
                       changed_ranges=[{"path": "pkg/work.py", "start_line": line + 2, "end_line": line + 2}])
        result = diagnose(report, tmp_path, options)
        assert result["status"] == "complete" and len(provider["requests"]) == 1
        wire = provider["requests"][0]["state"]
        compact, full = wire["source_scope"], result["source_scope"]
        assert not {"omitted", "excluded", "omitted_count", "omitted_sha256", "excluded_sha256"}.intersection(compact), "GLOBAL_BOOKKEEPING_ABSENT_FROM_MODEL_STATE"
        assert full["omitted"] and full["excluded"], "FULL_OMISSION_LEDGER_RETAINED"
        assert full["source_bytes"] == sum(len(item["source"].encode()) for item in wire["sources"])
        assert wire["sources"][0]["source"] == changed
        assert all(item["path"] == "pkg/work.py" for item in wire["sources"])
        assert full["truncated"] == []
        assert len(wire["candidates"]) == 1
        assert wire["candidates"][0]["path"] == "pkg/work.py" and wire["candidates"][0]["start_line"] == line
        represented = {item["role"] for item in wire["source_files"] + wire["candidates"]}
        assert wire["profile"]["roles"] == {key: report["profile"]["roles"][key] for key in represented}
        assert "subject" not in wire and result["subject"] == report["subject"]
        assert wire["deterministic"] == {"findings": report["findings"], "limitations": report["coverage"]["limitations"]}
        source_files = {item["path"]: item for item in wire["source_files"]}
        assert set(source_files) == set(full["selected"]), "RELEVANT_FILE_IDENTITIES_BOUND"
        for path, item in source_files.items():
            assert item["sha256"] == next(row["sha256"] for row in report["files"] if row["path"] == path), "SOURCE_FILE_IDENTITY_RETAINED"
        assert result["evidence_context"] == wire, "EXACT_PROVIDER_STATE_RETAINED"
        encoded = json.dumps(wire, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
        assert result["context_sha256"] == hashlib.sha256(encoded).hexdigest(), "PROVIDER_STATE_DIGEST_BOUND"


@pytest.mark.parametrize("option,value", [
    ("cache_dir", {}), ("batch", "true"), ("cache_ttl_seconds", 0),
    pytest.param("cache_ttl_seconds", 10 ** 400, id="overflow-ttl"),
])
def test_diagnose_invalid_cache_options_are_visible_configuration_errors(tmp_path, option, value):
    sources, _, _ = measured_sources(tmp_path)
    with http_choices() as provider:
        options = {key: value for key, value in provider["options"].items() if key != "criteria"}
        try:
            result = diagnose(source_report(sources), tmp_path, {**options, option: value})
        except Exception as error:
            result = {"status": "raised", "exception_type": type(error).__name__}
        assert result["status"] == "error" and result["reason"] == "invalid_configuration", "CACHE_OPTIONS_VALIDATED"
        assert result["assessments"] == [] and provider["requests"] == []


def test_cli_cache_and_batch_flags_reach_diagnostics(tmp_path, monkeypatch):
    from scripts.architecture.__main__ import main

    sources, line, _ = measured_sources(tmp_path / "candidate")
    root, cache = tmp_path / "candidate", tmp_path / "external-cache"
    config = root / "diagnostic.json"
    profile = root / "profile.json"
    profile.write_text("{}")
    monkeypatch.delenv("JEV_ENDPOINT", raising=False)
    monkeypatch.delenv("JEV_MODEL", raising=False)
    monkeypatch.setattr("scripts.architecture.__main__.check", lambda *args: source_report(sources))
    with http_choices() as provider:
        options = {key: value for key, value in provider["options"].items() if key != "criteria"}
        options["changed_ranges"] = [{"path": "pkg/work.py", "start_line": line + 2, "end_line": line + 2}]
        config.write_text(json.dumps(options))
        output = root / "output.json"
        arguments = ["--root", str(root), "--profile", "profile.json", "--jev-config", "diagnostic.json",
                     "--cache-dir", str(cache), "--batch", "--output", str(output)]
        assert main(arguments) == main(arguments) == 0
        report = json.loads(output.read_text())
        assert len(provider["requests"]) == 1 and report["diagnostics"]["execution"]["cache_status"] == "hit", "CLI_CACHE_AND_BATCH_APPLIED"
        assert list(cache.glob("*.json")) and not list(root.glob("*cache*"))


def test_ci_restores_external_advisory_cache_and_invokes_it():
    workflow = json.loads(Path(".github/workflows/jev.yml").read_text())
    advice = workflow["jobs"]["advice"]
    restored = [step for step in advice["steps"] if step.get("uses", "").startswith("actions/cache@")]
    assert len(restored) == 1, "CI_ADVISORY_CACHE_RESTORED"
    step = restored[0]
    assert step["with"]["path"] == "${{ runner.temp }}/jev-assessment-cache", "CI_CACHE_OUTSIDE_CANDIDATE"
    assert len(step["uses"].rsplit("@", 1)[1]) == 40
    diagnostic = next(item for item in advice["steps"] if "-m scripts.ci.advice assess" in item.get("run", ""))
    assert '--cache-dir "$RUNNER_TEMP/jev-assessment-cache"' in diagnostic["run"], "CI_CACHE_INVOKED"
    assert diagnostic.get("continue-on-error") is True, "CI_ASSESSMENT_FAILURE_ADVISORY"
    required = json.loads(Path(".github/workflows/ci.yml").read_text())
    assert not any(item.get("uses", "").startswith("actions/cache@")
                   and "jev-assessment-cache" in str(item.get("with", {}))
                   for job in required["jobs"].values() for item in job["steps"]), "CI_CACHE_FAILURE_ADVISORY"
