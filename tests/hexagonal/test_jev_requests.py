"""Observe bounded full requests, provider retries and exact packet reuse."""
from __future__ import annotations

import json
import time

import pytest

from scripts.architecture.diagnostics import diagnose
from scripts.architecture.requests import provider_context, run_budgeted_tasks
from tests.hexagonal.support.architecture_context import measured_sources, source_report
from tests.hexagonal.support.jev_efficiency import http_choices
from tests.hexagonal.support.jev_requests import (
    aligned_choices,
    overflow_unless_single,
    population,
    transient_then_success,
)
from xorchestrate.adapters.evaluation import TypeSafeEvaluator
from xorchestrate.model import OrchestrationError


def test_full_request_budget_includes_relevant_contract_and_question_text():
    tasks, context = population(1, source_size=0)
    context["profile"]["roles"]["core"]["rule"] = "required contract clause " * 150
    context["candidates"][0]["contract"]["declaration"]["rule"] = context["profile"]["roles"]["core"]["rule"]
    with http_choices() as provider:
        evaluator = TypeSafeEvaluator(provider["options"])
        caught = None
        try:
            run_budgeted_tasks(evaluator, tasks, context, batch=True,
                               request_limits={"max_request_tokens": 1000, "bytes_per_token_estimate": 1, "token_headroom": 0})
        except OrchestrationError as error:
            caught = error
        assert caught is not None and caught.code == "evaluation_unavailable", "FULL_REQUEST_CONTRACT_BUDGET_ENFORCED"
        assert caught.details.get("cause") == "request_budget_exceeded"
        assert provider["requests"] == []


def test_budgeted_groups_preserve_whole_candidate_packets_and_task_alignment():
    tasks, context = population(4, source_size=35)
    with http_choices() as provider:
        provider["reply"] = aligned_choices
        evaluator = TypeSafeEvaluator(provider["options"])
        answers, execution = run_budgeted_tasks(evaluator, tasks, context, batch=True,
            request_limits={"max_request_tokens": 2200, "max_question_tokens": 2200, "bytes_per_token_estimate": 1, "token_headroom": 0})
        assert len(answers) == len(tasks) and len(provider["requests"]) > 1, "FULL_REQUEST_SPLIT_RETAINS_TASK_POPULATION"
        assert [answer.outcome for answer in answers] == ["concern", "no_concern", "insufficient_evidence", "concern"], "SPLIT_ANSWERS_RETAIN_TASK_ALIGNMENT"
        assert max(provider["request_bytes"]) <= 2200, "EVERY_SERIALIZED_REQUEST_WITHIN_BUDGET"
        observed = []
        for request in provider["requests"]:
            packet = request["state"]
            supplied = {item["unit_id"] for item in packet["sources"]}
            assert all(item["unit_id"] in supplied for item in packet["candidates"]), "COMPLETE_CANDIDATE_SUPPORT_RETAINED"
            assert all(item["source"].endswith("# evidence\n") for item in packet["sources"])
            observed.extend(question["instructions"] for question in request["questions"].values())
        assert sorted(observed) == sorted(task["question"] for task in tasks)
        assert execution["provider_calls"] == len(provider["requests"])
        assert sum(len(batch["task_ids"]) for batch in execution["batches"]) == len(tasks)


def test_packet_projection_preserves_selected_and_unowned_evidence_only():
    _, context = population(2, source_size=0)
    context["sources"].append({"unit_id": "shared", "path": "sample/shared.py", "role": "core", "source": "SHARED = True\n"})
    original_source = context["sources"][0]["source"]
    packet = provider_context(context, {"candidate-0"})
    assert {item["unit_id"] for item in packet["sources"]} == {"unit-0", "shared"}, "CANDIDATE_OWNERSHIP_PROJECTION_PRESERVED"
    assert [item["candidate_id"] for item in packet["candidates"]] == ["candidate-0"]
    assert packet["sources"][0]["source"] == original_source
    assert len(context["sources"]) == 3, "PACKET_PROJECTION_DOES_NOT_MUTATE_INPUT"


@pytest.mark.parametrize("status", [503, 529])
def test_transient_provider_error_retries_with_actual_attempt_accounting(status):
    tasks, context = population(1, source_size=0)
    with http_choices() as provider:
        provider["reply"] = transient_then_success(status)
        evaluator = TypeSafeEvaluator({**provider["options"], "max_retries": 1, "retry_backoff": 0})
        caught, answers, execution = None, [], {}
        try:
            answers, execution = run_budgeted_tasks(evaluator, tasks, context, batch=True)
        except OrchestrationError as error:
            caught = error
        assert caught is None and len(answers) == 1 and len(provider["requests"]) == 2, "TRANSIENT_FAILURE_RETRIED_ONCE"
        assert execution["provider_calls"] == 2, "ACTUAL_HTTP_ATTEMPTS_ACCOUNTED"
        assert execution["fresh_usage"] is None, "FAILED_ATTEMPT_USAGE_REMAINS_UNKNOWN"
        assert answers[0].details["usage_unmeasured_attempts"] == 1


def test_permanent_error_is_not_retried_or_cached_and_exposes_safe_error_fields(tmp_path, monkeypatch):
    tasks, context = population(1, source_size=0)
    marker = "fixture-secret-never-report"
    monkeypatch.setenv("JEV_REQUEST_TEST_KEY", marker)
    with http_choices() as provider:
        provider["reply"] = lambda *_: {"status": 400, "body": {"error": {"code": "invalid_schema", "message": marker}},
                                         "headers": {"x-typesafe-request-id": "fixture-request-02"}}
        evaluator = TypeSafeEvaluator({**provider["options"], "credential_env": "JEV_REQUEST_TEST_KEY", "max_retries": 3, "retry_backoff": 0})
        caught = None
        try:
            run_budgeted_tasks(evaluator, tasks, context, batch=True, cache_dir=tmp_path)
        except OrchestrationError as error:
            caught = error
        assert caught is not None and len(provider["requests"]) == 1, "PERMANENT_ERROR_NOT_RETRIED"
        assert caught.details.get("request_id") == "fixture-request-02", "SAFE_PROVIDER_REQUEST_ID_RETAINED"
        assert "provider_code" not in caught.details
        assert marker not in json.dumps(caught.details) and marker not in str(caught)
        assert list(tmp_path.glob("*.json")) == []


def test_recognized_overflow_splits_questions_without_losing_work_or_caching_failures(tmp_path):
    tasks, context = population(2, source_size=0)
    with http_choices() as provider:
        provider["reply"] = overflow_unless_single
        evaluator = TypeSafeEvaluator({**provider["options"], "max_retries": 0})
        caught, answers, execution = None, [], {}
        try:
            answers, execution = run_budgeted_tasks(evaluator, tasks, context, batch=True, cache_dir=tmp_path)
        except OrchestrationError as error:
            caught = error
        assert caught is None and len(answers) == 2 and len(provider["requests"]) == 3, "RECOGNIZED_OVERFLOW_REPACKED"
        assert execution["provider_calls"] == 3
        assert [len(request["questions"]) for request in provider["requests"]] == [2, 1, 1]
        answer_entries = [path for path in tmp_path.glob("*.json") if not path.name.endswith(".plan.json")]
        assert len(answer_entries) == 2, "ONLY_SUCCESSFUL_SPLIT_PACKETS_CACHED"
        assert len(list(tmp_path.glob("*.plan.json"))) == 1, "SUCCESSFUL_PARTITION_PLAN_RETAINED"
        before = len(provider["requests"])
        _, reused = run_budgeted_tasks(evaluator, tasks, context, batch=True, cache_dir=tmp_path)
        assert reused["fresh_usage"] == {"input_tokens": 0, "output_tokens": 0}, "SPLIT_PACKET_CACHE_REUSED"
        assert len(provider["requests"]) == before


@pytest.mark.parametrize("dimension", ["source", "contract"])
def test_unrelated_bookkeeping_reuses_cache_but_selected_evidence_changes_invalidate(tmp_path, dimension):
    tasks, context = population(1, source_size=0)
    with http_choices() as provider:
        evaluator = TypeSafeEvaluator(provider["options"])
        run_budgeted_tasks(evaluator, tasks, context, batch=True, cache_dir=tmp_path)
        context["subject"]["source_sha256"] = "different-unrelated-repository-state"
        context["profile"]["roles"]["unused"] = {"irrelevant": "unselected-role"}
        context["source_scope"]["omitted"] = ["unrelated/tool.py"]
        _, reused = run_budgeted_tasks(evaluator, tasks, context, batch=True, cache_dir=tmp_path)
        assert len(provider["requests"]) == 1 and reused["provider_calls"] == 0, "UNRELATED_BOOKKEEPING_DOES_NOT_SPEND_PROVIDER_CALL"
        assert reused["fresh_usage"] == {"input_tokens": 0, "output_tokens": 0}
        if dimension == "source":
            context["sources"][0]["source"] += "# changed selected behavior\n"
        else:
            context["candidates"][0]["contract"]["declaration"]["allowed_roles"] = ["core", "adapters"]
        _, changed = run_budgeted_tasks(evaluator, tasks, context, batch=True, cache_dir=tmp_path)
        assert len(provider["requests"]) == 2 and changed["provider_calls"] == 1, "SELECTED_EVIDENCE_INVALIDATES_CACHE"


def test_diagnose_revalidates_source_before_reusing_an_unchanged_selected_packet(tmp_path):
    source_root, cache_root = tmp_path / "source", tmp_path / "cache"
    sources, line, _ = measured_sources(source_root)
    report = source_report(sources)
    report["subject"]["source_sha256"] = "original-global-inventory"
    with http_choices() as provider:
        options = {key: value for key, value in provider["options"].items() if key != "criteria"}
        options.update(cache_dir=str(cache_root), batch=True, checks=["semantic_boundaries"],
                       changed_ranges=[{"path": "pkg/work.py", "start_line": line + 2, "end_line": line + 2}])
        cold = diagnose(report, source_root, options)
        assert cold["status"] == "complete" and len(provider["requests"]) == 1
        sources["pkg/adapters/disk.py"] = "def unrelated(value):\n    return value + 1\n"
        (source_root / "pkg/adapters/disk.py").write_text(sources["pkg/adapters/disk.py"])
        stale = diagnose(report, source_root, options)
        assert stale["status"] == "inconclusive" and len(provider["requests"]) == 1, "STALE_MEASUREMENT_CANNOT_REUSE_CACHE"
        fresh_report = source_report(sources)
        fresh_report["subject"]["source_sha256"] = "changed-global-inventory"
        reused = diagnose(fresh_report, source_root, options)
        assert reused["status"] == "complete" and reused["execution"]["provider_calls"] == 0, "REMEASURED_UNRELATED_CHANGE_REUSES_PACKET"
        assert len(provider["requests"]) == 1


def test_single_question_overflow_stops_explicitly():
    tasks, context = population(1, source_size=0)
    with http_choices() as provider:
        provider["reply"] = lambda *_: {"status": 400, "body": {"code": "max_tokens_exceeded"}}
        evaluator = TypeSafeEvaluator({**provider["options"], "max_retries": 0})
        caught = None
        try:
            run_budgeted_tasks(evaluator, tasks, context, batch=True, request_limits={"max_batches": 1})
        except OrchestrationError as error:
            caught = error
        assert caught is not None and len(provider["requests"]) == 1, "IRREDUCIBLE_OVERFLOW_STOPS"
        assert caught.details.get("provider_code") == "max_tokens_exceeded", "IRREDUCIBLE_OVERFLOW_CODE_RETAINED"


def test_request_cap_prevents_sending_overflow_replacements_beyond_the_limit():
    tasks, context = population(2, source_size=0)
    with http_choices() as provider:
        provider["reply"] = overflow_unless_single
        evaluator = TypeSafeEvaluator({**provider["options"], "max_retries": 0})
        caught = None
        try:
            run_budgeted_tasks(evaluator, tasks, context, batch=True, request_limits={"max_batches": 1})
        except OrchestrationError as error:
            caught = error
        assert caught is not None and caught.details.get("cause") == "request_limit", "REQUEST_POPULATION_CAP_ENFORCED"
        assert len(provider["requests"]) == 1 and caught.details["execution"]["completed_tasks"] == 0


def test_retry_backoff_cannot_extend_the_total_provider_deadline():
    tasks, context = population(1, source_size=0)
    with http_choices() as provider:
        provider["reply"] = lambda *_: {"status": 503, "body": {"error": {"code": "temporary"}}}
        evaluator = TypeSafeEvaluator({**provider["options"], "max_retries": 5, "retry_backoff": 1, "timeout": .2})
        started, caught = time.monotonic(), None
        try:
            run_budgeted_tasks(evaluator, tasks, context, batch=True)
        except OrchestrationError as error:
            caught = error
        elapsed = time.monotonic() - started
        assert caught is not None and len(provider["requests"]) == 1, "RETRY_DEADLINE_PREVENTS_NEW_ATTEMPT"
        assert elapsed < .7, "RETRY_BACKOFF_WITHIN_TOTAL_DEADLINE"


def test_framed_content_length_success_does_not_retry_a_complete_body():
    tasks, context = population(1, source_size=0)
    def framed(payload, attempt):
        reply = aligned_choices(payload, attempt)
        reply["headers"] = {"Content-Length": str(len(json.dumps(reply["body"]).encode()))}
        return reply
    with http_choices() as provider:
        provider["reply"] = framed
        evaluator = TypeSafeEvaluator({**provider["options"], "max_retries": 1, "retry_backoff": 0})
        caught, answers = None, []
        try:
            answers, execution = run_budgeted_tasks(evaluator, tasks, context, batch=True)
        except OrchestrationError as error:
            caught = error
        assert caught is None and len(answers) == 1, "FRAMED_COMPLETE_RESPONSE_ACCEPTED"
        assert len(provider["requests"]) == execution["provider_calls"] == 1


@pytest.mark.parametrize("detail,recognized", [
    ("max_tokens_exceeded", True), ({"code": "max_tokens_exceeded"}, True),
    ("unrelated prefix max_tokens_exceeded suffix", False),
], ids=["detail-string", "detail-code", "freeform-detail"])
def test_documented_bad_request_detail_envelopes_are_classified_exactly(detail, recognized):
    tasks, context = population(2, source_size=0)
    def overflow(payload, attempt):
        return {"status": 400, "body": {"detail": detail}} if len(payload["questions"]) > 1 else {"status": 200, "body": None}
    with http_choices() as provider:
        provider["reply"] = overflow
        evaluator = TypeSafeEvaluator({**provider["options"], "max_retries": 0})
        caught, answers = None, []
        try:
            answers, _ = run_budgeted_tasks(evaluator, tasks, context, batch=True)
        except OrchestrationError as error:
            caught = error
        if recognized:
            assert caught is None and len(answers) == 2 and len(provider["requests"]) == 3, "DOCUMENTED_OVERFLOW_DETAIL_REPACKED"
        else:
            assert caught is not None and len(provider["requests"]) == 1, "FREEFORM_DETAIL_NOT_PROMOTED_TO_CODE"
            assert "provider_code" not in caught.details


@pytest.mark.parametrize("partial", [False, True], ids=["successful-warm-plan", "partial-results"])
def test_interleaved_candidate_tasks_preserve_all_results_and_warm_partition_order(tmp_path, partial):
    original, context = population(2, source_size=0)
    tasks = [{**task, "task_id": task["task_id"] + ":" + phase,
              "question": task["question"] + " phase=" + phase}
             for phase in ("first", "second") for task in original]
    def reply(payload, attempt):
        candidate_ids = {item["candidate_id"] for item in payload["state"]["candidates"]}
        if len(candidate_ids) > 1:
            return {"status": 400, "body": {"detail": "max_tokens_exceeded"}}
        if partial and candidate_ids == {"candidate-1"}:
            return {"status": 422, "body": {"detail": "invalid packet"}}
        result = aligned_choices(payload, attempt)
        for key, question in payload["questions"].items():
            if "candidate-0" in question["instructions"] and "phase=second" in question["instructions"]:
                result["body"]["answers"][key].update(choice="insufficient_evidence", probabilities={
                    "concern": 0, "no_concern": 0, "insufficient_evidence": 1})
        return result
    with http_choices() as provider:
        provider["reply"] = reply
        evaluator = TypeSafeEvaluator({**provider["options"], "max_retries": 0})
        caught, answers = None, []
        try:
            answers, execution = run_budgeted_tasks(evaluator, tasks, context, batch=True, cache_dir=tmp_path)
        except OrchestrationError as error:
            caught = error
        if partial:
            assert caught is not None
            completed = caught.details.get("completed_task_assessments", {})
            assert set(completed) == {tasks[0]["task_id"], tasks[2]["task_id"]}, "ALL_INTERLEAVED_PARTIAL_RESULTS_RETAINED"
            assert caught.details["execution"]["completed_tasks"] == 2
            assert caught.details["execution"]["pending_task_ids"] == [tasks[1]["task_id"], tasks[3]["task_id"]]
        else:
            assert caught is None and [answer.outcome for answer in answers] == [
                "concern", "no_concern", "insufficient_evidence", "no_concern"], "INTERLEAVED_RESULT_ORDER_PRESERVED"
            count = len(provider["requests"])
            _, warm = run_budgeted_tasks(evaluator, tasks, context, batch=True, cache_dir=tmp_path)
            assert len(provider["requests"]) == count and warm["provider_calls"] == 0, "INTERLEAVED_WARM_PLAN_REUSED"
