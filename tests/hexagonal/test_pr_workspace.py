"""Real Git witnesses for owned, recoverable PR preparation and non-force publication."""

import os
import subprocess
from unittest.mock import patch

import pytest

from tests.hexagonal.support.pr_operations import GitRepository
from tests.hexagonal.support.projects import git
from xorchestrate.adapters.pr_workspace import GitPRWorkspace
from xorchestrate.model import OrchestrationError


@pytest.fixture
def repository(tmp_path):
    return GitRepository(tmp_path)


def workspace(repository):
    return GitPRWorkspace(repository.options())


def test_current_branch_is_a_noop_without_an_empty_checkpoint(repository):
    source = repository.head(repository.feature)
    before = git("rev-list", "--count", "HEAD", cwd=repository.feature)
    result = workspace(repository).refresh("owned", source, source, [])
    assert result["status"] == "no_change"
    assert result["head"] == source
    assert git("rev-list", "--count", "HEAD", cwd=repository.feature) == before
    assert git("status", "--porcelain", cwd=repository.feature) == ""


def test_refresh_checkpoints_selected_work_and_preserves_unrelated_untracked_files(repository):
    source = repository.head(repository.feature)
    target = repository.advance_base()
    selected = repository.feature / "feature.txt"
    selected.write_text("intentional local work\n")
    untracked = repository.feature / "private.credentials"
    untracked.write_text("must remain outside the checkpoint\n")
    result = workspace(repository).refresh("owned", source, target, ["feature.txt"])
    assert result["status"] == "updated"
    assert result["checkpoint"]
    assert selected.read_text() == "intentional local work\n"
    assert (repository.feature / "base.txt").read_text() == "target change\n"
    assert untracked.read_text() == "must remain outside the checkpoint\n"
    assert git("diff-tree", "--no-commit-id", "--name-only", "-r", result["checkpoint"], cwd=repository.feature) == "feature.txt"
    assert git("ls-files", "--", "private.credentials", cwd=repository.feature) == ""
    assert git("merge-base", "--is-ancestor", target, "HEAD", cwd=repository.feature) == ""


def test_unrelated_staged_bytes_are_not_silently_included_or_discarded(repository):
    source = repository.head(repository.feature)
    target = repository.advance_base()
    (repository.feature / "feature.txt").write_text("selected change\n")
    (repository.feature / "shared.txt").write_text("another writer's staged content\n")
    git("add", "--", "shared.txt", cwd=repository.feature)
    index = git("write-tree", cwd=repository.feature)
    with pytest.raises(OrchestrationError):
        workspace(repository).refresh("owned", source, target, ["feature.txt"])
    assert repository.head(repository.feature) == source
    assert git("write-tree", cwd=repository.feature) == index
    assert (repository.feature / "shared.txt").read_text() == "another writer's staged content\n"
    assert (repository.feature / "feature.txt").read_text() == "selected change\n"


def test_conflicted_refresh_keeps_checkpoint_and_recoverable_merge_index(repository):
    source = repository.head(repository.feature)
    target = repository.advance_base("shared.txt", "target version\n")
    (repository.feature / "shared.txt").write_text("local version\n")
    result = workspace(repository).refresh("owned", source, target, ["shared.txt"])
    assert result["status"] == "needs_resolution"
    assert result["conflicts"] == ["shared.txt"]
    assert result["checkpoint"] == repository.head(repository.feature)
    assert git("show", f"{result['checkpoint']}:shared.txt", cwd=repository.feature) == "local version"
    assert git("rev-parse", "MERGE_HEAD", cwd=repository.feature) == target
    assert {line.split()[2] for line in git("ls-files", "-u", cwd=repository.feature).splitlines()} == {"1", "2", "3"}
    conflict = (repository.feature / "shared.txt").read_text()
    assert "local version" in conflict and "target version" in conflict
    # Another refresh must preserve the in-progress owner resolution state.
    before = git("ls-files", "-u", cwd=repository.feature)
    with pytest.raises(OrchestrationError):
        workspace(repository).refresh("owned", result["checkpoint"], target, [])
    assert git("ls-files", "-u", cwd=repository.feature) == before
    assert (repository.feature / "shared.txt").read_text() == conflict


def test_stale_expected_revision_cannot_checkpoint_or_merge(repository):
    source = repository.head(repository.feature)
    target = repository.advance_base()
    (repository.feature / "feature.txt").write_text("preserve these bytes\n")
    with pytest.raises(OrchestrationError):
        workspace(repository).refresh("owned", "f" * 40, target, ["feature.txt"])
    assert repository.head(repository.feature) == source
    assert (repository.feature / "feature.txt").read_text() == "preserve these bytes\n"
    assert git("status", "--porcelain", cwd=repository.feature) == "M feature.txt"


def test_editor_changes_during_checkpoint_do_not_enter_an_unreviewed_commit(repository):
    source = repository.head(repository.feature)
    target = repository.advance_base()
    selected = repository.feature / "feature.txt"
    selected.write_text("selected initial bytes\n")
    external = repository.feature / "shared.txt"
    real_run = subprocess.run
    edited = False

    def racing_editor(arguments, **options):
        nonlocal edited
        if "add" in arguments and not edited:
            edited = True
            selected.write_text("editor's newer selected bytes\n")
            external.write_text("editor's unrelated staged bytes\n")
            real_run(["git", "add", "--", "shared.txt"], cwd=repository.feature,
                     check=True, capture_output=True)
        return real_run(arguments, **options)

    with patch("xorchestrate.adapters.pr_workspace.subprocess.run", side_effect=racing_editor):
        with pytest.raises(OrchestrationError):
            workspace(repository).refresh("owned", source, target, ["feature.txt"])
    assert edited, "the editor interleaving did not occur"
    assert repository.head(repository.feature) == source, "unreviewed bytes entered a checkpoint"
    assert selected.read_text() == "editor's newer selected bytes\n"
    assert external.read_text() == "editor's unrelated staged bytes\n"
    assert "shared.txt" in git("diff", "--cached", "--name-only", cwd=repository.feature).splitlines()


def test_staging_just_before_checkpoint_commit_cannot_absorb_another_writers_path(repository):
    source = repository.head(repository.feature)
    target = repository.advance_base()
    selected = repository.feature / "feature.txt"
    selected.write_text("selected checkpoint bytes\n")
    external = repository.feature / "shared.txt"
    real_run = subprocess.run
    injected = False

    def stage_before_commit(arguments, **options):
        nonlocal injected
        if ("commit" in arguments or "commit-tree" in arguments) and not injected:
            injected = True
            external.write_text("another writer's late staged bytes\n")
            real_run(["git", "add", "--", "shared.txt"], cwd=repository.feature,
                     check=True, capture_output=True)
        return real_run(arguments, **options)

    result = None
    with patch("xorchestrate.adapters.pr_workspace.subprocess.run", side_effect=stage_before_commit):
        try:
            result = workspace(repository).refresh("owned", source, target, ["feature.txt"])
        except OrchestrationError:
            pass  # An explicit recoverable preparation conflict is an allowed result.
    assert injected, "the late external staging interleaving did not occur"
    checkpoint = result["checkpoint"] if result is not None else repository.head(repository.feature)
    assert git("show", f"{checkpoint}:shared.txt", cwd=repository.feature) == "original", \
        "checkpoint absorbed another writer's path after the selected snapshot was checked"
    assert external.read_text() == "another writer's late staged bytes\n"
    assert "shared.txt" in git("diff", "--cached", "--name-only", cwd=repository.feature).splitlines()
    assert selected.read_text() == "selected checkpoint bytes\n"


@pytest.mark.parametrize("path", ["../outside.txt", "/tmp/outside.txt", "linked.txt"])
def test_checkpoint_paths_cannot_escape_owned_regular_files(repository, path):
    source = repository.head(repository.feature)
    target = repository.advance_base()
    outside = repository.root / "outside.txt"
    outside.write_text("outside content\n")
    if path == "linked.txt":
        (repository.feature / path).symlink_to(outside)
    with pytest.raises(OrchestrationError):
        workspace(repository).refresh("owned", source, target, [path])
    assert repository.head(repository.feature) == source
    assert outside.read_text() == "outside content\n"


def test_normal_publication_refuses_remote_advancement_without_overwriting_it(repository):
    remote = repository.root / "remote.git"
    git("init", "-q", "--bare", str(remote), cwd=repository.main)
    git("remote", "add", "origin", str(remote), cwd=repository.feature)
    source = repository.head(repository.feature)
    adapter = workspace(repository)
    adapter.publish("owned", source, "feature")
    assert git("rev-parse", "refs/heads/feature", cwd=remote) == source
    # Advance the remote through another writer while the owned source stays fixed.
    target = repository.advance_base()
    git("push", str(remote), f"{target}:refs/heads/feature", cwd=repository.main)
    with pytest.raises(OrchestrationError):
        adapter.publish("owned", source, "feature")
    assert git("rev-parse", "refs/heads/feature", cwd=remote) == target
    assert repository.head(repository.feature) == source


def test_candidate_git_hooks_and_ambient_credentials_are_not_executed_by_refresh(repository):
    source = repository.head(repository.feature)
    target = repository.advance_base()
    marker = repository.root / "hook-ran"
    hookdir = repository.root / "candidate-hooks"
    hookdir.mkdir()
    for name in ("pre-commit", "post-commit", "post-merge"):
        hook = hookdir / name
        hook.write_text(f"#!/bin/sh\nprintf '%s' \"$PR_CREDENTIAL_CANARY\" > '{marker}'\n")
        hook.chmod(0o755)
    git("config", "core.hooksPath", str(hookdir), cwd=repository.feature)
    (repository.feature / "feature.txt").write_text("selected work\n")
    with patch.dict(os.environ, {"PR_CREDENTIAL_CANARY": "inert canary"}):
        result = workspace(repository).refresh("owned", source, target, ["feature.txt"])
    assert result["status"] == "updated"
    assert not marker.exists(), "candidate-controlled Git hooks ran in the controller path"


def test_https_publication_credentials_only_reach_isolated_git_directory(repository):
    source = repository.head(repository.feature)
    git("remote", "add", "origin", "https://github.com/example/project.git", cwd=repository.feature)
    callbacks, credentialed = [], []

    def credential():
        callbacks.append(True)
        return "inert-publication-token"

    adapter = GitPRWorkspace(repository.options(), credential=credential)
    real_run = subprocess.run
    reads = 0
    publisher_settings = {"HTTPS_PROXY": "http://proxy.invalid:8443", "SSL_CERT_FILE": "/inert/ca.pem"}

    def controlled_https(arguments, **options):
        nonlocal reads
        environment = options.get("env", {})
        header = environment.get("GIT_CONFIG_VALUE_0", "")
        if "inert-publication-token" not in header:
            if str(options.get("cwd")) in {str(repository.feature), str(repository.main)}:
                assert not (publisher_settings.keys() & environment.keys())
            return real_run(arguments, **options)
        credentialed.append((list(arguments), str(options.get("cwd"))))
        assert any(value.startswith("--git-dir=") for value in arguments)
        assert str(options.get("cwd")) != str(repository.feature)
        assert str(options.get("cwd")) != str(repository.main)
        assert all(environment.get(key) == value for key, value in publisher_settings.items())
        if "ls-remote" in arguments:
            reads += 1
            output = "" if reads == 1 else source + "\trefs/heads/feature\n"
        else:
            assert "push" in arguments
            output = ""
        return subprocess.CompletedProcess(arguments, 0, output, "")

    with patch.dict(os.environ, publisher_settings), \
            patch("xorchestrate.adapters.pr_workspace.subprocess.run", side_effect=controlled_https):
        result = adapter.publish("owned", source, "feature")
    assert result["status"] == "published"
    assert callbacks == [True]
    assert len(credentialed) == 3


def test_untrusted_https_origin_is_rejected_before_loading_publication_credential(repository):
    source = repository.head(repository.feature)
    git("remote", "add", "origin", "https://untrusted.invalid/example/project.git", cwd=repository.feature)
    callbacks = []
    adapter = GitPRWorkspace(repository.options(), credential=lambda: callbacks.append(True))
    real_run = subprocess.run

    def no_external_network(arguments, **options):
        if any("untrusted.invalid" in value for value in arguments):
            return subprocess.CompletedProcess(arguments, 1, "", "fixture refused external transport")
        return real_run(arguments, **options)

    with patch("xorchestrate.adapters.pr_workspace.subprocess.run", side_effect=no_external_network):
        with pytest.raises(OrchestrationError) as error:
            adapter.publish("owned", source, "feature")
    assert error.value.code == "pr_remote_mismatch"
    assert callbacks == []


def test_option_like_remote_cannot_execute_a_candidate_upload_pack_program(repository):
    source = repository.head(repository.feature)
    marker = repository.root / "candidate-command-ran"
    candidate_program = repository.root / "candidate-upload-pack"
    candidate_program.write_text(f"#!/bin/sh\nprintf '%s' \"$HTTPS_PROXY\" > '{marker}'\nexit 1\n")
    candidate_program.chmod(0o755)
    git("config", "remote.origin.url", "--upload-pack=" + str(candidate_program), cwd=repository.feature)
    adapter = workspace(repository)
    real_run = subprocess.run
    transport_calls = []

    def observe_publication(arguments, **options):
        if "ls-remote" in arguments or "push" in arguments:
            transport_calls.append(list(arguments))
        return real_run(arguments, **options)

    with patch.dict(os.environ, {"HTTPS_PROXY": "http://inert-proxy-canary.invalid:8443"}), \
            patch("xorchestrate.adapters.pr_workspace.subprocess.run", side_effect=observe_publication):
        with pytest.raises(OrchestrationError):
            adapter.publish("owned", source, "feature")
    assert not marker.exists(), "candidate upload-pack command executed in the trusted publication process"
    assert transport_calls == [], "an option-like remote reached Git publication before rejection"
    assert repository.head(repository.feature) == source


@pytest.mark.parametrize("name", ["ascii.txt", "café.txt", "tab\tname.txt", "line\nbreak.txt"],
                         ids=["ascii", "unicode", "tab", "newline"])
@pytest.mark.parametrize("conflict", [False, True], ids=["updated", "conflicted"])
def test_selected_legal_tracked_names_preserve_checkpoint_bytes_and_literal_conflict_paths(repository, name, conflict):
    (repository.main / name).write_bytes(b"original named file\n")
    common = repository.commit(repository.main, [name], "track legal filename")
    git("merge", "--ff-only", common, cwd=repository.feature)
    source = repository.head(repository.feature)
    target = repository.advance_base(name if conflict else "base.txt", "target version\n")
    selected = repository.feature / name
    selected.write_bytes(b"selected named file bytes\n")
    try:
        result = workspace(repository).refresh("owned", source, target, [name])
    except OrchestrationError as error:
        result = {"status": "rejected", "code": error.code, "details": error.details}
    expected = "needs_resolution" if conflict else "updated"
    assert result["status"] == expected, ("legal selected tracked pathname was rejected or misclassified", name, result)
    checkpoint = result["checkpoint"]
    names = git("diff-tree", "--no-commit-id", "--name-only", "-z", "-r", checkpoint,
                cwd=repository.feature).split("\0")
    assert [value for value in names if value] == [name]
    assert git("show", f"{checkpoint}:{name}", cwd=repository.feature) == "selected named file bytes"
    assert result["paths"] == [name]
    if conflict:
        assert result["conflicts"] == [name], "conflict reporting must retain the literal tracked pathname"
        entries = git("ls-files", "--unmerged", "-z", cwd=repository.feature).split("\0")
        assert [entry.partition("\t")[2] for entry in entries if entry] == [name] * 3
        assert git("rev-parse", "MERGE_HEAD", cwd=repository.feature) == target
    else:
        assert result["conflicts"] == []
        assert selected.read_bytes() == b"selected named file bytes\n"
        assert (repository.feature / "base.txt").read_text() == "target version\n"


def test_selected_unicode_name_does_not_authorize_unrelated_tracked_changes(repository):
    name = "café.txt"
    selected = repository.feature / name
    selected.write_bytes(b"original named file\n")
    source = repository.commit(repository.feature, [name], "track legal filename")
    target = repository.advance_base()
    selected.write_bytes(b"selected named file bytes\n")
    unrelated = repository.feature / "shared.txt"
    unrelated.write_bytes(b"unrelated tracked bytes\n")
    with pytest.raises(OrchestrationError) as error:
        workspace(repository).refresh("owned", source, target, [name])
    assert error.value.code == "pr_checkpoint_conflict"
    assert error.value.details["paths"] == ["shared.txt"], "only actual unrelated paths should be reported"
    assert repository.head(repository.feature) == source
    assert selected.read_bytes() == b"selected named file bytes\n"
    assert unrelated.read_bytes() == b"unrelated tracked bytes\n"
