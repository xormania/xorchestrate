"""Resolve one shared shard model and carry complete CI measurements forward.

The shared snapshot and skipped-run preservation adapt UXor's shard-timings
workflow. Timings only balance selected modules; they never select the suite.
GitHub intake reuses the existing bounded producer/archive evidence services.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import tempfile
import time
import xml.etree.ElementTree as ElementTree
import zipfile
from pathlib import Path
from urllib.parse import urlencode


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Keep the isolated direct-file CLI independent of pytest and installed packages.
candidate = _load("ci_timing_candidate", Path(__file__).with_name("candidate.py"))
sharding = _load("ci_timing_sharding", Path(__file__).parents[1] / "testkit/sharding.py")
MAX_RUNS = 20
MAX_PAGES = 20
LOOKUP_SECONDS = 15
ARTIFACT = "ci-test-timings--attempt-"
ERRORS = (OSError, ValueError, TypeError, KeyError, AttributeError, StopIteration,
          RecursionError, zipfile.BadZipFile, ElementTree.ParseError)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate timing JSON key")
        result[key] = value
    return result


def _read(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > candidate.MAX_JSON:
        raise ValueError("Timing input is not a bounded regular file")
    return json.loads(path.read_bytes(), object_pairs_hook=_unique)


def _payload(document):
    return (json.dumps(document, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def _digest(document):
    return hashlib.sha256(_payload(document)).hexdigest()


def _model(document):
    sharding.validate_timings(document)
    if not document["modules"]:
        raise ValueError("Timing model has no recorded modules")
    return document


def _identity(context, attempt=None):
    return {"repository": context["repository"], "run_id": context["producer"]["run_id"],
            "run_attempt": context["producer"]["run_attempt"] if attempt is None else attempt,
            "source_sha": context["source_sha"]}


def _pair(directory):
    directory = Path(directory)
    document, snapshot = _model(_read(directory / "timings.json")), _read(directory / "snapshot.json")
    if (type(snapshot.get("schema_version")) is not int or snapshot["schema_version"] != 1
            or snapshot.get("timings_sha256") != _digest(document)
            or not isinstance(snapshot.get("source"), dict)):
        raise ValueError("Timing snapshot does not identify its model")
    return document, snapshot


def _write(root, output, document, snapshot):
    root, output = Path(root).resolve(), Path(output).resolve()
    if root == output or root in output.parents:
        raise ValueError("Timing snapshots must stay outside the candidate checkout")
    output.mkdir(parents=True, exist_ok=True)
    snapshot = {**snapshot, "schema_version": 1, "timings_sha256": _digest(document)}
    candidate.write_json(output / "timings.json", document)
    candidate.write_json(output / "snapshot.json", snapshot)
    return snapshot


def _get(url, token):
    if not token:
        raise ValueError("Authenticated timing observations are unavailable")
    return json.loads(candidate.api_bytes(url, token, candidate.MAX_JSON), object_pairs_hook=_unique)


def _pages(get, url, key):
    rows = []
    for page in range(1, MAX_PAGES + 1):
        value = get(url + ("&" if "?" in url else "?") + f"per_page=100&page={page}")
        batch = value[key]
        if not isinstance(batch, list) or any(not isinstance(row, dict) for row in batch):
            raise ValueError("Malformed timing evidence collection")
        rows.extend(batch)
        if len(batch) < 100:
            return rows
    raise ValueError("Timing evidence collection is incomplete")


def _trusted_run(run, context, branch):
    return (isinstance(run, dict) and candidate._positive(run.get("id"))
            and run["id"] != context["producer"]["run_id"]
            and candidate._positive(run.get("run_attempt")) and run["run_attempt"] <= candidate.MAX_ATTEMPTS
            and run.get("path", "").split("@")[0] == candidate.WORKFLOW
            and run.get("event") == "push" and run.get("head_branch") == branch
            and run.get("status") == "completed" and run.get("conclusion") == "success"
            and candidate._sha(run.get("head_sha"))
            and all(isinstance(run.get(side), dict)
                    and {key: run[side].get(key) for key in ("id", "full_name")} == context["repository"]
                    for side in ("repository", "head_repository")))


def _select_source(root, context, branch, token, get, temporary, check_deadline):
    prefix = "https://api.github.com/repos/" + context["repository"]["full_name"]
    query = urlencode({"branch": branch, "event": "push", "status": "success", "per_page": MAX_RUNS})
    listing = get(prefix + "/actions/workflows/ci.yml/runs?" + query)
    runs = listing["workflow_runs"]
    if not isinstance(runs, list) or len(runs) > MAX_RUNS:
        raise ValueError("Timing run history exceeds its bound")
    job_name = _read(Path(root) / candidate.WORKFLOW)["jobs"]["ci-result"]["name"]
    for row in sorted(runs, key=lambda value: value.get("id", 0), reverse=True):
        try:
            if not _trusted_run(row, context, branch):
                continue
            # Most bootstrap runs have no timing publication. Avoid fetching
            # run/job lineage until an actual model artifact exists to inspect.
            artifacts = _pages(get, prefix + f"/actions/runs/{row['id']}/artifacts", "artifacts")
            if not any(isinstance(artifact.get("name"), str)
                       and artifact["name"].startswith(ARTIFACT) for artifact in artifacts):
                continue
            run = get(prefix + f"/actions/runs/{row['id']}")
            if (not _trusted_run(run, context, branch)
                    or any(run.get(key) != row.get(key) for key in ("id", "head_sha", "run_attempt"))):
                continue
            jobs = _pages(get, prefix + f"/actions/runs/{run['id']}/jobs?filter=all", "jobs")
            producer, observation = candidate.lane_producer(jobs, job_name)
            if (observation.get("status") != "completed" or observation.get("conclusion") != "success"
                    or observation["run_attempt"] > run["run_attempt"]
                    or any(job.get("run_id") != run["id"] or job.get("head_sha") != run["head_sha"]
                           for job in (producer, observation))):
                continue
            attempt = producer["run_attempt"]
            matches = [artifact for artifact in artifacts if artifact.get("name") == ARTIFACT + str(attempt)]
            if len(matches) != 1:
                continue
            artifact = matches[0]
            if (not candidate._positive(artifact.get("id")) or artifact.get("expired") is not False
                    or type(artifact.get("size_in_bytes")) is not int
                    or not 0 < artifact["size_in_bytes"] <= candidate.MAX_ARCHIVE
                    or artifact.get("workflow_run", {}).get("id") != run["id"]
                    or artifact["workflow_run"].get("head_sha") != run["head_sha"]):
                continue
            check_deadline()
            payload = candidate.api_bytes(prefix + f"/actions/artifacts/{artifact['id']}/zip", token,
                                          candidate.MAX_ARCHIVE)
            directory = temporary / str(artifact["id"])
            candidate.extract_artifact(payload, directory, digest=artifact.get("digest"))
            document, snapshot = _pair(directory)
            publication = {"repository": context["repository"], "run_id": run["id"],
                           "run_attempt": attempt, "source_sha": run["head_sha"]}
            if snapshot.get("publication") != publication:
                continue
            return document, {**snapshot, "status": "selected", "reason": "latest_usable_successful_push",
                              "selected_from": {**publication, "artifact_id": artifact["id"],
                                                "artifact_digest": artifact["digest"]},
                              "publication": None, "consumer": _identity(context)}
        except ERRORS:
            continue
    return None


def select(root, context, output, *, branch="master", token="", get=None):
    """Resolve once; unavailable history always leaves the committed model usable."""
    candidate.validate_context(context)
    root = Path(root)
    document = _model(_read(root / sharding.TIMINGS))
    snapshot = {"status": "fallback", "reason": "no_usable_successful_push",
                "source": {"kind": "committed", "path": sharding.TIMINGS,
                           "source_sha": context["checkout"]["commit"]},
                "publication": None, "consumer": _identity(context)}
    deadline = time.monotonic() + LOOKUP_SECONDS
    reader = get or (lambda url: _get(url, token))

    def check_deadline():
        # Bound optional fan-out between requests. The shared candidate HTTP
        # reader retains its existing 30-second timeout for one in-flight call.
        if time.monotonic() >= deadline:
            raise TimeoutError("Timing lookup scheduling budget exhausted")

    def bounded_get(url):
        check_deadline()
        return reader(url)

    try:
        with tempfile.TemporaryDirectory(prefix="xor-timing-intake-") as temporary:
            selected = _select_source(root, context, branch, token, bounded_get, Path(temporary), check_deadline)
            if selected is not None:
                document, snapshot = selected
            elif time.monotonic() >= deadline:
                snapshot["reason"] = "timing_lookup_budget_exhausted"
    except ERRORS:
        snapshot["reason"] = "timing_history_unavailable"
    return _write(root, output, document, snapshot)


def _measure(root, context, metadata, result, artifacts):
    if (result.get("schema_version") != 2 or result.get("status") != "passed" or result.get("errors")
            or result.get("candidate_context") != context or result.get("source_sha") != context["source_sha"]
            or result.get("tested_sha") != context["checkout"]["commit"]):
        raise ValueError("Current aggregate did not qualify")
    workflow, catalog = _read(root / candidate.WORKFLOW), _read(root / "contracts/testing.json")
    expected = {lane: name for lane, name in candidate.expected_lanes(workflow).items()
                if lane.startswith("framework-tests-")}
    directories, manifests = {}, {}
    for lane, name in expected.items():
        producer, _ = candidate.lane_producer(metadata["jobs"], name)
        artifact_name = lane + "--attempt-" + str(producer["run_attempt"])
        directory = artifacts / artifact_name
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError("Authoritative shard directory is unavailable")
        directories[lane] = directory
        manifests[artifact_name] = _read(directory / "producer.json")
    references = candidate.qualify_lineage(context, manifests, metadata, expected)
    recorded = result.get("evidence_references", [])
    for reference in references:
        matches = [row for row in recorded if row.get("lane") == reference["lane"]]
        if len(matches) != 1 or any(matches[0].get(key) != value for key, value in reference.items()):
            raise ValueError("Shard observations disagree with qualified aggregate")
    # Reuse the aggregate's coverage reconciliation rather than inventing another
    # module/case inventory. No pytest import is needed by this direct-file module.
    aggregate = _load("ci_timing_result", Path(__file__).with_name("result.py"))
    aggregate.shard_evidence(artifacts, workflow, tested_sha=context["checkout"]["commit"], catalog=catalog,
                             directories=directories, tested_tree=context["checkout"]["tree"])
    modules = {module for family in catalog["families"].values() for module in family["modules"]}
    measured = {}
    for directory in directories.values():
        inventories, reports = list(directory.glob("**/inventory.json")), list(directory.glob("**/tests.xml"))
        if len(inventories) != 1 or len(reports) != 1:
            raise ValueError("Shard measurements are missing or ambiguous")
        inventory, path = _read(inventories[0]), reports[0]
        if path.is_symlink() or not path.is_file() or path.stat().st_size > candidate.MAX_JSON:
            raise ValueError("Shard JUnit is not bounded")
        payload = path.read_bytes()
        if b"<!DOCTYPE" in payload.upper() or b"<!ENTITY" in payload.upper():
            raise ValueError("Shard JUnit declarations are unsupported")
        elements = list(ElementTree.fromstring(payload).iter("testcase"))
        if any(case.find("failure") is not None or case.find("error") is not None for case in elements):
            raise ValueError("Shard JUnit reports unsuccessful cases")
        cases = [(case.get("classname"), float(case.get("time", "0") or 0)) for case in elements]
        selected = inventory["shard"]["modules"]
        durations = sharding.module_durations(cases, selected)
        if len(cases) != inventory["selected"] or set(durations) != set(selected) or measured.keys() & durations.keys():
            raise ValueError("Shard JUnit does not cover its complete selection")
        measured.update(durations)
    if measured.keys() != modules:
        raise ValueError("Measurements do not cover the current catalog")
    return sharding.timings_document(measured), references


def publish(root, context, metadata, result, artifacts, inherited, output):
    """Update only complete fresh evidence; skipped/reused/failed runs carry forward."""
    candidate.validate_context(context)
    root, artifacts = Path(root), Path(artifacts)
    metadata = metadata if isinstance(metadata, dict) else {}
    result = result if isinstance(result, dict) else {}
    try:
        document, previous = _pair(inherited)
    except ERRORS:
        document = _model(_read(root / sharding.TIMINGS))
        previous = {"source": {"kind": "committed", "path": sharding.TIMINGS,
                               "source_sha": context["checkout"]["commit"]}}
    run = metadata.get("run")
    attempt = run.get("run_attempt") if isinstance(run, dict) else context["producer"]["run_attempt"]
    if not candidate._positive(attempt):
        attempt = context["producer"]["run_attempt"]
    publication = _identity(context, attempt)
    snapshot = {"status": "preserved", "reason": "measurements_unavailable", "source": previous["source"],
                "publication": publication, "consumer": _identity(context)}
    if result.get("documentation_only"):
        snapshot["reason"] = "documentation_only"
    elif result.get("reuse"):
        snapshot["reason"] = "behavioral_evidence_reused"
    elif result.get("status") != "passed":
        snapshot["reason"] = "aggregate_not_passed"
    else:
        try:
            document, references = _measure(root, context, metadata, result, artifacts)
            snapshot.update(status="updated", reason="complete_qualified_shards",
                            source={"kind": "ci", **publication, "shards": [
                                {key: row[key] for key in ("lane", "run_attempt", "job_id", "artifact_id", "artifact_digest")}
                                for row in references]})
        except ERRORS:
            snapshot["reason"] = "complete_measurements_unavailable"
    return _write(root, output, document, snapshot)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="mode", required=True)
    for mode in ("select", "publish"):
        command = commands.add_parser(mode)
        command.add_argument("--root", type=Path, required=True)
        command.add_argument("--context", type=Path, required=True)
        command.add_argument("--output", type=Path, required=True)
        if mode == "select":
            command.add_argument("--branch", default=os.environ.get("TIMINGS_BRANCH")
                                 or os.environ.get("GITHUB_DEFAULT_BRANCH") or "master")
            command.add_argument("--github-output", type=Path)
        else:
            for name in ("metadata", "result", "artifacts", "inherited"):
                command.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        context = _read(args.context)
        if args.mode == "select":
            snapshot = select(args.root, context, args.output, branch=args.branch,
                              token=os.environ.get("GITHUB_TOKEN", ""))
            if args.github_output:
                with args.github_output.open("a") as stream:
                    stream.write("sha256=" + snapshot["timings_sha256"] + "\n")
        else:
            snapshot = publish(args.root, context, _read(args.metadata), _read(args.result), args.artifacts,
                               args.inherited, args.output)
        print(json.dumps({key: snapshot[key] for key in ("status", "reason", "timings_sha256")}, sort_keys=True))
    except ERRORS as error:
        print(json.dumps({"status": "unavailable", "reason": type(error).__name__}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
