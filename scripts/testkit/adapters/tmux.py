"""Owned tmux/process lifecycle for acceptance runs on Linux and WSL2."""

from __future__ import annotations

import ctypes
import json
import math
import os
import shlex
import shutil
import signal
import subprocess
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .evidence import write_json


class AcceptanceFailure(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Identity:
    pid: int
    started: int


def processes():
    """Read identities without command lines, environment, or credential data."""
    result = {}
    for path in Path("/proc").glob("[0-9]*/stat"):
        try:
            fields = path.read_text().rsplit(") ", 1)[1].split()
            result[int(path.parent.name)] = (Identity(int(path.parent.name), int(fields[19])), int(fields[1]), fields[0])
        except (OSError, ValueError, IndexError):
            continue  # A process may disappear between listing and reading it.
    return result


def alive(identity):
    current = processes().get(identity.pid)
    return current is not None and current[0] == identity and current[2] != "Z"


def send_signal(identity, number):
    """Fence a signal against PID reuse; use a pinned pidfd where supported."""
    if not alive(identity):
        return
    descriptor = None
    try:
        if hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal"):
            try:
                descriptor = os.pidfd_open(identity.pid)
            except OSError:
                descriptor = None
        if alive(identity):
            if descriptor is not None:
                signal.pidfd_send_signal(descriptor, number)
            else:
                os.kill(identity.pid, number)
    except ProcessLookupError:
        pass
    finally:
        if descriptor is not None:
            os.close(descriptor)


@contextmanager
def subreaper():
    """Adopt orphaned grandchildren so even a fast double fork remains owned."""
    library = ctypes.CDLL(None, use_errno=True)
    previous = ctypes.c_int()
    if library.prctl(37, ctypes.byref(previous), 0, 0, 0) or library.prctl(36, 1, 0, 0, 0):
        raise AcceptanceFailure("process_adoption_unavailable")
    try:
        yield
    finally:
        library.prctl(36, previous.value, 0, 0, 0)


class OwnedProcesses:
    """Track this single-threaded supervisor's new children and descendants.

    Preexisting children and their observed descendants are excluded. The runner
    launches only its tmux server and tmux clients after this snapshot. Subreaping
    keeps a child that outlives its immediate parent inside this ownership scope.
    This standalone supervisor must not launch unrelated children concurrently:
    snapshots cannot attribute an unseen descendant orphaned between observations.
    """

    def __init__(self):
        self.parent = os.getpid()
        self.baseline = set()
        self.known = set()
        self._exclude_descendants(processes(), {self.parent})

    def _exclude_descendants(self, snapshot, roots):
        # Keep identities after reparenting: subreaping does not confer ownership
        # of an unrelated process whose original parent has exited.
        changed = True
        while changed:
            changed = False
            for identity, parent, _ in snapshot.values():
                if parent in roots and identity.pid not in roots:
                    self.baseline.add(identity)
                    roots.add(identity.pid)
                    changed = True

    def observe(self):
        snapshot = processes()
        excluded = {identity.pid for identity in self.baseline if snapshot.get(identity.pid, (None,))[0] == identity}
        self._exclude_descendants(snapshot, excluded)
        roots = {self.parent}
        roots.update(identity.pid for identity in self.known if snapshot.get(identity.pid, (None,))[0] == identity)
        changed = True
        while changed:
            changed = False
            for identity, parent, _ in snapshot.values():
                if parent in roots and identity not in self.baseline and identity.pid not in roots:
                    self.known.add(identity)
                    roots.add(identity.pid)
                    changed = True
        return [identity for identity in self.known if snapshot.get(identity.pid, (None,))[0] == identity and snapshot[identity.pid][2] != "Z"]

    def reap(self, exclude=()):
        snapshot = processes()
        for identity in self.known:
            if identity.pid not in exclude and snapshot.get(identity.pid, (None,))[0] == identity:
                try:
                    os.waitpid(identity.pid, os.WNOHANG)
                except ChildProcessError:
                    pass


def environment(profile, run_dir):
    if profile == "native":
        result = dict(os.environ)
    else:
        names = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TERM",
                 "PYTHONPYCACHEPREFIX", "PYTHONDONTWRITEBYTECODE")
        result = {name: os.environ[name] for name in names if name in os.environ}
        for name, folder in (("HOME", "home"), ("XDG_CONFIG_HOME", "config"), ("XDG_CACHE_HOME", "cache"), ("XDG_DATA_HOME", "data")):
            path = run_dir / "environment" / folder
            path.mkdir(parents=True, mode=0o700)
            result[name] = str(path)
        result["PYTHONNOUSERSITE"] = "1"
    result.pop("TMUX", None)
    result.pop("TMUX_PANE", None)
    result["PYTHONUNBUFFERED"] = "1"
    result.setdefault("TERM", "xterm-256color")
    return result


def supervise(command, run_dir, *, profile="fixture", timeout=120, cwd=None, tmux=None, grace=10,
              cleanup_budget=None):
    """Run one worker, retaining private diagnostics and an exportable receipt."""
    if cleanup_budget is not None and (isinstance(cleanup_budget, bool)
            or not isinstance(cleanup_budget, (int, float)) or not math.isfinite(cleanup_budget)
            or cleanup_budget <= 0):
        raise ValueError("cleanup budget must be a positive finite number")
    run_dir = Path(run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    started = time.monotonic()
    report = {"version": 1, "profile": profile, "status": "failed", "worker_exit": None,
              "scenario_status": "missing", "cleanup": {"verified": False}, "failure": None,
              "tmux_status_refreshes": 0}
    interrupted = []
    handlers = {}
    process = None
    worker = None
    socket_dir = None
    tracker = None
    server_log = None
    socket = None
    binary = tmux or shutil.which("tmux")
    cleanup_deadline = None

    def remaining(bound):
        if cleanup_deadline is None:
            return bound
        available = cleanup_deadline - time.monotonic()
        if available <= 0:
            raise subprocess.TimeoutExpired("owned cleanup", cleanup_budget)
        return min(bound, available)

    def interrupt(number, frame):
        interrupted.append(number)

    def control(*arguments, check=True):
        response = subprocess.run([binary, "-N", "-S", str(socket), "-f", os.devnull, *arguments],
                                  cwd=cwd, env=env, capture_output=True, text=True, timeout=remaining(3))
        if check and response.returncode:
            raise AcceptanceFailure("tmux_control_failed")
        return response

    def observe_wait(duration):
        deadline = time.monotonic() + duration
        if cleanup_deadline is not None:
            deadline = min(deadline, cleanup_deadline)
        while time.monotonic() < deadline:
            tracker.observe()
            tracker.reap(exclude=(process.pid,))
            time.sleep(min(0.02, max(0, deadline - time.monotonic())))

    def finish():
        nonlocal cleanup_deadline
        cleanup_deadline = time.monotonic() + cleanup_budget if cleanup_budget is not None else None
        errors = []
        forced = False
        if tracker:
            tracker.observe()
        if worker and alive(worker):
            try:
                send_signal(worker, signal.SIGTERM)
                deadline = time.monotonic() + grace
                if cleanup_deadline is not None:
                    deadline = min(deadline, cleanup_deadline)
                while alive(worker) and time.monotonic() < deadline:
                    observe_wait(0.05)
            except OSError:
                errors.append("worker_shutdown")
        if socket and process and process.poll() is None:
            try:
                capture = control("capture-pane", "-p", "-t", "acceptance:0.0", "-S", "-2000", check=False)
                (run_dir / "pane.txt").write_text(capture.stdout, encoding="utf-8")
                control("kill-server", check=False)
                process.wait(timeout=remaining(3))
            except (OSError, subprocess.TimeoutExpired):
                errors.append("tmux_shutdown")
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
        if process:
            try:
                process.wait(timeout=remaining(3))
            except subprocess.TimeoutExpired:
                errors.append("server_reap")
        if tracker:
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
        if not binary:
            report["failure"] = "tmux_missing"
            return report
        if not Path("/proc/self/stat").exists():
            report["failure"] = "process_identity_unavailable"
            return report
        env = environment(profile, run_dir)
        for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            handlers[number] = signal.signal(number, interrupt)
        with subreaper():
            tracker = OwnedProcesses()
            try:
                socket_dir = tempfile.TemporaryDirectory(prefix="xa-", dir="/tmp")
                socket = Path(socket_dir.name) / "tmux.sock"
                server_log = (run_dir / "tmux.log").open("w", encoding="utf-8")
                process = subprocess.Popen([binary, "-D", "-S", str(socket), "-f", os.devnull],
                                           cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=server_log, stderr=server_log)
                deadline = started + timeout
                while not socket.exists():
                    tracker.observe()
                    if interrupted or process.poll() is not None or time.monotonic() >= deadline:
                        raise AcceptanceFailure("interrupted" if interrupted else "tmux_start_failed")
                    time.sleep(0.02)
                server_identity = processes().get(process.pid, (None,))[0]
                if server_identity is None:
                    raise AcceptanceFailure("tmux_start_failed")
                control("set-window-option", "-g", "remain-on-exit", "on")
                result = control("new-session", "-d", "-s", "acceptance", "-P", "-F", "#{pane_pid}",
                                 "-x", "160", "-y", "48", "-c", str(cwd or Path.cwd()), "--", *command)
                worker_pid = int(result.stdout.strip())
                snapshot = processes()
                worker = snapshot.get(worker_pid, (None,))[0]
                tracker.observe()
                attach = shlex.join([binary, "-S", str(socket), "attach-session", "-t", "acceptance"])
                write_json(run_dir / "session.json", {"attach": attach, "worker_pid": worker_pid})
                boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
                owner = processes()[os.getpid()][0]
                write_json(run_dir / "terminal.json", {
                    "schema_version": 1, "kind": "tmux", "purpose": "acceptance worker",
                    "owner": {"pid": owner.pid, "started": owner.started, "boot_id": boot_id, "uid": os.getuid()},
                    "server": {"pid": server_identity.pid, "started": server_identity.started,
                               "boot_id": boot_id, "uid": os.getuid()},
                    "socket": str(socket), "session": "acceptance", "pane": "acceptance:0.0",
                })
                print(f"Attach while running: {attach}", flush=True)
                while True:
                    tracker.observe()
                    if interrupted:
                        report["failure"] = "interrupted"
                        break
                    if time.monotonic() >= deadline:
                        report["failure"] = "timeout"
                        break
                    status = control("display-message", "-p", "-t", "acceptance:0.0",
                                     "#{pane_dead}|#{pane_dead_status}|#{pane_dead_signal}").stdout.strip().split("|")
                    # pane_dead means the PTY closed. Its child may still be
                    # running, or tmux may not have collected SIGCHLD yet.
                    # Only a populated exit status/signal establishes an exit.
                    if len(status) == 3 and status[0] == "1":
                        if status[1].isdigit():
                            report["worker_exit"] = int(status[1])
                            break
                        if status[2]:
                            # tmux uses a signal name where sys_signame exists,
                            # and a decimal signal number on Linux builds.
                            number = (int(status[2]) if status[2].isdigit() else
                                      getattr(signal, "SIG" + status[2].upper().removeprefix("SIG"), None))
                            report["worker_exit"] = -int(number) if isinstance(number, int) and number > 0 else None
                            report["failure"] = "worker_signaled"
                            break
                        # tmux 3.4 with libutempter can lose the SIGCHLD
                        # notification in foreground (-D) mode (tmux#4559).
                        # Ask only our identity-fenced server to reap again;
                        # its actual wait status is still required to finish.
                        send_signal(server_identity, signal.SIGCHLD)
                        report["tmux_status_refreshes"] += 1
                    tracker.reap(exclude=(process.pid,))
                    time.sleep(0.05)
            finally:
                finish()
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        # Error text can contain native configuration; receipt exposes type only.
        report["failure"] = report["failure"] or getattr(error, "code", type(error).__name__)
    finally:
        if server_log:
            server_log.close()
        if socket_dir:
            socket_dir.cleanup()
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
