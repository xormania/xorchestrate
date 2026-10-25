"""Effect knowledge carried across application and native adapter boundaries.

An exception category does not establish whether an effect occurred. Only an
explicit pre-effect boundary or the adapter's structured outcome can do that.
"""

from contextlib import contextmanager

from .model import OrchestrationError


def effect_outcome(error: Exception) -> str:
    """Return retained effect knowledge; unfamiliar or absent facts stay unknown."""
    outcome = getattr(error, "_xorchestrate_effect_outcome", None)
    if outcome is None and isinstance(error, OrchestrationError):
        outcome = error.details.get("outcome")
    return outcome if outcome in {"not_started", "failed", "completed"} else "unknown"


@contextmanager
def before_effect():
    """Mark failures in a block which cannot yet execute its target effect.

    Existing effect outcomes (notably an unresolved idempotent replay) are never
    replaced. Callers must end this boundary before invoking the effect/handler.
    """
    try:
        yield
    except Exception as error:
        if (getattr(error, "_xorchestrate_effect_outcome", None) is None
                and (not isinstance(error, OrchestrationError) or "outcome" not in error.details)):
            error._xorchestrate_effect_outcome = "not_started"
            if isinstance(error, OrchestrationError):
                error.details = {**error.details, "outcome": "not_started"}
        raise
