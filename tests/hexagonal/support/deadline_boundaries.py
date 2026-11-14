"""Controlled phase costs and real pipes with independent watchdogs."""

import contextlib
import io
import os
import subprocess
import threading
import time
from types import SimpleNamespace

from scripts.acceptance.scenario import Client


def guarded_call(call, release, *, timeout=1):
    """Release blocked descriptors even when the subject misses its own deadline."""
    done = threading.Event()
    outcome = []

    def invoke():
        try:
            outcome.append(call())
        except BaseException as error:
            outcome.append(error)
        finally:
            done.set()

    thread = threading.Thread(target=invoke, daemon=True)
    thread.start()
    completed = done.wait(timeout)
    release()
    thread.join(timeout=1)
    assert not thread.is_alive(), "test pipe did not release its blocked reader/writer"
    return completed, outcome[0]


@contextlib.contextmanager
def saturated_client(timeout=.05):
    reader, writer = os.pipe()
    os.set_blocking(writer, False)
    try:
        while True:
            os.write(writer, b"x" * 4096)
    except BlockingIOError:
        pass
    os.set_blocking(writer, True)
    stream = os.fdopen(writer, "wb", buffering=0)
    client = Client.__new__(Client)
    client.process = SimpleNamespace(stdin=stream)
    client.deadline = time.monotonic() + timeout
    client.buffer, client.identifier, client.budget = b"", 0, None
    try:
        yield client, lambda: os.close(reader)
    finally:
        stream.close()


@contextlib.contextmanager
def partial_host(frame=b'{"ready":'):
    reader, writer = os.pipe()
    stream = os.fdopen(reader, "r")
    os.write(writer, frame)
    process = SimpleNamespace(stdout=stream, pid=1234, poll=lambda: None)
    try:
        yield process, lambda: os.close(writer)
    finally:
        stream.close()


@contextlib.contextmanager
def responding_client(response):
    request_reader, request_writer = os.pipe()
    response_reader, response_writer = os.pipe()
    request = os.fdopen(request_writer, "wb", buffering=0)
    reply = os.fdopen(response_reader, "rb", buffering=0)
    os.write(response_writer, response)
    client = Client.__new__(Client)
    client.process = SimpleNamespace(stdin=request, stdout=reply)
    client.deadline = time.monotonic() + 1
    client.buffer, client.identifier, client.budget = b"", 0, None
    try:
        yield client, request_reader
    finally:
        request.close()
        reply.close()
        os.close(request_reader)
        os.close(response_writer)


class PhaseChild:
    def __init__(self, clock, *, drain_expires=False):
        self.clock, self.drain_expires = clock, drain_expires
        self.timeouts = []
        self.returncode = None
        self.pid = 1234
        self.stdin, self.stdout, self.stderr = (io.BytesIO() for _ in range(3))

    def communicate(self, *, timeout, input=None):
        self.timeouts.append(("drain", timeout))
        if self.drain_expires:
            self.clock.advance(elapsed=timeout)
            raise subprocess.TimeoutExpired("controlled child", timeout, output=b"partial")
        self.returncode = 0
        return b"retained", b"diagnostic"

    def wait(self, *, timeout):
        self.timeouts.append(("reap", timeout))
        self.returncode = 0
        return 0


class PhaseGroup:
    known = {1}
    unknown = set()

    def __init__(self, clock, *, signal_cost):
        self.clock, self.signal_cost = clock, signal_cost

    def signal(self, number, *, owner_only=False):
        self.clock.advance(elapsed=self.signal_cost)

    def observe(self):
        return set()


class QuietTracker:
    known = set()

    def children(self):
        return set()

    def hand_to_host(self, before):
        pass

    def observe(self):
        return set()

    def reap(self):
        pass

    def release_host(self):
        pass


class CostedHost:
    name = "controlled-host"
    interactive = False

    def __init__(self, clock, *, observation_cost, shutdown_cost=0):
        self.clock, self.observation_cost, self.shutdown_cost = clock, observation_cost, shutdown_cost
        self.observations, self.graces = 0, []
        self.terminal = SimpleNamespace(name="acceptance", running=False, exit_status=0, receipt={})

    def open_terminal(self, *args, **kwargs):
        return self.terminal

    def terminals(self):
        self.observations += 1
        if self.observations > 1:
            self.clock.advance(elapsed=self.observation_cost)
        return [self.terminal]

    def recent_output(self, *args):
        return ""

    def close_terminal(self, name, *, grace):
        self.graces.append(grace)
        return SimpleNamespace(cleanup_verified=True)

    def shutdown(self):
        self.clock.advance(elapsed=self.shutdown_cost)
