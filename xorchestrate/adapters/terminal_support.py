"""Mechanism-neutral helpers shared by terminal host adapters.

Process identities come from ``/proc`` (pid, start time, boot) so that a
signal or an adoption is fenced against PID reuse. Nothing here is exposed
through the terminal host port; callers see only opaque receipts.
"""

from __future__ import annotations

import errno
import os
import re
import signal
import time
from dataclasses import dataclass
from pathlib import Path

from ..model import OrchestrationError, public_error_code
from ..ports import TerminalCommand

NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]{0,63}")
ENVIRONMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def check_name(name, what="terminal name"):
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise OrchestrationError(f"Invalid {what}.", "invalid_arguments", {"name": str(name)[:64]})
    return name


def check_command(command, *, remove=()):
    """Validate a command; return ``(argv, cwd, environment dict)`` with ``remove`` names dropped."""
    if not isinstance(command, TerminalCommand):
        raise OrchestrationError("command must be a TerminalCommand.", "invalid_arguments")
    argv = command.argv
    if (not isinstance(argv, tuple) or not argv or any(not isinstance(item, str) or "\0" in item for item in argv)
            or "=" in argv[0]):
        raise OrchestrationError("command argv must be a non-empty tuple of strings.", "invalid_arguments")
    if not isinstance(command.cwd, str) or not os.path.isabs(command.cwd) or not os.path.isdir(command.cwd):
        raise OrchestrationError("command cwd must be an existing absolute directory.", "invalid_arguments")
    environment = {}
    for pair in command.environment:
        if (not isinstance(pair, tuple) or len(pair) != 2 or not isinstance(pair[0], str)
                or not ENVIRONMENT_NAME.fullmatch(pair[0]) or not isinstance(pair[1], str) or "\0" in pair[1]):
            raise OrchestrationError("command environment must be (name, value) string pairs.", "invalid_arguments")
        environment[pair[0]] = pair[1]
    for name in remove:
        environment.pop(name, None)
    for value in (command.rows, command.columns):
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 1000):
            raise OrchestrationError("rows and columns must be integers from 1 to 1000.", "invalid_arguments")
    return list(argv), command.cwd, environment


def check_lines(lines, cap):
    if lines is None:
        return cap
    if isinstance(lines, bool) or not isinstance(lines, int) or lines < 1:
        raise OrchestrationError("lines must be a positive integer.", "invalid_arguments")
    return min(lines, cap)


def check_grace(grace, default):
    if grace is None:
        return default
    if isinstance(grace, bool) or not isinstance(grace, (int, float)) or not 0 <= grace <= 3600:
        raise OrchestrationError("grace must be a number of seconds from 0 to 3600.", "invalid_arguments")
    return float(grace)


def last_lines(text, lines):
    rows = text.rstrip("\n").split("\n") if text.strip() else []
    while rows and not rows[-1].strip():
        rows.pop()
    return "\n".join(rows[-lines:])


@dataclass(frozen=True)
class Identity:
    pid: int
    started: int
    boot_id: str

    def as_json(self):
        return {"pid": self.pid, "started": self.started, "boot_id": self.boot_id}

    @classmethod
    def from_json(cls, value):
        if (not isinstance(value, dict) or type(value.get("pid")) is not int or type(value.get("started")) is not int
                or not isinstance(value.get("boot_id"), str)):
            return None
        return cls(value["pid"], value["started"], value["boot_id"])


def boot_id():
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return ""


def _stat(pid):
    """``(state, session, started, uid)`` of ``pid``, or ``None`` when it is gone."""
    try:
        path = Path(f"/proc/{pid}")
        fields = (path / "stat").read_text().rsplit(") ", 1)[1].split()
        return fields[0], int(fields[3]), int(fields[19]), path.stat().st_uid
    except (OSError, ValueError, IndexError):
        return None


def identity(pid):
    """The identity of a live process this user owns, or ``None``."""
    current = _stat(pid)
    if current is None or current[0] == "Z" or current[3] != os.getuid():
        return None
    return Identity(pid, current[2], boot_id())


def alive(expected):
    return expected is not None and identity(expected.pid) == expected


def session_members(leader):
    """Live processes in the terminal session that ``leader`` started (it included)."""
    members = []
    for path in Path("/proc").glob("[0-9]*"):
        current = _stat(int(path.name))
        if (current is not None and current[1] == leader.pid and current[0] != "Z"
                and current[2] >= leader.started and current[3] == os.getuid()):
            members.append(Identity(int(path.name), current[2], leader.boot_id))
    return members


def send_signal(expected, number):
    """Signal ``expected`` only while it is still that process; pin it with a pidfd where supported."""
    if not alive(expected):
        return False
    descriptor = None
    try:
        if hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal"):
            try:
                descriptor = os.pidfd_open(expected.pid)
            except OSError:
                descriptor = None
        if not alive(expected):
            return False
        if descriptor is not None:
            signal.pidfd_send_signal(descriptor, number)
        else:
            os.kill(expected.pid, number)
        return True
    except ProcessLookupError:
        return False
    finally:
        if descriptor is not None:
            os.close(descriptor)


def stop_session(leader, grace, *, poll=0.02, straggler_grace=1.0):
    """Stop ``leader``'s session in order. Return whether none of its members remain.

    The terminal's own command gets the whole grace period first: TERM to the
    leader alone, so it can stop its children itself. Members still left once
    the leader has gone (or the grace has passed) get TERM, a short wait, then
    KILL.
    """
    current = _stat(leader.pid)
    if current is not None and current[2] != leader.started:
        return False  # The leader's pid was reused; its session id now names someone else's work.
    if send_signal(leader, signal.SIGTERM):
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline and alive(leader):
            time.sleep(poll)
    for number, wait in ((signal.SIGTERM, straggler_grace), (signal.SIGKILL, 2.0)):
        members = session_members(leader)
        if not members:
            return True
        for member in members:
            send_signal(member, number)
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline and session_members(leader):
            time.sleep(poll)
    return not session_members(leader)


def private_directory(path):
    """Create (0700) and check a private location under this user's control.

    An unsafe location is ``terminal_host_unavailable`` (a code the port lists), with the reason in details.
    """
    problem = location_problem(path)
    if problem is not None:
        raise OrchestrationError(f"The terminal host location is unsafe: {problem}.", "terminal_host_unavailable",
                                 {"location": str(path), "reason": problem})
    path = Path(path)
    missing = [path, *(parent for parent in path.parents if not parent.exists())]
    for directory in reversed(missing):  # mkdir(parents=True) would give the parents the umask's mode.
        directory.mkdir(mode=0o700, exist_ok=True)
    metadata = path.stat()
    if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
        raise OrchestrationError("The terminal host location is not private to this user.",
                                 "terminal_host_unavailable", {"location": str(path)})
    return path


def location_problem(path):
    """Why ``path`` is not a safe private location, or ``None``."""
    from .local_socket import check_private_location

    try:
        check_private_location(Path(path))
        if Path(path).exists():
            metadata = Path(path).stat()
            if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
                return "the location is not private to this user"
    except OrchestrationError as error:
        return str(error)
    except OSError as error:
        return f"the location could not be checked ({os_error_code(error)})"
    return None


def log_path(log):
    """A terminal's log: an absolute path in an existing private directory of this user."""
    if not isinstance(log, str) or not os.path.isabs(log):
        raise OrchestrationError("log must be an absolute path.", "invalid_arguments")
    path = Path(log)
    if not path.parent.is_dir() or location_problem(path.parent) is not None:
        raise OrchestrationError("The log must be in an existing private directory.", "invalid_arguments",
                                 {"location": str(path.parent)})
    return path


def os_error_code(error: BaseException, default: str | None = "external_error") -> str | None:
    """The errno name (``ENOENT``) of a failed local OS call, else ``public_error_code``.

    For the terminal hosts' own file, process and socket calls: errno names are a
    closed, non-sensitive vocabulary there, so they keep useful host diagnostics
    without exposing an exception's class name or text. Not for library errors
    that reuse ``errno`` for their own codes (``ssl.SSLError``).
    """
    if isinstance(error, OSError) and isinstance(error.errno, int) and error.errno in errno.errorcode:
        return errno.errorcode[error.errno]
    return public_error_code(error, default)
