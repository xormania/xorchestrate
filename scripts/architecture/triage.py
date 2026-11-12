"""Advisory Jev triage of candidate-bound test observations and their source."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
from xml.etree import ElementTree

from scripts.ci import candidate
from xorchestrate.adapters.evaluation import TypeSafeEvaluator

from .atomic import digest, evaluate_subjects
from .context import changed_lines

CHECKS = {
    "guarantee_gap": "The supplied assertions check a weaker behavior than the supplied guarantee requires.",
    "failure_regression": "At least one supplied assertion failure contradicts the supplied guarantee in the candidate implementation. Identify support using only the matched executed case and its concrete binding.",
}
_MAX_FILE = 2 * 1024 * 1024
_MAX_JSON = 32 * 1024 * 1024
_MAX_FAILURE = 8 * 1024 * 1024


def _read(path):
    if path.is_symlink() or path.stat().st_size > _MAX_JSON:
        raise ValueError("Unusable evidence file")
    return json.loads(path.read_text())


def _source(root, relative):
    path = root / relative
    if not path.resolve().is_relative_to(root) or path.is_symlink() or path.stat().st_size > _MAX_FILE:
        raise ValueError("Unusable source file")
    return path.read_text()


def _module_path(root, module):
    for relative in (module.replace(".", "/") + ".py", module.replace(".", "/") + "/__init__.py"):
        if (root / relative).is_file():
            return relative
    return None


def source_evidence(root, path, case_ids, *, max_bytes=24000):
    """Read complete test units and resolved bindings without importing code.

    Dependencies are a bounded static search, not a claim of execution. Unknown
    dynamic bindings and omitted units remain explicit evidence limitations.
    """
    names = list(dict.fromkeys(identifier.split("::")[1].split("[")[0] for identifier in case_ids))
    queue, visited, evidence, omitted, used = [(path, set(names), 0)], set(), {}, [], 0
    while queue and len(visited) < 16:
        relative, wanted, depth = queue.pop(0)
        marker = (relative, tuple(sorted(wanted)))
        if marker in visited:
            continue
        visited.add(marker)
        try:
            text = _source(root, relative)
            tree = ast.parse(text)
        except (OSError, ValueError, SyntaxError, UnicodeError):
            omitted.append({"path": relative, "reason": "source_unavailable"})
            continue
        lines = text.splitlines(keepends=True)
        units = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                 and (not wanted or node.name in wanted)]
        if depth == 0:
            units.sort(key=lambda node: names.index(node.name) if node.name in names else len(names))
        # Fixture functions live in the collected module; their definitions bind
        # abstract subjects to concrete setup and must accompany their callers.
        local = {node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
        pending = list(units)
        seen = set()
        referenced = set(wanted)
        while pending:
            node = pending.pop(0)
            if node.name in seen:
                continue
            seen.add(node.name)
            start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
            content = "".join(lines[start - 1:node.end_lineno])
            size = len(content.encode())
            if used + size > max_bytes:
                omitted.append({"path": relative, "symbol": node.name, "reason": "source_budget"})
                continue
            used += size
            kind = "test_source" if depth == 0 else ("contract_source" if "/contracts/" in relative else "binding_source")
            identifier = "source:" + digest([relative, start, node.end_lineno, content])[:24]
            evidence[identifier] = {"kind": kind, "path": relative, "start_line": start,
                "end_line": node.end_lineno, "sha256": hashlib.sha256(content.encode()).hexdigest(), "content": content}
            referenced.update(item.id for item in ast.walk(node) if isinstance(item, ast.Name))
            for name in sorted(referenced & local.keys()):
                if name not in seen:
                    pending.append(local[name])
        if depth >= 2:
            continue
        for node in tree.body:
            if not isinstance(node, ast.ImportFrom) or node.level or not node.module:
                continue
            for alias in node.names:
                if (alias.asname or alias.name) not in referenced:
                    continue
                submodule = _module_path(root, node.module + "." + alias.name)
                dependency = submodule or _module_path(root, node.module)
                if dependency:
                    queue.append((dependency, set() if submodule else {alias.name}, depth + 1))
    omitted.extend({"path": path, "reason": "dependency_budget"} for path, _, _ in queue)
    return evidence, omitted


def _touches(root, module, changed):
    """Select tests through bounded static bindings; this is not coverage."""
    pending, visited = [(module, 0)], set()
    while pending and len(visited) < 48:
        relative, depth = pending.pop(0)
        if relative in changed:
            return True
        if relative in visited or depth > 2:
            continue
        visited.add(relative)
        try:
            tree = ast.parse(_source(root, relative))
        except (OSError, ValueError, SyntaxError, UnicodeError):
            continue
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module and not node.level:
                for alias in node.names:
                    dependency = _module_path(root, node.module + "." + alias.name) or _module_path(root, node.module)
                    if dependency:
                        pending.append((dependency, depth + 1))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    dependency = _module_path(root, alias.name)
                    if dependency:
                        pending.append((dependency, depth + 1))
    return False


def _failures(path, case_id):
    if not path.exists():
        return []
    if path.is_symlink() or path.stat().st_size > _MAX_FAILURE:
        raise ValueError("Unusable failure evidence")
    payload = path.read_bytes()
    if b"<!DOCTYPE" in payload or b"<!ENTITY" in payload:
        raise ValueError("External XML declarations are not evidence")
    tree = ElementTree.fromstring(payload)
    failures = []
    module, _, name = case_id.partition("::")
    expected_class = module.removesuffix(".py").replace("/", ".")
    if "::" in name:
        owner, _, name = name.rpartition("::")
        expected_class += "." + owner.replace("::", ".")
    for case in tree.iter("testcase"):
        if case.get("name") != name or case.get("classname") != expected_class:
            continue
        for item in list(case):
            if item.tag in {"failure", "error"}:
                failures.append({"case": case.get("name"), "class": case.get("classname"),
                                 "message": (item.get("message", "") + "\n" + (item.text or ""))[:4000]})
    return failures[:12]


def _observations(directory, context, catalog, lane):
    """Keep outcome and identity distinct: failed tests are valid observations."""
    rows = []
    catalog_hash = hashlib.sha256(json.dumps(catalog, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    reports = [(path, _read(path)) for path in sorted(directory.glob("**/report.json"))]
    reports = [(path, report) for path, report in reports if report.get("suite") == "tests"]
    if len(reports) != 1:
        raise ValueError("Expected one test report per lane")
    index, total = map(int, lane.rsplit("shard-", 1)[1].split("-of-"))
    for path, report in reports:
        if report.get("schema_version") != 2 or report.get("suite") != "tests":
            continue
        identity = _read(path.parent / "candidate.json")
        if (identity.get("commit") != context["checkout"]["commit"] or identity.get("tree") != context["checkout"]["tree"]
                or identity.get("status") != "" or report.get("status") not in {"passed", "failed"}):
            raise ValueError("Foreign or unexecuted test evidence")
        inventory_path = path.parent / "inventory.json"
        inventory = _read(inventory_path)
        if (inventory.get("schema_version") != 1 or inventory.get("catalog_id") != catalog["id"]
                or inventory.get("catalog_sha256") != catalog_hash):
            raise ValueError("Foreign test inventory")
        checks = [check for check in report.get("checks", []) if check.get("name") == "tests"]
        if len(checks) != 1 or checks[0].get("tool_report", {}).get("sha256") != hashlib.sha256(inventory_path.read_bytes()).hexdigest():
            raise ValueError("Unbound test inventory")
        selected = [case for case in inventory.get("cases", []) if case.get("selected") is True]
        ids = [case.get("id") for case in selected]
        shard = inventory.get("shard", {})
        if (report.get("shard") != f"{index}/{total}" or shard.get("index") != index or shard.get("count") != total
                or not isinstance(shard.get("modules"), list) or len(shard["modules"]) != len(set(shard["modules"]))
                or set(shard["modules"]) != {case.get("path") for case in selected}):
            raise ValueError("Test inventory does not belong to this shard")
        failed = any(case.get("outcome") == "failed" for case in selected)
        passed = report["status"] == "passed"
        if (not selected or any(not isinstance(value, str) for value in ids) or len(ids) != len(set(ids))
                or inventory.get("selected") != len(selected) or checks[0].get("tool_report", {}).get("selected") != len(selected)
                or checks[0].get("status") != report["status"] or inventory.get("status") != report["status"]
                or type(inventory.get("exit_status")) is not int
                or (passed and (inventory["exit_status"] != 0 or failed or any(case.get("outcome") == "not_run" for case in selected)))
                or (not passed and inventory["exit_status"] == 0)):
            raise ValueError("Contradictory inventory execution")
        for case in selected:
            if case.get("selected") is not True:
                continue
            if (case.get("family") not in catalog["families"]
                    or case.get("path") not in catalog["families"][case["family"]]["modules"]
                    or case.get("outcome") not in {"passed", "failed", "skipped", "not_run"}
                    or not case["id"].startswith(case["path"] + "::")):
                raise ValueError("Unclassified or contradictory test case")
            rows.append({**case, "report_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                         "inventory_sha256": hashlib.sha256(inventory_path.read_bytes()).hexdigest(),
                         "failures": _failures(path.parent / "tests.xml", case["id"]) if case["outcome"] == "failed" else []})
    return rows


def triage(root, artifacts, result, metadata, workflow, catalog, options, *, evaluator_factory=TypeSafeEvaluator):
    """Read authenticated executed lanes; retain missing evidence as uncertainty."""
    root, artifacts = Path(root).resolve(), Path(artifacts)
    base = {"schema_version": 1, "stage": "post_test", "advisory": True, "status": "inconclusive",
            "assessments": [], "execution": [], "evidence": [], "missing_evidence": [],
            "deterministic_status": result.get("status")}
    try:
        checks = options.get("checks", list(CHECKS))
        maximum = options.get("max_subjects", 3)
        case_limit = options.get("max_cases_per_subject", 4)
        if (not isinstance(checks, list) or not checks or len(set(checks)) != len(checks)
                or any(name not in CHECKS for name in checks) or type(maximum) is not int or not 1 <= maximum <= 30
                or type(case_limit) is not int or not 1 <= case_limit <= 32):
            raise ValueError("Invalid triage configuration")
        context = candidate.validate_context(result["candidate_context"])
        if (result.get("schema_version") != 2 or result.get("tested_sha") != context["checkout"]["commit"]
                or result.get("source_sha") != context["source_sha"]
                or candidate._git(root, "rev-parse", "HEAD") != context["checkout"]["commit"]
                or candidate._git(root, "rev-parse", "HEAD^{tree}") != context["checkout"]["tree"]
                or candidate._git(root, "status", "--porcelain=v1", "--untracked-files=all") != ""
                or candidate._inputs(root) != context["inputs"]
                or _read(root / "contracts/testing.json") != catalog
                or _read(root / ".github/workflows/ci.yml") != workflow
                or _read(artifacts / f"candidate-context--attempt-{context['producer']['run_attempt']}" / "context.json") != context):
            raise ValueError("Candidate evidence is not current")
        # Validate the complete run association even when it has no usable lanes.
        candidate.qualify_lineage(context, {}, metadata, {}, allow_failed=True)
        base["candidate"] = {"source_sha": context["source_sha"], "tested_sha": context["checkout"]["commit"],
                             "tree": context["checkout"]["tree"]}
        if result.get("reuse"):
            return {**base, "reason": "behavioral_evidence_reused",
                    "missing_evidence": ["This metadata run reuses earlier qualified behavior; no fresh execution is claimed."]}
        producers = {path.parent.name: _read(path) for path in artifacts.glob("*--attempt-*/producer.json")}
        rows = []
        for lane, name in candidate.expected_lanes(workflow, documentation_only=result.get("documentation_only", False)).items():
            if not lane.startswith("framework-tests-"):
                continue
            try:
                reference = candidate.qualify_lineage(context, producers, metadata, {lane: name}, allow_failed=True)[0]
                observed = _observations(artifacts / reference["artifact_name"], context, catalog, lane)
                rows.extend({**row, "reference": reference} for row in observed)
                if not observed:
                    base["missing_evidence"].append({"lane": lane, "reason": "no_test_observations"})
            except (OSError, ValueError, TypeError, KeyError, ElementTree.ParseError):
                base["missing_evidence"].append({"lane": lane, "reason": "unqualified_or_missing_lane"})
        changed = {span["path"] for span in changed_lines(root, context.get("tested_base_sha") or "HEAD^")}
        related = {path for path in {row["path"] for row in rows} if _touches(root, path, changed)}
        modules = sorted({row["path"] for row in rows if row["outcome"] == "failed" or row["path"] in related},
                         key=lambda path: (not any(row["path"] == path and row["outcome"] == "failed" for row in rows), path))
        subjects = []
        for module in modules[:maximum]:
            observed_cases = sorted((row for row in rows if row["path"] == module),
                                    key=lambda row: (row["outcome"] != "failed", row["id"]))
            # Bound the subject itself before collecting its complete source;
            # thousands of parametrizations are not thousands of code units.
            cases, remaining, selected_units = [], [], set()
            for row in observed_cases:
                unit = row["id"].split("[")[0]
                if len(cases) < case_limit and (row["outcome"] == "failed" or unit not in selected_units):
                    cases.append(row)
                    selected_units.add(unit)
                else:
                    remaining.append(row["id"])
            if remaining:
                base["missing_evidence"].append({"path": module, "reason": "case_subject_limit", "omitted_cases": len(remaining),
                                               "omitted_case_ids_sha256": digest(remaining)})
            family = cases[0]["family"]
            sources, omitted = source_evidence(root, module, [row["id"] for row in cases],
                                                max_bytes=options.get("max_source_bytes", 24000))
            contract = {"kind": "contract", "path": "contracts/testing.json", "family": family,
                        "content": catalog["families"][family]["guarantee"]}
            contract["sha256"] = digest(contract)
            evidence = {**sources, "contract:" + family: contract}
            receipts, observations = {}, []
            for row in cases:
                receipt = {key: row[key] for key in ("report_sha256", "inventory_sha256", "reference")}
                receipt_id = digest(receipt)
                receipts[receipt_id] = receipt
                observations.append({"id": row["id"], "outcome": row["outcome"], "receipt_id": receipt_id})
            observation_packet = {"cases": observations, "receipts": receipts, "unselected_case_count": len(remaining)}
            failure_details = [{"case_id": row["id"], "failures": row["failures"], "reference": row["reference"]}
                               for row in cases if row["outcome"] == "failed" and row["failures"]]
            base["missing_evidence"].extend({"case_id": row["id"], "reason": "matching_failure_detail_unavailable"}
                                           for row in cases if row["outcome"] == "failed" and not row["failures"])
            if failure_details:
                evidence["failures:" + module] = {"kind": "failure_detail", "sha256": digest(failure_details), "content": failure_details}
            evidence["observations:" + family + ":" + module] = {"kind": "test_observation", "sha256": digest(observation_packet), "content": observation_packet}
            base["missing_evidence"].extend(omitted)
            base["evidence"].extend({"id": key, **value} for key, value in evidence.items())
            executed = any(row["outcome"] in {"passed", "failed"} for row in cases)
            for check in checks:
                if check == "failure_regression" and not any(row["outcome"] == "failed" for row in cases):
                    continue
                subjects.append({"subject_id": module, "check_id": check, "claim": CHECKS[check],
                    "selected_case_ids": [row["id"] for row in cases], "unselected_case_count": len(remaining),
                    "evidence": evidence, "required_evidence": ["contract", "test_source", "binding_source", "test_observation"]
                    + ([] if executed else ["executed_test"])
                    + (["failure_detail"] if check == "failure_regression" else []), "limitations": omitted})
        base["unselected_modules"] = modules[maximum:]
        if not subjects:
            return {**base, "reason": "no_changed_or_failed_test_subject"}
        if candidate._git(root, "status", "--porcelain=v1", "--untracked-files=all") != "":
            raise ValueError("Source changed while collecting evidence")
        base["assessments"], base["execution"] = evaluate_subjects(subjects, options, evaluator_factory=evaluator_factory)
        unavailable = all(not row["judgments"] for row in base["assessments"]) and any(
            row.get("reason") not in {None, "required_evidence_missing"} for row in base["assessments"])
        return {**base, "status": "unavailable" if unavailable else "complete",
                "concerns": sum(row["outcome"] == "concern" for row in base["assessments"])}
    except (OSError, ValueError, TypeError, KeyError, IndexError, RecursionError):
        return {**base, "status": "inconclusive", "reason": "candidate_or_configuration_unavailable"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    for name in ("artifacts", "result", "metadata", "workflow", "catalog", "jev-config", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args(argv)
    options = _read(args.jev_config)
    options.update(options.pop("post_test", {}))
    for key, variable in (("endpoint", "JEV_ENDPOINT"), ("model", "JEV_MODEL")):
        if os.environ.get(variable):
            options[key] = os.environ[variable]
    if args.cache_dir:
        options["cache_dir"] = str(args.cache_dir)
    report = triage(args.root, args.artifacts, _read(args.result), _read(args.metadata), _read(args.workflow),
                    _read(args.catalog), options)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    if args.summary:
        from scripts.ci.result import jev_triage_markdown
        with args.summary.open("a") as stream:
            stream.write(jev_triage_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
