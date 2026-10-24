"""Full-duplex contracts through real pipes and the active MCP input adapter."""
import io
import json
import os
import queue
import sys
import threading

import pytest

from xorchestrate.adapters.jsonrpc import JsonLineProcess
from xorchestrate.mcp import serve
from xorchestrate.model import OrchestrationError

PEER = r'''
import json, sys, threading
lock = threading.Lock()
held = []
def emit(packet):
    with lock:
        print(json.dumps(dict(jsonrpc="2.0", **packet)), flush=True)
for line in sys.stdin:
    packet = json.loads(line)
    method = packet.get("method")
    if method == "start":
        emit({"id":packet["id"],"result":{"accepted":True}})
        emit({"id":"native-call", "method":"callback", "params":{"value":1}})
    elif method == "hold":
        held.append(packet)
        emit({"method":"submitted","params":{"id":packet["id"]}})
    elif method == "callback_hold":
        held.append(packet)
        emit({"id":"native-call", "method":"callback", "params":{"value":1}})
    elif method == "release":
        for item in reversed(held):
            emit({"id":item["id"],"result":item.get("params",{})})
        held.clear()
    elif method == "duplicate":
        emit({"id":packet["id"],"result":{}})
        emit({"id":"duplicate", "method":"callback", "params":{"value":1}})
        emit({"id":"duplicate", "method":"callback", "params":{"value":1}})
    elif method == "conflict":
        emit({"id":"native-call", "method":"callback", "params":{"value":2}})
    elif method == "bad_response":
        emit({"id":987654, "result":{}})
    elif method == "flood_callbacks":
        for i in range(20):
            emit({"id":"flood-"+str(i), "method":"callback", "params":{}})
    elif method == "ping":
        emit({"id":packet["id"], "result":{"pong":True}})
    elif method is None:
        emit({"method":"callback_answered", "params":{"id":packet["id"]}})
'''


def transport(handler=None, observer=None, **extra):
    return JsonLineProcess({"command": [sys.executable, "-u", "-c", PEER],
                            "timeout": 3, "shutdown_timeout": 0.01, **extra},
                           request_handler=handler, notification_handler=observer)


def test_callback_after_submission_ack_progresses_without_poll():
    called = threading.Event()
    answered = threading.Event()

    def handle(method, params):
        assert (method, params) == ("callback", {"value": 1})
        called.set()
        return {"ok": True}

    peer = transport(handle, lambda method, params: answered.set() if method == "callback_answered" else None)
    try:
        assert peer.request("start") == {"accepted": True}
        assert called.wait(1), "post_ack_callback_progress"
        assert answered.wait(1), "post_ack_callback_response"
    finally:
        peer.close()


def test_pending_native_response_does_not_block_control_or_submission():
    submitted = threading.Event()
    peer = transport(observer=lambda method, params: submitted.set() if method == "submitted" else None)
    try:
        future = peer.request_async("hold", {"message": "first"})
        assert submitted.wait(1), "submission_frame_written"
        assert not future.done(), "submission_is_not_completion"
        assert not future.cancel(), "submitted_effect_cannot_be_cancelled_away"
        assert peer.request("ping") == {"pong": True}, "pending_response_does_not_block_control"
        peer.notify("release")
        assert future.result(timeout=1) == {"message": "first"}
    finally:
        peer.close()


def test_held_callback_allows_native_control_and_nested_request():
    entered = threading.Event()
    release = threading.Event()
    answered = threading.Event()
    peer = None

    def handle(method, params):
        assert peer.request("ping") == {"pong": True}, "callback_can_issue_nested_request"
        entered.set()
        assert release.wait(2)
        return {}

    peer = transport(handle, lambda method, params: answered.set() if method == "callback_answered" else None)
    try:
        future = peer.request_async("callback_hold")
        assert entered.wait(1), "callback_started_while_outer_response_pending"
        assert not future.done()
        assert peer.request("ping") == {"pong": True}, "held_callback_does_not_block_control"
        release.set()
        assert answered.wait(1)
        peer.notify("release")
        assert future.result(timeout=1) == {}
    finally:
        release.set()
        peer.close()


def test_out_of_order_responses_remain_bound_to_their_request():
    peer = transport()
    try:
        first = peer.request_async("hold", {"message": "first"})
        second = peer.request_async("hold", {"message": "second"})
        peer.notify("release")
        assert first.result(timeout=1) == {"message": "first"}
        assert second.result(timeout=1) == {"message": "second"}
    finally:
        peer.close()


def test_duplicate_native_call_never_reexecutes_callback():
    calls = []
    answered = threading.Event()
    peer = transport(lambda method, params: calls.append((method, params)) or {},
                     lambda method, params: answered.set() if method == "callback_answered" else None)
    try:
        peer.request("duplicate")
        assert answered.wait(1)
        # A later ping is ordered after both duplicate packets on the same pipe.
        assert peer.request("ping")["pong"]
        assert calls == [("callback", {"value": 1})], "duplicate_callback_executed_once"
    finally:
        peer.close()


@pytest.mark.parametrize("fault", ["conflict", "bad_response"])
def test_conflicting_call_or_unmatched_response_closes_with_unknown_outcome(fault):
    answered = threading.Event()
    peer = transport(lambda method, params: {},
                     lambda method, params: answered.set() if method == "callback_answered" else None)
    try:
        peer.request("start")
        assert answered.wait(1)
        waiting = peer.request_async("hold")
        peer.notify(fault)
        with pytest.raises(OrchestrationError) as caught:
            waiting.result(timeout=2)
        assert caught.value.code == "provider_protocol"
        assert caught.value.details["outcome"] == "unknown"
        assert peer.process.poll() is not None
    finally:
        peer.close()


def test_callback_queue_overload_is_bounded_and_uncertain():
    release = threading.Event()
    peer = transport(lambda method, params: release.wait(2) or {},
                     max_pending_messages=1, callback_workers=1)
    try:
        pending = peer.request_async("flood_callbacks")
        with pytest.raises(OrchestrationError) as caught:
            pending.result(timeout=2)
        assert caught.value.code == "provider_protocol"
        assert caught.value.details["outcome"] == "unknown"
    finally:
        release.set()
        peer.close()


def test_pending_request_admission_does_not_invalidate_existing_effect():
    peer = transport(max_pending_messages=1)
    try:
        future = peer.request_async("hold")
        with pytest.raises(OrchestrationError) as caught:
            peer.request_async("ping")
        assert caught.value.code == "provider_capacity"
        assert caught.value.details["outcome"] == "not_started"
        assert not future.done()
        peer.notify("release")
        assert future.result(timeout=1) == {}
    finally:
        peer.close()


class Capture:
    def __init__(self):
        self.replies = queue.Queue()

    def write(self, value):
        self.replies.put(json.loads(value))

    def flush(self):
        pass


def test_mcp_held_calls_leave_control_and_unrelated_requests_responsive():
    entered = threading.Event()
    release = threading.Event()

    class Application:
        def call(self, name, arguments, actor):
            if name == "held":
                entered.set()
                assert release.wait(3)
            return {"name": name, "actor": actor}

    read_fd, write_fd = os.pipe()
    source = os.fdopen(read_fd, "rb", buffering=0)
    target = os.fdopen(write_fd, "wb", buffering=0)
    capture = Capture()
    runner = threading.Thread(target=serve, args=(Application(), source, capture),
                              kwargs={"request_workers": 1, "max_pending_requests": 1}, daemon=True)
    runner.start()

    def send(identifier, method, params):
        target.write((json.dumps({"jsonrpc": "2.0", "id": identifier, "method": method, "params": params}) + "\n").encode())

    try:
        send(1, "initialize", {"protocolVersion": "2025-06-18"})
        assert capture.replies.get(timeout=1)["id"] == 1
        send(2, "tools/call", {"name": "held"})
        assert entered.wait(1)
        send(3, "tools/call", {"name": "poll_session"})
        observed = capture.replies.get(timeout=1)
        assert observed["id"] == 3 and not observed["result"]["isError"], "mcp_control_progress"
        send(4, "ping", {})
        assert capture.replies.get(timeout=1)["id"] == 4, "mcp_ping_progress"
        send(5, "tools/call", {"name": "over_capacity"})
        overload = capture.replies.get(timeout=1)
        assert overload["id"] == 5 and overload["error"]["code"] == -32001
        release.set()
        assert capture.replies.get(timeout=1)["id"] == 2
    finally:
        release.set()
        target.close()
        runner.join(timeout=3)
        source.close()
    assert not runner.is_alive()


def test_idle_callback_timeout_closes_owned_process_without_controller_poll():
    entered = threading.Event()
    release = threading.Event()

    def held(method, params):
        entered.set()
        release.wait(2)
        return {}

    peer = transport(held, callback_timeout=0.1)
    try:
        assert peer.request("start")["accepted"]
        assert entered.wait(1)
        assert peer._close_complete.wait(1), "idle_callback_deadline_enforced"
        with pytest.raises(OrchestrationError) as caught:
            peer.poll_notifications()
        assert caught.value.code == "provider_timeout"
        assert caught.value.details["outcome"] == "unknown"
        assert peer.process.poll() is not None
    finally:
        release.set()
        peer.close()


def test_notification_observer_failure_retains_unknown_and_sanitizes_exception():
    def observer(method, params):
        raise RuntimeError("private observer detail")

    peer = transport(observer=observer)
    try:
        pending = peer.request_async("hold")
        with pytest.raises(OrchestrationError) as caught:
            pending.result(timeout=1)
        assert caught.value.code == "provider_unavailable"
        assert caught.value.details == {"outcome": "unknown"}
        assert "private observer detail" not in str(caught.value)
        assert peer.process.poll() is not None
    finally:
        peer.close()


def test_mcp_invalid_tool_names_do_not_break_concurrent_dispatch():
    class Application:
        def call(self, *args, **kwargs):
            raise AssertionError("Malformed request must not reach application")

    packets = [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "before_initialize"}},
        {"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        *[{"jsonrpc": "2.0", "id": index, "method": "tools/call", "params": {"name": value}}
          for index, value in enumerate(([], {}, False), 3)],
        {"jsonrpc": "2.0", "id": 6, "method": "ping"},
    ]
    source = io.BytesIO("".join(json.dumps(packet) + "\n" for packet in packets).encode())
    target = io.StringIO()
    serve(Application(), source, target)
    results = {packet["id"]: packet for packet in map(json.loads, target.getvalue().splitlines())}
    assert results[1]["error"]["code"] == -32002
    assert all(results[index]["error"]["code"] == -32602 for index in range(3, 6))
    assert results[6]["result"] == {}


def test_one_write_slot_keeps_immediate_native_callback_progress():
    entered = threading.Event()
    release_writer = threading.Event()
    answered = threading.Event()
    peer = transport(lambda method, params: entered.set() or {},
        lambda method, params: answered.set() if method == "callback_answered" else None,
        max_pending_messages=1)
    original = peer.process.stdin

    class PausedAfterWrite:
        first = True

        def write(self, value):
            count = original.write(value)
            if self.first:
                self.first = False
                assert release_writer.wait(2)
            return count

        def close(self):
            original.close()

    peer.process.stdin = PausedAfterWrite()
    result = []
    failure = []

    def submit():
        try:
            result.append(peer.request("start"))
        except BaseException as error:
            failure.append(error)

    caller = threading.Thread(target=submit, daemon=True)
    caller.start()
    try:
        assert entered.wait(1)
        # The peer can consume a complete frame before the writer returns. A
        # callback response waits for the bounded slot instead of failing it.
        assert not peer._close_complete.wait(.1), "callback_write_waits_for_capacity"
        release_writer.set()
        caller.join(timeout=1)
        assert not caller.is_alive()
        assert not failure
        assert result == [{"accepted": True}]
        assert answered.wait(1)
    finally:
        release_writer.set()
        peer.close()
        caller.join(timeout=1)
