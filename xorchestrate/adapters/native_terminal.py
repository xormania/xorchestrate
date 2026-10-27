"""Owned POSIX terminal for a native launcher, independent of protocol streams.

The launcher starts this helper once. It execs exactly one native command in a
new controlling PTY and exposes a private, token-bound local attachment socket.
It is not a session launcher or conversation-resume API of its own.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import pty
import secrets
import select
import signal
import socket
import struct
import termios
import time
from pathlib import Path

MAX_PENDING = 1_048_576
MAX_CAPTURE = 8_388_608


def process_identity(pid):
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
    return {"pid": pid, "started": int(fields[19]), "uid": os.getuid(),
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip()}


def _write_json(path, value):
    # Each helper has its own exclusive directory and is the sole descriptor writer.
    temporary = path.with_suffix(".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream)
            stream.write("\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def serve(directory, command, *, native_id, incarnation, purpose="native prompt", grace=2.0):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    # Linux limits pathname Unix sockets to 107 bytes. The socket lives in a
    # short private directory, while its descriptor remains with run evidence.
    import tempfile

    socket_directory = tempfile.TemporaryDirectory(prefix="xt-", dir="/tmp")
    socket_path = Path(socket_directory.name) / "terminal.sock"
    token = secrets.token_hex(32)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    os.chmod(socket_path, 0o600)
    listener.listen(2)
    previous = {}
    child = None
    master = None
    peer = None
    deadline = None
    shutdown = False
    forced = False
    exit_status = None
    captured = 0
    truncated = False
    outgoing = bytearray()
    incoming = bytearray()
    backlog = bytearray()

    def stop(number, frame):
        nonlocal shutdown, deadline
        shutdown = True
        if deadline is None:
            deadline = time.monotonic() + grace
            if child is not None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(child, signal.SIGTERM)

    try:
        child, master = pty.fork()
        if child == 0:
            try:
                os.execvpe(command[0], command, os.environ)
            except BaseException:
                os._exit(127)
        os.set_blocking(master, False)
        fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 48, 160, 0, 0))
        for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            previous[number] = signal.signal(number, stop)
        _write_json(directory / "terminal.json", {
            "schema_version": 1, "kind": "pty", "owner": process_identity(os.getpid()),
            "native_process": process_identity(child), "socket": str(socket_path), "token": token,
            "native_id": native_id, "incarnation": incarnation, "purpose": purpose,
            "capture": {"path": str(directory / "terminal.log"), "limit_bytes": MAX_CAPTURE,
                        "privacy": "raw_private"},
        })
        with (directory / "terminal.log").open("xb") as capture:
            os.chmod(directory / "terminal.log", 0o600)
            while exit_status is None:
                ended, status = os.waitpid(child, os.WNOHANG)
                if ended:
                    exit_status = os.waitstatus_to_exitcode(status)
                    break
                if shutdown and deadline is not None and time.monotonic() >= deadline:
                    forced = True
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(child, signal.SIGKILL)
                    deadline = None
                readers = [listener, master, *([peer] if peer is not None else [])]
                writers = ([peer] if peer is not None and outgoing else []) + ([master] if incoming else [])
                readable, writable, _ = select.select(readers, writers, [], .05)
                if listener in readable:
                    connection, _ = listener.accept()
                    connection.settimeout(.5)
                    try:
                        header = bytearray()
                        while len(header) <= 4096 and not header.endswith(b"\n"):
                            part = connection.recv(1)
                            if not part:
                                break
                            header.extend(part)
                        request = json.loads(header)
                        if not isinstance(request, dict):
                            raise ValueError("invalid terminal request")
                        if not secrets.compare_digest(request.get("token", ""), token) or peer is not None:
                            raise ValueError("terminal unavailable")
                        rows, columns = request.get("rows", 48), request.get("columns", 160)
                        if type(rows) is not int or type(columns) is not int or not (1 <= rows <= 1000 and 1 <= columns <= 1000):
                            raise ValueError("invalid terminal size")
                        fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))
                        connection.sendall(b"OK\n")
                        connection.setblocking(False)
                        peer = connection
                        outgoing = bytearray(backlog)
                    except (ValueError, OSError, TypeError):
                        connection.close()
                if master in readable:
                    try:
                        data = os.read(master, 65536)
                    except OSError:
                        data = b""
                    if data:
                        remaining = max(MAX_CAPTURE - captured, 0)
                        capture.write(data[:remaining])
                        capture.flush()
                        captured += min(len(data), remaining)
                        truncated = truncated or len(data) > remaining
                        backlog.extend(data)
                        del backlog[:-65536]
                        if peer is not None:
                            outgoing.extend(data)
                            if len(outgoing) > MAX_PENDING:
                                peer.close()
                                peer = None
                                outgoing.clear()
                if peer is not None and peer in readable:
                    try:
                        data = peer.recv(65536)
                    except OSError:
                        data = b""
                    if data:
                        incoming.extend(data)
                        if len(incoming) > MAX_PENDING:
                            incoming.clear()
                            peer.close()
                            peer = None
                    else:
                        peer.close()
                        peer = None
                if master in writable and incoming:
                    try:
                        del incoming[:os.write(master, incoming)]
                    except OSError:
                        incoming.clear()
                if peer is not None and peer in writable and outgoing:
                    try:
                        del outgoing[:peer.send(outgoing)]
                    except OSError:
                        peer.close()
                        peer = None
        _write_json(directory / "terminal-exit.json", {
            "exit_status": exit_status, "shutdown_requested": shutdown, "forced": forced,
            "capture": "truncated" if truncated else "captured", "captured_bytes": captured,
        })
        return exit_status if exit_status is not None and exit_status >= 0 else 1
    finally:
        if child is not None and exit_status is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(child, signal.SIGKILL)
            with contextlib.suppress(ChildProcessError):
                os.waitpid(child, 0)
        if peer is not None:
            peer.close()
        if master is not None:
            os.close(master)
        listener.close()
        socket_directory.cleanup()
        for number, handler in previous.items():
            signal.signal(number, handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--native-id", required=True)
    parser.add_argument("--incarnation", required=True)
    parser.add_argument("--purpose", default="native prompt")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a native command is required")
    os.umask(0o077)
    return serve(args.directory, command, native_id=args.native_id, incarnation=args.incarnation, purpose=args.purpose)


if __name__ == "__main__":
    raise SystemExit(main())
