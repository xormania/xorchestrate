"""Actual Git candidates, platform associations and lane attempts remain distinct."""

import base64
import hashlib
import io
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPRedirectHandler

import pytest

from scripts.ci import candidate as subject
from scripts.ci import result as aggregate
from tests.hexagonal.support.ci_candidate import (
    artifact_zip,
    candidate_repository,
    git,
    github_observation,
    lane_lineage,
    lane_runtime,
    strict_documentation_receipts,
    write_json,
)


@pytest.fixture
def repository(tmp_path):
    return candidate_repository(tmp_path / "repository")


@pytest.fixture
def captured(repository):
    event, env = github_observation(repository)
    return subject.capture_context(repository["root"], event, env)


def test_capture_proves_ordered_merge_parents_and_preserves_event_observation(repository):
    event, env = github_observation(repository)
    # Event metadata is an observation; Git's merge parent proves the tested base.
    event["pull_request"]["base"]["sha"] = repository["initial"]
    context = subject.capture_context(repository["root"], event, env)
    assert context["schema_version"] == 1
    assert context["source_sha"] == repository["source"]
    assert context["checkout"] == {
        "commit": repository["tested_commit"], "tree": repository["tree"],
        "parents": repository["parents"], "status": "", "kind": "pr_merge",
    }
    assert context["tested_base_sha"] == repository["tested_base"]
    assert context["event"]["reported_base_sha"] == repository["initial"]
    assert context["pr"] == {"number": 17, "source_ref": "feature", "target_ref": "master"}
    assert context["producer"]["check_sha"] == repository["source"]
    assert context["producer"]["run_id"] == 42778000000
    assert context["producer"]["run_attempt"] == 1
    assert set(context["inputs"]) == {
        "workflow_sha256", "catalog_sha256", "lock_sha256", "profile_sha256", "checker_sha256",
    }
    assert all(len(value) == 64 for value in context["inputs"].values())
    assert subject.validate_context(context) == context


def test_capture_records_material_input_bytes_and_distinct_fork_origin(repository):
    event, env = github_observation(repository)
    event["pull_request"]["head"]["repo"] = {"id": 777, "full_name": "contributor/xorchestrate"}
    context = subject.capture_context(repository["root"], event, env)
    assert context["repository"] == {"id": 1430398736, "full_name": "xormania/xorchestrate"}
    assert context["source_repository"] == {"id": 777, "full_name": "contributor/xorchestrate"}
    for key, path in (
        ("workflow_sha256", ".github/workflows/ci.yml"),
        ("catalog_sha256", "contracts/testing.json"),
        ("lock_sha256", "uv.lock"),
        ("profile_sha256", "pyproject.toml"),
    ):
        expected = hashlib.sha256((repository["root"] / path).read_bytes()).hexdigest()
        assert context["inputs"][key] == expected, key


@pytest.mark.parametrize("fault", ("old_check", "source_not_second_parent", "single_parent", "dirty"))
def test_actual_checkout_cannot_certify_an_unrelated_platform_event(repository, fault):
    event, env = github_observation(repository)
    if fault == "old_check":
        env["GITHUB_SHA"] = repository["initial"]
    elif fault == "source_not_second_parent":
        event["pull_request"]["head"]["sha"] = repository["tested_base"]
    elif fault == "single_parent":
        git(repository["root"], "checkout", "--quiet", "feature")
        env["GITHUB_SHA"] = repository["source"]
    else:
        (repository["root"] / "source.py").write_text("VALUE = 99\n")
    with pytest.raises(ValueError):
        subject.capture_context(repository["root"], event, env)


@pytest.mark.parametrize("event_name", ("push", "workflow_dispatch"))
def test_non_pr_execution_records_a_branch_without_inventing_integration(repository, event_name):
    git(repository["root"], "checkout", "--quiet", "feature")
    event, env = github_observation(repository, event_name=event_name)
    event.pop("pull_request")
    event.pop("number")
    env.update(GITHUB_SHA=repository["source"], GITHUB_REF="refs/heads/feature")
    context = subject.capture_context(repository["root"], event, env)
    assert context["pr"] is None
    assert context["tested_base_sha"] is None
    assert context["checkout"]["kind"] == "branch"
    assert context["checkout"]["commit"] == repository["source"]
    assert context["checkout"]["parents"] == [repository["initial"]]


@pytest.mark.parametrize("fault", (
    "unsupported_schema", "boolean_schema", "missing_repository", "reversed_parents",
    "wrong_source", "wrong_tested_base", "different_check", "missing_status", "dirty_status",
))
def test_manifest_revalidation_rejects_contradictory_identity(captured, fault):
    context = deepcopy(captured)
    if fault == "unsupported_schema":
        context["schema_version"] = 999
    elif fault == "boolean_schema":
        context["schema_version"] = True
    elif fault == "missing_repository":
        del context["repository"]
    elif fault == "reversed_parents":
        context["checkout"]["parents"].reverse()
    elif fault == "wrong_source":
        context["source_sha"] = "f" * 40
    elif fault == "wrong_tested_base":
        context["tested_base_sha"] = "f" * 40
    elif fault == "different_check":
        context["producer"]["check_sha"] = "f" * 40
    elif fault == "missing_status":
        del context["checkout"]["status"]
    else:
        context["checkout"]["status"] = " M source.py\n"
    with pytest.raises(ValueError):
        subject.validate_context(context)


def test_lane_capture_crosschecks_actual_checkout_and_keeps_resolved_runtime(repository, captured):
    _, env = github_observation(repository, attempt=2)
    runtime = lane_runtime(tmux="tmux 3.4")
    manifest = subject.capture_lane(repository["root"], captured, "tmux-acceptance-evidence", env,
                                    runtime=runtime)
    assert manifest["context"] == captured
    assert manifest["lane"] == "tmux-acceptance-evidence"
    assert manifest["run_attempt"] == 2
    assert manifest["runtime"] == runtime
    git(repository["root"], "checkout", "--quiet", "feature")
    with pytest.raises(ValueError):
        subject.capture_lane(repository["root"], captured, "tmux-acceptance-evidence", env,
                             runtime=runtime)


def test_partial_rerun_qualifies_each_original_producer_without_hiding_failure(captured):
    context, manifests, metadata, expected = lane_lineage(captured)
    references = subject.qualify_lineage(context, manifests, metadata, expected)
    assert len(references) == len(expected)
    assert {reference["run_attempt"] for reference in references} == {1, 2}
    assert {reference["artifact_id"] for reference in references} == {901, 902, 903}
    assert {reference["job_id"] for reference in references} == {101, 102, 103}
    assert all(reference["artifact_digest"].startswith("sha256:") for reference in references)
    # Input history must survive qualification unchanged.
    assert any(job["conclusion"] == "failure" for job in metadata["jobs"])


@pytest.mark.parametrize("fault", (
    "wrong_run", "wrong_repository", "wrong_event", "wrong_source", "wrong_check",
    "wrong_workflow", "future_attempt", "newer_failed_lane", "newer_cancelled_lane",
    "expired_artifact", "missing_artifact", "duplicate_artifact", "different_manifest_attempt",
    "different_manifest_context", "invalid_digest", "git_tree", "git_parent", "check_app",
    "check_suite", "head_branch", "head_repository",
))
def test_partial_rerun_cannot_borrow_foreign_or_contradictory_lane_evidence(captured, fault):
    context, manifests, metadata, expected = lane_lineage(captured)
    first = next(iter(manifests.values()))
    if fault == "wrong_run":
        metadata["run"]["id"] += 1
    elif fault == "wrong_repository":
        metadata["run"]["repository"]["id"] += 1
    elif fault == "wrong_event":
        metadata["run"]["event"] = "workflow_dispatch"
    elif fault == "wrong_source":
        metadata["run"]["head_sha"] = "f" * 40
    elif fault == "wrong_check":
        metadata["check"]["head_sha"] = "f" * 40
    elif fault == "wrong_workflow":
        metadata["run"]["path"] = ".github/workflows/untrusted.yml"
    elif fault == "future_attempt":
        metadata["jobs"][0]["run_attempt"] = 3
    elif fault in {"newer_failed_lane", "newer_cancelled_lane"}:
        metadata["jobs"].append({**metadata["jobs"][0], "id": 999, "run_attempt": 2,
                                 "conclusion": "failure" if fault == "newer_failed_lane" else "cancelled"})
    elif fault == "expired_artifact":
        metadata["artifacts"][0]["expired"] = True
    elif fault == "missing_artifact":
        metadata["artifacts"].pop(0)
    elif fault == "duplicate_artifact":
        metadata["artifacts"].append({**metadata["artifacts"][0], "id": 999})
    elif fault == "different_manifest_attempt":
        first["run_attempt"] = 2
    elif fault == "different_manifest_context":
        first["context"]["inputs"]["lock_sha256"] = "f" * 64
    elif fault == "invalid_digest":
        metadata["artifacts"][0]["digest"] = "not-a-digest"
    elif fault == "git_tree":
        metadata["git"]["tree"]["sha"] = "f" * 40
    elif fault == "git_parent":
        metadata["git"]["parents"].reverse()
    elif fault == "check_app":
        metadata["check"]["app"]["id"] = 999
    elif fault == "check_suite":
        metadata["check"]["id"] = 999
    elif fault == "head_branch":
        metadata["run"]["head_branch"] = "other"
    else:
        metadata["run"]["head_repository"]["id"] = 999
    with pytest.raises(ValueError):
        subject.qualify_lineage(context, manifests, metadata, expected)


def _archive_digest(payload):
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def test_archive_consumer_verifies_api_digest_before_extracting(tmp_path):
    payload = artifact_zip([("lane/manifest.json", b'{"schema_version":1}\n', "file")])
    destination = tmp_path / "healthy"
    subject.extract_artifact(payload, destination, digest=_archive_digest(payload))
    assert (destination / "lane/manifest.json").read_bytes() == b'{"schema_version":1}\n'
    rejected = tmp_path / "wrong-digest"
    with pytest.raises(ValueError):
        subject.extract_artifact(payload, rejected, digest="sha256:" + "0" * 64)
    assert not rejected.exists(), "Unverified archive bytes must not become retained evidence"


@pytest.mark.parametrize("fault", (
    "parent_traversal", "absolute", "backslash", "drive", "symlink", "duplicate", "alias", "file_prefix",
))
def test_archive_consumer_rejects_unsafe_or_ambiguous_members_before_writing(tmp_path, fault):
    entries = [("report.json", b"{}", "file")]
    if fault == "duplicate":
        entries.append(entries[0])
    elif fault == "alias":
        entries.append(("./report.json", b"different", "file"))
    elif fault == "file_prefix":
        entries.append(("report.json/member.json", b"different", "file"))
    else:
        name = {"parent_traversal": "../escape.json", "absolute": "/escape.json",
                "backslash": "lane\\report.json", "drive": "C:/escape.json",
                "symlink": "linked.json"}[fault]
        entries.append((name, b"../../outside" if fault == "symlink" else b"{}",
                        "symlink" if fault == "symlink" else "file"))
    payload = artifact_zip(entries)
    destination = tmp_path / "rejected"
    with pytest.raises((ValueError, OSError)):
        subject.extract_artifact(payload, destination, digest=_archive_digest(payload))
    assert not destination.exists(), "Reject the whole unsafe archive before extracting safe-looking members"


@pytest.mark.parametrize("limit", ("MAX_ARCHIVE", "MAX_EXPANDED", "MAX_MEMBERS"))
def test_archive_consumer_enforces_each_configured_resource_bound(tmp_path, monkeypatch, limit):
    # Empty members isolate compressed archive size from per-member size bounds.
    contents = b"" if limit == "MAX_ARCHIVE" else b"one"
    payload = artifact_zip([("one.json", contents, "file"), ("two.json", contents, "file")])
    monkeypatch.setattr(subject, limit, 1)
    with pytest.raises(ValueError):
        subject.extract_artifact(payload, tmp_path / "bounded", digest=_archive_digest(payload))


@pytest.mark.parametrize("url", (
    "http://api.github.com/repos/x/y", "https://api.github.com.evil.invalid/repos/x/y",
    "https://other.invalid/archive.zip", "https://token@api.github.com/repos/x/y",
    "https://api.github.com:8443/repos/x/y", "file:///tmp/archive.zip",
))
def test_api_reader_does_not_contact_an_unapproved_origin(monkeypatch, url):
    def unexpected(*args, **kwargs):
        pytest.fail("An invalid credential destination must be rejected before HTTP I/O")
    monkeypatch.setattr(subject, "urlopen", unexpected)
    with pytest.raises(ValueError):
        subject.api_bytes(url, "inert-canary", 10)


def test_api_reader_bounds_bytes_and_does_not_forward_authorization_on_redirect(monkeypatch):
    observed = []

    def respond(request, *, timeout):
        observed.append(request)
        assert timeout == 30
        return io.BytesIO(b"12345")

    monkeypatch.setattr(subject, "urlopen", respond)
    url = "https://api.github.com/repos/xormania/xorchestrate/actions/artifacts/1/zip"
    assert subject.api_bytes(url, "inert-canary", 5) == b"12345"
    redirected = HTTPRedirectHandler().redirect_request(
        observed[0], None, 302, "Found", {}, "https://blob.invalid/archive.zip")
    assert observed[0].get_header("Authorization") == "Bearer inert-canary"
    assert redirected.get_header("Authorization") is None
    with pytest.raises(ValueError):
        subject.api_bytes(url, "inert-canary", 4)


def test_strict_aggregate_binds_original_context_and_preserves_attempt_history(tmp_path, captured):
    context, metadata, needs, workflow = strict_documentation_receipts(tmp_path, captured)
    result = aggregate.evaluate(needs, event="pull_request", source_sha=context["source_sha"],
                                tested_sha=context["checkout"]["commit"], workflow=workflow,
                                artifacts=tmp_path, catalog={}, context=context, metadata=metadata)
    assert result["status"] == "passed", result["errors"]
    assert result["schema_version"] == 2
    assert result["candidate_context"] == context
    assert len(result["evidence_references"]) == 1
    assert result["evidence_references"][0]["artifact_id"] == 901
    assert result["evidence_references"][0]["run_attempt"] == 1
    assert any(job["conclusion"] == "failure" for job in result["attempts"])
    assert "GitHub check association" in aggregate.markdown(result, "https://example.invalid/run/1")


@pytest.mark.parametrize("fault", (
    "missing_original", "changed_original", "wrong_source", "wrong_execution", "receipt_tree",
))
def test_strict_aggregate_cannot_change_the_captured_candidate(tmp_path, captured, fault):
    context, metadata, needs, workflow = strict_documentation_receipts(tmp_path, captured)
    source, tested = context["source_sha"], context["checkout"]["commit"]
    if fault == "missing_original":
        (tmp_path / "candidate-context--attempt-1/context.json").unlink()
    elif fault == "changed_original":
        original = deepcopy(context)
        original["checkout"]["tree"] = "f" * 40
        write_json(tmp_path / "candidate-context--attempt-1/context.json", original)
    elif fault == "wrong_source":
        source = "f" * 40
    elif fault == "wrong_execution":
        tested = "f" * 40
    else:
        path = tmp_path / "static-checks-python-3.12--attempt-1/static/candidate.json"
        receipt = json.loads(path.read_text())
        receipt["tree"] = "f" * 40
        write_json(path, receipt)
    result = aggregate.evaluate(needs, event="pull_request", source_sha=source, tested_sha=tested,
                                workflow=workflow, artifacts=tmp_path, catalog={}, context=context,
                                metadata=metadata)
    assert result["status"] == "failed"
    expected = ("Suite evidence",) if fault == "receipt_tree" else ("candidate", "context")
    assert any(any(word in message for word in expected) for message in result["errors"])


def test_actual_aggregate_command_requires_context_and_writes_a_qualified_result(tmp_path, captured):
    artifacts = tmp_path / "artifacts"
    context, metadata, needs, workflow = strict_documentation_receipts(artifacts, captured)
    for name, value in (("context", context), ("metadata", metadata), ("needs", needs),
                        ("workflow", workflow), ("catalog", {})):
        write_json(tmp_path / (name + ".json"), value)
    command = [
        sys.executable, "-I", "-B", str(Path(aggregate.__file__).resolve()),
        "--needs", str(tmp_path / "needs.json"), "--workflow", str(tmp_path / "workflow.json"),
        "--catalog", str(tmp_path / "catalog.json"), "--artifacts", str(artifacts),
        "--summary", str(tmp_path / "summary.md"), "--output", str(tmp_path / "result.json"),
        "--event", "pull_request", "--source-sha", context["source_sha"],
        "--tested-sha", context["checkout"]["commit"], "--run-url", "https://example.invalid/run/1",
        "--metadata", str(tmp_path / "metadata.json"),
    ]
    missing = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, timeout=30, check=False)
    assert missing.returncode != 0
    assert "--context" in missing.stderr
    qualified = subprocess.run([*command, "--context", str(tmp_path / "context.json")], cwd=tmp_path,
                               capture_output=True, text=True, timeout=30, check=False)
    assert qualified.returncode == 0, qualified.stderr
    result = json.loads((tmp_path / "result.json").read_text())
    assert result["status"] == "passed" and result["candidate_context"] == context


@pytest.mark.parametrize("fault", (
    "number", "target", "source_repository", "base_repository", "missing_relation", "duplicate_relation",
    "workflow_digest", "missing_workflow_digest",
))
def test_pr_relation_and_immutable_workflow_are_authoritative(captured, fault):
    context, manifests, metadata, expected = lane_lineage(captured)
    edge = metadata["run"]["pull_requests"][0]
    if fault in {"number", "target"}:
        # A coherent claim across every uploaded context is still just a claim.
        key, value = ("number", 999) if fault == "number" else ("target_ref", "other-base")
        context["pr"][key] = value
        for manifest in manifests.values():
            manifest["context"]["pr"][key] = value
    elif fault == "source_repository":
        edge["head"]["repo"]["id"] = 999
    elif fault == "base_repository":
        edge["base"]["repo"]["id"] = 999
    elif fault == "missing_relation":
        metadata["run"]["pull_requests"] = []
    elif fault == "duplicate_relation":
        metadata["run"]["pull_requests"].append(deepcopy(edge))
    elif fault == "workflow_digest":
        metadata["workflow_bytes_sha256"] = "f" * 64
    else:
        del metadata["workflow_bytes_sha256"]
    with pytest.raises(ValueError):
        subject.qualify_lineage(context, manifests, metadata, expected)


def test_pr_relation_retains_base_observation_without_replacing_proven_tested_base(captured):
    context, manifests, metadata, expected = lane_lineage(captured)
    metadata["run"]["pull_requests"][0]["base"]["sha"] = "f" * 40
    references = subject.qualify_lineage(context, manifests, metadata, expected)
    assert len(references) == 3
    assert {reference["run_attempt"] for reference in references} == {1, 2}
    assert context["tested_base_sha"] == captured["checkout"]["parents"][0]
    assert context["producer"]["check_sha"] == context["source_sha"]
    assert context["checkout"]["commit"] != context["source_sha"]


def test_metadata_collector_hashes_workflow_bytes_at_the_reported_immutable_revision(captured, monkeypatch):
    context, _, metadata, _ = lane_lineage(captured)
    workflow_bytes = b'{"name":"CI","jobs":{}}\n'
    expected_digest = hashlib.sha256(workflow_bytes).hexdigest()
    requests = []

    def respond(url, token, limit):
        assert token == "inert-canary"
        parsed = urlsplit(url)
        requests.append(parsed)
        path = parsed.path
        if path.endswith("/contents/.github/workflows/ci.yml"):
            assert parse_qs(parsed.query) == {"ref": [context["producer"]["workflow_sha"]]}
            value = {"encoding": "base64", "content": base64.b64encode(workflow_bytes).decode()}
        elif "/attempts/" in path:
            attempt = int(path.split("/attempts/")[1].split("/")[0])
            value = {"jobs": [job for job in metadata["jobs"] if job["run_attempt"] == attempt]}
        elif path.endswith("/artifacts"):
            value = {"artifacts": metadata["artifacts"]}
        elif "/check-suites/" in path:
            value = metadata["check"]
        elif "/git/commits/" in path:
            value = metadata["git"]
        else:
            assert path.endswith("/actions/runs/" + str(context["producer"]["run_id"])), path
            value = metadata["run"]
        return json.dumps(value).encode()

    monkeypatch.setattr(subject, "api_bytes", respond)
    collected = subject.collect_metadata(context, "inert-canary")
    assert collected.get("workflow_bytes_sha256") == expected_digest
    assert any(request.path.endswith("/contents/.github/workflows/ci.yml") for request in requests)
