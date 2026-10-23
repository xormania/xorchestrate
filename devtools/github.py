"""GitHub tasks with work context, focused answers, and managed CI evidence."""

import hashlib
import json
import mimetypes
import os
import re
import time
import urllib.parse
import zipfile

from .artifacts import Artifacts
from .github_http import GitHubError, GitHubHTTP
from .store import UNSET, canonical, digest, stamp
from .work import Work, record

PASS = {"success", "neutral", "skipped"}
FAIL = {"failure", "error", "cancelled", "timed_out", "action_required", "startup_failure", "stale"}
COMMENTS = """comments(first:10,after:$cursor) { nodes { databaseId body url viewerDidAuthor author { login } }
    pageInfo { hasNextPage endCursor } }"""


def short_pr(pr):
    return {
        "number": pr["number"],
        "url": pr["html_url"],
        "title": pr["title"],
        "state": "merged" if pr.get("merged") or pr.get("merged_at") else "draft" if pr.get("draft") else pr["state"],
        "branch": pr["head"]["ref"],
        "base": pr["base"]["ref"],
        "revision": pr["head"]["sha"],
        "mergeable": pr.get("mergeable"),
        "merge_state": pr.get("mergeable_state", "unknown"),
    }


def short_run(run):
    return {
        "run": run["id"],
        "name": run.get("name"),
        "url": run["html_url"],
        "revision": run["head_sha"],
        "branch": run.get("head_branch"),
        "event": run.get("event"),
        "attempt": run.get("run_attempt", 1),
        "status": run["status"],
        "conclusion": run.get("conclusion"),
    }


def state_of(checks):
    if not checks:
        return "unknown"
    if any(row.get("status") != "completed" for row in checks):
        return "pending"
    if any(row.get("conclusion") in FAIL for row in checks):
        return "failed"
    return "passed" if all(row.get("conclusion") in PASS for row in checks) else "unknown"


class GitHub:
    def __init__(self, store, member, http=None):
        self.store, self.member = store, member
        path = store.directory / "team.json"
        self.config = json.loads(path.read_text()).get("github", {}) if path.is_file() else {}
        self.http = http or GitHubHTTP(self.config)

    def _context(self, repository=None, pr=None, job=UNSET):
        with self.store.connect() as db:
            order, job = self.store.current_work(db, self.member, job=job)
            work = record(db, "jobs", job) if job is not None else record(db, "work_orders", order) if order else {}
        linked = re.fullmatch(r"https://github\.com/([^/]+/[^/]+)/pull/(\d+)", work.get("pull_request", ""))
        repository = (
            repository or work.get("repository") or (linked[1] if linked else None) or self.config.get("repository")
        )
        if (
            not repository
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*/[A-Za-z0-9_.-]+", repository)
            or repository.split("/")[-1] in {".", ".."}
        ):
            raise ValueError("Supply owner/repository or record the repository on the selected job.")
        if pr is None and linked and linked[1].casefold() == repository.casefold():
            pr = int(linked[2])
        return {
            "work_order": order,
            "job": job,
            "repository": repository,
            "pr": pr,
            "work": work,
            "root": "/repos/" + repository,
        }

    def call(self, name, arguments):
        args = dict(arguments)
        if name == "ci_watch_stop":
            return self.ci_watch_stop(**args)
        context = self._context(**{key: args.pop(key) for key in ("repository", "pr", "job") if key in args})
        result = getattr(self, name)(context, **args)
        return self.http.clean({key: context[key] for key in ("work_order", "job", "repository")} | result)

    def _event(self, context, operation, **data):
        self.store.event(
            self.member,
            "github_" + operation,
            work_order=context["work_order"],
            job=context["job"],
            repository=context["repository"],
            pr=context["pr"],
            **data,
        )

    def _all(self, path, key=None, **query):
        rows, page = [], 1
        while True:
            result = self.http.request("GET", path, per_page=100, page=page, **query)
            batch = result[key] if key else result
            rows.extend(batch)
            if len(batch) < 100:
                return rows
            page += 1

    def _pr(self, context):
        if context["pr"] is None:
            branch = context["work"].get("branch")
            if branch:
                found = self.http.request(
                    "GET",
                    context["root"] + "/pulls",
                    state="open",
                    head=context["repository"].split("/")[0] + ":" + branch,
                    per_page=2,
                )
                if len(found) == 1:
                    context["pr"] = found[0]["number"]
            if context["pr"] is None:
                raise ValueError("Supply a PR number or use pr_open to attach the job's PR.")
        return self.http.request("GET", context["root"] + f"/pulls/{context['pr']}")

    @staticmethod
    def _revision(pr, expected):
        if expected and pr["head"]["sha"] != expected:
            raise ValueError("The PR head changed. Inspect the current revision before reviewing or merging it.")

    def _unchanged(self, context, previous):
        current = self._pr(context)
        self._revision(current, previous["head"]["sha"])
        if (current["base"].get("sha"), current.get("merge_commit_sha")) != (
            previous["base"].get("sha"),
            previous.get("merge_commit_sha"),
        ):
            raise ValueError("The PR base or test-merge revision changed during inspection. Refresh the result.")

    def _bind(self, context, pr):
        if context["job"] is not None:
            work = context["work"]
            if work.get("repository") and work["repository"].casefold() != context["repository"].casefold():
                raise ValueError(
                    "This job records another repository; update/select the intended job before attaching its PR."
                )
            Work(self.store, self.member).job_update(
                job=context["job"],
                repository=context["repository"],
                branch=pr["head"]["ref"],
                pull_request=pr["html_url"],
            )
        if context["work_order"] is not None:
            if context["job"] is None:
                Work(self.store, self.member).work_order_update(
                    work_order=context["work_order"],
                    repository=context["repository"],
                    branch=pr["head"]["ref"],
                    pull_request=pr["html_url"],
                )
            Work(self.store, self.member).work_link(
                work_order=context["work_order"], job=context["job"], kind="pull_request", reference=pr["html_url"]
            )

    def pr_list(self, context, state="open", page=1):
        rows = self.http.request("GET", context["root"] + "/pulls", state=state, sort="updated", per_page=20, page=page)
        return {"pull_requests": [short_pr(row) for row in rows], "next_page": page + 1 if len(rows) == 20 else None}

    def pr_get(self, context, description_offset=0):
        pr = self._pr(context)
        owner, repo = context["repository"].split("/")
        overview = self.http.graphql(
            """query Overview($owner:String!,$repo:String!,$number:Int!) {
            repository(owner:$owner,name:$repo) { pullRequest(number:$number) {
                reviewDecision mergeStateStatus reviews(last:10) { nodes { author { login } state body
                    commit { oid } url } } } } }""",
            owner=owner,
            repo=repo,
            number=context["pr"],
        )["repository"]["pullRequest"]
        ci = self.ci_status(context, _pr=pr)
        blockers = []
        if pr.get("draft"):
            blockers.append("PR is a draft")
        if pr.get("mergeable") is False:
            blockers.append("GitHub reports merge conflicts")
        if overview.get("reviewDecision") in {"CHANGES_REQUESTED", "REVIEW_REQUIRED"}:
            blockers.append(overview["reviewDecision"].lower().replace("_", " "))
        if ci["state"] != "passed":
            blockers.append("Observed CI: " + ci["state"])
        if overview.get("mergeStateStatus") not in {"CLEAN", "HAS_HOOKS"}:
            blockers.append("GitHub merge state: " + overview.get("mergeStateStatus", "UNKNOWN"))
        return {
            "pr": short_pr(pr),
            "description": (pr.get("body") or "")[description_offset : description_offset + 8000],
            "next_description_offset": description_offset + 8000
            if len(pr.get("body") or "") > description_offset + 8000
            else None,
            "review_decision": overview.get("reviewDecision"),
            "reviews": [{**row, "body": row.get("body", "")[:1500]} for row in overview["reviews"]["nodes"]],
            "ci": ci,
            "observed_blockers": blockers,
            "note": "GitHub's repository rules determine merge eligibility.",
        }

    def pr_open(self, context, branch=None, base=None, title=None, summary="", draft=True):
        if context["job"] is not None and context["work"].get("repository") not in {"", context["repository"]}:
            raise ValueError("Select a job in the intended repository before opening its PR.")
        reused = context["pr"] is not None
        if reused:
            pr = self._pr(context)
        else:
            branch = branch or context["work"].get("branch")
            base = base or self.config.get("base")
            if not branch or not base:
                raise ValueError(
                    "Record the job's published branch and supply the target base branch (or configure github.base)."
                )
            found = self.http.request(
                "GET",
                context["root"] + "/pulls",
                state="open",
                base=base,
                head=context["repository"].split("/")[0] + ":" + branch,
                per_page=2,
            )
            if found:
                if len(found) != 1:
                    raise ValueError("Multiple matching PRs; supply the intended PR number.")
                pr, reused = found[0], True
            else:
                work = context["work"]
                title = title or work.get("title")
                if not title:
                    raise ValueError("Supply a PR title or select a job with a title.")
                body = summary
                if work:
                    body += f"\n\nWork order: {context['work_order']}"
                    if context["job"] is not None:
                        body += f" · Job: {context['job']}"
                    body += "\n\n" + work["objective"]
                    body += "\n\nDone when:\n" + work["done_when"]
                    if work["constraints"]:
                        body += "\n\nBoundaries:\n" + work["constraints"]
                try:
                    pr = self.http.request(
                        "POST",
                        context["root"] + "/pulls",
                        dict(head=branch, base=base, title=title, body=body.strip(), draft=draft),
                    )
                except GitHubError as error:
                    if error.status != 422:
                        raise
                    # Another harness may have created this exact PR during our request.
                    found = self.http.request(
                        "GET",
                        context["root"] + "/pulls",
                        state="open",
                        base=base,
                        head=context["repository"].split("/")[0] + ":" + branch,
                        per_page=2,
                    )
                    if len(found) != 1:
                        raise error
                    pr, reused = found[0], True
        context["pr"] = pr["number"]
        self._bind(context, pr)
        self._event(context, "pr_attached", revision=pr["head"]["sha"], reused=reused)
        return {"pr": short_pr(pr), "reused": reused}

    def pr_update(self, context, draft=None, **fields):
        pr = self._pr(context)
        if not fields and draft is None:
            raise ValueError("Supply the PR fields to update.")
        if fields:
            pr = self.http.request("PATCH", context["root"] + f"/pulls/{context['pr']}", fields)
        if draft is not None and pr["draft"] != draft:
            mutation = "convertPullRequestToDraft" if draft else "markPullRequestReadyForReview"
            self.http.graphql(
                f"mutation Draft($id:ID!) {{ {mutation}(input:{{pullRequestId:$id}}) {{ pullRequest {{ id }} }} }}",
                id=pr["node_id"],
            )
            pr = self._pr(context)
        self._event(context, "pr_updated", revision=pr["head"]["sha"], fields=sorted(fields), draft=draft)
        return {"pr": short_pr(pr)}

    def pr_diff(self, context, path=None, revision=None, page=1, offset=0):
        pr = self._pr(context)
        self._revision(pr, revision)
        endpoint = context["root"] + f"/pulls/{context['pr']}/files"
        if path is None:
            rows = self.http.request("GET", endpoint, per_page=20, page=page)
            result = {
                "files": [
                    {
                        k: row.get(k)
                        for k in ("filename", "previous_filename", "status", "additions", "deletions", "changes")
                    }
                    for row in rows
                ],
                "next_page": page + 1 if len(rows) == 20 else None,
                "total_files": pr.get("changed_files"),
            }
        else:
            row = next((row for row in self._all(endpoint) if row["filename"] == path), None)
            if row is None:
                raise ValueError("That path is not in the PR's changed-file list.")
            patch = row.get("patch")
            result = {
                "path": path,
                "patch": patch[offset : offset + 16000] if patch is not None else None,
                "next_offset": offset + 16000 if patch and len(patch) > offset + 16000 else None,
                "note": "GitHub can omit or truncate patches for binary/large files; this is the patch it supplied.",
            }
        self._unchanged(context, pr)
        return {"revision": pr["head"]["sha"], **result}

    def pr_review(self, context, revision, decision, body, findings=None):
        pr = self._pr(context)
        self._revision(pr, revision)
        payload = dict(
            commit_id=revision,
            event={"comment": "COMMENT", "approve": "APPROVE", "request_changes": "REQUEST_CHANGES"}[decision],
            body=body,
        )
        if findings:
            payload["comments"] = [{"side": "RIGHT", **finding} for finding in findings]
        review = self.http.request("POST", context["root"] + f"/pulls/{context['pr']}/reviews", payload)
        self._event(context, "pr_reviewed", revision=revision, review=review["id"], decision=decision)
        return {"review": review["id"], "url": review["html_url"], "revision": revision, "state": review["state"]}

    def pr_comment(self, context, body):
        self._pr(context)
        comment = self.http.request("POST", context["root"] + f"/issues/{context['pr']}/comments", {"body": body})
        self._event(context, "pr_commented", comment=comment["id"])
        return {"comment": comment["id"], "url": comment["html_url"]}

    def pr_comments(self, context, kind="conversation", comment=None, page=1, offset=0):
        self._pr(context)
        root, number = context["root"], context["pr"]
        endpoint = {
            "conversation": f"/issues/{number}/comments",
            "review": f"/pulls/{number}/reviews",
            "inline": f"/pulls/{number}/comments",
        }[kind]
        if comment is not None:
            endpoint = {
                "conversation": f"/issues/comments/{comment}",
                "review": f"/pulls/{number}/reviews/{comment}",
                "inline": f"/pulls/comments/{comment}",
            }[kind]
            row = self.http.request("GET", root + endpoint)
            if kind != "review":
                field, path = ("issue_url", "issues") if kind == "conversation" else ("pull_request_url", "pulls")
                if row.get(field, "").casefold() != (self.http.api_root + root + f"/{path}/{number}").casefold():
                    raise ValueError("That comment does not belong to the selected PR.")
            rows = [row]
        else:
            rows = self.http.request("GET", root + endpoint, per_page=20, page=page)
        comments = []
        for row in rows:
            body = row.get("body") or ""
            start, limit = (offset, 8000) if comment is not None else (0, 1500)
            comments.append(
                {
                    "comment": row["id"],
                    "kind": kind,
                    "author": (row.get("user") or {}).get("login"),
                    "state": row.get("state"),
                    "revision": row.get("commit_id"),
                    "url": row.get("html_url"),
                    "body": body[start : start + limit],
                    "characters": len(body),
                    "next_offset": start + limit if len(body) > start + limit else None,
                }
            )
        return {"comments": comments, "next_page": page + 1 if comment is None and len(rows) == 20 else None}

    def pr_threads(self, context, action="read", thread=None, cursor=None, body=None):
        self._pr(context)
        if thread:
            result = self.http.graphql(
                "query Thread($id:ID!,$cursor:String) { node(id:$id) { ... on PullRequestReviewThread { "
                "id isResolved isOutdated path line pullRequest { number repository { nameWithOwner } } "
                + COMMENTS
                + " } } }",
                id=thread,
                cursor=cursor,
            )["node"]
            if (
                not result
                or result["pullRequest"]["number"] != context["pr"]
                or result["pullRequest"]["repository"]["nameWithOwner"].casefold() != context["repository"].casefold()
            ):
                raise ValueError("That thread does not belong to the selected PR.")
            if action == "reply":
                if not body:
                    raise ValueError("Supply the reply body.")
                result = self.http.graphql(
                    """mutation Reply($id:ID!,$body:String!) {
                    addPullRequestReviewThreadReply(input:{pullRequestReviewThreadId:$id,body:$body}) { comment { id url } }
                    }""",
                    id=thread,
                    body=body,
                )["addPullRequestReviewThreadReply"]
            elif action in {"resolve", "reopen"}:
                mutation = "resolveReviewThread" if action == "resolve" else "unresolveReviewThread"
                result = self.http.graphql(
                    f"mutation Resolve($id:ID!) {{ {mutation}(input:{{threadId:$id}}) {{ thread {{ id isResolved }} }} }}",
                    id=thread,
                )[mutation]
            if action != "read":
                self._event(context, "pr_thread_updated", thread=thread, action=action)
            else:
                for comment in result["comments"]["nodes"]:
                    comment["characters"] = len(comment["body"])
                    comment["body"] = comment["body"][:1500]
            return {"thread": result}
        if action != "read":
            raise ValueError("Supply the review thread ID.")
        owner, repo = context["repository"].split("/")
        result = self.http.graphql(
            """query Threads($owner:String!,$repo:String!,$number:Int!,$cursor:String) {
            repository(owner:$owner,name:$repo) { pullRequest(number:$number) { reviewThreads(first:20,after:$cursor) {
                nodes { id isResolved isOutdated path line
                    comments(first:1) { nodes { databaseId body url updatedAt author { login } } }
                    latest: comments(last:1) { totalCount nodes { databaseId body url updatedAt author { login } } } }
                pageInfo { hasNextPage endCursor } } } } }""",
            owner=owner,
            repo=repo,
            number=context["pr"],
            cursor=cursor,
        )["repository"]["pullRequest"]["reviewThreads"]
        for row in result["nodes"]:
            # Keep feedback bounded while exposing new replies and their update signal.
            latest = row.pop("latest")
            row["comments"]["totalCount"] = latest["totalCount"]
            row["comments"]["nodes"] = list({
                comment["databaseId"]: comment for comment in row["comments"]["nodes"] + latest["nodes"]
            }.values())
            for comment in row["comments"]["nodes"]:
                comment["body"] = comment["body"][:1500]
        return {
            "threads": result["nodes"],
            "next_cursor": result["pageInfo"]["endCursor"] if result["pageInfo"]["hasNextPage"] else None,
        }

    def pr_merge(self, context, revision, method=None):
        pr = self._pr(context)
        self._revision(pr, revision)
        if pr.get("merged"):
            return {"merged": True, "commit": pr["merge_commit_sha"], "already_merged": True}
        repo = self.http.request("GET", context["root"])
        allowed = [
            name
            for name, field in (
                ("merge", "allow_merge_commit"),
                ("squash", "allow_squash_merge"),
                ("rebase", "allow_rebase_merge"),
            )
            if repo.get(field)
        ]
        method = method or self.config.get("merge_method") or (allowed[0] if allowed else None)
        if method not in allowed:
            raise ValueError("Choose one of the repository's allowed merge methods: " + ", ".join(allowed))
        current = self.pr_get(context)
        if (
            current["pr"]["revision"] != revision
            or current["pr"]["branch"] != pr["head"]["ref"]
            or current["pr"]["base"] != pr["base"]["ref"]
        ):
            raise ValueError("The PR changed. Inspect its current revision and target before merging it.")
        if current["pr"]["state"] != "open" or current["observed_blockers"]:
            raise ValueError(
                "The PR is not ready to merge: "
                + "; ".join(current["observed_blockers"] or [current["pr"]["state"]])
            )
        # Honor GitHub's requirements even if the credential has bypass authority.
        # A passing subset of checks does not establish that required lane passed.
        result = self.http.request(
            "PUT", context["root"] + f"/pulls/{context['pr']}/merge", {"sha": revision, "merge_method": method}
        )
        self._event(
            context, "pr_merge", revision=revision, merged=result.get("merged", False), commit=result.get("sha")
        )
        return {
            "merged": result.get("merged", False),
            "commit": result.get("sha"),
            "message": result.get("message"),
            "method": method,
        }

    def _run(self, context, run):
        if run is None:
            raise ValueError("Choose a run from ci_status and supply its run ID.")
        return self.http.request("GET", context["root"] + f"/actions/runs/{run}")

    def ci_status(self, context, run=None, page=1, _pr=None, _observe=False):
        if run is not None:
            row = self._run(context, run)
            return {"state": state_of([row]), "runs": [short_run(row)]}
        pr = _pr or self._pr(context)
        head = pr["head"]["sha"]
        revisions = [head]
        if not pr.get("merged") and pr.get("merge_commit_sha") and pr["merge_commit_sha"] != head:
            revisions.append(pr["merge_commit_sha"])
        groups, runs = [], {}
        for revision in revisions:
            checks = self._all(context["root"] + f"/commits/{revision}/check-runs", "check_runs", filter="latest")
            statuses = self._all(context["root"] + f"/commits/{revision}/status", "statuses")
            group = [
                {
                    "check": c["id"],
                    "name": c["name"],
                    "status": c["status"],
                    "conclusion": c.get("conclusion"),
                    "url": c.get("details_url"),
                    "revision": revision,
                }
                for c in checks
            ]
            group += [
                {
                    "name": s["context"],
                    "status": "completed" if s["state"] != "pending" else "in_progress",
                    "conclusion": s["state"],
                    "url": s.get("target_url"),
                    "revision": revision,
                }
                for s in statuses
            ]
            groups.append(group)
            for row in self._all(context["root"] + "/actions/runs", "workflow_runs", head_sha=revision):
                runs[row["id"]] = short_run(row)
        # GitHub uses test-merge results when present. Report both revision sets, never mix them into a false green.
        effective = groups[-1] if len(groups) > 1 and groups[-1] else groups[0]
        active_runs = [row for row in runs.values() if row["status"] != "completed"]
        all_checks = [row for group in groups for row in group]
        offset = (page - 1) * 20
        state = "pending" if active_runs else state_of(effective)
        self._unchanged(context, pr)
        result = {
            "revision": head,
            "tested_revision": effective[0]["revision"] if effective else None,
            "state": state,
            "checks": all_checks[offset : offset + 20],
            "check_count": len(all_checks),
            "next_page": page + 1 if offset + 20 < len(all_checks) else None,
            "runs": sorted(runs.values(), key=lambda row: row["run"], reverse=True)[offset : offset + 20],
            "run_count": len(runs),
            "next_run_page": page + 1 if offset + 20 < len(runs) else None,
            "note": "Observed checks; GitHub decides which requirements block merging.",
        }
        if _observe:
            # Watches need changes across all checks/runs, including outside the agent's preview page.
            result["_observation"] = digest([
                sorted(all_checks, key=canonical), sorted(runs.values(), key=canonical),
            ])
        return result

    def ci_failures(self, context, run, check=None, attempt=None, page=1, log_offset=None, annotation_page=1):
        row = self._run(context, run)
        attempt = attempt or row.get("run_attempt", 1)
        endpoint = context["root"] + f"/actions/runs/{run}/attempts/{attempt}/jobs"
        tasks = self._all(endpoint, "jobs")
        selected = (
            [task for task in tasks if task["id"] == check]
            if check
            else [task for task in tasks if task.get("conclusion") in FAIL]
        )
        if check and not selected:
            raise ValueError("That CI check is not part of this run attempt.")
        offset = (page - 1) * 5
        details = []
        for task in selected[offset : offset + 5]:
            log, log_error = "", None
            try:
                with self.http.download(
                    context["root"] + f"/actions/jobs/{task['id']}/logs", maximum=16 * 1024 * 1024
                ) as stream:
                    log = self.http.clean(stream.read().decode("utf8", errors="replace"))
            except GitHubError as error:
                log_error = str(error)
            if log_offset is None:
                lines = log.splitlines()
                hits = [
                    i for i, line in enumerate(lines) if re.search(r"error|fail|exception|assert|panic", line, re.I)
                ]
                indices = sorted({n for i in hits[-15:] for n in range(max(0, i - 2), min(len(lines), i + 4))})
                excerpt = "\n".join(lines[i] for i in indices)[-8000:] if indices else log[-8000:]
            else:
                excerpt = log[log_offset : log_offset + 8000]
            annotations, annotation_error = [], None
            check_url = task.get("check_run_url", "")
            match = re.search(r"/check-runs/(\d+)$", check_url)
            if match:
                try:
                    annotations = self.http.request(
                        "GET",
                        context["root"] + f"/check-runs/{match[1]}/annotations",
                        per_page=10,
                        page=annotation_page,
                    )
                except GitHubError as error:
                    annotation_error = str(error)
            details.append(
                {
                    "check": task["id"],
                    "name": task["name"],
                    "conclusion": task.get("conclusion"),
                    "steps": [
                        {k: step.get(k) for k in ("name", "number", "status", "conclusion")}
                        for step in task.get("steps", [])
                        if step.get("conclusion") in FAIL
                    ],
                    "annotations": [
                        {
                            **{k: item.get(k) for k in ("path", "start_line", "end_line", "annotation_level", "title")},
                            "message": (item.get("message") or "")[:1500],
                        }
                        for item in annotations
                    ],
                    "next_annotation_page": annotation_page + 1 if len(annotations) == 10 else None,
                    "annotation_error": annotation_error,
                    "log_excerpt": excerpt,
                    "log_error": log_error,
                    "log_characters": len(log),
                    "next_log_offset": log_offset + 8000
                    if log_offset is not None and log_offset + 8000 < len(log)
                    else None,
                    "url": task.get("html_url"),
                }
            )
        return {
            "run": short_run(row),
            "attempt": attempt,
            "failures": details,
            "next_page": page + 1 if offset + 5 < len(selected) else None,
            "note": "Use check and log_offset=0 for consecutive log pages; excerpts are evidence, not a root-cause verdict.",
        }

    def ci_workflows(self, context, page=1):
        result = self.http.request("GET", context["root"] + "/actions/workflows", per_page=20, page=page)
        return {
            "workflows": [
                {k: row.get(k) for k in ("id", "name", "path", "state", "html_url")} for row in result["workflows"]
            ],
            "next_page": page + 1 if page * 20 < result["total_count"] else None,
        }

    def ci_run(self, context, workflow, ref=None, inputs=None):
        ref = ref or context["work"].get("branch")
        if not ref:
            raise ValueError("Supply the published branch/tag or record the job's branch.")
        self.http.request(
            "POST",
            context["root"] + "/actions/workflows/" + urllib.parse.quote(workflow, safe="") + "/dispatches",
            {"ref": ref, "inputs": inputs or {}},
        )
        self._event(context, "ci_requested", workflow=workflow, ref=ref)
        return {
            "requested": True,
            "workflow": workflow,
            "ref": ref,
            "note": "Accepted by GitHub; no run completion or revision is inferred.",
        }

    def ci_rerun(self, context, run=None, failed_only=True):
        row = self._run(context, run)
        self.http.request(
            "POST", context["root"] + f"/actions/runs/{run}/" + ("rerun-failed-jobs" if failed_only else "rerun")
        )
        self._event(
            context, "ci_rerun_requested", run=run, revision=row["head_sha"], previous_attempt=row.get("run_attempt", 1)
        )
        return {"requested": True, "run": run, "revision": row["head_sha"], "failed_only": failed_only}

    def ci_cancel(self, context, run=None):
        row = self._run(context, run)
        if row["status"] == "completed":
            return {"requested": False, "run": short_run(row), "note": "Run has already completed."}
        self.http.request("POST", context["root"] + f"/actions/runs/{run}/cancel")
        self._event(context, "ci_cancel_requested", run=run, revision=row["head_sha"])
        return {"requested": True, "run": run}

    def ci_artifacts(self, context, run=None, page=1):
        row = self._run(context, run)
        result = self.http.request("GET", context["root"] + f"/actions/runs/{run}/artifacts", per_page=20, page=page)
        return {
            "run": short_run(row),
            "artifacts": [
                {
                    k: item.get(k)
                    for k in ("id", "name", "size_in_bytes", "expired", "created_at", "expires_at", "digest")
                }
                for item in result["artifacts"]
            ],
            "next_page": page + 1 if page * 20 < result["total_count"] else None,
        }

    def ci_artifact_save(self, context, artifact, entry=None, name=None, entry_offset=0):
        info = self.http.request("GET", context["root"] + f"/actions/artifacts/{artifact}")
        if info.get("expired"):
            raise ValueError("This GitHub artifact has expired and cannot be downloaded.")
        run = self._run(context, info["workflow_run"]["id"])
        maximum = int(self.config.get("max_download_bytes", 256 * 1024 * 1024))
        with self.http.download(context["root"] + f"/actions/artifacts/{artifact}/zip", maximum=maximum) as archive:
            digest = hashlib.file_digest(archive, "sha256").hexdigest()
            if info.get("digest") and info["digest"] != "sha256:" + digest:
                raise ValueError("Downloaded artifact does not match GitHub's digest.")
            archive.seek(0)
            with zipfile.ZipFile(archive) as zipped:
                entries = [
                    {"path": item.filename, "bytes": item.file_size} for item in zipped.infolist() if not item.is_dir()
                ]
                origin = {
                    "provider": "github",
                    "reference": f"{context['repository']}/actions/artifacts/{artifact}"
                    + ("/" + entry if entry else ""),
                    "repository": context["repository"],
                    "pr": context["pr"],
                    "run": run["id"],
                    "observed_attempt": run.get("run_attempt", 1),
                    "note": "GitHub's artifact metadata does not identify its producing attempt; observed_attempt is the run's current attempt.",
                    "revision": info["workflow_run"]["head_sha"],
                    "source_artifact": artifact,
                    "entry": entry,
                    "url": run["html_url"],
                    "download_sha256": digest,
                }
                storage = Artifacts(self.store, self.member)
                if entry:
                    if entry not in {item["path"] for item in entries}:
                        raise ValueError("That entry is not in the CI artifact archive.")
                    stream = zipped.open(entry)
                    result = storage.ingest(
                        name or entry,
                        stream,
                        mimetypes.guess_type(entry)[0] or "application/octet-stream",
                        work_order=context["work_order"],
                        job=context["job"],
                        provenance=origin,
                        maximum=maximum,
                    )
            if not entry:
                archive.seek(0)
                result = storage.ingest(
                    name or info["name"] + ".zip",
                    archive,
                    "application/zip",
                    work_order=context["work_order"],
                    job=context["job"],
                    provenance=origin,
                    maximum=maximum,
                )
        self._event(
            context,
            "ci_artifact_saved",
            artifact=result["artifact"]["id"],
            source_artifact=artifact,
            run=run["id"],
            revision=origin["revision"],
        )
        return {
            **result,
            "source": origin,
            "entries": entries[entry_offset : entry_offset + 50],
            "entry_count": len(entries),
            "next_entry_offset": entry_offset + 50 if entry_offset + 50 < len(entries) else None,
        }

    def ci_watch(self, context, run=None, timeout=3600, *, reviews=False):
        from .watcher import start

        revision = self._run(context, run)["head_sha"] if run is not None else self._pr(context)["head"]["sha"]
        now = time.time()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if reviews:
                old = db.execute(
                    "SELECT id FROM github_watches WHERE work_order=? AND job IS NULL AND repository=? "
                    "AND pr=? AND run IS NULL AND revision=? ORDER BY (member=?) DESC LIMIT 1",
                    (context["work_order"], context["repository"], context["pr"], revision, self.member),
                ).fetchone()
            else:
                old = db.execute(
                    "SELECT id FROM github_watches WHERE member=? AND repository=? AND pr IS ? AND run IS ? AND revision=?",
                    (self.member, context["repository"], context["pr"], run, revision),
                ).fetchone()
            if old:
                ident = old[0]
                db.execute(
                    "UPDATE github_watches SET state='active',work_order=?,job=?,expires=?,next_poll=?,poll_pid=NULL,updated=?,reviews=max(reviews,?),member=? WHERE id=?",
                    (context["work_order"], context["job"], now + timeout, now, stamp(), int(reviews), self.member, ident),
                )
            else:
                ident = db.execute(
                    "INSERT INTO github_watches(member,work_order,job,repository,pr,run,revision,expires,next_poll,updated,reviews) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        self.member,
                        context["work_order"],
                        context["job"],
                        context["repository"],
                        context["pr"],
                        run,
                        revision,
                        now + timeout,
                        now,
                        stamp(),
                        int(reviews),
                    ),
                ).lastrowid
            if reviews:
                db.execute(
                    "UPDATE github_watches SET state='stopped' WHERE id!=? AND work_order=? AND job IS NULL "
                    "AND repository=? AND pr=? AND run IS NULL AND state='active'",
                    (ident, context["work_order"], context["repository"], context["pr"]),
                )
        self._event(context, "ci_watched", watch=ident, run=run, revision=revision)
        try:
            start(self.store)
        except Exception as error:
            self._event(context, "watch_start_failed", error=self.http.clean(str(error))[:1000])
            raise RuntimeError("PR tracking could not start. Ask the operator to inspect the tracking service.") from error
        return {
            "watch": ident,
            "revision": revision,
            "run": run,
            "notifications": "Durable messages to this member and its current supervising role when still scoped to the watched work.",
        }

    def ci_watch_stop(self, watch):
        with self.store.connect() as db:
            changed = db.execute(
                "UPDATE github_watches SET state='stopped',updated=? WHERE id=? AND member=? AND state='active'",
                (stamp(), watch, self.member),
            ).rowcount
        return {"watch": watch, "stopped": bool(changed)}

    def tick(self):
        now = time.time()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = None
            for candidate in db.execute(
                "SELECT * FROM github_watches WHERE member=? AND state='active' ORDER BY next_poll", (self.member,)
            ):
                due = candidate["next_poll"] <= now
                if not due and candidate["poll_pid"]:
                    try:
                        os.kill(candidate["poll_pid"], 0)
                    except ProcessLookupError:
                        due = True
                    except PermissionError:
                        pass
                if due:
                    row = candidate
                    break
            if not row:
                return
            watch = dict(row)
            lease = now + 600
            db.execute("UPDATE github_watches SET next_poll=?,poll_pid=? WHERE id=?", (lease, os.getpid(), watch["id"]))
        context = {key: watch[key] for key in ("work_order", "job", "repository", "pr")}
        context.update(work={}, root="/repos/" + watch["repository"])
        terminal, retry_at, findings = False, 0, None
        try:
            if now >= watch["expires"]:
                result, terminal = {"state": "expired", "revision": watch["revision"]}, True
            elif watch["run"] is None and self._pr(context)["head"]["sha"] != watch["revision"]:
                result, terminal = {"state": "superseded", "revision": watch["revision"]}, True
            elif watch["reviews"]:
                from .codex_reviews import CodexReviews

                pr = self._pr(context)
                if pr["state"] == "closed":
                    result, terminal = {"state": "closed", "revision": watch["revision"]}, True
                else:
                    tracker = CodexReviews(self)
                    review = tracker.status(context, watch["revision"])
                    ci = self.ci_status(context, _pr=pr, _observe=True)
                    findings = [tracker.findings, ci["_observation"]]
                    result = {
                        "state": "ci_failed" if ci["state"] == "failed" else review["state"]
                        if review["state"] != "ready" else "ready" if ci["state"] == "passed" else "pending",
                        "revision": watch["revision"], "ci": ci["state"], "reviews": review["reviews"],
                        "unresolved": review["unresolved"],
                    }
            else:
                result = self.ci_status(context, run=watch["run"])
                terminal = result["state"] in {"passed", "failed"}
        except Exception as error:
            retry_at = getattr(error, "retry_at", None) or 0
            result = {
                "state": "unavailable",
                "error": self.http.clean(str(error))[:1000],
                "revision": watch["revision"],
            }
        result = self.http.clean(result)
        from .control import binding, includes
        from .role_packages import names_for_adapter

        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT * FROM github_watches WHERE id=?", (watch["id"],)).fetchone()
            if current["state"] != "active" or current["next_poll"] != lease:
                return
            if watch["reviews"]:
                recipients = {r[0] for r in db.execute(
                    "SELECT member FROM member_bindings WHERE work_order=? AND job IS NULL AND role IN (SELECT value FROM json_each(?))",
                    (watch["work_order"], canonical([*names_for_adapter("co"), *names_for_adapter("xo")])),
                )}
            else:
                recipients = {self.member}
                scope = binding(db, self.member)
                if includes(scope, watch["work_order"], watch["job"]) and scope["reports_to"]:
                    parent = binding(db, scope["reports_to"])
                    if includes(parent, watch["work_order"], watch["job"]):
                        recipients.add(parent["member"])
            signature = digest([result, findings, sorted(recipients)])
            sequence = current["sequence"]
            if signature != current["signature"]:
                sequence += 1
                body = (
                    f"{'Work-order PR #' + str(watch['pr']) if watch['reviews'] else 'GitHub CI'}: {result['state']}.\n"
                    + canonical(result)[:1500]
                    + ('\nUse monitor(section="feedback") for reviews or monitor(section="ci") for CI. '
                       'After integrating fixes, work_submit requests fresh reviews.' if watch["reviews"] else '')
                )
                for recipient in recipients:
                    self.store._send(
                        db,
                        self.member,
                        recipient,
                        body,
                        key=f"github-watch-{watch['id']}-{sequence}-{recipient}",
                        work_order=watch["work_order"],
                        job=watch["job"],
                    )
                self.store._event(db, self.member, "github_ci_watch_update", {
                    "work_order": watch["work_order"], "job": watch["job"], "watch": watch["id"],
                    "state": result["state"], "revision": watch["revision"],
                })
            # Commit the observation with its notifications. A restart is quiet,
            # but a later return to the same state is a new transition.
            db.execute(
                "UPDATE github_watches SET state=?,signature=?,sequence=?,next_poll=?,poll_pid=NULL,updated=? WHERE id=? AND state='active' AND next_poll=?",
                (
                    "finished" if terminal else "active",
                    signature,
                    sequence,
                    max(retry_at, time.time() + max(15, self.config.get("poll_seconds", 30))),
                    stamp(),
                    watch["id"],
                    lease,
                ),
            )
