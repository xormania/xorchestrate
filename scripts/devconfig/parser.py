"""Strict KEY=VALUE parsing. Values are literal text: never sourced, executed or interpolated."""

from __future__ import annotations

import re

from scripts.devconfig.model import DevConfigError

KEY = re.compile(r"[A-Z][A-Z0-9_]*")


def parse_env_file(text: str, path: str) -> tuple[dict[str, tuple[str, int]], list[tuple[str, str, int, str]]]:
    """Return ``{key: (value, line)}`` and warnings; refuse any line that is not blank, a comment or KEY=VALUE.

    A later line for the same key replaces an earlier one. One matching pair of
    surrounding single or double quotes is stripped; nothing else is interpreted.
    """
    values: dict[str, tuple[str, int]] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not KEY.fullmatch(key):
            raise DevConfigError("ENV_FILE_INVALID",
                                 f"{path}:{number}: expected KEY=VALUE, a comment or a blank line")
        values[key] = (_unquote(value.strip()), number)
    return values, []


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value
