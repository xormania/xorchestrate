"""Trusted queue cleanup preserves current validation and cancels proved old work."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from tests.hexagonal.support.ci_queue import QueueAPI

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def queue():
    from scripts.ci.queue import reconcile

    api = QueueAPI()
    return api, lambda trigger=300: reconcile(api.repository, trigger, get=api.get, cancel=api.cancel)


@pytest.mark.parametrize("action", ("opened", "synchronize", "reopened", "edited"))
def test_current_source_or_metadata_replacement_releases_only_old_source_work(queue, action):
    api, reconcile = queue
    api.runs[300] = api.run(300, action=action)
    api.runs[201] = api.run(201, head="c" * 40, action="edited", status="queued")
    api.runs[202] = api.run(202)  # Same-head validation must survive, even if older.
    api.runs[400] = api.run(400, head="d" * 40)  # Delayed old trigger must not cancel newer work.
    reconcile()
    assert set(api.cancelled) == {200, 201}, "CI_QUEUE_CANCELS_ONLY_PROVED_OLDER_SOURCE"


@pytest.mark.parametrize("status", ("queued", "in_progress", "waiting", "pending", "requested"))
def test_old_work_is_cancelled_before_or_during_execution(queue, status):
    api, reconcile = queue
    api.runs[200]["status"] = status
    reconcile()
    assert api.cancelled == [200], "CI_QUEUE_RELEASES_OLD_RUNNER_DEMAND"


def test_delayed_trigger_for_old_source_cannot_cancel_current_validation(queue):
    api, reconcile = queue
    api.runs[100] = api.run(100, head="d" * 40)
    reconcile(200)
    assert api.cancelled == [], "CI_QUEUE_DELAYED_EVENT_HAS_NO_CANCELLATION_AUTHORITY"


@pytest.mark.parametrize("target", (200, 300))
@pytest.mark.parametrize("fault", (
    "repository", "source_repository", "workflow", "workflow_path", "event", "title_head",
    "title_pr", "association_pr", "source_branch", "association_repository", "missing_attempt",
))
def test_incomplete_or_foreign_run_identity_cannot_authorize_cancellation(queue, target, fault):
    api, reconcile = queue
    row = api.runs[target]
    if fault == "repository":
        row["repository"]["id"] += 1
    elif fault == "source_repository":
        row["head_repository"]["id"] += 1
    elif fault == "workflow":
        row["workflow_id"] += 1
    elif fault == "workflow_path":
        row["path"] = ".github/workflows/release.yml"
    elif fault == "event":
        row["event"] = "push"
    elif fault == "title_head":
        row["display_title"] = row["display_title"].replace(row["head_sha"], "f" * 40)
    elif fault == "title_pr":
        row["display_title"] = row["display_title"].replace(":17:", ":18:")
    elif fault == "association_pr":
        row["pull_requests"][0]["number"] = 18
    elif fault == "source_branch":
        row["head_branch"] = "unrelated"
    elif fault == "association_repository":
        row["pull_requests"][0]["head"]["repo"]["id"] += 1
    elif fault == "missing_attempt":
        row.pop("run_attempt")
    reconcile()
    assert api.cancelled == [], "CI_QUEUE_FOREIGN_OR_INCOMPLETE_IDENTITY_IS_PRESERVED"


@pytest.mark.parametrize("fault", ("closed", "changed_pr_head", "changed_canonical_head", "missing_head", "base_only"))
def test_current_pr_identity_and_source_replacement_are_required(queue, fault):
    api, reconcile = queue
    if fault == "closed":
        api.pr["state"] = "closed"
    elif fault == "changed_pr_head":
        api.pr["head"]["sha"] = "e" * 40
    elif fault == "changed_canonical_head":
        api.ref["object"]["sha"] = "e" * 40
    elif fault == "missing_head":
        api.pr["head"]["repo"] = None
    elif fault == "base_only":
        api.runs[200] = api.run(200)
        api.runs[200]["display_title"] = api.runs[200]["display_title"].rsplit(":", 1)[0] + ":" + "e" * 40
    reconcile()
    assert api.cancelled == [], "CI_QUEUE_REQUIRES_CURRENT_SOURCE_REPLACEMENT"


@pytest.mark.parametrize("target", (200, 300))
def test_completed_cancelled_runs_cannot_be_targets_or_replacements(queue, target):
    api, reconcile = queue
    api.runs[target].update(status="completed", conclusion="cancelled")
    reconcile()
    assert api.cancelled == [], "CI_QUEUE_CANCELLED_REPLACEMENT_OR_TERMINAL_TARGET_IS_PRESERVED"


@pytest.mark.parametrize("fault", ("replacement_cancelled", "replacement_attempt", "target_attempt", "head_advanced"))
def test_changed_observations_after_listing_do_not_authorize_cancellation(queue, fault):
    api, reconcile = queue
    listed = False

    def advance(peer, path, count):
        nonlocal listed
        if path.startswith("/actions/workflows/ci.yml/runs?"):
            listed = True
            return
        if not listed:
            return
        if fault == "replacement_cancelled":
            peer.runs[300].update(status="completed", conclusion="cancelled")
        elif fault == "replacement_attempt":
            peer.runs[300]["run_attempt"] = 2
        elif fault == "target_attempt":
            peer.runs[200]["run_attempt"] = 2
        elif fault == "head_advanced":
            peer.pr["head"]["sha"] = peer.ref["object"]["sha"] = "e" * 40

    api.before_read = advance
    reconcile()
    assert api.cancelled == [], "CI_QUEUE_REREADS_BEFORE_CANCELLATION"


def test_api_failure_does_not_turn_unknown_state_into_cancellation(queue):
    api, reconcile = queue

    def fail(_peer, path, count):
        if path == "/git/ref/heads/feature":
            raise OSError("unavailable")

    api.before_read = fail
    reconcile()
    assert api.cancelled == [], "CI_QUEUE_UNAVAILABLE_OBSERVATION_PRESERVES_WORK"


def test_each_cancellation_rechecks_current_source(queue):
    api, reconcile = queue
    api.runs[201] = api.run(201, head="c" * 40)

    def advance(peer, _run_id):
        peer.pr["head"]["sha"] = peer.ref["object"]["sha"] = "e" * 40

    api.before_cancel = advance
    reconcile()
    assert len(api.cancelled) == 1, "CI_QUEUE_SECOND_CANCELLATION_REQUIRES_FRESH_CURRENT_HEAD"


def test_listing_cannot_substitute_for_native_target_observation(queue):
    api, reconcile = queue
    api.listing = deepcopy(list(api.runs.values()))
    api.runs[200]["head_sha"] = api.pr["head"]["sha"]
    reconcile()
    assert api.cancelled == [], "CI_QUEUE_NATIVE_TARGET_OVERRIDES_STALE_LISTING"


def test_queue_controller_runs_trusted_code_with_narrow_permissions():
    workflows = [json.loads(path.read_text()) for path in (ROOT / ".github/workflows").glob("*.yml")]
    controllers = [workflow for workflow in workflows if
                   "requested" in workflow.get("on", {}).get("workflow_run", {}).get("types", [])]
    assert len(controllers) == 1, "CI_QUEUE_HAS_REQUESTED_RUN_CLEANUP"
    workflow = controllers[0]
    trigger = workflow["on"]["workflow_run"]
    assert trigger["workflows"] == ["CI"]
    assert {"requested", "in_progress"} <= set(trigger["types"]), "CI_QUEUE_COVERS_NEW_RUNS_AND_RERUNS"
    assert set(workflow["on"]) == {"workflow_run"}, "CI_QUEUE_NEVER_EXECUTES_PR_EVENT_CODE"
    for job in workflow["jobs"].values():
        permissions = job.get("permissions", workflow.get("permissions", {}))
        assert permissions == {"contents": "read", "actions": "write", "pull-requests": "read"}, \
            "CI_QUEUE_WRITE_AUTHORITY_IS_SCOPED"
        checkout = [step for step in job["steps"] if step.get("uses", "").startswith("actions/checkout@")]
        assert len(checkout) == 1
        assert checkout[0]["with"]["ref"] == "${{ github.sha }}", "CI_QUEUE_CHECKOUT_IS_TRUSTED_IMMUTABLE_SHA"
        assert checkout[0]["with"]["persist-credentials"] is False
        assert not any("download-artifact" in step.get("uses", "") for step in job["steps"])
        commands = "\n".join(step.get("run", "") for step in job["steps"])
        assert "python3 -I -B scripts/ci/queue.py" in commands, "CI_QUEUE_USES_ISOLATED_TRUSTED_CONTROLLER"
        assert not any(word in commands for word in ("pip install", "uv sync", "github.event."))

