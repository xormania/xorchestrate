"""Server state as one JSON file per tree, with a lock file per tree."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import tempfile
from collections.abc import Iterator
from dataclasses import asdict
from pathlib import Path

from scripts.worktrees.model import ServerState


def _name(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:32]


class JsonStateStore:
    def __init__(self, directory: Path | str, locks: Path | str | None = None) -> None:
        self.directory = Path(directory)
        self.locks = Path(locks) if locks is not None else self.directory.parent / "locks"

    def _path(self, key: str) -> Path:
        return self.directory / (_name(key) + ".json")

    def save(self, key: str, state: ServerState) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=self.directory, prefix=".", suffix=".tmp")
        with os.fdopen(handle, "w", encoding="utf-8") as output:
            json.dump({"key": key, "state": asdict(state)}, output, indent=2, sort_keys=True)
        os.replace(temporary, self._path(key))

    def load(self, key: str) -> ServerState | None:
        record = self._read(self._path(key))
        return record[1] if record and record[0] == key else None

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def list(self) -> list[tuple[str, ServerState]]:
        if not self.directory.is_dir():
            return []
        records = [self._read(path) for path in self.directory.glob("*.json")]
        return sorted((record for record in records if record), key=lambda record: record[0])

    @contextlib.contextmanager
    def lock(self, key: str) -> Iterator[None]:
        self.locks.mkdir(parents=True, exist_ok=True)
        with open(self.locks / (_name(key) + ".lock"), "a+b") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    @staticmethod
    def _read(path: Path) -> tuple[str, ServerState] | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            state = dict(data["state"])
            state["argv"] = tuple(state.get("argv") or ())
            return data["key"], ServerState(**state)
        except (OSError, ValueError, KeyError, TypeError):
            return None
