"""The member's bridge loop, sessions slice 2, in process.

Contract: ``ops/conductor/sessions-lever.md``, "Slice 2 — persistent
native conversation": the bridge reads the harness's frames, carries
queued messages on its tick, and never exits on a delivery error (it
backs off and reads degraded). ``sessions.py``'s ``Bridge`` is driven
here directly, without tmux or a profile: the codex adapter speaks to
the planted codex fake over its own ``connect`` (the bridge's call),
and the carry pass is observed at the ``Bridge.carry`` seam.

Rows (11's review of fed6a4ce, relayed by the XO 2026-09-23):

- a turn streaming deltas across the tick still gets a carry pass while
  it streams (the loop once went back to the channel after every frame);
- a ``sqlite3.OperationalError`` "database is locked" on the route pass
  backs off and reads degraded, and the pass returns; a clean pass beside
  it resets nothing and returns the tick.

Planted faults: ``session_bridge_faults.py`` beside this module, anchored
on sessions.py at bf924ee7.

The one command that reads this file is
`python3 -m unittest discover -s ops/conductor/tests -p test_session_bridge.py`.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from ops.testkit import World

REPO = Path(__file__).resolve().parents[3]
TOOLS = REPO / "ops" / "conductor" / "tools"
SESSIONS = TOOLS / "sessions.py"
CODEX = TOOLS / "session_adapters" / "codex.py"
FAKES = Path(__file__).resolve().parent / "fakes"
FAKE_SRC = FAKES / "session_codex.py"
ORIGIN = "sessions-lever.md#slice-2 bridge"
CHANNEL_ARGV = ["app-server", "--listen", "stdio://"]
SHORT_ROOT = "/tmp"
TICK = 0.2
DELTAS = 40
INTERVAL = 0.05
LOOP_WAIT = 20.0
LOCKED = "database is locked"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def open_world(prefix: str) -> World:
    """A world rooted under /tmp, as the other slice 2 modules root theirs."""
    saved = tempfile.tempdir
    tempfile.tempdir = SHORT_ROOT
    try:
        return World(prefix=prefix)
    finally:
        tempfile.tempdir = saved


def fake_text(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    return f"#!{sys.executable}\n{text}"


class TheBridge(unittest.TestCase):
    """origin: sessions-lever.md#slice-2 bridge"""

    def setUp(self):
        self.world = open_world("session-bridge-")
        self.addCleanup(self.world.close)
        self.planted = self.world.tools.plant("codex", script=fake_text(FAKE_SRC))
        self.directory = self.world.root / "member"
        self.directory.mkdir()
        self.sessions = load_module(SESSIONS, "sessions_bridge_rows")
        self.codex = load_module(CODEX, "session_adapters_codex_bridge_rows")

    def write_spec(self, **fields) -> Path:
        spec_path = self.world.tools.spec_path("codex")
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        spec.update(fields)
        spec_path.write_text(json.dumps(spec, indent=1) + "\n", encoding="utf-8")
        if json.loads(spec_path.read_text(encoding="utf-8")) != spec:
            raise AssertionError("INVALID: codex spec did not land intact")
        return spec_path

    def bridge(self, adapter, rpc):
        member = {"name": "dispatcher", "harness": "codex", "generation": 1}
        return self.sessions.Bridge(adapter, rpc, member, self.directory, TICK)

    def test_a_streaming_turn_still_gets_a_carry_pass(self):
        """A turn streaming a delta every 50 ms, past a 200 ms tick, sees a
        carry pass between its first delta and its last."""
        with self.subTest(id="carry-while-streaming", origin=ORIGIN):
            spec_path = self.write_spec(
                native_id="thread-bridge", turn_id="turn-bridge", reply="d",
                stream_deltas=DELTAS, stream_interval=INTERVAL, die_after="turn/start",
            )
            adapter = self.codex.Adapter()
            rpc = adapter.connect(
                [str(self.planted), *CHANNEL_ARGV],
                env=self.world.env(extra={"TESTKIT_FAKE_SPEC": str(spec_path)}),
                cwd=str(self.world.cwd),
            )
            self.world.own(rpc.process)
            self.addCleanup(rpc.close)
            adapter.handshake(rpc)
            adapter.submit(rpc, "stream a long turn", busy=False)
            bridge = self.bridge(adapter, rpc)
            carries: list[float] = []
            heard: list[tuple[float, str]] = []
            original = bridge.heard

            def carry() -> float:
                carries.append(time.monotonic())
                return TICK

            def hear(reading: str) -> None:
                heard.append((time.monotonic(), reading))
                original(reading)

            bridge.carry = carry
            bridge.heard = hear
            ended: list = []
            printed = io.StringIO()

            def run_loop() -> None:
                with contextlib.redirect_stdout(printed):
                    ended.append(bridge.loop())

            loop = threading.Thread(target=run_loop, name="bridge-loop", daemon=True)
            loop.start()
            loop.join(LOOP_WAIT)
            self.assertFalse(loop.is_alive(), f"the loop did not end within {LOOP_WAIT}s")
            deltas = [at for at, reading in heard if reading.startswith("output ")]
            self.assertEqual(len(deltas), DELTAS, heard)
            during = [at for at in carries if deltas[0] < at < deltas[-1]]
            self.assertTrue(during, f"no carry pass while the turn streamed: carries {carries}, "
                                    f"deltas {deltas[0]}..{deltas[-1]}")

    def test_a_locked_store_backs_off_and_the_pass_returns(self):
        """``database is locked`` on the route pass: the pass returns the
        backoff, doubling on a second lock, and reads degraded."""
        with self.subTest(id="locked-store-backs-off", origin=ORIGIN):
            bridge = self.bridge(self.codex.Adapter(), None)
            courier = self.courier()
            locked = sqlite3.OperationalError(LOCKED)
            printed = io.StringIO()
            with mock.patch.object(courier, "route_step", side_effect=locked), \
                    contextlib.redirect_stdout(printed):
                first = bridge.carry()
                second = bridge.carry()
            self.assertGreater(first, TICK, printed.getvalue())
            self.assertEqual(second, min(first * 2, self.sessions.BACKOFF_MAX))
            self.assertIn(LOCKED, str(bridge.state["degraded"]))
            saved = json.loads((self.directory / "bridge.json").read_text(encoding="utf-8"))
            self.assertIn(LOCKED, str(saved["degraded"]))
            self.assertEqual(saved["backoff"], second)

    def test_a_clean_pass_returns_the_tick(self):
        """The clean neighbour: a pass with nothing to carry and no error
        returns the tick and reads no degradation."""
        with self.subTest(id="clean-pass-tick", origin=ORIGIN):
            bridge = self.bridge(self.codex.Adapter(), None)
            courier = self.courier()
            printed = io.StringIO()
            with mock.patch.object(courier, "route_step", return_value=None), \
                    mock.patch.object(courier, "carry", return_value=[]), \
                    contextlib.redirect_stdout(printed):
                waited = bridge.carry()
            self.assertEqual(waited, TICK, printed.getvalue())
            self.assertIsNone(bridge.state["degraded"])

    def courier(self):
        """The courier module ``Bridge.carry`` imports: the one on the
        tools path sessions.py put there."""
        import courier
        self.assertEqual(Path(courier.__file__).resolve(), (TOOLS / "courier.py").resolve())
        return courier


if __name__ == "__main__":
    unittest.main()
