"""Read semantic objects from direct provider results or MCP envelopes.

This reader does not validate measurements or assessments. The caller names its
direct payload field and validates the returned object for that purpose. Raw
provider results remain separate evidence and are never changed by this reader.
"""

from __future__ import annotations

import json
from copy import deepcopy

from .model import JSON, unique_keys


class ProviderResultError(ValueError):
    """A fixed reason for an unreadable result, without provider diagnostics."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _invalid_constant(value):
    raise ValueError("Nonfinite provider result value")


def read_provider_result(raw: object, *, direct_field: str) -> JSON:
    """Return a detached semantic object, preserving explicit envelope precedence.

    A reported error wins over content. Structured content wins over text and
    direct fields. Otherwise the caller's direct field distinguishes a payload
    with ordinary business ``content`` from an MCP text envelope. Text envelopes
    must hold one JSON text block with unique fields and finite values.
    """
    if not isinstance(raw, dict):
        raise ProviderResultError("invalid_object")
    if raw.get("isError"):
        raise ProviderResultError("reported_error")
    if "structuredContent" in raw:
        value = raw["structuredContent"]
    elif direct_field in raw or "content" not in raw:
        value = raw
    else:
        blocks = raw["content"]
        if (not isinstance(blocks, list) or len(blocks) != 1 or not isinstance(blocks[0], dict)
                or blocks[0].get("type") != "text" or not isinstance(blocks[0].get("text"), str)):
            raise ProviderResultError("invalid_content")
        try:
            value = json.loads(blocks[0]["text"], object_pairs_hook=unique_keys("Duplicate provider result field"),
                               parse_constant=_invalid_constant)
        except (ValueError, TypeError, RecursionError):
            raise ProviderResultError("invalid_json") from None
    if not isinstance(value, dict):
        raise ProviderResultError("invalid_object")
    try:
        # This also rejects overflowing JSON numbers such as 1e999, which the
        # decoder represents as infinity without invoking parse_constant.
        json.dumps(value, allow_nan=False)
        return deepcopy(value)
    except (ValueError, TypeError, RecursionError, OverflowError):
        raise ProviderResultError("invalid_json") from None
