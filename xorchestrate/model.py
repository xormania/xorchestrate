"""Values exchanged at the application boundary; independent of infrastructure."""

from dataclasses import dataclass, field
from typing import Any

JSON = dict[str, Any]


class OrchestrationError(ValueError):
    """An expected failure with a machine-readable category."""

    def __init__(self, message: str, code: str = "invalid_request", details: JSON | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


class Conflict(OrchestrationError):
    def __init__(self, message: str = "The record changed; read its current version before retrying."):
        super().__init__(message, "conflict")


@dataclass(frozen=True)
class Record:
    kind: str
    key: str
    data: JSON
    version: int


@dataclass(frozen=True)
class Capability:
    name: str
    description: str
    input_schema: JSON = field(default_factory=lambda: {"type": "object"})
    annotations: JSON = field(default_factory=dict)


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
