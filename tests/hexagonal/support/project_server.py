"""Subjects and bounded peers for the project server and its client transports.

In-process servers use the production dispatcher, server and transports over
real pipes and Unix sockets; CLI peers launch the real entrypoints. Every read
has a deadline so a regression fails instead of hanging.
"""

import json
import os
import selectors
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from xorchestrate.adapters.local_socket import same_user
from xorchestrate.adapters.unix import UnixClient, UnixClientTransport
from xorchestrate.mcp import MCPDispatcher, serve
from xorchestrate.server import ProjectServer

ROOT = Path(__file__).resolve().parents[3]
SESSION_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "session_server.py"
DEADLINE = 15


def encode(frame):
    return frame if isinstance(frame, bytes) else json.dumps(frame).encode()


@contextmanager
def short_directory():
    """A private directory whose paths fit ``sun_path`` (pytest's tmp_path may not)."""
    directory = Path(tempfile.mkdtemp(prefix="xo-"))
    try:
        yield directory
    finally:
        shutil.rmtree(directory, ignore_errors=True)


class PipeChannel:
    """Newline-delimited JSON over a writable file object and a readable descriptor."""

    def __init__(self, writer, read_fd):
        self.writer = writer
        self.read_fd = read_fd
        self.pending = b""
        self.selector = selectors.DefaultSelector()
        self.selector.register(read_fd, selectors.EVENT_READ)

    def send(self, frame):
        self.writer.write(encode(frame) + b"\n")
        self.writer.flush()

    def read(self, timeout=DEADLINE):
        deadline = time.monotonic() + timeout
        while b"\n" not in self.pending:
            remaining = deadline - time.monotonic()
            assert remaining > 0 and self.selector.select(remaining), "no complete reply before the deadline"
            chunk = os.read(self.read_fd, 65536)
            if not chunk:
                return None
            self.pending += chunk
        line, self.pending = self.pending.split(b"\n", 1)
        return json.loads(line)

    def request(self, frame):
        self.send(frame)
        return self.read()

    def close_input(self):
        self.writer.close()

    def close(self):
        self.selector.close()
        if not self.writer.closed:
            self.writer.close()


class SocketChannel:
    """A connected ``UnixClient`` with bounded reads."""

    def __init__(self, client):
        self.client = client
        client.socket.settimeout(DEADLINE)

    def send(self, frame):
        self.client.socket.sendall(encode(frame) + b"\n")

    def read(self, timeout=DEADLINE):
        self.client.socket.settimeout(timeout)
        line = self.client.reader.readline()
        return json.loads(line) if line else None

    def request(self, frame):
        self.send(frame)
        return self.read()

    def close_input(self):
        self.client.socket.shutdown(socket.SHUT_WR)

    def close(self):
        self.client.close()


class LargeReplies:
    """An application double whose every call returns a reply larger than socket buffers."""

    def call(self, name, arguments, actor):
        return {"blob": "x" * 400_000}

    def describe(self):
        return []

    def live_sessions(self):
        return []


class HeldApplication:
    """An application double whose ``held`` call blocks until released."""

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.live = []
        self.calls = []

    def call(self, name, arguments, actor):
        self.calls.append((name, actor))
        if name == "held":
            self.entered.set()
            assert self.release.wait(DEADLINE)
        return {"name": name, "actor": actor}

    def describe(self):
        return []

    def live_sessions(self):
        return list(self.live)


class InProcessServer:
    """A production ``ProjectServer`` on an owner-only socket, served from a thread."""

    def __init__(self, application, socket_path, *, check=same_user, max_frame_bytes=1_048_576,
                 request_workers=8, max_pending_requests=64, control_workers=2, max_connections=64,
                 handshake_timeout=5.0, write_timeout=30.0):
        self.socket_path = Path(socket_path)
        self.dispatcher = MCPDispatcher(application, request_workers, max_pending_requests, control_workers)
        self.transport = UnixClientTransport(socket_path, max_frame_bytes=max_frame_bytes, check=check,
                                             handshake_timeout=handshake_timeout, write_timeout=write_timeout)
        self.server = ProjectServer(application, self.transport, self.dispatcher, poll_interval=0.05,
                                    max_connections=max_connections)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.channels = []

    def client(self, actor="owner", purpose="mcp", max_frame_bytes=1_048_576):
        channel = SocketChannel(UnixClient(self.socket_path, actor=actor, purpose=purpose, max_frame_bytes=max_frame_bytes))
        self.channels.append(channel)
        return channel

    def wait_connections(self, count, timeout=DEADLINE):
        deadline = time.monotonic() + timeout
        while self.server.status()["connections"] != count:
            assert time.monotonic() < deadline, "server did not reach the expected connection count"
            time.sleep(0.02)

    def stop(self):
        for channel in self.channels:
            channel.close()
        if not self.server.stopped:
            # Bounded: a regression that never drains fails here instead of hanging.
            threading.Thread(target=self.server.down, kwargs={"detach": True}, daemon=True).start()
        self.thread.join(DEADLINE)
        assert not self.thread.is_alive(), "server did not stop within the deadline"
        self.dispatcher.close()


class StdioServer:
    """``serve`` over real pipes, as the CLI runs it, in a thread."""

    def __init__(self, application, *, actor="owner", max_frame_bytes=1_048_576):
        to_server_read, to_server_write = os.pipe()
        from_server_read, from_server_write = os.pipe()
        self.source = os.fdopen(to_server_read, "rb", buffering=0)
        self.target = os.fdopen(from_server_write, "w", encoding="utf-8")
        self.errors = []
        self.thread = threading.Thread(target=self._run, args=(application, actor, max_frame_bytes), daemon=True)
        self.thread.start()
        self.channel = PipeChannel(os.fdopen(to_server_write, "wb", buffering=0), from_server_read)

    def _run(self, application, actor, max_frame_bytes):
        try:
            serve(application, self.source, self.target, actor, max_frame_bytes)
        except Exception as exc:
            self.errors.append(exc)
        finally:
            self.target.close()

    def stop(self):
        self.channel.close()
        self.thread.join(DEADLINE)
        assert not self.thread.is_alive() and not self.errors, self.errors
        self.source.close()
        os.close(self.channel.read_fd)


class CliEnvironment:
    """Run the real CLI with a private xorchestrate home and configuration.

    Only ``XORCHESTRATE_HOME`` isolates the test; runtime-directory variables
    are removed so they cannot mask how servers are discovered.
    """

    def __init__(self, directory, options=None):
        self.directory = Path(directory)
        self.config = self.directory / "config.json"
        self.home = self.directory / "home"
        self.options = options if options is not None else {"store": {"adapter": "sqlite", "options": {"path": "state.sqlite3"}}}
        self.config.write_text(json.dumps(self.options), encoding="utf-8")
        inherited = {key: value for key, value in os.environ.items()
                     if key not in ("XORCHESTRATE_RUNTIME_DIR", "XDG_RUNTIME_DIR")}
        self.environment = {**inherited, "XORCHESTRATE_HOME": str(self.home),
                            "PYTHONPATH": os.pathsep.join([str(ROOT), os.environ.get("PYTHONPATH", "")])}
        self.processes = []

    def command(self, *arguments, frame_limit=None):
        command = [sys.executable, "-m", "xorchestrate", "--config", str(self.config)]
        if frame_limit is not None:
            command += ["--max-frame-bytes", str(frame_limit)]
        return command + list(arguments)

    def run(self, *arguments, timeout=60, environment=None):
        return subprocess.run(self.command(*arguments), capture_output=True, cwd=self.directory,
                              env=environment or self.environment, timeout=timeout)

    def json(self, *arguments, expect=0, environment=None):
        result = self.run(*arguments, environment=environment)
        assert result.returncode == expect, (result.returncode, result.stdout, result.stderr)
        return json.loads(result.stdout if expect in (0, 3) else result.stderr)

    def spawn(self, *arguments, frame_limit=None):
        process = subprocess.Popen(self.command(*arguments, frame_limit=frame_limit), stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=self.directory,
                                   env=self.environment, bufsize=0)
        channel = PipeChannel(process.stdin, process.stdout.fileno())
        self.processes.append((process, channel))
        return process, channel

    def finish(self, process, channel):
        """Close the client's input and return its exit code, remaining stdout and stderr."""
        channel.close_input()
        stdout, stderr = process.communicate(timeout=DEADLINE)
        return process.returncode, channel.pending + stdout, stderr

    def server_log(self):
        logs = list((self.home / "servers").glob("*.log"))
        return "".join(path.read_text(encoding="utf-8") for path in logs)

    def cleanup(self):
        for process, channel in self.processes:
            channel.close()
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=DEADLINE)
        if self.run("status").returncode == 0:
            self.run("down", "--detach")
