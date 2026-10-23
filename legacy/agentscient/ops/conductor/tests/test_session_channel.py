"""The channel's reader, sessions slice 2.

Contract: `ops/conductor/sessions-lever.md` "The bridge" at 7952a9a6: a
response whose id matches no request this side sent is dropped and written
to `channel.log` with its id, never fatal, while every other framing
refusal ends the channel. The subject is `session_adapters/channel.py`'s
`RPC`, spoken to over two OS pipes the row owns: the row writes the peer's
lines into the reader's pipe and the RPC's log is a file in the world.

The stray row is aa's shape, measured on grok 1.0.40 (e8cae56c): a
`session/update`, then the `skills-reload` response nobody asked for,
then `session/update` and `session/request_permission`. The channel stays
alive, every good frame arrives in order, `strays` reads 1 and the log
carries one `channel: dropped` line naming `'skills-reload'`. The
neighbours still end the channel: a non-JSON line, and a second request
with an id the bridge has not answered (origin: A-527, aa's e8cae56c,
and the contract line).

Planted faults: `session_channel_faults.py` beside this module, anchored
on the channel at 7952a9a6.

The one command that reads this file is
`python3 -m unittest discover -s ops/conductor/tests -p test_session_channel.py`.
"""

from __future__ import annotations

import importlib.util
import json
import os
import time
import unittest
from pathlib import Path

from ops.testkit import World

REPO = Path(__file__).resolve().parents[3]
CHANNEL = REPO / "ops" / "conductor" / "tools" / "session_adapters" / "channel.py"
ORIGIN = "A-527, aa's e8cae56c; sessions-lever.md the bridge at 7952a9a6"
SESSION_ID = "sess-channel-527"
STRAY = "skills-reload"
WAIT = 2.0


def load_channel():
    spec = importlib.util.spec_from_file_location("session_adapters_channel_527", CHANNEL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def update(text: str) -> dict:
    return {"jsonrpc": "2.0", "method": "session/update", "params": {
        "sessionId": SESSION_ID,
        "update": {"sessionUpdate": "agent_message_chunk",
                   "content": {"type": "text", "text": text}},
    }}


def permission(ident: str) -> dict:
    return {"jsonrpc": "2.0", "id": ident, "method": "session/request_permission",
            "params": {"sessionId": SESSION_ID, "title": "use_tool",
                       "toolCall": {"toolCallId": "call-1", "title": "use_tool"},
                       "options": [{"optionId": "reject-once", "name": "Reject",
                                    "kind": "reject_once"}]}}


def line(frame) -> str:
    return frame if isinstance(frame, str) else json.dumps(frame)


class TheChannelReader(unittest.TestCase):
    """RPC._read over pipes. Rows are addressed by id."""

    def setUp(self):
        self.world = World(prefix="session-channel-")
        self.addCleanup(self.world.close)
        self.log_path = self.world.root / "channel.log"

    def open_rpc(self):
        """An RPC over two pipes: the peer's end of its reader, and the
        RPC's log, a file in the world. None when channel.py is missing."""
        if not CHANNEL.is_file():
            return None, None
        module = load_channel()
        read_fd, peer_fd = os.pipe()
        sink_read, sink_write = os.pipe()
        reader = os.fdopen(read_fd, "rb")
        peer = os.fdopen(peer_fd, "wb")
        writer = os.fdopen(sink_write, "wb")
        drain = os.fdopen(sink_read, "rb")
        log = self.enterContext(open(self.log_path, "ab"))
        for handle in (reader, writer, drain):
            self.addCleanup(handle.close)
        rpc = module.RPC(reader, writer, jsonrpc=True, log=log)
        # The peer's end closes first: the reader thread reads end of input
        # and returns, so closing the reader never waits on its lock.
        self.addCleanup(rpc.dead.wait, WAIT)
        self.addCleanup(peer.close)
        return rpc, peer

    def feed(self, peer, frames) -> None:
        for frame in frames:
            peer.write((line(frame) + "\n").encode("utf-8"))
        peer.flush()

    def drain(self, rpc, count: int) -> list:
        """Up to ``count`` frames the RPC queued, within the wait."""
        got = []
        deadline = time.monotonic() + WAIT
        while len(got) < count and time.monotonic() < deadline:
            frame = rpc.next_frame(0.05)
            if frame is not None:
                got.append(frame)
        return got

    def wait_dead(self, rpc) -> bool:
        return rpc.dead.wait(WAIT)

    def logged(self) -> list[str]:
        if not self.log_path.is_file():
            return []
        return [text for text in self.log_path.read_text(encoding="utf-8").splitlines()
                if text.startswith("channel: dropped")]

    def test_a_stray_response_between_good_frames_is_dropped(self):
        """origin: A-527, aa's e8cae56c; sessions-lever.md the bridge at 7952a9a6"""
        with self.subTest(id="channel-stray-dropped", origin=ORIGIN):
            rpc, peer = self.open_rpc()
            self.assertIsNotNone(rpc, f"channel.py missing: {CHANNEL}")
            good = [update("before"), update("after"), permission("perm-1")]
            self.feed(peer, [good[0], {"jsonrpc": "2.0", "id": STRAY, "result": {}},
                             good[1], good[2]])
            got = self.drain(rpc, 3)
            alive = not rpc.dead.is_set()
            self.assertEqual(got, good)
            self.assertTrue(alive, rpc.failure)
            self.assertIsNone(rpc.failure)
            self.assertEqual(len(rpc.strays), 1, rpc.strays)
            lines = self.logged()
            self.assertEqual(len(lines), 1, lines)
            self.assertIn(repr(STRAY), lines[0])

    def test_a_stray_does_not_settle_a_pending_request(self):
        """origin: A-527, aa's e8cae56c; sessions-lever.md the bridge at 7952a9a6"""
        with self.subTest(id="channel-stray-leaves-pending", origin=ORIGIN):
            rpc, peer = self.open_rpc()
            self.assertIsNotNone(rpc, f"channel.py missing: {CHANNEL}")
            pending = rpc.send("session/prompt", {"sessionId": SESSION_ID})
            self.feed(peer, [{"jsonrpc": "2.0", "id": STRAY, "result": {}},
                             {"jsonrpc": "2.0", "id": 1, "result": {"stopReason": "end_turn"}}])
            result = pending.wait(WAIT)
            self.assertEqual(result, {"stopReason": "end_turn"})
            self.assertFalse(rpc.dead.is_set(), rpc.failure)
            self.assertEqual(len(rpc.strays), 1, rpc.strays)

    def test_every_other_framing_refusal_ends_the_channel(self):
        """origin: A-527, aa's e8cae56c; sessions-lever.md the bridge at 7952a9a6"""
        rows = [
            ("channel-non-json-dead", "this is not json", "non-JSON line"),
            ("channel-duplicate-request-dead", permission("perm-1"),
             "a second request with id 'perm-1'"),
        ]
        for ident, bad, named in rows:
            with self.subTest(id=ident, origin=ORIGIN):
                rpc, peer = self.open_rpc()
                self.assertIsNotNone(rpc, f"channel.py missing: {CHANNEL}")
                first = permission("perm-1") if ident == "channel-duplicate-request-dead" else update("before")
                self.feed(peer, [first, bad, update("after")])
                dead = self.wait_dead(rpc)
                got = self.drain(rpc, 1)
                late = rpc.next_frame(0)
                self.assertTrue(dead, "the channel stayed alive")
                self.assertIn(named, rpc.failure or "")
                self.assertEqual((got, late), ([first], None))
                self.assertEqual(rpc.strays, [])


if __name__ == "__main__":
    unittest.main()
