"""The local filesystem, as the core needs it."""

from __future__ import annotations

import os


class LocalFiles:
    def is_dir(self, path: str) -> bool:
        return os.path.isdir(path)

    def exists(self, path: str) -> bool:
        return os.path.lexists(path)

    def read_text(self, path: str) -> str | None:
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                return handle.read()
        except OSError:
            return None

    def make_dirs(self, path: str) -> None:
        os.makedirs(path, exist_ok=True)
