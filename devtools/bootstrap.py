"""Isolated stdlib bootstrap; receive launch settings without a disk snapshot."""

import json
import os
import socket
import struct
import sys


def send(stream, value):
    data = json.dumps(value).encode()
    stream.sendall(struct.pack("!I", len(data)) + data)


def receive(stream):
    def exact(size):
        data = bytearray()
        while len(data) < size:
            chunk = stream.recv(size - len(data))
            if not chunk:
                raise RuntimeError("Launch handoff closed before completion")
            data.extend(chunk)
        return data

    size = struct.unpack("!I", exact(4))[0]
    if size > 4 * 1024 * 1024:
        raise ValueError("Launch handoff exceeds 4 MiB")
    return json.loads(exact(size))


def main():
    # Abstract Unix sockets disappear when closed, even if the launcher crashes.
    with socket.socket(socket.AF_UNIX) as stream:
        stream.settimeout(10)
        stream.connect("\0" + sys.argv[1])
        environment = receive(stream)
        # These values identify the new terminal, not the caller's older pane.
        environment.update({key: os.environ[key] for key in ("TERM", "TMUX", "TMUX_PANE") if key in os.environ})
        stream.set_inheritable(True)
        argv = [*sys.argv[2:], "--launch-fd", str(stream.fileno())]
        os.execve(argv[0], argv, environment)


if __name__ == "__main__":
    main()
