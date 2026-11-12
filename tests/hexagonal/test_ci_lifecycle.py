"""Workflow event scenarios preserve required checks while removing metadata churn."""

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
def test_metadata_edits_do_not_start_validation_or_replace_required_check_names(changes):
    jobs, context = workflow()["jobs"], pr_context("edited", changes)
    for name in VALIDATION_JOBS:
        job = jobs[name]
        assert expression(job.get("if", True), context) is False, "CI_METADATA_DOES_NOT_START_" + name
        assert expression(job.get("name", name), context) not in REQUIRED_NAMES, \
            "CI_METADATA_CANNOT_REPLACE_REQUIRED_CHECK_WITH_SKIPPED_GREEN"
    active = [name for name, job in jobs.items() if expression(job.get("if", True), context)]
    assert len(active) == 1, "CI_METADATA_HAS_ONE_LIGHTWEIGHT_OBSERVER"
    assert not any("scripts.testkit" in step.get("run", "") for step in jobs[active[0]].get("steps", []))


@pytest.mark.parametrize("action,changes", VALIDATION_EVENTS)
def test_code_unknown_and_base_edit_events_keep_full_candidate_validation(action, changes):
    jobs, context = workflow()["jobs"], pr_context(action, changes)
    for name in VALIDATION_JOBS:
        assert expression(jobs[name].get("if", True), context), "CI_MATERIAL_EVENT_VALIDATES_" + name
    assert expression(jobs["ci-result"]["name"], context) == "CI result"
    assert expression(jobs["architecture-status"]["name"], context) == "Hexagonal architecture"


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
    group = expression(concurrency["group"], previous)
    assert group != expression(concurrency["group"], new_head), "CI_DELAYED_OLD_HEAD_CANNOT_CANCEL_CURRENT_HEAD"
    assert group != expression(concurrency["group"], new_base), "CI_DELAYED_OLD_BASE_CANNOT_CANCEL_CURRENT_BASE"
    assert group == expression(concurrency["group"], repeated), "CI_IDENTICAL_CANDIDATE_EVENTS_SHARE_CANCELLATION"
    assert group != expression(concurrency["group"], metadata), "CI_METADATA_CANNOT_CANCEL_VERIFICATION"
    assert group != expression(concurrency["group"], other_pr)
    assert expression(concurrency["cancel-in-progress"], repeated), "CI_IDENTICAL_CANDIDATE_WORK_IS_CANCELLED"


def test_superseded_candidate_skips_heavy_work_but_cannot_publish_required_success():
    jobs, context = workflow()["jobs"], pr_context("synchronize")
    context["needs"]["paths"]["outputs"]["superseded"] = "true"
    for name in ("changes", "application-tests", "application", "verification", "tmux-acceptance", "package"):
        assert not expression(jobs[name]["if"], context), "CI_PROVEN_REPLACEMENT_SUPPRESSES_" + name
    assert expression(jobs["ci-result"]["if"], context), "CI_SUPERSEDED_CANDIDATE_REMAINS_UNSUCCESSFUL"
    assert expression(jobs["ci-result"]["name"], context) == "CI result"
