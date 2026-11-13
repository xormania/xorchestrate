"""Every selected CI run preserves required verdicts and qualifies behavioral reuse."""

import json
from pathlib import Path

import pytest

from tests.hexagonal.contracts.ci_lifecycle import (
    METADATA_CHANGES,
    REQUIRED_NAMES,
    VALIDATION_EVENTS,
    VALIDATION_JOBS,
)
from tests.hexagonal.support.github_actions import expression, pr_context

ROOT = Path(__file__).resolve().parents[2]


def workflow():
    return json.loads((ROOT / ".github/workflows/ci.yml").read_text())


@pytest.mark.parametrize("changes", METADATA_CHANGES)
@pytest.mark.parametrize("dependency_result", ("success", "failure", "cancelled", "skipped"))
def test_metadata_edits_report_required_checks_in_the_selected_run(changes, dependency_result):
    jobs, context = workflow()["jobs"], pr_context("edited", changes)
    for dependency in context["needs"].values():
        dependency["result"] = dependency_result
    # GitHub selects the latest CI run, not a union of same-head run histories.
    # Each selected run must produce both required verdicts, even after failure.
    required = {job["name"]: job for job in jobs.values() if job.get("name") in REQUIRED_NAMES}
    assert set(required) == REQUIRED_NAMES, "CI_SELECTED_RUN_OWNS_BOTH_STABLE_REQUIRED_NAMES"
    for name, job in required.items():
        assert expression(job.get("if", True), context), "CI_METADATA_REPORTS_REQUIRED_VERDICT_" + name
        assert not job.get("continue-on-error"), "CI_REQUIRED_FAILURE_CANNOT_BE_IGNORED"
    assert expression(jobs["paths"].get("if", True), context), "CI_METADATA_QUALIFIES_CURRENT_EVIDENCE"


@pytest.mark.parametrize("changes", METADATA_CHANGES)
def test_metadata_reuse_keeps_static_and_verdicts_without_repeating_behavior(changes):
    jobs, context = workflow()["jobs"], pr_context("edited", changes)
    context["needs"]["paths"]["outputs"]["reuse"] = "true"
    for name in ("application-tests", "application", "verification", "tmux-acceptance", "package"):
        assert not expression(jobs[name]["if"], context), "CI_QUALIFIED_METADATA_REUSES_" + name
    for name in ("paths", "changes", "architecture-status", "ci-result"):
        assert expression(jobs[name]["if"], context), "CI_METADATA_REUSE_RETAINS_CURRENT_" + name


@pytest.mark.parametrize("action,changes", VALIDATION_EVENTS)
def test_code_unknown_and_base_edit_events_keep_full_candidate_validation(action, changes):
    jobs, context = workflow()["jobs"], pr_context(action, changes)
    for name in VALIDATION_JOBS:
        assert expression(jobs[name].get("if", True), context), "CI_MATERIAL_EVENT_VALIDATES_" + name
    assert expression(jobs["ci-result"]["name"], context) == "CI result"
    assert expression(jobs["architecture-status"]["name"], context) == "Hexagonal architecture"
    assert expression(workflow()["concurrency"]["cancel-in-progress"], context), \
        "CI_MATERIAL_EVENT_CAN_REPLACE_CURRENT_WORK"


@pytest.mark.parametrize("changes", METADATA_CHANGES)
@pytest.mark.parametrize("source_action", ("opened", "synchronize", "reopened"))
def test_same_candidate_metadata_queues_behind_source_validation(changes, source_action):
    concurrency = workflow()["concurrency"]
    source, metadata = pr_context(source_action), pr_context("edited", changes)
    assert expression(concurrency["group"], source) == expression(concurrency["group"], metadata), \
        "CI_METADATA_MUST_WAIT_IN_THE_SOURCE_CANDIDATE_GROUP"
    assert not expression(concurrency["cancel-in-progress"], metadata), \
        "CI_METADATA_MUST_NOT_CANCEL_THE_RUNNING_PRODUCER"
    assert expression(concurrency["cancel-in-progress"], source), \
        "CI_SOURCE_EVENTS_RETAIN_CANCELLATION"


def test_ready_transition_does_not_reexecute_the_full_ci_workflow():
    actions = workflow()["on"]["pull_request"]["types"]
    assert "ready_for_review" not in actions, "CI_READINESS_REUSES_EXISTING_VERIFICATION"
    assert {"opened", "synchronize", "reopened", "edited"} <= set(actions)


def test_delayed_old_source_and_metadata_cannot_cancel_current_validation():
    concurrency = workflow()["concurrency"]
    previous = pr_context("synchronize")
    new_head = pr_context("synchronize", head="c" * 40)
    new_base = pr_context("edited", {"base": {"from": "previous"}}, base="d" * 40)
    repeated = pr_context("reopened")
    metadata = pr_context("edited", {"body": {"from": "old"}})
    other_pr = pr_context("synchronize", number=18)
    metadata_new_head = pr_context("edited", {"body": {"from": "old"}}, head="c" * 40)
    metadata_new_base = pr_context("edited", {"body": {"from": "old"}}, base="d" * 40)
    other_repository = pr_context("synchronize")
    other_repository["github"]["repository_id"] = 78
    group = expression(concurrency["group"], previous)
    assert group != expression(concurrency["group"], new_head), "CI_DELAYED_OLD_HEAD_CANNOT_CANCEL_CURRENT_HEAD"
    assert group != expression(concurrency["group"], new_base), "CI_DELAYED_OLD_BASE_CANNOT_CANCEL_CURRENT_BASE"
    assert group == expression(concurrency["group"], repeated), "CI_IDENTICAL_CANDIDATE_EVENTS_SHARE_CANCELLATION"
    assert group == expression(concurrency["group"], metadata), "CI_METADATA_SERIALIZES_WITH_VERIFICATION"
    assert not expression(concurrency["cancel-in-progress"], metadata), "CI_METADATA_CANNOT_CANCEL_VERIFICATION"
    assert group != expression(concurrency["group"], other_pr)
    assert group != expression(concurrency["group"], other_repository)
    metadata_group = expression(concurrency["group"], metadata)
    assert metadata_group != expression(concurrency["group"], metadata_new_head), \
        "CI_DELAYED_OLD_METADATA_CANNOT_CANCEL_CURRENT_HEAD"
    assert metadata_group != expression(concurrency["group"], metadata_new_base), \
        "CI_DELAYED_OLD_METADATA_CANNOT_CANCEL_CURRENT_BASE"
    assert expression(concurrency["cancel-in-progress"], repeated), "CI_IDENTICAL_CANDIDATE_WORK_IS_CANCELLED"


def test_superseded_candidate_skips_heavy_work_but_cannot_publish_required_success():
    jobs, context = workflow()["jobs"], pr_context("synchronize")
    context["needs"]["paths"]["outputs"]["superseded"] = "true"
    for name in ("changes", "application-tests", "application", "verification", "tmux-acceptance", "package"):
        assert not expression(jobs[name]["if"], context), "CI_PROVEN_REPLACEMENT_SUPPRESSES_" + name
    assert expression(jobs["ci-result"]["if"], context), "CI_SUPERSEDED_CANDIDATE_REMAINS_UNSUCCESSFUL"
    assert expression(jobs["ci-result"]["name"], context) == "CI result"
