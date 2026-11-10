"""Effect knowledge carried across application and native adapter boundaries.

An exception category does not establish whether an effect occurred. Only an
explicit pre-effect boundary or the adapter's structured outcome can do that.
"""

from contextlib import contextmanager

from .model import OrchestrationError, public_error


def effect_outcome(error: Exception) -> str:
    """Return retained effect knowledge; unfamiliar or absent facts stay unknown."""
    outcome = getattr(error, "_xorchestrate_effect_outcome", None)
    if outcome is None and isinstance(error, OrchestrationError) and isinstance(error.details, dict):
        outcome = error.details.get("outcome")
    return outcome if isinstance(outcome, str) and outcome in {"not_started", "failed", "completed"} else "unknown"


def completed_effect_result(error: Exception) -> dict:
    """Retain only an explicitly public, JSON-valid adapter completion result.

    This is the generic provider protocol, not a delivery-specific result
    schema: successful JSON values remain compatible with existing providers.
    Invalid metadata cannot erase the separately recorded completion fact.
    """
    details = public_error(error)["details"]
    for field in ("external_result", "result"):
        if field in details:
            return {"external_result": details[field]}
    return {}


@contextmanager
def before_effect():
    """Mark failures in a block which cannot yet execute its target effect.

    Existing effect outcomes (notably an unresolved idempotent replay) are never
    replaced. Callers must end this boundary before invoking the effect/handler.
    """
    try:
        yield
    except Exception as error:
        details = error.details if isinstance(error, OrchestrationError) and isinstance(error.details, dict) else {}
        if (getattr(error, "_xorchestrate_effect_outcome", None) is None
                and "outcome" not in details):
            error._xorchestrate_effect_outcome = "not_started"
            if isinstance(error, OrchestrationError):
                error.details = {**details, "outcome": "not_started"}
        raise
