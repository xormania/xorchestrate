"""Two Codex reviews on the submitted work-order head, through existing GitHub services."""

import re

from .github_http import GitHubError
from .store import digest
from .workspaces import Workspaces

BOT = "chatgpt-codex-connector[bot]"
BOT_ID = 199175422
SUMMARY = "<!-- codex-pull-request-review-summary -->"
KINDS = {"code": ("Code Review", "@codex review"), "security": ("Security Review", "@codex security review")}


class CodexReviews:
    def __init__(self, github):
        self.github = github
        self.store = github.store

    @staticmethod
    def identity(context, revision, kind):
        return (context["repository"], context["pr"], revision, kind)

    def request(self, context, revision):
        if context["job"] is not None or not context["work_order"]:
            raise ValueError("Codex reviews belong to the integrated work-order PR.")
        key = digest([context["repository"], context["pr"]])
        with Workspaces(self.store, self.github.member).lock("codex-" + key):
            pr = self.github._pr(context)
            self.github._revision(pr, revision)
            if pr["state"] != "open" or pr.get("draft"):
                raise ValueError("Submit an open, ready work-order PR before requesting Codex reviews.")
            comments = self.github._all(context["root"] + f"/issues/{context['pr']}/comments")
            for kind, (_, command) in KINDS.items():
                identity = self.identity(context, revision, kind)
                marker = "<!-- agentscient-codex:" + digest(identity) + " -->"
                with self.store.connect() as db:
                    db.execute("INSERT OR IGNORE INTO codex_requests VALUES (?,?,?,?,'pending',NULL)", identity)
                    row = db.execute(
                        "SELECT * FROM codex_requests WHERE repository=? AND pr=? AND revision=? AND kind=?", identity
                    ).fetchone()
                if row["state"] == "requested":
                    continue
                existing = self._request_url(comments, marker, command)
                if existing:
                    url = existing
                elif row["state"] == "uncertain":
                    raise ValueError("Codex request outcome is uncertain. Inspect the PR before repeating the request.")
                else:
                    self.github._unchanged(context, pr)
                    with self.store.connect() as db:
                        db.execute(
                            "UPDATE codex_requests SET state='uncertain' WHERE repository=? AND pr=? AND revision=? AND kind=?",
                            identity,
                        )
                    try:
                        result = self.github.pr_comment(context, f"{command}\n\nWork-order revision: `{revision}`\n\n{marker}")
                    except GitHubError as error:
                        if error.status in {400, 401, 403, 404, 422, 429}:
                            with self.store.connect() as db:
                                db.execute(
                                    "UPDATE codex_requests SET state='pending' WHERE repository=? AND pr=? AND revision=? AND kind=?",
                                    identity,
                                )
                        raise
                    url = result["url"]
                with self.store.connect() as db:
                    db.execute(
                        "UPDATE codex_requests SET state='requested',url=? WHERE repository=? AND pr=? AND revision=? AND kind=?",
                        (url, *identity),
                    )
            self.github._unchanged(context, pr)
        return {"revision": revision, "state": "requested", "kinds": list(KINDS)}

    def _request_url(self, comments, marker, command):
        for row in comments:
            if marker not in (row.get("body") or "") or not row.get("node_id"):
                continue
            # GitHub checks authorship against the authenticated identity, including App credentials.
            comment = self.github.http.graphql(
                "query RequestComment($id:ID!) { node(id:$id) { ... on IssueComment { viewerDidAuthor body url } } }",
                id=row["node_id"],
            )["node"]
            if (comment and comment["viewerDidAuthor"] and marker in comment["body"]
                    and comment["body"].startswith(command + "\n")):
                return comment["url"]
        return None

    def status(self, context, revision, page=1):
        pr = self.github._pr(context)
        self.github._revision(pr, revision)
        comments = self.github._all(context["root"] + f"/issues/{context['pr']}/comments")
        summaries = [row for row in comments if SUMMARY in (row.get("body") or "")
                     and (row.get("user") or {}).get("id") == BOT_ID
                     and row["user"].get("login") == BOT and row["user"].get("type") == "Bot"]
        summary = max(summaries, key=lambda row: row["id"], default={})
        reviews = {kind: {"state": "unknown", "revision": None} for kind in KINDS}
        resolved, seen = {}, set()
        for line in summary.get("body", "").splitlines():
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if not line.startswith("|") or len(cells) != 4:
                continue
            kind = next((k for k, (label, _) in KINDS.items() if f"**{label}**" in cells[0]), None)
            if not kind:
                continue
            if kind in seen:
                reviews[kind] = {"state": "unknown", "revision": None}
                continue
            seen.add(kind)
            state = re.search(r"\*\*(Completed|Running|Queued|Failed|Cancelled|Canceled)\*\*", cells[1])
            commit = re.fullmatch(r"`([0-9a-f]{7,40})`", cells[2])
            if not state or not commit:
                continue
            short = commit[1]
            # Resolve GitHub's abbreviated display to a full SHA; prefix matching
            # alone can credit the wrong commit, and ambiguity must remain unknown.
            if short not in resolved:
                try:
                    resolved[short] = self.github.http.request("GET", context["root"] + "/commits/" + short)["sha"]
                except GitHubError as error:
                    if error.status not in {404, 422}:
                        raise
                    resolved[short] = None
            if resolved[short] is None:
                continue
            reviews[kind] = {"state": state[1].lower(), "revision": resolved[short]}
            if resolved[short] != revision:
                reviews[kind]["state"] = "stale"
        threads, cursor = [], None
        while True:
            result = self.github.pr_threads(context, cursor=cursor)
            threads.extend(result["threads"])
            cursor = result["next_cursor"]
            if not cursor:
                break
        unresolved = sum(not thread["isResolved"] for thread in threads)
        self.findings = digest([
            [row["id"], row["comments"]] for row in threads if not row["isResolved"]
        ])
        complete = all(row["state"] == "completed" and row["revision"] == revision for row in reviews.values())
        failed = any(row["state"] in {"failed", "cancelled", "canceled"} for row in reviews.values())
        start = (page - 1) * 10
        self.github._unchanged(context, pr)
        return {
            "revision": revision,
            "state": "findings" if unresolved else "ready" if complete else "needs_attention" if failed else "pending",
            "reviews": reviews, "unresolved": unresolved, "summary": summary.get("html_url"),
            "threads": [
                {"reference": "thread:" + row["id"], "resolved": row["isResolved"], "outdated": row["isOutdated"],
                 "path": row["path"], "line": row["line"], "comments": row["comments"]["nodes"],
                 "comment_count": row["comments"]["totalCount"]}
                for row in threads[start:start + 10]
            ],
            "next_page": page + 1 if start + 10 < len(threads) else None,
        }

    def require_ready(self, context, revision):
        result = self.status(context, revision)
        if result["state"] != "ready":
            raise ValueError("Both Codex reviews must complete for the current commit and review threads must be resolved. "
                             "Use monitor(section=\"feedback\") to inspect and review_respond to address findings.")
        return result
