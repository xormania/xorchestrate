"""Documentation-only pull requests skip heavy CI jobs; every uncertainty runs the full pipeline."""

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


def _commit(root, files=None, *, remove=()):
    for name, text in (files or {}).items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    for name in remove:
        _git(root, "rm", "-q", name)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "change")
    return _git(root, "rev-parse", "HEAD")


def _pull_request(root, base):
    return subject.decide("pull_request", root=root, pull_request_base=base)


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
    special = subject.classify(["docs/a.md"], special=["docs/link"])
    contract.full_run(special)
    assert special["code_paths"] == ["docs/link"]


@pytest.mark.parametrize("event,kwargs,reason", [
    ("workflow_dispatch", {"pull_request_base": "a" * 40}, "event always runs full validation"),
    ("push", {"pull_request_base": "a" * 40}, "event always runs full validation"),
    ("schedule", {}, "event always runs full validation"),
    ("pull_request", {}, "comparison base unavailable"),
    ("pull_request", {"pull_request_base": subject.NULL_SHA}, "comparison base unavailable"),
    ("pull_request", {"pull_request_base": "not-a-sha"}, "comparison base unavailable"),
])
def test_events_without_a_trustworthy_base_run_everything(tmp_path, event, kwargs, reason):
    def unexpected(*args, **kwargs):
        raise AssertionError("No diff may be attempted without a pull request comparison base")
    contract.full_run(subject.decide(event, root=tmp_path, runner=unexpected, **kwargs), reason)


def test_documentation_only_pull_request_diff(repository):
    root, base = repository
    _commit(root, {"README.md": "intro\n", "docs/guide.md": "changed\n", "examples/notes.md": "n\n"})
    contract.documentation_only(_pull_request(root, base), ["README.md", "docs/guide.md", "examples/notes.md"])
    # A push of the same documentation-only change still validates the integrated tree.
    contract.full_run(subject.decide("push", root=root, pull_request_base=base), "event always runs full validation")


def test_renames_and_deletions_count_both_sides(repository):
    root, base = repository
    _git(root, "mv", "module.py", "docs/module.md")
    _git(root, "commit", "-q", "-m", "move")
    decision = _pull_request(root, base)
    contract.full_run(decision)
    assert decision["code_paths"] == ["module.py"]
    second = _git(root, "rev-parse", "HEAD")
    _commit(root, remove=["docs/guide.md"])
    contract.documentation_only(_pull_request(root, second), ["docs/guide.md"])


@pytest.mark.parametrize("target", ["../missing", ".."])
def test_symlinks_under_docs_are_code(repository, target):
    root, base = repository
    (root / "docs/link").symlink_to(target)
    _commit(root)
    decision = _pull_request(root, base)
    contract.full_run(decision, "non-documentation paths changed")
    assert decision["code_paths"] == ["docs/link"]
    # Replacing the link with a regular file also changes a non-regular entry.
    linked = _git(root, "rev-parse", "HEAD")
    (root / "docs/link").unlink()
    _commit(root, {"docs/link": "now a file\n"})
    contract.full_run(_pull_request(root, linked), "non-documentation paths changed")


def test_gitlinks_under_docs_are_code(repository):
    root, base = repository
    _git(root, "update-index", "--add", "--cacheinfo", "160000," + base + ",docs/vendor")
    _git(root, "commit", "-q", "-m", "gitlink")
    decision = _pull_request(root, base)
    contract.full_run(decision, "non-documentation paths changed")
    assert decision["code_paths"] == ["docs/vendor"]


def test_executable_documentation_remains_documentation(repository):
    root, base = repository
    (root / "docs/run.md").write_text("x\n")
    (root / "docs/run.md").chmod(0o755)
    _commit(root)
    contract.documentation_only(_pull_request(root, base), ["docs/run.md"])


@pytest.mark.parametrize("output", [b":100644 100644 a b M\0", b"garbage\0docs/a.md\0", b":1 2\0docs/a.md\0"])
def test_malformed_raw_diff_runs_everything(repository, output):
    root, base = repository

    def malformed(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, output, b"")
    contract.full_run(subject.decide("pull_request", root=root, pull_request_base=base, runner=malformed),
                      "changed paths could not be computed")


def test_unknown_base_or_failing_git_runs_everything(repository, tmp_path_factory):
    root, _ = repository
    missing = "1" * 40
    contract.full_run(_pull_request(root, missing), "changed paths could not be computed")
    outside = tmp_path_factory.mktemp("not-a-repository")
    contract.full_run(_pull_request(outside, missing), "changed paths could not be computed")

    def unavailable(*args, **kwargs):
        raise OSError("git is not installed")
    contract.full_run(subject.decide("pull_request", root=root, pull_request_base=missing, runner=unavailable),
                      "changed paths could not be computed")


def test_command_writes_the_github_output(repository, tmp_path_factory):
    root, base = repository
    _commit(root, {"docs/guide.md": "changed\n"})
    output = tmp_path_factory.mktemp("output") / "github_output"
    prefix = [sys.executable, "-B", "-m", "scripts.ci.classify_changes", "--root", str(root),
              "--github-output", str(output)]
    result = subprocess.run([*prefix, "--event", "pull_request", "--pull-request-base", base], cwd=ROOT,
                            capture_output=True, text=True, check=False, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["code"] is False
    assert output.read_text() == "code=false\n"
    dispatch = subprocess.run([*prefix, "--event", "workflow_dispatch"], cwd=ROOT, capture_output=True,
                              text=True, check=False, timeout=30)
    assert dispatch.returncode == 0, dispatch.stderr
    assert output.read_text() == "code=false\ncode=true\n"


def _jobs():
    return json.loads((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]


def test_workflow_gates_only_heavy_jobs_on_the_classifier():
    jobs = _jobs()
    classifier = jobs[contract.CLASSIFIER_JOB]
    contract.classifier_output(classifier["outputs"]["code"])
    assert not classifier.get("needs") and not classifier.get("if")
    checkout = classifier["steps"][0]
    assert checkout["with"]["fetch-depth"] == 0 and checkout["with"]["persist-credentials"] is False
    step = next(step for step in classifier["steps"] if step.get("id") == "classify")
    assert "python3 -I -B scripts/ci/classify_changes.py" in step["run"] and "$GITHUB_OUTPUT" in step["run"]
    assert step["env"]["PULL_REQUEST_BASE"] == "${{ github.event.pull_request.base.sha }}"
    for name in contract.HEAVY_JOBS:
        if name in contract.AGGREGATE_JOBS:
            contract.heavy_job_gate(jobs[name], contract.AGGREGATE_JOBS[name], status="always()")
        else:
            contract.heavy_job_gate(jobs[name])
    contract.always_running(jobs["changes"], needs=["paths"])
    assert jobs["changes"]["if"] == "${{ always() }}"
    contract.always_running(jobs["architecture-status"], needs=["changes"])
    assert set(jobs) == {contract.CLASSIFIER_JOB, *contract.HEAVY_JOBS, *contract.ALWAYS_JOBS}


def _guard(root, output, **env):
    step = next(step for step in _jobs()[contract.CLASSIFIER_JOB]["steps"] if step.get("id") == "guard")
    contract.independent_guard(step)
    environment = {"PATH": "/usr/bin:/bin", "RUNNER_TEMP": str(output.parent), "GITHUB_OUTPUT": str(output),
                   "EVENT_NAME": "pull_request", **env}
    result = subprocess.run(["/bin/bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]],
                            cwd=root, env=environment, capture_output=True, text=True, check=False, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    return output.read_text().splitlines()[-1]


@pytest.mark.parametrize("path", contract.GUARDED_PATHS)
def test_guard_forces_full_validation_independently_of_the_classifier(repository, tmp_path_factory, path):
    root, base = repository
    _commit(root, {path: "changed\n", "docs/guide.md": "changed\n"})
    output = tmp_path_factory.mktemp("guard") / "github_output"
    assert _guard(root, output, PULL_REQUEST_BASE=base) == "force=true"


def test_guard_allows_classification_only_for_an_inspected_pull_request(repository, tmp_path_factory):
    root, base = repository
    output = tmp_path_factory.mktemp("guard") / "github_output"
    assert _guard(root, output, PULL_REQUEST_BASE=base) == "force=true", "An empty diff is uncertain"
    _commit(root, {"docs/guide.md": "changed\n", "docs/tests/notes.md": "n\n"})
    assert _guard(root, output, PULL_REQUEST_BASE=base) == "force=false"
    assert _guard(root, output, PULL_REQUEST_BASE=base, EVENT_NAME="push") == "force=true"
    assert _guard(root, output, PULL_REQUEST_BASE="") == "force=true"
    assert _guard(root, output, PULL_REQUEST_BASE="1" * 40) == "force=true"


def test_guard_sees_the_source_side_of_a_rename(repository, tmp_path_factory):
    root, _ = repository
    base = _commit(root, {"tests/test_example.py": "def test_example():\n    assert True\n"})
    _git(root, "mv", "tests/test_example.py", "docs/test_example.py")
    _commit(root)
    output = tmp_path_factory.mktemp("guard") / "github_output"
    assert _guard(root, output, PULL_REQUEST_BASE=base) == "force=true"
