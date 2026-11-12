"""Trusted post-CI advice, isolated from the candidate's executable code.

The event/eligibility/reservation boundary adapts Mago–Serena's
``tools/ci/prepare_diagnosis.py``. Archive and producer verification reuse the
existing Xorchestrate candidate evidence service; advice never qualifies CI.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import zipfile
from pathlib import Path
from urllib.parse import urlencode

from scripts.ci import candidate, reuse

WORKFLOW = ".github/workflows/jev.yml"
MAX_ARTIFACTS = 100
MAX_SELECTED_ARCHIVES = 8
MAX_TOTAL_ARCHIVE = 192 * 1024 * 1024


def read_json(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > candidate.MAX_JSON:
        raise ValueError("Advice input is not a bounded regular JSON file")
    return json.loads(path.read_text(), object_pairs_hook=reuse._unique)


def api_json(url, token):
    return json.loads(candidate.api_bytes(url, token, candidate.MAX_JSON), object_pairs_hook=reuse._unique)


def validate_source(event, run):
    """Bind a completed same-repository CI attempt to its actual event."""
    repository, source = event.get("repository", {}), event.get("workflow_run", {})
    if (not candidate._repository(repository) or not isinstance(source, dict) or not isinstance(run, dict)
            or run.get("status") != "completed" or run.get("path", "").split("@")[0] != candidate.WORKFLOW
            or run.get("event") not in {"pull_request", "push", "workflow_dispatch"}
            or not candidate._positive(run.get("id")) or not candidate._positive(run.get("run_attempt"))
            or run["run_attempt"] > candidate.MAX_ATTEMPTS or not candidate._sha(run.get("head_sha"))
            or any(run.get(key) != source.get(key) for key in ("id", "run_attempt", "head_sha"))
            or any(run.get(side, {}).get(key) != repository.get(key)
                   for side in ("repository", "head_repository") for key in ("id", "full_name"))):
        raise ValueError("Source event is not the identified same-repository CI attempt")
    return {"repository": {key: repository[key] for key in ("id", "full_name")},
            "run_id": run["id"], "run_attempt": run["run_attempt"], "source_sha": run["head_sha"]}


def prior_reservation(repo, name, default_branch, token, *, get=None):
    """Only this trusted workflow can reserve a source; expiry is not a retry."""
    get = get or (lambda url: api_json(url, token))
    prefix = "https://api.github.com/repos/" + repo
    listing = get(prefix + "/actions/artifacts?" + urlencode({"name": name, "per_page": MAX_ARTIFACTS}))
    rows = listing.get("artifacts")
    if (not isinstance(rows, list) or type(listing.get("total_count")) is not int
            or listing["total_count"] != len(rows) or len(rows) > MAX_ARTIFACTS):
        raise ValueError("Reservation inventory is incomplete")
    for artifact in rows:
        if artifact.get("name") != name:
            continue
        owner = artifact.get("workflow_run", {}).get("id")
        if not candidate._positive(owner):
            raise ValueError("Reservation owner is missing")
        run = get(prefix + f"/actions/runs/{owner}")
        if (run.get("path", "").split("@")[0] == WORKFLOW and run.get("head_branch") == default_branch
                and run.get("event") in {"workflow_run", "workflow_dispatch"}
                and run.get("repository", {}).get("full_name") == repo):
            return {"artifact_id": artifact.get("id"), "consumer_run_id": owner,
                    "expired": bool(artifact.get("expired"))}
    return None


def download_artifact(source, listing, name, token, directory, budget):
    """Bound the complete selected intake, in addition to each ZIP's limits."""
    if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9.-]{1,180}--attempt-[1-9][0-9]*", name):
        raise ValueError("Invalid advice artifact name")
    rows = [item for item in listing if item.get("name") == name]
    if len(rows) != 1:
        raise ValueError("Advice artifact is missing or ambiguous")
    item = rows[0]
    producer = item.get("workflow_run", {})
    if (not candidate._positive(item.get("id")) or item.get("expired") is not False
            or producer.get("id") != source["run_id"] or producer.get("head_sha") != source["source_sha"]
            or type(item.get("size_in_bytes")) is not int or not 0 <= item["size_in_bytes"] <= candidate.MAX_ARCHIVE):
        raise ValueError("Advice artifact identity or size is invalid")
    if budget["count"] >= MAX_SELECTED_ARCHIVES or budget["bytes"] + item["size_in_bytes"] > MAX_TOTAL_ARCHIVE:
        raise ValueError("Advice artifact intake exceeds its total budget")
    url = ("https://api.github.com/repos/" + source["repository"]["full_name"]
           + f"/actions/artifacts/{item['id']}/zip")
    payload = candidate.api_bytes(url, token, min(candidate.MAX_ARCHIVE, MAX_TOTAL_ARCHIVE - budget["bytes"]))
    budget["count"] += 1
    budget["bytes"] += len(payload)
    candidate.extract_artifact(payload, Path(directory) / name, digest=item.get("digest"))
    return Path(directory) / name


def prepare(event, work, token, env, *, get=None, observe=None):
    """Prepare evidence without a provider key; a durable upload follows this."""
    get = get or (lambda url: api_json(url, token))
    observe = observe or reuse.observe_current
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    repository = event.get("repository", {})
    trusted = env.get("JEV_TRUSTED_SHA")
    if (not candidate._repository(repository) or not candidate._sha(trusted)
            or env.get("GITHUB_REPOSITORY") != repository["full_name"]
            or not candidate._positive(int(env.get("GITHUB_RUN_ID", "0")))):
        raise ValueError("Invalid trusted consumer identity")
    record = {"schema_version": 1, "advisory": True, "provider_needed": False,
              "trusted_sha": trusted, "consumer_run_id": int(env["GITHUB_RUN_ID"]),
              "consumer_attempt": int(env.get("GITHUB_RUN_ATTEMPT", "0"))}
    if record["consumer_attempt"] < 1:
        raise ValueError("Invalid consumer attempt")
    if record["consumer_attempt"] > 1:
        return {**record, "status": "consumer_rerun_suppressed"}
    repo, default = repository["full_name"], repository["default_branch"]
    if env.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
        if (env.get("GITHUB_REF") != "refs/heads/" + default
                or event.get("inputs", {}).get("calibration") not in (True, "true")):
            raise ValueError("Calibration requires explicit dispatch on the default branch")
        name = f"jev-reservation-calibration-{record['consumer_run_id']}"
        record.update(kind="calibration")
    elif env.get("GITHUB_EVENT_NAME") == "workflow_run":
        event_run = event.get("workflow_run", {})
        if not candidate._positive(event_run.get("id")):
            raise ValueError("Invalid source run")
        prefix = "https://api.github.com/repos/" + repo
        run = get(prefix + f"/actions/runs/{event_run['id']}")
        source = validate_source(event, run)
        record.update(kind="source", source=source)
        if run.get("conclusion") in {"cancelled", "skipped"}:
            return {**record, "status": "source_" + run["conclusion"]}
        listing = get(prefix + f"/actions/runs/{source['run_id']}/artifacts?per_page={MAX_ARTIFACTS}")
        rows = listing.get("artifacts")
        if (not isinstance(rows, list) or type(listing.get("total_count")) is not int
                or len(rows) != listing["total_count"] or len(rows) > MAX_ARTIFACTS):
            raise ValueError("Source artifact inventory is incomplete")
        budget = {"count": 0, "bytes": 0}
        directory = work / "artifacts"
        result_dir = download_artifact(source, rows, f"ci-result-evidence--attempt-{source['run_attempt']}",
                                       token, directory, budget)
        result = read_json(result_dir / "result.json")
        context = candidate.validate_context(result["candidate_context"])
        if (context["repository"] != source["repository"] or context["source_repository"] != source["repository"]
                or context["producer"]["run_id"] != source["run_id"]
                or context["producer"]["run_attempt"] != source["run_attempt"]
                or context["source_sha"] != source["source_sha"]):
            raise ValueError("Candidate artifact disagrees with the source event")
        metadata = candidate.collect_metadata(context, token)
        candidate.qualify_lineage(context, {}, metadata, {}, allow_failed=True)
        candidate.write_json(work / "metadata.json", metadata)
        current = observe(context, token)
        record.update(candidate_context=context, source_currentness=current, result_path=str(result_dir / "result.json"))
        if current["status"] == "superseded":
            return {**record, "status": "superseded_candidate"}
        if result.get("documentation_only") is True or result.get("reuse"):
            return {**record, "status": "documentation_only" if result.get("documentation_only") else "behavioral_evidence_reused"}
        context_dir = download_artifact(source, rows, f"candidate-context--attempt-{source['run_attempt']}",
                                        token, directory, budget)
        if read_json(context_dir / "context.json") != context:
            raise ValueError("Candidate context artifacts disagree")
        # Partial failed runs can still supply source advice. Triage independently
        # qualifies each available test lane, retaining missing lanes as unknown.
        test_artifacts = {}
        for item in rows:
            name = item.get("name", "")
            if not name.startswith("framework-tests-"):
                continue
            lane, separator, attempt_text = name.rpartition("--attempt-")
            if not separator or not attempt_text.isdigit() or not 1 <= int(attempt_text) <= source["run_attempt"]:
                raise ValueError("Invalid test artifact attempt")
            if int(attempt_text) > test_artifacts.get(lane, (0, ""))[0]:
                test_artifacts[lane] = (int(attempt_text), name)
        for _, name in sorted(test_artifacts.values()):
            download_artifact(source, rows, name, token, directory, budget)
        record.update(tested_sha=context["checkout"]["commit"], artifact_budget=budget)
        name = f"jev-reservation-source-{source['run_id']}-{source['run_attempt']}"
    else:
        raise ValueError("Unsupported advice event")
    previous = prior_reservation(repo, name, default, token, get=get)
    record["reservation_name"] = name
    if previous:
        return {**record, "status": "duplicate_suppressed", "prior_reservation": previous}
    record.update(status="reservation_required", provider_needed=True)
    candidate.write_json(work / "reservation" / "reservation.json", record)
    return record


def assess(plan, source_root, trusted_root, output, *, token, cache_dir=None):
    """Run trusted collectors against the source checkout as data, never import it."""
    from scripts.architecture.diagnostics import diagnose
    from scripts.architecture.python import check
    from scripts.architecture.triage import triage

    source_root, trusted_root, output = map(Path, (source_root, trusted_root, output))
    context = candidate.validate_context(plan["candidate_context"])
    if (plan.get("provider_needed") is not True or plan.get("kind") != "source"
            or candidate._git(trusted_root, "rev-parse", "HEAD") != plan["trusted_sha"]
            or candidate._git(source_root, "rev-parse", "HEAD") != context["checkout"]["commit"]
            or candidate._git(source_root, "rev-parse", "HEAD^{tree}") != context["checkout"]["tree"]
            or candidate._git(source_root, "status", "--porcelain=v1", "--untracked-files=all") != ""
            or candidate._inputs(source_root) != context["inputs"]):
        raise ValueError("Advice checkout identity changed")
    current = reuse.observe_current(context, token)
    if current["status"] == "superseded":
        return {"status": "superseded_candidate", "provider_needed": False, "source_currentness": current}
    options = read_json(trusted_root / "contracts/hexagonal-jev.json")
    for key, variable in (("endpoint", "JEV_ENDPOINT"), ("model", "JEV_MODEL")):
        if os.environ.get(variable):
            options[key] = os.environ[variable]
    options["base_ref"] = context.get("tested_base_sha") or "HEAD^"
    if cache_dir:
        options["cache_dir"] = str(cache_dir)
    profile = read_json(trusted_root / "contracts/hexagonal.json")
    report = check(source_root, profile)
    report["diagnostics"] = diagnose(report, source_root, options)
    provenance = {"trusted_sha": plan["trusted_sha"], "source": plan["source"],
                  "tested_sha": context["checkout"]["commit"], "consumer_run_id": plan["consumer_run_id"]}
    report["advice_provenance"] = provenance
    candidate.write_json(output / "diagnostics.json", report)
    work = Path(plan["result_path"]).parents[2]
    triage_options = {**options, **options.get("post_test", {})}
    triage_report = triage(source_root, work / "artifacts", read_json(plan["result_path"]),
                           read_json(work / "metadata.json"), read_json(source_root / candidate.WORKFLOW),
                           read_json(source_root / "contracts/testing.json"), triage_options)
    triage_report["advice_provenance"] = provenance
    candidate.write_json(output / "triage.json", triage_report)
    # Evidence is retained even if the branch advances during inference. It is
    # explicitly historical and cannot be presented as advice on the new head.
    after = reuse.observe_current(context, token)
    return {"schema_version": 1, "advisory": True, "status": "complete" if after["status"] != "superseded" else "superseded_after_assessment",
            "source_currentness": after, **provenance,
            "source_status": report["diagnostics"]["status"], "triage_status": triage_report["status"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "assess"))
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--trusted-root", type=Path, default=Path.cwd())
    parser.add_argument("--cache-dir", type=Path)
    args = parser.parse_args(argv)
    token = os.environ.get("GITHUB_TOKEN", "")
    try:
        if args.mode == "prepare":
            result = prepare(read_json(os.environ["GITHUB_EVENT_PATH"]), args.work, token, os.environ)
            candidate.write_json(args.work / "plan.json", result)
            if os.environ.get("GITHUB_OUTPUT"):
                with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
                    for key in ("provider_needed", "reservation_name", "tested_sha", "kind"):
                        value = result.get(key, False if key == "provider_needed" else "")
                        stream.write(key + "=" + (str(value).lower() if isinstance(value, bool) else str(value)) + "\n")
        else:
            result = assess(read_json(args.work / "plan.json"), args.source_root, args.trusted_root,
                            args.work / "output", token=token, cache_dir=args.cache_dir)
            candidate.write_json(args.work / "output" / "result.json", result)
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError, zipfile.BadZipFile, RecursionError):
        result = {"status": "advice_unavailable", "provider_needed": False}
        candidate.write_json(args.work / "output" / "unavailable.json", result)
        raise
    finally:
        if os.environ.get("GITHUB_STEP_SUMMARY") and "result" in locals():
            with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as stream:
                stream.write("\n### Jev advisory\n\nStatus: **" + result["status"]
                             + "**. Required CI completes independently.\n")
                if "source_status" in result:
                    stream.write("Source assessment: **" + result["source_status"]
                                 + "**; test evidence triage: **" + result["triage_status"] + "**.\n")
                    stream.write("Tested revision: `" + result["tested_sha"] + "`; trusted collector: `"
                                 + result["trusted_sha"] + "`. Full judgments and evidence are in the advice artifact.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
