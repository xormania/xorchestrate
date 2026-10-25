"""Separate, workspace-bound Serena MCP gateway. No control-plane tools are forwarded."""

from __future__ import annotations

import atexit
import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from .control import REQUEST_BINDING, Control
from .process_observation import ProcessObservation
from .retirement import key, lease
from .role_packages import adapter, descriptor
from .role_views import RoleSet
from .sessions import Lease
from .store import Store
from .telemetry import Trace, context
from .workspaces import EDITORS, Workspaces, project_config

SERVER = "serena"
ROLES = RoleSet("workspace", "serena")
TOOL = {
    "name": "serena_repl",
    "description": "Use Serena v2's persistent Python REPL for your current job. The service supplies the session and workspace. Start with s.info() or s.info('lsp', 'edit') for current facade documentation. Variables persist between calls. Source writes are limited to your role's mounted paths; Git, checks, Maker and delivery belong to the control-plane tools.",
    "inputSchema": {
        "type": "object",
        "properties": {"code": {"type": "string", "minLength": 1, "maxLength": 18000}},
        "required": ["code"],
        "additionalProperties": False,
    },
}


def enabled(store, scope):
    return bool(
        scope and (scope["job"] or adapter(scope["role"]) == "planner") and scope["role"] in ROLES and project_config(store).get("serena", {}).get("image")
    )


def server_config(directory, member):
    generation = Store(directory).member(member)["generation"]
    return {
        "command": sys.executable,
        "args": [str(Path(__file__).with_name("run.py")), "serena", "--state", str(directory), "--member", member,
                 *(["--generation", generation] if generation else [])],
    }


def mount(source, target, readonly=False):
    if any(char in str(source) + target for char in (",", "\n", "\r")):
        raise ValueError("Container mount paths must not contain commas or line breaks.")
    return ["--mount", f"type=bind,src={source},dst={target}" + (",readonly" if readonly else "")]


def writable_paths(root, editable, protected):
    """Split directory mounts around protected descendants without creating them."""
    from .assignments import covers

    result = set()

    def include(value):
        value = Path(value).as_posix()
        if any(covers(path, value) for path in protected):
            return
        source = root / value
        if source.resolve() != source or not source.resolve().is_relative_to(root):
            raise ValueError("Editable mount escapes the managed workspace.")
        if not source.exists():
            return
        if any(covers(value, path) for path in protected):
            if source.is_dir():
                for child in sorted(source.iterdir()):
                    include(child.relative_to(root).as_posix())
        else:
            result.add(value)

    for value in editable:
        include(value)
    return sorted(result)


class Serena:
    def __init__(self, directory, member, generation=None):
        self.store, self.member = Store(directory), member
        self.session_lease = Lease(self.store, member, generation)
        self.control = Control(self.store, member)
        self.spaces = Workspaces(self.store, member)
        self.process = None
        self.packets = queue.Queue()
        self.ident = 0
        self.container = "agentscient-serena-" + uuid.uuid4().hex
        self.session = uuid.uuid4().hex[:8]
        self.signature = None
        self.workspace_root = None
        self.mount_layout = None
        self.paused = False
        self.log = None
        self.mutex = threading.RLock()
        self.workspace_lease = None
        self.observation = None
        self.gateway_connection = None
        self.gateway_request_id = None
        atexit.register(self.close)

    def scope(self):
        from .assignments import current as assignment_for
        from .assignments import policy

        self.session_lease.check()
        current = self.control.snapshot()
        if not enabled(self.store, current):
            raise ValueError("Serena is not configured for your current job and role.")
        if current["paused"]:
            raise ValueError("This work is paused. Use the control plane for reports or guidance.")
        if current["role"] in EDITORS:
            self.spaces.scope(write=True)
        identity = tuple(current[k] for k in ("job", "work_order", "role", "reports_to"))
        config = project_config(self.store)
        assignment = assignment_for(self.store, current)
        signature = (identity, json.dumps({"config": {key: config.get(key) for key in ("serena", "paths")},
                                         "assignment": assignment, "policy": policy(self.store, config),
                                         "role_package": descriptor(current["role"])}, sort_keys=True))
        if self.signature is not None and self.signature != signature:
            self.close()
            raise ValueError("The role or workspace changed. Reconnect Serena for the current responsibility.")
        return current, signature

    def command(self, scope, workspace):
        from .assignments import covers, policy
        from .assignments import current as assignment_for

        config = project_config(self.store)["serena"]
        image = config["image"]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:@-]+", image):
            raise ValueError("Configure a local Serena container image.")
        self.docker = config.get("docker", "docker")
        root = Path(workspace["root"])
        project_state = root / ".serena"
        if project_state.is_symlink() or project_state.resolve() != project_state:
            raise ValueError("Provider mountpoint must not be a symlink.")
        project_state.mkdir(exist_ok=True)
        private = self.store.directory / "providers" / self.member / self.container
        private.mkdir(mode=0o700, parents=True, exist_ok=True)
        provider_state = private / "serena"
        provider_state.mkdir(mode=0o700, exist_ok=True)
        (provider_state / "project.yml").write_text(
            "project_name: managed-job\nlanguage_servers: [php_phpactor]\nread_only: "
            + ("false" if scope["role"] in EDITORS else "true")
            + "\nignored_paths: [devtools, .git, var]\n"
        )
        argv = [
            self.docker,
            "run",
            "--rm",
            "--interactive",
            "--name",
            self.container,
            "--label",
            "agentscient.workspace=" + key(workspace["root"]),
            "--pull=never",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--pids-limit=128",
            "--memory=" + str(config.get("memory", "2g")),
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=512m",
            "--workdir",
            "/workspace",
        ]
        assignment = assignment_for(self.store, scope)
        rules = policy(self.store, project_config(self.store))
        editable = self.spaces.paths(scope["role"])
        # Explicit missing paths are files unless the assignment ends in '/'.
        # Never manufacture default optional files just by starting inspection.
        for value in sorted(editable, key=lambda value: (len(Path(value).parts), value)):
            source = root / value
            if source.resolve() != source or not source.resolve().is_relative_to(root):
                raise ValueError("Editable mount escapes the managed workspace.")
            if value.endswith("/") and source.exists() and not source.is_dir():
                raise ValueError("An editable directory path names an existing file.")
            if not source.exists():
                if any(covers(value, path) for path in rules["protected_paths"]):
                    continue  # A missing protected ancestor remains read-only.
                if value.endswith("/"):
                    source.mkdir(parents=True)
                elif "editable_paths" in assignment:
                    source.parent.mkdir(parents=True, exist_ok=True)
                    source.touch(exist_ok=False)
        writable = writable_paths(root, editable, rules["protected_paths"])
        if "context_paths" in assignment:
            view = private / "view"
            view.mkdir(exist_ok=True)
            (view / ".serena").mkdir(exist_ok=True)
            selected = {Path(value).as_posix() for value in
                        assignment["context_paths"] + assignment.get("editable_paths", [])}
            for value in sorted(selected, key=lambda value: (len(Path(value).parts), value)):
                source = root / value
                if source.resolve() != source or not source.resolve().is_relative_to(root):
                    raise ValueError("Context mount escapes the managed workspace.")
                if not source.exists():
                    continue
                target = view / value
                target.parent.mkdir(parents=True, exist_ok=True)
                if source.is_dir():
                    target.mkdir(exist_ok=True)
                else:
                    target.touch()
            argv += mount(view, "/workspace", readonly=True)
            for value in sorted(selected, key=lambda value: (len(Path(value).parts), value)):
                source = root / value
                if source.exists():
                    argv += mount(source, "/workspace/" + value, readonly=True)
        else:
            argv += mount(root, "/workspace", readonly=True)
        argv += mount(provider_state, "/workspace/.serena")
        for value in writable:
            argv += mount(root / value, "/workspace/" + value)
        # Context and edit paths can overlap; Docker needs one final mount per destination.
        flags, mounts, index = [], {}, 0
        while index < len(argv):
            if argv[index] == "--mount":
                value = argv[index + 1]
                target = next(part[4:] for part in value.split(",") if part.startswith("dst="))
                mounts[target] = value
                index += 2
            else:
                flags.append(argv[index])
                index += 1
        for target in sorted(mounts, key=lambda path: (len(Path(path).parts), path)):
            flags += ["--mount", mounts[target]]
        self.mount_layout = self.layout(scope, workspace)
        return [*flags, image]

    def layout(self, scope, workspace):
        from .assignments import current as assignment_for
        from .assignments import policy

        assignment = assignment_for(self.store, scope)
        root = Path(workspace["root"])
        editable = self.spaces.paths(scope["role"])
        writable = writable_paths(root, editable, policy(self.store, project_config(self.store))["protected_paths"])
        paths = {Path(value).as_posix() for value in assignment.get("context_paths", []) + editable + writable}
        result = []
        for path in sorted(paths):
            source = root / path
            info = source.stat() if source.exists() else None
            # File bind mounts keep the old inode when Git/Maker replaces a file.
            result.append((path, (info.st_dev, info.st_ino, source.is_dir()) if info else None))
        return result

    def reader(self, process, packets):
        try:
            while line := process.stdout.readline(4 * 1024 * 1024 + 1):
                if len(line) > 4 * 1024 * 1024:
                    raise ValueError("Serena response exceeded its transport limit.")
                packets.put(json.loads(line))
        except Exception as error:
            packets.put(error)
        finally:
            packets.put(ConnectionError("Serena closed its transport. Inspect the operator's provider log."))

    def write(self, packet):
        self.process.stdin.write((json.dumps({"jsonrpc": "2.0", **packet}) + "\n").encode())
        self.process.stdin.flush()

    def request(self, method, params, timeout=100):
        observation = self.observation if method == "tools/call" else None
        ident = self.ident + 1
        if observation is not None:
            try:
                frame_context = context(self.store, self.member)
            except Exception:
                frame_context = observation.frame_context
            observation.begin(ident, frame_context, self.gateway_request_id)
        try:
            return self._request(method, params, timeout)
        finally:
            if observation is not None:
                observation.finish(ident)

    def _request(self, method, params, timeout=100):
        self.ident += 1
        ident = self.ident
        self.write({"id": ident, "method": method, "params": params})
        end = min(time.monotonic() + timeout, self.deadline)
        while True:
            try:
                packet = self.packets.get(timeout=max(0, end - time.monotonic()))
            except queue.Empty:
                self.close()
                raise ValueError(
                    "Serena timed out. Its process was closed; inspect retained files before retrying. REPL variables were lost."
                ) from None
            if isinstance(packet, Exception):
                raise packet
            if "method" in packet:
                if "id" in packet:
                    self.write(
                        {"id": packet["id"], "error": {"code": -32601, "message": "No host client capabilities"}}
                    )
                continue
            if packet.get("id") == ident:
                if "error" in packet:
                    raise ValueError(str(packet["error"])[:1200])
                return packet.get("result", {})

    def start(self, scope, workspace, signature):
        argv = self.command(scope, workspace)
        self.workspace_lease = lease(self.store, workspace["root"])
        self.workspace_lease.__enter__()
        with self.store.connect() as db:
            db.execute("UPDATE managed_worktrees SET provider=1 WHERE root=?", (workspace["root"],))
        path = self.store.directory / "telemetry" / (self.member + "-serena.log")
        path.parent.mkdir(mode=0o700, exist_ok=True)
        self.log = path.open("ab")
        self.observation = ProcessObservation(self.store.directory, self.member, self.container,
                                              context(self.store, self.member), self.gateway_connection)
        try:
            self.process = subprocess.Popen(
                argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True
            )
        except Exception:
            self.workspace_lease.__exit__(None, None, None)
            self.workspace_lease = None
            self.log.close()
            self.observation.close()
            self.observation = None
            raise
        self.observation.attach(self.process.stderr, self.log)
        self.signature = signature
        self.workspace_root = workspace["root"]
        threading.Thread(target=self.retire_when_released, args=(self.process, signature), daemon=True).start()
        threading.Thread(target=self.reader, args=(self.process, self.packets), daemon=True).start()
        self.request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "agentscient-serena-gateway", "version": "1"},
            },
        )
        self.write({"method": "notifications/initialized"})
        tools = self.request("tools/list", {})["tools"]
        if "serena_repl" not in {row["name"] for row in tools}:
            self.close()
            raise ValueError("The configured image does not provide Serena v2's REPL interface.")
        self.observation.check_ready()

    def freeze(self, paused):
        result = subprocess.run(
            [self.docker, "pause" if paused else "unpause", self.container], capture_output=True, timeout=10
        )
        if result.returncode:
            self.close()
            raise ValueError(
                "Could not freeze Serena between calls; the provider was closed to preserve workspace coordination."
            )
        self.paused = paused

    def call(self, arguments):
        from .api import validate

        validate(arguments, TOOL["inputSchema"])
        with self.session_lease.hold(), self.mutex:
            scope, signature = self.scope()
            token = REQUEST_BINDING.set((self.member, scope))
            try:
                result = self._call(arguments, scope, signature)
                from .diagnostics import deliver_guidance

                guidance = deliver_guidance(self.store, self.member, "serena_repl", result=result)
                if guidance:
                    result["content"].append({"type": "text", "text": json.dumps(guidance, ensure_ascii=False)})
                return result
            except Exception as error:
                from .diagnostics import deliver_guidance

                error.diagnostic_guidance = deliver_guidance(self.store, self.member, "serena_repl", error=error)
                raise
            finally:
                REQUEST_BINDING.reset(token)

    def retire_when_released(self, process, signature):
        while self.process is process:
            time.sleep(0.5)
            with self.mutex:
                if self.process is not process:
                    return
                current = self.control.snapshot()
                identity = tuple(current[k] for k in ("job", "work_order", "role", "reports_to")) if current else None
                try:
                    self.session_lease.check()
                    current_session = True
                except ValueError:
                    current_session = False
                if identity != signature[0] or not current_session:
                    try:
                        self.close()
                    except Exception as error:
                        self.store.event(self.member, "serena_shutdown_error", error=str(error)[:1000])
                    return

    def _call(self, arguments, scope, signature):
        self.deadline = time.monotonic() + 100
        with self.spaces.use(write=scope["role"] in EDITORS) as (_, workspace):
            self.scope()
            if self.process is not None and self.mount_layout is not None and self.mount_layout != self.layout(scope, workspace):
                self.close()
                raise ValueError("Workspace paths changed after generation or refresh. Repeat the call to start a current Serena view.")
            if self.process is not None and self.workspace_root != workspace["root"]:
                self.close()
                raise ValueError(
                    "The inspected source snapshot changed. REPL variables were cleared; repeat inspection on the new snapshot."
                )
            try:
                if self.process is None:
                    self.start(scope, workspace, signature)
                elif self.paused:
                    self.freeze(False)
                result = self.request(
                    "tools/call",
                    {"name": "serena_repl", "arguments": {"session": self.session, "code": arguments["code"]}},
                )
                # FastMCP also supplies structuredContent with the same rendered text.
                # Keep the standard MCP content once rather than counting/returning it twice.
                result = {"content": result.get("content", []), "isError": result.get("isError", False)}
                text = json.dumps(result, ensure_ascii=False)
                if len(text) > 18000:
                    import io

                    from .artifacts import Artifacts

                    saved = Artifacts(self.store, self.member).ingest(
                        "serena-result.json",
                        io.BytesIO(text.encode()),
                        "application/json",
                        work_order=scope["work_order"],
                        job=scope["job"],
                    )
                    result = {
                        "content": [
                            {
                                "type": "text",
                                "text": "Serena result retained as artifact:"
                                + str(saved["artifact"]["id"])
                                + ". Narrow output in the REPL or read evidence through work_context.\nPreview: "
                                + text[:2500],
                            }
                        ],
                        "isError": result.get("isError", False),
                    }
                self.store.event(
                    self.member,
                    "serena_call",
                    job=scope["job"],
                    work_order=scope["work_order"],
                    success=not result.get("isError", False),
                )
                return result
            except Exception:
                self.close()
                raise
            finally:
                if self.process is not None:
                    self.freeze(True)

    def close(self):
        with self.mutex:
            self._close()

    def _close(self):
        if self.process is None:
            return
        process, self.process = self.process, None
        try:
            subprocess.run([self.docker, "rm", "--force", self.container], capture_output=True, timeout=10)
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            for stream in (process.stdin, process.stdout):
                stream.close()
            if self.observation is not None:
                self.observation.close()
                self.observation = None
            if self.log:
                self.log.close()
            self.paused = False
            self.container = "agentscient-serena-" + uuid.uuid4().hex
            self.session = uuid.uuid4().hex[:8]
            self.packets = queue.Queue()
            if self.workspace_lease:
                self.workspace_lease.__exit__(None, None, None)
                self.workspace_lease = None


def serve(directory, member, generation=None):
    def stop(signum, frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    provider = Serena(directory, member, generation)
    trace = Trace(directory, member, component="serena-mcp", context=lambda: {
        **context(provider.store, member), "connection_generation": provider.session_lease.generation,
    })
    provider.gateway_connection = trace.connection
    initialized = False
    try:
        while line := sys.stdin.buffer.readline(131073):
            if len(line) > 131072:
                raise ValueError("Serena MCP frame exceeds 128 KiB.")
            packet = json.loads(line)
            trace.record("mcp_in", packet)
            ident, method = packet.get("id"), packet.get("method")
            if method == "notifications/initialized":
                initialized = True
                continue
            if ident is None:
                continue
            try:
                provider.session_lease.check()
                if method == "initialize":
                    result = {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": SERVER, "version": "2-beta-gateway"},
                        "instructions": "Serena handles code discovery and editing for your bound job. Session and workspace are automatic. Use s.info() in serena_repl for progressive documentation. Use the separate control plane for workflow, Maker, checks and Git delivery.",
                    }
                elif method == "ping":
                    result = {}
                elif not initialized:
                    raise ValueError("Initialize this MCP connection first.")
                elif method == "tools/list":
                    result = {"tools": [TOOL] if enabled(provider.store, provider.control.snapshot()) else []}
                elif method == "tools/call":
                    params = packet.get("params", {})
                    if params.get("name") != TOOL["name"]:
                        raise ValueError("Only the bound Serena REPL is available through this provider.")
                    provider.gateway_request_id = ident
                    try:
                        result = provider.call(params.get("arguments", {}))
                    finally:
                        provider.gateway_request_id = None
                else:
                    raise ValueError("Unsupported MCP method.")
                response = {"id": ident, "result": result}
            except Exception as error:
                response = (
                    {"id": ident, "result": {"isError": True, "content": [{"type": "text", "text": str(error)[:1800]}]}}
                    if method == "tools/call"
                    else {"id": ident, "error": {"code": -32602, "message": str(error)[:1800]}}
                )
                if method == "tools/call" and getattr(error, "diagnostic_guidance", None):
                    response["result"]["content"].append({"type": "text", "text": json.dumps(error.diagnostic_guidance, ensure_ascii=False)})
            trace.record("mcp_out", response)
            print(json.dumps({"jsonrpc": "2.0", **response}), flush=True)
    finally:
        provider.close()
        trace.close()
