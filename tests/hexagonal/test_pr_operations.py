"""PR delivery contracts cross the real application journal and MCP input boundary."""

import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace

import pytest

from tests.hexagonal.support.application import application
from tests.hexagonal.support.pr_operations import (
    BASE,
    HEAD,
    MERGE,
    REPOSITORY,
    HostingFixture,
    WorkspaceFixture,
    publication,
)
from xorchestrate.lifecycles import default_definitions
from xorchestrate.mcp import MCPServer
from xorchestrate.model import OrchestrationError
from xorchestrate.pr import PullRequestOperations
from xorchestrate.state import StateEngine


def setup(store):
    host, workspace = HostingFixture(), WorkspaceFixture()
    provider = PullRequestOperations(host, workspace, store, lambda: "2026-11-10T12:00:00Z")
    app = application(store, providers={"pr": provider})
    return app, host, workspace


def invoke(app, name, arguments, key):
    return app.call("invoke", {"capability": f"pr/{name}", "arguments": arguments,
                               "idempotency_key": key})["invocation"]["result"]


def candidate():
    return {"repository": REPOSITORY, "number": 17, "expected_head": HEAD}


def test_current_handoff_reports_validation_and_preserves_draft_readiness(store):
    app, host, _ = setup(store)
    result = invoke(app, "pr.handoff", candidate(), "handoff")
    assert result["status"] == "observed"
    assert result["technical_validation"] == "passed"
    assert result["candidate"]["head"] == HEAD and result["candidate"]["base"] == BASE
    assert result["candidate"]["merge"] == MERGE
    assert result["blockers"] == []
    assert host.current["draft"] is True
    assert not host.created


@pytest.mark.parametrize("kind,expected", [
    ("missing", "unavailable"), ("matching", "applicable"), ("head", "historical"),
    ("tree", "historical"), ("incomplete", "unverified"),
])
def test_review_evidence_reports_source_applicability_without_certifying_independence(store, kind, expected):
    app, host, _ = setup(store)
    arguments = candidate()
    review = publication()["review"]
    if kind == "head":
        review["head"] = "f" * 40
    elif kind == "tree":
        review["tree"] = "f" * 40
    elif kind == "incomplete":
        review = {"reviewer": "independent-session"}
    if kind != "missing":
        arguments["review"] = review
    result = invoke(app, "pr.handoff", arguments, "review-observation")
    assert result["review_evidence"]["status"] == expected
    assert result["review_evidence"]["independence_verified"] is False
    assert result["technical_validation"] == "passed"
    assert result["readiness_changed"] is False and host.current["draft"] is True


@pytest.mark.parametrize("change", ["new_attempt", "reopened_thread", "ready", "closed", "retargeted"])
def test_fixed_git_identity_does_not_hide_changed_blockers_or_pr_state(store, change):
    app, host, _ = setup(store)
    before = deepcopy(host.current)
    after = deepcopy(before)
    if change == "new_attempt":
        after["checks"][0].update(id=102, conclusion="failure", run_attempt=2)
    elif change == "reopened_thread":
        after["threads"] = [{"id": "thread-1", "resolved": False}]
    elif change == "ready":
        after["draft"] = False
    elif change == "closed":
        after["state"] = "closed"
    else:
        after["base_ref"] = "another-target"
    assert (before["head"], before["base"], before["merge"]) == (after["head"], after["base"], after["merge"])
    host.snapshots = [before, after]
    result = invoke(app, "pr.handoff", candidate(), "handoff")
    assert result["status"] == "superseded"
    assert host.current["draft"] is True, "observation must not change readiness"


def test_incomplete_collection_cannot_be_reported_as_no_blockers(store):
    app, host, _ = setup(store)
    host.current["complete"] = False
    result = invoke(app, "pr.inspect", candidate(), "inspect")
    assert result["status"] == "incomplete"
    assert result["technical_validation"] != "passed"


def test_replayed_inspection_is_historical_and_a_new_key_reads_current_state(store):
    app, host, _ = setup(store)
    first = invoke(app, "pr.inspect", candidate(), "observation-one")
    reads = host.reads
    host.current["threads"] = [{"id": "owner-discussion", "resolved": False}]
    replay = invoke(app, "pr.inspect", candidate(), "observation-one")
    assert replay == first
    assert host.reads == reads, "a journal replay cannot pretend to be a fresh remote read"
    fresh = invoke(app, "pr.inspect", candidate(), "observation-two")
    assert host.reads > reads
    assert fresh["blockers"], "the fresh observation must see the newly unresolved discussion"


def test_adopting_an_existing_draft_preserves_owner_body(store):
    app, host, _ = setup(store)
    owner_body = "Owner text edited after an earlier automated proposal."
    host.current["body"] = owner_body
    host.existing = [deepcopy(host.current)]
    result = invoke(app, "pr.open_draft", publication(), "adopt")
    assert result["status"] == "published" and result["adopted"] is True
    assert host.current["body"] == owner_body
    assert host.created == [], "adoption must not create a duplicate PR"
    assert host.current["draft"] is True
    with store.transaction() as transaction:
        transitions = [row for row in transaction.events() if row.get("type") == "state.transition"
                       and row.get("subject") == {"kind": "operation", "key": result["operation_id"]}]
    assert any(row["event"] == "progress" and row["source"] == row["target"] == "pending"
               for row in transitions), "PR progress must participate in configured state transitions"


def test_configured_operation_progress_guard_can_refuse_before_any_external_write(store):
    host, workspace = HostingFixture(), WorkspaceFixture()
    definitions = default_definitions()
    definition = definitions["operation"]
    definitions["operation"] = replace(definition, transitions=tuple(
        replace(rule, guard=lambda _: False) if rule.event == "progress" else rule
        for rule in definition.transitions))
    engine = StateEngine(definitions)
    provider = PullRequestOperations(host, workspace, store, lambda: "2026-11-10T12:00:00Z", state_engine=engine)
    app = application(store, providers={"pr": provider}, state_engine=engine)
    with pytest.raises(OrchestrationError) as error:
        invoke(app, "pr.open_draft", publication(), "denied-progress")
    assert error.value.code == "operation_failed"
    assert error.value.details.get("cause") == "state_guard_rejected"
    assert workspace.publications == [] and host.created == []


@pytest.mark.parametrize("changed", ["ready", "base", "head"])
def test_incompatible_existing_pr_is_not_retargeted_or_reopened(store, changed):
    app, host, _ = setup(store)
    if changed == "ready":
        host.current["draft"] = False
    elif changed == "base":
        host.current["base_ref"] = "other"
    else:
        host.current["head"] = "f" * 40
    existing = deepcopy(host.current)
    host.existing = [deepcopy(existing)]
    try:
        result = invoke(app, "pr.open_draft", publication(), "open")
    except OrchestrationError:
        pass
    else:
        assert result["status"] not in {"created", "adopted", "published"}
    assert host.current == existing
    assert host.created == []


def test_review_for_another_tree_cannot_authorize_publication(store):
    app, host, workspace = setup(store)
    arguments = publication()
    arguments["review"]["tree"] = "f" * 40
    with pytest.raises(OrchestrationError):
        invoke(app, "pr.open_draft", arguments, "wrong-review")
    assert workspace.publications == [] and host.created == []


def test_unknown_creation_is_retained_through_mcp_and_reconciled_without_repeating_write(store):
    app, host, _ = setup(store)

    def timed_out_after_creation(remote):
        raise TimeoutError("response lost after the PR was created")

    host.create_callback = timed_out_after_creation
    server = MCPServer(app)
    server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                   "params": {"protocolVersion": "2025-06-18"}})
    arguments = {"capability": "pr/pr.open_draft", "arguments": publication(), "idempotency_key": "create-once"}
    request = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
               "params": {"name": "invoke", "arguments": arguments}}
    response = server.handle(request)["result"]
    assert response["isError"] is True
    error = response["structuredContent"]["error"]
    assert error["code"] == "operation_unknown"
    operation = app.call("get_operation", {"operation_id": error["details"]["operation_id"]})["operation"]
    assert operation["status"] == "unknown"
    assert operation["pr_progress"]["step"] == "create_dispatch"
    assert operation["pr_progress"]["published"]["head"] == HEAD
    repeated = server.handle({**request, "id": 3})["result"]
    assert repeated["structuredContent"]["error"]["code"] == "operation_unresolved"
    assert len(host.created) == 1
    # A different idempotency key cannot escape custody of an unresolved target.
    with pytest.raises(OrchestrationError) as competing:
        invoke(app, "pr.open_draft", publication(), "another-key")
    assert competing.value.details.get("cause") == "pr_target_busy" or competing.value.code == "pr_target_busy"
    assert len(host.created) == 1
    # Read the actual remote outcome, then use the existing journal reconciliation.
    observed = invoke(app, "pr.inspect", candidate(), "reconcile-read")
    assert observed["candidate"]["head"] == HEAD
    verified = {"status": "created", "candidate": observed["candidate"]}
    app.call("resolve_operation", {"operation_id": operation["id"], "expected_version": operation["version"],
                                   "outcome": "completed", "result": verified,
                                   "reason": "Verified the created draft against the remote system"})
    replay = server.handle({**request, "id": 4})["result"]
    assert replay["isError"] is False
    assert replay["structuredContent"]["invocation"]["result"] == verified
    assert len(host.created) == 1


def test_remote_head_moving_during_creation_leaves_the_draft_superseded(store):
    app, host, _ = setup(store)

    def moved_after_creation(remote):
        remote.current["head"] = "f" * 40
        return deepcopy(remote.current)

    host.create_callback = moved_after_creation
    result = invoke(app, "pr.open_draft", publication(), "moving-head")
    assert result["status"] == "superseded"
    assert len(host.created) == 1 and host.current["draft"] is True
    assert host.current["head"] == "f" * 40


def test_distinct_operation_keys_cannot_write_the_same_target_concurrently(store):
    app, host, _ = setup(store)
    entered, release = threading.Event(), threading.Event()

    def held_creation(remote):
        entered.set()
        assert release.wait(5), "test never released held remote write"
        return deepcopy(remote.current)

    host.create_callback = held_creation
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(invoke, app, "pr.open_draft", publication(), "first-writer")
        assert entered.wait(5)
        try:
            with pytest.raises(OrchestrationError) as error:
                invoke(app, "pr.open_draft", publication(), "second-writer")
            assert error.value.details.get("cause") == "pr_target_busy" or error.value.code == "pr_target_busy"
            assert len(host.created) == 1
        finally:
            release.set()
        assert first.result(timeout=5)["status"] == "published"
