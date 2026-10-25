"""The protocol channel to one native harness process: its pipes, one
reader thread, the framing parser and one writer lock.

Contract: ops/conductor/sessions-lever.md, "The adapter boundary,
extended": `rpc` is this module. Adapted from the proof's asyncio `RPC`
(Source: /home/scientdb/projects/poc/multi-harness-proof@a944f31,
mhproof/rpc.py `RPC.request`, `RPC.notify`, `RPC.read`, `RPC.close`),
made synchronous with a thread, so the bridge can hold no event loop and
no SQLite transaction across a native call. `RPC(reader, writer)` speaks
over a stream pair it is handed, as the proof's did; `Channel(argv)`
spawns the harness and is an `RPC` over the process's pipes. Every received line goes
through `framing.Parser`; a line it refuses ends the channel with that
reading, as the proof's reader ended on a non-JSON line. One refusal does
not: a response whose id matches no request this side sent is dropped,
kept in `strays` and written as one line to the log. grok 1.0.40 sends
one on its own (`"id":"skills-reload"`) after its tool calls, and ending
the channel on it lost the turn's later frames.

A request the other side sends and a notification are not answered here:
they wait on `frames` for the adapter's `observe`, which answers a
request through `answer`. Nothing on this channel reads the store.

Stdlib only.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import queue
import subprocess
import threading
from pathlib import Path

REQUEST_TIMEOUT = 120.0
CLOSE_WAIT = 5.0


def _framing():
    """framing.py beside this file, by path: the adapters are loaded by
    path, never as a package, so a staged copy of the directory works."""
    path = Path(__file__).with_name("framing.py")
    spec = importlib.util.spec_from_file_location("session_framing", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


framing = _framing()


class ChannelError(Exception):
    """A request that failed on the channel: the method, the reading, and the
    JSON-RPC error code when the other side answered with one."""

    def __init__(self, method: str, reading: str, code=None):
        self.method = method
        self.reading = reading
        self.code = code
        super().__init__(f"{method}: {reading}")


class Pending:
    """One request this side sent, answered or not yet."""

    def __init__(self, ident, method: str):
        self.ident = ident
        self.method = method
        self._done = threading.Event()
        self.result = None
        self.error = None

    def settle(self, *, result=None, error=None) -> None:
        self.result, self.error = result, error
        self._done.set()

    def done(self) -> bool:
        return self._done.is_set()

    def wait(self, timeout: float | None = None):
        """The result, or ChannelError: the other side's error, the channel's
        death, or no answer within `timeout`."""
        if not self._done.wait(timeout):
            raise ChannelError(self.method, f"no answer within {timeout:g}s")
        if self.error is not None:
            code = self.error.get("code") if isinstance(self.error, dict) else None
            message = (self.error.get("message") if isinstance(self.error, dict)
                       else str(self.error))
            raise ChannelError(self.method, f"error {code}: {message}", code)
        return self.result if isinstance(self.result, dict) else {}


class RPC:
    """Newline-delimited JSON over a reader and a writer: pipes or a socket's
    files, in text or binary mode.

    `jsonrpc` says whether frames carry `"jsonrpc": "2.0"`: grok's ACP does,
    codex's app-server does not.
    """

    def __init__(self, reader, writer, *, jsonrpc: bool = True, log=None):
        self.jsonrpc = jsonrpc
        self.reader = reader
        self.writer = writer
        self.log = log
        self.strays: list = []
        self._text = isinstance(writer, io.TextIOBase)
        self.parser = framing.Parser()
        self.frames: queue.Queue = queue.Queue()
        self.dead = threading.Event()
        self.failure: str | None = None
        self._write = threading.Lock()
        self._book = threading.Lock()
        self._next = 0
        self._pending: dict = {}
        self._reader = threading.Thread(target=self._read, name="channel-reader", daemon=True)
        self._reader.start()

    def packet(self, **fields) -> dict:
        return {**({"jsonrpc": "2.0"} if self.jsonrpc else {}), **fields}

    def _send_line(self, packet: dict) -> None:
        if self.dead.is_set():
            raise ChannelError(str(packet.get("method", "write")),
                               self.failure or "the channel is closed")
        line = json.dumps(packet) + "\n"
        try:
            self.writer.write(line if self._text else line.encode("utf-8"))
            self.writer.flush()
        except (BrokenPipeError, OSError, ValueError) as error:
            raise ChannelError(str(packet.get("method", "write")),
                               f"write failed: {error}") from None

    def send(self, method: str, params=None) -> Pending:
        """Write one request; the answer arrives on the returned Pending."""
        with self._write:
            with self._book:
                self._next += 1
                ident = self._next
                pending = Pending(ident, method)
                self._pending[ident] = pending
                self.parser.sent(ident)
            try:
                self._send_line(self.packet(id=ident, method=method, params=params or {}))
            except ChannelError:
                with self._book:
                    self._pending.pop(ident, None)
                raise
        return pending

    def request(self, method: str, params=None, timeout: float = REQUEST_TIMEOUT) -> dict:
        return self.send(method, params).wait(timeout)

    def notify(self, method: str, params=None) -> None:
        with self._write:
            self._send_line(self.packet(method=method, params=params or {}))

    def answer(self, frame: dict, *, result=None, error=None) -> None:
        """Answer a request the other side sent; a closed channel is not an
        error here, as in the proof: the answer has nowhere to go."""
        ident = frame.get("id")
        body = {"error": error} if error is not None else {"result": result or {}}
        with self._write:
            with contextlib.suppress(ChannelError):
                self._send_line(self.packet(id=ident, **body))
            with self._book:
                self.parser.answered(ident)

    def next_frame(self, timeout: float | None = None):
        """The next notification or request the other side sent, or None."""
        try:
            return self.frames.get(timeout=timeout)
        except queue.Empty:
            return None

    def note(self, reading: str) -> None:
        """Keep a dropped frame's reading, and write it to the log if any."""
        self.strays.append(reading)
        if self.log is None:
            return
        line = f"channel: dropped {reading}\n"
        with contextlib.suppress(OSError, ValueError):
            self.log.write(line if isinstance(self.log, io.TextIOBase) else line.encode("utf-8"))
            self.log.flush()

    def _read(self) -> None:
        try:
            for raw in self.reader:
                text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
                line = text.strip()
                if not line:
                    continue
                with self._book:
                    try:
                        frame = self.parser.feed(line)
                    except framing.Refused as refused:
                        if not refused.stray:
                            raise
                        frame = None
                        stray = refused.reading
                    pending = (None if frame is None or "method" in frame
                               else self._pending.pop(frame.get("id"), None))
                if frame is None:
                    self.note(stray)
                    continue
                if pending is None:
                    self.frames.put(frame)
                elif "error" in frame:
                    pending.settle(error=frame["error"])
                else:
                    pending.settle(result=frame.get("result", {}))
        except framing.Refused as refused:
            self.failure = refused.reading
        except (OSError, ValueError) as error:
            self.failure = f"read failed: {error}"
        finally:
            self.failure = self.failure or "the harness closed its stdout"
            self.dead.set()
            with self._book:
                left, self._pending = list(self._pending.values()), {}
            for pending in left:
                pending.settle(error={"code": None, "message": self.failure})

    def close(self):
        """Close the writer: the other side reads its end of input."""
        with contextlib.suppress(OSError, ValueError):
            self.writer.close()


class Channel(RPC):
    """A harness process spoken to over its stdin and stdout."""

    def __init__(self, argv, *, jsonrpc: bool = True, env=None, cwd=None, stderr=None):
        self.cwd = cwd
        self.process = subprocess.Popen(  # noqa: S603 - argv is a list, never a shell string
            [str(part) for part in argv], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=stderr if stderr is not None else subprocess.DEVNULL,
            env=env, cwd=cwd,
        )
        log = stderr if hasattr(stderr, "write") else None
        super().__init__(self.process.stdout, self.process.stdin, jsonrpc=jsonrpc, log=log)

    @property
    def pid(self) -> int:
        return self.process.pid

    def close(self) -> int | None:
        """Close stdin, then terminate and kill if the process stays."""
        super().close()
        try:
            return self.process.wait(CLOSE_WAIT)
        except subprocess.TimeoutExpired:
            self.process.terminate()
        try:
            return self.process.wait(CLOSE_WAIT)
        except subprocess.TimeoutExpired:
            self.process.kill()
            return self.process.wait(CLOSE_WAIT)
