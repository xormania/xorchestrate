"""Resolve and attach only run-owned, currently live interactive terminals."""

from __future__ import annotations

import json
import os
import select
import socket
import stat
import subprocess
import sys
import termios
import tty
from pathlib import Path

from .ownership import Identity, alive

HOSTED_SCHEMA = 2
"""Terminal-host descriptors carry an opaque receipt and the host's attach argv (schema 1 named a socket and binary)."""


def _live_identity(identity):
    try:
        return (identity["uid"] == os.getuid()
                and identity["boot_id"] == Path("/proc/sys/kernel/random/boot_id").read_text().strip()
                and alive(Identity(identity["pid"], identity["started"])))
    except (KeyError, TypeError, ValueError, OSError):
        return False


def refusal(descriptor):
    """Why a descriptor's shape is not accepted at all (``None`` when it is), before any liveness check."""
    kind, schema = descriptor.get("kind"), descriptor.get("schema_version")
    if kind == "terminal_host":
        return None if schema == HOSTED_SCHEMA else f"Unsupported terminal descriptor schema {schema!r}; expected 2"
    if kind == "pty":
        return None if schema == 1 else f"Unsupported native terminal descriptor schema {schema!r}; expected 1"
    return ("This terminal descriptor is from an older test kit (schema 1 named its socket directly); "
            "it cannot be attached. Start a new run to attach to its terminals.")


def _hosted(descriptor):
    """The run that opened it is still live and its recorded attach command is well formed.

    The host validated its private location when it answered the attach
    instruction; the descriptor itself is private to this user (see discover).
    """
    attach = descriptor.get("attach")
    return (_live_identity(descriptor["owner"]) and isinstance(attach, dict) and isinstance(attach.get("argv"), list)
            and bool(attach["argv"]) and all(isinstance(part, str) and part for part in attach["argv"]))


def validate(descriptor):
    """Recheck the owner's incarnation (and a native PTY's private socket) immediately before use."""
    try:
        if refusal(descriptor) is not None:
            return False
        if descriptor["kind"] == "terminal_host":
            return _hosted(descriptor)
        if not _live_identity(descriptor["owner"]):
            return False
        path = Path(descriptor["socket"])
        info = path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            return False
        return _live_identity(descriptor["native_process"]) and isinstance(descriptor.get("token"), str)
    except (KeyError, TypeError, ValueError, OSError):
        return False
    return False


def discover(run_directory):
    """Search the explicitly supplied run only; stale descriptors are omitted."""
    root = Path(run_directory).resolve()
    found = []
    for path in root.rglob("terminal.json"):
        try:
            info = path.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > 65536
                    or not path.resolve().is_relative_to(root)):
                continue
            descriptor = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(descriptor, dict) and validate(descriptor):
                found.append({**descriptor, "descriptor_path": str(path)})
        except (OSError, ValueError, TypeError):
            continue
    # The native prompt wins over its enclosing worker's progress terminal.
    return sorted(found, key=lambda item: (item["kind"] != "pty", item["descriptor_path"]))


def connect(descriptor, *, rows=48, columns=160):
    """Authenticated raw PTY stream; useful to terminal clients and boundary tests."""
    if descriptor.get("kind") != "pty" or not validate(descriptor):
        raise ValueError("Native terminal is no longer owned and live")
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(3)
    try:
        connection.connect(descriptor["socket"])
        connection.sendall(json.dumps({"token": descriptor["token"], "rows": rows, "columns": columns}).encode() + b"\n")
        acknowledgement = bytearray()
        while len(acknowledgement) < 16 and not acknowledgement.endswith(b"\n"):
            piece = connection.recv(1)
            if not piece:
                break
            acknowledgement.extend(piece)
        if acknowledgement != b"OK\n":
            raise ValueError("Native terminal is unavailable or already attached")
        return connection
    except BaseException:
        connection.close()
        raise


def attach(descriptor):
    """Detach with Ctrl-] from a native PTY, or as the terminal host's attach instruction says."""
    problem = refusal(descriptor)
    if problem is not None:
        raise ValueError(problem)
    if not validate(descriptor):
        raise ValueError("Interactive terminal is no longer owned and live")
    if descriptor["kind"] == "terminal_host":
        print(descriptor["attach"].get("text", ""), flush=True)
        return subprocess.call(descriptor["attach"]["argv"])
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError("Interactive attachment requires a terminal")
    size = os.get_terminal_size(sys.stdout.fileno())
    connection = connect(descriptor, rows=size.lines, columns=size.columns)
    original = termios.tcgetattr(sys.stdin.fileno())
    try:
        tty.setraw(sys.stdin.fileno())
        while True:
            readable, _, _ = select.select([sys.stdin.fileno(), connection], [], [])
            if connection in readable:
                data = connection.recv(65536)
                if not data:
                    return 0
                os.write(sys.stdout.fileno(), data)
            if sys.stdin.fileno() in readable:
                data = os.read(sys.stdin.fileno(), 65536)
                if not data or b"\x1d" in data:
                    return 0
                connection.sendall(data)
    finally:
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, original)
        connection.close()
