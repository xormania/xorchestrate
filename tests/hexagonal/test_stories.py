"""Story selection, admitted-request bounds and real owned tmux automation."""

import json
import shutil

import pytest

from scripts.testkit import __main__ as cli
from scripts.testkit import composition
from scripts.testkit.adapters import profiles
from tests.hexagonal.contracts.stories import STORIES, completed_story, rejected_before_dispatch
from tests.hexagonal.support.managed_workflow import PublicWorkflow, isolated_candidate
from tests.hexagonal.support.stories import DispatchProbe, acceptance


def test_selected_story_is_a_tmux_supervised_check_with_retained_bounds(tmp_path):
    checks = composition.plan(directory=tmp_path, suite="stories", mode="protocol-fixture",
                              stories=("coordination",), max_sessions=1, max_turns=2, max_tool_calls=30)
    assert len(checks) == 1
    argv = checks[0].argv
    assert argv[argv.index("-m") + 1] == "scripts.acceptance"
    assert argv[argv.index("--story") + 1] == "coordination"
    assert argv[argv.index("--max-sessions") + 1] == "1"
    assert argv[argv.index("--max-turns") + 1] == "2"
    assert argv[argv.index("--max-tool-calls") + 1] == "30"
    # Logical IDs stay readable, but evidence must survive portable artifact archives.
    assert checks[0].name == "story:coordination"
    assert ":" not in argv[argv.index("--output") + 1].removeprefix(str(tmp_path))


@pytest.mark.parametrize("stories,mode", [(("missing-story",), "protocol-fixture"),
                                         (("coordination", "coordination"), "protocol-fixture"),
                                         (("coordination",), "native")])
def test_invalid_story_selection_is_rejected_before_a_check_can_launch(tmp_path, stories, mode):
    with pytest.raises(ValueError):
        composition.plan(directory=tmp_path, suite="stories", mode=mode, stories=stories)
    assert list(tmp_path.iterdir()) == []


def test_native_story_selection_expands_only_requested_harnesses(tmp_path):
    checks = composition.plan(directory=tmp_path, suite="stories", mode="native", config=tmp_path / "native.json",
                              stories=("native-callback",), drivers=("codex", "custom/name"))
    assert len(checks) == 2
    assert len({row.name for row in checks}) == 2
    assert all("/" not in row.name for row in checks)
    assert [row.argv[row.argv.index("--driver") + 1] for row in checks] == ["codex", "custom/name"]
    assert all(row.argv[row.argv.index("--story") + 1] == "native-callback" for row in checks)


@pytest.mark.parametrize("name", ["max_sessions", "max_turns", "max_tool_calls"])
@pytest.mark.parametrize("value", [0, -1, True, 1.5, float("nan"), float("inf")])
def test_invalid_request_limits_are_rejected_before_allocation(tmp_path, name, value):
    with pytest.raises(ValueError):
        composition.plan(directory=tmp_path, suite="stories", mode="protocol-fixture",
                         stories=("coordination",), **{name: value})
    assert list(tmp_path.iterdir()) == []


def test_public_dry_plan_rejects_nonfinite_run_deadline(tmp_path, capsys):
    output = tmp_path / "must-not-exist"
    result = cli.main(["plan", "--suite", "stories", "--story", "coordination",
                       "--run-timeout", "nan", "--output", str(output)])
    response = json.loads(capsys.readouterr().out)
    assert result != 0
    assert response["status"] == "setup_error"
    assert "run_timeout" in response["diagnostic"]
    assert not output.exists()


def test_public_dry_plan_exposes_aggregate_limits_without_launch_or_allocation(tmp_path, capsys, monkeypatch):
    from scripts.testkit import operator_composition as managed

    def forbidden(*args, **kwargs):
        raise AssertionError("A dry plan cannot launch a process or allocate a run")

    monkeypatch.setattr(managed.subprocess, "Popen", forbidden)
    monkeypatch.setattr(managed.ManagedStore, "allocate", forbidden)
    output = tmp_path / "must-not-exist"
    result = cli.main(["plan", "--suite", "stories", "--story", "native-callback", "--mode", "native",
                       "--config", str(tmp_path / "unopened.json"), "--driver", "codex", "--driver", "claude",
                       "--max-sessions", "1", "--max-turns", "2", "--max-tool-calls", "30",
                       "--run-timeout", "60", "--output", str(output)])
    response = json.loads(capsys.readouterr().out)
    assert result == 0, response
    assert response["status"] == "passed"
    assert response["stories"] == [{"id": "native-callback", "version": 1}]
    assert response["story_attempts"] == len(response["checks"]) == 2
    assert response["story_limits"] == {"max_sessions": 1, "max_turns": 2, "max_tool_calls": 30}
    assert response["battery_story_limits"] == {"max_sessions": 2, "max_turns": 4, "max_tool_calls": 60}
    assert response["run_timeout"] == 60
    assert response["tokens"] == "unknown"
    assert not output.exists()


def test_profile_story_selection_and_bounds_overrides_are_retained_without_rewriting_source(tmp_path):
    root = tmp_path / "candidate"
    root.mkdir()
    path = tmp_path / "proof.json"
    source = {"schema_version": 1, "suite": "stories", "mode": "protocol-fixture",
              "stories": ["work-acceptance"], "max_sessions": 2, "max_turns": 4, "max_tool_calls": 5000}
    path.write_text(json.dumps(source))
    unchanged = path.read_bytes()
    result = profiles.load_profile(path, root=root, overrides={"stories": ["coordination", "effects-recovery"],
                                                            "max_sessions": 1, "max_turns": 3, "max_tool_calls": 31})
    assert result["stories"] == ["coordination", "effects-recovery"]
    assert {key: result[key] for key in ("max_sessions", "max_turns", "max_tool_calls")} == {
        "max_sessions": 1, "max_turns": 3, "max_tool_calls": 31}
    assert path.read_bytes() == unchanged
    assert set(tmp_path.iterdir()) == {root, path}


@pytest.mark.parametrize("name", ["max_sessions", "max_turns", "max_tool_calls"])
@pytest.mark.parametrize("value", [0, True, float("nan")])
def test_profile_rejects_invalid_story_bounds(tmp_path, name, value):
    root = tmp_path / "candidate"
    root.mkdir()
    path = tmp_path / "proof.json"
    path.write_text(json.dumps({"suite": "stories", "stories": ["coordination"], name: value}))
    with pytest.raises(ValueError):
        profiles.load_profile(path, root=root)
    assert set(tmp_path.iterdir()) == {root, path}


@pytest.mark.parametrize("counter,action", [("sessions", "start_session"), ("turns", "send_session"),
                                          ("tool_calls", "invoke")])
def test_exhausted_limit_rejects_before_dispatch_without_partial_admission(tmp_path, counter, action):
    subject = DispatchProbe(tmp_path, max_sessions=1, max_turns=1, max_tool_calls=2)
    subject.budget.consumed[counter] = subject.budget.limits[counter]
    rejected_before_dispatch(subject, action, {"marker": "must-not-dispatch"})


def test_valid_dispatch_consumes_only_applicable_limits(tmp_path):
    subject = DispatchProbe(tmp_path, max_sessions=2, max_turns=2, max_tool_calls=3)
    subject.client.tool("start_session", {})
    assert subject.budget.consumed == {"sessions": 1, "turns": 0, "tool_calls": 1}
    subject.client.tool("send_session", {})
    assert subject.budget.consumed == {"sessions": 1, "turns": 1, "tool_calls": 2}
    subject.client.tool("get_work", {})
    assert subject.budget.consumed == {"sessions": 1, "turns": 1, "tool_calls": 3}
    assert len(subject.dispatched) == 3


@pytest.mark.skipif(shutil.which("tmux") is None, reason="Real tmux is required for story automation")
def test_managed_tmux_repeat_preserves_story_limits_and_original_evidence(tmp_path):
    candidate = isolated_candidate(tmp_path / "candidate")
    workflow = PublicWorkflow(candidate, tmp_path / "operator")
    try:
        first, _ = workflow.start(suite="stories", mode="protocol-fixture", story="coordination",
                                  max_sessions=1, max_turns=1, max_tool_calls=40)
        waited, first_result = workflow.command("wait", first)
        assert waited.returncode == 0, waited.stdout + waited.stderr
        assert first_result["proof"] == "passed"
        saved_plan = (first / "plan.json").read_bytes()
        saved_report = (first / "checks" / "report.json").read_bytes()
        second, _ = workflow.start(suite="stories", mode="protocol-fixture", source=first)
        waited, second_result = workflow.command("wait", second)
        assert waited.returncode == 0, waited.stdout + waited.stderr
        assert second_result["proof"] == "passed"
        assert (first / "plan.json").read_bytes() == saved_plan
        assert (first / "checks" / "report.json").read_bytes() == saved_report
        second_plan = json.loads((second / "plan.json").read_text())
        first_plan = json.loads(saved_plan)
        for key in ("stories", "max_sessions", "max_turns", "max_tool_calls"):
            assert second_plan[key] == first_plan[key]
        assert second_plan["parent_run_id"] == first.name
        assert first != second
        first_receipts = list((first / "checks").rglob("scenario.json"))
        second_receipts = list((second / "checks").rglob("scenario.json"))
        assert len(first_receipts) == len(second_receipts) == 1
        assert json.loads(first_receipts[0].read_text())["story"]["id"] == "coordination"
        assert json.loads(second_receipts[0].read_text())["story"]["id"] == "coordination"
    finally:
        workflow.close()


@pytest.mark.parametrize("story", STORIES)
@pytest.mark.skipif(shutil.which("tmux") is None, reason="Real tmux is required for story automation")
def test_named_story_runs_in_tmux_and_has_complete_named_evidence(tmp_path, story):
    result, _, receipt, lifecycle, directory = acceptance(tmp_path / "runs", story)
    assert result.returncode == 0, result.stdout + result.stderr
    completed_story(receipt, story)
    assert lifecycle["status"] == "passed"
    assert lifecycle["cleanup"]["verified"]
    assert not lifecycle["cleanup"]["forced"]
    assert directory is not None
    assert any(row["name"] == "tmux_worker_context" for row in receipt["checks"])
    if story == "effects-recovery":
        assert len((directory / "cli-effects.jsonl").read_text().splitlines()) == 1
        assert len((directory / "mcp-effects.jsonl").read_text().splitlines()) == 2
    if story == "work-acceptance":
        effects = [json.loads(line) for line in (directory / "measurement-effects.jsonl").read_text().splitlines()]
        assert [row["passed"] for row in effects] == [True, True, False]
        assert effects[0]["candidate"] == effects[1]["candidate"]
        assert effects[2]["candidate"] != effects[1]["candidate"]
        assert (directory / "product.txt").read_bytes() == b"broken\n"


@pytest.mark.skipif(shutil.which("tmux") is None, reason="Real tmux is required for story automation")
def test_budget_exhaustion_fails_story_and_still_cleans_owned_tmux_resources(tmp_path):
    result, _, receipt, lifecycle, _ = acceptance(tmp_path / "runs", "native-callback", "--max-tool-calls", "1")
    assert result.returncode != 0, result.stdout + result.stderr
    assert receipt["status"] == "failed"
    assert "budget" in receipt["error"]["code"]
    assert receipt["budget"]["consumed"]["tool_calls"] == 1
    assert receipt["budget"]["consumed"]["sessions"] == 0
    assert lifecycle["status"] == "failed"
    assert lifecycle["cleanup"]["verified"]


def test_native_unsupported_story_does_not_allocate_or_fallback(tmp_path):
    output = tmp_path / "must-not-exist"
    result, _, _, _, _ = acceptance(output, "coordination", "--profile", "native", "--config", str(tmp_path / "missing.json"),
                                  "--driver", "codex")
    assert result.returncode != 0
    assert not output.exists()
