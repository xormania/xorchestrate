"""Documentation-only changes skip heavy CI jobs; every uncertainty runs the full pipeline."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.ci import classify_changes as subject
from tests.hexagonal.contracts import ci_changes as contract

ROOT = Path(__file__).resolve().parents[2]


def _git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True,
                          timeout=30).stdout.strip()


@pytest.fixture
def repository(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "master")
    _git(tmp_path, "config", "user.email", "ci@example.invalid")
    _git(tmp_path, "config", "user.name", "ci")
    _git(tmp_path, "config", "commit.gpgsign", "false")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/guide.md").write_text("guide\n")
    (tmp_path / "module.py").write_text("VALUE = 1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    return tmp_path, _git(tmp_path, "rev-parse", "HEAD")


def _commit(root, files, *, remove=()):
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    for name in remove:
        _git(root, "rm", "-q", name)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "change")


@pytest.mark.parametrize("path", contract.DOCUMENTATION_PATHS)
def test_allow_listed_prose_is_documentation(path):
    contract.documentation_only(subject.classify([path]), [path])


@pytest.mark.parametrize("path", contract.CODE_PATHS)
def test_anything_outside_the_allow_list_is_code(path):
    contract.full_run(subject.classify([path]), "non-documentation paths changed")


def test_one_code_path_makes_a_mixed_change_code_and_no_paths_is_uncertain():
    decision = subject.classify(["README.md", "docs/a.md", "xorchestrate/model.py"])
    contract.full_run(decision)
    assert decision["code_paths"] == ["xorchestrate/model.py"]
    contract.full_run(subject.classify([]), "no changed paths observed")


@pytest.mark.parametrize("event,kwargs,reason", [
    ("workflow_dispatch", {"pull_request_base": "a" * 40, "push_before": "a" * 40},
     "event always runs full validation"),
    ("schedule", {}, "event always runs full validation"),
    ("pull_request", {}, "comparison base unavailable"),
    ("push", {"push_before": subject.NULL_SHA}, "comparison base unavailable"),
    ("push", {"push_before": "not-a-sha"}, "comparison base unavailable"),
    ("push", {"pull_request_base": "a" * 40}, "comparison base unavailable"),
])
def test_events_without_a_trustworthy_base_run_everything(tmp_path, event, kwargs, reason):
    def unexpected(*args, **kwargs):
        raise AssertionError("No diff may be attempted without a comparison base")
    contract.full_run(subject.decide(event, root=tmp_path, runner=unexpected, **kwargs), reason)


def test_documentation_only_pull_request_and_push_diffs(repository):
    root, base = repository
    _commit(root, {"README.md": "intro\n", "docs/guide.md": "changed\n", "examples/notes.md": "n\n"})
    expected = ["README.md", "docs/guide.md", "examples/notes.md"]
    contract.documentation_only(subject.decide("pull_request", root=root, pull_request_base=base), expected)
    contract.documentation_only(subject.decide("push", root=root, push_before=base), expected)


def test_renames_and_deletions_count_both_sides(repository):
    root, base = repository
    _git(root, "mv", "module.py", "docs/module.md")
    _git(root, "commit", "-q", "-m", "move")
    decision = subject.decide("push", root=root, push_before=base)
    contract.full_run(decision)
    assert decision["code_paths"] == ["module.py"]
    second = _git(root, "rev-parse", "HEAD")
    _commit(root, {}, remove=["docs/guide.md"])
    contract.documentation_only(subject.decide("push", root=root, push_before=second), ["docs/guide.md"])


def test_unknown_base_or_failing_git_runs_everything(repository, tmp_path_factory):
    root, _ = repository
    missing = "1" * 40
    contract.full_run(subject.decide("push", root=root, push_before=missing), "changed paths could not be computed")
    outside = tmp_path_factory.mktemp("not-a-repository")
    contract.full_run(subject.decide("pull_request", root=outside, pull_request_base=missing),
                      "changed paths could not be computed")

    def unavailable(*args, **kwargs):
        raise OSError("git is not installed")
    contract.full_run(subject.decide("push", root=root, push_before=missing, runner=unavailable),
                      "changed paths could not be computed")


def test_command_writes_the_github_output(repository, tmp_path_factory):
    root, base = repository
    _commit(root, {"docs/guide.md": "changed\n"})
    output = tmp_path_factory.mktemp("output") / "github_output"
    command = [sys.executable, "-B", "-m", "scripts.ci.classify_changes", "--root", str(root),
               "--event", "pull_request", "--pull-request-base", base, "--github-output", str(output)]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["code"] is False
    assert output.read_text() == "code=false\n"
    dispatch = subprocess.run([*command[:-6], "--event", "workflow_dispatch", "--github-output", str(output)],
                              cwd=ROOT, capture_output=True, text=True, check=False, timeout=30)
    assert dispatch.returncode == 0, dispatch.stderr
    assert output.read_text() == "code=false\ncode=true\n"


def test_workflow_gates_only_heavy_jobs_on_the_classifier():
    workflow = json.loads((ROOT / ".github/workflows/ci.yml").read_text())
    jobs = workflow["jobs"]
    classifier = jobs[contract.CLASSIFIER_JOB]
    assert classifier["outputs"]["code"] == "${{ steps.classify.outputs.code }}"
    assert not classifier.get("needs") and not classifier.get("if")
    checkout = classifier["steps"][0]
    assert checkout["with"]["fetch-depth"] == 0 and checkout["with"]["persist-credentials"] is False
    step = next(step for step in classifier["steps"] if step.get("id") == "classify")
    assert "-m scripts.ci.classify_changes" in step["run"] and "$GITHUB_OUTPUT" in step["run"]
    assert step["env"]["PULL_REQUEST_BASE"] == "${{ github.event.pull_request.base.sha }}"
    assert step["env"]["PUSH_BEFORE"] == "${{ github.event.before }}"
    for name in contract.HEAVY_JOBS:
        contract.heavy_job_gate(jobs[name])
    contract.always_running(jobs["changes"], needs=[])
    contract.always_running(jobs["architecture-status"], needs=["changes"])
    assert set(jobs) == {contract.CLASSIFIER_JOB, *contract.HEAVY_JOBS, *contract.ALWAYS_JOBS}
