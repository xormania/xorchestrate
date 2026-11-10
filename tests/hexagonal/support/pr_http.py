"""Loopback GitHub boundary with inspectable requests and separately built CI evidence."""

import hashlib
import json
import threading
from contextlib import contextmanager
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

from scripts.ci.candidate import capture_context
from tests.hexagonal.support.ci_candidate import artifact_zip, candidate_repository, github_observation
from tests.hexagonal.support.ci_candidate import git as git_command


class PullRequestAPI:
    def __init__(self, root):
        git = candidate_repository(root)
        event, env = github_observation(git)
        self.context = capture_context(git["root"], event, env)
        self.repository = event["repository"]["full_name"]
        owner = {"full_name": self.repository, "id": event["repository"]["id"]}
        self.pr = {"number": 17, "draft": True, "state": "open", "mergeable": True,
                   "head": {"sha": git["source"], "ref": "feature", "repo": owner},
                   "base": {"sha": git["tested_base"], "ref": "master", "repo": owner},
                   "merge_commit_sha": git["tested_commit"], "html_url": "https://github.com/fixture/pull/17"}
        self.run = {"id": int(env["GITHUB_RUN_ID"]), "run_attempt": 1, "event": "pull_request",
                    "head_sha": git["source"], "path": ".github/workflows/ci.yml",
                    "status": "completed", "conclusion": "success", "check_suite_id": 901,
                    "pull_requests": [{"number": 17}]}
        self.runs = [deepcopy(self.run)]
        self.checks = [{"id": index, "name": name, "head_sha": git["source"],
                        "status": "completed", "conclusion": "success", "check_suite": {"id": 901},
                        "app": {"slug": "github-actions"}}
                       for index, name in enumerate(("CI result", "Hexagonal architecture"), 100)]
        self.commit = {"sha": git["tested_commit"], "tree": {"sha": git["tree"]},
                       "parents": [{"sha": value} for value in git["parents"]]}
        self.references = {
            "refs/heads/feature": {"sha": git["source"], "commit": {"tree": {
                "sha": git_command(git["root"], "rev-parse", git["source"] + "^{tree}")}}},
            "refs/heads/master": {"sha": git["tested_base"], "commit": {"tree": {
                "sha": git_command(git["root"], "rev-parse", git["tested_base"] + "^{tree}")}}},
        }
        self.threads = [{"id": "owner-thread", "isResolved": False}]
        self.result = {"schema_version": 2, "status": "passed", "candidate_context": self.context}
        self.requests = []
        self.extra = None

    def archive(self):
        return artifact_zip([("result.json", json.dumps(self.result), "file")])

    def response(self, method, path, body):
        parsed = urlsplit(path)
        query = parse_qs(parsed.query)
        root = f"/repos/{self.repository}"
        if self.extra is not None:
            override = self.extra(method, parsed.path, query, body)
            if override is not None:
                return override
        if parsed.path == root + "/pulls/17":
            return 200, self.pr
        if parsed.path == root + "/actions/runs":
            return 200, {"workflow_runs": self.runs}
        if parsed.path.startswith(root + "/actions/runs/") and "/attempts/" in parsed.path:
            parts = parsed.path.split("/")
            identifier, attempt = int(parts[-3]), int(parts[-1])
            row = next((value for value in self.runs if value["id"] == identifier
                        and value["run_attempt"] == attempt), None)
            if row is not None:
                return 200, {**row, "run_started_at": row.get("run_started_at", f"2026-11-10T12:00:{attempt:02}Z")}
        if parsed.path == root + f"/commits/{self.pr['head']['sha']}/check-runs":
            return 200, {"check_runs": self.checks}
        if parsed.path.startswith(root + "/commits/"):
            reference = unquote(parsed.path.removeprefix(root + "/commits/"))
            if reference in self.references:
                return 200, self.references[reference]
        if parsed.path == root + "/pulls/17/reviews":
            return 200, []
        if parsed.path == "/graphql":
            return 200, {"data": {"repository": {"pullRequest": {"reviewThreads": {
                "nodes": self.threads, "pageInfo": {"hasNextPage": False, "endCursor": None}}}}}}
        if parsed.path == root + f"/actions/runs/{self.run['id']}/artifacts":
            archive = self.archive()
            return 200, {"artifacts": [{"id": 501, "name": f"ci-result-evidence--attempt-{self.run['run_attempt']}", "expired": False,
                                       "size_in_bytes": len(archive),
                                       "digest": "sha256:" + hashlib.sha256(archive).hexdigest()}]}
        if parsed.path == root + "/actions/artifacts/501/zip":
            return 200, self.archive()
        if parsed.path == root + f"/git/commits/{self.commit['sha']}":
            return 200, self.commit
        return 404, {"message": "No fixture response"}


@contextmanager
def pull_request_api(fixture):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - standard server API
            self.respond()

        def do_POST(self):  # noqa: N802 - standard server API
            self.respond()

        def respond(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            body = json.loads(raw) if raw else None
            fixture.requests.append({"method": self.command, "path": self.path, "body": body,
                                     "authorization": self.headers.get("Authorization")})
            if self.headers.get("Authorization") != "Bearer inert-pr-test-token":
                status, payload = 401, {"message": "fixture authentication required"}
            else:
                status, payload = fixture.response(self.command, self.path, body)
            data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *arguments):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
