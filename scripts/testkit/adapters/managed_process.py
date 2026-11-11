"""Managed worker terminals in an injected terminal host, with the test kit's own ownership tracking."""

from __future__ import annotations

import contextlib
import os
import signal
import time
from pathlib import Path

from xorchestrate.ports import TerminalCommand

from .ownership import Identity, OwnedProcesses, alive, send_signal

WORKER, DASHBOARD = "worker", "dashboard"
ROWS, COLUMNS = 48, 160


def identity_record(identity):
    return {"pid": identity.pid, "started": identity.started} if identity else None


def observed_alive(record):
    return bool(record and alive(Identity(record["pid"], record["started"])))


class ManagedWorker:
    """One terminal host with a check worker and an intentionally retained dashboard.

    The host arrives from composition (owned, or surviving when the run keeps its
    terminals); this class never names a mechanism. Native processes still
    belong to their existing check adapters. Descendant observation and
    identity-fenced signalling are the test kit's ownership primitives: whatever
    exists after the dashboard opened is presentation, everything later is work.
    """

    def __init__(self, directory, *, host, root, interpreter, grace=10, host_report=None, keep_after_run=False):
        self.directory, self.root = Path(directory), Path(root)
        self.interpreter, self.grace = interpreter, grace
        self.host, self.host_report, self.keep_after_run = host, dict(host_report or {}), keep_after_run
        self.tracker = OwnedProcesses()
        self.retained = set()
        self.receipts = {}
        self.opened = False

    def start(self):
        env = dict(os.environ)
        env.update(XORCHESTRATE_PROOF_DIRECTORY=str(self.directory), PYTHONUNBUFFERED="1")
        environment = tuple(env.items())
        dashboard = self.host.open_terminal(DASHBOARD, TerminalCommand(
            (self.interpreter, "-m", "scripts.testkit", "_dashboard", str(self.directory)), str(self.root),
            environment, ROWS, COLUMNS))
        self.opened = True
        self.retained = set(self.tracker.observe())  # the host and the dashboard: presentation, not work
        worker = self.host.open_terminal(WORKER, TerminalCommand(
            (self.interpreter, "-m", "scripts.testkit", "_worker", str(self.directory)), str(self.root),
            environment, ROWS, COLUMNS))
        self.tracker.observe()
        self.receipts = {DASHBOARD: dashboard.receipt, WORKER: worker.receipt}
        attach = None
        if self.host.interactive:
            instruction = self.host.attach_instruction(DASHBOARD)
            attach = {"argv": list(instruction.argv), "text": instruction.text}
        return {"terminal": {"host": self.host_report, "receipts": self.receipts, "attach": attach,
                             "keep_after_run": self.keep_after_run}}

    def _observe(self, name):
        found = next((item for item in self.host.terminals() if item.name == name), None)
        if found is None:
            raise RuntimeError("Managed terminal is no longer held by this host")
        return found

    def poll(self):
        self.tracker.observe()
        self.tracker.reap()
        worker = self._observe(WORKER)
        if worker.running:
            return None
        if worker.exit_status is not None:
            return worker.exit_status
        return -1  # ended by a signal, or in a way the host cannot report

    def cleanup_work(self):
        before = [item for item in self.tracker.observe() if item not in self.retained]
        forced = bool(before)
        errors = []
        if not self.keep_after_run and WORKER in {item.name for item in self.host.terminals()}:
            try:
                if not self.host.close_terminal(WORKER, grace=self.grace).cleanup_verified:
                    errors.append("terminal_cleanup_unverified")
            except (OSError, ValueError) as error:
                errors.append(getattr(error, "code", "terminal_shutdown"))
        for number in (signal.SIGTERM, signal.SIGKILL):
            survivors = [item for item in self.tracker.observe() if item not in self.retained]
            for item in survivors:
                send_signal(item, number)
            deadline = time.monotonic() + (self.grace if number == signal.SIGTERM else 2)
            while survivors and time.monotonic() < deadline:
                self.tracker.reap()
                survivors = [item for item in self.tracker.observe() if item not in self.retained]
                time.sleep(.025)
            if not survivors:
                break
        after = [item for item in self.tracker.observe() if item not in self.retained]
        result = {"verified": not after and not errors, "forced": forced, "before": [identity_record(x) for x in before],
                  "remaining": [identity_record(x) for x in after],
                  "retained_presentation": [identity_record(x) for x in self.retained]}
        if errors:
            result["errors"] = sorted(set(errors))
        if self.keep_after_run:
            # Kept terminals are not cleaned up yet: ``verified`` covers the work's processes only.
            result["retained"] = {"terminals": sorted(self.receipts), "verified": False}
        return result

    def interrupt(self):
        if self.opened:
            with contextlib.suppress(OSError, ValueError):
                self.host.interrupt(WORKER)

    def close(self, *, keep=False):
        """End the run's terminals; with ``keep`` (a surviving host) release them for a later ``close``."""
        if keep and self.host.survives_controller:
            self.host.close()
            return
        with contextlib.suppress(OSError, ValueError):
            self.host.shutdown()
        self.host.close()
        for item in self.tracker.observe():
            send_signal(item, signal.SIGKILL)
        self.tracker.reap()
