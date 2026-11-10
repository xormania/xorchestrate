"""Optional exact-request reuse and batching for advisory architecture assessments."""
from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import asdict, replace
from pathlib import Path

from scripts.testkit.adapters.evidence import canonical, write_json
from xorchestrate.adapters.evaluation import checked_assessment
from xorchestrate.model import OrchestrationError

_SCHEMA = 1
_MAX_ENTRY_BYTES = 8 * 1024 * 1024


def _hash(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _usage(assessments):
    values = [item.details.get("usage") for item in assessments]
    if not values or any(value is None and item.details.get("usage_scope") != "batch"
                         for item, value in zip(assessments, values)):
        return None
    supplied = [value for value in values if value is not None]
    if not supplied:
        return None
    if any(not isinstance(value, dict) or any(type(value.get(key)) is not int or value[key] < 0
                                            for key in ("input_tokens", "output_tokens")) for value in supplied):
        return None
    return {key: sum(value[key] for value in supplied) for key in ("input_tokens", "output_tokens")}


def _cached_results(value, *, key, evaluator, count, now, ttl):
    if (not isinstance(value, dict) or value.get("schema_version") != _SCHEMA
            or value.get("request_sha256") != key
            or type(value.get("created_at")) not in {int, float}
            or not math.isfinite(value["created_at"]) or not 0 <= now - value["created_at"] <= ttl
            or not isinstance(value.get("assessments"), list) or len(value["assessments"]) != count
            or value.get("results_sha256") != _hash(value["assessments"])):
        raise ValueError("Unusable advisory cache entry")
    results = [checked_assessment(item) for item in value["assessments"]]
    for item in results:
        probabilities = item.details.get("probabilities")
        if (item.outcome not in evaluator.criteria or type(item.confidence) not in {int, float}
                or item.details.get("model") != evaluator.model
                or not isinstance(probabilities, dict) or probabilities.keys() != evaluator.criteria.keys()
                or any(type(p) not in {int, float} or not math.isfinite(p) or not 0 <= p <= 1
                       for p in probabilities.values()) or abs(sum(probabilities.values()) - 1) > .01):
            raise ValueError("Invalid cached choice")
    return results


def _valid_ttl(value):
    try:
        return type(value) in {int, float} and math.isfinite(value) and value > 0
    except OverflowError:
        return False


def run_tasks(evaluator, tasks, context, *, cache_dir=None, batch=False, cache_ttl_seconds=86400):
    """Return aligned assessments and execution facts; cached usage is never fresh.

    Reuse is scoped to a trusted local cache. The digest detects corruption; it
    does not attest a writer. Errors and partial batches are never cached. Batch
    mode is explicit because joint questions can change provider judgments.
    """
    if ((cache_dir is not None and (not isinstance(cache_dir, (str, Path)) or not str(cache_dir) or "\0" in str(cache_dir)))
            or type(batch) is not bool or not _valid_ttl(cache_ttl_seconds)
            or not isinstance(tasks, list) or not tasks
            or any(not isinstance(task, dict) or not isinstance(task.get("task_id"), str)
                   or not task["task_id"] or not isinstance(task.get("question"), str)
                   or not task["question"].strip() for task in tasks)
            or len({task["task_id"] for task in tasks}) != len(tasks)):
        raise OrchestrationError("Invalid advisory execution options or tasks.", "invalid_configuration")
    started, now = time.monotonic(), time.time()
    key = _hash({"schema_version": _SCHEMA, "endpoint": evaluator.endpoint, "model": evaluator.model,
                 "criteria": evaluator.criteria, "batch": batch, "tasks": tasks, "context": context})
    metadata = {"request_sha256": key, "mode": "batch" if batch else "single", "cache_status": "disabled",
                "provider_calls": 0, "reused_assessments": 0, "fresh_usage": None, "reused_usage": None}
    path = Path(cache_dir).expanduser() / (key + ".json") if cache_dir is not None else None
    if path is not None:
        metadata["cache_status"] = "miss"
        try:
            if path.stat().st_size > _MAX_ENTRY_BYTES:
                raise ValueError("Oversized cache entry")
            entry = json.loads(path.read_text())
            results = _cached_results(entry, key=key, evaluator=evaluator, count=len(tasks), now=now, ttl=cache_ttl_seconds)
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError, OrchestrationError):
            metadata["cache_status"] = "unusable"
        else:
            metadata.update({"cache_status": "hit", "reused_assessments": len(results), "reused_usage": _usage(results),
                             "fresh_usage": {"input_tokens": 0, "output_tokens": 0}, "original_created_at": entry["created_at"]})
            reused = []
            for item in results:
                details = dict(item.details)
                if "usage" in details:
                    details["original_usage"] = details.pop("usage")
                details["cache_reused"] = True
                reused.append(replace(item, details=details))
            return reused, {**metadata, "elapsed_seconds": time.monotonic() - started}
    results = []
    try:
        if batch:
            questions = {f"q{index}": task["question"] for index, task in enumerate(tasks)}
            metadata["provider_calls"] = 1
            answers = evaluator.assess_many(questions, context)
            if not isinstance(answers, dict) or answers.keys() != questions.keys():
                raise OrchestrationError("The provider did not return the complete batch.", "evaluation_invalid")
            results = [answers[key] for key in questions]
        else:
            for task in tasks:
                metadata["provider_calls"] += 1
                results.append(evaluator.assess(task["question"], context))
    except OrchestrationError as error:
        raise OrchestrationError(str(error), error.code, {
            "execution": {**metadata, "fresh_usage": _usage(results), "elapsed_seconds": time.monotonic() - started},
            "completed_assessments": json.loads(canonical([asdict(item) for item in results])),
        }) from None
    # Keep the same independent validation on writes as on reads.
    rows = json.loads(canonical([asdict(item) for item in results]))
    entry = {"schema_version": _SCHEMA, "request_sha256": key, "created_at": now,
             "assessments": rows, "results_sha256": _hash(rows)}
    _cached_results(entry, key=key, evaluator=evaluator, count=len(tasks), now=now, ttl=cache_ttl_seconds)
    metadata["fresh_usage"] = _usage(results)
    if path is not None:
        try:
            if len(canonical(entry)) > _MAX_ENTRY_BYTES:
                raise ValueError("Oversized cache entry")
            write_json(path, entry)
        except (OSError, ValueError):
            metadata["cache_status"] = "write_unavailable"
    return results, {**metadata, "elapsed_seconds": time.monotonic() - started}
