"""Controlled short writes around a real bridge peer and local stream socket."""

import json
import select
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from unittest.mock import patch

from tests.hexagonal.support.native import wait_for
from xorchestrate.adapters.bridge_transport import Peer
from xorchestrate.model import OrchestrationError


class ControlledWrites:
    """Send real bytes, then inject one uncertain write without breaking recv."""

    def __init__(self, connection):
        self.connection = connection
        self.wire = bytearray()
        self.frame_written = threading.Event()
        self.failure = None
        self.prefix_sent = False
        self.send_calls = 0
        self.closed = False

    def fileno(self):
        return self.connection.fileno()

    def send(self, data, flags=0):
        self.send_calls += 1
        if self.failure and self.prefix_sent:
            if self.failure == "error":
                raise OSError("controlled partial write")
            if self.failure == "zero":
                return 0
        sent = self.connection.send(data[:5], flags)
        self.wire.extend(data[:sent])
        if self.failure:
            self.prefix_sent = True
        if self.wire.endswith(b"\n"):
            self.frame_written.set()
        return sent

    def recv(self, maximum):
        return self.connection.recv(maximum)

    def shutdown(self, how):
        self.connection.shutdown(how)

    def close(self):
        self.closed = True
        self.connection.close()


class BridgeFrameSubject:
    error_type = OrchestrationError

    def __init__(self, connection, remote, *, timeout, maximum):
        self.connection = connection
        self.remote = remote
        self.peer = Peer(connection, lambda method, params: {},
                         {"timeout": timeout, "max_frame_bytes": maximum})
        self.pool = ThreadPoolExecutor(max_workers=2)

    @property
    def closed(self):
        return self.peer.closed

    @property
    def wire(self):
        return bytes(self.connection.wire)

    def arm_failure(self, failure):
        self.connection.failure = failure
        self.connection.prefix_sent = False

    def request(self, method, params=None):
        return self.peer.request(method, {} if params is None else params)

    def pending_request(self):
        self.connection.frame_written.clear()
        pending = self.pool.submit(self.request, "unanswered")
        if not self.connection.frame_written.wait(1):
            raise AssertionError("controlled pending request did not finish its frame")
        return pending

    def complete_request(self, method, result):
        self.connection.frame_written.clear()
        pending = self.pool.submit(self.request, method)
        if not self.connection.frame_written.wait(1):
            raise AssertionError("controlled request did not finish its frame")
        packet = json.loads(self.wire.splitlines()[-1])
        self.remote.sendall(json.dumps({"id": packet["id"], "result": result}).encode() + b"\n")
        return pending.result(1)

    def complete_late_request(self, result):
        packet = json.loads(self.wire.splitlines()[-1])
        self.remote.sendall(json.dumps({"id": packet["id"], "result": result}).encode() + b"\n")
        wait_for(lambda: not self.peer._pending, timeout=1,
                 message="late response was not joined to its original request")

    def close(self):
        self.peer.close()
        self.remote.close()
        self.pool.shutdown(wait=True, cancel_futures=True)


@contextmanager
def bridge_frame_subject(*, timeout=3, maximum=1024):
    near, far = socket.socketpair()
    connection = ControlledWrites(near)
    original_select = select.select

    def readiness(reads, writes, errors, delay=None):
        if writes == [connection] and connection.failure == "timeout" and connection.prefix_sent:
            return [], [], []
        return original_select(reads, writes, errors, delay)

    with patch("xorchestrate.adapters.bridge_transport.select.select", readiness):
        subject = BridgeFrameSubject(connection, far, timeout=timeout, maximum=maximum)
        try:
            yield subject
        finally:
            subject.close()
