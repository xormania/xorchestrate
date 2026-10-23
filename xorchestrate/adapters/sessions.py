"""Configured, process-owned sessions behind the application session port.

Session handles identify a connection owned by this driver instance; they never
contain a PID or permit attaching to an unrelated process. Native conversation
IDs may be retained and explicitly resumed with a new start call. A saved handle
alone does not claim a process survived an application restart.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..model import JSON, OrchestrationError
from .jsonrpc import JsonLineProcess, command_options, decode_json, encode_json, positive_integer


def _object(value: Any, name: str, code: str = "invalid_request") -> JSON:
    if not isinstance(value, dict):
        raise OrchestrationError(f"{name} must be a JSON object.", code, {"outcome": "unknown" if code == "provider_protocol" else "not_started"})
    # Do not retain caller-owned mutable data or non-JSON values.
    return decode_json(encode_json(value))


def _identifier(value: Any, name: str = "session ID") -> str:
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise OrchestrationError(f"The session provider returned an invalid {name}.", "provider_protocol", {"outcome": "unknown"})
    return value


@dataclass
class _LiveSession:
    context: JSON
    native_id: str | None = None
    process: JsonLineProcess | None = None
    lock: threading.RLock = field(default_factory=threading.RLock)


class _ProcessSessions:
    protocol = "jsonrpc"
    jsonrpc = True
    resumable = False

    def __init__(self, options: JSON, request_handler: Callable | None = None):
        self.options = _object(options, "Session options", "invalid_configuration")
        command_options(self.options)  # Validate configuration before the first effect.
        self.max_sessions = positive_integer(self.options, "max_sessions", 64)
        self._handler = request_handler
        self._instance = uuid.uuid4().hex
        self._sessions: dict[str, _LiveSession] = {}
        self._starting = 0
        self._lock = threading.RLock()
        self._closed = False

    def set_request_handler(self, handler: Callable | None) -> None:
        """Bind trusted composition behavior, never a tool-supplied callback."""
        if handler is not None and not callable(handler):
            raise OrchestrationError("Session request handler must be callable.", "invalid_configuration")
        with self._lock:
            if self._sessions or self._starting:
                raise OrchestrationError("Bind session handlers before starting sessions.", "conflict")
            self._handler = handler

    def _verify_identity(self, live: _LiveSession, params: JSON) -> None:
        for name in ("threadId", "sessionId", "session_id"):
            observed = params.get(name)
            if observed is not None and observed != live.native_id:
                raise OrchestrationError("Native session identity does not match the owned connection.", "session_identity")

    def _request(self, live: _LiveSession, method: str, params: Any) -> JSON:
        params = _object(params, "Native request", "provider_protocol")
        self._verify_identity(live, params)
        if live.native_id is None:
            raise OrchestrationError("The native session has not established its identity.", "session_identity")
        if self._handler is None:
            raise OrchestrationError("No handler is configured for this native request.", "unsupported_method")
        return self._handler(method, params, _object(live.context, "Context"))

    def _new(self, specification: JSON) -> _LiveSession:
        with self._lock:
            if self._closed:
                raise OrchestrationError("Session driver is closed.", "session_unavailable", {"outcome": "not_started"})
            if len(self._sessions) + self._starting >= self.max_sessions:
                raise OrchestrationError("Configured concurrent session limit reached.", "session_capacity", {"outcome": "not_started"})
            self._starting += 1
        try:
            context = _object(specification.get("context", {}), "Session context")
            live = _LiveSession(context=context)
            live.process = JsonLineProcess(self.options, lambda method, params: self._request(live, method, params), jsonrpc=self.jsonrpc)
            return live
        except BaseException:
            with self._lock:
                self._starting -= 1
            raise

    def _publish(self, live: _LiveSession) -> JSON:
        handle = uuid.uuid4().hex
        with self._lock:
            if self._closed:
                raise OrchestrationError("Session driver closed while starting the session.", "session_unavailable")
            self._sessions[handle] = live
        return {"instance": self._instance, "handle": handle, "native_id": live.native_id,
                "protocol": self.protocol, "resumable": self.resumable}

    def _start(self, specification: JSON, handshake: Callable) -> JSON:
        specification = _object(specification, "Session specification")
        live = self._new(specification)
        try:
            handshake(live, specification)
            return self._publish(live)
        except BaseException:
            live.process.close()
            raise
        finally:
            with self._lock:
                self._starting -= 1

    def _get(self, session: JSON) -> _LiveSession:
        session = _object(session, "Session handle")
        with self._lock:
            handle = session.get("handle")
            if not isinstance(handle, str) or session.get("instance") != self._instance:
                raise OrchestrationError("Session connection is unavailable in this driver instance. Explicitly resume its native ID if supported.", "session_unavailable", {"outcome": "not_started"})
            live = self._sessions.get(handle)
            if live is None or session.get("native_id") != live.native_id or session.get("protocol") != self.protocol:
                raise OrchestrationError("Session handle does not identify an owned connection.", "session_unavailable", {"outcome": "not_started"})
            return live

    def _notifications(self, live: _LiveSession) -> list[JSON]:
        result = live.process.poll_notifications()
        try:
            for packet in result:
                self._verify_identity(live, _object(packet.get("params", {}), "Native notification", "provider_protocol"))
        except OrchestrationError:
            live.process.close()
            raise
        return result

    def poll(self, session: JSON) -> list[JSON]:
        live = self._get(session)
        with live.lock:
            return self._notifications(live)

    def stop(self, session: JSON) -> JSON:
        live = self._get(session)
        with live.lock:
            live.process.close()
            with self._lock:
                self._sessions.pop(session["handle"], None)
        return {"stopped": True, "native_id": live.native_id}

    def close(self) -> None:
        with self._lock:
            self._closed = True
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for live in sessions:
            with live.lock:
                live.process.close()


class JSONRPCSessionDriver(_ProcessSessions):
    """Bridge contract: start -> session_id; send -> object; poll -> events.

    The configured executable reads JSON-RPC lines from stdin. Method names are
    configurable through ``methods``. ``initialize`` optionally supplies a
    method and params. No command, environment, or executable comes from a
    session specification. The entire specification is passed as ordinary data.
    """

    def __init__(self, options: JSON, request_handler: Callable | None = None):
        super().__init__(options, request_handler)
        methods = _object(self.options.get("methods", {}), "Session methods", "invalid_configuration")
        self.methods = {operation: methods.get(operation, f"session/{operation}") for operation in ("start", "send", "poll", "stop")}
        if any(not isinstance(value, str) or not value for value in self.methods.values()):
            raise OrchestrationError("Session methods must be nonempty strings.", "invalid_configuration")
        self.initialize = self.options.get("initialize")
        if self.initialize is not None:
            self.initialize = _object(self.initialize, "Session initialization", "invalid_configuration")
            if not isinstance(self.initialize.get("method"), str) or not self.initialize["method"]:
                raise OrchestrationError("Session initialization needs a method.", "invalid_configuration")
            _object(self.initialize.get("params", {}), "Session initialization params", "invalid_configuration")

    def start(self, specification: JSON) -> JSON:
        def handshake(live, spec):
            if self.initialize is not None:
                live.process.request(self.initialize["method"], self.initialize.get("params", {}))
            result = _object(live.process.request(self.methods["start"], spec), "Session start result", "provider_protocol")
            live.native_id = _identifier(result.get("session_id"))
        return self._start(specification, handshake)

    def send(self, session: JSON, message: JSON) -> JSON:
        message = _object(message, "Session message")
        live = self._get(session)
        with live.lock:
            return _object(live.process.request(self.methods["send"], {"session_id": live.native_id, "message": message}), "Session send result", "provider_protocol")

    def poll(self, session: JSON) -> list[JSON]:
        live = self._get(session)
        with live.lock:
            result = _object(live.process.request(self.methods["poll"], {"session_id": live.native_id}), "Session poll result", "provider_protocol")
            events = result.get("events")
            if not isinstance(events, list) or any(not isinstance(event, dict) for event in events):
                raise OrchestrationError("Session poll result must contain an events array of objects.", "provider_protocol", {"outcome": "unknown"})
            return [*self._notifications(live), *events]

    def stop(self, session: JSON) -> JSON:
        live = self._get(session)
        with live.lock:
            try:
                result = _object(live.process.request(self.methods["stop"], {"session_id": live.native_id}), "Session stop result", "provider_protocol")
            finally:
                super().stop(session)
            return {"stopped": True, "native_id": live.native_id, "result": result}


class CodexSessionDriver(_ProcessSessions):
    """Codex app-server adapter, with explicit process and native policy config.

    ``command`` contains the complete argv (typically codex app-server --listen
    stdio://). ``thread`` holds configured thread/start parameters. A start may
    supply ``parameters`` for unset fields and ``resume`` for an existing thread.
    Configured parameters win over requested values. Native tools remain under
    the configured Codex sandbox/approval settings; this adapter does not silently
    disable native capabilities or implicitly approve requests.
    """

    protocol = "codex"
    jsonrpc = False
    resumable = True

    def __init__(self, options: JSON, request_handler: Callable | None = None):
        super().__init__(options, request_handler)
        self.thread = _object(self.options.get("thread", {}), "Codex thread parameters", "invalid_configuration")
        self.turn = _object(self.options.get("turn", {}), "Codex turn parameters", "invalid_configuration")
        if "threadId" in self.thread or "threadId" in self.turn or "input" in self.turn:
            raise OrchestrationError("Native thread IDs and turn input belong to the active session.", "invalid_configuration")
        if "dynamic_tools" in self.options:
            self.set_tools(self.options["dynamic_tools"])

    def set_tools(self, tools: list[JSON]) -> None:
        with self._lock:
            if self._sessions or self._starting:
                raise OrchestrationError("Bind dynamic tools before starting sessions.", "conflict")
            if not isinstance(tools, list):
                raise OrchestrationError("Dynamic tools must be an array.", "invalid_configuration")
            result = []
            names = set()
            for item in tools:
                tool = _object(item, "Dynamic tool", "invalid_configuration")
                name = tool.get("name")
                if not isinstance(name, str) or not name or name in names or not isinstance(tool.get("description", ""), str):
                    raise OrchestrationError("Dynamic tools require unique names and string descriptions.", "invalid_configuration")
                schema = _object(tool.get("inputSchema", {"type": "object"}), "Dynamic tool schema", "invalid_configuration")
                names.add(name)
                result.append({"type": "function", "name": name, "description": tool.get("description", ""), "inputSchema": schema})
            self.thread["dynamicTools"] = result

    def _request(self, live: _LiveSession, method: str, params: Any) -> JSON:
        if method != "item/tool/call":
            return super()._request(live, method, params)
        try:
            result = super()._request(live, method, params)
            return {"contentItems": [{"type": "inputText", "text": encode_json(result).decode("utf-8")}], "success": True}
        except OrchestrationError as exc:
            result = {"error": {"code": exc.code, "message": str(exc), "details": exc.details}}
            return {"contentItems": [{"type": "inputText", "text": encode_json(result).decode("utf-8")}], "success": False}

    def start(self, specification: JSON) -> JSON:
        def handshake(live, spec):
            live.process.request("initialize", {"clientInfo": {"name": "xorchestrate", "version": "0.2.0"}, "capabilities": {"experimentalApi": True}})
            live.process.notify("initialized")
            requested = _object(spec.get("parameters", {}), "Codex thread parameters")
            # Process ownership and dynamic callbacks are composition decisions.
            if "threadId" in requested or "dynamicTools" in requested:
                raise OrchestrationError("Use resume for a thread ID and configure dynamic tools on the driver.", details={"outcome": "not_started"})
            params = {**requested, **self.thread}
            resume = spec.get("resume")
            if resume is not None:
                live.native_id = _identifier(resume, "resume ID")
                params["threadId"] = live.native_id
            result = _object(live.process.request("thread/resume" if resume is not None else "thread/start", params), "Codex thread result", "provider_protocol")
            thread = _object(result.get("thread"), "Codex thread", "provider_protocol")
            native_id = _identifier(thread.get("id"))
            if resume is not None and native_id != resume:
                raise OrchestrationError("Resumed thread identity changed.", "session_identity")
            live.native_id = native_id
        return self._start(specification, handshake)

    def send(self, session: JSON, message: JSON) -> JSON:
        message = _object(message, "Session message")
        text = message.get("text")
        if text is None:
            text = encode_json(message).decode("utf-8")
        if not isinstance(text, str):
            raise OrchestrationError("Session message text must be a string.", details={"outcome": "not_started"})
        live = self._get(session)
        with live.lock:
            return _object(live.process.request("turn/start", {**self.turn, "threadId": live.native_id, "input": [{"type": "text", "text": text}]}), "Codex turn result", "provider_protocol")


class ACPSessionDriver(_ProcessSessions):
    """Agent Client Protocol over configured stdio, without vendor CLI assumptions.

    ACP prompt responses complete a turn, so send is synchronous and subject to
    the configured request timeout. Streamed notifications are retained for poll.
    Native filesystem/terminal client requests require an explicitly bound
    handler; no filesystem or shell capability is advertised by default.
    """

    protocol = "acp"

    def __init__(self, options: JSON, request_handler: Callable | None = None):
        super().__init__(options, request_handler)
        self.session = _object(self.options.get("session", {}), "ACP session parameters", "invalid_configuration")
        self.initialize = _object(self.options.get("initialize", {"protocolVersion": 1, "clientCapabilities": {}}), "ACP initialization", "invalid_configuration")
        self.authentication = self.options.get("authentication")
        if self.authentication is not None:
            self.authentication = _object(self.authentication, "ACP authentication", "invalid_configuration")
        self.resumable = self.options.get("resume_supported", False)
        if not isinstance(self.resumable, bool):
            raise OrchestrationError("resume_supported must be boolean.", "invalid_configuration")
        if "sessionId" in self.session:
            raise OrchestrationError("Native session IDs belong to the active session.", "invalid_configuration")

    def start(self, specification: JSON) -> JSON:
        def handshake(live, spec):
            initialized = _object(live.process.request("initialize", self.initialize), "ACP initialization result", "provider_protocol")
            capabilities = _object(initialized.get("agentCapabilities", {}), "ACP agent capabilities", "provider_protocol")
            if self.authentication is not None:
                live.process.request("authenticate", self.authentication)
            requested = _object(spec.get("parameters", {}), "ACP session parameters")
            if "sessionId" in requested:
                raise OrchestrationError("Use resume for an existing ACP session ID.", details={"outcome": "not_started"})
            params = {**requested, **self.session}
            resume = spec.get("resume")
            if resume is not None:
                if not self.resumable or capabilities.get("loadSession") is not True:
                    raise OrchestrationError("ACP session loading is not enabled and advertised by this agent.", "unsupported_operation", {"outcome": "not_started"})
                live.native_id = _identifier(resume, "resume ID")
                params["sessionId"] = live.native_id
                _object(live.process.request("session/load", params), "ACP session load result", "provider_protocol")
            else:
                result = _object(live.process.request("session/new", params), "ACP session result", "provider_protocol")
                live.native_id = _identifier(result.get("sessionId"))
        return self._start(specification, handshake)

    def send(self, session: JSON, message: JSON) -> JSON:
        message = _object(message, "Session message")
        text = message.get("text")
        if text is None:
            text = encode_json(message).decode("utf-8")
        if not isinstance(text, str):
            raise OrchestrationError("Session message text must be a string.", details={"outcome": "not_started"})
        live = self._get(session)
        with live.lock:
            result = _object(live.process.request("session/prompt", {"sessionId": live.native_id, "prompt": [{"type": "text", "text": text}]}), "ACP prompt result", "provider_protocol")
            if not isinstance(result.get("stopReason"), str) or not result["stopReason"]:
                raise OrchestrationError("ACP prompt did not return a stop reason.", "provider_protocol", {"outcome": "unknown"})
            return result
