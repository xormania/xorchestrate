"""Exact-candidate behavioral reuse; unavailable proof always means execution.

Candidate capture and bounded artifact retrieval are shared with the ordinary
full-validation path. Prior receipts retain their original producer identity.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import time
import zipfile
import zlib
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

# The isolated direct-file CLI must not import candidate-controlled package code.
_spec = importlib.util.spec_from_file_location("ci_candidate", Path(__file__).with_name("candidate.py"))
candidate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(candidate)

MAX_RUNS = 20
WORKFLOW = ".github/workflows/ci.yml"
METADATA_ACTIONS = {"edited", "ready_for_review"}
STATIC_LANE = "static-checks-python-3.12"
TMUX_LANE = "tmux-acceptance-evidence"


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate evidence member key")
        result[key] = value
    return result


def candidate_key(context):
    """Keep observation/producer time separate from the immutable tested inputs."""
    candidate.validate_context(context)
    if context.get("event", {}).get("name") != "pull_request":
        raise ValueError("Only identified PR evidence is reusable")
    required = ("repository", "source_repository", "pr", "source_sha", "checkout", "tested_base_sha", "inputs")
    if any(context.get(name) is None for name in required):
        raise ValueError("Incomplete PR candidate identity")
    checkout = context["checkout"]
    if (checkout.get("kind") != "pr_merge" or checkout.get("status") != ""
            or checkout.get("parents") != [context["tested_base_sha"], context["source_sha"]]):
        raise ValueError("Unproved PR test merge")
    producer = context.get("producer", {})
    workflow_ref = producer.get("workflow_ref", "")
    workflow = workflow_ref.partition("@")[0]
    return {"schema_version": 1, **{name: deepcopy(context[name]) for name in required}, "workflow": workflow}


def compatible_context(current, original):
    try:
        return candidate_key(current) == candidate_key(original)
    except (ValueError, TypeError, AttributeError):
        return False


def run_title(context, action):
    """A workflow-generated title, with no user title/body text, survives queue cancellation."""
    return (f"CI:pull_request:{action}:{context['pr']['number']}:{context['source_sha']}:"
            f"{context['event']['reported_base_sha']}")


def unstarted_metadata(run, jobs, context):
    """Positively identify cancelled metadata observations that never ran behavior."""
    return (run.get("run_attempt") == 1 and run.get("status") == "completed" and run.get("conclusion") == "cancelled"
            and jobs == [] and any(run.get("display_title") == run_title(context, action)
                                   for action in METADATA_ACTIONS))


def runtime_known(value, lane):
    if not isinstance(value, dict):
        return False
    if any(not isinstance(value.get(key), str) or not value[key]
           for key in ("python", "implementation", "architecture", "platform", "uv", "mode")):
        return False
    runner = value.get("runner")
    if not isinstance(runner, dict) or any(not isinstance(runner.get(key), str) or not runner[key]
                                           for key in ("os", "image", "version")):
        return False
    tmux = value.get("tmux")
    return (value["mode"] == "protocol-fixture"
            and (isinstance(tmux, str) and bool(tmux) if lane == TMUX_LANE else tmux is None))


def _attempt_start(value):
    if not isinstance(value, str) or not value:
        raise ValueError("Latest attempt start ordering is unknown")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Latest attempt start has no timezone")
    return parsed


def select_run(runs, current, inspect, *, runtime, enabled=True):
    """Inspect newest relevant work first; never search past a material failed run.

    ``inspect`` supplies authenticated run metadata, the context/result and the
    original lane manifests. A missing/malformed record is a bounded reuse miss.
    The caller still executes ordinary validation on every miss.
    """
    fallback = {"schema_version": 1, "decision": "execute", "reason": "no_qualified_evidence", "history": []}
    if not enabled:
        return {**fallback, "reason": "reuse_disabled"}
    try:
        candidate_key(current)
        if not isinstance(runs, list) or any(not isinstance(run, dict) for run in runs):
            raise ValueError("Malformed workflow history")
        if any(type(run.get("id")) is not int or type(run.get("run_number")) is not int for run in runs):
            raise ValueError("Unidentified workflow history")
        ordered = sorted(runs, key=lambda run: (_attempt_start(run.get("attempt_started_at")),
                                               run["run_number"], run["id"]), reverse=True)
        relevant = [run for run in ordered if run.get("id") != current["producer"]["run_id"]
                    and run.get("event") == "pull_request" and run.get("path") == WORKFLOW
                    and run.get("head_sha") == current["source_sha"]]
        irrelevant = set()
        for run in relevant[:MAX_RUNS]:
            started_at = _attempt_start(run["attempt_started_at"])
            bundle = inspect(run)
            observed_run = bundle.get("run", run)
            if (not isinstance(observed_run, dict) or observed_run.get("id") != run["id"]
                    or observed_run.get("run_attempt") != run.get("run_attempt")):
                return {**fallback, "reason": "producer_changed_during_inspection"}
            run = observed_run
            observation = {"run_id": run["id"], "run_attempt": run.get("run_attempt"),
                           "status": run.get("status"), "conclusion": run.get("conclusion")}
            fallback["history"].append(observation)
            if unstarted_metadata(run, bundle.get("jobs"), current):
                observation["classification"] = "superseded_unstarted_metadata"
                irrelevant.add(run["id"])
                continue
            original = bundle.get("context")
            if not isinstance(original, dict):
                return {**fallback, "reason": "unknown_newer_candidate"}
            if not compatible_context(current, original):
                observation["classification"] = "different_inputs"
                irrelevant.add(run["id"])
                continue
            if run.get("status") != "completed" or run.get("conclusion") != "success":
                return {**fallback, "reason": "newer_material_attempt"}
            if bundle.get("qualified") is not True or bundle.get("result", {}).get("status") != "passed":
                return {**fallback, "reason": "unqualified_producer"}
            references = bundle.get("references")
            evidence = bundle.get("result", {}).get("evidence_references", [])
            if (bundle.get("result", {}).get("reuse") or bundle.get("result", {}).get("decision") == "reuse"
                    or not isinstance(references, list) or not references or not isinstance(evidence, list)
                    or any(not isinstance(row, dict) or row.get("decision") != "executed" for row in (*references, *evidence))):
                return {**fallback, "reason": "recursive_reuse"}
            lanes = bundle.get("lanes")
            if (not isinstance(lanes, dict) or not lanes or set(lanes) != set(runtime)
                    or any(not isinstance(manifest, dict) or not runtime_known(runtime[lane], lane)
                           or not runtime_known(manifest.get("runtime"), lane) or manifest.get("runtime") != runtime[lane]
                           for lane, manifest in lanes.items())):
                return {**fallback, "reason": "runtime_changed_or_unknown"}
            full_original = bundle.get("original_context", original)
            if not compatible_context(current, full_original):
                return {**fallback, "reason": "unqualified_original_candidate"}
            peers = [peer for peer in relevant if peer["id"] != run["id"] and peer["id"] not in irrelevant
                     and _attempt_start(peer["attempt_started_at"]) == started_at]
            if peers:
                return {**fallback, "reason": "ambiguous_attempt_start",
                        "ambiguous_attempts": [{key: peer.get(key) for key in
                            ("id", "run_attempt", "attempt_started_at", "status", "conclusion")} for peer in peers]}
            return {"schema_version": 1, "decision": "reuse", "reason": "exact_candidate",
                    "current_context": deepcopy(current), "original_context": deepcopy(full_original),
                    "producer": observation, "history": fallback["history"],
                    "lanes": deepcopy(lanes), "references": deepcopy(bundle["references"]),
                    **({"summary_ref": deepcopy(bundle["summary_ref"])} if bundle.get("summary_ref") else {})}
    except (ValueError, TypeError, KeyError, OSError, AttributeError):
        return {**fallback, "reason": "evidence_unavailable"}
    return fallback


def _read(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > candidate.MAX_JSON:
        raise ValueError("Missing or oversized reuse evidence")
    value = json.loads(path.read_bytes(), object_pairs_hook=_unique)
    if not isinstance(value, dict):
        raise ValueError("Expected an evidence object")
    return value


def _get(context, token, path):
    if not isinstance(token, str) or not token:
        raise ValueError("Authenticated workflow observations are unavailable")
    prefix = "https://api.github.com/repos/" + context["repository"]["full_name"]
    return json.loads(candidate.api_bytes(prefix + path, token, candidate.MAX_JSON), object_pairs_hook=_unique)


def _pages(context, token, path, key):
    rows = []
    for page in range(1, 21):
        value = _get(context, token, path + ("&" if "?" in path else "?") + f"per_page=100&page={page}")
        batch = value.get(key)
        if not isinstance(batch, list) or any(not isinstance(row, dict) for row in batch):
            raise ValueError("Malformed workflow evidence collection")
        rows.extend(batch)
        if len(batch) < 100:
            return rows
    raise ValueError("Workflow evidence collection is incomplete")


def observe_current(context, token):
    """Observe the current candidate without changing the immutable event checkout."""
    candidate.validate_context(context)
    if context["event"]["name"] != "pull_request":
        return {"status": "not_applicable"}
    pr = _get(context, token, f"/pulls/{context['pr']['number']}")
    source = pr.get("head", {})
    target = pr.get("base", {})
    identity = {"head": source.get("sha"), "base": target.get("sha"), "merge": pr.get("merge_commit_sha"),
                "head_ref": source.get("ref"), "base_ref": target.get("ref"), "state": pr.get("state"),
                "draft": pr.get("draft"), "number": pr.get("number"),
                "source_repository": {key: source.get("repo", {}).get(key) for key in ("id", "full_name")}}
    if (any(not candidate._sha(identity[key]) for key in ("head", "base", "merge"))
            or type(identity["number"]) is not int or identity["number"] <= 0
            or identity["state"] not in {"open", "closed"} or type(identity["draft"]) is not bool
            or any(not isinstance(identity[key], str) or not identity[key] for key in ("head_ref", "base_ref"))
            or not candidate._repository(identity["source_repository"])):
        raise ValueError("Current PR observation is incomplete")
    current = (identity["head"] == context["source_sha"] and identity["base"] == context["tested_base_sha"]
               and identity["merge"] == context["checkout"]["commit"] and identity["number"] == context["pr"]["number"]
               and identity["head_ref"] == context["pr"]["source_ref"] and identity["base_ref"] == context["pr"]["target_ref"]
               and identity["source_repository"] == context["source_repository"] and identity["state"] == "open")
    canonical = {}
    if current:
        for side, repository, branch in (("source", context["source_repository"], context["pr"]["source_ref"]),
                                         ("target", context["repository"], context["pr"]["target_ref"])):
            reference = _get({**context, "repository": repository}, token, "/git/ref/heads/" + quote(branch, safe="/"))
            if not isinstance(reference, dict):
                raise ValueError("Canonical PR branch observation is incomplete")
            actual = reference.get("object", {})
            expected = "refs/heads/" + branch
            if (reference.get("ref") != expected or not isinstance(actual, dict)
                    or actual.get("type") != "commit" or not candidate._sha(actual.get("sha"))):
                raise ValueError("Canonical PR branch observation is incomplete")
            canonical[side] = {"repository": deepcopy(repository), "ref": expected, "sha": actual["sha"]}
        current = canonical["source"]["sha"] == context["source_sha"] and canonical["target"]["sha"] == context["tested_base_sha"]
    return {"status": "current" if current else "superseded", "candidate": identity,
            "canonical_refs": canonical,
            "observed_at": datetime.now(timezone.utc).isoformat()}


def _artifact(context, metadata, name, token, destination):
    matches = [row for row in metadata["artifacts"] if row.get("name") == name]
    if len(matches) != 1:
        raise ValueError("Missing or ambiguous reuse artifact")
    artifact = matches[0]
    if (type(artifact.get("id")) is not int or artifact["id"] <= 0 or artifact.get("expired") is not False
            or artifact.get("workflow_run", {}).get("id") != metadata["run"]["id"]
            or artifact.get("workflow_run", {}).get("head_sha") != metadata["run"]["head_sha"]):
        raise ValueError("Foreign or expired reuse artifact")
    prefix = "https://api.github.com/repos/" + context["repository"]["full_name"] + "/actions/artifacts/"
    payload = candidate.api_bytes(prefix + str(artifact["id"]) + "/zip", token, candidate.MAX_ARCHIVE)
    candidate.extract_artifact(payload, destination, digest=artifact.get("digest"))
    return artifact


def _same_executions(recorded, verified, metadata, expected):
    """Retain historical observations while authenticating newer native wrappers."""
    if (not isinstance(recorded, list) or not isinstance(verified, list)
            or any(not isinstance(row, dict) for row in (*recorded, *verified))):
        return False
    observations = {"observation_job_id", "observation_attempt", "carried_forward"}
    try:
        for row in recorded:
            owner = metadata[row["run_id"]]
            attempt = row.get("observation_attempt", row["run_attempt"])
            identifier = row.get("observation_job_id", row["job_id"])
            if not candidate._positive(attempt) or attempt > owner["run"]["run_attempt"]:
                return False
            history = [job for job in owner["jobs"] if job.get("run_attempt", 0) <= attempt]
            producer, observed = candidate.lane_producer(history, expected[row["lane"]])
            if (producer.get("id") != row["job_id"] or producer.get("run_attempt") != row["run_attempt"]
                    or observed.get("id") != identifier or observed.get("run_attempt") != attempt
                    or observed.get("status") != "completed" or observed.get("conclusion") != "success"
                    or row.get("carried_forward", False) != (producer["id"] != observed["id"])):
                return False
        def identities(rows):
            return sorted(({key: value for key, value in row.items() if key not in observations} for row in rows),
                          key=lambda row: row.get("lane", ""))
        return identities(recorded) == identities(verified)
    except (KeyError, TypeError, ValueError):
        return False


def inspect_producer(current, run, workflow, token, directory, qualifier, catalog, *, allow_reuse=True, static_qualifier=None):
    """Authenticate original full execution with the same qualifier used by CI."""
    identifier, attempt = run["id"], run.get("run_attempt")
    if type(attempt) is not int or attempt < 1 or attempt > candidate.MAX_ATTEMPTS:
        raise ValueError("Unsupported producer attempt")
    latest_jobs = _pages(current, token, f"/actions/runs/{identifier}/attempts/{attempt}/jobs", "jobs")
    if unstarted_metadata(run, latest_jobs, current):
        observed = _get(current, token, f"/actions/runs/{identifier}")
        if observed.get("id") != identifier or not unstarted_metadata(observed, latest_jobs, current):
            raise ValueError("Metadata run changed before its empty first attempt was established")
        return {"jobs": [], "run": observed}
    artifact_rows = _pages(current, token, f"/actions/runs/{identifier}/artifacts", "artifacts")
    candidates = [row for row in artifact_rows if row.get("name", "").startswith("candidate-context--attempt-")]
    contexts = []
    for row in candidates:
        try:
            number = int(row["name"].removeprefix("candidate-context--attempt-"))
        except ValueError:
            continue
        if 0 < number <= attempt:
            contexts.append((number, row))
    if not contexts:
        return {"jobs": latest_jobs}
    context_attempt, artifact = max(contexts, key=lambda item: item[0])
    preliminary = {"run": run, "artifacts": artifact_rows}
    context_dir = Path(directory) / "captured"
    _artifact(current, preliminary, artifact["name"], token, context_dir)
    original = candidate.validate_context(_read(context_dir / "context.json"))
    if original["producer"]["run_id"] != identifier or original["producer"]["run_attempt"] != context_attempt:
        raise ValueError("Context belongs to another run or attempt")
    metadata = candidate.collect_metadata(original, token)
    actual = metadata["run"]
    if actual["id"] != identifier or actual["run_attempt"] != attempt:
        raise ValueError("Producer changed while it was inspected")
    # Even an incompatible/failed context must be authenticated before it can
    # classify a newer attempt as irrelevant to the current candidate.
    candidate.qualify_lineage(original, {}, metadata, {})
    bundle = {"context": original, "jobs": metadata["jobs"], "metadata": metadata, "run": actual}
    if not compatible_context(current, original):
        return bundle
    if actual.get("status") != "completed" or actual.get("conclusion") != "success":
        return bundle
    aggregate, aggregate_observation = candidate.lane_producer(metadata["jobs"], "CI result")
    if (aggregate_observation.get("run_attempt") != attempt
            or aggregate_observation.get("conclusion") != "success" or aggregate_observation.get("status") != "completed"
            or aggregate.get("conclusion") != "success" or aggregate.get("status") != "completed"):
        raise ValueError("Latest producer aggregate did not succeed")
    result_dir = Path(directory) / "aggregate"
    result_artifact = _artifact(original, metadata, f"ci-result-evidence--attempt-{aggregate['run_attempt']}", token, result_dir)
    aggregate_reference = {"job_id": aggregate["id"], "run_attempt": aggregate["run_attempt"],
        "observation_job_id": aggregate_observation["id"], "observation_attempt": aggregate_observation["run_attempt"],
        "carried_forward": aggregate["id"] != aggregate_observation["id"]}
    result = _read(result_dir / "result.json")
    bundle["result"] = result
    if (type(result.get("schema_version")) is not int or result["schema_version"] != 2 or result.get("status") != "passed"
            or result.get("candidate_context") != original or result.get("documentation_only") is not False):
        return bundle
    if result.get("reuse"):
        if not allow_reuse or static_qualifier is None:
            raise ValueError("Reuse summary chain exceeds its one-hop bound")
        pointer = result["reuse"]
        if not isinstance(pointer, dict):
            raise ValueError("Malformed reuse summary pointer")
        full_context = candidate.validate_context(pointer.get("original_context"))
        full_id = full_context["producer"]["run_id"]
        if full_id == identifier or not compatible_context(original, full_context):
            raise ValueError("Reuse summary points to a cycle or different candidate")
        summary_artifacts = Path(directory) / "summary-receipts"
        candidate.download_evidence(original, metadata, token, summary_artifacts,
                                    candidate.expected_lanes(workflow, documentation_only=True))
        static = static_qualifier(original, metadata, workflow, summary_artifacts, needs=result["jobs"])
        full_run = _get(current, token, f"/actions/runs/{full_id}")
        full = inspect_producer(current, full_run, workflow, token, Path(directory) / "original", qualifier, catalog,
                                allow_reuse=False, static_qualifier=static_qualifier)
        if not full.get("qualified") or full.get("context") != full_context:
            raise ValueError("Summary original execution no longer qualifies")
        if pointer.get("result_artifact") != full.get("result_artifact"):
            raise ValueError("Summary substituted its original aggregate artifact")
        expected = static + [{**row, "decision": "reused", "equivalence": "exact-candidate-v1",
                              "consumer_run_id": identifier} for row in full["references"]]
        recorded = result.get("evidence_references")
        if not _same_executions(recorded, expected, {identifier: metadata, full_id: full["metadata"]},
                                candidate.expected_lanes(workflow)):
            raise ValueError("Summary lane references do not identify the original execution")
        bundle.update(qualified=True, original_context=full_context, result=full["result"],
                      references=full["references"], lanes=full["lanes"], artifacts=full["artifacts"],
                      metadata=full["metadata"], result_artifact=full["result_artifact"],
                      result_producer=full["result_producer"],
                      summary_ref={"run_id": identifier, "run_attempt": attempt, "context": original,
                          "result_artifact": {key: result_artifact.get(key) for key in ("id", "name", "digest")},
                          "result_producer": aggregate_reference,
                          "static_references": static})
        return bundle
    if any(row.get("decision") != "executed" for row in result.get("evidence_references", [])):
        return bundle
    artifacts = Path(directory) / "receipts"
    candidate.download_evidence(original, metadata, token, artifacts, candidate.expected_lanes(workflow))
    verified = qualifier(result["jobs"], event="pull_request", source_sha=original["source_sha"],
                         tested_sha=original["checkout"]["commit"], workflow=workflow, artifacts=artifacts,
                         catalog=catalog, context=original, metadata=metadata)
    if (verified.get("status") != "passed"
            or not _same_executions(result.get("evidence_references"), verified.get("evidence_references"),
                                    {identifier: metadata}, candidate.expected_lanes(workflow))):
        raise ValueError("Original execution receipts do not qualify")
    references = [row for row in verified["evidence_references"] if row["lane"] != STATIC_LANE]
    manifests = {row["lane"]: _read(artifacts / row["artifact_name"] / "producer.json") for row in references}
    bundle.update(qualified=True, references=references, lanes=manifests, artifacts=artifacts,
                  result_artifact={key: result_artifact.get(key) for key in ("id", "name", "digest")},
                  result_producer=aggregate_reference)
    return bundle


def prepare(context, workflow, token, env, directory, qualifier, *, enabled=True, catalog=None, static_qualifier=None):
    """Return a bounded plan and selected original receipts, or ordinary execution."""
    started, bundles = time.monotonic(), {}
    plan = {"schema_version": 1, "decision": "execute", "reason": "evidence_unavailable", "history": []}
    try:
        if context["event"]["name"] != "pull_request":
            plan["reason"] = "full_validation_event"
            return plan, None
        observation = observe_current(context, token)
        if not enabled:
            plan.update(reason="reuse_disabled", current_pr=observation)
            return plan, None
        if observation["status"] != "current":
            plan.update(reason="superseded_candidate", current_pr=observation)
            return plan, None
        catalog = _read(Path.cwd() / "contracts/testing.json") if catalog is None else catalog
        runs = _pages(context, token, "/actions/workflows/ci.yml/runs?event=pull_request&head_sha=" + context["source_sha"], "workflow_runs")
        runs = [row for row in runs if row.get("id") != context["producer"]["run_id"]]
        if len(runs) > MAX_RUNS:
            raise ValueError("Same-source history exceeds its inspection bound")
        for run in runs:
            latest = _get(context, token, f"/actions/runs/{run['id']}/attempts/{run['run_attempt']}")
            if not isinstance(latest.get("run_started_at"), str) or not latest["run_started_at"]:
                raise ValueError("Latest attempt has no start ordering")
            run["attempt_started_at"] = latest["run_started_at"]
        lanes = {key: value for key, value in candidate.expected_lanes(workflow).items() if key != STATIC_LANE}
        runtime = {lane: candidate.runtime_manifest(lane, env) for lane in lanes}
        def inspect(run):
            bundle = inspect_producer(context, run, workflow, token, Path(directory) / f"run-{run['id']}", qualifier, catalog,
                                      static_qualifier=static_qualifier)
            bundles[run["id"]] = bundle
            return bundle
        plan = select_run(runs, context, inspect, runtime=runtime)
        plan["initial_pr"] = observation
        plan["current_pr"] = observe_current(context, token)
        if plan["current_pr"]["status"] != "current":
            plan.update(decision="execute", reason="superseded_candidate")
        if plan["decision"] == "reuse":
            bundle = bundles[plan["producer"]["run_id"]]
            plan["result_artifact"] = bundle["result_artifact"]
            plan["result_producer"] = bundle["result_producer"]
            return plan, bundle
    except (OSError, ValueError, KeyError, TypeError, AttributeError, zipfile.BadZipFile, zlib.error, EOFError, RuntimeError):
        pass
    finally:
        plan["lookup_seconds"] = round(time.monotonic() - started, 3)
    return plan, None


def qualify(plan, context, workflow, catalog, directory, qualifier, *, token, env, static_qualifier=None):
    """Recheck a plan after heavy jobs skipped; an unavailable proof now fails CI."""
    if (not isinstance(plan, dict) or type(plan.get("schema_version")) is not int or plan["schema_version"] != 1 or plan.get("decision") != "reuse"
            or plan.get("current_context") != context):
        raise ValueError("Skipped lanes have no bound reuse decision")
    observed, bundle = prepare(context, workflow, token, env, directory, qualifier, catalog=catalog, static_qualifier=static_qualifier)
    if observed.get("decision") != "reuse" or bundle is None:
        raise ValueError("Planned evidence no longer qualifies: " + observed.get("reason", "unknown"))
    for field in ("producer", "references", "original_context", "result_artifact", "result_producer", "summary_ref"):
        if observed.get(field) != plan.get(field):
            raise ValueError("Original producer changed after reuse planning")
    return observed, bundle


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", required=True, type=Path)
    parser.add_argument("--workflow", default=Path(WORKFLOW), type=Path)
    parser.add_argument("--catalog", default=Path("contracts/testing.json"), type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--work", required=True, type=Path)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args(argv)
    spec = importlib.util.spec_from_file_location("ci_result", Path(__file__).with_name("result.py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    context, workflow = _read(args.context), _read(args.workflow)
    plan, _ = prepare(context, workflow, os.environ.get("GITHUB_TOKEN", ""), os.environ, args.work, result.evaluate,
                      enabled=os.environ.get("CI_REUSE_ENABLED", "true").lower() == "true", catalog=_read(args.catalog),
                      static_qualifier=result.qualify_static)
    candidate.write_json(args.output, plan)
    if args.github_output:
        with args.github_output.open("a") as stream:
            stream.write("reuse=" + str(plan["decision"] == "reuse").lower() + "\n")
            stream.write("reuse_plan=" + json.dumps(plan, separators=(",", ":"), allow_nan=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
