"""Private failure reasons retained beside exportable code-only receipts.

A scenario receipt exposes only deliberate assertion codes, so it can be
shared. The reason an operator needs to act on (the application's own error,
the request that failed) is written here instead: a private file in the run
directory that the managed export never binds, and one-line views of it.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from urllib.parse import unquote

from .evidence import write_json

NAME = "diagnostics.json"
MAX_TEXT = 2000
MAX_ITEMS = 32
MAX_DEPTH = 8
MAX_LINE = 600
MAX_DETAILS = 8000
MAX_FILE_BYTES = 1024 * 1024
MAX_LINES = 20


def bounded(value, depth=MAX_DEPTH):
    """A JSON-safe copy with bounded text, breadth and depth."""
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if value == value and abs(value) != float("inf") else str(value)
    if isinstance(value, str):
        return value if len(value) <= MAX_TEXT else value[:MAX_TEXT] + "…"
    if depth <= 0:
        return "…"
    if isinstance(value, dict):
        return {str(key)[:MAX_TEXT]: bounded(item, depth - 1) for key, item in list(value.items())[:MAX_ITEMS]}
    if isinstance(value, (list, tuple)):
        return [bounded(item, depth - 1) for item in list(value)[:MAX_ITEMS]]
    return bounded(str(value))


def failure(check, error):
    """One private row for a failed scenario check; ``check`` is None outside a named check."""
    row = {"check": check, "type": type(error).__name__, "message": bounded(str(error))}
    code = getattr(error, "code", None)
    if isinstance(code, str) and code:
        row["code"] = code
    # Scenario assertions carry ``detail``; application errors carry ``details``.
    for name in ("detail", "details"):
        detail = getattr(error, name, None)
        if isinstance(detail, dict) and detail:
            row["details"] = bounded(detail)
            break
    return row


def write_failures(directory, rows):
    """Retain the private reasons of one scenario; returns the written path."""
    path = Path(directory) / NAME
    write_json(path, {"schema_version": 1, "scope": "private",
                      "failures": [_fit(bounded(row)) for row in list(rows)[:MAX_ITEMS]]})
    return path


def _fit(row):
    """Keep one row small enough that the written file always stays within what the reader accepts."""
    details = row.get("details") if isinstance(row, dict) else None
    if details is None:
        return row
    text = json.dumps(details, sort_keys=True, separators=(",", ":"))
    if len(text) <= MAX_DETAILS:
        return row
    kept = {"truncated": text[:MAX_DETAILS] + "…"}
    if isinstance(details, dict) and isinstance(details.get("tool"), str):
        kept["tool"] = details["tool"]
    return {**row, "details": kept}


def _flat(text):
    text = " ".join(str(text).split())
    return text if len(text) <= MAX_LINE else text[:MAX_LINE] + "…"


def describe(row):
    """One line naming the failed check, the request involved and the reported reason."""
    check = row.get("check") or "scenario"
    code = row.get("code") or row.get("type") or "failed"
    details = row.get("details") if isinstance(row.get("details"), dict) else {}
    tool, error = details.get("tool"), details.get("error")
    operation = details.get("operation")
    if isinstance(operation, dict) and isinstance(operation.get("error"), dict):
        # The preserved outcome explains more than the response that summarized it.
        error = operation["error"]
    if isinstance(tool, str) and isinstance(error, dict):
        reason = f"{tool} -> {error.get('code', 'error')}: {error.get('message', '')}".rstrip(": ").rstrip()
        extra = error.get("details")
        if isinstance(extra, dict) and extra:
            reason += " " + json.dumps(extra, sort_keys=True, separators=(",", ":"))
        return _flat(f"{check}: {code}: {reason}")
    if isinstance(tool, str):
        return _flat(f"{check}: {code} during {tool}")
    message = row.get("message")
    if isinstance(message, str) and message and message != code:
        return _flat(f"{check}: {code}: {message}")
    if details:
        return _flat(f"{check}: {code} " + json.dumps(details, sort_keys=True, separators=(",", ":")))
    return _flat(f"{check}: {code}")


def _read(path):
    try:
        # Run directories are writable by the launched harness: never follow a link or wait on a pipe.
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return None
            raw = stream.read(MAX_FILE_BYTES + 1)
        value = json.loads(raw) if len(raw) <= MAX_FILE_BYTES else None
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def failure_lines(directory):
    """Private one-line reasons for the unsuccessful checks beneath one run's check directory.

    Checks blocked before launch contribute their readiness reason. Launched
    scenarios contribute the reasons they retained. The lines can quote
    application errors and local paths: show them to the operator, never
    write them into an exportable receipt.
    """
    directory = Path(directory)
    lines = []
    report = _read(directory / "report.json") or {}
    for row in report.get("checks", []) if isinstance(report.get("checks"), list) else []:
        if (isinstance(row, dict) and row.get("launched") is False and row.get("reason")
                and row.get("status") not in {"passed", "selected", "running"}):
            lines.append(_flat(f"{row.get('name')}: {row.get('status')}: {row['reason']}"))
    for path in sorted(directory.glob("*/*/" + NAME)):
        if path.parent.is_symlink() or path.parent.parent.is_symlink():
            continue
        document = _read(path) or {}
        label = unquote(path.parent.parent.name)
        for row in document.get("failures", []) if isinstance(document.get("failures"), list) else []:
            if isinstance(row, dict):
                lines.append(_flat(f"{label}: failed at {describe(row)}"))
    return lines[:MAX_LINES]
