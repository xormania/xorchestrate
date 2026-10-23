"""Configured recovery templates reference the existing managed tool surface."""

from __future__ import annotations

import math

PLACEHOLDERS = {"$reference": "artifact:1", "$check": "configured-check"}


def substitute(value, facts):
    if isinstance(value, str) and value.startswith("$"):
        if value not in PLACEHOLDERS:
            raise ValueError("Unknown recovery placeholder: " + value)
        return facts[value]
    if isinstance(value, dict):
        return {key: substitute(item, facts) for key, item in value.items()}
    if isinstance(value, list):
        return [substitute(item, facts) for item in value]
    if value is not None and type(value) not in {str, int, float, bool}:
        raise ValueError("Recovery arguments must be JSON values")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Recovery arguments must be finite")
    return value


def placeholders(value):
    if isinstance(value, str):
        return {value} if value.startswith("$") else set()
    if isinstance(value, dict):
        return set().union(*(placeholders(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(placeholders(item) for item in value))
    return set()


def validate_templates(entries, tools, validate):
    """Use actual MCP schemas; this is not a second tool or argument catalog."""
    schemas = {row["name"]: row["inputSchema"] for row in tools}
    for entry in entries:
        if entry["tool"] not in schemas:
            raise ValueError("Recovery references an unknown managed tool: " + entry["tool"])
        validate(substitute(entry["arguments"], PLACEHOLDERS), schemas[entry["tool"]])
