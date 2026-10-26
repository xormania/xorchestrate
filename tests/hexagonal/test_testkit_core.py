"""Active contracts adapted from Agentscient testkit checks/world cases.

Source: uscient/agentscient@456457b9a0a9eb1ff285ee2d3e40535b6f722cdb,
ops/testkit/tests/{test_checks,test_world_daemon,test_cases}.py.
"""

from dataclasses import replace

import pytest

from scripts.testkit.core import CheckRecorder, ResourceScope, execute_checks, required_inputs
from scripts.testkit.model import Check, Result, Timeout


class Inputs:
    def __init__(self, present=()):
        self.present = set(present)
        self.calls = []

    def exists(self, path, *, cwd):
        self.calls.append((cwd, path))
        return path in self.present


class Runner:
    def __init__(self, failures=None, results=None):
        self.calls = []
        self.failures = failures or {}
        self.results = results or {}

    def run(self, argv, *, cwd, env, timeout):
        self.calls.append((argv, cwd, dict(env), timeout))
        if argv[0] in self.failures:
            raise self.failures[argv[0]]
        if argv[0] in self.results:
            return self.results[argv[0]]
        return Result(tuple(argv), 0, argv[0], "", 0.25, cwd)


GOOD = Check("first", ("tool", "one argument; untouched"), 2)


@pytest.mark.parametrize("plan", [
    (), (GOOD, GOOD), (replace(GOOD, name=""),),
    (GOOD, replace(GOOD, name="second", required=("",))),
    (replace(GOOD, argv=()),), (replace(GOOD, argv=("",)),),
    (replace(GOOD, timeout=0),), (replace(GOOD, timeout=float("inf")),),
    (replace(GOOD, timeout=float("nan")),), (replace(GOOD, timeout=True),),
    (replace(GOOD, required=("",)),),
    (replace(GOOD, required="path"),), (replace(GOOD, argv="tool"),),
    (None,),
], ids=["empty", "duplicate", "empty-name", "late-malformed-input", "empty-command",
        "empty-argument", "zero-timeout", "infinite-timeout", "nan-timeout",
        "boolean-timeout", "empty-input", "string-inputs", "string-command", "non-check"])
def test_invalid_whole_plan_launches_nothing(plan):
    runner, inputs, prepared = Runner(), Inputs(), []
    with pytest.raises(ValueError):
        execute_checks(plan, runner=runner, inputs=inputs, cwd="workspace", env={}, prepare=prepared.append)
    assert runner.calls == []
    assert inputs.calls == []
    assert prepared == []


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
    assert results["absent"].exit is None
    assert results["absent"].launched is False and results["absent"].status == "setup_error"
    assert "FileNotFoundError" in results["absent"].stderr
    assert results["slow"].exit == 3
    assert results["slow"].stdout == "partial output"
    assert results["slow"].stderr.startswith("partial error\nTimeout:")
    assert results["slow"].duration == 2.1
    assert results["slow"].launched is True and results["slow"].status == "timeout"
    assert results["after"].exit == 0


@pytest.mark.parametrize("fault", ["missing-input", "preparation", "invalid-prepared",
                                 "renamed-prepared", "new-missing-input"])
def test_unavailable_prerequisite_or_preparation_only_blocks_owning_check(fault):
    runner, inputs, prepared = Runner(), Inputs(), []
    checks = tuple(Check(name, (name,), 2) for name in ("before", "affected", "after"))
    if fault == "missing-input":
        checks = (checks[0], replace(checks[1], required=("absent",)), checks[2])

    def prepare(check):
        prepared.append(check.name)
        if check.name != "affected":
            return None
        if fault == "preparation":
            raise OSError("unable to prepare frozen input")
        if fault == "invalid-prepared":
            return replace(check, argv=())
        if fault == "renamed-prepared":
            return replace(check, name="unexpected")
        if fault == "new-missing-input":
            return replace(check, required=("new-absent",))
        return None

    results = dict(execute_checks(checks, runner=runner, inputs=inputs,
                                  cwd="workspace", env={}, prepare=prepare))
    assert list(results) == ["before", "affected", "after"]
    assert [call[0][0] for call in runner.calls] == ["before", "after"]
    assert results["before"].exit == results["after"].exit == 0
    affected = results["affected"]
    assert affected.exit is None and affected.launched is False and affected.status == "setup_error"
    assert affected.stderr and affected.stdout == "" and affected.duration == 0
    assert affected.raw_stdout is None and affected.raw_stderr is None
    assert ("affected" in prepared) == (fault != "missing-input")


def test_preparation_resolves_command_and_selects_runner_by_stable_id():
    captured, terminal, inputs = Runner(), Runner(), Inputs(("resolved-input",))
    checks = (Check("captured", ("initial",), 2), Check("interactive", ("live",), 3))

    def prepare(check):
        return replace(check, argv=("resolved", "input list"), required=("resolved-input",)) if check.name == "captured" else None

    results = dict(execute_checks(checks, runner=captured, inputs=inputs, cwd="workspace", env={},
                                  prepare=prepare, runners={"interactive": terminal}))
    assert captured.calls == [(("resolved", "input list"), "workspace", {}, 2)]
    assert terminal.calls == [(("live",), "workspace", {}, 3)]
    assert inputs.calls == [("workspace", "resolved-input")]
    assert results["captured"].argv == ("resolved", "input list")


@pytest.mark.parametrize("malformed", [None, "success", Result(("affected",), False, "", "", 0, "workspace"),
                                      Result(("affected",), 0, "", "", 0, "workspace", launched=False),
                                      Result(("affected",), 1, "", "", 0, "workspace", status="passed")])
def test_malformed_runner_result_cannot_pass_or_suppress_later_checks(malformed):
    runner = Runner(results={"affected": malformed})
    checks = tuple(Check(name, (name,), 2) for name in ("affected", "after"))
    results = dict(execute_checks(checks, runner=runner, inputs=Inputs(), cwd="workspace", env={}))
    assert results["affected"].exit is None
    assert results["affected"].status == "execution_error"
    assert results["after"].exit == 0


def test_readiness_probe_uses_the_same_required_inputs_as_execution():
    inputs = Inputs(("available",))
    check = replace(GOOD, required=("available", "missing"))
    assert required_inputs(check, inputs=inputs, cwd="workspace") == ("missing",)
    result = dict(execute_checks((check,), runner=Runner(), inputs=inputs, cwd="workspace", env={}))[check.name]
    assert result.status == "setup_error" and "missing" in result.stderr


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

