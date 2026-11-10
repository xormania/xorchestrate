"""Combine existing CI outcomes and retained receipts into one reviewable result."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

ALWAYS = ("paths", "changes", "architecture-status")
HEAVY = ("application-tests", "application", "verification", "tmux-acceptance")
ARTIFACTS = {
    "static-checks-python-3.12": ("static",),
    "calibration-python-3.12": ("calibration",),
    "composed-protocol-evidence": ("multi-harness",),
    "tmux-acceptance-evidence": ("acceptance", "stories"),
}


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
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("Expected an evidence object: " + str(path))
    return value


def _clean_candidate(candidate, tested_sha):
    return isinstance(candidate, dict) and candidate.get("commit") == tested_sha and candidate.get("status") == ""


def _reports(directory, suites, tested_sha):
    reports = []
    for path in sorted(directory.glob("**/report.json")):
        value = _read(path)
        if value.get("schema_version") == 2 and value.get("suite") in suites:
            candidate = _read(path.parent / "candidate.json")
            if not _clean_candidate(candidate, tested_sha):
                raise ValueError("Suite evidence does not identify the clean tested commit: " + value["suite"])
            reports.append((path, value))
    for suite in suites:
        matches = [value for _, value in reports if value["suite"] == suite]
        if len(matches) != 1 or matches[0].get("status") != "passed":
            raise ValueError("Required suite evidence did not pass exactly once: " + suite)
    return reports


def shard_evidence(artifacts, workflow, *, tested_sha, catalog):
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
        directory = artifacts / artifact
        inventories = list(directory.glob("**/inventory.json"))
        if len(inventories) != 1:
            raise ValueError("Expected one inventory for " + artifact)
        path = inventories[0]
        inventory = _read(path)
        report = _read(path.parent / "report.json")
        candidate = _read(path.parent / "candidate.json")
        shard = inventory.get("shard", {})
        if (report.get("schema_version") != 2 or type(inventory.get("schema_version")) is not int
                or inventory["schema_version"] != 1 or not _clean_candidate(candidate, tested_sha)
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


def evaluate(needs, *, event, source_sha, tested_sha, workflow, artifacts, catalog):
    package = "package" in workflow["jobs"]
    docs, errors = job_verdict(needs, event=event, package=package)
    result = {"schema_version": 1, "status": "failed", "source_sha": source_sha, "tested_sha": tested_sha,
              "documentation_only": docs, "jobs": needs if isinstance(needs, dict) else {}, "shards": [], "families": {}, "suites": [], "errors": errors}
    try:
        for name, suites in ARTIFACTS.items():
            if docs and name != "static-checks-python-3.12":
                continue
            for _, report in _reports(artifacts / name, suites, tested_sha):
                result["suites"].append({"suite": report["suite"], "status": report["status"], "artifact": name})
        if not docs:
            result["shards"], result["families"] = shard_evidence(artifacts, workflow, tested_sha=tested_sha, catalog=catalog)
            if package:
                reports = list((artifacts / "package-acceptance-evidence").glob("**/report.json"))
                if len(reports) != 1:
                    raise ValueError("Expected one installed-package report")
                report = _read(reports[0])
                if (type(report.get("schema_version")) is not int or report["schema_version"] != 1
                        or report.get("status") != "passed" or not _clean_candidate(report.get("candidate"), tested_sha)):
                    raise ValueError("Installed-package evidence has an unclean/foreign identity or did not pass")
                result["suites"].append({"suite": "installed-package", "status": "passed",
                                         "artifact": "package-acceptance-evidence"})
    except (OSError, ValueError, KeyError, TypeError, StopIteration) as error:
        errors.append("Evidence qualification: " + str(error))
    result["status"] = "failed" if errors else "passed"
    return result


def markdown(result, run_url):
    """Render existing observations; counts do not establish assertion quality."""
    def text(value):
        return str(value).replace("|", "\\|").replace("\n", " ").replace("<", "&lt;")
    rows = ["## Evidence qualification: " + result["status"], "", f"Source head: `{result['source_sha']}`  ",
            f"Tested commit: `{result['tested_sha']}`", "", "| Job | Outcome |", "|---|---|"]
    for name, job in sorted(result["jobs"].items()):
        outcome = job.get("result", "missing") if isinstance(job, dict) else "malformed"
        rows.append(f"| {text(name)} | {text(outcome)} |")
    rows.extend(["", "| Shard | Passed | Skipped | Command seconds |", "|---|---:|---:|---:|"])
    for shard in result["shards"]:
        counts = shard["outcomes"]
        rows.append(f"| {shard['shard']} | {counts.get('passed', 0)} | {counts.get('skipped', 0)} | {shard['command_seconds']} |")
    if result["families"]:
        rows.extend(["", "| Family | Passed | Skipped |", "|---|---:|---:|"])
        for name, counts in result["families"].items():
            rows.append(f"| {text(name)} | {counts.get('passed', 0)} | {counts.get('skipped', 0)} |")
    if result["documentation_only"]:
        rows.extend(["", "Documentation-only PR: heavy checks explicitly skipped; static evidence retained."])
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
    args = parser.parse_args(argv)
    result = evaluate(_read(args.needs), event=args.event, source_sha=args.source_sha, tested_sha=args.tested_sha,
                      workflow=_read(args.workflow), artifacts=args.artifacts, catalog=_read(args.catalog))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    with args.summary.open("a") as stream:
        stream.write(markdown(result, args.run_url))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
