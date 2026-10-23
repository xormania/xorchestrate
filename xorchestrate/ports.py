"""Purpose-oriented contracts owned by the orchestration application.

Implementations are supplied by composition. No port exposes SQL, subprocesses,
MCP packets, vendor model names, or a prescribed organizational hierarchy.
"""
from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Protocol

from .model import JSON, Assessment, Capability, Record, Verdict


class Transaction(Protocol):
    def get(self, kind: str, key: str) -> Record | None: ...
    def list(self, kind: str) -> list[Record]: ...
    def put(self, kind: str, key: str, data: JSON, expected_version: int | None = None) -> Record:
        """Create if expected_version is None; otherwise compare and replace."""
        ...
    def delete(self, kind: str, key: str, expected_version: int) -> None: ...
    def emit(self, event: JSON) -> int: ...
    def events(self, after: int = 0, limit: int = 100) -> list[JSON]: ...


class StateStore(Protocol):
    def transaction(self) -> AbstractContextManager[Transaction]: ...
    def close(self) -> None: ...


class CapabilityProvider(Protocol):
    def discover(self) -> list[Capability]: ...
    def invoke(self, name: str, arguments: JSON, context: JSON) -> JSON:
        """Invoke the descriptor in context['capability']; reject changed metadata.

        The descriptor is the detached discovery snapshot selected by the caller,
        so a concurrent discovery must not silently change this invocation.
        """
        ...
    def close(self) -> None: ...


class SessionDriver(Protocol):
    def start(self, specification: JSON) -> JSON: ...
    def send(self, session: JSON, message: JSON) -> JSON: ...
    def poll(self, session: JSON) -> list[JSON]: ...
    def stop(self, session: JSON) -> JSON: ...
    def close(self) -> None: ...


class ResumableSessionDriver(SessionDriver, Protocol):
    def resume(self, source_backend: JSON, specification: JSON) -> JSON:
        """Resume a verified application session using its opaque driver receipt.

        The application authorizes source control and destination creation before
        calling this optional port. Native identity stays inside the adapter.
        """
        ...


class ApplicationToolCallback(Protocol):
    def __call__(self, action: str, arguments: JSON, context: JSON,
                 idempotency_key: str | None = None) -> JSON:
        """Call an application action using the owned session's trusted context.

        Adapters decode native arguments and supply a stable invocation key from
        their verified session identity, never a claimed identity in tool input.
        Composition namespaces that key by configured driver and requires it
        when the action schema requires idempotency_key. Context and arguments
        are detached JSON objects; incoming arguments cannot choose the caller.
        """
        ...


class ApplicationToolSessionDriver(SessionDriver, Protocol):
    def bind_application_tools(self, capabilities: list[Capability], callback: ApplicationToolCallback) -> None:
        """Optionally expose application actions before starting sessions.

        Native tool descriptions, requests and response envelopes belong to the
        adapter. Ordinary session drivers need not implement this optional port.
        """
        ...


class Evaluator(Protocol):
    def assess(self, question: str, context: JSON) -> Assessment: ...
    def close(self) -> None: ...


class Guardrail(Protocol):
    def evaluate(self, action: str, context: JSON) -> Verdict: ...


class RoutingPolicy(Protocol):
    def recipients(self, sender: str, destination: JSON, actors: list[JSON], relations: list[JSON]) -> list[str]: ...


class Clock(Protocol):
    def __call__(self) -> str: ...


Identifier = Callable[[], str]
