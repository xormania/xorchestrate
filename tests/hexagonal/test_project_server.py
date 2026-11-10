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
from tests.hexagonal.support.application import SessionCompletionFailure, Sessions, application, make_store
from tests.hexagonal.support.project_server import (
    SESSION_FIXTURE,
    CliEnvironment,
    HeldApplication,
    InProcessServer,
    LargeReplies,
    StdioServer,
    short_directory,
)
from xorchestrate.adapters.local_socket import (
    InstanceLock,
    OwnerSocketListener,
    PeerCredentials,
    check_socket_directory,
    lock_is_held,
    same_user,
)
from xorchestrate.adapters.stdio import StdioConnection
from xorchestrate.adapters.unix import UnixClient
from xorchestrate.mcp import MCPDispatcher
from xorchestrate.model import OrchestrationError
from xorchestrate.server import SERVER_LOCK_WAIT, ServerFiles, default_socket_path, server_identity, xorchestrate_home


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
    live = OwnerSocketListener(directory / "live.sock")
    try:
        # Even with its lock file gone, a socket something listens on is never replaced.
        with pytest.raises(OrchestrationError) as running:
            OwnerSocketListener(directory / "live.sock", replace_stale=True)
        assert running.value.code == "server_running"
        assert (directory / "live.sock").exists()
    finally:
        live.close()
    stale = OwnerSocketListener(directory / "stale.sock")
    stale.socket.close()  # a crashed server leaves its socket file behind
    fresh = OwnerSocketListener(directory / "stale.sock", replace_stale=True)
    assert stat.S_IMODE((directory / "stale.sock").stat().st_mode) == 0o600
    fresh.close()
    assert not (directory / "stale.sock").exists()


def test_a_listener_with_a_saturated_backlog_is_never_replaced(directory):
    path = directory / "busy.sock"
    busy = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    busy.bind(str(path))
    busy.listen(1)  # alive, but never accepts
    queued = []
    try:
        while True:  # fill the backlog until a further connect would block
            peer = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            peer.setblocking(False)
            queued.append(peer)
            try:
                peer.connect(str(path))
            except BlockingIOError:
                break
            assert len(queued) < 64, "the backlog never filled"
        identity = path.lstat().st_ino
        with pytest.raises(OrchestrationError) as running:
            OwnerSocketListener(path, replace_stale=True)
        assert running.value.code == "server_running"
        assert path.lstat().st_ino == identity, "a live socket was unlinked"
    finally:
        for peer in queued:
            peer.close()
        busy.close()


def test_client_refuses_a_listener_run_by_another_user_and_sends_nothing(directory):
    impostor = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    impostor.bind(str(directory / "shared.sock"))
    impostor.listen(1)
    impostor.settimeout(5)

    def foreign(credentials):
        # As if another OS user had pre-bound the path.
        return same_user(PeerCredentials(credentials.pid, credentials.uid + 1, credentials.gid))

    try:
        with pytest.raises(OrchestrationError) as untrusted:
            UnixClient(directory / "shared.sock", actor="alice", check=foreign)
        assert untrusted.value.code == "peer_untrusted"
        captured, _ = impostor.accept()
        captured.settimeout(5)
        assert captured.recv(65536) == b"", "the client sent its hello to an untrusted listener"
        captured.close()
        # The default check accepts this user's own listener (no hello answer: a timeout, not a refusal).
        with pytest.raises(OrchestrationError) as own:
            UnixClient(directory / "shared.sock", timeout=0.3)
        assert own.value.code == "handshake_timeout"
    finally:
        impostor.close()


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
        assert refused["error"]["code"] == -32003, "new effect calls are refused while draining"
        observed = bob.request(rpc(4, "tools/call", {"name": "poll_session"}))
        assert observed["result"]["structuredContent"] == {"name": "poll_session", "actor": "bob"}, \
            "observation/control tools stay available while draining"
        with pytest.raises(OrchestrationError) as late:
            UnixClient(directory / "s.sock", actor="late")
        assert (late.value.code, late.value.details["reason"]) == ("connection_refused", "draining")
        status = subject.client("operator", purpose="control").request({"command": "status"})
        assert status["draining"] is True, "control connections are still served while draining"
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


def test_null_identifier_tool_calls_obey_drain_admission_and_count_in_flight(directory):
    app = HeldApplication()
    subject = InProcessServer(app, directory / "s.sock")
    try:
        alice, bob = subject.client("alice"), subject.client("bob")
        open_session(alice)
        open_session(bob)
        alice.send({"jsonrpc": "2.0", "id": None, "method": "tools/call", "params": {"name": "held"}})
        assert app.entered.wait(5)
        assert subject.dispatcher.in_flight == 1 and not subject.dispatcher.wait_idle(0.2), \
            "a null-identifier call ran outside the drain's in-flight accounting"
        app.release.set()
        assert alice.read()["result"]["structuredContent"] == {"name": "held", "actor": "alice"}
        assert subject.dispatcher.wait_idle(5)
        subject.dispatcher.begin_drain()
        refused = bob.request({"jsonrpc": "2.0", "id": None, "method": "tools/call", "params": {"name": "late"}})
        assert (refused["id"], refused["error"]["code"]) == (None, -32003)
        observed = bob.request({"jsonrpc": "2.0", "id": None, "method": "tools/call", "params": {"name": "poll_session"}})
        assert observed["result"]["structuredContent"]["name"] == "poll_session"
        subject.dispatcher.stop_admission()
        stopped = bob.request({"jsonrpc": "2.0", "id": None, "method": "tools/call", "params": {"name": "poll_session"}})
        assert stopped["error"]["code"] == -32003
        assert [name for name, _ in app.calls] == ["held", "poll_session"], "an effect ran during the drain"
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
    assert cli.json("status", expect=3) == {"running": False, "socket": None, "overrides": []}


def test_sessions_started_through_the_server_survive_client_disconnect(cli):
    cli.options["sessions"] = {"fixture": {"adapter": "jsonrpc", "options": {
        "command": [sys.executable, str(SESSION_FIXTURE), "jsonrpc"], "timeout": 10}}}
    cli.save()
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
    assert cli.json("down") == {"running": False, "socket": None}


# -- stopping is decided independently of the reply ----------------------------

def test_down_whose_client_gave_up_still_stops_and_status_never_stops(directory):
    app = HeldApplication()
    subject = InProcessServer(app, directory / "s.sock")
    try:
        alice = subject.client("alice")
        open_session(alice)
        alice.send(rpc(2, "tools/call", {"name": "held"}))
        assert app.entered.wait(5)
        impatient = subject.client("operator", purpose="control")
        impatient.send({"command": "down"})
        with pytest.raises(TimeoutError):
            impatient.read(timeout=0.3)
        impatient.close()  # the down client times out and leaves
        status = subject.client("operator", purpose="control").request({"command": "status"})
        assert status["draining"] is True and not subject.server.stopped, "a status request never stops the server"
        app.release.set()
        assert alice.read()["result"]["structuredContent"]["name"] == "held"
        subject.thread.join(5)
        assert not subject.thread.is_alive(), "the drain finished and the server stopped without another request"
    finally:
        app.release.set()
        subject.stop()


def test_a_decided_stop_is_carried_out_even_if_the_down_exchange_fails(directory):
    subject = InProcessServer(HeldApplication(), directory / "s.sock")
    try:
        decide = subject.server._drain

        def decide_then_fail(detach):
            decide(detach)
            raise RuntimeError("failure after the stop was decided")

        subject.server._drain = decide_then_fail
        subject.client("operator", purpose="control").send({"command": "down"})
        subject.thread.join(5)
        assert not subject.thread.is_alive(), "a decided stop was lost when its control exchange failed"
    finally:
        subject.stop()


def test_control_down_requires_a_boolean_detach(directory):
    subject = InProcessServer(HeldApplication(), directory / "s.sock")
    try:
        control = subject.client("operator", purpose="control")
        reply = control.request({"command": "down", "detach": "yes"})
        assert reply["error"]["code"] == "invalid_arguments"
        assert control.request({"command": "status"})["running"] is True and not subject.server.stopped
    finally:
        subject.stop()


# -- handshakes never hold up other clients ------------------------------------

def test_idle_and_dripping_peers_do_not_delay_other_clients(directory):
    import time
    subject = InProcessServer(HeldApplication(), directory / "s.sock", handshake_timeout=1.0)
    stop = threading.Event()
    peers = []
    try:
        for _ in range(2):
            peers.append(socket.socket(socket.AF_UNIX, socket.SOCK_STREAM))
            peers[-1].connect(str(directory / "s.sock"))
        idle, drip = peers

        def dripper():
            while not stop.is_set():
                try:
                    drip.sendall(b" ")
                except OSError:
                    return
                time.sleep(0.2)

        threading.Thread(target=dripper, daemon=True).start()
        started = time.monotonic()
        open_session(subject.client("alice"))
        control = subject.client("operator", purpose="control").request({"command": "status"})
        assert control["running"] and time.monotonic() - started < 0.5, "other clients were held up by a handshake"
        for peer in (idle, drip):
            peer.settimeout(5)
            started = time.monotonic()
            reply = peer.makefile("rb").readline()
            assert b'"handshake_timeout"' in reply, reply
            assert time.monotonic() - started < 2.5, "the hello deadline is total, not per receive"
    finally:
        stop.set()
        for peer in peers:
            peer.close()
        subject.stop()


def test_unanswered_handshake_is_a_timeout_not_a_refusal(directory):
    silent = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    silent.bind(str(directory / "silent.sock"))
    silent.listen(1)
    try:
        with pytest.raises(OrchestrationError) as caught:
            UnixClient(directory / "silent.sock", timeout=0.3)
        assert caught.value.code == "handshake_timeout"
    finally:
        silent.close()


def test_a_server_side_hello_timeout_is_reported_as_a_timeout(directory):
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(directory / "slow.sock"))
    listener.listen(1)

    def give_up():
        peer, _ = listener.accept()
        peer.makefile("rb").readline()
        peer.sendall(b'{"xorchestrate":"refused","error":{"code":"handshake_timeout","message":"late"}}\n')
        peer.close()

    responder = threading.Thread(target=give_up, daemon=True)
    responder.start()
    try:
        with pytest.raises(OrchestrationError) as caught:
            UnixClient(directory / "slow.sock", timeout=5)
        assert (caught.value.code, caught.value.details["reason"]) == ("handshake_timeout", "handshake_timeout")
    finally:
        responder.join(5)
        listener.close()


# -- failed writes close the connection ----------------------------------------

def test_write_timeout_closes_the_connection_instead_of_corrupting_frames(directory):
    import time
    subject = InProcessServer(LargeReplies(), directory / "s.sock", write_timeout=0.3)
    try:
        stalled = subject.client("alice", max_frame_bytes=4_000_000)
        open_session(stalled)
        for identifier in range(2, 6):
            stalled.send(rpc(identifier, "tools/call", {"name": "big"}))
        time.sleep(1.5)  # stop reading for longer than the write timeout
        stalled.client.socket.settimeout(5)
        data = b""
        while chunk := stalled.client.reader.read1(1 << 20):
            data += chunk
        complete, _, truncated = data.rpartition(b"\n")
        lines = [line for line in complete.split(b"\n") if line]
        assert all(json.loads(line)["result"] for line in lines), "a complete line held a corrupted frame"
        assert len(lines) < 4, "the stalled client should have lost the connection"
        assert b"\n" not in truncated
        # The connection ended; the server keeps serving others.
        assert subject.client("bob").request(initialize())["result"]["serverInfo"]["name"] == "xorchestrate"
    finally:
        subject.stop()


# -- capacity and refusals -----------------------------------------------------

def test_full_server_refuses_in_the_handshake_and_keeps_control_available(directory):
    subject = InProcessServer(HeldApplication(), directory / "s.sock", max_connections=1)
    try:
        open_session(subject.client("alice"))
        subject.wait_connections(1)
        with pytest.raises(OrchestrationError) as full:
            UnixClient(directory / "s.sock", actor="bob")
        assert (full.value.code, full.value.details["reason"]) == ("connection_refused", "server_full")
        assert "connection limit" in str(full.value)
        status = subject.client("operator", purpose="control").request({"command": "status"})
        assert (status["connections"], status["max_connections"]) == (1, 1)
    finally:
        subject.stop()


def test_connect_reports_a_full_server_and_starts_no_other(cli):
    server, output = cli.spawn("serve", "--transport", "unix", "--max-connections", "1")
    first = None
    try:
        assert "serving" in output.read()
        first = cli.spawn("connect", "--actor", "first")
        open_session(first[1])
        refused = cli.run("connect", "--actor", "second", "--start-timeout", "5")
        assert refused.returncode == 2, refused
        error = json.loads(refused.stderr)["error"]
        assert error["code"] == "connection_refused" and error["details"]["reason"] == "server_full"
        assert cli.server_log() == "", "no second server was started"
    finally:
        if first is not None:
            cli.finish(*first)
        cli.run("down", "--detach")
        server.communicate(timeout=10)


# -- drain mechanics -----------------------------------------------------------

def test_requests_on_a_stopped_dispatcher_release_their_admission():
    import io
    dispatcher = MCPDispatcher(HeldApplication(), request_workers=1, max_pending_requests=1)
    dispatcher.close()  # pools shut down: submit raises
    frames = [initialize(), rpc(2, "tools/call", {"name": "late"}), rpc(3, "tools/call", {"name": "later"})]
    output = io.StringIO()
    source = io.BytesIO(b"".join(json.dumps(frame).encode() + b"\n" for frame in frames))
    runner = threading.Thread(target=dispatcher.serve_connection, args=(StdioConnection(source, output),), daemon=True)
    runner.start()
    runner.join(5)
    assert not runner.is_alive(), "a request that never started is still counted as pending"
    replies = {reply["id"]: reply for reply in map(json.loads, output.getvalue().splitlines())}
    assert replies[2]["error"]["code"] == -32003 and replies[3]["error"]["code"] == -32003, replies
    assert dispatcher.in_flight == 0


def test_live_sessions_snapshots_sessions_that_workers_add_concurrently():
    from contextlib import contextmanager
    app = application()
    app._owned_session_ids.update({"absent-1", "absent-2"})
    store = app.store

    class AddingTransaction:
        def __init__(self, tx):
            self.tx = tx

        def get(self, kind, key):
            # A request worker starts another session while live_sessions reads.
            app._owned_session_ids.add(f"added-{len(app._owned_session_ids)}")
            return self.tx.get(kind, key)

    class AddingStore:
        @contextmanager
        def transaction(self):
            with store.transaction() as tx:
                yield AddingTransaction(tx)

    app.store = AddingStore()
    assert app.live_sessions() == []


def _unapplied_start():
    """A session start whose driver succeeded but whose receipt failed to apply."""
    store = SessionCompletionFailure(make_store())
    app = application(store, driver=Sessions())
    work = app.call("create_work", {"goal": "Feature work"})["work"]
    store.armed = True
    with pytest.raises(OrchestrationError) as unapplied:
        app.call("start_session", {"driver": "agent", "specification": {}, "work_id": work["id"],
                                   "idempotency_key": "start-1"})
    assert unapplied.value.code == "result_unapplied"
    operation = app.call("get_operation", {"operation_id": unapplied.value.details["operation_id"]})["operation"]
    return app, store, operation


def _down_is_refused_for(app, directory, session_id):
    subject = InProcessServer(app, directory / "s.sock")
    try:
        control = subject.client("operator", purpose="control")
        refusal = control.request({"command": "down"})
        assert refusal["error"]["code"] == "sessions_live", "down would close a session this runtime's driver holds"
        assert refusal["error"]["details"]["sessions"] == [session_id]
        assert not subject.server.stopped
    finally:
        subject.stop()


def test_an_unapplied_session_start_blocks_down_without_detach(directory):
    app, store, operation = _unapplied_start()
    session_id = operation["session_id"]
    assert app.live_sessions() == [session_id]
    sequence = iter(range(1000))
    other_runtime = application(store, driver=Sessions(), identifiers=lambda: f"other-{next(sequence)}")
    assert other_runtime.live_sessions() == [], "a session reserved by another runtime is not this runtime's"
    _down_is_refused_for(app, directory, session_id)


def test_a_session_start_reconciled_later_blocks_down_without_detach(directory):
    app, _store, operation = _unapplied_start()
    resolved = app.call("resolve_operation", {"operation_id": operation["id"], "expected_version": operation["version"],
                                              "outcome": "completed", "reason": "Apply adapter receipt"})["operation"]
    session = resolved["result"]["session"]
    assert session["status"] == "active"
    assert app.live_sessions() == [session["id"]]
    _down_is_refused_for(app, directory, session["id"])


# -- one server per project, whatever the client's environment ----------------

def test_clients_with_different_environments_reach_one_server(cli, directory):
    base = {**cli.environment}
    first = {**base, "XORCHESTRATE_RUNTIME_DIR": str(directory / "run-a"), "TMPDIR": str(directory)}
    second = {**base, "XDG_RUNTIME_DIR": str(directory / ("x" * 120)), "TMPDIR": "/tmp"}
    (directory / "run-a").mkdir(mode=0o700)
    a = cli.json("up", environment=first)
    b = cli.json("up", environment=second)
    c = cli.json("up")
    assert (a["started"], b["started"], c["started"]) == (True, False, False)
    assert a["pid"] == b["pid"] == c["pid"] and a["socket"] == b["socket"] == c["socket"]
    relative = cli.run("up", environment={**base, "XORCHESTRATE_RUNTIME_DIR": "relative/run"})
    assert relative.returncode == 0, "clients do not compute the socket, so their runtime setting is irrelevant"
    assert cli.json("down")["stopped"] is True


def test_server_locations_are_private_absolute_and_bounded(directory):
    environ = {"XORCHESTRATE_HOME": str(directory / "home")}
    files = ServerFiles.locate(server_identity("instance-a"), environ).ensure()
    assert files == ServerFiles.locate(server_identity("instance-a"), environ)
    assert files.key != ServerFiles.locate(server_identity("instance-b"), environ).key
    assert stat.S_IMODE(files.directory.stat().st_mode) == 0o700
    assert default_socket_path(files, environ).parent == files.directory
    long_home = ServerFiles.locate(files.identity, {"XORCHESTRATE_HOME": str(directory / ("h" * 120))})
    fallback = default_socket_path(long_home, {})
    assert fallback.name == f"{files.key}.sock" and len(os.fsencode(str(fallback))) <= 107
    for variable in ("XORCHESTRATE_HOME", "XORCHESTRATE_RUNTIME_DIR"):
        with pytest.raises(OrchestrationError):
            default_socket_path(files, {variable: "relative/dir"}) if variable != "XORCHESTRATE_HOME" \
                else xorchestrate_home({variable: "relative/dir"})
    shared = directory / "shared"
    shared.mkdir(mode=0o755)
    shared.chmod(0o755)
    with pytest.raises(OrchestrationError):
        default_socket_path(files, {"XORCHESTRATE_RUNTIME_DIR": str(shared)})
    foreign_mode = files.directory / "loose.lock"
    foreign_mode.write_text("")
    foreign_mode.chmod(0o644)
    with pytest.raises(OrchestrationError):
        InstanceLock(foreign_mode).acquire()


def test_unsafe_explicit_socket_directories_are_refused_by_every_command(cli, directory):
    shared = directory / "shared"
    shared.mkdir()
    shared.chmod(0o777)
    sticky = directory / "sticky"
    sticky.mkdir()
    sticky.chmod(0o1777)  # like /tmp: another user could still pre-create the name
    for unsafe in (shared, sticky):
        for command in (["serve", "--transport", "unix"], ["connect"], ["up"], ["status"], ["down"]):
            result = cli.run(*command, "--socket", str(unsafe / "s.sock"), timeout=30)
            assert result.returncode == 2, (command, result)
            error = json.loads(result.stderr)["error"]
            assert error["code"] == "socket_path_unsafe", (command, error)
            assert error["details"] == {"directory": str(unsafe), "reason": "it is writable by group or others"}
        assert not (unsafe / "s.sock").exists()
    assert cli.server_log() == "", "no server was started for an unsafe socket"


def test_socket_directory_owned_by_another_user_is_refused(directory):
    with pytest.raises(OrchestrationError) as foreign:
        check_socket_directory(directory / "s.sock", uid=os.getuid() + 1)
    assert foreign.value.code == "socket_path_unsafe"
    assert foreign.value.details == {"directory": str(directory), "reason": "it is not owned by this user"}
    parent = directory / "open"
    parent.mkdir()
    parent.chmod(0o777)
    (parent / "mine").mkdir(mode=0o700)
    with pytest.raises(OrchestrationError) as renameable:
        check_socket_directory(parent / "mine" / "s.sock")
    assert renameable.value.details["directory"] == str(parent), "an enclosing directory others can rename into"


def test_private_explicit_and_default_socket_directories_are_accepted(cli, directory):
    private = directory / "private"
    private.mkdir(mode=0o700)
    assert check_socket_directory(private / "s.sock") == private / "s.sock"
    started = cli.json("up", "--socket", str(private / "s.sock"))
    assert started["started"] is True and started["socket"] == str(private / "s.sock")
    assert cli.json("status", "--socket", str(private / "s.sock"))["pid"] == started["pid"]
    assert cli.json("down", "--socket", str(private / "s.sock"))["stopped"] is True
    files = ServerFiles.locate(server_identity("instance-a"), {"XORCHESTRATE_HOME": str(directory / "home")})
    files.ensure()
    for environ in ({}, {"XORCHESTRATE_RUNTIME_DIR": str(directory / "run")},
                    {"XORCHESTRATE_HOME": str(directory / ("h" * 120))}):
        located = files if "XORCHESTRATE_HOME" not in environ else ServerFiles.locate(files.identity, environ)
        check_socket_directory(default_socket_path(located, environ))


def test_lock_probes_never_make_a_starting_server_give_up(directory):
    lock_path = directory / "server.lock"
    created = InstanceLock(lock_path)
    assert created.acquire()
    created.release()
    stop = threading.Event()

    def probe():
        # Back-to-back probes, as from several clients polling at once; a single
        # non-blocking attempt by the server loses to them regularly.
        while not stop.is_set():
            lock_is_held(lock_path)

    probes = [threading.Thread(target=probe, daemon=True) for _ in range(2)]
    for thread in probes:
        thread.start()
    try:
        for _ in range(50):
            lock = InstanceLock(lock_path)
            assert lock.acquire(wait=SERVER_LOCK_WAIT), "a probe made the server's lock acquisition fail"
            assert lock_is_held(lock_path)
            lock.release()
    finally:
        stop.set()
        for thread in probes:
            thread.join(5)


# -- crash recovery and startup failure through the CLI ------------------------

def test_a_crashed_server_is_replaced_on_the_next_start(cli):
    import signal
    import time
    first = cli.json("up")
    os.kill(first["pid"], signal.SIGKILL)
    deadline = time.monotonic() + 10
    while cli.run("status").returncode != 3:
        assert time.monotonic() < deadline, "the killed server still answered"
        time.sleep(0.05)
    assert os.path.exists(first["socket"]), "the crash left its socket file behind"
    second = cli.json("up")
    assert second["started"] is True and second["pid"] != first["pid"] and second["socket"] == first["socket"]
    process, channel = cli.spawn("connect", "--actor", "after-crash")
    open_session(channel)
    assert created_by(create(channel, 2, "after the crash")) == "after-crash"
    assert cli.finish(process, channel)[0] == 0
    assert cli.json("down")["stopped"] is True


def test_startup_failure_is_reported_with_the_server_log(cli):
    cli.save({"not_a_section": {}})
    failed = cli.run("up", "--start-timeout", "20")
    assert failed.returncode == 2, failed
    error = json.loads(failed.stderr)["error"]
    assert error["code"] == "server_unavailable" and error["details"]["exit_code"] == 2
    assert "configuration_error" in cli.server_log()
    assert cli.run("status").returncode == 3
