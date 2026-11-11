"""Run one acceptance worker in a terminal of an owned terminal host, keeping a private receipt.

The host arrives from composition through the product's terminal host port; this
module never names a mechanism. Ownership stays authoritative: the run snapshots
its descendants under a subreaper, so anything the host or the worker leaves
behind is found and reported as a leak.
"""

from __future__ import annotations

import json
import math
import os
import signal
import time
from pathlib import Path

from xorchestrate.ports import TerminalCommand

from .evidence import write_json
from .ownership import OwnedProcesses, environment, processes, send_signal, subreaper

TERMINAL = "acceptance"
ROWS, COLUMNS = 48, 160
CAPTURE_LINES = 2000
WORKER_CONTEXT = "XORCHESTRATE_TERMINAL_HOST"
"""Set in the worker's environment to the host's name: the scenario's proof that it runs in the supervised host."""


def _log_tail(path, lines=CAPTURE_LINES, limit=4 * 1024 * 1024):
    """The last ``lines`` lines of a terminal log, read within a bounded size."""
    try:
        with open(path, "rb") as source:
            size = source.seek(0, os.SEEK_END)
            source.seek(max(0, size - limit))
            data = source.read(limit)
    except OSError:
        return ""
    return "\n".join(data.decode("utf-8", "replace").splitlines()[-lines:]) + "\n"


def _boot_id():
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def supervise(command, run_dir, *, host, host_report=None, profile="fixture", timeout=120, cwd=None, grace=10,
              cleanup_budget=None, selection=None, placement=None):
    """Run one worker in ``host`` (or report why none is ready), retaining diagnostics and an exportable receipt.

    ``host`` is a terminal host in owned mode, or ``None`` when no host is
    ready; ``host_report`` describes the selection (adapter, fallback, reason).
    ``selection`` and ``placement`` are recorded so an operator can attach:
    attach rebuilds the host from them and asks it, nothing recorded is executed.
    """
    if cleanup_budget is not None and (isinstance(cleanup_budget, bool)
            or not isinstance(cleanup_budget, (int, float)) or not math.isfinite(cleanup_budget)
            or cleanup_budget <= 0):
        raise ValueError("cleanup budget must be a positive finite number")
    run_dir = Path(run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    started = time.monotonic()
    report = {"version": 1, "profile": profile, "status": "failed", "worker_exit": None,
              "scenario_status": "missing", "cleanup": {"verified": False}, "failure": None,
              "terminal_host": dict(host_report or {})}
    interrupted = []
    handlers = {}
    tracker = None
    opened = False
    cleanup_deadline = None

    def remaining(bound):
        if cleanup_deadline is None:
            return bound
        return max(0.0, min(bound, cleanup_deadline - time.monotonic()))

    def interrupt(number, frame):
        interrupted.append(number)

    def observe_wait(duration):
        deadline = time.monotonic() + remaining(duration)
        while time.monotonic() < deadline:
            tracker.observe()
            tracker.reap()
            time.sleep(min(0.02, max(0, deadline - time.monotonic())))

    def observation():
        return next((item for item in host.terminals() if item.name == TERMINAL), None)

    def finish():
        nonlocal cleanup_deadline
        cleanup_deadline = time.monotonic() + cleanup_budget if cleanup_budget is not None else None
        errors = []
        forced = False
        if tracker:
            tracker.observe()
        if opened:
            try:
                current = observation()
                if current is not None and current.running:
                    # Timeout or interruption: close first. The host asks the worker alone to stop and gives it the
                    # grace (its children stay up for it to stop in order); what it prints meanwhile goes to the
                    # terminal's log, whose tail becomes the capture.
                    closure = host.close_terminal(TERMINAL, grace=max(0.1, remaining(grace)))
                    if not closure.cleanup_verified:
                        errors.append("terminal_cleanup_unverified")
                    (run_dir / "pane.txt").write_text(_log_tail(run_dir / "terminal.log"), encoding="utf-8")
                else:
                    (run_dir / "pane.txt").write_text(host.recent_output(TERMINAL, CAPTURE_LINES), encoding="utf-8")
                    closure = host.close_terminal(TERMINAL, grace=max(0.1, remaining(grace)))
                    if not closure.cleanup_verified:
                        errors.append("terminal_cleanup_unverified")
            except (OSError, ValueError) as error:
                errors.append(getattr(error, "code", "terminal_shutdown"))
        if host is not None:
            try:
                host.shutdown()
            except (OSError, ValueError) as error:
                errors.append(getattr(error, "code", "terminal_host_shutdown"))
        if tracker:
            for number in (signal.SIGTERM, signal.SIGKILL):
                survivors = tracker.observe()
                if not survivors:
                    break
                forced = True
                for identity in survivors:
                    try:
                        send_signal(identity, number)
                    except OSError:
                        errors.append("process_signal")
                observe_wait(min(grace, 1))
            tracker.reap()
            survivors = tracker.observe()
        else:
            survivors = []
        if cleanup_deadline is not None and time.monotonic() >= cleanup_deadline:
            errors.append("cleanup_deadline")
        report["cleanup"] = {"verified": not errors and not survivors,
                             "forced": forced, "remaining_processes": len(survivors),
                             "observed_processes": len(tracker.known) if tracker else 0, "errors": sorted(set(errors)),
                             "budget_seconds": cleanup_budget, "scenario_grace_seconds": grace}

    try:
        if host is None:
            report["failure"] = "terminal_host_unavailable"
            return report
        if not Path("/proc/self/stat").exists():
            report["failure"] = "process_identity_unavailable"
            return report
        env = environment(profile, run_dir)
        env[WORKER_CONTEXT] = host.name
        for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            handlers[number] = signal.signal(number, interrupt)
        with subreaper():
            tracker = OwnedProcesses()
            try:
                deadline = started + timeout
                terminal = host.open_terminal(TERMINAL, TerminalCommand(
                    tuple(command), str(cwd or Path.cwd()), tuple(env.items()), ROWS, COLUMNS),
                    log=str(run_dir / "terminal.log"))  # everything the worker prints, beyond the capture
                opened = True
                tracker.observe()
                owner = processes()[os.getpid()][0]
                write_json(run_dir / "terminal.json", {
                    "schema_version": 2, "kind": "terminal_host", "purpose": "acceptance worker",
                    "owner": {"pid": owner.pid, "started": owner.started, "boot_id": _boot_id(), "uid": os.getuid()},
                    "terminal": TERMINAL, "receipt": terminal.receipt, "interactive": host.interactive,
                    "host": {"selection": selection, "placement": {key: str(value) for key, value in
                                                                   (placement or {}).items()}},
                })
                if host.interactive:
                    print(f"Attach while running: {host.attach_instruction(TERMINAL).text}", flush=True)
                while True:
                    tracker.observe()
                    if interrupted:
                        report["failure"] = "interrupted"
                        break
                    if time.monotonic() >= deadline:
                        report["failure"] = "timeout"
                        break
                    current = observation()
                    if current is not None and not current.running:
                        if current.exit_status is not None:
                            report["worker_exit"] = current.exit_status
                        else:
                            report["worker_exit"] = -current.exit_signal if current.exit_signal else None
                            report["failure"] = "worker_signaled"
                        break
                    tracker.reap()
                    time.sleep(0.05)
            finally:
                finish()
    except (OSError, ValueError, RuntimeError) as error:
        # Error text can contain native configuration; receipt exposes the code or type only.
        report["failure"] = report["failure"] or getattr(error, "code", type(error).__name__)
    finally:
        for number, handler in handlers.items():
            signal.signal(number, handler)
        try:
            path = run_dir / "scenario.json"
            with path.open(encoding="utf-8") as source:
                raw = source.read(1_048_577)
            if len(raw) > 1_048_576:
                raise ValueError("Receipt too large")
            scenario = json.loads(raw)
            report["scenario_status"] = scenario.get("status") if isinstance(scenario, dict) else "invalid"
        except FileNotFoundError:
            pass
        except (ValueError, OSError):
            report["scenario_status"] = "invalid"
        if report["failure"] is None and report["cleanup"].get("forced"):
            report["failure"] = "owned_process_leak"
        if report["failure"] is None and report["worker_exit"] == 0 and report["scenario_status"] == "passed" and report["cleanup"]["verified"]:
            report["status"] = "passed"
        elif report["failure"] is None:
            report["failure"] = ("worker_failed" if report["worker_exit"] != 0 else
                                 "scenario_failed" if report["scenario_status"] != "passed" else "cleanup_failed")
        report["duration_seconds"] = round(time.monotonic() - started, 3)
        write_json(run_dir / "report.json", report)
    return report
