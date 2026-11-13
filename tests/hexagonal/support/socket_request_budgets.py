"""Socket-boundary witnesses with independent elapsed time and real fragmentation."""

from collections import deque
from contextlib import contextmanager
import io
import json
import os
import socket
import struct
import threading
import time

from tests.hexagonal.support.temporal_waits import ControlledClock


WELCOME = b'{"xorchestrate":"welcome"}\n'
RESULT = b'{"result":{"ok":true}}\n'


class SocketReader(io.RawIOBase):
    def __init__(self, connection):
        self.connection = connection

    def readable(self):
        return True

    def readinto(self, buffer):
        return self.connection.recv_into(buffer)


class ScriptedSocket:
    """Enforce each socket timeout while independently advancing stage latency.

    A real BufferedReader drives recv_into, so multiple fragments expose an idle
    timeout incorrectly renewed by readline without mocking the line reader.
    """

    def __init__(self, clock, *, connect=0, sends=(0,), fragments=()):
        self.clock = clock
        self.connect_delay = connect
        self.send_delays = deque(sends)
        self.fragments = deque(fragments)
        self.timeout = None
        self.sent = []
        self.operations = []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def settimeout(self, timeout):
        self.timeout = timeout

    def gettimeout(self):
        return self.timeout

    def _consume(self, operation, delay):
        if self.closed:
            raise OSError("scripted socket is closed")
        self.operations.append((operation, self.clock.elapsed, self.timeout, delay))
        if self.timeout is not None and delay > self.timeout:
            self.clock.advance(elapsed=self.timeout, wall=-50)
            raise TimeoutError("scripted socket timeout")
        self.clock.advance(elapsed=delay, wall=-50)

    def connect(self, address):
        self.address = address
        self._consume("connect", self.connect_delay)

    def sendall(self, data):
        self._consume("send", self.send_delays.popleft() if self.send_delays else 0)
        self.sent.append(bytes(data))

    def recv(self, maximum, *_):
        if not self.fragments:
            return b""
        delay, data = self.fragments.popleft()
        self._consume("receive", delay)
        if len(data) > maximum:
            self.fragments.appendleft((0, data[maximum:]))
        return data[:maximum]

    def recv_into(self, buffer, size=0, *_):
        data = self.recv(size or len(buffer))
        buffer[:len(data)] = data
        return len(data)

    def makefile(self, *_args, **_kwargs):
        return io.BufferedReader(SocketReader(self))

    def getsockopt(self, *_):
        return struct.pack("3i", os.getpid(), os.getuid(), os.getgid())

    def shutdown(self, *_):
        pass

    def close(self):
        self.closed = True


def scripted_socket(monkeypatch, **options):
    clock = ControlledClock()
    connection = ScriptedSocket(clock, **options)
    monkeypatch.setattr(time, "monotonic", clock.monotonic)
    monkeypatch.setattr(time, "time", clock.time)
    monkeypatch.setattr(socket, "socket", lambda *_args, **_kwargs: connection)
    return clock, connection


def split_response(raw, delay):
    midpoint = len(raw) // 2
    return [(delay, raw[:midpoint]), (delay, raw[midpoint:midpoint + 2]),
            (delay, raw[midpoint + 2:-1]), (delay, raw[-1:])]


def descriptor(path, *, address="/unused/gateway.sock", transport="unix", timeout=1):
    path.write_text(json.dumps({"transport": transport, "address": address,
                               "token": "budget-test-capability", "maximum": 4096, "timeout": timeout}))
    return path


class ConnectedSocket:
    """Use a real socketpair at the Unix client's already-connected boundary."""

    def __init__(self, connection):
        self.connection = connection

    def connect(self, _address):
        pass

    def __getattr__(self, name):
        return getattr(self.connection, name)


class FragmentedPeer:
    """Reply fragments arrive faster than idle timeout but outlive total budget.

    The outer watchdog is only a broken-test escape. Success/failure is asserted
    from the public request outcome; no upper elapsed-time assertion is used.
    """

    timeout = 1.0
    watchdog = 8.0

    def __init__(self, *, control=False, gateway=False):
        self.control = control
        self.gateway = gateway
        self.stop = threading.Event()
        self.done = threading.Event()
        self.sent = []
        self.requests = []
        self.errors = []

    def serve(self, connection):
        try:
            with connection, connection.makefile("rb") as stream:
                connection.settimeout(self.watchdog)
                self.requests.append(json.loads(stream.readline()))
                if self.control:
                    connection.sendall(WELCOME)
                    self.requests.append(json.loads(stream.readline()))
                raw = RESULT if self.gateway else (b'{"ok":true}\n' if self.control else WELCOME)
                # Whitespace is valid JSON. No newline arrives until 1.6 seconds
                # of traffic, each receive separated by only 0.08 seconds.
                for fragment in [b" "] * 20 + [raw]:
                    if self.stop.wait(0.08):
                        return
                    connection.sendall(fragment)
                    self.sent.append(time.monotonic())
        except (BrokenPipeError, ConnectionResetError):
            pass  # the bounded client is expected to close an unfinished reply
        except BaseException as error:
            self.errors.append(error)
        finally:
            self.done.set()


@contextmanager
def fragmented_unix(monkeypatch, *, control=False):
    client, server = socket.socketpair()
    peer = FragmentedPeer(control=control)
    thread = threading.Thread(target=peer.serve, args=(server,), daemon=True)
    monkeypatch.setattr(socket, "socket", lambda *_args, **_kwargs: ConnectedSocket(client))
    thread.start()
    try:
        yield peer
    finally:
        peer.stop.set()
        client.close()
        thread.join(peer.watchdog)
        assert not thread.is_alive(), "fragmented Unix peer did not terminate"
        assert not peer.errors, peer.errors


@contextmanager
def fragmented_gateway(path):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(FragmentedPeer.watchdog)
    peer = FragmentedPeer(gateway=True)
    config = descriptor(path, address=listener.getsockname(), transport="tcp", timeout=peer.timeout)

    def accept():
        try:
            connection, _ = listener.accept()
            peer.serve(connection)
        except BaseException as error:
            peer.errors.append(error)
            peer.done.set()

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    try:
        yield config, peer
    finally:
        peer.stop.set()
        listener.close()
        thread.join(peer.watchdog)
        assert not thread.is_alive(), "fragmented TCP peer did not terminate"
        assert not peer.errors, peer.errors
