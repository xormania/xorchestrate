"""Infrastructure contracts for the test-kit application services."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from .model import Result


class CommandRunner(Protocol):
    def run(self, argv: Sequence[str], *, cwd: str, env: Mapping[str, str],
            timeout: float) -> Result: ...


class InputProbe(Protocol):
    def exists(self, path: str, *, cwd: str) -> bool: ...


class Clock(Protocol):
    def monotonic(self) -> float: ...

