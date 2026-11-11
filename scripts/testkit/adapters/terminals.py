"""Resolve and attach only run-owned, currently live interactive terminals."""

from __future__ import annotations

import json
import os
import select
import socket
import stat
import subprocess
import sys
import tempfile
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


ENDPOINT_PREFIX = "xa-"


def _private_directory(path):
    try:
        info = Path(path).lstat()
    except OSError:
        return False
    return stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077


def placement_problem(descriptor):
    """Why a recorded placement is not this run's own private location (``None`` when it is).

    The host lives at ``<run>/terminal-host`` beside the descriptor and its
    endpoint in a private ``xa-*`` directory of the temporary root, as the
    acceptance runner creates them. Anything else is refused before a host is built.
    """
    try:
        placement = descriptor["host"]["placement"]
        run = Path(descriptor["descriptor_path"]).parent
        location, socket = Path(placement["location"]), Path(placement["socket"])
        name = placement["name"]
    except (KeyError, TypeError):
        return "the descriptor names no placement"
    if not isinstance(name, str) or location != run / "terminal-host":
        return "the host location is not this run's own"
    endpoint = socket.parent
    if (endpoint.parent != Path(tempfile.gettempdir()) or not endpoint.name.startswith(ENDPOINT_PREFIX)
            or socket.name != "host.sock" or not _private_directory(endpoint)):
        return "the host endpoint is not this run's private directory"
    return None


def _hosted(descriptor):
    """The run that opened it is live and its recorded placement is the run's own private location.

    Nothing recorded is executed: attach rebuilds the host there and asks it.
    """
    return (_live_identity(descriptor["owner"]) and isinstance(descriptor.get("receipt"), dict)
            and placement_problem(descriptor) is None)


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
            if isinstance(descriptor, dict):
                descriptor = {**descriptor, "descriptor_path": str(path)}
                if validate(descriptor):
                    found.append(descriptor)
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


def attach(descriptor, *, host_for=None):
    """Detach with Ctrl-] from a native PTY, or as the terminal host's attach instruction says.

    ``host_for(selection, placement)`` builds the run's host (composition injects
    it); the host verifies the receipt and supplies the only command that runs.
    """
    problem = refusal(descriptor)
    if problem is not None:
        raise ValueError(problem)
    if not validate(descriptor):
        raise ValueError("Interactive terminal is no longer owned and live")
    if descriptor["kind"] == "terminal_host":
        if host_for is None:
            raise ValueError("Attaching a terminal host requires the host it runs in")
        host = host_for(descriptor["host"].get("selection"), descriptor["host"]["placement"])
        if host is None:
            raise ValueError("The run's terminal host is not ready here")
        try:
            instruction = host.attach_instruction_for(descriptor["receipt"])
        finally:
            host.close()
        print(instruction.text, flush=True)
        return subprocess.call(list(instruction.argv))
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
