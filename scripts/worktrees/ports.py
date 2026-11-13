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

    def list_dir(self, path: str) -> list[str]:
        """Names of the entries in ``path``; empty when it cannot be read."""

    def mtime(self, path: str) -> float | None: ...

    def is_symlink(self, path: str) -> bool: ...

    def write_text(self, path: str, text: str) -> None: ...

    def remove_tree(self, path: str) -> None:
        """Delete a directory tree without following symbolic links out of it."""

    def archive(self, directory: str, tar_path: str, arcname: str) -> None:
        """Write ``directory`` into a new tar file whose members are rooted at ``arcname``."""

    def find_dirs(self, root: str, names: frozenset[str]) -> list[str]:
        """Directories under ``root`` with one of ``names``, not following links and not entering .git."""

    def real_path(self, path: str) -> str:
        """``path`` made absolute with every symbolic link resolved (missing parts kept as given)."""


class Github(Protocol):
    def merged_pr_head(self, branch: str) -> str | None:
        """The head commit of a merged pull request from ``branch``, or None; may raise when unavailable."""


class DiskUsage(Protocol):
    def size(self, path: str) -> int:
        """Apparent size in bytes of the regular files under ``path``, not following links."""


class PortPicker(Protocol):
    def __call__(self) -> int:
        """A currently free local TCP port."""
