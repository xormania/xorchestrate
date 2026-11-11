"""Plan bounded Jev packets while retaining complete candidate evidence.

The reviewed public API documents usage after evaluation but no tokenizer or
preflight endpoint. Estimates below are deliberately labelled estimates. They
include the complete JSON packet, use headroom, and retain diagnosed overflow.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

from scripts.testkit.adapters.evidence import canonical, write_json
from xorchestrate.adapters.evaluation import checked_assessment, choice_payload
from xorchestrate.model import OrchestrationError

from .cache import run_tasks, validate_execution

BUDGET_OPTIONS = {"max_request_tokens", "max_question_tokens", "bytes_per_token_estimate", "token_headroom", "max_batches"}


class RequestBudget:
    """Configurable heuristic, not a claim to reproduce Jev's tokenizer."""

    def __init__(self, options=None):
        options = {} if options is None else options
        if not isinstance(options, dict) or options.keys() - BUDGET_OPTIONS:
            raise ValueError("Invalid request budget options.")
        for name, default in (("max_request_tokens", 64000), ("max_question_tokens", 32000), ("max_batches", 16)):
            value = options.get(name, default)
            if type(value) is not int or value < 1 or name == "max_batches" and value > 256:
                raise ValueError("Request limits must be positive integers; max_batches cannot exceed 256.")
            setattr(self, name, value)
        for name, default, low, high in (("bytes_per_token_estimate", 2.0, 1, 8), ("token_headroom", .2, 0, .9)):
            value = options.get(name, default)
            if type(value) not in {int, float} or not math.isfinite(value) or not low <= value <= high:
                raise ValueError("Invalid token estimate or headroom.")
            setattr(self, name, value)

    def estimate(self, evaluator, tasks, context):
        questions = {f"q{index}": task["question"] for index, task in enumerate(tasks)}
        payload = choice_payload(evaluator.model, evaluator.criteria, questions, context)
        largest = max(len(choice_payload(evaluator.model, evaluator.criteria, {key: question}, context))
                      for key, question in questions.items())
        # The framing allowance is deliberately additional to serialized JSON.
        return {"method": "utf8_bytes_with_headroom", "exact": False,
                "bytes_per_token_estimate": self.bytes_per_token_estimate, "token_headroom": self.token_headroom,
                "request_bytes": len(payload), "largest_question_bytes": largest,
                "estimated_request_tokens": math.ceil(len(payload) / self.bytes_per_token_estimate) + 128,
                "estimated_question_tokens": math.ceil(largest / self.bytes_per_token_estimate) + 128,
                "request_token_budget": math.floor(self.max_request_tokens * (1 - self.token_headroom)),
                "question_token_budget": math.floor(self.max_question_tokens * (1 - self.token_headroom))}

    @staticmethod
    def fits(estimate):
        return (estimate["estimated_request_tokens"] <= estimate["request_token_budget"]
                and estimate["estimated_question_tokens"] <= estimate["question_token_budget"])


def provider_context(context, candidate_ids=None):
    """Project relevant evidence; the enclosing report keeps global provenance.

No selected source is truncated. A support unit without ownership is retained
conservatively. Global inventory digests and unrepresented role definitions are
not model evidence and must not invalidate otherwise identical packet reuse.
    """
    result = deepcopy(context)
    if not {"profile", "sources", "source_scope"} <= result.keys():
        return result
    result.pop("subject", None)
    if candidate_ids is not None:
        selected = set(candidate_ids)
        result["candidates"] = [c for c in result.get("candidates", []) if c["candidate_id"] in selected]
        for key in ("sources", "before_sources"):
            if key in result:
                result[key] = [s for s in result[key] if not s.get("support_for") or selected.intersection(s["support_for"])]
        if "missing_inputs" in result:
            result["missing_inputs"] = [m for m in result["missing_inputs"] if m.get("candidate_id") in selected]
    sources = [*result.get("sources", []), *result.get("before_sources", [])]
    # A file can supply dozens of complete units. Keep its measured identity
    # once, keyed by path and revision, instead of repeating it for every import.
    files = {canonical(item): item for item in result.get("source_files", [])}
    for source in sources:
        if "sha256" in source:
            item = {key: source[key] for key in ("path", "module", "sha256", "side", "git_commit") if key in source}
            files[canonical(item)] = item
            source.pop("sha256")
            source.pop("module", None)
    paths = {(s["path"], s.get("side"), s.get("git_commit")) for s in sources}
    if files:
        result["source_files"] = [item for _, item in sorted(files.items())
                                  if (item["path"], item.get("side"), item.get("git_commit")) in paths]
    candidates = result.get("candidates", [])
    roles = {item.get("role") for item in [*sources, *candidates]}
    profile = result.get("profile")
    if isinstance(profile, dict) and isinstance(profile.get("roles"), dict):
        profile["roles"] = {key: value for key, value in profile["roles"].items() if key in roles}
    scope = result.get("source_scope", {})
    ranges = [span for span in scope.get("changed_ranges", []) if any(
        span["path"] == c["path"] and span["start_line"] <= c["end_line"] and span["end_line"] >= c["start_line"]
        for c in candidates)]
    # Omitted/excluded/global range ledgers remain in diagnostics.source_scope.
    # Only the selected spans and per-candidate missing inputs inform this call.
    if "source_scope" in result:
        result["source_scope"] = {"selection": scope.get("selection"), "selected": sorted({s["path"] for s in sources}),
                                  "source_bytes": sum(len(s["source"].encode("utf-8")) for s in sources),
                                  "changed_ranges": ranges, "truncated": []}
    deterministic = result.get("deterministic")
    if isinstance(deterministic, dict):
        paths = {s["path"] for s in sources}
        # Overall counts and role inventories describe the repository, not the
        # selected packet. Keep directly relevant findings and the stated limits.
        coverage = deterministic.get("coverage") or {}
        result["deterministic"] = {"findings": [f for f in deterministic.get("findings", [])
                                                if not f.get("path") or f["path"] in paths],
                                   "limitations": coverage.get("limitations", deterministic.get("limitations", []))}
    return result


def _split(tasks, context):
    ids = list(dict.fromkeys(task.get("candidate_id") for task in tasks))
    if len(ids) > 1 and None not in ids:
        left = set(ids[:len(ids) // 2])
        groups = [[task for task in tasks if (task["candidate_id"] in left) == side] for side in (True, False)]
        return [(group, provider_context(context, {t["candidate_id"] for t in group})) for group in groups]
    if len(tasks) > 1:
        middle = len(tasks) // 2
        return [(tasks[:middle], context), (tasks[middle:], context)]
    return []


def _sum_usage(executions, field, activity):
    relevant = [e for e in executions if e.get(activity, 0)]
    if any(e.get(field) is None for e in relevant):
        return None
    return {key: sum(e[field][key] for e in relevant) for key in ("input_tokens", "output_tokens")}


def run_budgeted_tasks(evaluator, tasks, context, *, request_limits=None, cache_dir=None, batch=False, cache_ttl_seconds=86400):
    """Run complete packets with a bounded request count and shared deadline.

Only a recognized context overflow causes reactive splitting. Permanent errors
are never repeated unchanged; partial successful packets remain available.
    """
    try:
        budget = RequestBudget(request_limits)
    except (ValueError, TypeError, OverflowError):
        raise OrchestrationError("Invalid request budget.", "invalid_configuration") from None
    validate_execution(tasks, cache_dir, batch, cache_ttl_seconds)
    started = time.monotonic()
    original_timeout = getattr(evaluator, "timeout", 30)
    deadline = started + original_timeout
    context = provider_context(context)
    queue = [(tasks, context)] if batch else [
        ([task], provider_context(context, {task["candidate_id"]}) if task.get("candidate_id") is not None else context)
        for task in tasks]
    completed, batches = {}, []
    plan_key = hashlib.sha256(canonical({"context": context, "tasks": tasks, "model": evaluator.model,
                                        "endpoint": evaluator.endpoint, "criteria": evaluator.criteria,
                                        "batch": batch, "limits": request_limits or {}})).hexdigest()
    plan_path = Path(cache_dir).expanduser() / (plan_key + ".plan.json") if cache_dir is not None else None
    plan_status = "disabled" if plan_path is None else "miss"
    if plan_path is not None:
        try:
            if plan_path.stat().st_size > 1024 * 1024:
                raise ValueError("Oversized request plan")
            saved = json.loads(plan_path.read_text())
            groups = saved["groups"]
            if (saved.get("schema_version") != 1 or saved["request_sha256"] != plan_key
                    or type(saved["created_at"]) not in {int, float}
                    or not 0 <= time.time() - saved["created_at"] <= cache_ttl_seconds
                    or saved.get("groups_sha256") != hashlib.sha256(canonical(groups)).hexdigest()
                    or not isinstance(groups, list) or not 1 <= len(groups) <= budget.max_batches
                    or sorted(key for group in groups for key in group["task_ids"]) != sorted(t["task_id"] for t in tasks)):
                raise ValueError("Unusable request plan")
            by_id = {task["task_id"]: task for task in tasks}
            prepared = []
            for group in groups:
                members = [by_id[key] for key in group["task_ids"]]
                ids = {t.get("candidate_id") for t in members}
                packet = provider_context(context, ids if None not in ids else None)
                if group["context_sha256"] != hashlib.sha256(canonical(packet)).hexdigest():
                    raise ValueError("Request plan no longer matches evidence")
                prepared.append((members, packet))
            queue = prepared
            plan_status = "hit"
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
            plan_status = "unusable"

    def metadata():
        executions = [item["execution"] for item in batches if "execution" in item]
        states = {e["cache_status"] for e in executions}
        return {"request_sha256": plan_key, "mode": "batch" if batch else "single",
                "cache_status": next(iter(states)) if len(states) == 1 else "mixed" if states else "disabled",
                "logical_requests": len(executions), "requested_tasks": len(tasks), "completed_tasks": len(completed),
                "pending_task_ids": [task["task_id"] for task in tasks if task["task_id"] not in completed],
                "provider_calls": sum(e.get("provider_calls", 0) for e in executions),
                "reused_assessments": sum(e.get("reused_assessments", 0) for e in executions),
                "fresh_usage": _sum_usage(executions, "fresh_usage", "provider_calls"),
                "reused_usage": _sum_usage(executions, "reused_usage", "reused_assessments"),
                "elapsed_seconds": time.monotonic() - started, "plan_cache_status": plan_status, "batches": batches}

    try:
        while queue:
            group, packet = queue.pop(0)
            estimate = budget.estimate(evaluator, group, packet)
            if not budget.fits(estimate):
                parts = _split(group, packet)
                if parts:
                    queue[:0] = parts
                    continue
                batches.append({"task_ids": [t["task_id"] for t in group], "estimate": estimate,
                                "context_sha256": hashlib.sha256(canonical(packet)).hexdigest(), "evidence_context": packet,
                                "status": "not_sent", "reason": "request_budget_exceeded"})
                raise OrchestrationError("The complete candidate packet exceeds the request budget.",
                                         "evaluation_unavailable", {"cause": "request_budget_exceeded"})
            if len([b for b in batches if "execution" in b]) >= budget.max_batches:
                raise OrchestrationError("The diagnostic request limit was reached.", "evaluation_unavailable", {"cause": "request_limit"})
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise OrchestrationError("The diagnostic request deadline was reached.", "evaluation_unavailable", {"cause": "request_deadline"})
            evaluator.timeout = remaining
            receipt = {"task_ids": [t["task_id"] for t in group], "context_sha256": hashlib.sha256(canonical(packet)).hexdigest(),
                       "evidence_context": packet, "estimate": estimate}
            try:
                results, execution = run_tasks(evaluator, group, packet, cache_dir=cache_dir, batch=batch,
                                               cache_ttl_seconds=cache_ttl_seconds)
            except OrchestrationError as error:
                receipt.update(status="unavailable", execution=error.details.get("execution", {}))
                receipt["error"] = {key: error.details[key] for key in ("status", "provider_code", "request_id", "cause")
                                    if key in error.details}
                batches.append(receipt)
                for task, result in zip(group, error.details.get("completed_assessments", [])):
                    completed[task["task_id"]] = checked_assessment(result)
                if error.details.get("status") == 400 and error.details.get("provider_code") == "max_tokens_exceeded":
                    parts = _split(group, packet)
                    if parts:
                        receipt["reason"] = "max_tokens_exceeded"
                        queue[:0] = parts
                        continue
                raise
            receipt.update(status="complete", execution=execution)
            batches.append(receipt)
            completed.update((task["task_id"], result) for task, result in zip(group, results))
    except OrchestrationError as error:
        prefix = []
        for task in tasks:
            if task["task_id"] not in completed:
                break
            prefix.append(asdict(completed[task["task_id"]]))
        safe = {key: error.details[key] for key in ("status", "provider_code", "request_id", "cause") if key in error.details}
        raise OrchestrationError(str(error), error.code, {
            **safe, "execution": metadata(), "completed_assessments": prefix,
            "completed_task_assessments": {task_id: asdict(result) for task_id, result in completed.items()},
        }) from None
    finally:
        evaluator.timeout = original_timeout
    if plan_path is not None and plan_status != "hit":
        groups = [{key: b[key] for key in ("task_ids", "context_sha256")} for b in batches if b["status"] == "complete"]
        try:
            write_json(plan_path, {"schema_version": 1, "request_sha256": plan_key, "created_at": time.time(),
                                   "groups": groups, "groups_sha256": hashlib.sha256(canonical(groups)).hexdigest()})
        except (OSError, ValueError):
            plan_status = "write_unavailable"
    return [completed[task["task_id"]] for task in tasks], metadata()
