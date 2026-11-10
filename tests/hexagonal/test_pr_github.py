"""GitHub PR contracts exercise real loopback HTTP and bounded signed-archive reads."""

import hashlib
import io
import json
import urllib.error
import warnings
import zipfile
from copy import deepcopy
from unittest.mock import patch

import pytest

from tests.hexagonal.support.ci_candidate import artifact_zip
from tests.hexagonal.support.pr_http import PullRequestAPI, pull_request_api
from xorchestrate.adapters.pr_github import GitHubPRHost
from xorchestrate.composition import build
from xorchestrate.model import OrchestrationError


@pytest.fixture
def remote(tmp_path, monkeypatch):
    monkeypatch.setenv("PR_TEST_TOKEN", "inert-pr-test-token")
    return PullRequestAPI(tmp_path / "candidate")


def host(api, **options):
    return GitHubPRHost({"api_url": api, "token_variables": ["PR_TEST_TOKEN"], "token_command": [], **options})


def test_http_observation_joins_source_checks_to_proven_tested_merge_and_reviews(remote):
    with pull_request_api(remote) as api:
        result = host(api).snapshot(remote.repository, 17)
    assert result["complete"] is True and result["validation"] == "passed"
    assert result["provenance"]["status"] == "qualified"
    assert {item["sha"] for item in result["checks"]} == {remote.pr["head"]["sha"]}
    assert result["provenance"]["context"]["checkout"]["commit"] == remote.pr["merge_commit_sha"]
    assert result["threads"] == [{"id": "owner-thread", "resolved": False}]
    assert all(request["authorization"] == "Bearer inert-pr-test-token" for request in remote.requests)


def test_configured_pr_provider_is_active_through_project_application_composition(remote, project):
    with pull_request_api(remote) as api:
        config = {"version": 1, "providers": {"delivery": {"adapter": "pr", "options": {
            "github": {"api_url": api, "token_variables": ["PR_TEST_TOKEN"], "token_command": []},
            "git": {"workspaces": {"owned": {"path": str(project.repo), "repository": remote.repository}}},
        }}}}
        with build(config, project=project.context) as runtime:
            app = runtime.application
            discovered = app.call("capabilities", {"provider": "delivery"})
            assert "delivery/pr.inspect" in {row["name"] for row in discovered["capabilities"]}
            reply = app.call("invoke", {"capability": "delivery/pr.inspect", "arguments": {
                "repository": remote.repository, "number": 17}, "idempotency_key": "configured-observation"})
    result = reply["invocation"]["result"]
    assert result["status"] == "observed"
    assert result["technical_validation"] == "passed"
    assert "unresolved_review_threads" in result["blockers"]


def test_http_creation_explicitly_opens_a_draft_with_literal_supplied_metadata(remote):
    def creation(method, path, query, body):
        if method == "POST" and path == f"/repos/{remote.repository}/pulls":
            return 201, remote.pr
        return None
    remote.extra = creation
    title, body = "Prepare CI lifecycle", "Owner-visible evidence and $(literal text)"
    with pull_request_api(remote) as api:
        result = host(api).create(remote.repository, "feature", "master", title, body)
    writes = [request for request in remote.requests if request["method"] == "POST"]
    assert len(writes) == 1
    assert writes[0]["body"] == {"head": "feature", "base": "master", "title": title, "body": body, "draft": True}
    assert result["draft"] is True and result["head"] == remote.pr["head"]["sha"]


def test_latest_failed_attempt_cannot_disappear_behind_an_older_success(remote):
    newer = {**remote.run, "run_attempt": 2, "conclusion": "failure"}
    remote.runs = [deepcopy(remote.run), newer]
    for row in remote.checks:
        row["conclusion"] = "failure"
    with pull_request_api(remote) as api:
        result = host(api).snapshot(remote.repository, 17)
    assert result["run"]["run_attempt"] == 2
    assert result["validation"] == "failed"
    assert result["provenance"]["status"] != "qualified"


@pytest.mark.parametrize("older_rerun_starts_later", [True, False])
def test_authoritative_attempt_start_orders_reruns_across_different_run_ids(remote, older_rerun_starts_later):
    # Run A was created first, but its retry can start after run B's original attempt.
    older = {**remote.run, "id": remote.run["id"] - 1, "run_attempt": 2, "check_suite_id": 900,
             "conclusion": "failure", "run_started_at": "2026-11-10T12:30:00Z" if older_rerun_starts_later else "2026-11-10T11:00:00Z",
             "updated_at": "2026-11-10T13:00:00Z"}
    newer = {**remote.run, "run_started_at": "2026-11-10T12:00:00Z", "updated_at": "2026-11-10T12:01:00Z"}
    remote.runs = [older, newer]
    remote.checks.extend({**row, "id": row["id"] + 100, "conclusion": "failure", "check_suite": {"id": 900}}
                         for row in list(remote.checks))
    with pull_request_api(remote) as api:
        result = host(api).snapshot(remote.repository, 17)
    chosen = older if older_rerun_starts_later else newer
    assert result["run"]["id"] == chosen["id"]
    assert result["run"]["run_attempt"] == chosen["run_attempt"]
    assert result["validation"] == ("failed" if older_rerun_starts_later else "passed")
    assert result["provenance"]["status"] == ("incomplete" if older_rerun_starts_later else "qualified")


def test_missing_authoritative_attempt_start_cannot_certify_latest_success(remote):
    def missing_started_at(method, path, query, body):
        if "/attempts/" in path:
            return 200, deepcopy(remote.run)
        return None
    remote.extra = missing_started_at
    with pull_request_api(remote) as api:
        with pytest.raises(OrchestrationError) as error:
            host(api).snapshot(remote.repository, 17)
    assert error.value.code == "pr_incomplete"


def test_partial_rerun_can_retain_its_original_captured_context(remote):
    remote.run["run_attempt"] = 2
    remote.runs = [deepcopy(remote.run)]
    assert remote.context["producer"]["run_attempt"] == 1
    with pull_request_api(remote) as api:
        result = host(api).snapshot(remote.repository, 17)
    assert result["validation"] == "passed"
    assert result["provenance"]["status"] == "qualified"
    assert result["provenance"]["run_attempt"] == 2
    assert result["provenance"]["context"]["producer"]["run_attempt"] == 1


def test_missing_authoritative_artifact_digest_cannot_qualify_evidence(remote):
    def missing_digest(method, path, query, body):
        if path.endswith("/artifacts"):
            return 200, {"artifacts": [{"id": 501, "name": "ci-result-evidence--attempt-1", "expired": False,
                                       "size_in_bytes": len(remote.archive())}]}
        return None
    remote.extra = missing_digest
    with pull_request_api(remote) as api:
        result = host(api).snapshot(remote.repository, 17)
    assert result["provenance"]["status"] != "qualified"


def test_incorrect_source_check_association_cannot_qualify_the_candidate(remote):
    for check in remote.checks:
        check["head_sha"] = "f" * 40
    with pull_request_api(remote) as api:
        result = host(api).snapshot(remote.repository, 17)
    assert result["validation"] != "passed"


@pytest.mark.parametrize("field", ["attempt", "base", "tree", "workflow"])
def test_foreign_candidate_evidence_cannot_qualify_the_handoff(remote, field):
    if field == "attempt":
        remote.context["producer"]["run_attempt"] = 2
    elif field == "base":
        remote.context["tested_base_sha"] = "f" * 40
    elif field == "tree":
        remote.context["checkout"]["tree"] = "f" * 40
    else:
        remote.context["producer"]["workflow_ref"] = remote.repository + "/.github/workflows/unrelated.yml@refs/heads/master"
    with pull_request_api(remote) as api:
        result = host(api).snapshot(remote.repository, 17)
    assert result["provenance"]["status"] != "qualified"


def test_collection_page_budget_cannot_turn_missing_threads_into_an_empty_list(remote):
    def full_pages(method, path, query, body):
        if path == "/graphql":
            return 200, {"data": {"repository": {"pullRequest": {"reviewThreads": {
                "nodes": [{"id": "hidden-blocker", "isResolved": False}],
                "pageInfo": {"hasNextPage": True, "endCursor": "next"}}}}}}
        return None
    remote.extra = full_pages
    with pull_request_api(remote) as api:
        with pytest.raises(OrchestrationError) as error:
            host(api, max_pages=1).snapshot(remote.repository, 17)
    assert error.value.code == "pr_incomplete"


def test_rest_pagination_reads_all_pages_and_reports_exhausted_budget(remote):
    def full_pages(method, path, query, body):
        if path.endswith("/reviews"):
            page = int(query.get("page", [1])[0])
            return 200, [{"id": page * 100 + index, "state": "COMMENTED"} for index in range(100)] if page == 1 else [{"id": 999, "state": "APPROVED"}]
        return None
    remote.extra = full_pages
    with pull_request_api(remote) as api:
        rows = host(api)._all(f"/repos/{remote.repository}/pulls/17/reviews")
        assert len(rows) == 101 and rows[-1]["id"] == 999
        with pytest.raises(OrchestrationError) as error:
            host(api, max_pages=1)._all(f"/repos/{remote.repository}/pulls/17/reviews")
    assert error.value.code == "pr_incomplete"


@pytest.mark.parametrize("kind", ["duplicate", "traversal", "symlink", "expanded_size"])
def test_artifact_archive_guards_reject_unsafe_or_unbounded_members(remote, kind):
    entries = [("result.json", json.dumps(remote.result), "file")]
    if kind == "duplicate":
        entries.extend([("extra.log", "repeated auxiliary member", "file")] * 2)
    elif kind == "traversal":
        entries.append(("../outside", "outside", "file"))
    elif kind == "symlink":
        entries.append(("linked", "outside", "symlink"))
    else:
        entries.append(("oversized", "x" * 16_777_217, "file"))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        archive = artifact_zip(entries)
    if kind == "expanded_size":
        compressed = io.BytesIO()
        with zipfile.ZipFile(compressed, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
            for name, content, _ in entries:
                zipped.writestr(name, content)
        archive = compressed.getvalue()
        assert len(archive) < 16_777_216, "this control must test expansion rather than transfer size"
    adapter = host("http://127.0.0.1")
    listing = [{"id": 501, "name": "ci-result-evidence--attempt-1", "expired": False,
                "size_in_bytes": len(archive), "digest": "sha256:" + hashlib.sha256(archive).hexdigest()}]
    with patch.object(adapter, "_all", return_value=listing), \
            patch("xorchestrate.adapters.pr_github._OPENER.open", return_value=io.BytesIO(archive)):
        with pytest.raises(ValueError):
            adapter._artifact_result(remote.repository, remote.run)


def test_signed_artifact_redirect_drops_the_api_credential(remote):
    adapter = host("http://127.0.0.1")
    signed = "https://signed.example.invalid/artifact.zip?signature=inert"
    redirect = urllib.error.HTTPError("http://127.0.0.1/archive", 302, "redirect", {"Location": signed}, None)
    listing = [{"id": 501, "name": "ci-result-evidence--attempt-1", "expired": False,
                "size_in_bytes": len(remote.archive()),
                "digest": "sha256:" + hashlib.sha256(remote.archive()).hexdigest()}]
    with patch.object(adapter, "_all", return_value=listing), \
            patch("xorchestrate.adapters.pr_github._OPENER.open", side_effect=[redirect, io.BytesIO(remote.archive())]) as opening:
        result, _ = adapter._artifact_result(remote.repository, remote.run)
    assert result["status"] == "passed"
    assert opening.call_args_list[0].args[0].get_header("Authorization") == "Bearer inert-pr-test-token"
    assert opening.call_args_list[1].args[0].full_url == signed
    assert opening.call_args_list[1].args[0].get_header("Authorization") is None
