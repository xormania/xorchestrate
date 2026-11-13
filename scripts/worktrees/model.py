"""Values of the work-tree tool; no effects."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Proc:
    """One process as the process table reports it."""

    pid: int
    ppid: int
    pgid: int
    cwd: str
    argv: tuple[str, ...]
    open_paths: tuple[str, ...]
    start_time: int | None = None


@dataclass(frozen=True)
class Tree:
    path: str
    branch: str | None
    head: str | None
    main: bool = False
    locked: bool = False


@dataclass(frozen=True)
class ServerState:
    """A per-tree server, recorded only once it has answered a real symbol query."""

    tree: str
    pid: int
    pgid: int
    port: int
    head: str
    version: str
    started: float
    last_call: float | None
    argv: tuple[str, ...] = ()
    start_time: int | None = None


@dataclass(frozen=True)
class Refusal:
    code: str
    detail: str

    def line(self) -> str:
        return f"{self.code}: {self.detail}"


@dataclass(frozen=True)
class Config:
    """The tool's resolved settings."""

    root: str
    state_dir: str
    base: str | None
    serena_version: str
    serena_context: str
    serena_timeout: float
    serena_command: tuple[str, ...]
    probe_tool: str | None
    probe_args: dict[str, Any] | None
    allow_edits: bool = False


@dataclass
class Outcome:
    """What a command reports: an exit status, lines for stdout and lines for stderr."""

    code: int = 0
    out: list[str] = field(default_factory=list)
    err: list[str] = field(default_factory=list)

    def refuse(self, refusal: Refusal) -> Outcome:
        self.code = max(self.code, 1)
        self.err.append(refusal.line())
        return self
