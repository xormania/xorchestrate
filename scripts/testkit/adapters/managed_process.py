"""Managed worker terminals using the existing tmux ownership primitives."""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from .tmux import Identity, OwnedProcesses, alive, processes, send_signal


def identity_record(identity):
    return {"pid": identity.pid, "started": identity.started} if identity else None


def observed_alive(record):
    return bool(record and alive(Identity(record["pid"], record["started"])))


class ManagedWorker:
    """One private server, one check worker and an intentionally retained dashboard.

    Native processes still belong to their existing check adapters. Descendant
    observation and identity-fenced signaling reuse the acceptance implementation.
    """

    def __init__(self, directory, *, root, interpreter, grace=10):
        self.directory, self.root = Path(directory), Path(root)
        self.interpreter, self.grace = interpreter, grace
        self.binary = shutil.which("tmux")
        if not self.binary:
            raise FileNotFoundError("Managed start requires tmux; foreground run remains available")
        self.tracker = OwnedProcesses()
        self.socket_dir = tempfile.TemporaryDirectory(prefix="xp-", dir="/tmp")
        self.socket = Path(self.socket_dir.name) / "tmux.sock"
        self.server = self.server_identity = self.worker = self.dashboard = None
        self.log = (self.directory / "terminal.log").open("ab")

    def control(self, *argv, check=True):
        if self.server_identity and not alive(self.server_identity):
            raise RuntimeError("Owned terminal server is no longer alive")
        result = subprocess.run([self.binary, "-N", "-S", str(self.socket), "-f", os.devnull, *argv],
                                cwd=self.root, capture_output=True, text=True, timeout=3)
        self.tracker.observe()
        if check and result.returncode:
            raise RuntimeError("Owned tmux command failed: " + result.stderr.strip())
        return result

    def start(self):
        env = dict(os.environ)
        env.pop("TMUX", None)
        env.pop("TMUX_PANE", None)
        env.update(XORCHESTRATE_PROOF_DIRECTORY=str(self.directory), PYTHONUNBUFFERED="1")
        self.server = subprocess.Popen([self.binary, "-D", "-S", str(self.socket), "-f", os.devnull],
                                       cwd=self.root, env=env, stdin=subprocess.DEVNULL,
                                       stdout=self.log, stderr=self.log)
        deadline = time.monotonic() + 10
        while not self.socket.exists():
            self.tracker.observe()
            if self.server.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError("Private tmux server did not start")
            time.sleep(.025)
        self.server_identity = processes().get(self.server.pid, (None,))[0]
        if self.server_identity is None:
            raise RuntimeError("Private tmux server identity is unavailable")
        self.control("set-window-option", "-g", "remain-on-exit", "on")
        args = [self.interpreter, "-m", "scripts.testkit", "_worker", str(self.directory)]
        result = self.control("new-session", "-d", "-s", "proof", "-n", "worker", "-P", "-F", "#{pane_pid}",
                              "-x", "160", "-y", "48", "-c", str(self.root), "--", *args)
        self.worker = processes().get(int(result.stdout.strip()), (None,))[0]
        result = self.control("new-window", "-d", "-t", "=proof:", "-n", "dashboard", "-P", "-F", "#{pane_pid}",
                              "--", self.interpreter, "-m", "scripts.testkit", "_dashboard", str(self.directory))
        self.dashboard = processes().get(int(result.stdout.strip()), (None,))[0]
        if self.worker is None:
            raise RuntimeError("Managed worker identity is unavailable")
        self.tracker.observe()
        return {"worker": identity_record(self.worker), "dashboard": identity_record(self.dashboard),
                "terminal": {"binary": self.binary, "socket": str(self.socket), "session": "proof",
                             "server": identity_record(self.server_identity)}}

    def poll(self):
        self.tracker.observe()
        self.tracker.reap(exclude=(self.server.pid,))
        if self.server.poll() is not None:
            raise RuntimeError("Managed terminal server exited before finalization")
        status = self.control("display-message", "-p", "-t", "=proof:worker.0",
                              "#{pane_dead}|#{pane_dead_status}|#{pane_dead_signal}").stdout.strip().split("|")
        if len(status) == 3 and status[0] == "1":
            if status[1].isdigit():
                return int(status[1])
            if status[2]:
                return -1
            send_signal(self.server_identity, signal.SIGCHLD)
        return None

    def cleanup_work(self):
        retained = {item for item in (self.server_identity, self.dashboard) if item}
        before = [item for item in self.tracker.observe() if item not in retained]
        forced = bool(before)
        for number in (signal.SIGTERM, signal.SIGKILL):
            survivors = [item for item in self.tracker.observe() if item not in retained]
            for item in survivors:
                send_signal(item, number)
            deadline = time.monotonic() + (self.grace if number == signal.SIGTERM else 2)
            while survivors and time.monotonic() < deadline:
                self.tracker.reap(exclude=(self.server.pid,))
                survivors = [item for item in self.tracker.observe() if item not in retained]
                time.sleep(.025)
            if not survivors:
                break
        after = [item for item in self.tracker.observe() if item not in retained]
        return {"verified": not after, "forced": forced, "before": [identity_record(x) for x in before],
                "remaining": [identity_record(x) for x in after],
                "retained_presentation": [identity_record(x) for x in retained]}

    def interrupt(self):
        if self.worker:
            send_signal(self.worker, signal.SIGTERM)

    def close(self):
        if self.server and self.server.poll() is None:
            if self.server_identity:
                with contextlib.suppress(OSError, RuntimeError, subprocess.TimeoutExpired):
                    self.control("kill-server", check=False)
            else:
                self.server.terminate()
            try:
                self.server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if self.server_identity:
                    send_signal(self.server_identity, signal.SIGKILL)
                else:
                    self.server.kill()
                self.server.wait(timeout=3)
        for item in self.tracker.observe():
            send_signal(item, signal.SIGKILL)
        self.tracker.reap()
        self.socket_dir.cleanup()
        self.log.close()
