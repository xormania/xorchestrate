"""One quiet GitHub observer per team, independent of native agent sessions."""

import fcntl
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import processes
from .github import GitHub
from .profiles import write_json
from .store import Store

ENTRY = Path(__file__).with_name("run.py")


def status(store):
    try:
        saved = json.loads((store.directory / "watcher.json").read_text())
    except FileNotFoundError:
        saved = None
    running = bool(saved and processes.identity(saved["pid"]) == saved)
    with store.connect() as db:
        active = db.execute("SELECT count(*) FROM github_watches WHERE state='active'").fetchone()[0]
    return {"running": running, "pid": saved["pid"] if running else None, "active_watches": active}


def start(store):
    processes.boot_id()
    # Serialize launch registration as well as the daemon itself.
    with (store.directory / "watcher-start.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if (current := status(store))["running"]:
            return current
        with (store.directory / "watcher.log").open("ab") as output:
            process = subprocess.Popen(
                [sys.executable, str(ENTRY), "watch", "--state", str(store.directory), "--action", "run", "--detach"],
                stdin=subprocess.DEVNULL, stdout=output, stderr=output, start_new_session=True,
            )
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)
            raise RuntimeError("GitHub watcher could not detach; inspect watcher.log.") from None
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if (current := status(store))["running"]:
                return current
            time.sleep(0.02)
        raise RuntimeError("GitHub watcher did not start; inspect watcher.log.")


def stop(store):
    with (store.directory / "watcher-start.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if status(store)["running"]:
            saved = json.loads((store.directory / "watcher.json").read_text())
            processes.signal_owned([saved], signal.SIGTERM)
            deadline = time.monotonic() + 5
            while processes.alive([saved]) and time.monotonic() < deadline:
                time.sleep(0.02)
            if processes.alive([saved]):
                processes.signal_owned([saved], signal.SIGKILL)
        return status(store)


def resume(store):
    """Called on a new MCP connection; idle teams need no observer process."""
    current = status(store)
    return start(store) if current["active_watches"] and not current["running"] else current


def run(directory, detach=False):
    # Fork only in this fresh CLI process. Reparent before agent process-family
    # cleanup can capture the observer as part of the session that requested it.
    if detach:
        if os.fork():
            return
        os.setsid()
    store = Store(directory)
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    with (store.directory / "watcher.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        temporary = store.directory / "watcher.tmp"
        identity = processes.identity(os.getpid())
        if identity is None:
            raise RuntimeError("GitHub watcher requires /proc in its PID namespace.")
        write_json(temporary, identity)
        temporary.replace(store.directory / "watcher.json")
        with store.connect() as db:
            # The singleton lock proves no preceding daemon still owns these leases.
            db.execute("UPDATE github_watches SET next_poll=0,poll_pid=NULL WHERE state='active' AND poll_pid IS NOT NULL")
        while not stopped.is_set() and store.path.is_file():
            with store.connect() as db:
                members = [r[0] for r in db.execute("SELECT DISTINCT member FROM github_watches WHERE state='active'")]
            for member in members:
                if stopped.is_set():
                    break
                provider = GitHub(store, member)
                try:
                    provider.tick()
                except Exception as error:
                    store.event(member, "github_watch_error", error=provider.http.clean(str(error))[:1000])
            stopped.wait(1)
