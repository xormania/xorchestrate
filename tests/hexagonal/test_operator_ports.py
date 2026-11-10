"""Operator ports: companion, version control and repository hosting adapters, their selection and doctor's use."""

import json
import socket

import pytest

from tests.hexagonal.contracts import operator_ports as contract
from tests.hexagonal.support.operator_ports import FakeHosting, FakeVersionControl, MemoryCompanion, github_api
from tests.hexagonal.support.projects import ProjectFixture, git
from xorchestrate.adapters.companion_git import GitDirectoryCompanion
from xorchestrate.adapters.git_cli import GitCLI
from xorchestrate.adapters.github import GitHubHosting
from xorchestrate.instance import companion as records
from xorchestrate.instance import operations
from xorchestrate.instance.home import Home
from xorchestrate.instance.instance import load_instance
from xorchestrate.instance.services import Services
from xorchestrate.model import OrchestrationError

SUPPORT = "tests.hexagonal.support.operator_ports"


# -- port contracts: each adapter and its fake -------------------------------------

@pytest.mark.parametrize("subject", ["git-directory", "memory"])
def test_companion_adapters_keep_project_scoped_extensible_records(subject, tmp_path):
    companion = (GitDirectoryCompanion({"path": str(tmp_path / "companion")}, version_control=GitCLI())
                 if subject == "git-directory" else MemoryCompanion())
    contract.companion_records(companion)


def test_git_directory_companion_files_follow_the_documented_layout(tmp_path):
    companion = GitDirectoryCompanion({"path": str(tmp_path / "c")}, version_control=GitCLI())
    companion.initialize()
    companion.records("app").write("overlay", "overlay.json", b"{}")
    companion.records(None).write("launchers", "tool", b"#!/bin/sh\n")
    assert (tmp_path / "c" / "overlay" / "app" / "overlay.json").read_bytes() == b"{}"
    assert (tmp_path / "c" / "launchers" / "tool").exists()


@pytest.mark.parametrize("subject", ["git", "fake"])
def test_version_control_adapters_identify_worktrees_and_history(subject, tmp_path):
    repository, linked, elsewhere = tmp_path / "repo", tmp_path / "linked", tmp_path / "elsewhere"
    elsewhere.mkdir()
    if subject == "git":
        git("init", "-q", str(repository), cwd=tmp_path)
        git("commit", "-q", "--allow-empty", "-m", "root", cwd=repository)
        git("worktree", "add", "-q", "-b", "topic", str(linked), cwd=repository)
        vcs = GitCLI()
    else:
        vcs = FakeVersionControl({"repositories": {"repo": {"worktrees": [str(repository), str(linked)],
                                                            "roots": ["abc"]}}})
    contract.version_control(vcs, repository, linked, elsewhere)


@pytest.fixture
def hosted():
    with github_api({"owner/private-companion": True, "owner/public-companion": False}) as api:
        yield api


@pytest.mark.parametrize("subject", ["github", "fake"])
def test_repository_hosting_adapters_report_visibility_or_unknown(subject, hosted, monkeypatch):
    private, public = "git@github.com:owner/private-companion.git", "https://github.com/owner/public-companion"
    missing, elsewhere = "https://github.com/owner/missing.git", "https://gitlab.example/owner/repo.git"
    if subject == "github":
        monkeypatch.setenv("XO_TEST_GITHUB_TOKEN", "test-token")
        hosting = GitHubHosting({"api_url": hosted, "token_variables": ["XO_TEST_GITHUB_TOKEN"], "token_command": []})
    else:
        hosting = FakeHosting({"visibility": {private: "private", public: "public"}})
    contract.repository_hosting(hosting, private, public, [missing, elsewhere])


def test_github_adapter_reports_unknown_without_a_token_or_a_network(hosted, monkeypatch):
    monkeypatch.delenv("XO_TEST_GITHUB_TOKEN", raising=False)
    url = "https://github.com/owner/private-companion.git"
    unauthenticated = GitHubHosting({"api_url": hosted, "token_variables": ["XO_TEST_GITHUB_TOKEN"],
                                     "token_command": []}).visibility(url)
    assert unauthenticated.visibility == "unknown" and "authenticated" in unauthenticated.reason
    monkeypatch.setenv("XO_TEST_GITHUB_TOKEN", "wrong-token")
    rejected = GitHubHosting({"api_url": hosted, "token_variables": ["XO_TEST_GITHUB_TOKEN"],
                              "token_command": []}).visibility(url)
    assert rejected.visibility == "unknown" and rejected.reason == "HTTP 401"
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        closed = f"http://127.0.0.1:{reserved.getsockname()[1]}"
    offline = GitHubHosting({"api_url": closed, "token_variables": ["XO_TEST_GITHUB_TOKEN"], "token_command": [],
                             "timeout": 2}).visibility(url)
    assert offline.visibility == "unknown" and "reached" in offline.reason


# -- selection ------------------------------------------------------------------------

def test_a_plain_companion_path_setting_is_migrated_to_an_adapter_selection(project):
    Home(project.home).update_settings(companion=str(project.companion))
    assert project.run("describe").returncode == 0
    settings = json.loads((project.home / "settings.json").read_text())
    assert settings["companion"] == {"adapter": "git-directory", "options": {"path": str(project.companion)}}


def test_a_project_companion_takes_precedence_over_the_machine_default(project, tmp_path):
    environ = project.provisioned.environment
    personal = tmp_path / "project-companion"
    report = operations.companion_init(personal, environ, project=project.repo)
    assert report["scope"] == "project" and report["project"] == "fixture"
    instance = json.loads((project.data_dir / "instance.json").read_text())
    assert instance["companion"] == {"adapter": "git-directory", "options": {"path": str(personal)}}
    settings = json.loads((project.home / "settings.json").read_text())
    assert settings["companion"]["options"]["path"] == str(project.companion), "the machine default is unchanged"
    assert ".xorchestrate" not in {path.name for path in personal.iterdir()}
    # The project's own companion has no approval yet, so the project no longer runs ...
    error = json.loads(project.run("describe").stderr)["error"]
    assert error["code"] == "approval_required"
    # ... until it is approved there; the machine default's approvals are not consulted.
    approved = operations.approve(project.repo, environ)
    assert approved["companion"] == str(personal)
    assert project.run("describe").returncode == 0
    services = Services(Home(project.home))
    assert records.approved_hashes(services.companion(), "fixture") != []
    assert len(records.approved_hashes(services.companion(load_instance(Home(project.home), "fixture")), "fixture")) == 1


def test_the_companion_adapter_refuses_its_own_unsafe_location(project, tmp_path):
    inside = project.repo / "notes" / "companion"
    companion = GitDirectoryCompanion({"path": str(inside)}, version_control=GitCLI())
    assert companion.validate_location() == f"it is inside the project worktree {project.repo}"
    assert GitDirectoryCompanion({"path": str(tmp_path / "outside")}, version_control=GitCLI()).validate_location() is None
    with pytest.raises(OrchestrationError) as refused:
        operations.companion_init(inside, {"XORCHESTRATE_HOME": str(tmp_path / "h")})
    assert refused.value.code == "companion_exposed" and refused.value.details == {"location": str(inside)}


# -- doctor reports the companion's remote ------------------------------------------------

@pytest.mark.parametrize(("answer", "status"), [("private", "ok"), ("public", "warning"), (None, "warning")])
def test_doctor_reports_whether_the_companion_remote_is_private(answer, status, tmp_path):
    project = ProjectFixture(tmp_path / "p")
    remote = "git@github.com:owner/companion.git"
    git("init", "-q", str(project.companion), cwd=tmp_path)
    git("remote", "add", "origin", remote, cwd=project.companion)
    Home(project.home).update_settings(repository_hosting={
        "adapter": f"{SUPPORT}:FakeHosting", "options": {"visibility": {remote: answer} if answer else {}}})
    checks = {check["check"]: check for check in project.json("doctor")["checks"]}
    reported = checks["companion_remote"]
    assert reported["status"] == status and reported["remote"] == remote
    assert reported["visibility"] == (answer or "unknown")
    if answer == "public":
        assert "PUBLIC" in reported["detail"]


def test_doctor_reports_a_companion_without_remote(project):
    checks = {check["check"]: check for check in project.json("doctor")["checks"]}
    assert checks["companion_remote"]["status"] == "ok" and checks["companion_remote"]["remote"] is None
    assert checks["companion"]["status"] == "ok"


def test_the_cli_selects_a_companion_for_one_project(project, tmp_path):
    other = tmp_path / "other-companion"
    created = project.json("companion", "init", str(other), "--project", str(project.repo), project=False)
    assert created["scope"] == "project" and created["companion"]["options"]["path"] == str(other)
    used = project.json("companion", "use", str(project.companion), project=False)
    assert used["scope"] == "machine"
