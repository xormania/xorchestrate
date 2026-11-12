"""Small advisory judgments over complete PR-event evidence.

Agentscient supplies the failure/excerpt pattern; CCE supplies narrow relevance
questions, complete candidate cards and semantic cache identity. The existing
TypeSafe adapter and bounded request executor retain validation and usage facts.
No assessment changes a check verdict, review state or source file.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import os
import re
import time
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from urllib.parse import quote

from scripts.architecture.requests import RequestBudget, run_budgeted_tasks
from scripts.ci.candidate import api_bytes
from scripts.testkit.adapters.evidence import canonical, write_json
from xorchestrate.adapters.evaluation import TypeSafeEvaluator
from xorchestrate.model import OrchestrationError
from xorchestrate.telemetry import protect


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) is not None


def _policy(policy):
    if not isinstance(policy, dict) or policy.get("schema_version") != 1 or type(policy.get("enabled")) is not bool:
        raise ValueError("invalid_policy")
    limits = policy["limits"]
    for key in ("packet_bytes", "source_bytes", "candidates", "excerpts", "cache_seconds"):
        if type(limits.get(key)) is not int or limits[key] <= 0:
            raise ValueError("invalid_policy")
    if not .5 < policy["threshold"] <= 1:
        raise ValueError("invalid_policy")
    for key in ("failure_question", "excerpt_question", "review_question", "scope_question"):
        if not _text(policy.get(key)):
            raise ValueError("invalid_policy")
    if policy["request_budget"].get("max_batches") != 1 or policy["connection"].get("max_retries", 0) != 0:
        raise ValueError("event_probes_require_one_attempt")
    RequestBudget(policy["request_budget"])
    return limits


def _packet(packet, limits):
    if not isinstance(packet, dict) or packet.get("schema_version") != 1:
        raise ValueError("invalid_packet")
    source = packet["source"]
    if (not isinstance(source, dict) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", source.get("repository", ""))
            or type(source.get("pr_number")) is not int or source["pr_number"] <= 0 or not _sha(source.get("head_sha"))):
        raise ValueError("invalid_source")
    evidence = packet["evidence"]
    if not isinstance(evidence, dict) or evidence.get("complete") is not True:
        raise ValueError("incomplete_evidence")
    if len(canonical(evidence)) > limits["packet_bytes"]:
        raise ValueError("packet_limit")
    kind = packet.get("kind")
    if kind == "failure":
        if not _text(evidence["failure"].get("text")) or not _text(evidence["failure"].get("id")):
            raise ValueError("missing_failure")
        rows, maximum = evidence["excerpts"], limits["excerpts"]
    elif kind == "scope":
        if not _text(evidence.get("purpose")):
            raise ValueError("missing_purpose")
        rows, maximum = evidence["candidates"], limits["candidates"]
    elif kind == "review":
        finding, unit = evidence["finding"], evidence["source"]
        if (not _text(finding.get("text")) or not _text(finding.get("id")) or not _text(finding.get("path"))
                or unit.get("path") != finding["path"] or not _text(unit.get("text"))
                or unit.get("sha256") != hashlib.sha256(unit["text"].encode()).hexdigest()
                or len(unit["text"].encode()) > limits["source_bytes"]):
            raise ValueError("incomplete_source")
        return kind, evidence
    else:
        raise ValueError("unsupported_probe")
    if (not isinstance(rows, list) or not 1 <= len(rows) <= maximum
            or any(not isinstance(row, dict) or not _text(row.get("text"))
                   or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", row.get("id", "")) for row in rows)
            or len({row["id"] for row in rows}) != len(rows)
            or kind == "failure" and any(row["id"] == "none" for row in rows)):
        raise ValueError("invalid_candidates")
    return kind, evidence


def run_probe(packet, policy, *, cache_dir=None, evaluator_factory=TypeSafeEvaluator):
    """Return advice and original assessments; incomplete inputs make zero calls.

    Failure category and excerpt selection have different Choice rubrics, hence
    two cached requests sharing one deadline. Other probes use one batch. Source
    identity is retained outside the model state: unchanged evidence can reuse
    inference across lifecycle transitions while publication checks freshness.
    """
    policy = deepcopy(policy)
    secrets = tuple(value for key, value in os.environ.items() if value and re.search(
        r"(?i)token|secret|password|credential|api.?key", key))
    report = {"schema_version": 1, "status": "unavailable", "advisory": True,
              "source": protect(packet.get("source"), secrets=secrets) if isinstance(packet, dict) else None,
              "assessments": [], "execution": {"provider_calls": 0, "fresh_usage": {"input_tokens": 0, "output_tokens": 0}}}
    try:
        limits = _policy(policy)
        kind, _ = _packet(packet, limits)
        original_sha256 = packet["evidence"]["source"]["sha256"] if kind == "review" else None
        packet = protect(packet, secrets=secrets)
        if kind == "review":
            unit = packet["evidence"]["source"]
            unit["sha256"] = hashlib.sha256(unit["text"].encode()).hexdigest()
            # The original identity is provenance, not part of protected model
            # evidence: redacted-equivalent sources can reuse the same inference.
            report["source_content_identity"] = {"path": unit["path"], "source_sha256": original_sha256,
                                                 "inference_sha256": unit["sha256"]}
        kind, evidence = _packet(packet, limits)
        if not policy["enabled"]:
            return {**report, "status": "disabled", "reason": "policy_disabled"}
        report.update({"kind": kind, "packet": packet, "policy": policy, "policy_sha256": digest(policy),
                       "request_sha256": digest({"kind": kind, "evidence": evidence, "policy": policy})})
        if kind == "failure":
            groups = [([{"task_id": "failure_kind", "question": policy["failure_question"]}], policy["failure_criteria"]),
                      ([{"task_id": "causal_excerpt", "question": policy["excerpt_question"]}],
                       {**{row["id"]: "The supplied excerpt with this ID." for row in evidence["excerpts"]},
                        "none": "No supplied excerpt is useful for this failure."})]
        elif kind == "review":
            groups = [([{"task_id": "finding_present", "question": policy["review_question"]}], policy["criteria"])]
        else:
            groups = [([{"task_id": row["id"], "question": policy["scope_question"] + f" Candidate: candidates[{index}]."}
                        for index, row in enumerate(evidence["candidates"])], policy["criteria"])]
        deadline = time.monotonic() + policy["connection"]["timeout"]
        executions = []
        for tasks, criteria in groups:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                report["reason"] = "deadline"
                break
            evaluator = evaluator_factory({**policy["connection"], "timeout": remaining, "criteria": criteria})
            try:
                answers, execution = run_budgeted_tasks(evaluator, tasks, evidence, batch=True, cache_dir=cache_dir,
                    cache_ttl_seconds=limits["cache_seconds"], request_limits=policy["request_budget"])
            except OrchestrationError as error:
                execution = error.details.get("execution", {"provider_calls": 0, "fresh_usage": None})
                executions.append(execution)
                report["reason"] = error.code
                break
            finally:
                evaluator.close()
            executions.append(execution)
            for task, answer in zip(tasks, answers):
                raw = asdict(answer)
                probability = answer.details["probabilities"][answer.outcome]
                outcome = answer.outcome if probability >= policy["threshold"] else "unknown"
                report["assessments"].append({"id": task["task_id"], "outcome": outcome, "assessment": raw})
        usage = [item.get("fresh_usage") for item in executions]
        report["execution"] = {"provider_calls": sum(e.get("provider_calls", 0) for e in executions),
            "reused_assessments": sum(e.get("reused_assessments", 0) for e in executions),
            "fresh_usage": None if any(u is None for u in usage) else
                {key: sum(u[key] for u in usage) for key in ("input_tokens", "output_tokens")}, "requests": executions}
        expected = sum(len(tasks) for tasks, _ in groups)
        if len(report["assessments"]) == expected:
            report["status"] = "complete"
        elif report["assessments"]:
            report["status"] = "partial"
        return report
    except (ValueError, TypeError, KeyError, AttributeError, OrchestrationError):
        return {**report, "reason": "invalid_or_incomplete_input"}


def _content(api, repository, path, sha, limit):
    value = api(f"/repos/{repository}/contents/{quote(path, safe='/')}?ref={sha}")
    if value.get("type") != "file" or value.get("encoding") != "base64" or value.get("size", limit + 1) > limit:
        raise ValueError("source_limit")
    raw = base64.b64decode(value["content"], validate=False)
    if len(raw) > limit:
        raise ValueError("source_limit")
    return {"path": path, "text": raw.decode("utf-8"), "sha256": hashlib.sha256(raw).hexdigest()}


def _review_access(event, api, policy, repository, number):
    """Authorize the actual submitted reviewer, not claims in a relay artifact."""
    allowed = policy["review_authorization"]["allowed_actor_ids"]
    if (not isinstance(allowed, list) or any(type(actor) is not int or actor <= 0 for actor in allowed)
            or len(set(allowed)) != len(allowed)):
        raise ValueError("invalid_review_authorization")
    review_id = event["review"]["id"]
    if type(review_id) is not int or review_id <= 0:
        raise ValueError("invalid_review")
    endpoint = f"/repos/{repository}/pulls/{number}"
    review = api(f"{endpoint}/reviews/{review_id}")
    author, sender = review["user"]["id"], event["sender"]["id"]
    if (type(author) is not int or author <= 0 or type(sender) is not int or sender <= 0
            or type(review["id"]) is not int or review["id"] != review_id
            or review["pull_request_url"] != "https://api.github.com" + endpoint):
        raise ValueError("invalid_review_identity")
    if author != sender or author not in allowed:
        return "reviewer_not_authorized"
    return None


def collect_event(event_name, event, api, policy):
    """Fetch source as data at a verified head; never check out or execute it."""
    skipped = {"schema_version": 1, "status": "skipped"}
    try:
        limits = _policy(policy)
        repository = event["repository"]["full_name"]
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("invalid_repository")
        number = event["pull_request"]["number"]
        if type(number) is not int or number <= 0:
            raise ValueError("invalid_pr")
        action = event.get("action")
        if event_name in {"pull_request", "pull_request_target"}:
            if action not in {"opened", "reopened", "ready_for_review", "synchronize", "edited"}:
                return {**skipped, "reason": "irrelevant_event"}
            if action == "edited" and not {"body", "title"}.intersection(event.get("changes", {})):
                return {**skipped, "reason": "metadata_only"}
        elif event_name != "pull_request_review" or action != "submitted":
            return {**skipped, "reason": "irrelevant_event"}
        pr = api(f"/repos/{repository}/pulls/{number}")
        sha = pr["head"]["sha"]
        if (pr.get("state") != "open" or not _sha(sha) or pr["head"]["repo"]["full_name"] != repository
                or pr["base"]["repo"]["full_name"] != repository):
            return {**skipped, "reason": "ineligible_pr"}
        if event["pull_request"]["head"]["sha"] != sha:
            return {**skipped, "reason": "stale_event"}
        if event_name == "pull_request_review":
            reason = _review_access(event, api, policy, repository, number)
            if reason:
                return {**skipped, "reason": reason}
        source = {"repository": repository, "pr_number": number, "head_sha": sha}
        files = api(f"/repos/{repository}/pulls/{number}/files?per_page=100")
        paths = {row["filename"] for row in files if row.get("status") != "removed"}
        comments = []
        if event_name == "pull_request_review":
            review_id = event["review"]["id"]
            comments = api(f"/repos/{repository}/pulls/{number}/reviews/{review_id}/comments?per_page=100")
        elif action == "synchronize":
            comments = api(f"/repos/{repository}/pulls/{number}/comments?per_page=100")
        comments = sorted((row for row in comments if row.get("path") in paths and _text(row.get("body"))),
                          key=lambda row: row.get("id", 0), reverse=True)
        if comments:
            row = comments[0]
            unit = _content(api, repository, row["path"], sha, limits["source_bytes"])
            return {"schema_version": 1, "kind": "review", "source": source, "evidence": {
                "complete": True, "finding": {"id": str(row["id"]), "text": row["body"], "path": row["path"]}, "source": unit}}
        if event_name == "pull_request_review":
            return {**skipped, "reason": "no_inline_finding"}
        if not paths:
            return {**skipped, "reason": "no_source"}
        path = sorted(paths)[0]
        unit = _content(api, repository, path, sha, limits["source_bytes"])
        changed = next(row for row in files if row["filename"] == path)
        return {"schema_version": 1, "kind": "scope", "source": source, "evidence": {
            "complete": True, "purpose": pr.get("body") or pr.get("title", ""), "source": unit,
            "change": {"path": path, "status": changed.get("status"), "patch": changed.get("patch"),
                       "patch_scope": "GitHub supplied patch; completeness is not established."},
            "selected_paths": [path], "omitted_path_count": max(0, pr.get("changed_files", len(files)) - 1),
            "candidates": policy["review_topics"]}}
    except (OSError, ValueError, KeyError, TypeError, AttributeError, UnicodeError):
        return {"schema_version": 1, "status": "unavailable", "reason": "incomplete_event_evidence"}


def current_source(source, api):
    try:
        pr = api(f"/repos/{source['repository']}/pulls/{source['pr_number']}")
        return (pr.get("state") == "open" and pr["head"]["sha"] == source["head_sha"]
                and pr["head"]["repo"]["full_name"] == source["repository"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def collect_failure(event, api, logs):
    """Select real failure excerpts from one completed PR CI run, never retry it."""
    try:
        run = event["workflow_run"]
        repository = event["repository"]["full_name"]
        if (run["path"] != ".github/workflows/ci.yml" or run["conclusion"] != "failure"
                or run["head_repository"]["full_name"] != repository or len(run["pull_requests"]) != 1):
            return {"status": "skipped", "reason": "no_failed_pr_ci"}
        number = run["pull_requests"][0]["number"]
        pr = api(f"/repos/{repository}/pulls/{number}")
        sha = run["head_sha"]
        source = {"repository": repository, "pr_number": number, "head_sha": sha}
        if pr["head"]["sha"] != sha or not current_source(source, api):
            return {"status": "stale", "reason": "superseded_ci"}
        jobs = api(f"/repos/{repository}/actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs?per_page=100")["jobs"]
        failed = [job for job in jobs if job.get("conclusion") == "failure"]
        if not failed:
            return {"status": "skipped", "reason": "no_failed_job"}
        job = failed[0]
        text = logs(f"/repos/{repository}/actions/jobs/{job['id']}/logs")
        lines = text.splitlines()
        matches = [i for i, line in enumerate(lines) if re.search(r"(?i)assertionerror|^.*FAIL(?:ED|URE)|error:|traceback", line)]
        if not matches:
            return {"status": "unavailable", "reason": "no_failure_excerpt"}
        excerpts = []
        for index in list(dict.fromkeys([matches[0], matches[-1]])):
            start, end = max(0, index - 3), min(len(lines), index + 9)
            excerpt = "\n".join(lines[start:end])
            if len(excerpt.encode()) > 4000:
                continue
            excerpts.append({"id": "excerpt_" + str(len(excerpts)), "text": excerpt,
                             "source": f"job:{job['id']}:lines:{start + 1}-{end}"})
        return {"schema_version": 1, "kind": "failure", "source": source, "evidence": {"complete": True,
            "failure": {"id": str(job["id"]), "text": "Failed CI job: " + job["name"],
                        "failed_steps": [step["name"] for step in job.get("steps", []) if step.get("conclusion") == "failure"]},
            "excerpts": excerpts, "source_run": {"id": run["id"], "attempt": run["run_attempt"]},
            "log_sha256": hashlib.sha256(text.encode()).hexdigest(), "log_lines": len(lines),
            "scope": "Selected complete excerpt windows; omitted log lines are not assessed."}}
    except (OSError, ValueError, KeyError, TypeError, AttributeError, UnicodeError):
        return {"status": "unavailable", "reason": "failure_evidence_unavailable"}


def relay_event(event, payload):
    """Authenticate a review relay's PR association before reading review data."""
    run = event["workflow_run"]
    repository = event["repository"]["full_name"]
    actor = run["actor"]["id"]
    if (run["path"] != ".github/workflows/jev-review-events.yml"
            or type(actor) is not int or actor <= 0
            or run["head_repository"]["full_name"] != repository or run["conclusion"] != "success"
            or payload["repository"]["full_name"] != repository
            or not any(pr["number"] == payload["pull_request"]["number"]
                       and pr["head"]["sha"] == payload["pull_request"]["head"]["sha"]
                       for pr in run.get("pull_requests", []))):
        raise ValueError("unidentified_review_relay")
    authenticated = deepcopy(payload)
    authenticated["sender"] = {"id": actor}
    return authenticated


def summary(report):
    lines = ["### Jev event advice", "", "Advisory only; CI, review state and merge authority are unchanged.", "",
             "Status: " + html.escape(report.get("status", "unavailable"))]
    if report.get("source"):
        lines.append("Source: `" + report["source"]["head_sha"] + "`")
        source = report["source"]
        evidence = report.get("packet", {}).get("evidence", {})
        finding = evidence.get("finding", {})
        if str(finding.get("id", "")).isdigit():
            lines.append(f"[Selected finding](https://github.com/{source['repository']}/pull/{source['pr_number']}"
                         f"#discussion_r{finding['id']})")
        run = evidence.get("source_run", {})
        if type(run.get("id")) is int:
            lines.append(f"[Failed source run](https://github.com/{source['repository']}/actions/runs/{run['id']})")
    if report.get("reason"):
        lines.append("Reason: " + html.escape(report["reason"]))
    for row in report.get("assessments", []):
        lines.append("- " + html.escape(row["id"]) + ": **" + html.escape(row["outcome"]) + "**")
    lines.append("Provider calls: " + str(report.get("execution", {}).get("provider_calls", 0)))
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", default="contracts/jev-events.json")
    parser.add_argument("--packet")
    parser.add_argument("--event")
    parser.add_argument("--event-name")
    parser.add_argument("--relay")
    parser.add_argument("--output", required=True)
    parser.add_argument("--cache")
    args = parser.parse_args(argv)
    policy = json.loads(Path(args.policy).read_text())
    token = os.environ.get("GITHUB_TOKEN", "")
    def api(path):
        return json.loads(api_bytes("https://api.github.com" + path, token, 1024 * 1024))
    if args.packet:
        packet = json.loads(Path(args.packet).read_text())
    else:
        event = json.loads(Path(args.event).read_text())
        if args.event_name == "workflow_run" and event["workflow_run"]["path"] == ".github/workflows/ci.yml":
            def logs(path):
                return api_bytes("https://api.github.com" + path, token, 1024 * 1024).decode("utf-8")
            packet = collect_failure(event, api, logs)
        elif args.relay:
            relay = json.loads(Path(args.relay).read_text())
            packet = collect_event("pull_request_review", relay_event(event, relay), api, policy)
        else:
            packet = collect_event(args.event_name, event, api, policy)
    before = current_source(packet["source"], api) if args.event and "kind" in packet else True
    if "kind" not in packet:
        report = packet
    elif before is not True:
        report = {"status": "stale" if before is False else "unavailable", "source": packet["source"],
                  "reason": "head_changed_before_evaluation" if before is False else "current_head_unavailable"}
    else:
        report = run_probe(packet, policy, cache_dir=args.cache)
        after = current_source(packet["source"], api) if args.event else True
        if after is not True:
            report = {**report, "status": "stale" if after is False else "unavailable", "assessments": [],
                      "reason": "head_changed_during_evaluation" if after is False else "current_head_unavailable"}
    report["trusted_sha"] = os.environ.get("TRUSTED_SHA")
    write_json(Path(args.output) / "assessment.json", report)
    Path(args.output, "summary.md").write_text(summary(report))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
            stream.write(summary(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
