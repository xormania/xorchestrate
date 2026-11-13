"""The port every end-to-end scenario is written against: a project world with named disturbances.

A world is one project on one terminal host, driven through the real command line, project server and
clients. Scenarios never reach for processes, sockets or sleeps themselves; they ask the world.

``World`` (see ``tests.hexagonal.support.e2e_world`` for the adapter):

- ``host``: the terminal host's name.
- ``save(policy=None)`` writes the project's configuration; ``policy="manual"`` selects the manual recovery policy.
- ``client(actor="worker")`` connects an MCP client through ``connect``. A client has ``call(name, args, *,
  refused=None)``, ``attempt(name, args)`` (the reply's structured content whether accepted or refused, with
  ``error`` when refused; it never raises on a refusal), ``send_without_waiting(name, args)``, ``close()`` and
  ``kill()`` (ends its process outright, as a crashed harness would), and is a context manager that closes it.
- ``start(client, *, in_flight=False)`` starts the hosted session and sends one turn; with ``in_flight`` a second
  turn is left in flight at the peer. Returns the session.
- ``up()``, ``down()`` (detaching), ``recover()`` and ``status()`` return the command's JSON reply.
- ``disturb(value)`` applies a disturbance and returns a ``Receipt``.
- ``bridge_hosts()`` lists the running bridge hosts of this project.
- ``socket_files()`` lists the Unix socket files present under the world's home, live or left behind.
- ``await_(label, predicate, *, timeout=None)`` returns the zero-argument predicate's first truthy value; at the
  deadline (the world's, or ``timeout``) it raises ``WaitExpired`` naming ``label``.
- ``hold(stage)`` arms a gate in the session peer at ``stage`` (``"resume"``: the peer is asked to continue a
  conversation) before the action that reaches it, and returns a ``Gate``: ``entered()`` is true once the peer is
  waiting there, ``release()`` lets it and every later arrival through.
- ``peer_received(session)`` lists what the session peer took delivery of for that session's conversation in this
  world, as entries with ``native_id`` and ``text``, in delivery order.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class TotalLoss:
    """Every process of the project dies at once, as in a container failure; the durable home stays."""
    keep_sockets: bool = False


@dataclass(frozen=True)
class ServerLoss:
    """Only the project server dies; terminals and their bridge hosts keep running."""


@dataclass(frozen=True)
class Receipt:
    kind: str
    target: Any
    before: Any
    effect: Any
    outcome: str
    """``"applied"``, or ``"no_victim"`` when there was nothing to disturb."""
    selected: dict = field(default_factory=dict)
    """Why each identity in ``before`` was a victim: ``"home"`` (it carried the world's XORCHESTRATE_HOME),
    ``"spawned"`` (``spawn`` started it), ``"socket"`` (it holds a listening Unix socket bound inside the world's
    home) or ``"descendant"`` (it descends from a process selected by another signal)."""
    ancestors: dict = field(default_factory=dict)
    """For each victim selected as ``"descendant"``: the identity of the ancestor that ``"home"``, ``"spawned"`` or
    ``"socket"`` selected, from which this descendant was reached (its root, not a descendant in between)."""
    leaked: list = field(default_factory=list)
    """Identities still observed in the world after the disturbance that no signal selected (re-parented away from
    the world). Identities only; reported, never killed."""


SELECTION_SIGNALS = frozenset({"home", "spawned", "socket", "descendant"})


class WaitExpired(AssertionError):
    """``await_`` reached its deadline; the message names what was awaited. A distinct type, so a calibrated check
    can tell an expired wait from any other assertion."""


class World(Protocol):
    host: str

    def save(self, policy=None): ...

    def client(self, actor="worker"): ...

    def start(self, client, *, in_flight=False) -> dict: ...

    def up(self) -> dict: ...

    def down(self) -> dict: ...

    def recover(self) -> dict: ...

    def status(self) -> dict: ...

    def disturb(self, value) -> Receipt: ...

    def bridge_hosts(self) -> list: ...

    def socket_files(self) -> list: ...

    def await_(self, label, predicate, *, timeout=None): ...

    def hold(self, stage): ...

    def peer_received(self, session) -> list: ...


def awaited_or_none(world, label, predicate):
    """``await_``'s value, or ``None`` at the deadline, so a calibrated check can assert on the outcome itself."""
    try:
        return world.await_(label, predicate)
    except WaitExpired:
        return None
