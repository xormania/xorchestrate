"""The effects the work-tree tool depends on, as protocols its adapters implement."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from typing import Any, Protocol

from scripts.worktrees.model import Proc, ServerState


class GitError(Exception):
    def __init__(self, args: Sequence[str], status: int, stderr: str) -> None:
        super().__init__(f"git {' '.join(args)} exited {status}: {stderr.strip()}")
        self.args_ = list(args)
        self.status = status
        self.stderr = stderr


class TransportError(Exception):
    """The MCP endpoint did not answer: connection refused, an HTTP error or an unreadable body."""


class Git(Protocol):
    def run(self, args: Sequence[str], cwd: str) -> str:
        """Run git; return stdout without its trailing newline, raise GitError on a non-zero exit."""


class ProcessTable(Protocol):
    def processes(self) -> list[Proc]: ...

    def signal_group(self, pgid: int, sig: int) -> None: ...

    def alive(self, pid: int) -> bool: ...


class Launcher(Protocol):
    def spawn(self, argv: Sequence[str], cwd: str, env: Mapping[str, str], log: str) -> int:
        """Start ``argv`` in a new session with output to ``log``; raise FileNotFoundError if it cannot be found."""


class McpTransport(Protocol):
    def post(self, url: str, body: Mapping[str, Any], session: str | None,
             timeout: float | None = None) -> tuple[str | None, list[dict[str, Any]]]:
        """POST one JSON-RPC message within ``timeout`` seconds; return the session id and the messages received."""

    def delete(self, url: str, session: str) -> None: ...


class Clock(Protocol):
    def now(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


class StateStore(Protocol):
    def save(self, key: str, state: ServerState) -> None: ...

    def load(self, key: str) -> ServerState | None: ...

    def delete(self, key: str) -> None: ...

    def list(self) -> list[tuple[str, ServerState]]: ...

    def lock(self, key: str) -> AbstractContextManager[None]:
        """Exclusive access to one key across processes, held for the ``with`` block."""


class FileSystem(Protocol):
    def is_dir(self, path: str) -> bool: ...

    def exists(self, path: str) -> bool: ...

    def read_text(self, path: str) -> str | None:
        """The file's text, or None when it cannot be read."""

    def make_dirs(self, path: str) -> None: ...

    def write_text(self, path: str, text: str) -> None: ...

    def real_path(self, path: str) -> str:
        """``path`` made absolute with every symbolic link resolved (missing parts kept as given)."""


class PortPicker(Protocol):
    def __call__(self) -> int:
        """A currently free local TCP port."""
