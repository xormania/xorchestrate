"""Operator-owned implementation registry; load trusted adapters only when selected.

Each implementation is a module:attribute reference to installed Python code.
Changing the manifest registers an adapter without modifying dispatch logic.
The manifest is trusted executable configuration, never an MCP argument.
"""

from __future__ import annotations

import importlib
import json
import re
from pathlib import Path

MANIFEST = Path(__file__).with_name("adapters.json")
NAME = re.compile(r"[a-z][a-z0-9_-]{0,79}")
IMPLEMENTATION = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", re.ASCII)


def registry():
    def distinct(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Adapter manifest contains duplicate keys.")
            result[key] = value
        return result

    def finite(value):
        raise ValueError("Adapter manifest must contain finite JSON values.")

    try:
        document = json.loads(MANIFEST.read_text(), object_pairs_hook=distinct, parse_constant=finite)
    except (OSError, ValueError):
        raise ValueError("The adapter manifest must be readable JSON.") from None
    if (not isinstance(document, dict) or document.keys() != {"version", "adapters"}
            or type(document["version"]) is not int or document["version"] != 1
            or not isinstance(document["adapters"], dict)):
        raise ValueError("Unsupported adapter manifest format.")
    for kind, entries in document["adapters"].items():
        if not isinstance(kind, str) or not NAME.fullmatch(kind) or not isinstance(entries, dict):
            raise ValueError("Adapter groups require named implementation mappings.")
        for name, implementation in entries.items():
            if (not isinstance(name, str) or not NAME.fullmatch(name)
                    or not isinstance(implementation, str) or not IMPLEMENTATION.fullmatch(implementation)):
                raise ValueError("Adapters require a valid name and installed module:attribute implementation.")
    return document["adapters"]


def names(kind):
    return tuple(sorted(registry().get(kind, {})))


def implementation(kind, name):
    try:
        return registry()[kind][name]
    except (KeyError, TypeError):
        raise ValueError("The configured adapter is not registered for this capability.") from None


def factory(kind, name):
    module, attribute = implementation(kind, name).split(":", 1)
    try:
        value = importlib.import_module(module)
        for part in attribute.split("."):
            value = getattr(value, part)
    except (ImportError, AttributeError):
        raise ValueError("The configured adapter implementation is not installed.") from None
    if not callable(value):
        raise ValueError("The configured adapter implementation must be callable.")
    return value
