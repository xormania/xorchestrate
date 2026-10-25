"""world.daemon and world.adopt: a recorded daemon is stopped at close and
proven gone; a daemon its own stop does not bring down is refused class
daemon on the wire; a daemon started around the kit is refused by close's
sweep
(docs/tests.md §The kit, Daemons; the invariant testkit.daemon).

Each row closes a world holding one planted daemon in a child process,
fixtures/closer.py, so the wire is read as a caller reads it: exit, stdout,
stderr. Whether anything survived is read here from /proc, never from the
kit's own process table. Every survivor is killed before the row is judged.
The planted faults in world.py these rows catch are fixtures/daemon_faults.py.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import threading
import time
import unittest
import uuid
from pathlib import Path

import support

from ops.testkit import DaemonRefused, PlantFailed, RefusalCase, World, run, run_table

CLOSER = "ops.testkit.tests.fixtures.closer"
TIMEOUT = "2"
ORIGIN = "PR #274; PR #275: at a93368b5, 1 run in 20 leaked 18 tmux servers with no failure"
DAEMON = support.FIXTURES / "daemon.py"
A528 = "record 20260923T030855Z-review-codex-8585d2 (A-528, the audit of #274 at 86ed5089)"

CLEAN = (
    RefusalCase(
        "recorded-and-well-behaved", ("recorded",), "none", exit=0, origin=ORIGIN,
        note="world.daemon records it, its stop brings it down, close leaves nothing",
    ),
    RefusalCase(
        "adopted-and-well-behaved", ("adopted",), "none", exit=0, origin=ORIGIN,
        note="the subject starts it, world.adopt records it by its socket, close leaves nothing",
    ),
)
REFUSED = (
    RefusalCase(
        "recorded-and-deaf-to-its-stop", ("recorded", "--deaf-stop"), "daemon",
        named=("fixture", "SIGTERM ended it"), origin=ORIGIN,
        note="its stop answers and it keeps serving; SIGTERM ends it, and close still refuses",
    ),
    RefusalCase(
        "recorded-and-deaf-to-stop-and-sigterm", ("recorded", "--deaf-stop", "--deaf-term"), "daemon",
        named=("fixture", "SIGKILL ended it"), origin=ORIGIN,
        note="only SIGKILL ends it",
    ),
    RefusalCase(
        "started-around-the-kit", ("around",), "daemon",
        named=("started around the kit",), origin=ORIGIN,
        note="a well-behaved daemon the case started with run and never recorded",
    ),
)


def alive(pid: int, started: str) -> bool:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    rest = raw[raw.rindex(")") + 2:].split()
    return rest[0] not in ("Z", "X") and rest[19] == started


def started(pid: int) -> str:
    raw = Path(f"/proc/{pid}/stat").read_text()
    return raw[raw.rindex(")") + 2:].split()[19]


def kill_survivors(pids: dict) -> list[int]:
    """The recorded pids still running, each killed before it is reported."""
    left = sorted(int(pid) for pid, started in pids.items() if alive(int(pid), started))
    for pid in left:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)
    return left


@unittest.skipUnless(Path("/proc/self/stat").is_file(), "world.daemon reads /proc; this host has none")
class Daemons(unittest.TestCase):
    def close(self, world, row):
        """Close a world holding the row's daemon; returns (result, reading),
        after proving the reading names a daemon and nothing of it survived."""
        reading = world.root / f"{row.id}.json"
        result = run([support.PYTHON, "-m", CLOSER, str(reading), TIMEOUT, *row.argv],
                     cwd=support.REPO, env=world.env(), timeout=120)
        self.assertTrue(reading.is_file(), f"{row.id}: no reading; exit {result.exit}: {result.stderr[-400:]}")
        record = json.loads(reading.read_text(encoding="utf-8"))
        self.assertTrue(record["pids"], f"{row.id}: the reading names no daemon: {record}")
        self.assertEqual(kill_survivors(record["pids"]), [], f"{row.id}: alive after close")
        return result, record

    def test_a_recorded_or_adopted_daemon_whose_stop_works_leaves_nothing_at_close(self):
        with World(host_path=False) as world:
            for row in CLEAN:
                with self.subTest(id=row.id):
                    result, record = self.close(world, row)
                    self.assertEqual((result.exit, result.stdout, result.stderr), (0, "", ""), row.id)
                    self.assertFalse(Path(record["root"]).exists(), f"{row.id}: the world's root survived close")

    def test_a_daemon_its_stop_does_not_end_or_the_kit_never_recorded_is_refused(self):
        with World(host_path=False) as world:
            run_table(self, REFUSED, lambda row: self.close(world, row)[0])


@unittest.skipUnless(Path("/proc/self/stat").is_file(), "world.daemon reads /proc; this host has none")
class Foreign(unittest.TestCase):
    """A process the world did not start is never the world's to record or
    to kill, and a stop that blocks does not hold close past its timeout.
    The foreign daemon runs in a second world, the host, with a token in its
    socket path that the world under test also names."""

    def setUp(self):
        host = World(host_path=False)
        self.addCleanup(host.close)
        self.token = "a528" + uuid.uuid4().hex[:8]
        self.foreign_sock = host.home / f"{self.token}.sock"
        result = run([support.PYTHON, str(DAEMON), "start", str(self.foreign_sock)],
                     cwd=host.cwd, env=host.env(), timeout=30, expect=0)
        self.foreign = int(result.stdout.split()[1])
        self.foreign_started = started(self.foreign)
        self.addCleanup(kill_survivors, {self.foreign: self.foreign_started})
        self.addCleanup(run, [support.PYTHON, str(DAEMON), "stop", str(self.foreign_sock)],
                        cwd=host.cwd, env=host.env(), timeout=30)

    def close(self, world):
        """Close ``world``; its refusal lines, or () when it closed clean."""
        try:
            world.close()
        except DaemonRefused as refused:
            return tuple(refused.lines)
        return ()

    def test_a_process_outside_the_world_sharing_a_match_token_is_neither_recorded_nor_killed(self):
        # origin: A528 finding 1, world.py:737-761 at 86ed5089: start_daemon unions
        # the new pids with every host process matching the tokens.
        world = World(host_path=False)
        sock = world.home / f"{self.token}.sock"
        try:
            daemon = world.daemon("fixture", [support.PYTHON, str(DAEMON), "start", str(sock)],
                                  [support.PYTHON, str(DAEMON), "stop", str(sock)],
                                  match=(self.token,), socket=sock, timeout=5)
            recorded = dict(daemon.pids)
        finally:
            lines = self.close(world)
        self.assertNotIn(self.foreign, recorded, "the world recorded a process it did not start")
        self.assertEqual(lines, (), "the world's own well-behaved daemon was refused")
        self.assertTrue(alive(self.foreign, self.foreign_started), "close killed a process outside the world")
        self.assertEqual(kill_survivors({pid: s for pid, s in recorded.items() if pid != self.foreign}), [])

    def test_adopt_never_takes_a_process_outside_the_world(self):
        # origin: A528 finding 2, world.py:764-794 at 86ed5089: adopt_daemon takes any
        # host process whose command line carries the tokens.
        world = World(host_path=False)
        adopted = {}
        try:
            with contextlib.suppress(PlantFailed):
                adopted = dict(world.adopt("foreign", None, match=(self.token,), timeout=0.5).pids)
        finally:
            self.close(world)
        self.assertNotIn(self.foreign, adopted, "adopt took a process the world did not start")
        self.assertTrue(alive(self.foreign, self.foreign_started), "close killed a process outside the world")

    def test_a_stop_that_blocks_does_not_hold_close_past_the_timeout(self):
        # origin: A528 finding 5, world.py:804-815 at 86ed5089: a callable stop runs
        # with no bound, so one that blocks holds close for as long as it blocks.
        world = World(host_path=False)
        released = threading.Event()
        self.addCleanup(released.set)
        sock = world.home / "slow.sock"
        try:
            daemon = world.daemon("slow", [support.PYTHON, str(DAEMON), "start", str(sock)],
                                  lambda: released.wait(20), socket=sock, timeout=0.5)
            recorded = dict(daemon.pids)
        except BaseException:
            self.close(world)
            raise
        began = time.monotonic()
        lines = self.close(world)
        elapsed = time.monotonic() - began
        released.set()
        self.assertLess(elapsed, 10, f"close took {elapsed:.1f}s on a 0.5s timeout; the stop blocks for 20s")
        self.assertEqual(kill_survivors(recorded), [], "the daemon survived close")
        self.assertTrue(lines, "a daemon its own stop never brought down was not refused")
        self.assertIn("slow", lines[0])


if __name__ == "__main__":
    unittest.main()
