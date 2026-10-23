# testkit: exempt the kit suite's planted daemon, a subject
#!/usr/bin/env python3
"""A daemon for the world's daemon rows. ``start SOCKET`` forks twice,
leaves its own session, serves SOCKET and prints ``pid N`` once the socket
listens; the command then exits and the daemon runs on. ``stop SOCKET``
asks it to stop: it answers, removes its socket and exits. Two flags plant
the faults: ``--deaf-stop`` answers the stop and keeps serving,
``--deaf-term`` ignores SIGTERM, so only SIGKILL ends it. Misuse refuses on
the wire, one line on stderr, exit 2."""

import os
import select
import signal
import socket
import sys

READY_TIMEOUT = 10.0
FLAGS = ("--deaf-stop", "--deaf-term")


def refuse(cls, expected, found, needed):
    sys.stderr.write(f"{cls}: expected {expected}; found {found}; needed {needed}\n")
    return 2


def serve(listener, path, deaf_stop):
    while True:
        conn, _ = listener.accept()
        with conn:
            conn.settimeout(5)
            try:
                word = conn.recv(64)
            except OSError:
                continue
            if word.strip() != b"stop":
                continue
            conn.sendall(b"ok\n")
        if not deaf_stop:
            listener.close()
            os.unlink(path)
            return


def daemonize(path, flags, ready):
    """The grandchild: no terminal, no inherited pipes, the socket bound,
    its pid written to ``ready``; it never returns into the caller."""
    try:
        null = os.open(os.devnull, os.O_RDWR)
        for fd in (0, 1, 2):
            os.dup2(null, fd)
        if "--deaf-term" in flags:
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(path)
        listener.listen(4)
        os.write(ready, str(os.getpid()).encode())
        os.close(ready)
        serve(listener, path, "--deaf-stop" in flags)
    finally:
        os._exit(0)


def start(path, flags):
    if len(os.fsencode(path)) > 100:
        return refuse("start", "a socket path of at most 100 bytes", f"{len(os.fsencode(path))} bytes",
                      "a shorter TMPDIR for the world")
    read_end, write_end = os.pipe()
    first = os.fork()
    if first == 0:
        os.close(read_end)
        os.setsid()
        if os.fork() == 0:
            daemonize(path, flags, write_end)
        os._exit(0)
    os.close(write_end)
    os.waitpid(first, 0)
    ready, _, _ = select.select([read_end], [], [], READY_TIMEOUT)
    pid = os.read(read_end, 64) if ready else b""
    if not pid.isdigit():
        return refuse("start", f"the daemon serving {path} within {READY_TIMEOUT:g}s",
                      "no pid" if not pid else repr(pid), "a writable socket path")
    sys.stdout.write(f"pid {int(pid)}\n")
    return 0


def stop(path):
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(5)
    try:
        client.connect(path)
        client.sendall(b"stop\n")
        client.recv(64)
    except OSError as exc:
        return refuse("stop", f"a daemon serving {path}", f"{type(exc).__name__}: {exc}",
                      "start the daemon first")
    finally:
        client.close()
    return 0


def main(argv):
    if len(argv) >= 2 and argv[0] == "start" and all(flag in FLAGS for flag in argv[2:]):
        return start(argv[1], argv[2:])
    if len(argv) == 2 and argv[0] == "stop":
        return stop(argv[1])
    return refuse("arguments", "start SOCKET [--deaf-stop] [--deaf-term], or stop SOCKET",
                  " ".join(argv) or "nothing", "a verb and its socket")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
