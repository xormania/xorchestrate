"""Persistent native transports adapted from the accepted multi-harness proof.

Codex uses one app-server thread, Grok one ACP session, and Claude one native
TUI with MCP channel notifications. Messages never create a new conversation.
"""

import asyncio
import os
import signal
import subprocess
import time
import uuid
from collections import deque

from .api import API, SERVER, TOOLS
from .compat import ClaudeChannelLog, codex_capability
from .profiles import CODEX_FEATURES, claude_command, permitted, prepare, toml, tool_catalogue
from .rpc import RPC
from .serena_provider import SERVER as SERENA_SERVER
from .serena_provider import TOOL as SERENA_TOOL
from .serena_provider import enabled as serena_enabled
from .serena_provider import server_config as serena_config
from .store import Store
from .telemetry import Trace, Transcript, context, observed, requested


class Native:
    def __init__(self, directory, member, config, generation):
        self.store, self.member = Store(directory), member
        self.api = API(directory, member)
        self.control = self.api.control
        self.config, self.generation = config, generation
        self.workspace = config["workspace"]
        self.harness = config["harness"]
        from .harnesses import get

        self.protocol = get(self.harness).protocol
        if self.protocol not in {"codex", "grok"}:
            raise ValueError("This native backend does not implement the selected harness protocol.")
        self.private, self.env, self.server = prepare(directory, member, self.harness, config=config)
        self.trace = Trace(directory, member, context=lambda: context(self.store, member))
        requested(self.store, member, config, self.private)
        self.session = None
        self.busy = False
        self.prompts_in_flight = 0
        self.native_busy = False
        self.grok_turn = 0
        self.grok_prompt_id = None
        self.grok_seen_ids = deque(maxlen=128)
        self.grok_completed_ids = deque(maxlen=128)
        self.pending_interjections = set()
        self.rpc = self.mcp = None
        self.serena = None
        self.serena_server = (
            serena_config(directory, member) if serena_enabled(self.store, self.control.snapshot()) else None
        )
        self.tasks = set()

    def event(self, kind, **data):
        self.store.event(self.member, kind, **data)

    def state(self, state, **extra):
        self.store.session(self.member, generation=self.generation, state=state, **extra)

    async def drain_stderr(self, process, component):
        while line := await process.stderr.readline():
            self.trace.record(component + "_stderr", {"text": line.decode(errors="replace")})

    async def spawn(self, argv, handler, notify, jsonrpc=True, component="native"):
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=self.workspace,
            env=self.env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=4 * 1024 * 1024,
            start_new_session=True,
        )
        self.trace.record("process_started", {"component": component, "argv": argv, "pid": process.pid})
        from .processes import register

        register(self.store, self.member, self.generation, process.pid)
        rpc = RPC(process, handler, notify, jsonrpc=jsonrpc, trace=self.trace, component=component)
        task = asyncio.create_task(self.drain_stderr(process, component))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return rpc

    async def reject(self, method, params):
        raise ValueError("This MCP client has no additional capabilities")

    async def ignore(self, method, params):
        return None

    async def start(self):
        binary = self.config["binary"]
        if self.protocol == "codex":
            capability = await asyncio.to_thread(codex_capability, binary)
            self.event("protocol_capability", **capability)
            if capability["status"] != "supported":
                raise ValueError("Installed Codex must expose TurnStartParams.toolOutput for live communication")
            # Dynamic tools are a native proxy to a real stdio MCP server.
            self.mcp = await self.spawn(
                [self.server["command"], *self.server["args"]],
                self.reject,
                self.ignore,
                component="mcp",
            )
            await self.mcp.request(
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "native-codex-bridge", "version": "1"},
                },
            )
            await self.mcp.notify("notifications/initialized")
            catalogue = (await self.mcp.request("tools/list", {}))["tools"]
            if self.serena_server:
                self.serena = await self.spawn(
                    [self.serena_server["command"], *self.serena_server["args"]],
                    self.reject,
                    self.ignore,
                    component="serena",
                )
                await self.serena.request(
                    "initialize",
                    {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "native-codex-bridge", "version": "1"},
                    },
                )
                await self.serena.notify("notifications/initialized")
                catalogue.extend((await self.serena.request("tools/list", {}))["tools"])
            argv = [binary]
            for key, value in CODEX_FEATURES.items():
                argv += ["-c", f"features.{key}={toml(value)}"]
            argv += ["-c", "mcp_servers={}", "-c", 'web_search="disabled"', "app-server", "--listen", "stdio://"]
            self.rpc = await self.spawn(argv, self.request, self.notification, jsonrpc=False)
            await self.rpc.request(
                "initialize",
                {"clientInfo": {"name": "agentscient-live", "version": "1"}, "capabilities": {"experimentalApi": True}},
            )
            await self.rpc.notify("initialized")
            params = {
                "cwd": self.workspace,
                "sandbox": "read-only",
                "approvalPolicy": "untrusted",
                "developerInstructions": self.api.instructions(),
                "dynamicTools": [
                    {
                        "type": "function",
                        "name": row["name"],
                        "description": row["description"],
                        "inputSchema": row["inputSchema"],
                    }
                    for row in catalogue
                ],
            }
            if self.config.get("model"):
                params["model"] = self.config["model"]
            if self.config.get("reasoning"):
                params["config"] = {"model_reasoning_effort": self.config["reasoning"]}
            if self.config.get("provider"):
                params["modelProvider"] = self.config["provider"]
            result = await self.rpc.request("thread/start", params)
            self.session = result["thread"]["id"]
        else:
            argv = [binary, "--no-auto-update", "--no-subagents"]
            if self.config.get("model"):
                argv += ["--model", self.config["model"]]
            if self.config.get("reasoning"):
                argv += ["--effort", self.config["reasoning"]]
            self.rpc = await self.spawn(argv + ["agent", "stdio"], self.request, self.notification)
            result = await self.rpc.request("initialize", {"protocolVersion": 1, "clientCapabilities": {}})
            methods = [row["id"] for row in result.get("authMethods", [])]
            if methods:
                method = "xai.api_key" if self.env.get("XAI_API_KEY") and "xai.api_key" in methods else "cached_token"
                if method not in methods:
                    raise ValueError("Grok needs its existing local login or XAI_API_KEY")
                await self.rpc.request("authenticate", {"methodId": method, "_meta": {"headless": True}})
            result = await self.rpc.request(
                "session/new",
                {
                    "cwd": self.workspace,
                    "mcpServers": [
                        {"name": SERVER, "command": self.server["command"], "args": self.server["args"], "env": []}
                    ]
                    + ([{"name": SERENA_SERVER, **self.serena_server, "env": []}] if self.serena_server else []),
                },
            )
            self.session = result["sessionId"]
        self.state("idle", native_id=self.session, detail={"transport": self.harness})
        observed(self.store, self.member, self.harness + "/session-start", result)
        self.event("native_session", native_id=self.session, harness=self.harness)

    def verify_identity(self, params):
        observed = params.get("threadId", params.get("sessionId"))
        if observed is not None and self.session is not None and observed != self.session:
            raise ValueError("Native session identity changed during live coordination")

    async def request(self, method, params):
        self.verify_identity(params)
        if self.protocol == "codex" and method == "item/tool/call":
            name, args = params.get("tool"), params.get("arguments")
            if not permitted(self.harness, name, args, catalogue=tool_catalogue(self.store.directory, self.member)) or name not in {row["name"] for row in [*TOOLS, SERENA_TOOL]}:
                self.event("native_tool_denied", tool=name)
                raise ValueError("Use this team's MCP tools")
            provider = self.serena if name == SERENA_TOOL["name"] else self.mcp
            if provider is None:
                raise ValueError("This provider is not available in the current role.")
            result = await provider.request("tools/call", {"name": name, "arguments": args})
            self.event("native_tool", tool=name, call_id=params.get("callId"), native_id=self.session)
            return {
                "contentItems": [
                    {"type": "inputText", "text": row["text"]}
                    for row in result.get("content", [])
                    if row.get("type") == "text"
                ],
                "success": not result.get("isError", False),
            }
        if self.protocol == "grok" and method == "session/request_permission":
            call = params.get("toolCall", {})
            metadata = call.get("_meta", {}).get("x.ai/tool", {})
            allowed = metadata.get("namespace") == "grok_build" and permitted(
                self.harness, metadata.get("name"), call.get("rawInput", {}),
                catalogue=tool_catalogue(self.store.directory, self.member),
            )
            option = next(
                (
                    row
                    for row in params.get("options", [])
                    if row.get("kind") == ("allow_once" if allowed else "reject_once")
                ),
                None,
            )
            self.event("native_permission", allowed=allowed, tool=metadata.get("name"))
            return {
                "outcome": {"outcome": "selected", "optionId": option["optionId"]}
                if option
                else {"outcome": "cancelled"}
            }
        self.event("native_permission", allowed=False, method=method)
        if method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
            return {"decision": "decline"}
        if method == "item/permissions/requestApproval":
            return {"permissions": {}, "scope": "turn"}
        if method == "mcpServer/elicitation/request":
            return {"action": "decline", "content": None}
        raise ValueError("No native client capability for " + method)

    async def notification(self, method, params):
        self.verify_identity(params)
        if self.session is None:
            return
        update = params.get("update", {})
        if method in {"session/update", "thread/started", "thread/configuration/updated"}:
            observed(self.store, self.member, method, params)
        if method == "thread/tokenUsage/updated" or "usage" in method.lower() or "usage" in update:
            self.event(
                "native_usage",
                source=method,
                native_id=self.session,
                observation=params.get("tokenUsage", update.get("usage", params)),
            )
        if self.protocol == "codex":
            if method in {"turn/started", "turn/completed"}:
                self.busy = method == "turn/started"
                self.state("active" if self.busy else "idle")
                self.event(method.replace("/", "_"), native_id=self.session, turn=params.get("turn", {}))
            elif method in {"item/started", "item/completed"}:
                kind = params.get("item", {}).get("type", "")
                if kind in {"commandExecution", "fileChange", "webSearch", "imageView", "imageGeneration"} or (
                    kind.endswith("ToolCall") and kind != "dynamicToolCall"
                ):
                    self.event("native_tool_violation", tool=kind)
                    raise ValueError("Native non-MCP tool activity observed: " + kind)
        else:
            kind = update.get("sessionUpdate")
            prompt_id = params.get("promptId", update.get("prompt_id", params.get("_meta", {}).get("promptId")))
            if method == "_x.ai/session/prompt_complete" or kind == "turn_completed":
                self.grok_complete(prompt_id)
                self.event(
                    "turn_completed", native_id=self.session, turn=prompt_id
                )
            elif kind in {"agent_message_chunk", "agent_thought_chunk", "tool_call", "tool_call_update"}:
                self.grok_started(prompt_id)
        if method == "item/agentMessage/delta":
            print(params.get("delta", ""), end="", flush=True)
        elif update.get("sessionUpdate") == "agent_message_chunk":
            print(update.get("content", {}).get("text", ""), end="", flush=True)

    def delivered(self, message, error=None, transport=None):
        self.store.delivered(self.member, message["id"], message["attempt"], error=error, transport=transport)

    async def grok_prompt(self, message, prompt_id, turn):
        submitted = False

        async def sent():
            nonlocal submitted
            self.delivered(message, transport="grok/session/prompt")
            submitted = True

        try:
            result = await self.rpc.request(
                "session/prompt",
                {
                    "sessionId": self.session,
                    "prompt": [{"type": "text", "text": self.control.render(message)}],
                    "_meta": {"promptId": prompt_id},
                },
                timeout=3600,
                on_sent=sent,
            )
            if not isinstance(result, dict) or not isinstance(result.get("stopReason"), str) or not result["stopReason"]:
                raise ValueError("Grok session/prompt response is missing its stopReason")
            # ACP's response completes this prompt even without an extension notification.
            # An interjected turn may have started before this coroutine resumes.
            self.grok_complete(prompt_id, turn=turn)
        except Exception as error:
            # A written prompt may already have executed; do not automatically replay it.
            self.event("native_prompt_error", message=message["id"], error=str(error)[:1000])
            if not submitted:
                self.delivered(message, error=str(error)[:1000])
        finally:
            self.prompts_in_flight -= 1
            self.grok_activity()

    def grok_started(self, prompt_id=None):
        if prompt_id is not None:
            # Late chunks from a previously observed turn cannot replace newer activity.
            # Its outstanding request still counts as live until its own response arrives.
            if prompt_id in self.grok_completed_ids or (
                prompt_id != self.grok_prompt_id and prompt_id in self.grok_seen_ids
            ):
                return self.grok_turn
            if prompt_id not in self.grok_seen_ids:
                self.grok_seen_ids.append(prompt_id)
        # Without a provider ID, a completion followed by activity starts a new epoch.
        if not self.native_busy or (prompt_id is not None and prompt_id != self.grok_prompt_id):
            self.grok_turn += 1
            self.grok_prompt_id = prompt_id
        self.native_busy = True
        self.grok_activity()
        return self.grok_turn

    def grok_complete(self, prompt_id=None, *, turn=None):
        completed_id = self.grok_prompt_id if prompt_id is None else prompt_id
        if completed_id is not None and completed_id not in self.grok_completed_ids:
            self.grok_completed_ids.append(completed_id)
        if (turn is None or turn == self.grok_turn) and (
            prompt_id is None or prompt_id == self.grok_prompt_id
        ):
            self.native_busy = False
        self.grok_activity()

    def grok_activity(self):
        if self.pending_interjections:
            with self.store.connect() as db:
                acknowledged = {
                    row[0]
                    for row in db.execute(
                        "SELECT id FROM messages WHERE recipient=? AND acknowledged IS NOT NULL", (self.member,)
                    )
                }
            self.pending_interjections.difference_update(acknowledged)
        active = bool(self.prompts_in_flight or self.native_busy or self.pending_interjections)
        if active != self.busy:
            self.busy = active
            self.state("active" if active else "idle")

    async def deliver(self, message):
        try:
            if self.protocol == "codex":
                await self.rpc.request(
                    "turn/start",
                    {
                        "threadId": self.session,
                        "input": [],
                        "toolOutput": {
                            "name": "peer_message_received",
                            "namespace": None,
                            "output": self.control.render(message),
                        },
                    },
                )
                self.delivered(message, transport="codex/turn/start.toolOutput")
            elif self.busy:
                self.pending_interjections.add(message["id"])
                await self.rpc.request(
                    "_x.ai/interject",
                    {
                        "sessionId": self.session,
                        "text": self.control.render(message),
                        "interjectionId": str(message["id"]),
                    },
                )
                self.delivered(message, transport="grok/_x.ai/interject")
            else:
                self.prompts_in_flight += 1
                prompt_id = str(uuid.uuid4())
                turn = self.grok_started(prompt_id)
                task = asyncio.create_task(self.grok_prompt(message, prompt_id, turn))
                self.tasks.add(task)
                task.add_done_callback(self.tasks.discard)
        except Exception as error:
            self.pending_interjections.discard(message["id"])
            self.delivered(message, error=str(error)[:1000])

    async def run(self):
        try:
            await self.start()
            while not self.rpc.dead.is_set() and self.store.member(self.member)["desired"] == "running":
                if self.protocol == "grok":
                    self.grok_activity()
                message = self.store.take_delivery(self.member, scoped=True, generation=self.generation)
                if message:
                    await self.deliver(message)
                else:
                    await asyncio.sleep(0.1)
            if self.rpc.dead.is_set() and self.store.member(self.member)["desired"] == "running":
                raise RuntimeError(self.rpc.failure or "Native transport stopped")
        finally:
            for task in list(self.tasks):
                task.cancel()
            await asyncio.gather(*self.tasks, return_exceptions=True)
            for rpc in (self.rpc, self.mcp, self.serena):
                if rpc:
                    await rpc.close()
                    try:
                        os.killpg(rpc.process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
            self.trace.close()


def claude(directory, member, config, generation):
    store = Store(directory)
    private, env, _ = prepare(directory, member, config["harness"], config=config)
    argv, native_id = claude_command(
        config, private, API(directory, member).instructions(),
        tool_catalogue(directory, member, package_ceiling=True),
        "Stay in this conversation and handle live peer messages through " + SERVER + ".",
    )
    channel_log = ClaudeChannelLog(private / "native-debug.log")
    trace = Trace(directory, member, component="claude", context=lambda: context(store, member))
    transcript = Transcript(store, member, trace)
    requested(store, member, config, private)
    process = subprocess.Popen(argv, cwd=config["workspace"], env=env, start_new_session=True)
    from .processes import register

    register(store, member, generation, process.pid)
    trace.record("process_started", {"argv": argv, "pid": process.pid})
    store.session(member, generation=generation, native_id=native_id, detail={"transport": "claude/channel"})
    ready = False
    try:
        while process.poll() is None and store.member(member)["desired"] == "running":
            transcript.poll()
            if not ready:
                if observation := channel_log.poll():
                    ready = True
                    store.session(member, generation=generation, state="idle")
                    store.event(member, "channel_ready", native_id=native_id, **observation)
            time.sleep(0.1)
        if process.returncode and store.member(member)["desired"] == "running":
            raise RuntimeError(f"Claude exited with status {process.returncode}; inspect its terminal")
    finally:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        transcript.poll()
        trace.close()
