"""Domain-free test values adapted from Agentscient's command and checks kit.

Source: uscient/agentscient@456457b9a0a9eb1ff285ee2d3e40535b6f722cdb,
ops/testkit/{command,checks}.py. Historical refusal-wire policy is not required.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Check:
    name: str
    argv: tuple[str, ...]
    timeout: float
    required: tuple[str, ...] = ()


@dataclass(frozen=True)
class Result:
    argv: tuple[str, ...]
    exit: int
    stdout: str
    stderr: str
    duration: float
    cwd: str
    raw_stdout: bytes | None = None
    raw_stderr: bytes | None = None

    @property
    def returncode(self) -> int:
        """The subprocess spelling, without a second result representation."""
        return self.exit


class Timeout(TimeoutError):
    """A command exceeded its bound; retain its partial output for evidence."""

    def __init__(self, argv, *, cwd, timeout, stdout="", stderr="", duration=0.0,
                 raw_stdout=None, raw_stderr=None):
        self.argv = tuple(argv)
        self.cwd = str(cwd)
        self.timeout = timeout
        self.stdout = stdout
        self.stderr = stderr
        self.duration = duration
        self.raw_stdout = raw_stdout
        self.raw_stderr = raw_stderr
        super().__init__(f"command outlived {timeout:g}s; killed and reaped")

