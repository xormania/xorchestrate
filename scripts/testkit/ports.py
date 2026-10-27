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


class Cancellation(Protocol):
    """Return a run cancellation reason, or None while scheduling may continue."""

    def __call__(self) -> str | None: ...


class ResultSink(Protocol):
    """Persist a JSON-compatible transition before execution proceeds."""

    def __call__(self, event: Mapping[str, object]) -> None: ...

