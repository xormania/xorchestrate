"""Server state as one JSON file per tree."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from urllib.parse import quote

from scripts.worktrees.model import ServerState


class JsonStateStore:
    def __init__(self, directory: Path | str) -> None:
        self.directory = Path(directory)

    def _path(self, key: str) -> Path:
        return self.directory / (quote(key, safe="") + ".json")

    def save(self, key: str, state: ServerState) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=self.directory, prefix=".", suffix=".tmp")
        with os.fdopen(handle, "w", encoding="utf-8") as output:
            json.dump({"key": key, "state": asdict(state)}, output, indent=2, sort_keys=True)
        os.replace(temporary, self._path(key))

    def load(self, key: str) -> ServerState | None:
        record = self._read(self._path(key))
        return record[1] if record else None

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def list(self) -> list[tuple[str, ServerState]]:
        if not self.directory.is_dir():
            return []
        records = [self._read(path) for path in self.directory.glob("*.json")]
        return sorted((record for record in records if record), key=lambda record: record[0])

    @staticmethod
    def _read(path: Path) -> tuple[str, ServerState] | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data["key"], ServerState(**data["state"])
        except (OSError, ValueError, KeyError, TypeError):
            return None
