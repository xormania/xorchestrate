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


def public_error_message(error: Exception) -> str:
    # Expected adapter errors have deliberately public messages. Arbitrary errors
    # may embed credentials, process input or SDK internals.
    return str(error) if isinstance(error, OrchestrationError) else f"External operation did not return a verified result ({type(error).__name__})."


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
