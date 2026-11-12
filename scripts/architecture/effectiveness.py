"""Compare architecture diagnostics with the existing deterministic checker.

The frozen corpus is an independent oracle, never part of a provider packet.
Fixture mode exercises reporting only; live mode measures the configured provider.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import tempfile
import time
from pathlib import Path

from scripts.testkit.adapters.evidence import candidate_identity, canonical, write_json

from .diagnostics import CHECKS, diagnose
from .python import check

OUTCOMES = {"concern", "no_concern", "insufficient_evidence"}


def _digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _checks(value):
    if (not isinstance(value, list) or not value or any(not isinstance(item, str) or item not in CHECKS for item in value)
            or len(value) != len(set(value))):
        raise ValueError("Checks must be a nonempty list of distinct named diagnostic checks.")


def load_corpus(path: Path) -> dict:
    corpus = json.loads(Path(path).read_text())
    _validate(corpus)
    return corpus


def _validate(corpus):
    if not isinstance(corpus, dict) or corpus.get("schema_version") != 1:
        raise ValueError("Unsupported effectiveness corpus schema.")
    if not isinstance(corpus.get("profile"), dict) or not isinstance(corpus.get("common_sources"), dict):
        raise ValueError("Corpus requires a profile and common sources.")
    cases = corpus.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Corpus must contain cases.")
    ids, groups = set(), {}
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("Corpus cases must be objects.")
        identifier, group = case.get("id"), case.get("group")
        if not isinstance(identifier, str) or not identifier or identifier in ids or not isinstance(group, str) or not group:
            raise ValueError("Corpus case IDs must be unique; groups must be named.")
        ids.add(identifier)
        _checks(case.get("checks", ["semantic_boundaries"]))
        oracle = case.get("oracle")
        if not isinstance(oracle, dict) or type(oracle.get("fault")) is not bool:
            raise ValueError("Every case needs an independent fault oracle.")
        groups.setdefault(group, set()).add(oracle["fault"])
        if not isinstance(case.get("sources"), dict) or not case["sources"]:
            raise ValueError("Every case needs source bytes.")
        sources = {**corpus["common_sources"], **case["sources"]}
        for path, source in sources.items():
            relative = Path(path)
            if (relative.is_absolute() or ".." in relative.parts or "\\" in path
                    or not path.endswith(".py") or not isinstance(source, str)):
                raise ValueError("Corpus sources must be relative Python source files.")
        findings = oracle.get("findings")
        if not isinstance(findings, list):
            raise ValueError("Oracle findings must be a list.")
        for span in findings:
            if (not isinstance(span, dict) or span.get("path") not in sources or type(span.get("start_line")) is not int
                    or type(span.get("end_line")) is not int
                    or not 1 <= span["start_line"] <= span["end_line"] <= len(sources[span["path"]].splitlines())
                    or not span.get("checks") or not span.get("reason")):
                raise ValueError("Oracle findings need an exact source span, checks and rationale.")
            _checks(span["checks"])
            rules = span.get("deterministic_rule_ids", [])
            if (not isinstance(rules, list) or any(not isinstance(rule, str) or not rule for rule in rules)
                    or len(rules) != len(set(rules))):
                raise ValueError("Oracle deterministic rules must be a list of distinct nonempty rule IDs.")
        if oracle["fault"] != bool(findings):
            raise ValueError("Fault truth and oracle findings must agree.")
        if case.get("fixture_outcome") not in OUTCOMES:
            raise ValueError("Fixture predictions must be declared independently of truth.")
    if any(values != {False, True} for values in groups.values()):
        raise ValueError("Each dependency group needs a healthy and faulty control.")


def _overlaps(left, right):
    return (left.get("path") == right.get("path")
            and left.get("start_line", 0) <= right.get("end_line", 0)
            and right.get("start_line", 0) <= left.get("end_line", 0))


def _baseline(report, oracle):
    findings = [item for item in report.get("findings", []) if item.get("kind") != "extraction"]
    confirmed = any(
        item.get("path") == expected["path"]
        and expected["start_line"] <= item.get("line", 0) <= expected["end_line"]
        and item.get("rule_id") in expected.get("deterministic_rule_ids", [])
        for item in findings for expected in oracle["findings"])
    return {"outcome": "concern" if report.get("status") == "violation" else (
                "no_concern" if report.get("status") == "pass" else "insufficient_evidence"),
            "confirmed": confirmed, "report": report}


def _score_diagnostics(diagnostics, oracle):
    from .diagnostics import evaluation_tasks

    context = diagnostics.get("evidence_context", {})
    # Grounding is checked against independently reconstructed tasks. A confidence
    # or a generic concern cannot supply a source witness for itself.
    tasks = {item["task_id"]: item for item in evaluation_tasks(context)} if context else {}
    concerns = [item for item in diagnostics.get("assessments", []) if item.get("outcome") == "concern"]
    grounded, confirmed = 0, False
    fields = ("candidate_id", "check_id", "path", "start_line", "end_line", "claim", "contract")
    for item in concerns:
        expected_task = tasks.get(item.get("task_id"))
        if not expected_task or any(item.get(field) != expected_task.get(field) for field in fields):
            continue
        grounded += 1
        confirmed |= any(_overlaps(item, expected) and item["check_id"] in expected["checks"]
                         for expected in oracle["findings"])
    assessments = diagnostics.get("assessments", [])
    outcome = "concern" if concerns else (
        "no_concern" if assessments and all(item.get("outcome") == "no_concern" for item in assessments)
        else "insufficient_evidence")
    usage = [item.get("details", {}).get("usage") for item in assessments]
    return {"status": diagnostics.get("status", "error"), "outcome": outcome,
            "confirmed": bool(confirmed), "grounded_concerns": grounded,
            "unsupported_concerns": len(concerns) - sum(
                bool(tasks.get(item.get("task_id"))) and all(item.get(field) == tasks[item["task_id"]].get(field)
                    for field in fields) and any(_overlaps(item, expected) and item.get("check_id") in expected["checks"]
                                               for expected in oracle["findings"])
                for item in concerns),
            "usage": usage if any(item is not None for item in usage) else None,
            "diagnostics": diagnostics}


def fixture_diagnostics(report, root, options, case):
    """Scripted answers for keyless plumbing checks; never a Jev measurement."""
    from .context import build_context
    from .diagnostics import evaluation_tasks

    context = build_context(report, root, **{key: options[key] for key in (
        "max_source_bytes", "max_files", "source_paths", "changed_ranges", "max_candidates") if key in options})
    assessments = [{**task, "outcome": case["fixture_outcome"], "confidence": None,
                    "rationale": "Scripted fixture answer; no provider executed.", "evidence": [],
                    "details": {"provider": "scripted_fixture", "usage": None}}
                   for task in evaluation_tasks(context, options.get("checks"))]
    return {"status": "complete", "provider": "scripted_fixture", "model": "fixture-v1",
            "evidence_context": context, "context_sha256": _digest(context), "assessments": assessments}


def summarize(rows: list) -> dict:
    ids = [row.get("id") for row in rows]
    if not rows or any(not isinstance(value, str) or not value for value in ids) or len(ids) != len(set(ids)):
        raise ValueError("An effectiveness population needs distinct case IDs.")
    for row in rows:
        if (not isinstance(row.get("group"), str) or not row["group"]
                or type(row.get("expected_fault")) is not bool
                or type(row.get("comparison_eligible")) is not bool):
            raise ValueError("Rows need a group, fault truth and evidence eligibility.")
    result = {"cases": len(rows), "groups": len({row["group"] for row in rows}),
              "fault_cases": sum(row["expected_fault"] for row in rows),
              "healthy_cases": sum(not row["expected_fault"] for row in rows),
              "comparison_eligible_cases": sum(row["comparison_eligible"] for row in rows),
              "baseline": {"confirmed_findings": 0, "misses": 0, "false_alarms": 0, "unknown": 0},
              "jev": {"confirmed_findings": 0, "incremental_confirmed_findings": 0, "misses": 0,
                      "false_clears": 0, "false_alarms": 0, "unknown": 0, "unavailable": 0,
                      "errors": 0, "unsupported_concerns": 0},
              "reviewer_minutes": None, "cost": None}
    for row in rows:
        fault = row["expected_fault"]
        for arm in ("baseline", "jev"):
            value, counts = row[arm], result[arm]
            if value["outcome"] not in OUTCOMES or type(value["confirmed"]) is not bool:
                raise ValueError("Invalid arm outcome.")
            if value["confirmed"] and (not fault or value["outcome"] != "concern"):
                raise ValueError("Confirmed findings require fault truth and a concern.")
            if arm == "jev":
                counts["unsupported_concerns"] += value["unsupported_concerns"]
                if value["status"] in {"unavailable", "not_configured"}:
                    counts["unavailable"] += 1
                    continue
                if value["status"] == "error":
                    counts["errors"] += 1
                    continue
                if value["status"] != "complete" or not row["comparison_eligible"]:
                    counts["unknown"] += 1
                    continue
            if value["outcome"] == "insufficient_evidence":
                counts["unknown"] += 1
            elif value["confirmed"]:
                counts["confirmed_findings"] += 1
                if arm == "jev" and not row["baseline"]["confirmed"]:
                    counts["incremental_confirmed_findings"] += 1
            elif fault:
                counts["misses"] += 1
                if arm == "jev" and value["outcome"] == "no_concern":
                    counts["false_clears"] += 1
            elif value["outcome"] == "concern":
                counts["false_alarms"] += 1
    return result


def evaluate(corpus: dict, *, mode="fixture", options=None, diagnose_fn=None, max_seconds=120) -> dict:
    _validate(corpus)
    if mode not in {"fixture", "live"}:
        raise ValueError("Mode must be fixture or live.")
    if type(max_seconds) not in {int, float} or not math.isfinite(max_seconds) or not 0 < max_seconds <= 900:
        raise ValueError("max_seconds must be finite, positive and at most 900.")
    options = dict(options or {})
    rows = []
    run_started = time.monotonic()
    for case in corpus["cases"]:
        sources = {**corpus["common_sources"], **case["sources"]}
        with tempfile.TemporaryDirectory(prefix="xorchestrate-jev-case-") as directory:
            root = Path(directory)
            for path, source in sources.items():
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source)
            report = check(root, corpus["profile"])
            # Both arms get the same sources, contract and configured budget.
            # Reduced packets cannot qualify for a full-source comparison.
            case_options = {**options, "source_paths": list(sources), "changed_ranges": case["selection"],
                            "checks": case.get("checks", ["semantic_boundaries"])}
            case_options.pop("base_ref", None)
            case_options["role_priorities"] = []  # The synthetic corpus declares its own roles.
            started = time.monotonic()
            remaining = max_seconds - (started - run_started)
            if mode == "fixture":
                diagnostics = fixture_diagnostics(report, root, case_options, case)
            elif remaining <= 0:
                diagnostics = {"status": "budget_exhausted", "reason": "overall_evaluation_deadline", "assessments": []}
            else:
                candidates = case_options.get("max_candidates", 1)
                max_tasks = (candidates if type(candidates) is int and candidates > 0 else 1) * len(case_options["checks"])
                timeout = case_options.get("timeout", 15)
                if type(timeout) in {int, float} and math.isfinite(timeout) and timeout > 0:
                    case_options["timeout"] = min(timeout, remaining / max_tasks)
                diagnostics = (diagnose_fn or diagnose)(report, root, case_options)
            elapsed = time.monotonic() - started
            jev = _score_diagnostics(diagnostics, case["oracle"])
            context = diagnostics.get("evidence_context", {})
            source_records = context.get("sources", [])
            # Compare exact observed bytes, not file-count declarations. Complete
            # units may split a file; reconstruct their line coverage below.
            visible = {}
            for item in source_records:
                path = item.get("path")
                original = sources.get(path, "").splitlines(keepends=True)
                start, end = item.get("start_line", 0), item.get("end_line", 0)
                if (start > 0 and end >= start and item.get("source") == "".join(original[start - 1:end])):
                    visible.setdefault(path, set()).update(range(start, end + 1))
            eligible = all(set(range(1, len(source.splitlines()) + 1)) <= visible.get(path, set())
                           for path, source in sources.items())
            jev["latency_seconds"] = elapsed
            jev["latency_basis"] = "fixture_execution" if mode == "fixture" else "diagnostic_call_wall_clock"
            rows.append({"id": case["id"], "group": case["group"], "expected_fault": case["oracle"]["fault"],
                         "oracle": case["oracle"], "sources": sources, "source_sha256": _digest(sources),
                         "baseline": _baseline(report, case["oracle"]), "jev": jev,
                         "comparison_eligible": eligible})
    return {"schema_version": 1, "mode": mode,
            "measurement_basis": "scripted_fixture_only" if mode == "fixture" else "live_provider",
            "corpus_sha256": _digest(corpus), "budget": {key: options.get(key, default) for key, default in (
                ("max_source_bytes", 24000), ("max_files", 8), ("max_candidates", 1))},
            "max_seconds": max_seconds, "duration_seconds": time.monotonic() - run_started,
            "summary": summarize(rows), "cases": rows,
            "limits": ["Synthetic paired cases are grouped, not independent production incidents.",
                       "Confirmation uses the frozen source oracle, not reviewer acceptance or measured production benefit.",
                       "The baseline is the unchanged deterministic architecture checker, including all configured rules.",
                       "Unknown outcomes are retained, not scored as clear or incorrect.",
                       "Reviewer time, prices and monetary benefit are unmeasured."]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=Path("tests/fixtures/architecture-effectiveness.json"))
    parser.add_argument("--mode", choices=("fixture", "live"), default="fixture")
    parser.add_argument("--jev-config", type=Path, default=Path("contracts/hexagonal-jev.json"))
    parser.add_argument("--output", type=Path, default=Path(".xorchestrate/jev-effectiveness.json"))
    parser.add_argument("--max-seconds", type=float, default=120,
                        help="Overall live measurement budget; remaining cases stay unknown after expiry")
    args = parser.parse_args(argv)
    try:
        corpus = load_corpus(args.corpus)
        options = json.loads(args.jev_config.read_text()) if args.mode == "live" else {}
        for key, variable in (("endpoint", "JEV_ENDPOINT"), ("model", "JEV_MODEL")):
            if args.mode == "live" and os.environ.get(variable):
                options[key] = os.environ[variable]
        result = evaluate(corpus, mode=args.mode, options=options, max_seconds=args.max_seconds)
        result["execution"] = candidate_identity(Path.cwd())
        write_json(args.output, result)
    except (OSError, ValueError, TypeError) as error:
        parser.exit(2, f"Effectiveness evaluation failed: {error}\n")
    print(f"Jev effectiveness: {result['measurement_basis']}; {len(result['cases'])} cases; evidence: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
