"""Apparent disk usage of a directory tree."""

from __future__ import annotations

import os
import stat


class WalkDiskUsage:
    def size(self, path: str) -> int:
        try:
            info = os.lstat(path)
        except OSError:
            return 0
        if stat.S_ISREG(info.st_mode):
            return info.st_size
        if not stat.S_ISDIR(info.st_mode):
            return 0
        total = 0
        for current, _, files in os.walk(path, followlinks=False):
            for name in files:
                try:
                    entry = os.lstat(os.path.join(current, name))
                except OSError:
                    continue
                if stat.S_ISREG(entry.st_mode):
                    total += entry.st_size
        return total
