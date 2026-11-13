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
def partial_host():
    reader, writer = os.pipe()
    stream = os.fdopen(reader, "r")
    os.write(writer, b'{"ready":')
    process = SimpleNamespace(stdout=stream, pid=1234, poll=lambda: None)
    try:
        yield process, lambda: os.close(writer)
    finally:
        stream.close()


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
