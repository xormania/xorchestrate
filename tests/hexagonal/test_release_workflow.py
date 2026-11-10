"""The release workflow gates publishing on identity and installed-package checks with least privilege."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RELEASE = json.loads((ROOT / ".github/workflows/release.yml").read_text())
CI = json.loads((ROOT / ".github/workflows/ci.yml").read_text())
JOBS = RELEASE["jobs"]
PINNED = re.compile(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}")


def steps(job):
    return JOBS[job]["steps"]


def uses(job):
    return [step["uses"] for step in steps(job) if "uses" in step]


def test_release_tags_and_manual_dry_runs_trigger_the_workflow():
    assert RELEASE["on"] == {"push": {"tags": ["v*.*.*"]}, "workflow_dispatch": {}}
    assert RELEASE["concurrency"]["cancel-in-progress"] is False


def test_every_action_is_pinned_to_a_full_commit():
    actions = [action for job in JOBS for action in uses(job)]
    assert actions and all(PINNED.fullmatch(action) for action in actions), actions
    assert any(action.startswith("pypa/gh-action-pypi-publish@") for action in actions)


def test_shared_actions_and_runtime_match_ci():
    ci_actions = {step["uses"].split("@")[0]: step["uses"] for job in CI["jobs"].values()
                  for step in job["steps"] if "uses" in step}
    for job in JOBS:
        for action in uses(job):
            name = action.split("@")[0]
            assert name not in ci_actions or ci_actions[name] == action, action
        for step in steps(job):
            if step.get("uses", "").startswith("astral-sh/setup-uv@"):
                assert step["with"]["version"] == "0.13.7"
            assert "--python 3.12.16" in step.get("run", "") or "uv sync" not in step.get("run", "")


def test_permissions_are_least_privilege_and_only_publish_has_an_id_token():
    assert RELEASE["permissions"] == {}
    assert {job: JOBS[job]["permissions"] for job in JOBS} == {
        "build": {"contents": "read"}, "check": {"contents": "read"},
        "publish": {"id-token": "write"}, "github-release": {"contents": "write"}}
    for job in JOBS:
        for step in steps(job):
            if step.get("uses", "").startswith("actions/checkout@"):
                assert step["with"]["persist-credentials"] is False


def test_publishing_waits_for_identity_and_acceptance_and_uses_trusted_publishing():
    assert JOBS["check"]["needs"] == ["build"]
    assert set(JOBS["publish"]["needs"]) == {"build", "check"}
    assert JOBS["publish"]["environment"]["name"] == "${{ github.event_name == 'push' && 'pypi' || 'testpypi' }}"
    publish = next(step for step in steps("publish") if step.get("uses", "").startswith("pypa/"))
    assert set(publish["with"]) == {"packages-dir", "repository-url"}
    assert "password" not in json.dumps(RELEASE) and "secrets." not in json.dumps(RELEASE)
    identity = next(step for step in steps("build") if step.get("id") == "identity")
    assert "scripts/ci/release_identity.py" in identity["run"] and "--dist" in identity["run"]
    assert "--tag \"$TAG\" --annotated --ancestor-of" in identity["run"]
    acceptance = next(step for step in steps("check") if "package_acceptance" in step.get("run", ""))
    assert "--artifact" in acceptance["run"]


def test_the_dry_run_targets_testpypi_and_only_tags_reach_pypi_and_github_releases():
    index = next(step for step in steps("build") if step.get("id") == "index")["run"]
    assert index.index('"$EVENT_NAME" = push') < index.index("$PYPI_UPLOAD_URL") < index.index("$TESTPYPI_UPLOAD_URL")
    assert RELEASE["env"]["TESTPYPI_UPLOAD_URL"] == "https://test.pypi.org/legacy/"
    assert RELEASE["env"]["PYPI_UPLOAD_URL"] == "https://upload.pypi.org/legacy/"
    assert JOBS["github-release"]["if"] == "${{ github.event_name == 'push' }}"
    assert set(JOBS["github-release"]["needs"]) == {"build", "publish"}


def test_the_alias_is_built_checked_accepted_and_published_with_the_package():
    assert RELEASE["env"]["ALIAS_PROJECT"] == "packaging/xork"
    assert (ROOT / RELEASE["env"]["ALIAS_PROJECT"] / "pyproject.toml").is_file()
    build = next(step for step in steps("build") if "-m build" in step.get("run", ""))["run"]
    assert 'for project in . "$ALIAS_PROJECT"; do' in build and '--outdir "$RUNNER_TEMP/dist" "$project"' in build
    identity = next(step for step in steps("build") if step.get("id") == "identity")["run"]
    assert '--alias "$ALIAS_PROJECT"' in identity and '--dist "$RUNNER_TEMP/dist"' in identity
    # Acceptance gets every built file, the alias's included; publishing uploads the whole directory.
    acceptance = next(step for step in steps("check") if "package_acceptance" in step.get("run", ""))["run"]
    assert 'for artifact in "$RUNNER_TEMP"/dist/*.whl "$RUNNER_TEMP"/dist/*.tar.gz; do' in acceptance
    publish = next(step for step in steps("publish") if step.get("uses", "").startswith("pypa/"))
    assert publish["with"]["packages-dir"] == "dist/"
    download = next(step for step in steps("publish") if step.get("uses", "").startswith("actions/download-artifact@"))
    assert download["with"] == {"name": "distributions", "path": "dist"}
    assert [step for step in steps("publish") if "uses" in step] == [download, publish]
