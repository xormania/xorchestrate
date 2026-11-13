"""Temporary repositories and controlled effects for the optional work-tree tool's tests.

Real git runs against throwaway repositories with a bare origin. Everything else
the tool touches is replaced: a fake process table, a fake /proc directory, a
fake launcher that answers on the requested port with a stdlib MCP endpoint, and
a fake clock through which all waiting passes. No real Serena, no network beyond
127.0.0.1, and HOME points into the test's own directory.

The tool is imported when a test asks for it, so a missing module fails that test
instead of breaking collection.
"""

import contextlib
import dataclasses
import importlib
import io
import json
import os
import shutil
import signal
import socket
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tests.hexagonal.support.git_repositories import (  # noqa: F401  (re-exported for the tests)
    DEFAULT_BRANCH,
    FILES,
    REPOSITORY_NAME,
    commit,
    git,
    isolate,
    make_repository,
)

PROBE_FILE = "pkg/b_model.py"
"""The first sorted tracked Python file with a top-level class or function in ``FILES``."""

STORE_SUBDIR = "servers"
"""Where the tool keeps server state inside its state directory (confirmed with the implementer)."""



def tool(name=""):
    return importlib.import_module("scripts.worktrees" + ("." + name if name else ""))


IDENTITY_FIELDS = frozenset({"argv", "start_time"})
"""Fields that identify a recorded server's process (agreed with the implementer): the spawned argv and the
process start time (``/proc/<pid>/stat`` field 22). Before the tool records them they are left out, so an
identity test fails on its own assertion instead of a constructor error."""


def build(cls, **values):
    """``cls(**values)``, leaving out identity fields that ``cls`` does not declare yet."""
    declared = {item.name for item in dataclasses.fields(cls)}
    return cls(**{name: value for name, value in values.items()
                  if name in declared or name not in IDENTITY_FIELDS})


def server_argv(tree, port):
    return ["serena", "start-mcp-server", "--project", str(tree), "--port", str(port)]


class RecordingGit:
    """The tool's real git adapter, with every argument list kept for inspection.

    ``fail`` maps an argument prefix to stderr text: a matching call raises the tool's GitError
    without running. ``after`` maps a prefix to a callable run after a matching call succeeded.
    """

    def __init__(self, events):
        self.inner = tool("adapters.git").SubprocessGit()
        self.calls = []
        self.events = events
        self.fail = {}
        self.after = {}

    @staticmethod
    def _matches(args, prefix):
        return [str(word) for word in args[:len(prefix)]] == list(prefix)

    def run(self, args, cwd):
        self.calls.append(list(args))
        self.events.append(("git", *map(str, args)))
        for prefix, stderr in self.fail.items():
            if self._matches(args, prefix):
                raise tool("ports").GitError([str(arg) for arg in args], 128, stderr)
        output = self.inner.run(args, cwd)
        for prefix, hook in self.after.items():
            if self._matches(args, prefix):
                hook()
        return output


class FakeClock:
    """Starts at the real time, so file modification times and ages agree; only ``sleep`` moves it."""

    def __init__(self, start=None):
        self.time = time.time() if start is None else start
        self.sleeps = []

    def now(self):
        return self.time

    def sleep(self, seconds):
        assert seconds >= 0
        self.sleeps.append(seconds)
        self.time += seconds


class FakeProcessTable:
    """Processes the tests declare; signals only ever reach these records.

    ``ignore_term``: pids that survive SIGTERM. ``zombie_on_term``: pids that become zombies on SIGTERM,
    still listed (as /proc lists them) but not alive. ``linger``: pid to seconds on the clock a pid
    takes to die after SIGKILL; ``ignore_kill``: pids that never die.
    """

    def __init__(self, events, clock=None):
        self.events = events
        self.clock = clock
        self.procs = {}
        self.signals = []
        self.ignore_term = set()
        self.zombie_on_term = set()
        self.zombies = set()
        self.linger = {}
        self.ignore_kill = set()
        self.dying = {}
        self.on_exit = {}

    def add(self, pid, *, ppid=1, pgid=None, cwd="/", argv=(), open_paths=(), start_time=None):
        self.procs[pid] = build(tool("model").Proc, pid=pid, ppid=ppid, pgid=pid if pgid is None else pgid,
                                cwd=str(cwd), argv=tuple(map(str, argv)), open_paths=tuple(map(str, open_paths)),
                                start_time=start_time)
        self.zombies.discard(pid)
        return self.procs[pid]

    def kill(self, pid):
        self.zombies.discard(pid)
        self.dying.pop(pid, None)
        if self.procs.pop(pid, None) is not None and pid in self.on_exit:
            self.on_exit.pop(pid)()

    def _settle(self):
        if self.clock is None:
            return
        for pid, when in list(self.dying.items()):
            if self.clock.now() >= when:
                self.kill(pid)

    def processes(self):
        self._settle()
        return list(self.procs.values())

    def signal_group(self, pgid, sig):
        self.signals.append((pgid, sig))
        self.events.append(("signal", pgid, sig))
        for proc in [proc for proc in self.procs.values() if proc.pgid == pgid]:
            if proc.pid in self.zombies:
                continue
            if sig == signal.SIGTERM and proc.pid in self.ignore_term:
                continue
            if sig == signal.SIGTERM and proc.pid in self.zombie_on_term:
                self.zombies.add(proc.pid)
                continue
            if sig == signal.SIGKILL and proc.pid in self.ignore_kill:
                continue
            if sig == signal.SIGKILL and proc.pid in self.linger:
                self.dying.setdefault(proc.pid, self.clock.now() + self.linger[proc.pid])
                continue
            self.kill(proc.pid)

    def alive(self, pid):
        self._settle()
        return pid in self.procs and pid not in self.zombies

    def groups_signalled(self, sig=None):
        return [pgid for pgid, sent in self.signals if sig is None or sent == sig]


class FakeGithub:
    """Merged pull-request heads by branch; ``raises`` simulates no gh, no network or an API error.

    ``heads[branch]`` is a head sha (a pull request merged into whatever base is asked about) or a
    ``(sha, base)`` pair (merged into ``base`` only). ``calls`` records ``(branch, base)``.
    """

    def __init__(self):
        self.heads = {}
        self.raises = False
        self.calls = []
        self.on_call = {}

    def merged_pr_head(self, branch, base=None):
        self.calls.append((branch, base))
        if branch in self.on_call:
            self.on_call.pop(branch)()
        if self.raises:
            raise OSError("gh: network unreachable")
        entry = self.heads.get(branch)
        if isinstance(entry, tuple):
            sha, into = entry
            return sha if into == base else None
        return entry


class ZeroDisk:
    """Disk usage that walks nothing, for tests whose roots must never be measured."""

    def size(self, path):
        return 0


@dataclass
class McpBehaviour:
    """How every fake MCP endpoint answers; tests change it between calls."""
    mode: str = "json"
    probe: str = "ok"
    tools: tuple = ("find_symbol", "get_symbols_overview", "read_file")
    call_error: bool = False
    rpc_error: bool = False
    requests: list = field(default_factory=list)

    def answer(self, name, arguments):
        if self.rpc_error:
            return "error", {"code": -32602, "message": "unknown tool"}
        if name == "get_symbols_overview" and self.probe != "ok":
            content = [] if self.probe == "empty" else [{"type": "text", "text": "language server not ready"}]
            return "result", {"content": content, "isError": self.probe == "error"}
        if self.call_error:
            return "result", {"content": [{"type": "text", "text": "tool failed"}], "isError": True}
        return "result", {"content": [{"type": "text", "text": json.dumps({"tool": name, "arguments": arguments})}],
                          "isError": False}


class FakeMcpServer:
    """A stdlib streamable-HTTP MCP endpoint at http://127.0.0.1:<port>/mcp."""

    def __init__(self, port, behaviour):
        self.behaviour = behaviour
        self.sessions = set()
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _record(self, body):
                behaviour.requests.append({"http": self.command, "path": self.path, "port": port,
                                           "session": self.headers.get("Mcp-Session-Id"), "body": body})

            def _send(self, status, payload=None, headers=()):
                data = b""
                if payload is not None:
                    text = json.dumps(payload)
                    if behaviour.mode == "sse":
                        data = f"event: message\ndata: {text}\n\n".encode()
                        content_type = "text/event-stream"
                    else:
                        data = text.encode()
                        content_type = "application/json"
                self.send_response(status)
                for name, value in headers:
                    self.send_header(name, value)
                if payload is not None:
                    self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"null")
                self._record(body)
                method = (body or {}).get("method")
                if self.path != "/mcp":
                    return self._send(404)
                if method == "initialize":
                    session = uuid.uuid4().hex
                    server.sessions.add(session)
                    return self._send(200, {"jsonrpc": "2.0", "id": body["id"], "result": {
                        "protocolVersion": body["params"]["protocolVersion"], "capabilities": {"tools": {}},
                        "serverInfo": {"name": "fake", "version": "0"}}}, [("Mcp-Session-Id", session)])
                if self.headers.get("Mcp-Session-Id") not in server.sessions:
                    return self._send(400)
                if method == "notifications/initialized":
                    return self._send(202)
                if method == "tools/list":
                    return self._send(200, {"jsonrpc": "2.0", "id": body["id"], "result": {
                        "tools": [{"name": name, "inputSchema": {"type": "object"}} for name in behaviour.tools]}})
                if method == "tools/call":
                    kind, value = behaviour.answer(body["params"]["name"], body["params"].get("arguments", {}))
                    return self._send(200, {"jsonrpc": "2.0", "id": body["id"], kind: value})
                return self._send(200, {"jsonrpc": "2.0", "id": body.get("id"),
                                        "error": {"code": -32601, "message": "no such method"}})

            def do_DELETE(self):
                self._record(None)
                server.sessions.discard(self.headers.get("Mcp-Session-Id"))
                self._send(200)

        self.stopped = False
        self.http = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        if self.stopped:
            return
        self.stopped = True
        self.http.shutdown()
        self.http.server_close()


class RawEndpoint:
    """A local TCP listener on ``port`` that misbehaves as an MCP endpoint.

    ``hang``: accepts (through the kernel backlog) and never replies. ``reply``: reads one request and
    sends ``reply`` bytes verbatim, then closes, whatever it promised.
    """

    def __init__(self, port, reply=None):
        self.reply = reply
        self.listener = socket.create_server(("127.0.0.1", port), backlog=16)
        self.held = []
        self.stopped = False
        if reply is not None:
            self.thread = threading.Thread(target=self._serve, daemon=True)
            self.thread.start()

    def _serve(self):
        while not self.stopped:
            try:
                connection, _ = self.listener.accept()
            except OSError:
                return
            with contextlib.suppress(OSError):
                connection.settimeout(5)
                data = b""
                while b"\r\n\r\n" not in data:
                    chunk = connection.recv(65536)
                    if not chunk:
                        break
                    data += chunk
                connection.sendall(self.reply)
            with contextlib.suppress(OSError):
                connection.close()

    def stop(self):
        if self.stopped:
            return
        self.stopped = True
        with contextlib.suppress(OSError):
            self.listener.close()


def truncated_reply(sent=10, promised=100):
    """HTTP headers promising ``promised`` body bytes, then only ``sent`` of them."""
    return (f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {promised}\r\n"
            "Connection: close\r\n\r\n").encode() + b"{" * sent


def json_reply(payload):
    """A complete HTTP response whose JSON body is ``payload`` (any JSON value)."""
    body = json.dumps(payload).encode()
    return (f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n"
            "Mcp-Session-Id: raw\r\nConnection: close\r\n\r\n").encode() + body


ADDRESS_IN_USE = "ERROR: [Errno 98] error while attempting to bind on address ('127.0.0.1', {port}): address already in use"
CRASH_TEXT = "Traceback (most recent call last):\nRuntimeError: planted language server crash"


class FakeLauncher:
    """Starts nothing real. Each spawn follows the next scripted behaviour (default ``serve``):

    ``serve`` answers MCP on the argv's port; ``address_in_use`` and ``exit`` log and die at once;
    ``silent`` stays alive and never answers; ``missing`` raises FileNotFoundError; ``hang`` listens
    on the port and never replies; ``raw`` answers every request with ``raw_reply`` bytes.
    ``on_spawn`` (optional) is called with the argv before anything starts.
    Each spawned process gets the start time ``START_TIME_BASE + pid``.
    """

    START_TIME_BASE = 500_000

    def __init__(self, processes, behaviour):
        self.processes = processes
        self.behaviour = behaviour
        self.script = []
        self.spawns = []
        self.servers = {}
        self.next_pid = 40_000
        self.raw_reply = b""
        self.on_spawn = None
        self.lock = threading.Lock()

    def spawn(self, argv, cwd, env, log):
        argv = [str(word) for word in argv]
        if self.on_spawn is not None:
            self.on_spawn(argv)
        with self.lock:
            self.spawns.append({"argv": argv, "cwd": str(cwd), "env": dict(env), "log": str(log)})
            behaviour = self.script.pop(0) if self.script else "serve"
            if behaviour == "missing":
                raise FileNotFoundError(argv[0])
            pid, self.next_pid = self.next_pid, self.next_pid + 10
        port = int(argv[argv.index("--port") + 1])
        log = Path(log)
        log.parent.mkdir(parents=True, exist_ok=True)
        self.processes.add(pid, ppid=1, pgid=pid, cwd=cwd, argv=argv, start_time=self.START_TIME_BASE + pid)
        if behaviour in ("hang", "raw"):
            self.servers[pid] = RawEndpoint(port, None if behaviour == "hang" else self.raw_reply)
            self.processes.on_exit[pid] = self.servers[pid].stop
            log.write_text("serving\n", encoding="utf-8")
        elif behaviour == "serve":
            self.servers[pid] = FakeMcpServer(port, self.behaviour)
            self.processes.on_exit[pid] = self.servers[pid].stop
            log.write_text("serving\n", encoding="utf-8")
        elif behaviour == "address_in_use":
            log.write_text("starting\n" + ADDRESS_IN_USE.format(port=port) + "\n", encoding="utf-8")
            self.processes.kill(pid)
        elif behaviour == "exit":
            log.write_text("starting\n" + CRASH_TEXT + "\n", encoding="utf-8")
            self.processes.kill(pid)
        elif behaviour == "silent":
            log.write_text("starting\n", encoding="utf-8")
        else:
            raise AssertionError(behaviour)
        return pid

    def stop_all(self):
        for pid in list(self.servers):
            self.processes.kill(pid)
            with contextlib.suppress(Exception):
                self.servers.pop(pid).stop()


DEFAULT_START_TIME = 12345
"""The start time (``stat`` field 22) ``fake_proc`` writes unless told otherwise."""


def fake_proc(root, entries, *, states=None, start_times=None):
    """A /proc-shaped directory: ``entries`` maps pid to (ppid, pgid, comm, argv, cwd, open_paths).

    ``states`` maps a pid to its state letter (default S); ``start_times`` to its ``stat`` field 22.
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    for pid, (ppid, pgid, comm, argv, cwd, open_paths) in entries.items():
        directory = root / str(pid)
        (directory / "fd").mkdir(parents=True)
        state = (states or {}).get(pid, "S")
        start = (start_times or {}).get(pid, DEFAULT_START_TIME)
        (directory / "stat").write_text(f"{pid} ({comm}) {state} {ppid} {pgid} {pgid} 0 -1 4194560 100 0 0 0 1 2 0 0 20"
                                        f" 0 1 0 {start} 1000 100\n", encoding="utf-8")
        (directory / "cmdline").write_bytes(b"".join(word.encode() + b"\0" for word in argv))
        os.symlink(cwd, directory / "cwd")
        for number, path in enumerate(open_paths, start=3):
            os.symlink(path, directory / "fd" / str(number))
    return root


@dataclass
class Result:
    code: int
    out: str
    err: str
    git_calls: list

    @property
    def text(self):
        return self.out + self.err


class Workspace:
    """One throwaway repository, root, state directory and set of fakes, isolated from the user's environment."""

    def __init__(self, base, monkeypatch, files=None, share=None):
        """``share``: another Workspace whose state directory, clock, process table and launcher this one uses,
        as a second repository on the same machine would."""
        base = Path(base)
        base.mkdir(parents=True, exist_ok=True)
        self.base = base
        if share is None:
            # Isolate before any git runs, so the fixture repository never sees the user's own git setup.
            self.home = base / "home"
            self.home.mkdir()
            isolate(monkeypatch, self.home)
        self.origin, self.main = make_repository(base, files)
        self.root = base / "trees"
        if share is None:
            self.state = base / "state"
            self.clock = FakeClock()
            self.events = []
            self.processes = FakeProcessTable(self.events, self.clock)
            self.mcp = McpBehaviour()
            self.launcher = FakeLauncher(self.processes, self.mcp)
        else:
            self.home, self.state, self.clock, self.events = share.home, share.state, share.clock, share.events
            self.processes, self.mcp, self.launcher = share.processes, share.mcp, share.launcher
        self.github = FakeGithub()
        self._git = None

    @property
    def git(self):
        if self._git is None:
            self._git = RecordingGit(self.events)
        return self._git

    def env(self, **extra):
        values = {name: value for name, value in os.environ.items()
                  if not name.startswith(("XDG_", "XORCHESTRATE_"))}
        values.update({name: str(value) for name, value in extra.items()})
        return values

    def ports(self, **overrides):
        values = {"git": self.git, "processes": self.processes, "launcher": self.launcher, "transport": None,
                  "clock": self.clock, "store": None, "github": self.github, "disk": None}
        values.update(overrides)
        return tool("composition").Ports(**values)

    def run(self, *argv, env=None, locations=True, repo=True, ports=None):
        prefix = []
        if repo:
            prefix += ["--repo", str(self.main)]
        if locations:
            prefix += ["--root", str(self.root), "--state-dir", str(self.state)]
        main = tool("__main__").main
        start = len(self.git.calls)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = main([*prefix, *map(str, argv)], env=self.env() if env is None else env,
                            ports=self.ports() if ports is None else ports)
            except SystemExit as exit_:
                code = exit_.code if isinstance(exit_.code, int) else 2
        return Result(code, out.getvalue(), err.getvalue(), self.git.calls[start:])

    def tree(self, branch):
        return self.root / branch

    def new(self, branch):
        result = self.run("new", branch)
        assert result.code == 0, result.text
        return self.tree(branch)

    def state_files(self):
        """Recorded server state: files in the state directory other than logs, Serena's home and lock files."""
        return sorted(path for path in self.state.rglob("*") if path.is_file() and "logs" not in path.parts
                      and "serena-home" not in path.parts and "locks" not in path.parts) if self.state.exists() else []

    def store(self):
        """The tool's real state store over the state directory the tool was given."""
        return tool("adapters.state").JsonStateStore(self.state / STORE_SUBDIR)

    def key(self, tree):
        """The state key: the tree's absolute real path, so repositories sharing a state directory never collide."""
        return os.path.realpath(self.root / tree if not Path(tree).is_absolute() else tree)

    def saved(self, tree):
        """The recorded state for a branch under the root or a tree path, or None."""
        return self.store().load(self.key(tree))

    def make_state(self, tree, **values):
        """A ServerState for ``tree`` with the given fields (identity fields left out until the tool has them)."""
        return build(tool("model").ServerState, tree=str(tree), **values)

    def seed_server(self, tree, *, pid=7_000, pgid=None, port=1, head=None, version="2.1.0", serve=False,
                    start_time=None, record_argv=None):
        """A live server for ``tree`` as the tool would have recorded it: a process and its saved state.

        The process runs ``server_argv``; the state records ``record_argv`` (default: the same argv) and
        the process's start time.
        """
        tree = Path(tree)
        argv = server_argv(tree, port)
        self.processes.add(pid, pgid=pgid, cwd=tree, argv=argv, start_time=start_time)
        if serve:
            server = FakeMcpServer(port, self.mcp)
            self.launcher.servers[pid] = server
            self.processes.on_exit[pid] = server.stop
        state = self.make_state(tree, pid=pid, pgid=pid if pgid is None else pgid, port=port,
                           head=head or git(tree, "rev-parse", "HEAD"), version=version, started=self.clock.now(),
                           last_call=None, argv=tuple(argv if record_argv is None else record_argv),
                           start_time=start_time)
        self.store().save(self.key(tree), state)
        return state

    def close(self):
        self.launcher.stop_all()


DAY = 86_400


def age(path, seconds, now):
    """Set ``path``'s access and modification times to ``seconds`` before ``now``."""
    os.utime(path, (now - seconds, now - seconds))


def admin_dir(tree):
    return Path(git(tree, "rev-parse", "--absolute-git-dir"))


def push(tree, branch):
    git(tree, "push", "-q", "origin", branch)


def merge_on_origin(ws, branch, *, squash=False):
    """Land ``branch`` on the origin's default branch: a merge commit, or a squash copy that is no ancestor."""
    git(ws.main, "fetch", "-q", "origin")
    if squash:
        git(ws.main, "merge", "-q", "--squash", f"origin/{branch}")
        git(ws.main, "commit", "-q", "-m", f"squash {branch}")
    else:
        git(ws.main, "merge", "-q", "--no-ff", "-m", f"merge {branch}", f"origin/{branch}")
    git(ws.main, "push", "-q", "origin", DEFAULT_BRANCH)


class Fleet:
    """Trees and directories in every state the maintenance report distinguishes.

    Trees: ``merged`` (merge commit), ``squash`` (squash merge, GitHub knows its head), ``keep`` (pushed,
    unmerged, recent), ``unpushed``, ``idle`` (pushed, unmerged, last used 8 days ago), ``dirty``, ``stash``
    and ``locked`` (no commits of their own), ``busy`` (merged with a commit of its own, but a process works
    in it), ``missing`` and ``missing-locked`` (pushed, directory deleted), and an unregistered ``orphan``
    directory under the root.
    Directories: marked and unmarked scratch, recognised and unrecognised runs, extra entries, old and new.
    """

    def __init__(self, ws):
        self.ws = ws
        now = ws.clock.now()
        trees = {}
        trees["merged"] = ws.new("merged")
        commit(trees["merged"], "merged.txt", "m\n")
        push(trees["merged"], "merged")
        merge_on_origin(ws, "merged")
        trees["squash"] = ws.new("squash")
        self.squash_tip = commit(trees["squash"], "squash.txt", "s\n")
        push(trees["squash"], "squash")
        merge_on_origin(ws, "squash", squash=True)
        ws.github.heads["squash"] = self.squash_tip
        for name in ("keep", "idle", "missing", "missing-locked"):
            trees[name] = ws.new(name)
            commit(trees[name], f"{name}.txt", name + "\n")
            push(trees[name], name)
        trees["unpushed"] = ws.new("unpushed")
        commit(trees["unpushed"], "local.txt", "local\n")
        trees["busy"] = ws.new("busy")
        commit(trees["busy"], "busy.txt", "b\n")
        push(trees["busy"], "busy")
        merge_on_origin(ws, "busy")
        for name in ("dirty", "stash", "locked"):
            trees[name] = ws.new(name)
        (trees["dirty"] / "untracked.txt").write_text("x\n")
        (trees["stash"] / "README.md").write_text("stashed\n")
        git(trees["stash"], "stash", "push", "-q")
        git(ws.main, "worktree", "lock", trees["locked"])
        ws.processes.add(8_800, cwd=trees["busy"], argv=["bash"])
        admin = admin_dir(trees["idle"])
        for path in (admin / "index", admin / "logs" / "HEAD"):
            age(path, 8 * DAY, now)
        git(ws.main, "worktree", "lock", trees["missing-locked"])
        self.missing_admin = {name: admin_dir(trees[name]) for name in ("missing", "missing-locked")}
        for name in ("missing", "missing-locked"):
            shutil.rmtree(trees[name])
        orphan = ws.root / "orphan"
        orphan.mkdir(parents=True)
        (orphan / ".git").write_text("gitdir: /nonexistent/worktrees/orphan\n")
        trees["orphan"] = orphan
        self.trees = trees

        self.scratch = ws.base / "scratch"
        self.runs = ws.base / "runs"
        self.extra = ws.base / "extra"
        self.dirs = {}
        for name, created in (("old-marked", now - 2 * DAY), ("new-marked", now - 3600), ("busy-marked", now - 2 * DAY)):
            path = self.scratch / f"{name}-20260101T000000Z"
            path.mkdir(parents=True)
            (path / ".scratch.json").write_text(json.dumps({"owner": None, "created": created, "purpose": name}))
            self.dirs[name] = path
        ws.processes.add(8_900, cwd=self.dirs["busy-marked"], argv=["bash"])
        self.dirs["unmarked-old"] = self.scratch / "unmarked-old"
        self.dirs["unmarked-old"].mkdir()
        age(self.dirs["unmarked-old"], 30 * DAY, now)
        for name, seconds, recognised in (("run-old", 30 * DAY, True), ("run-new", 60, True),
                                          ("notarun-old", 30 * DAY, False)):
            path = self.runs / name
            path.mkdir(parents=True)
            target = path / "plan.json" if recognised else path / "other.txt"
            target.write_text("{}")
            age(target, seconds, now)
            age(path, seconds, now)
            self.dirs[name] = path
        for name, seconds in (("extra-old", 30 * DAY), ("extra-new", 60)):
            path = self.extra / name
            path.mkdir(parents=True)
            age(path, seconds, now)
            self.dirs[name] = path

    def env(self, **extra):
        return self.ws.env(XORCHESTRATE_DEV_SCRATCH_ROOT=self.scratch,
                           XORCHESTRATE_DEV_RUN_ROOTS=json.dumps([str(self.runs)]),
                           XORCHESTRATE_DEV_EXTRA_ROOTS=json.dumps([str(self.extra)]), **extra)
