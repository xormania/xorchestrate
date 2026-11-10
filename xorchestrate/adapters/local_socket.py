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
import threading
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




def _unsafe(directory, reason):
    return OrchestrationError(f"Unsafe socket directory {directory}: {reason}.", "socket_path_unsafe",
                              {"directory": str(directory), "reason": reason})


def check_socket_directory(path, *, uid=None) -> Path:
    """Refuse a socket path whose directory another user could tamper with.

    The socket's directory must be a directory owned by this user that no
    group or other user can write to (a sticky bit is not enough: another
    user could still pre-create the socket name). Every ancestor, along the
    path as given and after resolving symlinks, must be owned by this user or
    root and must not be group/other-writable unless it is sticky, so the
    directory cannot be renamed and replaced underneath. Returns ``path``.
    """
    uid = os.getuid() if uid is None else uid
    path = Path(path)
    directory = path.parent
    try:
        metadata = os.stat(directory)
    except FileNotFoundError:
        raise _unsafe(directory, "it does not exist") from None
    if not stat.S_ISDIR(metadata.st_mode):
        raise _unsafe(directory, "it is not a directory")
    if metadata.st_uid != uid:
        raise _unsafe(directory, "it is not owned by this user")
    if metadata.st_mode & 0o022:
        raise _unsafe(directory, "it is writable by group or others")
    for ancestor in dict.fromkeys([*directory.parents, *directory.resolve().parents]):
        try:
            metadata = os.stat(ancestor)
        except FileNotFoundError:
            raise _unsafe(ancestor, "it does not exist") from None
        if metadata.st_uid not in (uid, 0):
            raise _unsafe(ancestor, "an enclosing directory is owned by another user")
        if metadata.st_mode & 0o022 and not metadata.st_mode & stat.S_ISVTX:
            raise _unsafe(ancestor, "an enclosing directory is writable by others without the sticky bit")
    return path


# The umask is process-wide; serialize the binds that narrow it.
_UMASK_LOCK = threading.Lock()


def is_listening(path) -> bool:
    """Whether the socket at ``path`` may have a live listener.

    Only a refused connection (or a vanished path) proves nothing listens.
    A full backlog, a timeout or any other error is indeterminate and counts
    as listening, so a live server is never replaced.
    """
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.settimeout(1.0)
        probe.connect(str(path))
        return True
    except (ConnectionRefusedError, FileNotFoundError):
        return False
    except OSError:
        return True
    finally:
        probe.close()


class OwnerSocketListener:
    """Bind an owner-only stream socket and accept only peers the check allows.

    ``accept`` returns ``(connection, credentials)`` like ``socket.accept`` so
    callers can keep their own accept loops. A refused peer is closed and the
    listener keeps waiting. The socket is created under a ``0177`` umask, so it
    is never more permissive than ``0600``. An existing path is never replaced
    unless ``replace_stale`` is set by a caller that holds the instance lock,
    and then only when it is this user's socket and nothing is listening on it.
    """

    def __init__(self, path, backlog=16, *, check=same_user, replace_stale=False):
        self.path = check_socket_path(path)
        self.check = check
        self.closed = False
        self._identity = None
        if replace_stale:
            self._remove_stale()
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            with _UMASK_LOCK:
                previous = os.umask(0o177)
                try:
                    self.socket.bind(str(self.path))
                finally:
                    os.umask(previous)
            self._identity = self._stat_identity()
            os.chmod(self.path, 0o600)
            self.socket.listen(backlog)
        except Exception:
            self.socket.close()
            if self._identity is not None:
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
        if is_listening(self.path):
            raise OrchestrationError("Another server is listening on this socket.", "server_running",
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


def open_private_file(path, flags):
    """Open a regular file that must be owned by this user and private (no group/other bits)."""
    fd = os.open(path, flags | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    metadata = os.fstat(fd)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
        os.close(fd)
        raise OrchestrationError("A server file must be a private regular file owned by this user.",
                                 "invalid_configuration", {"path": str(path)})
    return fd


class InstanceLock:
    """An exclusive advisory lock file; the kernel releases it if the holder dies."""

    def __init__(self, path):
        self.path = Path(path)
        self._fd = None

    def acquire(self, wait=0.0, interval=0.02, *, shared=False):
        """Take the lock, retrying for up to ``wait`` seconds. Return whether it is held."""
        fd = open_private_file(self.path, os.O_RDWR | os.O_CREAT)
        deadline = time.monotonic() + max(0.0, wait)
        mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
        while True:
            try:
                fcntl.flock(fd, mode | fcntl.LOCK_NB)
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
    """Whether a holder currently owns the exclusive lock at ``path``.

    The probe takes a shared lock, so concurrent probes never conflict with
    each other; a server acquiring the exclusive lock retries briefly to ride
    over a probe that holds it for an instant.
    """
    probe = InstanceLock(path)
    try:
        if probe.acquire(shared=True):
            probe.release()
            return False
    except FileNotFoundError:
        return False
    return True
