"""JSON-lines framing and request correlation for a native harness's
protocol channel.

Contract: ops/conductor/sessions-lever.md, "The adapter boundary". Adapted
from the proof's asyncio RPC reader (`RPC.read`) and its id book-keeping,
made synchronous: this module reads no process and owns no transport. The
caller records each request this side sends with `sent`, feeds each line
it receives with `feed`, and records each answer it writes with `answered`.

As in the proof, a response correlates with a request this side sent; a
request the other side sends is answered, and its id is held only until
the answer is recorded, so a second request with that id is refused.

Source: /home/scientdb/projects/poc/multi-harness-proof@a944f31
(mhproof/rpc.py: RPCError, RPC.request, RPC.read)

Stdlib only.
"""
from __future__ import annotations

import json


class Refused(Exception):
    """A frame the parser cannot correlate. `reading` names the line.
    `stray` is true only for a response whose id matches no request this
    side sent: the frame is whole, and the caller may drop it."""

    def __init__(self, reading: str, *, stray: bool = False):
        self.reading = reading
        self.stray = stray
        super().__init__(reading)


class Parser:
    """Classifies one received native-protocol line at a time.

    A line this parser cannot place is refused, naming the line itself,
    never a summary of it.
    """

    def __init__(self):
        self._sent: set = set()
        self._received: set = set()

    def sent(self, ident) -> None:
        """Record a request this side wrote, so its response correlates."""
        if ident is None:
            raise Refused("a sent request with no id cannot be correlated")
        _check_id(ident, repr(ident))
        if ident in self._sent:
            raise Refused(f"a second sent request with id {ident!r} already pending")
        self._sent.add(ident)

    def answered(self, ident) -> None:
        """Record that this side answered a received request."""
        self._received.discard(ident)

    def feed(self, line: str) -> dict:
        try:
            packet = json.loads(line)
        except ValueError:
            raise Refused(f"non-JSON line on the native protocol channel: {line}") from None
        if not isinstance(packet, dict):
            raise Refused(f"non-object frame on the native protocol channel: {line}")
        if "method" in packet:
            ident = packet.get("id")
            if ident is not None:
                _check_id(ident, line)
                if ident in self._received:
                    raise Refused(f"a second request with id {ident!r} before the first was answered: {line}")
                self._received.add(ident)
            return packet
        ident = packet.get("id")
        if ident is None:
            raise Refused(f"a result with no id to correlate it: {line}")
        _check_id(ident, line)
        if ident not in self._sent:
            raise Refused(f"a response whose id {ident!r} matches no request this side sent: {line}",
                          stray=True)
        self._sent.discard(ident)
        return packet


def _check_id(ident, line: str) -> None:
    """A JSON-RPC id is a string or a number; anything else is refused."""
    if isinstance(ident, bool) or not isinstance(ident, (str, int, float)):
        raise Refused(f"an id that is neither a string nor a number: {line}")
