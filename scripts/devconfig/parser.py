"""Strict KEY=VALUE parsing. Values are literal text: never sourced, executed or interpolated."""

from __future__ import annotations

import re

from scripts.devconfig.model import DevConfigError

KEY = re.compile(r"[A-Z][A-Z0-9_]*")
BLANKS = " \t"
"""Only spaces and tabs are trimmed; other characters, form feeds included, belong to the value."""


def parse_env_file(text: str, path: str) -> tuple[dict[str, tuple[str, int]], list[tuple[str, str, int, str]]]:
    """Return ``{key: (value, line)}`` and warnings; refuse any line that is not blank, a comment or KEY=VALUE.

    Lines end only at a newline (a trailing carriage return and a leading byte order mark
    are dropped). A later line for the same key replaces an earlier one. One matching pair of
    surrounding single or double quotes is stripped; nothing else is interpreted.
    """
    values: dict[str, tuple[str, int]] = {}
    for number, raw in enumerate(text.removeprefix("\ufeff").split("\n"), start=1):
        line = raw.removesuffix("\r").strip(BLANKS)
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key = key.strip(BLANKS)
        if not separator or not KEY.fullmatch(key):
            raise DevConfigError("ENV_FILE_INVALID",
                                 f"{path}:{number}: expected KEY=VALUE, a comment or a blank line")
        values[key] = (_unquote(value.strip(BLANKS)), number)
    return values, []


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value
