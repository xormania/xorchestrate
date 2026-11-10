"""Combine existing CI outcomes and retained receipts into one reviewable result."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
from collections import Counter
from pathlib import Path

# Keep the isolated direct-file CLI usable without importing candidate packages.
_spec = importlib.util.spec_from_file_location("ci_candidate", Path(__file__).with_name("candidate.py"))
candidate_evidence = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(candidate_evidence)

ALWAYS = ("paths", "changes", "architecture-status")
HEAVY = ("application-tests", "application", "verification", "tmux-acceptance")
ARTIFACTS = {
    "static-checks-python-3.12": ("static",),
    "calibration-python-3.12": ("calibration",),
    "composed-protocol-evidence": ("multi-harness",),
    "tmux-acceptance-evidence": ("acceptance", "stories"),
}
JEV_OUTCOMES = {"concern", "no_concern", "insufficient_evidence"}
JEV_STATUSES = {"complete", "unavailable", "error", "inconclusive", "not_configured"}
# The public diagnostic selector permits 100 candidates and three named checks.
JEV_MAX_ASSESSMENTS = 100 * 3


def _sha(value, length=64):
    return isinstance(value, str) and len(value) == length and all(char in "0123456789abcdef" for char in value)


def _bounded_paths(value):
    return (isinstance(value, list) and len(value) <= 10000
            and all(isinstance(path, str) and 0 < len(path) <= 1024 for path in value)
            and len(value) == len(set(value)))


def _diagnostic_scope(value):
    if not isinstance(value, dict) or not all(_bounded_paths(value.get(key)) for key in ("selected", "omitted", "truncated")):
        raise ValueError("Invalid diagnostic source scope")
    selected, omitted, truncated = (set(value[key]) for key in ("selected", "omitted", "truncated"))
    if selected.intersection(omitted) or not truncated.issubset(selected):
        raise ValueError("Inconsistent diagnostic source scope")
    if any(type(value.get(key)) is not int for key in ("source_bytes", "max_source_bytes")):
        raise ValueError("Invalid diagnostic source budget")
    if not 0 <= value["source_bytes"] <= value["max_source_bytes"] <= 1000000:
        raise ValueError("Invalid diagnostic source budget")
    return {key: value[key] for key in ("selected", "omitted", "truncated", "source_bytes", "max_source_bytes")}


def _diagnostic_assessments(values):
    if not isinstance(values, list) or len(values) > JEV_MAX_ASSESSMENTS:
        raise ValueError("Invalid diagnostic assessment list")
    result, identities, task_ids = [], set(), set()
    for value in values:
        if not isinstance(value, dict):
            raise ValueError("Invalid diagnostic assessment")
        check_id, outcome, confidence = (value.get(key) for key in ("check_id", "outcome", "confidence"))
        if (not isinstance(check_id, str) or not 0 < len(check_id) <= 128
                or not isinstance(outcome, str) or outcome not in JEV_OUTCOMES
                or (confidence is not None and (type(confidence) not in (int, float)
                    or not math.isfinite(confidence) or not 0 <= confidence <= 1))):
            raise ValueError("Invalid diagnostic assessment")
        located = {}
        for name in ("candidate_id", "task_id"):
            if name in value:
                identifier = value[name]
                if not isinstance(identifier, str) or not 0 < len(identifier) <= 256:
                    raise ValueError("Invalid diagnostic task identity")
                located[name] = identifier
        identity = (("candidate", located["candidate_id"], check_id) if "candidate_id" in located else
                    ("task", located["task_id"]) if "task_id" in located else ("legacy", check_id))
        if identity in identities or ("task_id" in located and located["task_id"] in task_ids):
            raise ValueError("Duplicate diagnostic task")
        if located or any(name in value for name in ("path", "start_line", "end_line")):
            path, start, end = (value.get(name) for name in ("path", "start_line", "end_line"))
            if (not isinstance(path, str) or not 0 < len(path) <= 1024
                    or type(start) is not int or type(end) is not int or not 1 <= start <= end):
                raise ValueError("Invalid diagnostic source span")
            located.update(path=path, start_line=start, end_line=end)
        details = value.get("details", {})
        if not isinstance(details, dict):
            raise ValueError("Invalid diagnostic details")
        kept = {}
        probabilities = details.get("probabilities")
        if probabilities is not None:
            if (not isinstance(probabilities, dict) or set(probabilities) != JEV_OUTCOMES
                    or any(type(p) not in (int, float) or not math.isfinite(p) or not 0 <= p <= 1 for p in probabilities.values())
                    or abs(sum(probabilities.values()) - 1) > 0.01):
                raise ValueError("Invalid diagnostic distribution")
            kept["probabilities"] = probabilities
        if "usage" in details:
            usage = details["usage"]
            if not isinstance(usage, dict) or any(type(usage.get(key)) is not int or usage[key] < 0 for key in ("input_tokens", "output_tokens")):
                raise ValueError("Invalid diagnostic usage")
            kept["usage"] = {key: usage[key] for key in ("input_tokens", "output_tokens")}
        result.append({"check_id": check_id, "outcome": outcome, "confidence": confidence, "details": kept, **located})
        identities.add(identity)
        if "task_id" in located:
            task_ids.add(located["task_id"])
    return result



def _diagnostic_execution(value):
    if (not isinstance(value, dict) or value.get("mode") not in {"single", "batch"}
            or value.get("cache_status") not in {"disabled", "miss", "unusable", "hit", "write_unavailable"}
            or not _sha(value.get("request_sha256"))
            or any(type(value.get(key)) is not int or value[key] < 0
                   for key in ("provider_calls", "reused_assessments"))):
        raise ValueError("Invalid diagnostic execution")
    kept = {key: value[key] for key in ("mode", "cache_status", "request_sha256", "provider_calls", "reused_assessments")}
    for name in ("fresh_usage", "reused_usage"):
        usage = value.get(name)
        if usage is not None and (not isinstance(usage, dict) or any(type(usage.get(key)) is not int or usage[key] < 0
                                                                   for key in ("input_tokens", "output_tokens"))):
            raise ValueError("Invalid diagnostic execution usage")
        kept[name] = usage
    return kept

def jev_evidence(artifacts, needs, *, tested_sha):
    """Describe optional advice without allowing it to qualify deterministic CI.

    Only a report from a clean checkout of the tested revision supplies current
    assessments. Missing, malformed and foreign reports remain visible diagnoses.
    """
    job = needs.get("architecture", {}) if isinstance(needs, dict) else {}
    job = job if isinstance(job, dict) else {}
    base = {"provider": "jev", "artifact": "architecture-evidence", "current": False,
            "job_outcome": job.get("result", "missing"), "assessments": []}
    try:
        paths = list((artifacts / "architecture-evidence").glob("**/diagnostics.json"))
        if not paths:
            status = "skipped" if job.get("result") == "skipped" else "missing"
            return {**base, "status": status, "reason": "missing_report"}
        if len(paths) != 1:
            return {**base, "status": "invalid", "reason": "multiple_reports"}
        path = paths[0]
        if path.stat().st_size > 2097152:
            raise ValueError("Diagnostic report exceeds the evidence budget")
        report = _read(path)
        if type(report.get("schema_version")) is not int or report["schema_version"] != 1:
            raise ValueError("Unknown architecture report schema")
        base["report_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        execution = report.get("execution", {})
        if (not isinstance(execution, dict) or not _sha(tested_sha, 40)
                or execution.get("git_commit") != tested_sha or execution.get("git_status") != ""):
            return {**base, "status": "foreign", "reason": "foreign_or_dirty_candidate"}
        diagnostic = report.get("diagnostics")
        if not isinstance(diagnostic, dict) or diagnostic.get("status") not in JEV_STATUSES:
            raise ValueError("Invalid diagnostic status")
        status = diagnostic["status"]
        subject = report.get("subject")
        if status == "complete" or "subject" in diagnostic:
            if (not isinstance(subject, dict) or not all(_sha(subject.get(key)) for key in ("source_sha256", "contract_sha256"))
                    or diagnostic.get("subject") != subject):
                return {**base, "status": "foreign", "reason": "diagnostic_subject_mismatch"}
        assessments = _diagnostic_assessments(diagnostic.get("assessments", []))
        if status == "complete" and not assessments:
            raise ValueError("A complete diagnostic needs assessments")
        base.update(current=True, status=status, assessments=assessments)
        if "model" in diagnostic:
            if not isinstance(diagnostic["model"], str) or len(diagnostic["model"]) > 128:
                raise ValueError("Invalid diagnostic model")
            base["model"] = diagnostic["model"]
        if "source_scope" in diagnostic:
            base["source_scope"] = _diagnostic_scope(diagnostic["source_scope"])
        if "context_sha256" in diagnostic:
            if not _sha(diagnostic["context_sha256"]):
                raise ValueError("Invalid diagnostic context identity")
            base["context_sha256"] = diagnostic["context_sha256"]
        if status == "complete" and ("source_scope" not in base or "context_sha256" not in base):
            raise ValueError("A complete diagnostic needs its measured context")
        if "execution" in diagnostic:
            base["execution"] = _diagnostic_execution(diagnostic["execution"])
        if "provider_status" in diagnostic:
            status_code = diagnostic["provider_status"]
            if type(status_code) is not int or not 100 <= status_code <= 599:
                raise ValueError("Invalid diagnostic HTTP status")
            base["provider_status"] = status_code
        base["concerns"] = sum(row["outcome"] == "concern" for row in assessments)
        if "reason" in diagnostic:
            reason = diagnostic["reason"]
            if not isinstance(reason, str) or len(reason) > 512:
                raise ValueError("Invalid diagnostic reason")
            base["reason"] = reason
        return base
    except (OSError, ValueError, TypeError, KeyError, RecursionError, OverflowError):
        return {key: value for key, value in {
            **base, "current": False, "status": "invalid", "reason": "invalid_report", "assessments": [],
        }.items() if key not in {"source_scope", "context_sha256", "concerns"}}


def job_verdict(needs, *, event, package=False):
    """Only a successful PR classifier may permit documented heavy-job skips."""
    errors = []
    if not isinstance(needs, dict):
        return False, ["Job outcomes are missing or malformed"]
    paths = needs.get("paths", {})
    paths = paths if isinstance(paths, dict) else {}
    outputs = paths.get("outputs", {})
    code = outputs.get("code") if isinstance(outputs, dict) else None
    if code not in ("true", "false"):
        errors.append("Classifier did not provide an explicit code decision")
    docs = event == "pull_request" and paths.get("result") == "success" and code == "false"
    if code == "false" and event != "pull_request":
        errors.append("Only pull requests may skip documentation-only validation")
    for name in (*ALWAYS, *HEAVY, *(("package",) if package else ())):
        row = needs.get(name)
        state = row.get("result") if isinstance(row, dict) else None
        allowed = {"success", "skipped"} if docs and name not in ALWAYS else {"success"}
        if state not in allowed:
            errors.append(f"Required job {name} did not pass: {state or 'missing'}")
    return docs, errors


def _read(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError("Required evidence is missing or is not a regular file: " + str(path))
    if path.stat().st_size > candidate_evidence.MAX_ARCHIVE:
        raise ValueError("Evidence JSON exceeds its byte limit")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("Expected an evidence object: " + str(path))
    return value


def _clean_candidate(candidate, tested_sha, tested_tree=None):
    return (isinstance(candidate, dict) and candidate.get("commit") == tested_sha and candidate.get("status") == ""
            and (tested_tree is None or candidate.get("tree") == tested_tree))


def _reports(directory, suites, tested_sha, tested_tree=None):
    reports = []
    for path in sorted(directory.glob("**/report.json")):
        value = _read(path)
        if value.get("schema_version") == 2 and value.get("suite") in suites:
            candidate = _read(path.parent / "candidate.json")
            if not _clean_candidate(candidate, tested_sha, tested_tree):
                raise ValueError("Suite evidence does not identify the clean tested commit: " + value["suite"])
            reports.append((path, value))
    for suite in suites:
        matches = [value for _, value in reports if value["suite"] == suite]
        if len(matches) != 1 or matches[0].get("status") != "passed":
            raise ValueError("Required suite evidence did not pass exactly once: " + suite)
    return reports


def shard_evidence(artifacts, workflow, *, tested_sha, catalog, directories=None, tested_tree=None):
    """Reconcile the shard set using the workflow and existing catalog/receipts."""
    matrix = workflow["jobs"]["application-tests"]["strategy"]["matrix"]
    counts, indices = matrix["shards"], matrix["shard"]
    if (not isinstance(counts, list) or len(counts) != 1 or type(counts[0]) is not int
            or counts[0] < 1 or indices != list(range(1, counts[0] + 1))):
        raise ValueError("Workflow must declare one complete shard matrix")
    total = counts[0]
    canonical = json.dumps(catalog, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    catalog_hash = hashlib.sha256(canonical).hexdigest()
    expected_modules = {module for family in catalog["families"].values() for module in family["modules"]}
    modules, cases, families, rows = set(), set(), {}, []
    for index in indices:
        artifact = f"framework-tests-python-3.12-shard-{index}-of-{total}"
        directory = (directories or {}).get(artifact, artifacts / artifact)
        inventories = list(directory.glob("**/inventory.json"))
        if len(inventories) != 1:
            raise ValueError("Expected one inventory for " + artifact)
        path = inventories[0]
        inventory = _read(path)
        report = _read(path.parent / "report.json")
        candidate = _read(path.parent / "candidate.json")
        shard = inventory.get("shard", {})
        if (report.get("schema_version") != 2 or type(inventory.get("schema_version")) is not int
                or inventory["schema_version"] != 1 or not _clean_candidate(candidate, tested_sha, tested_tree)
                or report.get("status") != "passed"
                or report.get("suite") != "tests" or report.get("shard") != f"{index}/{total}"
                or inventory.get("status") != "passed" or inventory.get("exit_status") != 0
                or inventory.get("catalog_sha256") != catalog_hash or inventory.get("catalog_id") != catalog["id"]
                or shard.get("index") != index or shard.get("count") != total):
            raise ValueError("Shard evidence has an unclean/foreign identity or unsuccessful outcome: " + artifact)
        selected = [case for case in inventory["cases"] if case["selected"]]
        ids = [case["id"] for case in selected]
        selected_modules = {case["path"] for case in selected}
        recorded_modules = shard.get("modules")
        if (not selected or len(ids) != len(set(ids)) or cases.intersection(ids)
                or not isinstance(recorded_modules, list) or len(recorded_modules) != len(set(recorded_modules))
                or selected_modules != set(recorded_modules) or modules.intersection(selected_modules)
                or inventory.get("selected") != len(selected)
                or any(case["outcome"] not in {"passed", "skipped"} for case in selected)):
            raise ValueError("Shard evidence overlaps, omits cases or contradicts its selection: " + artifact)
        check = next((row for row in report["checks"] if row["name"] == "tests"), {})
        bound = check.get("tool_report", {})
        if (check.get("status") != "passed" or bound.get("sha256") != hashlib.sha256(path.read_bytes()).hexdigest()
                or bound.get("selected") != len(selected)):
            raise ValueError("Shard inventory is not the qualified artifact: " + artifact)
        outcomes = dict(Counter(case["outcome"] for case in selected))
        rows.append({"artifact": artifact, "shard": f"{index}/{total}", "selected": len(selected),
                     "outcomes": outcomes, "command_seconds": check.get("duration_seconds")})
        for case in selected:
            if case["family"] not in catalog["families"] or case["path"] not in catalog["families"][case["family"]]["modules"]:
                raise ValueError("Shard case has foreign family ownership")
            families.setdefault(case["family"], Counter())[case["outcome"]] += 1
        modules.update(selected_modules)
        cases.update(ids)
    if modules != expected_modules:
        raise ValueError("Complete shard evidence does not cover the catalog modules")
    return rows, {name: dict(counts) for name, counts in sorted(families.items())}


def evaluate(needs, *, event, source_sha, tested_sha, workflow, artifacts, catalog, context=None, metadata=None):
    package = "package" in workflow["jobs"]
    docs, errors = job_verdict(needs, event=event, package=package)
    result = {"schema_version": 2 if context is not None or metadata is not None else 1,
              "status": "failed", "source_sha": source_sha, "tested_sha": tested_sha,
              "documentation_only": docs, "jobs": needs if isinstance(needs, dict) else {}, "shards": [], "families": {}, "suites": [], "errors": errors}
    try:
        directories, tested_tree = {}, None
        if context is not None or metadata is not None:
            candidate_evidence.validate_context(context)
            result["candidate_context"] = context
            if (context["source_sha"] != source_sha or context["checkout"]["commit"] != tested_sha
                    or context["event"]["name"] != event):
                raise ValueError("Aggregate inputs contradict the captured candidate")
            captured = _read(artifacts / f"candidate-context--attempt-{context['producer']['run_attempt']}" / "context.json")
            if captured != context:
                raise ValueError("Aggregate context does not match the original captured context")
            expected = candidate_evidence.expected_lanes(workflow, documentation_only=docs)
            manifests = {}
            for path in artifacts.glob("*--attempt-*/producer.json"):
                manifests[path.parent.name] = _read(path)
            references = candidate_evidence.qualify_lineage(context, manifests, metadata, expected)
            tested_tree = context["checkout"]["tree"]
            directories = {row["lane"]: artifacts / row["artifact_name"] for row in references}
            result.update(schema_version=2, candidate_context=context, evidence_references=references,
                          attempts=[{key: job.get(key) for key in ("id", "name", "run_attempt", "status", "conclusion")}
                                    for job in metadata["jobs"]])
        for name, suites in ARTIFACTS.items():
            if docs and name != "static-checks-python-3.12":
                continue
            for _, report in _reports(directories.get(name, artifacts / name), suites, tested_sha, tested_tree):
                result["suites"].append({"suite": report["suite"], "status": report["status"], "artifact": name})
        if not docs:
            result["shards"], result["families"] = shard_evidence(artifacts, workflow, tested_sha=tested_sha, catalog=catalog,
                                                               directories=directories, tested_tree=tested_tree)
            if package:
                reports = list(directories.get("package-acceptance-evidence", artifacts / "package-acceptance-evidence").glob("**/report.json"))
                if len(reports) != 1:
                    raise ValueError("Expected one installed-package report")
                report = _read(reports[0])
                if (type(report.get("schema_version")) is not int or report["schema_version"] != 1
                        or report.get("status") != "passed" or not _clean_candidate(report.get("candidate"), tested_sha, tested_tree)):
                    raise ValueError("Installed-package evidence has an unclean/foreign identity or did not pass")
                result["suites"].append({"suite": "installed-package", "status": "passed",
                                         "artifact": "package-acceptance-evidence"})
    except (OSError, ValueError, KeyError, TypeError, StopIteration, RecursionError) as error:
        errors.append("Evidence qualification: " + str(error))
    result["status"] = "failed" if errors else "passed"
    result["jev"] = jev_evidence(artifacts, needs, tested_sha=tested_sha)
    return result


def markdown(result, run_url):
    """Render existing observations; counts do not establish assertion quality."""
    def text(value):
        value = str(value).replace("&", "&amp;").replace("<", "&lt;").replace("\n", " ").replace("\r", " ")
        for char in ("\\", "|", "`", "[", "]"):
            value = value.replace(char, "\\" + char)
        return value
    rows = ["## Evidence qualification: " + result["status"], "", f"Source head: `{result['source_sha']}`  ",
            f"Tested commit: `{result['tested_sha']}`", "", "| Job | Outcome |", "|---|---|"]
    context = result.get("candidate_context")
    if isinstance(context, dict) and "checkout" in context:
        rows[4:4] = [f"Tested base: `{context.get('tested_base_sha') or 'not applicable'}`  ",
                     f"Tested tree: `{context['checkout'].get('tree', 'unknown')}`  ",
                     f"GitHub check association: `{context.get('producer', {}).get('check_sha', 'unknown')}`", ""]
    for name, job in sorted(result["jobs"].items()):
        outcome = job.get("result", "missing") if isinstance(job, dict) else "malformed"
        rows.append(f"| {text(name)} | {text(outcome)} |")
    rows.extend(["", "| Shard | Passed | Skipped | Command seconds |", "|---|---:|---:|---:|"])
    for shard in result["shards"]:
        counts = shard["outcomes"]
        rows.append(f"| {shard['shard']} | {counts.get('passed', 0)} | {counts.get('skipped', 0)} | {shard['command_seconds']} |")
    if result.get("evidence_references"):
        rows.extend(["", "| Evidence lane | Original run / attempt | Job | Artifact |", "|---|---|---|---|"])
        for reference in result["evidence_references"]:
            rows.append(f"| {text(reference['lane'])} | {reference['run_id']} / {reference['run_attempt']} | "
                        f"{reference['job_id']} | {reference['artifact_id']} |")
    if result["families"]:
        rows.extend(["", "| Family | Passed | Skipped |", "|---|---:|---:|"])
        for name, counts in result["families"].items():
            rows.append(f"| {text(name)} | {counts.get('passed', 0)} | {counts.get('skipped', 0)} |")
    if result["documentation_only"]:
        rows.extend(["", "Documentation-only PR: heavy checks explicitly skipped; static evidence retained."])
    diagnostic = result.get("jev", {})
    rows.extend(["", "## Jev advisory diagnostics", "",
                 f"Status: **{text(diagnostic.get('status', 'missing'))}**; diagnostic job: {text(diagnostic.get('job_outcome', 'missing'))}.",
                 "Current tested revision: " + ("yes" if diagnostic.get("current") else "unverified; no current assessment is claimed") + "."])
    if diagnostic.get("reason"):
        rows.append("Reason: " + text(diagnostic["reason"]) + ".")
    if "provider_status" in diagnostic:
        rows.append(f"Provider HTTP status: {diagnostic['provider_status']}.")
    if diagnostic.get("current"):
        if diagnostic.get("model"):
            rows.append("Model: " + text(diagnostic["model"]) + ".")
        rows.extend(["", "| Candidate | Check | Outcome | Confidence | Distribution |", "|---|---|---|---:|---|"])
        for assessment in diagnostic.get("assessments", []):
            distribution = "; ".join(f"{name}: {value:g}" for name, value in sorted(assessment["details"].get("probabilities", {}).items())) or "not supplied"
            confidence = assessment["confidence"] if assessment["confidence"] is not None else "not supplied"
            location = (f"{assessment['path']}:{assessment['start_line']}-{assessment['end_line']}"
                        if "path" in assessment else "scope-wide")
            if "candidate_id" in assessment:
                location += " (" + assessment["candidate_id"] + ")"
            rows.append(f"| {text(location)} | {text(assessment['check_id'])} | {text(assessment['outcome'])} | {confidence} | {distribution} |")
        execution = diagnostic.get("execution")
        if execution:
            rows.append(f"Assessment execution: {execution['mode']}; cache {execution['cache_status']}; "
                        f"provider calls {execution['provider_calls']}; reused assessments {execution['reused_assessments']}.")
            for label, name in (("Fresh provider usage", "fresh_usage"), ("Historical reused usage", "reused_usage")):
                usage = execution[name]
                rows.append(label + ": " + (f"{usage['input_tokens']} input / {usage['output_tokens']} output tokens"
                                             if usage is not None else "unknown") + ".")

        scope = diagnostic.get("source_scope")
        if scope:
            rows.extend(["", f"Source scope: {len(scope['selected'])} selected; {len(scope['omitted'])} omitted; "
                         f"{len(scope['truncated'])} truncated; {scope['source_bytes']}/{scope['max_source_bytes']} source bytes.",
                         "Selected: " + ", ".join(text(path) for path in scope["selected"]) + ".",
                         "Omitted sample: " + (", ".join(text(path) for path in scope["omitted"][:8]) or "none")
                         + (f"; {len(scope['omitted']) - 8} more in the artifact" if len(scope["omitted"]) > 8 else "") + ".",
                         "Truncated: " + (", ".join(text(path) for path in scope["truncated"]) or "none") + "."])
        if diagnostic.get("context_sha256"):
            rows.append(f"Context SHA-256: `{diagnostic['context_sha256']}`.")
    if diagnostic.get("report_sha256"):
        rows.append(f"Report SHA-256: `{diagnostic['report_sha256']}`.")
    job = result["jobs"].get("architecture", {})
    outputs = job.get("outputs", {}) if isinstance(job, dict) else {}
    artifact_url = outputs.get("artifact_url", "") if isinstance(outputs, dict) else ""
    prefix = run_url + "/artifacts/"
    if not isinstance(artifact_url, str) or not artifact_url.startswith(prefix) or not artifact_url[len(prefix):].isdigit():
        artifact_url = run_url + "#artifacts"
    rows.extend(["", f"[architecture-evidence artifact]({artifact_url})", "",
                 "Advisory outcomes do not change deterministic evidence qualification. Confidence and distributions are model outputs, not calibrated defect probabilities."])
    rows.extend(["", f"[Retained artifacts and job logs]({run_url}#artifacts)", "",
                 "The GitHub CI result check is authoritative for the final job outcome, including checkout/download/upload failures.",
                 "Jev remains advisory. Recorded counts describe execution scope; independent assertions establish behavior."])
    rows.extend("\n- " + text(error) for error in result["errors"])
    return "\n".join(rows) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("needs", "workflow", "artifacts", "catalog", "summary", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    for name in ("event", "source-sha", "tested-sha", "run-url"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--context", required=True, type=Path)
    parser.add_argument("--metadata", required=True, type=Path)
    args = parser.parse_args(argv)
    result = evaluate(_read(args.needs), event=args.event, source_sha=args.source_sha, tested_sha=args.tested_sha,
                      workflow=_read(args.workflow), artifacts=args.artifacts, catalog=_read(args.catalog),
                      context=_read(args.context), metadata=_read(args.metadata))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    with args.summary.open("a") as stream:
        stream.write(markdown(result, args.run_url))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
