"""Purpose-oriented contracts owned by the orchestration application.

The public ports, the ones adapters implement, are defined in the Apache-2.0
``xcontracts`` package and re-exported here, so the application and its
adapters keep importing them from ``xorchestrate.ports``. The driving-side
client transport ports (``PeerIdentity``, ``ClientConnection``,
``ClientTransport``) stay here: the entrypoints choose them, not configuration,
and they evolve with the project server. So do the pull request delivery ports
(``PullRequestHost``, ``PullRequestWorkspace``), which are new and still changing,
and the terminal host port (``TerminalHost`` and its values), for the same reason.

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
    "PullRequestHost", "PullRequestWorkspace", "ReceiptPolicy", "RepositoryHosting", "ResumableSessionDriver", "RoutingPolicy", "SessionDriver",
    "SessionSelection", "StateStore", "TelemetrySink", "Transaction", "VersionControl", "WorktreeLocation",
    "AttachInstruction", "HostedTerminal", "TerminalClosure", "TerminalCommand", "TerminalHost",
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


# -- pull request delivery ports ------------------------------------------------
# Used by the application's PR preparation (``xorchestrate/pr.py``) through the
# built-in ``pr`` provider. They are new and still changing, so they stay here
# rather than in xcontracts, like the client transport ports.

class PullRequestHost(Protocol):
    """Fresh hosted PR observations and the one draft-creation write.

    Snapshots enumerate their bounded collections or explicitly fail incomplete.
    Write errors retain effect-outcome knowledge; they never return an ordinary
    success-shaped object for an uncertain mutation.
    """

    def snapshot(self, repository: str, number: int) -> JSON: ...
    def branch(self, repository: str, ref: str) -> JSON | None: ...
    def find(self, repository: str, branch: str) -> list[JSON]: ...
    def create(self, repository: str, branch: str, base: str, title: str, body: str) -> JSON: ...


class PullRequestWorkspace(Protocol):
    """Explicitly owned source preparation without candidate execution."""

    def identity(self, name: str) -> JSON: ...
    def refresh(self, name: str, expected_head: str, target: str, paths: list[str]) -> JSON: ...
    def publish(self, name: str, expected_head: str, branch: str) -> JSON: ...


# -- terminal host ---------------------------------------------------------------
# Where Xorchestrate keeps long-running local work (the project server, hosted
# sessions) so that it can be listed, driven, attached to and recovered. The
# adapter is selected by name from the user's home; nothing here names a
# particular multiplexer or terminal mechanism.


@dataclass(frozen=True)
class TerminalCommand:
    """What one hosted terminal runs. Comes only from trusted configuration.

    ``environment`` is the exact environment the command runs with: the caller
    builds it. The adapter adds only what its mechanism requires (for example a
    terminal type) and removes its own nesting markers. ``rows``/``columns``
    size the terminal; ``None`` takes the adapter's configured default.
    """

    argv: tuple[str, ...]
    cwd: str
    environment: tuple[tuple[str, str], ...] = ()
    rows: int | None = None
    columns: int | None = None


@dataclass(frozen=True)
class HostedTerminal:
    """One terminal a host owns, as observed now.

    ``running`` is false only when the adapter knows the command exited; then
    ``exit_status`` (an exit code) or ``exit_signal`` (the terminating signal
    number) says how, and both are ``None`` when the adapter cannot know.
    ``receipt`` is opaque detached JSON the adapter produces; saving it lets a
    restarted controller ``adopt`` the terminal. It carries the adapter's own
    identity evidence, never a caller-supplied process identifier, and the port
    never exposes process identifiers.
    """

    name: str
    running: bool
    exit_status: int | None
    exit_signal: int | None
    receipt: JSON


@dataclass(frozen=True)
class TerminalClosure:
    """The final observation of a closed terminal and whether its cleanup was verified.

    ``cleanup_verified`` is true only when the adapter confirmed that the terminal's
    work is gone; otherwise something may still be running and the caller
    should report it.
    """

    terminal: HostedTerminal
    cleanup_verified: bool


@dataclass(frozen=True)
class AttachInstruction:
    """How an operator joins a terminal interactively: a command and a human sentence."""

    argv: tuple[str, ...]
    text: str


class TerminalHost(Protocol):
    """A named group of terminals (one host per project) that this user owns.

    Ownership: a controller controls only the terminals it opened or adopted.
    ``terminals()`` lists exactly those; anything else found at the host's
    location is not controllable until ``adopt(receipt)`` has verified it.
    Every control call (input, interrupt, output, attach, close) checks the
    terminal's identity first and refuses a terminal that is no longer the one
    it opened or adopted.

    Terminal names are letters, digits, ``.``, ``_`` and ``-`` (else
    ``invalid_arguments``). Other failures are ``OrchestrationError`` coded
    ``terminal_host_unavailable``, ``terminal_operation_unsupported`` (the
    host lacks the capability, see ``interactive``),
    ``terminal_exists``, ``terminal_unknown``, ``terminal_not_recoverable`` or
    ``terminal_identity_mismatch``.
    """

    name: str
    survives_controller: bool
    """Whether terminals outlive the process that opened them (and so can be adopted)."""
    interactive: bool
    """Whether terminals accept ``send_input`` and have an ``attach_instruction``."""

    def readiness(self) -> str | None:
        """Why this host cannot run terminals on this machine now, or ``None``. Never raises."""
        ...

    def open_terminal(self, name: str, command: TerminalCommand, log: str | None = None) -> HostedTerminal:
        """Start ``command`` in a new terminal named ``name``.

        A name is refused (``terminal_exists``) only while a terminal of that
        name is running at this host. An exited terminal of that name, held by
        this controller or not, is replaced: its output is already in its log or
        has been read. A running terminal this controller does not hold is
        still refused; for an interactive host the error details carry an
        ``attach`` command so the operator can look at it.

        ``log`` is an absolute path in a private directory of this user:
        everything the terminal prints is also appended there (created 0600),
        so the output outlives the terminal and its mechanism.
        """
        ...

    def terminals(self) -> list[HostedTerminal]:
        """The terminals this controller opened or adopted, sorted by name, exited ones included until closed."""
        ...

    def send_input(self, name: str, text: str) -> None:
        """Type ``text`` literally into a running terminal (include ``\\n`` to submit); needs ``interactive``."""
        ...

    def interrupt(self, name: str) -> None:
        """Ask the terminal's work to stop (an interrupt, as the operator's keyboard would); the terminal stays open."""
        ...

    def recent_output(self, name: str, lines: int | None = None) -> str:
        """The last ``lines`` lines of output (adapter-configured cap when ``None``).

        Works for an exited terminal until it is closed.
        """
        ...

    def attach_instruction(self, name: str | None = None) -> AttachInstruction:
        """How the operator attaches to ``name``, or to the whole host for ``None``.

        Validates first: the host's private location is still owned by this
        user and the terminal is still the one opened or adopted; refuses
        otherwise. ``terminal_operation_unsupported`` when not ``interactive``.
        """
        ...

    def close_terminal(self, name: str, grace: float | None = None) -> TerminalClosure:
        """Stop and forget one terminal, allowing ``grace`` seconds (adapter default when ``None``) before forcing.

        Idempotent for a known name until forgotten.
        """
        ...

    def adopt(self, receipt: JSON) -> HostedTerminal:
        """Take back a terminal this host opened before the controller restarted.

        The adapter verifies the receipt against its own private record of what
        it launched and against what is actually running: a receipt of another
        host, of something the host did not launch, or of a reused identity is
        ``terminal_identity_mismatch``; a vanished one ``terminal_not_recoverable``.
        This protects against stale, corrupt or foreign records; other
        processes of the same user are trusted, as elsewhere in the product.
        """
        ...

    def shutdown(self) -> None:
        """Stop every terminal of this host and remove its private state."""
        ...

    def close(self) -> None:
        """Release this controller's resources. Terminals survive iff ``survives_controller``."""
        ...
