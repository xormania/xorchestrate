"""Hosted-session subjects: a real bridge host per session, the deterministic protocol peer, a real terminal host.

The receipt layout and the terminal mechanism are known here only; the contract
speaks of conversations, terminals and private state.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from tests.hexagonal.support.host_processes import processes_under, track
from xorchestrate.adapters.hosted import HostedSessionDriver
from xorchestrate.composition import factory_for
from xorchestrate.ports import TerminalClosure

ROOT = Path(__file__).resolve().parents[3]
PEER = ROOT / "tests/hexagonal/fixtures/session_server.py"
# The codex-protocol mode of the deterministic peer resumes a thread by id, so continuity is observable.
INNER = {"adapter": "codex", "options": {"command": [sys.executable, str(PEER), "codex"]}}
FAILING = {"adapter": "codex", "options": {"command": [
    sys.executable, "-c", "import sys; sys.stderr.write('hosted-start-marker\\n'); sys.exit(3)"]}}

CONTROLLER = """
import json, sys, time
from xorchestrate.adapters.hosted import HostedSessionDriver
from xorchestrate.composition import factory_for
spec = json.loads(sys.argv[1])
host = factory_for("terminal_host", spec["adapter"])(spec["placement"])
driver = HostedSessionDriver(spec["options"])
driver.bind_terminal_host(host, endpoints=spec["endpoints"], entry="agent")
print(json.dumps(driver.start(spec["specification"])), flush=True)
time.sleep(600)
"""


class UnverifiedHost:
    """A host whose closes report cleanup it could not verify (everything else is the real host)."""

    def __init__(self, host):
        self._host = host

    def __getattr__(self, name):
        return getattr(self._host, name)

    def close_terminal(self, name, grace=None):
        closure = self._host.close_terminal(name, grace)
        return TerminalClosure(closure.terminal, False)


@dataclass
class HostedSubject:
    adapter: str
    base: Path

    @property
    def placement(self):
        return {"name": "project", "location": str(self.base / "host"), "socket": str(self.base / "h.sock")}

    @property
    def sessions(self):
        return self.base / "sessions"

    def host(self):
        return factory_for("terminal_host", self.adapter)(self.placement)

    def options(self, inner=INNER, **extra):
        return {"session": inner, "cwd": str(ROOT), "directory": str(self.sessions), "attach_timeout": 20, **extra}

    def _bound(self, host, inner=INNER):
        driver = HostedSessionDriver(self.options(inner))
        driver.bind_terminal_host(host, endpoints=str(self.base), entry="agent")
        return driver

    def driver(self):
        return self._bound(self.host())

    def unbound(self):
        return HostedSessionDriver(self.options())

    def unverified(self):
        return self._bound(UnverifiedHost(self.host()))

    def failing(self):
        return self._bound(self.host(), FAILING)

    @staticmethod
    def spec(key):
        return {"context": {"session_id": key}, "goal": f"hosted contract {key}"}

    @staticmethod
    def native_id(session):
        return session["native_id"]

    def terminal_running(self, session):
        name = session["hosted"]["terminal"]["name"]
        host = self.host()
        try:
            return any(item.name == name and item.running for item in host.inventory())
        finally:
            host.close()

    def state_kept(self, session):
        return self.sessions.is_dir() and any(path.is_file() for path in self.sessions.rglob("*state*"))

    def lose(self, session, *, everything=True):
        """Kill the session outright, cleaning nothing: with ``everything`` (a container failure) every process of
        this subject, the terminal host included; otherwise only the bridge host, by the identity in its receipt."""
        from xorchestrate.adapters.terminal_support import alive, identity

        if everything:
            victims = [pid for pid, _ in processes_under([self.sessions])]
        else:
            recorded = session["hosted"]["terminal"]["process"]
            leader = identity(recorded["pid"])
            victims = [leader.pid] if leader is not None and leader.started == recorded["started"] else []
        assert victims, "nothing of the session was running"
        for pid in victims:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + 10
        if everything:
            while processes_under([self.sessions]) and time.monotonic() < deadline:
                time.sleep(0.05)
            assert processes_under([self.sessions]) == [], "the simulated loss left processes"
        else:
            while alive(leader) and time.monotonic() < deadline:
                time.sleep(0.05)
            assert not alive(leader), "the bridge host survived"

    def crash(self, key):
        spec = {"adapter": self.adapter, "placement": self.placement, "options": self.options(),
                "endpoints": str(self.base), "specification": self.spec(key)}
        controller = subprocess.Popen([sys.executable, "-c", CONTROLLER, json.dumps(spec)], cwd=ROOT,
                                      stdout=subprocess.PIPE, text=True)
        try:
            line = controller.stdout.readline()
        finally:
            controller.send_signal(signal.SIGKILL)
            controller.wait(timeout=10)
            controller.stdout.close()
        assert line, "the controller process did not report a receipt"
        return json.loads(line)

    @staticmethod
    def _strings(value, key=""):
        if isinstance(value, dict):
            for name, item in value.items():
                yield from HostedSubject._strings(item, name)
        elif isinstance(value, list):
            for item in value:
                yield from HostedSubject._strings(item, key)
        elif isinstance(value, str):
            yield key, value

    def failure_output(self, details):
        return any("output" in key and text.strip() for key, text in self._strings(details))

    def log_exists(self, details):
        return any("log" in key and os.path.isfile(text) for key, text in self._strings(details))

    def log_has_output(self, details):
        logs = [text for key, text in self._strings(details) if "log" in key and os.path.isfile(text)]
        return any(Path(path).stat().st_size > 0 for path in logs)

    @staticmethod
    def secret():
        import secrets

        return "xo-goal-" + secrets.token_hex(12)

    @staticmethod
    def argv_containing(text):
        from tests.hexagonal.support.host_processes import arguments_containing

        # This test process's own arguments never hold it; anything else that does is a leak.
        return [command for pid, command in arguments_containing(text) if pid != os.getpid()]

    def cleanup(self):
        host = self.host()
        try:
            host.shutdown()
        except Exception:  # noqa: BLE001 - best effort teardown; the leak guard reports what remains
            pass
        for pid, _ in processes_under([self.base]):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        shutil.rmtree(self.base, ignore_errors=True)


def hosted_subject(adapter):
    return HostedSubject(adapter, Path(track(tempfile.mkdtemp(prefix="xhs-"))))
