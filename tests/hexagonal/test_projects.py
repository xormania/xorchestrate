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
from xorchestrate.instance import operations
from xorchestrate.instance.home import account_home
from xorchestrate.instance.instance import prepare
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
    assert project.run("describe").returncode == 0


def test_several_configurations_may_be_approved(project):
    first = {"version": 1}
    second = {"version": 1, "routing": {"adapter": "configurable"}}
    project.write_config(second)
    project.write_config(first, approved=False)
    assert project.run("describe").returncode == 0, "the first configuration stays approved"
    approvals = json.loads((project.companion / "approvals" / "fixture.json").read_text())
    assert len(approvals["approved"]) == 2
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
    assert (tmp_path / "companion" / "overlays" / "app.json").exists()
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
    assert report["created"] == [str(tmp_path / "second-companion" / "overlays" / "fixture.json")]
    assert report["mcp"]["state"] == "absent" and not (clone / ".mcp.json").exists()
    assert {path: path.read_bytes() for path in (clone / ".xorchestrate").rglob("*") if path.is_file()} == before
    assert git("status", "--porcelain", cwd=clone) == ""


def test_setup_creates_or_links_a_companion(tmp_path):
    environ = {"XORCHESTRATE_HOME": str(tmp_path / "home")}
    created = operations.setup(tmp_path / "companion", environ)
    assert created["action"] == "created"
    assert {path.name for path in (tmp_path / "companion").iterdir()} >= {"overlays", "approvals", "policies",
                                                                         "launchers", "README.md", ".gitattributes"}
    settings = json.loads((tmp_path / "home" / "settings.json").read_text())
    assert settings == {"schema": 1, "companion": str(tmp_path / "companion")}
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
    (project.companion / "policies" / "team.json").write_text(json.dumps(
        {"assessment": {"confidence_threshold": 0.9, "uncertain_outcomes": ["maybe"]}}))
    assert project.config["assessment"] == {"confidence_threshold": 0.9, "uncertain_outcomes": ["unsure"]}
    sources = {item["path"]: item["source"] for item in project.json("status", expect=3)["overrides"]}
    assert sources["/assessment/confidence_threshold"] == "companion_policy:team.json"


def test_rendering_substitutes_the_closed_placeholder_set(tmp_path):
    launcher = {"command": ["${launchers}/tool", "--data", "${data}", "--instance", "${instance}", "--home", "${home}"],
                "cwd": "${repo}"}
    project = ProjectFixture(tmp_path / "p", {"version": 1, "providers": {"tool": {"adapter": "cli",
                                                                                   "options": launcher}}})
    (project.companion / "launchers" / "tool").write_text("#!/bin/sh\necho launched\n")
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
