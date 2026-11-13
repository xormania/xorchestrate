"""Bounded framing over exclusively owned pipes, using one supplied deadline.

The descriptor is nonblocking while accessed here: readiness alone cannot make
a buffered write or readline obey the caller's remaining budget. These helpers
do not close streams, run background readers, or cancel remote operations.
"""

import os
import selectors
from contextlib import contextmanager


class FrameTooLarge(ValueError):
    """A frame exceeded its configured byte bound before decoding."""


@contextmanager
def _ready(fd, event):
    blocking = os.get_blocking(fd)
    os.set_blocking(fd, False)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(fd, event)
            yield selector
    finally:
        os.set_blocking(fd, blocking)


def _wait(selector, deadline):
    remaining = deadline.remaining()
    if remaining <= 0 or not selector.select(remaining):
        raise TimeoutError("stream deadline expired")


def write_bytes(stream, payload, deadline):
    """Send all bytes within the remaining allowance, including partial writes."""
    fd, pending = stream.fileno(), memoryview(payload)
    with _ready(fd, selectors.EVENT_WRITE) as selector:
        while pending:
            if deadline.remaining() <= 0:
                raise TimeoutError("stream deadline expired")
            try:
                written = os.write(fd, pending)
            except BlockingIOError:
                _wait(selector, deadline)
                continue
            if written == 0:
                raise BrokenPipeError("stream closed during write")
            pending = pending[written:]


def read_line(stream, pending, deadline, *, limit):
    """Return one complete line and its retained suffix; partial lines stay bounded."""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError("frame limit must be a positive integer")
    if len(pending) > limit:
        raise FrameTooLarge("stream frame exceeds byte limit")
    fd = stream.fileno() if hasattr(stream, "fileno") else stream
    with _ready(fd, selectors.EVENT_READ) as selector:
        while b"\n" not in pending:
            _wait(selector, deadline)
            if deadline.remaining() <= 0:
                raise TimeoutError("stream deadline expired")
            try:
                chunk = os.read(fd, min(65536, limit + 1 - len(pending)))
            except BlockingIOError:
                continue
            if not chunk:
                raise EOFError("stream ended before a complete frame")
            pending += chunk
            if len(pending) > limit:
                raise FrameTooLarge("stream frame exceeds byte limit")
    line, suffix = pending.split(b"\n", 1)
    return line, suffix
