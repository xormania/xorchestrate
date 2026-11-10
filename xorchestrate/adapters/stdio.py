"""``stdio`` client transport: one connection over a process's own streams.

This is the transport of ``serve`` without ``--transport``: the launching
process is the only client, and its actor is the one chosen on the command line.
"""
from __future__ import annotations

import io
import os
import threading

from ..model import OrchestrationError
from ..ports import PeerIdentity


def read_line_frame(stream, maximum):
    """Read one nonblank newline-delimited frame of at most ``maximum`` bytes.

    Returns the frame without its line ending, or ``None`` at end of input. An
    oversized frame is consumed through its newline and reported as
    ``frame_too_large`` so the next frame can still be read.
    """
    while True:
        line = stream.readline(maximum + 1)
        if not line:
            return None
        if len(line) > maximum:
            while line and not line.endswith(b"\n"):
                line = stream.readline(maximum + 1)
            raise OrchestrationError("Input frame exceeds configured limit.", "frame_too_large")
        if line.strip():
            return line.rstrip(b"\r\n")


class StdioConnection:
    """A ``ClientConnection`` over a binary input and a text or binary output stream."""

    def __init__(self, stdin, stdout, actor="owner", max_frame_bytes=1_048_576):
        self.stdin = stdin
        self.stdout = stdout
        self.max_frame_bytes = max_frame_bytes
        self.peer = PeerIdentity(uid=os.getuid(), pid=None, actor=actor)
        self._binary = isinstance(stdout, (io.RawIOBase, io.BufferedIOBase))
        self._write_lock = threading.Lock()
        self._closed = False

    def read_frame(self):
        if self._closed:
            return None
        return read_line_frame(self.stdin, self.max_frame_bytes)

    def write_frame(self, frame):
        with self._write_lock:
            if self._binary:
                self.stdout.write(frame + b"\n")
            else:
                self.stdout.write(frame.decode("utf-8") + "\n")
            self.stdout.flush()

    def close(self):
        # The process owns its standard streams; ending the connection stops reads only.
        self._closed = True


class StdioTransport:
    """A ``ClientTransport`` that yields exactly one ``StdioConnection``."""

    def __init__(self, stdin, stdout, actor="owner", max_frame_bytes=1_048_576):
        self._connection = StdioConnection(stdin, stdout, actor, max_frame_bytes)

    def accept(self, timeout=None):
        connection, self._connection = self._connection, None
        return connection

    def close(self):
        self._connection = None
