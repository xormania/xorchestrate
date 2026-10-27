"""Reusable check execution, scenario recording and resource ownership.

Check preflight and named results adapt Agentscient ops/testkit/checks.py at
456457b9a0a9eb1ff285ee2d3e40535b6f722cdb. Resource ownership preserves its
World.close lesson: register allocations, drain in reverse, retain failures.
Infrastructure and scenario assertions are supplied through ports/callables.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager

from .model import Cancelled, Check, Result, Timeout
from .ports import Cancellation, Clock, CommandRunner, InputProbe, ResultSink


def validate_checks(checks: Sequence[Check]) -> tuple[Check, ...]:
    """Reject structural errors before any readiness probes or preparation."""
    checks = tuple(checks)
    if not checks:
        raise ValueError("check plan must not be empty")
    if any(not isinstance(check, Check) for check in checks):
        raise ValueError("check plan must contain Check values")
    names = [check.name for check in checks]
    if any(not isinstance(name, str) or not name for name in names) or len(set(names)) != len(names):
        raise ValueError("check names must be unique nonempty strings")
    for check in checks:
        if (not isinstance(check.argv, (tuple, list)) or not check.argv
                or any(not isinstance(arg, str) or not arg for arg in check.argv)):
            raise ValueError(f"check {check.name}: argv must contain nonempty strings")
        if (isinstance(check.timeout, bool) or not isinstance(check.timeout, (int, float))
                or not math.isfinite(check.timeout) or check.timeout <= 0):
            raise ValueError(f"check {check.name}: timeout must be finite and positive")
        if (not isinstance(check.required, (tuple, list))
                or any(not isinstance(path, str) or not path for path in check.required)):
            raise ValueError(f"check {check.name}: required inputs must be nonempty paths")
    return checks


def required_inputs(check: Check, *, inputs: InputProbe, cwd: str) -> tuple[str, ...]:
    """Return absent prerequisites; missing inputs are readiness, not structure."""
    return tuple(path for path in check.required if not inputs.exists(path, cwd=cwd))


def execute_checks(checks: Sequence[Check], *, runner: CommandRunner,
                   inputs: InputProbe, cwd: str, env: Mapping[str, str],
                   prepare: Callable[[Check], Check | None] | None = None,
                   runners: Mapping[str, CommandRunner] | None = None,
                   cancellation: Cancellation | None = None,
                   result_sink: ResultSink | None = None):
    """Validate structure once, then retain each check's independent outcome.

    Readiness and preparation failures belong only to the affected check and
    have no invented process exit. Preparation may bind the same check ID to
    resolved inputs; process/terminal policy is supplied by runner adapters.
    """
    checks = validate_checks(checks)
    results = []
    revision = 0

    def publish(event, **values):
        nonlocal revision
        revision += 1
        if result_sink is not None:
            result_sink({"event": event, "revision": revision, **values})

    def retain(check, result):
        results.append((check.name, result))
        publish("completed", check=check.name, status=result.status,
                exit_status=result.exit, launched=result.launched,
                reason=result.stderr if result.exit != 0 else "",
                shutdown=result.shutdown, validation="pending")

    publish("inventory", checks=[{"name": c.name, "argv": list(c.argv),
                                 "timeout": c.timeout, "required": list(c.required)} for c in checks])
    for check in checks:
        reason = cancellation() if cancellation is not None else None
        if reason:
            retain(check, Result(tuple(check.argv), None, "", str(reason), 0.0, str(cwd),
                                 launched=False, status="not_run",
                                 shutdown={"scope": "run", "reason": str(reason), "forced": False}))
            continue
        try:
            missing = required_inputs(check, inputs=inputs, cwd=cwd)
            if missing:
                raise ValueError(f"required inputs absent: {list(missing)}")
            if prepare is not None:
                prepared = prepare(check)
                if prepared is not None:
                    validate_checks((prepared,))
                    if prepared.name != check.name:
                        raise ValueError("preparation must preserve the check ID")
                    if prepared.required != check.required:
                        missing = required_inputs(prepared, inputs=inputs, cwd=cwd)
                        if missing:
                            raise ValueError(f"required inputs absent: {list(missing)}")
                    check = prepared
        except Cancelled as exc:
            result = Result(tuple(check.argv), None, exc.stdout, exc.stderr or str(exc),
                            exc.duration, str(cwd), raw_stdout=exc.raw_stdout,
                            raw_stderr=exc.raw_stderr, launched=exc.launched,
                            status="cancelled", shutdown=exc.shutdown)
            publish("readiness", check=check.name, status="cancelled")
            retain(check, result)
            continue
        except Exception as exc:
            result = Result(tuple(check.argv), None, "", f"{type(exc).__name__}: {exc}\n", 0.0, str(cwd),
                            launched=False, status="setup_error")
            publish("readiness", check=check.name, status="setup_error", reason=result.stderr)
            retain(check, result)
            continue
        publish("readiness", check=check.name, status="passed")
        # Preparation may itself run bounded probes. A request arriving there
        # must prevent the main command as well as every subsequent check.
        reason = cancellation() if cancellation is not None else None
        if reason:
            retain(check, Result(tuple(check.argv), None, "", str(reason), 0.0, str(cwd),
                                 launched=False, status="not_run",
                                 shutdown={"scope": "run", "reason": str(reason), "forced": False}))
            continue
        publish("started", check=check.name, argv=list(check.argv), launched=None, phase="launch_requested")
        try:
            selected_runner = runners.get(check.name, runner) if runners is not None else runner
            result = selected_runner.run(check.argv, cwd=cwd, env=env, timeout=check.timeout)
            if not isinstance(result, Result):
                raise TypeError("command runner must return Result")
            if (result.exit is not None and
                    (isinstance(result.exit, bool) or not isinstance(result.exit, int))):
                raise ValueError("command result exit status must be an integer or None")
            if result.exit == 0 and result.launched is not True:
                raise ValueError("an unlaunched command cannot have a successful process exit")
            if result.status == "passed" and result.exit != 0:
                raise ValueError("a successful outcome requires a zero process exit")
        except Exception as exc:
            interrupted = isinstance(exc, (Timeout, Cancelled))
            stdout = exc.stdout if interrupted else ""
            stderr = exc.stderr if interrupted else ""
            duration = exc.duration if interrupted else 0.0
            diagnostic = f"{type(exc).__name__}: {exc}\n"
            timeout = isinstance(exc, Timeout)
            cancelled = isinstance(exc, Cancelled)
            setup = isinstance(exc, OSError) and not timeout
            result = Result(tuple(check.argv), 3 if timeout else None,
                            stdout, stderr + diagnostic, duration, str(cwd),
                            raw_stdout=exc.raw_stdout if interrupted else None,
                            raw_stderr=exc.raw_stderr if interrupted else None,
                            launched=exc.launched if cancelled else True if timeout else False if setup else None,
                            status="timeout" if timeout else "cancelled" if cancelled else (
                                "setup_error" if setup else "execution_error"),
                            shutdown=exc.shutdown if interrupted else None)
        retain(check, result)
    return results


class ResourceScope:
    """Own cleanup once; a failed explicit release remains a failed close."""

    def __init__(self):
        self._resources: dict[int, tuple[str, Callable[[], object]]] = {}
        self._next_token = 0
        self._errors: list[dict[str, str]] = []
        self._closed = False

    def defer(self, name: str, callback: Callable[[], object]) -> int:
        if self._closed:
            raise ValueError("resource scope is closed")
        if not isinstance(name, str) or not name or not callable(callback):
            raise ValueError("resource cleanup needs a name and callable")
        token = self._next_token
        self._next_token += 1
        self._resources[token] = (name, callback)
        return token

    def release(self, token: int):
        if token not in self._resources:
            raise ValueError("resource token is not owned by this scope")
        name, callback = self._resources.pop(token)
        try:
            callback()
        except BaseException as exc:
            # Cleanup must drain even when a callback exits or is interrupted.
            # The owning runner decides its final status from retained errors.
            error = {"name": name, "type": type(exc).__name__, "diagnostic": str(exc)}
            self._errors.append(error)
            return dict(error)
        return None

    def close(self) -> list[dict[str, str]]:
        self._closed = True
        for token in reversed(tuple(self._resources)):
            self.release(token)
        return [dict(error) for error in self._errors]


class CheckRecorder:
    """Retain named scenario outcomes without knowing the scenario's domain."""

    def __init__(self, clock: Clock):
        self.clock = clock
        self.checks: list[dict] = []

    @property
    def records(self):
        return self.checks

    @contextmanager
    def check(self, name: str):
        if not isinstance(name, str) or not name or any(row["name"] == name for row in self.checks):
            raise ValueError("scenario checks need unique nonempty names")
        started = self.clock.monotonic()
        record = {"name": name, "status": "running", "duration_seconds": 0.0}
        self.checks.append(record)
        try:
            yield
        except BaseException as exc:
            record.update(status="failed", failure={"type": type(exc).__name__, "diagnostic": str(exc)})
            raise
        else:
            record["status"] = "passed"
        finally:
            record["duration_seconds"] = self.clock.monotonic() - started

