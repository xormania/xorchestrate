"""Cancel proved obsolete PR CI from trusted default-branch workflow code.

GitHub remains the authority for run state. Cancellation requests are retained
separately from the state observed after the request; neither supplies a verdict.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

_spec = importlib.util.spec_from_file_location("ci_candidate", Path(__file__).with_name("candidate.py"))
candidate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(candidate)

WORKFLOW = ".github/workflows/ci.yml"
ACTIVE = {"queued", "in_progress", "waiting", "pending", "requested"}
MATERIAL = {"success", "failure", "timed_out", "action_required"}
TITLE = re.compile(r"CI:pull_request:(opened|synchronize|reopened|edited):([1-9][0-9]*):([0-9a-f]{40}):([0-9a-f]{40})")
ERRORS = (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError)


def _repository(value):
    if not candidate._repository(value):
        raise ValueError("Repository identity is incomplete")
    return {key: value[key] for key in ("id", "full_name")}


def _run(value, repository, workflow):
    """Authenticate native CI identity without relying on owner-supplied prose."""
    if (not isinstance(value, dict) or not candidate._positive(value.get("id"))
            or not candidate._positive(value.get("run_attempt"))
            or value.get("workflow_id") != workflow["id"] or value.get("path") != WORKFLOW
            or value.get("event") != "pull_request" or _repository(value.get("repository")) != repository
            or _repository(value.get("head_repository")) != repository):
        raise ValueError("Run is not identified same-repository PR CI")
    title = TITLE.fullmatch(value.get("display_title", ""))
    if title is None or title[3] != value.get("head_sha"):
        raise ValueError("Run title does not bind its native source")
    associations = value.get("pull_requests")
    if not isinstance(associations, list):
        raise ValueError("Run PR association is missing")
    matches = [pr for pr in associations if isinstance(pr, dict) and pr.get("number") == int(title[2])]
    if len(matches) != 1:
        raise ValueError("Run PR association is ambiguous")
    pr, branch = matches[0], value.get("head_branch")
    if (not isinstance(branch, str) or not branch or pr.get("head", {}).get("ref") != branch
            or pr.get("head", {}).get("repo", {}).get("id") != repository["id"]
            or pr.get("base", {}).get("repo", {}).get("id") != repository["id"]):
        raise ValueError("Run PR branch/repository association is incomplete")
    # GitHub's association head/base SHAs can advance with the live PR. The
    # native run head and generated title retain the run's immutable source.
    return {"id": value["id"], "run_attempt": value["run_attempt"], "head": title[3],
            "event_base": title[4], "pr": int(title[2]), "branch": branch,
            "action": title[1], "workflow_id": workflow["id"]}


def _current(number, repository, get):
    pr = get(f"/pulls/{number}")
    if not isinstance(pr, dict) or pr.get("number") != number or pr.get("state") != "open":
        raise ValueError("PR is not currently open")
    head, base = pr.get("head", {}), pr.get("base", {})
    if (_repository(head.get("repo")) != repository or _repository(base.get("repo")) != repository
            or not candidate._sha(head.get("sha")) or not candidate._sha(base.get("sha"))
            or not isinstance(head.get("ref"), str) or not head["ref"]
            or not isinstance(base.get("ref"), str) or not base["ref"]):
        raise ValueError("Current same-repository PR identity is incomplete")
    source = get("/git/ref/heads/" + quote(head["ref"], safe="/"))
    if (source.get("ref") != "refs/heads/" + head["ref"]
            or source.get("object", {}).get("type") != "commit"
            or source.get("object", {}).get("sha") != head["sha"]):
        raise ValueError("Canonical source ref does not match the PR")
    return {"pr": number, "head": head["sha"], "branch": head["ref"],
            "base": base["sha"], "base_ref": base["ref"]}


def _survives(run):
    return ((run.get("status") in ACTIVE and run.get("conclusion") is None)
            or (run.get("status") == "completed" and run.get("conclusion") in MATERIAL))


def _runs(get, branch):
    rows = []
    for page in range(1, 6):
        result = get("/actions/workflows/ci.yml/runs?event=pull_request&branch=" + quote(branch, safe="")
                     + f"&per_page=100&page={page}")
        batch = result.get("workflow_runs")
        if not isinstance(batch, list) or any(not isinstance(row, dict) for row in batch):
            raise ValueError("Workflow run collection is incomplete")
        rows.extend(batch)
        if len(batch) < 100:
            return rows, False
    # Each target has its own positive proof. Old unrelated history outside the
    # bounded recent scan cannot invalidate an identified cancellation target.
    return rows, True


def reconcile(repository, trigger_run_id, *, get, cancel):
    """Request cancellation only while a newer current-source run is proved.

    ``get`` reads repository-relative REST paths; ``cancel`` submits an ordinary
    cancellation for one run ID. Both adapters are supplied by the trusted caller.
    Unavailable or changing observations leave validation running.
    """
    report = {"schema_version": 1, "trigger_run_id": trigger_run_id,
              "status": "observed", "reason": "no_obsolete_runs", "cancellations": []}
    try:
        repository = _repository(repository)
        if not candidate._positive(trigger_run_id):
            raise ValueError("Trigger run ID is invalid")
        workflow = get("/actions/workflows/ci.yml")
        if (not isinstance(workflow, dict) or not candidate._positive(workflow.get("id"))
                or workflow.get("path") != WORKFLOW):
            raise ValueError("Native CI workflow identity is unavailable")
        trigger = get(f"/actions/runs/{trigger_run_id}")
        replacement = _run(trigger, repository, workflow)
        if replacement["id"] != trigger_run_id or not _survives(trigger):
            raise ValueError("Current replacement run is unavailable")
        current = _current(replacement["pr"], repository, get)
        if replacement["head"] != current["head"] or replacement["branch"] != current["branch"]:
            report["reason"] = "trigger_source_is_not_current"
            return report
        report["replacement"] = replacement
        targets = []
        runs, report["history_truncated"] = _runs(get, current["branch"])
        for run in runs:
            if run.get("status") not in ACTIVE or run.get("conclusion") is not None:
                continue
            try:
                identity = _run(run, repository, workflow)
            except ERRORS:
                continue
            if (identity["id"] < replacement["id"] and identity["head"] != current["head"]
                    and identity["pr"] == current["pr"] and identity["branch"] == current["branch"]):
                targets.append(identity)
        for target in targets:
            action = {"run_id": target["id"], "run_attempt": target["run_attempt"], "requested": False}
            report["cancellations"].append(action)
            try:
                observed = get(f"/actions/runs/{target['id']}")
                if _run(observed, repository, workflow) != target:
                    raise ValueError("Target run identity or attempt changed")
                if observed.get("status") not in ACTIVE or observed.get("conclusion") is not None:
                    action.update(status="observed_completed", observed_status=observed.get("status"),
                                  observed_conclusion=observed.get("conclusion"))
                    continue
                survivor = get(f"/actions/runs/{trigger_run_id}")
                if _run(survivor, repository, workflow) != replacement or not _survives(survivor):
                    raise ValueError("Replacement run changed or no longer survives")
                if _current(current["pr"], repository, get) != current:
                    raise ValueError("Current source or PR changed before cancellation")
                cancel(target["id"])
                action.update(status="requested", requested=True)
                report["reason"] = "obsolete_cancellation_requested"
                try:
                    after = get(f"/actions/runs/{target['id']}")
                    if _run(after, repository, workflow) == target:
                        action.update(observed_status=after.get("status"), observed_conclusion=after.get("conclusion"))
                except ERRORS:
                    action["observation"] = "unavailable_after_request"
            except ERRORS:
                action.update(status="refused", reason="proof_unavailable_or_changed")
                report["status"] = "partial"
        return report
    except ERRORS:
        report.update(status="unavailable", reason="proof_unavailable_or_changed")
        return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    repository = _repository(event.get("repository"))
    if (os.environ.get("GITHUB_EVENT_NAME") != "workflow_run" or event.get("action") not in {"requested", "in_progress"}
            or repository["full_name"] != os.environ.get("GITHUB_REPOSITORY")
            or str(repository["id"]) != os.environ.get("GITHUB_REPOSITORY_ID")
            or not candidate._sha(os.environ.get("GITHUB_SHA"))
            or os.environ.get("GITHUB_REF") != "refs/heads/" + event["repository"].get("default_branch", "")):
        raise ValueError("Queue cleanup must run from an identified trusted workflow event")
    trigger = event.get("workflow_run", {})
    if not candidate._positive(trigger.get("id")):
        raise ValueError("Workflow event has no trigger identity")
    prefix = "https://api.github.com/repos/" + repository["full_name"]
    token = os.environ["GITHUB_TOKEN"]

    def get(path):
        return json.loads(candidate.api_bytes(prefix + path, token, candidate.MAX_JSON))

    def cancel(identifier):
        request = Request(prefix + f"/actions/runs/{identifier}/cancel", data=b"", method="POST",
                          headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
                                   "X-GitHub-Api-Version": "2022-11-28"})
        with urlopen(request, timeout=30) as response:
            if response.status != 202:
                raise ValueError("GitHub did not accept the cancellation request")

    report = reconcile(repository, trigger["id"], get=get, cancel=cancel)
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
