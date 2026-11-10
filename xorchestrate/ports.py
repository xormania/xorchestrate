"""Purpose-oriented contracts owned by the orchestration application.

The public ports, the ones adapters implement, are defined in the Apache-2.0
``xcontracts`` package and re-exported here, so the application and its
adapters keep importing them from ``xorchestrate.ports``. The driving-side
client transport ports (``PeerIdentity``, ``ClientConnection``,
``ClientTransport``) stay here: the entrypoints choose them, not configuration,
and they evolve with the project server.

Implementations are supplied by composition. No port exposes SQL, subprocesses,
MCP packets, vendor model names, or a prescribed organizational hierarchy.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from xcontracts.ports import (
    AcceptancePolicy,
    ApplicationToolCallback,
    ApplicationToolSessionDriver,
    CapabilityProvider,
    Clock,
    Companion,
    CompanionRecords,
    DeliveryRetry,
    DeliveryScheduling,
    Evaluator,
    Guardrail,
    HostingVisibility,
    Identifier,
    ReceiptPolicy,
    RepositoryHosting,
    ResumableSessionDriver,
    RoutingPolicy,
    SessionDriver,
    SessionSelection,
    StateStore,
    TelemetrySink,
    Transaction,
    VersionControl,
    WorktreeLocation,
)

from .model import JSON

__all__ = [
    "AcceptancePolicy", "ApplicationToolCallback", "ApplicationToolSessionDriver", "CapabilityProvider",
    "ClientConnection", "ClientTransport", "Clock", "Companion", "CompanionRecords", "DeliveryRetry",
    "DeliveryScheduling", "Evaluator", "Guardrail", "HostingVisibility", "Identifier", "PeerIdentity",
    "ReceiptPolicy", "RepositoryHosting", "ResumableSessionDriver", "RoutingPolicy", "SessionDriver",
    "SessionSelection", "StateStore", "TelemetrySink", "Transaction", "VersionControl", "WorktreeLocation",
]


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
