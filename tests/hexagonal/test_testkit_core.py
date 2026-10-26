"""Active contracts adapted from Agentscient testkit checks/world cases.

Source: uscient/agentscient@456457b9a0a9eb1ff285ee2d3e40535b6f722cdb,
ops/testkit/tests/{test_checks,test_world_daemon,test_cases}.py.
"""

from dataclasses import replace

import pytest

from scripts.testkit.core import CheckRecorder, ResourceScope, execute_checks
from scripts.testkit.model import Check, Result, Timeout


class Inputs:
    def __init__(self, present=()):
        self.present = set(present)
        self.calls = []

    def exists(self, path, *, cwd):
        self.calls.append((cwd, path))
        return path in self.present


class Runner:
    def __init__(self, failures=None):
        self.calls = []
        self.failures = failures or {}

    def run(self, argv, *, cwd, env, timeout):
        self.calls.append((argv, cwd, dict(env), timeout))
        if argv[0] in self.failures:
            raise self.failures[argv[0]]
        return Result(tuple(argv), 0, argv[0], "", 0.25, cwd)


GOOD = Check("first", ("tool", "one argument; untouched"), 2)


@pytest.mark.parametrize("plan", [
    (), (GOOD, GOOD), (replace(GOOD, name=""),),
    (GOOD, replace(GOOD, name="second", required=("absent",))),
    (replace(GOOD, argv=()),), (replace(GOOD, argv=("",)),),
    (replace(GOOD, timeout=0),), (replace(GOOD, timeout=float("inf")),),
    (replace(GOOD, timeout=float("nan")),), (replace(GOOD, timeout=True),),
    (replace(GOOD, required=("",)),),
], ids=["empty", "duplicate", "empty-name", "late-missing-input", "empty-command",
        "empty-argument", "zero-timeout", "infinite-timeout", "nan-timeout",
        "boolean-timeout", "empty-input"])
def test_invalid_whole_plan_launches_nothing(plan):
    runner = Runner()
    with pytest.raises(ValueError):
        execute_checks(plan, runner=runner, inputs=Inputs(), cwd="workspace", env={})
    assert runner.calls == []


def test_every_named_check_gets_same_declared_context_once_and_retains_result():
    runner, inputs = Runner(), Inputs(("fixture",))
    checks = [replace(GOOD, required=("fixture",)), Check("second", ("other",), 5)]
    results = execute_checks(checks, runner=runner, inputs=inputs,
                             cwd="workspace", env={"CONFIG": "selected"})
    assert runner.calls == [(c.argv, "workspace", {"CONFIG": "selected"}, c.timeout) for c in checks]
    assert inputs.calls == [("workspace", "fixture")]
    assert [name for name, _ in results] == [c.name for c in checks]
    for check, (_, result) in zip(checks, results):
        assert result == Result(check.argv, 0, check.argv[0], "", 0.25, "workspace")
        assert result.returncode == result.exit


def test_missing_command_and_timeout_remain_failed_rows_and_do_not_hide_later_checks():
    timeout = Timeout(("slow",), cwd="workspace", timeout=2,
                      stdout="partial output", stderr="partial error\n", duration=2.1)
    runner = Runner({"absent": FileNotFoundError("no executable"), "slow": timeout})
    checks = [Check(name, (name,), 2) for name in ("absent", "slow", "after")]
    results = dict(execute_checks(checks, runner=runner, inputs=Inputs(), cwd="workspace", env={}))
    assert list(results) == ["absent", "slow", "after"]
    assert results["absent"].exit == 3
    assert "FileNotFoundError" in results["absent"].stderr
    assert results["slow"].exit == 3
    assert results["slow"].stdout == "partial output"
    assert results["slow"].stderr.startswith("partial error\nTimeout:")
    assert results["slow"].duration == 2.1
    assert results["after"].exit == 0


def test_resources_close_in_reverse_order_once_despite_all_failure_types():
    scope, called = ResourceScope(), []

    def cleanup(name, error=None):
        def close():
            called.append(name)
            if error:
                raise error
        return close

    scope.defer("oldest", cleanup("oldest"))
    released = scope.defer("early", cleanup("early", OSError("early failed")))
    scope.defer("middle", cleanup("middle", SystemExit("exit during cleanup")))
    scope.defer("newest", cleanup("newest", RuntimeError("newest failed")))
    error = scope.release(released)
    assert error == {"name": "early", "type": "OSError", "diagnostic": "early failed"}
    errors = scope.close()
    assert called == ["early", "newest", "middle", "oldest"]
    assert [error["name"] for error in errors] == ["early", "newest", "middle"]
    assert scope.close() == errors
    assert called == ["early", "newest", "middle", "oldest"]
    with pytest.raises(ValueError):
        scope.defer("late", lambda: None)
    with pytest.raises(ValueError):
        scope.release(released)


def test_successful_explicit_release_is_not_repeated_by_close():
    scope, called = ResourceScope(), []
    token = scope.defer("resource", lambda: called.append("closed"))
    assert scope.release(token) is None
    assert scope.close() == []
    assert called == ["closed"]


class Clock:
    def __init__(self):
        self.value = 0.0

    def monotonic(self):
        return self.value


def test_scenario_recorder_preserves_failure_and_timing_without_swallowing_it():
    clock = Clock()
    recorder = CheckRecorder(clock)
    with recorder.check("setup"):
        clock.value = 1.25
    with pytest.raises(ValueError, match="scenario failed"):
        with recorder.check("behavior"):
            clock.value = 2.75
            raise ValueError("scenario failed")
    assert recorder.records is recorder.checks
    assert recorder.checks == [
        {"name": "setup", "status": "passed", "duration_seconds": 1.25},
        {"name": "behavior", "status": "failed", "duration_seconds": 1.5,
         "failure": {"type": "ValueError", "diagnostic": "scenario failed"}},
    ]
    for name in ("setup", ""):
        with pytest.raises(ValueError):
            with recorder.check(name):
                pytest.fail("invalid scenario check entered")
    assert len(recorder.checks) == 2

