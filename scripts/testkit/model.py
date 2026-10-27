"""Domain-free test values adapted from Agentscient's command and checks kit.

Source: uscient/agentscient@456457b9a0a9eb1ff285ee2d3e40535b6f722cdb,
ops/testkit/{command,checks}.py. Historical refusal-wire policy is not required.
"""

from __future__ import annotations

from dataclasses import dataclass

COMPONENT_SUITES = ("static", "tests", "calibration", "acceptance", "multi-harness")
SUITES = (*COMPONENT_SUITES, "checks", "all")
MODES = ("fixture", "protocol-fixture", "native")


@dataclass(frozen=True)
class Check:
    name: str
    argv: tuple[str, ...]
    timeout: float
    required: tuple[str, ...] = ()


@dataclass(frozen=True)
class Result:
    argv: tuple[str, ...]
    exit: int | None
    stdout: str
    stderr: str
    duration: float
    cwd: str
    raw_stdout: bytes | None = None
    raw_stderr: bytes | None = None
    launched: bool | None = True
    status: str = "completed"
    shutdown: dict | None = None

    @property
    def returncode(self) -> int | None:
        """The subprocess spelling, without a second result representation."""
        return self.exit


class Timeout(TimeoutError):
    """A command exceeded its bound; retain its partial output for evidence."""

    def __init__(self, argv, *, cwd, timeout, stdout="", stderr="", duration=0.0,
                 raw_stdout=None, raw_stderr=None, shutdown=None):
        self.argv = tuple(argv)
        self.cwd = str(cwd)
        self.timeout = timeout
        self.stdout = stdout
        self.stderr = stderr
        self.duration = duration
        self.raw_stdout = raw_stdout
        self.raw_stderr = raw_stderr
        self.shutdown = shutdown
        super().__init__(f"command outlived {timeout:g}s; shut down and reaped")


class Cancelled(RuntimeError):
    """A run-wide request interrupted one command; its evidence stays available."""

    def __init__(self, argv, *, cwd, reason, stdout="", stderr="", duration=0.0,
                 raw_stdout=None, raw_stderr=None, shutdown=None, launched=True):
        self.argv, self.cwd, self.reason = tuple(argv), str(cwd), str(reason)
        self.stdout, self.stderr, self.duration = stdout, stderr, duration
        self.raw_stdout, self.raw_stderr, self.shutdown = raw_stdout, raw_stderr, shutdown
        self.launched = launched
        super().__init__(f"run cancelled: {self.reason}")

