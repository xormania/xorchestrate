"""A real end-to-end world for contract scenarios: a project on one real terminal host, driven through the CLI.

The world implements the contract-owned protocol in ``tests.hexagonal.contracts.e2e_world``. It drives the project
through the real CLI and project server (``CliEnvironment``), talks MCP through ``connect`` as a harness would,
observes status and the project's processes, waits through the shared classified wait budget, and applies named
disturbances through ``support.e2e_disturbances``. Nothing here decides a scenario's verdict.
"""
import json
import subprocess
import sys
from contextlib import contextmanager

import pytest

from scripts.testkit.adapters.ownership import processes
from scripts.testkit.core import ResourceScope
from tests.hexagonal.contracts.project_server import INITIALIZED, initialize, rpc
from tests.hexagonal.support import e2e_disturbances
from tests.hexagonal.support.host_processes import arguments_containing, processes_under
from tests.hexagonal.support.project_server import SESSION_FIXTURE, CliEnvironment, short_directory

HOSTS = {"multiplexer": "tmux", "detached-process": "detached-process"}
"""The real terminal hosts a world can run on: the contract's host name and the product's adapter name."""

DEADLINE = 60
"""The harness allowance a world's waits draw on, scaled by ``XO_TEST_TIMEOUT_SCALE`` in the shared wait."""

DEFAULT_COMMAND_TIMEOUT = 60
"""A CLI command's own limit outside any await, as before extraction."""


class Client:
    """An MCP client through ``connect``, as a harness would use it."""

    def __init__(self, cli, actor="worker"):
        self.cli = cli
        self.process, self.channel = cli.spawn("connect", "--actor", actor)
        assert self.channel.request(initialize())["result"]["serverInfo"]["name"] == "xorchestrate"
        self.channel.send(INITIALIZED)
        self.ids = iter(range(2, 10_000))

    def call(self, name, arguments, *, refused=None):
        reply = self.channel.request(rpc(next(self.ids), "tools/call", {"name": name, "arguments": arguments}))
        content = reply["result"].get("structuredContent", {})
        if refused is not None:
            assert reply["result"].get("isError") and content.get("error", {}).get("code") == refused, \
                str(content)[:300]
            return content
        assert not reply["result"].get("isError"), str(content)[:300]
        return content

    def send_without_waiting(self, name, arguments):
        self.channel.send(rpc(next(self.ids), "tools/call", {"name": name, "arguments": arguments}))

    def kill(self):
        """The harness side of a failure: the client process dies with the project."""
        self.process.kill()

    def close(self):
        self.cli.finish(self.process, self.channel)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class World:
    """One project on one real terminal host, with the resumable deterministic session peer."""

    def __init__(self, cli, host, *, clock=None, sleep=None, gates, scope=None):
        self.cli = cli
        self.host = host
        self.scope = ResourceScope() if scope is None else scope
        """This world's own cleanup (held gates and the like), closed before the project is shut down."""
        self.clock, self.sleep = clock, sleep
        self.gates = gates
        """The directory of this world's controlled gates (see ``hold``)."""
        self.spawned = []

    def _json(self, *arguments):
        """A CLI command's JSON reply; inside an await it gets only the budget that is left."""
        left = self.remaining()
        return self.cli.json(*arguments, timeout=DEFAULT_COMMAND_TIMEOUT if left is None else left)

    @property
    def directory(self):
        """The world's own short directory; its sockets and processes live under it."""
        return self.cli.directory

    def save(self, policy=None):
        if policy is not None:
            self.cli.options["recovery_policy"] = {"adapter": policy}
        self.cli.save()

    def client(self, actor="worker"):
        return Client(self.cli, actor)

    def start(self, client, *, in_flight=False):
        session = client.call("start_session", {"driver": "agent", "specification": {},
                                                "idempotency_key": "start"})["session"]
        assert client.call("send_session", {"session_id": session["id"], "message": {"text": "before"},
                                            "idempotency_key": "send-before"})
        if in_flight:  # a turn in flight when the failure comes: the peer holds "hang" for seconds
            client.send_without_waiting("send_session", {"session_id": session["id"], "message": {"text": "hang"},
                                                         "idempotency_key": "send-hang"})
            self.await_("the peer took delivery of the turn left in flight",
                        lambda: "hang" in [entry["text"] for entry in self.peer_received(session)])
        return session

    def up(self):
        return self._json("up")

    def down(self):
        return self._json("down", "--detach")

    def recover(self):
        return self._json("recover")

    def status(self):
        return self._json("status")

    def hold(self, stage):
        """Arm a controlled gate at ``stage`` before the action that reaches it (see ``e2e_disturbances.hold``)."""
        return e2e_disturbances.hold(self, stage)

    def disturb(self, value):
        return e2e_disturbances.apply(value, self)

    def bridge_hosts(self):
        return sorted(pid for pid, command in arguments_containing("xorchestrate.bridge")
                      if str(self.cli.directory) in command)

    @property
    def ledger(self):
        """The session peer's delivery record for this world (see ``fixtures/session_server.py``)."""
        return self.directory / "peer-ledger.jsonl"

    def peer_received(self, session):
        """What the session peer took delivery of for ``session``'s conversation, in delivery order."""
        native_id = session["backend"]["native_id"]
        if not self.ledger.exists():
            return []
        entries = [json.loads(line) for line in self.ledger.read_text(encoding="utf-8").splitlines() if line]
        return [entry for entry in entries if entry["native_id"] == native_id]

    def processes(self):
        """The project's live processes: identities under its directory."""
        return processes_under([self.cli.directory])

    def await_(self, label, predicate, *, timeout=None):
        """The predicate's first truthy value; raises ``WaitExpired`` naming ``label`` when the deadline passes."""
        return e2e_disturbances.await_(self, label, predicate, DEADLINE if timeout is None else timeout)

    def remaining(self):
        """Support only: the seconds left of the wait in progress, for the world's own blocking calls."""
        return e2e_disturbances.remaining(self)

    def spawn(self, argv):
        """Support only: start an extra owned process inside the world's directory and return its identity."""
        process = subprocess.Popen(argv, cwd=self.cli.directory, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.spawned.append(process)
        return processes()[process.pid][0]


def _session_options(ledger, gates):
    """The resumable deterministic peer, told where to record deliveries and where its gates are."""
    environment = {"XORCHESTRATE_FIXTURE_LEDGER": str(ledger), "XORCHESTRATE_FIXTURE_GATES": str(gates)}
    return {"agent": {"adapter": "hosted", "options": {"session": {
        "adapter": "codex", "options": {"command": [sys.executable, str(SESSION_FIXTURE), "codex"], "timeout": 10,
                                        "env": environment}}}}}


@contextmanager
def world(host, *, clock=None, sleep=None, gates=None):
    """A fresh world on ``host`` (a key of ``HOSTS``); skips when that host cannot run here, never falling back.

    ``clock`` and ``sleep`` are handed to the shared wait, so a test can drive the world's deadline. ``gates`` is the
    directory for controlled gates; by default one inside the world, removed with it.
    """
    with short_directory() as directory:
        scope = ResourceScope()
        cli = CliEnvironment(directory, terminal_host={"adapter": HOSTS[host]})
        try:
            probe = cli.json("status", expect=3)["terminal_host"]
            if probe.get("error") or probe.get("adapter") != HOSTS[host]:
                pytest.skip(f"terminal host unavailable: {probe}")
            gate_directory = directory / "gates" if gates is None else gates
            gate_directory.mkdir(parents=True, exist_ok=True)
            cli.options["sessions"] = _session_options(directory / "peer-ledger.jsonl", gate_directory)
            yield World(cli, host, clock=clock, sleep=sleep, gates=gate_directory, scope=scope)
        finally:
            scope.close()  # release anything held first, so shutting the project down cannot stall on it
            cli.cleanup()
            e2e_disturbances.reap(directory)
