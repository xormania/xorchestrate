"""Versioned, bounded Jev calibration; default operation is entirely offline.

Run from repository root with `python -m experiments.jev_failure_correlation.runner`.
No credential is consulted except during the explicit live subcommand.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import time
import urllib.error
import urllib.request

from devtools.jev import checked_questions, checked_response
from devtools.github_http import NoRedirect

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
ENDPOINT = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-1.13.0"
TIMEOUT = 20
MAX_ATTEMPTS = 2
MAX_RESPONSE = 131072
MAX_REQUEST = 28000
CLIENT = "xorchestrate-jev-failure-correlation/1.1.0"
LEAK = re.compile(r"\bF(?:0[1-9]|10)\b|defect_reproduced|expected_oracle|answer_key|review/tracks|root.cause|EXPECTED_VIOLATION", re.I)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def sources():
    paths = sorted(p for directory in (ROOT, ROOT / "data", ROOT / "tests", ROOT / "probe_fixtures")
                   for p in (directory.glob("*") if directory == ROOT else directory.rglob("*"))
                   if p.is_file() and p.suffix in {".py", ".json"} and "__pycache__" not in p.parts)
    paths += [REPO / p for p in ("devtools/jev.py", "devtools/jev_config.py", "devtools/github_http.py", "devtools/configuration.json", ".github/workflows/jev-failure-correlation.yml")]
    return {str(p.relative_to(REPO)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def load_data():
    cases = read(ROOT / "data/cases.json")["cases"]
    pairs = read(ROOT / "data/pairs.json")["pairs"]
    key = read(ROOT / "data/answer_key.json")
    rubric = read(ROOT / "rubric.json")
    ids = [c["id"] for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate case identifiers")
    if set(ids) != set(key["cases"]):
        raise ValueError("Answer-key identities differ from corpus")
    families = {}
    for case in cases:
        label = key["cases"][case["id"]]
        if families.setdefault(label["family"], label["split"]) != label["split"]:
            raise ValueError("Family split contamination")
        assert_visible(case["packet"])
    pair_ids = [p["id"] for p in pairs]
    if len(set(pair_ids)) != len(pair_ids):
        raise ValueError("Duplicate pair identifiers")
    if set(pair_ids) != set(key.get("relationships", key.get("pairs", {}))):
        raise ValueError("Pair answer-key identities differ")
    for pair in pairs:
        if pair["left"] not in ids or pair["right"] not in ids:
            raise ValueError("Unknown pair member")
    return cases, pairs, key, rubric


def assert_visible(packet):
    if LEAK.search(canonical(packet).decode()):
        raise ValueError("Diagnostic leakage in visible packet")
    forbidden = {"family", "split", "verdict", "expected", "finding", "rationale", "source_path"}
    def visit(value):
        if isinstance(value, dict):
            if forbidden & set(value):
                raise ValueError("Hidden label in visible packet")
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(packet)


def request_body(packet, rubric):
    assert_visible(packet)
    options = packet["probe_options"]
    body = {"model": MODEL, "state": packet, "questions": {
        "mechanism": rubric["mechanism"],
        "probe": {"type": "choice", "instructions": rubric["probe_instructions"], "criteria": options},
    }}
    checked_questions(body["questions"])
    if len(canonical(body)) > MAX_REQUEST:
        raise ValueError("Request exceeds preserved transport budget")
    return body


def identity(body, rubric, source_hashes):
    return digest({"body": body, "endpoint": ENDPOINT, "client": CLIENT,
                   "rubric_sha256": digest(rubric), "source_hashes": source_hashes})


def baseline_case(packet):
    text = canonical(packet.get("observations", [])).decode().lower()
    def anyof(*words):
        return any(word in text for word in words)
    def both(a, b):
        return anyof(*a) and anyof(*b)
    if anyof("timeouterror", "modulenotfounderror", "operation not permitted", "skipped", "collection_error"):
        mechanism = "assertion_proxy" if anyof("calibrat", "detected", "assertion") else "infrastructure"
    elif both(("denied", "not_permitted"), ("disclos", "private", "canary", "secret")) or both(("original",), ("replacement", "rebind")):
        mechanism = "authority_scope"
    elif both(("replay", "cache"), ("revision", "current", "accept")) or both(("evaluator", "context"), ("changed", "mutation", "material")):
        mechanism = "obsolete_result"
    elif both(("counter", "applied", "effect"), ("not_started", "reconnect")):
        mechanism = "post_effect_uncertainty"
    elif both(("held", "pending", "blocked"), ("idle", "stop")):
        mechanism = "concurrent_activity"
    elif both(("arguments", "native"), ("mismatch", "destination")):
        mechanism = "evidence_contradiction"
    elif both(("unknown",), ("submitted", "sequence")):
        mechanism = "post_effect_uncertainty"
    elif both(("completion", "reply"), ("timestamp", "before")):
        mechanism = "assertion_proxy"
    elif anyof('"no_change":true', '"valid_behavior":true'):
        mechanism = "legitimate"
    else:
        mechanism = "insufficient"
    probe = {"authority_scope": "p5", "obsolete_result": "p1", "post_effect_uncertainty": "p2",
             "concurrent_activity": "p6", "evidence_contradiction": "p3", "assertion_proxy": "p4",
             "legitimate": "none", "infrastructure": "p4", "insufficient": "insufficient"}[mechanism]
    if mechanism == "obsolete_result" and anyof("evaluator", "context"):
        probe = "p7"
    if mechanism == "post_effect_uncertainty" and anyof("sequence"):
        probe = "p3"
    if mechanism == "assertion_proxy" and anyof("reply"):
        probe = "p3"
    if probe not in packet["probe_options"]:
        probe = "insufficient"
    return {"mechanism": mechanism, "probe": probe}


def baseline_pair(left, right):
    a, b = baseline_case(left)["mechanism"], baseline_case(right)["mechanism"]
    if {a, b} & {"insufficient", "infrastructure"}:
        return "insufficient"
    if "legitimate" in (a, b):
        return "no_relationship"
    if a == b:
        return "same_mechanism"
    if {a, b} == {"obsolete_result", "post_effect_uncertainty"}:
        return "shared_invariant"
    return "no_relationship"


def prepare(out):
    out = Path(out)
    cases, pairs, key, rubric = load_data()
    source_hashes = sources()
    rows = []
    smoke = {"model": MODEL, "state": {"observations": [{"value": "alpha"}]}, "questions": {
        "transport": {"type": "choice", "instructions": "Select the literal value observed.", "criteria": {"alpha": "The observation value is alpha.", "beta": "The observation value is beta."}}}}
    rows.append({"id": "smoke", "kind": "smoke", "body": smoke})
    for c in cases:
        rows.append({"id": c["id"], "kind": "case", "body": request_body(c["packet"], rubric)})
    byid = {c["id"]: c["packet"] for c in cases}
    for pair in pairs:
        body = {"model": MODEL, "state": {"left": byid[pair["left"]], "right": byid[pair["right"]]}, "questions": {"relationship": rubric["relationship"]}}
        checked_questions(body["questions"])
        if len(canonical(body)) > MAX_REQUEST:
            raise ValueError("Pair request exceeds transport budget")
        rows.append({"id": pair["id"], "kind": "pair", "body": body})
    for row in rows:
        row["assessment_id"] = identity(row["body"], rubric, source_hashes)
        write(out / "requests" / (row["id"] + ".json"), row["body"])
    corpus_binding = digest({"cases": cases, "pairs": pairs, "key": key, "rubric": rubric})
    manifest = {"version": "1.1.0", "corpus_binding": corpus_binding, "endpoint": ENDPOINT, "requested_model": MODEL,
                "model_sha256": digest(MODEL), "rubric_sha256": digest(rubric), "source_hashes": source_hashes,
                "client": CLIENT, "python": platform.python_version(), "max_attempts_per_request": MAX_ATTEMPTS,
                "timeout_seconds": TIMEOUT, "maximum_calls": len(rows) * MAX_ATTEMPTS, "minimum_calls_if_success": len(rows),
                "maximum_request_bytes": MAX_REQUEST, "maximum_response_bytes": MAX_RESPONSE,
                "estimated_cost_usd": None, "cost_note": "No verified current provider pricing; no inferred cost. Actual usage is retained.",
                "repeats": [], "requests": [{k: v for k, v in row.items() if k != "body"} for row in rows]}
    try:
        manifest["git_head"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        manifest["git_head"] = None
    write(out / "matrix.json", manifest)
    baseline = {"selector": "baseline", "corpus_binding": corpus_binding,
                "cases": {c["id"]: baseline_case(c["packet"]) for c in cases},
                "pairs": {p["id"]: {"relationship": baseline_pair(byid[p["left"]], byid[p["right"]])} for p in pairs}}
    write(out / "baseline.json", baseline)
    write(out / "baseline-scoring.json", score_predictions(baseline, key))
    return manifest, rows


def acceptable(value):
    return value if isinstance(value, list) else [value]


def score_predictions(predictions, key):
    """Missing results remain unrun; no arbitrary exception earns detection credit."""
    result = {"selector": predictions.get("selector"), "by_split": {}, "by_family": {}, "cases": {}, "pairs": {}}
    def inc(group, name, amount=1):
        group[name] = group.get(name, 0) + amount
    for cid, expected in key["cases"].items():
        actual = predictions.get("cases", {}).get(cid, {})
        row = {"family": expected["family"], "split": expected["split"], "kind": expected["kind"]}
        groups = [result["by_split"].setdefault(expected["split"], {}), result["by_family"].setdefault(expected["family"], {})]
        for group in groups:
            inc(group, "cases")
        for question in ("mechanism", "probe"):
            choice = actual.get(question)
            correct = choice in acceptable(expected[question]) if choice is not None else None
            row[question] = {"choice": choice, "acceptable": expected[question], "correct": correct}
            for group in groups:
                inc(group, question + "_denominator")
                inc(group, question + ("_unrun" if choice is None else "_correct" if correct else "_incorrect"))
                if choice is not None:
                    inc(group, question + "_answered")
                if choice == "insufficient":
                    inc(group, question + "_abstentions")
        mechanism = actual.get("mechanism")
        for group in groups:
            if expected["kind"] == "valid_control":
                inc(group, "valid_control_denominator")
                if mechanism is not None and mechanism not in {"legitimate", "insufficient"}:
                    inc(group, "false_alarms")
            if expected["kind"] == "environment_failure":
                inc(group, "unrelated_failure_denominator")
                if mechanism is not None and mechanism not in {"infrastructure", "insufficient"}:
                    inc(group, "unrelated_failures_miscredited")
        result["cases"][cid] = row
    for pid, expected in key.get("relationships", key.get("pairs", {})).items():
        choice = predictions.get("pairs", {}).get(pid, {}).get("relationship")
        target = expected["relationship"]
        correct = choice in acceptable(target) if choice is not None else None
        result["pairs"][pid] = {"choice": choice, "acceptable": target, "correct": correct, "split": expected["split"], "family": expected.get("family", "pair")}
        for group in [result["by_split"].setdefault(expected["split"], {}), result["by_family"].setdefault(expected.get("family", "pair"), {})]:
            inc(group, "relationship_denominator")
            inc(group, "relationship_unrun" if choice is None else "relationship_correct" if correct else "relationship_incorrect")
    result["interpretation"] = "Option agreement only; probe usefulness requires separate executed observations. Unrun is not incorrect or abstention."
    return result


def sanitize_response(body, key):
    text = body.decode("utf8", errors="replace").replace(key, "[REDACTED]") if key else body.decode("utf8", errors="replace")
    text = re.sub(r"(?i)Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", text)
    try:
        value = json.loads(text)
    except ValueError:
        return text
    def redact(v):
        if isinstance(v, dict):
            return {k: "[REDACTED]" if re.search(r"authorization|api.?key|password|credential|secret", k, re.I) else redact(x) for k, x in v.items()}
        if isinstance(v, list):
            return [redact(x) for x in v]
        return v
    return json.dumps(redact(value), ensure_ascii=False)


def live(out):
    out = Path(out)
    if (out / "attempts.jsonl").exists() or (out / "live-status.json").exists():
        raise ValueError("Refusing to overwrite historical live run; use a fresh output directory")
    manifest, rows = prepare(out)
    key = os.environ.get("TYPESAFE_API_KEY")
    predictions = {"selector": "jev", "corpus_binding": manifest["corpus_binding"], "cases": {}, "pairs": {}}
    status = {"execution": "incomplete", "calls": 0, "provider_failures": 0, "invalid_responses": 0, "latency_seconds": 0.0,
              "usage": {"input_tokens": 0, "output_tokens": 0}, "cost_usd": None, "requested_model": MODEL, "returned_models": [], "unrun": []}
    if not key:
        status["blocked_operation"] = "Authenticated Jev smoke: repository Actions secret TYPESAFE_API_KEY was not injected."
        status["unrun"] = [r["id"] for r in rows]
    else:
        opener = urllib.request.build_opener(NoRedirect())
        smoke_ok = False
        for row in rows:
            if row["kind"] != "smoke" and not smoke_ok:
                status["unrun"].append(row["id"])
                continue
            payload = canonical(row["body"])
            if key in payload.decode():
                raise ValueError("Credential material present in request body")
            success = False
            for attempt in range(1, MAX_ATTEMPTS + 1):
                started = time.monotonic()
                record = {"id": row["id"], "assessment_id": row["assessment_id"], "attempt": attempt,
                          "started_at": dt.datetime.now(dt.timezone.utc).isoformat(), "endpoint": ENDPOINT,
                          "request_sha256": hashlib.sha256(payload).hexdigest(), "requested_model": MODEL}
                response = b""
                retry = False
                try:
                    request = urllib.request.Request(ENDPOINT, data=payload, method="POST", headers={"Authorization": "Bearer " + key, "Content-Type": "application/json", "Accept": "application/json", "User-Agent": CLIENT})
                    with opener.open(request, timeout=TIMEOUT) as handle:
                        record["http_status"] = handle.status
                        response = handle.read(MAX_RESPONSE + 1)
                    if len(response) > MAX_RESPONSE:
                        raise ValueError("Response exceeds byte budget")
                    value = json.loads(response)
                    validated = checked_response(value, row["body"]["questions"], MODEL)
                    record["outcome"] = "valid"
                    record["returned_model"] = validated["model"]
                    if validated["model"] not in status["returned_models"]:
                        status["returned_models"].append(validated["model"])
                    record["usage"] = validated["usage"]
                    for k, count in validated["usage"].items():
                        status["usage"][k] += count
                    answers = {k: v["choice"] for k, v in validated["answers"].items()}
                    if row["kind"] == "smoke":
                        smoke_ok = answers["transport"] == "alpha"
                        if not smoke_ok:
                            record["outcome"] = "smoke_semantic_failure"
                            status["blocked_operation"] = "Authenticated smoke response was schema-valid but selected the wrong literal; batch not sent."
                    else:
                        predictions["cases" if row["kind"] == "case" else "pairs"][row["id"]] = answers
                    success = True
                except urllib.error.HTTPError as error:
                    record.update(outcome="provider_failure", http_status=error.code)
                    response = error.read(MAX_RESPONSE + 1)
                    retry = error.code in {429, 529} and attempt < MAX_ATTEMPTS
                    error.close()
                    status["provider_failures"] += 1
                except (urllib.error.URLError, OSError):
                    record["outcome"] = "transport_failure"
                    status["provider_failures"] += 1
                except (ValueError, TypeError, KeyError) as error:
                    record["outcome"] = "invalid_response"
                    record["validation_error"] = getattr(error, "failure", {}).get("details", {}).get("validation", type(error).__name__)
                    status["invalid_responses"] += 1
                record["latency_seconds"] = time.monotonic() - started
                record["response_truncated"] = len(response) > MAX_RESPONSE
                record["raw_response_sanitized"] = sanitize_response(response[:MAX_RESPONSE], key)
                status["calls"] += 1
                status["latency_seconds"] += record["latency_seconds"]
                with (out / "attempts.jsonl").open("a") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
                if success or not retry:
                    break
                time.sleep(0.5)
            if not success and row["kind"] == "smoke":
                status["blocked_operation"] = "Authenticated Jev transport/schema smoke did not succeed; batch not sent."
    complete = len(predictions["cases"]) + len(predictions["pairs"]) == len(rows) - 1
    status["execution"] = "complete" if complete else "incomplete"
    status["answered_cases"] = len(predictions["cases"])
    status["answered_pairs"] = len(predictions["pairs"])
    write(out / "predictions.json", predictions)
    write(out / "live-status.json", status)
    write(out / "jev-scoring.json", score_predictions(predictions, read(ROOT / "data/answer_key.json")))
    return 0 if complete else 1


def replay_attempts(out, manifest, rubric):
    """Reconstruct selected labels from recorded typed responses, checking exact joins."""
    out = Path(out)
    rows = {r["id"]: r for r in manifest["requests"]}
    predictions = {"selector": "jev", "corpus_binding": manifest["corpus_binding"], "cases": {}, "pairs": {}}
    seen = set()
    smoke_passed = False
    for line in (out / "attempts.jsonl").read_text().splitlines():
        attempt = json.loads(line)
        row = rows[attempt["id"]]
        body = read(out / "requests" / (row["id"] + ".json"))
        expected_id = identity(body, rubric, manifest["source_hashes"])
        if attempt["assessment_id"] != expected_id or row["assessment_id"] != expected_id:
            raise ValueError("Assessment identity mismatch")
        if attempt["request_sha256"] != hashlib.sha256(canonical(body)).hexdigest():
            raise ValueError("Material request identity mismatch")
        if attempt["requested_model"] != MODEL:
            raise ValueError("Requested model mismatch")
        marker = (row["id"], attempt["attempt"])
        if marker in seen:
            raise ValueError("Duplicate attempt identity")
        seen.add(marker)
        if attempt["outcome"] != "valid":
            continue
        value = json.loads(attempt["raw_response_sanitized"])
        checked = checked_response(value, body["questions"], MODEL)
        if row["kind"] == "smoke":
            if checked["answers"]["transport"]["choice"] != "alpha":
                raise ValueError("Recorded successful smoke selected wrong value")
            smoke_passed = True
            continue
        if not smoke_passed:
            raise ValueError("Batch response precedes authenticated successful smoke")
        category = "cases" if row["kind"] == "case" else "pairs"
        if row["id"] in predictions[category]:
            raise ValueError("Multiple accepted answers for one assessment")
        predictions[category][row["id"]] = {k: v["choice"] for k, v in checked["answers"].items()}
    return predictions


def score(out):
    out = Path(out)
    manifest = read(out / "matrix.json")
    cases, pairs, key, rubric = load_data()
    binding = digest({"cases": cases, "pairs": pairs, "key": key, "rubric": rubric})
    if manifest["corpus_binding"] != binding:
        raise ValueError("Corpus/rubric changed; cannot reuse this run")
    if manifest["source_hashes"] != sources():
        raise ValueError("Experiment source changed; restore recorded revision before scoring")
    if (out / "attempts.jsonl").exists():
        replayed = replay_attempts(out, manifest, rubric)
        if (out / "predictions.json").exists() and replayed != read(out / "predictions.json"):
            raise ValueError("Predictions disagree with retained validated responses")
        write(out / "predictions.json", replayed)
    elif (out / "predictions.json").exists():
        predictions = read(out / "predictions.json")
        if predictions.get("cases") or predictions.get("pairs"):
            raise ValueError("Nonempty Jev predictions require retained attempts")
    for name in ("baseline", "predictions"):
        path = out / (name + ".json")
        if path.exists():
            predictions = read(path)
            if predictions["corpus_binding"] != binding:
                raise ValueError("Prediction corpus identity mismatch")
            write(out / ("baseline-scoring.json" if name == "baseline" else "jev-scoring.json"), score_predictions(predictions, key))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["prepare", "live", "score"])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.mode == "live":
        return live(args.out)
    if args.mode == "prepare":
        prepare(args.out)
    else:
        score(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
