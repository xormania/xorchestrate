"""JSON-lines store boundary for the tooling fixture."""

from __future__ import annotations

import json
from pathlib import Path


def record_path(root: Path, name: str) -> Path:
    return root / name


def checked_record_path(root: Path, name: str) -> Path:
    base = root.resolve()
    candidate = (base / name).resolve()
    candidate.relative_to(base)
    return candidate


def read_records(path: Path) -> list[dict[str, object]]:
    try:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    except Exception:
        return []


def read_records_checked(path: Path) -> list[dict[str, object]]:
    try:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    except OSError:
        raise


def write_records(path: Path, records: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(record, sort_keys=True) + "\n" for record in records)
    path.write_text(body, encoding="utf-8")
