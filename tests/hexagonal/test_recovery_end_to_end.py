"""Recovery after a container failure, end to end at the public boundary, on both terminal hosts.

A hosted session runs through the real CLI and project server with the resumable deterministic peer. A total
loss kills every process of the project at once and removes its sockets, keeping the durable home; a
server-only crash kills just the server. Then ``up``, and, under the manual policy, ``recover``.
"""
import os
import signal
import stat
import sys
import time

import pytest

from tests.hexagonal.contracts.project_server import INITIALIZED, initialize, rpc
from tests.hexagonal.support.host_processes import arguments_containing, processes_under
from tests.hexagonal.support.project_server import SESSION_FIXTURE, CliEnvironment, short_directory

HOSTS = {"multiplexer": "tmux", "detached-process": "detached-process"}


@pytest.fixture(params=sorted(HOSTS))
def project(request):
    with short_directory() as directory:
        cli = CliEnvironment(directory, terminal_host={"adapter": HOSTS[request.param]})
        probe = cli.json("status", expect=3)["terminal_host"]
        if probe.get("error") or probe.get("adapter") != HOSTS[request.param]:
            cli.cleanup()
            pytest.skip(f"terminal host unavailable: {probe}")
        cli.options["sessions"] = {"agent": {"adapter": "hosted", "options": {"session": {
            "adapter": "codex", "options": {"command": [sys.executable, str(SESSION_FIXTURE), "codex"], "timeout": 10}}}}}
        try:
            yield cli
        finally:
            cli.cleanup()
            for pid, _ in processes_under([directory]):
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass


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

    def close(self):
        self.cli.finish(self.process, self.channel)


def started_session(cli, *, in_flight=False):
    client = Client(cli)
    session = client.call("start_session", {"driver": "agent", "specification": {}, "idempotency_key": "start"})
    session = session["session"]
    assert client.call("send_session", {"session_id": session["id"], "message": {"text": "before"},
                                        "idempotency_key": "send-before"})
    if in_flight:  # a turn in flight when the failure comes: the peer holds "hang" for seconds
        client.send_without_waiting("send_session", {"session_id": session["id"], "message": {"text": "hang"},
                                                     "idempotency_key": "send-hang"})
        time.sleep(1)
    return client, session


def bridge_hosts(cli):
    return sorted(pid for pid, command in arguments_containing("xorchestrate.bridge") if str(cli.directory) in command)


def total_loss(cli):
    """Every process of the project dies at once and its sockets are gone; the durable home stays."""
    for pid, _ in processes_under([cli.directory]):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 10
    while processes_under([cli.directory]) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert processes_under([cli.directory]) == [], "the simulated failure left processes"
    for root, _, files in os.walk(cli.directory):
        for name in files:
            path = os.path.join(root, name)
            if stat.S_ISSOCK(os.lstat(path).st_mode):
                os.unlink(path)


def finished_recovery(cli, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        recovery = cli.json("status")["recovery"]
        if recovery.get("report") and not recovery.get("running"):
            return recovery
        time.sleep(0.25)
    raise AssertionError(f"recovery did not finish: {cli.json('status')['recovery']}")


def sessions(client):
    return {item["id"]: item for item in client.call("list_sessions", {})["sessions"]}


def unresolved(client, replacement_id):
    """The operations the replacement carries over as unresolved, with their preserved outcomes."""
    record = sessions(client)[replacement_id]
    return {operation_id: client.call("get_operation", {"operation_id": operation_id})["operation"]["status"]
            for operation_id in record.get("unresolved_effects") or []}


def assert_continues(client, original, replacement_id):
    listed = sessions(client)
    assert listed[replacement_id]["backend"]["native_id"] == original["backend"]["native_id"], "same conversation"
    reply = client.call("send_session", {"session_id": replacement_id, "message": {"text": "after"},
                                         "idempotency_key": "send-after"})
    assert reply["output"]["native_id"] == original["backend"]["native_id"], str(reply)[:300]


def recovered_map(report):
    return {item["session_id"]: item["replacement_session_id"] for item in report["recovered"]}


def test_after_total_loss_the_default_policy_brings_the_session_back_without_a_call(project):
    cli = project
    cli.save()
    client, original = started_session(cli)
    client.process.kill()
    total_loss(cli)
    assert cli.json("up")["started"] is True
    mapped = recovered_map(finished_recovery(cli)["report"])
    assert list(mapped) == [original["id"]], mapped
    after = Client(cli)
    try:
        assert_continues(after, original, mapped[original["id"]])
    finally:
        after.close()
    assert cli.json("down", "--detach")["stopped"] is True


def test_a_turn_in_flight_at_the_loss_is_left_unresolved_reported_and_never_replayed(project):
    cli = project
    cli.save()
    client, original = started_session(cli, in_flight=True)
    client.process.kill()
    total_loss(cli)
    cli.json("up")
    report = finished_recovery(cli)["report"]
    replacement = recovered_map(report)[original["id"]]
    after = Client(cli)
    try:
        carried = unresolved(after, replacement)
        # Exactly as the effect journal has it: recovery adds no reconciliation of its own.
        assert list(carried.values()) == ["pending"], f"the turn in flight is left as the journal has it: {carried}"
        (operation_id,) = carried
        listed = [entry for entries in report.values() if isinstance(entries, list) for entry in entries
                  if isinstance(entry, dict) and entry.get("operation_id") == operation_id]
        assert listed and all(entry.get("call") for entry in listed), \
            f"the report names the interrupted operation and the call that reconciles it: {str(report)[:400]}"
        assert sessions(after)[replacement]["backend"]["native_id"] == original["backend"]["native_id"]
        after.call("poll_session", {"session_id": replacement, "idempotency_key": "poll-after"})  # observation works
        after.call("send_session", {"session_id": replacement, "message": {"text": "after"},
                                    "idempotency_key": "send-after"}, refused="conflict")
        assert unresolved(after, replacement) == carried, "nothing replayed or resolved it"
    finally:
        after.close()
    cli.json("down", "--detach")


def test_under_manual_nothing_is_reconnected_until_recover(project):
    cli = project
    cli.options["recovery_policy"] = {"adapter": "manual"}
    cli.save()
    client, original = started_session(cli)
    client.process.kill()
    total_loss(cli)
    cli.json("up")
    time.sleep(2)
    status = cli.json("status")
    assert status["recovery"]["detected"]["selected"] == [], status["recovery"]
    assert any(item["session_id"] == original["id"] for item in status["sessions_needing_reconnect"]), status
    assert bridge_hosts(cli) == [], "nothing was reconnected"
    recovered = cli.json("recover")
    mapped = recovered_map(recovered.get("report") or recovered)
    assert original["id"] in mapped, str(recovered)[:300]
    after = Client(cli)
    try:
        assert_continues(after, original, mapped[original["id"]])
    finally:
        after.close()
    cli.json("down", "--detach")


def test_a_server_only_crash_adopts_the_running_terminal_and_keeps_its_bridge_host(project):
    cli = project
    cli.save()
    client, original = started_session(cli)
    hosts = bridge_hosts(cli)
    assert len(hosts) == 1, hosts
    os.kill(cli.json("status")["pid"], signal.SIGKILL)  # only the server
    client.process.kill()
    cli.json("up")
    mapped = recovered_map(finished_recovery(cli)["report"])
    assert original["id"] in mapped, mapped
    assert bridge_hosts(cli) == hosts, "the running terminal was adopted: the same bridge host process serves it"
    after = Client(cli)
    try:
        assert_continues(after, original, mapped[original["id"]])
    finally:
        after.close()
    cli.json("down", "--detach")
