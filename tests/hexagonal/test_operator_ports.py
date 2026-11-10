"""Operator ports: companion, version control and repository hosting adapters, their selection and doctor's use."""

import json
import socket
import stat

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
    assert companion.validate_location() == f"it is inside the project at {project.repo}"
    assert GitDirectoryCompanion({"path": str(tmp_path / "outside")}, version_control=GitCLI()).validate_location() is None
    with pytest.raises(OrchestrationError) as refused:
        operations.companion_init(inside, {"XORCHESTRATE_HOME": str(tmp_path / "h")})
    assert refused.value.code == "companion_exposed" and refused.value.details["location"] == str(inside)


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


def test_new_companions_are_private_directories(tmp_path):
    root = tmp_path / "private"
    GitDirectoryCompanion({"path": str(root)}, version_control=GitCLI()).initialize()
    assert stat.S_IMODE(root.stat().st_mode) == 0o700


def test_a_companion_others_can_write_is_refused_when_registered_and_when_opened(project, tmp_path):
    environ = project.provisioned.environment
    shared = tmp_path / "shared-companion"
    GitDirectoryCompanion({"path": str(shared)}, version_control=GitCLI()).initialize()
    shared.chmod(0o777)
    with pytest.raises(OrchestrationError) as registered:
        operations.companion_use(shared, environ)
    assert registered.value.code == "companion_exposed"
    assert "writable by group or others" in registered.value.details["reason"]
    # A companion that became writable by others after registration is refused on every open.
    project.companion.chmod(0o770)
    error = json.loads(project.run("describe").stderr)["error"]
    assert error["code"] == "companion_exposed" and error["details"]["location"] == str(project.companion)
    project.companion.chmod(0o700)
    assert project.run("describe").returncode == 0
    loose = tmp_path / "loose"
    loose.mkdir()
    loose.chmod(0o777)
    with pytest.raises(OrchestrationError) as enclosing:
        operations.companion_init(loose / "inner", {"XORCHESTRATE_HOME": str(tmp_path / "h")})
    assert "enclosing directory" in enclosing.value.details["reason"]


def test_a_companion_nested_in_its_own_repository_inside_a_project_is_refused(project, tmp_path):
    nested = project.repo / "vendor" / "companion"
    nested.mkdir(parents=True)
    git("init", "-q", str(nested), cwd=project.repo)
    companion = GitDirectoryCompanion({"path": str(nested)}, version_control=GitCLI())
    assert companion.validate_location() == f"it is inside the project at {project.repo}"
    with pytest.raises(OrchestrationError) as refused:
        operations.companion_init(nested, {"XORCHESTRATE_HOME": str(tmp_path / "h")})
    assert refused.value.code == "companion_exposed"
    # Registered behind the check's back, it is refused when the project opens it.
    companion.initialize()
    Home(project.home).update_settings(companion={"adapter": "git-directory", "options": {"path": str(nested)}})
    error = json.loads(project.run("describe").stderr)["error"]
    assert error["code"] == "companion_exposed"


def test_github_adapter_refuses_plain_http_except_loopback():
    for url in ("http://api.github.example", "http://10.0.0.1:8080", "ftp://127.0.0.1"):
        with pytest.raises(OrchestrationError) as refused:
            GitHubHosting({"api_url": url})
        assert refused.value.code == "invalid_configuration", url
    for url in ("https://ghe.example/api/v3", "http://127.0.0.1:9", "http://[::1]:9", "http://localhost:9"):
        GitHubHosting({"api_url": url})


def test_github_adapter_never_follows_a_redirect_with_the_token(monkeypatch):
    monkeypatch.setenv("XO_TEST_GITHUB_TOKEN", "test-token")
    elsewhere = []
    with github_api({"owner/companion": True}, requests=elsewhere) as other:
        with github_api({}, redirects={"owner/companion": f"{other}/repos/owner/companion"}) as api:
            report = GitHubHosting({"api_url": api, "token_variables": ["XO_TEST_GITHUB_TOKEN"],
                                    "token_command": []}).visibility("https://github.com/owner/companion.git")
    assert report.visibility == "unknown" and report.reason == "redirect not followed"
    assert elsewhere == [], "the token was not sent to the redirect target"


def test_an_xorchestrate_home_others_can_replace_is_refused(tmp_path):
    loose = tmp_path / "loose"
    loose.mkdir()
    loose.chmod(0o777)
    for home in (loose / "home", loose):
        with pytest.raises(OrchestrationError) as refused:
            Home.locate({"XORCHESTRATE_HOME": str(home)})
        assert refused.value.code == "invalid_configuration" and "unsafe" in str(refused.value)
    # Through the CLI, before any setting (such as a version-control program) is used.
    project = ProjectFixture(tmp_path / "p")
    project.home.chmod(0o755)
    project.home.chmod(0o775)
    error = json.loads(project.run("describe").stderr)["error"]
    assert error["code"] == "invalid_configuration" and str(project.home) in error["message"]
    project.home.chmod(0o700)
    assert project.run("describe").returncode == 0
    # The account's default home stays usable.
    assert Home.locate({}).root.name == ".xorchestrate"


def test_home_documents_must_be_private_regular_files(project):
    settings = project.home / "settings.json"
    settings.chmod(0o644)
    error = json.loads(project.run("describe").stderr)["error"]
    assert error["code"] == "invalid_configuration"
    settings.chmod(0o600)
    moved = project.home / "real-settings.json"
    settings.rename(moved)
    settings.symlink_to(moved)
    assert json.loads(project.run("describe").stderr)["error"]["code"] == "invalid_configuration"
