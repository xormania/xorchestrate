"""Owner-only local Unix socket listener, peer credentials and instance locks.

Shared by the session bridge host and the ``unix`` client transport. Each keeps
its own protocol above the accepted stream; only binding, permission and peer
checks live here. The socket file is ``0600`` and every accepted peer is checked
with ``SO_PEERCRED`` where the platform offers it.
"""
from __future__ import annotations

import fcntl
import os
import select
import socket
import stat
import struct
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from ..model import OrchestrationError

# sockaddr_un.sun_path is 108 bytes on Linux and 104 on BSD/macOS, including
# the terminating NUL.
MAX_SOCKET_PATH_BYTES = 107 if sys.platform.startswith("linux") else 103


@dataclass(frozen=True)
class PeerCredentials:
    pid: int | None
    uid: int | None
    gid: int | None


def peer_credentials(connection) -> PeerCredentials | None:
    """Return the kernel-reported peer of a connected Unix socket, if observable."""
    if not hasattr(socket, "SO_PEERCRED"):
        return None
    pid, uid, gid = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
    return PeerCredentials(pid, uid, gid)


def same_user(credentials: PeerCredentials | None) -> bool:
    """Default peer policy: only the user running this process may connect.

    Platforms without ``SO_PEERCRED`` rely on the socket's ``0600`` mode.
    """
    return credentials is None or credentials.uid == os.getuid()


def check_socket_path(path) -> Path:
    path = Path(path).expanduser().absolute()
    if len(os.fsencode(str(path))) > MAX_SOCKET_PATH_BYTES:
        raise OrchestrationError(f"Unix socket path exceeds {MAX_SOCKET_PATH_BYTES} bytes; choose a shorter path.",
                                 "invalid_configuration", {"path": str(path)})
    return path


class OwnerSocketListener:
    """Bind an owner-only stream socket and accept only peers the check allows.

    ``accept`` returns ``(connection, credentials)`` like ``socket.accept`` so
    callers can keep their own accept loops. A refused peer is closed and the
    listener keeps waiting. An existing path is never replaced unless
    ``replace_stale`` is set by a caller that holds the instance lock, and then
    only when the path is a socket owned by this user.
    """

    def __init__(self, path, backlog=16, *, check=same_user, replace_stale=False):
        self.path = check_socket_path(path)
        self.check = check
        self.closed = False
        if replace_stale:
            self._remove_stale()
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self.socket.bind(str(self.path))
            os.chmod(self.path, 0o600)
            self._identity = self._stat_identity()
            self.socket.listen(backlog)
        except Exception:
            self.socket.close()
            if getattr(self, "_identity", None) is not None:
                self.path.unlink(missing_ok=True)
            raise

    def _stat_identity(self):
        metadata = self.path.lstat()
        return metadata.st_dev, metadata.st_ino

    def _remove_stale(self):
        try:
            metadata = self.path.lstat()
        except FileNotFoundError:
            return
        if not stat.S_ISSOCK(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise OrchestrationError("The socket path exists and is not this user's socket.", "invalid_configuration",
                                     {"path": str(self.path)})
        self.path.unlink()

    def fileno(self):
        return self.socket.fileno()

    def accept(self, timeout=None):
        """Wait for an allowed peer. With a timeout, return ``(None, None)`` when it expires."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            if deadline is not None:
                remaining = deadline - time.monotonic()
                try:
                    if remaining <= 0 or not select.select([self.socket], [], [], remaining)[0]:
                        return None, None
                except ValueError as exc:  # closed descriptor
                    raise OSError("listener closed") from exc
            connection, _ = self.socket.accept()
            try:
                credentials = peer_credentials(connection)
                allowed = self.check(credentials)
            except Exception:
                allowed = False
            if allowed:
                return connection, credentials
            connection.close()

    def shutdown(self, *_):
        self.closed = True
        try:
            self.socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def close(self):
        self.closed = True
        self.socket.close()
        # Remove only the socket this listener created, never a replacement.
        try:
            if self._stat_identity() == self._identity:
                self.path.unlink()
        except FileNotFoundError:
            pass


class InstanceLock:
    """An exclusive advisory lock file; the kernel releases it if the holder dies."""

    def __init__(self, path):
        self.path = Path(path)
        self._fd = None

    def acquire(self, wait=0.0, interval=0.05):
        """Take the lock, waiting up to ``wait`` seconds. Return whether it is held."""
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        deadline = time.monotonic() + max(0.0, wait)
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    os.close(fd)
                    return False
                time.sleep(interval)
                continue
            self._fd = fd
            return True

    def held(self):
        return self._fd is not None

    def release(self):
        if self._fd is not None:
            fd, self._fd = self._fd, None
            os.close(fd)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.release()


def lock_is_held(path) -> bool:
    """Whether another holder currently owns the lock file at ``path``."""
    probe = InstanceLock(path)
    try:
        if probe.acquire():
            probe.release()
            return False
    except FileNotFoundError:
        return False
    return True
