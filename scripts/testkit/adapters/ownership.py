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


def _identity_of(pid):
    """``(Identity, state)`` of the one process ``pid``, read as ``processes`` reads it, or None if it is gone."""
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
        return Identity(pid, int(fields[19])), fields[0]
    except (OSError, ValueError, IndexError):
        return None


def alive(identity):
    """Whether this exact identity (pid and start time) still runs; a zombie is dead. Reads only that process."""
    current = _identity_of(identity.pid)
    return current is not None and current[0] == identity and current[1] != "Z"


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


def processes_naming(text):
    """Live identities of this user's processes whose command line names ``text``, never this process or its ancestors.

    How a run whose supervisor is gone finds what it left: its terminals' commands name the run directory.
    """
    snapshot = processes()
    ancestors, pid = set(), os.getpid()
    while pid in snapshot and pid not in ancestors:
        ancestors.add(pid)
        pid = snapshot[pid][1]
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
