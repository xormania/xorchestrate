"""Purpose-oriented contracts owned by the orchestration application.

Implementations are supplied by composition. No port exposes SQL, subprocesses,
MCP packets, vendor model names, or a prescribed organizational hierarchy.
"""
from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Protocol

from .model import JSON, Assessment, Capability, Record, Verdict


class Transaction(Protocol):
    def abort(self) -> None:
        """Mark the whole transaction for rollback, even if its caller catches an error."""
        ...
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


class TelemetrySink(Protocol):
    """Optional observations; capture receipts do not establish action success.

    Rows have stable event identities, collection sequences, protected payloads
    and their explicit source context. Reads do not initialize absent storage.
    Bundle operations authorize every row of their complete closure through the
    supplied callback before returning a receipt.
    """

    project_id: str | None

    def new_id(self) -> str: ...
    def record(self, event_type: str, payload: JSON, context: JSON, **metadata: object) -> JSON: ...
    def scan(self, *, after: int = 0, limit: int = 100, **filters: object) -> JSON:
        """Filter one bounded raw batch.

        Returns matching ``events``, raw ``next_after`` and ``scanned`` progress,
        and ``exhausted``. At most ``limit`` repository rows may be read,
        including rows rejected by resource filters. Empty matches may advance.
        """
        ...
    def get_event(self, event_id: str, *, hydrate: bool = True) -> JSON | None: ...
    def resolve_scope(self, row: JSON, *, database_path: object | None = None) -> JSON | None:
        """Recover typed work/session/operation scope for an unscoped row, if known."""
        ...
    def view_observation(self, row: JSON, *, hydrate: bool = True, artifact_store: object | None = None) -> JSON: ...
    def status(self) -> JSON: ...
    def read_payload(self, event_id: str, digest: str | None = None, offset: int = 0, limit: int = 65536) -> JSON: ...
    def snapshot_source(self) -> JSON: ...
    def bundle_source(self, destination: str) -> JSON: ...
    def snapshot(self, destination: str, *, authorize: Callable[[JSON], bool] | None = None,
                 receipt: bool = False) -> JSON: ...
    def verify(self, destination: str, *, authorize: Callable[[JSON], bool] | None = None,
               receipt: bool = False) -> JSON: ...
    def restore(self, bundle: str, destination: str, *, authorize: Callable[[JSON], bool] | None = None,
                receipt: bool = False) -> JSON: ...
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
    """Optional port for drivers that expose application actions as native tools.

    Native tool descriptions, requests and response envelopes belong to the
    adapter. Ordinary session drivers need not implement this port.

    A driver that answers repeated native calls from its own call ledger may
    also define ``bind_application_replay(callback)``, with the same callback
    signature. Composition then validates each completed cache hit through it
    without invoking a new effect: the callback retains the original native
    call identity, rechecks current authorization and result applicability,
    and rejects a missing/unresolved durable call. Drivers without a replay
    ledger omit it and keep their own cache-only replay contract.
    """

    def bind_application_tools(self, capabilities: list[Capability], callback: ApplicationToolCallback) -> None:
        """Bind application actions before starting sessions."""
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


class SessionSelection(Protocol):
    def select(self, message: JSON, recipient: str, sessions: list[JSON]) -> str | None: ...


class DeliveryScheduling(Protocol):
    dispatch_on_enqueue: bool
    def ready(self, delivery: JSON, session: JSON, now: str) -> bool: ...


class DeliveryRetry(Protocol):
    def permit(self, delivery: JSON, history: list[JSON]) -> bool: ...


class ReceiptPolicy(Protocol):
    def evaluate(self, snapshot: JSON, recipient: str, expectation: JSON) -> JSON: ...


class AcceptancePolicy(Protocol):
    identity: str
    def evaluate(self, snapshot: JSON) -> JSON: ...


@dataclass(frozen=True)
class PeerIdentity:
    """Who is on the other end of one client connection.

    ``uid`` and ``pid`` are what the transport verified (``None`` when it cannot
    observe them). ``actor`` is asserted by the client and has the same trust
    level as a local ``--actor`` flag. ``purpose`` names what the client asked
    for (an MCP session or a server control request). ``attributes`` carries
    further client-asserted context as detached JSON; later identity facts,
    such as the client's worktree, travel here without changing the port.
    """

    uid: int | None
    pid: int | None
    actor: str
    purpose: str = "mcp"
    attributes: JSON = field(default_factory=dict)


class ClientConnection(Protocol):
    """One client's bidirectional stream of newline-free protocol frames."""

    peer: PeerIdentity

    def read_frame(self) -> bytes | None:
        """Return the next nonblank frame, or ``None`` once the client has finished.

        Called from one reader thread. An oversized frame is discarded and
        reported with an ``OrchestrationError`` coded ``frame_too_large``; the
        connection stays usable for the next frame.
        """
        ...

    def write_frame(self, frame: bytes) -> None:
        """Send one frame. Safe to call from several threads; frames never interleave."""
        ...

    def close(self) -> None:
        """End this connection only; idempotent. Pending reads return ``None``."""
        ...


class ClientTransport(Protocol):
    """Driving-side source of client connections, chosen by the entrypoint.

    A transport with a handshake may also offer ``set_admission(admission)``:
    ``admission.admit(peer)`` returns ``None`` to accept or ``(code, message)``
    to refuse the client in the handshake reply, and ``admission.cancel(peer)``
    undoes an admission whose connection was never delivered.
    """

    def accept(self, timeout: float | None = None) -> ClientConnection | None:
        """Wait for the next verified connection; ``None`` on timeout or once closed."""
        ...

    def close(self) -> None:
        """Stop accepting; already accepted connections remain open."""
        ...
