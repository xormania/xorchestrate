"""Bounded PR feedback and CI diagnosis without agent-managed provider identifiers."""

import io
import json

from .artifacts import Artifacts
from .github import FAIL
from .store import canonical


def packet(github, context, section, page=1, offset=None):
    pr = github._pr(context)
    revision = pr["head"]["sha"]
    if section == "ci":
        status = github.ci_status(context, _pr=pr)
        runs = {row["run"]: row for row in status["runs"]}
        next_page = status.get("next_run_page")
        while next_page:
            more = github.ci_status(context, page=next_page, _pr=pr)
            runs.update({row["run"]: row for row in more["runs"]})
            next_page = more.get("next_run_page")
        failed = []
        for run in runs.values():
            if run["conclusion"] not in FAIL:
                continue
            tasks = github._all(context["root"] + f"/actions/runs/{run['run']}/attempts/{run['attempt']}/jobs", "jobs")
            failed.extend((run, task["id"]) for task in tasks if task.get("conclusion") in FAIL)
        start = (page - 1) * 2
        failures = []
        for run, check in failed[start : start + 2]:
            result = github.ci_failures(context, run=run["run"], check=check, attempt=run["attempt"], log_offset=offset)
            if result["run"]["revision"] != run["revision"]:
                raise ValueError("CI changed during inspection. Inspect again.")
            failures.extend({"workflow": run["name"], **row} for row in result["failures"])
        result = {
            "revision": revision,
            "ci": status,
            "failures": failures,
            "failed_check_count": len(failed),
            "next_page": page + 1 if start + 2 < len(failed) else None,
            "note": "Failure excerpts by default; offset=0 begins consecutive log reads. Page selects two failed checks across the job's observed runs. Provider logs may be expired or unavailable.",
        }
    else:
        if offset is not None:
            raise ValueError("Feedback uses page; full comment bodies are retained in the returned evidence.")
        groups, more = {}, False
        endpoints = {
            "conversation": f"/issues/{context['pr']}/comments",
            "review": f"/pulls/{context['pr']}/reviews",
            "inline": f"/pulls/{context['pr']}/comments",
        }
        for kind, endpoint in endpoints.items():
            rows = github.http.request("GET", context["root"] + endpoint, per_page=10, page=page)
            more |= len(rows) == 10
            groups[kind] = [
                {
                    "author": (row.get("user") or {}).get("login"),
                    "body": row.get("body") or "",
                    "url": row.get("html_url"),
                    "state": row.get("state"),
                    "path": row.get("path"),
                    "line": row.get("line"),
                    "revision": row.get("commit_id"),
                    "original_revision": row.get("original_commit_id"),
                }
                for row in rows
            ]
        result = {
            "revision": revision,
            "comments": groups,
            "next_page": page + 1 if more else None,
            "note": "Review comments can concern older revisions; use their location/revision and the bound source when assessing them. Complete bodies are retained in evidence.",
        }
    github._unchanged(context, pr)
    return github.http.clean(result)


def retain(store, member, work, section, result):
    saved = Artifacts(store, member).ingest(
        f"{section}-{result['revision']}.json",
        io.BytesIO(canonical(result).encode()),
        "application/json",
        work_order=work["work_order"],
        job=work["id"],
    )
    preview = json.loads(canonical(result))
    for failure in preview.get("failures", []):
        failure["log_excerpt"] = failure["log_excerpt"][:1500]
        failure["annotations"] = failure["annotations"][:3]
    for group in preview.get("comments", {}).values():
        for comment in group:
            comment["characters"] = len(comment["body"])
            comment["body"] = comment["body"][:500]
    return {
        **preview,
        "evidence": "artifact:" + str(saved["artifact"]["id"]),
        "evidence_note": "Read the retained diagnostic packet through work_context for complete excerpts, annotations and comment bodies.",
    }
