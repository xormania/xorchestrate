"""Option validation, JSON encoding and process-group shutdown for adapters.

Commands come exclusively from operator configuration, never remote metadata.
"""

from __future__ import annotations

import json
import math
import os
import signal
import subprocess
from typing import Any

from ..model import OrchestrationError, unique_keys
from ..worktrees import current_worktree


def positive_number(options: dict, name: str, default: float) -> float:
    value = options.get(name, default)
    try:
        valid = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and value > 0
    except OverflowError:
        valid = False
    if not valid:
        raise OrchestrationError(f"{name} must be a finite positive number.", "invalid_configuration")
    return float(value)


def positive_integer(options: dict, name: str, default: int) -> int:
    value = options.get(name, default)
    if type(value) is not int or value <= 0:
        raise OrchestrationError(f"{name} must be a positive integer.", "invalid_configuration")
    return value


REPO_PLACEHOLDER = "${repo}"


def resolve_repo(value: str) -> str:
    """Replace ``${repo}`` with the root of the worktree the current call came from.

    Rendering leaves ``${repo}`` unresolved because one project server serves
    every worktree of a project: a session started over a worktree's
    connection runs in that worktree. Without a bound worktree (construction-
    time validation, or a runtime built without a project) the value is
    returned unchanged, so a launch with an unresolved ``${repo}`` fails as an
    unavailable command rather than running somewhere else.
    """
    worktree = current_worktree() if REPO_PLACEHOLDER in value else None
    return value if worktree is None else value.replace(REPO_PLACEHOLDER, worktree["root"])


def command_options(options: dict, key: str = "command") -> dict:
    command = options.get(key)
    if not isinstance(command, list) or not command or any(not isinstance(arg, str) or "\0" in arg for arg in command) or not command[0]:
        raise OrchestrationError(f"{key} must be a nonempty argv list.", "invalid_configuration")
    cwd = options.get("cwd")
    if cwd is not None and (not isinstance(cwd, str) or not cwd or "\0" in cwd):
        raise OrchestrationError("cwd must be a directory path.", "invalid_configuration")
    environment = options.get("env", {})
    if not isinstance(environment, dict) or any(
        not isinstance(k, str) or not k or "=" in k or "\0" in k or not isinstance(v, str) or "\0" in v
        for k, v in environment.items()
    ):
        raise OrchestrationError("env must map environment variable names to strings.", "invalid_configuration")
    inherit = options.get("inherit_env", False)
    if not isinstance(inherit, bool):
        raise OrchestrationError("inherit_env must be boolean.", "invalid_configuration")
    pass_env = options.get("pass_env", [])
    if not isinstance(pass_env, list) or any(not isinstance(name, str) or not name or "=" in name or "\0" in name for name in pass_env):
        raise OrchestrationError("pass_env must list environment variable names.", "invalid_configuration")
    # The explicit default prevents unrelated host credentials reaching a tool.
    env = dict(os.environ) if inherit else {}
    env.update({name: os.environ[name] for name in pass_env if name in os.environ})
    env.update({name: resolve_repo(value) for name, value in environment.items()})
    return {"args": [resolve_repo(arg) for arg in command], "cwd": None if cwd is None else resolve_repo(cwd),
            "env": env, "shell": False, "start_new_session": os.name == "posix"}


def encode_json(value: Any, *, sort_keys: bool = False) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"), sort_keys=sort_keys).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise OrchestrationError("Value is not valid JSON.", "invalid_request", {"outcome": "not_started"}) from exc


def decode_json(data: bytes) -> Any:
    def invalid_constant(_):
        raise ValueError("Non-finite JSON number")

    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=unique_keys("Duplicate JSON property"), parse_constant=invalid_constant)
        # The parser can also produce infinity from a syntactically valid
        # exponent such as 1e999; parse_constant alone cannot reject that case.
        json.dumps(value, allow_nan=False)
        return value
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise OrchestrationError("Subprocess returned invalid JSON.", "provider_protocol") from exc


def _signal_group(process: subprocess.Popen, sig: int) -> None:
    try:
        if os.name == "posix":
            os.killpg(process.pid, sig)
        elif process.poll() is None:
            process.kill() if sig == signal.SIGKILL else process.terminate()
    except ProcessLookupError:
        pass


def close_process(process: subprocess.Popen, grace: float = 0.2) -> None:
    """Close stdin, then terminate and kill the owned process group if needed."""
    if process.stdin is not None:
        try:
            process.stdin.close()
        except (OSError, ValueError):
            pass
    try:
        process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        _signal_group(process, signal.SIGTERM)
        try:
            process.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            _signal_group(process, signal.SIGKILL)
            process.wait(timeout=max(grace, 1))
    # A child can survive its parent and retain our pipe descriptors.
    _signal_group(process, signal.SIGKILL)
    if process.stdout is not None:
        try:
            process.stdout.close()
        except (OSError, ValueError):
            pass

