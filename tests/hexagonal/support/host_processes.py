"""Find processes a test run left behind, by the private paths they were placed under.

Tests that create terminal hosts register their temporary roots with ``track``.
A process belongs to a root when its command line names a path under it or its
working directory is under it. Only this user's processes are considered, and
never this process or its ancestors.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOTS: set[str] = set()


def track(path):
    """Register a temporary root whose processes must all be gone when the run ends."""
    ROOTS.add(os.path.realpath(str(path)))
    return path


def _ancestors():
    pids, pid = set(), os.getpid()
    while pid > 1:
        pids.add(pid)
        try:
            with open(f"/proc/{pid}/stat", encoding="utf-8") as source:
                pid = int(source.read().rsplit(") ", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            break
    return pids


def processes_under(roots):
    """``[(pid, command line)]`` of live processes placed under any of ``roots``."""
    roots = [os.path.realpath(str(root)).rstrip("/") for root in roots]
    if not roots or not Path("/proc/self/stat").exists():
        return []
    found, excluded, uid = [], _ancestors(), os.getuid()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) in excluded:
            continue
        try:
            if entry.stat().st_uid != uid:
                continue
            with open(entry / "stat", encoding="utf-8") as source:
                if source.read().rsplit(") ", 1)[1].split()[0] == "Z":
                    continue
            command = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
            cwd = os.readlink(entry / "cwd").removesuffix(" (deleted)")
        except (OSError, ValueError, IndexError):
            continue  # gone, or not ours to inspect
        if any(root in command or cwd == root or cwd.startswith(root + "/") for root in roots):
            found.append((int(entry.name), command))
    return sorted(found)


def arguments_containing(text):
    """``[(pid, command line)]`` of this user's live processes whose arguments contain ``text``."""
    found, uid, me = [], os.getuid(), os.getpid()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == me:
            continue
        try:
            if entry.stat().st_uid != uid:
                continue
            command = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if text.encode() in command:
            found.append((int(entry.name), command.replace(b"\0", b" ").decode(errors="replace")))
    return found
