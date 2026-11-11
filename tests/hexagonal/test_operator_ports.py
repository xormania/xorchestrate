"""Operator ports: companion, version control and repository hosting adapters, their selection and doctor's use."""

import json
import socket
import stat

import pytest
from xcontracts.testing import operator as contract

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

@pytest.mark.parametrize("check", [contract.companion_records, contract.companion_scopes_are_isolated], ids=str)
@pytest.mark.parametrize("subject", ["git-directory", "memory"])
def test_companion_adapters_keep_project_scoped_extensible_records(subject, check, tmp_path):
    companion = (GitDirectoryCompanion({"path": str(tmp_path / "companion")}, version_control=GitCLI())
                 if subject == "git-directory" else MemoryCompanion())
    check(companion)


def _refused(call):
    with pytest.raises(OrchestrationError) as refused:
        call()
    assert refused.value.code == "invalid_arguments"


def test_git_directory_kinds_keep_one_scope_and_say_so_on_disk(tmp_path):
    """The reproduced mixes: a shared ``lib/...`` tree, a shared listing of projects, a shared record named like one."""
    companion = GitDirectoryCompanion({"path": str(tmp_path / "c")}, version_control=GitCLI())
    companion.initialize()
    shared, lib, beta = companion.records(None), companion.records("lib"), companion.records("beta")
    shared.write("launchers", "lib/helper.sh", b"#!/bin/sh\n")
    assert (tmp_path / "c" / "launchers" / ".scope").read_text() == "shared\n"
    _refused(lambda: lib.read("launchers", "helper.sh"))
    _refused(lambda: lib.keys("launchers", recursive=True))
    _refused(lambda: lib.write("launchers", "helper.sh", b"x"))
    beta.write("telemetry", "i/b/manifest.json", b"{}")
    _refused(lambda: shared.keys("telemetry", recursive=True))
    _refused(lambda: shared.write("telemetry", "beta", b"x"))
    assert beta.read("telemetry", "i/b/manifest.json") == b"{}"
    assert shared.keys("launchers", recursive=True) == ["lib/helper.sh"], "the marker is never a record"


def test_unmarked_kind_directories_from_earlier_releases_keep_their_scope(tmp_path):
    root = tmp_path / "c"
    companion = GitDirectoryCompanion({"path": str(root)}, version_control=GitCLI())
    companion.initialize()
    (root / "overlay" / "alpha").mkdir()
    (root / "overlay" / "alpha" / "overlay.json").write_bytes(b"{}")
    (root / "policies" / "team.json").write_bytes(b"{}")
    _refused(lambda: companion.records(None).write("overlay", "alpha/overlay.json", b"x"))
    _refused(lambda: companion.records("team").read("policies", "team.json"))
    assert companion.records("alpha").read("overlay", "overlay.json") == b"{}"
    assert companion.records(None).keys("policies") == ["team.json"]


def test_an_interrupted_create_only_write_leaves_no_record(tmp_path, monkeypatch):
    """A crash mid-write must not leave a truncated record that ``create_only`` then keeps forever."""
    import xorchestrate.adapters.companion_git as module
    companion = GitDirectoryCompanion({"path": str(tmp_path / "c")}, version_control=GitCLI())
    companion.initialize()
    records = companion.records("app")
    records.write("overlay", "other.json", b"{}")  # the kind's scope is already recorded
    real = module.os.fdopen

    class Failing:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            self.stream.close()

        def write(self, data):
            self.stream.write(data[:1])
            raise OSError("disk full")
    monkeypatch.setattr(module.os, "fdopen", lambda descriptor, mode="r", **kw: Failing(real(descriptor, mode, **kw)))
    with pytest.raises(OSError):
        records.write("overlay", "overlay.json", b"complete", create_only=True)
    monkeypatch.undo()
    assert records.read("overlay", "overlay.json") is None
    assert records.write("overlay", "overlay.json", b"complete", create_only=True) is True
    assert records.read("overlay", "overlay.json") == b"complete"
    assert sorted(path.name for path in (tmp_path / "c" / "overlay" / "app").iterdir()) == ["other.json",
                                                                                          "overlay.json"]


def test_concurrent_writes_of_one_record_use_their_own_temporary_files(tmp_path, monkeypatch):
    import threading

    import xorchestrate.adapters.companion_git as module
    companion = GitDirectoryCompanion({"path": str(tmp_path / "c")}, version_control=GitCLI())
    companion.initialize()
    real, together = module.os.replace, threading.Barrier(2, timeout=10)

    def replace(source, target):
        together.wait()  # both writers have written their temporary file before either replaces
        return real(source, target)
    monkeypatch.setattr(module.os, "replace", replace)
    errors = []

    def write(data):
        try:
            companion.records(None).write("policies", "team.json", data)
        except Exception as error:  # noqa: BLE001 - recorded for the assertion
            errors.append(error)
    threads = [threading.Thread(target=write, args=(data,)) for data in (b"one", b"two")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    monkeypatch.undo()
    assert errors == []
    assert companion.records(None).read("policies", "team.json") in (b"one", b"two")
    assert sorted(path.name for path in (tmp_path / "c" / "policies").iterdir()) == [".scope", "team.json"]


def test_subdirectories_of_flat_companion_kinds_are_not_records(tmp_path):
    """A launcher library or a parked policy in a subdirectory is neither copied nor merged."""
    project = ProjectFixture(tmp_path / "project")
    (project.companion / "launchers" / "lib").mkdir(parents=True)
    (project.companion / "launchers" / "lib" / "helper.sh").write_text("#!/bin/sh\n")
    (project.companion / "launchers" / "tool").write_text("#!/bin/sh\n")
    (project.companion / "policies" / "disabled").mkdir(parents=True)
    (project.companion / "policies" / "disabled" / "old.json").write_text(json.dumps({"guardrail": {
        "adapter": "rules", "options": {"default": "deny"}}}))
    prepared = project.prepared()
    assert sorted(path.name for path in (project.data_dir / "launchers").iterdir()) == ["tool"]
    assert "guardrail" not in prepared.config


def test_git_directory_records_refuse_a_symbolically_linked_kind(tmp_path):
    companion = GitDirectoryCompanion({"path": str(tmp_path / "c")}, version_control=GitCLI())
    companion.initialize()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (tmp_path / "c" / "telemetry").symlink_to(elsewhere)
    with pytest.raises(OrchestrationError) as refused:
        companion.records("app").write("telemetry", "i/b/manifest.json", b"{}")
    assert refused.value.code == "invalid_arguments"
    with pytest.raises(OrchestrationError):
        companion.records("app").read("telemetry", "i/b/manifest.json")
    assert list(elsewhere.iterdir()) == [], "nothing was written through the link"


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
                                                            "roots": ["abc"], "head": "abc"}}})
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


def test_operator_factories_get_a_private_copy_of_nested_options():
    """A factory that changes its options cannot change the user's stored selection."""
    from xorchestrate.instance.services import construct
    selection = {"adapter": f"{SUPPORT}:MutatingHosting", "options": {"visibility": {"https://example.test/a": "private"}}}
    construct("repository_hosting", selection, "settings.json")
    assert selection == {"adapter": f"{SUPPORT}:MutatingHosting",
                         "options": {"visibility": {"https://example.test/a": "private"}}}


def test_github_adapter_names_the_repository_it_reports(hosted, monkeypatch):
    monkeypatch.setenv("XO_TEST_GITHUB_TOKEN", "test-token")
    hosting = GitHubHosting({"api_url": hosted, "token_variables": ["XO_TEST_GITHUB_TOKEN"], "token_command": []})
    for url in ("git@github.com:owner/private-companion.git", "https://github.com/owner/private-companion"):
        report = hosting.visibility(url)
        assert (report.visibility, report.repository) == ("private", "owner/private-companion")


def test_github_adapter_reports_unknown_for_a_malformed_response(monkeypatch):
    """A bad status line raises http.client.HTTPException; doctor must still get an answer, not a crash."""
    import threading
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)

    def answer():
        connection, _ = server.accept()
        with connection:
            connection.recv(65536)
            connection.sendall(b"NOT-HTTP garbage\r\n\r\n")
    thread = threading.Thread(target=answer, daemon=True)
    thread.start()
    monkeypatch.setenv("XO_TEST_GITHUB_TOKEN", "test-token")
    try:
        hosting = GitHubHosting({"api_url": f"http://127.0.0.1:{server.getsockname()[1]}",
                                 "token_variables": ["XO_TEST_GITHUB_TOKEN"], "token_command": []})
        report = hosting.visibility("https://github.com/owner/repo")
    finally:
        thread.join(10)
        server.close()
    assert (report.visibility, report.repository) == ("unknown", "owner/repo") and report.reason


def test_github_adapter_refuses_a_non_string_api_url():
    with pytest.raises(OrchestrationError) as refused:
        GitHubHosting({"api_url": 5})
    assert refused.value.code == "invalid_configuration"


@pytest.mark.parametrize("adapter", ["missing_module_for_test:Factory", "json:NoSuchFactory"])
def test_an_unimportable_operator_reference_is_a_configuration_error(adapter):
    from xorchestrate.instance.services import construct
    with pytest.raises(OrchestrationError) as refused:
        construct("repository_hosting", {"adapter": adapter}, "settings.json")
    assert refused.value.code == "configuration_error"


def test_git_adapter_answers_for_files_new_entries_and_metadata_as_the_port_states(tmp_path):
    repository = tmp_path / "repo"
    git("init", "-q", str(repository), cwd=tmp_path)
    git("commit", "-q", "--allow-empty", "-m", "root", cwd=repository)
    git("remote", "add", "origin", "https://example.test/repo.git", cwd=repository)
    (repository / "src").mkdir()
    (repository / "src" / "file.py").write_text("")
    vcs = GitCLI()
    head, roots = vcs.revision(str(repository)), vcs.root_commits(str(repository))
    assert head and roots
    for inside in (repository / "src" / "file.py", repository / "src" / "new.py"):
        assert vcs.worktree(str(inside)).root == str(repository.resolve())
        assert (vcs.revision(str(inside)), vcs.root_commits(str(inside))) == (head, roots)
        assert vcs.remote_url(str(inside)) == "https://example.test/repo.git"
    missing = repository / "missing" / "deeper" / "x"
    assert (vcs.worktree(str(missing)), vcs.revision(str(missing)), vcs.remote_url(str(missing))) == (None, None, None)
    assert vcs.root_commits(str(missing)) == frozenset()
    metadata = repository / ".git"
    assert vcs.worktree(str(metadata)) is None
    assert (vcs.revision(str(metadata)), vcs.root_commits(str(metadata))) == (head, roots)


def test_git_adapter_reports_a_nested_repository_as_the_innermost(tmp_path):
    outer, inner = tmp_path / "outer", tmp_path / "outer" / "vendor" / "inner"
    git("init", "-q", str(outer), cwd=tmp_path)
    git("commit", "-q", "--allow-empty", "-m", "outer", cwd=outer)
    inner.mkdir(parents=True)
    git("init", "-q", str(inner), cwd=tmp_path)
    git("commit", "-q", "--allow-empty", "-m", "inner", cwd=inner)
    vcs = GitCLI()
    assert vcs.worktree(str(inner / "file.py")).root == str(inner.resolve())
    assert vcs.root_commits(str(inner)) != vcs.root_commits(str(outer))
    assert vcs.revision(str(inner)) != vcs.revision(str(outer))
