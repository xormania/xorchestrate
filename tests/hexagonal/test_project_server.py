"""Project server: per-connection MCP sessions over interchangeable client transports."""

import json
import os
import socket
import stat
import sys
import threading

import pytest

from tests.hexagonal.contracts.project_server import (
    FRAME_LIMIT,
    INITIALIZED,
    created_by,
    initialize,
    rpc,
    run_conversation,
)
from tests.hexagonal.support.application import application
from tests.hexagonal.support.project_server import (
    SESSION_FIXTURE,
    CliEnvironment,
    HeldApplication,
    InProcessServer,
    StdioServer,
    short_directory,
)
from xorchestrate.adapters.local_socket import OwnerSocketListener, PeerCredentials, same_user
from xorchestrate.adapters.unix import UnixClient
from xorchestrate.model import OrchestrationError
from xorchestrate.server import default_socket_path


@pytest.fixture
def directory():
    with short_directory() as path:
        yield path


@pytest.fixture
def cli(directory):
    environment = CliEnvironment(directory)
    try:
        yield environment
    finally:
        environment.cleanup()


def open_session(channel):
    assert channel.request(initialize())["result"]["serverInfo"]["name"] == "xorchestrate"
    channel.send(INITIALIZED)


def create(channel, identifier, goal):
    return channel.request(rpc(identifier, "tools/call", {"name": "create_work", "arguments": {"goal": goal}}))


# -- one conversation, every transport ------------------------------------------

@pytest.mark.parametrize("transport", ["stdio", "unix"])
def test_same_mcp_conversation_passes_over_each_client_transport(transport, directory):
    app = application()
    if transport == "stdio":
        subject = StdioServer(app, actor="alice", max_frame_bytes=FRAME_LIMIT)
        channel = subject.channel
    else:
        subject = InProcessServer(app, directory / "s.sock", max_frame_bytes=FRAME_LIMIT)
        channel = subject.client("alice")
    try:
        work = run_conversation(channel, "alice")
        assert app.call("get_work", {"work_id": work["id"]})["work"]["created_by"] == "alice"
    finally:
        subject.stop()


@pytest.mark.parametrize("entry", ["serve", "connect"])
def test_same_mcp_conversation_passes_through_each_cli_entry(entry, cli):
    """``serve`` keeps today's stdio behaviour; ``connect`` reaches an auto-started unix server."""
    arguments = ["--actor", "alice", "serve"] if entry == "serve" else ["connect", "--actor", "alice"]
    process, channel = cli.spawn(*arguments, frame_limit=FRAME_LIMIT)
    work = run_conversation(channel, "alice")
    code, rest, stderr = cli.finish(process, channel)
    assert (code, rest, stderr) == (0, b"", b"")
    assert cli.json("call", "get_work", "--json", f'{{"work_id":"{work["id"]}"}}')["work"]["goal"] == work["goal"]
    status = cli.run("status")
    assert status.returncode == (0 if entry == "connect" else 3), status


# -- shared application, per-connection sessions -------------------------------

def test_clients_share_one_application_with_their_own_actor_and_session(directory):
    app = application()
    subject = InProcessServer(app, directory / "s.sock")
    try:
        alice, bob = subject.client("alice"), subject.client("bob")
        open_session(alice)
        # Initialization is per connection: bob's session has not been initialized.
        assert bob.request(rpc(1, "tools/list"))["error"]["code"] == -32002
        open_session(bob)
        first = create(alice, 2, "Alice's work")
        forged = bob.request(rpc(2, "tools/call", {"name": "create_work", "arguments": {"goal": "Bob's work"},
                                                    "actor": "alice"}))
        assert (created_by(first), created_by(forged)) == ("alice", "bob")
        # The same request identifier on two connections is two requests.
        read = bob.request(rpc(3, "tools/call", {"name": "get_work",
                                                  "arguments": {"work_id": first["result"]["structuredContent"]["work"]["id"]}}))
        assert read["result"]["structuredContent"]["work"]["created_by"] == "alice"
        subject.wait_connections(2)
        assert subject.server.status()["actors"] == ["alice", "bob"]
    finally:
        subject.stop()


def test_admission_limits_and_reserved_control_workers_are_shared_across_connections(directory):
    app = HeldApplication()
    subject = InProcessServer(app, directory / "s.sock", request_workers=1, max_pending_requests=1)
    try:
        alice, bob = subject.client("alice"), subject.client("bob")
        open_session(alice)
        open_session(bob)
        alice.send(rpc(2, "tools/call", {"name": "held"}))
        assert app.entered.wait(5)
        over = bob.request(rpc(2, "tools/call", {"name": "create_work"}))
        assert over["error"]["code"] == -32001, "admission is per server, not per connection"
        control = bob.request(rpc(3, "tools/call", {"name": "poll_session"}))
        assert control["result"]["structuredContent"] == {"name": "poll_session", "actor": "bob"}
        assert bob.request(rpc(4, "ping"))["result"] == {}
        app.release.set()
        assert alice.read()["result"]["structuredContent"] == {"name": "held", "actor": "alice"}
        assert created_by_actor(bob.request(rpc(5, "tools/call", {"name": "create_work"}))) == "bob"
    finally:
        app.release.set()
        subject.stop()


def created_by_actor(reply):
    return reply["result"]["structuredContent"]["actor"]


def test_disconnect_ends_only_that_client(directory):
    app = HeldApplication()
    subject = InProcessServer(app, directory / "s.sock")
    try:
        alice, bob = subject.client("alice"), subject.client("bob")
        open_session(alice)
        open_session(bob)
        alice.send(rpc(2, "tools/call", {"name": "held"}))
        assert app.entered.wait(5)
        alice.close()
        assert bob.request(rpc(2, "ping"))["result"] == {}
        app.release.set()
        subject.wait_connections(1)
        assert created_by_actor(bob.request(rpc(3, "tools/call", {"name": "after"}))) == "bob"
        assert ("held", "alice") in app.calls, "the disconnected client's admitted call still ran"
    finally:
        app.release.set()
        subject.stop()


# -- owner-only access ---------------------------------------------------------

def test_peer_credentials_are_checked_and_other_users_refused(directory):
    seen = []

    def refuse_all(credentials):
        seen.append(credentials)
        return False

    subject = InProcessServer(HeldApplication(), directory / "s.sock", check=refuse_all)
    try:
        assert stat.S_IMODE((directory / "s.sock").stat().st_mode) == 0o600
        with pytest.raises(OrchestrationError) as refused:
            UnixClient(directory / "s.sock", actor="intruder")
        assert refused.value.code == "connection_refused"
        if sys.platform.startswith("linux"):
            assert seen and seen[0].uid == os.getuid() and seen[0].pid == os.getpid()
        assert subject.server.status()["connections"] == 0
    finally:
        subject.stop()
    assert same_user(PeerCredentials(pid=1, uid=os.getuid(), gid=0))
    assert not same_user(PeerCredentials(pid=1, uid=os.getuid() + 1, gid=0))


def test_listener_never_replaces_a_foreign_path_and_removes_only_its_own_socket(directory):
    unrelated = directory / "file.sock"
    unrelated.write_text("operator data")
    with pytest.raises(OSError):
        OwnerSocketListener(unrelated)
    with pytest.raises(OrchestrationError):
        OwnerSocketListener(unrelated, replace_stale=True)
    assert unrelated.read_text() == "operator data"
    stale = OwnerSocketListener(directory / "stale.sock")
    stale.socket.close()  # a crashed server leaves its socket file behind
    fresh = OwnerSocketListener(directory / "stale.sock", replace_stale=True)
    fresh.close()
    assert not (directory / "stale.sock").exists()


def test_handshake_is_required_before_any_frame(directory):
    subject = InProcessServer(HeldApplication(), directory / "s.sock")
    try:
        raw = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        raw.settimeout(5)
        raw.connect(str(directory / "s.sock"))
        raw.sendall(b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n')
        reply = raw.makefile("rb").readline()
        assert b'"refused"' in reply
        raw.close()
    finally:
        subject.stop()


# -- draining shutdown ---------------------------------------------------------

def test_down_drains_in_flight_requests_and_stops_accepting(directory):
    app = HeldApplication()
    subject = InProcessServer(app, directory / "s.sock")
    try:
        alice, bob = subject.client("alice"), subject.client("bob")
        open_session(alice)
        open_session(bob)
        alice.send(rpc(2, "tools/call", {"name": "held"}))
        assert app.entered.wait(5)
        replies = []
        down = threading.Thread(target=lambda: replies.append(subject.server.down()))
        down.start()
        down.join(0.3)
        assert down.is_alive(), "down waits for the in-flight call"
        refused = bob.request(rpc(3, "tools/call", {"name": "late"}))
        assert refused["error"]["code"] == -32003
        late = UnixClient(directory / "s.sock", actor="late")
        late.socket.settimeout(5)
        assert late.reader.readline() == b"", "a draining server admits no new MCP client"
        late.close()
        app.release.set()
        assert alice.read()["result"]["structuredContent"]["name"] == "held"
        down.join(5)
        assert replies == [{"stopping": True, "pid": os.getpid(), "detached_sessions": []}]
        subject.thread.join(5)
        assert not subject.thread.is_alive()
        assert ("late", "bob") not in app.calls
    finally:
        app.release.set()
        subject.stop()


def test_down_is_refused_while_sessions_are_live_unless_detached(directory):
    app = HeldApplication()
    app.live = ["session-1"]
    subject = InProcessServer(app, directory / "s.sock")
    try:
        control = subject.client("operator", purpose="control")
        refusal = control.request({"command": "down"})
        assert refusal["error"]["code"] == "sessions_live"
        assert refusal["error"]["details"]["sessions"] == ["session-1"]
        alice = subject.client("alice")
        open_session(alice)
        assert created_by_actor(alice.request(rpc(2, "tools/call", {"name": "still-serving"}))) == "alice"
        status = control.request({"command": "status"})
        assert (status["running"], status["draining"], status["live_sessions"]) == (True, False, ["session-1"])
        stopped = control.request({"command": "down", "detach": True})
        assert stopped == {"stopping": True, "pid": os.getpid(), "detached_sessions": ["session-1"]}
        subject.thread.join(5)
        assert not subject.thread.is_alive()
    finally:
        subject.stop()


# -- CLI: auto-start, survival, status and down --------------------------------

def test_simultaneous_connects_start_exactly_one_server(cli):
    clients = [cli.spawn("connect", "--actor", f"agent-{index}") for index in range(4)]
    for index, (_process, channel) in enumerate(clients):
        open_session(channel)
        assert created_by(create(channel, 2, f"work {index}")) == f"agent-{index}"
    status = cli.json("status")
    assert status["connections"] == 4 and status["actors"] == [f"agent-{index}" for index in range(4)]
    log = cli.server_log()
    assert log.count('"serving"') == 1, log
    assert "server_running" not in log, log
    for process, channel in clients:
        assert cli.finish(process, channel)[0] == 0
    assert cli.json("down")["stopped"] is True
    assert cli.json("status", expect=3) == {"running": False, "socket": status["socket"]}


def test_sessions_started_through_the_server_survive_client_disconnect(cli):
    cli.options["sessions"] = {"fixture": {"adapter": "jsonrpc", "options": {
        "command": [sys.executable, str(SESSION_FIXTURE), "jsonrpc"], "timeout": 10}}}
    cli.config.write_text(json.dumps(cli.options), encoding="utf-8")
    first, channel = cli.spawn("connect", "--actor", "worker")
    open_session(channel)
    started = channel.request(rpc(2, "tools/call", {"name": "start_session", "arguments": {
        "driver": "fixture", "specification": {"goal": "Feature work"}, "idempotency_key": "start-1"}}))
    session_id = started["result"]["structuredContent"]["session"]["id"]
    assert cli.finish(first, channel)[0] == 0  # the harness exits; its connection closes

    second, channel = cli.spawn("connect", "--actor", "worker")
    open_session(channel)
    sent = channel.request(rpc(2, "tools/call", {"name": "send_session", "arguments": {
        "session_id": session_id, "message": {"text": "Continue"}, "idempotency_key": "send-1"}}))
    assert sent["result"]["isError"] is False, sent
    polled = channel.request(rpc(3, "tools/call", {"name": "poll_session", "arguments": {
        "session_id": session_id, "idempotency_key": "poll-1"}}))
    assert polled["result"]["structuredContent"]["output"] == [{"kind": "message", "message": {"text": "Continue"}}]
    assert cli.finish(second, channel)[0] == 0

    assert cli.json("status")["live_sessions"] == [session_id]
    refusal = cli.json("down", expect=2)
    assert refusal["error"]["code"] == "sessions_live"
    assert cli.json("status")["running"] is True
    stopped = cli.json("down", "--detach")
    assert stopped["stopped"] is True and stopped["detached_sessions"] == [session_id]
    sessions = {item["id"]: item for item in cli.json("call", "list_sessions", "--json", "{}")["sessions"]}
    assert sessions[session_id]["status"] == "disconnected"


def test_up_starts_once_and_status_reports_absence(cli):
    assert cli.json("status", expect=3)["running"] is False
    first = cli.json("up")
    second = cli.json("up")
    assert (first["started"], second["started"]) == (True, False)
    assert first["pid"] == second["pid"]
    assert cli.json("down")["stopped"] is True
    assert cli.json("down") == {"running": False, "socket": first["socket"]}


# -- socket location -----------------------------------------------------------

def test_default_socket_path_is_deterministic_private_and_bounded(directory):
    config = directory / "team.json"
    environ = {"XORCHESTRATE_RUNTIME_DIR": str(directory / "run")}
    path = default_socket_path(config, environ=environ)
    assert path == default_socket_path(directory / "." / "team.json", environ=environ)
    assert path != default_socket_path(directory / "other.json", environ=environ)
    assert path.parent == directory / "run" and stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert len(os.fsencode(str(path))) <= 107
    long_runtime = {"XDG_RUNTIME_DIR": str(directory / ("x" * 120))}
    fallback = default_socket_path(config, environ=long_runtime)
    assert fallback.name == path.name and "x" * 120 not in str(fallback)
    with pytest.raises(OrchestrationError):
        default_socket_path(config, environ={"XORCHESTRATE_RUNTIME_DIR": str(directory / ("y" * 120))})
    shared = directory / "shared"
    shared.mkdir(mode=0o755)
    shared.chmod(0o755)
    with pytest.raises(OrchestrationError):
        default_socket_path(config, environ={"XORCHESTRATE_RUNTIME_DIR": str(shared)})
