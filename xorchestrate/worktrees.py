"""The worktree a call came from, bound for the duration of that call.

One project server serves every git worktree of a project (D11). Input
adapters bind the caller's worktree identity to each application call the way
they bind the actor; the application puts it in the guardrail context, in
created records and events, and in telemetry, and adapters resolve
``${repo}`` against its root when they launch a process for that call.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from typing import Any

WORKTREE_FIELDS = ("root", "name", "branch", "config_hash")

_CALL_WORKTREE: ContextVar = ContextVar("xorchestrate_call_worktree", default=None)
_SESSION_WORKTREE: ContextVar = ContextVar("xorchestrate_session_worktree", default=None)


def worktree_identity(value: Any) -> dict | None:
    """Return the closed worktree identity fields of ``value``, or ``None`` when absent or malformed."""
    if not isinstance(value, dict) or not isinstance(value.get("root"), str) or not value["root"]:
        return None
    return {key: value[key] for key in WORKTREE_FIELDS if isinstance(value.get(key), str)}


def session_worktree(session: Any) -> dict | None:
    """The worktree a session record runs in: its ``workspace``, else the worktree that created it."""
    if not isinstance(session, dict):
        return None
    return worktree_identity(session.get("workspace")) or worktree_identity(session.get("created_worktree"))


def current_worktree() -> dict | None:
    """The worktree bound to the application call running in this context, if any."""
    value = _CALL_WORKTREE.get()
    return deepcopy(value) if value is not None else None


def workspace_root() -> str | None:
    """The root ``${repo}`` resolves to for a process launched now.

    A session's own worktree when an existing session is relaunched, resumed,
    reconnected or driven; otherwise the calling worktree.
    """
    for variable in (_SESSION_WORKTREE, _CALL_WORKTREE):
        value = variable.get()
        if value is not None:
            return value["root"]
    return None


@contextmanager
def session_workspace(worktree: dict | None):
    """Resolve ``${repo}`` to an existing session's creating worktree while its driver runs.

    ``None`` (a session recorded without a worktree) leaves the calling worktree in effect.
    """
    identity = worktree_identity(worktree)
    token = _SESSION_WORKTREE.set(identity) if identity is not None else None
    try:
        yield
    finally:
        if token is not None:
            _SESSION_WORKTREE.reset(token)


@contextmanager
def bound_worktree(worktree: dict | None):
    """Bind ``worktree`` for the duration of one application call in this context."""
    token = _CALL_WORKTREE.set(deepcopy(worktree) if worktree is not None else None)
    try:
        yield
    finally:
        _CALL_WORKTREE.reset(token)
