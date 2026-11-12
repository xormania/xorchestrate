"""Replay pinned engineering incidents without treating model labels as proof."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import subprocess
import tarfile
import tempfile
import time
from copy import deepcopy
from pathlib import Path, PurePosixPath

from scripts.testkit.adapters.evidence import candidate_identity, canonical, write_json
from xorchestrate.model import OrchestrationError

from .context import build_context
from .effectiveness import OUTCOMES
from .python import check

DEFAULT_CATALOG = Path("tests/fixtures/jev-operational/catalog.json")
FULL_INPUTS = (
    "scripts/architecture/", "contracts/hexagonal", "tests/fixtures/architecture-effectiveness",
    "tests/fixtures/jev-operational/", "xorchestrate/adapters/evaluation.py",
    "xorchestrate/assessment.py", "tests/hexagonal/test_architecture", "tests/hexagonal/test_jev_",
    "tests/hexagonal/contracts/jev_", "tests/hexagonal/support/jev_",
    ".github/workflows/ci.yml",
)


def _hash(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _question_identity():
    directory = Path(__file__).parent
    paths = [directory / "atomic.py", directory / "requests.py",
             directory.parents[1] / "xorchestrate/adapters/evaluation.py"]
    return _hash({path.name: hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None for path in paths})


def _hex(value, length):
    return isinstance(value, str) and len(value) == length and set(value) <= set("0123456789abcdef")


def _relative(value):
    if (not isinstance(value, str) or not value or "\\" in value or "\0" in value
            or PurePosixPath(value).is_absolute() or any(part in {"", ".", ".."} for part in value.split("/"))):
        raise ValueError("Catalog paths must be confined relative paths.")
    return value


def _finite(value):
    try:
        return type(value) in {int, float} and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def validate_catalog(catalog):
    if (not isinstance(catalog, dict) or catalog.get("schema_version") != 1
            or not isinstance(catalog.get("id"), str) or not isinstance(catalog.get("cases"), list)
            or not catalog["cases"]):
        raise ValueError("Invalid operational catalog.")
    ids, groups = set(), {}
    for case in catalog["cases"]:
        if not isinstance(case, dict) or not isinstance(case.get("id"), str) or not case["id"] or case["id"] in ids:
            raise ValueError("Case IDs must be nonempty and unique.")
        ids.add(case["id"])
        if not _hex(case.get("source_ref"), 40) or case.get("split") not in {"tuning", "heldout"}:
            raise ValueError("Cases need immutable source refs and an explicit split.")
        _relative(case.get("profile_path"))
        pins, requirements, selection = case.get("pins"), case.get("requirements"), case.get("selection")
        if not isinstance(pins, list) or not pins or not isinstance(requirements, list) or not requirements:
            raise ValueError("Cases need source pins and explicit operational requirements.")
        paths = set()
        for pin in pins:
            if not isinstance(pin, dict) or not _hex(pin.get("sha256"), 64) or _relative(pin.get("path")) in paths:
                raise ValueError("Source pins must be distinct and hash-bound.")
            paths.add(pin["path"])
        if case["profile_path"] not in paths:
            raise ValueError("The architecture profile must also be pinned.")
        requirement_ids = set()
        for requirement in requirements:
            if (not isinstance(requirement, dict) or not isinstance(requirement.get("id"), str)
                    or not requirement["id"] or requirement["id"] in requirement_ids
                    or not isinstance(requirement.get("statement"), str) or not requirement["statement"].strip()):
                raise ValueError("Requirements need distinct IDs and nonempty statements.")
            requirement_ids.add(requirement["id"])
        if not isinstance(selection, list) or not selection:
            raise ValueError("A case must select source spans.")
        for span in selection:
            if (not isinstance(span, dict) or span.get("path") not in paths
                    or type(span.get("start_line")) is not int or type(span.get("end_line")) is not int
                    or not 1 <= span["start_line"] <= span["end_line"]):
                raise ValueError("Invalid selected source span.")
        oracle = case.get("oracle")
        if (not isinstance(oracle, dict) or type(oracle.get("fault")) is not bool
                or not isinstance(oracle.get("findings"), list) or oracle["fault"] != bool(oracle["findings"])):
            raise ValueError("Independent fault labels need matching source witnesses.")
        for finding in oracle["findings"]:
            if (not isinstance(finding, dict) or finding.get("path") not in paths
                    or type(finding.get("start_line")) is not int or type(finding.get("end_line")) is not int
                    or not 1 <= finding["start_line"] <= finding["end_line"]
                    or not isinstance(finding.get("requirements"), list) or not finding["requirements"]
                    or any(not isinstance(value, str) or value not in requirement_ids for value in finding["requirements"])
                    or len(finding["requirements"]) != len(set(finding["requirements"]))):
                raise ValueError("Oracle findings need exact requirement IDs and spans.")
        if case.get("fixture_outcome") not in OUTCOMES or not isinstance(case.get("provenance"), dict):
            raise ValueError("Fixture predictions and historical provenance must be explicit.")
        group = case.get("group")
        if not isinstance(group, str) or not group:
            raise ValueError("Cases need a dependency group.")
        previous = groups.setdefault(group, {"split": case["split"], "requirements": requirements, "faults": set()})
        if previous["split"] != case["split"] or previous["requirements"] != requirements:
            raise ValueError("A dependency group cannot cross splits or change its requirements.")
        previous["faults"].add(oracle["fault"])
    if any(group["faults"] != {False, True} for group in groups.values()):
        raise ValueError("Each group needs a faulty revision and a healthy control.")


def load_catalog(path):
    value = json.loads(Path(path).read_text())
    validate_catalog(value)
    return value


def classify_evaluation_changes(paths):
    if paths is None:
        return {"mode": "full", "reason": "change_scope_unavailable", "paths": []}
    if not isinstance(paths, (list, tuple)) or any(not isinstance(path, str) for path in paths):
        raise ValueError("Changed paths must be a list of paths or null.")
    selected = sorted(set(paths))
    relevant = [path for path in selected if any(path.startswith(prefix) for prefix in FULL_INPUTS)]
    return {"mode": "full" if relevant else "health", "reason": "evaluation_inputs_changed" if relevant else "live_health_only",
            "paths": selected, "evaluation_paths": relevant}


def _snapshot(root, case, destination):
    """Read immutable source bytes; never import or execute a historical module."""
    destination.mkdir()
    result = subprocess.run(["git", "archive", "--format=tar", case["source_ref"]], cwd=root,
                            capture_output=True, check=False, timeout=30)
    if result.returncode or len(result.stdout) > 128 * 1024 * 1024:
        raise ValueError("Pinned source archive is unavailable or exceeds the archive bound.")
    with tarfile.open(fileobj=io.BytesIO(result.stdout), mode="r:") as archive:
        members = {member.name: member for member in archive.getmembers() if member.isfile()}
        profile_member = members.get(case["profile_path"])
        if profile_member is None:
            raise ValueError("Pinned architecture profile is missing.")
        profile = json.loads(archive.extractfile(profile_member).read())
        for name, member in members.items():
            _relative(name)
            if name == case["profile_path"] or (name.endswith(".py") and any(
                    name.startswith(prefix.rstrip("/") + "/") for prefix in profile["source_roots"])):
                target = destination / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.extractfile(member).read())
    return profile


def _verify_pins(root, case):
    for pin in case["pins"]:
        path = root / pin["path"]
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != pin["sha256"]:
            raise ValueError("Pinned source bytes do not match the catalog.")
    for span in [*case["selection"], *case["oracle"]["findings"]]:
        if span["end_line"] > len((root / span["path"]).read_text().splitlines()):
            raise ValueError("Catalog source span exceeds its pinned file.")


def operational_subjects(context, requirements):
    """Build source/contract subjects only; labels and discovery history stay out."""
    evidence = {}
    for unit in context.get("sources", []):
        # Regression tests are evaluation witnesses, never model answer keys.
        if unit.get("relation") == "test_caller" or unit.get("path", "").startswith("tests/"):
            continue
        identity = unit.get("unit_id") or _hash(unit)
        evidence[identity] = {"kind": "source", "path": unit["path"], "sha256": unit["sha256"],
                              "start_line": unit["start_line"], "end_line": unit["end_line"],
                              "content": unit["source"], "relation": unit.get("relation")}
    subjects = []
    for candidate in context.get("candidates", []):
        source = {key: candidate[key] for key in ("path", "sha256", "start_line", "end_line")}
        for requirement in requirements:
            binding = {"source": source, "requirement": requirement}
            subjects.append({"subject_id": _hash(binding), "check_id": requirement["id"],
                             "requirement_id": requirement["id"], "source": source,
                             "claim": f"The source candidate violates this operational requirement: {requirement['statement']}",
                             "evidence": {**evidence, "requirement": {"kind": "contract", "content": requirement["statement"],
                                                                       "sha256": _hash(requirement)}},
                             "required_evidence": ["source", "contract"]})
    return subjects


def _live_provider(subjects, options):
    from .atomic import TRANSPORT, evaluate_subjects, execution_summary
    from .requests import BUDGET_OPTIONS

    allowed = TRANSPORT | BUDGET_OPTIONS | {"threshold", "cache_dir", "cache_ttl_seconds", "request_limits",
                                             "max_support_evidence"}
    transport = {key: value for key, value in options.items() if key in allowed}
    try:
        assessments, executions = evaluate_subjects(subjects, transport)
    except OrchestrationError as error:
        return {"status": "unavailable" if error.code == "evaluation_unavailable" else "error",
                "reason": error.code, "assessments": [],
                "execution": error.details.get("execution", {})}
    execution = execution_summary(executions)
    models = {value.get("details", {}).get("model") for item in assessments
              for value in item.get("judgments", {}).values()}
    models.discard(None)
    failures = {item["reason"] for item in assessments if item.get("reason")}
    status = ("unavailable" if failures == {"evaluation_unavailable"} else "error" if failures
              else "complete")
    return {"status": status, "assessments": assessments, "execution": execution,
            "model": next(iter(models)) if len(models) == 1 else None, "requested_model": options.get("model")}


def _fixture_provider(subjects, case):
    return {"status": "complete", "provider": "scripted_fixture", "model": "fixture-v1",
            "assessments": [{"subject_id": subject["subject_id"], "outcome": case["fixture_outcome"]} for subject in subjects],
            "execution": {"provider_calls": 0, "cache_status": "disabled", "fresh_usage": None, "reused_usage": None}}


def health(*, mode="fixture", options=None, provider_fn=None):
    """One intentional source control through the actual evaluator, without history."""
    if mode not in {"fixture", "live"}:
        raise ValueError("Invalid health mode.")
    source = "def forward(port, value):\n    return port(value)\n"
    sha = hashlib.sha256(source.encode()).hexdigest()
    context = {"candidates": [{"path": "health/control.py", "sha256": sha, "start_line": 1, "end_line": 2}],
               "sources": [{"unit_id": "health-source", "path": "health/control.py", "sha256": sha,
                            "start_line": 1, "end_line": 2, "source": source, "relation": "candidate"}]}
    requirements = [{"id": "forward_unchanged", "statement": "The function forwards its value to the injected callable unchanged."}]
    subjects = operational_subjects(context, requirements)
    tick = time.monotonic()
    health_options = dict(options or {})
    health_options.pop("cache_dir", None)  # A copied answer cannot establish current transport health.
    diagnostic = (_fixture_provider(subjects, {"fixture_outcome": "no_concern"}) if mode == "fixture"
                  else (provider_fn or _live_provider)(subjects, health_options))
    diagnostic = _checked_result(diagnostic, subjects)
    return {"schema_version": 1, "health_only": True, "mode": mode,
            "measurement_basis": "scripted_fixture_only" if mode == "fixture" else "intentional_live_health_control",
            "packet_sha256": _hash(subjects), "question_sha256": _hash([subject["claim"] for subject in subjects]),
            "subjects": subjects, "diagnostics": diagnostic, "latency_seconds": time.monotonic() - tick,
            "expected_control_outcome": "no_concern", "operational_benefit": None,
            "limits": ["This small control measures evaluator availability/protocol and its explicit control answer, not engineering benefit."]}


def _checked_result(value, subjects):
    if not isinstance(value, dict):
        return {"status": "error", "reason": "invalid_provider_result", "assessments": []}
    assessments = value.get("assessments", [])
    expected = {subject["subject_id"] for subject in subjects}
    if (not isinstance(assessments, list) or any(not isinstance(item, dict) or item.get("outcome") not in OUTCOMES
                                               for item in assessments)):
        return {"status": "error", "reason": "invalid_assessment_population", "assessments": []}
    identities = [item.get("subject_id") for item in assessments]
    if value.get("status") == "complete" and (any(not isinstance(identity, str) for identity in identities)
                                              or len(identities) != len(expected) or set(identities) != expected):
        return {"status": "error", "reason": "invalid_assessment_population", "assessments": []}
    return value


def _dispositions(value, catalog):
    if value is None:
        return []
    if not isinstance(value, dict) or value.get("schema_version") != 1 or not isinstance(value.get("entries"), list):
        raise ValueError("Invalid disposition ledger.")
    cases = {case["id"]: case for case in catalog["cases"]}
    seen = set()
    for entry in value["entries"]:
        case = cases.get(entry.get("case_id")) if isinstance(entry, dict) and isinstance(entry.get("case_id"), str) else None
        if (case is None or entry.get("source_ref") != case["source_ref"]
                or not _hex(entry.get("packet_sha256"), 64) or not _hex(entry.get("subject_id"), 64)
                or entry.get("status") not in {"confirmed", "dismissed", "unconfirmed"}
                or (entry.get("reviewer_minutes") is not None and not _finite(entry["reviewer_minutes"]))):
            raise ValueError("Disposition identity or measured review time is invalid.")
        key = (entry["case_id"], entry["packet_sha256"], entry["subject_id"])
        if key in seen:
            raise ValueError("Duplicate disposition binding.")
        seen.add(key)
        if entry["status"] == "confirmed":
            witness = entry.get("witness")
            if not isinstance(witness, dict) or not _hex(witness.get("sha256"), 64) or not isinstance(witness.get("path"), str):
                raise ValueError("Confirmation requires a hash-bound independent witness file.")
    return value["entries"]


def _witness(entry, case, subject):
    witness = entry["witness"]
    path = Path(witness["path"])
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != witness["sha256"]:
        raise ValueError("Independent witness bytes changed.")
    value = json.loads(raw)
    if (not isinstance(value, dict) or value.get("schema_version") != 1 or value.get("case_id") != case["id"]
            or value.get("source_ref") != case["source_ref"] or value.get("requirement_id") != subject["requirement_id"]
            or value.get("verdict") != "defect_reproduced"
            or value.get("source_sha256") != subject["source"]["sha256"]
            or not isinstance(value.get("command"), list) or not value["command"]
            or any(not isinstance(arg, str) for arg in value["command"])
            or not isinstance(value.get("test_id"), str) or not value["test_id"]
            or not isinstance(value.get("assertion"), str) or not value["assertion"]):
        raise ValueError("Independent witness does not establish this source/requirement binding.")
    return {"sha256": witness["sha256"], "record": value}


def _observed(diagnostics, subjects, case, packet_hash, entries, mode):
    if diagnostics.get("status") != "complete":
        return {"outcome": "insufficient_evidence", "grounded": False, "known_fault_match": False,
                "confirmed": False, "unconfirmed": False, "disposition_status": "unconfirmed",
                "reviewer_minutes": None, "witnesses": []}
    by_id = {subject["subject_id"]: subject for subject in subjects}
    assessments = diagnostics.get("assessments", [])
    concerns = [item for item in assessments if item.get("outcome") == "concern"]
    outcome = "concern" if concerns else ("no_concern" if assessments and all(
        item.get("outcome") == "no_concern" for item in assessments) else "insufficient_evidence")
    match, grounded, confirmed, disposition, minutes = False, False, False, "unconfirmed", None
    witnesses, review_records = [], {}
    for item in concerns:
        subject = by_id.get(item.get("subject_id"))
        if subject is None:
            continue
        grounded = True
        source = subject["source"]
        known = any(source["path"] == finding["path"] and source["start_line"] <= finding["end_line"]
                    and finding["start_line"] <= source["end_line"] and subject["requirement_id"] in finding["requirements"]
                    for finding in case["oracle"]["findings"])
        match |= known
        entry = next((entry for entry in entries if entry["case_id"] == case["id"]
                      and entry["packet_sha256"] == packet_hash and entry["subject_id"] == subject["subject_id"]), None)
        if entry is not None:
            disposition, minutes = entry["status"], entry.get("reviewer_minutes")
            review_id = _hash({key: entry[key] for key in ("case_id", "source_ref", "packet_sha256", "subject_id")})
            review_records[review_id] = minutes
            if entry["status"] == "confirmed":
                proof = _witness(entry, case, subject)
                if not known:
                    raise ValueError("Confirmation contradicts the selected historical fault oracle.")
                witnesses.append(proof)
                confirmed |= mode == "live"
    return {"outcome": outcome, "grounded": grounded, "known_fault_match": bool(match),
            "confirmed": bool(confirmed), "unconfirmed": bool(concerns) and not confirmed,
            "disposition_status": disposition, "reviewer_minutes": minutes, "review_records": review_records,
            "witnesses": witnesses}


def _usage(rows, key):
    values = [row["diagnostics"].get("execution", {}).get(key) for row in rows]
    if any(value is None for value in values):
        return None
    if any(not isinstance(value, dict) or any(type(value.get(name)) is not int or value[name] < 0
                                            for name in ("input_tokens", "output_tokens")) for value in values):
        raise ValueError("Invalid provider usage counters.")
    return {name: sum(value[name] for value in values) for name in ("input_tokens", "output_tokens")}


def summarize_operational(rows):
    if not isinstance(rows, list) or not rows:
        raise ValueError("An operational report needs observed attempts.")
    keys = [(row["id"], row["repetition"]) for row in rows]
    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate operational attempts.")
    summary = {"cases": len({row["id"] for row in rows}), "attempts": len(rows), "known_fault_matches": 0,
               "confirmed_findings": 0, "unconfirmed_findings": 0, "false_alerts": 0, "misses": 0,
               "unknown": 0, "unavailable": 0, "errors": 0, "provider_calls": 0, "unknown_call_counts": 0,
               "cache_hits": 0, "coverage": {"eligible": 0, "ineligible": 0},
               "baseline": {"architecture": {"pass": 0, "violation": 0, "error": 0}, "historical_codex_findings": 0},
               "cost": {"status": "unknown", "reason": "no_rate_card_or_invoice"}}
    groups, minutes, stability, reviews = set(), [], {}, {}
    unique = {name: set() for name in ("known_fault_matches", "confirmed_findings", "unconfirmed_findings")}
    for row in rows:
        groups.add(row["group"])
        status, observed = row["diagnostics"].get("status"), row["observed"]
        if observed["outcome"] not in OUTCOMES or type(observed["confirmed"]) is not bool:
            raise ValueError("Invalid operational outcome.")
        eligible = row["coverage"]["target_complete"]
        summary["coverage"]["eligible" if eligible else "ineligible"] += 1
        baseline_status = row["baseline"].get("status", "error")
        summary["baseline"]["architecture"][baseline_status if baseline_status in {"pass", "violation"} else "error"] += 1
        execution = row["diagnostics"].get("execution", {})
        calls = execution.get("provider_calls")
        if type(calls) is int and calls >= 0:
            summary["provider_calls"] += calls
        else:
            summary["unknown_call_counts"] += 1
        summary["cache_hits"] += execution.get("cache_status") == "hit"
        minutes.append(observed.get("reviewer_minutes"))
        reviews.update(observed.get("review_records", {}))
        if status in {"unavailable", "not_configured"}:
            summary["unavailable"] += 1
        elif status == "error":
            summary["errors"] += 1
        elif status != "complete" or not eligible or observed["outcome"] == "insufficient_evidence":
            summary["unknown"] += 1
        else:
            summary["known_fault_matches"] += observed["known_fault_match"]
            summary["confirmed_findings"] += observed["confirmed"]
            summary["unconfirmed_findings"] += observed["unconfirmed"]
            summary["false_alerts"] += not row["expected_fault"] and observed["outcome"] == "concern"
            summary["misses"] += row["expected_fault"] and observed["outcome"] == "no_concern"
            finding_key = (row["id"], row["source_ref"], row["packet_sha256"])
            for name, field in (("known_fault_matches", "known_fault_match"), ("confirmed_findings", "confirmed"),
                                ("unconfirmed_findings", "unconfirmed")):
                if observed[field]:
                    unique[name].add(finding_key)
        stability.setdefault(row["id"], []).append(row)
    summary["groups"] = len(groups)
    summary["baseline"]["historical_codex_findings"] = len({row["group"] for row in rows if row["expected_fault"]
        and row.get("provenance", {}).get("finding_url")})
    summary["missing_review_times"] = sum(value is None for value in minutes)
    known_minutes = [value for value in (reviews.values() if reviews else minutes) if value is not None]
    summary["reviewer_minutes"] = sum(known_minutes) if all(value is not None for value in minutes) else None
    summary["observed_review_minutes"] = sum(known_minutes) if known_minutes else None
    summary["fresh_usage"], summary["reused_usage"] = _usage(rows, "fresh_usage"), _usage(rows, "reused_usage")
    stable = {"compared_cases": 0, "changed_cases": 0, "identical_cases": 0, "cache_replays_excluded": 0}
    for values in stability.values():
        fresh = []
        for row in values:
            if row["diagnostics"].get("execution", {}).get("cache_status") == "hit":
                stable["cache_replays_excluded"] += 1
            elif (row["diagnostics"].get("status") == "complete" and row["coverage"]["target_complete"]
                  and row.get("model_identity") is not None):
                fresh.append(row)
        if len(fresh) < 2 or len({(row["packet_sha256"], row.get("question_sha256"), row.get("model_identity")) for row in fresh}) != 1:
            continue
        stable["compared_cases"] += 1
        changed = len({row["observed"]["outcome"] for row in fresh}) > 1
        stable["changed_cases" if changed else "identical_cases"] += 1
    summary["stability"] = stable
    summary["attempt_outcomes"] = {name: summary[name] for name in (
        "known_fault_matches", "confirmed_findings", "unconfirmed_findings", "false_alerts", "misses", "unknown", "unavailable", "errors")}
    summary.update({name: len(values) for name, values in unique.items()})
    summary["finding_unit"] = "unique_case_source_packet"
    return summary


def adjudicate(report, dispositions):
    """Apply independent dispositions to retained trials without more model calls."""
    if not isinstance(report, dict) or report.get("schema_version") != 1 or not isinstance(report.get("cases"), list):
        raise ValueError("Adjudication requires an operational replay report.")
    result = deepcopy(report)
    entries = _dispositions(dispositions, {"cases": result["cases"]})
    for row in result["cases"]:
        if row["packet_sha256"] != _hash(row["subjects"]):
            raise ValueError("Retained subject packet changed before adjudication.")
        row["observed"] = _observed(row["diagnostics"], row["subjects"], row, row["packet_sha256"], entries, result["mode"])
    result["summary"] = summarize_operational(result["cases"])
    result["adjudication"] = {"ledger_sha256": _hash(dispositions), "provider_calls": 0,
                               "basis": "independently_supplied_witness_records"}
    return result


def replay(catalog, *, root=None, mode="fixture", options=None, split="all", repeats=1,
           provider_fn=None, max_seconds=120, dispositions=None, health=False):
    validate_catalog(catalog)
    if mode not in {"fixture", "live"} or split not in {"all", "tuning", "heldout"}:
        raise ValueError("Invalid replay mode or split.")
    if type(repeats) is not int or not 1 <= repeats <= 10 or not _finite(max_seconds) or not 0 < max_seconds <= 900:
        raise ValueError("Invalid replay repetition or elapsed-time bound.")
    root, options = Path(root or Path.cwd()).resolve(), dict(options or {})
    entries = _dispositions(dispositions, catalog)
    selected = [case for case in catalog["cases"] if split == "all" or case["split"] == split]
    if health:
        selected = [case for case in selected if not case["oracle"]["fault"]][:1]
    if not selected:
        raise ValueError("The selected evaluation population is empty.")
    rows, snapshots = [], {}
    question_policy = _question_identity()
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="xorchestrate-jev-history-") as directory:
        for repetition in range(1, repeats + 1):
            for case in selected:
                if case["source_ref"] not in snapshots:
                    location = Path(directory) / case["source_ref"]
                    profile = _snapshot(root, case, location)
                    snapshots[case["source_ref"]] = (location, profile)
                location, profile = snapshots[case["source_ref"]]
                _verify_pins(location, case)
                baseline = check(location, profile)
                context = build_context(baseline, location, source_paths=[span["path"] for span in case["selection"]],
                                        changed_ranges=case["selection"], max_source_bytes=options.get("max_source_bytes", 64000),
                                        max_files=options.get("max_files", 16), max_candidates=options.get("max_candidates", 1))
                subjects = operational_subjects(context, case["requirements"])
                packet_hash = _hash(subjects)
                complete = bool(subjects) and all(any(
                    subject["source"]["path"] == span["path"] and subject["source"]["start_line"] <= span["start_line"]
                    and subject["source"]["end_line"] >= span["end_line"] for subject in subjects) for span in case["selection"])
                remaining = max_seconds - (time.monotonic() - started)
                tick = time.monotonic()
                if not complete:
                    diagnostic = {"status": "inconclusive", "reason": "target_source_incomplete", "assessments": []}
                elif mode == "fixture":
                    diagnostic = _fixture_provider(subjects, case)
                elif remaining <= 0:
                    diagnostic = {"status": "budget_exhausted", "reason": "overall_evaluation_deadline", "assessments": []}
                else:
                    call_options = {**options, "timeout": min(options.get("timeout", 15), remaining / max(1, len(subjects)))}
                    diagnostic = (provider_fn or _live_provider)(subjects, call_options)
                diagnostic = _checked_result(diagnostic, subjects)
                elapsed = time.monotonic() - tick
                models = {item.get("details", {}).get("model") for item in diagnostic.get("assessments", [])}
                models.discard(None)
                model = diagnostic.get("model") or (next(iter(models)) if len(models) == 1 else None)
                observed = _observed(diagnostic, subjects, case, packet_hash, entries, mode)
                rows.append({"id": case["id"], "group": case["group"], "split": case["split"], "repetition": repetition,
                             "source_ref": case["source_ref"], "pins": case["pins"], "expected_fault": case["oracle"]["fault"],
                             "provenance": case["provenance"], "oracle": case["oracle"], "baseline": baseline,
                             "packet_sha256": packet_hash, "question_sha256": _hash({"claims": [subject["claim"] for subject in subjects],
                                                                                    "policy_sha256": question_policy}),
                             "subjects": subjects, "model_identity": model, "diagnostics": diagnostic, "observed": observed,
                             "coverage": {"target_complete": complete, "source_scope": context.get("source_scope")},
                             "latency_seconds": elapsed, "latency_basis": "fixture_execution" if mode == "fixture" else "provider_adapter_wall_clock"})
    return {"schema_version": 1, "catalog_sha256": _hash(catalog), "question_policy_sha256": question_policy,
            "measurement_basis": "scripted_fixture_only" if mode == "fixture" else "historical_live_provider_replay",
            "mode": mode, "split": split, "health_only": health, "repeats": repeats, "max_seconds": max_seconds,
            "duration_seconds": time.monotonic() - started, "summary": summarize_operational(rows), "cases": rows,
            "limits": ["Historical known incidents are not prospective new-defect discovery; heldout means withheld from tuning, not unseen public code.",
                       "Unchanged architecture checking and historical Codex discovery are separate comparators, not a reconstructed full historical CI result.",
                       "A local witness records independently supplied evidence and is not a provider or execution attestation.",
                       "Unknown billing, review time and missing outcomes remain explicit; no measured engineering time saving is inferred."]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--mode", choices=("fixture", "live"), default="fixture")
    parser.add_argument("--split", choices=("all", "tuning", "heldout"), default="all")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--health", action="store_true")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--jev-config", type=Path, default=Path("contracts/hexagonal-jev.json"))
    parser.add_argument("--dispositions", type=Path)
    parser.add_argument("--adjudicate", type=Path, help="Apply dispositions to an existing replay report without provider calls")
    parser.add_argument("--max-seconds", type=float, default=120)
    parser.add_argument("--output", type=Path, default=Path(".xorchestrate/jev-operational.json"))
    parser.add_argument("--classify-changes", type=Path, help="JSON list of changed paths; outputs full/health selection")
    parser.add_argument("--select-base", help="Select full/health from changed Git paths; an unavailable base selects full")
    parser.add_argument("--github-output", type=Path, help="Append evaluation_mode when selecting a CI scope")
    args = parser.parse_args(argv)
    try:
        if args.classify_changes or args.select_base is not None:
            paths = None
            if args.classify_changes:
                paths = json.loads(args.classify_changes.read_text())
            elif args.select_base and not args.select_base.startswith("-"):
                diff = subprocess.run(["git", "diff", "--name-only", "-z", args.select_base, "HEAD", "--"],
                                      cwd=args.root, capture_output=True, check=False, timeout=30)
                if diff.returncode == 0:
                    paths = [path for path in diff.stdout.decode("utf-8").split("\0") if path]
            result = classify_evaluation_changes(paths)
            if args.github_output:
                with args.github_output.open("a") as stream:
                    stream.write("evaluation_mode=" + result["mode"] + "\n")
        else:
            options = json.loads(args.jev_config.read_text()) if args.mode == "live" and not args.adjudicate else {}
            for key, variable in (("endpoint", "JEV_ENDPOINT"), ("model", "JEV_MODEL")):
                if os.environ.get(variable):
                    options[key] = os.environ[variable]
            dispositions = json.loads(args.dispositions.read_text()) if args.dispositions else None
            if dispositions:
                for entry in dispositions.get("entries", []):
                    if isinstance(entry.get("witness"), dict) and isinstance(entry["witness"].get("path"), str):
                        entry["witness"]["path"] = str((args.dispositions.parent / entry["witness"]["path"]).resolve())
            result = (adjudicate(json.loads(args.adjudicate.read_text()), dispositions) if args.adjudicate else
                      health(mode=args.mode, options=options) if args.health else replay(
                load_catalog(args.catalog), root=args.root, mode=args.mode, options=options, split=args.split,
                repeats=args.repeat, max_seconds=args.max_seconds, dispositions=dispositions))
            result["execution"] = candidate_identity(args.root)
        write_json(args.output, result)
    except (OSError, ValueError, TypeError, subprocess.SubprocessError, tarfile.TarError) as error:
        parser.exit(2, f"Operational evaluation failed: {error}\n")
    print(f"Jev operational evidence: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
