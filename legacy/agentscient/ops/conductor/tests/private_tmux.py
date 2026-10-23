"""A private tmux server a case's subject leaves, recorded with the kit.

The rows that start a member leave a tmux server on the world's private
socket, and the harness holding in its panes. A cleanup of the case's own
that ran kill-server when the socket file existed left both alive when it
did not (the kit's measure at a93368b5), and the host gathered 1,212 such
servers (the XO, 2026-09-23). ``RecordedTmux`` records the server with
``world.adopt(socket=)`` when a subject left it, or starts it through
``world.daemon`` when the case starts it, so the world's close stops it,
proves it gone and refuses class ``daemon`` for a survivor (docs/tests.md,
Daemons). ``carrying_home`` is what the kit's sweep reads as a process of
the world, for the rows that prove it.
"""
from __future__ import annotations

import contextlib
import os
import shutil
import signal
from pathlib import Path

from ops.testkit import run

HOST_TMUX = shutil.which("tmux")
# A recorded server's stop, and each of the kit's escalations after it.
SERVER_STOP = 10.0
ORIGIN_DAEMON = "docs/tests.md Daemons; the kit's measure at a93368b5"


def carrying_home(home: str) -> list[int]:
    """The live processes whose environment carries ``HOME=<home>``."""
    wanted = b"HOME=" + os.fsencode(home)
    found = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            environ = (entry / "environ").read_bytes()
        except OSError:
            continue
        if wanted in environ.split(b"\0"):
            found.append(int(entry.name))
    return sorted(found)


class RecordedTmux:
    """A TestCase mixin over ``self.world`` and ``self.socket``."""

    _server = None
    _servers = 0

    def server_running(self) -> bool:
        """Whether a tmux server answers on this world's socket."""
        if not HOST_TMUX or not self.socket.exists():
            return False
        probe = run(
            [HOST_TMUX, "-S", str(self.socket), "list-sessions"],
            cwd=self.world.cwd,
            env=self.world.env(),
            timeout=SERVER_STOP,
        )
        return probe.exit == 0

    def own_server(self) -> None:
        """Record the server a subject call left on this world's socket."""
        if self._server is not None and self._server.alive():
            return
        if not self.server_running():
            return
        box: dict = {}
        self._servers += 1
        box["daemon"] = self._server = self.world.adopt(
            f"tmux-{self._servers}", lambda: self.stop_recorded(box["daemon"]),
            socket=self.socket, timeout=SERVER_STOP,
        )

    def start_server(self, *argv: str) -> None:
        """A tmux session the case starts itself: through ``world.daemon``
        when no recorded server is up, since a daemon started around the kit
        is a finding; on the recorded server otherwise."""
        if self._server is not None and self._server.alive():
            done = run(
                [HOST_TMUX, "-S", str(self.socket), *argv],
                cwd=self.world.cwd,
                env=self.world.env(),
                timeout=SERVER_STOP,
            )
            self.assertEqual(done.exit, 0, done.stderr)
            return
        box: dict = {}
        self._servers += 1
        box["daemon"] = self._server = self.world.daemon(
            f"tmux-{self._servers}", [HOST_TMUX, "-S", str(self.socket), *argv],
            lambda: self.stop_recorded(box["daemon"]),
            socket=self.socket, timeout=SERVER_STOP,
        )

    def stop_recorded(self, daemon) -> None:
        """A recorded server's stop: tmux's own kill-server on the socket,
        then SIGTERM to any of its recorded processes still up, which is
        every one of them when the socket file is gone."""
        if HOST_TMUX and self.socket.exists():
            with contextlib.suppress(Exception):
                run(
                    [HOST_TMUX, "-S", str(self.socket), "kill-server"],
                    cwd=self.world.cwd,
                    env=self.world.env(),
                    timeout=SERVER_STOP,
                )
        for pid in daemon.alive():
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(pid, signal.SIGTERM)
