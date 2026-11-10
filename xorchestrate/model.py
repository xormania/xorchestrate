"""Values exchanged at the application boundary; independent of infrastructure."""

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

JSON = dict[str, Any]


def canonical_digest(value: Any) -> str:
    """SHA-256 of canonical JSON: sorted keys, compact separators, finite numbers only."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class OrchestrationError(ValueError):
    """An expected failure with a machine-readable category."""

    def __init__(self, message: str, code: str = "invalid_request", details: JSON | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


def unique_keys(message: str):
    """Return a JSON object_pairs_hook that rejects duplicate keys.

    ``message`` may reference the duplicated name as ``{key}``.
    """
    def hook(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(message.format(key=key))
            result[key] = value
        return result
    return hook


class Conflict(OrchestrationError):
    def __init__(self, message: str = "The record changed; read its current version before retrying.", *, retry_safe: bool = False):
        super().__init__(message, "conflict", {"retry_safe": True} if retry_safe else None)


@dataclass(frozen=True)
class Record:
    kind: str
    key: str
    data: JSON
    version: int


def record_view(record: Record) -> JSON:
    """A detached public copy of a record with its identity and version."""
    return {**deepcopy(record.data), "id": record.key, "version": record.version}


def public_error(error: Exception) -> JSON:
    """One JSON-safe diagnostic envelope for public and retained failures.

    Only the application's typed exception declares its diagnostic public.
    Arbitrary exception attributes, including a dynamically named class, never
    become public data. A malformed typed detail payload is omitted without
    changing independently known effect outcome.
    """
    fallback = {"code": "external_error",
                "message": "External operation did not return a verified result.", "details": {}}
    if not isinstance(error, OrchestrationError):
        return fallback
    try:
        message, code, supplied_details = str(error), error.code, error.details
    except Exception:
        return fallback
    if not isinstance(code, str) or not code:
        return fallback
    details = {}
    if isinstance(supplied_details, dict):
        try:
            details = json.loads(json.dumps(supplied_details, allow_nan=False))
        except (TypeError, ValueError, OverflowError, RecursionError):
            pass
    return {"code": code, "message": message, "details": details}


def public_error_message(error: Exception) -> str:
    return public_error(error)["message"]


def project_operation_errors(value: JSON) -> JSON:
    """Project legacy diagnostics conservatively; do not purge stored history.

    Diagnostic-version markers are written by the journal, separately for each
    field. Provider results are opaque and are deliberately not traversed.
    """
    visible = deepcopy(value)
    if "error" in visible and visible.get("error_schema_version") != 1:
        visible["error"] = public_error(Exception())
        visible["legacy_error_retained"] = True
    if "application_error" in visible and visible.get("application_error_schema_version") != 1:
        visible["application_error"] = public_error_message(Exception())
        visible["legacy_error_retained"] = True
    return visible


@dataclass(frozen=True)
class Capability:
    name: str
    description: str
    input_schema: JSON = field(default_factory=lambda: {"type": "object"})
    annotations: JSON = field(default_factory=dict)


def action_spec(description: str, properties: JSON, required: tuple[str, ...] = (), *, read: bool = False) -> Capability:
    """Describe an application action with a closed JSON object input schema."""
    return Capability("", description, {
        "type": "object", "properties": properties, "required": list(required),
        "additionalProperties": False,
    }, {"readOnlyHint": read})


@dataclass(frozen=True)
class Assessment:
    outcome: str
    confidence: float | None = None
    rationale: str = ""
    evidence: tuple[Any, ...] = ()
    details: JSON = field(default_factory=dict)


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    reason: str = ""
    review: bool = False
    details: JSON = field(default_factory=dict)
