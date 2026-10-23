"""Service-owned failure facts; suggestions never confer execution authority."""

from __future__ import annotations

from .telemetry import clean

CATEGORIES = frozenset({
    "not_permitted", "not_ready", "invalid_request", "evaluation_unavailable",
    "evaluation_invalid", "operation_failed",
})


class Failure(ValueError):
    """Keep the human diagnostic while exposing a stable, source-owned cause."""

    def __init__(self, message, *, category, reason, details=None):
        super().__init__(message)
        if category not in CATEGORIES or not isinstance(reason, str) or not reason:
            raise ValueError("Invalid failure category or reason")
        self.failure = {"category": category, "reason": reason, "details": details or {}}


def failure_info(error):
    """Unknown exceptions remain unknown; never infer permission or provider state."""
    if isinstance(error, Failure):
        return clean(error.failure)
    return {"category": "operation_failed", "reason": "unclassified", "details": {}}
