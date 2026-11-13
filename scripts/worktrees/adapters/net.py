"""Local TCP ports."""

from __future__ import annotations

import socket


def free_port() -> int:
    """A port the kernel reports free on 127.0.0.1 right now; the caller retries if it is taken meanwhile."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]
