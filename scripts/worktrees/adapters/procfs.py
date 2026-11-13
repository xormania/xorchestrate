"""The process table read from a /proc-shaped directory; signals sent to process groups."""

from __future__ import annotations

import os
from pathlib import Path

from scripts.worktrees.model import Proc


class ProcFs:
    def __init__(self, root: Path | str = "/proc") -> None:
        self.root = Path(root)

    def processes(self) -> list[Proc]:
        found = []
        try:
            entries = list(self.root.iterdir())
        except OSError:
            return found
        for entry in entries:
            if entry.name.isdigit():
                proc = self._read(entry)
                if proc is not None:
                    found.append(proc)
        return found

    def _read(self, directory: Path) -> Proc | None:
        try:
            stat = (directory / "stat").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        fields = stat[stat.rfind(")") + 1:].split()
        if len(fields) < 3:
            return None
        try:
            ppid, pgid = int(fields[1]), int(fields[2])
        except ValueError:
            return None
        try:
            argv = tuple(word.decode(errors="replace")
                         for word in (directory / "cmdline").read_bytes().split(b"\0") if word)
        except OSError:
            argv = ()
        try:
            cwd = os.readlink(directory / "cwd")
        except OSError:
            cwd = ""
        open_paths = []
        try:
            descriptors = list((directory / "fd").iterdir())
        except OSError:
            descriptors = []
        for descriptor in descriptors:
            try:
                open_paths.append(os.readlink(descriptor))
            except OSError:
                continue
        return Proc(pid=int(directory.name), ppid=ppid, pgid=pgid, cwd=cwd, argv=argv, open_paths=tuple(open_paths))

    def signal_group(self, pgid: int, sig: int) -> None:
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            pass

    def alive(self, pid: int) -> bool:
        try:
            stat = (self.root / str(pid) / "stat").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        fields = stat[stat.rfind(")") + 1:].split()
        return bool(fields) and fields[0] not in ("Z", "X")
