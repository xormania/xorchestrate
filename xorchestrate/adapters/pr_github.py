"""Bounded GitHub PR observations and draft creation over the configured API.

Repository/check state is read anew. Retained CI JSON is qualified against its
producer run and immutable Git object; it is never executed or extracted.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import stat
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone

from ..model import OrchestrationError, unique_keys
from .github import _OPENER, GitHubHosting


class GitHubPRHost:
    def __init__(self, options=None):
        options = options or {}
        allowed = {"api_url", "host", "token_variables", "token_command", "timeout", "required_checks", "max_pages"}
        if not isinstance(options, dict) or options.keys() - allowed:
            raise OrchestrationError("Invalid PR hosting options.", "invalid_configuration")
        self.connection = GitHubHosting({key: value for key, value in options.items()
            if key not in {"required_checks", "max_pages"}})
        self.required_checks = options.get("required_checks", ["CI result", "Hexagonal architecture"])
        self.max_pages = options.get("max_pages", 20)
        if (not isinstance(self.required_checks, list) or not self.required_checks
                or not all(isinstance(value, str) and value for value in self.required_checks)
                or type(self.max_pages) is not int or not 1 <= self.max_pages <= 100):
            raise OrchestrationError("Invalid PR check or pagination options.", "invalid_configuration")

    def _request(self, method, path, body=None):
        token = self.connection._token()
        if not token and method != "GET":
            raise OrchestrationError("PR hosting authentication is unavailable.", "pr_authentication", {"outcome": "not_started"})
        data = None if body is None else json.dumps(body, allow_nan=False).encode()
        request = urllib.request.Request(self.connection.api_url + path, data=data, method=method,
            headers={"Accept": "application/vnd.github+json", **({"Authorization": "Bearer " + token} if token else {}),
                "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "xorchestrate-pr",
                "Content-Type": "application/json"})
        try:
            with _OPENER.open(request, timeout=self.connection.timeout) as response:
                raw = response.read(4_194_305)
                if len(raw) > 4_194_304:
                    raise ValueError("bounded response")
                return json.loads(raw, object_pairs_hook=unique_keys("Duplicate hosting key"))
        except urllib.error.HTTPError as error:
            outcome = "failed" if 400 <= error.code < 500 and error.code not in {408, 429} else "unknown"
            raise OrchestrationError("GitHub rejected or could not confirm the requested operation.",
                "pr_hosting_rejected", {"http_status": error.code, "outcome": "not_started" if method == "GET" else outcome}) from error
        except (urllib.error.URLError, OSError, ValueError, TimeoutError) as error:
            raise OrchestrationError("GitHub did not return a verified response.", "pr_hosting_unknown",
                {"outcome": "not_started" if method == "GET" else "unknown"}) from error

    def _all(self, path, field=None):
        result = []
        separator = "&" if "?" in path else "?"
        for page in range(1, self.max_pages + 1):
            payload = self._request("GET", path + separator + f"per_page=100&page={page}")
            rows = payload.get(field) if field and isinstance(payload, dict) else payload
            if not isinstance(rows, list):
                raise OrchestrationError("GitHub returned incomplete collection data.", "pr_incomplete")
            result.extend(rows)
            if len(rows) < 100:
                return result
        raise OrchestrationError("The observation exceeded its configured page budget.", "pr_incomplete")

    @staticmethod
    def _short(pr):
        return {"repository": pr["base"]["repo"]["full_name"], "number": pr["number"],
            "head": pr["head"]["sha"], "base": pr["base"]["sha"], "merge": pr.get("merge_commit_sha"),
            "head_ref": pr["head"]["ref"], "base_ref": pr["base"]["ref"],
            "source_repository": (pr["head"].get("repo") or {}).get("full_name"),
            "draft": pr["draft"], "state": pr["state"], "mergeable": pr.get("mergeable"),
            "url": pr["html_url"]}

    def branch(self, repository, ref):
        try:
            commit = self._request("GET", f"/repos/{repository}/commits/" + urllib.parse.quote(ref, safe=""))
        except OrchestrationError as error:
            if error.details.get("http_status") == 404:
                return None
            raise
        return {"sha": commit["sha"], "tree": commit["commit"]["tree"]["sha"]}

    def find(self, repository, branch):
        query = urllib.parse.urlencode({"state": "all", "head": repository.split("/")[0] + ":" + branch})
        return [self._short(pr) for pr in self._all(f"/repos/{repository}/pulls?{query}")]

    def create(self, repository, branch, base, title, body):
        pr = self._request("POST", f"/repos/{repository}/pulls", {
            "head": branch, "base": base, "title": title, "body": body, "draft": True})
        return self._short(pr)

    def _threads(self, repository, number):
        owner, name = repository.split("/")
        rows, cursor = [], None
        query = """query PRThreads($owner:String!,$name:String!,$number:Int!,$after:String) {
          repository(owner:$owner,name:$name) { pullRequest(number:$number) {
            reviewThreads(first:100,after:$after) { nodes { id isResolved }
              pageInfo { hasNextPage endCursor } } } } }"""
        for _ in range(self.max_pages):
            data = self._request("POST", "/graphql", {"query": query,
                "variables": {"owner": owner, "name": name, "number": number, "after": cursor}})
            if data.get("errors"):
                raise OrchestrationError("Review-thread observation is incomplete.", "pr_incomplete")
            page = data["data"]["repository"]["pullRequest"]["reviewThreads"]
            rows.extend({"id": row["id"], "resolved": row["isResolved"]} for row in page["nodes"])
            if page["pageInfo"]["hasNextPage"] is False:
                return rows
            cursor = page["pageInfo"]["endCursor"]
        raise OrchestrationError("Review threads exceeded the configured page budget.", "pr_incomplete")

    def _artifact_result(self, repository, run):
        artifacts = self._all(f"/repos/{repository}/actions/runs/{run['id']}/artifacts", "artifacts")
        expected = "ci-result-evidence--attempt-" + str(run["run_attempt"])
        selected = [row for row in artifacts if row.get("name") == expected and row.get("expired") is False]
        if len(selected) != 1:
            raise ValueError("missing candidate evidence")
        artifact = selected[0]
        if artifact.get("size_in_bytes", 0) > 16_777_216:
            raise ValueError("oversized evidence")
        token = self.connection._token()
        request = urllib.request.Request(self.connection.api_url + f"/repos/{repository}/actions/artifacts/{artifact['id']}/zip",
            headers={**({"Authorization": "Bearer " + token} if token else {}),
                "Accept": "application/vnd.github+json", "User-Agent": "xorchestrate-pr"})
        try:
            response = _OPENER.open(request, timeout=self.connection.timeout)
        except urllib.error.HTTPError as error:
            if error.code not in {301, 302, 303, 307, 308}:
                raise
            location = error.headers.get("Location", "")
            parsed = urllib.parse.urlsplit(location)
            if parsed.scheme != "https" or parsed.username or parsed.password or not parsed.hostname:
                raise ValueError("invalid artifact location") from error
            # The API supplied this signed location. Credentials are deliberately absent.
            response = _OPENER.open(urllib.request.Request(location), timeout=self.connection.timeout)
        with response:
            archive = response.read(16_777_217)
        if len(archive) > 16_777_216:
            raise ValueError("oversized archive")
        digest = artifact.get("digest")
        if digest != "sha256:" + hashlib.sha256(archive).hexdigest():
            raise ValueError("artifact digest mismatch")
        with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
            entries = zipped.infolist()
            if len(entries) > 100 or sum(row.file_size for row in entries) > 16_777_216:
                raise ValueError("oversized artifact contents")
            names = [row.filename for row in entries]
            if len(names) != len(set(names)):
                raise ValueError("duplicate archive member")
            for row in entries:
                if (row.filename.startswith("/") or ".." in row.filename.split("/")
                        or "\\" in row.filename or stat.S_ISLNK(row.external_attr >> 16)):
                    raise ValueError("unsafe archive member")
            result_members = [row for row in entries if row.filename.split("/")[-1] == "result.json"]
            if len(result_members) != 1 or result_members[0].file_size > 4_194_304:
                raise ValueError("invalid result member")
            return json.loads(zipped.read(result_members[0]), object_pairs_hook=unique_keys("Duplicate evidence key")), artifact

    def _provenance(self, repository, pr, run):
        try:
            result, artifact = self._artifact_result(repository, run)
            context = result["candidate_context"]
            producer, checkout = context["producer"], context["checkout"]
            obj = self._request("GET", f"/repos/{repository}/git/commits/{checkout['commit']}")
            expected = (result.get("schema_version") == 2 and result.get("status") == "passed"
                and context.get("schema_version") == 1
                and context["repository"]["full_name"] == repository
                and context["source_repository"]["full_name"] == pr["source_repository"]
                and context["pr"]["number"] == pr["number"]
                and context["source_sha"] == pr["head"]
                and checkout["commit"] == pr["merge"]
                and context["tested_base_sha"] == pr["base"]
                and context["pr"]["source_ref"] == pr["head_ref"]
                and context["pr"]["target_ref"] == pr["base_ref"]
                and checkout["status"] == "" and checkout["kind"] == "pr_merge"
                and checkout["parents"] == [pr["base"], pr["head"]]
                and checkout["parents"] == [parent["sha"] for parent in obj["parents"]]
                and checkout["tree"] == obj["tree"]["sha"]
                and str(producer["run_id"]) == str(run["id"])
                and type(producer["run_attempt"]) is int and 1 <= producer["run_attempt"] <= run["run_attempt"]
                and isinstance(producer.get("workflow_ref"), str)
                and producer["workflow_ref"].startswith(repository + "/.github/workflows/ci.yml@")
                and isinstance(producer.get("workflow_sha"), str)
                and re.fullmatch(r"[0-9a-f]{40}", producer["workflow_sha"]) is not None
                and producer["check_sha"] == pr["head"]
                and context["event"]["name"] == "pull_request")
            if not expected:
                return {"status": "invalid", "reason": "candidate_producer_mismatch"}
            return {"status": "qualified", "run_id": run["id"], "run_attempt": run["run_attempt"],
                "artifact_id": artifact["id"], "artifact_digest": artifact.get("digest"),
                "context": context}
        except (KeyError, TypeError, ValueError, OSError, urllib.error.URLError, zipfile.BadZipFile, OrchestrationError):
            return {"status": "incomplete", "reason": "candidate_evidence_unavailable"}

    def _latest_attempt(self, repository, runs):
        """An old run can have a newly started retry; creation IDs do not order it."""
        ordered = []
        for run in runs:
            attempt = self._request("GET", f"/repos/{repository}/actions/runs/{run['id']}/attempts/{run['run_attempt']}")
            started = attempt.get("run_started_at")
            try:
                instant = datetime.fromisoformat(started.replace("Z", "+00:00"))
            except (AttributeError, TypeError, ValueError) as error:
                raise OrchestrationError("Latest attempt start is unavailable; current ordering is incomplete.", "pr_incomplete") from error
            if (instant.tzinfo is None or attempt.get("id") != run["id"]
                    or attempt.get("run_attempt") != run["run_attempt"]
                    or attempt.get("head_sha") != run["head_sha"]):
                raise OrchestrationError("Latest attempt identity is incomplete.", "pr_incomplete")
            latest = {**run, **{key: attempt[key] for key in ("status", "conclusion") if key in attempt},
                "attempt_started_at": started}
            ordered.append((instant, latest))
        if not ordered:
            return None
        ordered.sort(key=lambda item: item[0], reverse=True)
        if len(ordered) > 1 and ordered[0][0] == ordered[1][0]:
            raise OrchestrationError("Latest relevant attempt ordering is ambiguous.", "pr_incomplete")
        return ordered[0][1]

    def snapshot(self, repository, number):
        root = f"/repos/{repository}"
        pr = self._short(self._request("GET", root + f"/pulls/{number}"))
        runs = self._all(root + "/actions/runs?" + urllib.parse.urlencode({"head_sha": pr["head"]}), "workflow_runs")
        relevant = [row for row in runs if row.get("event") == "pull_request"
            and row.get("path") == ".github/workflows/ci.yml" and row.get("head_sha") == pr["head"]
            and any(item.get("number") == number for item in row.get("pull_requests", []))]
        run = self._latest_attempt(repository, relevant)
        checks = self._all(root + f"/commits/{pr['head']}/check-runs", "check_runs")
        selected = [row for row in checks if run and row.get("check_suite", {}).get("id") == run["check_suite_id"]
            and row.get("head_sha") == pr["head"]
            and row.get("app", {}).get("slug") == "github-actions"]
        latest = {}
        for row in sorted(selected, key=lambda item: item["id"]):
            latest[row["name"]] = row
        validation = "unknown"
        if run:
            required = [latest.get(name) for name in self.required_checks]
            validation = ("pending" if any(row is None or row.get("status") != "completed" for row in required)
                else "passed" if run.get("conclusion") == "success" and all(row.get("conclusion") == "success" for row in required)
                else "failed")
        reviews = self._all(root + f"/pulls/{number}/reviews")
        pr.update({"checks": [{"id": row["id"], "name": row["name"], "sha": row["head_sha"],
            "status": row["status"], "conclusion": row.get("conclusion"),
            "run_id": run["id"] if run else None, "run_attempt": run["run_attempt"] if run else None,
            "event": run["event"] if run else None} for row in latest.values()],
            "reviews": [{"id": row["id"], "state": row["state"], "commit": row.get("commit_id")} for row in reviews],
            "threads": self._threads(repository, number), "complete": True, "validation": validation,
            "provenance": self._provenance(repository, pr, run) if run and validation == "passed" else {"status": "incomplete"},
            "run": {key: run.get(key) for key in ("id", "run_attempt", "event", "status", "conclusion", "attempt_started_at")} if run else None,
            "observed_at": datetime.now(timezone.utc).isoformat()})
        return pr
