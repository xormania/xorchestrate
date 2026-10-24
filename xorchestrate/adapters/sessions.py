"""Native adapters with distinct conversation, connection and activity identities.

Configured process drivers own their subprocesses; externally managed conversations
use the persistent bridge adapter. A saved backend receipt is not a live connection.
Submission results never establish native receipt, completion or work acceptance.
"""

from __future__ import annotations

import hashlib
import threading
import uuid
from collections import deque
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..model import JSON, Capability, OrchestrationError
from ..ports import ApplicationToolCallback
from ..schema import check_schema, validate
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
    incarnation: str = field(default_factory=lambda: uuid.uuid4().hex)
    activity: str = "idle"
    settings: JSON = field(default_factory=lambda: {"requested": {}, "resolved": {}, "observed": {}})
    events: list[JSON] = field(default_factory=list)
    calls: dict[str, tuple[str, JSON]] = field(default_factory=dict)
    pending: dict[str, Any] = field(default_factory=dict)
    closed: bool = False
    preidentity: list[JSON] = field(default_factory=list)
    completed: deque = field(default_factory=lambda: deque(maxlen=1024))
    seen_turns: deque = field(default_factory=lambda: deque(maxlen=1024))


class _ProcessSessions:
    protocol = "jsonrpc"
    jsonrpc = True
    resumable = False
    contract_version = "native-session-v1"

    def __init__(self, options: JSON, request_handler: Callable | None = None):
        self.options = _object(options, "Session options", "invalid_configuration")
        command_options(self.options)  # Validate configuration before the first effect.
        self.max_sessions = positive_integer(self.options, "max_sessions", 64)
        self.max_native_calls = positive_integer(self.options, "max_native_calls", 10000)
        if not isinstance(self.options.get("allow_configuration_change_on_resume", False), bool):
            raise OrchestrationError("allow_configuration_change_on_resume must be boolean.", "invalid_configuration")
        self._handler = request_handler
        self._instance = uuid.uuid4().hex
        self._sessions: dict[str, _LiveSession] = {}
        self._starting = 0
        self._lock = threading.RLock()
        self._closed = False
        self._observer = None
        self._application_replay = None
        self.configuration_snapshot_id = hashlib.sha256(encode_json(self.options, sort_keys=True)).hexdigest()
        operational = {"timeout", "turn_timeout", "callback_timeout", "callback_workers", "shutdown_timeout",
                       "gateway_timeout", "gateway_workers", "observation_timeout", "allow_configuration_change_on_resume"}
        material = {key: value for key, value in self.options.items() if key not in operational and not key.startswith("max_")}
        self.configuration_id = hashlib.sha256(encode_json({"protocol": self.protocol,
            "contract_version": self.contract_version, "options": material}, sort_keys=True)).hexdigest()

    def bind_observer(self, observer: Callable) -> None:
        if not callable(observer):
            raise OrchestrationError("Session observer must be callable.", "invalid_configuration")
        with self._lock:
            if self._sessions or self._starting:
                raise OrchestrationError("Bind observers before starting sessions.", "conflict")
            self._observer = observer

    def bind_application_replay(self, callback: ApplicationToolCallback) -> None:
        """Bind a read-only replay validator, separate from effectful dispatch."""
        if not callable(callback):
            raise OrchestrationError("Application replay validator must be callable.", "invalid_configuration")
        with self._lock:
            if self._sessions or self._starting:
                raise OrchestrationError("Bind replay validation before starting sessions.", "conflict")
            self._application_replay = callback

    def _observation(self, live: _LiveSession, kind: str, **fields) -> JSON:
        event = {"kind": kind, "source": self.protocol, "native_id": live.native_id,
                 "incarnation": live.incarnation, "at": datetime.now(timezone.utc).isoformat(), **deepcopy(fields)}
        if self._observer and live.native_id is not None:
            self._observer(deepcopy(live.context), deepcopy(event))
        return event

    def _notification(self, live: _LiveSession, method: str, params: JSON) -> None:
        params = _object(params, "Native notification", "provider_protocol")
        if live.native_id is None:
            if len(live.preidentity) >= self.options.get("max_pending_messages", 1024):
                raise OrchestrationError("Too many notifications before native identity.", "provider_protocol")
            live.preidentity.append({"method": method, "params": params})
            return
        self._verify_identity(live, params)
        self._observation(live, "native_notification", method=method, payload=params)

    def _callback_context(self, live: _LiveSession, action: str, call_id: str | None) -> JSON:
        return {**_object(live.context, "Context"), "native_id": live.native_id,
                "incarnation": live.incarnation, "call_id": call_id, "action": action}

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

    def _verified_request(self, live: _LiveSession, params: Any) -> JSON:
        params = _object(params, "Native request", "provider_protocol")
        if live.closed:
            raise OrchestrationError("Native callback belongs to a closed connection.", "session_unavailable")
        self._verify_identity(live, params)
        if live.native_id is None:
            raise OrchestrationError("The native session has not established its identity.", "session_identity")
        return params

    def _request(self, live: _LiveSession, method: str, params: Any) -> JSON:
        params = self._verified_request(live, params)
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
            live.process = JsonLineProcess(self.options, lambda method, params: self._request(live, method, params), jsonrpc=self.jsonrpc,
                                           notification_handler=lambda method, params: self._notification(live, method, params))
            return live
        except BaseException:
            with self._lock:
                self._starting -= 1
            raise

    def _publish(self, live: _LiveSession) -> JSON:
        handle = uuid.uuid4().hex
        queued, live.preidentity = live.preidentity, []
        for packet in queued:
            self._notification(live, packet["method"], packet["params"])
        with self._lock:
            if self._closed:
                raise OrchestrationError("Session driver closed while starting the session.", "session_unavailable")
            self._sessions[handle] = live
        receipt = {"instance": self._instance, "handle": handle, "native_id": live.native_id,
                   "protocol": self.protocol, "resumable": self.resumable, "incarnation": live.incarnation,
                   "ownership": "owned", "connection_state": "connected", "activity": live.activity,
                   "configuration_id": self.configuration_id, "configuration_snapshot_id": self.configuration_snapshot_id,
                   "settings": deepcopy(live.settings)}
        self._observation(live, "session_started", settings=live.settings, configuration_id=self.configuration_id,
                          ownership="owned", connection_state="connected")
        return receipt

    def _start(self, specification: JSON, handshake: Callable) -> JSON:
        specification = _object(specification, "Session specification")
        live = self._new(specification)
        try:
            handshake(live, specification)
            return self._publish(live)
        except BaseException:
            live.closed = True
            live.process.close()
            if hasattr(live, "gateway"):
                live.gateway.close()
            with self._lock:
                for key, current in list(self._sessions.items()):
                    if current is live:
                        self._sessions.pop(key)
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
            if (live is None or live.closed or session.get("native_id") != live.native_id
                    or session.get("protocol") != self.protocol or session.get("incarnation", live.incarnation) != live.incarnation):
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
        result = self._notifications(live)
        with live.lock:
            extra, live.events = live.events, []
        return [*result, *extra]

    def status(self, session: JSON) -> JSON:
        live = self._get(session)
        return {"native_id": live.native_id, "incarnation": live.incarnation,
                "connection_state": "connected" if live.process.process.poll() is None else "disconnected",
                "activity": live.activity, "settings": deepcopy(live.settings), "ownership": "owned"}

    def detach(self, session: JSON) -> JSON:
        # An owned stdio pipe cannot outlive its driver. End that connection while
        # retaining the resumable conversation receipt. External bridge detach is
        # deliberately different and does not terminate the native process.
        result = self.stop(session)
        return {**result, "detached": True, "ownership": "owned", "connection_state": "disconnected"}

    def reconnect(self, source: JSON, specification: JSON) -> JSON:
        resume = getattr(self, "resume", None)
        if not callable(resume):
            raise OrchestrationError("This adapter does not support reconnect.", "unsupported_operation", {"outcome": "not_started"})
        return resume(source, specification)

    def deliver(self, session: JSON, message: JSON) -> JSON:
        return self.send(session, message)

    def stop(self, session: JSON) -> JSON:
        live = self._get(session)
        live.closed = True
        live.process.close()
        with self._lock:
            self._sessions.pop(session["handle"], None)
        self._observation(live, "session_disconnected", ownership="owned")
        return {"stopped": True, "native_id": live.native_id, "incarnation": live.incarnation}

    def close(self) -> None:
        with self._lock:
            self._closed = True
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for live in sessions:
            live.closed = True
            live.process.close()
            self._observation(live, "session_disconnected", ownership="owned")


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


class _NativeSessions(_ProcessSessions):
    """Native start parameters require a delegation from trusted composition."""

    def __init__(self, options: JSON, request_handler: Callable | None = None):
        super().__init__(options, request_handler)
        self.request_parameters = self.options.get("request_parameters", {"type": "object", "additionalProperties": False})
        check_schema(self.request_parameters)

    def _prepare_start(self, specification: JSON, reserved: tuple[str, ...]) -> tuple[JSON, JSON]:
        specification = _object(specification, "Session specification")
        if "resume" in specification:
            raise OrchestrationError("Resume requires an authorized source session, not a caller-supplied native ID.", details={"outcome": "not_started"})
        requested = _object(specification.get("parameters", {}), "Native session parameters")
        try:
            validate(requested, self.request_parameters)
        except OrchestrationError as exc:
            exc.details["outcome"] = "not_started"
            raise
        if any(name in requested for name in reserved):
            raise OrchestrationError("Native session identity and dynamic tools are owned by the configured driver.", details={"outcome": "not_started"})
        return specification, requested

    def start(self, specification: JSON) -> JSON:
        return self._launch(specification, None)

    def resume(self, session: JSON, specification: JSON) -> JSON:
        """Resume a backend selected and authorized by the application."""
        session = _object(session, "Resume source")
        if not self.resumable:
            raise OrchestrationError("Session resumption is not enabled for this driver.", "unsupported_operation", {"outcome": "not_started"})
        native_id = session.get("native_id")
        if session.get("protocol") != self.protocol or not isinstance(native_id, str) or not native_id or len(native_id) > 4096:
            raise OrchestrationError("Resume source does not identify a native session for this protocol.", "session_identity", {"outcome": "not_started"})
        specification = _object(specification, "Session specification")
        previous = session.get("configuration_id")
        if previous and previous != self.configuration_id and not self.options.get("allow_configuration_change_on_resume", False):
            raise OrchestrationError("Configured driver changed since the measured session; explicit trusted resume policy is required.",
                                     "configuration_changed", {"outcome": "not_started", "previous": previous, "current": self.configuration_id})
        if "parameters" not in specification and session.get("settings", {}).get("requested"):
            specification["parameters"] = deepcopy(session["settings"]["requested"])
        result = self._launch(specification, native_id)
        if session.get("instance") == self._instance:
            try:
                self._get(session)
            except OrchestrationError:
                pass  # A previous detach/stop already released this connection.
            else:
                self.stop(session)
                result["replaced_connection"] = {"incarnation": session.get("incarnation"), "state": "closed"}
        if previous and previous != self.configuration_id:
            result["configuration_change"] = {"from": previous, "to": self.configuration_id,
                                               "authorized_by": "trusted_configuration"}
        return result


class CodexSessionDriver(_NativeSessions):
    """Codex app-server adapter, with explicit process and native policy config.

    ``command`` contains the complete argv (typically codex app-server --listen
    stdio://). ``thread`` holds configured thread/start parameters. ``resume``
    accepts an application-authorized stored backend. Requested ``parameters`` require
    an explicit ``request_parameters`` JSON Schema in trusted configuration;
    the default accepts an empty object only. Configured thread parameters win
    over delegated values. Native tools remain under
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
        self._application_tools: dict[str, Capability] = {}
        self._application_callback: ApplicationToolCallback | None = None
        permissions = _object(self.options.get("native_permissions", {}), "Native permission responses", "invalid_configuration")
        if any(not isinstance(method, str) or not method or not isinstance(response, dict) for method, response in permissions.items()):
            raise OrchestrationError("Native permission responses must map methods to response objects.", "invalid_configuration")
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

    def bind_application_tools(self, capabilities: list[Capability], callback: ApplicationToolCallback) -> None:
        if not callable(callback) or not isinstance(capabilities, list) or any(not isinstance(item, Capability) for item in capabilities):
            raise OrchestrationError("Application tools require capability descriptors and a callback.", "invalid_configuration")
        capabilities = deepcopy(capabilities)
        with self._lock:
            configured = [tool for tool in self.thread.get("dynamicTools", []) if tool.get("name") not in self._application_tools]
            self.set_tools([*configured, *[
                {"name": item.name, "description": item.description, "inputSchema": item.input_schema}
                for item in capabilities
            ]])
            self._application_tools = {item.name: item for item in capabilities}
            self._application_callback = callback

    def _application_request(self, live: _LiveSession, params: JSON) -> JSON:
        arguments = params.get("arguments", {})
        if isinstance(arguments, str):
            arguments = decode_json(arguments.encode("utf-8"))
        arguments = _object(arguments, "Tool arguments")
        key = None
        if "callId" in params:
            call_id = params["callId"]
            if not isinstance(call_id, str) or not call_id:
                raise OrchestrationError("Native tool callId must be a nonempty string.")
            key = "codex:" + encode_json([live.native_id, call_id]).decode("utf-8")
        call_id = _identifier(params.get("callId"), "native tool call ID")
        if params.get("threadId") != live.native_id:
            raise OrchestrationError("Application tools require an independently observed native thread ID.", "session_identity")
        trusted = self._callback_context(live, params["tool"], call_id)
        self._observation(live, "native_tool_call", call_id=call_id, action=params["tool"], arguments=arguments)
        result = self._application_callback(params["tool"], arguments, trusted, key)
        self._observation(live, "native_tool_result", call_id=call_id, action=params["tool"], arguments=arguments, result=result)
        return result

    def _request(self, live: _LiveSession, method: str, params: Any) -> JSON:
        if method != "item/tool/call":
            configured = self.options.get("native_permissions", {})
            if method in configured:
                params = self._verified_request(live, params)
                self._observation(live, "native_permission", method=method, payload=params, decision=configured[method])
                return deepcopy(configured[method])
            return super()._request(live, method, params)
        claimed = False
        call_id = None
        fingerprint = None
        try:
            params = self._verified_request(live, params)
            action = params.get("tool")
            call_id = params.get("callId")
            fingerprint = hashlib.sha256(encode_json([action, params.get("arguments", {})], sort_keys=True)).hexdigest()
            replay = None
            if call_id is not None:
                _identifier(call_id, "tool call ID")
                with live.lock:
                    prior = live.calls.get(call_id)
                    if prior:
                        if prior[0] != fingerprint:
                            raise OrchestrationError("Native call ID was reused with different arguments.", "call_id_conflict")
                        if prior[1] is None:
                            raise OrchestrationError("The native call is already in progress.", "call_in_progress")
                        replay = deepcopy(prior[1])
                    else:
                        if len(live.calls) >= self.max_native_calls:
                            raise OrchestrationError("Native call replay ledger is full.", "session_capacity")
                        live.calls[call_id] = (fingerprint, None)
                        claimed = True
            if replay is not None:
                if replay.get("success") and action in self._application_tools and self._application_replay:
                    arguments = params.get("arguments", {})
                    if isinstance(arguments, str):
                        arguments = decode_json(arguments.encode("utf-8"))
                    result = self._application_replay(action, _object(arguments, "Tool arguments"),
                        self._callback_context(live, action, call_id),
                        "codex:" + encode_json([live.native_id, call_id]).decode("utf-8"))
                    return {"contentItems": [{"type": "inputText", "text": encode_json(result).decode("utf-8")}], "success": True}
                return replay
            if self._application_callback is not None and isinstance(action, str) and action in self._application_tools:
                result = self._application_request(live, params)
            else:
                result = super()._request(live, method, params)
            response = {"contentItems": [{"type": "inputText", "text": encode_json(result).decode("utf-8")}], "success": True}
            if call_id is not None:
                with live.lock:
                    live.calls[call_id] = (fingerprint, deepcopy(response))
            return response
        except OrchestrationError as exc:
            result = {"error": {"code": exc.code, "message": str(exc), "details": exc.details}}
            response = {"contentItems": [{"type": "inputText", "text": encode_json(result).decode("utf-8")}], "success": False}
            if claimed:
                with live.lock:
                    live.calls[call_id] = (fingerprint, deepcopy(response))
            return response

    def _launch(self, specification: JSON, resume: str | None) -> JSON:
        specification, requested = self._prepare_start(specification, ("threadId", "dynamicTools"))

        def handshake(live, spec):
            live.process.request("initialize", {"clientInfo": {"name": "xorchestrate", "version": "0.2.0"}, "capabilities": {"experimentalApi": True}})
            live.process.notify("initialized")
            params = {**requested, **self.thread}
            if resume is not None:
                live.native_id = _identifier(resume, "resume ID")
                params["threadId"] = live.native_id
            result = _object(live.process.request("thread/resume" if resume is not None else "thread/start", params), "Codex thread result", "provider_protocol")
            thread = _object(result.get("thread"), "Codex thread", "provider_protocol")
            native_id = _identifier(thread.get("id"))
            if resume is not None and native_id != resume:
                raise OrchestrationError("Resumed thread identity changed.", "session_identity")
            live.native_id = native_id
            observed = {key: result.get(key, thread.get(key)) for key in ("model", "modelProvider", "reasoningEffort")}
            live.settings = {"requested": requested, "resolved": deepcopy(params), "observed": observed,
                             "precedence": "trusted_configuration"}
        return self._start(specification, handshake)

    def _notification(self, live: _LiveSession, method: str, params: JSON) -> None:
        super()._notification(live, method, params)
        turn = params.get("turn", {})
        turn_id = turn.get("id", params.get("turnId"))
        with live.lock:
            if method == "turn/started":
                if turn_id in live.completed or (turn_id in live.seen_turns and turn_id != live.pending.get("turn")):
                    self._observation(live, "stale_native_activity", turn_id=turn_id, method=method)
                    return
                if turn_id not in live.seen_turns:
                    live.seen_turns.append(turn_id)
                live.activity = "active"
                live.pending["turn"] = turn_id
            elif method == "turn/completed":
                if turn_id is not None:
                    live.completed.append(turn_id)
                stale = bool(live.pending.get("turn") and turn_id != live.pending["turn"])
                if not stale:
                    live.activity = "idle"
                    live.pending.pop("turn", None)
                self._observation(live, "native_completion", turn_id=turn_id, stale=stale, result=turn)
            elif method == "thread/configuration/updated":
                live.settings["observed"].update({key: params[key] for key in ("model", "modelProvider", "reasoningEffort") if key in params})

    def send(self, session: JSON, message: JSON) -> JSON:
        message = _object(message, "Session message")
        text = message.get("text")
        if text is None:
            text = encode_json(message).decode("utf-8")
        if not isinstance(text, str):
            raise OrchestrationError("Session message text must be a string.", details={"outcome": "not_started"})
        live = self._get(session)
        result = _object(live.process.request("turn/start", {**self.turn, "threadId": live.native_id, "input": [{"type": "text", "text": text}]}), "Codex turn result", "provider_protocol")
        with live.lock:
            turn_id = result.get("turn", {}).get("id")
            if turn_id not in live.completed:
                live.activity = "active"
                live.pending["turn"] = turn_id
                if turn_id not in live.seen_turns:
                    live.seen_turns.append(turn_id)
        self._observation(live, "native_submission", transport="codex/turn/start", result=result)
        return {**result, "submitted": True, "native_id": live.native_id, "incarnation": live.incarnation,
                "transport": "codex/turn/start", "completion": "pending"}

    def deliver(self, session: JSON, message: JSON) -> JSON:
        live = self._get(session)
        message = _object(message, "Delivery envelope")
        result = _object(live.process.request("turn/start", {
            **self.turn, "threadId": live.native_id, "input": [],
            "toolOutput": {"name": "peer_message_received", "namespace": None,
                           "output": encode_json(message).decode("utf-8")},
        }), "Codex delivery result", "provider_protocol")
        self._observation(live, "native_submission", transport="codex/turn/start.toolOutput", message=message, result=result)
        return {"submitted": True, "native_id": live.native_id, "incarnation": live.incarnation,
                "transport": "codex/turn/start.toolOutput", "result": result, "completion": "pending"}


class ACPSessionDriver(_NativeSessions):
    """Agent Client Protocol over configured stdio, without vendor CLI assumptions.

    ACP prompt responses complete a turn. Send acknowledges the written request;
    the independent native response is retained as a completion event. Streamed
    notifications are retained for poll while callbacks and control progress.
    Native filesystem/terminal client requests require an explicitly bound
    handler; no filesystem or shell capability is advertised by default.
    Native ``session`` settings, including MCP server commands, come from trusted
    configuration. Requested parameters require explicit delegation through the
    configured ``request_parameters`` JSON Schema and are validated before spawn.
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

    def _launch(self, specification: JSON, resume: str | None) -> JSON:
        specification, requested = self._prepare_start(specification, ("sessionId",))

        def handshake(live, spec):
            initialized = _object(live.process.request("initialize", self.initialize), "ACP initialization result", "provider_protocol")
            capabilities = _object(initialized.get("agentCapabilities", {}), "ACP agent capabilities", "provider_protocol")
            if self.authentication is not None:
                live.process.request("authenticate", self.authentication)
            params = {**requested, **self.session}
            if resume is not None:
                if not self.resumable or capabilities.get("loadSession") is not True:
                    raise OrchestrationError("ACP session loading is not enabled and advertised by this agent.", "unsupported_operation", {"outcome": "not_started"})
                live.native_id = _identifier(resume, "resume ID")
                params["sessionId"] = live.native_id
                _object(live.process.request("session/load", params), "ACP session load result", "provider_protocol")
            else:
                result = _object(live.process.request("session/new", params), "ACP session result", "provider_protocol")
                live.native_id = _identifier(result.get("sessionId"))
            live.settings = {"requested": requested, "resolved": deepcopy(params),
                             "observed": {"model": None, "reasoning": None, "provider": None},
                             "precedence": "trusted_configuration"}
            live.settings["observed"].update({key: result[key] for key in ("model", "reasoning", "provider", "configOptions", "models", "modes") if resume is None and key in result})
        return self._start(specification, handshake)

    def _prompt_started(self, live, prompt_id):
        pass

    def _prompt_finished(self, live, prompt_id, completed):
        live.activity = "unknown" if not completed else ("active" if live.pending else "idle")

    def send(self, session: JSON, message: JSON) -> JSON:
        message = _object(message, "Session message")
        text = message.get("text", encode_json(message).decode("utf-8"))
        if not isinstance(text, str):
            raise OrchestrationError("Session message text must be a string.", details={"outcome": "not_started"})
        live = self._get(session)
        with live.lock:
            if live.pending:
                raise OrchestrationError("This ACP driver has an outstanding prompt and no interjection contract.", "session_busy", {"outcome": "not_started"})
            prompt_id = uuid.uuid4().hex
            live.pending[prompt_id] = None
            live.activity = "active"
            self._prompt_started(live, prompt_id)
        try:
            future = live.process.request_async("session/prompt", {
                "sessionId": live.native_id, "prompt": [{"type": "text", "text": text}],
                "_meta": {"promptId": prompt_id},
            }, timeout=self.options.get("turn_timeout", 3600))
        except Exception:
            with live.lock:
                live.pending.pop(prompt_id, None)
                live.activity = "idle"
            raise
        with live.lock:
            live.pending[prompt_id] = future
        self._observation(live, "native_submission", transport=self.protocol + "/session/prompt", prompt_id=prompt_id, message=message)

        def completed(result):
            try:
                value = _object(result.result(), "ACP prompt result", "provider_protocol")
                if not isinstance(value.get("stopReason"), str) or not value["stopReason"]:
                    raise OrchestrationError("ACP prompt did not return a stop reason.", "provider_protocol", {"outcome": "unknown"})
                event = self._observation(live, "native_completion", prompt_id=prompt_id, result=value)
            except Exception as exc:
                event = self._observation(live, "native_completion_unknown", prompt_id=prompt_id,
                                          error=exc.code if isinstance(exc, OrchestrationError) else type(exc).__name__, outcome="unknown")
            with live.lock:
                live.pending.pop(prompt_id, None)
                self._prompt_finished(live, prompt_id, event["kind"] == "native_completion")
                if len(live.events) >= self.options.get("max_pending_messages", 1024):
                    live.process.close()
                    return
                live.events.append(event)
        future.add_done_callback(completed)
        return {"submitted": True, "native_id": live.native_id, "incarnation": live.incarnation,
                "prompt_id": prompt_id, "transport": self.protocol + "/session/prompt", "completion": "pending"}
