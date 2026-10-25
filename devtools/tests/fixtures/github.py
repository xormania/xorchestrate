"""Shared github arrangements without test methods."""

import copy
import hashlib
import io
import json
import tempfile
import threading
import urllib.parse
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from support import Client, initialize

from devtools.github_http import GitHubHTTP
from devtools.store import Store

HEAD, MERGE = "a" * 40, "b" * 40


class Provider:
    """HTTP fixture for the documented REST and GraphQL shapes; not a live GitHub acceptance run."""


    def __init__(self):
        self.requests, self.comments, self.reviews = [], [], []
        self.actor = {"id": 9, "login": "operator"}
        self.inline = []
        self.exists = False
        self.logs_missing = False
        self.review_decision = "REVIEW_REQUIRED"
        self.merge_state = "BLOCKED"
        self.pr = {
            "number": 7,
            "node_id": "PR_7",
            "html_url": "https://github.com/example/project/pull/7",
            "title": "Test PR",
            "body": "Description",
            "head": {"ref": "feature", "sha": HEAD},
            "base": {"ref": "dev"},
            "state": "open",
            "draft": True,
            "mergeable": True,
            "mergeable_state": "clean",
            "merge_commit_sha": MERGE,
            "merged": False,
            "changed_files": 1,
        }
        self.run = {
            "id": 10,
            "html_url": "https://github.com/example/project/actions/runs/10",
            "name": "CI",
            "head_sha": HEAD,
            "head_branch": "feature",
            "event": "pull_request",
            "run_attempt": 1,
            "status": "completed",
            "conclusion": "failure",
            "run_started_at": "2026-09-26T10:00:00Z",
        }
        self.checks = {HEAD: [self.check("success")], MERGE: [self.check("failure")]}
        self.tasks = [
            {
                "id": 101,
                "name": "storage",
                "status": "completed",
                "conclusion": "failure",
                "check_run_url": "https://api.github.com/repos/example/project/check-runs/201",
                "html_url": self.run["html_url"] + "/job/101",
                "steps": [{"name": "Run tests", "number": 3, "status": "completed", "conclusion": "failure"}],
            }
        ]
        self.files = [
            {
                "filename": "devtools/example.py",
                "status": "modified",
                "additions": 1,
                "deletions": 1,
                "changes": 2,
                "patch": "@@ -1 +1 @@\n-old\n+new",
            }
        ]
        self.thread = {
            "id": "THREAD_1",
            "isResolved": False,
            "isOutdated": False,
            "path": "devtools/example.py",
            "line": 1,
            "pullRequest": {"number": 7, "repository": {"nameWithOwner": "example/project"}},
            "comments": {
                "nodes": [
                    {
                        "databaseId": 12,
                        "body": "Please fix this",
                        "url": self.pr["html_url"] + "#discussion_r12",
                        "author": {"login": "reviewer"},
                    }
                ],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            },
        }
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr("test-results.xml", "<testsuite tests='12' failures='0'/>")
            zipped.writestr("../evidence.txt", "Names in ZIP files must never become write paths")
        self.archive = archive.getvalue()
        self.artifact = {
            "id": 90,
            "name": "test-results",
            "size_in_bytes": len(self.archive),
            "expired": False,
            "created_at": "2026-09-26T10:01:00Z",
            "expires_at": "2026-12-25T10:01:00Z",
            "digest": "sha256:" + hashlib.sha256(self.archive).hexdigest(),
            "workflow_run": {"id": 10, "head_sha": HEAD},
        }


    @staticmethod

    def check(conclusion):
        return {
            "id": 201,
            "name": "storage",
            "status": "completed",
            "conclusion": conclusion,
            "details_url": "https://github.com/example/project/actions/runs/10",
        }


    @staticmethod

    def page(rows, query):
        size, page = int(query.get("per_page", [100])[0]), int(query.get("page", [1])[0])
        return rows[(page - 1) * size : page * size]


    def reply(self, method, target, body, headers):
        self.requests.append((method, target, copy.deepcopy(body), headers.get("Authorization")))
        parsed = urllib.parse.urlsplit(target)
        path, query = parsed.path, urllib.parse.parse_qs(parsed.query)
        root = "/repos/example/project"
        if path.startswith("/downloads/"):
            self.assert_download_auth = headers.get("Authorization")
            return (
                200,
                self.archive
                if path.endswith("archive")
                else b"setup\nERROR: assertion failed\nBearer fixture-credential\nfixture-credential\n",
                {},
            )
        if path == "/graphql":
            q, v = body["query"], body["variables"]
            if "query RequestComment" in q:
                comment = next((row for row in self.comments if row.get("node_id") == v["id"]), None)
                node = None if comment is None else {
                    "viewerDidAuthor": (comment.get("user") or {}).get("id") == self.actor["id"],
                    "body": comment["body"], "url": comment["html_url"],
                }
                return 200, {"data": {"node": node}}, {}
            if "query Overview" in q:
                return (
                    200,
                    {
                        "data": {
                            "repository": {
                                "pullRequest": {
                                    "reviewDecision": self.review_decision,
                                    "mergeStateStatus": self.merge_state,
                                    "reviews": {"nodes": []},
                                }
                            }
                        }
                    },
                    {},
                )
            if "query Threads" in q:
                row = copy.deepcopy(self.thread)
                comments = row["comments"]["nodes"]
                row["comments"] = {"nodes": comments[:1]}
                if "latest: comments(last:1)" in q:
                    row["latest"] = {"totalCount": len(comments), "nodes": comments[-1:]}
                return (
                    200,
                    {
                        "data": {
                            "repository": {
                                "pullRequest": {
                                    "reviewThreads": {
                                        "nodes": [row],
                                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                                    }
                                }
                            }
                        }
                    },
                    {},
                )
            if "query Thread" in q:
                row = copy.deepcopy(self.thread)
                for comment in row["comments"]["nodes"]:
                    comment["viewerDidAuthor"] = comment["author"]["login"] == self.actor["login"]
                if v["id"] == "WRONG_THREAD":
                    row["pullRequest"]["number"] = 999
                return 200, {"data": {"node": row}}, {}
            for mutation in ("convertPullRequestToDraft", "markPullRequestReadyForReview"):
                if mutation in q:
                    self.pr["draft"] = mutation == "convertPullRequestToDraft"
                    return 200, {"data": {mutation: {"pullRequest": {"id": "PR_7"}}}}, {}
            if "addPullRequestReviewThreadReply" in q:
                self.thread["comments"]["nodes"].append({
                    "databaseId": 13, "body": v["body"], "url": self.pr["html_url"] + "#discussion_r13",
                    "author": {"login": "operator"},
                })
                return (
                    200,
                    {
                        "data": {
                            "addPullRequestReviewThreadReply": {
                                "comment": {"id": "C13", "url": self.pr["html_url"] + "#discussion_r13"}
                            }
                        }
                    },
                    {},
                )
            for mutation in ("unresolveReviewThread", "resolveReviewThread"):
                if mutation in q:
                    self.thread["isResolved"] = mutation == "resolveReviewThread"
                    return (
                        200,
                        {"data": {mutation: {"thread": {"id": "THREAD_1", "isResolved": self.thread["isResolved"]}}}},
                        {},
                    )
        if path == root:
            return 200, {"allow_merge_commit": True, "allow_squash_merge": False, "allow_rebase_merge": False}, {}
        if path == root + "/pulls":
            if method == "POST":
                self.exists = True
                self.pr.update({key: body[key] for key in ("title", "body", "draft")})
                return 201, self.pr, {}
            return 200, [self.pr] if self.exists else [], {}
        if path == root + "/pulls/7":
            if method == "PATCH":
                self.pr.update(body)
            return 200, self.pr, {}
        if path == root + "/pulls/7/files":
            return 200, self.page(self.files, query), {}
        if path == root + "/pulls/7/reviews":
            if method == "POST":
                result = {
                    "id": 20 + len(self.reviews),
                    "html_url": self.pr["html_url"] + "#review",
                    "state": body["event"],
                    **body,
                }
                self.reviews.append(result)
                return 200, result, {}
            return 200, self.page(self.reviews, query), {}
        if path == root + "/pulls/7/comments":
            return 200, self.page(self.inline, query), {}
        if path == root + "/issues/7/comments":
            if method == "POST":
                result = {
                    "id": 40 + len(self.comments),
                    "node_id": f"IC_{40 + len(self.comments)}",
                    "user": dict(self.actor),
                    "html_url": self.pr["html_url"] + "#comment",
                    "issue_url": self.url + root + "/issues/7",
                    **body,
                }
                self.comments.append(result)
                return 201, result, {}
            return 200, self.page(self.comments, query), {}
        if path.startswith(root + "/issues/comments/"):
            return 200, next(row for row in self.comments if row["id"] == int(path.rsplit("/", 1)[1])), {}
        if path == root + "/pulls/7/merge":
            if body["sha"] != self.pr["head"]["sha"]:
                return 409, {"message": "Head changed"}, {}
            self.pr.update(merged=True, state="closed")
            return 200, {"merged": True, "sha": MERGE, "message": "Merged"}, {}
        if path.startswith(root + "/commits/"):
            if len(path.removeprefix(root + "/commits/").split("/")) == 1:
                short = path.rsplit("/", 1)[1]
                matches = {sha for sha in (HEAD, MERGE, self.pr["head"]["sha"]) if sha.startswith(short)}
                return (200, {"sha": matches.pop()}, {}) if len(matches) == 1 else (422, {"message": "Ambiguous commit"}, {})
            revision = path.split("/")[-2]
            if path.endswith("/check-runs"):
                rows = self.checks.get(revision, [])
                return 200, {"total_count": len(rows), "check_runs": self.page(rows, query)}, {}
            return 200, {"state": "pending", "total_count": 0, "statuses": []}, {}
        if path == root + "/actions/runs":
            rows = [self.run] if query.get("head_sha", [None])[0] in (None, self.run["head_sha"]) else []
            return 200, {"workflow_runs": self.page(rows, query), "total_count": len(rows)}, {}
        if path == root + "/actions/runs/10":
            return 200, self.run, {}
        if path.startswith(root + "/actions/runs/10/attempts/"):
            if path.endswith("/jobs"):
                return 200, {"jobs": self.page(self.tasks, query), "total_count": len(self.tasks)}, {}
            return 200, {**self.run, "run_attempt": 1, "run_started_at": "2026-09-26T10:00:00Z"}, {}
        if path in (root + "/actions/jobs/101/logs", root + "/actions/artifacts/90/zip"):
            if path.endswith("logs") and self.logs_missing:
                return 410, {"message": "Logs expired"}, {}
            return (
                302,
                b"",
                {"Location": self.url + ("/downloads/log" if path.endswith("logs") else "/downloads/archive")},
            )
        if path == root + "/check-runs/201/annotations":
            return (
                200,
                [
                    {
                        "path": "test_storage.py",
                        "start_line": 12,
                        "message": "assertion failed",
                        "annotation_level": "failure",
                    }
                ],
                {},
            )
        if path == root + "/actions/workflows":
            return (
                200,
                {
                    "total_count": 1,
                    "workflows": [{"id": 2, "name": "CI", "path": ".github/workflows/ci.yml", "state": "active"}],
                },
                {},
            )
        if path == root + "/actions/workflows/ci.yml/dispatches":
            return 204, b"", {}
        if path.endswith(("/rerun", "/rerun-failed-jobs")):
            self.run.update(run_attempt=self.run["run_attempt"] + 1, status="queued", conclusion=None)
            return 201, b"", {}
        if path == root + "/actions/runs/10/cancel":
            self.run.update(status="completed", conclusion="cancelled")
            return 202, b"", {}
        if path == root + "/actions/runs/10/artifacts":
            return 200, {"total_count": 1, "artifacts": [self.artifact]}, {}
        if path == root + "/actions/artifacts/90":
            return 200, self.artifact, {}
        return 404, {"message": "Fixture route missing: " + path}, {}


class GitHubFixture:

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.directory = self.root / "state"
        initialize(self.directory, self.workspace, ["alpha:external", "beta:external"])
        self.store = Store(self.directory)
        config = json.loads((self.directory / "team.json").read_text())
        config["github"] = {"repository": "example/project", "base": "dev"}
        (self.directory / "team.json").write_text(json.dumps(config))
        self.provider = Provider()
        provider = self.provider

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                data = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                body = json.loads(data) if data else None
                status, value, headers = provider.reply(self.command, self.path, body, self.headers)
                encoded = value if isinstance(value, bytes) else json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(encoded)))
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(encoded)

            do_POST = do_PATCH = do_PUT = do_GET

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        # shutdown() waits for the server's next poll; its 0.5s default adds
        # idle teardown time to every HTTP fixture without testing anything.
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        self.provider.url = "http://127.0.0.1:" + str(self.server.server_port)
        self.http = GitHubHTTP(api_root=self.provider.url, credential=lambda: "fixture-credential")
        from devtools import watcher

        self.addCleanup(watcher.stop, self.store)
        # Direct provider tests drive ticks themselves. MCP process tests use the
        # real daemon, with only its HTTP transport supplied by github_fixture.
        mocked_start = patch("devtools.watcher.start", return_value={"running": False})
        mocked_start.start()
        self.addCleanup(mocked_start.stop)

    def client(self, member="alpha"):
        env = {"FIXTURE_GITHUB_ROOT": self.provider.url}
        with patch("support.ENTRY", (Path(__file__).resolve().parent.parent / "github_fixture.py")):
            client = Client(self.directory, member, env=env, operator=True)
        self.addCleanup(client.close)
        return client

    def work(self, client):
        order = client.invoke(
            "work_order_create", title="GitHub support", objective="Agent tasks through MCP", done_when="CI passes"
        )
        job = client.invoke(
            "job_create",
            title="Add GitHub tools",
            objective="Help agents finish PR work",
            done_when="Reviewed PR",
            repository="example/project",
            branch="feature",
        )
        return order["work_order"]["id"], job["job"]["id"]
