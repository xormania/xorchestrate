"""Request logging boundary for the fixture service."""

from __future__ import annotations

from pathlib import Path


def _headers(request: dict[str, object]) -> dict[str, object]:
    headers = request.get("headers", {})
    return headers if isinstance(headers, dict) else {}


def log_request(request: dict[str, object], path: Path) -> None:
    headers = _headers(request)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"headers={headers}\n", encoding="utf-8")


def log_request_redacted(request: dict[str, object], path: Path) -> None:
    _headers(request)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("authorization=[REDACTED]\n", encoding="utf-8")
