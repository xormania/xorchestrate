"""The local filesystem, as the core needs it."""

from __future__ import annotations

import os
import shutil
import tarfile


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

    def list_dir(self, path: str) -> list[str]:
        try:
            return sorted(os.listdir(path))
        except OSError:
            return []

    def mtime(self, path: str) -> float | None:
        try:
            return os.lstat(path).st_mtime
        except OSError:
            return None

    def is_symlink(self, path: str) -> bool:
        return os.path.islink(path)

    def write_text(self, path: str, text: str) -> None:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)

    def remove_tree(self, path: str) -> None:
        shutil.rmtree(path)

    def archive(self, directory: str, tar_path: str, arcname: str) -> None:
        os.makedirs(os.path.dirname(tar_path), exist_ok=True)
        with tarfile.open(tar_path, "x") as archive:
            archive.add(directory, arcname=arcname)

    def find_dirs(self, root: str, names: frozenset[str]) -> list[str]:
        found = []
        for current, directories, _ in os.walk(root, followlinks=False):
            directories[:] = [name for name in directories if name != ".git"]
            found += [os.path.join(current, name) for name in directories if name in names]
        return sorted(found)
