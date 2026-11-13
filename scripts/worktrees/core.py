"""Work trees and per-tree Serena servers, through ports only.

Services return an ``Outcome``; the entry point prints it. Refusals change nothing.
"""

from __future__ import annotations

import ast
import hashlib
import json
import posixpath
import signal
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from scripts.worktrees.model import Config, Outcome, Proc, Refusal, ServerState, Tree
from scripts.worktrees.ports import (
    Clock,
    FileSystem,
    Git,
    GitError,
    Launcher,
    McpTransport,
    PortPicker,
    ProcessTable,
    StateStore,
    TransportError,
)

PROTOCOL_VERSION = "2025-06-18"
DEFAULT_PROBE_TOOL = "get_symbols_overview"
PORT_TRIES = 3
GRACE_SECONDS = 10.0
POLL_SECONDS = 1.0
STOP_POLL_SECONDS = 0.5
PROBE_REQUEST_SECONDS = 30.0
"""Longest a single readiness request may wait; a request also never waits past the readiness deadline."""
MIN_REQUEST_SECONDS = 0.1
SERVER_MARKER = "start-mcp-server"
BASE_HINT = "set XORCHESTRATE_DEV_BASE or pass --base"
COMMAND_HINT = "install Serena's launcher or set XORCHESTRATE_DEV_SERENA_COMMAND"


# Paths ----------------------------------------------------------------------------------------------

def _inside(path: str, root: str) -> bool:
    path, root = posixpath.normpath(path), posixpath.normpath(root)
    return path == root or path.startswith(root.rstrip("/") + "/")


def display_name(root: str, tree: str) -> str:
    """How a tree is shown: its path relative to the root when inside it, else its absolute path."""
    if _inside(tree, root) and posixpath.normpath(tree) != posixpath.normpath(root):
        return posixpath.relpath(tree, root)
    return posixpath.normpath(tree)


# Git views ------------------------------------------------------------------------------------------

class Repository:
    """Read-only git questions about one repository, asked through the Git port."""

    def __init__(self, git: Git, main: str) -> None:
        self.git = git
        self.main = main

    def ok(self, args: list[str], cwd: str | None = None) -> bool:
        try:
            self.git.run(args, cwd or self.main)
        except GitError:
            return False
        return True

    def out(self, args: list[str], cwd: str | None = None) -> str | None:
        try:
            return self.git.run(args, cwd or self.main)
        except GitError:
            return None

    def trees(self) -> list[Tree]:
        text = self.out(["worktree", "list", "--porcelain"]) or ""
        found: list[Tree] = []
        for block in text.strip().split("\n\n"):
            fields: dict[str, str] = {}
            for line in block.splitlines():
                name, _, value = line.partition(" ")
                fields[name] = value
            if "worktree" not in fields:
                continue
            branch = fields.get("branch")
            found.append(Tree(path=fields["worktree"], head=fields.get("HEAD"),
                              branch=branch.removeprefix("refs/heads/") if branch else None,
                              main=not found, locked="locked" in fields))
        return found

    def default_base(self) -> str | None:
        ref = self.out(["symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"])
        return ref or None

    def commit_of(self, ref: str) -> str | None:
        return self.out(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"])

    def is_ancestor(self, commit: str, ref: str) -> bool:
        return self.ok(["merge-base", "--is-ancestor", commit, ref])

    def on_origin(self, commit: str) -> bool:
        return bool(self.out(["for-each-ref", "--contains", commit, "--format=%(refname)", "refs/remotes/origin/"]))

    def stashed_on(self, branch: str) -> bool:
        """Whether a stash entry was made on ``branch``; raises GitError when the stash cannot be read."""
        subjects = self.git.run(["stash", "list", "--format=%gs"], self.main).splitlines()
        return any(subject.startswith((f"WIP on {branch}:", f"On {branch}:")) for subject in subjects)


# Tree commands --------------------------------------------------------------------------------------

class TreeService:
    def __init__(self, config: Config, repo: Repository, processes: ProcessTable, store: StateStore,
                 files: FileSystem, serena: SerenaService) -> None:
        self.config = config
        self.repo = repo
        self.processes = processes
        self.store = store
        self.files = files
        self.serena = serena

    def new(self, branch: str, base: str | None = None) -> Outcome:
        outcome = Outcome()
        path = posixpath.join(self.config.root, branch)
        if not self.repo.ok(["check-ref-format", "--branch", branch]):
            return outcome.refuse(Refusal("BAD_BRANCH", f"{branch!r} is not a valid branch name"))
        if self.files.exists(path):
            return outcome.refuse(Refusal("TREE_EXISTS", f"{path} already exists"))
        if self.repo.ok(["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"]):
            return outcome.refuse(Refusal("BRANCH_EXISTS", f"branch {branch} already exists"))
        base = base or self.config.base or self.repo.default_base()
        if not base:
            return outcome.refuse(Refusal("BASE_UNRESOLVED", f"origin/HEAD does not name a branch; {BASE_HINT}"))
        if not self.repo.ok(["fetch", "origin"]):
            return outcome.refuse(Refusal("FETCH_FAILED", "git fetch origin failed; nothing was changed"))
        if self.repo.ok(["show-ref", "--verify", "--quiet", f"refs/remotes/origin/{branch}"]):
            return outcome.refuse(Refusal("BRANCH_EXISTS", f"origin already has a branch {branch}"))
        if self.repo.commit_of(base) is None:
            return outcome.refuse(Refusal("BASE_MISSING", f"base {base} does not exist; {BASE_HINT}"))
        try:
            self.repo.git.run(["worktree", "add", "--no-track", "-b", branch, path, base], self.repo.main)
        except GitError as error:
            return outcome.refuse(Refusal("ADD_FAILED", str(error)))
        if self.repo.out(["status", "--porcelain"], path):
            outcome.err.append(f"warning: the new tree at {path} is not clean")
        outcome.out.append(path)
        return outcome

    def find(self, target: str) -> Tree | None:
        wanted = posixpath.normpath(target) if target.startswith("/") else None
        for tree in self.repo.trees():
            if tree.branch == target or (wanted and posixpath.normpath(tree.path) == wanted):
                return tree
        return None

    def remove(self, target: str, keep_branch: bool = False) -> Outcome:
        outcome = Outcome()
        tree = self.find(target)
        if tree is None:
            return outcome.refuse(Refusal("NOT_A_TREE", f"no work tree for {target}"))
        if tree.main:
            return outcome.refuse(Refusal("MAIN_CHECKOUT", f"{tree.path} is the main checkout"))
        if not self.files.is_dir(tree.path):
            return outcome.refuse(Refusal("NOT_A_TREE", f"{tree.path} is registered but its directory is missing "
                                          "or moved; nothing was changed"))
        refusals, merged = self._removal_refusals(tree)
        for refusal in refusals:
            outcome.refuse(refusal)
        if refusals:
            outcome.err.append(f"{tree.path} was left as it is")
            return outcome
        stopped = self.serena.down(tree.path)
        outcome.out += stopped.out
        if stopped.code:
            outcome.err += stopped.err
            return outcome.refuse(Refusal("IN_USE", f"the tree's Serena server did not stop; {tree.path} kept"))
        try:
            self.repo.git.run(["worktree", "remove", tree.path], self.repo.main)
        except GitError as error:
            return outcome.refuse(Refusal("REMOVE_FAILED", str(error)))
        outcome.out.append(f"removed {tree.path}")
        if tree.branch is None:
            return outcome
        if keep_branch:
            outcome.out.append(f"branch {tree.branch} kept as asked")
        elif merged:
            try:
                self.repo.git.run(["update-ref", "-d", f"refs/heads/{tree.branch}", tree.head or ""], self.repo.main)
                outcome.out.append(f"deleted branch {tree.branch}")
            except GitError:
                outcome.out.append(f"branch {tree.branch} kept: it moved while the tree was being removed")
        else:
            outcome.out.append(f"branch {tree.branch} kept: it is not merged into the base")
        return outcome

    def _removal_refusals(self, tree: Tree) -> tuple[list[Refusal], bool]:
        """Every reason not to remove ``tree``; a check that cannot run counts as a reason (fail closed)."""
        refusals = []
        try:
            if self.repo.git.run(["status", "--porcelain"], tree.path):
                refusals.append(Refusal("DIRTY", f"{tree.path} has uncommitted or untracked changes"))
        except GitError as error:
            refusals.append(Refusal("DIRTY", f"could not check {tree.path} for changes: {error}"))
        if tree.locked:
            refusals.append(Refusal("LOCKED", f"{tree.path} is locked"))
        if tree.branch:
            try:
                if self.repo.stashed_on(tree.branch):
                    refusals.append(Refusal("STASH", f"a stash entry was made on {tree.branch}"))
            except GitError as error:
                refusals.append(Refusal("STASH", f"could not read the stash: {error}"))
        users = self.users(tree.path)
        if users:
            refusals.append(Refusal("IN_USE", "processes working in the tree: "
                                    + ", ".join(f"{proc.pid} ({' '.join(proc.argv[:2])})" for proc in users)))
        base = self.config.base or self.repo.default_base()
        merged = bool(tree.head and base and self.repo.is_ancestor(tree.head, base))
        if tree.head and not merged and not self.repo.on_origin(tree.head):
            refusals.append(Refusal("UNPUSHED", f"{tree.head[:12]} is on no origin branch and not merged"))
        return refusals, merged

    def users(self, path: str) -> list[Proc]:
        """Processes with their directory or an open file inside ``path``, except the tree's own server family."""
        table = self.processes.processes()
        own = self.serena.family_of(path, table)
        return [proc for proc in table if proc.pid not in own
                and (_inside(proc.cwd, path) or any(_inside(open_path, path) for open_path in proc.open_paths))]


def family(table: list[Proc], pid: int) -> set[int]:
    """``pid`` and all its descendants."""
    children: dict[int, list[int]] = {}
    for proc in table:
        children.setdefault(proc.ppid, []).append(proc.pid)
    found, pending = set(), [pid]
    while pending:
        current = pending.pop()
        if current not in found:
            found.add(current)
            pending += children.get(current, [])
    return found


# MCP over streamable HTTP ---------------------------------------------------------------------------

class McpError(Exception):
    pass


@dataclass
class McpSession:
    transport: McpTransport
    url: str
    timeout: Callable[[], float | None] = lambda: None
    session: str | None = None
    next_id: int = 1

    def open(self) -> McpSession:
        self.session, _ = self._exchange("initialize", {
            "protocolVersion": PROTOCOL_VERSION, "capabilities": {},
            "clientInfo": {"name": "scripts.worktrees", "version": "1"}})
        self.transport.post(self.url, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
                            self.session, timeout=self.timeout())
        return self

    def request(self, method: str, params: Mapping[str, Any]) -> dict[str, Any]:
        return self._exchange(method, params)[1]

    def close(self) -> None:
        if self.session:
            try:
                self.transport.delete(self.url, self.session)
            except TransportError:
                pass

    def _exchange(self, method: str, params: Mapping[str, Any]) -> tuple[str | None, dict[str, Any]]:
        number, self.next_id = self.next_id, self.next_id + 1
        session, messages = self.transport.post(
            self.url, {"jsonrpc": "2.0", "id": number, "method": method, "params": dict(params)}, self.session,
            timeout=self.timeout())
        for message in messages:
            if isinstance(message, dict) and message.get("id") == number:
                if "error" in message:
                    raise McpError(json.dumps(message["error"]))
                result = message.get("result")
                if result is None:
                    result = {}
                if not isinstance(result, dict):
                    raise TransportError(f"the reply to {method} is not an object")
                return session or self.session, result
        raise TransportError(f"no reply to {method}")


def texts(result: Mapping[str, Any]) -> list[str]:
    return [item.get("text", "") for item in result.get("content") or [] if isinstance(item, dict)]


# Serena commands ------------------------------------------------------------------------------------

class SerenaService:
    def __init__(self, config: Config, repo: Repository, processes: ProcessTable, launcher: Launcher,
                 transport: McpTransport, clock: Clock, store: StateStore, files: FileSystem,
                 free_port: PortPicker, env: Mapping[str, str]) -> None:
        self.config = config
        self.repo = repo
        self.processes = processes
        self.launcher = launcher
        self.transport = transport
        self.clock = clock
        self.store = store
        self.files = files
        self.free_port = free_port
        self.env = env

    # Locating trees, servers and probes

    def resolve(self, target: str) -> str:
        """The real path of the tree's top level for a branch under the root or any path inside a tree."""
        path = posixpath.normpath(target) if target.startswith("/") else posixpath.join(self.config.root, target)
        if self.files.is_dir(path):
            top = self.repo.out(["rev-parse", "--show-toplevel"], path)
            if top:
                path = top
        return self.files.real_path(path)

    def key(self, tree: str) -> str:
        """The state key: the tree's absolute real path, unique across repositories sharing a state directory."""
        return self.files.real_path(tree)

    def name(self, tree: str) -> str:
        return display_name(self.files.real_path(self.config.root), tree)

    def ours(self, state: ServerState, tree: str | None = None, table: list[Proc] | None = None) -> Proc | None:
        """The live process of a recorded server, or None when the pid now belongs to anything else.

        The state must record the spawned command; the process must run it (or, like it, a Serena
        server) and, when both are known, have the recorded start time. A state recorded for
        another tree is never ours.
        """
        if state.pid <= 1 or (tree is not None and self.files.real_path(state.tree) != self.files.real_path(tree)):
            return None
        table = self.processes.processes() if table is None else table
        proc = next((item for item in table if item.pid == state.pid), None)
        if proc is None or not self.processes.alive(state.pid):
            return None
        if not state.argv:
            return None  # a record without the spawned command is never trusted
        same_command = tuple(proc.argv) == tuple(state.argv)
        both_servers = SERVER_MARKER in proc.argv and SERVER_MARKER in state.argv
        if not (same_command or both_servers):
            return None
        if state.start_time is not None and proc.start_time is not None and proc.start_time != state.start_time:
            return None
        return proc

    def family_of(self, tree: str, table: list[Proc]) -> set[int]:
        """The pids of ``tree``'s own live server and its descendants; empty when it has none."""
        key = self.key(tree)
        state = self.store.load(key)
        return family(table, state.pid) if state and self.ours(state, key, table) else set()

    def probe(self, tree: str) -> tuple[str, dict[str, Any]] | None:
        tool = self.config.probe_tool or DEFAULT_PROBE_TOOL
        if self.config.probe_args is not None:
            return tool, dict(self.config.probe_args)
        if self.config.probe_tool:
            return tool, {}
        for path in sorted((self.repo.out(["ls-files", "-z", "--", "*.py"], tree) or "").split("\0")):
            if path and self._defines_something(posixpath.join(tree, path)):
                return tool, {"relative_path": path}
        return None

    def _defines_something(self, path: str) -> bool:
        text = self.files.read_text(path)
        if text is None:
            return False
        try:
            body = ast.parse(text).body
        except (SyntaxError, ValueError):
            return False
        return any(isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) for node in body)

    def answers(self, port: int, probe: tuple[str, dict[str, Any]], deadline: float | None = None) -> bool:
        """True when the server completes MCP initialization and returns a non-empty, non-error probe result.

        With ``deadline`` (on the clock), no single request may wait past it.
        """
        def timeout() -> float | None:
            if deadline is None:
                return PROBE_REQUEST_SECONDS
            return max(MIN_REQUEST_SECONDS, min(PROBE_REQUEST_SECONDS, deadline - self.clock.now()))

        session = McpSession(self.transport, f"http://127.0.0.1:{port}/mcp", timeout)
        try:
            session.open()
            result = session.request("tools/call", {"name": probe[0], "arguments": probe[1]})
        except (TransportError, McpError):
            return False
        finally:
            session.close()
        return not result.get("isError") and any(text.strip() for text in texts(result))

    # up

    def up(self, target: str) -> Outcome:
        outcome = Outcome()
        tree = self.resolve(target)
        if not self.files.is_dir(tree) or self.repo.out(["rev-parse", "--show-toplevel"], tree) is None:
            return outcome.refuse(Refusal("NOT_A_TREE", f"{tree} is not a work tree"))
        key, name = self.key(tree), self.name(tree)
        head = self.repo.out(["rev-parse", "HEAD"], tree) or ""
        probe = self.probe(tree)
        if probe is None:
            return outcome.refuse(Refusal("NO_PROBE", "no tracked Python file defines a class or function; "
                                          "set XORCHESTRATE_DEV_PROBE_TOOL and XORCHESTRATE_DEV_PROBE_ARGS"))
        with self.store.lock(key):
            current = self.store.load(key)
            if current is not None:
                if self.ours(current, tree):
                    if current.head == head and self.answers(current.port, probe):
                        outcome.out.append(f"{name}: already up on port {current.port} (pid {current.pid})")
                        return outcome
                    restarted = self._down(key, name)
                    outcome.out += restarted.out
                    if restarted.code:
                        outcome.err += restarted.err
                        return outcome.refuse(Refusal("SERVER_FAILED", "the previous server did not stop"))
                else:
                    self.store.delete(key)
            return self._launch(tree, key, name, head, probe, outcome)

    def _launch(self, tree: str, key: str, name: str, head: str, probe: tuple[str, dict[str, Any]],
                outcome: Outcome) -> Outcome:
        home = posixpath.join(self.config.state_dir, "serena-home")
        digest = hashlib.sha256(key.encode()).hexdigest()[:12]
        log = posixpath.join(self.config.state_dir, "logs", f"{posixpath.basename(key)}-{digest}.log")
        self.files.make_dirs(home)
        env = {**self.env, "SERENA_HOME": home}
        for attempt in range(1, PORT_TRIES + 1):
            port = self.free_port()
            argv = [self._fill(word, tree, port) for word in self.config.serena_command]
            try:
                pid = self.launcher.spawn(argv, tree, env, log)
            except FileNotFoundError as error:
                return outcome.refuse(Refusal("SERENA_UNAVAILABLE", f"cannot start {error}; {COMMAND_HINT}"))
            try:
                status = self._await_ready(pid, port, probe)
            except BaseException:
                self._stop(pid, self._pgid(pid))
                raise
            if status == "ready":
                proc = next((item for item in self.processes.processes() if item.pid == pid), None)
                state = ServerState(tree=tree, pid=pid, pgid=proc.pgid if proc else pid, port=port, head=head,
                                    version=self.config.serena_version, started=self.clock.now(), last_call=None,
                                    argv=tuple(argv), start_time=proc.start_time if proc else None)
                self.store.save(key, state)
                outcome.out.append(f"{name}: up on port {port} (pid {pid})")
                return outcome
            tail = self._tail(log)
            in_use = "address already in use" in tail.lower()
            if status == "exited" and in_use and attempt < PORT_TRIES:
                continue
            if status == "timeout":
                self._stop(pid, self._pgid(pid))
                reason = f"no answer to the probe within {self.config.serena_timeout:g} s"
            elif in_use:
                reason = f"no free port after {PORT_TRIES} tries"
            else:
                reason = "the server exited before answering"
            outcome.refuse(Refusal("SERVER_FAILED", f"{reason}; log {log}"))
            outcome.err += [f"  {line}" for line in tail.splitlines()]
            return outcome
        return outcome  # pragma: no cover - the loop always returns

    def _fill(self, word: str, tree: str, port: int) -> str:
        for name, value in (("{version}", self.config.serena_version), ("{context}", self.config.serena_context),
                            ("{project}", tree), ("{port}", str(port))):
            word = word.replace(name, value)
        return word

    def _await_ready(self, pid: int, port: int, probe: tuple[str, dict[str, Any]]) -> str:
        deadline = self.clock.now() + self.config.serena_timeout
        while True:
            if not self.processes.alive(pid):
                return "exited"
            if self.answers(port, probe, deadline):
                return "ready"
            if self.clock.now() >= deadline:
                return "timeout"
            self.clock.sleep(min(POLL_SECONDS, max(deadline - self.clock.now(), 0.0)))

    def _pgid(self, pid: int) -> int:
        for proc in self.processes.processes():
            if proc.pid == pid:
                return proc.pgid
        return pid

    def _tail(self, log: str, lines: int = 20) -> str:
        return "\n".join((self.files.read_text(log) or "").splitlines()[-lines:])

    # down

    def down(self, target: str) -> Outcome:
        tree = self.resolve(target)
        key = self.key(tree)
        with self.store.lock(key):
            return self._down(key, self.name(tree))

    def _down(self, key: str, name: str) -> Outcome:
        outcome = Outcome()
        state = self.store.load(key)
        if state is None:
            outcome.out.append(f"{name}: no server")
            return outcome
        if self.ours(state, key) is None:
            self.store.delete(key)
            outcome.out.append(f"{name}: forgot a recorded server that is no longer running")
            return outcome
        skipped = self._stop(state.pid, state.pgid)
        for pgid in skipped:
            outcome.refuse(Refusal("GROUP_SKIPPED", f"process group {pgid} also holds processes outside the "
                                   "server; not signalled"))
        if self.processes.alive(state.pid):
            return outcome.refuse(Refusal("SERVER_FAILED", f"server {state.pid} is still running; state kept"))
        self.store.delete(key)
        outcome.out.append(f"{name}: stopped")
        return outcome

    def _stop(self, pid: int, pgid: int) -> list[int]:
        """SIGTERM the server's group and its descendants' groups, SIGKILL survivors after the grace.

        A group that also holds a process outside the server's family, and groups 0 and 1, are never
        signalled; their ids are returned.
        """
        if pid <= 1:
            return [pgid]
        table = self.processes.processes()
        members = family(table, pid)
        groups = list(dict.fromkeys([pgid] + [proc.pgid for proc in table if proc.pid in members]))
        targets, skipped = [], []
        for group in groups:
            outsiders = [proc for proc in table if proc.pgid == group and proc.pid not in members]
            (skipped if outsiders or group <= 1 else targets).append(group)
        for group in targets:
            self.processes.signal_group(group, signal.SIGTERM)
        self._wait(targets, members)
        survivors = self._survivors(targets, members)
        for group in survivors:
            self.processes.signal_group(group, signal.SIGKILL)
        if survivors:
            self._wait(survivors, members)
        return skipped

    def _wait(self, groups: list[int], members: set[int]) -> None:
        deadline = self.clock.now() + GRACE_SECONDS
        while self._survivors(groups, members) and self.clock.now() < deadline:
            self.clock.sleep(min(STOP_POLL_SECONDS, deadline - self.clock.now()))

    def _survivors(self, groups: list[int], members: set[int]) -> list[int]:
        """Groups that still hold a live member (zombies count as gone)."""
        alive = {proc.pgid for proc in self.processes.processes()
                 if proc.pid in members and self.processes.alive(proc.pid)}
        return [group for group in groups if group in alive]

    # ls and reap

    def ls(self) -> Outcome:
        outcome = Outcome()
        table = self.processes.processes()
        for _, state in self.store.list():
            alive = "alive" if self.ours(state, None, table) else "dead"
            exists = "yes" if self.files.is_dir(state.tree) else "no"
            outcome.out.append(f"{self.name(state.tree)}  port {state.port}  pid {state.pid}  {alive}  tree {exists}")
        return outcome

    def reap(self) -> Outcome:
        outcome = Outcome()
        for key, state in self.store.list():
            name = self.name(state.tree)
            with self.store.lock(key):
                if self.ours(state) is None:
                    self.store.delete(key)
                    outcome.out.append(f"{name}: forgot server {state.pid}, which is no longer running")
                elif not self.files.is_dir(state.tree):
                    stopped = self._down(key, name)
                    outcome.code = max(outcome.code, stopped.code)
                    outcome.err += stopped.err
                    outcome.out.append(f"{name}: stopped server {state.pid}; its tree is gone")
        return outcome

    # call, tools and check

    def _live(self, target: str) -> tuple[str, str, ServerState | None]:
        tree = self.resolve(target)
        key = self.key(tree)
        state = self.store.load(key)
        if state is None or self.ours(state, tree) is None:
            return tree, key, None
        return tree, key, state

    def call(self, target: str, tool: str, arguments: Mapping[str, Any]) -> Outcome:
        return self._session(target, "tools/call", {"name": tool, "arguments": dict(arguments)})

    def tools(self, target: str) -> Outcome:
        return self._session(target, "tools/list", {})

    def _session(self, target: str, method: str, params: Mapping[str, Any]) -> Outcome:
        outcome = Outcome()
        tree, key, state = self._live(target)
        if state is None:
            return outcome.refuse(Refusal("NOT_UP", f"no running server for {self.name(tree)}; run serena up first"))
        session = McpSession(self.transport, f"http://127.0.0.1:{state.port}/mcp")
        try:
            session.open()
            result = session.request(method, params)
        except McpError as error:
            return outcome.refuse(Refusal("CALL_FAILED", f"protocol error {error}"))
        except TransportError as error:
            return outcome.refuse(Refusal("NOT_ANSWERING", f"{self.name(tree)}: {error}"))
        finally:
            session.close()
        with self.store.lock(key):
            if self.store.load(key) == state:
                self.store.save(key, replace(state, last_call=self.clock.now()))
        if method == "tools/list":
            outcome.out += [entry.get("name", "") for entry in result.get("tools") or [] if isinstance(entry, dict)]
            return outcome
        outcome.out += texts(result)
        if result.get("isError"):
            outcome.code = 1
        return outcome

    def check(self, target: str) -> Outcome:
        outcome = Outcome()
        tree, key, state = self._live(target)
        if state is None:
            return outcome.refuse(Refusal("NOT_UP", f"no running server for {self.name(tree)}"))
        probe = self.probe(tree)
        if probe is None or not self.answers(state.port, probe):
            outcome.refuse(Refusal("NOT_ANSWERING", f"port {state.port} did not answer the probe"))
        toplevel = self.files.real_path(self.repo.out(["rev-parse", "--show-toplevel"], tree) or tree)
        table = self.processes.processes()
        server = next((proc for proc in table if proc.pid == state.pid), None)
        if server is not None:
            project = self.files.real_path(_option(server.argv, "--project") or server.cwd)
            if project != toplevel:
                outcome.refuse(Refusal("PROJECT_MISMATCH", f"the server serves {project}, not {toplevel}"))
        servers = self._servers_for(table, toplevel)
        if len(servers) > 1:
            outcome.refuse(Refusal("MULTIPLE_SERVERS", "servers on this tree: " + ", ".join(map(str, servers))))
        if not self.repo.ok(["diff", "--quiet", "HEAD", "--", ".serena/project.yml"], tree):
            outcome.refuse(Refusal("PROJECT_YML_CHANGED", ".serena/project.yml differs from HEAD; restore it"))
        head = self.repo.out(["rev-parse", "HEAD"], tree)
        if head != state.head:
            outcome.refuse(Refusal("STALE_HEAD", "the tree moved since the server started; run serena up again"))
        if state.version != self.config.serena_version:
            outcome.refuse(Refusal("VERSION_MISMATCH",
                                   f"server {state.version}, configured {self.config.serena_version}"))
        if not outcome.code:
            outcome.out.append(f"{self.name(tree)}: ok")
        return outcome

    def _servers_for(self, table: list[Proc], tree: str) -> list[int]:
        """Serena server processes for ``tree``, counting a launcher and the server it started once."""
        matching = {proc.pid: proc for proc in table if SERVER_MARKER in proc.argv
                    and self.files.real_path(_option(proc.argv, "--project") or proc.cwd) == tree}
        return sorted(pid for pid, proc in matching.items() if proc.ppid not in matching)


def _option(argv: tuple[str, ...], name: str) -> str | None:
    if name in argv and argv.index(name) + 1 < len(argv):
        return argv[argv.index(name) + 1]
    return None
