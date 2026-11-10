"""Projects: no project no run, instance identity, approval, overlay, rendering and the operator commands."""

import json
import os
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from tests.hexagonal.support.projects import ROOT, ProjectFixture, git
from xorchestrate.composition import RELEASE, build
from xorchestrate.instance import companion as records
from xorchestrate.instance import operations
from xorchestrate.instance.home import Home, account_home
from xorchestrate.instance.instance import context_for, load_instance, locate_project, prepare, require_companion
from xorchestrate.instance.render import render
from xorchestrate.model import OrchestrationError

ENTRYPOINTS = (["describe"], ["call", "list_work", "--json", "{}"], ["run"], ["serve"], ["connect"], ["up"],
               ["down"], ["status"])


def cli(*arguments, cwd, home, input=b""):
    environment = {**os.environ, "XORCHESTRATE_HOME": str(home),
                   "PYTHONPATH": os.pathsep.join([str(ROOT), os.environ.get("PYTHONPATH", "")])}
    return subprocess.run([sys.executable, "-m", "xorchestrate", *arguments], cwd=cwd, env=environment,
                          capture_output=True, input=input, timeout=60)


def error_of(result):
    assert result.returncode == 2, result
    return json.loads(result.stderr.splitlines()[-1])["error"]


# -- no project, no run ----------------------------------------------------------

def test_every_entrypoint_refuses_outside_a_project_and_names_init(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    repository = tmp_path / "repository"
    git("init", "-q", str(repository), cwd=tmp_path)
    unset = tmp_path / "unset"
    git("init", "-q", str(unset), cwd=tmp_path)
    (unset / ".xorchestrate").mkdir()
    (unset / ".xorchestrate" / "project.json").write_text(json.dumps(
        {"schema": 1, "name": "unset", "release": RELEASE, "requires": []}))
    for directory in (plain, repository, unset):
        for command in ENTRYPOINTS:
            error = error_of(cli(*command, cwd=directory, home=tmp_path / "home"))
            assert error["code"] == "project_required", (directory, command, error)
            assert "xorchestrate init" in error["message"], error
    assert not (tmp_path / "home" / "servers").exists(), "a refused command started nothing"


def test_project_option_and_xorchestrate_home_are_honoured(project, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    described = cli("--project", str(project.repo), "describe", cwd=elsewhere, home=project.home)
    assert described.returncode == 0, described
    assert "create_work" in {item["name"] for item in json.loads(described.stdout)["operations"]}
    # A different home is a different machine-local set of projects.
    other = error_of(cli("--project", str(project.repo), "describe", cwd=elsewhere, home=tmp_path / "other-home"))
    assert other["code"] == "project_required"
    relative = error_of(cli("--project", str(project.repo), "describe", cwd=elsewhere, home="relative/home"))
    assert relative["code"] == "invalid_configuration"


# -- instance identity ---------------------------------------------------------

def test_worktrees_map_to_one_instance(project, worktree):
    main, linked = project.prepared(), project.prepared(worktree)
    assert main.instance.instance_id == linked.instance.instance_id
    assert main.instance.data_dir == linked.instance.data_dir == project.data_dir
    assert linked.context.worktree["root"] == str(worktree)
    assert linked.context.worktree["name"] == "feature" and linked.context.worktree["branch"] == "feature"
    again = operations.init(worktree, environ=project.provisioned.environment)
    assert again["registered"] is False and again["instance_id"] == main.instance.instance_id
    registry = json.loads((project.home / "registry.json").read_text())
    assert list(registry["instances"]) == [main.instance.instance_id]


def test_a_separate_clone_of_the_same_project_is_refused(project, tmp_path):
    project.commit()
    clone = tmp_path / "clone"
    git("clone", "-q", str(project.repo), str(clone), cwd=tmp_path)
    instance_id = project.prepared().instance.instance_id
    for attempt in (lambda: operations.init(clone, environ=project.provisioned.environment),
                    lambda: prepare(clone, environ=project.provisioned.environment)):
        with pytest.raises(OrchestrationError) as refused:
            attempt()
        assert refused.value.code == "project_clone_refused"
        assert refused.value.details["instance_id"] == instance_id
        assert refused.value.details["repository"] == str(project.repo)


# -- approval --------------------------------------------------------------------

def test_an_unapproved_configuration_refuses_to_run_and_names_the_changed_files(project):
    project.write_config({"version": 1, "routing": {"adapter": "configurable"}}, approved=False)
    refused = project.run("describe")
    error = error_of(refused)
    assert error["code"] == "approval_required" and error["details"]["files"] == ["config.json"]
    assert "xorchestrate approve" in error["message"]
    approved = project.json("approve")
    assert approved["newly_approved"] is True and approved["summary"]["files"].keys() == {"config.json", "project.json"}
    assert approved["summary"]["config_hash"] == approved["config_hash"]
    assert project.run("describe").returncode == 0


def test_several_configurations_may_be_approved(project):
    first = {"version": 1}
    second = {"version": 1, "routing": {"adapter": "configurable"}}
    project.write_config(second)
    project.write_config(first, approved=False)
    assert project.run("describe").returncode == 0, "the first configuration stays approved"
    assert len(project.approvals()) == 2
    assert operations.approve(project.repo, project.provisioned.environment)["newly_approved"] is False


def test_the_approval_is_checked_again_before_every_render(project):
    rendered = project.data_dir / "config.rendered.json"
    before = rendered.read_bytes()
    project.write_config({"version": 1, "providers": {"tool": {"adapter": "cli", "options": {
        "command": ["unapproved-tool"]}}}}, approved=False)
    with pytest.raises(OrchestrationError) as refused:
        project.prepared()
    assert refused.value.code == "approval_required"
    assert rendered.read_bytes() == before, "a refused render leaves the running configuration unchanged"


# -- init, approve, setup and companion ------------------------------------------

def test_first_init_summarizes_and_requires_a_separate_approve(tmp_path):
    environ = {"XORCHESTRATE_HOME": str(tmp_path / "home")}
    with pytest.raises(OrchestrationError) as no_companion:
        operations.init(tmp_path, environ=environ)
    assert no_companion.value.code in {"project_required", "companion_required"}
    setup = operations.setup(tmp_path / "companion", environ)
    assert setup["action"] == "created" and setup["doctor"]["checks"]
    repository = tmp_path / "app"
    git("init", "-q", str(repository), cwd=tmp_path)
    first = operations.init(repository, approve=True, environ=environ)
    assert first["approval"]["state"] == "required", "the first approval is always separate"
    assert first["project"] == "app" and first["registered"] is True
    assert (repository / ".xorchestrate" / "project.json").exists()
    assert json.loads((repository / ".mcp.json").read_text()) == {
        "mcpServers": {"xorchestrate": {"command": "xorchestrate", "args": ["connect"]}}}
    assert (tmp_path / "companion" / "overlay" / "app" / "overlay.json").exists()
    assert first["approval"]["summary"]["commands"] == []
    operations.approve(repository, environ)
    (repository / ".xorchestrate" / "config.json").write_text(json.dumps({"version": 1, "providers": {"lint": {
        "adapter": "cli", "options": {"command": ["${launchers}/lint", "--check"]}}}}))
    later = operations.init(repository, environ=environ)
    assert later["approval"]["state"] == "required"
    assert later["approval"]["summary"]["commands"][0]["command"] == ["${launchers}/lint", "--check"]
    both = operations.init(repository, approve=True, environ=environ)
    assert both["approval"]["state"] == "approved" and Path(both["rendered"]).exists()


def test_init_never_edits_tracked_files_of_a_cloned_project(project, tmp_path):
    (project.repo / ".mcp.json").unlink(missing_ok=True)
    project.commit()
    other_home = tmp_path / "second-machine"
    environ = {"XORCHESTRATE_HOME": str(other_home)}
    operations.companion_init(tmp_path / "second-companion", environ)
    clone = tmp_path / "cloned"
    git("clone", "-q", str(project.repo), str(clone), cwd=tmp_path)
    before = {path: path.read_bytes() for path in (clone / ".xorchestrate").rglob("*") if path.is_file()}
    report = operations.init(clone, environ=environ)
    assert report["created"] == [f"{tmp_path / 'second-companion'}: overlay fixture"]
    assert report["mcp"]["state"] == "absent" and not (clone / ".mcp.json").exists()
    assert {path: path.read_bytes() for path in (clone / ".xorchestrate").rglob("*") if path.is_file()} == before
    assert git("status", "--porcelain", cwd=clone) == ""


def test_setup_creates_or_links_a_companion(tmp_path):
    environ = {"XORCHESTRATE_HOME": str(tmp_path / "home")}
    created = operations.setup(tmp_path / "companion", environ)
    assert created["action"] == "created"
    assert {path.name for path in (tmp_path / "companion").iterdir()} >= {"overlay", "approvals", "policies",
                                                                         "launchers", "README.md", ".gitattributes"}
    settings = json.loads((tmp_path / "home" / "settings.json").read_text())
    assert settings == {"schema": 1, "companion": {"adapter": "git-directory",
                                                   "options": {"path": str(tmp_path / "companion")}}}
    second = {"XORCHESTRATE_HOME": str(tmp_path / "home2")}
    assert operations.setup(tmp_path / "companion", second)["action"] == "linked"
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "file").write_text("x")
    for attempt in (lambda: operations.setup(occupied, environ), lambda: operations.companion_use(occupied, environ)):
        with pytest.raises(OrchestrationError):
            attempt()


# -- overlay and rendering ----------------------------------------------------------

def test_an_explicit_overlay_override_wins_including_a_guardrail(tmp_path):
    tracked = {"version": 1, "guardrail": {"adapter": "rules", "options": {"default": "allow"}},
               "providers": {"extra": {"adapter": "cli", "options": {"command": ["extra-tool"]}}}}
    project = ProjectFixture(tmp_path / "p", tracked, policies={"base.json": {"routing": {"adapter": "configurable"}}},
                             overlay={"guardrail": {"options": {"default": "deny"}}, "providers": {"extra": None}})
    config = project.config
    assert config["guardrail"]["options"]["default"] == "deny"
    assert "extra" not in config.get("providers", {})
    assert config["routing"] == {"adapter": "configurable"}
    with build(config, project=project.context) as runtime:
        with pytest.raises(OrchestrationError) as denied:
            runtime.application.call("create_work", {"goal": "blocked by the overlay"})
    assert denied.value.code == "denied"
    expected = [{"path": "/guardrail/options/default", "source": "overlay", "project": "allow", "value": "deny"},
                {"path": "/providers/extra", "source": "overlay",
                 "project": {"adapter": "cli", "options": {"command": ["extra-tool"]}}, "removed": True}]
    status = project.json("status", expect=3)
    assert status["overrides"] == expected
    doctor = project.json("doctor")
    assert [check["overrides"] for check in doctor["checks"] if check["check"] == "overrides"] == [expected]


def test_companion_policies_sit_between_the_project_and_the_overlay(tmp_path):
    project = ProjectFixture(tmp_path / "p", {"version": 1, "assessment": {"confidence_threshold": 0.5}},
                             overlay={"assessment": {"uncertain_outcomes": ["unsure"]}})
    project.write_policy("team.json", {"assessment": {"confidence_threshold": 0.9, "uncertain_outcomes": ["maybe"]}})
    assert project.config["assessment"] == {"confidence_threshold": 0.9, "uncertain_outcomes": ["unsure"]}
    sources = {item["path"]: item["source"] for item in project.json("status", expect=3)["overrides"]}
    assert sources["/assessment/confidence_threshold"] == "companion_policy:team.json"


def test_rendering_substitutes_the_closed_placeholder_set(tmp_path):
    launcher = {"command": ["${launchers}/tool", "--data", "${data}", "--instance", "${instance}", "--home", "${home}"],
                "cwd": "${repo}"}
    project = ProjectFixture(tmp_path / "p", {"version": 1, "providers": {"tool": {"adapter": "cli",
                                                                                   "options": launcher}}})
    project.write_launcher("tool", b"#!/bin/sh\necho launched\n")
    rendered_path = project.data_dir / "config.rendered.json"
    config = project.config
    assert json.loads(rendered_path.read_text()) == config
    context = project.context
    options = config["providers"]["tool"]["options"]
    assert options["command"] == [str(project.data_dir / "launchers" / "tool"), "--data", str(project.data_dir),
                                  "--instance", context.instance_id, "--home", str(account_home())]
    assert options["cwd"] == "${repo}", "the worktree is resolved per call"
    assert config["store"] == {"adapter": "sqlite", "options": {"path": str(project.data_dir / "state.sqlite3")}}
    copied = project.data_dir / "launchers" / "tool"
    assert copied.read_text() == "#!/bin/sh\necho launched\n" and stat.S_IMODE(copied.stat().st_mode) == 0o755
    project.write_config({"version": 1, "providers": {"tool": {"adapter": "cli",
                                                               "options": {"command": ["${secret}"]}}}})
    with pytest.raises(OrchestrationError) as unknown:
        project.prepared()
    assert unknown.value.code == "configuration_error" and unknown.value.details == {"placeholder": "secret"}


def test_state_and_telemetry_live_in_the_instance_with_project_defaults(project):
    created = project.json("call", "create_work", "--json", '{"goal":"durable"}')["work"]
    assert created["created_worktree"]["root"] == str(project.repo)
    assert not (project.repo / ".xorchestrate" / "state.sqlite3").exists()
    assert (project.data_dir / "state.sqlite3").exists()
    corpus = project.data_dir / "telemetry" / "events.sqlite3"
    with sqlite3.connect(corpus) as database:
        metadata = dict(database.execute("SELECT key, value FROM metadata").fetchall())
        observations = [json.loads(row[0]) for row in database.execute("SELECT observation FROM events")]
    context = project.context
    assert metadata.get("project_id", context.name) == context.name
    assert context.instance_id in metadata.values() and "default" in metadata.values()
    sources = [json.loads(item["source_json"]) for item in observations if isinstance(item.get("source_json"), str)]
    assert sources, observations[:1]
    assert any(source["xorchestrate"].get("worktree", {}).get("root") == str(project.repo) for source in sources)


def test_telemetry_is_always_on_for_a_project_unless_disabled(project):
    with build({"version": 1, "store": {"adapter": "memory"}}, project=project.context) as runtime:
        status = runtime.application.call("telemetry_status", {})
        assert status["status"] not in {"disabled", "unavailable"}, status
    with build({"version": 1, "store": {"adapter": "memory"}, "telemetry": {"enabled": False}},
               project=project.context) as runtime:
        assert runtime.application.call("telemetry_status", {})["status"] == "disabled"
    project.write_overlay({"telemetry": {"options": {"dataset_id": "experiment-routing"}}})
    assert project.config["telemetry"]["options"]["dataset_id"] == "experiment-routing"
    assert project.config["telemetry"]["options"]["collector_id"] == project.context.instance_id


def test_records_events_and_guardrails_carry_the_callers_worktree(project):
    rules = {"version": 1, "store": {"adapter": "memory"}, "guardrail": {"adapter": "rules", "options": {"rules": [
        {"id": "other-reads-only", "effect": "deny", "actions": ["create_work"], "where": {"worktree.name": "other"}}]}}}
    other = {"root": "/elsewhere/other", "name": "other", "branch": "topic", "config_hash": "abc"}
    with build(rules, project=project.context) as runtime:
        app = runtime.application
        work = app.call("create_work", {"goal": "default worktree"})["work"]
        assert work["created_worktree"] == project.context.worktree
        with pytest.raises(OrchestrationError) as denied:
            app.call("create_work", {"goal": "denied"}, worktree=other)
        assert denied.value.code == "denied"
        updated = app.call("update_work", {"work_id": work["id"], "expected_version": work["version"],
                                           "changes": {"title": "renamed"}}, worktree=other)
        assert updated["work"]["created_worktree"] == project.context.worktree
        events = app.call("events", {"after": 0, "limit": 50})["events"]
    updates = [event for event in events if event["type"] == "work.updated"]
    assert updates and updates[0]["worktree"] == other


def test_doctor_reports_missing_harnesses_unset_environment_and_approval(project, monkeypatch):
    monkeypatch.delenv("XO_TEST_UNSET_TOKEN", raising=False)
    project.write_config({"version": 1, "providers": {"tool": {"adapter": "cli", "options": {
        "command": ["xo-test-missing-binary"], "pass_env": ["XO_TEST_UNSET_TOKEN"]}}}})
    report = project.json("doctor", expect=1)
    by_check = {}
    for check in report["checks"]:
        by_check.setdefault(check["check"], []).append(check)
    assert by_check["approval"][0]["status"] == "ok"
    assert by_check["harness"][0]["status"] == "error" and "xo-test-missing-binary" in by_check["harness"][0]["detail"]
    assert by_check["pass_env"][0]["missing"] == ["XO_TEST_UNSET_TOKEN"]
    assert {"python", "sqlite", "uv", "git"} <= by_check.keys()
    project.write_config({"version": 1, "routing": {"adapter": "configurable"}}, approved=False)
    unapproved = project.json("doctor", expect=1)
    approval = next(check for check in unapproved["checks"] if check["check"] == "approval")
    assert approval["status"] == "error" and approval["files"] == ["config.json"]


@pytest.mark.parametrize("example", sorted(path.name for path in (ROOT / "examples").iterdir() if path.is_dir()))
def test_every_example_project_validates_and_renders(example, tmp_path):
    tracked = ROOT / "examples" / example / ".xorchestrate"
    config = json.loads((tracked / "config.json").read_text())
    declared = json.loads((tracked / "project.json").read_text())
    assert declared["name"] == example and declared["schema"] == 1
    project = ProjectFixture(tmp_path / example, config, name=example)
    rendered = project.prepared()
    assert rendered.rendered.warnings == ()
    assert rendered.config["store"]["options"]["path"] == str(project.data_dir / "state.sqlite3")
    with build(rendered.config, project=rendered.context) as runtime:
        assert runtime.application.call("list_work", {}) == {"work": []}


# -- review regressions ------------------------------------------------------------

def test_rendering_uses_exactly_the_bytes_that_were_approved(project):
    environ = project.provisioned.environment
    tracked = locate_project(project.repo)
    assert records.is_approved(require_companion(Home.locate(environ)), tracked.name, tracked.config_hash)
    # The tracked file changes after it was read and hashed, before rendering.
    project.write_config({"version": 1, "providers": {"evil": {"adapter": "cli", "options": {
        "command": ["unapproved-tool"]}}}}, approved=False)
    home = Home.locate(environ)
    rendered = render(tracked, require_companion(home), context_for(load_instance(home, project.name), tracked),
                      write=False)
    assert "providers" not in rendered.config, "the rendered configuration is the approved one"


def test_a_symlinked_tracked_directory_is_refused(project, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    (project.repo / ".xorchestrate").rename(elsewhere)
    (project.repo / ".xorchestrate").symlink_to(elsewhere)
    error = error_of(project.run("describe"))
    assert error["code"] == "project_invalid" and "symbolic link" in error["message"]


def test_tracked_directory_without_project_file_is_a_clear_error(tmp_path):
    environ = {"XORCHESTRATE_HOME": str(tmp_path / "home")}
    operations.setup(tmp_path / "companion", environ)
    repository = tmp_path / "half"
    git("init", "-q", str(repository), cwd=tmp_path)
    (repository / ".xorchestrate").mkdir()
    (repository / ".xorchestrate" / "config.json").write_text("{}")
    with pytest.raises(OrchestrationError) as refused:
        operations.init(repository, environ=environ)
    assert refused.value.code == "project_invalid" and "project.json" in str(refused.value)


def test_legacy_state_inside_the_tracked_directory_is_refused(project):
    (project.tracked / "state.sqlite3").write_bytes(b"SQLite format 3\x00")
    error = error_of(project.run("describe"))
    assert error["code"] == "project_invalid" and "state.sqlite3" in error["details"]["path"]
    assert "move it" in error["message"]


def test_unrelated_repositories_with_the_same_name_get_a_distinct_refusal(project, tmp_path):
    project.commit()
    unrelated = tmp_path / "unrelated"
    git("init", "-q", str(unrelated), cwd=tmp_path)
    (unrelated / ".xorchestrate").mkdir()
    (unrelated / ".xorchestrate" / "project.json").write_text(json.dumps(
        {"schema": 1, "name": "fixture", "release": RELEASE, "requires": []}))
    (unrelated / ".xorchestrate" / "config.json").write_text("{}")
    git("add", "-A", cwd=unrelated)
    git("commit", "-q", "-m", "unrelated history", cwd=unrelated)
    with pytest.raises(OrchestrationError) as refused:
        operations.init(unrelated, environ=project.provisioned.environment)
    assert refused.value.code == "project_name_conflict"
    assert "rename" in str(refused.value) and refused.value.details["repository"] == str(project.repo)


def test_a_moved_repository_is_relocated_explicitly(project, tmp_path):
    project.commit()
    instance_id = project.prepared().instance.instance_id
    moved = tmp_path / "moved"
    project.repo.rename(moved)
    environ = project.provisioned.environment
    with pytest.raises(OrchestrationError) as refused:
        prepare(moved, environ=environ)
    assert refused.value.code == "project_moved" and "--relocate" in str(refused.value)
    report = operations.init(moved, relocate_instance=True, environ=environ)
    assert report["relocated"] is True and report["instance_id"] == instance_id
    assert prepare(moved, environ=environ).instance.instance_id == instance_id
    with pytest.raises(OrchestrationError) as live:
        clone = tmp_path / "clone"
        git("clone", "-q", str(moved), str(clone), cwd=tmp_path)
        operations.init(clone, relocate_instance=True, environ=environ)
    assert live.value.code == "project_not_moved"


def test_overrides_report_only_effective_differences(tmp_path):
    project = ProjectFixture(tmp_path / "p", {"version": 1, "guardrail": {"adapter": "rules",
                                                                           "options": {"default": "allow"}}},
                             overlay={"guardrail": {}, "routing": {"adapter": "configurable"},
                                      "assessment": {"confidence_threshold": 0.7}})
    project.write_policy("team.json", {"guardrail": {"options": {"default": "deny"}}})
    project.write_overlay({"guardrail": {"options": {"default": "allow"}}, "assessment": {}})
    assert project.json("status", expect=3)["overrides"] == [], \
        "a no-op patch and a value the overlay restores are not overrides"
    project.write_overlay({"telemetry": {"options": {"dataset_id": "trial"}}})
    assert project.json("status", expect=3)["overrides"] == [
        {"path": "/guardrail/options/default", "source": "companion_policy:team.json", "project": "allow",
         "value": "deny"},
        {"path": "/telemetry/options/dataset_id", "source": "overlay", "value": "trial"}]


def test_repo_placeholder_is_refused_outside_process_options(project):
    for config, pointer in (({"version": 1, "store": {"adapter": "sqlite", "options": {"path": "${repo}/s.sqlite3"}}},
                             "/store/options/path"),
                            ({"version": 1, "telemetry": {"options": {"runtime_root": "${repo}/t"}}},
                             "/telemetry/options/runtime_root")):
        project.write_config(config)
        with pytest.raises(OrchestrationError) as refused:
            project.prepared()
        assert refused.value.code == "configuration_error" and refused.value.details == {"path": pointer}


def test_approval_summary_and_doctor_flag_code_outside_the_approval(project):
    project.write_config({"version": 1, "guardrail": {"adapter": "projectpolicies.rules:Guardrail"},
                          "providers": {"local": {"adapter": "cli", "options": {
                              "command": ["${repo}/bin/tool", "--check"]}}}}, approved=False)
    summary = operations.approve(project.repo, project.provisioned.environment)["summary"]
    assert summary["repository_code"] == [
        {"where": "guardrail", "kind": "module", "adapter": "projectpolicies.rules:Guardrail"},
        {"where": "providers.local.command", "kind": "repository_command", "command": ["${repo}/bin/tool", "--check"]}]
    flagged = [check for check in project.json("doctor")["checks"] if check["check"] == "repository_code"]
    assert [check["where"] for check in flagged] == ["guardrail", "providers.local.command"]
    assert all(check["status"] == "warning" for check in flagged)


def test_a_companion_inside_a_project_is_refused_or_reported(project, tmp_path):
    environ = {"XORCHESTRATE_HOME": str(tmp_path / "other-home")}
    inside = project.repo / "notes" / "companion"
    for attempt in (lambda: operations.setup(inside, environ), lambda: operations.companion_init(inside, environ)):
        with pytest.raises(OrchestrationError) as refused:
            attempt()
        assert refused.value.code == "companion_exposed"
    assert not inside.exists()
    # A companion registered before this check is reported by doctor.
    import shutil
    shutil.copytree(project.companion, inside)
    Home(project.home).update_settings(companion=str(inside))
    checks = {check["check"]: check for check in project.json("doctor", expect=1)["checks"]}
    assert checks["companion"]["status"] == "error"


def test_init_skips_a_malformed_mcp_file(tmp_path):
    environ = {"XORCHESTRATE_HOME": str(tmp_path / "home")}
    operations.setup(tmp_path / "companion", environ)
    repository = tmp_path / "app"
    git("init", "-q", str(repository), cwd=tmp_path)
    (repository / ".mcp.json").write_text("{not json")
    report = operations.init(repository, environ=environ)
    assert report["mcp"]["state"] == "invalid"
    assert (repository / ".mcp.json").read_text() == "{not json"
