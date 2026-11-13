"""Process ownership for supervised runs on Linux and WSL2: identities, fenced signals and descendant tracking.

Independent of where a run's terminals are hosted; the terminal host itself is
reached only through the product's terminal host port.
"""

from __future__ import annotations

import ctypes
import os
import signal
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


class AcceptanceFailure(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Identity:
    pid: int
    started: int


def processes():
    """Read identities without command lines, environment, or credential data."""
    result = {}
    for path in Path("/proc").glob("[0-9]*/stat"):
        try:
            fields = path.read_text().rsplit(") ", 1)[1].split()
            result[int(path.parent.name)] = (Identity(int(path.parent.name), int(fields[19])), int(fields[1]), fields[0])
        except (OSError, ValueError, IndexError):
            continue  # A process may disappear between listing and reading it.
    return result


def alive(identity):
    current = processes().get(identity.pid)
    return current is not None and current[0] == identity and current[2] != "Z"


def send_signal(identity, number):
    """Fence a signal against PID reuse; use a pinned pidfd where supported."""
    if not alive(identity):
        return
    descriptor = None
    try:
        if hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal"):
            try:
                descriptor = os.pidfd_open(identity.pid)
            except OSError:
                descriptor = None
        if alive(identity):
            if descriptor is not None:
                signal.pidfd_send_signal(descriptor, number)
            else:
                os.kill(identity.pid, number)
    except ProcessLookupError:
        pass
    finally:
        if descriptor is not None:
            os.close(descriptor)


@contextmanager
def subreaper():
    """Adopt orphaned grandchildren so even a fast double fork remains owned."""
    library = ctypes.CDLL(None, use_errno=True)
    previous = ctypes.c_int()
    if library.prctl(37, ctypes.byref(previous), 0, 0, 0) or library.prctl(36, 1, 0, 0, 0):
        raise AcceptanceFailure("process_adoption_unavailable")
    try:
        yield
    finally:
        library.prctl(36, previous.value, 0, 0, 0)


class OwnedProcesses:
    """Track this single-threaded supervisor's new children and descendants.

    Preexisting children and their observed descendants are excluded. The runner
    launches only its terminal host and its terminals after this snapshot. Subreaping
    keeps a child that outlives its immediate parent inside this ownership scope.
    This standalone supervisor must not launch unrelated children concurrently:
    snapshots cannot attribute an unseen descendant orphaned between observations.
    """

    def __init__(self):
        self.parent = os.getpid()
        self.baseline = set()
        self.known = set()
        self.hosted = set()  # the terminal host's own children: it alone waits for them
        self._exclude_descendants(processes(), {self.parent})

    def _exclude_descendants(self, snapshot, roots):
        # Keep identities after reparenting: subreaping does not confer ownership
        # of an unrelated process whose original parent has exited.
        changed = True
        while changed:
            changed = False
            for identity, parent, _ in snapshot.values():
                if parent in roots and identity.pid not in roots:
                    self.baseline.add(identity)
                    roots.add(identity.pid)
                    changed = True

    def observe(self):
        snapshot = processes()
        excluded = {identity.pid for identity in self.baseline if snapshot.get(identity.pid, (None,))[0] == identity}
        self._exclude_descendants(snapshot, excluded)
        roots = {self.parent}
        roots.update(identity.pid for identity in self.known if snapshot.get(identity.pid, (None,))[0] == identity)
        changed = True
        while changed:
            changed = False
            for identity, parent, _ in snapshot.values():
                if parent in roots and identity not in self.baseline and identity.pid not in roots:
                    self.known.add(identity)
                    roots.add(identity.pid)
                    changed = True
        return [identity for identity in self.known if snapshot.get(identity.pid, (None,))[0] == identity and snapshot[identity.pid][2] != "Z"]

    def children(self):
        """This process's direct children now: taken before a host call, then handed to ``hand_to_host``."""
        return {identity for identity, parent, _ in processes().values() if parent == self.parent}

    def hand_to_host(self, before):
        """Children that appeared across a host call are the host's: it alone waits for them and reads their status.

        Waiting for one here would take its exit status, and the host would then see a failed worker as exit 0.
        """
        self.hosted.update(self.children() - set(before))

    def release_host(self):
        """The host has shut down and waits for nothing more: what it left unreaped is ours to reap."""
        self.hosted.clear()

    def reap(self, exclude=()):
        snapshot = processes()
        for identity in self.known:
            if identity in self.hosted:
                continue
            if identity.pid not in exclude and snapshot.get(identity.pid, (None,))[0] == identity:
                try:
                    os.waitpid(identity.pid, os.WNOHANG)
                except ChildProcessError:
                    pass


def environment(profile, run_dir):
    if profile == "native":
        result = dict(os.environ)
    else:
        names = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TERM",
                 "PYTHONPYCACHEPREFIX", "PYTHONDONTWRITEBYTECODE")
        result = {name: os.environ[name] for name in names if name in os.environ}
        for name, folder in (("HOME", "home"), ("XDG_CONFIG_HOME", "config"), ("XDG_CACHE_HOME", "cache"), ("XDG_DATA_HOME", "data")):
            path = run_dir / "environment" / folder
            path.mkdir(parents=True, mode=0o700)
            result[name] = str(path)
        result["PYTHONNOUSERSITE"] = "1"
    result["PYTHONUNBUFFERED"] = "1"
    result.setdefault("TERM", "xterm-256color")
    return result


def _ancestry(snapshot):
    """This process and its ancestors: never a candidate for any selection here."""
    ancestors, pid = set(), os.getpid()
    while pid in snapshot and pid not in ancestors:
        ancestors.add(pid)
        pid = snapshot[pid][1]
    return ancestors


def processes_naming(text):
    """Live identities of this user's processes whose command line names ``text``, never this process or its ancestors.

    A name is text anyone can put in a command line: use this to report, or to narrow a selection that
    ``owned_processes`` already made (its ``within``), never on its own to choose what to signal.
    """
    snapshot = processes()
    ancestors = _ancestry(snapshot)
    needle, found = os.fsencode(str(text)), []
    for pid, (identity, _, state) in snapshot.items():
        if pid in ancestors or state == "Z":
            continue
        try:
            path = Path("/proc") / str(pid)
            if path.stat().st_uid != os.getuid() or needle not in (path / "cmdline").read_bytes():
                continue
        except OSError:
            continue
        found.append(identity)
    return found


LISTENING = 0x10000
"""``__SO_ACCEPTCON`` in /proc/net/unix: the socket was bound and listens."""


@dataclass(frozen=True)
class Selection:
    """Processes selected by provenance: ``selected`` maps each identity to its signal ("home", "spawned",
    "socket" or "descendant"); ``ancestors`` maps each descendant to the identity that a direct signal selected."""

    selected: dict
    ancestors: dict


def _carries(pid, wanted):
    """Whether ``pid``'s initial environment holds exactly the entry ``wanted``; nothing else is kept."""
    try:
        with open(f"/proc/{pid}/environ", "rb") as source:
            block = source.read()
    except OSError:
        return False  # unreadable: not selected
    return any(entry == wanted for entry in block.split(b"\0"))


def _listening_inodes(root):
    """Inodes of listening Unix sockets whose bound path, resolved, lies inside the resolved ``root``."""
    prefix = os.path.realpath(root) + os.sep
    found = set()
    try:
        lines = Path("/proc/net/unix").read_text(encoding="utf-8", errors="replace").splitlines()[1:]
    except OSError:
        return found
    for line in lines:
        fields = line.split(None, 7)
        if len(fields) < 8 or fields[7].startswith("@"):
            continue  # unbound, or an abstract name that is no path
        try:
            listening = int(fields[3], 16) & LISTENING
        except ValueError:
            continue
        if listening and os.path.realpath(fields[7]).startswith(prefix):
            found.add(fields[6])
    return found


def _holds(pid, inodes):
    """Whether ``pid`` itself holds one of the listening sockets ``inodes``."""
    try:
        descriptors = os.listdir(f"/proc/{pid}/fd")
    except OSError:
        return False
    for descriptor in descriptors:
        try:
            link = os.readlink(f"/proc/{pid}/fd/{descriptor}")
        except OSError:
            continue
        if link.startswith("socket:[") and link[8:-1] in inodes:
            return True
    return False


def owned_processes(root, marker, *, spawned=(), within=None):
    """This user's processes that provably belong to the caller's run, each with the signal that selected it.

    - ``home``: the initial environment sets ``marker`` (a ``(key, value)`` pair, or None for no marker)
      exactly; only that key is compared, and an unreadable environment means "not selected";
    - ``spawned``: one of the caller's still-running ``spawned`` processes;
    - ``socket``: it holds a listening Unix socket whose bound path, resolved, lies inside ``root``;
    - ``descendant``: it descends, through parent links read now, from a process selected above.

    ``root`` must be an existing directory of this user, not a symlink; anything else is refused. This
    process and its ancestors are never selected. A name or path may narrow a selection that provenance has
    already made (``within``: identities outside it are never selected, descendants included); it never
    widens one and never selects by itself.
    """
    try:
        info = os.lstat(root)
    except OSError as error:
        raise ValueError(f"refusing to select: {root!s} is not a directory of this user") from error
    if not os.path.isdir(root) or os.path.islink(root) or info.st_uid != os.getuid():
        raise ValueError(f"refusing to select: {root!s} is not a directory of this user")
    wanted = None if marker is None else os.fsencode(marker[0]) + b"=" + os.fsencode(str(marker[1]))
    snapshot, uid = processes(), os.getuid()
    excluded = _ancestry(snapshot)
    allowed = None if within is None else set(within)
    inodes = _listening_inodes(root)
    mine, found = set(), {}
    for pid, (identity, _, state) in snapshot.items():
        if state == "Z" or pid in excluded:
            continue
        try:
            if os.stat(f"/proc/{pid}").st_uid != uid:
                continue
        except OSError:
            continue
        mine.add(pid)
        if allowed is not None and identity not in allowed:
            continue
        if wanted is not None and _carries(pid, wanted):
            found[identity] = "home"
        elif inodes and _holds(pid, inodes):
            found[identity] = "socket"
    for process in spawned:
        entry = snapshot.get(process.pid)
        if process.poll() is None and entry and process.pid in mine and (allowed is None or entry[0] in allowed):
            found.setdefault(entry[0], "spawned")
    children = {}
    for pid in mine:
        children.setdefault(snapshot[pid][1], []).append(pid)
    ancestors, pending = {}, [(identity, identity) for identity in found]
    while pending:
        parent, origin = pending.pop()
        for pid in children.get(parent.pid, ()):
            child = snapshot[pid][0]
            if child in found or (allowed is not None and child not in allowed):
                continue
            found[child], ancestors[child] = "descendant", origin
            pending.append((child, origin))
    return Selection(found, ancestors)
