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


class _FencedSignals:
    """While active, a raw ``os.kill`` or ``os.killpg`` fails the test, except the one the kit's fenced
    ``ownership.send_signal`` makes itself, after its identity check, where no pidfd is available."""

    def __init__(self, monkeypatch):
        import pytest

        from scripts.testkit.adapters import ownership

        self._context = monkeypatch.context()
        during = self._context.__enter__()
        inside = []
        real_send, real_kill = ownership.send_signal, os.kill

        def fenced_send(identity, number):
            inside.append(identity)
            try:
                return real_send(identity, number)
            finally:
                inside.pop()

        def kill(pid, number):
            if not inside:
                pytest.fail(f"raw os.kill({pid}, {number}) is unfenced")
            return real_kill(pid, number)

        during.setattr(ownership, "send_signal", fenced_send)
        during.setattr(os, "kill", kill)
        during.setattr(os, "killpg", lambda pgid, number: pytest.fail(f"os.killpg({pgid}, {number}) is unfenced"))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return self._context.__exit__(*exc)


def fenced_signals(monkeypatch):
    """Fence raw signals for a test: active from this call until ``__exit__`` (or the end of a ``with`` block).

    Only a signal sent through ``ownership.send_signal``, looked up on the module at call time, may reach
    ``os.kill``; anything else fails the test.
    """
    return _FencedSignals(monkeypatch)


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
