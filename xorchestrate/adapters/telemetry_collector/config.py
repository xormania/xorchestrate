"""Validation and path containment for inline collector options.

Nothing here opens storage or consults the environment. Storage adapters resolve
their own paths beneath the collector's ``runtime_root``.
"""

from __future__ import annotations

import stat
from pathlib import Path

from .domain import TelemetryError


def fail(message: str):
    raise TelemetryError("invalid_config", message)


def text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        fail(f"Telemetry {label} must be nonempty text.")
    if any(ord(char) < 32 for char in value):
        fail(f"Telemetry {label} contains a control character.")
    return value


def known(options: object, allowed: set[str], label: str) -> dict:
    if not isinstance(options, dict):
        fail(f"Telemetry {label} options must be an object.")
    if options.keys() - allowed:
        fail(f"Unknown telemetry {label} option.")
    return options


def no_symlinks(path: Path, label: str) -> None:
    for component in (path, *path.parents):
        try:
            mode = component.lstat().st_mode
        except FileNotFoundError:
            continue
        except OSError:
            fail(f"Telemetry {label} cannot be inspected.")
        if stat.S_ISLNK(mode):
            fail(f"Telemetry {label} cannot traverse symbolic links.")


def runtime_root(value: object) -> Path:
    root = Path(text(value, "runtime_root")).absolute()
    no_symlinks(root, "runtime_root")
    try:
        return root.resolve()
    except (OSError, RuntimeError):
        fail("Telemetry runtime_root cannot be resolved.")


def contained(root: Path, value: object, label: str) -> Path:
    """Resolve a relative child path strictly inside ``root`` without following links."""
    relative = Path(text(value, label))
    if relative.is_absolute() or ".." in relative.parts:
        fail(f"Telemetry {label} must be relative to runtime_root.")
    candidate = root / relative
    no_symlinks(candidate, label)
    try:
        resolved = candidate.resolve()
    except (OSError, RuntimeError):
        fail(f"Telemetry {label} cannot be resolved.")
    if resolved == root or not resolved.is_relative_to(root):
        fail(f"Telemetry {label} must be inside runtime_root.")
    return resolved


def overlapping(first: Path, second: Path) -> bool:
    return first == second or first.is_relative_to(second) or second.is_relative_to(first)


def selection(value: object, label: str) -> tuple[str, dict, bool]:
    """Validate one nested ``{"adapter", "options", "enabled"}`` storage selection."""
    if not isinstance(value, dict) or not isinstance(value.get("adapter"), str) or not value["adapter"]:
        fail(f"Telemetry {label} requires an adapter name.")
    if value.keys() - {"adapter", "options", "enabled"}:
        fail(f"Unknown telemetry {label} selection field.")
    enabled = value.get("enabled", True)
    if type(enabled) is not bool:
        fail(f"Telemetry {label} enabled must be a boolean.")
    options = value.get("options", {})
    if not isinstance(options, dict):
        fail(f"Telemetry {label} options must be an object.")
    return value["adapter"], dict(options), enabled
