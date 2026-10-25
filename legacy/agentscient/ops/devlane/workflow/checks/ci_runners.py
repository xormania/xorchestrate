"""Select CI capacity and recover an existing candidate through authenticated gh.

This operator command is independent of dispatch and does not run inside CI.
Requested recovery is never verification; inspect the resulting native jobs.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from ops.testkit import PlantFailed, Timeout, run

WORKFLOW = ".github/workflows/ci.yml"
ENVIRONMENTS = {"hosted": "github-hosted", "local": "self-hosted"}
RUNS_ON = "${{ fromJSON(vars.CI_RUNNER_MODE == 'local' && '\"self-hosted\"' || (vars.CI_RUNNER_MODE == '' || vars.CI_RUNNER_MODE == 'hosted') && '\"ubuntu-24.04\"' || 'INVALID_CI_RUNNER_MODE_expected_hosted_or_local') }}"
HOSTED = "${{ runner.environment == 'github-hosted' }}"


def mode(value):
    value = value or "hosted"
    if value not in ENVIRONMENTS:
        raise ValueError(f"ci-runner: expected CI_RUNNER_MODE hosted or local; found {value!r}; needed correct the repository variable")
    return value


def runner_info(ambient):
    if "CI_RUNNER_MODE" not in ambient:
        if ambient.get("GITHUB_RUN_ID"):
            raise ValueError("ci-runner: expected CI_RUNNER_MODE in Actions; found absent; needed the declared workflow environment")
        return {"requested_mode": "direct", "environment": "direct"}
    requested = mode(ambient["CI_RUNNER_MODE"])
    actual = ambient.get("CI_RUNNER_ENVIRONMENT", "")
    if actual != ENVIRONMENTS[requested]:
        raise ValueError(f"ci-runner: expected {ENVIRONMENTS[requested]}; found {actual!r}; needed a fresh run using CI_RUNNER_MODE={requested}")
    info = {"requested_mode": requested, "environment": actual}
    if requested == "hosted":
        image = {"os": ambient.get("ImageOS", ""), "version": ambient.get("ImageVersion", "")}
        if not all(image.values()):
            raise ValueError("ci-runner: expected hosted image identity; found missing ImageOS/ImageVersion; needed a standard Ubuntu runner")
        info["image"] = image
    return info


def gh(args, *, cwd, env):
    result = run(("gh", *args), cwd=cwd, env=env, timeout=60)
    if result.exit:
        raise ValueError(f"ci-runner: expected successful gh {args[0]}; found exit {result.exit}; needed inspect the GitHub command and its permissions")
    return result.stdout.strip()


def repository(value):
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", value):
        raise ValueError("ci-runner: expected owner/repository; found invalid repository; needed --repo OWNER/REPO")
    return value


def set_mode(repo, requested, *, cwd, env):
    repo, requested = repository(repo), mode(requested)
    gh(("variable", "set", "CI_RUNNER_MODE", "--repo", repo, "--body", requested), cwd=cwd, env=env)
    observed = gh(("variable", "get", "CI_RUNNER_MODE", "--repo", repo), cwd=cwd, env=env)
    if observed != requested:
        raise ValueError(f"ci-runner: expected CI_RUNNER_MODE={requested}; found {observed!r}; needed verify the repository setting before retrying")
    return {"state": "selected", "mode": requested}


def source(repo, run_id, *, cwd, env):
    body = json.loads(gh(("api", f"repos/{repository(repo)}/actions/runs/{run_id}"), cwd=cwd, env=env))
    if (not isinstance(body, dict) or body.get("id") != run_id
            or body.get("path") != WORKFLOW
            or body.get("repository", {}).get("full_name") != repo
            or body.get("head_repository", {}).get("full_name") != repo
            or body.get("event") not in ("pull_request", "push", "merge_group")
            or not re.fullmatch(r"[0-9a-f]{40}", body.get("head_sha", ""))):
        raise ValueError("ci-runner: expected this repository's CI candidate; found a different or incomplete source; needed the original trusted CI run ID")
    return body


def recover(repo, run_id, requested, *, cwd, env):
    body = source(repo, run_id, cwd=cwd, env=env)
    if body.get("status") != "completed":
        raise ValueError("ci-runner: expected a completed attempt; found active or queued work; needed cancel it and wait for completion before recovery")
    requested = mode(requested)
    set_mode(repo, requested, cwd=cwd, env=env)
    gh(("run", "rerun", str(run_id), "--repo", repo), cwd=cwd, env=env)
    return {"state": "requested", "mode": requested, "run_id": run_id, "source_sha": body["head_sha"]}


def verdict(body, jobs, requested, expected_sha):
    requested = mode(requested)
    if not re.fullmatch(r"[0-9a-f]{40}", expected_sha) or body.get("head_sha") != expected_sha:
        raise ValueError("ci-runner: expected the original candidate SHA; found a different or missing candidate; needed inspect the original run")
    expected = ["lane"]
    required = [job for job in jobs if job.get("name") in expected]
    passed = (body.get("status") == "completed" and body.get("conclusion") == "success"
              and sorted(job["name"] for job in required) == expected)
    for job in required:
        labels = job.get("labels") or []
        correct = "self-hosted" in labels if requested == "local" else "ubuntu-24.04" in labels and "self-hosted" not in labels
        passed = passed and correct and bool(job.get("runner_name")) and job.get("status") == "completed" and job.get("conclusion") == "success"
    return {"state": "passed" if passed else "not-verified", "mode": requested,
            "run_id": body.get("id"), "source_sha": expected_sha, "attempt": body.get("run_attempt"),
            "jobs": [{key: job.get(key) for key in ("name", "status", "conclusion", "labels", "runner_name", "html_url")} for job in required]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("set-mode", "recover", "inspect"))
    parser.add_argument("--repo", required=True)
    parser.add_argument("--mode", required=True, choices=tuple(ENVIRONMENTS))
    parser.add_argument("--run", type=int)
    parser.add_argument("--expect-sha")
    args = parser.parse_args(argv)
    context = {"cwd": Path.cwd(), "env": dict(os.environ)}
    try:
        if args.action == "set-mode":
            result = set_mode(args.repo, args.mode, **context)
        else:
            if not args.run or args.run < 1:
                raise ValueError("ci-runner: expected a positive run ID; found absent or invalid --run; needed the original CI run ID")
            if args.action == "recover":
                result = recover(args.repo, args.run, args.mode, **context)
            else:
                if not args.expect_sha:
                    raise ValueError("ci-runner: expected the original candidate; found no --expect-sha; needed the SHA returned by recovery")
                body = source(args.repo, args.run, **context)
                jobs = json.loads(gh(("api", f"repos/{args.repo}/actions/runs/{args.run}/jobs?filter=latest&per_page=100"), **context))
                if len(jobs["jobs"]) != jobs["total_count"]:
                    raise ValueError("ci-runner: expected complete job discovery; found a truncated page; needed inspect all jobs")
                result = verdict(body, jobs["jobs"], args.mode, args.expect_sha)
    except (ValueError, OSError, KeyError, TypeError, PlantFailed, Timeout) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 1 if result["state"] == "not-verified" else 0


if __name__ == "__main__":
    sys.exit(main())
